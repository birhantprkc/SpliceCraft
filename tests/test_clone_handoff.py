"""
test_clone_handoff — Synthesis "Clone Fragment" → cloning-workflow handoff.

Covers the reworked flow (2026-06-09):
  * Entry-vector acceptor validation (`_gb_text_acceptor_cut_count`,
    `_entry_vector_is_valid_acceptor`) — the guard that stops a clone
    silently degrading to the bare-fragment stub on a fresh install.
  * The two L0 deliverables are DISTINCT and full-length: the primed
    linear fragment and the cloned full plasmid are never the bare
    unprimed insert (the user-reported "same fragment 3×" regression).
  * `CloneMethodChooserModal` opens on Clone Fragment with NO premature
    library save; picking a grammar routes to the Domesticator prefilled
    byte-exact; an unbound grammar pops the entry-vector picker.
  * Gibson / Traditional route to the Constructor with the fragment
    pre-pasted on the matching tab.
  * The DNA + Protein Clear buttons exist and reset their editor.
"""
from __future__ import annotations

import random

import pytest

import splicecraft as sc


_TERM = (200, 50)

# A small coding insert (ATG … stop), no internal Esp3I/BsaI.
_INSERT = ("ATGGCAAGCGGTGGTAGCGGTTCTGGTAGCGGTAGCGGTAGCGGTAGCGGTAGC"
           "AAAGAACTGAAAGCAGAACTGGAAGCACTGAAAGCAGAACTGGGTGGTAGC"
           "GATGAAGCAGCAAAAGCAGAAGCAGAAGCAAAAGCAGAGGCAGAAGCATAA")


def _scrub(s: str) -> str:
    for site in ("CGTCTC", "GAGACG", "GGTCTC", "GAGACC"):
        s = s.replace(site, "CTGCAG")
    return s


def _make_acceptor_gb(n_esp3i: int = 2) -> str:
    """Build a circular gb_text acceptor with exactly ``n_esp3i`` Esp3I
    (CGTCTC) sites flanking a dropout, in an otherwise Esp3I-free
    ~1.4 kb backbone. With ≥2 inward sites the IIS clone simulation
    produces a real plasmid; with <2 it must be rejected as an acceptor."""
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord
    rng = random.Random(0xBEEF)
    backbone = _scrub("".join(rng.choice("ACGT") for _ in range(1400)))
    dropout = _scrub("".join(rng.choice("ACGT") for _ in range(160)))
    if n_esp3i >= 2:
        # Two inward Esp3I sites (left CGTCTC, right GAGACG = CGTCTC on
        # the bottom strand) flanking the dropout — the canonical UPD
        # layout, releasing 4-nt overhangs.
        cassette = "CGTCTCA" + "CTCG" + dropout + "TGAG" + "AGAGACG"
    elif n_esp3i == 1:
        cassette = "CGTCTCA" + "CTCG" + dropout
    else:
        cassette = dropout
    seq = backbone[:700] + cassette + backbone[700:]
    rec = SeqRecord(Seq(seq), id="TESTUPD", name="TESTUPD",
                    description="synthetic L0 acceptor",
                    annotations={"molecule_type": "DNA", "topology": "circular"})
    return sc._record_to_gb_text(rec)


