"""test_operon_soe — the Native Operon Domestication SOE primer engine
(`_design_operon_soe_primers`).

Pure-biology, fast (<2 s). The catastrophic-class guarantees: the cured operon
carries NO grammar-forbidden site, differs from the native sequence ONLY at the
cure positions, and EVERY cure rides a designed primer (the user amplifies
native DNA — nothing is synthesized).
"""
import splicecraft as sc

_GB = sc._BUILTIN_GRAMMARS["gb_l0"]
_FORBIDDEN = _GB["forbidden_sites"]


def _cds(internal_site: str) -> str:
    """A 72 bp in-frame CDS: ATG + 10·Lys + <site> + 10·Lys + stop. ``site`` is
    a 6 bp Type IIS recognition placed on a codon boundary so a synonymous
    wobble change can clear it (GGT|CTC = Gly|Leu, CGT|CTC = Arg|Leu)."""
    return "ATG" + "AAA" * 10 + internal_site + "AAA" * 10 + "TAA"


def _operon():
    """Two CDS (BsaI in A, Esp3I in B) + a clean intergenic spacer."""
    a = _cds("GGTCTC")                 # BsaI in cdsA
    b = _cds("CGTCTC")                 # Esp3I in cdsB
    inter = "ATAATAATAATA"            # 12 bp non-coding, no forbidden sites
    seq = a + inter + b
    feats = [
        {"start": 0, "end": len(a), "strand": 1, "type": "CDS",
         "codon_start": 1, "label": "cdsA"},
        {"start": len(a) + len(inter), "end": len(seq), "strand": 1,
         "type": "CDS", "codon_start": 1, "label": "cdsB"},
    ]
    return seq, feats


def _xlate(seq: str) -> str:
    from Bio.Seq import Seq
    return str(Seq(seq).translate())


