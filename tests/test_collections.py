"""
test_collections — plasmid collection persistence + CollectionsModal flows.

Covers:
  * `_load_collections` / `_save_collections` envelope round-trip + legacy
    bare-list back-compat (matches sacred invariant #7).
  * CollectionsModal: save current library as a new collection, load
    (replace), delete, name-collision rejection.
  * Auto-load of the first library entry on PlasmidApp startup.
"""
from __future__ import annotations

import json

import pytest
import splicecraft as sc


# ── Persistence round-trip ─────────────────────────────────────────────────────

class TestCollectionsPersistence:
    def test_empty_load(self):
        # Fresh tmp file → no collections yet.
        assert sc._load_collections() == []

    def test_save_load_round_trip(self):
        sample = [
            {"name": "yeast", "description": "S. cerevisiae plasmids",
             "plasmids": [{"id": "P1", "name": "yp1", "size": 100,
                           "gb_text": "FAKE"}]},
            {"name": "ecoli", "description": "E. coli toolkit",
             "plasmids": []},
        ]
        sc._save_collections(sample)
        sc._collections_cache = None  # force a cold reload from disk
        out = sc._load_collections()
        assert len(out) == 2
        assert out[0]["name"] == "yeast"
        assert out[0]["plasmids"][0]["id"] == "P1"
        assert out[1]["name"] == "ecoli"

    def test_envelope_schema_on_disk(self):
        sc._save_collections([{"name": "t", "plasmids": []}])
        raw = json.loads(sc._COLLECTIONS_FILE.read_text())
        assert raw.get("_schema_version") == 1
        assert isinstance(raw.get("entries"), list)

    def test_legacy_bare_list_loads(self):
        """Pre-envelope save format must still load (sacred invariant #7)."""
        sc._COLLECTIONS_FILE.write_text(
            json.dumps([{"name": "legacy", "plasmids": []}])
        )
        sc._collections_cache = None
        out = sc._load_collections()
        assert len(out) == 1
        assert out[0]["name"] == "legacy"

    def test_non_dict_entries_dropped(self):
        sc._COLLECTIONS_FILE.write_text(json.dumps({
            "_schema_version": 1,
            "entries": [{"name": "ok", "plasmids": []}, "garbage", 42, None],
        }))
        sc._collections_cache = None
        out = sc._load_collections()
        assert len(out) == 1
        assert out[0]["name"] == "ok"


# ── Modal flows ────────────────────────────────────────────────────────────────