# ═══════════════════════════════════════════════════════════════════════════════
# Entry-vector acceptor validation (unit)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAcceptorValidation:
    def _g(self):
        return sc._BUILTIN_GRAMMARS["gb_l0"]

    def test_two_site_vector_is_valid_acceptor(self):
        gb = _make_acceptor_gb(2)
        n = sc._gb_text_acceptor_cut_count(gb, self._g())
        assert n >= 2, f"expected ≥2 Esp3I cuts, got {n}"
        assert sc._entry_vector_is_valid_acceptor({"gb_text": gb}, self._g())

    def test_one_site_vector_is_rejected(self):
        gb = _make_acceptor_gb(1)
        assert sc._gb_text_acceptor_cut_count(gb, self._g()) < 2
        assert not sc._entry_vector_is_valid_acceptor({"gb_text": gb}, self._g())

    def test_no_site_vector_is_rejected(self):
        gb = _make_acceptor_gb(0)
        assert sc._gb_text_acceptor_cut_count(gb, self._g()) == 0
        assert not sc._entry_vector_is_valid_acceptor({"gb_text": gb}, self._g())

    def test_empty_and_none_are_rejected(self):
        assert sc._gb_text_acceptor_cut_count("", self._g()) == 0
        assert not sc._entry_vector_is_valid_acceptor(None, self._g())
        assert not sc._entry_vector_is_valid_acceptor({}, self._g())

    def test_unknown_enzyme_grammar_is_rejected(self):
        gb = _make_acceptor_gb(2)
        assert sc._gb_text_acceptor_cut_count(gb, {"enzyme": "NotAnEnzyme"}) == 0
        assert sc._gb_text_acceptor_cut_count(gb, {}) == 0

    def test_garbage_gb_text_does_not_raise(self):
        # Never raises into the picker — returns 0 on unparseable input.
        assert sc._gb_text_acceptor_cut_count("not a genbank file", self._g()) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Two deliverables are distinct + full-length (unit)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCloneDeliverables:
    def _part_with_vector(self):
        g = sc._BUILTIN_GRAMMARS["gb_l0"]
        gb = _make_acceptor_gb(2)
        sc._set_entry_vector("gb_l0", {
            "name": "TESTUPD", "size": 0, "source": "test",
            "id": "TESTUPD", "gb_text": gb,
        })
        cds_type = next((p["type"] for p in g["positions"]
                         if p["type"] in (g.get("coding_types") or [])),
                        g["positions"][0]["type"])
        d = sc._design_gb_primers(_INSERT, 0, len(_INSERT), cds_type,
                                  codon_raw=None, grammar=g)
        assert not d.get("error"), d.get("error")
        part = {
            "name": "TCDS", "type": d["part_type"], "position": d["position"],
            "oh5": d["oh5"], "oh3": d["oh3"], "backbone": "TESTUPD", "marker": "—",
            "sequence": d["insert_seq"], "fwd_primer": d["fwd_full"],
            "rev_primer": d["rev_full"], "fwd_primer_name": "TCDS-DOM-1-F",
            "rev_primer_name": "TCDS-DOM-1-R", "fwd_tm": d["fwd_tm"],
            "rev_tm": d["rev_tm"], "grammar": "gb_l0",
        }
        return part, d["insert_seq"]

    def test_primed_fragment_is_not_the_bare_insert(self):
        part, insert = self._part_with_vector()
        fr = sc._part_to_primed_fragment_seqrecord(part, name="FRAG")
        frs = str(fr.seq).upper()
        assert frs != insert
        assert insert[6:-6] in frs            # full body preserved
        assert "CGTCTC" in frs or "GAGACG" in frs   # carries the enzyme site
        assert sum(1 for f in fr.features if f.type == "primer_bind") == 2
        assert fr.annotations.get("topology") == "linear"

    def test_cloned_plasmid_is_full_and_not_stub(self):
        part, insert = self._part_with_vector()
        cl = sc._part_to_cloned_seqrecord(part)
        cls = str(cl.seq).upper()
        stub = sc._simulate_cloned_plasmid(insert, part["oh5"], part["oh3"],
                                           part["type"])
        assert cls != stub, "clone degraded to the pUPD2 stub fallback"
        assert cl.annotations.get("topology") == "circular"
        assert len(cls) > len(insert) + 1000      # full plasmid, not truncated
        assert insert[6:-6] in cls                 # the part is intact

    def test_clone_fragment_and_insert_all_distinct(self):
        # The exact "saves the same unprimed fragment 3×" regression guard.
        part, insert = self._part_with_vector()
        fr = str(sc._part_to_primed_fragment_seqrecord(part, name="F").seq).upper()
        cl = str(sc._part_to_cloned_seqrecord(part).seq).upper()
        assert len({insert, fr, cl}) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# Clone Fragment handoff routing (async / Pilot)
# ═══════════════════════════════════════════════════════════════════════════════

def _bind_test_vector():
    sc._set_entry_vector("gb_l0", {
        "name": "TESTUPD", "size": 0, "source": "test",
        "id": "TESTUPD", "gb_text": _make_acceptor_gb(2),
    })


async def _open_synthesis_with_seq(app, pilot, seq):
    for _ in range(6):
        await pilot.pause()
    while len(app.screen_stack) > 1:
        app.pop_screen()
        for _ in range(2):
            await pilot.pause()
    app.action_open_synthesis()
    for _ in range(6):
        await pilot.pause()
    ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
    ed._seq = seq
    return app.screen