class TestOperonSOEDesigner:
    def test_cures_all_sites_and_is_clean(self):
        seq, feats = _operon()
        # Sanity: the native operon really does contain both forbidden sites.
        assert sc._gb_find_forbidden_hits(seq, sites=_FORBIDDEN)
        res = sc._design_operon_soe_primers(seq, feats, _GB)
        assert res.get("ok"), res
        cured = res["cured_seq"]
        # No grammar-forbidden site survives.
        assert sc._gb_find_forbidden_hits(cured, sites=_FORBIDDEN) == []
        # Same length; differs ONLY at the recorded cure positions.
        assert len(cured) == len(seq)
        diffs = {i for i in range(len(seq)) if seq[i] != cured[i]}
        assert diffs == {e["pos"] for e in res["edits"]}

    def test_cures_are_synonymous_in_each_cds(self):
        seq, feats = _operon()
        res = sc._design_operon_soe_primers(seq, feats, _GB)
        cured = res["cured_seq"]
        for f in feats:
            s, e = f["start"], f["end"]
            assert _xlate(cured[s:e]) == _xlate(seq[s:e]), \
                "cure changed the protein — not synonymous"

    def test_every_cure_is_primer_encoded(self):
        """The catastrophic-class gate: each cure position lies inside a
        primer-covered window (a flank binding region or a mutagenic window).
        The designer only returns ok when this holds; re-checked here."""
        seq, feats = _operon()
        res = sc._design_operon_soe_primers(seq, feats, _GB)
        assert res.get("ok"), res
        windows = [tuple(p["covers"]) for p in res["primers"] if "covers" in p]
        for e in res["edits"]:
            assert any(s <= e["pos"] < q for s, q in windows), \
                f"cure at {e['pos']} not carried by any primer"

    def test_two_clusters_two_internal_pairs(self):
        seq, feats = _operon()
        res = sc._design_operon_soe_primers(seq, feats, _GB)
        # Two well-separated cures → 2 SOE junctions → 2 flank + 4 mutagenic.
        assert res["n_clusters"] == 2
        assert len(res["primers"]) == 2 + 2 * 2
        names = [p["name"] for p in res["primers"]]
        assert len(names) == len(set(names)), "duplicate primer names"

    def test_no_forbidden_sites_just_flanks(self):
        """A clean operon needs no curing — just the two cassette flank primers,
        cured == native."""
        clean = ("ATG" + "AAACCCTTT" * 8 + "TAA")
        feats = [{"start": 0, "end": len(clean), "strand": 1,
                  "type": "CDS", "codon_start": 1, "label": "clean"}]
        assert sc._gb_find_forbidden_hits(clean, sites=_FORBIDDEN) == []
        res = sc._design_operon_soe_primers(clean, feats, _GB)
        assert res.get("ok"), res
        assert res["cured_seq"] == clean
        assert res["n_clusters"] == 0
        assert len(res["primers"]) == 2

    def test_noncoding_site_needs_manual_then_resolves(self):
        """A forbidden site in the intergenic region can't be synonymously
        cured → needs_manual; supplying a manual base edit clears it → ok."""
        a = _cds("AAACCC")             # no forbidden site in the CDS
        inter = "AT" + "GGTCTC" + "AT"  # BsaI in the NON-coding spacer
        b = "ATG" + "AAACCC" * 6 + "TAA"
        seq = a + inter + b
        feats = [
            {"start": 0, "end": len(a), "strand": 1, "type": "CDS",
             "codon_start": 1, "label": "a"},
            {"start": len(a) + len(inter), "end": len(seq), "strand": 1,
             "type": "CDS", "codon_start": 1, "label": "b"},
        ]
        res = sc._design_operon_soe_primers(seq, feats, _GB)
        assert res.get("needs_manual"), res
        flagged = res["sites_skipped"]
        assert flagged and all(not f["in_cds"] for f in flagged)
        # Mark a base inside the flagged site that breaks it (G G T C T C → at
        # the middle T): pick the site position, flip a base, re-run.
        pos = flagged[0]["pos"]
        # GGTCTC → GGACTC (change the 3rd base, index pos+2, T→A) clears BsaI
        # without spawning Esp3I.
        res2 = sc._design_operon_soe_primers(
            seq, feats, _GB, manual_edits=[{"pos": pos + 2, "to": "A"}])
        assert res2.get("ok"), res2
        assert sc._gb_find_forbidden_hits(res2["cured_seq"],
                                          sites=_FORBIDDEN) == []
        # The manual edit shows up as a primer-encoded cure.
        assert any(e["region"] == "manual" for e in res2["edits"])

    def test_reverse_strand_cds_cured_synonymously(self):
        """A forbidden site inside a REVERSE-strand CDS is cured in that CDS's
        own frame (the divergent-gene case, e.g. luxR antisense to luxICDABE).
        The reverse CDS's protein must be preserved."""
        coding = "ATG" + "AAA" * 5 + "GGTCTC" + "AAA" * 5 + "TAA"  # M..G L..*
        rev_genomic = sc._rc(coding)
        flank = "AAACCCAAACCC"
        seq = flank + rev_genomic + flank
        s, e = len(flank), len(flank) + len(coding)
        feats = [{"start": s, "end": e, "strand": -1, "type": "CDS",
                  "codon_start": 1, "label": "antisense"}]
        # The BsaI site really is present (on the reverse strand).
        assert sc._gb_find_forbidden_hits(seq, sites=_FORBIDDEN)
        res = sc._design_operon_soe_primers(seq, feats, _GB)
        assert res.get("ok"), res
        cured = res["cured_seq"]
        assert sc._gb_find_forbidden_hits(cured, sites=_FORBIDDEN) == []
        # Protein preserved: translate the CODING strand (revcomp of the
        # reverse feature's genomic span) before and after.
        assert _xlate(sc._rc(cured[s:e])) == _xlate(sc._rc(seq[s:e]))

    def test_no_cds_sites_all_flagged(self):
        """An operon span with forbidden sites but NO CDS features → every site
        is non-coding → all flagged for manual override, none auto-mutated."""
        seq = ("ATAATAATAA" + "GGTCTC" + "ATAATAATAATAATA" + "CGTCTC"
               + "ATAATAATAA")
        res = sc._design_operon_soe_primers(seq, [], _GB)
        assert res.get("needs_manual"), res
        assert {f["enzyme"] for f in res["sites_skipped"]} == {"BsaI", "Esp3I"}
        assert all(not f["in_cds"] for f in res["sites_skipped"])
        # Nothing was auto-mutated (cured == native at this stage).
        assert res["cured_seq"] == seq

    def test_operon_position_registered(self):
        """OPERON is a CDS-equivalent GB position (AATG→GCTT), coding, and it
        propagates to the gb_l0 grammar's position list."""
        assert "OPERON" in sc._GB_POSITIONS
        assert sc._GB_POSITIONS["OPERON"][1:] == ("AATG", "GCTT")
        assert "OPERON" in sc._GB_CODING_PART_TYPES
        gb = sc._BUILTIN_GRAMMARS["gb_l0"]
        assert any(p["type"] == "OPERON" and p["oh5"] == "AATG"
                   and p["oh3"] == "GCTT" for p in gb["positions"])

    def test_operon_uses_cds_overhangs_and_atg_skip(self):
        """The operon clones as a CDS-equivalent part: AATG/GCTT overhangs, and
        when it begins with the first gene's ATG the forward primer binds at
        codon 2 (the AATG overhang carries the ATG — no duplicated start)."""
        seq, feats = _operon()
        assert seq[:3] == "ATG"
        res = sc._design_operon_soe_primers(seq, feats, _GB)
        assert res["overhangs"] == ["AATG", "GCTT"]
        assert res["fwd_skip"] == 3
        flank_fwd = next(p for p in res["primers"] if p["kind"] == "flank-fwd")
        assert flank_fwd["covers"][0] == 3            # binds from codon 2
        assert "AATGATG" not in flank_fwd["seq"]      # no duplicated start codon

    def test_extra_enzymes_cured_alongside_grammar(self):
        """`extra_enzymes` (EcoRI, KpnI) are cured alongside the GB Type IIS
        sites — synonymously, inside a CDS, protein preserved."""
        # GAATTC = GAA|TTC (Glu|Phe), GGTACC = GGT|ACC (Gly|Thr) — both on codon
        # boundaries so a wobble change clears them.
        cds = ("ATG" + "AAA" * 5 + "GAATTC" + "AAA" * 5 + "GGTACC"
               + "AAA" * 5 + "TAA")
        feats = [{"start": 0, "end": len(cds), "strand": 1, "type": "CDS",
                  "codon_start": 1, "label": "x"}]
        base = sc._design_operon_soe_primers(cds, feats, _GB)   # GB only
        assert base.get("ok")
        assert "GAATTC" in base["cured_seq"] and "GGTACC" in base["cured_seq"]
        res = sc._design_operon_soe_primers(cds, feats, _GB,
                                            extra_enzymes=["EcoRI", "KpnI"])
        assert res.get("ok"), res
        assert "GAATTC" not in res["cured_seq"]
        assert "GGTACC" not in res["cured_seq"]
        assert _xlate(res["cured_seq"]) == _xlate(cds)   # synonymous

    def test_unknown_extra_enzyme_ignored(self):
        """An unresolvable enzyme name in `extra_enzymes` is skipped, not a
        crash — the design proceeds with the grammar's set."""
        seq, feats = _operon()
        res = sc._design_operon_soe_primers(
            seq, feats, _GB, extra_enzymes=["Bogus", "NotAnEnzyme", ""])
        assert res.get("ok"), res
        assert sc._gb_find_forbidden_hits(res["cured_seq"], sites=_FORBIDDEN) == []

    def test_lowercase_operon_handled(self):
        """Lower-case input is normalised — cures land, output is upper-case."""
        seq, feats = _operon()
        res = sc._design_operon_soe_primers(seq.lower(), feats, _GB)
        assert res.get("ok"), res
        assert res["cured_seq"] == res["cured_seq"].upper()
        assert sc._gb_find_forbidden_hits(res["cured_seq"], sites=_FORBIDDEN) == []

    def test_single_cds_operon(self):
        """A one-gene 'operon' still domesticates as a CDS-equivalent OPERON
        part (AATG/GCTT)."""
        cds = _cds("GGTCTC")            # 72 bp, one BsaI site
        feats = [{"start": 0, "end": len(cds), "strand": 1, "type": "CDS",
                  "codon_start": 1, "label": "solo"}]
        res = sc._design_operon_soe_primers(cds, feats, _GB)
        assert res.get("ok"), res
        assert sc._gb_find_forbidden_hits(res["cured_seq"], sites=_FORBIDDEN) == []
        assert res["overhangs"] == ["AATG", "GCTT"]

    def test_too_short_operon_refused(self):
        """Below the SOE minimum, the designer refuses rather than emit junk."""
        res = sc._design_operon_soe_primers("ATGAAATAA", [], _GB)
        assert res.get("error") and "short" in res["error"].lower()
