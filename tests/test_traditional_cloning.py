"""
test_traditional_cloning — Restriction-digest + ligation engine.

Covers the module-level helpers that power ConstructorModal's "Traditional"
tab: `_enzyme_cuts`, `_digest_with_enzymes`, `_make_synthetic_fragment`,
`_ligate_fragments`, `_close_circular`, `_rc_fragment`,
`_simulate_traditional_cloning`. The UI is exercised separately in
test_smoke.py.

These are pure unit tests: no Textual app, no async setup. Each function
is a deterministic transform on a small synthetic SeqRecord-equivalent
seq string + feature dict list.
"""
from __future__ import annotations

import pytest

import splicecraft as sc


# A synthetic 28 bp circular plasmid carrying one EcoRI site (GAATTC at 4..10)
# and one BamHI site (GGATCC at 18..24). Cuts:
#   EcoRI:  top=5,  bot=9   → 5' overhang AATT
#   BamHI:  top=19, bot=23  → 5' overhang GATC
TINY_PLASMID = "AAAAGAATTCAAAAAAAAGGATCCAAAA"


# ──────────────────────────────────────────────────────────────────────────────
# _enzyme_cuts
# ──────────────────────────────────────────────────────────────────────────────


class TestEnzymeCuts:
    def test_finds_palindromic_cut(self):
        cuts = sc._enzyme_cuts("AAAGAATTCAAA", ["EcoRI"], circular=False)
        assert len(cuts) == 1
        c = cuts[0]
        assert c["enzyme"] == "EcoRI"
        assert c["top"] == 4   # G^AATTC, recognition starts at 3
        assert c["bot"] == 8
        assert c["kind"] == "5'"
        assert c["overhang_seq"] == "AATT"

    def test_finds_three_prime_overhang(self):
        # KpnI: GGTAC^C (fwd=5, rev=1) → 3' overhang GTAC
        cuts = sc._enzyme_cuts("AAAGGTACCAAA", ["KpnI"], circular=False)
        assert len(cuts) == 1
        assert cuts[0]["kind"] == "3'"
        assert cuts[0]["overhang_seq"] == "GTAC"

    def test_finds_blunt_cut(self):
        # EcoRV: GAT^ATC (fwd=3, rev=3) → blunt
        cuts = sc._enzyme_cuts("AAAGATATCAAA", ["EcoRV"], circular=False)
        assert len(cuts) == 1
        assert cuts[0]["kind"] == "blunt"
        assert cuts[0]["overhang_seq"] == ""

    def test_returns_empty_for_unknown_enzyme(self):
        assert sc._enzyme_cuts("AAAA", ["NotARealEnzyme"]) == []

    def test_returns_empty_for_empty_seq(self):
        assert sc._enzyme_cuts("", ["EcoRI"]) == []

    def test_multiple_enzymes_sorted_by_position(self):
        cuts = sc._enzyme_cuts(TINY_PLASMID, ["BamHI", "EcoRI"], circular=True)
        assert [c["enzyme"] for c in cuts] == ["EcoRI", "BamHI"]
        assert cuts[0]["top"] < cuts[1]["top"]

    def test_circular_finds_origin_spanning_site(self):
        # Place GAATTC straddling the origin: TTCxxxxxxGAA. The 6 bp
        # recognition would be seq[-3:] + seq[:3] = "GAA" + "TTC" = "GAATTC".
        # _enzyme_cuts must find it via the augmented scan.
        seq = "TTCAAAAAAAAGAA"   # 14 bp; "GAATTC" at indices 11,12,13,0,1,2
        cuts = sc._enzyme_cuts(seq, ["EcoRI"], circular=True)
        assert len(cuts) == 1
        # Top cut at 11 + 1 = 12 (mod 14)
        assert cuts[0]["top"] == 12

    def test_linear_skips_origin_spanning_site(self):
        seq = "TTCAAAAAAAAGAA"
        cuts = sc._enzyme_cuts(seq, ["EcoRI"], circular=False)
        assert cuts == []

    def test_origin_wrapping_cut_overhang_is_correct(self):
        """Regression guard for 2026-05-05 fix: a cut whose top + bot
        positions straddle the origin (post-modulo, top > bot even
        though the cut is a 5' overhang) used to compute
        `seq[bot:top]` — a slice that can be the entire plasmid minus
        4 bp instead of the 4-bp overhang. Now the function uses
        pre-modulo values to detect wrap and emits `seq[lo:] + seq[:hi]`
        for the canonical 4-bp overhang."""
        # Place GAATTC straddling the origin: site spans [11..14, 0..1].
        # Top cut at (11+1)%14 = 12, bot cut at (11+5)%14 = 2 (mod 14).
        # Naive impl would have lo=2, hi=12 → "AAAAAAAAA" (10 bp).
        # Correct impl: overhang_len = 4, anchor at top=12, end=2 (wrap)
        # → seq[12:] + seq[:2] = "AA" + "TT" = "AATT".
        seq = "TTCAAAAAAAAAGAA"   # 15 bp; hmm, let's recompute exactly.
        # Use 14 bp: "TTCAAAAAAAAGAA" — site GAATTC at pos 11, 12, 13, 0, 1, 2.
        seq = "TTCAAAAAAAAGAA"   # 14 bp
        cuts = sc._enzyme_cuts(seq, ["EcoRI"], circular=True)
        assert len(cuts) == 1
        assert cuts[0]["overhang_seq"] == "AATT", (
            f"expected AATT for wrap cut, got {cuts[0]['overhang_seq']!r}"
        )
        # Kind detection: pre-modulo top < pre-modulo bot for fwd<rev
        # (5' overhang). The post-modulo values can flip apparent
        # ordering but kind must still report "5'".
        assert cuts[0]["kind"] == "5'"

    def test_dedupes_palindromic_double_emission(self):
        """Palindromic enzymes match on both strands at the same site;
        the function should report each physical cut exactly once."""
        cuts = sc._enzyme_cuts("AAAGAATTCAAA", ["EcoRI"], circular=False)
        # Even though EcoRI matches on top AND bottom strands at this
        # site, the result is one cut.
        assert len(cuts) == 1

    def test_non_palindromic_finds_reverse_strand_match(self):
        # NdeI: CA^TATG — palindrome (RC is also CATATG)
        # Use a non-palindromic enzyme: BsaI (GGTCTC, type IIS, fwd=7, rev=11)
        # On the reverse strand this RC's to GAGACC.
        seq = "AAAGAGACCAAAAAA"  # GAGACC on top = BsaI binding on reverse
        cuts = sc._enzyme_cuts(seq, ["BsaI"], circular=False)
        assert len(cuts) == 1
        assert cuts[0]["enzyme"] == "BsaI"


# ──────────────────────────────────────────────────────────────────────────────
# _digest_with_enzymes — fragment slicing
# ──────────────────────────────────────────────────────────────────────────────


