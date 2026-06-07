"""Regression + property tests for the pure-Python RNA secondary-structure
free-energy engine in `splicecraft_biology` (`_rna_fold`,
`_rna_eval_structure`, `_rna_mfe`).

The Turner-2004 energy model + the Zuker MFE folder were validated
exhaustively against ViennaRNA during development (thousands of folds and
structure evaluations, exact to the cent). This suite is the shipped
regression guard: it locks the engine against a FROZEN ViennaRNA reference
(captured once; no ViennaRNA needed at test time) and exercises the
hardened public API + edge cases.

Reference captured with ViennaRNA `eval_structure` / `fold` (dangles=2,
the package default); energies in kcal/mol.
"""
import json

import pytest

import splicecraft_biology as bio

# Frozen ViennaRNA reference: {"vienna_version", "fold": [[seq, mfe, db], ...],
# "eval": [[seq, db, energy], ...]}. Injected at build time.
_REF = json.loads(r"""{"vienna_version":"2.7.2","fold":[["CGACCGAUGGCAAGUCACUCUCGGCGCCGGAUCUGUAAUUCUAC",-7.2,".(((((.((.(.((.....)).).)).))).))..........."],["AAAGCCACGGCUAGAAAGGCUAGUAUG",-4.2,"..((((...........))))......"],["CUGGGACAGUAUCCGUGCG",-1.7,"...(((.....)))....."],["ACUCCAGCAUGGAUAUUUA",-2.2,"..((((...))))......"],["AGACUAAUUGAUCGGGAU",0.0,".................."],["GGCUCAUAGUCACAUUGGCAACUCUAUGUUCUCGGCGG",-4.0,"((((...)))).(.((((.(((.....))).)))).)."],["CUAACUAACCUGA",0.0,"............."],["UUAUCUUAGAAGCCUCGUGACGGAGU",-2.5,".............(((......)))."],["CAUCUCUAGCUAAUUUCCUCGACAGGCU",-1.0,".......((((.............))))"],["CCAGUGUAUGUGGUC",-1.1,"(((.......))).."],["UCCCGAAACCCCAGGUAUUAGCGUAGUU",-0.6,".......(((...)))............"],["UCUAAGUAAUCUUGCGCUGAGUACUUACCGCUGAUCG",-4.6,"..((((((.((.......)).)))))).........."],["UCGGAUUUCACGUCUAUAGAGAACCGAGGCAAGCGGAUGUGUCU",-8.5,"((((.((((.........)))).))))((((........))))."],["AUCGCGUGAUUCGCAAUUUGGCCGGCGGAUCACUCACGGUUU",-13.9,"((((.(((((((((..........)))))))))...)))).."],["GGCGGUUCACCACCGCAUGGU",-7.1,".(((((.....)))))....."],["GCGCUGUGUUUGCACUUUGAUCGUGAA",-1.1,"(.((.......)).)............"],["GCGACCUGUGGACGUUUCACUAGU",-3.0,"...((..(((((...)))))..))"],["CGUGUCGUCGUCGGGGAGGUGG",-2.1,"(.(.(((....))).).)...."],["GAAAAUUGUUUCUGGCGGGCC",-0.1,"((((....))))........."],["CUCCCCUGAACGUCGA",0.0,"................"],["CAUGUUACUUUCACACCCAUAG",0.0,"......................"],["CCGACUAUUAGUGCAGUCUAGCCUUUGAGU",-3.3,"...(((...((.((......))))...)))"],["GUGAAGGUCCAUUAAU",0.0,"................"],["UGGUUUAGCAGUCUAUUAC",0.0,"..................."],["GCGCUACGCACGAACGGUAAAGUUAUAGCGA",-5.0,"......(((...(((......)))...)))."],["CGGGGCCCGCUGC",-0.7,"(((...)))...."],["CAUAAGCACUUGGCGAUCCCCGUAG",-1.3,"............(((.....))).."],["UCCGUCAAGGUAUGAUCACUAUGGAAGAUG",-3.8,"(((((...(((......))))))))....."],["CCAAUUUAUGGAC",-1.1,"(((.....))).."],["GUGCGUGGAAGCGGAACACGUUCC",-5.4,"(.(((((.........))))).)."],["AGUGCAUGGCGUCCUGGCGGGUAUCUUCAGGUCUG",-5.8,"..........(.(((((.(.....).))))).).."],["GUUAUAGCCGGAAACUGGUACAUUAG",-4.4,"......(((((...)))))......."],["GACUCCCGGCUUUCUAAGCUAC",-2.9,".......(((((...))))).."],["UACUGUACAUACGGCUGACUGUAGUUGCGACAUAAUGAACUUCACC",-6.0,"...((((..(((((....)))))..))))................."],["ACAUGAGUAUUUAAGUAAGCACAAGUACACG",-1.7,"......((((((..........))))))..."],["CAGUUGACUAAUCC",0.0,".............."],["GCGGAGGCUAUAGUAUUCCUGGGCGUGCGCCUAGAGAAGU",-12.3,"((....)).......(((((((((....)))))).))).."],["GAUAGCUCCCCUUGCAUACGCGUAGUAGUAUAACUAAUCUUCAUC",-2.7,"....((.......))((((........)))).............."],["GCUCGACUAACGUCAUGGAAAAUCCGUUAAUAACUUCACACCA",-4.6,"....(((....)))(((((...)))))................"],["GACAUCUGAUGCUUAAGGCGAGGUACAUUGCGUUAAUAUAG",-6.7,"............((((.((((......)))).))))....."],["GCACUAGACACACCGUCUGGUUGGGGCCGCCUCUUUGCCG",-10.6,"..(((((((.....))))))).((((...))))......."],["GUGUGGAUAAGUACAGUCCCGCCCCCUGACAGUAG",-5.8,"(((.((((.......)))))))............."],["UAGUUAAACCUGCCCGAUAUUGCAAAGUUUCAA",-1.8,".....((((.(((........)))..))))..."],["GGCUUGACCAUGCGUUAGGACCCGUGAGAUGCCUAUGGAC",-7.0,"..((((((.....))))))..(((((.......))))).."],["GUGCCUCAUCCCAUCACGGUAUGAUGCCAGUAGUCCCGUCGGG",-8.2,"(.((.((((.((.....)).)))).)))......(((...)))"],["UGAACAUUGGCACGCCAAUCCAUG",-3.0,".....(((((....)))))....."],["GUCCUAAGGCCCAUUUCCCGCGAGUGGAU",-4.8,"(((....)))((((((.....)))))).."],["UGUUAUCAGUUCGAAAGGGUAAUUUUACAUAUGGCCGCUUCAAUGC",-4.4,"............(((..(((.((.......)).)))..)))....."],["UUCCACCUCAGUUAUGAUUGCCUGCAGAAGUAUGGUGCGUGGCAGG",-9.9,".....(((..((((((...((((((....))).))).)))))))))"],["ACCUCGAGUUUGCUCGGCGUUGGGUUGCGACAGU",-7.0,"...(((((....))))).((((.....))))..."]],"eval":[["ACCCUAGUAUGGCCAUU","..((......)).....",-0.7],["CACACGCAUGUACCAGGAUGUCUGCUAUUAAGUCGACUAUUUGAUAGAG","..((.((((........)))).))(((((((((.....)))))))))..",-8.3],["UCGCGUCCGGCAAUACUUUCUAAUCGGCGAUAAAUAGAGCGGCCGUAUUAGAAUGGC","..((.....))....(.(((((((((((.............)))).))))))).)..",-14.4],["UAGAAUGCAAAGCGUCAGUGUUUCAAGGCAUGCUCCAAAUGAAGUGUGGCCCCCGGAACCCUUACACCUGC","......(((..(.((.((.(((((..((((((((........))))).)))...))))).)).)).).)))",-13.4],["UCUCUCCUCCCUUAUGUCUCUCGGCACCAGACAGCGACAUC",".............(((((.((.(........))).))))).",-5.2],["CUAUUCAGCGAUCGUUUAUUGCACUCUGCACUGUC",".......(((((.....))))).............",-3.0],["AUGGCUGGUGCCUACUCUACCUUCUGAGAGGAACCGCAAGUGUGACACGGCUGAGAGUGCU","...((.(((.(((.(((........)))))).)))))........(((........)))..",-15.0],["UCGCAAAAUGCGAG","(((((...))))).",-4.7],["CUGGUCAACGACGAAAUAGAACGAAGGGAGACGCAUCGGGCUUCA",".................((..(((.(.(...).).)))..))...",-4.4],["AAUUCUCUGAGUGAACUGCAUCGAACGGGCAACGAAUUAAACAGCUAAGACUGAUUAUAGA","(((((((((..(((......)))..))))....)))))...(((......)))........",-4.1],["UGUUCUGAAAACACGUUAAACCAACAAGGGCAACCAAC","((((((....................))))))......",-3.3],["CAUAGUAAUGAGAAUGAAUCACCCUCUGCUUAU","...((((..(((..((...))..)))))))...",-1.9],["AAAGGUGUGGGGCGC","....((((...))))",-0.5],["GCUCCUUUGACUUUGUCGAGCCGAAAGUAUUCAAUCCAGGAUUUAGUCGCCCGGUAUUUACUAUUAGU","((((....(((...)))))))....((((......((.((.........)).))....))))......",-8.2],["AGGAUUUGCGUCCCACUGUAGAAAACUCUGCUC",".((((....))))....(((((....)))))..",-6.7],["CCUGAUUGGACAUUAGAUGCUUUUAAACUUGAGUGUUGCUGCAGGUUCUCAAAUUGAU","((((....((((((....(........)...))))))....)))).............",-8.1],["CACUUUGCUAGGAGUAGCGCCGGAACAUUGUGGGUCU",".....(((((....)))))(((........)))....",-5.6],["UACGUGAGUCCCGUAUCUUCACCAGAUAGUGAGAUCUUC","((((.(....)))))..(((((......)))))......",-4.8],["UGUGUCACCAGGAUCGC",".(((((.....)).)))",-0.4],["UACGUUGCUCAAGCCGUGCCCCCUUUCUCCUUGUUCACCCCAUCCGGUCC","((((..((....))))))..................(((......)))..",-2.9],["UUAUAGCAUUCUCAGGUCGGAAAUCCUUUGCACGCGAAUUCACCAAAAUGCCCAAGCCCUGCCUCUUUGAU",".....(((((....(((..(((.(((.......).)).))))))..)))))....................",-5.5],["AACUAAUGCGAUCUAUCAAUGCUAAGCUCUCGGUUAUAUCUUAGCGGCAACGUGAGUACU","......(((..........(((((((.............))))))).........)))..",-6.0],["AUAACGUGAGGUAACUACGGCACCCGAAAAAAGGGCUCGAGAGCUGCAGAAUACGUGGAG","...(((((..(((.((.(((..(((.......))).)))..)).)))....)))))....",-11.6],["CUGGAGGUGGAGGCUCUAUCGUGCAGUUCGUGUAACCAGAGACAGUGUAGGCU","((((.((((((...)))))).((((.....)))).))))..............",-8.3],["GCAACGGCAAUCGGCUCACGAACAUUCG","..........(((.....))).......",-1.9],["ACUUCUAUUUUCCGUUGCCCAUUCCGCAUCAAAACACUGAGCCUGGAAAGCCAAG","................((...((((((.(((......)))))..)))).))....",-6.3],["GGUUGAGGUUCCCGCCCUCCGCCGUUGGUUAUCUGAACAUCAACGCAUUCACACGAGGGAGCAAUCGUCG","(((.((((.......)))).)))((((((.........))))))........((((........))))..",-15.2],["AGGGACGACAGGUUUGCGACUAGAAGCGGUCUCUUCGGGCUAGGAAGUUAACAUGUAGCUGGGAAC",".(((((........(((........))))))))(((.(((((.............))))).)))..",-13.5],["CUGAUCCAUAACUCAUCUUAGAGGGCGUUCCGAGACAGUGGGCUUAGGCGCUCAAUCAUACAUGU","....................(((.((.(.((.(.....).))...).)).)))............",-10.1],["UAGAGACAAGGUCGCGACACCCACGUCGAAAAGAGGACGUUCCACGUGAUCGUGAGCGAGAGCCAGU",".........(.(((((((((..(((((........))))).....))).)))))).)..........",-18.1],["UUGAGGUUAUACCGAGUGGAUUAAAAAACCGGUCUAUCGGUGUUGAGGCGCGGUACCCUAAAACUAUAGCAU","...(((...(((((.((...((((...((((......)))).)))).)).))))).))).............",-14.6],["GCUACGAAGCGGGUGGAACCAGGUGAGUGGUGUUA","(((((..(.(.(((...))).).)..)))))....",-8.5],["UAAGGCUCCAAAUAUAUUUUGACACUUUCUGACGGAUUCAGGAUUUGUUUGGUCAAGA",".......(((((((.(((((((..((.......))..))))))).)))))))......",-11.1],["UCAGGCCCCGUUCCCCAGCACCCGAUACAUUCCCCAGAUGCGUCUUGGCGCACCCAAUUAAUUCG",".................((.((.(((.((((.....)))).)))..)).))..............",-7.9],["GAGCAAUGAAUCGCCUUCCGUUGCGUAAACUCA","..((((((..........)))))).........",-5.6],["UACUAACACGGUAUGUCUCUUAACGUGUUCUAUGUAACAAG","....((((((.............))))))............",-5.5],["UAAACAGGAUCGGAAGGCC","......((.((....))))",-0.4],["GACGUUUUCCUUCGACCCAUAACCCUGAGGCUCGCUCCCAUUACGCGCA","(.(((.......(((((((......)).)).)))........))))...",-5.3],["GAUGUACCUUCCAUAAACGAGCAUGGGAGCAC",".......(((((((........)))))))...",-6.9],["GAAAGACUCACGGGGCGUUGGUAACAGUGUCAAAGUU","....((((.....(((((((....)))))))..))))",-5.3],["GGCUGGUUCACUAACAGGUCACCGGAGU","..(((((..(((....))).)))))...",-5.8],["GCAUGGAAAAUAGAGUGACGGGCGGACUGUCAAUUGCU","(((............((((((.....))))))..))).",-7.8],["CACUUAUACAAAGGCCAACUUAUCGCAUGCAA","..........(((.....)))...........",-0.3],["UAGGACACAACAGCCCAGCUACUAACGUCAUGCCUAUACUGCUA","((((.((..(((((...)))......))..))))))........",-3.8],["UAUACUCCUUUCCUACCAUGUGAACUAGAUUGGAAUUAAGUACAACAUCUUCGGUCUGAUACUUAU","...................(((...(((((((((...............))))))))).)))....",-5.9],["GUACGGCUGCUUAUUAGCAUAUAUUGGAACCUCCGAACGC",".......((((....))))....(((((...)))))....",-6.0],["GGCACGUCGGGUUAAAGGUCCUGGACAAAGCCCUUAACAUAGUACGUAGAUUCGUG","(((..((((((........))).)))...)))..........((((......))))",-10.1],["GGUUGGCCGUAUGUCUCAUAACUUUGUCGAGGAGUU","((....)).....(((((((....))).))))....",-2.4],["UCGCGACUACAUUCAAAGUCCGUGGGU","((((((((........))).)))))..",-6.0],["CCAGAUAACGCGGACGGGGUACAACU",".......((.(.....).))......",-1.6],["CCAGGCAGUAUUCUAAGCGAACCCCAGUGCACAUGGUACCUCAAUGCUUGGGCACUGUAAG","....(((((..((((((((.......((((.....)))).....)))))))).)))))...",-15.0],["GCAGGGAUUCGCCAUUUCAUUCACAUUCCAGUCGGA","...((......)).............(((....)))",-2.9],["GCGCUACUGAUGAUCAGCCGCUUCU","(((...((((...)))).)))....",-5.4],["UAGACUUAAGAGUCAUUGACAAAGGAUUGCAUCU","..((((....))))....................",-2.8],["UAAACCAAUCCUUCUACGUACUCCACCGCCAUACUCUCGUUAGACGAGUACUAAUUAGAGGGAUUGG","....((((((((((((.((((((......................))))))....))))))))))))",-21.6],["UAGGCAAAGCGUUGCCGGGCAUGUCAAGUGUGUUCGACG","..(((((....)))))((((((.......))))))....",-9.6],["AACGGAAAUUGGCUAAGGCUUACCGACCCGUAUUCAUG",".((((.....(((....))).......)))).......",-6.3],["UCGCUGCACAGGAGGGUCUGGGGCCGGCAGCUGCGCAUCUGUCUAGGACGCUGUG","..(((((.(.....((((...)))))))))).(((..(((.....))))))....",-13.2],["AGGUUAAGUCUGUCAAUACAAACCCGGCUUUUCAAAAUAAGACGCGCAACGGUAAAUAGGCAUCGAUAG",".......((((((...(((.....((((((........))).).)).....))).))))))........",-8.0],["GUCUAGUUGAGUACAAAAUUUGUAUGACAACUUAUCU","....(((((.((((((...))))))..))))).....",-8.1],["AUACCGAAUGAGUAAAGAGGAGGGAUACGGGGAGG","...(((.((...............)).))).....",-1.8],["CAAUACGCUUUCUUAGCUCU","......(((.....)))...",-2.1],["GAUAGUUUGGGCAUCGACAUUACACACGACCCCGAAGGACGAGGUCCCCUUAGUGUUCGCAAGCUGU",".((((((((.(....(((((((.....((((.((.....)).))))....)))))))).))))))))",-19.2],["CGACCGCGGAAAGAGGUUCACGAGGAAUGUUGGGCUUUUAUGGAACCUAAGCAGAUUAGGGUUUGGCGUACA","....(((.((((((((((((..(....)..)))))))))......(((((.....))))).))).)))....",-15.1],["UUGUAACUCUAGAGUCUUGAAACGUCGCACCCCAUAACCCUAUCAAGGUGUGCAAGCGUGAUGCCGAGCA",".............(.((((...((((((.(..((((..(((....)))))))...).)))))).))))).",-12.0],["GCGGACCCGCUCCUAUGCGGGGGUUGCCCCGCAUACCGUCCACUUGCUGCU","((((.........(((((((((....)))))))))...........)))).",-20.5],["GGUCAAUGGUUUUCAACUUGACCCUCCUCAAAAUUCGGCCGUU","((((((.(........)))))))....................",-7.0],["UAGGCGACAUCCUAUAUACUCGGGCAUAACCAACAUGGAAUUCUAACUAAGGGCGCUCACUCCGAGUGUUGU","((((......)))).(((((((((.........(.(((........))).).........)))))))))...",-12.9],["UAAUAACGAGCUUUUUACACGCUGCCGUGCACAUAGCAUCUAUCCCUGUGCGCUA","........(((.........)))...(((((((.............)))))))..",-9.8],["ACCAAGGGGUGUCCAGAUGGUACCGUUUCCGAUUAGUUAGACCAUUCCUAUACCAGCACAACCAAUGGCUA",".......(((((...((((((...................))))))...)))))(((.((.....))))).",-11.2],["GUUUCCACGGGAGGC","((((((...))))))",-3.9],["AUAAGGUACGACGGAGCCAAUGCGAGGUAGUCCUAGAAAUGAUCAGAGAUAGCACCAC","....(((.(....).))).......(((.(((((.((.....)))).)))...)))..",-6.5],["GCCCUUGGCGACUGUUACAGAU","(((...))).............",-3.0],["AAAGGUCUACGUAGAUAAGGCGUGGCAAGUAAUAUGUGGAGGUCACGCCGUACAAUA","....((((....))))..((((((((...((.....))...))))))))........",-14.7],["AGGGCUUCAGUGAUUGGGUGCCAGCAGAAACAUCGAUACUGGUUGCCUAUCCCCAUUUAAAGAAUUC",".((((.((((((.((((((..(....)..)).))))))))))..))))...................",-13.4],["UUAUGCUGGGUCCCUCCAUACUACCGAUUACC",".((((..((...))..))))............",-2.3],["CGGUUUCCUCGGCUGACAAAAGGUGGUACAG","........(((.((......)).))).....",-2.8],["AAUGACUUCCAUCCGUAUCGUCACAAACCCUGUUUCACACUUUCGACUCAGG","..((((.............)))).....((((..((........))..))))",-6.7],["ACGACUUAUUCACGGUGCGCUACCCUCCUACUUGGAU",".............((((...)))).(((.....))).",-4.1],["UCCUGUGUCCCCAGCCCCCGCGGUGCAGCAGCGUCUGAAGACGUACUAUCAUACCUCAAAACUGGCUACCU","......((..((((.....(.((((.....(((((....))))).......)))).)....))))..))..",-12.0]]}""")

