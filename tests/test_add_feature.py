"""
test_add_feature — AddFeatureModal, PlasmidFeaturePickerModal, and the
Insert-at-cursor pipeline.

Covers:
  - `_parse_qualifier_string` round-trips via `_qualifiers_to_string`
  - `_extract_feature_entries_from_record` respects strand + wrap
  - Modal mount + form gather (save / insert / validation branches)
  - App-side insert shifts feature coords and appends a new SeqFeature
    via the `_rebuild_record_with_edit` pipeline (sacred invariant #9
    remains intact for other features; the new feature lands exactly
    at the cursor with the requested strand + qualifiers)
"""
from __future__ import annotations

import pytest

import splicecraft as sc


TERMINAL_SIZE = (160, 48)


def _build_app(tiny_record, isolated_library) -> sc.PlasmidApp:
    app = sc.PlasmidApp()
    app._preload_record = tiny_record
    return app


# ═══════════════════════════════════════════════════════════════════════════════
# Pure helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestQualifierParsing:

    def test_single_pair(self):
        assert sc._parse_qualifier_string("gene=lacZ") == {"gene": ["lacZ"]}

    def test_multiple_pairs_semicolon(self):
        got = sc._parse_qualifier_string("gene=lacZ; product=LacZ alpha")
        assert got == {"gene": ["lacZ"], "product": ["LacZ alpha"]}

    def test_whitespace_is_stripped(self):
        got = sc._parse_qualifier_string("  gene  =  lacZ  ;  note  =  test  ")
        assert got == {"gene": ["lacZ"], "note": ["test"]}

    def test_duplicate_keys_collapsed_into_list(self):
        got = sc._parse_qualifier_string("note=a; note=b; note=c")
        assert got == {"note": ["a", "b", "c"]}

    def test_missing_equals_is_ignored(self):
        got = sc._parse_qualifier_string("gene=lacZ; garbage; product=LacZ")
        assert got == {"gene": ["lacZ"], "product": ["LacZ"]}

    def test_empty_input(self):
        assert sc._parse_qualifier_string("") == {}
        assert sc._parse_qualifier_string("   ") == {}

    def test_roundtrip_via_to_string(self):
        original = {"gene": ["lacZ"], "product": ["LacZ alpha"]}
        rendered = sc._qualifiers_to_string(original)
        parsed   = sc._parse_qualifier_string(rendered)
        assert parsed == original


# ═══════════════════════════════════════════════════════════════════════════════
# Feature extraction from a record
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractFeatureEntries:

    def test_skips_source_feature(self, tiny_record):
        # tiny_record has CDS + misc_feature, no 'source'. Add one to test.
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        tiny_record.features.insert(0, SeqFeature(
            FeatureLocation(0, len(tiny_record.seq), strand=1),
            type="source",
            qualifiers={"organism": ["synthetic"]},
        ))
        entries = sc._extract_feature_entries_from_record(tiny_record)
        # All entries must be non-source
        assert all(e["feature_type"] != "source" for e in entries)
        # CDS + misc_feature → 2 entries
        assert len(entries) == 2

    def test_forward_strand_sequence_matches_slice(self, tiny_record):
        entries = sc._extract_feature_entries_from_record(tiny_record)
        # tiny_record[0] is CDS at [0, 27, +1)
        cds = next(e for e in entries if e["feature_type"] == "CDS")
        assert cds["strand"] == 1
        assert cds["sequence"] == str(tiny_record.seq[0:27]).upper()

    def test_reverse_strand_sequence_is_revcomp(self, tiny_record):
        entries = sc._extract_feature_entries_from_record(tiny_record)
        mf = next(e for e in entries if e["feature_type"] == "misc_feature")
        assert mf["strand"] == -1
        # Fixture: misc_feature at [50, 80, -1). Stored sequence must be the
        # revcomp of the genomic slice (5'→3' of the feature as read).
        genomic = str(tiny_record.seq[50:80]).upper()
        assert mf["sequence"] == sc._rc(genomic)

    def test_qualifiers_preserved(self, tiny_record):
        entries = sc._extract_feature_entries_from_record(tiny_record)
        cds = next(e for e in entries if e["feature_type"] == "CDS")
        assert cds["qualifiers"].get("gene") == ["testA"]


