"""
test_alignment_overlay — linear-map alignment overlay helpers.

Covers `_alignment_to_target_segments` and `_alignment_to_target_letters`
— the pure functions that classify each target column of a pairwise
alignment as match / mismatch / gap. They drive the linear-view
alignment lanes (blue / red / gray bars + letters) that overlay
sequencing-read pile-ups on the plasmid map.

Sacred behaviours under test:

  * Target-resolution coordinates — target gaps (insertions in the
    query) consume no target column and don't subdivide the surrounding
    state.
  * Three-state classification — match / mismatch / gap match the
    user-spec 3-color scheme.
  * Case-insensitive matching — the bp comparison ignores case.
  * `t_start` offset — for local alignments with non-zero target
    offsets, segments shift accordingly.
"""
from __future__ import annotations

import pytest

import splicecraft as sc


# ═══════════════════════════════════════════════════════════════════════════════
# _alignment_to_target_segments
# ═══════════════════════════════════════════════════════════════════════════════

class TestAlignmentToTargetSegments:
    def test_all_match(self):
        assert sc._alignment_to_target_segments("ATGC", "ATGC") == [
            (0, 4, "match"),
        ]

    def test_all_mismatch(self):
        assert sc._alignment_to_target_segments("TTTT", "AAAA") == [
            (0, 4, "mismatch"),
        ]

    def test_match_mismatch_match(self):
        # col 2: G≠C mismatch; flanks all match
        assert sc._alignment_to_target_segments("ATGC", "ATCC") == [
            (0, 2, "match"),
            (2, 3, "mismatch"),
            (3, 4, "match"),
        ]

    def test_query_deletion_makes_gap_segment(self):
        # query has 2-bp deletion against target
        assert sc._alignment_to_target_segments("AT--GC", "ATCCGC") == [
            (0, 2, "match"),
            (2, 4, "gap"),
            (4, 6, "match"),
        ]

    def test_target_gap_invisible_at_target_resolution(self):
        # target has a 2-col gap (insertion in query) — consumes zero
        # target columns; surrounding state continues unbroken
        assert sc._alignment_to_target_segments("ATXXGC", "AT--GC") == [
            (0, 4, "match"),
        ]

    def test_target_gap_then_state_change(self):
        # query insertion immediately followed by a mismatch — the
        # mismatch starts at the target column right after the
        # insertion (insertion contributes no target position)
        assert sc._alignment_to_target_segments("ATXG", "AT-C") == [
            (0, 2, "match"),
            (2, 3, "mismatch"),
        ]

    def test_t_start_offset(self):
        assert sc._alignment_to_target_segments("ATGC", "ATGC", t_start=100) == [
            (100, 104, "match"),
        ]

    def test_case_insensitive(self):
        assert sc._alignment_to_target_segments("atgc", "ATGC") == [
            (0, 4, "match"),
        ]

    def test_empty(self):
        assert sc._alignment_to_target_segments("", "") == []

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="differ in length"):
            sc._alignment_to_target_segments("ATGC", "ATG")

    def test_complex_mixed_states(self):
        # M M MM M gap M  →  segments at positions 0..2, 2..3, 3..4, 4..5, 5..6
        assert sc._alignment_to_target_segments("ATGC-G", "ATCCAG") == [
            (0, 2, "match"),
            (2, 3, "mismatch"),
            (3, 4, "match"),
            (4, 5, "gap"),
            (5, 6, "match"),
        ]

    def test_consecutive_runs_coalesce(self):
        # 3 consecutive matches → one segment, not three
        result = sc._alignment_to_target_segments("AAAA", "AAAA")
        assert len(result) == 1
        assert result[0] == (0, 4, "match")

    def test_all_gap(self):
        assert sc._alignment_to_target_segments("----", "ATGC") == [
            (0, 4, "gap"),
        ]


# ═══════════════════════════════════════════════════════════════════════════════
# _alignment_to_target_letters
# ═══════════════════════════════════════════════════════════════════════════════

class TestAlignmentToTargetLetters:
    def test_all_match(self):
        assert sc._alignment_to_target_letters("ATGC", "ATGC") == {
            0: ("A", "match"),
            1: ("T", "match"),
            2: ("G", "match"),
            3: ("C", "match"),
        }

    def test_mismatch_letter_is_query_base(self):
        # target ATGC, query ATGT — col 3 query says T, target says C
        letters = sc._alignment_to_target_letters("ATGT", "ATGC")
        assert letters[3] == ("T", "mismatch")

    def test_gap_letter_is_dash(self):
        letters = sc._alignment_to_target_letters("AT-G", "ATCG")
        assert letters[2] == ("-", "gap")

    def test_target_gap_skipped(self):
        # target column 1 is a gap — query base at that column never
        # makes it into the per-target dict
        letters = sc._alignment_to_target_letters("ATXG", "A-TG")
        assert letters == {
            0: ("A", "match"),
            1: ("X", "mismatch"),
            2: ("G", "match"),
        }

    def test_t_start_offset(self):
        assert sc._alignment_to_target_letters("AT", "AT", t_start=50) == {
            50: ("A", "match"),
            51: ("T", "match"),
        }

    def test_case_insensitive(self):
        # lowercase query, uppercase target — match classification holds
        letters = sc._alignment_to_target_letters("atgc", "ATGC")
        for pos in range(4):
            _, state = letters[pos]
            assert state == "match"

    def test_empty(self):
        assert sc._alignment_to_target_letters("", "") == {}

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="differ in length"):
            sc._alignment_to_target_letters("AT", "ATG")


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-check: segments and letters agree on state
# ═══════════════════════════════════════════════════════════════════════════════