_TOL = 0.011        # 1 centi-kcal: the engine matches ViennaRNA to the cent


class TestRnaEvaluator:
    def test_eval_matches_frozen_vienna(self):
        bad = []
        for seq, db, ref_e in _REF["eval"]:
            e = bio._rna_eval_structure(seq, db)
            if abs(e - ref_e) > _TOL:
                bad.append((seq, db, e, ref_e))
        assert not bad, f"{len(bad)} eval mismatches vs frozen ViennaRNA: {bad[:3]}"

    def test_known_stemloop(self):
        # 3x GC/GC stacks (-3.30 each) + a 4-nt hairpin (+4.50) = -5.40
        assert abs(bio._rna_eval_structure("GGGGAAAACCCC", "((((....))))")
                   - (-5.40)) < 1e-9
        # the UUCG tetraloop is an extra-stable special loop
        assert bio._rna_eval_structure("GCGCUUCGGCGC", "((((....))))") < -5.0


class TestRnaFolder:
    def test_mfe_matches_frozen_vienna(self):
        bad = []
        for seq, ref_mfe, _ref_db in _REF["fold"]:
            db, mfe = bio._rna_fold(seq)
            if abs(mfe - ref_mfe) > _TOL:
                bad.append((seq, mfe, ref_mfe))
            # self-consistency: my structure evaluates to my reported MFE
            if "(" in db:
                assert abs(bio._rna_eval_structure(seq, db) - mfe) < _TOL, seq
            # optimality: never worse than ViennaRNA's MFE
            assert mfe <= ref_mfe + _TOL, (seq, mfe, ref_mfe)
        assert not bad, f"{len(bad)} MFE mismatches vs frozen ViennaRNA: {bad[:3]}"

    def test_fold_known(self):
        db, mfe = bio._rna_fold("GGGGAAAACCCC")
        assert db == "((((....))))" and abs(mfe - (-5.40)) < 1e-9


