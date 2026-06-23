"""
test_invariants_hypothesis — property-based fuzzing of the sacred invariants.

The hand-written tests in `test_dna_sanity.py` and friends cover known edge
cases. These tests use `hypothesis` to generate thousands of random inputs
to catch edge cases we didn't think to hand-write. Each test checks a
property that must hold for ALL valid inputs, not just specific examples.

Properties covered:
  1. `_rc` is involutive: _rc(_rc(s)) == s for any IUPAC DNA string.
  2. `_rc` preserves length.
  3. `_rc` output contains only IUPAC characters if input does.
  4. `_feat_len` returns the same value as the non-wrap arithmetic when
     start <= end, and the wrap formula when end < start.
  5. `_feat_len` is always non-negative and <= total.
  6. `_bp_in` is consistent with `_feat_len`: for a non-wrap feature of
     length L, exactly L positions in [0,total) satisfy _bp_in.
  7. The wrap-aware midpoint formula always lies on the feature's arc.
"""
from __future__ import annotations

from hypothesis import given, strategies as st, settings, assume, HealthCheck

import splicecraft as sc


# ── Strategy helpers ──────────────────────────────────────────────────────────

# `_rc` uppercases its input, so involution only holds for uppercase strings.
# Mixed-case tests belong in test_dna_sanity.py.
IUPAC_CHARS = "ACGTRYWSMKBDHVN"

iupac_dna = st.text(alphabet=IUPAC_CHARS, min_size=1, max_size=500)
small_total = st.integers(min_value=4, max_value=10_000)


def _bp_in(bp: int, start: int, end: int, total: int) -> bool:
    """Standalone mirror of PlasmidMap._bp_in (which is a method) so tests
    can call it as a pure function. See splicecraft.py:2245."""
    return (start <= bp < end) if end >= start else (bp >= start or bp < end)


def _arc_positions(start: int, end: int, total: int) -> list[int]:
    """Return the list of bp positions covered by [start, end) on a
    circular plasmid of length total. Handles wrap (end < start)."""
    if end < start:
        return list(range(start, total)) + list(range(0, end))
    return list(range(start, end))


# ── Property: _rc ─────────────────────────────────────────────────────────────

class TestReverseComplementProperties:
    @given(seq=iupac_dna)
    @settings(max_examples=300, deadline=None)
    def test_rc_is_involutive(self, seq):
        """Sacred invariant #3: `_rc` must round-trip for any IUPAC seq.
        Double reverse-complement returns the original string unchanged."""
        assert sc._rc(sc._rc(seq)) == seq

    @given(seq=iupac_dna)
    @settings(max_examples=300, deadline=None)
    def test_rc_preserves_length(self, seq):
        assert len(sc._rc(seq)) == len(seq)

    @given(seq=iupac_dna)
    @settings(max_examples=300, deadline=None)
    def test_rc_output_in_iupac(self, seq):
        """Every character in the output must be a valid IUPAC code.
        Catches regressions where ambiguity codes silently pass through
        un-complemented (producing garbage like 'X' or case drift)."""
        rc = sc._rc(seq)
        for ch in rc:
            assert ch in IUPAC_CHARS, f"RC produced non-IUPAC char {ch!r}"

    @given(seq=st.text(alphabet="ACGT", min_size=1, max_size=500))
    @settings(max_examples=100, deadline=None)
    def test_rc_matches_biopython_for_acgt(self, seq):
        """Cross-validate against Biopython's authoritative reverse
        complement for ACGT-only inputs (Biopython doesn't case-preserve
        the same way for full IUPAC, so we stick to ACGT here)."""
        from Bio.Seq import Seq
        assert sc._rc(seq.upper()) == str(Seq(seq.upper()).reverse_complement())


# ── Property: _feat_len ────────────────────────────────────────────────────────

