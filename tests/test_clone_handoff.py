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


def _clone_region_plasmid():
    """A varied circular plasmid so cloning-primer Tm lands near 60."""
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord
    b = "ACGT"
    seq = "".join(b[(i * 7 + (i * i) // 11 + i // 3) % 4] for i in range(600))
    rec = SeqRecord(Seq(seq), id="CloneRegionTest",
                    name="Clone Region Test",
                    annotations={"molecule_type": "DNA",
                                 "topology": "circular"})
    return rec, seq


async def _load_clone_region_plasmid(app, pilot):
    rec, seq = _clone_region_plasmid()
    for _ in range(6):
        await pilot.pause()
    while len(app.screen_stack) > 1:
        app.pop_screen()
        for _ in range(2):
            await pilot.pause()
    app._apply_record(rec)
    for _ in range(6):
        await pilot.pause()
    return rec, seq


class TestCloneRegion:
    """One-click "Clone selected region" (File ▸ Clone selected region):
    an arbitrary seq-panel highlight is PCR-tailed with two restriction
    sites and dropped into the Constructor's Traditional tab as a fully-
    configured donor."""

    def test_designs_tailed_amplicon_binding_region(self):
        _, seq = _clone_region_plasmid()
        d = sc._design_cloning_primers(seq, 100, 400, "EcoRI", "BamHI")
        assert not d.get("error"), d
        # Catastrophic-class: the forward primer's 3' binding IS the
        # region's 5' end (a cloning primer must anneal where it claims).
        assert d["fwd_binding"] == d["insert_seq"][:len(d["fwd_binding"])]
        assert d["fwd_full"].startswith("GCGC" + d["site_5"])
        amplicon = ("GCGC" + d["site_5"] + d["insert_seq"]
                    + d["site_3"] + sc._rc("GCGC"))
        # The added enzyme sites must be present so the later digest can
        # release the insert.
        assert "GAATTC" in amplicon and "GGATCC" in amplicon

    @pytest.mark.asyncio
    async def test_flow_seeds_constructor_with_configured_donor(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (100, 400)          # mark arbitrary DNA
            app.action_clone_region()
            for _ in range(6):
                await pilot.pause()
            assert isinstance(app.screen, sc.CloneRegionEnzymeModal)
            app.screen.dismiss({"enz5": "EcoRI", "enz3": "BamHI"})
            for _ in range(16):
                await pilot.pause()
            assert isinstance(app.screen, sc.ConstructorModal)
            pane = app.screen.query_one(sc.TraditionalCloningPane)
            donors = [s for s in pane._lane_inserts
                      if s.get("mode") == "pcr"]
            assert donors, "no PCR donor seeded into the Traditional lane"
            assert donors[0]["enz_left"] == "EcoRI"
            assert donors[0]["enz_right"] == "BamHI"
            assert "GAATTC" in donors[0]["pcr_seq"]
            saved = {p.get("name") for p in sc._load_primers()}
            assert any(n and n.endswith("-F") for n in saved)
            assert any(n and n.endswith("-R") for n in saved)

    @pytest.mark.asyncio
    async def test_no_selection_warns_no_modal(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = None
            sp._sel_range = None
            app.action_clone_region()
            for _ in range(4):
                await pilot.pause()
            assert not isinstance(app.screen, sc.CloneRegionEnzymeModal)

    @pytest.mark.asyncio
    async def test_short_region_warns_no_modal(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (100, 110)          # 10 bp < 18
            app.action_clone_region()
            for _ in range(4):
                await pilot.pause()
            assert not isinstance(app.screen, sc.CloneRegionEnzymeModal)

    # ── Hardening (adversarial review F1/F2/F3): catastrophic-class refusals ──

    async def _clone_refused(self, app, pilot, sel, enz5, enz3):
        sp = app.query_one("#seq-panel", sc.SequencePanel)
        sp._user_sel = sel
        sp._sel_range = None
        app.action_clone_region()
        for _ in range(6):
            await pilot.pause()
        if not isinstance(app.screen, sc.CloneRegionEnzymeModal):
            return True    # gated before the modal — also a refusal
        app.screen.dismiss({"enz5": enz5, "enz3": enz3})
        for _ in range(10):
            await pilot.pause()
        return not isinstance(app.screen, sc.ConstructorModal)

    def test_type_iis_detector(self):
        assert sc._enzyme_is_type_iis("BsaI")
        assert sc._enzyme_is_type_iis("BsmBI")
        assert not sc._enzyme_is_type_iis("EcoRI")
        assert not sc._enzyme_is_type_iis("BamHI")

    @pytest.mark.asyncio
    async def test_type_iis_enzyme_refused(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            assert await self._clone_refused(app, pilot, (100, 400),
                                             "BsaI", "BamHI")

    @pytest.mark.asyncio
    async def test_short_region_overlapping_primers_refused(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            assert await self._clone_refused(app, pilot, (100, 118),
                                             "HindIII", "SalI")

    @pytest.mark.asyncio
    async def test_internal_recognition_site_refused(self):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        b = "ACGT"
        sl = list("".join(b[(i * 7 + i // 3) % 4] for i in range(600)))
        sl[200:206] = list("GAATTC")            # EcoRI site INSIDE [100,400)
        rec = SeqRecord(Seq("".join(sl)), id="Int", name="Int",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app._apply_record(rec)
            for _ in range(6):
                await pilot.pause()
            assert await self._clone_refused(app, pilot, (100, 400),
                                             "EcoRI", "BamHI")

    @pytest.mark.asyncio
    async def test_region_features_carry_into_donor(self):
        """The cloned region's own features must ride into the Traditional PCR
        donor (and thence the product) — not vanish into a featureless insert."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        b = "ACGT"
        seq = "".join(b[(i * 7 + i // 3) % 4] for i in range(600))
        rec = SeqRecord(Seq(seq), id="R", name="R",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features.append(SeqFeature(FeatureLocation(150, 250, strand=1),
                            type="CDS", qualifiers={"label": ["TU-CDS"]}))
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app._apply_record(rec)
            for _ in range(6):
                await pilot.pause()
            app.query_one("#seq-panel", sc.SequencePanel)._user_sel = (100, 400)
            app.action_clone_region()
            for _ in range(6):
                await pilot.pause()
            assert isinstance(app.screen, sc.CloneRegionEnzymeModal)
            app.screen.dismiss({"enz5": "EcoRI", "enz3": "BamHI"})
            for _ in range(16):
                await pilot.pause()
            pane = app.screen.query_one(sc.TraditionalCloningPane)
            donors = [s for s in pane._lane_inserts if s.get("mode") == "pcr"]
            assert donors, "no PCR donor seeded"
            pf = donors[0].get("pcr_features") or []
            assert any(f.get("label") == "TU-CDS" for f in pf), \
                f"region feature not carried into the donor: {pf}"

    def test_clone_region_shortcut_bound(self):
        """Alt+Shift+P triggers Clone selected region, without clobbering the
        existing Alt+Shift+C (Capture → Feature library) binding."""
        binds = {(b.key, b.action) for b in sc.PlasmidApp.BINDINGS
                 if hasattr(b, "key")}
        assert ("alt+shift+p", "clone_region") in binds
        assert ("alt+shift+c", "capture_to_features") in binds

    def test_clone_region_modal_prefills_pcr_name(self):
        """The enzyme modal carries a "PCR-…" amplicon-name default (mirrors
        Synthesis's "FRAG-…"); the bare default is just "PCR-"."""
        m = sc.CloneRegionEnzymeModal(default_name="PCR-pUC19 100-400")
        assert m._default_name == "PCR-pUC19 100-400"
        assert sc.CloneRegionEnzymeModal()._default_name == "PCR-"

    def test_gather_region_feats_carries_all_spanning(self):
        """ALL features overlapping the region — fully-inside AND partially-
        spanning either edge — ride into the clone, clipped to region-local
        coords. Pseudo overlays (site/recut/source) + non-overlapping features
        are skipped."""
        feats = [
            {"type": "CDS",  "label": "inside",     "start": 120, "end": 180, "strand": 1},
            {"type": "CDS",  "label": "span_left",  "start": 50,  "end": 150, "strand": 1},
            {"type": "CDS",  "label": "span_right", "start": 180, "end": 260, "strand": 1},
            {"type": "CDS",  "label": "outside",    "start": 300, "end": 400, "strand": 1},
            {"type": "site", "label": "EcoRI",      "start": 130, "end": 136, "strand": 0},
        ]
        out = sc.PlasmidApp._gather_region_feats(feats, 100, 200)
        labels = {f["label"] for f in out}
        assert {"inside", "span_left", "span_right"} <= labels
        assert "outside" not in labels and "EcoRI" not in labels
        sl = next(f for f in out if f["label"] == "span_left")
        assert sl["start"] == 0 and sl["end"] == 50      # 50..150 clipped to region

    @pytest.mark.asyncio
    async def test_custom_amplicon_name_flows_to_donor(self):
        """The PCR-name textbox value rides through to the seeded donor row so
        the user can name the amplicon they're about to clone + save."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (100, 400)
            app.action_clone_region()
            for _ in range(6):
                await pilot.pause()
            assert isinstance(app.screen, sc.CloneRegionEnzymeModal)
            app.screen.dismiss({"enz5": "EcoRI", "enz3": "BamHI",
                                "name": "PCR-myInsert"})
            for _ in range(16):
                await pilot.pause()
            pane = app.screen.query_one(sc.TraditionalCloningPane)
            donors = [s for s in pane._lane_inserts if s.get("mode") == "pcr"]
            assert donors, "no PCR donor seeded"
            assert donors[0]["pcr_name"] == "PCR-myInsert"

    @pytest.mark.asyncio
    async def test_modal_name_sanitized_and_capped(self):
        """The modal strips control bytes from a pasted name and caps its
        length before dismissing — a hostile/giant name can't bloat the donor
        row, primer names, or the toast."""
        from textual.widgets import Input as _Input
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(4):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            captured: dict = {}
            app.push_screen(sc.CloneRegionEnzymeModal(default_name="PCR-x"),
                            lambda r: captured.update(r=r))
            for _ in range(4):
                await pilot.pause()
            modal = app.screen
            assert isinstance(modal, sc.CloneRegionEnzymeModal)
            modal.query_one("#cre-name", _Input).value = (
                "PCR-\x00\x07ab\x1f" + "Z" * 80)
            modal._submit()
            for _ in range(6):
                await pilot.pause()
            nm = captured["r"]["name"]
            assert all(c not in nm for c in "\x00\x07\x1f")
            assert nm.startswith("PCR-ab")          # control bytes excised
            assert len(nm) <= 64                     # capped

    @pytest.mark.asyncio
    async def test_clone_build_blank_name_falls_back_to_auto(self):
        """A name that's only whitespace / control bytes (e.g. handed in via
        the agent API) collapses to the auto "<plasmid> <start>-<end>" label
        rather than naming the donor an empty/garbage string."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (100, 400)
            app.action_clone_region()
            for _ in range(6):
                await pilot.pause()
            assert isinstance(app.screen, sc.CloneRegionEnzymeModal)
            app.screen.dismiss({"enz5": "EcoRI", "enz3": "BamHI",
                                "name": "  \x00\x07  "})
            for _ in range(16):
                await pilot.pause()
            pane = app.screen.query_one(sc.TraditionalCloningPane)
            donors = [s for s in pane._lane_inserts if s.get("mode") == "pcr"]
            assert donors
            nm = donors[0]["pcr_name"]
            assert all(c not in nm for c in "\x00\x07")
            assert "101-400" in nm                   # auto label (start+1..end)


def _plasmid_with_feat():
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord
    from Bio.SeqFeature import SeqFeature, FeatureLocation
    b = "ACGT"
    seq = "".join(b[(i * 7 + i // 3) % 4] for i in range(600))
    rec = SeqRecord(Seq(seq), id="FeatP", name="Feat P",
                    annotations={"molecule_type": "DNA",
                                 "topology": "circular"})
    rec.features.append(SeqFeature(FeatureLocation(120, 200, strand=1),
                        type="misc_feature", qualifiers={"label": ["MyFeat"]}))
    return rec, seq


class TestFeatureRichCopy:
    """Copying a selection stashes its features (rebased) on the app, and a
    matching paste into the Synthesis editor carries them in."""

    def test_gather_region_feats_rebases_and_clips(self):
        feats = [{"start": 120, "end": 200, "type": "misc_feature",
                  "label": "In", "color": "cyan", "strand": 1},
                 {"start": 250, "end": 350, "type": "gene", "label": "Stradl"},
                 {"start": 50, "end": 90, "type": "CDS", "label": "Out"},
                 {"start": 5, "end": 5, "type": "x"}]
        g = sc.PlasmidApp._gather_region_feats(feats, 100, 300)
        assert any(f["label"] == "In" and f["start"] == 20 and f["end"] == 100
                   for f in g)
        assert any(f["label"] == "Stradl" and f["start"] == 150
                   and f["end"] == 200 for f in g)        # clipped to span
        assert not any(f["label"] == "Out" for f in g)    # outside the span

    def test_render_keys_carried_only_when_contained(self):
        # Fully-contained CDS keeps codon_start / transl_table (reading
        # frame); a CLIPPED CDS drops them — its codon_start is relative to
        # the original off-selection start, so it'd mis-frame after rebasing
        # (adversarial review F4).
        feats = [{"start": 120, "end": 200, "type": "CDS", "label": "Cont",
                  "codon_start": 2, "transl_table": 11},
                 {"start": 50, "end": 150, "type": "CDS", "label": "Clip",
                  "codon_start": 3}]
        g = sc.PlasmidApp._gather_region_feats(feats, 100, 300)
        cont = next(f for f in g if f["label"] == "Cont")
        clip = next(f for f in g if f["label"] == "Clip")
        assert cont.get("codon_start") == 2 and cont.get("transl_table") == 11
        assert "codon_start" not in clip

    @pytest.mark.asyncio
    async def test_copy_then_synthesis_paste_carries_features(self):
        from textual.events import Paste
        rec, seq = _plasmid_with_feat()
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app._apply_record(rec)
            for _ in range(6):
                await pilot.pause()
            app.query_one("#seq-panel", sc.SequencePanel)._user_sel = (100, 300)
            app.action_copy_selection()
            for _ in range(4):
                await pilot.pause()
            cr = getattr(app, "_copied_region", None)
            assert cr and cr["seq"] == seq[100:300]
            assert any(f["label"] == "MyFeat" and f["start"] == 20
                       and f["end"] == 100 for f in cr["feats"])
            app.action_open_synthesis()
            for _ in range(10):
                await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.focus()
            for _ in range(2):
                await pilot.pause()
            try:
                ev = Paste(cr["seq"])
            except TypeError:
                ev = Paste(text=cr["seq"])
            ed.on_paste(ev)
            for _ in range(6):
                await pilot.pause()
            assert len(ed._seq) == 200
            assert any(f.get("label") == "MyFeat" and f.get("start") == 20
                       and f.get("end") == 100 for f in ed._feats)

    @pytest.mark.asyncio
    async def test_bottom_strand_copy_carries_no_features(self):
        rec, _ = _plasmid_with_feat()
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app._apply_record(rec)
            for _ in range(6):
                await pilot.pause()
            app.query_one("#seq-panel", sc.SequencePanel)._user_sel = (100, 300)
            app.action_copy_selection_bottom()
            for _ in range(4):
                await pilot.pause()
            assert getattr(app, "_copied_region", "x") is None


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
