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

# Frozen ViennaRNA RNAcofold reference for the bound-state heterodimer
# (`_rna_cofold`): {"cofold": [[seq_a, seq_b, dg], ...]}. Captured on the
# binding / anti-SD cases where the constrained bound state equals the
# unconstrained RNAcofold MFE. Injected at build time.
_COFOLD_REF = json.loads(r"""{"cofold":[["AAAGGAGGUAAAAAUG","ACCUCCUUA",-12.9],["UAAGGAGGUACAAAAAUGGCA","ACCUCCUUA",-13.2],["GGGGAGGUGAUACAUG","ACCUCCUUA",-12.2],["AAGGAGGACAUACUAUG","ACCUCCUUA",-11.3],["AGGAGGUAAAAAAAAAUG","ACCUCCUUA",-11.1],["UUUAGGAGGUUUUAUG","ACCUCCUUA",-10.9],["CGUCAGCACGAAAC","UGUUGGCCCAGUG",-8.3],["GAAUCGCUUAAGG","UUAAGUAAGUG",-4.5],["GAUGCAUACGCC","UUACUUGCUGUG",-3.1],["CCACCCCAUCGG","CUGGCA",-2.3],["AACUCGGGUAAUUUU","ACAGGUCACG",-0.7],["AGAGGCGC","GCCCUCCUGAAGUG",-7.6],["GUGGACACU","GCUAUGAAU",-1.9],["GAAUAAUGC","UUCGCUCUAU",-1.0],["GACUACGACGCGC","CAUUCCCUUGUCG",-5.0],["AGAGUUAUGGA","CAAGGAC",-0.4],["CUGUCUGAGA","UAGAAGAC",-3.2],["GAUAGUG","CACACGACCGGCGUC",-2.5],["CGUAGGGG","AGCGCAGUA",-1.7],["GCCAAGACUAUAG","CACUGUCGCA",-3.6],["UCACAAACGAUUAA","CUGAUAAAUGAGCC",-0.8],["UUUAUGACA","CGGGCAUAUGACUGG",-2.0],["UUACGAUAGUAUG","CCAACGGCGAGC",-1.0],["UUACAUUUGCUGU","AGAGGUACAGG",-3.4],["AUUAGUGAGAA","CCGUGCGUAU",-1.2],["CAAUUCGUACCUUG","GGGUCGUUAC",-5.0],["ACUCUGUU","CCACGAGC",-2.9],["GCAUUUCUGGA","GGCCAGCUUUUGA",-5.3],["CGUAAAGCUG","AAGUGGCUC",-4.0],["CAUGAACUUAGCUG","UAGUGUCAG",-2.1],["AACUUGAACGCC","UAGUGGUCAAAGAG",-5.5],["ACUGGUAAUCGU","GGUAUCUAU",-2.2],["UGUUCUCAGCCGG","GACUCCUAAUGCU",-2.0],["CUCCCCCGCG","UGCCAUA",-1.9],["AUCUGAG","AACCAGCUG",-2.1],["CGACAUUAUAU","CACUGUGGUAGGUU",-2.5],["AGCCGGCCAAUU","GCAUGAUAC",-1.6],["UCUCCAUCU","ACCCAAGAUUG",-1.4],["GCUUGUUCAAUU","UUCUUAACG",-0.5],["GAUAACAGAAUC","AACCUG",-2.1]]}""")


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


