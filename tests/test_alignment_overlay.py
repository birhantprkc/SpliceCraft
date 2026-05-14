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