class TestCloneFragmentHandoff:
    @pytest.mark.asyncio
    async def test_chooser_opens_with_no_premature_save(self):
        _bind_test_vector()
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            syn = await _open_synthesis_with_seq(app, pilot, _INSERT)
            before = len(sc._load_library())
            syn.action_clone_fragment()
            for _ in range(5):
                await pilot.pause()
            assert type(app.screen).__name__ == "CloneMethodChooserModal"
            assert len(sc._load_library()) == before   # NOTHING saved yet

    @pytest.mark.asyncio
    async def test_grammar_choice_routes_to_domesticator_byte_exact(self):
        _bind_test_vector()
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            syn = await _open_synthesis_with_seq(app, pilot, _INSERT)
            before = len(sc._load_library())
            syn.action_clone_fragment()
            for _ in range(5):
                await pilot.pause()
            app.screen.dismiss({"method": "grammar", "grammar_id": "gb_l0"})
            for _ in range(8):
                await pilot.pause()
            stack = [type(s).__name__ for s in app.screen_stack]
            assert "PartsBinModal" in stack
            assert type(app.screen).__name__ == "DomesticatorModal"
            ta = app.screen.query_one("#dom-direct-seq", sc.TextArea)
            assert ta.text.upper() == _INSERT      # byte-exact, no missed bases
            assert len(sc._load_library()) == before   # still no premature save

    @pytest.mark.asyncio
    async def test_unbound_grammar_pops_entry_vector_picker(self):
        sc._set_entry_vector("gb_l0", None)         # fresh-install: no vector
        # ...but a plasmid in the library to pick an acceptor from.
        sc._save_library([{
            "id": "acc1", "name": "acceptor", "gb_text": _make_acceptor_gb(2),
            "size": 0, "n_feats": 0, "source": "test", "added": "2026-06-09",
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            syn = await _open_synthesis_with_seq(app, pilot, _INSERT)
            syn.action_clone_fragment()
            for _ in range(5):
                await pilot.pause()
            app.screen.dismiss({"method": "grammar", "grammar_id": "gb_l0"})
            for _ in range(6):
                await pilot.pause()
            assert type(app.screen).__name__ == "PlasmidPickerModal"

    @pytest.mark.asyncio
    async def test_empty_library_no_vector_guides_user(self):
        # Fresh install: no entry vector AND no plasmids → don't dead-end on
        # an empty picker; stay in Synthesis (the user is told to fetch an
        # acceptor first) and write nothing.
        sc._set_entry_vector("gb_l0", None)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            syn = await _open_synthesis_with_seq(app, pilot, _INSERT)
            assert sc._load_library() == []
            syn.action_clone_fragment()
            for _ in range(5):
                await pilot.pause()
            app.screen.dismiss({"method": "grammar", "grammar_id": "gb_l0"})
            for _ in range(6):
                await pilot.pause()
            assert type(app.screen).__name__ == "SynthesisScreen"
            assert sc._load_library() == []


class TestConstructorSeed:
    @pytest.mark.asyncio
    async def test_gibson_route_prefills_paste_box(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app.push_screen(sc.ConstructorModal(
                seed_seq=_INSERT, seed_name="F1", seed_method="gibson"))
            for _ in range(10):
                await pilot.pause()
            tabs = app.screen.query_one("#ctor-tabs", sc.TabbedContent)
            assert tabs.active == "ctor-tab-gibson"
            assert app.screen.query_one("#gib-pcr-seq", sc.TextArea).text.upper() == _INSERT

    @pytest.mark.asyncio
    async def test_traditional_route_prefills_paste_box(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app.push_screen(sc.ConstructorModal(
                seed_seq=_INSERT, seed_name="F1", seed_method="traditional"))
            for _ in range(10):
                await pilot.pause()
            tabs = app.screen.query_one("#ctor-tabs", sc.TabbedContent)
            assert tabs.active == "ctor-tab-traditional"
            assert app.screen.query_one("#trad-pcr-seq", sc.TextArea).text.upper() == _INSERT


class TestSynthesisClearButtons:
    @pytest.mark.asyncio
    async def test_dna_clear_empties_editor(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            syn = await _open_synthesis_with_seq(app, pilot, "ATGAAACGTTAA")
            syn.query_one("#btn-syn-dna-clear", sc.Button).press()
            for _ in range(4):
                await pilot.pause()
            assert syn.query_one("#syn-editor", sc.SynthesisEditor).get_state()[0] == ""

    @pytest.mark.asyncio
    async def test_protein_tab_has_clear_button(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app.action_open_synthesis()
            for _ in range(6):
                await pilot.pause()
            app.screen.query_one("#syn-tabs", sc.TabbedContent).active = "syn-tab-protein"
            for _ in range(5):
                await pilot.pause()
            assert app.screen.query_one("#btn-syn-protein-clear", sc.Button) is not None