class TestRnaCofold:
    """Bound-state heterodimer ΔG (`_rna_cofold`) — the 16S anti-SD tail
    hybridized to an mRNA window. Locked against a frozen ViennaRNA
    RNAcofold reference (the binding / anti-SD cases where the constrained
    bound state equals the unconstrained cofold MFE)."""

    def test_matches_frozen_vienna(self):
        bad = []
        for a, b, ref in _COFOLD_REF["cofold"]:
            dg = bio._rna_cofold(a, b)
            if abs(dg - ref) > _TOL:
                bad.append((a, b, dg, ref))
        assert not bad, f"{len(bad)} cofold mismatches vs frozen RNAcofold: {bad[:3]}"

    def test_antisd_sd_duplex_is_strong(self):
        # the 16S anti-SD tail vs its complement -> a strong 9-bp duplex
        assert bio._rna_cofold("UAAGGAGGU", "ACCUCCUUA") < -10.0

    def test_weak_pair_is_unfavorable(self):
        # no complementarity: the forced-bound ΔG is high (weak / no RBS),
        # ~ the DuplexInit penalty, not a favorable binding energy
        assert bio._rna_cofold("AAAAAAAA", "AAAAAAAA") > -2.0

    def test_dna_t_to_u(self):
        assert abs(bio._rna_cofold("GGGGTTTT", "AAAACCCC")
                   - bio._rna_cofold("GGGGUUUU", "AAAACCCC")) < 1e-9

    def test_symmetric(self):
        assert abs(bio._rna_cofold("ACCUCCUUA", "UAAGGAGGU")
                   - bio._rna_cofold("UAAGGAGGU", "ACCUCCUUA")) < 1e-9

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            bio._rna_cofold("", "ACGU")
        with pytest.raises(ValueError):
            bio._rna_cofold("ACGU", "")

    def test_ambiguous_raises(self):
        with pytest.raises(ValueError):
            bio._rna_cofold("ACGUN", "ACGU")

    def test_overlength_raises(self):
        with pytest.raises(ValueError):
            bio._rna_cofold("A" * 300, "A" * 200)


class TestRbsStrength:
    """Relative RBS translation-initiation strength (`_rbs_strength`). The
    structural ΔGs are exact (folder/cofold, locked above); this validates
    the MODEL'S RELATIVE RANKING on the canonical determinants — the tuning
    utility — not absolute values (β / spacing / start are calibration)."""

    @staticmethod
    def _mk(utr, sd, spacer, codon="AUG", cds="AGCAAAGCAACU"):
        return utr + sd + spacer + codon + cds, len(utr + sd + spacer)

    def _tir(self, *a, **kw):
        seq, start = self._mk(*a, **kw)
        return bio._rbs_strength(seq, start)["rel_strength"]

    def test_sd_strength_ranking(self):
        assert (self._tir("AAUAAA", "AGGAGG", "AAUAA")
                > self._tir("AAUAAA", "AGAGG", "AAUAAA")
                > self._tir("AAUAAA", "CACACA", "AAUAA"))

    def test_start_codon_aug_beats_gug(self):
        assert (self._tir("AAUAAA", "AGGAGG", "AAUAA")
                > self._tir("AAUAAA", "AGGAGG", "AAUAA", codon="GUG"))

    def test_spacing(self):
        opt = self._tir("AAUAAA", "AGGAGG", "AAUAA")          # 5-nt spacer
        short = self._tir("AAUAAA", "AGGAGG", "AA")
        long_ = self._tir("AAUAAA", "AGGAGG", "AAUAAAAAAAA")
        assert opt > short and opt > long_
        assert short < long_        # too-short spacing penalised harder (steric)

    def test_5utr_structure_occludes(self):
        plain = self._tir("AAUAAA", "AGGAGG", "AAUAA")
        occluded = bio._rbs_strength("GGGCCGGAGGUGGCCCCCAUGAGCAAA", 18)["rel_strength"]
        assert occluded < plain / 10        # SD buried in a hairpin -> much weaker

    def test_result_dict_shape(self):
        r = bio._rbs_strength("AAUAAAAGGAGGAAUAAAUGAGCAAAGCAACU", 17)
        assert set(r) == {"dg_total", "dg_mrna", "dg_hybrid", "spacing", "rel_strength"}
        assert r["rel_strength"] > 0 and isinstance(r["spacing"], int)

    def test_dna_t_accepted(self):
        seq = "AAUAAAAGGAGGAAUAAAUGAGCAAAGCAACU"
        assert abs(bio._rbs_strength(seq, 17)["rel_strength"]
                   - bio._rbs_strength(seq.replace("U", "T"), 17)["rel_strength"]) < 1e-9

    def test_no_sd_room_returns_zero(self):
        assert bio._rbs_strength("AUGAAAAAAAAA", 0)["rel_strength"] == 0.0

    def test_bad_input_raises(self):
        for seq, st in (("AUGAAAAAA", 99), ("", 0), ("AUGNNN", 0),
                        ("AUGAAAAAA", 1.5), ("AUGAAAAAA", True)):
            with pytest.raises(ValueError):
                bio._rbs_strength(seq, st)

    def test_overlength_mrna_raises(self):
        # Caps the O(n) normalize/scan (the fold window itself is bounded),
        # mirroring the _rna_fold / _rna_cofold / _rbs_design length caps.
        with pytest.raises(ValueError):
            bio._rbs_strength("A" * (bio._RBS_STRENGTH_MAX_LEN + 1), 3)