class TestDigestWithEnzymes:
    def test_circular_two_cuts_yields_two_fragments(self):
        frags = sc._digest_with_enzymes(TINY_PLASMID, ["EcoRI", "BamHI"],
                                          circular=True)
        assert len(frags) == 2
        # Total bp should equal the original.
        assert sum(len(f["top_seq"]) for f in frags) == len(TINY_PLASMID)
        # Both fragments have 5' overhangs at both ends.
        for f in frags:
            assert f["left"]["kind"] == "5'"
            assert f["right"]["kind"] == "5'"

    def test_circular_zero_cuts_yields_one_uncut_fragment(self):
        # No EcoRI in the input
        seq = "AAAACCCCGGGG"
        frags = sc._digest_with_enzymes(seq, ["EcoRI"], circular=True)
        assert len(frags) == 1
        assert frags[0]["top_seq"] == seq
        assert frags[0]["left"]["kind"] == "linear"

    def test_linear_two_cuts_yields_three_fragments(self):
        seq = "AAAAGAATTCAAAAAAAAGGATCCAAAA"
        frags = sc._digest_with_enzymes(seq, ["EcoRI", "BamHI"],
                                          circular=False)
        assert len(frags) == 3
        # First fragment: linear left edge, EcoRI right edge.
        assert frags[0]["left"]["kind"]  == "linear"
        assert frags[0]["right"]["enzyme"] == "EcoRI"
        # Last fragment: BamHI left, linear right.
        assert frags[-1]["left"]["enzyme"] == "BamHI"
        assert frags[-1]["right"]["kind"]   == "linear"

    def test_round_trip_through_ligate_and_close(self):
        """Cut the plasmid → re-ligate two fragments → close circle.
        The closed top_seq must contain the original (rotated) as a
        substring of itself doubled."""
        frags = sc._digest_with_enzymes(TINY_PLASMID, ["EcoRI", "BamHI"],
                                          circular=True)
        joined  = sc._ligate_fragments(frags[0], frags[1])
        closed  = sc._close_circular(joined)
        assert closed is not None
        assert closed["circular"] is True
        # Doubling the original lets us match any rotation of the closed seq.
        doubled = TINY_PLASMID + TINY_PLASMID
        assert closed["top_seq"] in doubled

    def test_wrap_feature_splits_into_head_and_tail(self):
        """Regression guard for 2026-05-05 fix: a feature with end < start
        (origin-spanning, e.g., oriV) used to route through the slotting
        algorithm assuming start ≤ end and end up in the wrong fragment.
        Now wrap features pre-split into a tail [start, n) + head [0, end)
        so each half lands in the correct fragment."""
        # 28 bp circular plasmid. Cuts at top=5 (EcoRI) and top=19 (BamHI)
        # produce two fragments: [5..19) (14 bp) and [19..5) (14 bp wrap).
        # Wrap feature [22..3) covers positions 22..27 + 0..2.
        feats = [{"start": 22, "end": 3, "label": "wrap-feat", "strand": 1}]
        frags = sc._digest_with_enzymes(TINY_PLASMID, ["EcoRI", "BamHI"],
                                          circular=True, features=feats)
        assert len(frags) == 2
        # The wrap feature should appear in the wrap fragment (the one
        # whose left edge is BamHI). Its halves: tail=[22, 28) → local
        # [3, 9); head=[0, 3) → local [9, 12).
        wrap_frag = next(f for f in frags
                          if f["left"]["enzyme"] == "BamHI")
        labeled = [f for f in wrap_frag["features"]
                    if f.get("label") == "wrap-feat"]
        # Two halves, both tagged with `_wrap_origin_split`.
        assert len(labeled) == 2
        splits = {f["_wrap_origin_split"] for f in labeled}
        assert splits == {"tail", "head"}

    def test_features_get_slotted_into_correct_fragment(self):
        # Plasmid with one feature in the EcoRI..BamHI window (insert region)
        feats = [{"start": 8, "end": 15, "label": "ins-feat", "strand": 1}]
        frags = sc._digest_with_enzymes(TINY_PLASMID, ["EcoRI", "BamHI"],
                                          circular=True, features=feats)
        # The feature at 8..15 falls into the fragment whose top range
        # starts at the EcoRI cut (top=5) and runs to the BamHI cut (top=19).
        labeled = [f for f in frags if any(
            ft.get("label") == "ins-feat" for ft in f["features"])]
        assert len(labeled) == 1
        # And the local coords are shifted: 8-5=3 ... 15-5=10.
        ft = next(ft for ft in labeled[0]["features"]
                    if ft.get("label") == "ins-feat")
        assert ft["start"] == 3
        assert ft["end"]   == 10


# ──────────────────────────────────────────────────────────────────────────────
# _make_synthetic_fragment (modes b + c — PCR product / feature with tails)
# ──────────────────────────────────────────────────────────────────────────────


class TestMakeSyntheticFragment:
    def test_stamps_canonical_overhangs(self):
        frag = sc._make_synthetic_fragment(
            "GAGCATGAAACGGCCAAGTAA",
            enz_left="EcoRI", enz_right="BamHI",
            source_label="myPCR",
        )
        assert frag["left"]["overhang_seq"]  == "AATT"
        assert frag["left"]["enzyme"]        == "EcoRI"
        assert frag["right"]["overhang_seq"] == "GATC"
        assert frag["right"]["enzyme"]       == "BamHI"

    def test_three_prime_overhang_kind(self):
        frag = sc._make_synthetic_fragment(
            "AAA", enz_left="KpnI", enz_right="EcoRI",
        )
        assert frag["left"]["kind"]  == "3'"
        assert frag["right"]["kind"] == "5'"

    def test_blunt_kind(self):
        frag = sc._make_synthetic_fragment(
            "AAA", enz_left="EcoRV", enz_right="EcoRV",
        )
        assert frag["left"]["kind"]  == "blunt"
        assert frag["right"]["kind"] == "blunt"
        assert frag["left"]["overhang_seq"]  == ""
        assert frag["right"]["overhang_seq"] == ""

    def test_unknown_enzyme_raises(self):
        with pytest.raises(ValueError):
            sc._make_synthetic_fragment("AAA", enz_left="NotReal",
                                          enz_right="EcoRI")

    def test_type_iis_rejected_with_clear_message(self):
        """Regression guard for 2026-05-05 fix: BsaI / Esp3I / BsmBI
        cut OUTSIDE their recognition (`fwd_cut > site_len`), so the
        synthetic-fragment model can't know what the overhang bases
        will be — those depend on the surrounding context the user
        supplies. Used to silently produce an empty-overhang fragment
        that wouldn't ligate; now raises ValueError pointing the
        user at "From plasmid" mode."""
        with pytest.raises(ValueError) as exc:
            sc._make_synthetic_fragment("AAACCCGGG",
                                          enz_left="BsaI",
                                          enz_right="EcoRI")
        assert "Type IIS" in str(exc.value)
        # Both ends checked — also rejects when the right enzyme is IIS.
        with pytest.raises(ValueError) as exc:
            sc._make_synthetic_fragment("AAACCCGGG",
                                          enz_left="EcoRI",
                                          enz_right="Esp3I")
        assert "Type IIS" in str(exc.value)


# ──────────────────────────────────────────────────────────────────────────────
# _ligate_fragments + _close_circular (compatibility predicate)
# ──────────────────────────────────────────────────────────────────────────────


class TestLigateAndClose:
    def test_compatible_palindromic_ligates(self):
        a = sc._make_synthetic_fragment("AAA", enz_left="EcoRV",
                                          enz_right="EcoRI")
        b = sc._make_synthetic_fragment("CCC", enz_left="EcoRI",
                                          enz_right="EcoRV")
        merged = sc._ligate_fragments(a, b)
        assert merged is not None
        assert merged["top_seq"] == "AAACCC"
        # Outer ends preserved.
        assert merged["left"]  == a["left"]
        assert merged["right"] == b["right"]

    def test_incompatible_kinds_dont_ligate(self):
        # 5' overhang can't ligate to 3' overhang even if the bases match.
        a = sc._make_synthetic_fragment("AAA", enz_left="EcoRV",
                                          enz_right="EcoRI")
        # Hand-craft a 3' end with the same sequence as EcoRI's 5' overhang.
        b = {
            "top_seq": "CCC",
            "left":  {"overhang_seq": "AATT", "kind": "3'", "enzyme": "fake"},
            "right": {"overhang_seq": "", "kind": "linear", "enzyme": ""},
            "features": [], "source_label": "",
        }
        assert sc._ligate_fragments(a, b) is None

    def test_linear_edge_never_ligates(self):
        a = sc._make_synthetic_fragment("AAA", enz_left="EcoRI",
                                          enz_right="EcoRI")
        b = {
            "top_seq": "CCC",
            "left":  {"overhang_seq": "", "kind": "linear", "enzyme": ""},
            "right": {"overhang_seq": "", "kind": "linear", "enzyme": ""},
            "features": [], "source_label": "",
        }
        assert sc._ligate_fragments(a, b) is None

    def test_close_circular_palindromic_succeeds(self):
        # Synthetic fragment with EcoRI on both ends → ligates to a circle.
        frag = sc._make_synthetic_fragment("AAACCC", enz_left="EcoRI",
                                              enz_right="EcoRI")
        closed = sc._close_circular(frag)
        assert closed is not None
        assert closed["circular"] is True

    def test_close_circular_mismatched_ends_fails(self):
        frag = sc._make_synthetic_fragment("AAA", enz_left="EcoRI",
                                              enz_right="BamHI")
        assert sc._close_circular(frag) is None