class TestFeatLenProperties:
    @given(start=st.integers(min_value=0, max_value=999),
           length=st.integers(min_value=1, max_value=999),
           total=small_total)
    @settings(max_examples=300, deadline=None)
    def test_feat_len_matches_linear_when_no_wrap(self, start, length, total):
        """For end >= start, `_feat_len` must equal `end - start`."""
        assume(start < total)
        end = start + length
        assume(end <= total)
        assert sc._feat_len(start, end, total) == length

    @given(total=small_total, data=st.data())
    @settings(max_examples=300, deadline=None)
    def test_feat_len_matches_wrap_formula(self, total, data):
        """For end < start (wrap), `_feat_len` must equal (total - start) + end.
        Generate end first (strictly below total), then start strictly above
        end — so every sample is a valid wrap feature (no filtering)."""
        end = data.draw(st.integers(min_value=0, max_value=total - 2))
        start = data.draw(st.integers(min_value=end + 1, max_value=total - 1))
        assert sc._feat_len(start, end, total) == (total - start) + end

    @given(total=small_total, data=st.data())
    @settings(max_examples=300, deadline=None)
    def test_feat_len_nonneg_and_bounded(self, total, data):
        """Sacred invariant #8: `_feat_len` must be non-negative and
        never exceed the total plasmid length. Breakages here corrupt
        sort orders and primer-design math.

        Draws `start` / `end` conditional on `total` instead of
        filtering via `assume(start < total)` — when `total` is small
        (4-10), independent draws from [0, 9999] hit the
        `filter_too_much` health check in serial mode (~0.1 % pass
        rate). Conditional draws keep the test intent and remove the
        flake."""
        start = data.draw(st.integers(min_value=0, max_value=total - 1))
        end = data.draw(st.integers(min_value=0, max_value=total - 1))
        L = sc._feat_len(start, end, total)
        assert 0 <= L <= total


# ── Property: _bp_in ───────────────────────────────────────────────────────────

class TestBpInProperties:
    @given(total=st.integers(min_value=10, max_value=2000), data=st.data())
    @settings(max_examples=100, deadline=None)
    def test_bp_in_counts_match_feat_len_no_wrap(self, total, data):
        """For non-wrap features, the number of positions in [0,total) that
        return True from `_bp_in` must equal `_feat_len(start, end, total)`."""
        start = data.draw(st.integers(min_value=0, max_value=total - 1))
        end = data.draw(st.integers(min_value=start, max_value=total))
        count = sum(1 for i in range(total) if _bp_in(i, start, end, total))
        assert count == sc._feat_len(start, end, total)

    @given(total=st.integers(min_value=10, max_value=2000), data=st.data())
    @settings(max_examples=100, deadline=None)
    def test_bp_in_counts_match_feat_len_wrap(self, total, data):
        """Same as above, but for wrap features (end < start)."""
        end = data.draw(st.integers(min_value=0, max_value=total - 2))
        start = data.draw(st.integers(min_value=end + 1, max_value=total - 1))
        count = sum(1 for i in range(total) if _bp_in(i, start, end, total))
        assert count == sc._feat_len(start, end, total)


# ── Property: circular midpoint (sacred invariant #5) ─────────────────────────