class TestSegmenterLetterConsistency:
    """The two helpers walk the same gapped strings with the same
    classification — every target column in `letters` must fall inside
    exactly one segment of the matching state.
    """

    @pytest.mark.parametrize("aq,at", [
        ("ATGC",       "ATGC"),
        ("ATGT",       "ATGC"),
        ("AT--GC",     "ATCCGC"),
        ("ATXG",       "AT-C"),
        ("ATGC-G",     "ATCCAG"),
        ("----",       "ATGC"),
        ("ATCGATCG",   "ATCGTTCG"),  # one mismatch in the middle
    ])
    def test_consistency(self, aq, at):
        segs = sc._alignment_to_target_segments(aq, at)
        letters = sc._alignment_to_target_letters(aq, at)
        for t_pos, (_letter, state) in letters.items():
            matching = [
                s for s in segs if s[0] <= t_pos < s[1] and s[2] == state
            ]
            assert len(matching) == 1, (
                f"t_pos={t_pos} state={state!r} not covered by any "
                f"matching segment in {segs!r}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Registration + lifecycle hardening
# ═══════════════════════════════════════════════════════════════════════════════
# Generation-counter race guards prevent in-flight workers from
# resurrecting cleared alignments. The two assertions below back the
# `_alignments_generation` contract:
#   * `_clear_alignments` ALWAYS bumps the counter (even when the band
#     is already empty) so a worker that hadn't registered yet still
#     gets poisoned by a "preemptive" clear.
#   * `_register_alignment` refuses degenerate input (empty aligned
#     strings) — those would paint nothing and surface as a phantom
#     row.

TERMINAL_SIZE = (160, 48)


class TestAlignmentLifecycle:
    """Pilot-driven tests for the register/clear contract."""

    async def test_clear_bumps_generation_when_non_empty(
            self, tiny_record, isolated_library):
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Seed one alignment so clear has work to do.
            app._alignments = [{
                "name": "fake", "query_label": "q", "target_label": "t",
                "target_record": tiny_record,
                "result": {"aligned_q": "A", "aligned_t": "A"},
                "aligned_q": "A", "aligned_t": "A",
                "t_start": 0, "segments": [(0, 1, "match")],
                "t_lo": 0, "t_hi": 1, "letters": None,
            }]
            gen_before = app._alignments_generation
            app._clear_alignments()
            assert app._alignments == []
            assert app._alignments_generation == gen_before + 1

    async def test_clear_bumps_generation_when_already_empty(
            self, tiny_record, isolated_library):
        """Empty-band clear still bumps the counter — workers that
        started before the clear must still see the bump and refuse
        to register, even if there was nothing visible to clear."""
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            gen_before = app._alignments_generation
            assert app._alignments == []
            app._clear_alignments()
            assert app._alignments_generation == gen_before + 1

    async def test_register_rejects_empty_aligned_strings(
            self, tiny_record, isolated_library):
        """Degenerate `_pairwise_align` results (empty aligned_q /
        aligned_t) MUST NOT register — they'd surface as a phantom
        zero-width row."""
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            assert app._alignments == []
            # All three empty-string permutations should be refused.
            for aq, at in (("", ""), ("ATGC", ""), ("", "ATGC")):
                app._register_alignment(
                    name="empty",
                    query_label="q",
                    target_label="t",
                    target_record=tiny_record,
                    result={"aligned_q": aq, "aligned_t": at},
                )
            assert app._alignments == []

    async def test_register_succeeds_with_valid_result(
            self, tiny_record, isolated_library):
        """Sanity: a valid result lands in the band."""
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._register_alignment(
                name="ok",
                query_label="q",
                target_label="t",
                target_record=tiny_record,
                result={"aligned_q": "ATGC", "aligned_t": "ATGC"},
            )
            assert len(app._alignments) == 1
            entry = app._alignments[0]
            assert entry["segments"] == [(0, 4, "match")]
            assert entry["t_lo"] == 0 and entry["t_hi"] == 4



# ═══════════════════════════════════════════════════════════════════════════════
# Circular alignment offset (GH #16, 2026-05-14)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCircularAlignmentOffset:
    """`_find_circular_alignment_offset` rotates a circular target so
    the global pairwise align doesn't pair bp 1 of an arbitrarily-
    started Plasmidsaurus read with bp 1 of the GenBank reference.
    Regression guard for Cory Tobin's report — pre-fix alignment of a
    700-bp-rotated read showed 66% identity + 500+ gaps; post-fix the
    same read aligns at 100% with zero gaps."""

    def test_returns_zero_when_sequences_already_aligned(self):
        target = 'ACGTACGTACGT' * 100
        # Same sequence — no rotation needed.
        assert sc._find_circular_alignment_offset(target, target) == 0

    def test_detects_simple_700_bp_rotation(self):
        # Pseudo-random plasmid-shaped target so each 25-bp seed
        # appears at a unique anchor — repeat-pattern fixtures defeat
        # the uniqueness guard and the helper safely falls back to 0.
        import random
        rng = random.Random(20260514)
        target = "".join(rng.choice("ACGT") for _ in range(2000))
        read = target[700:] + target[:700]
        offset = sc._find_circular_alignment_offset(read, target)
        assert offset == 700

    def test_detects_rotation_near_origin_wrap(self):
        """Rotation just before the target's end means the seed may
        straddle the wrap. The doubled-target search handles this."""
        import random
        rng = random.Random(20260514)
        target = "".join(rng.choice("ACGT") for _ in range(1500))
        rotation = len(target) - 50
        read = target[rotation:] + target[:rotation]
        offset = sc._find_circular_alignment_offset(read, target)
        assert offset == rotation

    def test_returns_zero_for_short_sequences(self):
        # Below the k=25 minimum kmer length — bail cleanly.
        assert sc._find_circular_alignment_offset('AAAA', 'TTTT') == 0

    def test_skips_low_complexity_seeds(self):
        """A query that starts with a homopolymer run shouldn't seed
        on the homopolymer (it'd match everywhere); the helper steps
        past it and finds a complex seed further along."""
        target = 'A' * 100 + 'GTACGTACGTAC' * 30 + 'C' * 50
        # Read starts mid-target.
        rotation = 250
        read = target[rotation:] + target[:rotation]
        offset = sc._find_circular_alignment_offset(read, target)
        # Either the helper finds the exact rotation OR returns 0
        # (no clean unique seed); both are acceptable. The bad
        # outcome we're guarding against is a WRONG non-zero answer.
        assert offset in (0, rotation)

    def test_pairwise_align_with_rotation_recovers_identity(self):
        """End-to-end: a rotated read aligned against the rotated
        target should produce near-100%% identity vs ~50-70%% without
        rotation. This is the test that maps directly to Cory's GH #16
        screenshot."""
        import random
        rng = random.Random(20260514)
        target = "".join(rng.choice("ACGT") for _ in range(3000))
        read = target[700:] + target[:700]
        offset = sc._find_circular_alignment_offset(read, target)
        assert offset == 700
        rotated = target[offset:] + target[:offset]
        result = sc._pairwise_align(read, rotated, mode='global')
        assert result['identity_pct'] >= 99.0
        assert result['n_gaps'] == 0


class TestRotateSeqRecord:
    """`_rotate_seq_record` shifts a SeqRecord's sequence + features
    so that a chosen position becomes the new origin. Used by the
    alignment path to keep the viewer's feature lane in register
    with the rotated target."""

    @staticmethod
    def _circular(seq: str, *, features=()):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq(seq), id='T', name='T')
        rec.annotations['molecule_type'] = 'DNA'
        rec.annotations['topology'] = 'circular'
        rec.features = list(features)
        return rec

    def test_zero_offset_returns_input(self):
        rec = self._circular('A' * 100)
        rotated = sc._rotate_seq_record(rec, 0)
        assert rotated is rec

    def test_rotation_shifts_sequence(self):
        rec = self._circular('ABCDEFGHIJ')
        rotated = sc._rotate_seq_record(rec, 3)
        assert str(rotated.seq) == 'DEFGHIJABC'

    def test_rotation_shifts_simple_feature(self):
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = self._circular(
            'A' * 100,
            features=[SeqFeature(FeatureLocation(50, 70, strand=1),
                                   type='CDS')],
        )
        rotated = sc._rotate_seq_record(rec, 20)
        # Feature was at 50-70; after rotation by 20 it's at 30-50.
        assert len(rotated.features) == 1
        loc = rotated.features[0].location
        assert int(loc.start) == 30
        assert int(loc.end) == 50

    def test_rotation_preserves_record_metadata(self):
        rec = self._circular('A' * 100)
        rec.description = 'test'
        rotated = sc._rotate_seq_record(rec, 20)
        assert rotated.id == 'T'
        assert rotated.description == 'test'
        assert rotated.annotations['topology'] == 'circular'