# ──────────────────────────────────────────────────────────────────────────────
# _rc_fragment
# ──────────────────────────────────────────────────────────────────────────────


class TestRcFragment:
    def test_top_seq_reverse_complemented(self):
        frag = sc._make_synthetic_fragment(
            "ATGAAACG", enz_left="EcoRI", enz_right="BamHI",
        )
        rc = sc._rc_fragment(frag)
        assert rc["top_seq"] == "CGTTTCAT"

    def test_left_right_swapped(self):
        frag = sc._make_synthetic_fragment(
            "AAA", enz_left="EcoRI", enz_right="BamHI",
        )
        rc = sc._rc_fragment(frag)
        # After RC: the EcoRI end (was left) is now on the right; BamHI is on left.
        assert rc["left"]["enzyme"]  == "BamHI"
        assert rc["right"]["enzyme"] == "EcoRI"
        # Overhangs are RC'd; both palindromic so values unchanged.
        assert rc["left"]["overhang_seq"]  == "GATC"
        assert rc["right"]["overhang_seq"] == "AATT"

    def test_features_flipped_correctly(self):
        frag = sc._make_synthetic_fragment(
            "AAACCCGGG",  # 9 bp
            enz_left="EcoRI", enz_right="BamHI",
            features=[{"start": 1, "end": 4, "label": "x", "strand": 1}],
        )
        rc = sc._rc_fragment(frag)
        # 9 - end (4) → 5; 9 - start (1) → 8
        ft = rc["features"][0]
        assert ft["start"]  == 5
        assert ft["end"]    == 8
        assert ft["strand"] == -1


# ──────────────────────────────────────────────────────────────────────────────
# _simulate_traditional_cloning (full vector + insert pipeline)
# ──────────────────────────────────────────────────────────────────────────────


