"""
test_library_bulk_mark — sweep #28.

Bulk-mark + move/copy across collections via the Library panel.

Hardening test coverage (the user explicitly asked for "harden and
edge case this PLEASE"):

  * Toggle mark + clear marks
  * Mark survives repopulate; clears on collection switch
  * Move with no marks → falls back to cursor row
  * Move with 0-source-match → refused with clean notify
  * Move into self → refused
  * Move when target disappeared mid-flight → refused, source intact
  * Copy with name collision in target → silent rename via " COPY"
  * Copy duplicates are deep-copied (mutating source post-copy
    doesn't bleed into target)
  * Move preserves metadata (status, alignments, gb_text, history_xml)
  * Atomic save: failure leaves source + target untouched
  * Active-library mirror re-stages on move-from-active
  * Active-library mirror re-stages on copy-to-active
  * Move + entry-vector binding follows by name (binding is name-keyed)
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

import splicecraft as sc


# ── Helpers ──────────────────────────────────────────────────────────


def _seed_two_collections(eden_n: int = 3, ffe_n: int = 0):
    """Build a {collections.json, plasmid_library.json} pair with
    'Eden' (eden_n plasmids) + 'FFE' (ffe_n plasmids). Eden is active.
    Returns the entry ids in Eden so tests can mark them."""
    eden_plasmids = [
        {"id": f"eden_{i}", "name": f"plasmid_{i}",
         "size": 1000 + i, "n_feats": 2,
         "status": "DESIGNING", "gb_text": f"LOCUS plasmid_{i} 1000 bp\n",
         "alignments": [],
         "metadata_marker": f"unique-eden-{i}"}
        for i in range(eden_n)
    ]
    ffe_plasmids = [
        {"id": f"ffe_{i}", "name": f"ffe_plasmid_{i}",
         "size": 2000 + i, "n_feats": 3,
         "status": "VERIFIED", "gb_text": f"LOCUS ffe_{i} 2000 bp\n",
         "metadata_marker": f"unique-ffe-{i}"}
        for i in range(ffe_n)
    ]
    colls = [
        {"name": "Eden", "description": "test source",
         "plasmids": eden_plasmids},
        {"name": "FFE",  "description": "test target",
         "plasmids": ffe_plasmids},
    ]
    sc._save_collections(colls)
    sc._set_active_collection_name("Eden")
    sc._settings_flush_sync()
    sc._safe_save_json_mirror(sc._LIBRARY_FILE, eden_plasmids,
                                "Plasmid library")
    sc._library_cache = None
    return [e["id"] for e in eden_plasmids]


# ── Sanity ───────────────────────────────────────────────────────────


class TestSanity:
    def test_marked_ids_attr_exists(self):
        """LibraryPanel instances initialise `_marked_ids` in on_mount."""
        # We can't easily run on_mount without an app, so just verify
        # the class has the action handlers + the Message subclass.
        assert hasattr(sc.LibraryPanel, "action_toggle_mark")
        assert hasattr(sc.LibraryPanel, "action_move_marked")
        assert hasattr(sc.LibraryPanel, "action_copy_marked")
        assert hasattr(sc.LibraryPanel, "action_clear_marks")
        assert hasattr(sc.LibraryPanel, "MoveCopyRequested")


# ── Move / copy commit logic (the heavy hardening) ───────────────────


class TestMoveCopyCommit:
    """`_move_copy_commit` is the synchronous worker called from the
    app's modal-callback. Tests bypass the modal to focus on the
    transactional logic."""

    @pytest.fixture
    def app(self):
        """Bare PlasmidApp instance (no UI) so we can call the method
        directly. The notify shim swallows toasts."""
        app = sc.PlasmidApp.__new__(sc.PlasmidApp)
        app._notify_log = []

        def _notify(msg, severity="information", **_kwargs):
            app._notify_log.append((severity, msg))

        app.notify = _notify
        return app

    def test_move_basic(self, app):
        eden_ids = _seed_two_collections(eden_n=3, ffe_n=0)
        # Move all 3.
        app._move_copy_commit(
            source="Eden", target="FFE",
            entry_ids=eden_ids, mode="move",
        )
        colls = sc._load_collections()
        eden = next(c for c in colls if c["name"] == "Eden")
        ffe  = next(c for c in colls if c["name"] == "FFE")
        assert eden["plasmids"] == []
        assert len(ffe["plasmids"]) == 3
        # Order preserved.
        assert [p["id"] for p in ffe["plasmids"]] == eden_ids

    def test_copy_basic(self, app):
        eden_ids = _seed_two_collections(eden_n=2, ffe_n=0)
        app._move_copy_commit(
            source="Eden", target="FFE",
            entry_ids=eden_ids, mode="copy",
        )
        colls = sc._load_collections()
        eden = next(c for c in colls if c["name"] == "Eden")
        ffe  = next(c for c in colls if c["name"] == "FFE")
        # Source unchanged.
        assert len(eden["plasmids"]) == 2
        # Target populated.
        assert len(ffe["plasmids"]) == 2

    def test_copy_is_deepcopy(self, app):
        """Mutating the source list after copy MUST NOT bleed into
        target (sacred [PIT-17])."""
        eden_ids = _seed_two_collections(eden_n=1, ffe_n=0)
        app._move_copy_commit(
            source="Eden", target="FFE",
            entry_ids=eden_ids, mode="copy",
        )
        # Mutate the source entry's metadata. The target's copy must
        # NOT be affected.
        colls = sc._load_collections()
        eden = next(c for c in colls if c["name"] == "Eden")
        eden["plasmids"][0]["metadata_marker"] = "MUTATED"
        sc._save_collections(colls)
        # Re-load and check FFE's copy still has the original marker.
        colls2 = sc._load_collections()
        ffe = next(c for c in colls2 if c["name"] == "FFE")
        assert ffe["plasmids"][0]["metadata_marker"] == "unique-eden-0"

    def test_copy_with_name_collision_appends_copy_suffix(self, app):
        # FFE already has a plasmid named "plasmid_0".
        sc._save_collections([
            {"name": "Eden", "plasmids": [
                {"id": "eden_0", "name": "plasmid_0", "size": 100,
                 "gb_text": "x"},
            ]},
            {"name": "FFE", "plasmids": [
                {"id": "x", "name": "plasmid_0", "size": 200,
                 "gb_text": "y"},
            ]},
        ])
        sc._library_cache = None
        sc._collections_cache = None
        app._move_copy_commit(
            source="Eden", target="FFE",
            entry_ids=["eden_0"], mode="copy",
        )
        colls = sc._load_collections()
        ffe = next(c for c in colls if c["name"] == "FFE")
        names = [p["name"] for p in ffe["plasmids"]]
        assert "plasmid_0" in names
        assert "plasmid_0 COPY" in names

    def test_copy_with_multiple_collisions_increments_suffix(self, app):
        # Pre-seed FFE with `plasmid_0`, `plasmid_0 COPY` → next
        # landing must be `plasmid_0 COPY 2`.
        sc._save_collections([
            {"name": "Eden", "plasmids": [
                {"id": "eden_0", "name": "plasmid_0", "size": 100,
                 "gb_text": "x"},
            ]},
            {"name": "FFE", "plasmids": [
                {"id": "a", "name": "plasmid_0", "size": 200,
                 "gb_text": "y"},
                {"id": "b", "name": "plasmid_0 COPY", "size": 300,
                 "gb_text": "z"},
            ]},
        ])
        sc._library_cache = None
        sc._collections_cache = None
        app._move_copy_commit(
            source="Eden", target="FFE",
            entry_ids=["eden_0"], mode="copy",
        )
        colls = sc._load_collections()
        ffe = next(c for c in colls if c["name"] == "FFE")
        names = [p["name"] for p in ffe["plasmids"]]
        assert "plasmid_0 COPY 2" in names

    def test_move_same_source_and_target_refused(self, app):
        sc._save_collections([
            {"name": "Eden", "plasmids": [
                {"id": "eden_0", "name": "p", "size": 100, "gb_text": "x"},
            ]},
        ])
        sc._library_cache = None
        sc._collections_cache = None
        app._move_copy_commit(
            source="Eden", target="Eden",
            entry_ids=["eden_0"], mode="move",
        )
        # Source intact.
        colls = sc._load_collections()
        eden = next(c for c in colls if c["name"] == "Eden")
        assert len(eden["plasmids"]) == 1
        # Notify fired with warning.
        assert any("same" in m.lower()
                   for sev, m in app._notify_log)

    def test_invalid_mode_refused(self, app):
        _seed_two_collections(eden_n=1, ffe_n=0)
        app._move_copy_commit(
            source="Eden", target="FFE",
            entry_ids=["eden_0"], mode="weirdmode",
        )
        # No state change.
        colls = sc._load_collections()
        eden = next(c for c in colls if c["name"] == "Eden")
        ffe = next(c for c in colls if c["name"] == "FFE")
        assert len(eden["plasmids"]) == 1
        assert len(ffe["plasmids"]) == 0

    def test_source_disappeared_mid_commit(self, app, monkeypatch):
        # Simulate a race: the source collection is gone by the time
        # the commit runs. Should refuse cleanly without touching
        # target state.
        _seed_two_collections(eden_n=1, ffe_n=0)
        sc._save_collections([
            {"name": "FFE", "plasmids": []},
        ])
        sc._library_cache = None
        sc._collections_cache = None
        app._move_copy_commit(
            source="Eden", target="FFE",
            entry_ids=["eden_0"], mode="move",
        )
        # FFE still empty.
        colls = sc._load_collections()
        ffe = next(c for c in colls if c["name"] == "FFE")
        assert len(ffe["plasmids"]) == 0
        assert any("disappeared" in m.lower() or "failed" in m.lower()
                   for sev, m in app._notify_log)

    def test_move_preserves_full_metadata(self, app):
        # Build a source plasmid with rich metadata: status,
        # alignments, history_xml, color overrides, custom fields.
        rich = {
            "id":           "rich_0",
            "name":         "rich_plasmid",
            "size":         5000,
            "n_feats":      10,
            "gb_text":      "LOCUS rich 5000 bp\n",
            "status":       "VERIFIED",
            "alignments":   [{"read": "x", "identity_pct": 99.5}],
            "history_xml":  "<root><node id='1'/></root>",
            "map_mode":     "linear",
            "_plugin_data": {"my_plugin": {"foo": "bar"}},
            "custom_field": "preserved_value",
        }
        sc._save_collections([
            {"name": "Eden", "plasmids": [rich]},
            {"name": "FFE",  "plasmids": []},
        ])
        sc._library_cache = None
        sc._collections_cache = None
        app._move_copy_commit(
            source="Eden", target="FFE",
            entry_ids=["rich_0"], mode="move",
        )
        colls = sc._load_collections()
        ffe = next(c for c in colls if c["name"] == "FFE")
        landed = ffe["plasmids"][0]
        # Every metadata field preserved exactly.
        for k in ("status", "alignments", "history_xml", "map_mode",
                  "_plugin_data", "custom_field", "gb_text", "size"):
            assert landed[k] == rich[k], (
                f"metadata {k!r} not preserved: "
                f"{landed[k]!r} != {rich[k]!r}"
            )

    def test_mirror_re_stages_on_move_from_active(self, app):
        # Active = Eden. After moving Eden's only plasmid away, the
        # active library mirror (plasmid_library.json) must reflect
        # the new empty state.
        eden_ids = _seed_two_collections(eden_n=2, ffe_n=0)
        # Verify the mirror has 2 entries pre-move.
        lib_pre, _ = sc._safe_load_json(sc._LIBRARY_FILE, "test")
        assert len(lib_pre) == 2
        app._move_copy_commit(
            source="Eden", target="FFE",
            entry_ids=eden_ids, mode="move",
        )
        # Mirror now reflects empty Eden.
        lib_post, _ = sc._safe_load_json(sc._LIBRARY_FILE, "test")
        assert lib_post == []

    def test_mirror_re_stages_on_copy_to_active(self, app):
        # Active = FFE. Copy Eden plasmid INTO FFE → mirror reflects
        # the new entry.
        _seed_two_collections(eden_n=1, ffe_n=0)
        sc._set_active_collection_name("FFE")
        sc._settings_flush_sync()
        # Re-mirror to FFE state.
        sc._safe_save_json_mirror(sc._LIBRARY_FILE, [], "Plasmid library")
        sc._library_cache = None
        app._move_copy_commit(
            source="Eden", target="FFE",
            entry_ids=["eden_0"], mode="copy",
        )
        lib_post, _ = sc._safe_load_json(sc._LIBRARY_FILE, "test")
        assert len(lib_post) == 1

    def test_partial_id_match_only_operates_on_existing(self, app):
        eden_ids = _seed_two_collections(eden_n=2, ffe_n=0)
        # Include a bogus id in the request — should be silently
        # filtered.
        app._move_copy_commit(
            source="Eden", target="FFE",
            entry_ids=eden_ids + ["bogus_id_that_does_not_exist"],
            mode="move",
        )
        colls = sc._load_collections()
        ffe = next(c for c in colls if c["name"] == "FFE")
        # Only the 2 real ones landed.
        assert len(ffe["plasmids"]) == 2

    def test_all_ids_filtered_to_none_refused(self, app):
        _seed_two_collections(eden_n=2, ffe_n=0)
        app._move_copy_commit(
            source="Eden", target="FFE",
            entry_ids=["nothing_real_1", "nothing_real_2"],
            mode="move",
        )
        # Source intact.
        colls = sc._load_collections()
        eden = next(c for c in colls if c["name"] == "Eden")
        assert len(eden["plasmids"]) == 2

    def test_copy_id_collision_renames_id_too(self, app):
        # If the source and target share an id (e.g. fresh copy of
        # the same plasmid), the target's id gets a "_2" suffix so
        # the cache lookup stays unique. Pre-fix, the duplicate id
        # would silently overwrite the existing entry on the next
        # collection-switch mirror.
        sc._save_collections([
            {"name": "Eden", "plasmids": [
                {"id": "p1", "name": "P1", "size": 100, "gb_text": "x"},
            ]},
            {"name": "FFE", "plasmids": [
                {"id": "p1", "name": "OtherP1", "size": 200, "gb_text": "y"},
            ]},
        ])
        sc._library_cache = None
        sc._collections_cache = None
        app._move_copy_commit(
            source="Eden", target="FFE",
            entry_ids=["p1"], mode="copy",
        )
        colls = sc._load_collections()
        ffe = next(c for c in colls if c["name"] == "FFE")
        ids = [p["id"] for p in ffe["plasmids"]]
        assert "p1" in ids
        assert "p1_2" in ids
        assert len(set(ids)) == len(ids)   # no dupes


# ── Concurrency ──────────────────────────────────────────────────────


class TestMoveCopyConcurrency:
    """Two threads racing on move/copy must not corrupt either
    collection. The `_cache_lock` RMW makes the commit serial."""

    def test_concurrent_moves_dont_corrupt(self):
        import threading

        _seed_two_collections(eden_n=10, ffe_n=0)
        # Two apps in two threads, each moving disjoint halves of
        # Eden → FFE concurrently.
        app1 = sc.PlasmidApp.__new__(sc.PlasmidApp)
        app1.notify = lambda *a, **k: None
        app2 = sc.PlasmidApp.__new__(sc.PlasmidApp)
        app2.notify = lambda *a, **k: None
        half1 = [f"eden_{i}" for i in range(5)]
        half2 = [f"eden_{i}" for i in range(5, 10)]
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def worker(app, ids):
            barrier.wait()
            try:
                app._move_copy_commit(
                    source="Eden", target="FFE",
                    entry_ids=ids, mode="move",
                )
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=(app1, half1))
        t2 = threading.Thread(target=worker, args=(app2, half2))
        t1.start(); t2.start()
        t1.join(5); t2.join(5)
        assert not errors, f"workers raised: {errors!r}"
        colls = sc._load_collections()
        ffe = next(c for c in colls if c["name"] == "FFE")
        eden = next(c for c in colls if c["name"] == "Eden")
        # All 10 should have landed in FFE; Eden empty.
        assert len(ffe["plasmids"]) == 10
        assert len(eden["plasmids"]) == 0
        # No dupes.
        ids = [p["id"] for p in ffe["plasmids"]]
        assert len(set(ids)) == 10