class TestRbsDesign:
    """Reverse RBS design (`_rbs_design`) — search SD/spacer space for a
    target relative strength. A short CDS keeps the ~84-call search fast."""

    CDS = "AUGAGCAAAUACUAA"

    def test_design_predict_roundtrip(self):
        r = bio._rbs_design(self.CDS, 5.0)
        fwd = bio._rbs_strength(r["utr"] + self.CDS, len(r["utr"]))["rel_strength"]
        assert abs(r["rel_strength"] - fwd) < 1e-9        # design is a real RBS
        assert r["full"] == r["utr"] + self.CDS

    def test_result_shape(self):
        r = bio._rbs_design(self.CDS, 5.0)
        assert set(r) == {"utr", "full", "sd", "spacing", "rel_strength",
                          "dg_total", "achievable_min", "achievable_max", "on_target"}
        assert isinstance(r["spacing"], int)
        assert r["achievable_max"] >= r["achievable_min"]

    def test_monotonic_target(self):
        weak = bio._rbs_design(self.CDS, 0.1)["rel_strength"]
        strong = bio._rbs_design(self.CDS, 1e9)["rel_strength"]    # above range -> max
        assert strong > weak

    def test_in_range_target_on_target(self):
        achievable_max = bio._rbs_design(self.CDS, 1e9)["achievable_max"]
        r = bio._rbs_design(self.CDS, achievable_max * 0.4)
        assert r["on_target"] is True

    def test_out_of_range_flagged(self):
        r = bio._rbs_design(self.CDS, 1e12)
        assert r["on_target"] is False
        assert r["rel_strength"] <= r["achievable_max"] + 1e-9

    def test_dna_cds_accepted(self):
        assert abs(bio._rbs_design(self.CDS, 5.0)["rel_strength"]
                   - bio._rbs_design(self.CDS.replace("U", "T"), 5.0)["rel_strength"]) < 1e-9

    def test_bad_input_raises(self):
        for cds, tgt in ((self.CDS, -1), (self.CDS, "x"), ("", 5),
                         ("AUGNNN", 5), (self.CDS, True), ("AU", 5)):
            with pytest.raises(ValueError):
                bio._rbs_design(cds, tgt)