class TestRnaApiHardening:
    def test_dna_t_mapped_to_u(self):
        assert bio._rna_fold("GGGGTTTTCCCC")[0] == "((((....))))"
        assert abs(bio._rna_mfe("GGGGTTTTCCCC") - bio._rna_mfe("GGGGUUUUCCCC")) < 1e-9

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            bio._rna_fold("")

    def test_ambiguous_bases_raise(self):
        for bad in ("ACGUN", "ACGURY", "ACGU GU", "ACGU-GU"):
            with pytest.raises(ValueError):
                bio._rna_fold(bad)

    def test_overlength_raises(self):
        with pytest.raises(ValueError):
            bio._rna_fold("A" * 601)

    def test_non_string_raises(self):
        with pytest.raises(ValueError):
            bio._rna_fold(1234)

    def test_eval_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            bio._rna_eval_structure("ACGU", "((((((")

    def test_eval_malformed_structure_raises(self):
        with pytest.raises(ValueError):
            bio._rna_eval_structure("ACGUACGU", "((()....")    # unbalanced
        with pytest.raises(ValueError):
            bio._rna_eval_structure("ACGUACGU", "((xx))..")    # bad glyph

    def test_very_short_sequences(self):
        for s in ("A", "AC", "ACG", "ACGU", "ACGUA"):
            db, mfe = bio._rna_fold(s)
            assert len(db) == len(s)
            assert mfe <= _TOL        # the empty structure (0.0) is always available

    def test_mfe_helper_agrees_with_fold(self):
        for s in ("GGGGAAAACCCC", "ACGUACGUACGUGCAU", "GCGCAAAAGCGCAAAAGCGC"):
            assert abs(bio._rna_mfe(s) - bio._rna_fold(s)[1]) < 1e-12
