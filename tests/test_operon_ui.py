"""test_operon_ui — the Operon Design tab in the Synthesis screen.

Drives the real `SynthesisScreen` via Textual's `run_test`. The autouse
`_protect_user_data` fixture sandboxes the data dir, so seeding protein
collections here is safe.
"""
import pytest

import splicecraft as sc
from textual.widgets import DataTable, Input, Select, TabbedContent

_TERM = (180, 50)


class TestOperonTabRendering:
    async def test_tab_lists_collections_and_proteins(self):
        sc._protein_collection_add("Lux demo", "luxC", "MKFGLFFLNFINSTT")
        sc._protein_collection_add("Lux demo", "luxD", "MNKDIAYLPGTHQF")
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.action_open_synthesis()
            await pilot.pause()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, sc.SynthesisScreen)
            # the operon pane is composed at mount; on_mount populated it
            assert screen._operon_coll_choice == "Lux demo"
            sel = screen.query_one("#syn-operon-coll-select", Select)
            assert sel.value == "Lux demo"
            pt = screen.query_one("#syn-operon-prot-table", DataTable)
            assert pt.row_count == 2
            lane = screen.query_one("#syn-operon-lane", DataTable)
            assert lane.row_count == 0          # nothing added to the operon yet
            # switching to the tab keeps everything intact
            tabs = screen.query_one("#syn-tabs", TabbedContent)
            tabs.active = "syn-tab-operon"
            await pilot.pause()
            await pilot.pause()
            assert pt.row_count == 2

    async def test_tab_handles_no_collections(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.action_open_synthesis()
            await pilot.pause()
            await pilot.pause()
            screen = app.screen
            assert screen._operon_coll_choice == ""        # no crash, empty
            pt = screen.query_one("#syn-operon-prot-table", DataTable)
            assert pt.row_count == 0


class TestOperonBuilderFlow:
    async def _open(self, pilot, app):
        await pilot.pause()
        await pilot.pause()
        app.action_open_synthesis()
        await pilot.pause()
        await pilot.pause()
        return app.screen

    async def test_add_assemble_reorder_remove(self):
        sc._protein_collection_add("Lux", "luxA", "MKFLENISSTVQ")
        sc._protein_collection_add("Lux", "luxB", "MGDKNIYACFLW")
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            screen = await self._open(pilot, app)
            pt = screen.query_one("#syn-operon-prot-table", DataTable)
            pt.move_cursor(row=0); screen._operon_add_gene(None)
            pt.move_cursor(row=1); screen._operon_add_gene(None)
            await pilot.pause()
            assert [g["name"] for g in screen._operon_genes] == ["luxA", "luxB"]
            g0 = screen._operon_genes[0]
            assert g0["cds"].startswith("ATG") and g0["cds"][-3:] in ("TAA", "TAG", "TGA")
            assert screen.query_one("#syn-operon-lane", DataTable).row_count == 2
            # assemble with a promoter (pause so the Input.Changed that
            # setting .value posts is processed BEFORE Assemble — in the real
            # UI the user types, then clicks, so the flank-invalidation has
            # already settled)
            screen.query_one("#syn-operon-promoter", Input).value = \
                "TTGACAGCTAGCTCAGTCCTAGGTATAAT"
            await pilot.pause()
            screen._operon_genes[0]["target"] = 50.0
            screen._operon_assemble(None)
            await pilot.pause()
            res = screen._operon_result
            assert res is not None
            assert "U" not in res["sequence"] and res["sequence"].startswith("TTGACA")
            assert screen._operon_genes[0]["achieved"] is not None
            # reorder invalidates the result
            lane = screen.query_one("#syn-operon-lane", DataTable)
            lane.move_cursor(row=1); screen._operon_lane_move(-1)
            assert [g["name"] for g in screen._operon_genes] == ["luxB", "luxA"]
            assert screen._operon_result is None
            # remove the gene at the cursor
            screen._operon_remove(None)
            assert len(screen._operon_genes) == 1

    async def test_new_collection_modal(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            screen = await self._open(pilot, app)
            screen._operon_new_coll(None)                  # pushes the name modal
            await pilot.pause()
            await pilot.pause()
            modal = app.screen
            modal.query_one("#gname-input", Input).value = "My Operon"
            modal.action_submit()
            await pilot.pause()
            await pilot.pause()
            assert screen._operon_coll_choice == "My Operon"
            assert "My Operon" in {c["name"] for c in sc._load_protein_collections()}

    async def test_assemble_empty_is_safe(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            screen = await self._open(pilot, app)
            screen._operon_assemble(None)                  # no genes -> notify, no crash
            await pilot.pause()
            assert screen._operon_result is None

    async def test_add_gene_invalidates_prior_assembly(self):
        sc._protein_collection_add("Lux", "luxA", "MKFLENISSTVQ")
        sc._protein_collection_add("Lux", "luxB", "MGDKNIYACFLW")
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            screen = await self._open(pilot, app)
            pt = screen.query_one("#syn-operon-prot-table", DataTable)
            pt.move_cursor(row=0)
            screen._operon_add_gene(None)
            screen._operon_assemble(None)
            await pilot.pause()
            assert screen._operon_result is not None
            pt.move_cursor(row=1)               # add a second gene -> must invalidate
            screen._operon_add_gene(None)
            assert screen._operon_result is None
            assert all(g["achieved"] is None for g in screen._operon_genes)

    async def test_to_dna_tab_exports_annotated(self):
        sc._protein_collection_add("Lux", "luxA", "MKFLENISSTVQ")
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            screen = await self._open(pilot, app)
            pt = screen.query_one("#syn-operon-prot-table", DataTable)
            pt.move_cursor(row=0)
            screen._operon_add_gene(None)
            screen._operon_assemble(None)
            await pilot.pause()
            screen._operon_to_dna(None)
            await pilot.pause()
            ed = screen.query_one("#syn-editor", sc.SynthesisEditor)
            seq, feats = ed.get_state()
            assert seq == screen._operon_result["sequence"]
            assert any(f["type"] == "CDS" for f in feats)
            assert screen.query_one("#syn-tabs", TabbedContent).active == "syn-tab-dna"


class TestOperonTranslate:
    def test_translate_cds_to_protein(self):
        s = sc.SynthesisScreen()
        assert s._operon_translate("ATGAAATTT") == "MKF"
        assert s._operon_translate("ATGTAA") == "M"        # stops at first stop
        assert s._operon_translate("atgaaattt") == "MKF"   # case + frame trim
        assert s._operon_translate("ATGAAATT") == "MK"     # ragged tail trimmed
        assert s._operon_translate("xyz") is None
        assert s._operon_translate("") is None


class _FakeHandle:
    """Mimics Entrez.efetch's context-manager handle."""
    def __init__(self, text):
        self._t = text
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self, n=-1):
        return self._t


class TestFetchProtein:
    def test_parses_fasta_and_checks_accession(self, monkeypatch):
        from Bio import Entrez
        fasta = ">AAK98552.1 luxC [Photorhabdus luminescens]\nMKFLENISSTVQ\n"
        monkeypatch.setattr(Entrez, "efetch", lambda **kw: _FakeHandle(fasta))
        desc, seq = sc.fetch_protein("AAK98552")
        assert seq == "MKFLENISSTVQ" and "luxC" in desc

    def test_accession_mismatch_raises(self, monkeypatch):
        from Bio import Entrez
        fasta = ">XYZ99999.1 something else\nMKKK\n"
        monkeypatch.setattr(Entrez, "efetch", lambda **kw: _FakeHandle(fasta))
        with pytest.raises(ValueError):
            sc.fetch_protein("AAK98552")

    def test_empty_sequence_raises(self, monkeypatch):
        from Bio import Entrez
        monkeypatch.setattr(Entrez, "efetch",
                            lambda **kw: _FakeHandle(">AAK98552.1 x\n\n"))
        with pytest.raises(ValueError):
            sc.fetch_protein("AAK98552")


class TestOperonFetchButton:
    async def _open(self, pilot, app):
        await pilot.pause(); await pilot.pause()
        app.action_open_synthesis()
        await pilot.pause(); await pilot.pause()
        return app.screen

    async def test_fetch_adds_protein(self, monkeypatch):
        monkeypatch.setattr(sc, "fetch_protein",
                            lambda acc, email="x": ("luxC [demo]", "MKFLENISSTVQ"))
        sc._protein_collection_create("Lux")
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            screen = await self._open(pilot, app)
            screen._operon_coll_choice = "Lux"
            screen._operon_fetch_worker("Lux", "AAK98552")
            await app.workers.wait_for_complete()
            await pilot.pause(); await pilot.pause()
            lux = next(c for c in sc._load_protein_collections()
                       if c["name"] == "Lux")
            p = next((x for x in lux["proteins"] if x["name"] == "AAK98552"), None)
            assert p is not None and p["sequence"] == "MKFLENISSTVQ"
            assert p["source"] == "NCBI:AAK98552"

    async def test_fetch_failure_adds_nothing(self, monkeypatch):
        def _boom(acc, email="x"):
            raise ValueError("obsolete accession")
        monkeypatch.setattr(sc, "fetch_protein", _boom)
        sc._protein_collection_create("Lux")
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            screen = await self._open(pilot, app)
            screen._operon_coll_choice = "Lux"
            screen._operon_fetch_worker("Lux", "BADACC")
            await app.workers.wait_for_complete()
            await pilot.pause(); await pilot.pause()
            lux = next(c for c in sc._load_protein_collections()
                       if c["name"] == "Lux")
            assert lux["proteins"] == []