# ═══════════════════════════════════════════════════════════════════════════════
# App-side insert pipeline
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnnotateWithFeature:
    """`_annotate_with_feature` adds a SeqFeature to the loaded record
    spanning the given range without modifying the underlying DNA. This
    is the shared backend for both the AddFeatureModal "Insert feature"
    button and the agent-API `add-feature` endpoint — single source of
    truth for "annotate existing bases".

    Pre-2026-04-30 the modal button instead spliced new DNA at the
    cursor (`_insert_feature_at_cursor`); that path was removed in
    favour of "select region → Ctrl+F → annotate range" which lets the
    user mark up an existing region without changing its length. New
    DNA insertion lives in Ctrl+E (EditSeqDialog) for users who need it.
    """

    async def test_forward_feature_appends_with_correct_coords(
        self, tiny_record, isolated_library,
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            orig_len = len(tiny_record.seq)
            entry = {
                "name": "my-feat",
                "feature_type": "promoter",
                "strand": 1,
                "qualifiers": {"note": ["user-added"]},
            }
            app._annotate_with_feature(10, 25, entry)
            # Sequence is unchanged — the whole point of "annotate".
            assert len(app._current_record.seq) == orig_len
            # New feature is the last one.
            last = app._current_record.features[-1]
            assert last.type == "promoter"
            assert int(last.location.start) == 10
            assert int(last.location.end) == 25
            assert last.location.strand == 1
            # Qualifiers include the user's note + the auto-label.
            assert last.qualifiers.get("note") == ["user-added"]
            assert last.qualifiers.get("label") == ["my-feat"]

    async def test_reverse_strand_records_strand_minus_one(
        self, tiny_record, isolated_library,
    ):
        """Reverse-strand annotations don't touch the DNA — they only
        flag the SeqFeature's strand. (The displayed bases are still
        the underlying top-strand bases.)"""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            orig_seq = str(tiny_record.seq)
            entry = {
                "name": "rev-feat", "feature_type": "CDS",
                "strand": -1, "qualifiers": {},
            }
            app._annotate_with_feature(30, 39, entry)
            # Sequence unchanged.
            assert str(app._current_record.seq) == orig_seq
            last = app._current_record.features[-1]
            assert last.location.strand == -1
            assert int(last.location.start) == 30
            assert int(last.location.end) == 39

    async def test_other_features_keep_their_coords(
        self, tiny_record, isolated_library,
    ):
        """Annotating doesn't shift any existing features — opposite
        of the old splice behaviour."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            mf_pre = next(f for f in app._current_record.features
                          if f.type == "misc_feature")
            pre_start = int(mf_pre.location.start)
            pre_end   = int(mf_pre.location.end)
            entry = {
                "name": "x", "feature_type": "misc_feature",
                "strand": 1, "qualifiers": {},
            }
            # Annotate well before the existing misc_feature.
            app._annotate_with_feature(0, 5, entry)
            mf_post = next(f for f in app._current_record.features
                           if f.type == "misc_feature"
                           and f.qualifiers.get("label") != ["x"])
            assert int(mf_post.location.start) == pre_start
            assert int(mf_post.location.end)   == pre_end

    async def test_no_record_raises(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._current_record = None
            with pytest.raises(RuntimeError):
                app._annotate_with_feature(0, 5, {
                    "name": "x", "feature_type": "CDS",
                    "strand": 1, "qualifiers": {},
                })

    async def test_zero_length_range_raises(self, tiny_record,
                                              isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            with pytest.raises(ValueError):
                app._annotate_with_feature(5, 5, {
                    "name": "x", "feature_type": "CDS",
                    "strand": 1, "qualifiers": {},
                })

    async def test_out_of_range_raises(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            n = len(app._current_record.seq)
            with pytest.raises(ValueError):
                app._annotate_with_feature(n + 5, n + 10, {
                    "name": "x", "feature_type": "CDS",
                    "strand": 1, "qualifiers": {},
                })

    async def test_annotate_marks_dirty(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._unsaved = False
            app._annotate_with_feature(0, 6, {
                "name": "x", "feature_type": "CDS",
                "strand": 1, "qualifiers": {},
            })
            assert app._unsaved is True

    async def test_wrap_range_builds_compound_location(
        self, isolated_library,
    ):
        """end < start should produce a CompoundLocation with two
        FeatureLocation parts (tail [start, n) + head [0, end)) — the
        same wrap-aware shape every other code path expects (sacred
        invariant #9)."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import CompoundLocation
        rec = SeqRecord(Seq("A" * 100), id="wrap_anno", name="wrap_anno",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._annotate_with_feature(95, 5, {
                "name": "wrap-feat", "feature_type": "misc_feature",
                "strand": 1, "qualifiers": {},
            })
            last = app._current_record.features[-1]
            assert isinstance(last.location, CompoundLocation)
            parts = list(last.location.parts)
            assert int(parts[0].start) == 95
            assert int(parts[0].end)   == 100
            assert int(parts[1].start) == 0
            assert int(parts[1].end)   == 5

    async def test_strand_zero_accepted(self, tiny_record, isolated_library):
        """Arrowless / unknown-strand annotations (strand=0) should
        round-trip through `_annotate_with_feature` without crashing —
        BioPython needs `strand=None` for that case."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._annotate_with_feature(0, 10, {
                "name": "ds", "feature_type": "misc_feature",
                "strand": 0, "qualifiers": {},
            })
            last = app._current_record.features[-1]
            # FeatureLocation(strand=None) → location.strand == None
            assert last.location.strand is None
            assert int(last.location.start) == 0
            assert int(last.location.end)   == 10

    async def test_modal_dispatches_annotate_action(
        self, tiny_record, isolated_library,
    ):
        """End-to-end: setting a selection_range, opening the modal,
        and clicking "Insert feature" should fire `_add_feature_result`
        with `action="annotate"` and our captured range, which lands
        a feature at exactly that range."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            n_before = len(app._current_record.features)
            # Drive the result callback directly — the button is
            # wired to dismiss with this exact dict shape.
            app._add_feature_result({
                "action": "annotate",
                "range":  (12, 24),
                "entry":  {
                    "name": "marked",
                    "feature_type": "misc_feature",
                    "strand": 1,
                    "qualifiers": {},
                },
            })
            assert len(app._current_record.features) == n_before + 1
            new = app._current_record.features[-1]
            assert int(new.location.start) == 12
            assert int(new.location.end)   == 24
            assert new.qualifiers.get("label") == ["marked"]


class TestCDSDivisibleByThreeGate:
    """A CDS feature must span a whole number of codons. The modal
    blocks a non-divisible-by-3 selection inline (so the user fixes
    the highlight), and `_annotate_with_feature` repeats the check
    so direct callers (agent-API) get the same gate. The check uses
    the SELECTION SPAN, wrap-aware, not the typed-sequence length —
    the feature is anchored to the bp range, not whatever's in the
    Sequence textarea."""

    async def test_helper_rejects_non_divisible_cds(
        self, tiny_record, isolated_library,
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            with pytest.raises(ValueError, match="multiple of 3|divisible by 3"):
                app._annotate_with_feature(0, 10, {   # 10 bp — not %3
                    "name": "x", "feature_type": "CDS",
                    "strand": 1, "qualifiers": {},
                })

    async def test_helper_accepts_divisible_cds(
        self, tiny_record, isolated_library,
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._annotate_with_feature(0, 9, {   # 9 bp — passes
                "name": "x", "feature_type": "CDS",
                "strand": 1, "qualifiers": {},
            })
            assert app._current_record.features[-1].type == "CDS"

    async def test_helper_rejects_wrap_cds_when_total_span_indivisible(
        self, isolated_library,
    ):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 100), id="wrap_cds", name="wrap_cds",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Wrap span = (100 - 95) + 5 = 10, not %3.
            with pytest.raises(ValueError, match="divisible by 3"):
                app._annotate_with_feature(95, 5, {
                    "name": "wcds", "feature_type": "CDS",
                    "strand": 1, "qualifiers": {},
                })

    async def test_helper_accepts_wrap_cds_when_total_span_divisible(
        self, isolated_library,
    ):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 100), id="wrap_ok", name="wrap_ok",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Wrap span = (100 - 94) + 3 = 9, %3 == 0 → accept.
            app._annotate_with_feature(94, 3, {
                "name": "wcds", "feature_type": "CDS",
                "strand": 1, "qualifiers": {},
            })
            assert app._current_record.features[-1].type == "CDS"

    async def test_non_cds_unchecked_even_when_indivisible(
        self, tiny_record, isolated_library,
    ):
        """The gate applies ONLY to CDS — promoter / misc_feature / etc.
        accept any span length, including non-multiples of 3."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._annotate_with_feature(0, 10, {
                "name": "p", "feature_type": "promoter",
                "strand": 1, "qualifiers": {},
            })
            last = app._current_record.features[-1]
            assert last.type == "promoter"
            assert int(last.location.end) - int(last.location.start) == 10

    async def test_modal_blocks_indivisible_cds_inline(
        self, tiny_record, isolated_library,
    ):
        """Open the modal with a 10-bp selection, set type=CDS, click
        Insert. The button handler should NOT dismiss — the inline
        status box shows the divisible-by-3 error instead."""
        from textual.widgets import Static
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (0, 10)   # 10 bp — not %3
            app.action_add_feature()
            await pilot.pause()
            await pilot.pause(0.1)
            modal = app.screen
            assert isinstance(modal, sc.AddFeatureModal)
            modal.query_one("#addfeat-name").value = "bad-cds"
            # Type already defaults to "CDS"; force it explicitly.
            modal.query_one("#addfeat-type").value = "CDS"
            await pilot.pause(0.05)
            # Click Insert → still on the modal, status box flagged.
            await pilot.click("#btn-addfeat-insert")
            await pilot.pause(0.2)
            # Modal still on screen (not dismissed).
            assert isinstance(app.screen, sc.AddFeatureModal)
            status_text = str(modal.query_one("#addfeat-status", Static)
                                  .render())
            assert "multiple of 3" in status_text


class TestPackerNewOnTop:
    """Newly added features stack on top of older overlapping features.
    The packer iterates `feats` in insertion order; older features pack
    first and land at the bottom (closest to DNA), newer features get
    pushed to higher rows wherever their column range overlaps an
    older feature. Per-feature priority rotation will land in a future
    release; until then, recency is the rule."""

    def test_overlapping_new_feature_lands_above_old(self):
        old = {"start": 0, "end": 30, "type": "misc_feature",
                "label": "old", "strand": 1, "color": "white"}
        new = {"start": 10, "end": 20, "type": "misc_feature",
                "label": "new", "strand": 1, "color": "white"}
        placements = sc._pack_features_2d([old, new], 0, 30)
        rows = {p[0]["label"]: p[1] for p in placements}
        assert rows["old"] < rows["new"], (
            f"new feature should land above old; got rows={rows}"
        )

    def test_new_cds_pushed_above_existing_non_cds(self):
        """Pre-fix, CDS features pre-empted lane 0 over non-CDS.
        Post-fix, insertion order rules: an existing non-CDS keeps
        lane 0 and a newly added CDS lands above it."""
        old_promoter = {"start": 0, "end": 30, "type": "promoter",
                          "label": "p", "strand": 1, "color": "white"}
        new_cds = {"start": 10, "end": 22, "type": "CDS",
                     "label": "c", "strand": 1, "color": "white"}
        placements = sc._pack_features_2d(
            [old_promoter, new_cds], 0, 30,
        )
        rows = {p[0]["label"]: p[1] for p in placements}
        assert rows["p"] == 0
        assert rows["c"] > 0

    def test_non_overlapping_features_share_lane(self):
        """Features that don't overlap pack into the same row regardless
        of insertion order — recency only kicks in on collisions."""
        a = {"start": 0,  "end": 10, "type": "misc_feature",
              "label": "a", "strand": 1, "color": "white"}
        b = {"start": 20, "end": 30, "type": "misc_feature",
              "label": "b", "strand": 1, "color": "white"}
        placements = sc._pack_features_2d([a, b], 0, 30)
        rows = {p[0]["label"]: p[1] for p in placements}
        assert rows["a"] == 0
        assert rows["b"] == 0


class TestGlyphOwnerTracking:
    """`SequencePanel._chunk_glyph_owners` fills per-(packed_row, col)
    owners across the FULL `(footprint_rows × bp_range)` rectangle of
    each feature — clicks anywhere on the visible lane art (bar,
    label-text, label-padding, AA cells) all resolve to the right
    feature. `_check_packed` does the codon-vs-bar dispatch for CDS
    afterward."""

    def _build_panel(self, seq: str, feats: list) -> "sc.SequencePanel":
        sp = sc.SequencePanel.__new__(sc.SequencePanel)
        sp._seq = seq
        sp._feats = feats
        sp._chunks_owners = {}
        # `_chunk_glyph_owners` keys the per-chunk owner cache on
        # `_view_origin_bp` so rotated views don't reuse stale lookup
        # tables. The bare-attribute SequencePanel built here doesn't
        # run `__init__`, so wire the rotation default in by hand.
        sp._view_origin_bp = 0
        return sp

    def test_owners_fill_full_footprint_for_non_cds(self):
        """A non-CDS feature occupies 2 packed rows (bar + label).
        owners_above[0..1][bp_range] should all be the feature, even
        in label-padding cells (so clicking on padding still picks
        the feature). Outside the bp range, owner=None."""
        f = {"start": 5, "end": 15, "type": "misc_feature",
              "label": "f", "strand": 1, "color": "white"}
        above_p, below_p, above_rows, below_rows = sc._chunk_lane_groups(
            [f], 0, 20,
        )
        sp = self._build_panel("A" * 20, [f])
        result = sp._chunk_glyph_owners(
            0, 20, [f], above_p, below_p, above_rows, below_rows,
        )
        owners = result["owners_above"]
        assert len(owners) == above_rows == 2
        for r in (0, 1):
            for col in range(5, 15):
                assert owners[r][col] is f, (
                    f"row {r} col {col} should own f"
                )
            for col in (0, 4, 15, 19):
                assert owners[r][col] is None

    def test_nested_features_get_distinct_owners_per_cell(self):
        """Smaller feature inside a larger one — every cell in the
        smaller's rectangle should own the smaller, every cell in
        the larger's rectangle (outside the smaller) should own the
        larger. Greedy packing ensures the rectangles don't overlap
        per row, so each cell has one unambiguous owner."""
        outer = {"start": 0, "end": 30, "type": "misc_feature",
                  "label": "outer", "strand": 1, "color": "white"}
        inner = {"start": 12, "end": 18, "type": "misc_feature",
                  "label": "inner", "strand": 1, "color": "red"}
        above_p, below_p, above_rows, below_rows = sc._chunk_lane_groups(
            [outer, inner], 0, 30,
        )
        sp = self._build_panel("A" * 30, [outer, inner])
        result = sp._chunk_glyph_owners(
            0, 30, [outer, inner],
            above_p, below_p, above_rows, below_rows,
        )
        owners = result["owners_above"]
        # outer at rows 0-1, inner at rows 2-3 (greedy packing pushes
        # inner above outer where bp ranges overlap).
        assert owners[0][5]  is outer
        assert owners[0][15] is outer
        assert owners[0][25] is outer
        assert owners[1][5]  is outer
        # Inner's rectangle: rows 2-3, cols 12-17.
        for r in (2, 3):
            for col in range(12, 18):
                assert owners[r][col] is inner
            for col in (5, 11, 18, 25):
                assert owners[r][col] is None

    def test_below_strand_owners_filled(self):
        """Reverse-strand feature in the below-DNA stack — `_check_packed`
        for `is_below=True` looks up `owners_below` indexed by
        screen_row_idx_from_top (= packed_row directly). Owners must
        be filled across the bp range for that strand, mirroring above."""
        rev = {"start": 5, "end": 25, "type": "misc_feature",
                "label": "rev", "strand": -1, "color": "white"}
        above_p, below_p, above_rows, below_rows = sc._chunk_lane_groups(
            [rev], 0, 30,
        )
        # Reverse-strand goes to below_p, not above_p.
        assert any(p[0] is rev for p in below_p)
        sp = self._build_panel("A" * 30, [rev])
        result = sp._chunk_glyph_owners(
            0, 30, [rev], above_p, below_p, above_rows, below_rows,
        )
        owners = result["owners_below"]
        assert len(owners) == below_rows == 2
        for r in (0, 1):
            for col in range(5, 25):
                assert owners[r][col] is rev
            for col in (0, 4, 25, 29):
                assert owners[r][col] is None

    def test_owner_cache_invalidates_on_feats_change(self):
        """Cache key includes `id(self._feats)` — reassigning `_feats`
        forces a fresh compute. Without this, post-annotate clicks
        could lookup stale owners that don't include the new feature."""
        f1 = {"start": 0, "end": 10, "type": "misc_feature",
               "label": "f1", "strand": 1, "color": "white"}
        above_p, below_p, above_rows, below_rows = sc._chunk_lane_groups(
            [f1], 0, 30,
        )
        sp = self._build_panel("A" * 30, [f1])
        first = sp._chunk_glyph_owners(
            0, 30, [f1], above_p, below_p, above_rows, below_rows,
        )
        # Reassign feats — simulating annotate's update_seq path,
        # but skip the .clear() to verify cache key is the safety net.
        f2 = {"start": 20, "end": 28, "type": "misc_feature",
               "label": "f2", "strand": 1, "color": "red"}
        sp._feats = [f1, f2]
        above_p2, below_p2, above_rows2, below_rows2 = (
            sc._chunk_lane_groups([f1, f2], 0, 30)
        )
        second = sp._chunk_glyph_owners(
            0, 30, [f1, f2],
            above_p2, below_p2, above_rows2, below_rows2,
        )
        assert second is not first, (
            "id-based cache key must invalidate on feats reassignment"
        )

    def test_cds_owners_fill_full_footprint(self):
        """A CDS occupies 3 packed rows (AA + bar + label). All 3 rows
        own the CDS across its bp range — owner-fill is uniform across
        the rectangle. The codon-vs-bar dispatch happens in
        `_check_packed` based on `packed_row - bottom_row`, not in
        the owner data itself, so AA-row inter-letter cells still
        own the CDS (and `_check_packed` resolves them as bar clicks)."""
        cds = {"start": 0, "end": 30, "type": "CDS",
                "strand": 1, "color": "white", "label": "c"}
        above_p, below_p, above_rows, below_rows = sc._chunk_lane_groups(
            [cds], 0, 30,
        )
        sp = self._build_panel("A" * 30, [cds])
        result = sp._chunk_glyph_owners(
            0, 30, [cds], above_p, below_p, above_rows, below_rows,
        )
        owners = result["owners_above"]
        assert above_rows == 3
        for r in (0, 1, 2):
            for col in range(30):
                assert owners[r][col] is cds, (
                    f"row {r} col {col} should own CDS"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# Modal surface — mount + gather
# ═══════════════════════════════════════════════════════════════════════════════

class TestAddFeatureModal:

    async def test_modal_mounts(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.push_screen(sc.AddFeatureModal())
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.AddFeatureModal)
            # Every required widget present
            modal.query_one("#addfeat-name")
            modal.query_one("#addfeat-type")
            modal.query_one("#addfeat-seq")
            modal.query_one("#addfeat-quals")

    async def test_gather_rejects_empty_name(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.push_screen(sc.AddFeatureModal())
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            modal.query_one("#addfeat-name").value = ""
            modal.query_one("#addfeat-seq").text = "ATG"
            assert modal._gather() is None

    async def test_gather_rejects_invalid_bases(self, tiny_record,
                                                  isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.push_screen(sc.AddFeatureModal())
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            modal.query_one("#addfeat-name").value = "bad"
            modal.query_one("#addfeat-seq").text = "ATGXXZZ"
            assert modal._gather() is None

    async def test_gather_accepts_valid_entry(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.push_screen(sc.AddFeatureModal())
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            modal.query_one("#addfeat-name").value = "lacZ"
            modal.query_one("#addfeat-seq").text = "atg aaa tag"   # spaces ok
            modal.query_one("#addfeat-quals").value = "gene=lacZ"
            entry = modal._gather()
            assert entry is not None
            assert entry["name"] == "lacZ"
            assert entry["sequence"] == "ATGAAATAG"
            assert entry["qualifiers"] == {"gene": ["lacZ"]}
            assert entry["strand"] == 1   # default = forward

    async def test_gather_iupac_bases_allowed(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.push_screen(sc.AddFeatureModal())
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            modal.query_one("#addfeat-name").value = "degenerate"
            modal.query_one("#addfeat-seq").text = "RRYYWWN"
            entry = modal._gather()
            assert entry is not None
            assert entry["sequence"] == "RRYYWWN"


# ═══════════════════════════════════════════════════════════════════════════════
# Save-to-library flow via _add_feature_result
# ═══════════════════════════════════════════════════════════════════════════════

class TestAddFeatureSelectionPrefill:
    """Ctrl+F (`action_add_feature`) checks the seq panel for an
    active multi-bp selection and pre-fills the modal's Sequence body
    with those bases verbatim. Saves the typical "select region →
    Ctrl+C → paste into modal" round-trip when adding a feature for
    a region the user just highlighted.

    Selection sources covered: drag/Shift-click (`_user_sel`) and
    feature picks (`_sel_range`). Single-bp selections are NOT
    pre-filled — a click that lands on one base shouldn't be treated
    as a selection. Wrap-around selections (end < start) splice
    tail+head correctly. Pre-existing 2026-04-30 add-feature path."""

    async def test_user_sel_prefills_sequence(self, tiny_record,
                                                isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            # tiny_record sequence starts with "ATGAAAGATCTGGAATTC..."
            sp._user_sel = (0, 9)   # "ATGAAAGAT"
            app.action_add_feature()
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.AddFeatureModal)
            seq_box = modal.query_one("#addfeat-seq")
            assert seq_box.text == "ATGAAAGAT"

    async def test_sel_range_prefills_sequence(self, tiny_record,
                                                 isolated_library):
        """`_sel_range` is set when a feature is highlighted via map /
        sidebar / lane click. The Ctrl+F flow should still pick it up."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._sel_range = (3, 12)   # "AAAGATCTG"
            app.action_add_feature()
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.AddFeatureModal)
            seq_box = modal.query_one("#addfeat-seq")
            assert seq_box.text == "AAAGATCTG"

    async def test_user_sel_takes_precedence_over_sel_range(
        self, tiny_record, isolated_library,
    ):
        """If both selections happen to be set (e.g., feature picked
        then user dragged a different region), the user's drag wins —
        same precedence Ctrl+C uses."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel  = (0, 6)    # "ATGAAA"
            sp._sel_range = (10, 20)  # different region
            app.action_add_feature()
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            seq_box = modal.query_one("#addfeat-seq")
            assert seq_box.text == "ATGAAA"

    async def test_single_bp_selection_does_not_prefill(
        self, tiny_record, isolated_library,
    ):
        """A 1-bp 'selection' is what a plain click produces; treat it
        as no selection so the modal opens with an empty Sequence box."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (5, 6)   # 1 bp
            app.action_add_feature()
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            seq_box = modal.query_one("#addfeat-seq")
            assert seq_box.text == ""

    async def test_no_selection_opens_empty_modal(
        self, tiny_record, isolated_library,
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel  = None
            sp._sel_range = None
            app.action_add_feature()
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            seq_box = modal.query_one("#addfeat-seq")
            assert seq_box.text == ""

    async def test_wrap_selection_splices_tail_plus_head(
        self, isolated_library,
    ):
        """Wrap-around selections (end < start) should splice the tail
        [start, n) + head [0, end) — same convention as `_user_sel` set
        by `select_feature_range` for an origin-spanning feature."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        seq = "AAAACCCCGGGGTTTT"   # n = 16
        rec = SeqRecord(Seq(seq), id="wrap_sel", name="wrap_sel",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (12, 4)   # tail "TTTT" + head "AAAA"
            app.action_add_feature()
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            seq_box = modal.query_one("#addfeat-seq")
            assert seq_box.text == "TTTTAAAA"

    async def test_prefill_uppercases_lowercase_input(
        self, isolated_library,
    ):
        """The seq panel can hold lowercase bases (some users edit
        input as lowercase to mark introns / annotation overlays).
        Ctrl+F should normalise to uppercase like Ctrl+C does, since
        the modal's downstream validator expects ACGT/IUPAC uppercase."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("aaaaccccGGGG"), id="case_test",
                        name="case_test",
                        annotations={"molecule_type": "DNA"})
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (0, 8)
            app.action_add_feature()
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            seq_box = modal.query_one("#addfeat-seq")
            assert seq_box.text == "AAAACCCC"


class TestSaveToLibraryFlow:
    """The modal dismisses with {"action": "save", "entry": ...}; the app's
    `_add_feature_result` must persist via _save_features."""

    async def test_save_appends_entry(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sc._features_cache = None
            assert sc._load_features() == []
            app._add_feature_result({
                "action": "save",
                "entry": {
                    "name": "lacZ-alpha",
                    "feature_type": "CDS",
                    "sequence": "ATGAAA",
                    "strand": 1,
                    "qualifiers": {"gene": ["lacZ"]},
                    "description": "",
                },
            })
            assert sc._FEATURES_FILE.exists()
            entries = sc._load_features()
            assert len(entries) == 1
            assert entries[0]["name"] == "lacZ-alpha"

    async def test_save_deduplicates_by_name_and_type(self, tiny_record,
                                                       isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sc._features_cache = None
            app._add_feature_result({"action": "save", "entry": {
                "name": "dup", "feature_type": "CDS",
                "sequence": "A", "strand": 1,
                "qualifiers": {}, "description": "",
            }})
            app._add_feature_result({"action": "save", "entry": {
                "name": "dup", "feature_type": "CDS",
                "sequence": "T", "strand": 1,
                "qualifiers": {}, "description": "",
            }})
            entries = sc._load_features()
            assert len(entries) == 1
            assert entries[0]["sequence"] == "T"   # latest wins


# ═══════════════════════════════════════════════════════════════════════════════
# Feature picker
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlasmidFeaturePickerModal:

    async def test_picker_mounts_with_entries(self, tiny_record,
                                               isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            feats = sc._extract_feature_entries_from_record(tiny_record)
            app.push_screen(sc.PlasmidFeaturePickerModal(feats,
                                                         plasmid_name="tiny"))
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            table = modal.query_one("#featpick-table")
            assert table.row_count == len(feats)