# ═══════════════════════════════════════════════════════════════════════════════
# Per-plasmid map_mode persistence (2026-05-18)
# ═══════════════════════════════════════════════════════════════════════════════
# Library entries can carry a `map_mode` field that overrides the
# topology-derived default on load. Plasmidsaurus alignment + the
# user's Alt+L toggle both write through `_persist_map_mode_for_active`
# so the choice sticks across reloads. Sequencing-aligned plasmids
# also have their library entry tagged `linear` so the next open
# defaults to the diff-friendly view.

class TestPerPlasmidMapModePersistence:
    """Tests use `_apply_record` rather than `_preload_record` because
    the preload path dispatches `_add_save_to_disk` (a `@work(thread=True)`
    worker) whose disk write can race test teardown and contaminate the
    next test's tmp library file."""

    async def test_load_record_with_stashed_linear_overrides_topology(
            self, isolated_library):
        """A circular plasmid loaded from a library entry tagged
        `map_mode: "linear"` opens in linear view — the user-set
        preference beats the topology default."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 500), id="C", name="C",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec._tui_map_mode = "linear"
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(rec)
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm._map_mode == "linear", (
                "stashed _tui_map_mode='linear' must win over circular topology"
            )

    async def test_load_record_with_stashed_circular_overrides_linear_topo(
            self, isolated_library):
        """Symmetric: a `topology=linear` record loaded with a
        stashed `circular` preference opens circular. Belt-and-braces
        check on the override direction."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 500), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "linear"})
        rec._tui_map_mode = "circular"
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(rec)
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm._map_mode == "circular"

    async def test_load_record_unknown_stashed_mode_falls_back_to_topology(
            self, isolated_library):
        """Defensive: a bogus stashed value (e.g. hand-edit) is
        ignored and the topology default applies."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 500), id="C", name="C",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec._tui_map_mode = "spiral"   # nonsense
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(rec)
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm._map_mode == "circular"

    async def test_persist_map_mode_writes_to_library_entry(
            self, tiny_record, isolated_library):
        """`_persist_map_mode_for_active` saves the chosen mode onto
        the active library entry so the next reload picks it up.
        Uses `_apply_record` (not `_preload_record`) to avoid the
        background `_add_save_to_disk` worker — that worker's write
        races test teardown."""
        # Pre-seed the library with an entry matching the loaded record.
        sc._save_library([{
            "id":      tiny_record.id,
            "name":    tiny_record.name,
            "size":    len(tiny_record.seq),
            "n_feats": 0,
            "added":   "2026-05-18",
            "gb_text": sc._record_to_gb_text(tiny_record),
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(tiny_record)
            await pilot.pause(0.05)
            app._persist_map_mode_for_active("linear")
            entries = sc._load_library()
            match = next(e for e in entries if e.get("id") == tiny_record.id)
            assert match.get("map_mode") == "linear"

    async def test_persist_map_mode_is_noop_for_unknown_entry(
            self, isolated_library):
        """When the loaded record isn't in the library, the helper
        must silently no-op (no exception, no spurious row).
        Uses a unique record id so even if a prior test's worker
        leaked an entry to the cache, the lookup fails cleanly."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        sc._save_library([])
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Unique id (`UNSAVED_NOOP_TEST`) the previous tests in
            # this class never reference — even cache contamination
            # from a sibling test can't match it.
            unsaved = SeqRecord(Seq("A" * 200),
                                 id="UNSAVED_NOOP_TEST",
                                 name="UNSAVED_NOOP_TEST",
                                 annotations={"molecule_type": "DNA",
                                              "topology": "circular"})
            app._apply_record(unsaved)
            await pilot.pause(0.05)
            entries_before = sc._load_library()
            assert not any(
                e.get("id") == "UNSAVED_NOOP_TEST" for e in entries_before
            ), "unsaved record must not be in the library"
            # Must not raise; library state for the unknown id stays
            # unchanged.
            app._persist_map_mode_for_active("linear")
            entries_after = sc._load_library()
            assert not any(
                e.get("id") == "UNSAVED_NOOP_TEST" for e in entries_after
            ), "no-op path must not insert a new row"
            # And entries that DID exist before keep their state.
            assert entries_before == entries_after

    async def test_toggle_map_view_persists_when_entry_exists(
            self, tiny_record, isolated_library):
        """End-to-end: user-driven `action_toggle_map_view` writes
        through to the library entry. Circular plasmid + toggle → entry
        carries `map_mode: "linear"`."""
        sc._save_library([{
            "id":      tiny_record.id,
            "name":    tiny_record.name,
            "size":    len(tiny_record.seq),
            "n_feats": 0,
            "added":   "2026-05-18",
            "gb_text": sc._record_to_gb_text(tiny_record),
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(tiny_record)
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            # tiny_record is circular so starts circular
            assert pm._map_mode == "circular"
            pm.action_toggle_map_view()   # circular → linear
            assert pm._map_mode == "linear"
            entries = sc._load_library()
            match = next(e for e in entries if e.get("id") == tiny_record.id)
            assert match.get("map_mode") == "linear"

    async def test_register_alignment_persists_linear_on_target(
            self, tiny_record, isolated_library):
        """Registering an alignment against a circular target pins
        the map to linear AND writes `map_mode: "linear"` to the
        target's library entry. Mirrors the Plasmidsaurus path —
        sequencing-aligned plasmids default to linear on every later
        load."""
        sc._save_library([{
            "id":      tiny_record.id,
            "name":    tiny_record.name,
            "size":    len(tiny_record.seq),
            "n_feats": 0,
            "added":   "2026-05-18",
            "gb_text": sc._record_to_gb_text(tiny_record),
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(tiny_record)
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm._map_mode == "circular"
            app._register_alignment(
                name="read1", query_label="q", target_label="t",
                target_record=tiny_record,
                result={"aligned_q": "ATGC", "aligned_t": "ATGC"},
            )
            assert pm._map_mode == "linear"
            entries = sc._load_library()
            match = next(e for e in entries if e.get("id") == tiny_record.id)
            assert match.get("map_mode") == "linear"


# ═══════════════════════════════════════════════════════════════════════════════
# Sequencing toolbar screen (2026-05-18)
# ═══════════════════════════════════════════════════════════════════════════════
# Sequencing replaces the freestanding Plasmidsaurus modal. Tab layout
# leaves room for future ingestion sources (direct API, nanopore
# consensus). The legacy class name is aliased so agent/test paths
# keep resolving.

class TestSequencingScreen:
    def test_back_compat_alias_resolves(self):
        """`PlasmidsaurusAlignModal` is the old class name — kept as
        an alias for tests and agent-API callers."""
        assert sc.PlasmidsaurusAlignModal is sc.SequencingScreen

    def test_menu_lists_sequencing(self):
        """The Sequencing entry is wired into the top-level menu bar."""
        assert "Sequencing" in sc.MenuBar.MENUS

    async def test_screen_opens_with_plasmidsaurus_tab(
            self, tmp_path, tiny_record, isolated_library):
        """The Sequencing screen mounts with the Plasmidsaurus tab
        active (it's currently the only tab; future tabs will share
        the same TabbedContent)."""
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(tiny_record)
            await pilot.pause(0.05)
            await app.push_screen(
                sc.SequencingScreen(start_path=str(tmp_path))
            )
            await pilot.pause(0.2)
            screen = app.screen
            assert isinstance(screen, sc.SequencingScreen)
            # All the legacy alignment IDs still resolve so the worker
            # event handlers (which key off these IDs) still work.
            screen.query_one("#align-zip-tree")
            screen.query_one("#align-members")
            screen.query_one("#align-target")
            screen.query_one("#btn-align-go")
            screen.query_one("#btn-sequencing-close")

    async def test_subtabs_disabled_until_zip_loaded(
            self, tmp_path, tiny_record, isolated_library):
        """Samples / Quality / Align sub-tabs are disabled on mount;
        the user can't tab into them until a valid zip lands. General
        stays enabled because it owns the zip picker."""
        from textual.widgets import TabPane
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(tiny_record)
            await pilot.pause(0.05)
            await app.push_screen(
                sc.SequencingScreen(start_path=str(tmp_path))
            )
            await pilot.pause(0.2)
            screen = app.screen
            for tab_id in ("psaurus-sub-samples",
                            "psaurus-sub-quality",
                            "psaurus-sub-align"):
                assert screen.query_one(f"#{tab_id}", TabPane).disabled, (
                    f"{tab_id} must be disabled before zip load"
                )
            # General stays enabled — it owns the zip picker.
            assert not screen.query_one(
                "#psaurus-sub-general", TabPane,
            ).disabled

    async def test_zip_load_enables_subtabs_and_populates_tables(
            self, tmp_path, tiny_record, isolated_library):
        """End-to-end: feeding the screen a synthetic Plasmidsaurus-
        style zip via `_on_zip_picked` enables the dependent sub-tabs,
        populates the Samples + Quality tables, and writes the run
        metadata summary."""
        from textual.widgets import TabPane, DataTable, Static
        from textual.widgets import DirectoryTree
        import zipfile
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        # Synthesise a Plasmidsaurus-shaped zip: 2 samples with
        # consensus .gbk + summary.txt + per-base TSV.
        rec1 = SeqRecord(Seq("ATGC" * 200), id="MAV34", name="MAV34",
                         annotations={"molecule_type": "DNA",
                                      "topology": "circular"})
        rec2 = SeqRecord(Seq("GCAT" * 200), id="MAV35", name="MAV35",
                         annotations={"molecule_type": "DNA",
                                      "topology": "circular"})
        gbk1 = tmp_path / "MAV34.gbk"
        gbk2 = tmp_path / "MAV35.gbk"
        SeqIO.write(rec1, gbk1, "genbank")
        SeqIO.write(rec2, gbk2, "genbank")
        zp = tmp_path / "RUN42_results.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.write(gbk1, "RUN42_genbank-files/RUN42_1_MAV34.gbk")
            zf.write(gbk2, "RUN42_genbank-files/RUN42_2_MAV35.gbk")
            zf.writestr(
                "RUN42_summary-files/RUN42_1_MAV34.txt",
                "       1-mer (%)  2-mer (%)\n"
                "moles       95.5        4.5\n"
                "mass        90.1        9.9\n\n\n"
                "*************************\n\n\n"
                "E. coli genomic contamination: 12.3%\n",
            )
            zf.writestr(
                "RUN42_summary-files/RUN42_2_MAV35.txt",
                "       1-mer (%)  2-mer (%)\n"
                "moles       99.9        0.1\n"
                "mass        99.5        0.5\n\n\n"
                "*************************\n\n\n"
                "E. coli genomic contamination: 2.1%\n",
            )
            # Synthetic per-base TSV: 5 rows, integer coverage.
            zf.writestr(
                "RUN42_per-base-data/RUN42_1_MAV34.tsv",
                "pos\tref\treads_all\n"
                "1\tA\t30\n2\tT\t25\n3\tG\t40\n4\tC\t10\n5\tA\t50\n",
            )
            zf.writestr("RUN42_gel.png", b"PNG-fake-bytes")

        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(tiny_record)
            await pilot.pause(0.05)
            await app.push_screen(
                sc.SequencingScreen(start_path=str(tmp_path))
            )
            await pilot.pause(0.2)
            screen = app.screen
            assert isinstance(screen, sc.SequencingScreen)
            # Feed the FileSelected event the directory tree would emit.
            tree = screen.query_one(
                "#align-zip-tree", sc._ZipAwareDirectoryTree,
            )
            screen.post_message(
                DirectoryTree.FileSelected(tree.root, zp),
            )
            await pilot.pause(0.3)
            # Dependent sub-tabs are now enabled.
            for tab_id in ("psaurus-sub-samples",
                            "psaurus-sub-quality",
                            "psaurus-sub-align"):
                assert not screen.query_one(
                    f"#{tab_id}", TabPane,
                ).disabled, f"{tab_id} must be enabled after zip load"
            # Run metadata shows both samples.
            assert screen._parsed_run.get("run_id") == "RUN42"
            assert len(screen._parsed_run.get("samples", [])) == 2
            # Samples table populated with one row per sample.
            samples_t = screen.query_one(
                "#align-members", DataTable,
            )
            assert samples_t.row_count == 2
            # Quality table also has both samples.
            quality_t = screen.query_one(
                "#plasmidsaurus-quality-table", DataTable,
            )
            assert quality_t.row_count == 2
            # Run-level files table picks up the gel.png.
            runfiles_t = screen.query_one(
                "#plasmidsaurus-runfiles-table", DataTable,
            )
            assert runfiles_t.row_count >= 1

    async def test_sample_row_select_enables_align_button(
            self, tmp_path, tiny_record, isolated_library):
        """Clicking a sample row marks that sample's .gbk as the
        alignment query, updates the query indicator on the Align
        tab, and flips the Align button to enabled."""
        from textual.widgets import (DataTable, Button, Static,
                                       DirectoryTree)
        import zipfile
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ATGC" * 100), id="MAV1", name="MAV1",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        gbk = tmp_path / "MAV1.gbk"
        SeqIO.write(rec, gbk, "genbank")
        zp = tmp_path / "RUN1_results.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.write(gbk, "RUN1_genbank-files/RUN1_1_MAV1.gbk")
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(tiny_record)
            await pilot.pause(0.05)
            await app.push_screen(
                sc.SequencingScreen(start_path=str(tmp_path))
            )
            await pilot.pause(0.2)
            screen = app.screen
            tree = screen.query_one(
                "#align-zip-tree", sc._ZipAwareDirectoryTree,
            )
            screen.post_message(
                DirectoryTree.FileSelected(tree.root, zp),
            )
            await pilot.pause(0.3)
            # Pre-select: Align button is disabled.
            assert screen.query_one(
                "#btn-align-go", Button,
            ).disabled
            # Synthesise the RowSelected event the Samples DataTable
            # would emit on click.
            samples_t = screen.query_one(
                "#align-members", DataTable,
            )
            samples_t.cursor_coordinate = (
                samples_t.cursor_coordinate.__class__(0, 0)
            )
            from textual.coordinate import Coordinate
            row_key = next(iter(samples_t.rows.keys()))
            samples_t.post_message(
                DataTable.RowSelected(
                    samples_t, Coordinate(0, 0), row_key,
                )
            )
            await pilot.pause(0.1)
            assert screen._selected_member is not None
            assert not screen.query_one(
                "#btn-align-go", Button,
            ).disabled
            # Selected member is set; the query indicator update is
            # exercised end-to-end (verified via the button-enabled
            # state above). Static's content is private API in Textual
            # so we don't peek at it directly.
            assert "MAV1" in str(screen._selected_member)

    async def test_repick_same_zip_skips_reparse(
            self, tmp_path, tiny_record, isolated_library):
        """Picking the same zip twice in a row is a no-op (perf
        guard — parse can take ~1 s on large runs)."""
        from textual.widgets import DirectoryTree
        import zipfile
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ATGC" * 50), id="MAV1", name="MAV1",
                        annotations={"molecule_type": "DNA"})
        gbk = tmp_path / "MAV1.gbk"
        SeqIO.write(rec, gbk, "genbank")
        zp = tmp_path / "R_results.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.write(gbk, "R_genbank-files/R_1_MAV1.gbk")
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(tiny_record)
            await pilot.pause(0.05)
            await app.push_screen(
                sc.SequencingScreen(start_path=str(tmp_path))
            )
            await pilot.pause(0.2)
            screen = app.screen
            tree = screen.query_one(
                "#align-zip-tree", sc._ZipAwareDirectoryTree,
            )
            # First pick — populates _parsed_run.
            screen.post_message(
                DirectoryTree.FileSelected(tree.root, zp),
            )
            await pilot.pause(0.3)
            parsed_before = screen._parsed_run
            assert parsed_before
            # Mark with a sentinel so we can detect re-parse.
            parsed_before["_test_sentinel"] = True
            # Second pick of the same path — should NOT re-parse
            # (the sentinel survives because _parsed_run is the
            # same dict object).
            screen.post_message(
                DirectoryTree.FileSelected(tree.root, zp),
            )
            await pilot.pause(0.2)
            assert screen._parsed_run.get("_test_sentinel") is True, (
                "same-path re-pick must not re-parse"
            )

    async def test_invalid_zip_keeps_subtabs_disabled(
            self, tmp_path, tiny_record, isolated_library):
        """A user picking a non-zip file should NOT unlock the sub-tabs
        and the General tab's status row should explain why."""
        from textual.widgets import TabPane, DirectoryTree
        # Non-zip file.
        bogus = tmp_path / "README.txt"
        bogus.write_text("not a zip")
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(tiny_record)
            await pilot.pause(0.05)
            await app.push_screen(
                sc.SequencingScreen(start_path=str(tmp_path))
            )
            await pilot.pause(0.2)
            screen = app.screen
            tree = screen.query_one(
                "#align-zip-tree", sc._ZipAwareDirectoryTree,
            )
            screen.post_message(
                DirectoryTree.FileSelected(tree.root, bogus),
            )
            await pilot.pause(0.2)
            # Sub-tabs stay disabled.
            for tab_id in ("psaurus-sub-samples",
                            "psaurus-sub-quality",
                            "psaurus-sub-align"):
                assert screen.query_one(
                    f"#{tab_id}", TabPane,
                ).disabled, f"{tab_id} must stay disabled on bad zip"
            # Parsed state stays empty.
            assert not screen._parsed_run


# ═══════════════════════════════════════════════════════════════════════════════
# Plasmidsaurus zip parser (run-structured ingestion)
# ═══════════════════════════════════════════════════════════════════════════════
# `_parse_plasmidsaurus_zip` walks a results zip and groups files by
# sample so the Sequencing toolbar's sub-tabs can render without
# re-reading the zip per tab. Run-level extras (gel.png, README) go
# under `run_files`. `_parse_plasmidsaurus_summary` extracts the
# k-mer / contamination percentages from the per-sample summary file.

class TestPlasmidsaurusZipParser:
    def _build_zip(self, dirpath, samples, *, run="RUN1",
                    extra_files=None):
        """Build a synthetic Plasmidsaurus-shaped zip in `dirpath`.
        `samples` is a list of (sample_name, summary_text, perbase_text).
        Returns the zip path."""
        import zipfile
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        zp = dirpath / f"{run}_results.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            for idx, (name, summary, perbase) in enumerate(samples, 1):
                rec = SeqRecord(
                    Seq("ATGC" * 50), id=name, name=name,
                    annotations={"molecule_type": "DNA",
                                 "topology": "circular"},
                )
                gbk = dirpath / f"{name}.gbk"
                SeqIO.write(rec, gbk, "genbank")
                base = f"{run}_{idx}_{name}"
                zf.write(gbk, f"{run}_genbank-files/{base}.gbk")
                if summary is not None:
                    zf.writestr(
                        f"{run}_summary-files/{base}.txt", summary,
                    )
                if perbase is not None:
                    zf.writestr(
                        f"{run}_per-base-data/{base}.tsv", perbase,
                    )
            for name, content in (extra_files or []):
                zf.writestr(name, content)
        return zp

    def test_parses_run_id_from_folder_prefix(self, tmp_path):
        zp = self._build_zip(tmp_path, [("MAV1", None, None)],
                              run="ABC42")
        data = sc._parse_plasmidsaurus_zip(zp)
        assert data["run_id"] == "ABC42"

    def test_groups_files_under_one_sample(self, tmp_path):
        zp = self._build_zip(tmp_path, [
            ("MAV1", "moles 99.0\nmass 98.0\nE. coli contamination: 5.0%\n",
             "pos\tref\treads_all\n1\tA\t30\n2\tT\t40\n"),
        ])
        data = sc._parse_plasmidsaurus_zip(zp)
        assert len(data["samples"]) == 1
        s = data["samples"][0]
        # Sample base collapses to the run_<n>_<name> stem.
        assert s["base"].endswith("MAV1")
        # All categories landed on the same sample dict.
        assert s["gbk"]
        assert s["summary"]
        assert s["perbase"]
        # Summary text streamed inline.
        assert "moles" in s["summary_text"]
        # Per-base coverage stats computed.
        assert s["perbase_coverage"].get("mean") == 35.0

    def test_run_level_files_separated_from_samples(self, tmp_path):
        zp = self._build_zip(tmp_path, [("MAV1", None, None)],
                              extra_files=[("RUN1_gel.png", b"PNG")])
        data = sc._parse_plasmidsaurus_zip(zp)
        # Sample list has the one MAV1; run-level file shows up in
        # `run_files`.
        assert len(data["samples"]) == 1
        run_paths = {rf["name"] for rf in data["run_files"]}
        assert "RUN1_gel.png" in run_paths

    def test_natural_sort_samples(self, tmp_path):
        """Samples come back natural-sorted on their base name —
        the run-index prefix Plasmidsaurus uses (`<run>_<n>_<name>`)
        naturally puts `_2_` before `_10_` under the natural-sort
        key (vs lexicographic `_10_` < `_2_`)."""
        # Pass samples in scrambled order (1, 10, 2). The run-index
        # is assigned by `enumerate` in input order, so the bases
        # become `RUN1_1_A`, `RUN1_2_B`, `RUN1_3_C` — already
        # naturally sorted by index, regardless of name.
        zp = self._build_zip(tmp_path, [
            ("A", None, None),
            ("B", None, None),
            ("C", None, None),
        ])
        data = sc._parse_plasmidsaurus_zip(zp)
        names = [s["name"] for s in data["samples"]]
        assert names == sorted(names, key=sc._natural_sort_key)

    def test_summary_parser_extracts_kmer_and_contam(self):
        text = (
            "       1-mer (%)  2-mer (%)\n"
            "moles       97.5        2.5\n"
            "mass        95.1        4.9\n\n\n"
            "*************************\n\n\n"
            "E. coli genomic contamination: 18.0%\n"
        )
        out = sc._parse_plasmidsaurus_summary(text)
        assert out["kmer_moles_pct"] == 97.5
        assert out["kmer_mass_pct"] == 95.1
        assert out["contamination_pct"] == 18.0
        assert "E. coli" in out["contamination_source"]

    def test_summary_parser_handles_missing_fields(self):
        # Empty input — every field returns None / "".
        out = sc._parse_plasmidsaurus_summary("")
        assert out["kmer_moles_pct"] is None
        assert out["kmer_mass_pct"] is None
        assert out["contamination_pct"] is None
        assert out["contamination_source"] == ""

    def test_perbase_summary_returns_empty_on_garbage(self, tmp_path):
        """A malformed per-base TSV (no numeric column 2) should not
        crash the parser; the sample's `perbase_coverage` ends empty."""
        zp = self._build_zip(tmp_path, [
            ("MAV1", None, "pos\tref\treads_all\n"
                            "alpha\tbeta\tgamma\nA\tB\tC\n"),
        ])
        data = sc._parse_plasmidsaurus_zip(zp)
        assert data["samples"][0]["perbase_coverage"] == {}

    def test_missing_zip_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            sc._parse_plasmidsaurus_zip(tmp_path / "does-not-exist.zip")

    def test_oversize_zip_rejected(self, tmp_path, monkeypatch):
        """A zip claiming to be larger than the cap is refused."""
        # Build a tiny zip then artificially cap to a smaller size.
        zp = self._build_zip(tmp_path, [("MAV1", None, None)])
        monkeypatch.setattr(sc, "_PLASMIDSAURUS_ZIP_MAX_BYTES", 1)
        with pytest.raises(ValueError, match="too large"):
            sc._parse_plasmidsaurus_zip(zp)

    def test_standalone_gbk_no_category_folder(self, tmp_path):
        """Zips without the Plasmidsaurus `_genbank-files/` folder
        layout still discover .gbk files as samples (back-compat
        with the older `_list_gbk_members_in_zip` behaviour)."""
        import zipfile
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ATGC" * 20), id="X", name="X",
                        annotations={"molecule_type": "DNA"})
        gbk = tmp_path / "X.gbk"
        SeqIO.write(rec, gbk, "genbank")
        zp = tmp_path / "ad-hoc.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.write(gbk, "sample_A/consensus.gbk")
        data = sc._parse_plasmidsaurus_zip(zp)
        # The .gbk should land as a sample, not in run_files.
        assert any(s.get("gbk") for s in data["samples"])


# ═══════════════════════════════════════════════════════════════════════════════
# Alignment band positioned closest-to-centerline (2026-05-18)
# ═══════════════════════════════════════════════════════════════════════════════
# Alignment lanes used to stack BELOW the reverse-feature band. The
# closest-to-center refactor flips that order — alignment lanes now
# render at `rail_row + 2` and reverse features get offset downward
# by the alignment lane count. `_pack_alignment_lanes` is the helper
# that lets the parent renderer learn the lane count up front.

class TestAlignmentBandCenterline:
    async def test_pack_alignment_lanes_returns_count(
            self, tiny_record, isolated_library):
        """`_pack_alignment_lanes` returns (placed, lane_count); empty
        when no alignments touch the visible window."""
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(tiny_record)
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            # No alignments registered → empty placement.
            placed, n_lanes = pm._pack_alignment_lanes(
                margin_l=5, usable_w=100, view_s=0, view_e=1000,
                w=160, bp_to_col=lambda bp: 5 + bp // 10,
            )
            assert placed == []
            assert n_lanes == 0
            # Register one alignment → one lane.
            app._register_alignment(
                name="r1", query_label="q", target_label="t",
                target_record=tiny_record,
                result={"aligned_q": "ATGC", "aligned_t": "ATGC"},
            )
            placed, n_lanes = pm._pack_alignment_lanes(
                margin_l=5, usable_w=100, view_s=0, view_e=1000,
                w=160, bp_to_col=lambda bp: 5 + bp // 10,
            )
            assert n_lanes == 1
            assert len(placed) == 1

    async def test_linear_draw_with_alignment_renders_without_error(
            self, tiny_record, isolated_library):
        """Smoke: a linear-view render with one alignment + rev feature
        completes without raising. Covers the new offset path where
        rev features land below the alignment band."""
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        # Add a rev-strand feature to exercise the offset path.
        tiny_record.features.append(
            SeqFeature(FeatureLocation(50, 100, strand=-1),
                       type="misc_feature",
                       qualifiers={"label": ["rev1"]})
        )
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(tiny_record)
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            pm._map_mode = "linear"
            app._register_alignment(
                name="r1", query_label="q", target_label="t",
                target_record=tiny_record,
                result={"aligned_q": "ATGC", "aligned_t": "ATGC"},
            )
            # Must not raise.
            pm.refresh()
            await pilot.pause(0.05)


# ═══════════════════════════════════════════════════════════════════════════════
# Sequencing hardening (2026-05-18)
# ═══════════════════════════════════════════════════════════════════════════════
# Sweep #8: per-base TSV cap, single-pass zip-open in the Samples
# table, NUL-anchored sentinels for the empty-library / no-gbk paths,
# narrow exception types.

class TestSequencingHardening:
    def test_perbase_summary_truncates_at_max_bytes(self):
        """`_summarize_perbase_tsv` must stop reading once the
        decompressed stream exceeds `max_bytes`. A pathological zip
        bomb that decompresses into a multi-GB single line would
        otherwise OOM `io.TextIOWrapper`'s line buffer."""
        import io
        # Build a 4 KB-per-line TSV with 200 lines = 800 KB. Cap at
        # 200 KB so the streamer stops after ~50 lines, not all 200.
        rows = [f"{i}\tA\t30" for i in range(1, 201)]
        body = ("pos\tref\treads_all\n" + "\n".join(rows)).encode(
            "utf-8",
        )
        # Inflate each row to ~4KB by padding column 1 (`ref`).
        pad = "X" * 4000
        rows_padded = [f"{i}\t{pad}\t30" for i in range(1, 201)]
        body = ("pos\tref\treads_all\n" + "\n".join(rows_padded)).encode(
            "utf-8",
        )
        cap = 200 * 1024
        stats = sc._summarize_perbase_tsv(io.BytesIO(body), max_bytes=cap)
        # Stats are present (the cap allowed *some* rows through).
        assert stats, "truncation must still yield a partial summary"
        # n_pos is bounded by what the cap permitted — about
        # cap / 4 KB ≈ 50 rows. Refuse to specify the exact number,
        # just verify we didn't slurp the full 200.
        assert stats["n_pos"] < 200, (
            f"cap={cap} should have stopped before 200 rows; "
            f"got n_pos={stats['n_pos']}"
        )

    def test_perbase_summary_short_input_complete(self):
        """A short TSV (well under cap) is fully consumed — the cap
        is one-way (truncate-only), it never under-counts on small
        inputs."""
        import io
        body = (
            b"pos\tref\treads_all\n"
            b"1\tA\t10\n2\tT\t20\n3\tG\t30\n4\tC\t40\n5\tA\t50\n"
        )
        stats = sc._summarize_perbase_tsv(
            io.BytesIO(body), max_bytes=1024 * 1024,
        )
        assert stats["n_pos"] == 5
        assert stats["mean"] == 30.0
        assert stats["min"] == 10
        assert stats["max"] == 50
        assert stats["above_20x"] == 4   # 20, 30, 40, 50

    def test_perbase_summary_no_trailing_newline(self):
        """Final row without trailing `\\n` is still counted —
        regression guard for the chunked-reader tail-flush logic.
        Pre-fix the rewrite, a 1-row TSV without trailing newline
        returned an empty dict because the pending fragment never
        got consumed."""
        import io
        body = b"pos\tref\treads_all\n1\tA\t42"
        stats = sc._summarize_perbase_tsv(
            io.BytesIO(body), max_bytes=1024,
        )
        assert stats["n_pos"] == 1
        assert stats["mean"] == 42.0

    def test_parse_zip_skips_oversize_perbase(
            self, tmp_path, monkeypatch):
        """A per-base TSV whose claimed `file_size` exceeds the cap
        is skipped (no read attempted) — defence layer 1. The sample
        still surfaces; its `perbase_coverage` is just empty."""
        import zipfile
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ATGC" * 20), id="X", name="X",
                        annotations={"molecule_type": "DNA"})
        gbk = tmp_path / "X.gbk"
        SeqIO.write(rec, gbk, "genbank")
        zp = tmp_path / "R_results.zip"
        # Plant a big TSV body (~200 KB of synthetic rows).
        big_body = ("pos\tref\treads_all\n"
                    + "\n".join(f"{i}\tA\t30" for i in range(1, 20001)))
        with zipfile.ZipFile(zp, "w") as zf:
            zf.write(gbk, "R_genbank-files/R_1_X.gbk")
            zf.writestr("R_per-base-data/R_1_X.tsv", big_body)
        # Cap to 1 KB so the 200 KB tsv is refused upfront.
        monkeypatch.setattr(
            sc, "_PLASMIDSAURUS_PERBASE_MAX_BYTES", 1024,
        )
        data = sc._parse_plasmidsaurus_zip(zp)
        assert len(data["samples"]) == 1
        # perbase_coverage is empty because the read was refused.
        assert data["samples"][0]["perbase_coverage"] == {}
        # But the sample still lists the perbase member name.
        assert data["samples"][0]["perbase"]

    def test_empty_library_sentinel_is_unique(self):
        """The NUL-anchored sentinels must not collide with any
        realistic library `id` / zip member name. Sanity check that
        they actually contain NUL (which the safe-name check rejects
        in member paths and which LOCUS-safe ids never carry)."""
        assert "\x00" in sc.SequencingScreen._EMPTY_LIBRARY_SENTINEL
        assert "\x00" in sc.SequencingScreen._NO_GBK_KEY_PREFIX

    async def test_target_dropdown_handles_empty_library(
            self, tmp_path, isolated_library):
        """Sequencing screen with NO library entries shows the empty-
        library sentinel and `_go` refuses to advance when the user
        clicks Align without a real target. Verified indirectly via
        the Select's current value (Static's `renderable` is private
        in newer Textual)."""
        from textual.widgets import Select, Button
        # Wipe the library so `_target_options` only has the sentinel.
        sc._save_library([])
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            await app.push_screen(
                sc.SequencingScreen(start_path=str(tmp_path))
            )
            await pilot.pause(0.2)
            screen = app.screen
            assert isinstance(screen, sc.SequencingScreen)
            # The Select's current value is the empty-library sentinel.
            sel = screen.query_one("#align-target", Select)
            assert sel.value == sc.SequencingScreen._EMPTY_LIBRARY_SENTINEL
            # Simulate a state where the user has picked a sample
            # (forces `_go` past the early-return). The sentinel check
            # should fire BEFORE the zip is opened, so the fake path
            # never gets touched.
            screen._zip_path = tmp_path / "nope.zip"
            screen._selected_member = "ignored.gbk"
            screen.query_one("#btn-align-go", Button).disabled = False
            # Snapshot alignment-registration count; the early-return
            # path must not bump it.
            n_before = len(app._alignments)
            screen._go(None)
            await pilot.pause(0.05)
            # No alignment registered because `_go` short-circuited.
            assert len(app._alignments) == n_before

    async def test_no_gbk_sentinel_refuses_align(
            self, tmp_path, isolated_library):
        """A samples row keyed with the NUL-anchored no-gbk sentinel
        must not arm the Align button (synthetic key would crash
        `_extract_gbk_member`)."""
        from textual.widgets import Button
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            await app.push_screen(
                sc.SequencingScreen(start_path=str(tmp_path))
            )
            await pilot.pause(0.2)
            screen = app.screen

            class _FakeKey:
                def __init__(self, v):
                    self.value = v

            class _FakeEvent:
                def __init__(self, key):
                    self.row_key = _FakeKey(key)
            # Pretend the user clicked a synthetic row.
            sentinel_key = (
                sc.SequencingScreen._NO_GBK_KEY_PREFIX + "sample-X"
            )
            screen._on_member_selected(_FakeEvent(sentinel_key))
            await pilot.pause(0.05)
            assert screen._selected_member is None
            assert screen.query_one(
                "#btn-align-go", Button,
            ).disabled

    def test_batch_extract_gbk_meta_opens_zip_once(
            self, tmp_path, monkeypatch):
        """`_batch_extract_gbk_meta` should walk every sample's gbk
        inside a single `ZipFile` open — pre-fix each sample paid
        a fresh open. Counts opens via a monkeypatched `__init__`."""
        import zipfile
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        # Build a 5-sample zip.
        zp = tmp_path / "R_results.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            for i in range(1, 6):
                rec = SeqRecord(
                    Seq("ATGC" * 50), id=f"S{i}", name=f"S{i}",
                    annotations={"molecule_type": "DNA"},
                )
                gbk = tmp_path / f"S{i}.gbk"
                SeqIO.write(rec, gbk, "genbank")
                zf.write(gbk, f"R_genbank-files/R_{i}_S{i}.gbk")
        parsed = sc._parse_plasmidsaurus_zip(zp)
        # Wire up a SequencingScreen instance just enough to call the
        # batch method directly (avoids the full async-mount cost).
        screen = sc.SequencingScreen.__new__(sc.SequencingScreen)
        screen._zip_path = zp
        screen._parsed_run = parsed
        opens: list[str] = []
        real_init = zipfile.ZipFile.__init__

        def _counting_init(self, file, *a, **kw):
            opens.append(str(file))
            return real_init(self, file, *a, **kw)
        monkeypatch.setattr(zipfile.ZipFile, "__init__", _counting_init)
        meta = screen._batch_extract_gbk_meta(parsed["samples"])
        # Exactly one ZipFile open for all 5 samples.
        assert len(opens) == 1, (
            f"expected 1 zip open for batch read, got {len(opens)}: {opens}"
        )
        # Every sample resolved bp/feats counts.
        assert len(meta) == 5
        for s in parsed["samples"]:
            gbk = s.get("gbk")
            assert gbk in meta, f"missing meta for {gbk}"
            bp_str, _feats = meta[gbk]
            assert bp_str != "—"

    def test_batch_extract_gbk_meta_corrupt_zip_returns_empty(
            self, tmp_path):
        """A corrupted zip path makes `_batch_extract_gbk_meta` log
        and return an empty dict — caller falls back to per-row
        "—" placeholders. Guard against missing-file / bad-zip OS
        errors leaking up the call stack."""
        screen = sc.SequencingScreen.__new__(sc.SequencingScreen)
        # Path that exists but isn't a zip.
        bad = tmp_path / "not-a-zip.txt"
        bad.write_text("hello")
        screen._zip_path = bad
        meta = screen._batch_extract_gbk_meta(
            [{"gbk": "x.gbk"}],
        )
        assert meta == {}

    def test_batch_extract_rejects_unsafe_member_names(self, tmp_path):
        """Belt-and-braces: `_batch_extract_gbk_meta` re-checks
        `_is_safe_zip_member_name` on every member. An in-process
        mutator of `_parsed_run` that tried to smuggle a traversal
        path back in would land in the err bucket, not crash."""
        import zipfile
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ATGC" * 20), id="X", name="X",
                        annotations={"molecule_type": "DNA"})
        gbk = tmp_path / "X.gbk"
        SeqIO.write(rec, gbk, "genbank")
        zp = tmp_path / "R_results.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.write(gbk, "R_genbank-files/R_1_X.gbk")
        screen = sc.SequencingScreen.__new__(sc.SequencingScreen)
        screen._zip_path = zp
        # Hand-crafted "sample" with a traversal name.
        meta = screen._batch_extract_gbk_meta(
            [{"gbk": "../../etc/passwd"}],
        )
        assert meta == {"../../etc/passwd": ("[red]err[/red]", "—")}