class TestWrapMidpointProperties:
    @given(data=st.data(), total=small_total)
    @settings(max_examples=300, deadline=None)
    def test_midpoint_lies_on_arc(self, data, total):
        """Sacred invariant #5: label-placement midpoint must lie on the
        feature's arc. For wrap features, the naive `(start+end)//2` sits
        on the wrong side of the plasmid; the modular formula must not.
        Covers both wrap and non-wrap cases.

        Drawing start/end from `[0, total)` directly (rather than the
        old `assume(start < total and end < total)`) avoids hypothesis'
        `filter_too_much` health-check failures when `total` is small
        and ~99 % of fixed-range inputs would be rejected.
        """
        start = data.draw(st.integers(min_value=0, max_value=total - 1))
        end   = data.draw(st.integers(min_value=0, max_value=total - 1))
        assume(start != end)  # zero-width has no midpoint semantics
        arc_len = (end - start) % total
        mid = (start + arc_len // 2) % total
        valid_positions = set(_arc_positions(start, end, total))
        assert mid in valid_positions, (
            f"midpoint {mid} not on arc for start={start} end={end} "
            f"total={total} (arc_len={arc_len})"
        )

    @given(data=st.data(), total=small_total)
    @settings(max_examples=200, deadline=None)
    def test_midpoint_is_within_feat_len(self, data, total):
        """The distance from start to midpoint (along the arc, modular)
        must be less than `_feat_len`. Catches off-by-one mistakes that
        place the midpoint just outside the arc."""
        start = data.draw(st.integers(min_value=0, max_value=total - 1))
        end   = data.draw(st.integers(min_value=0, max_value=total - 1))
        assume(start != end)
        arc_len = (end - start) % total
        mid = (start + arc_len // 2) % total
        feat_len = sc._feat_len(start, end, total)
        dist_from_start = (mid - start) % total
        assert dist_from_start < feat_len


# ── Property: restriction-site scan dedup (invariant #9) ─────────────────────
#
# Sacred invariant from CLAUDE.md: palindromic enzymes are scanned forward
# only (the bottom strand is implied — emitting a recut, not a second
# resite). The hand-written tests in test_dna_sanity.py cover specific
# canonical cases; these properties run thousands of randomly-generated
# inputs to catch dedup bugs that would otherwise only surface for an
# unlikely sequence pattern.

# ACGT-only — restriction sites use canonical bases. IUPAC ambiguity in
# the SCAN_CATALOG patterns is handled by the scanner internally, but we
# don't generate ambiguity-coded test sequences here because that would
# make the "exactly one EcoRI hit" claim hard to define.
acgt_dna = st.text(alphabet="ACGT", min_size=10, max_size=300)


def _resites_for(feats: list, enzyme: "str | None" = None) -> list:
    out = [f for f in feats if f.get("type") == "resite"]
    if enzyme is not None:
        out = [f for f in out if f.get("label") == enzyme]
    return out


def _recuts_for(feats: list, enzyme: "str | None" = None) -> list:
    out = [f for f in feats if f.get("type") == "recut"]
    if enzyme is not None:
        out = [f for f in out if f.get("label") == enzyme]
    return out


class TestRestrictionDedupProperties:
    """Property-based tests for the restriction-site scanner's dedup
    invariants. These complement the hand-written tests in
    test_dna_sanity.py by stress-testing the scanner on thousands of
    randomly-generated sequences."""

    @given(prefix=acgt_dna, suffix=acgt_dna)
    @settings(max_examples=200, deadline=None)
    def test_palindromic_eco_ri_single_resite(self, prefix, suffix):
        """Sacred invariant #1: ONE GAATTC site → ONE EcoRI resite.

        EcoRI recognises a palindrome (`GAATTC`); naively scanning both
        strands would yield two resites for the same physical site. The
        scanner explicitly de-duplicates via the `seen` set + bottom-
        strand `recut`-only emission. This test verifies the dedup
        holds for any prefix+suffix pair that has no other GAATTC
        occurrences."""
        # Avoid accidental GAATTC in prefix/suffix → ambiguous count.
        assume("GAATTC" not in prefix)
        assume("GAATTC" not in suffix)
        seq = prefix + "GAATTC" + suffix
        feats = sc._scan_restriction_sites(
            seq, min_recognition_len=6, unique_only=False, circular=False,
        )
        resites = _resites_for(feats, "EcoRI")
        assert len(resites) == 1, (
            f"expected 1 EcoRI resite, got {len(resites)}"
        )
        # Sacred invariant #1: palindromes always reported on forward strand.
        assert resites[0]["strand"] == 1

    @given(prefix=acgt_dna, suffix=acgt_dna)
    @settings(max_examples=200, deadline=None)
    def test_palindromic_eco_ri_emits_one_recut(self, prefix, suffix):
        """Each palindromic EcoRI site → exactly 1 `recut` (the bottom-
        strand cut is implied)."""
        assume("GAATTC" not in prefix)
        assume("GAATTC" not in suffix)
        seq = prefix + "GAATTC" + suffix
        feats = sc._scan_restriction_sites(
            seq, min_recognition_len=6, unique_only=False, circular=False,
        )
        recuts = _recuts_for(feats, "EcoRI")
        assert len(recuts) == 1, (
            f"expected 1 EcoRI recut, got {len(recuts)}"
        )

    @given(filler=acgt_dna, n_sites=st.integers(min_value=2, max_value=5))
    @settings(max_examples=100, deadline=None)
    def test_palindromic_count_scales_linearly(self, filler, n_sites):
        """N non-overlapping GAATTC sites in `filler`-padded gaps must
        produce exactly N EcoRI resites. Catches dedup bugs where two
        adjacent sites collapse to one."""
        assume("GAATTC" not in filler)
        # Pad with at least 6 bp of non-GAATTC so the sites can't merge.
        if len(filler) < 6:
            filler = filler + "A" * (6 - len(filler))
        seq_parts = ["AAA"]
        for _ in range(n_sites):
            seq_parts.append("GAATTC")
            seq_parts.append(filler)
        seq = "".join(seq_parts)
        feats = sc._scan_restriction_sites(
            seq, min_recognition_len=6, unique_only=False, circular=False,
        )
        resites = _resites_for(feats, "EcoRI")
        assert len(resites) == n_sites

    @given(seq=acgt_dna)
    @settings(max_examples=300, deadline=None)
    def test_resite_strand_consistency(self, seq):
        """Every resite must have strand ∈ {-1, +1}, never 0 or
        anything else. Catches a regression where a malformed enzyme
        catalog entry leaks into the output."""
        feats = sc._scan_restriction_sites(
            seq, min_recognition_len=4, unique_only=False, circular=False,
        )
        for f in _resites_for(feats):
            assert f.get("strand") in (-1, 1), (
                f"strand must be ±1 for {f.get('label')}; got "
                f"{f.get('strand')!r}"
            )

    @given(seq=acgt_dna)
    @settings(max_examples=200, deadline=None)
    def test_recut_inside_or_adjacent_to_recognition_span(self, seq):
        """For every recut, there's a corresponding resite for the same
        enzyme. (Type IIS cutters can have recuts OUTSIDE the recognition
        span, so we don't strictly require containment — but a recut
        must always be paired with at least one resite on the same
        enzyme.)"""
        feats = sc._scan_restriction_sites(
            seq, min_recognition_len=4, unique_only=False, circular=False,
        )
        resite_enzymes = {f.get("label") for f in _resites_for(feats)}
        recut_enzymes = {f.get("label") for f in _recuts_for(feats)}
        # Every recut enzyme must have at least one resite for the
        # same enzyme. (The reverse is not always true: some
        # enzymes' cuts may fall outside the recognition site of the
        # span we report, but a stray recut with no resite anchor is
        # a bug.)
        orphan_recuts = recut_enzymes - resite_enzymes
        assert not orphan_recuts, (
            f"orphan recuts (no matching resite): {orphan_recuts}"
        )

    @given(seq=acgt_dna)
    @settings(max_examples=200, deadline=None)
    def test_unique_only_filters_multi_cutters(self, seq):
        """unique_only=True must drop every enzyme whose total resite
        count > 1. Catches a regression where the filter misses
        palindromic-multi-cutter cases."""
        all_feats = sc._scan_restriction_sites(
            seq, min_recognition_len=6, unique_only=False, circular=False,
        )
        unique_feats = sc._scan_restriction_sites(
            seq, min_recognition_len=6, unique_only=True, circular=False,
        )
        # Group all_feats resite counts by enzyme.
        from collections import Counter
        all_counts = Counter(
            f["label"] for f in _resites_for(all_feats)
        )
        unique_labels = {
            f["label"] for f in _resites_for(unique_feats)
        }
        for enzyme in unique_labels:
            assert all_counts[enzyme] == 1, (
                f"{enzyme} appeared in unique_only=True output but had "
                f"{all_counts[enzyme]} hits in unique_only=False output"
            )


# ── Property: primer Tm bounds (rule-of-thumb fallback path) ──────────────────
#
# `_mut_tm` falls back to a 2°C/AT + 4°C/GC heuristic when primer3
# isn't available. This rule produces values that are physically
# meaningful: a 20-mer never melts above 80°C and never below 0°C.
# These tests stress-test the fallback heuristic, NOT primer3 itself
# (which has its own QA upstream). The primer3 path is exercised
# implicitly via the existing primer-design tests.

# Length range typical for PCR primers + mutagenesis oligos.
primer_seq = st.text(alphabet="ACGT", min_size=10, max_size=40)


class TestPrimerTmFallbackProperties:
    """Property-based tests for the rule-of-thumb Tm fallback in
    `_mut_tm`. The primer3-backed path is tested in test_primers.py;
    this class exercises only the guarantees the FALLBACK must
    uphold so callers never see physically nonsensical values."""

    @given(seq=primer_seq)
    @settings(
        max_examples=200, deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_fallback_tm_in_physically_reasonable_range(
        self, seq, monkeypatch,
    ):
        """The 2°C/AT + 4°C/GC fallback never exceeds 4 × len bp ≤
        160°C for a 40-mer, and is ≥ 2 × len bp ≥ 20°C for a 10-mer.
        A regression that, say, multiplied AT count incorrectly would
        push outputs outside this window."""
        # Force the fallback path by making primer3 unimportable for
        # the duration of the call.
        import builtins
        original_import = builtins.__import__

        def _fake_import(name, *a, **k):
            if name == "primer3":
                raise ImportError("primer3 unavailable for test")
            return original_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        # Drop any real value an earlier primer-design test memoized, so the
        # forced-fallback path isn't shadowed by a cached primer3 result.
        import splicecraft_primer as _p
        _p._mut_thermo_cache_clear()
        tm = sc._mut_tm(seq)
        assert isinstance(tm, (int, float))
        # Fallback formula: 2*AT + 4*GC. Min when all AT (2*len),
        # max when all GC (4*len).
        assert 2 * len(seq) <= tm <= 4 * len(seq)

    @given(seq=primer_seq)
    @settings(
        max_examples=200, deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_fallback_tm_monotonic_in_gc(self, seq, monkeypatch):
        """Adding a GC base to a primer should never DECREASE the
        fallback Tm — GC bonds are stronger. Catches a sign-flip
        regression in the fallback formula."""
        import builtins
        original_import = builtins.__import__

        def _fake_import(name, *a, **k):
            if name == "primer3":
                raise ImportError("primer3 unavailable for test")
            return original_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        import splicecraft_primer as _p
        _p._mut_thermo_cache_clear()
        tm_base = sc._mut_tm(seq)
        tm_plus_g = sc._mut_tm(seq + "G")
        tm_plus_a = sc._mut_tm(seq + "A")
        # Adding a G adds 4°C; adding an A adds 2°C. Strict ≥ to
        # catch the bug where AT and GC contributions get swapped.
        assert tm_plus_g >= tm_base + 1, (
            f"Tm should rise when GC added: base={tm_base}, "
            f"plus_g={tm_plus_g}"
        )
        assert tm_plus_g > tm_plus_a, (
            f"GC should contribute more than AT: plus_g={tm_plus_g}, "
            f"plus_a={tm_plus_a}"
        )


# ── Property: codon frame correctness across wrap ─────────────────────────────
#
# Sacred invariant: a CDS that wraps the origin (`end < start`) translates
# to the SAME protein as the linearised form. `_translate_cds`
# concatenates the tail + head before translating; this property test
# stresses that round-trip with thousands of random sequences.

# CDS sequence: must be a multiple of 3 for clean stop codon placement.
def _cds_seq(rng):
    n_codons = rng.draw(st.integers(min_value=10, max_value=80))
    codons = []
    for _ in range(n_codons):
        codons.append(rng.draw(st.text(alphabet="ACGT", min_size=3, max_size=3)))
    return "".join(codons)


class TestCodonFrameWrapProperties:
    """Sacred invariant: a wrapped CDS (`end < start`) and its
    linearised equivalent translate to the same protein."""

    @given(data=st.data())
    @settings(max_examples=100, deadline=None)
    def test_wrap_cds_matches_linearised(self, data):
        """For a circular plasmid where a CDS spans the origin, the
        protein MUST be identical to the protein you'd get by
        rotating the plasmid so the CDS doesn't wrap and translating
        the linear form. Catches off-by-one mistakes in the
        tail+head concatenation."""
        # Build a CDS sequence (multiple of 3 codons).
        cds = _cds_seq(data)
        n_codons = len(cds) // 3
        assume(n_codons >= 4)
        # Place the CDS so it wraps the origin: split the CDS into
        # `tail` (placed near end of plasmid) and `head` (placed at
        # 0). The combined plasmid has random padding before the tail.
        tail_codons = data.draw(st.integers(min_value=2, max_value=n_codons - 2))
        head_codons = n_codons - tail_codons
        tail_bp = tail_codons * 3
        head_bp = head_codons * 3
        # Random padding ensures the wrap is not at position 0.
        pad = data.draw(st.text(alphabet="ACGT", min_size=6, max_size=60))
        # Plasmid layout: pad + cds[head:] + cds[:head]   (length = pad + cds)
        # CDS feature: start = len(pad) + head_bp, end = len(pad)
        # Wait — that's not how it works. Let me think.
        # If CDS wraps, the CDS bp positions are [start..total) ∪ [0..end).
        # So we need:
        #   plasmid = head_part + tail_part
        #     where tail_part = cds[:tail_bp]      (placed at start..total)
        #           head_part = cds[tail_bp:]      (placed at 0..head_bp)
        #   but we also want some padding so wrap isn't at exact 0.
        # Easiest: rotate CDS into the plasmid such that:
        #   plasmid[start..end-of-plasmid] = cds[0..tail_bp]
        #   plasmid[0..head_bp]            = cds[tail_bp..]
        head_part = cds[tail_bp:]                     # head_bp chars
        tail_part = cds[:tail_bp]                     # tail_bp chars
        plasmid = head_part + pad + tail_part
        total = len(plasmid)
        start = head_bp + len(pad)                    # tail starts here
        end = head_bp                                 # head ends here
        # Sanity: the CDS coords wrap the origin (end < start).
        assume(end < start)
        protein_wrap = sc._translate_cds(plasmid, start, end, strand=1)
        # Linearised: just translate the CDS directly. (`_translate_cds`
        # with start=0, end=len(cds), strand=1 on `cds` gives the
        # canonical protein.)
        protein_lin = sc._translate_cds(cds, 0, len(cds), strand=1)
        assert protein_wrap == protein_lin, (
            f"wrapped CDS protein {protein_wrap!r} != linearised "
            f"{protein_lin!r}; start={start} end={end} total={total}"
        )

    @given(data=st.data())
    @settings(max_examples=100, deadline=None)
    def test_wrap_cds_reverse_strand_matches_linearised(self, data):
        """Same property on the reverse strand: a wrapped CDS on
        strand=-1 must translate to the reverse-complement protein
        of the linearised form."""
        cds = _cds_seq(data)
        n_codons = len(cds) // 3
        assume(n_codons >= 4)
        tail_codons = data.draw(st.integers(min_value=2, max_value=n_codons - 2))
        head_codons = n_codons - tail_codons
        tail_bp = tail_codons * 3
        head_bp = head_codons * 3
        pad = data.draw(st.text(alphabet="ACGT", min_size=6, max_size=60))
        head_part = cds[tail_bp:]
        tail_part = cds[:tail_bp]
        plasmid = head_part + pad + tail_part
        start = head_bp + len(pad)
        end = head_bp
        assume(end < start)
        protein_wrap = sc._translate_cds(plasmid, start, end, strand=-1)
        protein_lin = sc._translate_cds(cds, 0, len(cds), strand=-1)
        assert protein_wrap == protein_lin