class TestSimulateTraditionalCloning:
    def test_directional_cloning_only_one_orientation_works(self):
        # EcoRI + BamHI: different non-cross-compatible overhangs → directional.
        insert = sc._make_synthetic_fragment(
            "GAGCATGAAACGGCCAAGTAA",
            enz_left="EcoRI", enz_right="BamHI",
            source_label="insert",
        )
        # Vector with EcoRI on right, BamHI on left so the insert ligates
        # forward.
        vector = {
            "top_seq": "TGGCCCC" * 10,   # 70 bp dummy backbone
            "left":  {"overhang_seq": "GATC", "kind": "5'", "enzyme": "BamHI"},
            "right": {"overhang_seq": "AATT", "kind": "5'", "enzyme": "EcoRI"},
            "features":     [],
            "source_label": "vector",
        }
        result = sc._simulate_traditional_cloning(insert, vector)
        # Only forward should be compatible: vector.right(AATT) ↔
        # insert.left(AATT) ✓; vector.left(GATC) ↔ insert.right(GATC) ✓.
        assert result["forward"]["compatible"] is True
        assert result["reverse"]["compatible"] is False
        # Warnings include a directional-cloning message.
        assert any("Directional" in w for w in result["warnings"])
        assert result["errors"] == []

    def test_ambiguous_orientation_emits_warning(self):
        # Single-enzyme palindromic cut: insert can ligate either way.
        insert = sc._make_synthetic_fragment(
            "GAGCATGAAACGGCCAAGTAA",
            enz_left="EcoRI", enz_right="EcoRI",
            source_label="insert",
        )
        vector = {
            "top_seq": "TGGCCCC" * 10,
            "left":  {"overhang_seq": "AATT", "kind": "5'", "enzyme": "EcoRI"},
            "right": {"overhang_seq": "AATT", "kind": "5'", "enzyme": "EcoRI"},
            "features":     [],
            "source_label": "vector",
        }
        result = sc._simulate_traditional_cloning(insert, vector)
        assert result["forward"]["compatible"] is True
        assert result["reverse"]["compatible"] is True
        assert any("Ambiguous" in w for w in result["warnings"])

    def test_no_compatibility_raises_error(self):
        insert = sc._make_synthetic_fragment(
            "AAA", enz_left="EcoRI", enz_right="EcoRI",
        )
        vector = {
            "top_seq": "CCC",
            "left":  {"overhang_seq": "GATC", "kind": "5'", "enzyme": "BamHI"},
            "right": {"overhang_seq": "GATC", "kind": "5'", "enzyme": "BamHI"},
            "features":     [],
            "source_label": "vector",
        }
        result = sc._simulate_traditional_cloning(insert, vector)
        assert result["forward"]["compatible"] is False
        assert result["reverse"]["compatible"] is False
        assert any("Neither orientation" in e for e in result["errors"])

    def test_compat_compatible_overhang_enzymes(self):
        """BamHI (GATC) and BglII (GATC) produce the same canonical
        overhang — fragments cut with either should ligate."""
        insert = sc._make_synthetic_fragment(
            "AAA", enz_left="BamHI", enz_right="BamHI",
        )
        vector = {
            "top_seq": "CCC",
            "left":  {"overhang_seq": "GATC", "kind": "5'", "enzyme": "BglII"},
            "right": {"overhang_seq": "GATC", "kind": "5'", "enzyme": "BglII"},
            "features":     [],
            "source_label": "vector",
        }
        result = sc._simulate_traditional_cloning(insert, vector)
        assert result["forward"]["compatible"] is True

    def test_excise_fragment_pair_circular_two_cuts(self):
        frags, err = sc._excise_fragment_pair(
            TINY_PLASMID, ["EcoRI", "BamHI"], circular=True,
        )
        assert err is None
        assert len(frags) == 2

    def test_excise_fragment_pair_no_cuts_errors_with_message(self):
        frags, err = sc._excise_fragment_pair(
            "AAACCCGGG", ["EcoRI"], circular=True,
        )
        assert err is not None
        assert "no cut sites" in err["error"]

    def test_excise_fragment_pair_one_cut_circular_errors(self):
        # Plasmid with exactly one EcoRI: can't excise an insert.
        seq = "AAAGAATTCAAAACCCCGGGGTTTTAAAA"
        frags, err = sc._excise_fragment_pair(
            seq, ["EcoRI"], circular=True,
        )
        assert err is not None
        assert "≥2 cuts" in err["error"]

    def test_excise_fragment_pair_three_cuts_circular_errors(self):
        """Regression guard for 2026-05-06 fix: ≥3 cuts on a circular
        plasmid is ambiguous — the helper used to silently return the
        full fragment list with err=None, and a future caller blindly
        picking ``fragments[0:2]`` would ship a wrong product. Helper
        now surfaces a clear error so the user can pick a different
        enzyme pair."""
        # Three EcoRI sites on a circular plasmid.
        seq = ("GAATTC" + "A" * 10 +
               "GAATTC" + "A" * 10 +
               "GAATTC" + "A" * 10)
        frags, err = sc._excise_fragment_pair(
            seq, ["EcoRI"], circular=True,
        )
        assert err is not None
        assert "exactly 2" in err["error"]
        assert "3" in err["error"]   # tells the user how many were found
        # Caller must surface this; the fragment list is still returned
        # for diagnostic display (don't break the existing return shape).
        assert isinstance(frags, list)

    @pytest.mark.parametrize("n_sites", [3, 4, 5, 7])
    def test_excise_fragment_pair_many_cuts_circular_errors(self, n_sites):
        """Sacred invariant #25 generalised: ANY count >2 must error.
        Parameterised so a regression that special-cases 3 (e.g.
        `if n_cuts == 3:`) can't slip through against 4+. Each test
        builds a circular sequence with `n_sites` EcoRI sites + 10 bp
        spacers."""
        seq = "".join("GAATTC" + "A" * 10 for _ in range(n_sites))
        frags, err = sc._excise_fragment_pair(
            seq, ["EcoRI"], circular=True,
        )
        assert err is not None, (
            f"{n_sites} cuts on circular should error; got err=None"
        )
        assert "exactly 2" in err["error"]
        assert str(n_sites) in err["error"]

    def test_excise_fragment_pair_two_enzymes_three_total_cuts_errors(self):
        """Invariant #25: total cut count is what matters, not per-
        enzyme count. EcoRI×2 + BamHI×1 = 3 total → must error.
        Catches a hypothetical regression that only checks per-enzyme
        counts."""
        # Two EcoRI + one BamHI, all in a circular sequence.
        seq = (
            "GAATTC" + "A" * 10 +
            "GAATTC" + "A" * 10 +
            "GGATCC" + "A" * 10
        )
        frags, err = sc._excise_fragment_pair(
            seq, ["EcoRI", "BamHI"], circular=True,
        )
        assert err is not None
        assert "exactly 2" in err["error"]
        # Per-enzyme breakdown is in the message.
        assert "EcoRI" in err["error"]
        assert "BamHI" in err["error"]

    def test_excise_fragment_pair_linear_three_cuts_does_not_error(self):
        """Invariant #25's strict 2-cut requirement applies only to
        CIRCULAR plasmids. Linear sequences with N cuts produce N+1
        fragments naturally, no ambiguity in selecting "the insert"
        because the caller knows linear has ends. The check must NOT
        false-positive on the linear path."""
        seq = (
            "GAATTC" + "A" * 10 +
            "GAATTC" + "A" * 10 +
            "GAATTC" + "A" * 10
        )
        frags, err = sc._excise_fragment_pair(
            seq, ["EcoRI"], circular=False,
        )
        # Linear is allowed any cut count without the >2 hard-stop.
        # (`err` may still be set for OTHER reasons — e.g. zero cuts —
        # but should NOT be the "exactly 2" message.)
        if err is not None:
            assert "exactly 2" not in err["error"], (
                f"linear path should NOT trigger exactly-2 hard-stop; "
                f"got err={err['error']!r}"
            )

    # ──────────────────────────────────────────────────────────────────
    # End-to-end UI tests — open the modal, switch tabs, simulate.
    # ──────────────────────────────────────────────────────────────────

    async def test_constructor_opens_with_tabbed_content(
            self, tiny_record, isolated_library):
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            from textual.widgets import TabbedContent
            tabs = modal.query_one("#ctor-tabs", TabbedContent)
            # Three tabs registered (2026-05-07): the single modular
            # tab was split into per-grammar tabs (Golden Braid,
            # MoClo Plant) so each gets its own parts palette filtered
            # by the active grammar.
            tab_ids = [p.id for p in modal.query("TabPane")]
            assert "ctor-tab-traditional" in tab_ids
            assert "ctor-tab-gb_l0"       in tab_ids
            assert "ctor-tab-moclo_plant" in tab_ids
            # Traditional opens by default.
            assert tabs.active == "ctor-tab-traditional"

    async def test_traditional_pane_pcr_mode_simulate(
            self, tiny_record, isolated_library):
        """End-to-end: user pastes a PCR product + picks two enzymes
        + clicks Simulate. The result panel should show the
        forward/reverse summary."""
        # Seed a vector entry into the isolated library so the vector
        # picker has a row to select.
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio import SeqIO
        import io
        # Vector with EcoRI + BamHI sites flanking a stuffer; the larger
        # piece (excluding the stuffer) becomes the backbone after digest.
        vec = SeqRecord(
            Seq("AAAAAAAAGAATTCSTUFFERSTUFFERGGATCCTTTTTTTT"
                .replace("STUFFER", "AAAAA")),
            id="pVec", name="pVec",
            annotations={"molecule_type": "DNA", "topology": "circular"},
        )
        buf = io.StringIO()
        SeqIO.write(vec, buf, "genbank")
        sc._save_library([{
            "id": "pVec", "name": "pVec",
            "gb_text": buf.getvalue(),
            "size": len(vec.seq),
        }])
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            await pilot.pause(0.05)
            # Switch to Traditional tab.
            from textual.widgets import (TabbedContent, RadioButton,
                                          Input, TextArea, Select,
                                          DataTable)
            tabs = modal.query_one("#ctor-tabs", TabbedContent)
            tabs.active = "ctor-tab-traditional"
            await pilot.pause()
            await pilot.pause(0.05)
            # Switch insert source mode to "PCR product" by pressing the
            # third radio button directly. `RadioSet.action_next_button`
            # only navigates focus; it doesn't change the selection.
            modal.query_one("#trad-mode-pcr", RadioButton).value = True
            await pilot.pause()
            # Fill the PCR fields.
            modal.query_one("#trad-pcr-name", Input).value = "myInsert"
            ta = modal.query_one("#trad-pcr-seq", TextArea)
            ta.text = "GAGCATGAAACGGCCAAGTAA"
            # Pick the vector row. The display sorts entries by
            # `_natural_sort_key` (`pVec` lands before `TEST001` because
            # 'pvec' < 'test' lowercased), so compute the target row
            # against the SORTED list — matching what the user sees and
            # what `_record_for_table_row` resolves on click.
            vt = modal.query_one("#trad-vector-table", DataTable)
            entries = sorted(
                (e for e in sc._load_library() if isinstance(e, dict)),
                key=lambda e: sc._natural_sort_key(
                    e.get("name") or e.get("id") or ""
                ),
            )
            target_idx = next(
                (i for i, e in enumerate(entries) if e.get("name") == "pVec"),
                -1,
            )
            assert target_idx >= 0, (
                f"pVec not found in library; entries: "
                f"{[e.get('name') for e in entries]}")
            vt.move_cursor(row=target_idx)
            await pilot.pause()
            assert vt.cursor_row == target_idx
            # E1 = EcoRI (already default sorted-first or prompt); set explicitly.
            modal.query_one("#trad-enzyme-1", Select).value = "EcoRI"
            modal.query_one("#trad-enzyme-2", Select).value = "BamHI"
            await pilot.pause()
            # Simulate.
            await pilot.click("#btn-trad-simulate")
            await pilot.pause()
            # The pane caches the last simulation's products on
            # `_fwd_product` / `_rev_product`. Both should be populated
            # dicts after a successful simulate.
            pane = modal.query_one("#ctor-trad-pane",
                                     sc.TraditionalCloningPane)
            assert isinstance(pane._fwd_product, dict)
            assert isinstance(pane._rev_product, dict)
            # Both products carry the assembled top_seq + a feature list.
            assert "top_seq" in pane._fwd_product
            assert pane._fwd_product["top_seq"]   # non-empty
            assert "compatible" in pane._fwd_product

    async def test_traditional_pane_save_forward_to_library(
            self, tiny_record, isolated_library):
        """After a successful simulate the Save Forward button is
        enabled; clicking it appends a new entry to the library named
        `trad-fwd[-N]`. The persisted GenBank text round-trips."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio import SeqIO
        import io
        vec = SeqRecord(
            Seq("AAAAAAAAGAATTCAAAAAAAAAAGGATCCTTTTTTTT"),
            id="pVec", name="pVec",
            annotations={"molecule_type": "DNA", "topology": "circular"},
        )
        buf = io.StringIO()
        SeqIO.write(vec, buf, "genbank")
        sc._save_library([{"id": "pVec", "name": "pVec",
                            "gb_text": buf.getvalue(),
                            "size": len(vec.seq)}])
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            from textual.widgets import (TabbedContent, RadioButton,
                                          Input, TextArea, Select,
                                          DataTable, Button)
            modal.query_one("#ctor-tabs", TabbedContent).active = \
                "ctor-tab-traditional"
            await pilot.pause()
            await pilot.pause(0.05)
            modal.query_one("#trad-mode-pcr", RadioButton).value = True
            await pilot.pause()
            modal.query_one("#trad-pcr-name", Input).value = "myInsert"
            modal.query_one("#trad-pcr-seq",  TextArea).text = (
                "GAGCATGAAACGGCCAAGTAA")
            # Display sorts entries by `_natural_sort_key`; compute
            # target row against the same order so the cursor lands on
            # what the user sees as pVec (not the disk-order pVec).
            entries = sorted(
                (e for e in sc._load_library() if isinstance(e, dict)),
                key=lambda e: sc._natural_sort_key(
                    e.get("name") or e.get("id") or ""
                ),
            )
            target = next(i for i, e in enumerate(entries)
                          if e.get("name") == "pVec")
            modal.query_one("#trad-vector-table",
                             DataTable).move_cursor(row=target)
            await pilot.pause()
            modal.query_one("#trad-enzyme-1", Select).value = "EcoRI"
            modal.query_one("#trad-enzyme-2", Select).value = "BamHI"
            await pilot.pause()
            await pilot.click("#btn-trad-simulate")
            await pilot.pause()
            # Save Forward should be enabled (the directional cloning
            # case made forward compatible).
            save_fwd = modal.query_one("#btn-trad-save-fwd", Button)
            save_rev = modal.query_one("#btn-trad-save-rev", Button)
            # Exactly one of them is enabled (directional cloning).
            assert save_fwd.disabled is not save_rev.disabled
            # Trigger via `Button.press()` instead of `pilot.click()` —
            # the click coordinates fall inside a ScrollableContainer
            # whose hit-testing isn't reliable across Textual versions.
            (save_fwd if not save_fwd.disabled else save_rev).press()
            await pilot.pause()
            # Library now contains an entry whose name starts with "trad-".
            after = sc._load_library()
            trad_entries = [e for e in after
                              if isinstance(e, dict)
                              and str(e.get("name", "")).startswith("trad-")]
            assert len(trad_entries) == 1
            saved = trad_entries[0]
            assert saved.get("size", 0) > 0
            assert "gb_text" in saved
            assert "LOCUS" in saved["gb_text"]
            # Regression guard for 2026-05-05: after a successful Save,
            # both Save buttons must re-disable so a stray double-click
            # doesn't create `trad-fwd-2` as an exact duplicate. The user
            # has to re-Simulate to save again with a fresh increment.
            assert save_fwd.disabled and save_rev.disabled
            pane = modal.query_one("#ctor-trad-pane",
                                     sc.TraditionalCloningPane)
            assert pane._fwd_product is None
            assert pane._rev_product is None

    async def test_traditional_pane_save_records_history_xml(
            self, tiny_record, isolated_library):
        """Regression guard for 2026-05-06 Phase 4b wiring:
        a successful Traditional cloning save attaches a CommercialSaaS-
        compatible `<HistoryTree>` to the new library entry. The
        XML must parse via `_parse_commercialsaas_history`, name the new
        plasmid as the top node, and link both source plasmids as
        parent fragments."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio import SeqIO
        import io
        # Set up a vector with EcoRI + BamHI flanking a stuffer.
        vec = SeqRecord(
            Seq("AAAAAAAAGAATTCAAAAAAAAAAGGATCCTTTTTTTT"),
            id="pVec", name="pVec",
            annotations={"molecule_type": "DNA", "topology": "circular"},
        )
        buf = io.StringIO()
        SeqIO.write(vec, buf, "genbank")
        sc._save_library([{"id": "pVec", "name": "pVec",
                            "gb_text": buf.getvalue(),
                            "size": len(vec.seq)}])
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            from textual.widgets import (TabbedContent, RadioButton,
                                          Input, TextArea, Select,
                                          DataTable, Button)
            modal.query_one("#ctor-tabs", TabbedContent).active = \
                "ctor-tab-traditional"
            await pilot.pause(); await pilot.pause(0.05)
            modal.query_one("#trad-mode-pcr", RadioButton).value = True
            await pilot.pause()
            modal.query_one("#trad-pcr-name", Input).value = "myInsert"
            modal.query_one("#trad-pcr-seq",  TextArea).text = (
                "GAGCATGAAACGGCCAAGTAA")
            # Display sorts entries by `_natural_sort_key`; compute
            # target row against the same order so the cursor lands on
            # what the user sees as pVec (not the disk-order pVec).
            entries = sorted(
                (e for e in sc._load_library() if isinstance(e, dict)),
                key=lambda e: sc._natural_sort_key(
                    e.get("name") or e.get("id") or ""
                ),
            )
            target = next(i for i, e in enumerate(entries)
                          if e.get("name") == "pVec")
            modal.query_one("#trad-vector-table",
                             DataTable).move_cursor(row=target)
            await pilot.pause()
            modal.query_one("#trad-enzyme-1", Select).value = "EcoRI"
            modal.query_one("#trad-enzyme-2", Select).value = "BamHI"
            await pilot.pause()
            await pilot.click("#btn-trad-simulate")
            await pilot.pause()
            save_fwd = modal.query_one("#btn-trad-save-fwd", Button)
            save_rev = modal.query_one("#btn-trad-save-rev", Button)
            (save_fwd if not save_fwd.disabled else save_rev).press()
            await pilot.pause()
            # Find the saved trad-* entry; history_xml MUST be present.
            saved = next((e for e in sc._load_library()
                            if isinstance(e, dict)
                            and str(e.get("name", "")).startswith("trad-")),
                          None)
            assert saved is not None
            assert "history_xml" in saved, (
                f"history not attached to entry {saved.get('name')!r}; "
                f"keys: {sorted(saved)}")
            # Parse it — root should name the new product, with two
            # parent nodes (the synthesised PCR insert + the pVec
            # vector).
            root = sc._parse_commercialsaas_history(saved["history_xml"])
            assert root is not None
            assert saved["name"] in root.name   # "trad-fwd.dna" etc.
            assert root.operation == "insertFragment"
            parent_names = [p.name for p in root.parents]
            # Insert + vector both attached as parents.
            assert any("PCR-product" in pn or "myInsert" in pn
                        for pn in parent_names), parent_names
            assert any("pVec" in pn for pn in parent_names), parent_names
            # Regenerated sites carry both enzymes used.
            site_names = [s["name"] for s in root.regenerated_sites]
            assert set(site_names) == {"EcoRI", "BamHI"}
            # Input summary marks which orientation was saved.
            ops = [s["manipulation"] for s in root.input_summaries]
            assert any(op in ("ligateFwd", "ligateRev") for op in ops)

    async def test_save_buttons_redisable_on_input_change(
            self, tiny_record, isolated_library):
        """Regression guard for 2026-05-05 fix: after a successful
        Simulate the Save buttons enable, but if the user then
        changes any input (enzyme, source row, mode, PCR name) the
        cached product is stale — Save must re-disable until the
        user re-Simulates."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio import SeqIO
        import io
        vec = SeqRecord(
            Seq("AAAAAAAAGAATTCAAAAAAAAAAGGATCCTTTTTTTT"),
            id="pVec", name="pVec",
            annotations={"molecule_type": "DNA", "topology": "circular"},
        )
        buf = io.StringIO()
        SeqIO.write(vec, buf, "genbank")
        sc._save_library([{"id": "pVec", "name": "pVec",
                            "gb_text": buf.getvalue(),
                            "size": len(vec.seq)}])
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            from textual.widgets import (TabbedContent, RadioButton,
                                          Input, TextArea, Select,
                                          DataTable, Button)
            modal.query_one("#ctor-tabs", TabbedContent).active = \
                "ctor-tab-traditional"
            await pilot.pause(); await pilot.pause(0.05)
            modal.query_one("#trad-mode-pcr", RadioButton).value = True
            await pilot.pause()
            modal.query_one("#trad-pcr-name", Input).value = "x"
            modal.query_one("#trad-pcr-seq",  TextArea).text = (
                "GAGCATGAAACGGCCAAGTAA")
            # Display sorts entries by `_natural_sort_key`; compute
            # target row against the same order so the cursor lands on
            # what the user sees as pVec (not the disk-order pVec).
            entries = sorted(
                (e for e in sc._load_library() if isinstance(e, dict)),
                key=lambda e: sc._natural_sort_key(
                    e.get("name") or e.get("id") or ""
                ),
            )
            target = next(i for i, e in enumerate(entries)
                          if e.get("name") == "pVec")
            modal.query_one("#trad-vector-table",
                             DataTable).move_cursor(row=target)
            await pilot.pause()
            modal.query_one("#trad-enzyme-1", Select).value = "EcoRI"
            modal.query_one("#trad-enzyme-2", Select).value = "BamHI"
            await pilot.pause()
            await pilot.click("#btn-trad-simulate")
            await pilot.pause()
            save_fwd = modal.query_one("#btn-trad-save-fwd", Button)
            save_rev = modal.query_one("#btn-trad-save-rev", Button)
            # At least one Save is enabled after a successful sim.
            assert not (save_fwd.disabled and save_rev.disabled)
            # Now change an enzyme — both Save buttons re-disable.
            modal.query_one("#trad-enzyme-2", Select).value = "HindIII"
            await pilot.pause()
            assert save_fwd.disabled and save_rev.disabled

    async def test_traditional_pane_simulate_without_source_errors(
            self, tiny_record, isolated_library):
        """Hitting Simulate in plasmid mode without picking an insert
        plasmid surfaces a clear error rather than crashing. Library
        starts empty (the autouse fixture wipes it) so the source
        DataTable has no rows."""
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            from textual.widgets import TabbedContent
            modal.query_one("#ctor-tabs", TabbedContent).active = \
                "ctor-tab-traditional"
            await pilot.pause()
            # Click Simulate in default (plasmid) mode without any
            # plasmid in the library — should set a red error message
            # and leave the cached products empty.
            await pilot.click("#btn-trad-simulate")
            await pilot.pause()
            pane = modal.query_one("#ctor-trad-pane",
                                     sc.TraditionalCloningPane)
            # No products should be cached on a failed simulate.
            assert pane._fwd_product is None
            assert pane._rev_product is None

    def test_features_carry_through_with_correct_offsets(self):
        # Insert with one feature at positions 2..5 (relative to insert).
        insert = sc._make_synthetic_fragment(
            "AAACCCGGG", enz_left="EcoRI", enz_right="BamHI",
            features=[{"start": 2, "end": 5, "label": "in-feat", "strand": 1}],
        )
        vector = {
            "top_seq": "T" * 20,
            "left":  {"overhang_seq": "GATC", "kind": "5'", "enzyme": "BamHI"},
            "right": {"overhang_seq": "AATT", "kind": "5'", "enzyme": "EcoRI"},
            "features": [{"start": 5, "end": 10, "label": "vec-feat",
                            "strand": 1}],
            "source_label": "vector",
        }
        result = sc._simulate_traditional_cloning(insert, vector)
        fwd_feats = result["forward"]["features"]
        # Vector feat at 5..10 stays put; insert feat at 2..5 shifts by 20.
        labels = {f["label"]: (f["start"], f["end"]) for f in fwd_feats}
        assert labels["vec-feat"] == (5, 10)
        assert labels["in-feat"]  == (22, 25)


# ═══════════════════════════════════════════════════════════════════════════════
# Constructor modal — multi-grammar tabs (2026-05-07)
# ═══════════════════════════════════════════════════════════════════════════════
#
# The Constructor was refactored from a single Modular tab (gb_l0
# only) into per-grammar tabs (Golden Braid + MoClo Plant) plus the
# pre-existing Traditional tab. Each modular tab pulls its parts
# palette from `parts_bin.json` filtered by the tab's grammar id
# when the `constructor_filter_by_grammar` setting is on (default).
# These tests cover the wiring: tabs exist, palette filters, palette
# reflects user parts, per-grammar entry vector is independent.

import pytest as _ctor_pytest


@_ctor_pytest.fixture
def isolated_parts_bin(tmp_path, monkeypatch):
    """Redirect `_PARTS_BIN_FILE` to a tmp path so the Constructor
    palette tests don't touch the real parts_bin.json. Mirrors the
    fixture in test_domesticator.py."""
    tmp_bin = tmp_path / "parts_bin.json"
    monkeypatch.setattr(sc, "_PARTS_BIN_FILE", tmp_bin)
    monkeypatch.setattr(sc, "_parts_bin_cache", None)
    return tmp_bin


class TestConstructorMultiGrammarTabs:
    """The Constructor exposes Traditional + Golden Braid + MoClo
    Plant tabs. Each modular tab uses its own grammar context for
    palette filtering, validation, and entry-vector banner."""

    def test_palette_helper_excludes_builtin_catalog(self, isolated_parts_bin):
        """Regression guard for 2026-05-07: the palette must NOT
        include the built-in `_GB_L0_PARTS` catalog rows. Those are
        placeholder entries with no real sequence — they can't
        actually assemble, so showing them as palette options is
        misleading. An empty parts bin → an empty palette is the
        honest state."""
        # Empty parts bin → empty palette under either filter setting.
        sc._save_parts_bin([])
        for filter_enabled in (True, False):
            rows = sc._palette_rows_for_grammar(
                "gb_l0", filter_enabled=filter_enabled,
            )
            assert rows == [], (
                f"Empty parts bin must produce empty palette "
                f"(filter={filter_enabled}); got {len(rows)} rows"
            )
        # Even with the gb_l0 grammar's catalog populated, none of
        # the catalog names should leak into the palette.
        names_with_no_user_parts = {
            r[0] for r in sc._palette_rows_for_grammar(
                "gb_l0", filter_enabled=True,
            )
        }
        catalog_names = {row[0] for row in sc._GB_L0_PARTS}
        assert not (names_with_no_user_parts & catalog_names), (
            "Built-in catalog names leaked into the palette: "
            f"{names_with_no_user_parts & catalog_names}"
        )

    def test_palette_helper_filters_by_grammar(self, isolated_parts_bin):
        # Save two parts with different grammar ids; the helper
        # filtered by grammar should only return the matching one.
        sc._save_parts_bin([
            {
                "name": "myProm", "type": "Promoter", "position": "Pos 1",
                "oh5": "GGAG", "oh3": "TGAC", "grammar": "gb_l0",
                "sequence": "ACGT" * 10,
            },
            {
                "name": "myMocloProm", "type": "Promoter",
                "position": "Pos 1", "oh5": "GGAG", "oh3": "AATG",
                "grammar": "moclo_plant", "sequence": "ACGT" * 10,
            },
        ])
        gb_rows = sc._palette_rows_for_grammar(
            "gb_l0", filter_enabled=True,
        )
        names = {r[0] for r in gb_rows}
        assert "myProm" in names
        assert "myMocloProm" not in names
        moclo_rows = sc._palette_rows_for_grammar(
            "moclo_plant", filter_enabled=True,
        )
        names = {r[0] for r in moclo_rows}
        assert "myMocloProm" in names
        assert "myProm" not in names

    def test_palette_helper_unfiltered_shows_all_user_parts(
            self, isolated_parts_bin):
        sc._save_parts_bin([
            {
                "name": "myProm", "type": "Promoter", "position": "Pos 1",
                "oh5": "GGAG", "oh3": "TGAC", "grammar": "gb_l0",
                "sequence": "ACGT" * 10,
            },
            {
                "name": "myMocloProm", "type": "Promoter",
                "position": "Pos 1", "oh5": "GGAG", "oh3": "AATG",
                "grammar": "moclo_plant", "sequence": "ACGT" * 10,
            },
        ])
        rows = sc._palette_rows_for_grammar(
            "gb_l0", filter_enabled=False,
        )
        names = {r[0] for r in rows}
        assert "myProm" in names
        assert "myMocloProm" in names

    def test_grammar_tu_overhangs_derives_from_positions(self):
        # gb_l0: first position oh5=GGAG (Promoter), last oh3=CGCT (Term).
        gb_l0 = sc._BUILTIN_GRAMMARS["gb_l0"]
        assert sc._grammar_tu_overhangs(gb_l0) == ("GGAG", "CGCT")
        # MoClo Plant: first oh5=GGAG, last oh3=CGCT (same TU
        # boundaries by Engler 2014 convention, different junctions).
        moclo = sc._BUILTIN_GRAMMARS["moclo_plant"]
        assert sc._grammar_tu_overhangs(moclo) == ("GGAG", "CGCT")

    def test_grammar_pos_slots_includes_cds_ns_alias(self):
        gb_l0 = sc._BUILTIN_GRAMMARS["gb_l0"]
        slots = sc._grammar_pos_slots(gb_l0)
        # GB 2.0 expanded grammar: Promoter(1), Promoter-only(2),
        # 5' UTR(3), CDS(4), ...  — CDS is at pos 4 not 3 since the
        # expansion split Promoter into combined + separate variants.
        assert slots["CDS"] == 4
        # CDS-NS shares the CDS slot via alias-fallback.
        assert slots["CDS-NS"] == 4

    async def test_constructor_modal_has_three_tabs(
            self, tiny_record, isolated_library):
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            await pilot.pause(0.05)
            tab_ids = [p.id for p in modal.query("TabPane")]
            assert "ctor-tab-traditional" in tab_ids
            assert "ctor-tab-gb_l0"       in tab_ids
            assert "ctor-tab-moclo_plant" in tab_ids
            # Each grammar tab has its own palette + lane.
            from textual.widgets import DataTable
            assert modal.query_one("#ctor-palette-gb_l0", DataTable)
            assert modal.query_one("#ctor-lane-gb_l0",    DataTable)
            assert modal.query_one("#ctor-palette-moclo_plant", DataTable)
            assert modal.query_one("#ctor-lane-moclo_plant",    DataTable)

    async def test_modular_tab_palette_reflects_parts_bin(
            self, tiny_record, isolated_library, isolated_parts_bin,
    ):
        # User saves a Golden Braid CDS + a MoClo CDS. The GB
        # tab's palette should show only the GB part by default
        # (filter ON); the MoClo tab shows only the MoClo part.
        sc._save_parts_bin([
            {
                "name": "MyGBgene", "type": "CDS", "position": "Pos 3-4",
                "oh5": "AATG", "oh3": "GCTT", "grammar": "gb_l0",
                "sequence": "ATG" + "AAA" * 30 + "TAA",
            },
            {
                "name": "MyMocloGene", "type": "CDS",
                "position": "Pos 3", "oh5": "AGGT", "oh3": "GCTT",
                "grammar": "moclo_plant",
                "sequence": "ATG" + "GGG" * 30 + "TAA",
            },
        ])
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            await pilot.pause(0.05)
            gb_names = {r[0] for r in modal._palette_rows.get("gb_l0", [])}
            mc_names = {r[0] for r in modal._palette_rows.get("moclo_plant", [])}
            assert "MyGBgene"     in gb_names
            assert "MyMocloGene"  not in gb_names
            assert "MyMocloGene"  in mc_names
            assert "MyGBgene"     not in mc_names

    async def test_modular_tab_per_grammar_lane_state(
            self, tiny_record, isolated_library, isolated_parts_bin,
    ):
        # Adding a part on the GB tab doesn't pollute the MoClo
        # tab's lane — each tab owns its own state.
        sc._save_parts_bin([
            {
                "name": "GBProm", "type": "Promoter", "position": "Pos 1",
                "oh5": "GGAG", "oh3": "TGAC", "grammar": "gb_l0",
                "sequence": "ACGT" * 10,
            },
        ])
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            await pilot.pause(0.05)
            modal._add_selected_part("gb_l0")
            assert len(modal._lanes["gb_l0"])       == 1
            assert len(modal._lanes["moclo_plant"]) == 0

    async def test_filter_setting_default_on(
            self, tiny_record, isolated_library, isolated_parts_bin,
    ):
        # The persisted setting defaults to True; the filter checkbox
        # in each modular tab inherits that default value.
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            await pilot.pause(0.05)
            from textual.widgets import Checkbox
            chk_gb = modal.query_one(
                "#chk-ctor-filter-gb_l0", Checkbox,
            )
            chk_mc = modal.query_one(
                "#chk-ctor-filter-moclo_plant", Checkbox,
            )
            assert chk_gb.value is True
            assert chk_mc.value is True

    async def test_filter_setting_persists_to_disk(
            self, tiny_record, isolated_library, isolated_parts_bin,
    ):
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            assert sc._get_setting(
                "constructor_filter_by_grammar", True,
            ) is True
            app.action_toggle_constructor_filter()
            await pilot.pause()
            sc._settings_flush_sync()
            assert sc._get_setting(
                "constructor_filter_by_grammar", True,
            ) is False

    async def test_per_role_entry_vector_banner(
            self, tiny_record, isolated_library, isolated_parts_bin,
            tmp_path, monkeypatch,
    ):
        # Configure different L1 acceptors for the four GB roles
        # (Alpha1 / Alpha2 / Omega1 / Omega2). After the user picks
        # a backbone via `_select_backbone`, the banner-style
        # summary reflects the active role's bound vector.
        ev_file = tmp_path / "entry_vectors.json"
        monkeypatch.setattr(sc, "_ENTRY_VECTORS_FILE", ev_file)
        monkeypatch.setattr(sc, "_entry_vectors_cache", None)
        for role, name in (
            ("Alpha1", "FFE2_test"),
            ("Alpha2", "FFE3_test"),
            ("Omega1", "FFE4_test"),
            ("Omega2", "FFE5_test"),
        ):
            sc._set_entry_vector("gb_l0", {
                "name":   name, "size": 2500, "source": "test",
                "gb_text": (
                    f"LOCUS       {name}_locus  2500 bp DNA circular SYN 01-JAN-2026\n"
                    "FEATURES             Location/Qualifiers\n"
                    "ORIGIN\n        1 " + "a" * 60 + "\n//\n"
                ),
            }, role=role)
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            await pilot.pause(0.05)
            # No backbone active by default — the user has to press
            # one to set it (matches the 2026-05-08 click-to-set UX).
            modal._select_backbone("gb_l0", "Alpha1")
            banner = modal._entry_vector_summary_for_grammar("gb_l0")
            assert "FFE2_test" in banner
            assert "Alpha1"    in banner
            # Switch to Omega1 → banner swaps.
            modal._select_backbone("gb_l0", "Omega1")
            banner = modal._entry_vector_summary_for_grammar("gb_l0")
            assert "FFE4_test" in banner
            assert "Omega1"    in banner

    async def test_clicking_backbone_button_routes_to_select_backbone(
            self, tiny_record, isolated_library, isolated_parts_bin,
    ):
        """Regression guard for 2026-05-08: the dispatcher's
        `_gid_from_button` only matched buttons whose id ENDED with
        `-{gid}`. Backbone buttons have an extra `-{role}` suffix
        (`btn-bb-{gid}-{role}`), so clicks were silently dropped —
        the buttons looked clickable but did nothing. Clicking each
        bb button must now invoke `_select_backbone(gid, role)`."""
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            await pilot.pause(0.1)
            calls: list[tuple] = []
            real_select = modal._select_backbone

            def _spy(gid, role):
                calls.append((gid, role))
                # Don't call real_select — it would try to push the
                # picker modal for unbound roles, which the test
                # doesn't need.

            modal._select_backbone = _spy
            from textual.widgets import TabbedContent
            modal.query_one(
                "#ctor-tabs", TabbedContent,
            ).active = "ctor-tab-gb_l0"
            await pilot.pause()
            await pilot.pause(0.1)
            await pilot.click("#btn-bb-gb_l0-Alpha1")
            await pilot.pause()
            await pilot.click("#btn-bb-gb_l0-Omega2")
            await pilot.pause()
            assert ("gb_l0", "Alpha1") in calls
            assert ("gb_l0", "Omega2") in calls

    async def test_role_buttons_inside_modal_horizontal_extent(
            self, tiny_record, isolated_library, isolated_parts_bin,
    ):
        """Regression guard for 2026-05-08: a leading
        ``Static("Backbone:")`` inside the row's Horizontal grabbed
        the full width and pushed the role columns past the modal's
        right edge (modal x=20..140 but Alpha2 at x=152). The fix
        drops the inline label in favour of a section header above
        the row. This test pins every role button's right edge
        inside the modal's container so it can't regress."""
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            await pilot.pause(0.1)
            from textual.widgets import TabbedContent
            modal.query_one(
                "#ctor-tabs", TabbedContent,
            ).active = "ctor-tab-gb_l0"
            await pilot.pause()
            await pilot.pause(0.1)
            box_region = modal.query_one("#ctor-box").region
            modal_right = box_region.x + box_region.width
            from textual.widgets import Button
            for role in ("Alpha1", "Alpha2", "Omega1", "Omega2"):
                btn = modal.query_one(f"#btn-bb-gb_l0-{role}", Button)
                btn_right = btn.region.x + btn.region.width
                assert btn_right <= modal_right, (
                    f"{role} button extends past modal right edge "
                    f"({btn.region} vs box {box_region})"
                )

    async def test_role_button_label_shows_bound_vector_name(
            self, tiny_record, isolated_library, isolated_parts_bin,
            tmp_path, monkeypatch,
    ):
        # Per-role static label ABOVE each backbone button shows the
        # bound vector's name (truncated for the column width). With
        # no binding the label reads "(none)".
        ev_file = tmp_path / "entry_vectors.json"
        monkeypatch.setattr(sc, "_ENTRY_VECTORS_FILE", ev_file)
        monkeypatch.setattr(sc, "_entry_vectors_cache", None)
        sc._set_entry_vector("gb_l0", {
            "name":   "MyAcceptor", "size": 3000, "source": "test",
            "gb_text": "",
        }, role="Alpha1")
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            await pilot.pause(0.05)
            # Alpha1 label shows the bound vector name.
            assert "MyAcceptor" in modal._backbone_name_label("gb_l0", "Alpha1")
            # Omega1 has nothing bound — label says "(none)".
            assert "(none)" in modal._backbone_name_label("gb_l0", "Omega1")
            from textual.widgets import Static
            lbl_a1 = modal.query_one(
                "#lbl-bb-gb_l0-Alpha1", Static,
            )
            lbl_o1 = modal.query_one(
                "#lbl-bb-gb_l0-Omega1", Static,
            )
            assert "MyAcceptor" in str(lbl_a1.render())
            assert "(none)"     in str(lbl_o1.render())

    async def test_unbound_button_press_opens_picker(
            self, tiny_record, isolated_library, isolated_parts_bin,
            tmp_path, monkeypatch,
    ):
        # When the user clicks a role button that has no plasmid
        # bound yet, `_select_backbone` should hand off to
        # `_pick_acceptor_for_role(then_activate=True)` rather than
        # silently activating an empty role. We verify by stubbing
        # `_pick_acceptor_for_role` and checking it gets called with
        # the right role + activate flag.
        ev_file = tmp_path / "entry_vectors.json"
        monkeypatch.setattr(sc, "_ENTRY_VECTORS_FILE", ev_file)
        monkeypatch.setattr(sc, "_entry_vectors_cache", None)
        from tests.test_smoke import _build_app, TERMINAL_SIZE
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.ConstructorModal()
            await app.push_screen(modal)
            await pilot.pause()
            await pilot.pause(0.05)
            calls: list[tuple] = []

            def _stub(gid, role="", *, then_activate=False):
                calls.append((gid, role, then_activate))

            modal._pick_acceptor_for_role = _stub
            modal._select_backbone("gb_l0", "Alpha2")
            assert calls == [("gb_l0", "Alpha2", True)], (
                f"Unbound click should open picker; got {calls}"
            )

    def test_no_hardcoded_acceptor_ids_in_constructor_backbones(self):
        """Regression guard for 2026-05-08 (do-not-hardcode):
        `_CONSTRUCTOR_BACKBONES` must not contain a fixed ``id``
        field for any role. Roles are slots — the user binds a
        plasmid to each via "Change…" — never pre-bound to a
        specific vector by the source code."""
        for gid, roles in sc._CONSTRUCTOR_BACKBONES.items():
            for role, meta in roles.items():
                assert "id" not in meta, (
                    f"{gid}.{role} hardcodes id={meta.get('id')!r} "
                    f"— roles must not be pre-bound."
                )

    def test_set_get_entry_vector_role_isolation(
            self, tmp_path, monkeypatch,
    ):
        # Setting a vector for Alpha1 doesn't disturb Alpha2 / Omega1
        # / Omega2 / the legacy singleton (role="").
        ev_file = tmp_path / "entry_vectors.json"
        monkeypatch.setattr(sc, "_ENTRY_VECTORS_FILE", ev_file)
        monkeypatch.setattr(sc, "_entry_vectors_cache", None)
        sc._set_entry_vector("gb_l0", {
            "name": "L0_singleton", "size": 1, "source": "t",
            "gb_text": "",
        })
        sc._set_entry_vector("gb_l0", {
            "name": "alpha1_vec", "size": 2, "source": "t",
            "gb_text": "",
        }, role="Alpha1")
        sc._set_entry_vector("gb_l0", {
            "name": "omega1_vec", "size": 3, "source": "t",
            "gb_text": "",
        }, role="Omega1")
        # All three coexist.
        assert sc._get_entry_vector("gb_l0")["name"] == "L0_singleton"
        assert sc._get_entry_vector("gb_l0", "Alpha1")["name"] == "alpha1_vec"
        assert sc._get_entry_vector("gb_l0", "Omega1")["name"] == "omega1_vec"
        assert sc._get_entry_vector("gb_l0", "Alpha2") is None
        # Clearing one role doesn't disturb the others.
        sc._set_entry_vector("gb_l0", None, role="Alpha1")
        assert sc._get_entry_vector("gb_l0", "Alpha1") is None
        assert sc._get_entry_vector("gb_l0")["name"] == "L0_singleton"
        assert sc._get_entry_vector("gb_l0", "Omega1")["name"] == "omega1_vec"