class TestAssembleOperon:
    """Context-aware operon assembly (`_assemble_operon`). Short CDSs keep
    the per-gene RBS search fast."""

    G = [{"cds": "AUGAGCAAAGGUGAAUACAAAUAA", "target_strength": 5.0, "name": "A"},
         {"cds": "AUGGCAGAAUGGCUGUUUACCUAA", "target": 2.0, "name": "B"}]
    PROM = "UUGACAGCUAGCUCAGUCCUAGGUAUAAU"

    def test_layout_tiles_sequence_exactly(self):
        # THE off-by-one guard: layout must tile the DNA with no gap or overlap
        r = bio._assemble_operon(self.G, promoter=self.PROM, terminator="UUUUUUUU")
        seq = r["sequence"]
        assert "U" not in seq                          # DNA output
        cursor = 0
        for el in r["layout"]:
            assert el["start"] == cursor               # contiguous
            assert seq[el["start"]:el["end"]]          # non-empty slice
            cursor = el["end"]
        assert cursor == len(seq)                      # covers the whole sequence
        assert [el["kind"] for el in r["layout"]] == \
            ["promoter", "rbs", "cds", "rbs", "cds", "terminator"]
        for el in r["layout"]:                         # each CDS verbatim at its slot
            if el["kind"] == "cds":
                cds = next(g["cds"] for g in self.G if g["name"] == el["name"])
                assert seq[el["start"]:el["end"]] == cds.replace("U", "T")

    def test_context_aware_hits_reachable_targets(self):
        r = bio._assemble_operon(self.G, promoter=self.PROM)
        for g in r["genes"]:                           # both targets reachable
            assert g["on_target"] is True
            assert abs(g["rel_strength"] - g["target"]) <= 0.25 * g["target"]

    def test_unreachable_target_flagged(self):
        r = bio._assemble_operon(
            [{"cds": "AUGGCAGAAUGGCUGUUUACCUAA", "target": 1e6, "name": "X"}],
            promoter=self.PROM)
        g = r["genes"][0]
        assert g["on_target"] is False and g["rel_strength"] < 1e6

    def test_genes_report_shape(self):
        r = bio._assemble_operon(self.G)
        assert len(r["genes"]) == 2
        for g in r["genes"]:
            assert {"name", "target", "cds_len", "rbs", "spacing",
                    "rel_strength", "on_target"} <= set(g)
            assert "U" not in g["rbs"]                  # DNA

    def test_single_gene_no_flanks(self):
        r = bio._assemble_operon([{"cds": "AUGAAAUACUAA", "target": 1.0}])
        assert [el["kind"] for el in r["layout"]] == ["rbs", "cds"]
        assert r["layout"][0]["start"] == 0

    def test_bad_input_raises(self):
        for bad in ([], "x", [{"target": 5}], [{"cds": "AU", "target": 5}],
                    [{"cds": "AUGAAAUAA", "target": -1}],
                    [{"cds": "AUGNNN", "target": 5}], [42]):
            with pytest.raises(ValueError):
                bio._assemble_operon(bad)
        with pytest.raises(ValueError):
            bio._assemble_operon(self.G, promoter="AUGN")    # bad promoter


class TestAuditRegressions:
    """Locks for bugs found in the 2026-06-07 adversarial audit."""

    def test_large_loop_truncates_like_vienna(self):
        # >30 nt loop: _loop_init must TRUNCATE the log extrapolation (not
        # round half-up), else it diverged from ViennaRNA by +0.01 kcal.
        # The whole 4221-case validation had max loop 22 nt, so it couldn't
        # catch this. -6.44 is the ViennaRNA-exact value (was -6.43 buggy).
        seq = "GGGGG" + "A" * 35 + "CCCCC"
        db = "(((((" + "." * 35 + ")))))"
        assert abs(bio._rna_eval_structure(seq, db) - (-6.44)) < 1e-9

    def test_eval_rejects_noncanonical_pair(self):
        # was an uncaught int22 IndexError
        with pytest.raises(ValueError):
            bio._rna_eval_structure("ACCGCCCCCCA", "(..(...)..)")

    def test_eval_rejects_infeasible_hairpin(self):
        # was a silent _RNA_INF leak (~1e7 kcal) for a <3 nt hairpin loop
        with pytest.raises(ValueError):
            bio._rna_eval_structure("GCGC", "(..)")

    def test_rbs_strength_no_inf(self):
        # start too close to the 5' end -> dg_total None (JSON-valid), NOT inf
        r = bio._rbs_strength("AAAAAAUGAAA", 5)
        assert r["dg_total"] is None and r["rel_strength"] == 0.0
        assert "Infinity" not in json.dumps(r)

    def test_rbs_design_rejects_nonfinite_target(self):
        for bad in (float("inf"), float("nan"), -float("inf")):
            with pytest.raises(ValueError):
                bio._rbs_design("AUGAAAUAA", bad)

    def test_rbs_design_rejects_overlong_cds(self):
        with pytest.raises(ValueError):
            bio._rbs_design("AUG" + "A" * 60000, 5.0)

    def test_assemble_operon_rejects_nonfinite_target(self):
        for bad in (float("inf"), float("nan")):
            with pytest.raises(ValueError):
                bio._assemble_operon([{"cds": "AUGAAAUACUAA", "target": bad}])
