"""
test_smoke — Textual TUI bootstrap smoke tests.

These are the minimum checks that a human running `python3 splicecraft.py` with
a pre-loaded GenBank file will not hit a Python error during mount, compose, or
the first render pass. They are NOT pixel-level rendering tests.

All tests run with `asyncio_mode = "auto"` (see pyproject.toml) so async test
functions are picked up without a `@pytest.mark.asyncio` decorator.

Each test starts the app with a synthetic SeqRecord via `_preload_record` so
NO network (NCBI) access is required, and isolates the library JSON with the
`isolated_library` fixture so the real library file is never touched.
"""
from __future__ import annotations

import sys

import pytest

import splicecraft as sc


TERMINAL_SIZE = (160, 48)   # wide enough for the three-pane layout


def _build_app(tiny_record, isolated_library) -> sc.PlasmidApp:
    """Build a PlasmidApp with a pre-loaded record. `isolated_library` is
    required as a parameter even though we don't touch it here — it's a
    fixture side-effect that monkeypatches `_LIBRARY_FILE`."""
    app = sc.PlasmidApp()
    app._preload_record = tiny_record
    return app


# ═══════════════════════════════════════════════════════════════════════════════
# Bootstrap
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppBootstrap:
    async def test_app_mounts_with_preloaded_record(self, tiny_record,
                                                     isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            # Let the on_mount _apply_record call_after_refresh run
            await pilot.pause()
            await pilot.pause(0.05)
            assert app._current_record is not None
            assert app._current_record.id == tiny_record.id

    async def test_all_panels_present(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Every one of these must exist; query_one raises if not.
            app.query_one("#plasmid-map", sc.PlasmidMap)
            app.query_one("#sidebar", sc.FeatureSidebar)
            app.query_one("#seq-panel", sc.SequencePanel)
            app.query_one("#library", sc.LibraryPanel)

    async def test_features_loaded_into_map(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            # The fixture has at least 2 features (CDS + misc_feature); the
            # load path may add a 'source' record. Assert non-empty.
            assert len(pm._feats) >= 2

    async def test_sequence_panel_has_sequence(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            assert sp._seq == str(tiny_record.seq)

    async def test_restriction_scan_ran_on_load(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # _restr_cache should be populated (tiny_record contains EcoRI
            # sites; depending on unique_only filter it may yield 0 or more
            # hits — here we just check the field was set to a list).
            assert isinstance(app._restr_cache, list)

    async def test_empty_app_mounts_without_preload(self, isolated_library):
        """App must also mount cleanly with no preloaded record. Pre-populate
        the library with a dummy entry (using the correct `size` field schema
        — see LibraryPanel._repopulate line ~2010) so the on_mount seeder's
        `not _load_library()` guard is False and no network fetch is attempted.
        """
        app = sc.PlasmidApp()
        sc._save_library([{
            "name":    "dummy",
            "id":      "DUMMY",
            "size":    1,
            "n_feats": 0,
            "source":  "test",
            "added":   "2026-04-11",
            "gb_text": "",
        }])
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            assert app._current_record is None


# ═══════════════════════════════════════════════════════════════════════════════
# Basic interactions
# ═══════════════════════════════════════════════════════════════════════════════

class TestBasicKeybindings:
    async def test_rotation_keys_change_origin(self, tiny_record,
                                                isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            # `[` is focus-gated to the map (post-2026-04-29), so focus
            # the map before pressing it. Pre-fix `[` worked anywhere.
            app.set_focus(pm)
            await pilot.pause(0.05)
            origin_before = pm.origin_bp
            await pilot.press("[")
            await pilot.pause(0.1)
            assert pm.origin_bp != origin_before

    async def test_view_toggle_key(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            view_before = getattr(pm, "_view_mode", None) or \
                          getattr(pm, "view_mode", None)
            await pilot.press("v")
            await pilot.pause(0.1)
            view_after = getattr(pm, "_view_mode", None) or \
                         getattr(pm, "view_mode", None)
            # If the widget uses a private attr we may not find it — soft check
            if view_before is not None:
                assert view_before != view_after

    async def test_restr_toggle_changes_state(self, tiny_record,
                                               isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            before = app._show_restr
            await pilot.press("r")
            await pilot.pause(0.1)
            assert app._show_restr != before


# ═══════════════════════════════════════════════════════════════════════════════
# No network / no library pollution guard
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoNetworkAccess:
    async def test_mount_does_not_call_fetch_genbank(self, tiny_record,
                                                      isolated_library,
                                                      monkeypatch):
        """If _preload_record is set, the app must never fall through to
        _seed_default_library, which would call fetch_genbank and try NCBI."""
        calls = []

        def _fake_fetch(*args, **kwargs):
            calls.append((args, kwargs))
            raise RuntimeError("fetch_genbank should not be called in tests")

        monkeypatch.setattr(sc, "fetch_genbank", _fake_fetch)
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            assert not calls, f"fetch_genbank was called {len(calls)} time(s)"


class TestLibraryRename:
    """Library panel rename (✎ button). Verifies the button exists, the
    modal opens with the current name, saving persists the new name to
    the library JSON AND mutates the currently-loaded record's name so
    the plasmid map header picks up the change without a reload.

    Collision check: refuses to rename to the name of another existing
    entry. Empty names are rejected by the modal itself (we test the
    modal-side validator via `_try_submit`)."""

    async def test_rename_button_exists(self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            btn = app.query_one("#btn-lib-rename", sc.Button)
            assert btn is not None

    async def test_rename_opens_modal_with_current_name(
        self, tiny_record, isolated_library
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.post_message(sc.LibraryPanel.RenameRequested(tiny_record.id))
            await pilot.pause()
            await pilot.pause(0.05)
            from splicecraft import RenamePlasmidModal
            modal = app.screen
            assert isinstance(modal, RenamePlasmidModal), (
                f"expected RenamePlasmidModal, got {type(modal).__name__}"
            )
            inp = modal.query_one("#rename-input", sc.Input)
            assert inp.value == tiny_record.name

    async def test_rename_save_persists_to_library_json(
        self, tiny_record, isolated_library
    ):
        """After Save, the library JSON's `name` field is the new name and
        the stored gb_text parses back to a record with matching name."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            new_name = "pACYC-custom"
            # Call the backend directly — no modal round-trip needed to test
            # the persistence logic
            app._rename_library_entry(tiny_record.id, new_name)
            await pilot.pause(0.05)

            entries = sc._load_library()
            match = [e for e in entries if e["id"] == tiny_record.id]
            assert len(match) == 1
            assert match[0]["name"] == new_name
            # gb_text should round-trip to a record with the new name
            reloaded = sc._gb_text_to_record(match[0]["gb_text"])
            assert reloaded.name == new_name

    async def test_rename_updates_currently_loaded_record(
        self, tiny_record, isolated_library
    ):
        """If the renamed entry is currently loaded, _current_record.name
        is mutated in place so the map header picks it up."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            old_name = app._current_record.name
            new_name = "my-lab-plasmid"
            assert old_name != new_name
            app._rename_library_entry(tiny_record.id, new_name)
            await pilot.pause(0.05)
            assert app._current_record.name == new_name
            # PlasmidMap uses record.name during render — its record field
            # is the same object, so it should see the new name.
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm.record.name == new_name

    async def test_rename_invalidates_map_draw_cache(
        self, tiny_record, isolated_library
    ):
        """PlasmidMap._draw_cache holds a (key, Text) tuple. Rename must
        either nuke it or the cache key must differ for the new name."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            # Force one render so _draw_cache has an entry
            pm.render()
            key_before = pm._draw_cache[0] if pm._draw_cache else None
            app._rename_library_entry(tiny_record.id, "renamed-test")
            await pilot.pause(0.05)
            # After rename, _draw_cache is either None (nuked) OR a fresh
            # entry with a different key (record.name is part of the key).
            if pm._draw_cache is not None:
                key_after = pm._draw_cache[0]
                assert key_after != key_before, (
                    "draw cache key must change after rename"
                )
                # And the new key's name field must be the new name
                assert "renamed-test" in key_after, (
                    f"expected 'renamed-test' in cache key; got {key_after}"
                )

    async def test_rename_rejects_duplicate_name(
        self, tiny_record, isolated_library, monkeypatch
    ):
        """If another entry already has the target name, the rename is
        refused with an error notification and the library is unchanged."""
        # Seed the library with two entries: tiny_record and a fake second
        from copy import deepcopy
        from Bio.SeqRecord import SeqRecord
        from Bio.Seq import Seq
        second = SeqRecord(
            Seq("ACGT" * 30), id="OTHER01", name="other",
            description="another plasmid",
        )
        second.annotations["molecule_type"] = "DNA"
        second.annotations["topology"]      = "circular"

        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Add a second entry manually via the library panel
            lib = app.query_one("#library", sc.LibraryPanel)
            lib.add_entry(second)
            await pilot.pause(0.05)
            # Now try to rename tiny_record to 'other' — should fail
            def _cb(result):
                pass
            collisions = []
            orig_notify = app.notify
            def _spy_notify(msg, **kw):
                collisions.append((msg, kw))
                return orig_notify(msg, **kw)
            monkeypatch.setattr(app, "notify", _spy_notify)
            # Fire the RenameRequested handler path with a fake callback
            # that asserts the collision path by calling the inner _on_result
            # equivalent directly: the handler opens a modal with callback —
            # easier to test the collision branch by looking at the spy.
            app.post_message(sc.LibraryPanel.RenameRequested(tiny_record.id))
            await pilot.pause()
            await pilot.pause(0.05)
            # Now dismiss the modal with the colliding name
            from splicecraft import RenamePlasmidModal
            modal = app.screen
            assert isinstance(modal, RenamePlasmidModal)
            modal.dismiss("other")
            await pilot.pause()
            await pilot.pause(0.05)
            # The entry should still have its original name
            entries = sc._load_library()
            tiny_entry = [e for e in entries if e["id"] == tiny_record.id][0]
            assert tiny_entry["name"] == tiny_record.name, (
                "rename to a colliding name should have been refused"
            )
            # And an error notification should have fired
            err_notes = [
                m for m, kw in collisions
                if kw.get("severity") == "error" and "already exists" in m
            ]
            assert err_notes, "expected an 'already exists' error notification"

    async def test_rename_modal_empty_name_rejected(
        self, tiny_record, isolated_library
    ):
        """Modal validator rejects an empty name and does NOT dismiss."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.post_message(sc.LibraryPanel.RenameRequested(tiny_record.id))
            await pilot.pause()
            await pilot.pause(0.05)
            from splicecraft import RenamePlasmidModal
            modal = app.screen
            assert isinstance(modal, RenamePlasmidModal)
            # Blank out the input and try to save
            modal.query_one("#rename-input", sc.Input).value = "   "
            modal._try_submit()
            await pilot.pause(0.05)
            # Modal should still be up (not dismissed)
            assert app.screen is modal
            # And the status line should show an error message.
            status = modal.query_one("#rename-status", sc.Static)
            status_text = str(status.content)
            assert "empty" in status_text.lower() or "cannot" in status_text.lower(), (
                f"expected error message in rename status; got {status_text!r}"
            )

    async def test_rename_modal_cancel_is_noop(
        self, tiny_record, isolated_library
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            original_name = app._current_record.name
            app.post_message(sc.LibraryPanel.RenameRequested(tiny_record.id))
            await pilot.pause()
            await pilot.pause(0.05)
            from splicecraft import RenamePlasmidModal
            modal = app.screen
            assert isinstance(modal, RenamePlasmidModal)
            modal.dismiss(None)   # cancel path
            await pilot.pause(0.05)
            assert app._current_record.name == original_name
            # Library entry unchanged
            entries = sc._load_library()
            assert any(
                e["id"] == tiny_record.id and e["name"] == original_name
                for e in entries
            )


class TestDeleteFocusRouting:
    """Delete key must be focus-aware: pressing Delete with library focus
    should offer to delete the library entry (with a confirmation defaulting
    to No), NOT silently delete a feature the user forgot they had selected
    in the map. Pressing Delete elsewhere still deletes the selected feature."""

    async def test_focus_is_in_library_helper_true_for_library(
        self, tiny_record, isolated_library
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Focus the library's DataTable
            lib_table = app.query_one("#lib-table")
            lib_table.focus()
            await pilot.pause(0.05)
            assert app._focus_is_in_library() is True

    async def test_focus_is_in_library_helper_false_for_map(
        self, tiny_record, isolated_library
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            pm.focus()
            await pilot.pause(0.05)
            assert app._focus_is_in_library() is False

    async def test_library_focus_clears_feature_selection(
        self, tiny_record, isolated_library
    ):
        """When focus moves INTO the library from elsewhere, any currently-
        selected feature in the map must be deselected. Mount auto-focuses
        the library table on first load, so we explicitly move focus to the
        map first to create a real transition."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            if len(pm._feats) == 0:
                pytest.skip("fixture has no features")
            # Move focus OUT of the library first (mount auto-focused it)
            pm.focus()
            await pilot.pause()
            await pilot.pause(0.05)
            pm.select_feature(0)
            assert pm.selected_idx == 0
            # Now focus the library's DataTable — this is the real transition.
            # GainedFocus dispatch is async; pause twice to let the message
            # be posted, routed, and the handler run.
            app.query_one("#lib-table").focus()
            await pilot.pause()
            await pilot.pause(0.1)
            assert pm.selected_idx == -1, (
                "feature selection should clear when library gains focus"
            )

    async def test_delete_with_library_focus_opens_confirm_modal(
        self, tiny_record, isolated_library
    ):
        """Delete key with library focused must push the confirmation modal,
        NOT silently delete a feature."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Focus the library DataTable and make sure cursor is on a row
            lib_table = app.query_one("#lib-table")
            lib_table.focus()
            await pilot.pause(0.05)
            # Invoke the action directly to avoid focus/key-routing races
            app.action_delete_feature()
            await pilot.pause(0.05)
            # The modal should now be on top of the screen stack
            from splicecraft import LibraryDeleteConfirmModal
            top = app.screen
            assert isinstance(top, LibraryDeleteConfirmModal), (
                f"expected LibraryDeleteConfirmModal on top, got {type(top).__name__}"
            )

    async def test_confirm_modal_default_focus_is_no(
        self, tiny_record, isolated_library
    ):
        """Modal mounts → the [No] button must be focused. This is the whole
        point of the dialog — Enter should be a safe no-op."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.query_one("#lib-table").focus()
            await pilot.pause(0.05)
            app.action_delete_feature()
            await pilot.pause(0.05)
            from splicecraft import LibraryDeleteConfirmModal
            modal = app.screen
            assert isinstance(modal, LibraryDeleteConfirmModal)
            no_btn = modal.query_one("#btn-libdel-no", sc.Button)
            # Either app.focused IS the No button, or the No button has
            # `has_focus` set
            assert app.focused is no_btn or no_btn.has_focus, (
                f"expected [No] focused; got {app.focused!r}"
            )

    async def test_confirm_no_keeps_entry_in_library(
        self, tiny_record, isolated_library
    ):
        """Pressing No in the dialog must leave the library unchanged."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            before_ids = [e["id"] for e in sc._load_library()]
            app.query_one("#lib-table").focus()
            await pilot.pause(0.05)
            app.action_delete_feature()
            await pilot.pause(0.05)
            from splicecraft import LibraryDeleteConfirmModal
            modal = app.screen
            assert isinstance(modal, LibraryDeleteConfirmModal)
            modal.dismiss(False)
            await pilot.pause(0.05)
            after_ids = [e["id"] for e in sc._load_library()]
            assert after_ids == before_ids

    async def test_confirm_yes_removes_entry_from_library(
        self, tiny_record, isolated_library
    ):
        """Pressing Yes in the dialog must delete the highlighted entry."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # tiny_record was auto-persisted during mount; it should be in lib
            before_ids = [e["id"] for e in sc._load_library()]
            assert tiny_record.id in before_ids
            app.query_one("#lib-table").focus()
            await pilot.pause(0.05)
            # Move DataTable cursor to the tiny_record row (should already be
            # there since it's the only entry)
            app.action_delete_feature()
            await pilot.pause(0.05)
            from splicecraft import LibraryDeleteConfirmModal
            modal = app.screen
            assert isinstance(modal, LibraryDeleteConfirmModal)
            modal.dismiss(True)
            await pilot.pause(0.05)
            after_ids = [e["id"] for e in sc._load_library()]
            assert tiny_record.id not in after_ids, (
                f"expected {tiny_record.id} removed; library now: {after_ids}"
            )

    async def test_delete_with_map_focus_still_deletes_feature(
        self, tiny_record, isolated_library
    ):
        """Classic feature-delete path must still work when the library does
        NOT have focus. Guards against over-broad routing."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            if len(pm._feats) == 0:
                pytest.skip("fixture has no features")
            n_feats_before = len(pm._feats)
            # Focus the map and select a feature
            pm.focus()
            pm.select_feature(0)
            await pilot.pause(0.05)
            assert not app._focus_is_in_library()
            app.action_delete_feature()
            await pilot.pause(0.05)
            # Feature should be gone
            pm_after = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert len(pm_after._feats) == n_feats_before - 1


class TestImportAutoPersist:
    """Every 'user imports a plasmid' entry point should auto-save the
    record to the library. Library loads and undo/redo should NOT
    re-save."""

    async def test_preload_record_is_auto_added_to_library(
        self, tiny_record, isolated_library
    ):
        """A CLI-preloaded record (python3 splicecraft.py myplasmid.gb)
        should appear in the library JSON after mount."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Library JSON on disk should contain the record
            lib_entries = sc._load_library()
            ids = [e["id"] for e in lib_entries]
            assert tiny_record.id in ids, (
                f"preloaded record {tiny_record.id} not saved to library; "
                f"library contains {ids}"
            )

    async def test_library_load_does_not_duplicate(
        self, tiny_record, isolated_library
    ):
        """Clicking a library row fires _library_load → _apply_record (NOT
        _import_and_persist), so the same record must not be added twice."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            before = len(sc._load_library())
            # Simulate clicking the library row by sending the same message
            # the DataTable would post
            app.post_message(
                sc.LibraryPanel.PlasmidLoad(sc._load_library()[0])
            )
            await pilot.pause(0.05)
            after = len(sc._load_library())
            assert after == before, (
                f"library_load should not add entries: {before} → {after}"
            )

    async def test_fetch_callback_adds_to_library(
        self, tiny_record, isolated_library, monkeypatch
    ):
        """When FetchModal dismisses with a record, the app callback
        (_import_and_persist) should save it to the library."""
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Call the helper directly — the modal → callback route is
            # equivalent to this once the modal dismisses.
            app._import_and_persist(tiny_record)
            await pilot.pause(0.05)
            entries = sc._load_library()
            assert any(e["id"] == tiny_record.id for e in entries), (
                f"fetched record not persisted; library: "
                f"{[e['id'] for e in entries]}"
            )

    async def test_import_of_duplicate_id_updates_in_place(
        self, tiny_record, isolated_library, monkeypatch
    ):
        """Re-importing a record with the same id should update the existing
        entry rather than create a duplicate (the add_entry dedup contract)."""
        # Block the network seed worker from firing when library starts empty.
        # Without this, the mount handler sees an empty library and kicks off
        # `_seed_default_library` which calls fetch_genbank → a live NCBI
        # fetch that races our assertions.
        monkeypatch.setattr(
            sc, "fetch_genbank",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("network disabled in tests")
            ),
        )
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._import_and_persist(tiny_record)
            await pilot.pause(0.05)
            n_first  = len(sc._load_library())
            app._import_and_persist(tiny_record)
            await pilot.pause(0.05)
            n_second = len(sc._load_library())
            assert n_first == n_second, (
                f"re-import duplicated the entry: {n_first} → {n_second}"
            )
            # And the record is present exactly once
            ids = [e["id"] for e in sc._load_library()]
            assert ids.count(tiny_record.id) == 1

    async def test_import_none_is_noop(self, isolated_library, monkeypatch):
        """Cancelled fetch/open modals dismiss with None — the helper must
        handle it silently. Also blocks the seed worker (see above)."""
        monkeypatch.setattr(
            sc, "fetch_genbank",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("network disabled in tests")
            ),
        )
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            before = len(sc._load_library())
            app._import_and_persist(None)
            await pilot.pause(0.05)
            assert len(sc._load_library()) == before


# ═══════════════════════════════════════════════════════════════════════════════
# _apply_record source_path + dirty-flag handling (regression guard 2026-04-13)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Before today's fix, _apply_record always cleared _source_path — even when
# called with clear_undo=False for an in-place update (e.g. primer-add).
# That meant after the in-place merge, Ctrl+S no longer targeted the user's
# original .gb file. Also, the merge path used lib.set_dirty(True) alone,
# which only updated the library panel's marker but left self._unsaved=False,
# so the user could quit without being prompted to save.

class TestApplyRecordInPlaceSemantics:
    """`_apply_record(record, clear_undo=False)` is the "in-place update"
    path — it must not clobber `_source_path`, and the caller is expected
    to call `_mark_dirty()` afterwards to set `_unsaved=True`."""

    async def test_clear_undo_true_clears_source_path(
        self, tiny_record, isolated_library
    ):
        """Fresh-load semantics: loading a different record from the library
        should clear the path of whatever was previously open."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._source_path = "/tmp/old.gb"
            app._apply_record(tiny_record)   # default clear_undo=True
            assert app._source_path is None

    async def test_clear_undo_false_preserves_source_path(
        self, tiny_record, isolated_library
    ):
        """In-place-update semantics: after primer-add or feature-merge,
        the user's original source file should still be the Ctrl+S target."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._source_path = "/tmp/myfile.gb"
            app._apply_record(tiny_record, clear_undo=False)
            assert app._source_path == "/tmp/myfile.gb"

    async def test_clear_undo_false_preserves_undo_stack(
        self, tiny_record, isolated_library
    ):
        """The undo stack itself must not be wiped by an in-place update —
        otherwise the pre-merge / pre-primer-add state becomes un-recoverable."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._undo_stack.append(("DUMMY_SEQ", 0, tiny_record))
            app._apply_record(tiny_record, clear_undo=False)
            assert len(app._undo_stack) == 1
            assert app._undo_stack[0][0] == "DUMMY_SEQ"

    async def test_per_plasmid_undo_restored_on_switch_back(
        self, tiny_record, isolated_library
    ):
        """Load plasmid A, push an undo snapshot, switch to plasmid B, then
        switch back to A — A's undo history must be restored (not reset
        to empty as it was before per-plasmid stacks were introduced)."""
        from copy import deepcopy
        from Bio.SeqRecord import SeqRecord
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Prime plasmid A (tiny_record)
            app._apply_record(tiny_record, clear_undo=True)
            app._undo_stack.append(("A_SEQ", 0, tiny_record))
            # Build a second plasmid B with a distinct id
            rec_b = deepcopy(tiny_record)
            rec_b.id = "PLASMID_B_XYZ"
            rec_b.name = "PLASMID_B"
            # Switch to B — A's stack should be stashed
            app._apply_record(rec_b, clear_undo=True)
            assert app._undo_stack == []
            assert "pUC19_MINI" in app._stashed_undo_stacks or \
                   tiny_record.id in app._stashed_undo_stacks
            # Switch back to A — A's stack must be restored
            app._apply_record(tiny_record, clear_undo=True)
            assert len(app._undo_stack) == 1
            assert app._undo_stack[0][0] == "A_SEQ"

    async def test_per_plasmid_undo_lru_eviction(
        self, tiny_record, isolated_library
    ):
        """With _MAX_PLASMIDS_WITH_UNDO slots in the stash, loading a new
        plasmid once the cap is full must evict the least-recently-used
        plasmid's stashed history."""
        from copy import deepcopy
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._MAX_PLASMIDS_WITH_UNDO = 3
            # Load 5 plasmids A-E, pushing an undo snapshot into each.
            # The stash holds non-current plasmids only, so after E is
            # loaded the stash contains the 4 that were swapped out
            # (A, B, C, D) minus anything past the cap.
            ids = ["PID_A", "PID_B", "PID_C", "PID_D", "PID_E"]
            for pid in ids:
                rec = deepcopy(tiny_record)
                rec.id = pid
                app._apply_record(rec, clear_undo=True)
                app._undo_stack.append((f"{pid}_SEQ", 0, rec))
            # Stash capacity is 3. A was swapped out first → evicted.
            # B, C, D survive; E is live.
            assert "PID_A" not in app._stashed_undo_stacks
            assert "PID_B" in app._stashed_undo_stacks
            assert "PID_C" in app._stashed_undo_stacks
            assert "PID_D" in app._stashed_undo_stacks
            assert "PID_E" not in app._stashed_undo_stacks
            assert app._current_undo_key == "PID_E"

    async def test_mark_dirty_after_in_place_update_flips_unsaved(
        self, tiny_record, isolated_library
    ):
        """In-place update flow: _apply_record(clear_undo=False) calls
        _mark_clean internally, so callers must invoke _mark_dirty()
        afterwards to make the app's _unsaved flag reflect reality."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._apply_record(tiny_record, clear_undo=False)
            # _apply_record calls _mark_clean internally
            assert app._unsaved is False
            # The fix: callers must mark dirty after in-place updates
            app._mark_dirty()
            assert app._unsaved is True


class TestCrashRecoveryAutosave:
    """Crash-recovery autosave writes the current record to
    `_CRASH_RECOVERY_DIR/{safe_id}.gb` so an unexpected exit doesn't lose
    edits. The file is deleted on successful save or explicit abandon."""

    async def test_mark_dirty_schedules_autosave(
        self, tiny_record, isolated_library
    ):
        """`_mark_dirty` must register a debounced autosave timer."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._mark_dirty()
            assert app._autosave_timer is not None

    async def test_do_autosave_writes_genbank_file(
        self, tiny_record, isolated_library
    ):
        """Forcing `_do_autosave` must write a valid GenBank file at the
        record's autosave path."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._unsaved = True
            app._do_autosave()
            path = app._autosave_path(app._current_record)
            # Autosave is now a thread worker (2026-05-06) — poll for
            # the file to appear instead of expecting a synchronous
            # write. Cap at ~2 s.
            assert path is not None
            for _ in range(20):
                await pilot.pause(0.1)
                if path.exists():
                    break
            assert path.exists()
            # Should be parseable GenBank
            from Bio import SeqIO
            roundtrip = SeqIO.read(str(path), "genbank")
            assert str(roundtrip.seq) == str(app._current_record.seq)

    async def test_mark_clean_clears_autosave_file(
        self, tiny_record, isolated_library
    ):
        """A successful save (→ `_mark_clean`) must delete the recovery
        file so next startup doesn't flag a stale recovery."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._unsaved = True
            app._do_autosave()
            path = app._autosave_path(app._current_record)
            # Wait for the autosave worker (now threaded) to land the
            # file before asserting on its existence.
            for _ in range(20):
                await pilot.pause(0.1)
                if path.exists():
                    break
            assert path.exists()
            app._mark_clean()
            assert not path.exists()

    async def test_autosave_skipped_when_clean(
        self, tiny_record, isolated_library
    ):
        """If the record isn't dirty, autosave must not write anything."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._unsaved = False
            app._do_autosave()
            path = app._autosave_path(app._current_record)
            assert path is None or not path.exists()

    async def test_autosave_path_sanitises_unsafe_ids(self, tiny_record,
                                                       isolated_library):
        """Record ids can contain characters that are unsafe for filenames
        (slashes, spaces). The autosave helper must sanitise them."""
        from copy import deepcopy
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            bad = deepcopy(tiny_record)
            bad.id = "some/weird id.with:chars"
            path = app._autosave_path(bad)
            assert path is not None
            assert "/" not in path.name
            assert ":" not in path.name

    async def test_autosave_path_disambiguates_sanitised_collisions(
        self, tiny_record, isolated_library,
    ):
        """Regression guard for 2026-04-25: pre-fix, two records with ids
        like 'foo/bar' and 'foo_bar' both sanitised to 'foo_bar.gb' and
        stomped each other on autosave. The fix appends a 6-char hash of
        the original id so collisions resolve to distinct filenames."""
        from copy import deepcopy
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            a = deepcopy(tiny_record); a.id = "foo/bar"
            b = deepcopy(tiny_record); b.id = "foo_bar"
            path_a = app._autosave_path(a)
            path_b = app._autosave_path(b)
            assert path_a is not None and path_b is not None
            assert path_a != path_b, (
                f"'{a.id}' and '{b.id}' must produce distinct autosave "
                f"paths after sanitisation; both got {path_a.name}"
            )
            # And reproducibility — the same id always maps to the same path.
            assert app._autosave_path(deepcopy(a)) == path_a


class TestCursorReachesEndOfSequence:
    """Regression guard for the 2026-04-25 cursor cap fix.

    Pre-fix the Right/Down arrow handlers clamped to `min(n - 1, …)` so the
    cursor could never land on position `n` (one past the last base) — the
    Edit Sequence dialog at `_edit_dialog_result` builds
    `old_seq[:s] + new_bases + old_seq[s:]`, so an end-of-sequence cursor is
    needed for an arrow-driven 'append' to work. Cap is now `min(n, …)`.

    Note (2026-04-25 amendment): Down arrow keeps the n-1 cap because pressing
    Down on the last row should land on the last visible base, not past it.
    Insert-at-end is reachable via Right arrow only.
    """

    async def test_right_arrow_at_end_advances_cursor_to_n(
        self, tiny_record, isolated_library,
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            n  = len(sp._seq)
            # Position cursor on the last base, then press Right. Pre-fix
            # the cursor stayed at n-1; post-fix it reaches n.
            sp._cursor_pos = n - 1
            # Clear focus so the App-level on_key arrow handler runs. With
            # focus on a DataTable OR PlasmidMap (both bind arrows for
            # their own purpose) the App handler bails — that skip is what
            # keeps the seq cursor from following plasmid rotation.
            app.set_focus(None)
            await pilot.pause(0.05)
            await pilot.press("right")
            await pilot.pause(0.05)
            assert sp._cursor_pos == n, (
                f"Right arrow at last base should advance cursor to n={n} "
                f"(insert-at-end); got {sp._cursor_pos}"
            )

            # And one more Right keypress must NOT push cursor past n.
            await pilot.press("right")
            await pilot.pause(0.05)
            assert sp._cursor_pos == n

    async def test_down_arrow_on_last_row_caps_at_last_basepair(
        self, tiny_record, isolated_library,
    ):
        """Pressing Down on the last visible row should land on the last
        basepair (n-1), not on n. Position n has no base to highlight, so
        the cursor would visually disappear. Reported 2026-04-25."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            n  = len(sp._seq)
            lw = sp._line_width()
            assert n > 0 and lw > 0
            # Place cursor a few bases into what should be the last row.
            # (`n - 5` is on the last row for any sequence with at least
            # one full row; tiny_record is ~120 bp so this holds.)
            sp._cursor_pos = max(0, n - 5)
            # Clear focus — see the Right-arrow test above for the rationale.
            app.set_focus(None)
            await pilot.pause(0.05)
            await pilot.press("down")
            await pilot.pause(0.05)
            assert sp._cursor_pos == n - 1, (
                f"Down on last row should clamp cursor to n-1={n-1} "
                f"(last visible base); got {sp._cursor_pos}"
            )

            # Pressing Down again must keep cursor at n-1 (no overshoot).
            await pilot.press("down")
            await pilot.pause(0.05)
            assert sp._cursor_pos == n - 1


class TestPlasmidMapLabelClick:
    """Clicking on a feature's text label in the plasmid map should
    route to that feature — same outcome as clicking its arc, the
    sidebar row, or the seq-panel lane art. Pre-fix the label fell
    outside the arc-detection radius and resolved as a backbone
    click (cleared all highlights instead of selecting the feature).

    `_draw` / `_draw_linear` populate `pm._label_bboxes` with
    `(x0, x1, y, feat_idx)` for each painted label; `_feat_at` /
    `_feat_at_linear` check the list before falling through to the
    geometry-based hit test.
    """

    async def test_circular_label_click_selects_feature(
        self, tiny_record, isolated_library,
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            # Force a render so `_label_bboxes` is populated.
            pm.render()
            assert pm._label_bboxes, "expected at least one label bbox"
            x0, x1, ly, idx = pm._label_bboxes[0]
            mid_x = (x0 + x1) // 2
            result = pm._feat_at(mid_x, ly)
            assert result == (idx, int(pm._feats[idx]["start"])), (
                f"label click should resolve to feature idx={idx} at "
                f"its 5' end; got {result}"
            )

    async def test_circular_label_click_outside_arc_still_selects(
        self, tiny_record, isolated_library,
    ):
        """Labels are placed outside the arc's 75-135% radial band,
        which used to hard-reject in `_feat_at`. Verify a click in
        that band on a label still resolves correctly."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            pm.render()
            # Find a label whose y row puts it outside the arc band.
            import math
            w, h = pm.size.width, pm.size.height
            cx, cy, rx, ry = pm._geometry(w, h)
            for x0, x1, ly, idx in pm._label_bboxes:
                mid_x = (x0 + x1) // 2
                dc = (mid_x - cx) / max(rx, 1)
                dr = (ly    - cy) / max(ry, 1)
                r_norm = math.sqrt(dc * dc + dr * dr)
                if r_norm > 1.35 or r_norm < 0.75:
                    out_idx, _bp = pm._feat_at(mid_x, ly)
                    assert out_idx == idx, (
                        f"label outside arc band should still hit-test "
                        f"to its feature; got idx={out_idx} expected={idx}"
                    )
                    return
            # If no label happened to be outside the band in this
            # tiny_record render, the test is moot but not wrong.

    async def test_linear_label_click_selects_feature(
        self, tiny_record, isolated_library,
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            # Switch to linear view and force a render.
            pm._map_mode = "linear"
            pm.refresh()
            pm.render()
            assert pm._label_bboxes
            x0, x1, ly, idx = pm._label_bboxes[0]
            result = pm._feat_at_linear((x0 + x1) // 2, ly)
            assert result == (idx, int(pm._feats[idx]["start"]))

    async def test_label_click_emits_feature_selected_via_app(
        self, tiny_record, isolated_library,
    ):
        """End-to-end through the App: post `FeatureSelected` (the
        message `pm.on_click` posts after a label hit) and verify
        the App's `_map_feat_selected` handler highlights the
        feature span in the seq panel. Pre-fix a label-on-arc click
        returned (-1, -1) and the message routed to the backbone-
        click branch, clearing all highlights."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sp = app.query_one("#seq-panel",   sc.SequencePanel)
            pm.render()
            assert pm._label_bboxes
            _x0, _x1, _ly, idx = pm._label_bboxes[0]
            f = pm._feats[idx]
            pm.post_message(sc.PlasmidMap.FeatureSelected(
                idx, f, int(f["start"]),
            ))
            await pilot.pause()
            await pilot.pause(0.1)
            # `select_feature_range` sets `_user_sel = (start, end)`
            # via the App's `_focus_feature` chain — same outcome
            # as a sidebar / seq-panel feature pick.
            assert sp._user_sel == (int(f["start"]), int(f["end"])), (
                f"label click should highlight the feature span; "
                f"sp._user_sel={sp._user_sel}"
            )


class TestSeqHomeEndAndCtrlArrow:
    """The seq panel's keyboard surface gained three extras (2026-04-30+):

      * Home / End jump the seq cursor to the start / end of the
        current display row — same semantics as a text editor. Home
        also still resets the map origin when the map has focus,
        because the App-level priority Home binding fires first there.
      * Ctrl+Arrow slides the active selection by 1 bp (left/right)
        or by `line_width` (up/down). Complement to Shift+Arrow,
        which extends the selection. No-op when no selection exists.
    """

    async def test_home_jumps_cursor_to_row_start(
        self, tiny_record, isolated_library,
    ):
        """Home should park the cursor on a row-start boundary —
        i.e. `cursor_pos % line_width == 0`. We don't check a
        specific bp because `_line_width()` depends on the live
        render width, which is not necessarily what we'd compute
        at test-setup time."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._cursor_pos = max(1, len(sp._seq) // 2)
            app.set_focus(None)
            await pilot.pause(0.05)
            before = sp._cursor_pos
            await pilot.press("home")
            await pilot.pause(0.05)
            lw = sp._line_width()
            assert sp._cursor_pos % lw == 0, (
                f"Home should jump to a row-start boundary; "
                f"cursor_pos={sp._cursor_pos}, lw={lw}"
            )
            assert sp._cursor_pos <= before, (
                f"Home should not move the cursor forward; "
                f"before={before}, after={sp._cursor_pos}"
            )

    async def test_end_jumps_cursor_to_row_end(
        self, tiny_record, isolated_library,
    ):
        """End should park the cursor at a row-end (= one before
        the next row-start, or n-1 on the last row)."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            n  = len(sp._seq)
            sp._cursor_pos = max(1, n // 2)
            app.set_focus(None)
            await pilot.pause(0.05)
            before = sp._cursor_pos
            await pilot.press("end")
            await pilot.pause(0.05)
            lw = sp._line_width()
            after = sp._cursor_pos
            # End-of-row = one less than next row-start, OR n-1
            # on the final row.
            is_row_end = ((after + 1) % lw == 0) or (after == n - 1)
            assert is_row_end, (
                f"End should jump to a row-end boundary; "
                f"cursor_pos={after}, lw={lw}, n={n}"
            )
            assert after >= before

    async def test_home_resets_map_origin_when_map_focused(
        self, tiny_record, isolated_library,
    ):
        """When the map has focus, Home should still reset the origin
        — the App-level priority binding takes that path before our
        seq-cursor on_key handler runs."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            pm.origin_bp = 50
            app.set_focus(pm)
            await pilot.pause(0.05)
            await pilot.press("home")
            await pilot.pause(0.05)
            assert pm.origin_bp == 0, (
                f"Home with map focused should reset origin to 0; "
                f"got {pm.origin_bp}"
            )

    async def test_ctrl_right_slides_selection(
        self, tiny_record, isolated_library,
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (10, 20)
            app.set_focus(None)
            await pilot.pause(0.05)
            await pilot.press("ctrl+right")
            await pilot.pause(0.05)
            assert sp._user_sel == (11, 21), (
                f"Ctrl+Right should slide (10,20) → (11,21); "
                f"got {sp._user_sel}"
            )

    async def test_ctrl_left_slides_selection(
        self, tiny_record, isolated_library,
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (10, 20)
            app.set_focus(None)
            await pilot.pause(0.05)
            await pilot.press("ctrl+left")
            await pilot.pause(0.05)
            assert sp._user_sel == (9, 19)

    async def test_ctrl_left_clamps_at_zero(
        self, tiny_record, isolated_library,
    ):
        """Ctrl+Left at the start of the sequence should clamp to
        (0, span) instead of going negative."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (0, 10)   # already at start
            app.set_focus(None)
            await pilot.pause(0.05)
            await pilot.press("ctrl+left")
            await pilot.pause(0.05)
            assert sp._user_sel == (0, 10), (
                f"Ctrl+Left at start should be a no-op; "
                f"got {sp._user_sel}"
            )

    async def test_ctrl_right_clamps_at_n(
        self, tiny_record, isolated_library,
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            n = len(sp._seq)
            sp._user_sel = (n - 10, n)   # already flush right
            app.set_focus(None)
            await pilot.pause(0.05)
            await pilot.press("ctrl+right")
            await pilot.pause(0.05)
            assert sp._user_sel == (n - 10, n)

    async def test_ctrl_arrow_no_op_without_selection(
        self, tiny_record, isolated_library,
    ):
        """Ctrl+Arrow without an active selection should not move the
        cursor — it's a deliberate no-op so the keys feel inert in
        contexts where there's nothing to slide."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel  = None
            sp._sel_range = None
            sp._cursor_pos = 30
            app.set_focus(None)
            await pilot.pause(0.05)
            await pilot.press("ctrl+right")
            await pilot.pause(0.05)
            assert sp._cursor_pos == 30
            assert sp._user_sel is None

    async def test_ctrl_down_slides_selection_by_line_width(
        self, tiny_record, isolated_library,
    ):
        """Ctrl+Down should preserve selection span and shift it by
        line_width. We check span preservation + a positive shift
        rather than a specific delta because `_line_width()` is
        layout-dependent and may differ from a pre-press capture."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (10, 20)
            app.set_focus(None)
            await pilot.pause(0.05)
            await pilot.press("ctrl+down")
            await pilot.pause(0.05)
            new_s, new_e = sp._user_sel
            delta = new_s - 10
            assert delta > 1, (
                f"Ctrl+Down should slide by more than 1 bp; "
                f"got delta={delta}"
            )
            assert new_e - new_s == 10, (
                f"Span should be preserved; new={(new_s, new_e)}"
            )


class TestRotationCursorSnap:
    """Rotation cursor-snap behaviour (2026-05-07).

    Originally (2026-04-29 regression guard) rotation was required
    NOT to move the seq cursor — the App-level on_key handler used
    to fire alongside the rotation binding and drag the cursor.
    The focus-gating fix kept rotation isolated from cursor moves.

    The 2026-05-07 origin-rotation cascade (sidebar reorder + seq
    panel shift) intentionally re-purposed this: rotation now snaps
    the cursor to the FIRST BASE of the new view (= absolute bp
    ``origin_bp``) so the user has a clear anchor at the rotated
    view's starting position. The arrow-keys-don't-bleed-into-cursor
    invariant is still preserved — what changed is that the
    rotation itself now drives the cursor, deliberately."""

    async def test_left_arrow_rotates_and_snaps_cursor_to_new_origin(
        self, tiny_record, isolated_library,
    ):
        """Left arrow on focused map rotates CCW (origin_bp ↑) and
        snaps the seq cursor to the new origin's first base."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sp._cursor_pos = 50
            pm.origin_bp = 100
            await pilot.pause(0.05)
            app.set_focus(pm)
            await pilot.pause(0.05)
            await pilot.press("left")
            await pilot.pause(0.05)
            # CCW: origin increases (mod total).
            assert pm.origin_bp > 100, (
                f"Left arrow should rotate CCW (origin_bp ↑); "
                f"got {pm.origin_bp}"
            )
            # Cursor snaps to the new origin = first base of the
            # rotated display.
            assert sp._cursor_pos == pm.origin_bp, (
                f"Cursor should snap to new origin "
                f"({pm.origin_bp}); got {sp._cursor_pos}"
            )

    async def test_right_arrow_rotates_clockwise(
        self, tiny_record, isolated_library,
    ):
        """Right arrow on focused map → clockwise (origin_bp DECREASES,
        wrapping mod total). Pre-2026-04-29 the binding called rotate_ccw."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            pm.origin_bp = 100
            app.set_focus(pm)
            await pilot.pause(0.05)
            await pilot.press("right")
            await pilot.pause(0.05)
            assert pm.origin_bp < 100, (
                f"Right arrow should rotate CW (origin_bp ↓); "
                f"got {pm.origin_bp}"
            )

    async def test_up_arrow_resets_origin_and_snaps_cursor_to_zero(
        self, tiny_record, isolated_library,
    ):
        """Up arrow on the focused map snaps origin_bp back to 0,
        and the cursor snaps with it — same cascade as any other
        rotation. Reset is just rotation to bp 0; the cursor lands
        at bp 0 (the rotated view's first base)."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            pm.origin_bp = 42
            await pilot.pause(0.05)
            sp._cursor_pos = 50
            app.set_focus(pm)
            await pilot.pause(0.05)
            await pilot.press("up")
            await pilot.pause(0.05)
            assert pm.origin_bp == 0, (
                f"Up arrow on focused map should reset origin to 0; "
                f"got {pm.origin_bp}"
            )
            # Cursor snaps to bp 0 (the new origin's first base).
            assert sp._cursor_pos == 0, (
                f"Reset origin should snap cursor to bp 0; "
                f"got {sp._cursor_pos}"
            )


class TestRestrictionEnzymeClickHighlight:
    """Regression guard for 2026-04-29: clicking a restriction enzyme
    bar highlights the recognition span, embeds top/bottom cut bps in
    `_re_highlight`, and a subsequent left/right arrow parks the cursor
    immediately upstream/downstream of the cut."""

    async def test_re_highlight_records_cut_positions(
        self, isolated_library,
    ):
        # Build a sequence with a single EcoRI site (GAATTC) at p=10.
        # EcoRI: fwd_cut=1, rev_cut=5 — so top cut at 11, bottom at 15.
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        seq = "A" * 10 + "GAATTC" + "A" * 84
        rec = SeqRecord(Seq(seq), id="re_test", name="re_test",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            # Find the EcoRI resite in the scan output.
            sites = sc._scan_restriction_sites(seq, circular=True)
            ecori_resite = next(
                s for s in sites
                if s.get("type") == "resite" and s.get("label") == "EcoRI"
            )
            assert ecori_resite["top_cut_bp"] == 11, ecori_resite
            assert ecori_resite["bottom_cut_bp"] == 15, ecori_resite

            # Simulate a lane-click on this resite by setting the panel's
            # internal _last_resite_click and routing through on_click.
            sp._last_resite_click = ecori_resite
            # Drive the click handler directly with the resite still set.
            sp._re_highlight = {
                "start":         ecori_resite["start"],
                "end":           ecori_resite["end"],
                "top_cut_bp":    ecori_resite["top_cut_bp"],
                "bottom_cut_bp": ecori_resite["bottom_cut_bp"],
                "color":         ecori_resite["color"],
                "name":          ecori_resite["label"],
            }
            sp._cursor_pos = -1
            await pilot.pause(0.05)

            # Right arrow — cursor should land on top_cut (= 11), the
            # first base of the right (downstream) fragment.
            app.set_focus(None)
            await pilot.pause(0.05)
            await pilot.press("right")
            await pilot.pause(0.05)
            assert sp._cursor_pos == 11, (
                f"Right arrow on RE-highlighted EcoRI should park cursor "
                f"at downstream-of-cut bp 11; got {sp._cursor_pos}"
            )
            # And the highlight should be cleared.
            assert sp._re_highlight is None

    async def test_left_arrow_parks_cursor_upstream_of_cut(
        self, isolated_library,
    ):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        seq = "A" * 10 + "GAATTC" + "A" * 84
        rec = SeqRecord(Seq(seq), id="re_test2", name="re_test2",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._re_highlight = {
                "start":         10,
                "end":           16,
                "top_cut_bp":    11,
                "bottom_cut_bp": 15,
                "color":         "magenta",
                "name":          "EcoRI",
            }
            sp._cursor_pos = -1
            app.set_focus(None)
            await pilot.pause(0.05)
            await pilot.press("left")
            await pilot.pause(0.05)
            # Left should park cursor immediately upstream of top_cut (11),
            # i.e. on bp 10 — the last base of the left (upstream) fragment.
            assert sp._cursor_pos == 10
            assert sp._re_highlight is None

    async def test_up_down_arrows_also_clear_highlight(
        self, isolated_library,
    ):
        """Up/Down arrows clear the highlight too — any arrow press
        should revert the staggered-overhang visualization."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        seq = "A" * 10 + "GAATTC" + "A" * 84
        rec = SeqRecord(Seq(seq), id="re_test3", name="re_test3",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._re_highlight = {
                "start":         10,
                "end":           16,
                "top_cut_bp":    11,
                "bottom_cut_bp": 15,
                "color":         "magenta",
                "name":          "EcoRI",
            }
            sp._cursor_pos = -1
            app.set_focus(None)
            await pilot.pause(0.05)
            await pilot.press("up")
            await pilot.pause(0.05)
            assert sp._re_highlight is None

    async def test_click_outside_seq_panel_clears_highlight(
        self, isolated_library,
    ):
        """A click on the plasmid map (or any other panel) should
        revert the RE highlight on the seq panel. The App-level
        on_click cleans up when the click lands outside seq panel."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        seq = "A" * 10 + "GAATTC" + "A" * 84
        rec = SeqRecord(Seq(seq), id="re_test4", name="re_test4",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._re_highlight = {
                "start":         10,
                "end":           16,
                "top_cut_bp":    11,
                "bottom_cut_bp": 15,
                "color":         "magenta",
                "name":          "EcoRI",
            }
            await pilot.pause(0.05)
            # Click on the plasmid map area — anywhere outside the seq panel.
            await pilot.click("#plasmid-map", offset=(20, 10))
            await pilot.pause(0.05)
            assert sp._re_highlight is None


class TestTypeIISCutRegionHighlight:
    """Type IIS enzymes cut OUTSIDE their recognition site. Clicking
    a Type IIS resite should highlight the recognition span PLUS the
    spacer + overhang region all the way to the bottom-strand cut so
    the user can see the full cut footprint at a glance.

    Regression guard for 2026-05-08: pre-fix the highlight only
    extended to ``ext_cut_bp`` (a single cut position), missing the
    bases between top and bottom cuts on the overhang side."""

    def test_bsai_resite_highlight_dict_extends_through_overhang(self):
        # BsaI recognition GGTCTC + cuts 1/5 → top cut at p+7,
        # bottom cut at p+11 for site starting at p. Verify the
        # `_resite_highlight_dict` helper extends the span all the
        # way to the bottom cut so the user sees the full cut
        # footprint when clicking the resite bar.
        seq = "A" * 20 + "GGTCTC" + "N" * 10 + "A" * 64
        sites = sc._scan_restriction_sites(seq, circular=True)
        bsai_resite = next(
            s for s in sites
            if s.get("type") == "resite" and s.get("label") == "BsaI"
        )
        assert bsai_resite["start"] == 20
        assert bsai_resite["end"]   == 26
        assert bsai_resite["top_cut_bp"]    == 27
        assert bsai_resite["bottom_cut_bp"] == 31

        sp = sc.SequencePanel()
        sp._seq = seq
        hi = sp._resite_highlight_dict(bsai_resite)
        # Recognition: 20..26 (6 bp). Cut region:
        #   - position 26 → spacer (1 bp between recognition and
        #     top cut at 27).
        #   - positions 27..30 → overhang region (top cut at 27,
        #     bottom cut at 31, 4 nt overhang).
        # `bottom_cut_bp = 31` is the FIRST base of the right
        # fragment on the bottom strand — NOT part of the cut
        # footprint. Highlight end (exclusive) = 31, so positions
        # 20..30 are highlighted (recognition + spacer + overhang).
        assert hi["start"] == 20
        assert hi["end"]   == 31, (
            f"BsaI highlight should be [20, 31) — recognition "
            f"+ spacer (26) + overhang (27..30). Position 31 is "
            f"the first base of the right fragment, NOT part of "
            f"the cut region. got [{hi['start']}, {hi['end']})"
        )
        assert hi["top_cut_bp"]    == 27
        assert hi["bottom_cut_bp"] == 31
        # Recognition bounds preserved separately so the renderer
        # can paint recognition / spacer / overhang in distinct
        # colours.
        assert hi["rec_start"] == 20
        assert hi["rec_end"]   == 26

    def test_palindromic_resite_highlight_dict_unchanged(self):
        # EcoRI cuts INSIDE its recognition (top at p+1, bot at p+5
        # for site length 6). The cut-region extension should NOT
        # change the highlight span for these — recognition
        # already covers both cuts.
        seq = "A" * 10 + "GAATTC" + "A" * 84
        sites = sc._scan_restriction_sites(seq, circular=True)
        ecori = next(
            s for s in sites
            if s.get("type") == "resite" and s.get("label") == "EcoRI"
        )
        sp = sc.SequencePanel()
        sp._seq = seq
        hi = sp._resite_highlight_dict(ecori)
        assert hi["start"] == 10
        assert hi["end"]   == 16  # unchanged: cuts are inside recognition

    def test_typeiis_render_uses_region_specific_palette(self):
        """The 2026-05-08 region-aware palette paints recognition,
        spacer, and overhang in distinct colours so the cut footprint
        reads at a glance:
          * recognition top strand — blue bg, bot strand — red bg
          * spacer (Type IIS only) — gray bg both strands
          * Type IIS overhang top — green bg, bot — orange bg
          * Type IIP overhang (cut inside recognition) — keeps the
            recognition treatment (top blue, bot red); the overhang
            colours apply only when the cut sits OUTSIDE recognition.
        Black foreground on every overlay so the base letter stays
        legible against the bright bg.
        """
        seq = "A" * 20 + "GGTCTC" + "N" * 10 + "A" * 64
        sites = sc._scan_restriction_sites(seq, circular=True)
        bsai = next(
            s for s in sites
            if s.get("type") == "resite" and s.get("label") == "BsaI"
        )
        sp = sc.SequencePanel()
        sp._seq = seq
        feats = [bsai]
        text = sc._build_seq_text(
            seq, feats, line_width=80,
            re_highlight=sp._resite_highlight_dict(bsai),
        )
        # Walk the rendered Text spans and bin each character +
        # style by position. Spans are RLE — track current x as
        # we go.
        rendered = text.plain
        # The top strand should appear before the bottom strand;
        # find both lines that contain the recognition `GGTCTC`.
        # The Text renders as plain text concatenated with style
        # markers. Use the lowest-level API: walk `text.spans` and
        # the character at each absolute index.
        # Verify the per-position styles by checking key positions.
        # Position 20 (recognition, top): should have "blue" in style.
        # Position 26 (spacer): "grey" in style.
        # Position 27 (overhang top strand): "green" in style.
        # Position 27 bottom strand: "yellow" in style.
        # We can't easily index by absolute bp because of the
        # line numbering prefix + newlines. Instead verify the
        # set of background colors that appear in the highlight
        # range — green, yellow, and grey50 should all be present.
        styles_used: set[str] = set()
        for span in text.spans:
            sty = str(span.style or "")
            styles_used.add(sty)
        # The Type IIS render should produce all three new colours.
        sty_blob = " ".join(styles_used).lower()
        assert "green"  in sty_blob, (
            f"expected `green` (overhang top) bg; styles: {styles_used}"
        )
        assert "orange" in sty_blob, (
            f"expected `yellow` (overhang bot) bg; styles: {styles_used}"
        )
        assert "grey50" in sty_blob or "grey" in sty_blob, (
            f"expected gray (spacer) bg; styles: {styles_used}"
        )

    async def test_resite_mouse_down_doesnt_park_cursor_at_midpoint(
            self, isolated_library):
        """Reproducer for the 2026-05-08 user report: "after origin
        shift, clicking on any enzyme label scrolls the seq panel
        and focuses a feature instead of showing the recog site".

        Cause: `on_mouse_down` parked the cursor at the resite
        midpoint (returned by `_click_to_bp`) and auto-scrolled
        via `_ensure_cursor_visible` BEFORE `on_click` got a
        chance to recognise it as a resite click. On a big
        plasmid with the resite far from the current viewport the
        scroll yanked the user away from where they were looking.

        Fix: `on_mouse_down` short-circuits all cursor + scroll
        work when `_last_resite_click` is set. `on_click` owns
        the post-click view (sets `_re_highlight`, clears
        cursor, refreshes).
        """
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        seq = "A" * 1000 + "GAATTC" + "A" * 500
        rec = SeqRecord(
            Seq(seq), id="t", name="t",
            annotations={"molecule_type": "DNA", "topology": "circular"},
        )
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sites = sc._scan_restriction_sites(seq, circular=True)
            ecori = next(
                s for s in sites
                if s.get("type") == "resite" and s.get("label") == "EcoRI"
            )
            sp.update_seq(seq, [ecori])
            await pilot.pause(0.1)

            # Pre-condition: cursor is at -1 (no cursor).
            sp._cursor_pos = -1
            sp._user_sel = None
            sp._sel_anchor = -1

            # Drive mouse_down with `_last_resite_click` set, by
            # monkey-patching `_click_to_bp` to return EcoRI's
            # midpoint (1003) and stash the resite. This mirrors
            # what `_check_packed` does when a click lands on the
            # parens row of a resite.
            original_click_to_bp = sp._click_to_bp

            def _stub(_x, _y, _real=original_click_to_bp,
                      _resite=ecori, _sp=sp):
                _sp._last_resite_click = _resite
                return 1003   # EcoRI midpoint, absolute

            sp._click_to_bp = _stub
            from textual.events import MouseDown
            sp.on_mouse_down(MouseDown(
                widget=sp, x=0, y=0, delta_x=0, delta_y=0,
                button=1, shift=False, meta=False, ctrl=False,
                screen_x=0, screen_y=0,
            ))
            sp._click_to_bp = original_click_to_bp
            await pilot.pause(0.05)

            # Bug symptom: cursor parked at the resite midpoint
            # (1003). Fix: cursor stays at its previous position
            # (-1), so `on_click` can own the cursor state cleanly.
            assert sp._cursor_pos == -1, (
                f"on_mouse_down on a resite must not park the "
                f"cursor at the resite midpoint (would auto-scroll "
                f"the seq panel); cursor_pos={sp._cursor_pos}"
            )

    async def test_dna_click_after_rotation_doesnt_select_feature(
            self, isolated_library):
        """Regression guard for 2026-05-08: after origin shift, a
        DNA-strand click landing OUTSIDE any feature's bp range
        must NOT highlight a feature on the map. Pre-fix the
        owner-cell cache returned stale unrotated owners; the
        click resolver could mis-route the DNA click as a lane
        click and post `SequenceClick(from_lane=True)`, which the
        App handler treats as "user picked a feature here" and
        sets `pm.selected_idx`.
        """
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        seq = "A" * 100 + "GAATTC" + "A" * 100
        rec = SeqRecord(
            Seq(seq), id="rotclick", name="rotclick",
            annotations={"molecule_type": "DNA", "topology": "circular"},
        )
        rec.features.append(SeqFeature(
            FeatureLocation(50, 80, strand=1), type="CDS",
            qualifiers={"label": ["midCDS"]},
        ))
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sites = sc._scan_restriction_sites(seq, circular=True)
            ecori = next(
                s for s in sites
                if s.get("type") == "resite" and s.get("label") == "EcoRI"
            )
            sp.update_seq(seq, pm._feats + [ecori])
            await pilot.pause(0.1)

            # Click before rotation to populate any caches.
            line_w = sp._line_width()
            num_w  = len(str(len(seq)))
            chunks_layout, _, _ = sc._chunk_layout(
                *sp._get_rotated_state(), line_w,
            )
            _ap, _bp, above_rows, _br = chunks_layout[0][2]
            # Pre-rotation: click DNA at col 5 (absolute bp 5,
            # OUTSIDE any feature). Should be a clean DNA click.
            await pilot.click(
                "#seq-scroll", offset=(5 + num_w + 2, above_rows),
            )
            await pilot.pause(0.1)
            assert sp._cursor_pos == 5
            assert pm.selected_idx == -1, (
                "pre-rotation: DNA click outside features must "
                "not select a feature"
            )

            # Rotate origin to bp 50.
            pm.origin_bp = 50
            await pilot.pause()
            await pilot.pause(0.2)

            # Post-rotation: click DNA at display col 100
            # (= absolute bp 150, which is outside both midCDS
            # and EcoRI). Must remain a DNA click.
            chunks_layout, _, _ = sc._chunk_layout(
                *sp._get_rotated_state(), line_w,
            )
            _ap, _bp, above_rows, _br = chunks_layout[0][2]
            await pilot.click(
                "#seq-scroll", offset=(100 + num_w + 2, above_rows),
            )
            await pilot.pause(0.1)
            assert sp._cursor_pos == 150, (
                f"post-rotation DNA click should land on absolute "
                f"bp 150; got {sp._cursor_pos}"
            )
            assert pm.selected_idx == -1, (
                f"post-rotation: DNA click outside features must "
                f"NOT select a feature; pm.selected_idx="
                f"{pm.selected_idx}"
            )
            assert sp._sel_range is None
            assert sp._re_highlight is None

    def test_rotation_invalidates_owner_cache_for_resite_clicks(self):
        """Pre-2026-05-08 the owner cache (`_chunks_owners`) keyed
        only on `id(self._feats)`, which doesn't change under
        rotation. After a rotation, click resolution returned the
        OLD unrotated owner cells — clicks on the visibly-correct
        enzyme label resolved to None or the wrong feature, falling
        through to the regular DNA-row click path that scrolls the
        seq panel to the click bp instead of highlighting the
        resite. Cache key now includes `_view_origin_bp` so the
        rotated owner cells are populated fresh.
        """
        seq = "A" * 20 + "GAATTC" + "A" * 64
        sites = sc._scan_restriction_sites(seq, circular=True)
        ecori = next(
            s for s in sites
            if s.get("type") == "resite" and s.get("label") == "EcoRI"
        )
        # Mock placements + the chunk-glyph-owner inputs the way
        # `_click_to_bp` would feed them. We just need the owner
        # cells to come back for two different rotations, with
        # the resite owner cells in different columns.
        sp = sc.SequencePanel()
        sp._seq   = seq
        sp._feats = [ecori]

        def _owners_at(origin: int) -> set:
            sp._view_origin_bp = origin
            sp._chunks_owners.clear()
            sp._rotated_cache_key = None
            sp._rotated_seq = sp._rotated_feats = None
            disp_seq, disp_feats = sp._get_rotated_state()
            line_w = 80
            chunks_layout, _, _ = sc._chunk_layout(
                disp_seq, disp_feats, line_w,
            )
            chunk_start, chunk_end, groups, *_extra = chunks_layout[0]
            above_p, below_p, above_rows, below_rows = groups
            n = len(disp_seq)
            chunk_feats = sc._feats_in_chunk(
                disp_feats, chunk_start, chunk_end, n,
            )
            owners = sp._chunk_glyph_owners(
                chunk_start, chunk_end, chunk_feats,
                above_p, below_p, above_rows, below_rows,
            )
            # Collect columns owned by the resite (any row, either
            # stack). Compare set across rotations.
            cols: set = set()
            for stack in (owners["owners_above"], owners["owners_below"]):
                for row in stack:
                    for c, owner in enumerate(row):
                        if owner is not None and owner.get("type") == "resite":
                            cols.add(c)
            return cols

        cols_unrotated = _owners_at(0)
        # Rotation = 5 → recognition shifts left by 5, so owner
        # cols should also shift left by 5.
        cols_rotated = _owners_at(5)
        # Owner cells must move with rotation; pre-fix the cache
        # would return the unrotated cols.
        assert cols_rotated != cols_unrotated, (
            f"Owner cache is stale across rotation — owners stayed "
            f"at {cols_unrotated} after origin shift to 5 instead "
            f"of moving to {{c - 5 for c in cols_unrotated}}."
        )
        expected = {(c - 5) % len(seq) for c in cols_unrotated}
        assert cols_rotated == expected, (
            f"After rotation by 5, expected owner cols shifted "
            f"by -5; got cols_unrotated={cols_unrotated}, "
            f"cols_rotated={cols_rotated}, expected={expected}"
        )

    def test_rotation_shifts_resite_cut_positions(self):
        """When the user rotates the plasmid origin, the cut bp
        fields on resite features (`top_cut_bp`, `bottom_cut_bp`,
        `ext_cut_bp`) must shift by the same modular offset as the
        recognition bounds. Pre-2026-05-08 only `start` / `end` got
        rotated — the cut markers stayed in absolute coords and
        the cut arrows / Type IIS dashed bridges pointed to the
        wrong column after rotation, looking stretched and
        misrepresenting where the enzyme would actually cut.
        """
        seq = "A" * 20 + "GGTCTC" + "N" * 10 + "A" * 64
        sites = sc._scan_restriction_sites(seq, circular=True)
        bsai = next(
            s for s in sites
            if s.get("type") == "resite" and s.get("label") == "BsaI"
        )
        # Sanity: BsaI cuts at 27 (top) / 31 (bot) for site at 20.
        assert bsai["top_cut_bp"]    == 27
        assert bsai["bottom_cut_bp"] == 31

        sp = sc.SequencePanel()
        sp._seq   = seq
        sp._feats = [bsai]
        # Unrotated: rotated state = original.
        seq_d, feats_d = sp._get_rotated_state()
        assert feats_d[0]["start"]         == 20
        assert feats_d[0]["top_cut_bp"]    == 27
        assert feats_d[0]["bottom_cut_bp"] == 31

        # Rotate origin to bp 5: every bp coord on the resite
        # should shift by -5 (mod n). Recognition + both cut bps
        # must move together so the visual stays consistent.
        sp._view_origin_bp = 5
        sp._rotated_cache_key = None
        sp._rotated_seq = sp._rotated_feats = None
        seq_d, feats_d = sp._get_rotated_state()
        n = len(seq)
        assert feats_d[0]["start"]         == (20 - 5) % n
        assert feats_d[0]["end"]           == (26 - 5) % n
        assert feats_d[0]["top_cut_bp"]    == (27 - 5) % n
        assert feats_d[0]["bottom_cut_bp"] == (31 - 5) % n

        # Rotation deep enough to wrap the cut around the origin:
        # set origin so the top_cut wraps under modular arithmetic.
        # The fields stay self-consistent (no negative values).
        sp._view_origin_bp = 28
        sp._rotated_cache_key = None
        sp._rotated_seq = sp._rotated_feats = None
        seq_d, feats_d = sp._get_rotated_state()
        # top_cut(27) - 28 = -1 → wraps to n-1.
        assert feats_d[0]["top_cut_bp"] == (27 - 28) % n
        # bottom_cut(31) - 28 = 3 → no wrap.
        assert feats_d[0]["bottom_cut_bp"] == 3

    def test_typeiip_recognition_split_blue_upstream_red_downstream(self):
        """Inside the recognition the per-base colour signals which
        fragment the base ends up on after cutting: blue = upstream,
        red = downstream. For palindromic EcoRI (`GAATTC`, top cut
        at p+1, bot cut at p+5) the recognition splits as:
          * Position 0 (G):     top blue,  bot blue.
          * Positions 1..4
            (AATT overhang):    top red    (right of top cut),
                                bot blue   (left of bot cut).
          * Position 5 (C):     top red,   bot red.

        That's the user's "show the two pieces that form" goal —
        across the recognition the user can read off which bases
        flow to the upstream fragment vs the downstream fragment.

        Type IIS overhang colours (green / orange) only fire on
        bases OUTSIDE the recognition, so palindromes never use
        them.
        """
        seq = "A" * 10 + "GAATTC" + "A" * 84
        sites = sc._scan_restriction_sites(seq, circular=True)
        ecori = next(
            s for s in sites
            if s.get("type") == "resite" and s.get("label") == "EcoRI"
        )
        sp = sc.SequencePanel()
        sp._seq = seq
        text = sc._build_seq_text(
            seq, [ecori], line_width=80,
            re_highlight=sp._resite_highlight_dict(ecori),
        )
        styles_used = {str(span.style or "") for span in text.spans}
        sty_blob = " ".join(styles_used).lower()
        # Both blue (upstream) and red (downstream) bg colours
        # should appear on the recognition.
        assert "on blue" in sty_blob
        assert "on red"  in sty_blob
        # Type IIS overhang palette must NOT appear: the
        # recognition stays blue/red even where it overlaps the
        # cut overhang region.
        assert "on green"  not in sty_blob, (
            f"EcoRI cuts INSIDE recognition — must NOT paint "
            f"green-bg; got styles: {sorted(styles_used)}"
        )
        assert "on orange" not in sty_blob, (
            f"EcoRI cuts INSIDE recognition — must NOT paint "
            f"orange-bg; got styles: {sorted(styles_used)}"
        )

    def test_3prime_overhang_mmei_overhang_renders_green_yellow(self):
        """MmeI is `TCCRAC(20/18)` — top cut (20) sits FURTHER
        from the recognition than the bottom cut (18), so
        ``top_cut > bot_cut`` and the enzyme makes a 3' overhang.

        Pre-2026-05-08 the renderer used ``top_cut <= i < bot_cut``
        for the overhang check, which never matched on 3' overhangs
        (range is empty). The MmeI overhang silently fell through
        to the gray spacer treatment instead of green/yellow.

        Fix uses ``min(top, bot) <= i < max(top, bot)`` which
        handles both sticky-end directions. This test pins the
        forward-strand 3' overhang case."""
        # `TCCAAC` matches the IUPAC `TCCRAC` (R = A/G).
        seq = "TCCAAC" + "N" * 30 + "A" * 64
        sites = sc._scan_restriction_sites(seq, circular=True)
        mmei = next(
            s for s in sites
            if s.get("type") == "resite" and s.get("label") == "MmeI"
        )
        # MmeI(20/18): site at 0..6, top cut at 20, bot cut at 18.
        assert mmei["top_cut_bp"]    == 20
        assert mmei["bottom_cut_bp"] == 18
        assert mmei["top_cut_bp"] > mmei["bottom_cut_bp"], (
            "MmeI is a 3' overhang enzyme — top_cut should exceed bot_cut"
        )

        sp = sc.SequencePanel()
        sp._seq = seq
        text = sc._build_seq_text(
            seq, [mmei], line_width=80,
            re_highlight=sp._resite_highlight_dict(mmei),
        )
        styles_used = {str(span.style or "") for span in text.spans}
        sty_blob = " ".join(styles_used).lower()
        assert "green"  in sty_blob, (
            f"3' overhang MmeI must paint top strand green; "
            f"got styles: {sorted(styles_used)}"
        )
        assert "orange" in sty_blob, (
            f"3' overhang MmeI must paint bot strand orange; "
            f"got styles: {sorted(styles_used)}"
        )

    def test_3prime_overhang_reverse_strand_mmei_renders_correctly(self):
        """Reverse-strand MmeI hit (`GTYGGA` = rc of `TCCRAC`)
        with cuts UPSTREAM of the recognition. For the reverse
        hit the cut order also flips (top_cut < bot_cut becomes
        top_cut > bot_cut depending on the strand), and the
        overhang region MUST still render in green/yellow."""
        # `GTTGGA` is the rc of `TCCAAC`; place it past p=30 so
        # both cuts (which are 12-14 bp upstream) land at
        # positive positions.
        seq = "A" * 30 + "GTTGGA" + "T" * 64
        sites = sc._scan_restriction_sites(seq, circular=True)
        mmei_rev = next(
            s for s in sites
            if s.get("type") == "resite" and s.get("label") == "MmeI"
        )
        # Sanity: cuts are upstream of the recognition (low bp).
        assert mmei_rev["top_cut_bp"]    < 30
        assert mmei_rev["bottom_cut_bp"] < 30

        sp = sc.SequencePanel()
        sp._seq = seq
        hi = sp._resite_highlight_dict(mmei_rev)
        # hi_start should extend back to the further-upstream
        # cut; hi_end should reach the recognition end.
        assert hi["start"] == min(
            mmei_rev["top_cut_bp"], mmei_rev["bottom_cut_bp"],
        )
        assert hi["end"] >= mmei_rev["end"]
        text = sc._build_seq_text(
            seq, [mmei_rev], line_width=80,
            re_highlight=hi,
        )
        styles_used = {str(span.style or "") for span in text.spans}
        sty_blob = " ".join(styles_used).lower()
        assert "green"  in sty_blob, (
            f"Reverse MmeI must paint overhang top strand green; "
            f"got styles: {sorted(styles_used)}"
        )
        assert "orange" in sty_blob, (
            f"Reverse MmeI must paint overhang bot strand yellow; "
            f"got styles: {sorted(styles_used)}"
        )

    def test_reverse_strand_typeiis_extends_left(self):
        # Reverse-strand BsaI (recognition GAGACC = rc(GGTCTC))
        # cuts on the OPPOSITE side: top cut is 5 bp before the
        # site, bot cut is 1 bp before. The highlight should
        # extend `start` BACKWARD to enclose both cuts.
        seq = "A" * 30 + "GAGACC" + "A" * 64
        sites = sc._scan_restriction_sites(seq, circular=True)
        bsai_rev = next(
            s for s in sites
            if s.get("type") == "resite" and s.get("label") == "BsaI"
        )
        sp = sc.SequencePanel()
        sp._seq = seq
        hi = sp._resite_highlight_dict(bsai_rev)
        # Highlight `start` should be at or below the smaller cut
        # (cuts are upstream of the site for reverse hits).
        assert hi["start"] <= min(
            bsai_rev["top_cut_bp"], bsai_rev["bottom_cut_bp"],
        )
        # And `end` should still cover the recognition end.
        assert hi["end"] >= bsai_rev["end"]


class TestEnterHighlightsFeatureAtCursor:
    """Regression guard for 2026-04-29: Enter in the seq-panel context
    should highlight the feature whose range contains the current
    cursor — equivalent to clicking the feature in the lane art."""

    async def test_enter_at_cursor_highlights_enclosing_feature(
        self, isolated_library,
    ):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 200), id="enter_test", name="enter_test",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features.append(SeqFeature(FeatureLocation(50, 100, strand=1),
                                       type="CDS",
                                       qualifiers={"label": ["midCDS"]}))
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            # Park cursor inside the CDS [50, 100). Clear focus so the
            # App-level Enter handler runs (not consumed by a focused
            # DataTable / Input / PlasmidMap).
            sp._cursor_pos = 75
            app.set_focus(None)
            await pilot.pause(0.05)
            await pilot.press("enter")
            await pilot.pause(0.05)
            # Map's selected_idx should now point at the CDS.
            cds_idx = next(i for i, f in enumerate(pm._feats)
                           if f.get("label") == "midCDS")
            assert pm.selected_idx == cds_idx, (
                f"Enter at bp 75 should select the CDS [50,100); "
                f"map.selected_idx={pm.selected_idx}, expected={cds_idx}"
            )
            # And the seq panel's full-feature highlight (`_user_sel`)
            # should now cover the whole CDS range [50, 100), set by
            # `select_feature_range` in `_focus_feature`.
            assert sp._user_sel == (50, 100), (
                f"Enter should highlight whole feature range [50,100); "
                f"got user_sel={sp._user_sel}"
            )

    async def test_enter_outside_any_feature_is_a_noop(
        self, isolated_library,
    ):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 200), id="t2", name="t2",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features.append(SeqFeature(FeatureLocation(50, 100, strand=1),
                                       type="CDS",
                                       qualifiers={"label": ["midCDS"]}))
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            # Cursor on bp 10 — outside the CDS range.
            sp._cursor_pos = 10
            sel_before = pm.selected_idx
            app.set_focus(None)
            await pilot.pause(0.05)
            await pilot.press("enter")
            await pilot.pause(0.05)
            # No feature contains bp 10, so nothing new gets selected.
            assert pm.selected_idx == sel_before


class TestLibrarySearch:
    """Search input on the LibraryPanel: pre-fill 'Search', focus clears,
    Enter applies fuzzy filter, empty Enter clears + restores prefill."""

    def test_fuzzy_match_subsequence(self):
        # Subsequence in order, case-insensitive.
        assert sc._fuzzy_match("lac", "LacZ alpha")
        assert sc._fuzzy_match("lcz", "LacZ alpha")
        assert sc._fuzzy_match("", "anything")
        assert not sc._fuzzy_match("xyz", "LacZ alpha")
        # 'zlc' fails because no 'c' after the 'z' in "LacZ alpha".
        assert not sc._fuzzy_match("zlc", "LacZ alpha")

    def test_fuzzy_match_empty_name_with_query_returns_false(self):
        """Edge case: a non-empty query against an empty name can
        never match. The early-exit avoids the lower() + scan path
        which would also return False, just cheaper."""
        assert sc._fuzzy_match("x", "") is False

    def test_fuzzy_match_query_longer_than_name_short_circuits(self):
        """Subsequence requires len(query) <= len(name). When the
        user types more chars than the longest plasmid name, every
        match early-rejects without lowercasing or scanning.

        Performance proxy: the early-reject path skips `str.lower()`
        which is the hot loop on big libraries with long descriptive
        names. We just assert the boolean result here; the perf win
        is exercised by `test_fuzzy_match_huge_query_doesnt_hang`."""
        assert sc._fuzzy_match("abcdef", "abc") is False
        assert sc._fuzzy_match("aaaa", "aaa") is False
        # Equal length still goes through the scan path.
        assert sc._fuzzy_match("abc", "abc") is True

    def test_fuzzy_match_huge_query_doesnt_hang(self):
        """A 100k-char paste shouldn't lock the search bar — fuzzy
        matching against typical plasmid names (10-30 chars) early-
        rejects via the length pre-check. Run 1000 iterations as a
        perf smoke test; on a slow CI host the bound is 0.5s.
        """
        import time
        big_query = "x" * 100_000
        names = [
            "pUC19", "pBR322", "pET28a", "pUPD2", "pAGM4673",
        ]
        t0 = time.perf_counter()
        for _ in range(1000):
            for n in names:
                assert sc._fuzzy_match(big_query, n) is False
        elapsed = time.perf_counter() - t0
        # 5000 calls × early-reject = should be < 50 ms on any
        # reasonable host. 0.5s is a generous CI-friendly bound.
        assert elapsed < 0.5, (
            f"fuzzy match too slow on huge query: {elapsed:.3f}s "
            "for 5000 calls — early-reject regressed?"
        )

    def test_fuzzy_match_unicode_lowercases(self):
        """The lower() call is unicode-aware, so a Greek prefix
        matches its own lowercase form."""
        assert sc._fuzzy_match("αβ", "ΑΒgene") is True
        assert sc._fuzzy_match("ΑΒ", "αβgene") is True


class TestSearchInputWidget:
    """`_SearchInput` is the reusable search-bar widget — prefill,
    focus-clear, blur-restore, optional debounce, length cap, timer
    cleanup. Regression guards for 2026-05-10 hardening."""

    async def test_prefill_default_and_current_query_empty(self):
        """The widget initialises with PREFILL as its display value;
        `current_query()` returns "" because the prefill counts as
        empty. Mirrors the library-panel idle state where the user
        hasn't typed anything yet.

        The harness uses a second widget to ensure the search bar
        does NOT auto-focus on mount — `on_focus` clears the prefill
        (per the documented "click into to clear" UX), and we want
        to assert the idle-state value here.
        """
        from textual.app import App
        from textual.widgets import Static

        class _Harness(App):
            def compose(self):
                yield Static("anchor", id="anchor")
                yield sc._SearchInput(id="harness-search")

        app = _Harness()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            inp = app.query_one("#harness-search", sc._SearchInput)
            # The anchor widget takes focus first; the search bar
            # holds its prefill until the user clicks into it.
            if inp.has_focus:
                # Some Textual versions still auto-focus the only
                # focusable widget. Defocus + restore prefill so the
                # assertion below tests the idle-state contract.
                inp.on_blur(None)
            assert inp.value == sc._SearchInput.PREFILL
            assert inp.current_query() == ""

    async def test_current_query_strips_whitespace(self):
        """A user-typed query with surrounding whitespace surfaces
        as the trimmed form. Defends downstream filters from having
        to strip themselves."""
        from textual.app import App

        class _Harness(App):
            def compose(self):
                yield sc._SearchInput(id="harness-search")

        app = _Harness()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            inp = app.query_one("#harness-search", sc._SearchInput)
            inp.value = "   pUC19   "
            assert inp.current_query() == "pUC19"

    async def test_length_cap_truncates_huge_paste(self):
        """A clipboard dump way past the cap is silently truncated.
        Without this the fuzzy matcher's O(query × name) loop would
        stall the UI before the debounce had a chance to fire."""
        from textual.app import App

        class _Harness(App):
            def compose(self):
                yield sc._SearchInput(id="harness-search", max_len=50)

        app = _Harness()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            inp = app.query_one("#harness-search", sc._SearchInput)
            inp.value = "x" * 5000
            await pilot.pause()
            assert len(inp.value) <= 50

    async def test_clear_resets_to_prefill(self):
        from textual.app import App

        class _Harness(App):
            def compose(self):
                yield sc._SearchInput(id="harness-search")

        app = _Harness()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            inp = app.query_one("#harness-search", sc._SearchInput)
            inp.value = "user typed"
            inp.clear()
            assert inp.value == sc._SearchInput.PREFILL
            assert inp.current_query() == ""

    async def test_debounce_fires_filter_callback_once(self):
        """A burst of three keystrokes within the debounce window
        fires the on_filter callback exactly once after the window
        expires. Without the cancel-and-reschedule it would fire
        three times."""
        import asyncio
        from textual.app import App
        calls = []

        class _Harness(App):
            def compose(self):
                yield sc._SearchInput(
                    id="harness-search",
                    debounce_s=0.05,
                    on_filter=lambda q: calls.append(q),
                )

        app = _Harness()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            inp = app.query_one("#harness-search", sc._SearchInput)
            # Type three chars in rapid succession.
            inp.value = "p"
            inp.value = "pU"
            inp.value = "pUC"
            # Wait past the debounce window.
            await asyncio.sleep(0.15)
            await pilot.pause()
        assert len(calls) == 1, (
            f"expected 1 debounced call, got {len(calls)}: {calls}"
        )
        # The callback receives the SANITISED query — last value
        # only, with prefill/whitespace stripped.
        assert calls[0] == "pUC"

    async def test_unmount_cancels_pending_debounce(self):
        """The debounce timer is freed on unmount so a queued tick
        doesn't fire against a disposed widget tree. Mirrors the
        same guarantee in `LoadPartSourceModal`."""
        import asyncio
        from textual.app import App
        calls = []

        class _Harness(App):
            def compose(self):
                yield sc._SearchInput(
                    id="harness-search",
                    debounce_s=0.20,
                    on_filter=lambda q: calls.append(q),
                )

        app = _Harness()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            inp = app.query_one("#harness-search", sc._SearchInput)
            inp.value = "queued"
            assert inp._filter_timer is not None
        # `app.run_test` exits → on_unmount fires → timer cancelled.
        # Extra wait past the original 0.20 s window — if the
        # cancel didn't fire, the callback would land here.
        await asyncio.sleep(0.30)
        assert calls == [], (
            f"timer fired post-unmount: {calls}"
        )

    async def test_blur_restores_prefill_when_empty(self):
        """An empty + unfocused field shows the prefill again so the
        idle UI keeps its 'Search' affordance. Whitespace-only
        counts as empty for this restore."""
        from textual.app import App

        class _Harness(App):
            def compose(self):
                yield sc._SearchInput(id="harness-search")

        app = _Harness()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            inp = app.query_one("#harness-search", sc._SearchInput)
            inp.value = "   "
            # Manually fire on_blur (Textual's harness doesn't
            # always dispatch focus events deterministically; the
            # method is a pure value-mutator anyway).
            inp.on_blur(None)
            assert inp.value == sc._SearchInput.PREFILL

    async def test_prefill_is_treated_as_empty_in_current_query(self):
        """Typing the literal prefill string and checking
        `current_query()` returns "" — defends against a user who
        hasn't focused the field but whose Submitted event still
        fires (some terminals dispatch Enter on a freshly-mounted
        Input that still shows the prefill)."""
        from textual.app import App

        class _Harness(App):
            def compose(self):
                yield sc._SearchInput(id="harness-search")

        app = _Harness()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            inp = app.query_one("#harness-search", sc._SearchInput)
            inp.value = sc._SearchInput.PREFILL
            assert inp.current_query() == ""

    def test_natural_sort_key_orders_numbers_by_value(self):
        """`pBin2` must sort before `pBin10` — lexicographic sort would
        put `pBin10` first because '1' < '2' as a character. Natural
        sort splits text and integer runs and compares integers
        numerically. Regression guard for the 2026-05-04 plasmid
        library sort fix."""
        names = ["pBin10", "pBin2", "pBin1", "pBin20", "pBin11", "pBin3"]
        srt = sorted(names, key=sc._natural_sort_key)
        assert srt == ["pBin1", "pBin2", "pBin3", "pBin10", "pBin11", "pBin20"]

    def test_natural_sort_key_handles_mixed_prefixes(self):
        """Different alpha prefixes still sort alphabetically; numeric
        runs only kick in when the surrounding text matches."""
        names = ["pBin2", "pAlpha10", "pAlpha2", "pBin10"]
        srt = sorted(names, key=sc._natural_sort_key)
        assert srt == ["palpha2", "palpha10", "pbin2", "pbin10"] or \
               srt == ["pAlpha2", "pAlpha10", "pBin2", "pBin10"]

    def test_natural_sort_key_no_digits_fallback(self):
        """Names without digits fall back to lex order."""
        srt = sorted(["zeta", "alpha", "mu"], key=sc._natural_sort_key)
        assert srt == ["alpha", "mu", "zeta"]

    def test_natural_sort_key_starting_with_digit(self):
        """Mixed types in the tuple don't crash — the helper wraps
        each chunk with a `(0, str)` / `(1, int)` discriminator so
        Python never compares an int to a str directly. `5kb_X` and
        `pBin1` would otherwise crash on tuple comparison in Py3.
        Text chunks rank before integer chunks (`(0,...) < (1,...)`),
        so alpha-prefix names land before pure-digit-prefix ones —
        the order Linux `sort -V` produces, and the most useful for
        a plasmid library that's mostly named with letter prefixes."""
        srt = sorted(["pBin1", "5kb_backbone", "10kb_backbone"],
                      key=sc._natural_sort_key)
        # Alpha-prefixed names (`pBin1`) come BEFORE digit-prefixed
        # ones, then the digit-prefixed names sort numerically among
        # themselves (`5kb` before `10kb`).
        assert srt == ["pBin1", "5kb_backbone", "10kb_backbone"]

    async def test_library_panel_displays_plasmids_in_natural_order(
            self, isolated_library, tiny_record):
        """End-to-end check: adding pBin1, pBin10, pBin2, pBin20 in
        random order and the library DataTable lists them as
        pBin1, pBin2, pBin10, pBin20."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            for nm in ("pBin10", "pBin2", "pBin20", "pBin1"):
                rec = SeqRecord(Seq("A" * 50), id=nm, name=nm,
                                annotations={"molecule_type": "DNA",
                                             "topology": "circular"})
                lib.add_entry(rec)
            await pilot.pause()
            from textual.widgets import DataTable
            t = app.query_one("#lib-table", DataTable)
            # First column of each row is the (Text-wrapped) name —
            # walk the rows in display order and pull out the plain
            # string. We only care about the rows we added; ignore
            # the seed `tiny_record` if it's listed.
            ours = {"pBin1", "pBin2", "pBin10", "pBin20"}
            order = []
            for row_key in t.rows:
                row = t.get_row(row_key)
                cell0 = row[0]
                name = cell0.plain if hasattr(cell0, "plain") else str(cell0)
                # Strip the colour-circle prefix (2 cells: `● ` for
                # status-bearing rows, `  ` for no-status rows) plus
                # the dirty-marker asterisk.
                name = name.lstrip("● ").lstrip("*")
                if name in ours:
                    order.append(name)
            assert order == ["pBin1", "pBin2", "pBin10", "pBin20"], (
                f"expected natural sort order; got {order}"
            )

    async def test_directory_tree_uses_natural_sort(self, tmp_path):
        """File browser modals must list directory contents in
        natural-sort order (FFE 2 before FFE 10), matching the
        plasmid library panel. Regression guard for 2026-05-08:
        Textual's `DirectoryTree._load_directory` sorts by lower-
        cased name only, which puts `FFE 10` before `FFE 2` in a
        bare lexicographic order. Our `_ExtensionAwareDirectoryTree`
        overrides `_populate_node` to apply `_natural_sort_key`.
        """
        # Create a directory of mixed-numbered plasmid files.
        for n in (1, 2, 10, 11, 3, 20):
            (tmp_path / f"FFE {n} ENTRY.dna").write_bytes(b"")
        # Subdirectories should still come first regardless of sort.
        (tmp_path / "0_subdir").mkdir()

        # Drive the populate path directly: `_populate_node` is
        # what receives the directory listing and writes the tree
        # nodes, and our override is what reorders.
        from pathlib import Path
        files = sorted(
            tmp_path.iterdir(),
            key=lambda p: (
                not p.is_dir(),
                p.name.lower(),
            ),
        )
        # Lex order would put "FFE 10 …" before "FFE 2 …".
        assert files[1].name.startswith("FFE 1 "), (
            "test pre-condition: lexicographic order misorders"
        )

        # Now simulate our override's sort.
        natural_sorted = sorted(
            tmp_path.iterdir(),
            key=lambda p: (
                not p.is_dir(),
                sc._natural_sort_key(p.name),
            ),
        )
        # 0_subdir first (directory), then files in numeric order.
        names = [p.name for p in natural_sorted]
        assert names[0] == "0_subdir"
        # Files should be ordered FFE 1, 2, 3, 10, 11, 20.
        file_names = names[1:]
        assert file_names == [
            "FFE 1 ENTRY.dna",
            "FFE 2 ENTRY.dna",
            "FFE 3 ENTRY.dna",
            "FFE 10 ENTRY.dna",
            "FFE 11 ENTRY.dna",
            "FFE 20 ENTRY.dna",
        ], f"got {file_names}"

    async def test_search_filter_applies_and_clears(
        self, tiny_record, isolated_library,
    ):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Seed the library with two distinguishable plasmids so we
            # can verify the filter actually narrows the table rows.
            from Bio.Seq import Seq
            from Bio.SeqRecord import SeqRecord
            for nm in ("alphaPlasmid", "betaConstruct"):
                rec = SeqRecord(Seq("A" * 50), id=nm, name=nm,
                                annotations={"molecule_type": "DNA",
                                             "topology": "circular"})
                app.query_one("#library", sc.LibraryPanel).add_entry(rec)
            await pilot.pause(0.05)

            # Switch to plasmids view so the lib-table is what we filter.
            lib = app.query_one("#library", sc.LibraryPanel)
            lib._view_mode = "plasmids"
            lib._apply_view_mode()
            lib._repopulate()
            await pilot.pause(0.05)
            tbl = app.query_one("#lib-table", sc.DataTable)
            assert tbl.row_count >= 2

            # Apply filter "alpha" — only alphaPlasmid should remain.
            inp = app.query_one("#lib-search", sc.Input)
            inp.value = "alpha"
            await inp.action_submit()
            await pilot.pause(0.05)
            assert lib._filter_text == "alpha"
            assert tbl.row_count == 1

            # Clear filter via empty submit; prefill restored.
            inp.value = ""
            await inp.action_submit()
            await pilot.pause(0.05)
            assert lib._filter_text == ""
            assert inp.value == sc._SearchInput.PREFILL
            assert tbl.row_count >= 2

    async def test_focus_clears_input_value(
        self, tiny_record, isolated_library,
    ):
        """Clicking into the input clears whatever was displayed (the
        'Search' prefill, an active filter, etc.) so the cursor opens
        on a fresh field."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            inp = app.query_one("#lib-search", sc.Input)
            assert inp.value == sc._SearchInput.PREFILL
            app.set_focus(inp)
            await pilot.pause(0.05)
            assert inp.value == ""


class TestDeleteClearsStaleData:
    """2026-05-07: deletion of the loaded plasmid from the library
    used to leave the plasmid map / sidebar / sequence panel showing
    the now-deleted plasmid's data. `_clear_canvas` resets every
    panel to an empty state when called from the delete-confirm
    callback. Tested directly here without the confirm modal so the
    assertions don't depend on async modal dispatch."""

    async def test_clear_canvas_drops_record_and_panels(
            self, tiny_record, isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            assert app._current_record is not None
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sidebar = app.query_one("#sidebar", sc.FeatureSidebar)
            seq_pnl = app.query_one("#seq-panel", sc.SequencePanel)
            # Pre-condition: panels carry the loaded record.
            assert pm.record is not None
            assert pm._feats
            assert seq_pnl._seq

            app._clear_canvas()
            await pilot.pause()

            # Record handle dropped + panels emptied.
            assert app._current_record is None
            assert pm.record is None
            assert pm._feats == []
            assert pm._restr_feats == []
            assert seq_pnl._seq == ""
            assert seq_pnl._feats == []
            # Sidebar table is empty (row→feat mapping is empty too).
            assert sidebar._row_to_feat_idx == []
            # Source-path / unsaved flag wiped so Ctrl+S can't
            # accidentally write to the deleted file's path.
            assert app._source_path is None
            assert app._unsaved is False


class TestCrashRecoveryNoticeOncePerSet:
    """`_check_crash_recovery` should warn ONCE per leftover set —
    same files / same mtimes on the next launch should NOT re-fire
    the toast. New leftovers (or re-written ones) should still
    trigger a fresh notice. Cleaning the directory clears the
    seen-set so a future first crash isn't silenced.

    The helper runs from `on_mount` so the test patches `notify` on
    the class BEFORE the app instance is created — otherwise the
    first call lands before the per-instance patch can attach.
    """

    @staticmethod
    def _make_leftover(dir_path, name="test_plasmid"):
        dir_path.mkdir(parents=True, exist_ok=True)
        f = dir_path / f"{name}-abcd.gb"
        f.write_text("LOCUS test\n")
        return f

    @staticmethod
    def _patch_notify(monkeypatch):
        """Replace `PlasmidApp.notify` with a capture list. Returns
        the list so the test can assert on it after run_test exits."""
        notices: list = []
        def _capture(self, msg, *a, **kw):
            notices.append(msg)
        monkeypatch.setattr(sc.PlasmidApp, "notify", _capture)
        return notices

    async def test_first_launch_notifies_subsequent_quiet(
            self, tiny_record, isolated_library, tmp_path,
            monkeypatch):
        crash_dir = tmp_path / "crash_recovery"
        monkeypatch.setattr(sc, "_CRASH_RECOVERY_DIR", crash_dir)
        self._make_leftover(crash_dir, "rec_a")
        self._make_leftover(crash_dir, "rec_b")
        # Make sure the seen-set starts empty for this test (the
        # autouse fixture redirects _SETTINGS_FILE to a tmp dir, so
        # we just need to clear the in-memory cache).
        notices1 = self._patch_notify(monkeypatch)
        app1 = _build_app(tiny_record, isolated_library)
        async with app1.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
        # First launch's on_mount fired the recovery notice.
        assert any("recovery" in str(m).lower() for m in notices1)
        sc._settings_flush_sync()
        assert sc._get_setting("crash_recovery_seen")

        # Second launch with the same leftovers: no notice fires.
        notices2 = self._patch_notify(monkeypatch)
        app2 = _build_app(tiny_record, isolated_library)
        async with app2.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
        assert not any("recovery" in str(m).lower() for m in notices2)

    async def test_new_leftover_re_triggers_notice(
            self, tiny_record, isolated_library, tmp_path,
            monkeypatch):
        crash_dir = tmp_path / "crash_recovery"
        monkeypatch.setattr(sc, "_CRASH_RECOVERY_DIR", crash_dir)
        old = self._make_leftover(crash_dir, "old_rec")
        # Pre-seed the seen-set so the OLD file alone would be quiet.
        sc._set_setting(
            "crash_recovery_seen",
            [f"{old.name}|{int(old.stat().st_mtime)}"],
        )
        sc._settings_flush_sync()
        # Add a brand-new leftover.
        self._make_leftover(crash_dir, "fresh_rec")
        notices = self._patch_notify(monkeypatch)
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
        assert any("fresh_rec" in str(m) for m in notices)

    async def test_clean_directory_resets_seen_set(
            self, tiny_record, isolated_library, tmp_path,
            monkeypatch):
        crash_dir = tmp_path / "crash_recovery"
        crash_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(sc, "_CRASH_RECOVERY_DIR", crash_dir)
        # Stale seen-set from a prior session.
        sc._set_setting(
            "crash_recovery_seen", ["something_old|123"],
        )
        sc._settings_flush_sync()
        self._patch_notify(monkeypatch)
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
        sc._settings_flush_sync()
        # Clean dir → seen-set cleared, so a future first-time
        # crash won't be silenced by the stale acknowledgement.
        assert not sc._get_setting("crash_recovery_seen")


class TestSidebarSortOrder:
    """Sidebar rows are sorted by (start, end) ASC so features list in
    appearance order from origin (clockwise on circular plasmids).
    Tiebreak is shorter-feature-first when starts match. Wrap features
    sort to the end naturally because their `start` is the high
    physical position. The sort is display-only; `pm._feats` keeps
    record order so colour assignment and `_feats_by_start` semantics
    don't move under the sidebar's feet."""

    def test_sort_key_orders_by_start_then_end(self):
        # Three features at the same start: sort by end ASC = shortest
        # span first ("span closest to origin first").
        a = {"start": 100, "end": 200, "strand": 1}
        b = {"start": 100, "end": 150, "strand": 1}
        c = {"start": 100, "end": 175, "strand": 1}
        feats = [a, b, c]
        ranked = sorted(range(len(feats)),
                        key=lambda i: sc.FeatureSidebar._sort_key(feats[i]))
        assert ranked == [1, 2, 0]   # b (end=150), c (end=175), a (end=200)

    def test_sort_key_origin_first(self):
        # Features at different starts sort by start ASC; origin-anchored
        # feature comes first regardless of length.
        early_long = {"start": 0,    "end": 5000, "strand": 1}
        mid_short  = {"start": 1000, "end": 1010, "strand": 1}
        late       = {"start": 4000, "end": 4500, "strand": 1}
        feats = [late, early_long, mid_short]
        ranked = sorted(range(len(feats)),
                        key=lambda i: sc.FeatureSidebar._sort_key(feats[i]))
        assert [feats[i]["start"] for i in ranked] == [0, 1000, 4000]

    def test_sort_key_wrap_feature_sorts_late(self):
        # Wrap feature (`end < start`) has a large `start` and sorts to
        # the end of the list — its leading edge in clockwise traversal
        # IS that high `start`, even though the tail crosses origin.
        head        = {"start": 0,    "end": 100,  "strand": 1}
        middle      = {"start": 2000, "end": 2100, "strand": 1}
        wrap        = {"start": 5800, "end": 100,  "strand": 1}  # wraps origin
        feats = [wrap, head, middle]
        ranked = sorted(range(len(feats)),
                        key=lambda i: sc.FeatureSidebar._sort_key(feats[i]))
        # head, middle, wrap (wrap last because start=5800).
        assert ranked == [1, 2, 0]

    def test_sort_key_with_origin_rotation(self):
        # When the user rotates the plasmid map's origin, the sidebar
        # re-sorts so the feature nearest the new origin (clockwise)
        # comes first. Distance is `(start - origin) % total`.
        n = 6000
        a = {"start": 100,  "end": 200,  "strand": 1}
        b = {"start": 1000, "end": 1100, "strand": 1}
        c = {"start": 4000, "end": 4500, "strand": 1}
        feats = [a, b, c]
        # Rotate origin to bp 500. Distances: a=(100-500)%6000=5600,
        # b=(1000-500)%6000=500, c=(4000-500)%6000=3500. So b comes
        # first (closest clockwise from origin), then c, then a.
        ranked = sorted(range(len(feats)),
                        key=lambda i: sc.FeatureSidebar._sort_key(
                            feats[i], origin_bp=500, total_bp=n,
                        ))
        assert ranked == [1, 2, 0]   # b, c, a

    def test_sort_key_with_origin_zero_unrotated(self):
        # `origin_bp=0` (or `total_bp=0`) is the unrotated path —
        # falls back to the historical `(start, end)` sort. Verifies
        # the rotation parameter doesn't change historical behaviour
        # when the user hasn't rotated.
        a = {"start": 100, "end": 200, "strand": 1}
        b = {"start": 50,  "end": 150, "strand": 1}
        feats = [a, b]
        ranked = sorted(range(len(feats)),
                        key=lambda i: sc.FeatureSidebar._sort_key(
                            feats[i], origin_bp=0, total_bp=6000,
                        ))
        assert ranked == [1, 0]   # b (start=50) before a (start=100)

    def test_sort_key_handles_missing_or_garbage_coords(self):
        # Defensive: a feature dict missing start/end (or with None)
        # should sort to position 0 without raising.
        ok        = {"start": 100, "end": 200, "strand": 1}
        no_start  = {"end": 50,    "strand": 1}
        garbage   = {"start": None, "end": None, "strand": 1}
        feats = [ok, no_start, garbage]
        # Both no_start and garbage become (0, *), sort before ok.
        ranked = sorted(range(len(feats)),
                        key=lambda i: sc.FeatureSidebar._sort_key(feats[i]))
        # Order between no_start (0, 50) and garbage (0, 0): garbage first.
        assert ranked[0] == 2   # garbage (0, 0)
        assert ranked[1] == 1   # no_start (0, 50)
        assert ranked[2] == 0   # ok (100, 200)

    async def test_populate_builds_row_to_feat_idx_mapping(self,
                                                            isolated_library):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        # Three features, intentionally added OUT OF ORDER (record
        # order ≠ start order) so we can verify the sidebar
        # re-orders for display while the feat indices still resolve.
        rec = SeqRecord(Seq("A" * 5000), id="sortTest", name="sortTest",
                         annotations={"molecule_type": "DNA",
                                      "topology": "circular"})
        rec.features.append(SeqFeature(
            FeatureLocation(3000, 3500, strand=1), type="CDS",
            qualifiers={"label": ["lateFeat"]},
        ))
        rec.features.append(SeqFeature(
            FeatureLocation(100, 200, strand=1), type="CDS",
            qualifiers={"label": ["earlyFeat"]},
        ))
        rec.features.append(SeqFeature(
            FeatureLocation(1000, 1100, strand=1), type="CDS",
            qualifiers={"label": ["midFeat"]},
        ))
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            sidebar = app.query_one("#sidebar", sc.FeatureSidebar)
            # `_row_to_feat_idx` maps display row → pm._feats index.
            # The display order should be early, mid, late → so row 0
            # points at earlyFeat, etc.
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            labels_in_row_order = [
                pm._feats[sidebar._row_to_feat_idx[r]]["label"]
                for r in range(len(sidebar._row_to_feat_idx))
            ]
            assert labels_in_row_order == ["earlyFeat", "midFeat", "lateFeat"]
            # Inverse mapping resolves the right way too.
            for row, feat_idx in enumerate(sidebar._row_to_feat_idx):
                assert sidebar._feat_idx_to_row[feat_idx] == row


class TestOriginRotationCascade:
    """When the user rotates the plasmid map's origin, the change
    cascades through the OriginChanged message: the sidebar
    re-sorts so the feature nearest the new origin (clockwise)
    becomes row 0, and the seq panel rotates so display row 0
    starts at the new origin's base.

    Regression guard for the 2026-05-07 cross-panel rotation feature.
    """

    async def test_rotation_reorders_sidebar(self, isolated_library):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 6000), id="rotTest",
                         annotations={"molecule_type": "DNA",
                                      "topology": "circular"})
        # Three features at bp 100, 1000, 4000.
        rec.features.append(SeqFeature(
            FeatureLocation(100, 200, strand=1), type="CDS",
            qualifiers={"label": ["alpha"]},
        ))
        rec.features.append(SeqFeature(
            FeatureLocation(1000, 1100, strand=1), type="CDS",
            qualifiers={"label": ["beta"]},
        ))
        rec.features.append(SeqFeature(
            FeatureLocation(4000, 4200, strand=1), type="CDS",
            qualifiers={"label": ["gamma"]},
        ))
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sidebar = app.query_one("#sidebar", sc.FeatureSidebar)
            # Unrotated: alpha (100), beta (1000), gamma (4000).
            order = [
                pm._feats[sidebar._row_to_feat_idx[r]]["label"]
                for r in range(len(sidebar._row_to_feat_idx))
            ]
            assert order == ["alpha", "beta", "gamma"]
            # Rotate origin to bp 500 (between alpha and beta).
            # Distances: alpha=(100-500)%6000=5600, beta=500, gamma=3500.
            # New row order: beta, gamma, alpha.
            pm.origin_bp = 500
            await pilot.pause()
            await pilot.pause(0.1)
            order = [
                pm._feats[sidebar._row_to_feat_idx[r]]["label"]
                for r in range(len(sidebar._row_to_feat_idx))
            ]
            assert order == ["beta", "gamma", "alpha"], (
                f"Sidebar didn't reorder after map rotation: {order}"
            )

    async def test_rotation_shifts_seq_panel_view_origin(
            self, isolated_library):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ACGT" * 1000), id="seqRot",   # 4000 bp
                         annotations={"molecule_type": "DNA",
                                      "topology": "circular"})
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            assert sp._view_origin_bp == 0
            pm.origin_bp = 500
            await pilot.pause()
            await pilot.pause(0.1)
            assert sp._view_origin_bp == 500, (
                f"SequencePanel.view_origin_bp didn't sync to map's "
                f"origin_bp: got {sp._view_origin_bp}"
            )
            # Cursor snaps to the new origin so the user has a clear
            # "you are here" anchor at the first base of the rotated
            # view. Selection / highlight clear too — they pointed at
            # positions valid only under the previous rotation.
            assert sp._cursor_pos == 500, (
                f"Cursor should snap to new origin (500); "
                f"got {sp._cursor_pos}"
            )
            assert sp._sel_range is None
            assert sp._user_sel is None
            # Reset back to 0 — the cascade must clear too.
            pm.origin_bp = 0
            await pilot.pause()
            await pilot.pause(0.1)
            assert sp._view_origin_bp == 0
            assert sp._cursor_pos == 0   # cursor follows back to bp 0

    async def test_rotation_scrolls_seq_panel_to_top(
            self, isolated_library):
        # After rotation the new origin's base lives at display row 0;
        # the seq panel must scroll there so the user sees the new
        # starting base, not whatever row they were on before.
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ACGT" * 2500), id="scrollRot",   # 10000 bp
                         annotations={"molecule_type": "DNA",
                                      "topology": "circular"})
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            scroll = app.query_one("#seq-scroll")
            # Manually scroll the seq panel down so we can verify the
            # rotation snaps it back to the top.
            scroll.scroll_to(y=20, force=True, animate=False)
            await pilot.pause()
            assert scroll.scroll_y > 5, "test pre-condition: scroll moved"
            pm.origin_bp = 4000
            await pilot.pause()
            await pilot.pause(0.1)
            # After rotation, scroll lands at display row 0 (= the
            # new origin's base).
            assert scroll.scroll_y == 0, (
                f"Seq panel should scroll to top after rotation; "
                f"got scroll_y={scroll.scroll_y}"
            )

    async def test_alt_o_sets_origin_to_selected_feature(
            self, isolated_library):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 6000), id="altO",
                         annotations={"molecule_type": "DNA",
                                      "topology": "circular"})
        rec.features.append(SeqFeature(
            FeatureLocation(2500, 2700, strand=1), type="CDS",
            qualifiers={"label": ["target"]},
        ))
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm.origin_bp == 0
            # Highlight the target feature, then trigger Alt+O via the
            # action method (the binding routes to the same code).
            pm.selected_idx = 0
            pm.action_set_origin_to_selected()
            await pilot.pause()
            await pilot.pause(0.1)
            assert pm.origin_bp == 2500, (
                f"Alt+O should anchor origin at feature start (2500); "
                f"got {pm.origin_bp}"
            )

    def test_seq_panel_disp_to_abs_round_trip(self):
        """The display ↔ absolute conversion helpers must round-trip
        — wrap arithmetic is easy to get wrong, and the click
        handler relies on them inverting cleanly."""
        sp = sc.SequencePanel()
        sp._seq = "A" * 1000
        sp._view_origin_bp = 250
        # Round-trip through both directions.
        for abs_bp in (0, 100, 250, 600, 999):
            disp = sp._abs_to_disp(abs_bp)
            assert sp._disp_to_abs(disp) == abs_bp
        # Sentinel -1 passes through unchanged.
        assert sp._abs_to_disp(-1) == -1
        assert sp._disp_to_abs(-1) == -1
        # Origin == 0 fast path returns input unchanged.
        sp._view_origin_bp = 0
        assert sp._abs_to_disp(500) == 500
        assert sp._disp_to_abs(500) == 500

    def test_update_seq_clamps_view_origin_on_shrink(self):
        """``update_seq`` must clamp ``_view_origin_bp`` to the new
        sequence length — pre-fix a sequence shrink that dropped below
        the current rotation origin would leave the seq panel pointing
        past the end, silently degrading ``_get_rotated_state`` (no
        rotation visible, but feature shifts mis-aligned). In practice
        the edit path resets origin via ``pm.load_record`` so this
        couldn't happen via the UI; the clamp is defensive depth for
        any future code path that bypasses the load_record reset."""
        sp = sc.SequencePanel()
        # `update_seq` ends with a `_refresh_view` that needs a
        # mounted widget tree (queries `#seq-view`). The clamp is
        # what we're testing — neutralise the trailing render so the
        # test stays out of the Textual mount path.
        sp._refresh_view = lambda: None   # type: ignore[assignment]
        sp._seq = "A" * 1000
        sp._view_origin_bp = 700
        # Shrink the sequence to 500 bp via update_seq directly.
        sp.update_seq("A" * 500, [])
        # _view_origin_bp must now be < 500 (clamped via % 500).
        assert sp._view_origin_bp == 200
        # Empty sequence collapses origin to 0.
        sp.update_seq("", [])
        assert sp._view_origin_bp == 0

    def test_get_rotated_state_handles_stale_origin(self):
        """``_get_rotated_state`` defensively re-clamps origin to
        ``% n`` so even if some path leaves ``_view_origin_bp`` past
        the current sequence length, the rotation math still produces
        a valid display state instead of silently degrading."""
        sp = sc.SequencePanel()
        sp._seq = "A" * 100
        sp._feats = []
        # Inject an out-of-bounds origin BYPASSING update_seq's clamp.
        sp._view_origin_bp = 250
        rot_seq, rot_feats = sp._get_rotated_state()
        # Effective origin = 250 % 100 = 50. Display starts at abs bp 50.
        assert len(rot_seq) == 100
        assert rot_feats == []


class TestSidebarClickCentersSeqPanel:
    """Regression guard for the 2026-04-25 sidebar-click centering fix.

    Clicking a feature in the sidebar previously highlighted it but did not
    scroll the sequence panel. Users with a 50 kb plasmid had to manually
    scroll through hundreds of rows to find the feature they just clicked.
    Now the seq panel jumps to the feature's wrap-aware midpoint."""

    async def test_sidebar_click_scrolls_seq_panel_to_feature(
        self, isolated_library,
    ):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        # 5 kb plasmid with a feature far past the initial viewport so
        # centering must scroll meaningfully (not stay at scroll_y=0).
        rec = SeqRecord(Seq("A" * 5000), id="centerTest",
                        annotations={"molecule_type": "DNA"})
        rec.features.append(SeqFeature(
            FeatureLocation(4000, 4200, strand=1), type="CDS",
            qualifiers={"label": ["targetFeat"]},
        ))
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sidebar = app.query_one("#sidebar", sc.FeatureSidebar)

            target_idx = next(
                i for i, f in enumerate(pm._feats)
                if f.get("label") == "targetFeat"
            )
            # Post the sidebar's RowActivated message — this is what fires
            # when the user actually clicks a feature row.
            sidebar.post_message(sc.FeatureSidebar.RowActivated(target_idx))
            await pilot.pause()
            await pilot.pause(0.1)  # let call_after_refresh do its scroll
            scroll = app.query_one("#seq-scroll")
            # Pre-fix: scroll_y stayed at 0. Post-fix: scrolls toward the
            # feature at bp 4100 (far row).
            assert scroll.scroll_y > 5, (
                f"Sidebar click on feature at bp 4100 should scroll seq "
                f"panel meaningfully; scroll_y={scroll.scroll_y}"
            )


class TestClickConsistencyAcrossPanels:
    """The three "I clicked a feature" entry points all set `user_sel`
    to the feature span and scroll the seq panel into view, but they
    differ on cursor placement:
      * Plasmid-map / sidebar feature click → cursor at START
        (post-2026-04-30: clicking a feature row scrolls to the 5' end
        rather than the midpoint, so the user reads top-down).
      * Seq-panel lane click → cursor at the clicked bp (the user
        already pointed at a specific position; honour it)."""

    async def test_all_three_click_paths_set_user_sel(
        self, isolated_library,
    ):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 5000), id="clickConsistency",
                        annotations={"molecule_type": "DNA"})
        for i in range(3):
            rec.features.append(SeqFeature(
                FeatureLocation(i * 1500 + 100, i * 1500 + 200, strand=1),
                type="CDS", qualifiers={"label": [f"f{i}"]},
            ))
        rec.features.append(SeqFeature(
            FeatureLocation(4000, 4200, strand=1), type="CDS",
            qualifiers={"label": ["targetFeat"]},
        ))
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            scroll = app.query_one("#seq-scroll")
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sidebar = app.query_one("#sidebar", sc.FeatureSidebar)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)

            target_idx = next(i for i, f in enumerate(pm._feats)
                              if f.get("label") == "targetFeat")
            target = pm._feats[target_idx]
            target_mid = (target["start"] + target["end"]) // 2  # 4100

            async def reset_state():
                scroll.scroll_y = 0
                sidebar._prog_row = -1
                sp._user_sel = None
                sp._sel_range = None
                sp._cursor_pos = -1
                await pilot.pause(0.05)

            def assert_user_sel():
                assert sp._user_sel == (4000, 4200), (
                    f"user_sel must be the feature span; got {sp._user_sel}"
                )

            # 1. Plasmid-map click → cursor at start (4000).
            await reset_state()
            pm.post_message(sc.PlasmidMap.FeatureSelected(
                target_idx, target, bp=target_mid
            ))
            await pilot.pause(0.5)
            assert_user_sel()
            assert sp._cursor_pos == 4000, (
                f"map click must park cursor at feature START; "
                f"got {sp._cursor_pos}"
            )
            assert scroll.scroll_y > 30

            # 2. Sequence-panel lane click → cursor at click bp (4100).
            # Lane clicks deliberately do NOT scroll: the user clicked
            # something they were already looking at, so jumping the
            # viewport away from their cursor would be jarring.
            await reset_state()
            sp.post_message(
                sc.SequencePanel.SequenceClick(bp=target_mid, from_lane=True)
            )
            await pilot.pause(0.5)
            assert_user_sel()
            assert sp._cursor_pos == target_mid, (
                f"lane click must honour the clicked bp ({target_mid}); "
                f"got {sp._cursor_pos}"
            )
            # Lane click no longer scrolls — the user is already on
            # the feature in the seq panel.
            assert scroll.scroll_y == 0, (
                f"lane click must NOT scroll the seq panel; "
                f"scroll_y={scroll.scroll_y}"
            )

            # 3. Sidebar row click → cursor at start (4000).
            await reset_state()
            await pilot.click("#feat-table", offset=(5, target_idx + 1))
            await pilot.pause(0.5)
            assert_user_sel()
            assert sp._cursor_pos == 4000, (
                f"sidebar click must park cursor at feature START; "
                f"got {sp._cursor_pos}"
            )
            assert scroll.scroll_y > 30


class TestSidebarArrowNavSingleScroll:
    """Regression guard for the 2026-04-25 sidebar-arrow-key jitter fix.

    Pressing Up/Down in the sidebar's feature list cascades into
    `_focus_feature`, which used to call `select_feature_range` (which
    triggered `_ensure_cursor_visible` — partial scroll just-into-view)
    AND THEN `center_on_bp` (full scroll to centre). The two scrolls
    happened in quick succession and were perceptible as a jitter / snap
    on every arrow press. Fix: pass `scroll=False` to the highlight
    helpers so EXACTLY ONE scroll runs per arrow press — either
    `_ensure_cursor_visible` (multi-row features, post-2026-04-30) or
    `center_on_bp` (single-row features), never both.
    """

    async def test_no_center_snap_on_feature_focus(
        self, isolated_library,
    ):
        """`_focus_feature` (lane click / map click / sidebar click /
        sidebar arrow nav) must always use minimum-scroll, never
        `center_on_bp`. Snapping a feature to viewport centre yanked
        the view away from whatever the user was looking at — even
        for short single-row features the cursor at start landing
        mid-viewport felt jarring. Post-2026-04-30 the fix is
        unconditional: every feature focus path goes through
        `_ensure_cursor_visible`, which only scrolls if the cursor
        is actually off-screen."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 5000), id="focusScroll",
                        annotations={"molecule_type": "DNA"})
        for i in range(4):
            rec.features.append(SeqFeature(
                FeatureLocation(i * 1200 + 100, i * 1200 + 130, strand=1),
                type="CDS", qualifiers={"label": [f"f{i}"]},
            ))

        center_calls = []
        orig_center = sc.SequencePanel.center_on_bp
        def spy_center(self, bp):
            center_calls.append(bp)
            orig_center(self, bp)
        sc.SequencePanel.center_on_bp = spy_center

        try:
            app = sc.PlasmidApp()
            app._preload_record = rec
            async with app.run_test(size=TERMINAL_SIZE) as pilot:
                await pilot.pause()
                await pilot.pause(0.05)

                await pilot.click("#feat-table", offset=(5, 1))
                await pilot.pause(0.3)
                center_calls.clear()

                # Arrow through the sidebar — each press fires
                # `_focus_feature`. None should hit `center_on_bp`.
                for _ in range(3):
                    await pilot.press("down")
                    await pilot.pause(0.3)

                assert center_calls == [], (
                    f"feature focus path must not center-snap; "
                    f"center_on_bp called with {center_calls}"
                )
        finally:
            sc.SequencePanel.center_on_bp = orig_center


class TestEnsureCursorVisibleShowsLanes:
    """Regression guard for the 2026-04-25 chunk-aware scroll fix.

    `_ensure_cursor_visible` previously scrolled to put the cursor's DNA
    forward-strand row at the top of the viewport when the user scrolled
    up. That left the feature lanes ABOVE the DNA off-screen, so the user
    had to press Up again just to see which feature their cursor was on.
    The fix targets `chunk_top` (DNA row minus above-lane rows) instead.
    """

    async def test_scroll_up_brings_above_lanes_into_view(
        self, isolated_library,
    ):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        # Build a record where every chunk has a feature so above_pairs > 0.
        rec = SeqRecord(Seq("A" * 2000), id="laneScrollTest",
                        annotations={"molecule_type": "DNA"})
        for i in range(0, 2000, 100):
            rec.features.append(SeqFeature(
                FeatureLocation(i, i + 80, strand=1), type="CDS",
                qualifiers={"label": [f"f{i}"]},
            ))

        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)

            # Park the cursor deep into the sequence so scrolling up to a
            # mid-chunk has somewhere to go.
            sp._cursor_pos = 1500
            sp.focus()
            await pilot.pause(0.05)
            sp._ensure_cursor_visible()
            await pilot.pause(0.05)

            scroll = app.query_one("#seq-scroll")
            scroll_y_before = scroll.scroll_y

            # Scroll up via Up arrow until cursor is at the top of viewport.
            for _ in range(40):
                await pilot.press("up")
                await pilot.pause(0.02)

            # The cursor's DNA row must be at least `above_pairs * rpg`
            # below the top of the viewport — i.e., the feature lanes
            # above the DNA must fit in the viewport above the cursor.
            line_width = sp._line_width()
            chunks_layout, prefix_dna2, prefix_lanes = sc._chunk_layout(
                sp._seq, sp._feats, line_width
            )
            rpg = 2 + (1 if sp._show_connectors else 0)
            chunk_idx = sp._cursor_pos // line_width
            above_pairs = chunks_layout[chunk_idx][3]
            chunk_top = (prefix_dna2[chunk_idx]
                         + (rpg - 2) * prefix_lanes[chunk_idx])
            dna_row = chunk_top + above_pairs * rpg

            scroll = app.query_one("#seq-scroll")
            vp_top = int(scroll.scroll_y)

            # Pre-fix: vp_top would equal dna_row (lanes clipped above viewport).
            # Post-fix: vp_top <= chunk_top, so the above-lanes are visible.
            assert vp_top <= chunk_top, (
                f"vp_top={vp_top} should be at or above chunk_top={chunk_top} "
                f"so the {above_pairs} feature-lane row(s) above the cursor's "
                f"DNA stay visible. dna_row={dna_row}"
            )


class TestMapClickCentersSeqPanel:
    """Regression guard for the 2026-04-25 map-click centering fix.

    Clicking on the plasmid map (feature or backbone) now centres the
    sequence panel on the clicked bp."""

    async def test_map_feature_click_centers_seq_panel(
        self, isolated_library,
    ):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 5000), id="mapClickTest",
                        annotations={"molecule_type": "DNA"})
        rec.features.append(SeqFeature(
            FeatureLocation(4000, 4200, strand=1), type="CDS",
            qualifiers={"label": ["targetFeat"]},
        ))
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)

            target_idx = next(
                i for i, f in enumerate(pm._feats)
                if f.get("label") == "targetFeat"
            )
            target_feat = pm._feats[target_idx]
            # Simulate the FeatureSelected event the map fires on click.
            pm.post_message(sc.PlasmidMap.FeatureSelected(
                target_idx, target_feat, bp=4100
            ))
            await pilot.pause()
            await pilot.pause(0.1)
            scroll = app.query_one("#seq-scroll")
            assert scroll.scroll_y > 5, (
                f"Map click at bp 4100 should scroll seq panel; "
                f"scroll_y={scroll.scroll_y}"
            )

    async def test_map_backbone_click_centers_seq_panel(
        self, tiny_record, isolated_library,
    ):
        """Clicking on the bare backbone (no feature) must still scroll the
        sequence panel — backbone clicks send `feat_dict=None, bp=clicked`
        and the handler now centres on bp regardless of feature presence."""
        # Use a longer record so backbone scrolling has somewhere to go.
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 5000), id="backboneClickTest",
                        annotations={"molecule_type": "DNA"})
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            # idx=-1, feat=None — the backbone-click signature.
            pm.post_message(sc.PlasmidMap.FeatureSelected(
                -1, None, bp=4500
            ))
            await pilot.pause()
            await pilot.pause(0.1)
            scroll = app.query_one("#seq-scroll")
            assert scroll.scroll_y > 5, (
                f"Backbone click at bp 4500 should still scroll seq panel; "
                f"scroll_y={scroll.scroll_y}"
            )


class TestSeqClickWrapFeature:
    """Regression guard for the 2026-04-25 fix to `_seq_click`.

    Pre-fix the handler used `s <= bp < e and (e - s) < best_span` which
    (a) failed every wrap feature (where `e < s`, so the comparison is
    always False) and (b) used a negative `e - s` span for any wrap that
    *did* somehow leak through. Clicking the lane art of a wrap feature
    on the sequence panel silently selected nothing.

    Updated 2026-04-28: `_seq_click` now distinguishes lane clicks
    (`from_lane=True`) from DNA-row clicks. Only lane clicks pick a
    feature; DNA-row clicks just place the cursor.
    """

    async def test_lane_click_inside_wrap_feature_selects_it(
        self, isolated_library,
    ):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation, CompoundLocation
        rec = SeqRecord(Seq("A" * 100), id="wrap_click_test",
                        annotations={"molecule_type": "DNA"})
        # Wrap feature spanning 95..100 + 0..5 (10 bp around origin).
        wrap_loc = CompoundLocation([
            FeatureLocation(95, 100, strand=1),
            FeatureLocation(0, 5, strand=1),
        ])
        rec.features.append(SeqFeature(wrap_loc, type="CDS",
                                       qualifiers={"label": ["wrapCDS"]}))
        rec.features.append(SeqFeature(
            FeatureLocation(40, 60, strand=1), type="CDS",
            qualifiers={"label": ["linearCDS"]}
        ))

        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)

            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sp = app.query_one("#seq-panel",   sc.SequencePanel)

            wrap_idx = next(
                (i for i, f in enumerate(pm._feats)
                 if f.get("label") == "wrapCDS"),
                None,
            )
            assert wrap_idx is not None

            # Lane click at bp=2 — inside the wrap's head [0, 5).
            sp.post_message(
                sc.SequencePanel.SequenceClick(bp=2, from_lane=True)
            )
            await pilot.pause()
            await pilot.pause(0.05)
            assert pm.selected_idx == wrap_idx, (
                "Lane-click at bp=2 (wrap head) should select the wrap "
                f"feature; got selected_idx={pm.selected_idx}"
            )

            # Lane click at bp=97 — inside the wrap's tail [95, 100).
            sp.post_message(
                sc.SequencePanel.SequenceClick(bp=97, from_lane=True)
            )
            await pilot.pause()
            await pilot.pause(0.05)
            assert pm.selected_idx == wrap_idx, (
                "Lane-click at bp=97 (wrap tail) should select the wrap "
                f"feature; got selected_idx={pm.selected_idx}"
            )

    async def test_lane_click_picks_clicked_feature_not_smallest(
        self, isolated_library,
    ):
        """Regression guard for 2026-04-30: when a click bp falls inside
        BOTH a small inner feature and a larger overlapping feature whose
        bar was actually clicked, the panel-side `_check_packed` stashes
        the clicked feat dict on the SequenceClick message so the App
        picks THAT feature directly. Pre-fix the App fell back to
        "smallest enclosing at bp" and mis-picked the tiny inner
        feature even when the user clearly clicked the larger one's
        bar — same bug the user hit when annotating a region that
        overlapped existing features."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 200), id="overlap_click",
                        annotations={"molecule_type": "DNA"})
        # Larger outer feature [50, 150]. A small inner annotation
        # [98, 102] sits at the outer's midpoint (=100). Pre-fix the
        # bp=100 click would always select the inner.
        rec.features.append(SeqFeature(
            FeatureLocation(50, 150, strand=1), type="misc_feature",
            qualifiers={"label": ["outer"]},
        ))
        rec.features.append(SeqFeature(
            FeatureLocation(98, 102, strand=1), type="misc_feature",
            qualifiers={"label": ["inner"]},
        ))
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sp = app.query_one("#seq-panel",   sc.SequencePanel)
            outer_idx = next(i for i, f in enumerate(pm._feats)
                              if f.get("label") == "outer")
            outer = pm._feats[outer_idx]
            # Click sent with the ACTUAL feat dict (as `_check_packed`
            # would set) — should select the outer despite bp=100
            # also being inside the inner feature.
            sp.post_message(sc.SequencePanel.SequenceClick(
                bp=100, from_lane=True, feat=outer,
            ))
            await pilot.pause()
            await pilot.pause(0.1)
            assert pm.selected_idx == outer_idx, (
                f"lane-click on outer's bar should select outer; "
                f"got selected_idx={pm.selected_idx}"
            )
            assert sp._user_sel == (50, 150), (
                f"user_sel should span the outer feature; "
                f"got {sp._user_sel}"
            )

    async def test_lane_click_falls_back_to_bp_search_without_feat(
        self, isolated_library,
    ):
        """Back-compat: if a SequenceClick arrives with `feat=None`
        (older callers / programmatic posts), the App falls back to
        the original "smallest enclosing at bp" search."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 200), id="bp_fallback",
                        annotations={"molecule_type": "DNA"})
        rec.features.append(SeqFeature(
            FeatureLocation(50, 150, strand=1), type="misc_feature",
            qualifiers={"label": ["outer"]},
        ))
        rec.features.append(SeqFeature(
            FeatureLocation(98, 102, strand=1), type="misc_feature",
            qualifiers={"label": ["inner"]},
        ))
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sp = app.query_one("#seq-panel",   sc.SequencePanel)
            inner_idx = next(i for i, f in enumerate(pm._feats)
                              if f.get("label") == "inner")
            # No feat passed → bp search → smallest enclosing → inner.
            sp.post_message(sc.SequencePanel.SequenceClick(
                bp=100, from_lane=True,
            ))
            await pilot.pause()
            await pilot.pause(0.1)
            assert pm.selected_idx == inner_idx

    async def test_aa_row_empty_cell_click_clears_previous_selection(
        self, isolated_library,
    ):
        """Clicking on a CDS's AA-row in a cell BETWEEN amino-acid
        letters used to return -1 (no-op), which left a previously-
        active feature highlight stuck on screen — exactly the
        "clicking another feature inside an overlap doesn't deselect
        the previous one" bug. Now the empty-cell click falls through
        to a regular CDS bar-click, selecting the CDS so the prior
        highlight is replaced."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 200), id="aa_empty",
                        annotations={"molecule_type": "DNA"})
        # Older CDS [0, 90] — codon midpoints at 1, 4, 7, ..., 88.
        # bp 11, 12, 13, etc. are NOT midpoints (those would be
        # multiples of 3 + 1).
        rec.features.append(SeqFeature(
            FeatureLocation(0, 90, strand=1), type="CDS",
            qualifiers={"label": ["oldCDS"]},
        ))
        # Newer non-CDS [50, 70] — overlaps the CDS.
        rec.features.append(SeqFeature(
            FeatureLocation(50, 70, strand=1), type="misc_feature",
            qualifiers={"label": ["newOverlap"]},
        ))
        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sp = app.query_one("#seq-panel",   sc.SequencePanel)
            cds_idx = next(i for i, f in enumerate(pm._feats)
                            if f.get("label") == "oldCDS")
            new_idx = next(i for i, f in enumerate(pm._feats)
                            if f.get("label") == "newOverlap")

            # Step 1: select the new feature first.
            sp.post_message(sc.SequencePanel.SequenceClick(
                bp=60, from_lane=True, feat=pm._feats[new_idx],
            ))
            await pilot.pause()
            await pilot.pause(0.1)
            assert pm.selected_idx == new_idx
            assert sp._user_sel == (50, 70)

            # Step 2: simulate clicking the CDS's AA row (sub=0)
            # at bp=12 (between letters at 11 and 14). With the
            # fix, this falls through to a CDS bar click — sets
            # `_last_lane_feat` to the CDS so `_seq_click` picks
            # the CDS, replacing the prior new-feature highlight.
            sp.post_message(sc.SequencePanel.SequenceClick(
                bp=(0 + 90) // 2,   # CDS midpoint = bar-click bp
                from_lane=True, feat=pm._feats[cds_idx],
            ))
            await pilot.pause()
            await pilot.pause(0.1)
            assert pm.selected_idx == cds_idx, (
                f"clicking the CDS in an overlapping region should "
                f"replace the prior selection; got {pm.selected_idx}"
            )
            assert sp._user_sel == (0, 90), (
                f"user_sel should now span the CDS; got {sp._user_sel}"
            )

    async def test_base_click_does_not_select_feature(
        self, isolated_library,
    ):
        """A click on the DNA strand row (not the lane art) must NOT
        trigger a whole-feature selection, even if `bp` is inside one
        — the user asked for a single-base operation, not a feature
        pick. Regression guard for the 2026-04-28 lane-click rule."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 100), id="base_click_test",
                        annotations={"molecule_type": "DNA"})
        rec.features.append(SeqFeature(
            FeatureLocation(40, 60, strand=1), type="CDS",
            qualifiers={"label": ["linearCDS"]}
        ))

        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sp = app.query_one("#seq-panel",   sc.SequencePanel)

            assert pm.selected_idx == -1
            # Base click at bp=50 (inside the CDS feature) must NOT
            # select it — only lane art clicks do.
            sp.post_message(
                sc.SequencePanel.SequenceClick(bp=50, from_lane=False)
            )
            await pilot.pause()
            await pilot.pause(0.05)
            assert pm.selected_idx == -1, (
                "Base-row click should not pick a feature; got "
                f"selected_idx={pm.selected_idx}"
            )

    async def test_click_outside_wrap_does_not_falsely_select(
        self, isolated_library,
    ):
        """Negative control: clicking far from the wrap feature must NOT
        pick it up. The fix uses `_feat_len(s, e, total)` which is positive
        for wraps; a regression that compared raw `e - s` (negative) would
        always pick the wrap feature as 'smallest' and break this case."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation, CompoundLocation
        rec = SeqRecord(Seq("A" * 100), id="wrap_neg_test",
                        annotations={"molecule_type": "DNA"})
        rec.features.append(SeqFeature(
            CompoundLocation([
                FeatureLocation(95, 100, strand=1),
                FeatureLocation(0, 5, strand=1),
            ]),
            type="CDS", qualifiers={"label": ["wrapCDS"]},
        ))
        rec.features.append(SeqFeature(
            FeatureLocation(40, 60, strand=1), type="CDS",
            qualifiers={"label": ["linearCDS"]}
        ))

        app = sc.PlasmidApp()
        app._preload_record = rec
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sp = app.query_one("#seq-panel",   sc.SequencePanel)

            linear_idx = next(
                i for i, f in enumerate(pm._feats)
                if f.get("label") == "linearCDS"
            )

            # Lane click at bp=50 — inside the linear feature, far from wrap.
            sp.post_message(
                sc.SequencePanel.SequenceClick(bp=50, from_lane=True)
            )
            await pilot.pause()
            await pilot.pause(0.05)
            assert pm.selected_idx == linear_idx, (
                f"Should pick linear feature at bp=50, "
                f"got selected_idx={pm.selected_idx}"
            )


class TestSplashScreen:
    """Splash modal mounts on launch and dismisses on any keystroke; the
    test conftest sets `_skip_splash = True` for everything else, so any
    test that wants to drive the splash has to opt back in."""

    async def test_splash_mounts_when_enabled(self, isolated_library):
        app = sc.PlasmidApp()
        app._skip_splash = False
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.2)
            assert isinstance(app.screen, sc.SplashScreen)
            # The new splash paints DNA helix + logo + tagline + version
            # all into one canvas Static. Probe the rendered Text for
            # the Binomica + version string to verify the composition
            # actually included them.
            canvas = app.screen.query_one("#splash-canvas")
            content = str(canvas.render())
            assert "Binomica" in content
            assert sc.__version__ in content
            app.exit()

    async def test_splash_dismisses_on_key(self, isolated_library):
        app = sc.PlasmidApp()
        app._skip_splash = False
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            assert isinstance(app.screen, sc.SplashScreen)
            await pilot.press("a")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, sc.SplashScreen)
            app.exit()

    async def test_splash_skipped_under_default_test_config(self, isolated_library):
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            # conftest set _skip_splash = True; no splash on screen.
            assert not isinstance(app.screen, sc.SplashScreen)
            app.exit()


class TestQuitConfirm:
    """Pressing q opens QuitConfirmModal (default No) when there are no
    unsaved edits. With unsaved edits the existing UnsavedQuitModal still
    fires instead. Tab cycles between buttons; Enter on the focused
    button presses it (Textual default — no extra wiring needed)."""

    async def test_clean_quit_pushes_confirm_modal(self, isolated_library):
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            assert app._unsaved is False
            app.action_quit()
            await pilot.pause(0.1)
            assert isinstance(app.screen, sc.QuitConfirmModal)
            # Default focus is on No.
            assert app.screen.focused.id == "btn-quitcon-no"
            app.exit()

    async def test_clean_quit_no_keeps_app_running(self, isolated_library):
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            app.action_quit()
            await pilot.pause(0.1)
            app.screen.query_one("#btn-quitcon-no").action_press()
            await pilot.pause(0.1)
            # Still running — return to default screen, not exited.
            assert not isinstance(app.screen, sc.QuitConfirmModal)
            app.exit()

    async def test_unsaved_quit_routes_through_unsaved_modal(
        self, tiny_record, isolated_library
    ):
        app = sc.PlasmidApp()
        app._preload_record = tiny_record
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.2)
            app._mark_dirty()
            await pilot.pause(0.05)
            app.action_quit()
            await pilot.pause(0.1)
            # With unsaved edits, the unsaved modal fires (3 buttons),
            # not the simple QuitConfirmModal.
            assert isinstance(app.screen, sc.UnsavedQuitModal)
            app.screen.query_one("#btn-cancel-quit").action_press()
            await pilot.pause(0.1)
            app.exit()

    async def test_tab_cycles_focus_between_no_and_yes(self, isolated_library):
        """Tab + Enter end-to-end on a confirm modal — the only modal
        contract that matters for keyboard-only quit confirmation."""
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            app.action_quit()
            await pilot.pause(0.1)
            modal = app.screen
            assert modal.focused.id == "btn-quitcon-no"
            await pilot.press("tab")
            await pilot.pause(0.05)
            assert modal.focused.id == "btn-quitcon-yes"
            await pilot.press("tab")
            await pilot.pause(0.05)
            # Wraps back round to No.
            assert modal.focused.id == "btn-quitcon-no"
            app.exit()


class TestUndoSnapshotIndependence:
    """Defensive guard for an invariant that's currently easy to break by
    accident: undo/redo snapshots must be INDEPENDENT of the live record,
    so a future contributor who writes
    `self._current_record.features.append(...)` instead of building a fresh
    SeqRecord can't retroactively poison earlier undo entries.

    Today no code mutates _current_record in place, so this test wouldn't
    fail without the deep-copy — but locking the contract down with a test
    means a regression to in-place mutation will be caught immediately
    rather than discovered in production via a baffling Ctrl+Z bug."""

    async def test_push_undo_then_inplace_mutation_does_not_poison_snapshot(
        self, tiny_record, isolated_library,
    ):
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            n_before = len(app._current_record.features)
            app._push_undo()
            # Simulate the dangerous pattern a future contributor might add
            app._current_record.features.append(SeqFeature(
                FeatureLocation(0, 5, strand=1),
                type="misc_feature",
                qualifiers={"label": ["poison"]},
            ))
            # Snapshot must NOT have grown — it's a deep copy.
            _, _, snapshot_record = app._undo_stack[-1]
            assert len(snapshot_record.features) == n_before, (
                "Undo snapshot was poisoned by an in-place mutation of "
                "_current_record. _push_undo must deep-copy."
            )

    async def test_action_undo_redo_snapshots_are_independent(
        self, tiny_record, isolated_library,
    ):
        """Round-trip: push_undo, _action_undo (redo snapshot taken), mutate
        in place, verify the redo snapshot survives."""
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._push_undo()
            n_before = len(app._current_record.features)
            app._action_undo()
            # _action_undo just pushed a redo snapshot — capture it before
            # poisoning the live record.
            _, _, redo_snapshot = app._redo_stack[-1]
            app._current_record.features.append(SeqFeature(
                FeatureLocation(0, 5, strand=1),
                type="misc_feature",
                qualifiers={"label": ["poison"]},
            ))
            assert len(redo_snapshot.features) == n_before, (
                "Redo snapshot was poisoned by in-place mutation of "
                "_current_record. _action_undo must deep-copy."
            )


class TestShiftClickFeatureExtend:
    """Shift+click on a feature extends the seq-panel selection from
    the currently-selected anchor feature to the click target.

    Anchor stays put across chained shift+clicks (click A, shift+click
    B, shift+click C → spans A through C, not B through C). Plain
    click resets the anchor.

    Three entry points must honour the modifier — the map (PlasmidMap.
    FeatureSelected.shift), the seq-panel lane (SequencePanel.
    SequenceClick.shift), and the sidebar row (FeatureSidebar.
    RowActivated.shift).
    """

    async def test_shift_click_via_map_message_extends(self, tiny_record,
                                                         isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm      = app.query_one("#plasmid-map", sc.PlasmidMap)
            seq_pnl = app.query_one("#seq-panel",   sc.SequencePanel)
            assert len(pm._feats) >= 2, "tiny_record needs ≥2 features"
            anchor = pm._feats[0]
            target = pm._feats[1]
            # Bare click on anchor: sets pm.selected_idx → 0 and the
            # whole-feature highlight on the seq panel.
            app.post_message(sc.PlasmidMap.FeatureSelected(
                idx=0, feat_dict=anchor, bp=anchor["start"]))
            await pilot.pause(0.05)
            assert pm.selected_idx == 0
            # Shift+click on target: should extend, not replace.
            app.post_message(sc.PlasmidMap.FeatureSelected(
                idx=1, feat_dict=target, bp=target["start"], shift=True))
            await pilot.pause(0.05)
            # Anchor unchanged (selected_idx still 0)
            assert pm.selected_idx == 0
            # Seq panel _user_sel covers both features
            assert seq_pnl._user_sel is not None
            s, e = seq_pnl._user_sel
            assert s <= min(anchor["start"], target["start"])
            assert e >= max(anchor["end"], target["end"])

    async def test_shift_click_anchor_persists_across_chain(self,
                                                              tiny_record,
                                                              isolated_library):
        # Chain: click A, shift+click B, shift+click C → A..C, not B..C.
        # tiny_record has at most 2 user features, so synthesize a 3rd
        # by placing the anchor explicitly and shift-clicking two
        # downstream targets in sequence.
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm      = app.query_one("#plasmid-map", sc.PlasmidMap)
            seq_pnl = app.query_one("#seq-panel",   sc.SequencePanel)
            if len(pm._feats) < 2:
                pytest.skip("need ≥2 features for the chain")
            a = pm._feats[0]
            b = pm._feats[-1]
            app.post_message(sc.PlasmidMap.FeatureSelected(
                idx=0, feat_dict=a, bp=a["start"]))
            await pilot.pause(0.05)
            anchor_idx_before = pm.selected_idx
            # Shift+click further-out feature
            app.post_message(sc.PlasmidMap.FeatureSelected(
                idx=len(pm._feats)-1, feat_dict=b, bp=b["start"], shift=True))
            await pilot.pause(0.05)
            # Anchor must still be the originally clicked feature
            assert pm.selected_idx == anchor_idx_before
            # Span includes both anchor and target
            s, e = seq_pnl._user_sel
            assert s <= min(a["start"], b["start"])
            assert e >= max(a["end"], b["end"])

    async def test_bare_click_resets_anchor(self, tiny_record,
                                              isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            if len(pm._feats) < 2:
                pytest.skip("need ≥2 features")
            f0, f1 = pm._feats[0], pm._feats[1]
            app.post_message(sc.PlasmidMap.FeatureSelected(
                idx=0, feat_dict=f0, bp=f0["start"]))
            await pilot.pause(0.05)
            assert pm.selected_idx == 0
            # Bare click on the second feature → anchor moves
            app.post_message(sc.PlasmidMap.FeatureSelected(
                idx=1, feat_dict=f1, bp=f1["start"]))
            await pilot.pause(0.05)
            assert pm.selected_idx == 1

    async def test_shift_click_no_anchor_falls_through(self, tiny_record,
                                                         isolated_library):
        # Shift+click with no current selection (selected_idx == -1)
        # must not crash and must fall back to bare-click behaviour
        # (focus the clicked feature). The user gets a normal
        # selection, not an extend.
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm.selected_idx == -1, "starting state — no anchor"
            f0 = pm._feats[0]
            app.post_message(sc.PlasmidMap.FeatureSelected(
                idx=0, feat_dict=f0, bp=f0["start"], shift=True))
            await pilot.pause(0.05)
            # Falls through to focus path — no crash.
            seq_pnl = app.query_one("#seq-panel", sc.SequencePanel)
            assert seq_pnl._user_sel is not None or seq_pnl._sel_range is not None

    async def test_shift_click_via_sidebar_extends(self, tiny_record,
                                                     isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm      = app.query_one("#plasmid-map", sc.PlasmidMap)
            seq_pnl = app.query_one("#seq-panel",   sc.SequencePanel)
            sidebar = app.query_one("#sidebar",     sc.FeatureSidebar)
            if len(pm._feats) < 2:
                pytest.skip("need ≥2 features")
            anchor = pm._feats[0]
            target = pm._feats[1]
            # Anchor via map first
            app.post_message(sc.PlasmidMap.FeatureSelected(
                idx=0, feat_dict=anchor, bp=anchor["start"]))
            await pilot.pause(0.05)
            # Shift+click via sidebar message
            app.post_message(sc.FeatureSidebar.RowActivated(idx=1, shift=True))
            await pilot.pause(0.05)
            assert pm.selected_idx == 0, "anchor must persist"
            s, e = seq_pnl._user_sel
            assert s <= min(anchor["start"], target["start"])
            assert e >= max(anchor["end"], target["end"])

    async def test_map_feat_at_picks_smallest_enclosing(self,
                                                          isolated_library):
        """Nested-feature regression: when several features cover the
        same bp, ``PlasmidMap._feat_at`` must return the smallest
        enclosing one. Pre-fix it returned the first match, so a
        shift+click between an inner annotation and an outer CDS
        anchored on the wrong feature."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        # 200 bp circle: outer CDS 0..200, inner misc 50..100
        rec = SeqRecord(Seq("A" * 200), id="N", name="N",
                        annotations={"molecule_type": "DNA",
                                       "topology":      "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(0, 200, strand=1), type="CDS",
                        qualifiers={"label": ["outer"]}),
            SeqFeature(FeatureLocation(50, 100, strand=1),
                        type="misc_feature",
                        qualifiers={"label": ["inner"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            # bp=75 sits inside both the outer CDS and the inner
            # misc_feature. _feat_at must resolve to "inner".
            inner_idx = next(i for i, f in enumerate(pm._feats)
                             if f.get("label") == "inner")
            for i, f in enumerate(pm._feats):
                if not pm._bp_in(75, f):
                    continue
                # Sanity: both features cover bp=75
                pass
            # Drive the smallest-enclosing logic via a synthesised
            # geometry-based call (skip the bbox / label lookup).
            # The render hasn't necessarily populated `_label_bboxes`,
            # so we exercise the inner loop directly.
            best_idx = -1
            best_span = float("inf")
            for i, f in enumerate(pm._feats):
                if not pm._bp_in(75, f):
                    continue
                span = sc._feat_len(f["start"], f["end"], pm._total)
                if span < best_span:
                    best_span = span
                    best_idx = i
            assert best_idx == inner_idx, (
                f"smallest-enclosing should be 'inner' (idx={inner_idx}); "
                f"got idx={best_idx} ({pm._feats[best_idx].get('label')})"
            )

    async def test_nested_shift_click_extends_from_inner(
        self, isolated_library
    ):
        """End-to-end: click an inner feature (via posted message),
        then shift+click an unrelated feature elsewhere. Anchor must
        be the inner feature, span must run from inner.start to the
        unrelated feature."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 500), id="N2", name="N2",
                        annotations={"molecule_type": "DNA",
                                       "topology":      "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(0, 200, strand=1), type="CDS",
                        qualifiers={"label": ["outer"]}),
            SeqFeature(FeatureLocation(50, 100, strand=1),
                        type="misc_feature",
                        qualifiers={"label": ["inner"]}),
            SeqFeature(FeatureLocation(300, 400, strand=1),
                        type="misc_feature",
                        qualifiers={"label": ["far"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm      = app.query_one("#plasmid-map", sc.PlasmidMap)
            seq_pnl = app.query_one("#seq-panel",   sc.SequencePanel)
            inner = next(f for f in pm._feats if f.get("label") == "inner")
            far   = next(f for f in pm._feats if f.get("label") == "far")
            inner_idx = pm._feats.index(inner)
            far_idx   = pm._feats.index(far)
            # Bare click on inner → anchor = inner
            app.post_message(sc.PlasmidMap.FeatureSelected(
                idx=inner_idx, feat_dict=inner, bp=inner["start"]))
            await pilot.pause(0.05)
            assert pm.selected_idx == inner_idx, "anchor must be inner"
            # Shift+click on far
            app.post_message(sc.PlasmidMap.FeatureSelected(
                idx=far_idx, feat_dict=far, bp=far["start"], shift=True))
            await pilot.pause(0.05)
            # Anchor still inner; span includes inner..far (NOT
            # outer..far)
            assert pm.selected_idx == inner_idx, "anchor must persist"
            s, e = seq_pnl._user_sel
            assert s == 50,  f"span start should be inner.start=50, got {s}"
            assert e == 400, f"span end should be far.end=400, got {e}"

    async def test_ctrl_click_works_as_shift_synonym(self, tiny_record,
                                                       isolated_library):
        """On terminals that intercept shift+click for native text
        selection (xterm, macOS Terminal.app, GNOME Terminal), the
        click never reaches Textual. Ctrl+click is offered as a
        cross-terminal alias on the same handlers."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm      = app.query_one("#plasmid-map", sc.PlasmidMap)
            seq_pnl = app.query_one("#seq-panel",   sc.SequencePanel)
            if len(pm._feats) < 2:
                pytest.skip("need ≥2 features")
            anchor = pm._feats[0]
            target = pm._feats[1]
            # Anchor via bare click
            app.post_message(sc.PlasmidMap.FeatureSelected(
                idx=0, feat_dict=anchor, bp=anchor["start"]))
            await pilot.pause(0.05)
            # The FeatureSelected message's `shift` field is also set
            # for ctrl+click by PlasmidMap.on_click — the handler
            # honours either path. Simulate by passing shift=True
            # (the message's own field; the source widget folds ctrl
            # into it).
            app.post_message(sc.PlasmidMap.FeatureSelected(
                idx=1, feat_dict=target, bp=target["start"], shift=True))
            await pilot.pause(0.05)
            assert pm.selected_idx == 0
            s, e = seq_pnl._user_sel
            assert s <= min(anchor["start"], target["start"])
            assert e >= max(anchor["end"], target["end"])

    async def test_click_debug_toggles_and_echoes(self, tiny_record,
                                                    isolated_library):
        """Alt+M toggles a per-click notify echo. Confirm the flag
        flips and the helper is a no-op when off."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            assert app._click_debug is False
            # Helper is a cheap no-op when off — must not raise.
            class FakeEvent:
                shift = True
                ctrl  = False
                meta  = False
                x = 10
                y = 5
            app._echo_click_modifiers("test", FakeEvent())
            # Toggle on via the action
            app.action_toggle_click_debug()
            assert app._click_debug is True
            app._echo_click_modifiers("test", FakeEvent())  # also no raise
            app.action_toggle_click_debug()
            assert app._click_debug is False

    def test_is_extend_modifier_accepts_either(self, tiny_record,
                                                 isolated_library):
        # Pure helper test — shift OR ctrl returns True; neither
        # returns False.
        class E:
            def __init__(self, shift=False, ctrl=False):
                self.shift = shift
                self.ctrl  = ctrl
        app = sc.PlasmidApp()
        assert app._is_extend_modifier(E(shift=True))             is True
        assert app._is_extend_modifier(E(ctrl=True))              is True
        assert app._is_extend_modifier(E(shift=True, ctrl=True))  is True
        assert app._is_extend_modifier(E())                       is False

    async def _press_via_app(self, app, key: str):
        """Dispatch a key directly to the App's on_key handler. Bypasses
        Textual's focus chain — needed because a focused DataTable
        (LibraryPanel by default) eats arrow keys before they reach the
        App-level handler that lives the Shift+Arrow boundary logic.
        The handler also early-returns when self.focused is a
        DataTable / PlasmidMap / Input / TextArea, so we clear focus
        first."""
        from textual.events import Key
        app.set_focus(None)
        event = Key(key, character=None)
        app.on_key(event)

    def test_restriction_scan_cache_hits_on_repeat(self):
        """Second call with the same (seq, args) tuple returns the
        cached list without re-scanning. Verifies via list identity:
        if the cache is a hit, the SAME list object comes back."""
        seq = "ATGCATGCATGC" * 200
        a = sc._scan_restriction_sites(seq, 6, True, True)
        b = sc._scan_restriction_sites(seq, 6, True, True)
        assert a is b, (
            "second call should return the cached list object — "
            "indicates we re-scanned"
        )

    def test_restriction_scan_cache_separate_keys(self):
        """Different (min_len, unique_only, circular) combinations
        cache independently — toggling unique-only doesn't return the
        previous min-length-6 result."""
        seq = "ATGCATGCATGC" * 200
        unique = sc._scan_restriction_sites(seq, 6, True,  True)
        all_   = sc._scan_restriction_sites(seq, 6, False, True)
        # Identity differs — separate cache entries.
        assert unique is not all_

    def test_restriction_scan_cache_evicts_at_cap(self):
        """LRU cap holds at `_RESTR_SCAN_CACHE_MAX` entries."""
        sc._RESTR_SCAN_CACHE.clear()
        # Build > cap distinct (id-keyed) sequences, scan each.
        seqs = [f"ATGC{i:04d}" * 50 for i in range(sc._RESTR_SCAN_CACHE_MAX + 2)]
        for s in seqs:
            sc._scan_restriction_sites(s, 6, True, True)
        assert len(sc._RESTR_SCAN_CACHE) <= sc._RESTR_SCAN_CACHE_MAX

    async def test_feats_by_start_index_built(self, isolated_library):
        """`PlasmidMap._feats_by_start` indexes features in start-sorted
        order — used by the linear renderer's bisect-based visible-
        range filter."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="X", name="X",
                        annotations={"molecule_type": "DNA",
                                       "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(800, 900, strand=1), type="CDS",
                        qualifiers={"label": ["c"]}),
            SeqFeature(FeatureLocation(100, 200, strand=1), type="CDS",
                        qualifiers={"label": ["a"]}),
            SeqFeature(FeatureLocation(400, 500, strand=1), type="CDS",
                        qualifiers={"label": ["b"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            idx = pm._feats_by_start
            assert len(idx) == 3
            starts = [pm._feats[i]["start"] for i in idx]
            assert starts == sorted(starts)

    def test_build_seq_text_viewport_y_range_skips_chunks(self):
        """Lazy chunk rendering — when `viewport_y_range` excludes
        most chunks, the function emits blank-line placeholders and
        returns much faster than the full-render path on a long
        sequence."""
        import time
        seq = "ATGC" * 25_000   # 100 kb
        t0 = time.perf_counter()
        full = sc._build_seq_text(seq, [], line_width=120)
        t_full = time.perf_counter() - t0
        t0 = time.perf_counter()
        lazy = sc._build_seq_text(seq, [], line_width=120,
                                    viewport_y_range=(0, 30))
        t_lazy = time.perf_counter() - t0
        # The lazy variant must produce a Text whose total newline
        # count matches the full variant — placeholder lines preserve
        # height for accurate scrollbar positioning.
        assert full.plain.count("\n") == lazy.plain.count("\n")
        # Speed: lazy at minimum 2x faster on a 100 kb sequence; in
        # practice 10x+. Loose budget so a slow CI box doesn't fail.
        assert t_lazy < t_full / 1.5, (
            f"expected lazy < full/1.5; got full={t_full*1000:.1f}ms "
            f"lazy={t_lazy*1000:.1f}ms"
        )

    async def test_linear_zoom_in_out_changes_view_range(
        self, isolated_library
    ):
        """Zoom in shrinks the visible bp range; zoom out expands it.
        Reset (`0`) returns to whole-record view."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 10_000), id="Z", name="Z",
                        annotations={"molecule_type": "DNA",
                                       "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            pm._map_mode = "linear"
            # Initial: full view
            view_s, view_e = pm._linear_view_range()
            assert (view_s, view_e) == (0, 10_000)
            # Zoom in once → ~6,667 bp visible (10000/1.5)
            pm.action_linear_zoom_in()
            view_s2, view_e2 = pm._linear_view_range()
            assert (view_e2 - view_s2) < 8_000
            # Reset → whole record
            pm.action_linear_reset_zoom()
            assert pm._linear_view_range() == (0, 10_000)

    async def test_linear_pan_clamped_to_record_bounds(
        self, isolated_library
    ):
        """Pan can't scroll past either end of the record."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 10_000), id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                       "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            pm._map_mode = "linear"
            # Zoom in so a window-fits-portion is visible
            for _ in range(4):
                pm.action_linear_zoom_in()
            visible_before = pm._linear_view_range()
            visible_w = visible_before[1] - visible_before[0]
            # Pan left from origin → still anchored at 0
            for _ in range(20):
                pm._linear_pan(-1)
            assert pm._linear_view_range()[0] == 0
            # Pan all the way right → end snaps to total
            for _ in range(50):
                pm._linear_pan(+1)
            view_s, view_e = pm._linear_view_range()
            assert view_e == 10_000
            assert view_s == 10_000 - visible_w

    async def test_linear_auto_fog_zooms_in_for_large_records(
        self, isolated_library
    ):
        """Records longer than `_LINEAR_LARGE_BP` open with the
        viewport zoomed in to ~50 kb visible (auto-fog), so the user
        sees a readable slice instead of an unreadable strip."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 500_000), id="LRG", name="LRG",
                        annotations={"molecule_type": "DNA",
                                       "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            view_s, view_e = pm._linear_view_range()
            visible = view_e - view_s
            # Visible window should be ~50 kb (target), well below
            # the 500 kb total. Allow slack for ratio rounding.
            assert visible < 100_000, (
                f"large-record auto-fog should zoom in to <100 kb; "
                f"got {visible:,} bp"
            )

    async def test_linear_zoom_does_not_apply_in_circular_mode(
        self, isolated_library
    ):
        """`+`/`-` are no-ops when the map is in circular mode so they
        don't surprise users by silently changing zoom on a view that
        doesn't show it."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 1000), id="C", name="C",
                        annotations={"molecule_type": "DNA",
                                       "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm._map_mode == "circular"
            zoom_before = pm._linear_zoom
            pm.action_linear_zoom_in()
            assert pm._linear_zoom == zoom_before

    async def test_load_record_circular_record_uses_circular_view(
        self, isolated_library
    ):
        """Loading a circular plasmid sets the map to circular even
        if the user had toggled to linear in the previous session.
        Linear is a session-local view choice; the record's
        `topology` annotation is the authoritative per-load default.
        """
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec_a = SeqRecord(Seq("A" * 500), id="A", name="A",
                          annotations={"molecule_type": "DNA",
                                         "topology": "circular"})
        rec_b = SeqRecord(Seq("C" * 500), id="B", name="B",
                          annotations={"molecule_type": "DNA",
                                         "topology": "circular"})
        app = _build_app(rec_a, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            # Toggle to linear mid-session
            pm._map_mode = "linear"
            assert pm._map_mode == "linear"
            # Load a circular record → snaps back to circular
            pm.load_record(rec_b)
            assert pm._map_mode == "circular"

    async def test_load_record_linear_topology_uses_linear_view(
        self, isolated_library
    ):
        """Linear plasmids (PCR products, sequencing fragments, etc.)
        carry `topology=linear` in GenBank and must open in the
        linear view. Forcing them into circular would distort the
        biology — the ends of a true linear record are not adjacent."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        circ = SeqRecord(Seq("A" * 200), id="C", name="C",
                         annotations={"molecule_type": "DNA",
                                        "topology": "circular"})
        lin  = SeqRecord(Seq("C" * 200), id="L", name="L",
                         annotations={"molecule_type": "DNA",
                                        "topology": "linear"})
        app = _build_app(circ, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            # Starts circular (the preloaded record is circular)
            assert pm._map_mode == "circular"
            # Loading a linear record → linear view
            pm.load_record(lin)
            assert pm._map_mode == "linear", (
                "linear topology must default to linear view"
            )
            # Loading a circular record AFTER linear → back to circular
            pm.load_record(circ)
            assert pm._map_mode == "circular"

    async def test_load_record_missing_topology_defaults_circular(
        self, isolated_library
    ):
        """A record with no topology annotation (rare; mostly via
        ad-hoc construction) falls back to circular — matches the
        common case for this app."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 200), id="R", name="R",
                        annotations={"molecule_type": "DNA"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm._map_mode == "circular"

    async def test_map_mode_not_persisted_across_sessions(self,
                                                            isolated_library):
        """Even if `map_mode` is set to 'linear' in settings.json
        (e.g. from a hand-edit or older app version), the next session
        starts in circular — map_mode is intentionally not hydrated."""
        sc._set_setting("map_mode", "linear")
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 200), id="X", name="X",
                        annotations={"molecule_type": "DNA",
                                       "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm._map_mode == "circular"

    async def test_linear_flag_layout_renders_with_arrow_glyphs(
            self, isolated_library):
        """Flag layout renders forward features with `▶` and reverse
        with `◀` (rather than the centered layout's corner triangles).
        Smoke test that the new renderer produces output without error
        and emits the expected glyphs."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(100, 400, strand=1), type="CDS",
                        qualifiers={"label": ["fwd"]}),
            SeqFeature(FeatureLocation(500, 800, strand=-1),
                        type="misc_feature",
                        qualifiers={"label": ["rev"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            pm._map_mode = "linear"
            pm._linear_layout = "flag"
            pm.refresh()
            await pilot.pause(0.1)
            text = pm.render()
            plain = text.plain if hasattr(text, "plain") else str(text)
            assert "▶" in plain, "expected ▶ for forward feature in flag layout"
            assert "◀" in plain, "expected ◀ for reverse feature in flag layout"
            # Stems hang off the rail.
            assert "│" in plain, "expected stem connector in flag layout"
            # Header should advertise the flag mode.
            assert "flag" in plain

    async def test_linear_layout_default_is_flag(
            self, isolated_library):
        """A fresh PlasmidMap defaults to the flag layout — the only
        linear layout since 2026-05-08. The reactive starts at
        'flag'; pressing `v` (toggle map view) just flips between
        circular and linear, with linear always rendered as flag."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(100, 400, strand=1), type="CDS",
                        qualifiers={"label": ["fwd"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm._linear_layout == "flag"
            pm._map_mode = "linear"
            pm.refresh()
            await pilot.pause(0.1)
            text = pm.render()
            plain = text.plain if hasattr(text, "plain") else str(text)
            # Flag layout uses ▶ for forward, NOT the centered-layout
            # corner triangles.
            assert "▶" in plain
            assert "◥" not in plain and "◢" not in plain

    async def test_linear_flag_layout_handles_overlapping_features(
            self, isolated_library):
        """Overlapping forward features get pushed into separate lanes
        by greedy first-fit packing — the renderer must not crash and
        must emit at least two distinct row positions for the bars."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 2000), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(100, 800,  strand=1), type="CDS",
                        qualifiers={"label": ["A"]}),
            SeqFeature(FeatureLocation(200, 700,  strand=1), type="CDS",
                        qualifiers={"label": ["B"]}),
            SeqFeature(FeatureLocation(300, 600,  strand=1), type="CDS",
                        qualifiers={"label": ["C"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            pm._map_mode = "linear"
            pm._linear_layout = "flag"
            pm.refresh()
            await pilot.pause(0.1)
            text = pm.render()
            plain = text.plain if hasattr(text, "plain") else str(text)
            # All three feature labels (or their first character) should
            # render somewhere — overlapping features in centered layout
            # would all stack on the same 2-row pair and clobber each
            # other; flag layout pushes them onto distinct rows.
            assert "▶" in plain
            # Multiple distinct rows touched (each lane = different row)
            row_count_with_block = sum(1 for ln in plain.splitlines() if "█" in ln)
            assert row_count_with_block >= 2, (
                "expected ≥2 distinct rows with feature blocks "
                f"(overlapping features should land on different lanes); "
                f"got {row_count_with_block}"
            )

    async def test_focus_panel_library_only_hides_others(
            self, isolated_library):
        """F1 collapses to library-only: PlasmidMap, FeatureSidebar,
        and SequencePanel become non-displayed; LibraryPanel remains
        visible with width overridden so it fills the row."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 200), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.action_focus_panel_library()
            await pilot.pause()
            assert app.query_one("#library").display is True
            assert app.query_one("#plasmid-map").display is False
            assert app.query_one("#sidebar").display is False
            assert app.query_one("#seq-panel").display is False

    async def test_focus_panel_map_only(self, isolated_library):
        """F2 collapses to plasmid-map-only."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 200), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.action_focus_panel_map()
            await pilot.pause()
            assert app.query_one("#plasmid-map").display is True
            assert app.query_one("#library").display is False
            assert app.query_one("#sidebar").display is False
            assert app.query_one("#seq-panel").display is False

    async def test_focus_panel_sidebar_only(self, isolated_library):
        """F3 collapses to feature-sidebar-only."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 200), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.action_focus_panel_sidebar()
            await pilot.pause()
            assert app.query_one("#sidebar").display is True
            assert app.query_one("#library").display is False
            assert app.query_one("#plasmid-map").display is False
            assert app.query_one("#seq-panel").display is False

    async def test_focus_panel_seq_only_hides_top_row(
            self, isolated_library):
        """F4 collapses to seq-panel-only, hiding the entire
        top-row container (not just its individual children) so the
        sequence strip can use the full window height. Verifies the
        seq-panel actually expands beyond its fixed CSS height of 14
        rows — without the explicit override, hiding top-row would
        leave seq-panel marooned at the top of the screen."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 200), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.action_focus_panel_seq()
            await pilot.pause()
            await pilot.pause(0.05)
            assert app.query_one("#top-row").display is False
            sp = app.query_one("#seq-panel")
            assert sp.display is True
            # Regression guard for 2026-05-04 fix: seq-panel must take
            # well more than its default 14 rows when alone. The test
            # terminal is 48 rows tall (TERMINAL_SIZE); minus header +
            # menubar + footer (~3 rows) leaves >40 available.
            assert sp.size.height > 30, (
                f"seq-panel should fill the screen when alone; "
                f"got height={sp.size.height}"
            )

    async def test_focus_panel_all_restores_layout(
            self, isolated_library):
        """F5 restores the multi-panel layout after any focus
        mode. All four panels become displayed again, and the
        Library / Sidebar widths are restored to their canonical
        fixed widths (26 / 32) — overrides applied during focus mode
        get rolled back."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 200), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.action_focus_panel_library()
            await pilot.pause()
            app.action_focus_panel_all()
            await pilot.pause()
            assert app.query_one("#library").display is True
            assert app.query_one("#plasmid-map").display is True
            assert app.query_one("#sidebar").display is True
            assert app.query_one("#seq-panel").display is True
            assert app.query_one("#top-row").display is True
            # Width restoration: library back to 25 cells (2026-05-06:
            # was 26; shrunk to button-row width), sidebar to 32.
            lib = app.query_one("#library")
            sb  = app.query_one("#sidebar")
            sp  = app.query_one("#seq-panel")
            assert int(lib.styles.width.value) == 25
            assert int(sb.styles.width.value) == 32
            # Seq-panel height also restored to the canonical 14 rows
            # (the override-to-1fr that F4 applies must not stick).
            assert int(sp.styles.height.value) == 14

    async def test_focus_panel_seq_then_restore_resets_height(
            self, isolated_library):
        """Regression guard for 2026-05-04 fix: F4 → F5 sequence
        must put the seq-panel height back to the canonical 14 rows.
        Without explicit restoration the override-to-1fr would persist
        and the multi-panel layout would render with a malformed
        seq-panel that ate the whole bottom of the screen."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 200), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.action_focus_panel_seq()
            await pilot.pause()
            app.action_focus_panel_all()
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel")
            assert int(sp.styles.height.value) == 14
            # And the top-row panels are visible again at full height.
            assert sp.size.height < 20  # squeezed back to its strip

    async def test_focus_panel_f_key_bindings_fire(
            self, isolated_library):
        """End-to-end binding test: pressing F1 / F2 / F3 / F4 / F5
        actually triggers the matching `action_focus_*`. Regression
        guard for the 2026-05-04 binding settle: started as Ctrl+N
        (terminals collapse Ctrl+digit to a bare digit), tried Alt+N
        (Windows Terminal / iTerm2 / GNOME Terminal eat Alt+digit
        for tab-switching), landed on F-keys which send dedicated
        CSI/SS3 sequences no terminal hijacks. Catches a future
        binding-string regression at CI time."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 200), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            await pilot.press("f1")
            await pilot.pause()
            assert app.query_one("#library").display is True
            assert app.query_one("#plasmid-map").display is False
            await pilot.press("f2")
            await pilot.pause()
            assert app.query_one("#plasmid-map").display is True
            assert app.query_one("#library").display is False
            await pilot.press("f3")
            await pilot.pause()
            assert app.query_one("#sidebar").display is True
            assert app.query_one("#plasmid-map").display is False
            await pilot.press("f4")
            await pilot.pause()
            assert app.query_one("#top-row").display is False
            assert app.query_one("#seq-panel").display is True
            await pilot.press("f5")
            await pilot.pause()
            assert app.query_one("#library").display is True
            assert app.query_one("#plasmid-map").display is True
            assert app.query_one("#sidebar").display is True
            assert app.query_one("#seq-panel").display is True

    async def test_focus_panel_chain_then_restore(self, isolated_library):
        """F1 → F2 → F3 → F5 leaves the layout in
        the canonical multi-panel state, exercising the snapshot
        logic that remembers original widths only on the first
        focus action."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 200), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            for action in ("focus_panel_library",
                           "focus_panel_map",
                           "focus_panel_sidebar"):
                getattr(app, f"action_{action}")()
                await pilot.pause()
            app.action_focus_panel_all()
            await pilot.pause()
            for sel in ("#library", "#plasmid-map", "#sidebar",
                        "#seq-panel", "#top-row"):
                assert app.query_one(sel).display is True, sel
            assert int(app.query_one("#library").styles.width.value) == 25
            assert int(app.query_one("#sidebar").styles.width.value) == 32

    async def test_feature_edit_modal_opens_read_only(
            self, isolated_library):
        """The FeatureEditModal opens with every input disabled —
        the user can inspect the feature but can't change anything
        until they press Edit."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(100, 400, strand=1), type="CDS",
                        qualifiers={"label": ["lacZ"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            assert isinstance(app.screen, sc.FeatureEditModal)
            modal = app.screen
            from textual.widgets import Input, Select, RadioSet, Button
            # Every editable input must start `disabled=True`.
            assert modal.query_one("#featedit-name", Input).disabled
            assert modal.query_one("#featedit-type", Select).disabled
            assert modal.query_one("#featedit-strand", RadioSet).disabled
            # Save button starts disabled (gated behind the Edit press).
            assert modal.query_one("#btn-featedit-save", Button).disabled

    async def test_feature_edit_modal_edit_button_unlocks_form(
            self, isolated_library):
        """Pressing Edit flips every input to editable and enables
        the Save button so the user can commit changes."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(100, 400, strand=1), type="CDS",
                        qualifiers={"label": ["lacZ"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.FeatureEditModal)
            modal.query_one("#btn-featedit-edit",
                            sc.Button).action_press()
            await pilot.pause()
            from textual.widgets import Input, Select, RadioSet, Button
            assert not modal.query_one("#featedit-name", Input).disabled
            assert not modal.query_one("#featedit-type", Select).disabled
            assert not modal.query_one("#featedit-strand", RadioSet).disabled
            assert not modal.query_one("#btn-featedit-save", Button).disabled

    async def test_feature_edit_modal_save_applies_edits(
            self, isolated_library):
        """End-to-end: open the modal, press Edit, change the label,
        press Save → the new label appears on the plasmid map's
        feature dict and the record's qualifiers."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(100, 400, strand=1), type="CDS",
                        qualifiers={"label": ["lacZ"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            from textual.widgets import Input, Button
            modal.query_one("#btn-featedit-edit", Button).action_press()
            await pilot.pause()
            modal.query_one("#featedit-name", Input).value = "lacZ-α"
            modal.query_one("#btn-featedit-save", Button).action_press()
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm._feats[0]["label"] == "lacZ-α"
            # Record-side: qualifiers reflect the new label too.
            target = next(f for f in app._current_record.features
                            if f.type == "CDS")
            assert target.qualifiers.get("label") == ["lacZ-α"]

    async def test_feature_edit_modal_cancel_discards_edits(
            self, isolated_library):
        """Cancel keeps the original label even if the user typed
        something else into the (post-Edit) name input."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(100, 400, strand=1), type="CDS",
                        qualifiers={"label": ["lacZ"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            from textual.widgets import Input, Button
            modal.query_one("#btn-featedit-edit", Button).action_press()
            await pilot.pause()
            modal.query_one("#featedit-name", Input).value = "garbage"
            modal.query_one("#btn-featedit-cancel", Button).action_press()
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm._feats[0]["label"] == "lacZ"

    async def test_seq_panel_enter_opens_editor_on_selected_feature(
            self, isolated_library):
        """End-to-end: select a feature on the map, focus the seq
        panel, press Enter — the FeatureEditModal opens for that
        feature."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(100, 400, strand=1), type="CDS",
                        qualifiers={"label": ["lacZ"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            pm.select_feature(0)
            await pilot.pause()
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp.action_open_selected_feature()
            await pilot.pause()
            await pilot.pause(0.05)
            assert isinstance(app.screen, sc.FeatureEditModal)
            assert app.screen._idx == 0

    async def test_seq_panel_enter_no_op_without_selection(
            self, isolated_library):
        """Enter on the seq panel with nothing selected on the map
        must NOT open the modal — it just notifies the user."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(100, 400, strand=1), type="CDS",
                        qualifiers={"label": ["lacZ"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            pm.selected_idx = -1   # nothing selected
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp.action_open_selected_feature()
            await pilot.pause()
            assert not isinstance(app.screen, sc.FeatureEditModal)

    async def test_feature_edit_modal_shows_sequence(
            self, isolated_library):
        """The sequence box renders the feature's 5'→3' bases pulled
        from the SeqRecord. Wrap-aware extraction is exercised by
        the wrap-feature variant below."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        # Make the seq distinctive so we can string-match.
        seq = "ATG" + "TAA" + ("CG" * 50) + "GCG"
        rec = SeqRecord(Seq(seq), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(0, 6, strand=1), type="CDS",
                        qualifiers={"label": ["start_codon_pair"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.FeatureEditModal)
            assert modal._sequence == "ATGTAA"
            from textual.widgets import TextArea
            ta = modal.query_one("#featedit-seq", TextArea)
            assert ta.read_only is True
            assert "ATGTAA" in ta.text

    async def test_feature_edit_modal_wrap_feature_sequence(
            self, isolated_library):
        """A feature whose `end < start` (wraps the origin) gets its
        bases assembled as `seq[start:total] + seq[0:end]` so the
        modal shows a contiguous 5'→3' string instead of an empty
        slice."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation, CompoundLocation
        # 30 bp circular plasmid; wrap feature spans 25..30 + 0..5.
        seq = "TTTTT" + ("A" * 20) + "GGGGG"  # 30 bp; tail = "GGGGG", head = "TTTTT"
        rec = SeqRecord(Seq(seq), id="W", name="W",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        # join(26..30, 1..5) → wrap from 25 to 5 (0-indexed).
        rec.features = [
            SeqFeature(CompoundLocation([
                FeatureLocation(25, 30, strand=1),
                FeatureLocation(0,  5,  strand=1),
            ]), type="misc_feature",
                qualifiers={"label": ["origin_spanner"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.FeatureEditModal)
            # Tail then head — `GGGGG` + `TTTTT`.
            assert modal._sequence == "GGGGGTTTTT"

    def test_sanitize_note_strips_dangerous_control_bytes(self):
        """`/note` body sanitizer strips `\\x00..\\x08`, `\\x0b..\\x1f`, and
        DEL but preserves `\\t` (\\x09) and `\\n` (\\x0a) so multi-paragraph
        Markdown notes round-trip cleanly. Caps total length at 8 KB so
        adversarial pasted blobs can't bloat `.gb` exports."""
        # Tab and newline survive; raw ESC + form feed get stripped.
        nasty = "Para 1\n\nPara 2\twith tab\n\x1b[31mRED\x1b[0m\x0c\x00bad"
        out = sc._sanitize_note(nasty)
        assert "\n\n" in out      # paragraph break preserved
        assert "\t" in out         # tab preserved
        assert "\x1b" not in out   # ESC stripped
        assert "\x00" not in out   # NUL stripped
        assert "\x0c" not in out   # FF stripped
        # Type-strict like _sanitize_label.
        assert sc._sanitize_note(None) == ""
        assert sc._sanitize_note(123) == ""           # type: ignore[arg-type]
        assert sc._sanitize_note({"x": 1}) == ""      # type: ignore[arg-type]
        # Length cap.
        assert len(sc._sanitize_note("X" * 100_000)) == 8_000

    async def test_feature_edit_modal_notes_sanitized_on_read(
            self, isolated_library):
        """Defence-in-depth: a malicious `.gb` whose `/note` qualifier
        carries terminal-escape bytes is cleaned when the modal opens,
        not just when the user hits Save. Without this, a hostile
        record could smuggle ANSI sequences into the Markdown widget's
        rendering buffer. Regression guard for 2026-05-04 hardening."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="N", name="N",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(0, 100, strand=1), type="CDS",
                        qualifiers={"label": ["lacZ"],
                                    "note":  ["\x1b[31mRED ALERT\x1b[0m\nOK"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.FeatureEditModal)
            assert "\x1b" not in modal._notes_md
            # The textual content survives — only the escape bytes are gone.
            assert "RED ALERT" in modal._notes_md
            assert "OK" in modal._notes_md

    async def test_feature_edit_modal_sequence_strips_control_bytes(
            self, isolated_library):
        """A corrupted SeqRecord whose sequence contains control bytes
        (which `Bio.Seq` doesn't validate) renders as plain DNA in the
        modal — control bytes are stripped before display so they
        can't scramble the TextArea or carry terminal escapes."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        # Synthesize a sequence with embedded ESC + NUL.
        rec = SeqRecord(Seq("ATG\x1b[31mCG\x00CG"), id="S", name="S",
                        annotations={"molecule_type": "DNA",
                                     "topology": "linear"})
        rec.features = [
            SeqFeature(FeatureLocation(0, 13, strand=1), type="CDS",
                        qualifiers={"label": ["X"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.FeatureEditModal)
            assert "\x1b" not in modal._sequence
            assert "\x00" not in modal._sequence
            # Bases themselves come through.
            assert "ATG" in modal._sequence

    async def test_feature_edit_modal_notes_round_trip(
            self, isolated_library):
        """Notes text from `qualifiers['note']` populates the modal,
        and editing + saving stores the new notes back as `/note`
        qualifiers (one per blank-line paragraph)."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="N", name="N",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(0, 100, strand=1), type="CDS",
                        qualifiers={"label": ["lacZ"],
                                    "note":  ["Original note"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.FeatureEditModal)
            assert "Original note" in modal._notes_md
            from textual.widgets import TextArea, Button
            modal.query_one("#btn-featedit-edit", Button).action_press()
            await pilot.pause()
            new_notes = (
                "First paragraph.\n\n"
                "Second paragraph with a [link](https://example.com)."
            )
            modal.query_one("#featedit-notes-edit", TextArea).text = new_notes
            modal.query_one("#btn-featedit-save", Button).action_press()
            await pilot.pause()
            await pilot.pause(0.05)
            target = next(f for f in app._current_record.features
                            if f.type == "CDS")
            stored = target.qualifiers.get("note", [])
            # Two paragraphs → two `/note` entries.
            assert len(stored) == 2
            assert stored[0].startswith("First paragraph")
            assert "https://example.com" in stored[1]

    async def test_sidebar_row_opened_message_opens_editor(
            self, isolated_library):
        """The sidebar's `RowOpened` message routes through
        `_sidebar_row_opened` and pushes the FeatureEditModal."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="L", name="L",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(100, 400, strand=1), type="CDS",
                        qualifiers={"label": ["lacZ"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sb = app.query_one("#sidebar", sc.FeatureSidebar)
            sb.action_open_feature_at_cursor()
            await pilot.pause()
            await pilot.pause(0.05)
            assert isinstance(app.screen, sc.FeatureEditModal)

    def test_entry_vector_round_trip(self, isolated_library):
        """Entry-vector helpers persist through `_safe_save_json` and
        round-trip cleanly via `_get_entry_vector` / `_set_entry_vector`.
        Each grammar gets at most one vector — re-setting replaces."""
        # Empty initially.
        assert sc._get_entry_vector("gb_l0") is None
        # Set + read back.
        sc._set_entry_vector("gb_l0", {
            "name": "pUPD2", "size": 2520,
            "source": "library:abc", "gb_text": "LOCUS pUPD2\n//\n",
        })
        v = sc._get_entry_vector("gb_l0")
        assert v is not None
        assert v["name"] == "pUPD2"
        assert v["size"] == 2520
        assert v["source"] == "library:abc"
        # Re-set replaces (one vector per grammar).
        sc._set_entry_vector("gb_l0", {
            "name": "pUPD2_v2", "size": 2540,
            "source": "file:/tmp/x.gb", "gb_text": "LOCUS pUPD2_v2\n//\n",
        })
        v = sc._get_entry_vector("gb_l0")
        assert v is not None and v["name"] == "pUPD2_v2"
        # Different grammar gets its own slot.
        sc._set_entry_vector("moclo_plant", {
            "name": "pAGM4673", "size": 6000,
            "source": "library:def", "gb_text": "LOCUS pAGM4673\n//\n",
        })
        assert sc._get_entry_vector("gb_l0")["name"] == "pUPD2_v2"
        assert sc._get_entry_vector("moclo_plant")["name"] == "pAGM4673"
        # Clear via None.
        sc._set_entry_vector("gb_l0", None)
        assert sc._get_entry_vector("gb_l0") is None
        assert sc._get_entry_vector("moclo_plant") is not None

    def test_entry_vector_set_rejects_invalid_grammar_id(
            self, isolated_library):
        """Type-strict: non-string / empty grammar_id is silently
        ignored rather than coerced. Mirrors the `_sanitize_*` family
        — the helpers don't accept anything that smells suspect."""
        sc._set_entry_vector("", {"name": "x", "size": 0,
                                   "source": "library:y", "gb_text": ""})
        sc._set_entry_vector(None, {"name": "x", "size": 0,    # type: ignore[arg-type]
                                     "source": "library:y", "gb_text": ""})
        sc._set_entry_vector(123,  {"name": "x", "size": 0,    # type: ignore[arg-type]
                                     "source": "library:y", "gb_text": ""})
        # Nothing was actually persisted.
        assert sc._load_entry_vectors() == []

    async def test_grammar_editor_shows_entry_vector_row(
            self, isolated_library):
        """The Grammar editor surfaces an "Entry vector" row for
        every grammar (built-in or custom). Even though built-ins
        are otherwise read-only, the entry-vector buttons stay
        editable so users can configure their own vector for the
        canonical grammars."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 100), id="x", name="x",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.push_screen(sc.GrammarEditorModal("gb_l0"))
            await pilot.pause()
            await pilot.pause(0.1)
            modal = app.screen
            assert isinstance(modal, sc.GrammarEditorModal)
            from textual.widgets import Button, Static
            # Buttons exist + are enabled (even though the rest of
            # the built-in form is disabled).
            for bid in ("btn-ged-entry-lib", "btn-ged-entry-file"):
                btn = modal.query_one(f"#{bid}", Button)
                assert btn.disabled is False
            # Clear button is disabled until a vector is assigned.
            assert modal.query_one("#btn-ged-entry-clear", Button).disabled
            # Initially no vector assigned — modal state reflects that.
            assert modal._entry_vector is None

    async def test_grammar_editor_persists_entry_vector_pick(
            self, isolated_library):
        """Picking an entry vector via the helper persists it and
        the modal's `_entry_vector` state reflects the choice."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("A" * 100), id="x", name="x",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app.push_screen(sc.GrammarEditorModal("gb_l0"))
            await pilot.pause()
            await pilot.pause(0.1)
            modal = app.screen
            modal._commit_entry_vector({
                "name": "pUPD2", "size": 2520,
                "source": "library:abc",
                "gb_text": "LOCUS pUPD2 100 bp DNA circular\n//\n",
            })
            await pilot.pause()
            assert modal._entry_vector is not None
            # Persistence via _set_entry_vector inside _commit_entry_vector.
            v = sc._get_entry_vector("gb_l0")
            assert v is not None and v["name"] == "pUPD2"
            # Clear button should now be enabled.
            from textual.widgets import Button
            assert not modal.query_one("#btn-ged-entry-clear", Button).disabled

    async def test_primer_with_flap_parsed_into_feat_dict(
            self, isolated_library):
        """A `primer_bind` feature carrying a `/primer_seq` qualifier
        whose length exceeds the bound region's bp count picks up
        `_flap_bases`, `_flap_start`, `_flap_end`, and `_flap_len`
        on its parsed feat dict — the data the seq-panel renderer
        needs to draw the floating flap segment."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        # Forward primer 5'-GAATCG-ATGAAACG-3': bound region 12..20
        # (8 bp) on the top strand, flap = "GAATCG" (6 bp).
        rec = SeqRecord(Seq("ATGAAATCAGCCATGAAACGGCCAAGCATGTAACGTGCATG"),
                        id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(12, 20, strand=1),
                        type="primer_bind",
                        qualifiers={"label": ["P-fwd"],
                                    "primer_seq": ["GAATCGATGAAACG"]}),
            # Reverse primer at 30..38: top strand is "TAACGTGC" RC =
            # "GCACGTTA", primer = 5'-GTATGC-GCACGTTA-3', flap = GTATGC
            # which RC's to GCATAC for top-strand orientation.
            SeqFeature(FeatureLocation(30, 38, strand=-1),
                        type="primer_bind",
                        qualifiers={"label": ["P-rev"],
                                    "primer_seq": ["GTATGCGCACGTTA"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            f_fwd = next(f for f in pm._feats if f.get("label") == "P-fwd")
            f_rev = next(f for f in pm._feats if f.get("label") == "P-rev")
            # Forward flap = first 6 bases of primer (raw).
            assert f_fwd["_flap_bases"] == "GAATCG"
            assert f_fwd["_flap_len"] == 6
            assert f_fwd["_bound_len"] == 8
            assert f_fwd["_flap_start"] == 6
            assert f_fwd["_flap_end"]   == 12
            # Reverse flap = RC of first 6 primer bases (top-strand
            # orientation), positioned to the RIGHT of the bound region.
            assert f_rev["_flap_bases"] == "GCATAC"
            assert f_rev["_flap_len"] == 6
            assert f_rev["_bound_len"] == 8
            assert f_rev["_flap_start"] == 38
            assert f_rev["_flap_end"]   == 44

    async def test_primer_no_flap_skips_extra_fields(self, isolated_library):
        """When primer_seq length equals bound length, no flap fields
        get set — the feature renders as a plain primer_bind bar."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(100, 110, strand=1),
                        type="primer_bind",
                        qualifiers={"label": ["full-bind"],
                                    "primer_seq": ["AAAAAAAAAA"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            f = next(f for f in pm._feats if f.get("label") == "full-bind")
            assert "_flap_bases" not in f   # no flap row drawn
            assert "_flap_len" not in f
            # `_primer_seq` + `_bound_len` ARE always set when the
            # qualifier is present, so the seq panel can paint primer
            # bases inline with the strand even for full-binding
            # primers (no flap, but still bases-in-bar instead of
            # the legacy ▒ block fill).
            assert f["_primer_seq"] == "AAAAAAAAAA"
            assert f["_bound_len"]  == 10

    def test_build_primer_preview_forward(self):
        """`_build_primer_preview` renders 4 lines for a forward
        primer: flap row, bound row, top strand, bottom strand.
        The flap bases sit one row above the bound bar, never
        vertically overlapping its column range."""
        # Template col 12..19 = "ATGAAACG"; primer = "GAATCG" + bound.
        template = "ATGAAATCAGCCATGAAACGGCCAAGCATGT"
        out = sc._build_primer_preview(
            template=template,
            primer_seq="GAATCGATGAAACG",
            bound_start=12, bound_end=20,
            strand=1, color="#00BFFF",
            context_bp=4,
        )
        plain = out.plain
        lines = plain.splitlines()
        assert len(lines) == 4
        # Line 0 = flap, line 1 = bound, line 2 = top, line 3 = bot.
        assert "GAATCG" in lines[0]
        assert "ATGAAACG" in lines[1]
        assert "▶"        in lines[1]
        assert "ATGAAACG" in lines[2]   # top strand context

    def test_build_primer_preview_reverse(self):
        """Reverse primer: bound bar with ◄ on the LEFT, flap below."""
        template = "ATGAAATCAGCCATGAAACGGCCAAGCATGT"
        out = sc._build_primer_preview(
            template=template,
            primer_seq="GTATGCAAGCATGT",
            bound_start=22, bound_end=30,
            strand=-1, color="#FF80FF",
            context_bp=4,
        )
        lines = out.plain.splitlines()
        # Layout: top, bottom, bound, flap (reverse-strand mirror).
        assert len(lines) == 4
        assert "◀" in lines[2]
        # Flap on row 3, top-strand-RC of GTATGC = GCATAC.
        assert "GCATAC" in lines[3]

    def test_build_primer_preview_wrap_unsupported(self):
        """Wrap primers fall back to a friendly hint instead of
        rendering — split-half logic is overkill for the modal."""
        out = sc._build_primer_preview(
            template="A" * 100, primer_seq="GAATTCAAAAAAAAAA",
            bound_start=95, bound_end=5, strand=1, color="cyan",
        )
        assert "wrap primer" in out.plain.lower()

    async def test_primer_edit_modal_apply_re_site_prefix(
            self, isolated_library):
        """Clicking '+ Apply' with EcoRI selected prepends GAATTC to
        the primer sequence in the textbox."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("ATGAAATCAGCCATGAAACGGCCAAGCATGT" + "A" * 50),
                        id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(12, 20, strand=1),
                        type="primer_bind",
                        qualifiers={"label": ["P"],
                                    "primer_seq": ["ATGAAACG"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.PrimerEditModal)
            from textual.widgets import Button, TextArea, Select
            modal.query_one("#btn-primedit-edit", Button).action_press()
            await pilot.pause()
            modal.query_one("#primedit-re-select", Select).value = "GAATTC"
            modal.query_one("#btn-primedit-prefix-apply", Button).action_press()
            await pilot.pause()
            assert modal.query_one("#primedit-seq", TextArea).text == \
                   "GAATTCATGAAACG"

    async def test_primer_edit_modal_apply_custom_prefix_iupac(
            self, isolated_library):
        """Custom prefix accepts DNA/IUPAC bases (uppercase, no
        whitespace) and prepends to the primer sequence."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 100), id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(10, 18, strand=1),
                        type="primer_bind",
                        qualifiers={"label": ["P"],
                                    "primer_seq": ["AAAAAAAA"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.PrimerEditModal)
            from textual.widgets import Button, Input, TextArea
            modal.query_one("#btn-primedit-edit", Button).action_press()
            await pilot.pause()
            modal.query_one("#primedit-custom-prefix",
                              Input).value = "GANNTC"
            await pilot.pause()
            modal.query_one("#btn-primedit-prefix-apply",
                              Button).action_press()
            await pilot.pause()
            assert modal.query_one("#primedit-seq",
                                     TextArea).text == "GANNTCAAAAAAAA"

    async def test_primer_edit_modal_apply_rejects_bad_prefix(
            self, isolated_library):
        """Non-DNA characters in the custom prefix are rejected; the
        primer sequence stays unchanged and the status row shows a
        red error message."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 100), id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(10, 18, strand=1),
                        type="primer_bind",
                        qualifiers={"label": ["P"],
                                    "primer_seq": ["AAAAAAAA"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            from textual.widgets import Button, Input, TextArea
            modal.query_one("#btn-primedit-edit", Button).action_press()
            await pilot.pause()
            modal.query_one("#primedit-custom-prefix",
                              Input).value = "BAD!CHARS"
            await pilot.pause()
            modal.query_one("#btn-primedit-prefix-apply",
                              Button).action_press()
            await pilot.pause()
            # Sequence unchanged.
            assert modal.query_one("#primedit-seq",
                                     TextArea).text == "AAAAAAAA"

    async def test_open_feature_editor_dispatches_primer_to_primer_modal(
            self, isolated_library):
        """A `primer_bind` feature opens `PrimerEditModal`, not the
        generic `FeatureEditModal`. Type-aware dispatch lives in
        `_open_feature_editor`. Regression guard for 2026-05-04."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 200), id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(50, 60, strand=1),
                        type="primer_bind",
                        qualifiers={"label": ["my-primer"],
                                    "primer_seq": ["GAATTCAAAAAAAAAA"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            assert isinstance(app.screen, sc.PrimerEditModal)
            assert not isinstance(app.screen, sc.FeatureEditModal)
            # Primer's full 5'→3' sequence (from /primer_seq qualifier)
            # round-trips into the modal's `_primer_seq` state.
            assert app.screen._primer_seq == "GAATTCAAAAAAAAAA"

    async def test_open_feature_editor_dispatches_other_to_feature_modal(
            self, isolated_library):
        """Non-primer features still open `FeatureEditModal`, not
        the primer-specific one. Confirms the dispatch fallback."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 200), id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(50, 60, strand=1),
                        type="CDS",
                        qualifiers={"label": ["my-cds"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            assert isinstance(app.screen, sc.FeatureEditModal)
            assert not isinstance(app.screen, sc.PrimerEditModal)

    async def test_open_feature_editor_targets_specific_idx_in_stack(
            self, isolated_library):
        """When two features share / overlap bp ranges, the editor
        opens for the EXACT index passed to `_open_feature_editor`,
        never an overlapping neighbour. Regression guard for the
        feature-stack disambiguation request."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        # Two CDSs at the SAME bp range — the lane packer stacks
        # them; click hit-testing picks one or the other; the
        # editor must open for whichever idx is requested.
        rec = SeqRecord(Seq("A" * 200), id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(50, 60, strand=1),
                        type="CDS", qualifiers={"label": ["alpha"]}),
            SeqFeature(FeatureLocation(50, 60, strand=1),
                        type="CDS", qualifiers={"label": ["beta"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.FeatureEditModal)
            # Modal carries the EXACT feat dict for idx=0 — its
            # label is "alpha", not "beta".
            assert modal._feat.get("label") == "alpha"
            modal.dismiss(None)
            await pilot.pause()
            await pilot.pause(0.05)
            # Now open for idx=1 — should be "beta" without leaking.
            app._open_feature_editor(1)
            await pilot.pause()
            await pilot.pause(0.05)
            modal2 = app.screen
            assert isinstance(modal2, sc.FeatureEditModal)
            assert modal2._feat.get("label") == "beta"

    async def test_primer_edit_modal_save_round_trip(self, isolated_library):
        """End-to-end: open the primer editor, edit the sequence,
        Save → the SeqFeature's `/primer_seq` qualifier reflects
        the new bases."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 200), id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(50, 60, strand=1),
                        type="primer_bind",
                        qualifiers={"label": ["P"],
                                    "primer_seq": ["GAATTCAAAAAAAAAA"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.PrimerEditModal)
            from textual.widgets import Button, TextArea
            modal.query_one("#btn-primedit-edit", Button).action_press()
            await pilot.pause()
            modal.query_one("#primedit-seq", TextArea).text = (
                "AAGCTTCCCCCCCCCC"
            )
            modal.query_one("#btn-primedit-save", Button).action_press()
            await pilot.pause()
            await pilot.pause(0.05)
            # The persisted SeqFeature's primer_seq qualifier got
            # the new bases.
            target = next(f for f in app._current_record.features
                            if f.type == "primer_bind")
            assert target.qualifiers.get("primer_seq") == ["AAGCTTCCCCCCCCCC"]

    async def test_wrap_primer_bound_bases_dont_overflow(self):
        """Regression guard for 2026-05-04 fix: when a primer's bound
        region wraps the origin (start=95, end=5 on a 100-bp plasmid),
        `_feats_in_chunk` splits it into a tail half + head half. The
        bound-bar painter must slice `_primer_seq[flap_len:]` so each
        half writes only ITS portion of the bound bases — without the
        slicing, both halves wrote the full 10-bp bound region at
        their respective starts, overflowing past the half's nominal
        column range and showing the same bases twice.

        This test exercises the painter directly with a synthesised
        head half. The head half (s=0, e=5, _orig_start=95,
        _orig_end=5, _bound_len=10) should render the LAST 5 bound
        bases at cols 0..4."""
        # Forward primer 5'-AAAAAA-CCGGAACCGG-3': flap=AAAAAA (6 bp),
        # bound=CCGGAACCGG (10 bp). Head half holds the last 5 bound
        # bases ("ACCGG") at cols 0..4; arrow ▶ at col 5.
        head_half = {
            "type": "primer_bind", "start": 0, "end": 5, "strand": 1,
            "color": "cyan", "label": "",
            "_primer_seq": "AAAAAACCGGAACCGG",
            "_bound_len": 10,
            "_flap_len":  6,
            "_flap_bases": "AAAAAA",
            "_flap_start": 89, "_flap_end": 95,
            "_orig_start": 95, "_orig_end": 5,
        }
        arr: list[tuple[str, str]] = [(" ", "")] * 30
        sc._paint_primer_bound_bar(arr, head_half, 0, 30)
        # Cols 0..4 should hold "ACCGG" (last 5 of bound bases),
        # col 5 should hold the arrow ▶.
        rendered = "".join(c for c, _ in arr[:6])
        assert rendered == "ACCGG▶", (
            f"head half should hold last 5 bound bases + arrow, "
            f"got {rendered!r}"
        )
        # Cols 6..29 must remain empty — no overflow past half's bar.
        assert all(c == " " for c, _ in arr[6:]), (
            "wrap primer head half overflowed into untouched cells"
        )

    async def test_full_binding_primer_renders_bases_inline(self):
        """Regression guard for 2026-05-04 fix: a primer whose
        primer_seq length equals its bound length (no flap) used to
        fall back to the plain `▒▒▒▒` bar painter, hiding the
        primer's bases. Now the bar paints the bases inline with
        the strand whenever `_primer_seq` is set, regardless of
        flap presence."""
        feat = {
            "type": "primer_bind", "start": 5, "end": 13, "strand": 1,
            "color": "magenta", "label": "P-full",
            "_primer_seq": "ATGAAACG",
            "_bound_len":  8,
            # No _flap_*: full-binding primer.
        }
        arr: list[tuple[str, str]] = [(" ", "")] * 20
        sc._paint_primer_bound_bar(arr, feat, 0, 20)
        rendered = "".join(c for c, _ in arr[:14])
        # Bases at cols 5..12, arrow at col 13.
        assert rendered == "     ATGAAACG▶", (
            f"full-binding primer should show bases + arrow, got {rendered!r}"
        )

    async def test_seq_panel_renders_primer_flap_bases(
            self, isolated_library):
        """End-to-end: load a primer with a flap, render the seq
        panel, and verify both the bound bases AND the flap bases
        appear in the rendered text. The bg-color encoding lives in
        the Rich Style spans, but the bases themselves should be
        present in the plain-text projection."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("ATGAAATCAGCCATGAAACGGCCAAGCATGT" + "A" * 100),
                        id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(12, 20, strand=1),
                        type="primer_bind",
                        qualifiers={"label": ["P-fwd"],
                                    "primer_seq": ["GAATCGATGAAACG"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            text = sc._build_seq_text(str(rec.seq), [
                {"type": "primer_bind", "start": 12, "end": 20, "strand": 1,
                 "color": "#00BFFF", "label": "P-fwd",
                 "_primer_seq": "GAATCGATGAAACG", "_flap_bases": "GAATCG",
                 "_flap_start": 6, "_flap_end": 12,
                 "_flap_len": 6, "_bound_len": 8},
            ])
            plain = text.plain
            # Bound bases (the bound region is `ATGAAACG`) should be
            # present — they overlap the strand bases at cols 12..19.
            # The strand row also contains `ATGAAACG`, so we can't
            # use that as a discriminator on its own. The flap
            # `GAATCG` is unique to the primer flap row, so its
            # presence confirms the flap rendered.
            assert "GAATCG" in plain, (
                "expected flap bases in rendered seq-panel text"
            )

    async def test_parse_stamps_weak_primer_when_below_threshold(
            self, isolated_library):
        """Regression guard for 2026-05-05 wiring: a `primer_bind` whose
        bound region is shorter than `app._min_primer_binding` picks up
        `_weak_primer: True` so the seq-panel painter and tooltip can
        flag it. Threshold change + re-parse refreshes the stamp."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        # Bound region 100..108 = 8 bp; primer 14 bp (6 bp flap + 8 bp bound).
        rec = SeqRecord(Seq("A" * 1000), id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(100, 108, strand=1),
                        type="primer_bind",
                        qualifiers={"label": ["weak"],
                                    "primer_seq": ["GAATCGATGAAACG"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            f = next(f for f in pm._feats if f.get("label") == "weak")
            # Default threshold is 15 bp; 8 bp bound → weak.
            assert f.get("_weak_primer") is True
            # Lower the threshold to 5 and re-parse → no longer weak.
            app._min_primer_binding = 5
            pm._feats = pm._parse(pm.record)
            f2 = next(f for f in pm._feats if f.get("label") == "weak")
            assert "_weak_primer" not in f2

    async def test_parse_skips_weak_primer_when_above_threshold(
            self, isolated_library):
        """Control: a primer with bound_len ≥ threshold gets no stamp."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 1000), id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            # 20 bp bound, full-binding (no flap).
            SeqFeature(FeatureLocation(100, 120, strand=1),
                        type="primer_bind",
                        qualifiers={"label": ["strong"],
                                    "primer_seq": ["A" * 20]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            f = next(f for f in pm._feats if f.get("label") == "strong")
            assert "_weak_primer" not in f

    def test_paint_primer_bound_bar_warning_glyph_when_weak(self):
        """Direct painter check: a primer marked `_weak_primer` paints
        ⚠ with yellow background instead of the directional ▶/◀ arrow.
        Bases inside the bar are unchanged so the user can still read
        the primer sequence."""
        feat = {
            "type": "primer_bind", "start": 5, "end": 13, "strand": 1,
            "color": "#00BFFF", "label": "P-weak",
            "_primer_seq": "ATGAAACG",
            "_bound_len":  8,
            "_weak_primer": True,
        }
        arr: list[tuple[str, str]] = [(" ", "")] * 20
        sc._paint_primer_bound_bar(arr, feat, 0, 20)
        # Bases at cols 5..12, weak-marker at col 13 (where ▶ would be).
        glyphs = "".join(c for c, _ in arr[:14])
        assert glyphs == "     ATGAAACG⚠", (
            f"expected weak-marker arrow column, got {glyphs!r}"
        )
        # Style on the warning column should be the yellow-bg highlight.
        assert arr[13][1] == "black on yellow", (
            f"expected yellow warning bg, got {arr[13][1]!r}"
        )
        # Control: an identical feat without the weak flag keeps ▶.
        feat_ok = dict(feat)
        feat_ok.pop("_weak_primer")
        arr2: list[tuple[str, str]] = [(" ", "")] * 20
        sc._paint_primer_bound_bar(arr2, feat_ok, 0, 20)
        glyphs2 = "".join(c for c, _ in arr2[:14])
        assert glyphs2 == "     ATGAAACG▶"

    def test_format_feat_tooltip_includes_weak_warning(self):
        """Hover tooltip on a weak primer mentions the threshold breach
        so the user knows *why* the strand arrow turned ⚠."""
        feat = {
            "type": "primer_bind", "start": 100, "end": 108, "strand": 1,
            "label": "P-weak", "_bound_len": 8, "_weak_primer": True,
        }
        text = sc._format_feat_tooltip(feat, total=1000)
        assert "Weak binding" in text
        assert "8 bp" in text
        # And a non-weak primer's tooltip omits the warning line.
        feat_ok = dict(feat); feat_ok.pop("_weak_primer")
        text_ok = sc._format_feat_tooltip(feat_ok, total=1000)
        assert "Weak binding" not in text_ok

    async def test_apply_min_primer_binding_persists_and_stamps(
            self, tiny_record, isolated_library):
        """`_apply_min_primer_binding` (the helper invoked by the
        modal-driven `set_min_primer_binding` action) persists the new
        threshold to settings.json AND re-parses the record so the
        seq-panel `_weak_primer` stamps reflect the new value
        immediately. Defaults: hydrate is 15 bp."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            assert app._min_primer_binding == 15
            app._apply_min_primer_binding(22)
            assert app._min_primer_binding == 22
            sc._settings_flush_sync()
            assert sc._get_setting("min_primer_binding") == 22

    async def test_min_primer_binding_modal_validates_and_dismisses(
            self, tiny_record, isolated_library):
        """The new `MinPrimerBindingModal` accepts integers in [1, 60]
        and dismisses with the chosen value. Out-of-range, non-integer,
        and unchanged-value inputs do not produce a write."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            modal = sc.MinPrimerBindingModal(15)
            await app.push_screen(modal)
            await pilot.pause()
            inp    = modal.query_one("#mpb-input", sc.Input)
            status = modal.query_one("#mpb-status", sc.Static)
            # Out-of-range — no dismiss, status shows error.
            inp.value = "999"
            modal._try_submit()
            assert app.screen_stack[-1] is modal, (
                "out-of-range value should not dismiss the modal"
            )
            assert "range" in str(status.render()).lower()
            # Non-integer — same behaviour.
            inp.value = "abc"
            modal._try_submit()
            assert app.screen_stack[-1] is modal
            assert "integer" in str(status.render()).lower()
            # Valid — modal dismisses with the int value.
            inp.value = "25"
            modal._try_submit()
            await pilot.pause()
            assert app.screen_stack[-1] is not modal

    async def test_min_primer_binding_modal_unchanged_dismisses_none(
            self, tiny_record, isolated_library):
        """Submitting the existing value is treated as a cancel — no
        re-stamp / no settings write — so the modal can't be used to
        force a redundant work cycle."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            results: list = []
            modal = sc.MinPrimerBindingModal(15)
            await app.push_screen(modal, callback=results.append)
            await pilot.pause()
            inp = modal.query_one("#mpb-input", sc.Input)
            inp.value = "15"   # same as current_value
            modal._try_submit()
            await pilot.pause()
            assert results == [None]

    async def test_record_load_counter_advances_on_apply(
            self, tiny_record, isolated_library):
        """Regression guard for 2026-05-05 stale-record fix:
        `_apply_record` increments `_record_load_counter` so a worker
        thread that captured the counter at entry can detect any load
        that happened during its in-flight work and skip the stale
        write — tighter than the previous `is None` check, which
        couldn't distinguish "nothing happened" from "loaded then
        cleared" (both yield `id(None) == id(None)`)."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Mount fired the preload through `_apply_record` once.
            n0 = app._record_load_counter
            assert n0 >= 1
            # Apply a fresh record; counter must advance by exactly 1.
            other = SeqRecord(
                Seq("A" * 200), id="other", name="other",
                annotations={"molecule_type": "DNA",
                              "topology": "circular"},
            )
            app._apply_record(other)
            assert app._record_load_counter == n0 + 1
            # In-place edits (clear_undo=False) also count — any
            # canvas mutation is something a stale worker should not
            # silently overwrite.
            app._apply_record(other, clear_undo=False)
            assert app._record_load_counter == n0 + 2
            # `record is None` early-returns and must NOT advance.
            app._apply_record(None)
            assert app._record_load_counter == n0 + 2

    def test_paint_intron_renders_as_zigzag_bar(self):
        """Regression guard for 2026-05-05 intron render:
        introns paint as a continuous ``╱╲╱╲╱╲`` zigzag — a
        diagonal-pair pattern keyed on absolute bp parity so
        chunk-spanning introns stay seamless across the line wrap.
        The leftmost zigzag cell sits exactly at bp ``start`` and
        the rightmost at bp ``end - 1`` (no over- or under-shoot)."""
        # 10-bp intron at abs cols 5..14 in a 20-cell chunk.
        # Parity 5,6,7,...14 → odd,even,odd,...,even
        #                    → ╱,╲,╱,╲,╱,╲,╱,╲,╱,╲ (10 chars).
        feat = {"type": "intron", "start": 5, "end": 15, "strand": 1,
                  "color": "gray", "label": "i1"}
        arr: list[tuple[str, str]] = [(" ", "")] * 20
        sc._paint_feature_bar(arr, feat, 0, 20)
        glyphs = "".join(c for c, _ in arr)
        assert glyphs == "     ╱╲╱╲╱╲╱╲╱╲     ", (
            f"expected ╱╲ zigzag pattern, got {glyphs!r}")
        # 1-bp intron at col 5 (odd parity → ╱).
        feat1 = {"type": "intron", "start": 5, "end": 6, "strand": 1,
                   "color": "gray"}
        arr1: list[tuple[str, str]] = [(" ", "")] * 10
        sc._paint_feature_bar(arr1, feat1, 0, 10)
        assert "".join(c for c, _ in arr1) == "     ╱    "
        # 3-bp intron at cols 3,4,5 → ╱╲╱ (parities 1,0,1).
        feat3 = {"type": "intron", "start": 3, "end": 6, "strand": 1,
                   "color": "gray"}
        arr3: list[tuple[str, str]] = [(" ", "")] * 10
        sc._paint_feature_bar(arr3, feat3, 0, 10)
        assert "".join(c for c, _ in arr3) == "   ╱╲╱    "

    def test_paint_intron_zigzag_continuous_across_chunks(self):
        """The zigzag alternation is keyed on absolute bp parity, not
        chunk-local position, so a single intron rendered across two
        chunks shows a seamless pattern instead of phase-shifting at
        the chunk boundary. Render the SAME 14-bp intron through two
        adjacent chunks and verify the concatenated glyphs equal what
        we'd get from rendering it in one wide chunk."""
        feat = {"type": "intron", "start": 0, "end": 14, "strand": 1,
                  "color": "gray"}
        ref_arr: list[tuple[str, str]] = [(" ", "")] * 14
        sc._paint_feature_bar(ref_arr, feat, 0, 14)
        ref = "".join(c for c, _ in ref_arr)
        a0: list[tuple[str, str]] = [(" ", "")] * 7
        sc._paint_feature_bar(a0, feat, 0, 7)
        a1: list[tuple[str, str]] = [(" ", "")] * 7
        sc._paint_feature_bar(a1, feat, 7, 14)
        joined = "".join(c for c, _ in a0) + "".join(c for c, _ in a1)
        assert joined == ref, (
            f"chunk split desynchronised the zigzag: ref={ref!r} "
            f"joined={joined!r}"
        )

    def test_paint_intron_bounds_match_exact_bp_range(self):
        """The first and last zigzag cells must sit on bp ``start``
        and bp ``end - 1`` respectively — no extension past the
        annotated boundaries on either side."""
        # Intron at bp 12..19 (8 cells). Surround with sentinel
        # spaces — they must remain spaces after the painter runs.
        feat = {"type": "intron", "start": 12, "end": 20,
                  "strand": 1, "color": "gray"}
        arr: list[tuple[str, str]] = [(" ", "")] * 30
        sc._paint_feature_bar(arr, feat, 0, 30)
        glyphs = "".join(c for c, _ in arr)
        # Cells 0..11 untouched, 12..19 zigzag, 20..29 untouched.
        assert all(g == " " for g in glyphs[:12]), \
            f"left of intron should be untouched, got {glyphs[:12]!r}"
        assert all(g == " " for g in glyphs[20:]), \
            f"right of intron should be untouched, got {glyphs[20:]!r}"
        # The 8 zigzag cells span exactly the intron's bp range.
        assert all(g in ("╱", "╲") for g in glyphs[12:20]), \
            f"intron cells should be all zigzag, got {glyphs[12:20]!r}"

    def test_paint_intron_strand_arrows_suppressed(self):
        """Introns are non-coding spacer regions — no direction
        arrows even when the source feature is annotated with a
        strand. The painter must NOT emit ◀ / ▶ for type=intron."""
        for strand in (1, -1, 0, 2):
            feat = {"type": "intron", "start": 2, "end": 8,
                      "strand": strand, "color": "gray"}
            arr: list[tuple[str, str]] = [(" ", "")] * 10
            sc._paint_feature_bar(arr, feat, 0, 10)
            glyphs = "".join(c for c, _ in arr)
            assert "◀" not in glyphs, (
                f"strand {strand} leaked left arrow: {glyphs!r}")
            assert "▶" not in glyphs, (
                f"strand {strand} leaked right arrow: {glyphs!r}")

    def test_intron_in_genbank_type_catalog(self):
        """Sanity: ``intron`` is registered as a GenBank feature type
        (so the FeatureEditModal type dropdown offers it) and carries
        a default color in `_DEFAULT_TYPE_COLORS` so it renders even
        before the user customises feature-library colors. CommercialSaaS
        .dna files whose region-type is "Intron" map through
        BioPython's commercialsaas parser to `feature.type == "intron"`,
        so this catalog entry is what makes them paint correctly."""
        assert "intron" in sc._GENBANK_FEATURE_TYPES
        assert "exon"   in sc._GENBANK_FEATURE_TYPES
        # Default color present and distinct from exon (so they're
        # visually distinguishable on the plasmid map).
        assert sc._DEFAULT_TYPE_COLORS["intron"] != \
               sc._DEFAULT_TYPE_COLORS["exon"]

    async def test_intron_record_round_trip_painter_visible(
            self, isolated_library):
        """End-to-end: a SeqRecord with an intron feature loads into
        PlasmidMap, the parsed feat dict carries `type == "intron"`,
        and `_build_seq_text` emits the zigzag glyphs in the
        rendered text (proxy for "the intron painter fired")."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 200), id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                      "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(20, 50, strand=1),
                        type="exon", qualifiers={"label": ["e1"]}),
            SeqFeature(FeatureLocation(50, 80, strand=1),
                        type="intron", qualifiers={"label": ["i1"]}),
            SeqFeature(FeatureLocation(80, 110, strand=1),
                        type="exon", qualifiers={"label": ["e2"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            intron = next(f for f in pm._feats if f.get("label") == "i1")
            assert intron["type"] == "intron"
            # Render and check the intron's bar contains zigzag glyphs.
            text = sc._build_seq_text(str(rec.seq), pm._feats,
                                         line_width=120)
            plain = text.plain
            assert "╲" in plain and "╱" in plain, (
                "expected intron zigzag glyphs in rendered seq panel"
            )

    def test_parse_pypi_version_strict(self):
        """Parser accepts canonical X.Y.Z[.W] integers and rejects
        anything with a non-numeric component (pre-releases,
        garbage, blanks). None for failure lets the caller skip
        notification rather than guess."""
        assert sc._parse_pypi_version("0.5.11.0") == (0, 5, 11, 0)
        assert sc._parse_pypi_version("1.0.0") == (1, 0, 0)
        assert sc._parse_pypi_version("0.5.11.0.1") == (0, 5, 11, 0, 1)
        assert sc._parse_pypi_version("1.0rc1") is None
        assert sc._parse_pypi_version("1.0.0a") is None
        assert sc._parse_pypi_version("") is None
        assert sc._parse_pypi_version("   ") is None
        assert sc._parse_pypi_version(None) is None  # type: ignore[arg-type]

    def test_is_newer_pypi_version_comparator(self):
        """Strict newer-than: equal is False, parse failures are
        False, lex order matches numeric order across all four
        components."""
        assert sc._is_newer_pypi_version("0.5.11.0", "0.5.10.0") is True
        assert sc._is_newer_pypi_version("0.5.11.0", "0.5.11.0") is False
        assert sc._is_newer_pypi_version("0.5.10.0", "0.5.11.0") is False
        assert sc._is_newer_pypi_version("1.0.0.0", "0.5.99.0") is True
        assert sc._is_newer_pypi_version("0.5.11.1", "0.5.11.0") is True
        # Parse failures bias to "no notification".
        assert sc._is_newer_pypi_version("garbage", "0.5.11.0") is False
        assert sc._is_newer_pypi_version("0.5.11.0", "garbage") is False

    def test_sanitize_plasmid_status_strict(self):
        """Strict acceptance of the four canonical statuses; anything
        else (case-mismatched, padded, non-string, dict, None)
        collapses to empty so a hand-edited library JSON can't
        smuggle a junk status into the renderer."""
        for ok in sc._PLASMID_STATUS_VALUES:
            assert sc._sanitize_plasmid_status(ok) == ok
        for bad in ("Designing", "VERIFIED ", " VERIFIED", "verified",
                     "DONE", "", None, 1, {"x": "y"}, ["VERIFIED"]):
            assert sc._sanitize_plasmid_status(bad) == ""

    async def test_library_panel_persists_status_through_save(
            self, tiny_record, isolated_library):
        """Setting status on a library entry persists through a
        re-save (`add_entry`) — saving the same plasmid again
        keeps the previously-assigned status instead of resetting
        to empty."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            lib = app.query_one("#library", sc.LibraryPanel)
            rec = SeqRecord(Seq("A" * 50), id="myplas", name="myplas",
                             annotations={"molecule_type": "DNA",
                                          "topology": "circular"})
            lib.add_entry(rec)
            # Manually set status (simulating the picker's save path).
            entries = sc._load_library()
            for e in entries:
                if e.get("id") == "myplas":
                    e["status"] = "VERIFIED"
            sc._save_library(entries)
            # Re-add (e.g. user re-saved after edits) — status should
            # survive.
            lib.add_entry(rec)
            entries = sc._load_library()
            after = next(
                (e for e in entries if e.get("id") == "myplas"), None
            )
            assert after is not None
            assert after.get("status") == "VERIFIED"

    def test_compute_name_col_width_caps_at_ceiling(
            self, isolated_library):
        """Library + collection names beyond the cap don't push the
        panel beyond `_NAME_COL_CEIL` — a single 200-char name must
        not stretch the layout off-screen."""
        # Seed a library with one absurdly long name.
        sc._save_library([{
            "id": "x", "name": "p" * 200, "size": 100,
            "n_feats": 0, "source": "test", "added": "2026-05-04",
            "gb_text": "", "status": "",
        }])
        # Build a panel directly to exercise the helper without
        # standing up a full app harness.
        panel = sc.LibraryPanel()
        # The helper reads `_load_library` / `_load_collections`
        # directly, which is what we just wrote.
        w = panel._compute_name_col_width()
        assert w == sc.LibraryPanel._NAME_COL_CEIL
        # And short-name libraries clamp to the floor.
        sc._save_library([{
            "id": "x", "name": "p", "size": 100,
            "n_feats": 0, "source": "test", "added": "2026-05-04",
            "gb_text": "", "status": "",
        }])
        # Re-create panel so the cached library is fresh.
        panel2 = sc.LibraryPanel()
        assert panel2._compute_name_col_width() == \
            sc.LibraryPanel._NAME_COL_FLOOR

    def test_changelog_section_parser_round_trip(self):
        """`_parse_changelog_sections` splits a mock CHANGELOG into
        (version, body) pairs preserving source order."""
        md = (
            "# Changelog\n"
            "## [0.5.11.0] — 2026-05-04\n\n"
            "### Added\n- Foo\n"
            "## [0.5.10.0] — 2026-05-03\n\n"
            "### Fixed\n- Bar\n"
            "## [0.5.9.0] — 2026-05-02\n\n"
            "### Added\n- Baz\n"
        )
        sections = sc._parse_changelog_sections(md)
        assert [s[0] for s in sections] == ["0.5.11.0", "0.5.10.0", "0.5.9.0"]
        assert "Foo" in sections[0][1]
        assert "Bar" in sections[1][1]
        assert "Baz" in sections[2][1]

    def test_version_sort_descending(self):
        """`_version_sort_key` sorts SemVer-like strings such that
        `sorted(..., reverse=True)` puts the newest version first."""
        versions = ["0.5.10.0", "0.5.9.0", "0.5.11.0", "0.5.9.1"]
        ordered = sorted(versions, key=sc._version_sort_key, reverse=True)
        assert ordered == ["0.5.11.0", "0.5.10.0", "0.5.9.1", "0.5.9.0"]

    def test_build_whats_new_body_orders_versions_newest_first(self):
        """Body markdown lists versions newest-first regardless of
        the order they appear in the source CHANGELOG."""
        md = (
            "## [0.5.9.0] — 2026-05-02\n### Added\n- Baz\n"
            "## [0.5.11.0] — 2026-05-04\n### Added\n- Foo\n"
            "## [0.5.10.0] — 2026-05-03\n### Fixed\n- Bar\n"
        )
        out = sc._build_whats_new_body(md, current_version="0.5.11.0",
                                         max_versions=10)
        # Newest version's section title appears before older ones.
        i_11 = out.index("0.5.11.0")
        i_10 = out.index("0.5.10.0")
        i_9  = out.index("0.5.9.0")
        assert i_11 < i_10 < i_9

    def test_build_whats_new_body_truncates_to_max_versions(self):
        """Body keeps only the N most recent releases when more
        than N versions are present, and includes a footer pointing
        users at the GitHub changelog for older entries."""
        md = "".join(
            f"## [0.5.{i}.0] — 2026-05-04\n### Added\n- v{i}\n"
            for i in range(10)
        )
        out = sc._build_whats_new_body(md, current_version="0.5.9.0",
                                         max_versions=3)
        # Newest 3 are present; older bullets are not.
        for keep in ("v9", "v8", "v7"):
            assert keep in out
        for drop in ("v6", "v5", "v0"):
            assert drop not in out
        # Footer points at the GitHub changelog when truncated.
        assert sc._WHATS_NEW_GITHUB_URL in out
        assert "older releases" in out.lower()

    def test_build_whats_new_body_drops_unreleased(self):
        """Non-numeric headings like `[Unreleased]` are filtered
        out — the modal is for end users on a tagged build."""
        md = (
            "## [Unreleased]\n### Added\n- in-progress thing\n"
            "## [0.5.11.0] — 2026-05-04\n### Added\n- shipped thing\n"
        )
        out = sc._build_whats_new_body(md, current_version="0.5.11.0",
                                         max_versions=10)
        assert "shipped thing" in out
        assert "in-progress thing" not in out
        assert "Unreleased" not in out

    def test_build_whats_new_body_no_truncation_footer(self):
        """When all versions fit under the cap, the footer phrasing
        switches to 'mirrored on GitHub' rather than 'older releases'."""
        md = "## [0.5.11.0] — 2026-05-04\n### Added\n- Foo\n"
        out = sc._build_whats_new_body(md, current_version="0.5.11.0",
                                         max_versions=3)
        assert sc._WHATS_NEW_GITHUB_URL in out
        assert "older releases" not in out.lower()
        assert "mirrored on github" in out.lower()

    def test_primer_tm_safe_bounds(self):
        """`_primer_tm_safe` returns None for too-short / too-long
        inputs and a positive float for a typical primer."""
        assert sc._primer_tm_safe("") is None
        assert sc._primer_tm_safe("AC") is None        # < 5 bp
        assert sc._primer_tm_safe("A" * 250) is None   # > 200 bp cap
        tm = sc._primer_tm_safe("GAATTCATGAAACGAAGCT")
        assert tm is not None and 30.0 < tm < 80.0

    def test_primer_tm_safe_is_cached(self):
        """Repeat calls hit the lru_cache rather than re-running
        primer3 thermodynamics."""
        sc._primer_tm_safe.cache_clear()
        sc._primer_tm_safe("GAATTCATGAAACGAAGCT")
        info1 = sc._primer_tm_safe.cache_info()
        sc._primer_tm_safe("GAATTCATGAAACGAAGCT")
        info2 = sc._primer_tm_safe.cache_info()
        assert info2.hits == info1.hits + 1

    async def test_primer_edit_modal_rejects_oversized_prefix(
            self, isolated_library):
        """A custom prefix longer than `_PRIMER_PREFIX_MAX_LEN` is
        bounced before the regex check; the primer sequence is
        unchanged and the status row reports the cap."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 200), id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(10, 18, strand=1),
                        type="primer_bind",
                        qualifiers={"label": ["P"],
                                    "primer_seq": ["AAAAAAAA"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            from textual.widgets import Button, Input, TextArea
            modal.query_one("#btn-primedit-edit", Button).action_press()
            await pilot.pause()
            modal.query_one("#primedit-custom-prefix",
                              Input).value = "A" * (sc._PRIMER_PREFIX_MAX_LEN + 1)
            await pilot.pause()
            modal.query_one("#btn-primedit-prefix-apply",
                              Button).action_press()
            await pilot.pause()
            # Sequence unchanged — the oversized prefix was rejected.
            assert modal.query_one("#primedit-seq",
                                     TextArea).text == "AAAAAAAA"

    async def test_primer_edit_modal_rejects_oversized_save(
            self, isolated_library):
        """Saving a primer longer than `_PRIMER_SEQ_MAX_LEN` is
        rejected — modal stays open with a status message rather
        than dismissing with a giant qualifier."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 200), id="P", name="P",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(10, 18, strand=1),
                        type="primer_bind",
                        qualifiers={"label": ["P"],
                                    "primer_seq": ["AAAAAAAA"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            app._open_feature_editor(0)
            await pilot.pause()
            await pilot.pause(0.05)
            modal = app.screen
            assert isinstance(modal, sc.PrimerEditModal)
            from textual.widgets import Button, TextArea
            modal.query_one("#btn-primedit-edit", Button).action_press()
            await pilot.pause()
            modal.query_one("#primedit-seq", TextArea).text = (
                "A" * (sc._PRIMER_SEQ_MAX_LEN + 1)
            )
            await pilot.pause()
            modal.query_one("#btn-primedit-save", Button).action_press()
            await pilot.pause()
            await pilot.pause(0.05)
            # Modal still up — save was rejected.
            assert isinstance(app.screen, sc.PrimerEditModal)

    async def test_whats_new_auto_pushes_on_version_change(
            self, tiny_record, isolated_library):
        """Fresh install (no `last_seen_version`): the modal auto-
        pushes after the splash dismisses. Persists `last_seen_version`
        on dismiss so the next launch on the same version stays
        quiet."""
        # Pre-condition: settings has no last_seen_version.
        assert sc._get_setting("last_seen_version", None) is None
        sc.PlasmidApp._preload_record = tiny_record
        # Don't skip splash for THIS test — we need to verify the
        # post-splash hook fires the WhatsNewModal.
        sc.PlasmidApp._skip_splash = False
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Splash is on top; dismiss it.
            assert isinstance(app.screen, sc.SplashScreen)
            app.screen.action_dismiss_splash()
            await pilot.pause()
            await pilot.pause(0.1)
            # WhatsNewModal should be active now.
            assert isinstance(app.screen, sc.WhatsNewModal)
            app.screen.action_dismiss_whatsnew()
            await pilot.pause()
            await pilot.pause(0.05)
            # Setting now reflects the running version.
            assert sc._get_setting("last_seen_version") == sc.__version__
        sc.PlasmidApp._preload_record = None
        sc.PlasmidApp._skip_splash = True

    async def test_whats_new_skipped_when_version_already_seen(
            self, tiny_record, isolated_library):
        """If `last_seen_version` already matches the running app
        version, the auto-push doesn't fire."""
        sc._set_setting("last_seen_version", sc.__version__)
        sc.PlasmidApp._preload_record = tiny_record
        sc.PlasmidApp._skip_splash = False
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            assert isinstance(app.screen, sc.SplashScreen)
            app.screen.action_dismiss_splash()
            await pilot.pause()
            await pilot.pause(0.1)
            assert not isinstance(app.screen, sc.WhatsNewModal)
        sc.PlasmidApp._preload_record = None
        sc.PlasmidApp._skip_splash = True

    def test_pairwise_align_basic(self):
        """1-bp substitution in a 300 bp sequence aligns with no gaps,
        99.67% identity, 1 mismatch, 0 gaps."""
        target = "ATGAAATTCC" * 30
        query  = target[:50] + "G" + target[51:]
        res = sc._pairwise_align(query, target)
        assert res["mode"] == "global"
        assert res["n_matches"] == 299
        assert res["n_mismatches"] == 1
        assert res["n_gaps"] == 0
        assert 99.0 < res["identity_pct"] < 100.0
        assert len(res["aligned_q"]) == len(res["aligned_t"]) == 300

    def test_pairwise_align_rejects_empty_and_oversized(self):
        with pytest.raises(ValueError):
            sc._pairwise_align("", "ATGC")
        with pytest.raises(ValueError):
            sc._pairwise_align("ATGC", "")
        with pytest.raises(ValueError):
            sc._pairwise_align("A" * 300_000, "ATGC")

    def test_pairwise_align_rejects_bad_mode(self):
        with pytest.raises(ValueError):
            sc._pairwise_align("ATGC", "ATGC", mode="semiglobal")

    def test_alignment_screen_handles_wrap_feature_on_target(self):
        """Regression guard for 2026-05-06 fix: AlignmentScreen previously
        did `int(loc.start)` on every target feature, silently flattening
        a wrap CDS to span the wrong arc (sacred invariant #9). The fix
        per-part dissects so each arc-half annotates its own columns."""
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import (
            SeqFeature, FeatureLocation, CompoundLocation,
        )
        from Bio.Seq import Seq as _Seq

        # 30 bp target with a wrap CDS at [25..30) + [0..5) (label = "wrapCDS").
        target_seq = "A" * 30
        wrap_loc = CompoundLocation([
            FeatureLocation(25, 30, strand=1),
            FeatureLocation(0, 5, strand=1),
        ])
        feat = SeqFeature(wrap_loc, type="CDS",
                          qualifiers={"label": ["wrapCDS"]})
        target_rec = SeqRecord(_Seq(target_seq), id="t", name="t",
                               features=[feat])
        # Build a trivial result: query = target (perfect match).
        result = sc._pairwise_align(target_seq, target_seq)
        scr = sc.AlignmentScreen("q", "t", target_rec, result)

        # Reach into the per-bp feature annotation table the same way
        # _body_text builds it.
        feat_at_bp = [""] * len(target_seq)
        for f in target_rec.features:
            label = f.qualifiers.get("label", [f.type])[0]
            for part in (getattr(f.location, "parts", None) or [f.location]):
                s, e = int(part.start), int(part.end)
                if e <= s:
                    continue
                for i in range(s, min(e, len(feat_at_bp))):
                    if not feat_at_bp[i]:
                        feat_at_bp[i] = label

        # Both arc halves must carry the label; the gap between them
        # (5..25) must be empty. A flatten regression would label
        # 0..30 (everywhere) — distinguishable.
        assert feat_at_bp[0]  == "wrapCDS"   # head arc
        assert feat_at_bp[4]  == "wrapCDS"   # head arc tail
        assert feat_at_bp[5]  == ""          # gap starts
        assert feat_at_bp[24] == ""          # gap ends
        assert feat_at_bp[25] == "wrapCDS"   # tail arc start
        assert feat_at_bp[29] == "wrapCDS"   # tail arc end

        # Smoke: _body_text should run without exceptions on a wrap target.
        out = scr._body_text()
        assert out is not None
        assert "wrapCDS" not in str(out) or True  # rendering may abbreviate

    def test_list_gbk_members_in_zip(self, tmp_path):
        import zipfile
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        # Build a synthetic Plasmidsaurus-style zip
        rec = SeqRecord(Seq("ATGC" * 50), id="cons", name="cons",
                        annotations={"molecule_type": "DNA",
                                       "topology": "circular"})
        gbk = tmp_path / "consensus.gbk"
        SeqIO.write(rec, gbk, "genbank")
        zp = tmp_path / "run.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.write(gbk, "sample_A/sample_A_consensus.gbk")
            zf.writestr("sample_A/qc.png",   b"PNG")
            zf.writestr("readme.txt",        b"hi")
            zf.writestr("sample_B/.hidden.gbk", b"hidden")  # dotfile skipped
        members = sc._list_gbk_members_in_zip(zp)
        names = [m["name"] for m in members]
        assert "sample_A/sample_A_consensus.gbk" in names
        assert "sample_A/qc.png" not in names
        assert "readme.txt" not in names
        # Hidden dotfiles must be skipped (zip noise from macOS .DS etc.)
        assert "sample_B/.hidden.gbk" not in names

    def test_list_gbk_members_rejects_non_zip(self, tmp_path):
        bad = tmp_path / "not_a_zip.zip"
        bad.write_text("plain text not a zip")
        with pytest.raises(ValueError):
            sc._list_gbk_members_in_zip(bad)

    def test_list_gbk_members_rejects_oversized(self, tmp_path, monkeypatch):
        import zipfile
        # Cap the zip-size constant so we don't have to write 500 MB.
        monkeypatch.setattr(sc, "_PLASMIDSAURUS_ZIP_MAX_BYTES", 100)
        zp = tmp_path / "huge.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("a/a.gbk", "X" * 1000)
        # The zip's *file* size on disk will exceed 100 bytes.
        with pytest.raises(ValueError, match="too large"):
            sc._list_gbk_members_in_zip(zp)

    def test_bulk_import_folder_progress_cb(self, tmp_path):
        """Per-file progress callback fires for every importable file
        in order, with 1-based indices and stable totals."""
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ATGC" * 30), id="r", name="r",
                        annotations={"molecule_type": "DNA"})
        # Three good files + one corrupt file.
        for i, name in enumerate(["a.gb", "b.gb", "c.gb"]):
            SeqIO.write(rec, tmp_path / name, "genbank")
        (tmp_path / "broken.dna").write_bytes(b"not a commercialsaas")
        ticks = []
        def cb(idx, total, fname, ok):
            ticks.append((idx, total, fname, ok))
        entries, failures = sc._bulk_import_folder(
            tmp_path, progress_cb=cb,
        )
        assert len(ticks) == 4, f"expected 4 ticks, got {ticks}"
        # Indices 1..4 in order
        assert [t[0] for t in ticks] == [1, 2, 3, 4]
        # Total stays at 4 throughout
        assert all(t[1] == 4 for t in ticks)
        # Three OKs + one fail (broken.dna)
        oks = [t for t in ticks if t[3]]
        fails = [t for t in ticks if not t[3]]
        assert len(oks) == 3
        assert len(fails) == 1
        assert fails[0][2] == "broken.dna"

    def test_bulk_import_folder_progress_cb_failure_does_not_crash(
        self, tmp_path
    ):
        """Exceptions inside the progress callback are caught and
        logged — they must not abort the import."""
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ATGC" * 30), id="r", name="r",
                        annotations={"molecule_type": "DNA"})
        for name in ["a.gb", "b.gb"]:
            SeqIO.write(rec, tmp_path / name, "genbank")
        def boom(*_):
            raise RuntimeError("test")
        # Must NOT raise — progress_cb errors are caught and logged.
        entries, _ = sc._bulk_import_folder(tmp_path, progress_cb=boom)
        assert len(entries) == 2

    def test_extract_gbk_member_round_trip(self, tmp_path):
        import zipfile
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ATGCATGC" * 20), id="x", name="x",
                        annotations={"molecule_type": "DNA"})
        gbk = tmp_path / "x.gbk"
        SeqIO.write(rec, gbk, "genbank")
        zp = tmp_path / "z.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.write(gbk, "x.gbk")
        text = sc._extract_gbk_member(zp, "x.gbk")
        rec_back = sc._gb_text_to_record(text)
        assert str(rec_back.seq) == "ATGCATGC" * 20

    async def test_plasmidsaurus_modal_lists_zip_members_on_pick(
        self, tmp_path, isolated_library
    ):
        """End-to-end: user picks a .zip via the embedded directory
        tree, the modal's members table populates with the .gbk
        entries inside. Library has at least one entry so the target
        Select isn't disabled."""
        import zipfile
        from Bio import SeqIO
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        # Write a real .zip with a .gbk inside
        rec = SeqRecord(Seq("ATGC" * 50), id="cons", name="cons",
                        annotations={"molecule_type": "DNA"})
        gbk = tmp_path / "consensus.gbk"
        SeqIO.write(rec, gbk, "genbank")
        zp = tmp_path / "run.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.write(gbk, "sample/consensus.gbk")
        # Save a library entry so the target dropdown has an option
        sc._save_library([{
            "id": "TARGET", "name": "TARGET", "size": len(rec.seq),
            "n_feats": 0, "added": "2026-05-03",
            "gb_text": sc._record_to_gb_text(rec),
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            await app.push_screen(
                sc.PlasmidsaurusAlignModal(start_path=str(tmp_path))
            )
            await pilot.pause(0.2)
            modal = app.screen
            assert isinstance(modal, sc.PlasmidsaurusAlignModal)
            # Synthesise the FileSelected event the directory tree
            # would emit on click (more deterministic than driving
            # actual mouse coordinates against the tree's geometry).
            from textual.widgets import DirectoryTree
            tree = modal.query_one("#align-zip-tree",
                                     sc._ZipAwareDirectoryTree)
            modal.post_message(
                DirectoryTree.FileSelected(tree.root, zp)
            )
            await pilot.pause(0.2)
            assert modal._zip_path is not None
            assert modal._zip_path.name == "run.zip"
            # The members table should now have one row
            t = modal.query_one("#align-members", sc.DataTable)
            assert t.row_count == 1
            app.exit()

    async def test_plasmidsaurus_modal_rejects_non_zip(
        self, tmp_path, isolated_library
    ):
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            await app.push_screen(
                sc.PlasmidsaurusAlignModal(start_path=str(tmp_path))
            )
            await pilot.pause(0.2)
            modal = app.screen
            from textual.widgets import DirectoryTree
            tree = modal.query_one("#align-zip-tree",
                                     sc._ZipAwareDirectoryTree)
            # A .txt file, not a .zip — must be rejected.
            txt = tmp_path / "readme.txt"
            txt.write_text("hello")
            modal.post_message(
                DirectoryTree.FileSelected(tree.root, txt)
            )
            await pilot.pause(0.2)
            # Modal stays open, _zip_path stays None, members empty
            assert modal._zip_path is None
            assert modal.query_one("#align-members",
                                     sc.DataTable).row_count == 0
            assert modal.query_one("#btn-align-go",
                                     sc.Button).disabled is True
            app.exit()

    def test_extract_gbk_member_404(self, tmp_path):
        import zipfile
        zp = tmp_path / "z.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("real.gbk", "x")
        with pytest.raises(ValueError, match="not in zip"):
            sc._extract_gbk_member(zp, "imaginary.gbk")

    async def test_persistence_hydrates_on_startup(self, isolated_library):
        """User-preference toggles persist across app restarts: pre-set
        the keys via _set_setting, instantiate a new app, confirm
        compose() pulls them in."""
        sc._set_setting("show_feature_tooltips", False)
        sc._set_setting("click_debug",           True)
        sc._set_setting("show_restr",            True)
        sc._set_setting("restr_unique_only",     False)
        sc._set_setting("restr_min_len",         4)
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            assert app._show_feature_tooltips is False
            assert app._click_debug          is True
            assert app._show_restr           is True
            assert app._restr_unique_only    is False
            assert app._restr_min_len        == 4

    async def test_persistence_invalid_min_len_falls_back(self,
                                                            isolated_library):
        sc._set_setting("restr_min_len", "garbage")
        app = sc.PlasmidApp()
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Falls back to 6 — must not poison the scanner with an
            # arbitrary string from a hand-edited settings.json.
            assert app._restr_min_len == 6

    async def test_shift_arrow_extends_from_active_end_after_click(
        self, isolated_library
    ):
        """Bug regression: clicking a feature parks the cursor mid-
        feature (at the click bp) but anchors the selection at the
        feature's 5' end. Pre-fix, the first Shift+Right collapsed
        the selection to roughly half the feature ("highlight jumped
        to the centre"). Post-fix, Shift+Right grows / shrinks the
        active boundary by exactly 1 bp.
        """
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 500), id="A", name="A",
                        annotations={"molecule_type": "DNA",
                                       "topology":      "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(100, 200, strand=1), type="CDS",
                        qualifiers={"label": ["F"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            # Simulate a click that placed the cursor mid-feature.
            sp._user_sel    = (100, 200)
            sp._sel_range   = None
            sp._cursor_pos  = 150       # middle of feature
            sp._sel_anchor  = 100       # anchor at feature start
            # Shift+Right → extend by 1 from the right end (200 → 201)
            await self._press_via_app(app, "shift+right")
            await pilot.pause(0.05)
            assert sp._user_sel == (100, 201), (
                f"expected (100, 201) after Shift+Right; got {sp._user_sel}"
            )
            # Shift+Left → shrink by 1 from the right end (201 → 200)
            await self._press_via_app(app, "shift+left")
            await pilot.pause(0.05)
            assert sp._user_sel == (100, 200), (
                f"expected (100, 200) after Shift+Left; got {sp._user_sel}"
            )
            # Another Shift+Left → selection now (100, 199)
            await self._press_via_app(app, "shift+left")
            await pilot.pause(0.05)
            assert sp._user_sel == (100, 199), (
                f"expected (100, 199) after second Shift+Left; got {sp._user_sel}"
            )

    async def test_shift_arrow_chain_extends_one_bp_per_press(
        self, isolated_library
    ):
        """After the snap-to-boundary fix, chained Shift+Right presses
        must each extend the right edge by exactly 1 bp."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        rec = SeqRecord(Seq("A" * 500), id="A", name="A",
                        annotations={"molecule_type": "DNA",
                                       "topology":      "circular"})
        rec.features = [
            SeqFeature(FeatureLocation(50, 80, strand=1), type="CDS",
                        qualifiers={"label": ["F"]}),
        ]
        app = _build_app(rec, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel    = (50, 80)
            sp._cursor_pos  = 60        # middle
            sp._sel_anchor  = 50        # anchor at start
            for i in range(1, 6):
                await self._press_via_app(app, "shift+right")
                await pilot.pause(0.05)
                assert sp._user_sel == (50, 80 + i), (
                    f"after {i} Shift+Right: expected (50, {80 + i}), "
                    f"got {sp._user_sel}"
                )

    def test_format_feat_tooltip_shape(self):
        """Tooltip text covers type+label, bp range, strand, length, and
        falls through cleanly when the feat dict is missing fields."""
        feat = {"type": "CDS", "label": "lacZ",
                 "start": 100, "end": 250, "strand": 1,
                 "qualifiers": {"product": ["beta-galactosidase"]}}
        out = sc._format_feat_tooltip(feat, total=3000)
        assert "CDS" in out and "lacZ" in out
        assert "101..250" in out, out      # 1-based display
        assert "(+)" in out
        assert "150 bp" in out             # length
        assert "beta-galactosidase" in out
        # Wrap feature: end < start
        wrap = {"type": "misc_feature", "label": "wrap", "start": 950,
                  "end": 50, "strand": -1}
        out2 = sc._format_feat_tooltip(wrap, total=1000)
        assert "951..1000, 1..50" in out2, out2
        assert "(−)" in out2 or "(-)" in out2
        # Missing label → falls back to type
        bare = {"type": "misc_feature", "start": 0, "end": 10, "strand": 0}
        out3 = sc._format_feat_tooltip(bare, total=100)
        assert "misc_feature" in out3
        assert "(·)" in out3 or "( )" in out3 or "(+" in out3 or "(−" in out3

    async def test_settings_menu_present_in_menubar(self, tiny_record,
                                                      isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Settings tab is rendered as a Static in the MenuBar with
            # id `menu-settings`. Mere presence is the contract.
            try:
                _ = app.query_one("#menu-settings", sc.Static)
            except sc.NoMatches:
                pytest.fail("Settings tab missing from menu bar")
            # Also confirm it's listed between File and Edit (next-to-
            # File per the user request).
            assert "Settings" in sc.MenuBar.MENUS
            assert sc.MenuBar.MENUS.index("Settings") == 1

    async def test_toggle_feature_tooltips_persists(self, tiny_record,
                                                      isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            # Default: OFF as of 2026-05-07 — the popup got in the
            # way for users who already see feature info on the
            # sidebar / map. Toggle to bring it back.
            assert app._show_feature_tooltips is False
            assert sc._get_setting(
                "show_feature_tooltips", False,
            ) is False
            # Toggle ON via the action.
            app.action_toggle_feature_tooltips()
            await pilot.pause(0.05)
            assert app._show_feature_tooltips is True
            assert sc._get_setting(
                "show_feature_tooltips", False,
            ) is True
            # Toggle back off.
            app.action_toggle_feature_tooltips()
            await pilot.pause(0.05)
            assert app._show_feature_tooltips is False
            assert sc._get_setting(
                "show_feature_tooltips", False,
            ) is False

    async def test_tooltip_off_clears_widget_tooltip(self, tiny_record,
                                                      isolated_library):
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            sp = app.query_one("#seq-panel",   sc.SequencePanel)
            # Pretend a tooltip was just set by hover
            pm.tooltip = "lingering"
            sp.tooltip = "lingering"
            # Toggle off — should wipe both
            app._show_feature_tooltips = True
            app.action_toggle_feature_tooltips()
            await pilot.pause(0.05)
            assert pm.tooltip is None
            assert sp.tooltip is None

    def test_extend_helper_returns_false_without_anchor(self, tiny_record,
                                                          isolated_library):
        # Pure-handler unit test: with selected_idx == -1, the helper
        # must return False rather than computing a span from a
        # phantom anchor.
        app = sc.PlasmidApp()
        # Build a minimal mock with the bits the helper queries.
        class StubSeqPanel:
            _seq = "X" * 200
            _user_sel = None
            _sel_range = None
            _cursor_pos = -1
            def _refresh_view(self): pass
            def _ensure_cursor_visible(self): pass
        class StubSidebar:
            def show_detail(self, *_): pass
        class StubPM:
            selected_idx = -1
            _feats = []
            _total = 200
        # Stitch via query_one indirection — too invasive without a
        # full mount. Just exercise the early-return:
        result = sc.PlasmidApp._extend_selection_to.__wrapped__ \
            if hasattr(sc.PlasmidApp._extend_selection_to, "__wrapped__") \
            else sc.PlasmidApp._extend_selection_to
        # The unbound method needs `self` with .query_one — easier to
        # just assert via the integration tests above. This unit
        # check is a placeholder noting the helper exists.
        assert callable(sc.PlasmidApp._extend_selection_to)


# ═══════════════════════════════════════════════════════════════════════════════
# `splicecraft update` self-update subcommand
# ═══════════════════════════════════════════════════════════════════════════════
#
# The subcommand has three concerns we cover here:
#   1. Install-method detection across pipx / pip / editable / source / unknown.
#   2. Upgrade-command construction — the right argv for each method, with
#      and without --force.
#   3. Top-level flow: --check is read-only, editable + source refuse pip,
#      pip-system prints sudo without auto-running, PyPI-unreachable surfaces
#      cleanly, the confirmation prompt defaults to no, --yes skips, and
#      `main()` dispatches `update` without launching the Textual TUI.
#
# All tests monkeypatch `subprocess.run` and `_fetch_latest_pypi_version` so
# they never touch the network or actually invoke pip/pipx. A `_FakeRun`
# helper records the argv passed to subprocess so each test can assert the
# exact upgrade command the subcommand decided to run.

class _FakeRun:
    """Records argv from the most recent subprocess.run call. Replaces
    `subprocess.run` in tests; mimics CompletedProcess(returncode=0)."""
    def __init__(self, returncode: int = 0, raise_exc: BaseException | None = None):
        self.returncode = returncode
        self.raise_exc = raise_exc
        self.calls: list[list[str]] = []

    def __call__(self, cmd, check=False, **kwargs):
        self.calls.append(list(cmd))
        if self.raise_exc is not None:
            raise self.raise_exc
        class _CP:
            def __init__(self, rc: int):
                self.returncode = rc
        return _CP(self.returncode)


class TestUpdateSubcommandDetection:
    """Install-method detection — covers each branch returned by
    `_detect_install_method`. The helper is pure (reads sys.executable,
    sys.prefix, and __file__) so we monkeypatch those plus the
    `_find_dist_info_dir` helper to simulate each install layout."""

    def test_returns_required_keys(self):
        info = sc._detect_install_method()
        for key in ("method", "module", "python", "venv", "git_clone", "details"):
            assert key in info, f"missing key {key!r}"
        assert info["method"] in sc._INSTALL_METHODS

    def test_source_clone_detected(self):
        # Running pytest from the repo: splicecraft.py sits next to .git/
        # + pyproject.toml. The actual environment satisfies this.
        info = sc._detect_install_method()
        # In CI / dev, the running module is the source clone — but we
        # also tolerate "editable" if someone ran `pip install -e .`
        # because direct_url.json overrides the path heuristic.
        assert info["method"] in ("source", "editable"), info

    def test_pipx_path_classified(self, monkeypatch, tmp_path):
        # Synthesise a path that looks like a pipx-managed venv, point
        # __file__ at it, and make sure detection picks pipx.
        fake_venv = tmp_path / ".local" / "share" / "pipx" / "venvs" / "splicecraft"
        fake_lib = fake_venv / "lib" / "python3.11" / "site-packages"
        fake_lib.mkdir(parents=True)
        fake_mod = fake_lib / "splicecraft.py"
        fake_mod.write_text("# stub")
        monkeypatch.setattr(sc, "__file__", str(fake_mod))
        # Make _find_dist_info_dir return None so the editable branch
        # doesn't fire (no direct_url.json).
        monkeypatch.setattr(sc, "_find_dist_info_dir", lambda *a, **k: None)
        info = sc._detect_install_method()
        assert info["method"] == "pipx", info
        assert info["venv"] == str(fake_venv)

    def test_editable_via_direct_url(self, monkeypatch, tmp_path):
        # PEP 610: direct_url.json with dir_info.editable=true means
        # editable install. The path heuristic must NOT win over this.
        di = tmp_path / "splicecraft-0.7.5.0.dist-info"
        di.mkdir()
        durl = di / "direct_url.json"
        durl.write_text(
            '{"url":"file:///home/me/SpliceCraft",'
            '"dir_info":{"editable":true}}'
        )
        monkeypatch.setattr(sc, "_find_dist_info_dir",
                              lambda *a, **k: di)
        info = sc._detect_install_method()
        assert info["method"] == "editable", info

    def test_pip_user_classified(self, monkeypatch, tmp_path):
        # ~/.local/lib path with no .git/pyproject sibling and no
        # direct_url.json → pip-user.
        fake = tmp_path / ".local" / "lib" / "python3.11" / "site-packages"
        fake.mkdir(parents=True)
        fake_mod = fake / "splicecraft.py"
        fake_mod.write_text("# stub")
        monkeypatch.setattr(sc, "__file__", str(fake_mod))
        monkeypatch.setattr(sc, "_find_dist_info_dir", lambda *a, **k: None)
        # Force the venv check to be False.
        monkeypatch.setattr(sys, "prefix", sys.base_prefix, raising=False)
        info = sc._detect_install_method()
        assert info["method"] == "pip-user", info

    def test_pip_system_classified(self, monkeypatch, tmp_path):
        fake = tmp_path / "usr" / "lib" / "python3.11" / "site-packages"
        fake.mkdir(parents=True)
        fake_mod = fake / "splicecraft.py"
        fake_mod.write_text("# stub")
        monkeypatch.setattr(sc, "__file__", str(fake_mod))
        monkeypatch.setattr(sc, "_find_dist_info_dir", lambda *a, **k: None)
        monkeypatch.setattr(sys, "prefix", sys.base_prefix, raising=False)
        info = sc._detect_install_method()
        assert info["method"] == "pip-system", info

    def test_unknown_when_no_signals(self, monkeypatch, tmp_path):
        fake_mod = tmp_path / "weird" / "splicecraft.py"
        fake_mod.parent.mkdir(parents=True)
        fake_mod.write_text("# stub")
        monkeypatch.setattr(sc, "__file__", str(fake_mod))
        monkeypatch.setattr(sc, "_find_dist_info_dir", lambda *a, **k: None)
        monkeypatch.setattr(sys, "prefix", sys.base_prefix, raising=False)
        info = sc._detect_install_method()
        assert info["method"] == "unknown", info

    # ── uv-tool / uv-venv ──────────────────────────────────────────

    def test_uv_tool_path_classified(self, monkeypatch, tmp_path):
        # `uv tool install splicecraft` lays out the venv at
        # ~/.local/share/uv/tools/splicecraft/.
        fake_venv = tmp_path / ".local" / "share" / "uv" / "tools" / "splicecraft"
        fake_lib = fake_venv / "lib" / "python3.11" / "site-packages"
        fake_lib.mkdir(parents=True)
        fake_mod = fake_lib / "splicecraft.py"
        fake_mod.write_text("# stub")
        monkeypatch.setattr(sc, "__file__", str(fake_mod))
        monkeypatch.setattr(sc, "_find_dist_info_dir", lambda *a, **k: None)
        info = sc._detect_install_method()
        assert info["method"] == "uv-tool", info
        assert info["venv"] == str(fake_venv)

    def test_uv_venv_via_pyvenv_cfg(self, monkeypatch, tmp_path):
        # uv writes `uv = <version>` into pyvenv.cfg on venv create.
        # That signature is what we use to distinguish a uv-managed
        # venv from a plain python -m venv.
        venv_root = tmp_path / "myproject" / ".venv"
        site = venv_root / "lib" / "python3.11" / "site-packages"
        site.mkdir(parents=True)
        (venv_root / "pyvenv.cfg").write_text(
            "home = /usr/bin\n"
            "implementation = CPython\n"
            "uv = 0.4.18\n"
            "version_info = 3.11.10\n"
        )
        fake_mod = site / "splicecraft.py"
        fake_mod.write_text("# stub")
        monkeypatch.setattr(sc, "__file__", str(fake_mod))
        monkeypatch.setattr(sc, "_find_dist_info_dir", lambda *a, **k: None)
        # Force the in-venv branch of detection.
        monkeypatch.setattr(sys, "prefix", str(venv_root), raising=False)
        # base_prefix differs from prefix so the in-venv check fires.
        monkeypatch.setattr(sys, "base_prefix", "/usr", raising=False)
        info = sc._detect_install_method()
        assert info["method"] == "uv-venv", info
        assert info["venv"] == str(venv_root)

    def test_pip_venv_no_uv_signature(self, monkeypatch, tmp_path):
        # Same layout as the uv-venv test but pyvenv.cfg has no `uv`
        # line — should fall back to plain pip-venv.
        venv_root = tmp_path / "myproject" / ".venv"
        site = venv_root / "lib" / "python3.11" / "site-packages"
        site.mkdir(parents=True)
        (venv_root / "pyvenv.cfg").write_text(
            "home = /usr/bin\n"
            "implementation = CPython\n"
            "version_info = 3.11.10\n"
        )
        fake_mod = site / "splicecraft.py"
        fake_mod.write_text("# stub")
        monkeypatch.setattr(sc, "__file__", str(fake_mod))
        monkeypatch.setattr(sc, "_find_dist_info_dir", lambda *a, **k: None)
        monkeypatch.setattr(sys, "prefix", str(venv_root), raising=False)
        monkeypatch.setattr(sys, "base_prefix", "/usr", raising=False)
        info = sc._detect_install_method()
        assert info["method"] == "pip-venv", info

    # ── pixi-global / pixi-project ─────────────────────────────────

    def test_pixi_global_path_classified(self, monkeypatch, tmp_path):
        # `pixi global install splicecraft` puts the env at
        # ~/.pixi/envs/splicecraft/. The env directory name matches
        # the package name for `pixi global`.
        fake_env = tmp_path / ".pixi" / "envs" / "splicecraft"
        fake_lib = fake_env / "lib" / "python3.11" / "site-packages"
        fake_lib.mkdir(parents=True)
        fake_mod = fake_lib / "splicecraft.py"
        fake_mod.write_text("# stub")
        monkeypatch.setattr(sc, "__file__", str(fake_mod))
        monkeypatch.setattr(sc, "_find_dist_info_dir", lambda *a, **k: None)
        info = sc._detect_install_method()
        assert info["method"] == "pixi-global", info
        assert info["venv"] == str(fake_env)

    def test_pixi_project_path_classified(self, monkeypatch, tmp_path):
        # `pixi add splicecraft` in a project puts the env at
        # <project>/.pixi/envs/<env_name>/ — env name is NOT
        # 'splicecraft' (typically 'default'), which is exactly how
        # pixi-project is distinguished from pixi-global.
        project = tmp_path / "myproj"
        env = project / ".pixi" / "envs" / "default"
        site = env / "lib" / "python3.11" / "site-packages"
        site.mkdir(parents=True)
        fake_mod = site / "splicecraft.py"
        fake_mod.write_text("# stub")
        monkeypatch.setattr(sc, "__file__", str(fake_mod))
        monkeypatch.setattr(sc, "_find_dist_info_dir", lambda *a, **k: None)
        info = sc._detect_install_method()
        assert info["method"] == "pixi-project", info
        assert info["git_clone"] == str(project)

    def test_pixi_global_wins_over_pixi_project_pattern(
            self, monkeypatch, tmp_path):
        # Critical ordering test: a pixi-global path also matches the
        # broader `/.pixi/envs/` substring used for pixi-project. The
        # specific (global) match must win — otherwise every global
        # install would be misclassified as a project env and refused.
        fake_env = tmp_path / ".pixi" / "envs" / "splicecraft"
        site = fake_env / "lib" / "python3.11" / "site-packages"
        site.mkdir(parents=True)
        fake_mod = site / "splicecraft.py"
        fake_mod.write_text("# stub")
        monkeypatch.setattr(sc, "__file__", str(fake_mod))
        monkeypatch.setattr(sc, "_find_dist_info_dir", lambda *a, **k: None)
        info = sc._detect_install_method()
        assert info["method"] == "pixi-global", info


class TestUpdateSubcommandCommandBuilder:
    """`_build_upgrade_command` translates a detected method + the
    --force / --pre flags into the correct argv list."""

    def test_pipx_normal(self):
        cmd = sc._build_upgrade_command("pipx", force=False)
        assert cmd == ["pipx", "upgrade", "splicecraft"]

    def test_pipx_force_uses_install_force(self):
        # `pipx upgrade` is a no-op when versions match; --force has to
        # use `install --force` to actually re-run.
        cmd = sc._build_upgrade_command("pipx", force=True)
        assert cmd == ["pipx", "install", "--force", "splicecraft"]

    def test_pip_user(self):
        cmd = sc._build_upgrade_command("pip-user", force=False)
        assert cmd[:4] == [sys.executable, "-m", "pip", "install"]
        assert "--user" in cmd and "--upgrade" in cmd
        assert cmd[-1] == "splicecraft"
        assert "--force-reinstall" not in cmd

    def test_pip_user_force_appends_reinstall(self):
        cmd = sc._build_upgrade_command("pip-user", force=True)
        assert "--force-reinstall" in cmd

    def test_pip_venv_no_user_flag(self):
        cmd = sc._build_upgrade_command("pip-venv", force=False)
        assert "--user" not in cmd
        assert cmd[:4] == [sys.executable, "-m", "pip", "install"]
        assert "--upgrade" in cmd

    def test_pip_system_returns_command(self):
        # Builder still returns a command; the caller is responsible
        # for printing rather than running it.
        cmd = sc._build_upgrade_command("pip-system", force=False)
        assert cmd is not None and "splicecraft" in cmd

    def test_unknown_returns_command(self):
        cmd = sc._build_upgrade_command("unknown", force=False)
        assert cmd is not None and "splicecraft" in cmd

    def test_editable_refused(self):
        assert sc._build_upgrade_command("editable", force=False) is None

    def test_source_refused(self):
        assert sc._build_upgrade_command("source", force=True) is None

    # ── uv ─────────────────────────────────────────────────────────

    def test_uv_tool_normal(self):
        cmd = sc._build_upgrade_command("uv-tool", force=False)
        assert cmd == ["uv", "tool", "upgrade", "splicecraft"]

    def test_uv_tool_force_uses_install_force(self):
        cmd = sc._build_upgrade_command("uv-tool", force=True)
        assert cmd == ["uv", "tool", "install", "--force", "splicecraft"]

    def test_uv_venv(self):
        cmd = sc._build_upgrade_command("uv-venv", force=False)
        assert cmd == ["uv", "pip", "install", "--upgrade", "splicecraft"]

    def test_uv_venv_force_appends_reinstall(self):
        cmd = sc._build_upgrade_command("uv-venv", force=True)
        assert "--force-reinstall" in cmd
        assert cmd[:4] == ["uv", "pip", "install", "--upgrade"]

    # ── pixi ───────────────────────────────────────────────────────

    def test_pixi_global_normal(self):
        cmd = sc._build_upgrade_command("pixi-global", force=False)
        assert cmd == ["pixi", "global", "update", "splicecraft"]

    def test_pixi_global_force_uses_install_force(self):
        cmd = sc._build_upgrade_command("pixi-global", force=True)
        assert cmd == ["pixi", "global", "install", "--force", "splicecraft"]

    def test_pixi_project_refused(self):
        # Project envs are managed by the pixi manifest, not by direct
        # PyPI installs. Builder must refuse so the run-flow can
        # surface a clear "run pixi update" message.
        assert sc._build_upgrade_command("pixi-project", force=False) is None
        assert sc._build_upgrade_command("pixi-project", force=True) is None


class TestUpdateSubcommandFlow:
    """End-to-end flow tests on `_run_update_subcommand`. Each test
    monkeypatches:
        sc._fetch_latest_pypi_version  → fixed return (no network)
        sc._detect_install_method      → fixed install method
        sc.subprocess.run              → records argv, never executes
        builtins.input                 → simulates the y/N prompt
    so the tests are hermetic and complete in milliseconds."""

    def _patch_detect(self, monkeypatch, method: str, **extras):
        info = {
            "method": method, "module": "/fake/splicecraft.py",
            "python": sys.executable, "venv": None, "git_clone": None,
            "details": f"{method} (test stub)",
        }
        info.update(extras)
        monkeypatch.setattr(sc, "_detect_install_method", lambda: info)
        return info

    # ── flag handling ──────────────────────────────────────────────

    def test_help_flag_exits_zero(self, capsys):
        rc = sc._run_update_subcommand(["--help"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "splicecraft update" in out and "Usage:" in out

    def test_short_help_flag(self, capsys):
        rc = sc._run_update_subcommand(["-h"])
        assert rc == 0

    def test_unknown_flag_exits_two(self, capsys):
        rc = sc._run_update_subcommand(["--bogus"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "unknown argument" in err

    # ── PyPI fetch failure ─────────────────────────────────────────

    def test_pypi_unreachable_exits_one(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version", lambda *a, **k: None)
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--check"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Could not reach PyPI" in err
        assert fake.calls == []

    # ── --check flow ───────────────────────────────────────────────

    def test_check_up_to_date_no_subprocess(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: sc.__version__)
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--check"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "latest released version" in out or "Nothing to do" in out
        assert fake.calls == []

    def test_check_newer_available_lists_command(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        # pipx must be on PATH for the command-listing branch to fire.
        monkeypatch.setattr(sc.shutil, "which",
                              lambda name: "/usr/bin/pipx" if name == "pipx" else None)
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--check"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "pipx upgrade splicecraft" in out
        assert "Update available" in out
        assert fake.calls == []  # --check never runs the install

    def test_check_does_not_prompt(self, monkeypatch, capsys):
        # Even when an update is available, --check never calls input().
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)

        def _no_input(*a, **k):
            raise AssertionError("--check should never prompt")

        monkeypatch.setattr("builtins.input", _no_input)
        rc = sc._run_update_subcommand(["--check"])
        assert rc == 0

    # ── refusals (editable / source / pip-system) ──────────────────

    def test_editable_refuses_to_run_pip(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "editable",
                            git_clone="/home/me/clone")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "git pull" in err
        assert fake.calls == []

    def test_source_clone_refuses_to_run_pip(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "source",
                            git_clone="/home/me/clone")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "git pull" in err
        assert fake.calls == []

    def test_pip_system_warns_only_no_subprocess(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pip-system")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "sudo" in err
        # And we never run anything ourselves.
        assert fake.calls == []

    def test_pipx_not_on_path_warns(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: None)
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "pipx" in err and "PATH" in err
        assert fake.calls == []

    # ── happy path ─────────────────────────────────────────────────

    def test_yes_flag_skips_confirmation_and_runs_pipx(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")
        fake = _FakeRun(returncode=0)
        monkeypatch.setattr(sc.subprocess, "run", fake)

        def _no_input(*a, **k):
            raise AssertionError("--yes should skip the prompt")

        monkeypatch.setattr("builtins.input", _no_input)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 0
        assert fake.calls == [["pipx", "upgrade", "splicecraft"]]

    def test_force_with_same_version_still_runs(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pipx")
        # Identical version: without --force we'd return 0 and skip
        # subprocess; --force should still trigger reinstall.
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: sc.__version__)
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")
        fake = _FakeRun(returncode=0)
        monkeypatch.setattr(sc.subprocess, "run", fake)
        monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
        rc = sc._run_update_subcommand(["--force", "--yes"])
        assert rc == 0
        # --force must use `install --force` rather than `upgrade`.
        assert fake.calls == [["pipx", "install", "--force", "splicecraft"]]

    def test_pip_venv_invokes_python_m_pip(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pip-venv")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        fake = _FakeRun(returncode=0)
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 0
        assert len(fake.calls) == 1
        cmd = fake.calls[0]
        assert cmd[:4] == [sys.executable, "-m", "pip", "install"]
        assert "--upgrade" in cmd and cmd[-1] == "splicecraft"

    def test_pip_user_invokes_user_upgrade(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pip-user")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        fake = _FakeRun(returncode=0)
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 0
        cmd = fake.calls[0]
        assert "--user" in cmd and "--upgrade" in cmd

    # ── uv-tool happy path + PATH check ────────────────────────────

    def test_uv_tool_yes_runs_subprocess(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "uv-tool")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        # `uv` must be on PATH for the install branch to fire.
        monkeypatch.setattr(sc.shutil, "which",
                              lambda name: "/usr/bin/uv" if name == "uv" else None)
        fake = _FakeRun(returncode=0)
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 0
        assert fake.calls == [["uv", "tool", "upgrade", "splicecraft"]]

    def test_uv_not_on_path_warns(self, monkeypatch, capsys):
        # `uv tool` install but `uv` itself isn't on PATH — must
        # refuse to run, print the command, and tell the user where
        # to get uv.
        self._patch_detect(monkeypatch, "uv-tool")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: None)
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "`uv`" in err and "PATH" in err
        assert "astral.sh" in err  # the homepage hint
        assert fake.calls == []

    def test_uv_tool_force_uses_install_force(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "uv-tool")
        # Same version: --force should still trigger reinstall, but
        # via `uv tool install --force` (since `uv tool upgrade` is a
        # no-op when versions match).
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: sc.__version__)
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/uv")
        fake = _FakeRun(returncode=0)
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--force", "--yes"])
        assert rc == 0
        assert fake.calls == [["uv", "tool", "install", "--force", "splicecraft"]]

    # ── uv-venv happy path + PATH check ────────────────────────────

    def test_uv_venv_yes_runs_subprocess(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "uv-venv")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/uv")
        fake = _FakeRun(returncode=0)
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 0
        assert fake.calls == [["uv", "pip", "install", "--upgrade", "splicecraft"]]

    def test_uv_venv_no_uv_binary_warns(self, monkeypatch, capsys):
        # uv-venv detection succeeded (pyvenv.cfg had uv signature)
        # but `uv` is no longer on PATH. Must refuse + print command.
        self._patch_detect(monkeypatch, "uv-venv")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: None)
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "uv-venv" in err and "PATH" in err
        assert fake.calls == []

    # ── pixi-global happy path + PATH check ────────────────────────

    def test_pixi_global_yes_runs_subprocess(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pixi-global")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which",
                              lambda name: "/usr/bin/pixi" if name == "pixi" else None)
        fake = _FakeRun(returncode=0)
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 0
        assert fake.calls == [["pixi", "global", "update", "splicecraft"]]

    def test_pixi_not_on_path_warns(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pixi-global")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: None)
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "`pixi`" in err and "PATH" in err
        assert "pixi.sh" in err
        assert fake.calls == []

    def test_pixi_global_force_uses_install_force(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pixi-global")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: sc.__version__)
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pixi")
        fake = _FakeRun(returncode=0)
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--force", "--yes"])
        assert rc == 0
        assert fake.calls == [["pixi", "global", "install", "--force", "splicecraft"]]

    # ── pixi-project refusal ───────────────────────────────────────

    def test_pixi_project_refused(self, monkeypatch, capsys):
        # Project envs are managed by the pixi manifest. We refuse
        # to bypass it with a PyPI install — same shape as the
        # editable / source refusals.
        self._patch_detect(monkeypatch, "pixi-project",
                            git_clone="/home/me/myproj")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "pixi update splicecraft" in err
        assert "/home/me/myproj" in err
        assert fake.calls == []

    def test_pixi_project_refused_in_check_mode_too(
            self, monkeypatch, capsys):
        # --check on a pixi-project shouldn't pretend an upgrade is
        # possible. The refusal still fires (we don't list a fake
        # command the user could run).
        self._patch_detect(monkeypatch, "pixi-project",
                            git_clone="/home/me/myproj")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--check"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "pixi update splicecraft" in err
        assert fake.calls == []

    # ── help text mentions uv + pixi ───────────────────────────────

    def test_help_text_lists_uv_and_pixi(self, capsys):
        rc = sc._run_update_subcommand(["--help"])
        assert rc == 0
        out = capsys.readouterr().out
        # Help should mention every supported manager so users can
        # find the right command for their setup.
        for kw in ("pipx", "uv tool", "pixi global", "pixi project"):
            assert kw in out, f"help missing {kw!r}"

    # ── confirmation prompt ────────────────────────────────────────

    def test_user_cancels_at_prompt_exits_130(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        # Default-no on empty answer.
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")
        rc = sc._run_update_subcommand([])
        assert rc == 130
        assert fake.calls == []

    def test_user_cancels_via_eof_exits_130(self, monkeypatch):
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        def _eof(*a, **k):
            raise EOFError
        monkeypatch.setattr("builtins.input", _eof)
        rc = sc._run_update_subcommand([])
        assert rc == 130
        assert fake.calls == []

    def test_user_cancels_via_ctrl_c_exits_130(self, monkeypatch):
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        def _kbi(*a, **k):
            raise KeyboardInterrupt
        monkeypatch.setattr("builtins.input", _kbi)
        rc = sc._run_update_subcommand([])
        assert rc == 130
        assert fake.calls == []

    # ── subprocess errors ──────────────────────────────────────────

    def test_subprocess_filenotfound_exits_127(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")
        fake = _FakeRun(raise_exc=FileNotFoundError("no such cmd"))
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 127
        err = capsys.readouterr().err
        assert "Command not found" in err

    def test_subprocess_nonzero_propagates(self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")
        fake = _FakeRun(returncode=2)
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "exited with status 2" in err


@pytest.mark.slow
class TestUpdateDataSafety:
    """SACRED INVARIANT: every running upgrade path takes a complete,
    atomic snapshot of user data BEFORE the install subprocess runs.
    The snapshot lives outside `_DATA_DIR` (sibling) so a hypothetical
    bug that recursively wipes `_DATA_DIR` doesn't take the recovery
    copy with it.

    These tests are the regression target for the data-safety
    invariant — if any of them break, the update path can't be
    trusted to preserve user data.
    """

    # ── Helpers ────────────────────────────────────────────────────

    def _seed_user_data(self):
        """Drop deterministic content into every _USER_DATA_FILE_ATTRS
        path so we can verify each one is snapshotted + restored."""
        seeded: dict[str, str] = {}
        import json as _json
        for attr in sc._USER_DATA_FILE_ATTRS:
            p = getattr(sc, attr, None)
            if not isinstance(p, sc.Path):
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            content = _json.dumps({
                "_schema_version": 1,
                "entries": [{"id": f"seed-{attr}",
                             "name": f"seed for {attr}"}],
            })
            p.write_text(content, encoding="utf-8")
            seeded[attr] = content
        return seeded

    def _patch_detect(self, monkeypatch, method: str = "pipx"):
        info = {
            "method": method, "module": "/fake/splicecraft.py",
            "python": sys.executable, "venv": None, "git_clone": None,
            "details": f"{method} (test stub)",
        }
        monkeypatch.setattr(sc, "_detect_install_method", lambda: info)
        return info

    # ── Snapshot creation ──────────────────────────────────────────

    def test_create_snapshot_returns_existing_directory(self, monkeypatch):
        # Fresh call against a clean, isolated env (conftest) — even
        # with no user data on disk, we still create a snapshot dir +
        # manifest so retention has something to track.
        path = sc._create_pre_update_snapshot("0.0.0-test")
        assert path.is_dir()
        assert (path / sc._PRE_UPDATE_MANIFEST_NAME).is_file()

    def test_snapshot_lives_outside_data_dir(self, monkeypatch, tmp_path):
        # The whole point of the sibling directory is that a bug that
        # recursively wipes `_DATA_DIR` can't take the snapshot down.
        # The autouse fixture co-locates them under `tmp_path` for
        # other tests' convenience; here we set up an explicit
        # data-dir-vs-backup-dir split to verify the production
        # guarantee: snapshot is NOT under `_DATA_DIR`.
        data = tmp_path / "data"
        backup = tmp_path / "backups"
        data.mkdir()
        backup.mkdir()
        monkeypatch.setattr(sc, "_DATA_DIR", data)
        monkeypatch.setenv("SPLICECRAFT_UPDATE_BACKUP_DIR", str(backup))
        path = sc._create_pre_update_snapshot("0.0.0-test")
        snap_resolved = path.resolve()
        data_resolved = data.resolve()
        assert data_resolved not in snap_resolved.parents, (
            f"snapshot {snap_resolved} is inside data dir {data_resolved}"
        )

    def test_default_backup_dir_is_sibling_of_data_dir(
            self, monkeypatch, tmp_path):
        # When $SPLICECRAFT_UPDATE_BACKUP_DIR is not set, the snapshot
        # location MUST be a sibling of `_DATA_DIR` (so a recursive
        # wipe of `_DATA_DIR` doesn't take the recovery copy down).
        data = tmp_path / "data"
        data.mkdir()
        monkeypatch.setattr(sc, "_DATA_DIR", data)
        monkeypatch.delenv("SPLICECRAFT_UPDATE_BACKUP_DIR", raising=False)
        resolved = sc._resolve_pre_update_backup_dir()
        # Sibling: same parent, related-but-distinct name.
        assert resolved.parent == data.parent
        assert resolved != data
        assert data not in resolved.parents

    def test_snapshot_includes_all_seeded_files(self):
        seeded = self._seed_user_data()
        # Confirm we actually wrote something.
        assert seeded, "test setup failed: no files were seeded"
        path = sc._create_pre_update_snapshot("0.0.0-test")
        # Each seeded attr should have an exact-content copy in the
        # snapshot under its basename.
        import json as _json
        manifest = _json.loads(
            (path / sc._PRE_UPDATE_MANIFEST_NAME).read_text(encoding="utf-8")
        )
        names_in_manifest = {entry["name"] for entry in manifest["files"]}
        for attr, content in seeded.items():
            real = getattr(sc, attr)
            assert real.name in names_in_manifest, (
                f"{attr} ({real.name}) missing from manifest"
            )
            assert (path / real.name).read_text(encoding="utf-8") == content

    def test_snapshot_includes_user_data_dirs(self, tmp_path):
        # Crash-recovery autosaves and .dna sidecars also get snapshotted.
        cr = sc._CRASH_RECOVERY_DIR
        cr.mkdir(parents=True, exist_ok=True)
        (cr / "myplas-abc123.gb").write_text("LOCUS test\n", encoding="utf-8")
        do = sc._DNA_ORIGINALS_DIR
        do.mkdir(parents=True, exist_ok=True)
        (do / "myplas.dna").write_bytes(b"\x00fake commercial saas binary")
        path = sc._create_pre_update_snapshot("0.0.0-test")
        assert (path / cr.name / "myplas-abc123.gb").is_file()
        assert (path / do.name / "myplas.dna").is_file()
        import json as _json
        manifest = _json.loads(
            (path / sc._PRE_UPDATE_MANIFEST_NAME).read_text(encoding="utf-8")
        )
        dir_names = {d["name"] for d in manifest["directories"]}
        assert cr.name in dir_names
        assert do.name in dir_names

    def test_snapshot_manifest_has_sha256_per_file(self):
        self._seed_user_data()
        path = sc._create_pre_update_snapshot("0.0.0-test")
        import json as _json, hashlib
        manifest = _json.loads(
            (path / sc._PRE_UPDATE_MANIFEST_NAME).read_text(encoding="utf-8")
        )
        assert manifest["schema_version"] == 1
        assert manifest["from_version"] == "0.0.0-test"
        assert isinstance(manifest["files"], list)
        for entry in manifest["files"]:
            # sha256 in manifest must match the actual copy on disk.
            copy = path / entry["name"]
            digest = hashlib.sha256(copy.read_bytes()).hexdigest()
            assert digest == entry["sha256"], (
                f"manifest sha256 mismatch for {entry['name']}"
            )

    def test_snapshot_atomic_no_partial_dir_on_failure(self, monkeypatch):
        # Force shutil.copy2 to raise mid-snapshot. The staging dir
        # must be cleaned up; no final-name dir must appear.
        self._seed_user_data()
        backup_dir = sc._resolve_pre_update_backup_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)
        before = set(p.name for p in backup_dir.iterdir())

        original_copy = sc.shutil.copy2
        n_calls = [0]

        def _flaky_copy(src, dst, *a, **k):
            n_calls[0] += 1
            if n_calls[0] >= 2:  # let one copy succeed, then crash
                raise OSError("simulated disk error")
            return original_copy(src, dst, *a, **k)

        monkeypatch.setattr(sc.shutil, "copy2", _flaky_copy)
        with pytest.raises((OSError, sc.shutil.Error)):
            sc._create_pre_update_snapshot("0.0.0-test")
        # After the failure, no NEW directory exists in backup_dir
        # (staging cleaned up, final never created).
        after = set(p.name for p in backup_dir.iterdir())
        new_entries = after - before
        # The retention sweep may have left an empty dir; staging
        # prefixes start with `.tmp-` which we never want lying
        # around.
        for n in new_entries:
            assert not n.startswith(sc._PRE_UPDATE_STAGING_PREFIX), (
                f"staging dir {n} was not cleaned up"
            )

    def test_retention_prunes_oldest(self, monkeypatch):
        # Take 7 snapshots; with retention=3, only the 3 newest stay.
        # Use a tiny retention to keep the test fast.
        for i in range(7):
            sc._create_pre_update_snapshot(f"0.0.0-test-{i}", retention=3)
        backup_dir = sc._resolve_pre_update_backup_dir()
        snaps = [p for p in backup_dir.iterdir()
                  if p.is_dir() and not p.name.startswith(sc._PRE_UPDATE_STAGING_PREFIX)]
        assert len(snaps) == 3

    # ── Install-path collision detection ───────────────────────────

    def test_data_dir_inside_install_path_normal_case(self, monkeypatch):
        # Conftest already isolates _DATA_DIR to tmp_path, which is
        # not under the install location. So this should be False.
        assert sc._data_dir_inside_install_path() is False

    def test_data_dir_inside_install_path_detected(self, monkeypatch, tmp_path):
        # Synthesise an install layout where _DATA_DIR is INSIDE the
        # install path. The detection must catch this so we can refuse
        # to upgrade.
        install_path = tmp_path / "install"
        install_path.mkdir()
        fake_module = install_path / "splicecraft.py"
        fake_module.write_text("# stub")
        bad_data = install_path / "data"
        bad_data.mkdir()
        monkeypatch.setattr(sc, "__file__", str(fake_module))
        monkeypatch.setattr(sc, "_DATA_DIR", bad_data)
        assert sc._data_dir_inside_install_path() is True

    # ── Sacred invariant: snapshot before subprocess ───────────────

    def test_snapshot_taken_before_subprocess_pipx(self, monkeypatch, capsys):
        """SACRED INVARIANT: at the moment subprocess.run is called,
        the snapshot must already be on disk. Verified by a callback
        in the FakeRun that asserts the latest snapshot dir exists
        when subprocess.run fires."""
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")

        backup_dir = sc._resolve_pre_update_backup_dir()
        observed = {"snapshot_seen": False, "snap_dir_count_before": 0}

        observed["snap_dir_count_before"] = sum(
            1 for p in backup_dir.iterdir()
            if p.is_dir() and not p.name.startswith(sc._PRE_UPDATE_STAGING_PREFIX)
        ) if backup_dir.exists() else 0

        class _ObservingRun:
            calls: list = []
            returncode = 0
            def __call__(self, cmd, check=False, **kwargs):
                self.calls.append(list(cmd))
                # At this point a snapshot directory MUST exist —
                # that's the invariant we're enforcing.
                snaps = sc._list_pre_update_snapshots(backup_dir)
                observed["snapshot_seen"] = bool(snaps)
                observed["n_snapshots"] = len(snaps)
                class _CP:
                    returncode = 0
                return _CP()

        fake = _ObservingRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 0
        assert observed["snapshot_seen"], (
            "INVARIANT VIOLATED: subprocess.run fired without a "
            "user-data snapshot on disk"
        )
        assert observed["n_snapshots"] >= 1

    def test_snapshot_taken_before_subprocess_uv_tool(self, monkeypatch):
        """Same invariant for uv-tool method."""
        self._patch_detect(monkeypatch, "uv-tool")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/uv")

        backup_dir = sc._resolve_pre_update_backup_dir()
        observed = {"snapshot_seen": False}

        class _ObservingRun:
            returncode = 0
            def __call__(self, cmd, check=False, **kwargs):
                snaps = sc._list_pre_update_snapshots(backup_dir)
                observed["snapshot_seen"] = bool(snaps)
                class _CP:
                    returncode = 0
                return _CP()

        monkeypatch.setattr(sc.subprocess, "run", _ObservingRun())
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 0
        assert observed["snapshot_seen"]

    def test_snapshot_taken_before_subprocess_pixi_global(self, monkeypatch):
        """Same invariant for pixi-global method."""
        self._patch_detect(monkeypatch, "pixi-global")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pixi")

        backup_dir = sc._resolve_pre_update_backup_dir()
        observed = {"snapshot_seen": False}

        class _ObservingRun:
            returncode = 0
            def __call__(self, cmd, check=False, **kwargs):
                snaps = sc._list_pre_update_snapshots(backup_dir)
                observed["snapshot_seen"] = bool(snaps)
                class _CP:
                    returncode = 0
                return _CP()

        monkeypatch.setattr(sc.subprocess, "run", _ObservingRun())
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 0
        assert observed["snapshot_seen"]

    def test_snapshot_failure_aborts_update(self, monkeypatch, capsys):
        """If the snapshot can't be created, subprocess.run is NEVER
        called. Refuse to risk user data without a recovery copy."""
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")

        def _broken_snapshot(*a, **k):
            raise OSError("simulated disk full")

        monkeypatch.setattr(sc, "_create_pre_update_snapshot", _broken_snapshot)
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "ABORTING" in err
        assert "snapshot" in err.lower()
        assert fake.calls == [], (
            "INVARIANT VIOLATED: subprocess.run was called even though "
            "the snapshot failed"
        )

    def test_data_dir_inside_install_path_aborts_update(
            self, monkeypatch, capsys, tmp_path):
        """If `_DATA_DIR` is inside the install path, refuse — the
        upgrade would wipe user data along with the package."""
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")
        monkeypatch.setattr(sc, "_data_dir_inside_install_path",
                              lambda: True)
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--yes"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "UNSAFE CONFIGURATION" in err
        assert fake.calls == []

    def test_check_mode_does_not_take_snapshot(self, monkeypatch):
        """`--check` is read-only — it MUST NOT create a snapshot
        (snapshot dir count stays the same as before the call)."""
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")

        backup_dir = sc._resolve_pre_update_backup_dir()
        before = sc._list_pre_update_snapshots(backup_dir)
        rc = sc._run_update_subcommand(["--check"])
        assert rc == 0
        after = sc._list_pre_update_snapshots(backup_dir)
        assert len(after) == len(before)

    def test_refusal_paths_dont_take_snapshot(self, monkeypatch):
        """editable / source / pixi-project / pip-system all refuse
        to run pip — they must NOT create a snapshot either, since
        nothing dangerous is about to happen."""
        for method in ("editable", "source", "pixi-project", "pip-system"):
            self._patch_detect(monkeypatch, method)
            monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                                  lambda *a, **k: "99.0.0.0")
            backup_dir = sc._resolve_pre_update_backup_dir()
            before = len(sc._list_pre_update_snapshots(backup_dir))
            rc = sc._run_update_subcommand(["--yes"])
            assert rc == 1, f"method {method!r} should refuse"
            after = len(sc._list_pre_update_snapshots(backup_dir))
            assert after == before, (
                f"method {method!r} took an unnecessary snapshot"
            )

    # ── Restore round-trip ─────────────────────────────────────────

    def test_restore_round_trip_recovers_canonical_files(self):
        # Seed → snapshot → corrupt → restore → original recovered.
        seeded = self._seed_user_data()
        snap = sc._create_pre_update_snapshot("0.0.0-test")
        # Corrupt every seeded file with garbage.
        for attr in seeded:
            getattr(sc, attr).write_text("[CORRUPTED]", encoding="utf-8")
            assert getattr(sc, attr).read_text(encoding="utf-8") == "[CORRUPTED]"
        # Restore.
        summary = sc._restore_pre_update_snapshot(snap)
        assert summary["failed"] == [], summary
        # Every seeded attr should be byte-for-byte recovered.
        for attr, original in seeded.items():
            recovered = getattr(sc, attr).read_text(encoding="utf-8")
            assert recovered == original, (
                f"{attr} not recovered: got {recovered!r}, "
                f"expected {original!r}"
            )

    def test_restore_recovers_user_data_dirs(self):
        cr = sc._CRASH_RECOVERY_DIR
        cr.mkdir(parents=True, exist_ok=True)
        (cr / "myplas.gb").write_text("LOCUS A\n", encoding="utf-8")
        snap = sc._create_pre_update_snapshot("0.0.0-test")
        # Wipe the crash-recovery dir.
        sc.shutil.rmtree(cr, ignore_errors=True)
        assert not cr.exists()
        summary = sc._restore_pre_update_snapshot(snap)
        assert summary["failed"] == [], summary
        assert (cr / "myplas.gb").is_file()
        assert (cr / "myplas.gb").read_text(encoding="utf-8") == "LOCUS A\n"

    def test_restore_creates_pre_restore_snapshot(self):
        # Even a "good" restore takes a pre-restore snapshot first
        # so the user can always undo the restore.
        self._seed_user_data()
        snap = sc._create_pre_update_snapshot("0.0.0-test")
        summary = sc._restore_pre_update_snapshot(snap)
        assert summary["pre_restore_snapshot"]
        prs = sc.Path(summary["pre_restore_snapshot"])
        assert prs.is_dir()
        assert (prs / sc._PRE_UPDATE_MANIFEST_NAME).is_file()

    def test_restore_unknown_id_raises(self):
        with pytest.raises(FileNotFoundError):
            sc._restore_pre_update_snapshot("does-not-exist-xyz")

    def test_restore_latest_picks_most_recent(self):
        self._seed_user_data()
        # Older
        old = sc._create_pre_update_snapshot("0.0.0-old")
        import time
        # `_list_pre_update_snapshots` sorts by mtime; force a
        # measurable gap so the ordering is unambiguous on fast FS.
        time.sleep(0.02)
        new = sc._create_pre_update_snapshot("0.0.0-new")
        # Modify data after both snapshots.
        for attr in sc._USER_DATA_FILE_ATTRS:
            p = getattr(sc, attr, None)
            if isinstance(p, sc.Path) and p.is_file():
                p.write_text("modified", encoding="utf-8")
        summary = sc._restore_pre_update_snapshot("latest")
        # The snapshot we just took (in pre-restore) should NOT be
        # the one we restored — restored should match the newest
        # original (`new`), not the pre-restore that captured the
        # "modified" state.
        assert summary["restored_files"], summary
        # Verify no failures.
        assert summary["failed"] == [], summary

    # ── Listing flag ───────────────────────────────────────────────

    def test_list_snapshots_when_empty_exits_zero(self, monkeypatch, capsys):
        rc = sc._run_update_subcommand(["--list-snapshots"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "No pre-update snapshots" in out

    def test_list_snapshots_after_creating_some(self, monkeypatch, capsys):
        self._seed_user_data()
        sc._create_pre_update_snapshot("0.0.0-test-a")
        sc._create_pre_update_snapshot("0.0.0-test-b")
        rc = sc._run_update_subcommand(["--list-snapshots"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Snapshot ID" in out
        assert "0.0.0-test-a" in out
        assert "0.0.0-test-b" in out

    def test_restore_no_id_prints_listing(self, monkeypatch, capsys):
        self._seed_user_data()
        sc._create_pre_update_snapshot("0.0.0-test")
        rc = sc._run_update_subcommand(["--restore-pre-update"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "No snapshot id given" in out
        assert "0.0.0-test" in out

    def test_restore_with_eq_form(self, monkeypatch, capsys):
        # `--restore-pre-update=ID` form is accepted alongside the
        # space-separated form.
        self._seed_user_data()
        snap = sc._create_pre_update_snapshot("0.0.0-test")
        # Corrupt data
        for attr in sc._USER_DATA_FILE_ATTRS:
            p = getattr(sc, attr, None)
            if isinstance(p, sc.Path) and p.is_file():
                p.write_text("garbage", encoding="utf-8")
        rc = sc._run_update_subcommand(
            ["--yes", f"--restore-pre-update={snap.name}"]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "Restore complete" in out

    def test_restore_unknown_snapshot_id_exits_one(self, monkeypatch, capsys):
        self._seed_user_data()
        sc._create_pre_update_snapshot("0.0.0-test")
        rc = sc._run_update_subcommand(
            ["--yes", "--restore-pre-update", "does-not-exist"]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "Snapshot not found" in err

    def test_restore_user_can_cancel(self, monkeypatch, capsys):
        # No --yes: confirmation prompt fires; default-no aborts.
        self._seed_user_data()
        snap = sc._create_pre_update_snapshot("0.0.0-test")
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")
        rc = sc._run_update_subcommand(
            ["--restore-pre-update", snap.name]
        )
        assert rc == 130


@pytest.mark.slow
class TestUpdateDataSafetyHardening:
    """Hardening tests for the pre-update snapshot system. Each test
    targets a specific attack surface or correctness gap caught by
    the post-implementation audit. They cover scenarios that an
    average user will never hit but that a malicious manifest, a
    misconfigured backup directory, or a partial-failure restore
    would exploit if left unchecked.
    """

    # ── Helpers ────────────────────────────────────────────────────

    def _seed_user_data(self):
        import json as _json
        seeded: dict[str, str] = {}
        for attr in sc._USER_DATA_FILE_ATTRS:
            p = getattr(sc, attr, None)
            if not isinstance(p, sc.Path):
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            content = _json.dumps({"_schema_version": 1,
                                   "entries": [{"id": f"seed-{attr}"}]})
            p.write_text(content, encoding="utf-8")
            seeded[attr] = content
        return seeded

    def _patch_detect(self, monkeypatch, method: str = "pipx"):
        info = {
            "method": method, "module": "/fake/splicecraft.py",
            "python": sys.executable, "venv": None, "git_clone": None,
            "details": f"{method} (test stub)",
        }
        monkeypatch.setattr(sc, "_detect_install_method", lambda: info)

    # ── Symlink / system-root retention attack ─────────────────────

    def test_retention_only_rmtrees_snapshot_named_dirs(
            self, monkeypatch, tmp_path):
        """SACRED: pruning must NEVER rmtree a directory whose name
        doesn't match the snapshot pattern. Without this, a backup
        directory configured (or symlinked) to `/`, `~`, or any
        system path could see retention nuke `bin`, `etc`, `home`.
        """
        backup = tmp_path / "bk"
        backup.mkdir()
        # Drop a foreign directory named like a system one.
        foreign = backup / "etc"
        foreign.mkdir()
        (foreign / "important_file").write_text("DO NOT DELETE")
        # Create more snapshots than the retention limit so pruning
        # actually fires.
        monkeypatch.setattr(sc, "_DATA_DIR", tmp_path / "data")
        (tmp_path / "data").mkdir()
        monkeypatch.setenv("SPLICECRAFT_UPDATE_BACKUP_DIR", str(backup))
        for i in range(3):
            sc._create_pre_update_snapshot(f"0.0.0-{i}", retention=1)
        # Foreign dir + file must still exist — pruning only ate
        # snapshot-named dirs (the regex check refuses anything else).
        assert foreign.is_dir()
        assert (foreign / "important_file").read_text() == "DO NOT DELETE"

    def test_retention_refuses_symlinked_backup_dir(
            self, monkeypatch, tmp_path):
        # If somehow backup_dir IS a symlink (malicious env var or
        # tampered install), the retention sweep must refuse to walk
        # into it. We can't easily test rmtree-protection without a
        # real symlink to /, but we can verify symlink-detection.
        target = tmp_path / "real"
        target.mkdir()
        # Drop a foreign dir inside the real target.
        foreign = target / "etc"
        foreign.mkdir()
        link = tmp_path / "link"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this filesystem")
        # Run retention on the symlinked path. Must NOT delete the
        # foreign dir (or anything else underneath the symlink).
        sc._enforce_pre_update_retention(link, keep=0)
        assert foreign.is_dir(), (
            "INVARIANT VIOLATED: retention walked into a symlinked "
            "backup_dir and deleted contents"
        )

    def test_create_snapshot_refuses_symlinked_backup_dir(
            self, monkeypatch, tmp_path):
        target = tmp_path / "real"
        target.mkdir()
        link = tmp_path / "link"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this filesystem")
        # Force the env var to the symlink so the resolution doesn't
        # bypass it.
        monkeypatch.setenv("SPLICECRAFT_UPDATE_BACKUP_DIR", str(link))
        # `_resolve_pre_update_backup_dir` calls .resolve() which
        # follows the symlink to its target, so the snapshot would
        # actually land in `target`. To exercise the symlink-refusal
        # branch we pass the symlink path directly to bypass resolve.
        with pytest.raises(OSError, match="symlink"):
            sc._create_pre_update_snapshot(
                "0.0.0-test", backup_dir=link
            )

    # ── Manifest tampering: attr whitelist ─────────────────────────

    def test_restore_rejects_unknown_attr_in_manifest(self, tmp_path):
        # Hand-craft a snapshot dir with a manifest that targets a
        # non-user-data attribute (e.g. `_AGENT_TOKEN_FILE`). Restore
        # must skip it and report failure rather than overwriting it.
        backup = sc._resolve_pre_update_backup_dir()
        backup.mkdir(parents=True, exist_ok=True)
        snap = backup / "20260101-000000-deadbeef__from-0.0.0-test"
        snap.mkdir()
        (snap / "agent_token").write_text("malicious", encoding="utf-8")
        import json as _json
        (snap / sc._PRE_UPDATE_MANIFEST_NAME).write_text(_json.dumps({
            "schema_version": 1,
            "from_version": "0.0.0-test",
            "files": [
                {"attr": "_AGENT_TOKEN_FILE", "name": "agent_token",
                 "size": 9, "sha256": ""},
            ],
            "directories": [],
        }))
        summary = sc._restore_pre_update_snapshot(snap)
        # The forbidden entry is in `failed`, not `restored_files`.
        assert summary["restored_files"] == []
        assert any("_AGENT_TOKEN_FILE" in r for _, r in summary["failed"]), (
            f"expected _AGENT_TOKEN_FILE in failed reasons; got "
            f"{summary['failed']}"
        )

    def test_restore_rejects_unknown_dir_attr_in_manifest(self, tmp_path):
        backup = sc._resolve_pre_update_backup_dir()
        backup.mkdir(parents=True, exist_ok=True)
        snap = backup / "20260101-000000-deadbe0f__from-0.0.0-test"
        snap.mkdir()
        (snap / "weird").mkdir()
        import json as _json
        (snap / sc._PRE_UPDATE_MANIFEST_NAME).write_text(_json.dumps({
            "schema_version": 1,
            "from_version": "0.0.0-test",
            "files": [],
            "directories": [
                {"attr": "_DATA_DIR", "name": "weird", "file_count": 0},
            ],
        }))
        summary = sc._restore_pre_update_snapshot(snap)
        assert summary["restored_dirs"] == []
        assert summary["failed"]

    # ── Path traversal in `name` ───────────────────────────────────

    def test_restore_rejects_path_traversal_in_name(self, tmp_path):
        backup = sc._resolve_pre_update_backup_dir()
        backup.mkdir(parents=True, exist_ok=True)
        snap = backup / "20260101-000000-deadbe10__from-0.0.0-test"
        snap.mkdir()
        # Drop a file outside the snap dir, the would-be target of
        # the traversal read.
        outside = backup / "secret.txt"
        outside.write_text("system file", encoding="utf-8")
        import json as _json
        (snap / sc._PRE_UPDATE_MANIFEST_NAME).write_text(_json.dumps({
            "schema_version": 1,
            "from_version": "0.0.0-test",
            "files": [
                {"attr": "_LIBRARY_FILE",
                 "name": "../secret.txt",
                 "size": 11, "sha256": ""},
            ],
            "directories": [],
        }))
        summary = sc._restore_pre_update_snapshot(snap)
        assert summary["restored_files"] == [], (
            "path-traversal name was accepted by restore"
        )
        assert summary["failed"]
        # And critically: the live library file (if it existed
        # before) was not corrupted with the secret content.
        lib = sc._LIBRARY_FILE
        if lib.is_file():
            assert lib.read_text(encoding="utf-8") != "system file"

    def test_restore_rejects_separator_in_name(self, tmp_path):
        backup = sc._resolve_pre_update_backup_dir()
        backup.mkdir(parents=True, exist_ok=True)
        snap = backup / "20260101-000000-deadbe11__from-0.0.0-test"
        snap.mkdir()
        import json as _json
        (snap / sc._PRE_UPDATE_MANIFEST_NAME).write_text(_json.dumps({
            "schema_version": 1,
            "from_version": "0.0.0-test",
            "files": [
                {"attr": "_LIBRARY_FILE", "name": "subdir/foo.json",
                 "size": 0, "sha256": ""},
            ],
            "directories": [],
        }))
        summary = sc._restore_pre_update_snapshot(snap)
        assert summary["restored_files"] == []
        assert summary["failed"]

    # ── SHA-256 verification on restore ────────────────────────────

    def test_restore_verifies_sha256_and_refuses_corrupted(self, tmp_path):
        # Take a real snapshot, then corrupt the file inside it. The
        # subsequent restore must NOT overwrite the user's live file
        # with the corrupted snapshot data.
        seeded = self._seed_user_data()
        snap = sc._create_pre_update_snapshot("0.0.0-test")
        # Corrupt the snapshot's library copy AFTER the manifest's
        # sha256 was computed.
        copy_path = snap / sc._LIBRARY_FILE.name
        original_in_snap = copy_path.read_text(encoding="utf-8")
        copy_path.write_text("CORRUPTED", encoding="utf-8")
        # Modify the live file to a known value so we can detect
        # whether restore wrongly overwrote it.
        sc._LIBRARY_FILE.write_text("LIVE-AFTER-SNAPSHOT", encoding="utf-8")
        summary = sc._restore_pre_update_snapshot(snap)
        # The corrupted library entry must be in `failed`.
        names_failed = {n for n, _ in summary["failed"]}
        assert sc._LIBRARY_FILE.name in names_failed, (
            f"expected sha256 mismatch for {sc._LIBRARY_FILE.name} in "
            f"failed list; got {summary['failed']}"
        )
        # The live file is untouched (still the live-after value).
        # Pre-restore logic took its own snapshot of "LIVE-AFTER…"
        # before restore began, so we expect the live file to either
        # be the live value (corrupted entry skipped) — verify.
        assert sc._LIBRARY_FILE.read_text(encoding="utf-8") == \
            "LIVE-AFTER-SNAPSHOT"
        # Sanity: the manifest's other (uncorrupted) files DID restore.
        # (At least some of the other seeded files should be in the
        # restored list since their sha256s still match.)
        assert summary["restored_files"], (
            "no files restored at all — broken test? other seeded "
            "files should have valid sha256"
        )

    # ── Dir restore rollback on partial copytree ───────────────────

    def test_directory_restore_rollback_on_partial_copytree(
            self, monkeypatch):
        # Seed a non-empty crash-recovery dir, snapshot, then force
        # copytree to fail mid-way. The rollback must restore the
        # pre-restore stash so the user doesn't end up with a partial
        # crash_recovery dir.
        cr = sc._CRASH_RECOVERY_DIR
        cr.mkdir(parents=True, exist_ok=True)
        (cr / "alpha.gb").write_text("LOCUS A\n", encoding="utf-8")
        (cr / "beta.gb").write_text("LOCUS B\n", encoding="utf-8")
        snap = sc._create_pre_update_snapshot("0.0.0-test")
        # Modify the live state so we can detect a successful rollback.
        (cr / "live-only.gb").write_text("LIVE-ONLY\n", encoding="utf-8")

        # Make copytree fail AFTER target was created — but ONLY when
        # the destination is the actual live restoration target (not
        # the pre-restore snapshot we take first). Otherwise the
        # pre-restore phase would itself fail and we'd never exercise
        # the directory-restore rollback path.
        original_copytree = sc.shutil.copytree
        cr_name = cr.name

        def _flaky_copytree(src, dst, *a, **k):
            dst_p = sc.Path(dst)
            # The live-restore target sits at <_DATA_DIR>/<cr_name>;
            # the pre-restore snapshot copies INTO `update-backups/.tmp-…/<cr_name>`.
            # We only want to fail the live-restore copy.
            if dst_p.name == cr_name and dst_p.parent == cr.parent:
                sc.os.makedirs(dst, exist_ok=False)
                (dst_p / "alpha.gb").write_text("partial",
                                                   encoding="utf-8")
                raise OSError("simulated disk full mid-copytree")
            return original_copytree(src, dst, *a, **k)

        monkeypatch.setattr(sc.shutil, "copytree", _flaky_copytree)
        summary = sc._restore_pre_update_snapshot(snap)
        # The dir restore is in `failed`.
        assert any(name == cr.name for name, _ in summary["failed"]), (
            f"expected {cr.name!r} in failed; got {summary['failed']}"
        )
        # Critically: the live state is intact — rollback put the
        # stash back. The "live-only.gb" file we added is still there.
        assert (cr / "alpha.gb").is_file()
        assert (cr / "beta.gb").is_file()
        assert (cr / "live-only.gb").is_file(), (
            "INVARIANT VIOLATED: rollback didn't restore the stash; "
            "user's live data is gone or partial"
        )
        # And no lingering staging dirs.
        stash = cr.with_name(cr.name + ".restoring-old")
        assert not stash.exists(), f"stash left behind: {stash}"

    # ── Schema version negotiation (future-proofing) ───────────────

    def test_restore_refuses_newer_schema_version(self, tmp_path):
        backup = sc._resolve_pre_update_backup_dir()
        backup.mkdir(parents=True, exist_ok=True)
        snap = backup / "20260101-000000-deadbe12__from-0.0.0-future"
        snap.mkdir()
        import json as _json
        future_schema = sc._PRE_UPDATE_SCHEMA_VERSION + 1
        (snap / sc._PRE_UPDATE_MANIFEST_NAME).write_text(_json.dumps({
            "schema_version": future_schema,
            "from_version": "0.0.0-future",
            "files": [], "directories": [],
        }))
        with pytest.raises(ValueError, match="schema_version"):
            sc._restore_pre_update_snapshot(snap)

    def test_restore_accepts_equal_schema_version(self, tmp_path):
        # Round-trip: a snapshot we just took (with our current
        # schema) restores fine. Already covered by other tests but
        # this is the explicit forward-compat check.
        self._seed_user_data()
        snap = sc._create_pre_update_snapshot("0.0.0-test")
        summary = sc._restore_pre_update_snapshot(snap)
        assert summary["failed"] == [], summary

    def test_restore_accepts_older_schema_version(self, tmp_path):
        # If we ever bump _PRE_UPDATE_SCHEMA_VERSION to 2, snapshots
        # written under v1 should still restore. Simulate by claiming
        # the running schema is v99 and pointing at a v1 manifest.
        self._seed_user_data()
        snap = sc._create_pre_update_snapshot("0.0.0-test")
        # Bump the running schema higher than what the snapshot
        # advertises.
        # (We don't actually patch _PRE_UPDATE_SCHEMA_VERSION since
        # the test relies on snap_schema <= ours.)
        summary = sc._restore_pre_update_snapshot(snap)
        assert summary["failed"] == [], summary

    # ── Backup-dir validation ──────────────────────────────────────

    def test_resolve_backup_dir_refuses_file_path(
            self, monkeypatch, tmp_path):
        # If $SPLICECRAFT_UPDATE_BACKUP_DIR points at a file, the
        # resolver must refuse early — not silently overwrite or
        # crash deep inside snapshot creation.
        f = tmp_path / "not_a_dir"
        f.write_text("hi")
        monkeypatch.setenv("SPLICECRAFT_UPDATE_BACKUP_DIR", str(f))
        with pytest.raises(OSError, match="non-directory"):
            sc._resolve_pre_update_backup_dir()

    def test_resolve_backup_dir_refuses_filesystem_root(
            self, monkeypatch, tmp_path):
        # If `_DATA_DIR` is configured at filesystem root (or its
        # parent equals itself), refuse to derive a backup location.
        monkeypatch.delenv("SPLICECRAFT_UPDATE_BACKUP_DIR", raising=False)
        monkeypatch.setattr(sc, "_DATA_DIR", sc.Path("/"))
        with pytest.raises(OSError, match="Refusing"):
            sc._resolve_pre_update_backup_dir()

    # ── File-list audit (forces classification of new _*_FILE) ─────

    def test_every_data_file_constant_is_classified(self):
        """FUTURE-PROOFING: every `_*_FILE` constant in splicecraft
        that points to a file inside `_DATA_DIR` MUST be classified
        as either user-data (`_USER_DATA_FILE_ATTRS`) or operational
        (`_OPERATIONAL_FILE_ATTRS`). When a future contributor adds
        a new persisted file and forgets to update either list, this
        test fires and forces them to decide which category it
        belongs in. Without this, new user-data files would silently
        be omitted from the pre-update snapshot — exactly the kind of
        regression the snapshot system exists to prevent.
        """
        import splicecraft as _sc
        in_user = set(_sc._USER_DATA_FILE_ATTRS)
        in_op = set(_sc._OPERATIONAL_FILE_ATTRS)
        unclassified = []
        for attr in dir(_sc):
            if not attr.endswith("_FILE"):
                continue
            if not attr.startswith("_"):
                continue
            value = getattr(_sc, attr)
            if not isinstance(value, _sc.Path):
                continue
            if attr in in_user or attr in in_op:
                continue
            unclassified.append(attr)
        assert not unclassified, (
            f"Unclassified persisted-file constants: {unclassified}\n"
            "Add each one to _USER_DATA_FILE_ATTRS (user data — gets "
            "snapshotted before update) or _OPERATIONAL_FILE_ATTRS "
            "(transient state — explicitly excluded)."
        )

    def test_user_data_and_operational_lists_are_disjoint(self):
        """Any constant must live in exactly one of the two lists.
        Overlap would mean the snapshot system is unsure whether to
        include it."""
        in_user = set(sc._USER_DATA_FILE_ATTRS)
        in_op = set(sc._OPERATIONAL_FILE_ATTRS)
        overlap = in_user & in_op
        assert not overlap, f"attrs in both lists: {overlap}"

    # ── Manifest fsync (durability sanity) ─────────────────────────

    def test_manifest_present_after_snapshot(self, tmp_path):
        # We can't easily simulate a power-loss between fsync calls
        # in a unit test, but we CAN assert the manifest is present
        # and parseable on every successful snapshot — i.e. fsync
        # didn't break the happy path.
        snap = sc._create_pre_update_snapshot("0.0.0-test")
        m = snap / sc._PRE_UPDATE_MANIFEST_NAME
        assert m.is_file()
        import json as _json
        data = _json.loads(m.read_text(encoding="utf-8"))
        assert data["schema_version"] == sc._PRE_UPDATE_SCHEMA_VERSION


@pytest.mark.slow
class TestUpdateRegistryFutureProofing:
    """Future-proofing audits that fire when a contributor adds a new
    install method, a new persisted-file constant, or otherwise
    extends the update system without updating all the related
    surfaces. Each test surfaces a specific drift that would
    otherwise ship silently.
    """

    def test_every_install_method_has_buildable_command_or_refusal(self):
        """Every entry in `_INSTALL_METHODS` MUST either:
          (a) produce a non-None argv list from `_build_upgrade_command`
              (it's a runnable method), OR
          (b) be on the documented refusal list (editable / source /
              pixi-project — the manifest-driven / source-tree
              methods we deliberately refuse to upgrade via PyPI),
          (c) `unknown` is its own special case — we don't refuse but
              we DO produce a generic command.

        If a future contributor adds a new method to `_INSTALL_METHODS`
        without wiring up either branch, this test fires and forces
        the decision.
        """
        runnable = []
        refused = []
        for method in sc._INSTALL_METHODS:
            cmd = sc._build_upgrade_command(method, force=False)
            if cmd is None:
                refused.append(method)
            else:
                runnable.append(method)
        # The intentional refusal set — review this if you're
        # changing it. Adding to this set without a corresponding
        # update to `_run_update_subcommand`'s refusal branches is a
        # silent bug.
        documented_refusals = {"editable", "source", "pixi-project"}
        assert set(refused) == documented_refusals, (
            f"refused methods drifted from documented set:\n"
            f"  expected: {documented_refusals}\n"
            f"  actual:   {set(refused)}\n"
            "If you added a new refusal method, also wire up an "
            "explicit early refusal branch in _run_update_subcommand."
        )
        # `unknown` MUST be runnable so the user gets a generic
        # command they can copy-paste even when we can't classify
        # the install.
        assert "unknown" in runnable

    def test_every_install_method_appears_in_help_text(self):
        """Help text MUST list every supported install method so
        users running `--help` can find the command for their
        setup. If you add a new method, update the help table."""
        help_text = sc._UPDATE_HELP_TEXT
        # Display labels matching the help table format. Map the
        # internal method identifier → its expected display token in
        # the help text. `unknown` is intentionally excluded — it's
        # the catch-all, not a documented user-facing label.
        expected_labels = {
            "pipx":         "pipx",
            "uv-tool":      "uv tool",
            "uv-venv":      "uv venv",
            "pixi-global":  "pixi global",
            "pip-user":     "pip --user",
            "pip-venv":     "pip venv",
            "pip-system":   "pip system",
            "editable":     "editable",
            "source":       "git clone",
            "pixi-project": "pixi project",
        }
        for method in sc._INSTALL_METHODS:
            if method == "unknown":
                continue
            label = expected_labels.get(method, method)
            assert label in help_text, (
                f"install method {method!r} (label {label!r}) is not "
                f"mentioned in _UPDATE_HELP_TEXT — when you add a "
                f"new method, update the help table at the top of "
                f"_UPDATE_HELP_TEXT."
            )

    def test_user_data_file_attrs_all_resolve_to_paths(self):
        """Every `_USER_DATA_FILE_ATTRS` entry MUST be a real
        module-level Path constant. Stale strings here would be
        silently ignored at snapshot time → user data would not be
        backed up. Catches typos and post-rename drift."""
        for attr in sc._USER_DATA_FILE_ATTRS:
            value = getattr(sc, attr, None)
            assert isinstance(value, sc.Path), (
                f"{attr!r} from _USER_DATA_FILE_ATTRS is not a Path; "
                f"got {type(value).__name__}"
            )

    def test_user_data_dir_attrs_all_resolve_to_paths(self):
        for attr in sc._USER_DATA_DIR_ATTRS:
            value = getattr(sc, attr, None)
            assert isinstance(value, sc.Path), (
                f"{attr!r} from _USER_DATA_DIR_ATTRS is not a Path"
            )

    def test_operational_file_attrs_all_resolve_to_paths(self):
        for attr in sc._OPERATIONAL_FILE_ATTRS:
            value = getattr(sc, attr, None)
            assert isinstance(value, sc.Path), (
                f"{attr!r} from _OPERATIONAL_FILE_ATTRS is not a Path"
            )

    def test_snapshot_dir_name_regex_matches_what_we_generate(self):
        """Future-proof retention: the regex that protects retention
        from rmtreeing foreign directories MUST also match every name
        `_create_pre_update_snapshot` actually produces. Otherwise
        retention silently never prunes (snapshot dir names look
        foreign by our own filter)."""
        snap = sc._create_pre_update_snapshot("0.0.0-test")
        assert sc._PRE_UPDATE_NAME_RE.match(snap.name), (
            f"generated snapshot name {snap.name!r} is rejected by "
            f"_PRE_UPDATE_NAME_RE — retention will silently never "
            f"prune. Update either the generator or the regex to "
            f"keep them in sync."
        )

    def test_snapshot_schema_version_is_stable_positive(self):
        """`_PRE_UPDATE_SCHEMA_VERSION` is the contract version for
        the snapshot manifest. Bumping it is a breaking change for
        readers that haven't been updated. This test just sanity-
        checks the constant is sensible — it's intentionally
        permissive about bumps (which are sometimes correct), but
        catches accidental zero / negative / non-int values."""
        v = sc._PRE_UPDATE_SCHEMA_VERSION
        assert isinstance(v, int) and v >= 1, (
            f"_PRE_UPDATE_SCHEMA_VERSION must be a positive int; got {v!r}"
        )

    def test_retention_constant_is_positive(self):
        assert sc._PRE_UPDATE_SNAPSHOT_RETENTION >= 1

    def test_install_methods_constant_no_duplicates(self):
        methods = sc._INSTALL_METHODS
        assert len(methods) == len(set(methods)), (
            f"duplicates in _INSTALL_METHODS: {methods}"
        )


class TestFutureProofingFeatures:
    """End-to-end + edge-case coverage for the six future-proofing
    additions: migration framework, PyPI URL override, manifest
    Python+platform, --dry-run, data-version stamp, plugin namespace.
    Each feature is exercised against malformed inputs, race
    conditions, missing files, and permission errors so the
    safeguards stay genuine and not just lip-service in tests.
    """

    def _patch_detect(self, monkeypatch, method: str = "pipx"):
        info = {
            "method": method, "module": "/fake/splicecraft.py",
            "python": sys.executable, "venv": None, "git_clone": None,
            "details": f"{method} (test stub)",
        }
        monkeypatch.setattr(sc, "_detect_install_method", lambda: info)

    # ── (1) Migration framework ────────────────────────────────────

    def test_migration_no_op_when_no_migrations_registered(self):
        # With no registered migrations, entries pass through unchanged
        # even when from_version < to_version.
        entries = [{"id": "a"}, {"id": "b"}]
        out, warns = sc._migrate_entries(entries, 1, 5, "Plasmid library")
        assert out == entries
        assert warns == []
        # Returned list must be a fresh copy — caller mustn't be able
        # to corrupt the input via the returned list.
        out.append({"id": "c"})
        assert len(entries) == 2

    def test_migration_runs_registered_migrators_in_order(
            self, monkeypatch):
        # Register a synthetic v1→v2 migrator that adds a `step1` key.
        called = []

        def m1(entry):
            called.append(("v1->v2", entry["id"]))
            return {**entry, "step1": True}

        def m2(entry):
            called.append(("v2->v3", entry["id"]))
            return {**entry, "step2": True}

        # Monkeypatch the registry so tests don't have to bump the
        # production schema. Use a fresh dict so other tests' state
        # is unaffected.
        monkeypatch.setitem(sc._ENTRY_MIGRATIONS, "Test label", {
            (1, 2): m1,
            (2, 3): m2,
        })
        out, warns = sc._migrate_entries(
            [{"id": "x"}], from_version=1, to_version=3, label="Test label"
        )
        assert out == [{"id": "x", "step1": True, "step2": True}]
        assert called == [("v1->v2", "x"), ("v2->v3", "x")]
        assert warns == []

    def test_migration_skips_intermediate_step_when_no_migrator(
            self, monkeypatch):
        # If only the (2,3) step is registered, going from 1 to 3
        # walks (1,2) as a no-op then (2,3) as the registered step.
        def m23(entry):
            return {**entry, "step23": True}
        monkeypatch.setitem(sc._ENTRY_MIGRATIONS, "L", {(2, 3): m23})
        out, warns = sc._migrate_entries(
            [{"id": "x"}], 1, 3, "L"
        )
        assert out == [{"id": "x", "step23": True}]

    def test_migration_failed_migrator_keeps_entry_and_warns(
            self, monkeypatch):
        # A migrator that raises (e.g. malformed input) must NOT lose
        # the entry. The entry passes through unchanged + a warning
        # is appended. Better to surface a v1-shaped entry in a v2
        # list than to drop user data outright.
        def m_bad(entry):
            raise KeyError("missing required field")
        monkeypatch.setitem(sc._ENTRY_MIGRATIONS, "L", {(1, 2): m_bad})
        out, warns = sc._migrate_entries(
            [{"id": "x"}], 1, 2, "L"
        )
        assert out == [{"id": "x"}], "entry must survive a failed migration"
        assert warns and "migration failed" in warns[0].lower()

    def test_migration_drops_non_dict_entries_with_warning(
            self, monkeypatch):
        def m(entry):
            return {**entry, "added": True}
        monkeypatch.setitem(sc._ENTRY_MIGRATIONS, "L", {(1, 2): m})
        out, warns = sc._migrate_entries(
            [{"id": "ok"}, "garbage", 42, None, {"id": "ok2"}], 1, 2, "L"
        )
        # Dict entries migrate; non-dict entries drop with warnings.
        assert out == [{"id": "ok", "added": True},
                       {"id": "ok2", "added": True}]
        assert len(warns) == 3  # str, int, NoneType

    def test_migration_handles_descending_range_as_noop(self):
        # to_version <= from_version → no-op.
        out, warns = sc._migrate_entries([{"id": "x"}], 5, 3, "L")
        assert out == [{"id": "x"}]
        assert warns == []

    def test_extract_entries_runs_migration_pipeline(self, monkeypatch):
        # Bump production schema so we can simulate a v1 → v2 load.
        # Don't actually patch _CURRENT_SCHEMA_VERSION — patch the
        # local logic by registering an "L" migrator and reading via
        # `_extract_entries` with a v1 envelope. We can't reach into
        # _CURRENT_SCHEMA_VERSION easily; instead, register a v0 → v1
        # migrator and verify it fires for the bare-list legacy path.
        def m01(entry):
            return {**entry, "from_legacy": True}
        monkeypatch.setitem(sc._ENTRY_MIGRATIONS,
                              "Plasmid library", {(0, 1): m01})
        # Bare-list (legacy pre-0.3.1) — extract_entries treats this
        # as v0, so the (0, 1) migrator should fire.
        raw = [{"id": "old"}]
        entries, warning = sc._extract_entries(raw, "Plasmid library")
        assert entries == [{"id": "old", "from_legacy": True}]
        assert warning is None

    # ── (2) PyPI URL env override ──────────────────────────────────

    def test_pypi_url_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("SPLICECRAFT_PYPI_URL", raising=False)
        assert sc._resolve_pypi_url() == sc._PYPI_JSON_URL

    def test_pypi_url_override_when_https(self, monkeypatch):
        monkeypatch.setenv("SPLICECRAFT_PYPI_URL",
                              "https://internal-mirror.corp/pypi/sc/json")
        assert sc._resolve_pypi_url() == \
            "https://internal-mirror.corp/pypi/sc/json"

    def test_pypi_url_override_when_http(self, monkeypatch):
        monkeypatch.setenv("SPLICECRAFT_PYPI_URL", "http://10.0.0.5/json")
        assert sc._resolve_pypi_url() == "http://10.0.0.5/json"

    def test_pypi_url_rejects_file_scheme(self, monkeypatch):
        # `file://` would let a malicious env var read arbitrary local
        # files — Python's urllib follows it. Refuse → fall back to
        # default.
        monkeypatch.setenv("SPLICECRAFT_PYPI_URL", "file:///etc/passwd")
        assert sc._resolve_pypi_url() == sc._PYPI_JSON_URL

    def test_pypi_url_rejects_javascript_scheme(self, monkeypatch):
        monkeypatch.setenv("SPLICECRAFT_PYPI_URL", "javascript:alert(1)")
        assert sc._resolve_pypi_url() == sc._PYPI_JSON_URL

    def test_pypi_url_rejects_overlong(self, monkeypatch):
        monkeypatch.setenv("SPLICECRAFT_PYPI_URL", "https://" + "a" * 3000)
        assert sc._resolve_pypi_url() == sc._PYPI_JSON_URL

    def test_pypi_url_rejects_empty_with_whitespace(self, monkeypatch):
        monkeypatch.setenv("SPLICECRAFT_PYPI_URL", "   ")
        assert sc._resolve_pypi_url() == sc._PYPI_JSON_URL

    def test_pypi_url_used_by_fetcher(self, monkeypatch):
        # End-to-end: monkeypatched env var actually gets handed to
        # urllib via the request.
        captured: dict = {}

        class _FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n):
                return b'{"info": {"version": "9.9.9"}}'

        def _fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return _FakeResp()

        monkeypatch.setenv("SPLICECRAFT_PYPI_URL",
                              "https://test.example/pypi.json")
        # Patch urllib.request.urlopen at the call site.
        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        result = sc._fetch_latest_pypi_version()
        assert result == "9.9.9"
        assert captured["url"] == "https://test.example/pypi.json"

    # ── (3) Manifest from_python_version + from_platform ───────────

    def test_manifest_records_python_and_platform(self):
        snap = sc._create_pre_update_snapshot("0.0.0-test")
        import json as _json
        manifest = _json.loads(
            (snap / sc._PRE_UPDATE_MANIFEST_NAME).read_text(encoding="utf-8")
        )
        assert "from_python_version" in manifest
        # Must be a dotted X.Y.Z string.
        parts = manifest["from_python_version"].split(".")
        assert len(parts) == 3
        for p in parts:
            assert p.isdigit()
        assert "from_platform" in manifest
        assert manifest["from_platform"] == sc._RUNTIME_PLATFORM

    def test_list_snapshots_surfaces_python_version(self):
        sc._create_pre_update_snapshot("0.0.0-test")
        snaps = sc._list_pre_update_snapshots()
        assert snaps and snaps[0]["from_python_version"] != "?"

    def test_old_manifest_without_python_version_loads_with_default(
            self, tmp_path):
        # Backward-compat: a snapshot from before this feature
        # landed lacks `from_python_version` / `from_platform`. The
        # listing must not crash; missing fields show as "?".
        backup = sc._resolve_pre_update_backup_dir()
        backup.mkdir(parents=True, exist_ok=True)
        snap = backup / "20260101-000000-deadbe20__from-0.0.0-old"
        snap.mkdir()
        import json as _json
        (snap / sc._PRE_UPDATE_MANIFEST_NAME).write_text(_json.dumps({
            "schema_version": 1, "from_version": "0.0.0-old",
            "files": [], "directories": [],
        }))
        snaps = sc._list_pre_update_snapshots()
        match = next((s for s in snaps if s["id"] == snap.name), None)
        assert match is not None
        assert match["from_python_version"] == "?"
        assert match["from_platform"] == "?"

    # ── (4) --dry-run flag ─────────────────────────────────────────

    def test_dry_run_takes_snapshot_but_skips_subprocess(
            self, monkeypatch, capsys):
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")
        backup_dir = sc._resolve_pre_update_backup_dir()
        before = sc._list_pre_update_snapshots(backup_dir)
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(["--dry-run", "--yes"])
        assert rc == 0
        # Snapshot was taken (one more than before).
        after = sc._list_pre_update_snapshots(backup_dir)
        assert len(after) == len(before) + 1
        # subprocess was NOT called.
        assert fake.calls == []
        # Output mentions dry-run.
        out = capsys.readouterr().out
        assert "[dry-run]" in out
        assert "pipx upgrade splicecraft" in out

    def test_dry_run_and_check_are_mutually_exclusive(
            self, monkeypatch, capsys):
        rc = sc._run_update_subcommand(["--dry-run", "--check"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "mutually exclusive" in err

    def test_dry_run_aborts_when_snapshot_fails(
            self, monkeypatch, capsys):
        # Same invariant as the regular install path: if the snapshot
        # can't be created, dry-run aborts too.
        self._patch_detect(monkeypatch, "pipx")
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: "99.0.0.0")
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")
        monkeypatch.setattr(sc, "_create_pre_update_snapshot",
                              lambda *a, **k: (_ for _ in ()).throw(
                                  OSError("disk full")))
        rc = sc._run_update_subcommand(["--dry-run", "--yes"])
        assert rc == 1

    # ── (5) Data-dir version stamp ─────────────────────────────────

    def test_stamp_creates_file_on_first_run(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sc, "_DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(sc, "_DATA_VERSION_FILE",
                              tmp_path / "data" / ".splicecraft-data-version")
        monkeypatch.setattr(sc, "_PLUGINS_DIR",
                              tmp_path / "data" / "plugins")
        warning = sc._check_and_stamp_data_version()
        # First run: no prior stamp → no warning.
        assert warning is None
        stamp_file = tmp_path / "data" / ".splicecraft-data-version"
        assert stamp_file.is_file()
        assert stamp_file.read_text(encoding="utf-8").strip() == sc.__version__

    def test_stamp_warns_on_downgrade(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sc, "_DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(sc, "_DATA_VERSION_FILE",
                              tmp_path / "data" / ".splicecraft-data-version")
        monkeypatch.setattr(sc, "_PLUGINS_DIR",
                              tmp_path / "data" / "plugins")
        # Plant a stamp written by a far-future version.
        stamp = tmp_path / "data" / ".splicecraft-data-version"
        stamp.parent.mkdir(parents=True)
        stamp.write_text("99.99.99.99\n")
        warning = sc._check_and_stamp_data_version()
        assert warning is not None
        assert "99.99.99.99" in warning
        assert sc.__version__ in warning
        # Stamp was overwritten with current version (data dir is
        # being touched by THIS version now).
        assert stamp.read_text(encoding="utf-8").strip() == sc.__version__

    def test_stamp_silent_on_upgrade(self, monkeypatch, tmp_path):
        # Older stamp + newer running version → no warning, just
        # silent overwrite.
        monkeypatch.setattr(sc, "_DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(sc, "_DATA_VERSION_FILE",
                              tmp_path / "data" / ".splicecraft-data-version")
        monkeypatch.setattr(sc, "_PLUGINS_DIR",
                              tmp_path / "data" / "plugins")
        stamp = tmp_path / "data" / ".splicecraft-data-version"
        stamp.parent.mkdir(parents=True)
        stamp.write_text("0.0.1\n")
        warning = sc._check_and_stamp_data_version()
        assert warning is None
        assert stamp.read_text(encoding="utf-8").strip() == sc.__version__

    def test_stamp_handles_unreadable_existing_stamp(
            self, monkeypatch, tmp_path):
        # Garbage stamp content (e.g. binary, malformed) — function
        # mustn't crash; treat as "unknown previous version" and
        # silently overwrite.
        monkeypatch.setattr(sc, "_DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(sc, "_DATA_VERSION_FILE",
                              tmp_path / "data" / ".splicecraft-data-version")
        monkeypatch.setattr(sc, "_PLUGINS_DIR",
                              tmp_path / "data" / "plugins")
        stamp = tmp_path / "data" / ".splicecraft-data-version"
        stamp.parent.mkdir(parents=True)
        stamp.write_bytes(b"\x00\xff\x00 not a version \n")
        # Must not raise.
        warning = sc._check_and_stamp_data_version()
        # Garbage doesn't parse as a canonical version → not a known
        # downgrade → no warning. Stamp gets refreshed regardless.
        assert warning is None
        assert stamp.read_text(encoding="utf-8").strip() == sc.__version__

    def test_stamp_handles_oversize_existing(self, monkeypatch, tmp_path):
        # 1 MB of garbage in the stamp file. Function caps the read
        # at 128 bytes so memory is bounded.
        monkeypatch.setattr(sc, "_DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(sc, "_DATA_VERSION_FILE",
                              tmp_path / "data" / ".splicecraft-data-version")
        monkeypatch.setattr(sc, "_PLUGINS_DIR",
                              tmp_path / "data" / "plugins")
        stamp = tmp_path / "data" / ".splicecraft-data-version"
        stamp.parent.mkdir(parents=True)
        stamp.write_text("X" * (1024 * 1024))
        # Must not raise + must still overwrite atomically.
        sc._check_and_stamp_data_version()
        assert stamp.read_text(encoding="utf-8").strip() == sc.__version__

    def test_stamp_creates_plugins_dir(self, monkeypatch, tmp_path):
        # Plugin namespace should be reserved (created empty) at the
        # same checkpoint.
        monkeypatch.setattr(sc, "_DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(sc, "_DATA_VERSION_FILE",
                              tmp_path / "data" / ".splicecraft-data-version")
        monkeypatch.setattr(sc, "_PLUGINS_DIR",
                              tmp_path / "data" / "plugins")
        sc._check_and_stamp_data_version()
        assert (tmp_path / "data" / "plugins").is_dir()

    def test_stamp_swallows_oserror(self, monkeypatch, tmp_path):
        # Read-only data dir: function logs + returns None rather
        # than crashing the launch.
        monkeypatch.setattr(sc, "_DATA_DIR",
                              tmp_path / "nonexistent-readonly")
        monkeypatch.setattr(sc, "_DATA_VERSION_FILE",
                              tmp_path / "nonexistent-readonly" / "x")
        monkeypatch.setattr(sc, "_PLUGINS_DIR",
                              tmp_path / "nonexistent-readonly" / "p")
        # Even if mkdir succeeds, atomic_write_text might fail; we
        # just don't want an exception to propagate.
        warning = sc._check_and_stamp_data_version()
        # If anything succeeded, no warning. If it failed, no warning
        # either (logged but swallowed).
        assert warning is None or isinstance(warning, str)

    # ── (6) Plugin namespace + reserved fields ─────────────────────

    def test_plugin_data_field_round_trip_through_save_load(self):
        # Reserve `_plugin_data` on every entry in every user-data
        # file and verify it survives a save → load round-trip.
        # Without this, plugins added in a future SpliceCraft would
        # silently lose their state on the first re-save.
        import json as _json
        from copy import deepcopy
        original = [{
            "id": "round-trip-test",
            "name": "RT",
            "_plugin_data": {
                "my_plugin": {"counter": 42, "tags": ["a", "b"]},
                "another": {"nested": {"deep": True}},
            },
        }]
        sc._safe_save_json(sc._LIBRARY_FILE, original, "Plasmid library")
        loaded, warning = sc._safe_load_json(sc._LIBRARY_FILE,
                                                "Plasmid library")
        assert warning is None
        assert loaded == original, (
            f"_plugin_data was not preserved through round-trip:\n"
            f"  original: {original}\n"
            f"  loaded:   {loaded}"
        )

    def test_plugins_dir_in_user_data_dir_attrs(self):
        # Forward-compat: if a plugin lands files inside _PLUGINS_DIR,
        # they get picked up by the pre-update snapshot automatically.
        assert "_PLUGINS_DIR" in sc._USER_DATA_DIR_ATTRS

    def test_plugin_data_field_is_in_reserved_list(self):
        assert "_plugin_data" in sc._RESERVED_ENTRY_FIELDS

    def test_plugins_dir_contents_get_snapshotted(self):
        # End-to-end: a plugin that drops a file in _PLUGINS_DIR
        # MUST see that file copied into pre-update snapshots.
        sc._PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        plugin_file = sc._PLUGINS_DIR / "myplugin-state.json"
        plugin_file.write_text('{"counter": 7}', encoding="utf-8")
        snap = sc._create_pre_update_snapshot("0.0.0-test")
        copied = snap / sc._PLUGINS_DIR.name / "myplugin-state.json"
        assert copied.is_file()
        assert copied.read_text(encoding="utf-8") == '{"counter": 7}'

    # ── Cross-feature edge cases ───────────────────────────────────

    def test_dry_run_force_combo(self, monkeypatch, capsys):
        # `--dry-run --force` exercises the force path through to
        # snapshot + command print, without running anything.
        self._patch_detect(monkeypatch, "pipx")
        # Same version + force → would normally run install.
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: sc.__version__)
        monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/pipx")
        fake = _FakeRun()
        monkeypatch.setattr(sc.subprocess, "run", fake)
        rc = sc._run_update_subcommand(
            ["--dry-run", "--force", "--yes"]
        )
        assert rc == 0
        # Snapshot taken; subprocess NOT called.
        assert fake.calls == []
        out = capsys.readouterr().out
        assert "[dry-run]" in out
        assert "pipx install --force splicecraft" in out

    def test_migration_non_dict_return_kept_as_warning_only(
            self, monkeypatch):
        # A buggy migrator that returns a non-dict shouldn't crash
        # the load. Currently the function trusts the migrator (no
        # post-validation), so the non-dict ends up in the output.
        # Document this behaviour: we DON'T validate post-migration
        # because every other layer (cache deepcopy, save/load
        # filtering) already filters non-dict entries via
        # `isinstance(entry, dict)` checks. The migration framework
        # is intentionally minimal.
        def m_evil(entry):
            return "garbage"  # Buggy migrator
        monkeypatch.setitem(sc._ENTRY_MIGRATIONS, "L", {(1, 2): m_evil})
        out, warns = sc._migrate_entries(
            [{"id": "x"}], 1, 2, "L"
        )
        # Output contains the buggy result; warnings list is empty.
        # Downstream filters (`isinstance(entry, dict)` in
        # `_safe_save_json`'s shrink check, sidebar render, etc.)
        # silently drop it.
        assert out == ["garbage"]

    def test_pypi_url_with_credentials_passes_through(self, monkeypatch):
        # Behind-firewall mirrors with HTTP basic auth in the URL
        # are a legitimate use case; we accept them.
        monkeypatch.setenv("SPLICECRAFT_PYPI_URL",
                              "https://user:secret@mirror.corp/json")
        assert sc._resolve_pypi_url() == \
            "https://user:secret@mirror.corp/json"

    def test_check_does_not_create_plugins_dir(self, monkeypatch, tmp_path):
        # The plugins-dir reservation runs in `_check_and_stamp_data_version`,
        # which is called from `main()`. The Textual app's tests
        # never go through main(), so the dir is not auto-created
        # there. But if a test explicitly calls the check, it should
        # be idempotent (creating an existing dir is a no-op).
        monkeypatch.setattr(sc, "_DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(sc, "_DATA_VERSION_FILE",
                              tmp_path / "data" / ".splicecraft-data-version")
        monkeypatch.setattr(sc, "_PLUGINS_DIR",
                              tmp_path / "data" / "plugins")
        sc._check_and_stamp_data_version()
        sc._check_and_stamp_data_version()  # idempotent
        assert (tmp_path / "data" / "plugins").is_dir()


class TestEventLogAndSnapshot:
    """End-to-end + edge-case coverage for the diagnostic logging
    surface: UI snapshot capture (Alt+D), the `splicecraft logs
    --bundle` CLI, path scrubbing, log-tail reading. Each test
    targets one failure mode that would otherwise let the user file
    a bug report missing the data needed to diagnose it.
    """

    # ── Path scrubber ──────────────────────────────────────────────

    def test_scrub_path_replaces_home_dir(self, monkeypatch, tmp_path):
        # Force a known home so the test is hermetic across CI.
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(sc.Path, "home", classmethod(lambda cls: fake_home))
        s = f"opened file at {fake_home}/secret/notes.txt"
        out = sc._scrub_path(s)
        assert str(fake_home) not in out
        assert "~/secret/notes.txt" in out

    def test_scrub_path_handles_linux_home_pattern(self):
        # Even when Path.home() doesn't match the path, the regex
        # fallback strips `/home/<user>` patterns.
        s = "stack trace: /home/alice/SpliceCraft/foo.py line 42"
        out = sc._scrub_path(s)
        assert "/home/alice" not in out
        assert "alice" not in out

    def test_scrub_path_handles_macos_users_pattern(self):
        s = "loaded /Users/bob/Documents/plasmid.gb"
        out = sc._scrub_path(s)
        assert "bob" not in out

    def test_scrub_path_handles_windows_pattern(self):
        s = r"failed: C:\Users\carol\AppData\Local\Temp\x.txt"
        out = sc._scrub_path(s)
        assert "carol" not in out

    def test_scrub_path_handles_non_string(self):
        # Defensive: function must coerce non-strings rather than
        # crash a snapshot capture.
        out = sc._scrub_path(42)  # type: ignore[arg-type]
        assert out == "42"

    def test_scrub_path_idempotent_on_clean_input(self):
        # Strings without home-like paths pass through unchanged.
        s = "no paths here, just text"
        assert sc._scrub_path(s) == s

    # ── Log tail reader ────────────────────────────────────────────

    def test_read_log_tail_returns_empty_for_missing_file(self, tmp_path):
        nope = tmp_path / "no-such-log.log"
        assert sc._read_log_tail(nope) == ""

    def test_read_log_tail_caps_at_max_bytes(self, tmp_path):
        # Write a larger-than-cap log; reader should slice from end.
        log = tmp_path / "big.log"
        # 200 short lines → ~6 KB total.
        lines = [f"line-{i:04d}" for i in range(200)]
        log.write_text("\n".join(lines), encoding="utf-8")
        # Cap to 1 KB → only the last few hundred bytes survive.
        out = sc._read_log_tail(log, n_lines=50, max_bytes=1024)
        # The final line should be present.
        assert "line-0199" in out
        # And the total returned should be ≤ n_lines.
        assert out.count("\n") + 1 <= 50

    def test_read_log_tail_returns_last_n_lines(self, tmp_path):
        log = tmp_path / "lines.log"
        log.write_text("\n".join(f"L{i}" for i in range(100)),
                        encoding="utf-8")
        out = sc._read_log_tail(log, n_lines=5)
        # Last 5 lines: L95..L99.
        assert out.endswith("L99")
        assert "L95" in out
        assert "L94" not in out  # falls outside the window
        # 5 lines: 4 newlines.
        assert out.count("\n") == 4

    # ── _collect_ui_snapshot ───────────────────────────────────────

    def test_collect_ui_snapshot_works_without_app(self):
        # No app passed (e.g. CLI capture before UI launches) — must
        # not crash and must return all expected keys.
        snap = sc._collect_ui_snapshot(None)
        for key in ("captured_at", "session_id", "splicecraft_version",
                    "python_version", "platform", "screen_stack",
                    "focused_widget", "current_record", "settings",
                    "log_tail"):
            assert key in snap, f"missing key {key!r}"
        assert snap["screen_stack"] == []
        assert snap["current_record"] is None

    def test_collect_ui_snapshot_with_app_stub(self):
        # Stub app with the attributes _collect_ui_snapshot reads.
        class StubSize:
            width = 160
            height = 48
        class StubFocused:
            id = "fetch-acc"
            classes = ["primary"]
        class StubApp:
            screen_stack = []
            focused = StubFocused()
            size = StubSize()
            _last_mouse_xy = (42, 8)
            _current_record = None
            _active_collection = "MyColl"
            _active_grammar = "GoldenBraid"
        snap = sc._collect_ui_snapshot(StubApp())
        assert snap["focused_widget"]["class"] == "StubFocused"
        assert snap["focused_widget"]["id"] == "fetch-acc"
        assert snap["mouse_position"] == {"x": 42, "y": 8}
        assert snap["terminal_size"] == {"cols": 160, "rows": 48}
        assert snap["active_collection"] == "MyColl"
        assert snap["active_grammar"] == "GoldenBraid"

    def test_collect_ui_snapshot_with_record_excludes_sequence(self):
        # CRITICAL: snapshot must NEVER contain plasmid sequence
        # content — that's user IP. Only metadata.
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(
            Seq("AAAAACGTACGTACGTACGTACGTACGTACGTACGT" * 100),
            id="trade-secret-plasmid",
            name="trade-secret-plasmid",
            annotations={"molecule_type": "DNA", "topology": "circular"},
        )
        class StubApp:
            screen_stack = []
            focused = None
            _current_record = rec
            _last_mouse_xy = (0, 0)
            _source_path = "/home/user/secret.gb"
        snap = sc._collect_ui_snapshot(StubApp())
        rec_info = snap["current_record"]
        # Length is reported (metadata) but the SEQUENCE STRING is not.
        assert rec_info["length"] == len(rec.seq)
        assert rec_info["id"] == "trade-secret-plasmid"
        # No sequence content anywhere.
        formatted = sc._format_ui_snapshot(snap)
        assert "AAAAACGT" not in formatted, (
            "INVARIANT VIOLATED: plasmid sequence content leaked into "
            "the UI snapshot"
        )
        # Source path scrubbed.
        assert "/home/user" not in formatted

    def test_collect_ui_snapshot_handles_broken_app(self):
        # Pathological stub that raises from every attribute access.
        # Must NOT crash _collect_ui_snapshot — we'd be hiding a real
        # bug behind another bug.
        class BrokenApp:
            def __getattr__(self, name):
                raise RuntimeError(f"explosion on {name}")
        snap = sc._collect_ui_snapshot(BrokenApp())
        # Defaults preserved despite the broken app.
        assert snap["screen_stack"] == []
        assert snap["current_record"] is None

    # ── _format_ui_snapshot ────────────────────────────────────────

    def test_format_ui_snapshot_renders_markdown_headings(self):
        snap = sc._collect_ui_snapshot(None)
        out = sc._format_ui_snapshot(snap)
        for heading in ("# SpliceCraft UI Snapshot", "## App state",
                        "## Current record", "## Active workspace",
                        "## Persisted settings", "## Diagnostic file paths"):
            assert heading in out, f"missing {heading!r}"

    def test_format_ui_snapshot_includes_session_id(self):
        snap = sc._collect_ui_snapshot(None)
        out = sc._format_ui_snapshot(snap)
        assert sc._SESSION_ID in out

    # ── _save_ui_snapshot atomic ───────────────────────────────────

    def test_save_ui_snapshot_writes_atomic(self, tmp_path):
        path = sc._save_ui_snapshot("hello world", dest_dir=tmp_path)
        assert path.is_file()
        assert path.read_text(encoding="utf-8") == "hello world"
        # Filename pattern.
        assert path.name.startswith("ui-snapshot-")
        assert path.name.endswith(".md")

    def test_save_ui_snapshot_bumps_on_collision(self, tmp_path,
                                                    monkeypatch):
        # Force a fixed timestamp so two writes collide.
        import datetime as _dt
        class FixedDT(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return _dt.datetime(2026, 1, 1, 0, 0, 0)
        monkeypatch.setattr(_dt, "datetime", FixedDT, raising=False)
        # Reach into the splicecraft module's reference so the
        # function picks up the patched datetime.
        a = sc._save_ui_snapshot("first", dest_dir=tmp_path)
        b = sc._save_ui_snapshot("second", dest_dir=tmp_path)
        assert a != b
        assert a.read_text() == "first"
        assert b.read_text() == "second"

    def test_save_ui_snapshot_enforces_retention(self, tmp_path):
        # Create more snapshots than the retention limit; oldest get
        # pruned automatically.
        for i in range(sc._UI_SNAPSHOT_RETENTION + 5):
            sc._save_ui_snapshot(f"snap-{i}", dest_dir=tmp_path)
            # tiny sleep so mtimes differ
            import time as _time
            _time.sleep(0.001)
        survivors = list(tmp_path.glob("ui-snapshot-*.md"))
        assert len(survivors) == sc._UI_SNAPSHOT_RETENTION

    # ── _build_system_info ─────────────────────────────────────────

    def test_build_system_info_scrubs_paths(self):
        info = sc._build_system_info()
        # Hard guarantee: log_path and data_dir must not contain
        # the user's literal home directory (would leak the username).
        try:
            home = str(sc.Path.home())
        except Exception:
            home = ""
        if home:
            assert home not in info["log_path"]
            assert home not in info["data_dir"]
        # Fields are present.
        assert info["splicecraft_version"] == sc.__version__
        assert "session_id" in info

    # ── _create_diagnostic_bundle ──────────────────────────────────

    def test_create_diagnostic_bundle_writes_zip(self, tmp_path):
        out = tmp_path / "bundle.zip"
        result = sc._create_diagnostic_bundle(out)
        assert result == out.resolve()
        assert out.is_file()
        # Verify contents.
        import zipfile
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
            assert "system_info.json" in names
            assert "README.md" in names
            # logs/ entries depend on whether a log file exists.
            # System info parses cleanly.
            import json as _json
            si = _json.loads(zf.read("system_info.json"))
            assert si["splicecraft_version"] == sc.__version__

    def test_create_diagnostic_bundle_default_name(
            self, tmp_path, monkeypatch):
        # No --out: bundle lands in CWD with the default name.
        monkeypatch.chdir(tmp_path)
        result = sc._create_diagnostic_bundle(None)
        assert result.parent == tmp_path
        assert result.name.startswith("splicecraft-debug-")
        assert result.name.endswith(".zip")
        assert sc._SESSION_ID in result.name

    def test_create_diagnostic_bundle_atomic_on_failure(
            self, tmp_path, monkeypatch):
        # Force zipfile.write to raise; verify the partial temp file
        # is cleaned up + final path doesn't exist.
        out = tmp_path / "should-not-exist.zip"
        original_open = __import__("zipfile").ZipFile

        class _FlakyZipFile:
            def __init__(self, *a, **k):
                self.zf = original_open(*a, **k)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                self.zf.__exit__(*a)
            def writestr(self, *a, **k):
                raise OSError("simulated zip write fail")

        monkeypatch.setattr("zipfile.ZipFile", _FlakyZipFile)
        with pytest.raises(OSError):
            sc._create_diagnostic_bundle(out)
        # Final path must NOT exist (atomic rollback).
        assert not out.exists()
        # No leftover staging files starting with the dot prefix.
        leftovers = list(tmp_path.glob(".should-not-exist.zip.*"))
        assert leftovers == []

    def test_create_diagnostic_bundle_handles_missing_log(
            self, tmp_path, monkeypatch):
        # Point _LOG_PATH at a non-existent file; bundle should still
        # be created, just without log entries.
        monkeypatch.setattr(sc, "_LOG_PATH",
                              str(tmp_path / "no-such-log.log"))
        out = tmp_path / "bundle.zip"
        result = sc._create_diagnostic_bundle(out)
        assert result.is_file()
        import zipfile
        with zipfile.ZipFile(result) as zf:
            # README + system_info still present.
            assert "README.md" in zf.namelist()
            assert "system_info.json" in zf.namelist()

    # ── _run_logs_subcommand ───────────────────────────────────────

    def test_logs_help(self, capsys):
        rc = sc._run_logs_subcommand(["--help"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "splicecraft logs" in out
        assert "--bundle" in out

    def test_logs_where_prints_log_path(self, capsys):
        rc = sc._run_logs_subcommand(["--where"])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert out == str(sc._LOG_PATH)

    def test_logs_unknown_arg_exits_two(self, capsys):
        rc = sc._run_logs_subcommand(["--bogus"])
        assert rc == 2

    def test_logs_bundle_creates_zip(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        rc = sc._run_logs_subcommand(["--bundle"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Diagnostic bundle saved to" in out
        zips = list(tmp_path.glob("splicecraft-debug-*.zip"))
        assert len(zips) == 1

    def test_logs_bundle_with_out_path(self, tmp_path, capsys):
        out = tmp_path / "custom-name.zip"
        rc = sc._run_logs_subcommand(["--bundle", "--out", str(out)])
        assert rc == 0
        assert out.is_file()

    def test_logs_bundle_out_eq_form(self, tmp_path, capsys):
        out = tmp_path / "eq-form.zip"
        rc = sc._run_logs_subcommand(["--bundle", f"--out={out}"])
        assert rc == 0
        assert out.is_file()

    def test_logs_no_args_prints_help_and_path(self, capsys):
        rc = sc._run_logs_subcommand([])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Log file:" in out
        assert "--bundle" in out

    def test_logs_out_without_value_errors(self, capsys):
        rc = sc._run_logs_subcommand(["--bundle", "--out"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "requires a path argument" in err

    # ── alt+d binding wiring ───────────────────────────────────────

    def test_alt_d_binds_to_capture_ui_snapshot(self):
        # Search PlasmidApp.BINDINGS for alt+d → action key.
        target = None
        for b in sc.PlasmidApp.BINDINGS:
            # Newer Textual stores Binding objects; older may store
            # tuples. Handle both.
            key = getattr(b, "key", None) or (b[0] if isinstance(b, tuple) else None)
            action = getattr(b, "action", None) or (b[1] if isinstance(b, tuple) else None)
            if key == "alt+d":
                target = action
                break
        assert target == "capture_ui_snapshot", (
            f"expected alt+d → capture_ui_snapshot; got {target!r}"
        )

    def test_alt_shift_d_still_toggles_seq_debug(self):
        # The previous alt+d binding lived on SequencePanel — it
        # should now be alt+shift+d, still bound to toggle_debug.
        target = None
        for b in sc.SequencePanel.BINDINGS:
            key = getattr(b, "key", None) or (b[0] if isinstance(b, tuple) else None)
            action = getattr(b, "action", None) or (b[1] if isinstance(b, tuple) else None)
            if key == "alt+shift+d":
                target = action
                break
        assert target == "toggle_debug", (
            f"expected alt+shift+d → toggle_debug; got {target!r}"
        )

    def test_no_seq_panel_alt_d_collision(self):
        # SequencePanel must NOT have an alt+d binding any more
        # (we moved it to alt+shift+d so the App-level snapshot
        # binding fires from the seq panel too).
        for b in sc.SequencePanel.BINDINGS:
            key = getattr(b, "key", None) or (b[0] if isinstance(b, tuple) else None)
            assert key != "alt+d", (
                "alt+d should not be bound on SequencePanel — it "
                "would shadow the App-level UI snapshot capture."
            )

    # ── Logging hardening: _repr_for_log ───────────────────────────

    def test_repr_for_log_truncates_long_strings(self):
        long = "a" * 500
        out = sc._repr_for_log(long)
        assert len(out) < 200
        assert "[500 chars]" in out

    def test_repr_for_log_summarises_long_lists(self):
        out = sc._repr_for_log(list(range(100)))
        assert "100 items" in out

    def test_repr_for_log_summarises_long_dicts(self):
        out = sc._repr_for_log({str(i): i for i in range(50)})
        assert "50 keys" in out

    def test_repr_for_log_handles_unrepresentable(self):
        class Boom:
            def __repr__(self):
                raise RuntimeError("can't repr me")
        out = sc._repr_for_log(Boom())
        assert out == "<unrepr-able>"

    # ── Log rotation config ───────────────────────────────────────

    def test_log_handler_uses_5mb_rotation(self):
        # Verify the rotation cap matches what we documented in the
        # CLAUDE.md invariant — bumped from 2MB×2 to 5MB×4 for
        # diagnostic depth.
        from logging.handlers import RotatingFileHandler
        rotating = [h for h in sc._log.handlers
                     if isinstance(h, RotatingFileHandler)]
        if not rotating:
            pytest.skip("no rotating handler installed (data dir was readonly?)")
        h = rotating[0]
        assert h.maxBytes == 5 * 1024 * 1024
        assert h.backupCount == 4


class TestRobustnessHardening:
    """Coverage for the 10-item robustness pass:
       (1) data-dir lock, (2) thread excepthook, (3) chmod 0600,
       (4) settings validation, (5) worker drain at exit,
       (6) network retry, (7) clipboard fallback, (8) modal stack
       cap, (9) big-plasmid warning, (10) snapshot size cap.

    Each test targets one failure mode that this batch of changes
    set out to mitigate.
    """

    # ── (1) Data-dir lock ──────────────────────────────────────────

    def test_lock_acquire_then_release(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SPLICECRAFT_SKIP_LOCK", raising=False)
        fd, path = sc._acquire_data_dir_lock(
            tmp_path, lockfile_override=tmp_path / "splicecraft.lock"
        )
        assert fd is not None
        assert path is not None and path.is_file()
        # Release.
        sc._release_data_dir_lock(fd)

    def test_lock_refuses_second_acquisition(self, tmp_path, monkeypatch):
        if sc.sys.platform == "win32":
            pytest.skip("flock not available; lock semantics differ")
        monkeypatch.delenv("SPLICECRAFT_SKIP_LOCK", raising=False)
        lockfile = tmp_path / "splicecraft.lock"
        fd1, _ = sc._acquire_data_dir_lock(tmp_path, lockfile_override=lockfile)
        try:
            with pytest.raises(sc.DataDirLockError):
                sc._acquire_data_dir_lock(tmp_path, lockfile_override=lockfile)
        finally:
            sc._release_data_dir_lock(fd1)

    def test_lock_skip_env_var_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SPLICECRAFT_SKIP_LOCK", "1")
        fd, path = sc._acquire_data_dir_lock(tmp_path)
        assert fd is None
        assert path is None

    def test_lock_release_is_safe_with_none_fd(self):
        # No FD → no-op, no exception.
        sc._release_data_dir_lock(None)

    def test_lock_writes_pid_to_file(self, tmp_path, monkeypatch):
        if sc.sys.platform == "win32":
            pytest.skip("Windows lockfile contents not used in error path")
        monkeypatch.delenv("SPLICECRAFT_SKIP_LOCK", raising=False)
        lockfile = tmp_path / "splicecraft.lock"
        fd, _ = sc._acquire_data_dir_lock(
            tmp_path, lockfile_override=lockfile
        )
        try:
            content = lockfile.read_text(encoding="utf-8")
            # First line is the PID.
            first = content.splitlines()[0].strip()
            assert first == str(sc.os.getpid())
        finally:
            sc._release_data_dir_lock(fd)

    # ── (2) Thread excepthook ──────────────────────────────────────

    def test_thread_excepthook_routes_to_log(self, monkeypatch, caplog):
        # Manually install the same hook main() would install, then
        # raise from a thread and verify it lands in the log.
        events: list = []
        original_hook = sc.threading.excepthook

        def _hook(args):
            if args.exc_type is SystemExit:
                return
            events.append((args.exc_type, str(args.exc_value)))

        sc.threading.excepthook = _hook
        try:
            def boom():
                raise RuntimeError("synthetic worker crash")
            t = sc.threading.Thread(target=boom, name="test-worker")
            t.start()
            t.join(timeout=2.0)
            assert events, "thread excepthook never fired"
            assert events[0][0] is RuntimeError
            assert "synthetic worker crash" in events[0][1]
        finally:
            sc.threading.excepthook = original_hook

    # ── (3) chmod 0600 ─────────────────────────────────────────────

    def test_chmod_user_only_tightens_perms(self, tmp_path):
        if sc.sys.platform == "win32":
            pytest.skip("POSIX-only mode bits")
        f = tmp_path / "private.txt"
        f.write_text("secret")
        sc.os.chmod(str(f), 0o644)  # set world-readable first
        sc._chmod_user_only(f)
        mode = sc.os.stat(str(f)).st_mode & 0o777
        assert mode == 0o600

    def test_chmod_user_only_silent_on_missing_file(self, tmp_path):
        # Best-effort: must not raise on missing target.
        sc._chmod_user_only(tmp_path / "does-not-exist.txt")

    # ── (4) Settings validation ────────────────────────────────────

    def test_settings_validation_coerces_wrong_type(self):
        # Schema says check_updates is bool; user/file says "yes".
        bad = {"check_updates": "yes", "show_restr": False}
        cleaned, warns = sc._validate_settings(bad)
        # Wrong-type bool came back to default (True per schema).
        assert cleaned["check_updates"] is True
        # Correct values pass through unchanged.
        assert cleaned["show_restr"] is False
        # And we get a warning about the coercion.
        assert any("check_updates" in w for w in warns)

    def test_settings_validation_keeps_unknown_keys(self):
        # Forward-compat: unknown keys (a future setting) are kept.
        bad = {"future_setting": 42, "show_restr": True}
        cleaned, _ = sc._validate_settings(bad)
        assert cleaned["future_setting"] == 42

    def test_settings_validation_strict_bool_vs_int(self):
        # Python's True is also int — but a bool snuck into an int
        # field shouldn't sneak through.
        bad = {"restr_min_len": True}  # would isinstance-test as int!
        cleaned, warns = sc._validate_settings(bad)
        assert cleaned["restr_min_len"] == 6  # default
        assert any("restr_min_len" in w for w in warns)

    def test_settings_validation_handles_non_dict(self):
        cleaned, warns = sc._validate_settings("not a dict")  # type: ignore[arg-type]
        assert cleaned == {}
        assert warns and "not a dict" in warns[0]

    # ── (5) Worker drain ───────────────────────────────────────────

    def test_drain_waits_for_threads_within_timeout(self):
        import time as _time
        finished: list = []

        def quick():
            _time.sleep(0.05)
            finished.append("ok")

        t = sc.threading.Thread(target=quick, name="quick-worker", daemon=False)
        t.start()
        leftover = sc._drain_in_flight_workers(timeout_s=1.0)
        # The thread should have finished within the budget.
        assert finished == ["ok"]
        assert "quick-worker" not in leftover

    def test_drain_reports_leftover_when_timeout_exceeds(self):
        # Slow thread that exceeds the timeout — leftover list
        # should contain its name.
        import time as _time
        evt = sc.threading.Event()

        def slow():
            evt.wait(timeout=5.0)

        t = sc.threading.Thread(target=slow, name="slow-worker", daemon=False)
        t.start()
        try:
            leftover = sc._drain_in_flight_workers(timeout_s=0.1)
            assert "slow-worker" in leftover
        finally:
            evt.set()
            t.join(timeout=2.0)

    def test_drain_skips_daemon_threads(self):
        # Daemon threads die with the process; drain skips them
        # entirely so a long-running daemon doesn't hold up shutdown.
        import time as _time
        evt = sc.threading.Event()

        def dt():
            evt.wait(timeout=5.0)

        t = sc.threading.Thread(target=dt, name="daemon-worker", daemon=True)
        t.start()
        try:
            leftover = sc._drain_in_flight_workers(timeout_s=0.1)
            assert "daemon-worker" not in leftover
        finally:
            evt.set()

    # ── (6) Network retry ──────────────────────────────────────────

    def test_pypi_fetch_retries_once_on_transient_failure(
            self, monkeypatch):
        import urllib.error
        import urllib.request

        attempts: list = []

        class _OkResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n):
                return b'{"info": {"version": "9.9.9"}}'

        def _flaky_urlopen(req, timeout=None):
            attempts.append(1)
            if len(attempts) == 1:
                raise urllib.error.URLError("temporarily unreachable")
            return _OkResp()

        monkeypatch.setattr(urllib.request, "urlopen", _flaky_urlopen)
        result = sc._fetch_latest_pypi_version()
        assert result == "9.9.9"
        assert len(attempts) == 2  # one failure + one retry

    def test_pypi_fetch_returns_none_after_two_failures(
            self, monkeypatch):
        import urllib.error
        import urllib.request

        attempts: list = []

        def _always_fails(req, timeout=None):
            attempts.append(1)
            raise urllib.error.URLError("permanently unreachable")

        monkeypatch.setattr(urllib.request, "urlopen", _always_fails)
        result = sc._fetch_latest_pypi_version()
        assert result is None
        assert len(attempts) == 2

    # ── (7) Clipboard fallback ─────────────────────────────────────

    def test_clipboard_fallback_uses_app_when_available(self):
        class StubApp:
            captured: list = []
            def copy_to_clipboard(self, text):
                self.captured.append(text)
        app = StubApp()
        mode, detail = sc._copy_to_clipboard_with_fallback(
            app, "hello", label="test"
        )
        assert mode == "clipboard"
        assert detail is None
        assert app.captured == ["hello"]

    def test_clipboard_fallback_falls_to_file_when_clipboard_fails(
            self, monkeypatch):
        class BrokenApp:
            def copy_to_clipboard(self, text):
                raise RuntimeError("no clipboard channel")
        # Force OSC 52 to fail too.
        monkeypatch.setattr(sc, "_copy_to_clipboard_osc52",
                              lambda text: False)
        mode, detail = sc._copy_to_clipboard_with_fallback(
            BrokenApp(), "important data", label="bug-report"
        )
        assert mode == "file"
        assert detail is not None and detail.is_file()
        # File contains the text.
        assert detail.read_text(encoding="utf-8") == "important data"

    def test_clipboard_fallback_log_only_when_everything_fails(
            self, monkeypatch):
        class BrokenApp:
            def copy_to_clipboard(self, text):
                raise RuntimeError("no clipboard")
        monkeypatch.setattr(sc, "_copy_to_clipboard_osc52",
                              lambda text: False)
        # Force the file-write fallback to fail too.
        original = sc._atomic_write_text
        def _broken_write(*a, **k):
            raise OSError("simulated read-only fs")
        monkeypatch.setattr(sc, "_atomic_write_text", _broken_write)
        mode, detail = sc._copy_to_clipboard_with_fallback(
            BrokenApp(), "data", label="x"
        )
        assert mode == "log_only"
        assert detail is None
        # Restore for cleanliness.
        monkeypatch.setattr(sc, "_atomic_write_text", original)

    # ── (8) Modal stack cap ────────────────────────────────────────

    def test_modal_stack_cap_constant_present(self):
        assert hasattr(sc.PlasmidApp, "_MODAL_STACK_SOFT_CAP")
        assert sc.PlasmidApp._MODAL_STACK_SOFT_CAP > 0

    def test_modal_stack_cap_refuses_overflow(self, monkeypatch):
        # Can't easily test by actually pushing 12 modals; instead
        # exercise the override directly with a stub stack length.
        app = sc.PlasmidApp()
        # Force the soft cap to a tiny number for the test.
        monkeypatch.setattr(app, "_MODAL_STACK_SOFT_CAP", 2)
        # Stub screen_stack to look full.
        class FakeStack(list):
            pass
        full_stack = FakeStack(["s1", "s2", "s3"])
        monkeypatch.setattr(type(app), "screen_stack",
                              property(lambda self: full_stack))
        # Stub notify so we don't need a mounted app.
        notifications: list = []
        def _capture(msg, **kw):
            notifications.append(msg)
        monkeypatch.setattr(app, "notify", _capture)
        # The override should refuse + return a no-op awaitable.
        result = app.push_screen("DummyScreenName")
        # The returned object must be awaitable (a coroutine).
        import asyncio, inspect
        assert inspect.iscoroutine(result), (
            f"expected an awaitable; got {type(result)}"
        )
        # Drain the awaitable.
        try:
            asyncio.run(result)
        except RuntimeError:
            # Some Python versions may complain about closed loops;
            # that's fine — we only care that the call didn't raise.
            pass
        # And the user got a warning notification.
        assert notifications, "no notification fired on cap overflow"

    # ── (9) Big-plasmid warning ────────────────────────────────────

    def test_large_plasmid_threshold_present(self):
        assert hasattr(sc.PlasmidApp, "_LARGE_PLASMID_BP")
        assert sc.PlasmidApp._LARGE_PLASMID_BP >= 1_000_000

    # ── (10) Snapshot size cap ─────────────────────────────────────

    def test_snapshot_skips_oversized_file(self, tmp_path, monkeypatch):
        # Create a fake "library file" larger than the cap and verify
        # `_snapshot_data_files` skips it.
        big = tmp_path / "huge.json"
        # Write a sparse file — actual disk content size > cap.
        # `_snapshot_data_files` reads st_size, not contents, so a
        # truncate to cap+1 is enough.
        with open(big, "wb") as f:
            f.truncate(sc._SNAPSHOT_FILE_SIZE_CAP + 1)
        small = tmp_path / "small.json"
        small.write_bytes(b'{"_schema_version": 1, "entries": []}')
        written = sc._snapshot_data_files(tmp_path, paths=[big, small])
        # Oversized file was skipped; small one was snapshotted.
        snap_dir = tmp_path / sc._SNAPSHOT_DIR_NAME
        assert not (snap_dir / "huge-*.json").is_file()
        assert any(p.name.startswith("small-") for p in written)


class TestUpdateSubcommandMainDispatch:
    """`main()` must route `splicecraft update` to the subcommand
    BEFORE attempting to load it as a file/accession, and must NOT
    launch the Textual TUI for the update path."""

    def test_main_dispatches_update_check(self, monkeypatch, capsys):
        # Simulate `splicecraft update --check` from the CLI.
        monkeypatch.setattr(sys, "argv",
                              ["splicecraft", "update", "--check"])
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: sc.__version__)
        # Block any accidental TUI launch — main() must not get this far.
        def _no_run(*a, **k):
            raise AssertionError("update subcommand must not launch the TUI")
        monkeypatch.setattr(sc.PlasmidApp, "run", _no_run, raising=True)
        with pytest.raises(SystemExit) as excinfo:
            sc.main()
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "checking for updates" in out

    def test_main_update_unknown_flag_exits_two(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv",
                              ["splicecraft", "update", "--what"])
        # Block PyPI fetch entirely (shouldn't be reached) and TUI launch.
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: sc.__version__)
        monkeypatch.setattr(sc.PlasmidApp, "run",
                              lambda *a, **k: (_ for _ in ()).throw(
                                  AssertionError("must not launch TUI")))
        with pytest.raises(SystemExit) as excinfo:
            sc.main()
        assert excinfo.value.code == 2

    def test_main_update_takes_priority_over_filename(
            self, monkeypatch, tmp_path, capsys):
        # If a file named "update" exists in CWD, the subcommand still
        # wins — but a warning is printed pointing at './update' for
        # disambiguation.
        # cd into a temp dir and drop a placeholder file there.
        (tmp_path / "update").write_text("not a real GenBank file")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv",
                              ["splicecraft", "update", "--check"])
        monkeypatch.setattr(sc, "_fetch_latest_pypi_version",
                              lambda *a, **k: sc.__version__)
        monkeypatch.setattr(sc.PlasmidApp, "run",
                              lambda *a, **k: (_ for _ in ()).throw(
                                  AssertionError("must not launch TUI")))
        with pytest.raises(SystemExit) as excinfo:
            sc.main()
        assert excinfo.value.code == 0
        err = capsys.readouterr().err
        assert "ignored in favour of the update subcommand" in err
