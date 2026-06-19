"""
test_sweep20 — agent-API + event-logger + invariant-coverage audit
(2026-05-21).

Audit found three classes of drift after sweeps #15–#19 landed:

  * Agent-API gaps: experiments, experiment_projects, gels,
    protein_motifs all shipped with full `_load_*`/`_save_*`
    infrastructure but ZERO agent endpoints. External agents could
    not list/create/update/delete any of them. Twenty new endpoints
    added.

  * `_MASTER_DELETE_CACHE_ATTRS` (invariant #50) missing
    `_primer_usage_cache` and `_feature_library_index_cache`.
    Master Delete would wipe the files but leave stale Python
    dict/tuple references in memory.

  * `_blocks_undo` (invariant #41) missing on five input-bearing
    modals: FetchModal, OpenFileModal, ExportGenBankModal,
    NcbiTaxonPickerModal, FeatureSearchModal.

  * Collision modals (`ExactCopyConfirmModal`, `NameCollisionModal`)
    dismissed silently — no structured event for the user's
    Skip/Keep/Overwrite/Cancel choice. Breaks the
    "user pastes log → AI parses → patch" loop (invariant #42).

This file regression-locks the fixes.
"""
from __future__ import annotations

import splicecraft as sc


# ═══════════════════════════════════════════════════════════════════════════════
# Master Delete cache-attr coverage (invariant #50)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMasterDeleteCacheAttrsCoverage:
    """Regression guard: every `_*_cache` module-level global that
    backs a persisted file must appear in `_MASTER_DELETE_CACHE_ATTRS`
    so Master Delete resets it in memory after wiping the file. Sweep
    #20 noticed two missing caches that had been added in unrelated
    refactors.
    """

    def test_primer_usage_cache_in_attrs(self):
        assert "_primer_usage_cache" in sc._MASTER_DELETE_CACHE_ATTRS

    def test_feature_library_index_cache_in_attrs(self):
        assert "_feature_library_index_cache" \
            in sc._MASTER_DELETE_CACHE_ATTRS

    def test_attrs_all_exist_as_module_globals(self):
        """Every name in `_MASTER_DELETE_CACHE_ATTRS` must resolve to a
        real module attribute. Catches typos + name drift."""
        for attr in sc._MASTER_DELETE_CACHE_ATTRS:
            assert hasattr(sc, attr) or hasattr(sc._state, attr), (
                f"_MASTER_DELETE_CACHE_ATTRS references missing "
                f"attribute (hub or _state): {attr!r}"
            )

    def test_no_persisted_data_cache_missing(self):
        """Cross-reference against the data-file map in conftest's
        `_protect_user_data`. Every cache_attr there (when set) must
        be in `_MASTER_DELETE_CACHE_ATTRS` so a Master Delete that
        wipes the file also resets the in-memory cache.
        """
        # Hard-coded mirror of the conftest list — we can't import from
        # conftest cleanly without scope pollution. Keep in lockstep.
        persisted = {
            "_library_cache",
            "_parts_bin_cache",
            "_parts_bin_collections_cache",
            "_primers_cache",
            "_codon_tables_cache",
            "_features_cache",
            "_feature_colors_cache",
            "_grammars_cache",
            "_entry_vectors_cache",
            "_settings_cache",
            "_collections_cache",
            "_experiments_cache",
            "_experiment_projects_cache",
            "_gels_cache",
            "_protein_motifs_cache",
        }
        missing = persisted - set(sc._MASTER_DELETE_CACHE_ATTRS)
        assert not missing, (
            f"persisted-data caches missing from "
            f"_MASTER_DELETE_CACHE_ATTRS: {sorted(missing)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# _blocks_undo retrofit on input-bearing modals (invariant #41)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBlocksUndoOnInputModals:
    """Regression guard: modals hosting an Input (filename, URL, search
    query, accession) MUST opt out of app-level Ctrl+Z so that the
    Input's built-in undo takes precedence over the canvas record's
    undo stack. Sweep #12 sweep added a dozen modals; sweep #20 caught
    five that had been overlooked.
    """

    def test_fetch_modal(self):
        assert getattr(sc.FetchModal, "_blocks_undo", False) is True

    def test_open_file_modal(self):
        assert getattr(sc.OpenFileModal, "_blocks_undo", False) is True

    def test_export_genbank_modal(self):
        assert getattr(
            sc.ExportGenBankModal, "_blocks_undo", False,
        ) is True

    def test_ncbi_taxon_picker_modal(self):
        assert getattr(
            sc.NcbiTaxonPickerModal, "_blocks_undo", False,
        ) is True

    def test_feature_search_modal(self):
        assert getattr(
            sc.FeatureSearchModal, "_blocks_undo", False,
        ) is True


# ═══════════════════════════════════════════════════════════════════════════════
# Agent-API: experiments endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentExperimentsEndpoints:
    """Five endpoints covering CRUD on the lab-notebook layer. Routes
    through `_load_experiments` / `_save_experiments` so the four-layer
    data-safety net + cache-lock concurrency apply automatically.
    """

    def _call(self, name, payload=None):
        fn, _write = sc._AGENT_HANDLERS[name]
        return fn(None, payload or {})

    def test_endpoints_registered(self):
        for name in (
            "list-experiments", "get-experiment",
            "create-experiment", "update-experiment",
            "delete-experiment",
        ):
            assert name in sc._AGENT_HANDLERS, (
                f"missing agent endpoint: {name}"
            )

    def test_list_empty(self):
        sc._ensure_default_project()
        result = self._call("list-experiments")
        assert "experiments" in result
        assert result["experiments"] == []

    def test_create_then_list(self):
        sc._ensure_default_project()
        created = self._call("create-experiment", {
            "title": "Friday digest",
            "body_md": "Notes here",
            "tags": ["digestion", "BsaI"],
        })
        assert created.get("ok") is True
        eid = created["id"]
        listed = self._call("list-experiments")
        assert any(e["id"] == eid for e in listed["experiments"])
        assert any(
            e["title"] == "Friday digest"
            for e in listed["experiments"]
        )

    def test_get_returns_full_body(self):
        sc._ensure_default_project()
        created = self._call("create-experiment", {
            "title": "Cloning notes",
            "body_md": "Body text " * 20,
        })
        got = self._call("get-experiment", {"id": created["id"]})
        assert "experiment" in got
        assert got["experiment"]["body_md"].startswith("Body text")

    def test_get_unknown_id_404(self):
        result, status = self._call(
            "get-experiment", {"id": "exp-deadbeef"},
        )
        assert status == 404

    def test_get_invalid_id_400(self):
        result, status = self._call(
            "get-experiment", {"id": "../etc/passwd"},
        )
        assert status == 400

    def test_update_merges_fields(self):
        sc._ensure_default_project()
        created = self._call("create-experiment", {
            "title": "Original",
            "body_md": "Original body",
            "tags": ["a"],
        })
        eid = created["id"]
        # Update only the title — body + tags should survive.
        self._call("update-experiment", {
            "id": eid,
            "title": "Renamed",
        })
        got = self._call("get-experiment", {"id": eid})
        assert got["experiment"]["title"] == "Renamed"
        assert got["experiment"]["body_md"] == "Original body"
        assert got["experiment"]["tags"] == ["a"]

    def test_delete_removes_entry(self):
        sc._ensure_default_project()
        created = self._call("create-experiment", {"title": "Doomed"})
        eid = created["id"]
        deleted = self._call("delete-experiment", {"id": eid})
        assert deleted.get("ok") is True
        result, status = self._call("get-experiment", {"id": eid})
        assert status == 404


# ═══════════════════════════════════════════════════════════════════════════════
# Agent-API: experiment-projects endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentExperimentProjectsEndpoints:
    """Five endpoints covering the multi-project lab-notebook layer."""

    def _call(self, name, payload=None):
        fn, _write = sc._AGENT_HANDLERS[name]
        return fn(None, payload or {})

    def test_endpoints_registered(self):
        for name in (
            "list-experiment-projects",
            "set-active-experiment-project",
            "create-experiment-project",
            "rename-experiment-project",
            "delete-experiment-project",
        ):
            assert name in sc._AGENT_HANDLERS, (
                f"missing agent endpoint: {name}"
            )

    def test_list_after_default(self):
        sc._ensure_default_project()
        result = self._call("list-experiment-projects")
        assert "projects" in result
        # At least the default project exists.
        names = [p["name"] for p in result["projects"]]
        assert sc._DEFAULT_PROJECT_NAME in names

    def test_create_project(self):
        sc._ensure_default_project()
        created = self._call("create-experiment-project", {
            "name": "Friday Project",
            "description": "Friday-only work",
        })
        assert created.get("ok") is True
        names = [
            p["name"]
            for p in self._call("list-experiment-projects")["projects"]
        ]
        assert "Friday Project" in names

    def test_create_dup_name_409(self):
        sc._ensure_default_project()
        self._call("create-experiment-project", {"name": "P1"})
        result, status = self._call(
            "create-experiment-project", {"name": "P1"},
        )
        assert status == 409

    def test_set_active_switches(self):
        sc._ensure_default_project()
        self._call("create-experiment-project", {"name": "TargetProj"})
        result = self._call("set-active-experiment-project", {
            "name": "TargetProj",
        })
        assert result["active"] == "TargetProj"
        # Verify settings active pointer follows.
        assert sc._get_active_project_name() == "TargetProj"

    def test_set_active_unknown_404(self):
        sc._ensure_default_project()
        result, status = self._call(
            "set-active-experiment-project",
            {"name": "Does Not Exist"},
        )
        assert status == 404

    def test_rename_project(self):
        sc._ensure_default_project()
        self._call("create-experiment-project", {"name": "Old Name"})
        renamed = self._call("rename-experiment-project", {
            "name": "Old Name", "new_name": "New Name",
        })
        assert renamed.get("ok") is True
        names = [
            p["name"]
            for p in self._call("list-experiment-projects")["projects"]
        ]
        assert "New Name" in names
        assert "Old Name" not in names

    def test_delete_last_project_refused(self):
        sc._ensure_default_project()
        # Default project is the only one — should refuse.
        result, status = self._call("delete-experiment-project", {
            "name": sc._DEFAULT_PROJECT_NAME,
        })
        assert status == 409

    def test_delete_promotes_when_active(self):
        sc._ensure_default_project()
        self._call("create-experiment-project", {"name": "SecondProj"})
        self._call("set-active-experiment-project", {"name": "SecondProj"})
        result = self._call("delete-experiment-project", {
            "name": "SecondProj",
        })
        assert result["promoted"] != ""
        # Active should now be the surviving project.
        assert sc._get_active_project_name() == result["promoted"]


# ═══════════════════════════════════════════════════════════════════════════════
# Agent-API: gels endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentGelsEndpoints:
    """Five endpoints (list/get/create/update/delete) over the saved
    gel snapshot library. Round-trip respects all the `_normalise_gel_entry`
    caps (lane fields, agarose clamp, etc.).
    """

    def _call(self, name, payload=None):
        fn, _write = sc._AGENT_HANDLERS[name]
        return fn(None, payload or {})

    def test_endpoints_registered(self):
        for name in (
            "list-gels", "get-gel", "create-gel",
            "update-gel", "delete-gel",
        ):
            assert name in sc._AGENT_HANDLERS, (
                f"missing agent endpoint: {name}"
            )

    def test_list_empty(self):
        result = self._call("list-gels")
        assert result["gels"] == []

    def test_create_then_get(self):
        created = self._call("create-gel", {
            "name": "Friday digest gel",
            "lanes": [
                {"name": "L1", "source": "ladder",
                  "detail": "1 kb Plus"},
                {"name": "L2", "source": "empty"},
            ],
            "agarose_pct": 1.2,
            "notes": "Ran at 90V for 45 min.",
        })
        assert created.get("ok") is True
        gid = created["id"]
        got = self._call("get-gel", {"id": gid})
        assert got["gel"]["name"] == "Friday digest gel"
        assert len(got["gel"]["lanes"]) == 2
        # Notes survive.
        assert "Ran at 90V" in got["gel"]["notes"]

    def test_create_dup_name_409(self):
        self._call("create-gel", {
            "name": "Same Name", "lanes": [],
        })
        result, status = self._call("create-gel", {
            "name": "Same Name", "lanes": [],
        })
        assert status == 409

    def test_update_replaces_lanes(self):
        created = self._call("create-gel", {
            "name": "Replace lanes",
            "lanes": [{"name": "old", "source": "empty"}],
        })
        gid = created["id"]
        self._call("update-gel", {
            "id": gid,
            "lanes": [
                {"name": "new1", "source": "ladder"},
                {"name": "new2", "source": "plasmid"},
            ],
        })
        got = self._call("get-gel", {"id": gid})
        assert [ln["name"] for ln in got["gel"]["lanes"]] == [
            "new1", "new2",
        ]

    def test_update_clamps_agarose(self):
        created = self._call("create-gel", {
            "name": "Clamp test", "lanes": [],
        })
        gid = created["id"]
        self._call("update-gel", {
            "id": gid, "agarose_pct": 99.0,
        })
        got = self._call("get-gel", {"id": gid})
        assert got["gel"]["agarose_pct"] == sc._GEL_AGAROSE_MAX

    def test_delete_unknown_404(self):
        result, status = self._call(
            "delete-gel", {"id": "gel-deadbeef"},
        )
        assert status == 404

    def test_get_invalid_id_400(self):
        result, status = self._call("get-gel", {"id": "../etc"})
        assert status == 400


# ═══════════════════════════════════════════════════════════════════════════════
# Agent-API: protein-motif endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentProteinMotifEndpoints:
    """Three endpoints (list / set / delete) over the protein-motif
    library. User overrides land in `protein_motifs.json`; the merged
    list returned by `list-protein-motifs` reflects built-ins +
    overrides per `_load_protein_motifs`.
    """

    def _call(self, name, payload=None):
        fn, _write = sc._AGENT_HANDLERS[name]
        return fn(None, payload or {})

    def test_endpoints_registered(self):
        for name in (
            "list-protein-motifs",
            "set-protein-motif",
            "delete-protein-motif",
        ):
            assert name in sc._AGENT_HANDLERS, (
                f"missing agent endpoint: {name}"
            )

    def test_list_includes_builtins(self):
        # Built-ins are baked into source; no save needed to see them.
        result = self._call("list-protein-motifs")
        names = [m["name"] for m in result["motifs"]]
        assert "His6" in names
        assert "FLAG" in names

    def test_set_creates_user_motif(self):
        created = self._call("set-protein-motif", {
            "name": "MyCustomTag",
            "sequence": "PEPTIDE",
            "feature_type": "Tag",
            "color": "#123456",
            "description": "Custom test tag",
        })
        assert created.get("ok") is True
        names = [
            m["name"]
            for m in self._call("list-protein-motifs")["motifs"]
        ]
        assert "MyCustomTag" in names

    def test_set_rejects_non_aa(self):
        result, status = self._call("set-protein-motif", {
            "name": "BadSeq",
            "sequence": "ACGTU",   # T+U not in canonical AAs
        })
        assert status == 400

    def test_set_overrides_builtin(self):
        # Overriding His6 — built-in stays in source, but the listed
        # entry should now carry our override fields.
        self._call("set-protein-motif", {
            "name": "His6",
            "sequence": "HHHHHH",
            "color": "#ABCDEF",
            "description": "Overridden",
        })
        listed = {
            m["name"]: m
            for m in self._call("list-protein-motifs")["motifs"]
        }
        assert listed["His6"]["color"] == "#ABCDEF"

    def test_delete_user_motif(self):
        self._call("set-protein-motif", {
            "name": "DeleteMe",
            "sequence": "AAA",
        })
        result = self._call("delete-protein-motif", {"name": "DeleteMe"})
        assert result.get("ok") is True
        # Custom motif gone; built-ins remain.
        listed = {
            m["name"]
            for m in self._call("list-protein-motifs")["motifs"]
        }
        assert "DeleteMe" not in listed
        assert "His6" in listed

    def test_delete_builtin_without_override_404(self):
        # His6 hasn't been overridden — nothing to delete.
        result, status = self._call(
            "delete-protein-motif", {"name": "His6"},
        )
        assert status == 404


# ═══════════════════════════════════════════════════════════════════════════════
# Agent-API: entry-vector auto-detect + clear
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentEntryVectorEndpoints:
    """The two endpoints that close the entry-vector side-door gap.
    `set-entry-vector` already existed; sweep #20 added `auto-detect-
    entry-vectors` and `clear-entry-vectors-for-grammar`.
    """

    def _call(self, name, payload=None):
        fn, _write = sc._AGENT_HANDLERS[name]
        return fn(None, payload or {})

    def test_endpoints_registered(self):
        assert "auto-detect-entry-vectors" in sc._AGENT_HANDLERS
        assert "clear-entry-vectors-for-grammar" in sc._AGENT_HANDLERS

    def test_auto_detect_empty_library_returns_empty_summary(self):
        result = self._call("auto-detect-entry-vectors")
        assert result.get("ok") is True
        # Empty library → empty summary.
        assert result["summary"] == ""

    def test_clear_unknown_grammar_returns_zero(self):
        result = self._call("clear-entry-vectors-for-grammar", {
            "grammar_id": "gb_l0",
        })
        assert result.get("ok") is True
        # Nothing was bound, so n_cleared == 0.
        assert result["n_cleared"] == 0

    def test_clear_missing_grammar_id_400(self):
        result, status = self._call(
            "clear-entry-vectors-for-grammar", {},
        )
        assert status == 400


# ═══════════════════════════════════════════════════════════════════════════════
# Collision-modal structured events (invariant #42)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCollisionModalEvents:
    """The user pasting a SpliceCraft log into AI should see exactly
    what choice the user made when a collision modal popped. Pre-sweep
    #20, both modals dismissed silently — only the dismiss payload
    survived (as a Textual internal). Now every exit path emits a
    structured event.
    """

    def _capture_events(self, monkeypatch):
        """Replace `_log_event` with a list-recorder."""
        captured: list = []

        def _rec(event, **fields):
            captured.append((event, fields))

        monkeypatch.setattr(sc, "_log_event", _rec)
        # Phase D: the collision modals (NameCollisionModal / ExactCopyConfirmModal)
        # moved to splicecraft_modals, which imported `_log_event` by value — patch
        # that binding too, else their emits resolve in the sibling namespace and
        # the recorder never sees them.
        import splicecraft_modals
        monkeypatch.setattr(splicecraft_modals, "_log_event", _rec)
        return captured

    def test_exact_copy_skip_emits_event(self, monkeypatch):
        captured = self._capture_events(monkeypatch)
        m = sc.ExactCopyConfirmModal("part", ["A", "B", "C"])
        m.dismiss = lambda payload: None  # type: ignore[assignment]
        m._skip(None)
        assert any(
            e[0] == "collision.exact_copy.dismiss"
            and e[1].get("choice") == "skip"
            and e[1].get("entity") == "part"
            and e[1].get("n") == 3
            for e in captured
        )

    def test_exact_copy_keep_emits_event(self, monkeypatch):
        captured = self._capture_events(monkeypatch)
        m = sc.ExactCopyConfirmModal("plasmid", ["P1"])
        m.dismiss = lambda payload: None  # type: ignore[assignment]
        m._keep(None)
        assert any(
            e[0] == "collision.exact_copy.dismiss"
            and e[1].get("choice") == "keep"
            for e in captured
        )

    def test_exact_copy_esc_emits_event(self, monkeypatch):
        captured = self._capture_events(monkeypatch)
        m = sc.ExactCopyConfirmModal("primer", ["X"])
        m.dismiss = lambda payload: None  # type: ignore[assignment]
        m.action_cancel()
        assert any(
            e[0] == "collision.exact_copy.dismiss"
            and e[1].get("choice") == "esc"
            for e in captured
        )

    def test_name_collision_keep_emits_event(self, monkeypatch):
        captured = self._capture_events(monkeypatch)
        m = sc.NameCollisionModal("plasmid", ["X", "Y"])
        m.dismiss = lambda payload: None  # type: ignore[assignment]
        m._keep(None)
        assert any(
            e[0] == "collision.name_collision.dismiss"
            and e[1].get("choice") == "keep"
            and e[1].get("n") == 2
            for e in captured
        )

    def test_name_collision_overwrite_emits_event(self, monkeypatch):
        captured = self._capture_events(monkeypatch)
        m = sc.NameCollisionModal("part", ["A"])
        m.dismiss = lambda payload: None  # type: ignore[assignment]
        m._overwrite(None)
        assert any(
            e[0] == "collision.name_collision.dismiss"
            and e[1].get("choice") == "overwrite"
            for e in captured
        )

    def test_name_collision_cancel_emits_event(self, monkeypatch):
        captured = self._capture_events(monkeypatch)
        m = sc.NameCollisionModal("part", ["A"])
        m.dismiss = lambda payload: None  # type: ignore[assignment]
        m._cancel_btn(None)
        assert any(
            e[0] == "collision.name_collision.dismiss"
            and e[1].get("choice") == "cancel"
            for e in captured
        )

    def test_name_collision_esc_emits_event(self, monkeypatch):
        captured = self._capture_events(monkeypatch)
        m = sc.NameCollisionModal("collection", ["C1"])
        m.dismiss = lambda payload: None  # type: ignore[assignment]
        m.action_cancel()
        assert any(
            e[0] == "collision.name_collision.dismiss"
            and e[1].get("choice") == "esc"
            for e in captured
        )


# ═══════════════════════════════════════════════════════════════════════════════
# @_action_log retrofit (deferred items, sweep #20 follow-up)
# ═══════════════════════════════════════════════════════════════════════════════

class TestActionLogRetrofit:
    """The decorator captures user intent (action triggered) as a
    structured event, separately from any body-event that fires on
    outcome. Per CLAUDE.md invariant #42, every user-visible action
    that maps to a binding / button should carry the decorator. Sweep
    #20 retrofit covers 15 must-have actions that lacked it.

    White-box check: look for the wrapped-function marker that
    `functools.wraps` leaves on the resulting callable — when the
    decorator's been applied, `func.__wrapped__` is the original
    method. Skipping methods that ALREADY emit a body event (sweep
    #20 left those alone to avoid noise — single source of truth).
    """

    def _has_wrapper(self, cls, method_name):
        method = getattr(cls, method_name, None)
        if method is None:
            return False
        return hasattr(method, "__wrapped__")

    def test_feature_library_screen_actions(self):
        for action in ("action_export_fasta", "action_close",
                        "action_save", "action_add", "action_edit",
                        "action_rename", "action_remove"):
            assert self._has_wrapper(sc.FeatureLibraryScreen, action), (
                f"FeatureLibraryScreen.{action} missing @_action_log"
            )

    def test_entry_vectors_modal_close(self):
        assert self._has_wrapper(
            sc.EntryVectorsModal, "action_close",
        )

    def test_parts_bin_modal_delete(self):
        assert self._has_wrapper(
            sc.PartsBinModal, "action_delete_selected_parts",
        )

    def test_experiments_screen_open_projects(self):
        assert self._has_wrapper(
            sc.ExperimentsScreen, "action_open_projects",
        )

    def test_experiment_rename_modal_submit(self):
        assert self._has_wrapper(
            sc.ExperimentRenameModal, "action_submit",
        )

    def test_alignment_screen_close(self):
        assert self._has_wrapper(
            sc.AlignmentScreen, "action_close",
        )

    def test_history_screen_close(self):
        assert self._has_wrapper(
            sc.HistoryScreen, "action_close",
        )

    def test_feature_sidebar_open_at_cursor(self):
        assert self._has_wrapper(
            sc.FeatureSidebar, "action_open_feature_at_cursor",
        )

    def test_sequence_panel_open_feature(self):
        assert self._has_wrapper(
            sc.SequencePanel, "action_open_selected_feature",
        )

    # ── Sweep #20 follow-up: per the "shore up everything" push,
    # decorator applies to EVERY action_* even when a body event
    # also emits. The decorator captures user INTENT (action fired);
    # body events capture OUTCOME (state changed). If a Ctrl+Z is
    # blocked by an undo-blocking modal, `undo.trigger` never fires
    # but `app.undo` from the decorator does — both are signal.

    def test_experiments_screen_new_entry(self):
        assert self._has_wrapper(
            sc.ExperimentsScreen, "action_new_entry",
        )

    def test_experiments_screen_save_entry(self):
        assert self._has_wrapper(
            sc.ExperimentsScreen, "action_save_entry",
        )

    def test_synthesis_screen_toggle_codon_mode(self):
        assert self._has_wrapper(
            sc.SynthesisScreen, "action_toggle_codon_mode",
        )

    def test_synthesis_screen_new_fragment(self):
        assert self._has_wrapper(
            sc.SynthesisScreen, "action_new_fragment",
        )

    def test_synthesis_screen_load_fragment(self):
        assert self._has_wrapper(
            sc.SynthesisScreen, "action_load_fragment",
        )

    def test_synthesis_screen_save(self):
        assert self._has_wrapper(
            sc.SynthesisScreen, "action_save",
        )

    def test_synthesis_screen_add_feature(self):
        assert self._has_wrapper(
            sc.SynthesisScreen, "action_add_feature",
        )

    def test_plasmid_app_undo(self):
        assert self._has_wrapper(
            sc.PlasmidApp, "action_undo",
        )

    def test_plasmid_app_redo(self):
        assert self._has_wrapper(
            sc.PlasmidApp, "action_redo",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Agent-API: set-active-codon-table
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentSetActiveCodonTable:
    """The codon-table preference (taxid string) round-trips through
    settings.json so a SynthesisScreen open honors the agent's choice.
    Sweep #20 closes the gap I'd punted on in the initial pass.
    """

    def _call(self, name, payload=None):
        fn, _write = sc._AGENT_HANDLERS[name]
        return fn(None, payload or {})

    def test_endpoint_registered(self):
        assert "set-active-codon-table" in sc._AGENT_HANDLERS

    def test_endpoint_is_write(self):
        _fn, write = sc._AGENT_HANDLERS["set-active-codon-table"]
        assert write is True

    def test_set_known_taxid(self):
        # K12 is the seeded built-in in `_codon_tables_load`. Should
        # always be present.
        result = self._call("set-active-codon-table", {"taxid": "83333"})
        assert result.get("ok") is True
        assert result["active_taxid"] == "83333"
        # Setting persists in `active_codon_table`.
        assert sc._get_setting("active_codon_table", "") == "83333"

    def test_set_empty_clears(self):
        # First set to something non-empty.
        self._call("set-active-codon-table", {"taxid": "83333"})
        # Then clear.
        result = self._call("set-active-codon-table", {"taxid": ""})
        assert result.get("ok") is True
        assert result["active_taxid"] == ""
        assert sc._get_setting("active_codon_table", "") == ""

    def test_set_unknown_taxid_404(self):
        result, status = self._call(
            "set-active-codon-table",
            {"taxid": "999999999"},
        )
        assert status == 404

    def test_missing_taxid_400(self):
        result, status = self._call("set-active-codon-table", {})
        assert status == 400

    def test_settings_schema_includes_key(self):
        assert "active_codon_table" in sc._SETTINGS_SCHEMA

    def test_list_codon_tables_surfaces_active_taxid(self):
        # Existing endpoint extended with `active_taxid` field for
        # discoverability.
        self._call("set-active-codon-table", {"taxid": "83333"})
        result = self._call("list-codon-tables")
        assert result.get("active_taxid") == "83333"


# ═══════════════════════════════════════════════════════════════════════════════
# Total handler count guard
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentEndpointCountGuard:
    """Sanity floor: the audit found 64 handlers; sweep #20 adds 21
    (20 in initial pass + set-active-codon-table on the follow-up).
    Lock in ≥85 so a refactor can't silently rip endpoints out.
    Increment this floor (never decrement) when new endpoints land."""

    def test_at_least_85_handlers(self):
        assert len(sc._AGENT_HANDLERS) >= 85