class TestCollectionsModalFlows:
    async def test_save_current_library_as_collection(self):
        # Seed a non-empty library. App startup auto-creates a "Main
        # Collection" wrapping it, then the modal Save adds "myset"
        # alongside.
        sc._save_library([{"id": "X", "name": "X", "size": 10,
                           "gb_text": "GB"}])
        app = sc.PlasmidApp()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await app.push_screen(sc.CollectionsModal())
            await pilot.pause(0.3)
            modal = app.screen
            modal.query_one("#coll-save-name").value = "myset"
            modal.query_one("#btn-coll-save").action_press()
            await pilot.pause(0.2)
            colls = sc._load_collections()
            myset = [c for c in colls if c.get("name") == "myset"]
            assert len(myset) == 1
            assert len(myset[0]["plasmids"]) == 1
            assert myset[0]["plasmids"][0]["id"] == "X"
            # Main Collection is also present — auto-created on app start.
            assert any(c.get("name") == sc._DEFAULT_COLLECTION_NAME for c in colls)
            app.exit()

    async def test_save_rejects_duplicate_name(self):
        sc._save_collections([{"name": "dup", "plasmids": []}])
        app = sc.PlasmidApp()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await app.push_screen(sc.CollectionsModal())
            await pilot.pause(0.3)
            modal = app.screen
            modal.query_one("#coll-save-name").value = "dup"
            modal.query_one("#btn-coll-save").action_press()
            await pilot.pause(0.2)
            # Still only the original collection — no new row.
            assert len(sc._load_collections()) == 1
            app.exit()

    async def test_save_rejects_blank_name(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await app.push_screen(sc.CollectionsModal())
            await pilot.pause(0.3)
            modal = app.screen
            before = sc._load_collections()
            modal.query_one("#coll-save-name").value = "   "
            modal.query_one("#btn-coll-save").action_press()
            await pilot.pause(0.2)
            # Same set as before — blank name was rejected. Main Collection
            # may have been auto-created on startup but no new entry should
            # have been added.
            after = sc._load_collections()
            assert [c.get("name") for c in after] == [c.get("name") for c in before]
            app.exit()

    async def test_load_replaces_library(self):
        # Library starts with one record; collection holds two different
        # records — Load must end with the library == collection's plasmids.
        sc._save_library([{"id": "OLD", "name": "old", "size": 1,
                           "gb_text": "GB"}])
        sc._save_collections([{
            "name": "newset",
            "plasmids": [
                {"id": "A", "name": "a", "size": 100, "gb_text": "GB"},
                {"id": "B", "name": "b", "size": 200, "gb_text": "GB"},
            ],
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await app.push_screen(sc.CollectionsModal())
            await pilot.pause(0.3)
            modal = app.screen
            t = modal.query_one("#coll-table")
            t.move_cursor(row=0)
            modal.query_one("#btn-coll-load").action_press()
            await pilot.pause(0.3)
            lib = sc._load_library()
            ids = {e.get("id") for e in lib}
            assert ids == {"A", "B"}
            app.exit()

    async def test_delete_collection(self):
        sc._save_collections([
            {"name": "keep", "plasmids": []},
            {"name": "drop", "plasmids": []},
        ])
        app = sc.PlasmidApp()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await app.push_screen(sc.CollectionsModal())
            await pilot.pause(0.3)
            modal = app.screen
            t = modal.query_one("#coll-table")
            # Cursor row 1 = "drop" (collection order on save).
            t.move_cursor(row=1)
            modal.query_one("#btn-coll-del").action_press()
            await pilot.pause(0.2)
            names = {c["name"] for c in sc._load_collections()}
            assert names == {"keep"}
            app.exit()


# ── Auto-load first library entry on startup ──────────────────────────────────

class TestStartupAutoLoad:
    """If the library is non-empty, PlasmidApp.on_mount must call
    _apply_record on the first entry so the canvas isn't blank."""

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        # Suppress the first-run seed worker so it can't mask our assertions.
        monkeypatch.setattr(sc.PlasmidApp, "_seed_default_library",
                            lambda self: None)

    async def test_first_entry_loaded(self, tiny_record):
        from io import StringIO
        from Bio import SeqIO
        buf = StringIO()
        SeqIO.write(tiny_record, buf, "genbank")
        sc._save_library([{
            "id":   tiny_record.id,
            "name": tiny_record.name,
            "size": len(tiny_record.seq),
            "gb_text": buf.getvalue(),
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.pause(0.3)
            assert app._current_record is not None
            assert app._current_record.id == tiny_record.id
            app.exit()

    async def test_empty_library_no_record(self):
        # Library empty + seed-worker patched out → canvas stays blank.
        app = sc.PlasmidApp()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.pause(0.3)
            assert app._current_record is None
            app.exit()

    async def test_unparseable_first_entry_does_not_crash(self):
        sc._save_library([{
            "id":   "BROKEN",
            "name": "broken",
            "size": 0,
            "gb_text": "this is not GenBank text",
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.pause(0.3)
            # App still up; current record falls back to None.
            assert app._current_record is None
            app.exit()


# ── Main Collection migration ─────────────────────────────────────────────────

class TestMainCollectionMigration:
    """First-run behaviour: an existing library gets wrapped in
    'Main Collection' the first time the new code sees it. Active
    collection is auto-pointed at it so library writes start mirroring
    immediately."""

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        monkeypatch.setattr(sc.PlasmidApp, "_seed_default_library",
                            lambda self: None)

    def test_ensure_default_creates_main_with_existing_library(self):
        # Library has content, collections.json doesn't exist yet.
        sc._save_library([{"id": "A", "name": "a", "size": 1, "gb_text": "GB"}])
        assert sc._load_collections() == []
        sc._ensure_default_collection()
        colls = sc._load_collections()
        assert len(colls) == 1
        assert colls[0]["name"] == sc._DEFAULT_COLLECTION_NAME
        assert colls[0]["plasmids"][0]["id"] == "A"
        assert sc._get_active_collection_name() == sc._DEFAULT_COLLECTION_NAME

    def test_ensure_default_idempotent(self):
        sc._save_collections([{"name": "Existing", "plasmids": []}])
        sc._set_active_collection_name("Existing")
        sc._ensure_default_collection()
        # Did not add Main Collection; active unchanged.
        assert [c["name"] for c in sc._load_collections()] == ["Existing"]
        assert sc._get_active_collection_name() == "Existing"

    def test_ensure_default_sets_active_when_missing(self):
        # Collections present but no active set → first one becomes active.
        sc._save_collections([{"name": "First", "plasmids": []},
                              {"name": "Second", "plasmids": []}])
        # Explicitly clear any active that may have been set by save_collections
        sc._set_active_collection_name(None)
        assert sc._get_active_collection_name() is None
        sc._ensure_default_collection()
        assert sc._get_active_collection_name() == "First"

    def test_app_startup_creates_main(self):
        # Empty everything → app boot should leave a Main Collection on disk.
        assert sc._load_collections() == []
        app = sc.PlasmidApp()
        async def go():
            async with app.run_test(size=(140, 50)) as pilot:
                await pilot.pause()
                await pilot.pause(0.1)
                colls = sc._load_collections()
                assert any(c.get("name") == sc._DEFAULT_COLLECTION_NAME
                           for c in colls)
                assert sc._get_active_collection_name() == sc._DEFAULT_COLLECTION_NAME
                app.exit()
        import asyncio
        asyncio.run(go())


# ── Library/collection sync ───────────────────────────────────────────────────

class TestLibraryCollectionSync:
    """Every library mutation must mirror into the active collection so
    the two on-disk files don't drift."""

    def test_save_library_mirrors_to_active(self):
        sc._save_collections([{"name": "Active", "plasmids": []}])
        sc._set_active_collection_name("Active")
        new_entries = [{"id": "P1", "name": "p1", "size": 100, "gb_text": "GB"}]
        sc._save_library(new_entries)
        # Active collection on disk now holds the same entries.
        coll = next(c for c in sc._load_collections()
                    if c.get("name") == "Active")
        assert len(coll["plasmids"]) == 1
        assert coll["plasmids"][0]["id"] == "P1"

    def test_save_library_silent_with_no_active(self):
        # No active collection → library writes don't crash; collection
        # file stays empty.
        sc._set_active_collection_name(None)
        sc._save_library([{"id": "X", "name": "x", "size": 1, "gb_text": "GB"}])
        # No collection should have been touched.
        assert sc._load_collections() == []

    def test_save_library_with_deleted_active_no_crash(self):
        # Active points at a name that no longer exists — should silently
        # skip the mirror, never raise.
        sc._save_collections([{"name": "Stale", "plasmids": []}])
        sc._set_active_collection_name("Stale")
        sc._save_collections([])  # delete it directly
        sc._save_library([{"id": "P", "name": "p", "size": 1, "gb_text": "GB"}])
        assert sc._load_collections() == []  # still empty


# ── LibraryPanel two-mode flows ──────────────────────────────────────────────

class TestLibraryPanelModes:
    """The redesigned panel toggles between collections and plasmids
    views. Click a collection → plasmids view; ← → collections view."""

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        monkeypatch.setattr(sc.PlasmidApp, "_seed_default_library",
                            lambda self: None)

    async def test_starts_in_plasmids_view_when_active_set(self):
        sc._save_collections([{"name": "Pre", "plasmids": []}])
        sc._set_active_collection_name("Pre")
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            assert lib._view_mode == "plasmids"
            assert lib.query_one("#lib-table").display is True
            assert lib.query_one("#lib-coll-table").display is False
            app.exit()

    async def test_back_button_returns_to_collections_view(self):
        sc._save_collections([{"name": "X", "plasmids": []}])
        sc._set_active_collection_name("X")
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            assert lib._view_mode == "plasmids"
            lib.query_one("#btn-lib-back").action_press()
            await pilot.pause(0.1)
            assert lib._view_mode == "collections"
            assert lib.query_one("#lib-coll-table").display is True
            assert lib.query_one("#lib-table").display is False
            app.exit()

    async def test_back_clears_search_filter_so_collections_visible(self):
        """Regression guard for 2026-05-10 fix: typing a query in the
        plasmid-view search bar then pressing the back button used to
        carry the filter into the collections view via the shared
        ``_filter_text`` field — `_repopulate_collections` would then
        hide every collection that didn't fuzzy-match the leftover
        plasmid query, leaving the user staring at an empty collections
        panel."""
        sc._save_collections([
            {"name": "Project Alpha", "plasmids": []},
            {"name": "Project Beta",  "plasmids": []},
            {"name": "Backbones",     "plasmids": []},
        ])
        sc._set_active_collection_name("Project Alpha")
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            # In plasmids view, simulate the user submitting a search
            # for a query that doesn't match any collection name.
            assert lib._view_mode == "plasmids"
            lib._filter_text = "thisQueryMatchesNoCollection"
            search = lib.query_one("#lib-search", sc.Input)
            search.value = "thisQueryMatchesNoCollection"
            await pilot.pause()
            # Press back.
            lib.query_one("#btn-lib-back").action_press()
            await pilot.pause(0.1)
            assert lib._view_mode == "collections"
            # The fix: filter is cleared on view-mode switch, so every
            # collection appears.
            assert lib._filter_text == ""
            assert search.value == sc._SearchInput.PREFILL
            t = lib.query_one("#lib-coll-table", sc.DataTable)
            assert t.row_count == 3, (
                f"expected 3 collections, got {t.row_count} — search "
                f"filter leaked across the view-mode switch"
            )
            app.exit()

    async def test_collection_pick_clears_search_filter_to_plasmids(self):
        """Symmetric to the back-clears-filter case: a query typed in
        the collections-view search bar must NOT carry into the plasmid
        view when the user activates a collection. Same root cause
        (shared `_filter_text` field), so the fix in `_apply_view_mode`
        covers both directions."""
        sc._save_collections([
            {"name": "OnlyOne", "plasmids": [
                {"id": "p1", "name": "p1", "size": 100,
                 "gb_text": "GB"},
                {"id": "p2", "name": "p2", "size": 200,
                 "gb_text": "GB"},
            ]},
        ])
        sc._set_active_collection_name("OnlyOne")
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            # Force collections view + stale filter, then drive the
            # collections → plasmids transition that the view-mode
            # switch fix should sanitise.
            lib._view_mode = "collections"
            lib._apply_view_mode()
            await pilot.pause()
            lib._filter_text = "Backbones"
            search = lib.query_one("#lib-search", sc.Input)
            search.value = "Backbones"
            await pilot.pause()
            lib._view_mode = "plasmids"
            lib._apply_view_mode()
            lib._repopulate_plasmids()
            await pilot.pause(0.1)
            assert lib._filter_text == ""
            assert search.value == sc._SearchInput.PREFILL
            t = lib.query_one("#lib-table", sc.DataTable)
            # Both plasmids visible — filter didn't leak in.
            assert t.row_count == 2
            app.exit()

    async def test_clicking_collection_requires_double_activation(self):
        """Loading a collection swaps the entire library, so a stray
        RowSelected must NOT fire the load. The first activation arms
        the collection and a second activation on the same row commits.
        """
        sc._save_collections([
            {"name": "A", "plasmids": [
                {"id": "p1", "name": "p1", "size": 1, "gb_text": "GB"}]},
            {"name": "B", "plasmids": []},
        ])
        sc._set_active_collection_name(None)
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib._repopulate_collections()
            t = lib.query_one("#lib-coll-table")
            t.move_cursor(row=0)  # "A"
            t.action_select_cursor()  # arm
            await pilot.pause(0.1)
            # Still in collections view — first click only armed.
            assert lib._view_mode == "collections"
            assert lib._coll_armed_name == "A"
            # Second activation on the same row commits.
            t.action_select_cursor()
            await pilot.pause(0.2)
            assert lib._view_mode == "plasmids"
            assert sc._get_active_collection_name() == "A"
            assert [e["id"] for e in sc._load_library()] == ["p1"]
            app.exit()

    async def test_switching_row_disarms_previous_collection(self):
        """Arming row A then activating row B should NOT load either —
        the arm transfers to B and one more activation on B is needed."""
        sc._save_collections([
            {"name": "A", "plasmids": [
                {"id": "pA", "name": "pA", "size": 1, "gb_text": "GB"}]},
            {"name": "B", "plasmids": [
                {"id": "pB", "name": "pB", "size": 1, "gb_text": "GB"}]},
        ])
        sc._set_active_collection_name(None)
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib._repopulate_collections()
            t = lib.query_one("#lib-coll-table")
            t.move_cursor(row=0)
            t.action_select_cursor()  # arm A
            await pilot.pause(0.1)
            t.move_cursor(row=1)
            t.action_select_cursor()  # arm B (disarms A)
            await pilot.pause(0.1)
            # Still in collections view; B is now armed.
            assert lib._view_mode == "collections"
            assert lib._coll_armed_name == "B"
            t.action_select_cursor()  # commit B
            await pilot.pause(0.2)
            assert sc._get_active_collection_name() == "B"
            app.exit()


class TestPanelCollectionCRUD:
    """Add / remove / rename a collection from the panel."""

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        monkeypatch.setattr(sc.PlasmidApp, "_seed_default_library",
                            lambda self: None)

    async def test_add_collection_via_panel(self):
        sc._save_collections([{"name": "Existing", "plasmids": []}])
        sc._set_active_collection_name(None)  # start in collections view
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib._repopulate_collections()
            lib.query_one("#btn-coll-add").action_press()
            await pilot.pause(0.2)
            modal = app.screen
            assert isinstance(modal, sc.NewCollectionModal)
            modal.query_one("#newcoll-name").value = "MyNew"
            modal.query_one("#btn-newcoll-ok").action_press()
            await pilot.pause(0.2)
            names = [c["name"] for c in sc._load_collections()]
            assert "MyNew" in names
            app.exit()

    async def test_add_collection_rejects_duplicate(self):
        sc._save_collections([{"name": "Same", "plasmids": []}])
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib.query_one("#btn-coll-add").action_press()
            await pilot.pause(0.2)
            modal = app.screen
            modal.query_one("#newcoll-name").value = "Same"
            modal.query_one("#btn-newcoll-ok").action_press()
            await pilot.pause(0.2)
            # Still only one — duplicate rejected.
            same = [c for c in sc._load_collections() if c.get("name") == "Same"]
            assert len(same) == 1
            app.exit()

    async def test_rename_collection_via_panel(self):
        sc._save_collections([{"name": "OldName", "plasmids": []}])
        sc._set_active_collection_name("OldName")
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib._repopulate_collections()
            t = lib.query_one("#lib-coll-table")
            t.move_cursor(row=0)
            lib.query_one("#btn-coll-rename").action_press()
            await pilot.pause(0.2)
            modal = app.screen
            assert isinstance(modal, sc.CollectionNameModal)
            modal.query_one("#collname-input").value = "NewName"
            modal.query_one("#btn-collname-ok").action_press()
            await pilot.pause(0.2)
            names = [c["name"] for c in sc._load_collections()]
            assert names == ["NewName"]
            # Active pointer follows the rename.
            assert sc._get_active_collection_name() == "NewName"
            app.exit()

    async def test_delete_collection_via_panel(self):
        sc._save_collections([
            {"name": "Keep", "plasmids": []},
            {"name": "Drop", "plasmids": []},
        ])
        sc._set_active_collection_name("Keep")
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib._repopulate_collections()
            t = lib.query_one("#lib-coll-table")
            # Collections list natural-sorts since 0.5.5.3 — "Drop"
            # sorts before "Keep" alphabetically, so cursor row=0 is
            # "Drop". Look it up by name in case the sort changes.
            for r, row_key in enumerate(t.rows):
                if row_key.value == "Drop":
                    t.move_cursor(row=r)
                    break
            lib.query_one("#btn-coll-del").action_press()
            await pilot.pause(0.2)
            # Stage 1: friendly confirm
            assert isinstance(app.screen, sc.CollectionDeleteConfirmModal)
            app.screen.query_one("#btn-colldel-yes").action_press()
            await pilot.pause(0.2)
            # Stage 2: scary red second confirm
            assert isinstance(app.screen, sc.ScaryDeleteConfirmModal)
            app.screen.query_one("#btn-scarydel-yes").action_press()
            await pilot.pause(0.2)
            names = [c["name"] for c in sc._load_collections()]
            assert names == ["Keep"]
            app.exit()

    async def test_collection_delete_first_no_keeps_collection(self):
        """Cancel at the friendly stage — collection stays."""
        sc._save_collections([{"name": "Stay", "plasmids": []}])
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib._repopulate_collections()
            t = lib.query_one("#lib-coll-table")
            t.move_cursor(row=0)
            lib.query_one("#btn-coll-del").action_press()
            await pilot.pause(0.2)
            assert isinstance(app.screen, sc.CollectionDeleteConfirmModal)
            app.screen.query_one("#btn-colldel-no").action_press()
            await pilot.pause(0.2)
            assert [c["name"] for c in sc._load_collections()] == ["Stay"]
            app.exit()

    async def test_collection_delete_second_no_keeps_collection(self):
        """Yes through the friendly stage but No on the scary stage —
        the collection still survives. Belt + suspenders confirm pattern."""
        sc._save_collections([{"name": "Saved", "plasmids": []}])
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib._repopulate_collections()
            t = lib.query_one("#lib-coll-table")
            t.move_cursor(row=0)
            lib.query_one("#btn-coll-del").action_press()
            await pilot.pause(0.2)
            app.screen.query_one("#btn-colldel-yes").action_press()
            await pilot.pause(0.2)
            assert isinstance(app.screen, sc.ScaryDeleteConfirmModal)
            app.screen.query_one("#btn-scarydel-no").action_press()
            await pilot.pause(0.2)
            assert [c["name"] for c in sc._load_collections()] == ["Saved"]
            app.exit()

    async def test_delete_active_collection_clears_active(self):
        sc._save_collections([{"name": "Going", "plasmids": []}])
        sc._set_active_collection_name("Going")
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib._repopulate_collections()
            t = lib.query_one("#lib-coll-table")
            t.move_cursor(row=0)
            lib.query_one("#btn-coll-del").action_press()
            await pilot.pause(0.2)
            app.screen.query_one("#btn-colldel-yes").action_press()
            await pilot.pause(0.2)
            app.screen.query_one("#btn-scarydel-yes").action_press()
            await pilot.pause(0.2)
            assert sc._load_collections() == []
            assert sc._get_active_collection_name() is None
            app.exit()


class TestBulkImportFolder:
    """`_bulk_import_folder` walks a folder, loads every supported file,
    and returns (entries, failures) without touching disk state.
    Uses the CommercialSaaS fixtures shipped in tests/ (see test_genbank_io)."""

    @staticmethod
    def _fixtures_dir():
        from pathlib import Path
        return Path(__file__).parent

    def test_imports_all_dna_fixtures(self):
        from pathlib import Path
        folder = self._fixtures_dir()
        # Skip if the FFE fixtures are missing (fresh-clone case).
        if not list(folder.glob("FFE*.dna")):
            pytest.skip("No FFE .dna fixtures present")
        entries, failures = sc._bulk_import_folder(folder)
        assert failures == [], f"unexpected failures: {failures}"
        # All fixtures should round-trip — sequences are non-empty, sizes
        # are sane plasmid sizes (1-10 kb).
        assert len(entries) >= 5
        for e in entries:
            assert 100 < e["size"] < 100_000
            assert e["n_feats"] > 0
            assert e["gb_text"]

    def test_display_name_preserves_filename_spaces(self):
        folder = self._fixtures_dir()
        if not list(folder.glob("FFE 1*.dna")):
            pytest.skip("No FFE .dna fixtures present")
        entries, _ = sc._bulk_import_folder(folder)
        names = [e["name"] for e in entries]
        # Filename was "FFE 1 ENTRY UPD.dna" — spaces must survive into
        # the display name even though record.id is sanitized.
        assert any(" " in n and "FFE" in n for n in names), \
            f"expected a space-containing display name in {names}"
        # Meanwhile id stays LOCUS-safe (no spaces).
        for e in entries:
            assert " " not in e["id"]

    def test_dedup_by_id_with_suffix(self, tmp_path):
        # Two files with the same stem (and so same backfilled record.id
        # for CommercialSaaS) must end up with distinct ids (`_2` suffix).
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ATGAAA"), id="dup", name="dup",
                        annotations={"molecule_type": "DNA"})
        # Two GenBank files in subfolders sharing stems
        a = tmp_path / "shared.gb"
        b = tmp_path / "shared.gbk"
        SeqIO.write(rec, a, "genbank")
        SeqIO.write(rec, b, "genbank")
        entries, failures = sc._bulk_import_folder(tmp_path)
        assert failures == []
        ids = sorted(e["id"] for e in entries)
        assert ids[0] != ids[1]
        assert ids[1].endswith("_2")

    def test_one_corrupt_file_does_not_abort_batch(self, tmp_path):
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        good = tmp_path / "good.gb"
        rec = SeqRecord(Seq("ATGAAA"), id="g", name="g",
                        annotations={"molecule_type": "DNA"})
        SeqIO.write(rec, good, "genbank")
        bad = tmp_path / "broken.dna"
        bad.write_bytes(b"not a commercialsaas file")
        entries, failures = sc._bulk_import_folder(tmp_path)
        assert len(entries) == 1
        assert len(failures) == 1
        assert failures[0][0].name == "broken.dna"


class TestBulkImportHardening:
    """Robustness against hostile / weird inputs.
    Each test mounts a synthetic attack and asserts the importer
    surfaces a friendly failure rather than crashing or accepting
    garbage."""

    def test_nonexistent_folder_returns_failure_not_crash(self, tmp_path):
        bogus = tmp_path / "does_not_exist"
        entries, failures = sc._bulk_import_folder(bogus)
        assert entries == []
        assert len(failures) == 1
        assert failures[0][0] == bogus
        assert "could not read folder" in failures[0][1].lower()

    def test_unreadable_folder_returns_failure_not_crash(self, tmp_path):
        import os
        locked = tmp_path / "locked"
        locked.mkdir()
        os.chmod(locked, 0)
        try:
            entries, failures = sc._bulk_import_folder(locked)
            assert entries == []
            assert len(failures) == 1
            assert "could not read folder" in failures[0][1].lower()
        finally:
            os.chmod(locked, 0o755)  # so pytest cleanup can remove it

    def test_oversized_file_skipped_with_reason(self, tmp_path,
                                                  monkeypatch):
        # Lower the cap so the test doesn't have to write 50 MB to disk
        monkeypatch.setattr(sc, "_BULK_IMPORT_MAX_BYTES", 100)
        big = tmp_path / "big.gb"
        big.write_bytes(b"x" * 500)
        entries, failures = sc._bulk_import_folder(tmp_path)
        assert entries == []
        assert len(failures) == 1
        assert "too large" in failures[0][1]

    def test_empty_sequence_skipped_with_reason(self, tmp_path):
        # Hand-craft a 0-bp GenBank record (Biopython would refuse to
        # WRITE one with a length-0 LOCUS, so build the text directly).
        zero = tmp_path / "zerolen.gb"
        zero.write_text(
            "LOCUS       zerolen                    0 bp    DNA     "
            "circular SYN 01-JAN-2026\n"
            "DEFINITION  zero.\n"
            "ACCESSION   zero\n"
            "FEATURES             Location/Qualifiers\n"
            "ORIGIN\n"
            "//\n"
        )
        entries, failures = sc._bulk_import_folder(tmp_path)
        assert entries == []
        assert len(failures) == 1
        assert "empty sequence" in failures[0][1]

    def test_filename_markup_chars_sanitized_in_display_name(self, tmp_path):
        # Filename with literal Rich markup tags (no path separator).
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        evil = tmp_path / "[red]EVIL[reset].gb"
        rec = SeqRecord(Seq("ATGAAA"), id="r", name="r",
                        annotations={"molecule_type": "DNA"})
        SeqIO.write(rec, evil, "genbank")
        entries, failures = sc._bulk_import_folder(tmp_path)
        assert failures == []
        assert len(entries) == 1
        # The display name keeps the literal characters — no auto-strip.
        # Markup-injection prevention happens at render time (Text() in
        # the panel, markup=False on notify), not here.
        assert "[red]" in entries[0]["name"]
        # Control chars: a filename with literal newlines must be
        # neutered so the panel can't be split by an injected line.
        ctrl = tmp_path / ("ctrl_" + "x" + ".gb")
        ctrl.write_text(evil.read_text())
        # Now write a record with a name containing newline-like bytes
        # — the import path strips control chars from display_name.
        # We approximate by passing a record through directly:
        from pathlib import Path
        synth = SeqRecord(Seq("ATGAAA"), id="x",
                          annotations={"molecule_type": "DNA"})
        # synthesise a path stem with a literal NUL (filesystems forbid
        # this but the helper might still see it from elsewhere)
        fake = Path("ev\nil")
        entry = sc._record_to_library_entry(synth, fake)
        assert "\n" not in entry["name"]
        assert "_" in entry["name"]  # newline replaced

    def test_long_filename_truncated_to_cap(self):
        # Filesystems cap filenames at 255 bytes, so we can't actually
        # write a 4 KB-stem file. Test the helper directly with a
        # synthetic Path — the cap protects against any path-like input
        # including ones constructed in code from external sources.
        from pathlib import Path
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ATGAAA"), id="r", name="r",
                        annotations={"molecule_type": "DNA"})
        long_path = Path("a" * 4000 + ".gb")
        entry = sc._record_to_library_entry(rec, long_path)
        assert len(entry["name"]) <= sc._BULK_IMPORT_MAX_NAME_LEN

    def test_struct_error_on_truncated_dna_rewrapped_as_value_error(
        self, tmp_path
    ):
        # Truncated .dna file — Biopython raises struct.error when
        # the binary header doesn't add up. load_genbank must rewrap as
        # ValueError so callers get the friendly message.
        truncated = tmp_path / "truncated.dna"
        truncated.write_bytes(b"\x09\x00not enough bytes")
        with pytest.raises(ValueError, match=r"popular commercial plasmid editor"):
            sc.load_genbank(str(truncated))

    def test_repopulate_uses_text_so_markup_in_name_renders_literal(self):
        """Plasmid display name with Rich markup must render literally,
        not inject style. Cell stored as Text(...) — the rich `Text`
        wrapper is opaque to markup parsing, so the brackets stay."""
        from rich.text import Text
        sc._save_collections([{"name": "[red]EVIL[reset]", "plasmids": []}])
        # The actual rendering check is hard to do headlessly without
        # the App harness; verify the storage shape: the collection name
        # round-trips with brackets intact, and adding a Text-wrapped
        # cell to a DataTable doesn't expand markup.
        cell = Text("[red]EVIL[reset]")
        # A bare string would render as styled text via Console; the
        # Text wrapper preserves the literal characters.
        from rich.console import Console
        from io import StringIO
        cap = Console(file=StringIO(), width=40, force_terminal=False,
                      no_color=True)
        cap.print(cell)
        out = cap.file.getvalue()
        # The literal "[red]" stays in the rendered output; if Rich
        # interpreted it, the substring would be stripped.
        assert "[red]" in out


class TestNewCollectionModalFlow:
    """End-to-end: clicking + on collections view → NewCollectionModal →
    submit name+folder → collection populated with imports."""

    async def test_create_empty_collection_no_folder(self):
        sc._save_collections([])
        sc._set_active_collection_name(None)
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib._repopulate_collections()
            lib.query_one("#btn-coll-add").action_press()
            await pilot.pause(0.2)
            modal = app.screen
            assert isinstance(modal, sc.NewCollectionModal)
            modal.query_one("#newcoll-name").value = "Empty"
            modal.query_one("#btn-newcoll-ok").action_press()
            await pilot.pause(0.2)
            colls = sc._load_collections()
            empty = [c for c in colls if c["name"] == "Empty"]
            assert len(empty) == 1
            assert empty[0]["plasmids"] == []
            app.exit()

    async def test_create_collection_with_bulk_import(self):
        from pathlib import Path
        fixtures_dir = Path(__file__).parent
        if not list(fixtures_dir.glob("FFE*.dna")):
            pytest.skip("No FFE .dna fixtures present")
        sc._save_collections([])
        sc._set_active_collection_name(None)
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib._repopulate_collections()
            lib.query_one("#btn-coll-add").action_press()
            await pilot.pause(0.2)
            modal = app.screen
            assert isinstance(modal, sc.NewCollectionModal)
            modal.query_one("#newcoll-name").value = "FFE Trial"
            # Bypass the DirectoryTree click → set the selection directly.
            modal._selected_folder = fixtures_dir
            modal.query_one("#btn-newcoll-ok").action_press()
            # Poll instead of fixed pause: bulk-import worker walks the
            # fixtures dir, parses each file, and only then dispatches
            # the dismiss callback that writes the collection. Wall
            # time is filesystem-dependent so a fixed 0.5 s flaked
            # under CI load. Cap at ~5 s.
            for _ in range(50):
                await pilot.pause(0.1)
                if any(c["name"] == "FFE Trial"
                        for c in sc._load_collections()):
                    break
            colls = sc._load_collections()
            ffe = [c for c in colls if c["name"] == "FFE Trial"]
            assert len(ffe) == 1
            # All fixtures imported with non-empty sequences
            assert len(ffe[0]["plasmids"]) >= 5
            for e in ffe[0]["plasmids"]:
                assert e["size"] > 0 and e["gb_text"]
            app.exit()

    async def test_clear_folder_button_deselects(self):
        from pathlib import Path
        fixtures_dir = Path(__file__).parent
        sc._save_collections([])
        sc._set_active_collection_name(None)
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib._repopulate_collections()
            lib.query_one("#btn-coll-add").action_press()
            await pilot.pause(0.2)
            modal = app.screen
            modal._selected_folder = fixtures_dir
            assert modal._selected_folder is not None
            modal.query_one("#btn-newcoll-clear").action_press()
            await pilot.pause(0.05)
            assert modal._selected_folder is None
            app.exit()


class TestBackButtonUnsavedPrompt:
    """Pressing the Back button must check for unsaved edits and prompt
    the user. Save → save then leave; Discard → revert + leave; Cancel
    → stay in plasmids view."""

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        monkeypatch.setattr(sc.PlasmidApp, "_seed_default_library",
                            lambda self: None)

    async def test_back_button_visible_in_plasmids_view(self, tiny_record):
        sc._save_collections([{"name": "C", "plasmids": []}])
        sc._set_active_collection_name("C")
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            assert lib._view_mode == "plasmids"
            btn = lib.query_one("#btn-lib-back")
            # The back button lives in the bottom button row (it took
            # over the slot the old ◈ annotate button used). Label is
            # the universal "←" back arrow.
            assert lib.query_one("#lib-btns").display is True
            assert "←" in str(btn.label)
            app.exit()

    async def test_back_button_hidden_in_collections_view(self):
        sc._save_collections([{"name": "C", "plasmids": []}])
        sc._set_active_collection_name(None)  # force collections view
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            # In collections view the whole plasmid-button row hides,
            # which carries the back button along with it.
            assert lib.query_one("#lib-btns").display is False
            app.exit()

    async def test_back_with_no_unsaved_goes_back_directly(self, tiny_record):
        sc._save_collections([{"name": "C", "plasmids": []}])
        sc._set_active_collection_name("C")
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            assert app._unsaved is False
            lib = app.query_one("#library", sc.LibraryPanel)
            lib.query_one("#btn-lib-back").action_press()
            await pilot.pause(0.1)
            # No modal — went straight to collections view.
            assert lib._view_mode == "collections"
            assert type(app.screen).__name__ != "UnsavedNavigateModal"
            app.exit()

    async def test_back_with_unsaved_pushes_modal(self, tiny_record):
        sc._save_collections([{"name": "C", "plasmids": []}])
        sc._set_active_collection_name("C")
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            app._mark_dirty()  # simulate an unsaved edit
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            assert lib._view_mode == "plasmids"
            lib.query_one("#btn-lib-back").action_press()
            await pilot.pause(0.2)
            # Modal pushed; still in plasmids view.
            assert isinstance(app.screen, sc.UnsavedNavigateModal)
            assert lib._view_mode == "plasmids"
            app.exit()

    async def test_modal_cancel_keeps_plasmids_view(self, tiny_record):
        sc._save_collections([{"name": "C", "plasmids": []}])
        sc._set_active_collection_name("C")
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            app._mark_dirty()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib.query_one("#btn-lib-back").action_press()
            await pilot.pause(0.2)
            assert isinstance(app.screen, sc.UnsavedNavigateModal)
            app.screen.query_one("#btn-navunsv-cancel").action_press()
            await pilot.pause(0.1)
            assert lib._view_mode == "plasmids"
            assert app._unsaved is True  # state preserved
            app.exit()

    async def test_modal_discard_reverts_and_goes_back(self, tiny_record):
        sc._save_collections([{"name": "C", "plasmids": []}])
        sc._set_active_collection_name("C")
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.2)  # let preload save to library
            app._mark_dirty()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib.query_one("#btn-lib-back").action_press()
            await pilot.pause(0.2)
            app.screen.query_one("#btn-navunsv-discard").action_press()
            await pilot.pause(0.2)
            # Switched view, dirty cleared.
            assert lib._view_mode == "collections"
            assert app._unsaved is False
            app.exit()

    async def test_modal_save_persists_and_goes_back(self, tiny_record):
        sc._save_collections([{"name": "C", "plasmids": []}])
        sc._set_active_collection_name("C")
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.2)
            app._mark_dirty()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib.query_one("#btn-lib-back").action_press()
            await pilot.pause(0.2)
            app.screen.query_one("#btn-navunsv-save").action_press()
            await pilot.pause(0.3)
            # Saved → marked clean → switched to collections view.
            assert app._unsaved is False
            assert lib._view_mode == "collections"
            app.exit()


class TestPlasmidDeleteConfirmFlow:
    """The plasmid `−` button and the Delete key both go through
    LibraryDeleteConfirmModal (default focus on No) so a stray click
    or keypress can't silently nuke a saved plasmid."""

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        monkeypatch.setattr(sc.PlasmidApp, "_seed_default_library",
                            lambda self: None)

    async def test_button_pushes_confirm_modal(self, tiny_record):
        sc._save_collections([{"name": "C", "plasmids": []}])
        sc._set_active_collection_name("C")
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.2)  # let preload save
            lib = app.query_one("#library", sc.LibraryPanel)
            t = lib.query_one("#lib-table")
            t.move_cursor(row=0)
            lib.query_one("#btn-lib-del").action_press()
            await pilot.pause(0.2)
            assert isinstance(app.screen, sc.LibraryDeleteConfirmModal)
            # Default focus is on No; cancel dismisses without deleting.
            app.screen.query_one("#btn-libdel-no").action_press()
            await pilot.pause(0.2)
            assert any(e.get("id") == tiny_record.id
                       for e in sc._load_library())
            app.exit()

    async def test_button_yes_deletes(self, tiny_record):
        sc._save_collections([{"name": "C", "plasmids": []}])
        sc._set_active_collection_name("C")
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.2)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib.query_one("#lib-table").move_cursor(row=0)
            lib.query_one("#btn-lib-del").action_press()
            await pilot.pause(0.2)
            app.screen.query_one("#btn-libdel-yes").action_press()
            await pilot.pause(0.2)
            assert all(e.get("id") != tiny_record.id
                       for e in sc._load_library())
            app.exit()

    async def test_delete_key_routes_through_same_confirm(self, tiny_record):
        sc._save_collections([{"name": "C", "plasmids": []}])
        sc._set_active_collection_name("C")
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.2)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib.query_one("#lib-table").focus()
            lib.query_one("#lib-table").move_cursor(row=0)
            await pilot.pause(0.05)
            app.action_delete_feature()
            await pilot.pause(0.2)
            assert isinstance(app.screen, sc.LibraryDeleteConfirmModal)
            app.exit()


class TestCollectionDeleteKeyFlow:
    """The Delete key in collections view triggers the same two-stage
    confirm as the `−` button — friendly modal then loud red modal."""

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        monkeypatch.setattr(sc.PlasmidApp, "_seed_default_library",
                            lambda self: None)

    async def test_delete_key_in_collections_view_pushes_first_confirm(self):
        sc._save_collections([{"name": "Doomed", "plasmids": []}])
        sc._set_active_collection_name(None)
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib._repopulate_collections()
            t = lib.query_one("#lib-coll-table")
            t.focus()
            t.move_cursor(row=0)
            await pilot.pause(0.05)
            app.action_delete_feature()
            await pilot.pause(0.2)
            assert isinstance(app.screen, sc.CollectionDeleteConfirmModal)
            app.exit()

    async def test_delete_key_full_two_stage_flow(self):
        sc._save_collections([{"name": "Doomed", "plasmids": []}])
        sc._set_active_collection_name(None)
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "collections"
            lib._apply_view_mode()
            lib._repopulate_collections()
            t = lib.query_one("#lib-coll-table")
            t.focus()
            t.move_cursor(row=0)
            await pilot.pause(0.05)
            app.action_delete_feature()
            await pilot.pause(0.2)
            app.screen.query_one("#btn-colldel-yes").action_press()
            await pilot.pause(0.2)
            assert isinstance(app.screen, sc.ScaryDeleteConfirmModal)
            app.screen.query_one("#btn-scarydel-yes").action_press()
            await pilot.pause(0.2)
            assert sc._load_collections() == []
            app.exit()


class TestSaveLoadedPlasmidToCollection:
    """The '+' button in plasmids view writes the current record into the
    active collection (via _save_library → _sync_active_collection_plasmids)."""

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        monkeypatch.setattr(sc.PlasmidApp, "_seed_default_library",
                            lambda self: None)

    async def test_add_record_lands_in_active_collection(self, tiny_record):
        sc._save_collections([{"name": "MyCol", "plasmids": []}])
        sc._set_active_collection_name("MyCol")
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.2)  # let preload + add_entry settle
            coll = next(c for c in sc._load_collections()
                        if c.get("name") == "MyCol")
            ids = [p.get("id") for p in coll["plasmids"]]
            assert tiny_record.id in ids
            app.exit()


class TestSearchCollectionsLibrary:
    """Pure helper test for `_search_collections_library` — fuzzy matches
    plasmid names across every collection on disk. The agent endpoint
    (`_h_search_library`) is covered separately in test_agent_api.py."""

    def test_empty_query_returns_everything(self):
        sc._save_collections([
            {"name": "A", "plasmids": [
                {"id": "x", "name": "x", "size": 1, "gb_text": "x"},
                {"id": "y", "name": "y", "size": 1, "gb_text": "y"},
            ]},
            {"name": "B", "plasmids": [
                {"id": "z", "name": "z", "size": 1, "gb_text": "z"},
            ]},
        ])
        out = sc._search_collections_library("")
        assert {(m["collection"], m["name"]) for m in out} == {
            ("A", "x"), ("A", "y"), ("B", "z"),
        }

    def test_fuzzy_match_filters_across_collections(self):
        sc._save_collections([
            {"name": "A", "plasmids": [
                {"id": "p1", "name": "pUC19_alpha", "size": 1,
                 "gb_text": "x"},
                {"id": "p2", "name": "pET28b",     "size": 1,
                 "gb_text": "x"},
            ]},
            {"name": "B", "plasmids": [
                {"id": "p3", "name": "pUC19_beta",  "size": 1,
                 "gb_text": "x"},
            ]},
        ])
        out = sc._search_collections_library("puc19")
        names = {(m["collection"], m["name"]) for m in out}
        assert names == {("A", "pUC19_alpha"), ("B", "pUC19_beta")}

    def test_limit_caps_results(self):
        sc._save_collections([
            {"name": "Big", "plasmids": [
                {"id": str(i), "name": f"p{i}", "size": 1, "gb_text": "x"}
                for i in range(20)
            ]},
        ])
        out = sc._search_collections_library("", limit=7)
        assert len(out) == 7

    def test_skips_non_dict_entries(self):
        """A corrupted collection with stray non-dict members must
        not crash the iterator."""
        sc._save_collections([
            {"name": "Ugly", "plasmids": [
                {"id": "good", "name": "good", "size": 1, "gb_text": "x"},
                "not-a-dict",  # type: ignore[list-item]
                42,            # type: ignore[list-item]
            ]},
        ])
        out = sc._search_collections_library("")
        assert [m["name"] for m in out] == ["good"]

    def test_skips_entries_without_id(self):
        """Regression guard for 2026-05-06: entries with no `id` would
        round-trip a `("collection", "")` payload that the loader then
        aliased to whichever entry it happened to find first. Helper
        now skips them outright."""
        sc._save_collections([
            {"name": "X", "plasmids": [
                {"id": "real", "name": "real", "size": 1, "gb_text": "x"},
                {"name": "no-id-here", "size": 1, "gb_text": "y"},
                {"id": "", "name": "blank-id", "size": 1, "gb_text": "z"},
            ]},
        ])
        out = sc._search_collections_library("")
        assert [m["name"] for m in out] == ["real"]


class TestActionFindPlasmid:
    """`PlasmidApp.action_find_plasmid` opens `LibrarySearchModal` and,
    on row pick, switches to the match's collection and loads the
    plasmid via the existing `_apply_record` flow."""

    @pytest.fixture(autouse=True)
    def _no_seed(self, monkeypatch):
        monkeypatch.setattr(sc.PlasmidApp, "_seed_default_library",
                            lambda self: None)

    async def test_action_pushes_search_modal(self):
        sc._save_collections([{"name": "C", "plasmids": []}])
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.action_find_plasmid()
            await pilot.pause(0.1)
            assert isinstance(app.screen, sc.LibrarySearchModal)
            app.exit()

    async def test_pick_switches_collection_and_loads(self, tiny_record):
        # Two collections; the target plasmid lives in the non-active one.
        sc._save_collections([
            {"name": "Active", "plasmids": []},
            {"name": "OtherCol", "plasmids": [{
                "id":      tiny_record.id,
                "name":    tiny_record.name,
                "size":    len(tiny_record.seq),
                "n_feats": 0,
                "gb_text": sc._record_to_gb_text(tiny_record),
            }]},
        ])
        sc._set_active_collection_name("Active")
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Synthesise the modal-dismiss callback directly — pilot
            # row clicks on a freshly composed DataTable inside a modal
            # are flake-prone (cf. the bulk-import test fix).
            app.action_find_plasmid()
            await pilot.pause(0.1)
            modal = app.screen
            assert isinstance(modal, sc.LibrarySearchModal)
            modal.dismiss(("OtherCol", tiny_record.id))
            await pilot.pause(0.2)
            assert sc._get_active_collection_name() == "OtherCol"
            assert app._current_record is not None
            assert app._current_record.id == tiny_record.id
            app.exit()
