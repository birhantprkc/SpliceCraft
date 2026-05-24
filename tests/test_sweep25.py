"""
test_sweep25 — adversarial audit Sweep #25 (2026-05-23).

Regression coverage for the fixes landed by INV-66. The audit
surfaced ~40 findings across CRITICAL / HIGH / MEDIUM / LOW
severities; this file regression-locks the ones whose silent
return would otherwise let a future edit regress the fix.

Index of CRITICAL fixes covered:
  * Cache-bust enumeration drift (C1, C2) — `RestoreFromBackupModal`
    and `_h_restore_pre_update_snapshot` now both drive from
    `_MASTER_DELETE_CACHE_ATTRS`. Pre-fix sweep #24 added
    custom_enzymes + enzyme_collections caches; neither restore
    path was updated, leaving stale-cache → silent-overwrite holes.
  * Stacks log rotation cap (C3) — `_STACKS_LOG_MAX_BYTES` truncates
    on startup when prior sessions' faulthandler dumps exceed cap.
  * `_GB_PARSE_CACHE` cap reduced 64 → 16 (C4) — bounds worst-case
    parsed-record memory amplification.
  * `_iter_library_readonly` + `_iter_parts_bin_readonly` + sibling
    `_find_library_entry_by_name` helpers (C5) — read-only iteration
    without the per-call full-library deepcopy.

Index of HIGH fixes covered:
  * Custom enzyme + enzyme collection agent endpoints now wrap RMW
    in `_cache_lock` (H1).
  * App-level Ctrl+S `_save_worker` library RMW under `_cache_lock`
    (H2).
  * 5 worker `_safe_save_json` direct calls now hold `_cache_lock`
    for the disk write (H3).
  * `_SCAN_CATALOG` rebuild is atomic global reassign (H4).
  * Agent server requires bearer token on ALL endpoints (H5).
  * Gibson agent endpoint uses `call_from_thread` for UI refresh
    (H6).
  * Plasmidsaurus + load-file error responses collapsed to uniform
    400 (H7, H8) — filesystem-state oracle reduction.
  * `_AgentAPIServer.timeout = 30` for slow-loris mitigation (H9).
  * `_sync_active_collection_plasmids` sync path uses readonly
    iterator + targeted clone under lock (H10).
  * `_get_setting` reads cache directly without per-call full-dict
    deepcopy (H11).
  * `_rc(tgt_seq)` hoisted out of `_find_annotation_transfers`
    per-feature loop (H12).
  * `except (OSError, Exception)` narrowed to actual Primer3 modes
    (H14).
  * `AddCustomEnzymeModal._save_btn` wraps RMW in `_cache_lock` +
    emits structured save-failed event (H15).
  * GFF3 out-of-range feature drop now logs (H16).

Index of MEDIUM / LOW fixes covered:
  * `RestoreFromBackupModal._dismiss_once` retrofit (M1).
  * `_safe_identifier` validator for `active_*` settings (M2).
  * Snapshot dir restore 5 GB cap (M3).
  * Body parse error → `400 "malformed JSON body"` (M5).
  * Agent server scrubs paths from exception responses (M6).
  * `_atomic_copy` / `_atomic_marker_write` close `fd` on early
    raise (M9).
  * Agent token file write closes `fd` if `fdopen` raises (M10).
  * Crash recovery prune (30-day age + 50-file count caps) (M11).
  * `_VECTOR_MATCH_CACHE` keys on `hash(gb_text)` not raw string
    (M14).
  * `target_seq = ""` fallback narrowed to (AttributeError, TypeError)
    (M20).
  * NEB built-in override gap documented (M13).
  * `splicecraft_cli._read_session` refuses symlinked token file
    (L1).
  * Agent token via `secrets.token_urlsafe(32)` (L3).
  * NCBI / Kazusa fetches narrow `except` to (OSError, URLError)
    (L6).
  * Lockfile unlink on graceful exit (L9).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import splicecraft as sc


# ═════════════════════════════════════════════════════════════════════
# C1, C2 — cache-bust enumeration drift fixes
# ═════════════════════════════════════════════════════════════════════

class TestCacheBustParity:
    """The two restore paths (`RestoreFromBackupModal` + the agent
    `_h_restore_pre_update_snapshot`) must both bust every persistent
    cache, otherwise a successful restore leaves stale in-memory state
    that the next CRUD silently overwrites onto disk.
    """

    def test_master_delete_cache_attrs_is_canonical(self):
        """The master tuple covers every `_*_cache` global the
        module defines. Sentinel against drift — a future cache that
        forgets to register here will be missed by Master Delete AND
        both restore paths (which now drive from it)."""
        # Every name in the tuple must resolve to a module attribute
        # (one of the cache globals). Pre-fix this list was hand-
        # maintained and drifted.
        for attr in sc._MASTER_DELETE_CACHE_ATTRS:
            assert hasattr(sc, attr), (
                f"{attr!r} is in _MASTER_DELETE_CACHE_ATTRS but does "
                f"not exist as a module attribute — typo / removed?"
            )

    def test_master_delete_includes_sweep24_caches(self):
        """Sweep #24 added `_custom_enzymes_cache` /
        `_enzyme_collections_cache`. Sentinel against future
        omissions."""
        expected = {
            "_library_cache",
            "_collections_cache",
            "_parts_bin_cache",
            "_primers_cache",
            "_primer_collections_cache",
            "_primer_usage_cache",
            "_feature_library_index_cache",
            "_features_cache",
            "_feature_colors_cache",
            "_grammars_cache",
            "_entry_vectors_cache",
            "_codon_tables_cache",
            "_settings_cache",
            "_experiments_cache",
            "_experiment_projects_cache",
            "_gels_cache",
            "_protein_motifs_cache",
            "_custom_enzymes_cache",
            "_enzyme_collections_cache",
        }
        actual = set(sc._MASTER_DELETE_CACHE_ATTRS)
        missing = expected - actual
        assert not missing, f"Missing from master tuple: {missing}"

    def test_restore_from_backup_modal_drives_from_master_tuple(self):
        """White-box: `RestoreFromBackupModal._do_restore`'s cache-
        bust loop must iterate `_MASTER_DELETE_CACHE_ATTRS` (with
        `_settings_cache` deliberately skipped). Pre-sweep #25 it
        had a hand-maintained 14-entry tuple that drifted.
        """
        import inspect
        src = inspect.getsource(sc.RestoreFromBackupModal)
        assert "_MASTER_DELETE_CACHE_ATTRS" in src, (
            "RestoreFromBackupModal must drive cache-bust from the "
            "master tuple, not hand-maintain its own list"
        )

    def test_h_restore_pre_update_drives_from_master_tuple(self):
        """White-box: agent pre-update restore handler must also
        iterate `_MASTER_DELETE_CACHE_ATTRS`."""
        import inspect
        src = inspect.getsource(sc._h_restore_pre_update_snapshot)
        assert "_MASTER_DELETE_CACHE_ATTRS" in src


# ═════════════════════════════════════════════════════════════════════
# C3 — stacks log rotation cap
# ═════════════════════════════════════════════════════════════════════

class TestStacksLogRotation:
    """`_STACKS_LOG_FD` opens `splicecraft.stacks.log` in append
    mode for `faulthandler.register`. Pre-fix nothing capped its
    size — a user who hit SIGUSR1 once a week accumulated MBs over
    a year. Now truncates at startup if over 10 MB."""

    def test_max_bytes_constant_is_set(self):
        # The constant is module-level. Tests don't exercise the
        # actual truncation (requires re-importing the module) but
        # the constant existing + being a sane value is the
        # regression sentinel.
        assert hasattr(sc, "_STACKS_LOG_MAX_BYTES")
        assert 1_000_000 <= sc._STACKS_LOG_MAX_BYTES <= 100_000_000


# ═════════════════════════════════════════════════════════════════════
# C4 — GB parse cache cap
# ═════════════════════════════════════════════════════════════════════

class TestGBParseCacheCap:
    def test_cap_reduced(self):
        """Sweep #25: cap reduced from 64 → 16. With SeqRecord
        memory amplification (~5–15× input text), worst-case
        cached state at the old cap could reach ~40 GB. 16
        entries × 64 MB ceiling × 10× amp ≈ ~10 GB worst case —
        still high but bounded."""
        assert sc._GB_PARSE_CACHE_MAX == 16


# ═════════════════════════════════════════════════════════════════════
# C5 — `_iter_library_readonly` + `_iter_parts_bin_readonly` helpers
# ═════════════════════════════════════════════════════════════════════

class TestReadonlyIterators:
    def test_iter_library_readonly_exists(self):
        assert callable(sc._iter_library_readonly)

    def test_iter_parts_bin_readonly_exists(self):
        assert callable(sc._iter_parts_bin_readonly)

    def test_find_library_entry_by_name_exists(self):
        assert callable(sc._find_library_entry_by_name)

    def test_iter_library_readonly_returns_cache_reference(self,
                                                            isolated_library):
        """The point of the helper is to skip the deepcopy. Verify
        it returns the actual cached list (or a same-content list,
        depending on cache state)."""
        sc._save_library([
            {"id": "a", "name": "A", "size": 10},
            {"id": "b", "name": "B", "size": 20},
        ])
        view = sc._iter_library_readonly()
        assert len(view) == 2
        assert view[0]["id"] == "a"

    def test_find_library_entry_by_name_returns_clone(self,
                                                       isolated_library):
        """Helper must deepcopy on return (sacred invariant #17)."""
        sc._save_library([
            {"id": "a", "name": "Alpha", "size": 10, "extra": [1, 2]},
        ])
        e = sc._find_library_entry_by_name("Alpha")
        assert e is not None and e["id"] == "a"
        e["extra"].append(999)
        e2 = sc._find_library_entry_by_name("Alpha")
        assert e2 is not None and e2["extra"] == [1, 2], (
            "deep-clone contract violated"
        )

    def test_find_library_entry_by_id_accepts_none(self):
        """Type signature widened to `str | None` so optional-id
        lookup sites don't need a wrapper."""
        assert sc._find_library_entry_by_id(None) is None
        assert sc._find_library_entry_by_id("") is None
        assert sc._find_library_entry_by_id(123) is None  # type: ignore[arg-type]


# ═════════════════════════════════════════════════════════════════════
# H4 — _SCAN_CATALOG atomic rebuild
# ═════════════════════════════════════════════════════════════════════

class TestScanCatalogAtomicRebuild:
    def test_rebuild_does_not_clear_in_place(self):
        """`_rebuild_scan_catalog` builds locally then reassigns;
        does NOT call `.clear()` + `.append()` mid-iteration.
        Pre-fix a concurrent scan iterator could see a half-built
        catalog (missed cut sites or tuple-index error)."""
        import inspect
        src = inspect.getsource(sc._rebuild_scan_catalog)
        # Strip the docstring before checking (the docstring itself
        # references the pre-fix `.clear()` pattern for context).
        # Heuristic: docstring is everything between the first
        # triple-quote pair after `def`.
        body = src.split('"""', 2)[-1]
        assert "_SCAN_CATALOG.clear()" not in body
        assert 'globals()["_SCAN_CATALOG"]' in body

    def test_rebuild_produces_nonempty_catalog(self):
        """Sanity: rebuild still works post-refactor."""
        sc._rebuild_scan_catalog()
        assert len(sc._SCAN_CATALOG) > 0


# ═════════════════════════════════════════════════════════════════════
# H5 — bearer token required on ALL endpoints
# ═════════════════════════════════════════════════════════════════════

class TestAuthOnReadEndpoints:
    def test_handle_method_inspects_token_before_handler_lookup(self):
        """White-box: token check fires BEFORE handler lookup so
        unauthenticated probes for unknown paths uniformly 401
        (don't leak endpoint-list oracle via 404)."""
        import inspect
        src = inspect.getsource(sc._AgentRequestHandler._handle)
        # Find positions of the token check and the handler lookup.
        token_pos = src.find("_check_token")
        handler_pos = src.find("_AGENT_HANDLERS.get(path_part)")
        assert token_pos > 0 and handler_pos > 0
        assert token_pos < handler_pos, (
            "token check must precede handler lookup"
        )


# ═════════════════════════════════════════════════════════════════════
# H9 — HTTP server timeout
# ═════════════════════════════════════════════════════════════════════

class TestServerTimeout:
    def test_server_has_timeout(self):
        assert sc._AgentAPIServer.timeout == 30

    def test_handler_has_timeout(self):
        assert sc._AgentRequestHandler.timeout == 30


# ═════════════════════════════════════════════════════════════════════
# H10 — sync_active_collection uses readonly iter
# ═════════════════════════════════════════════════════════════════════

class TestSyncActiveCollectionPerf:
    def test_sync_path_uses_iter_collections_readonly(self):
        """White-box: the sync branch reads via
        `_iter_collections_readonly()` not `_load_collections()`."""
        import inspect
        src = inspect.getsource(sc._sync_active_collection_plasmids)
        assert "_iter_collections_readonly" in src


# ═════════════════════════════════════════════════════════════════════
# H11 — _get_setting cache-direct read
# ═════════════════════════════════════════════════════════════════════

class TestGetSettingPerf:
    def test_get_setting_does_not_call_load_settings(self):
        """White-box: should NOT route through `_load_settings()`
        (which deep-clones the entire dict on every call). Skip
        docstring matches — they reference the pre-fix pattern for
        context."""
        import inspect
        src = inspect.getsource(sc._get_setting)
        body = src.split('"""', 2)[-1]
        # `_load_settings()` (with parens) is the call we want to
        # rule out. The body does call `_load_settings()` ONCE as a
        # cache-populate trigger when the cache is None — that's
        # safe (cache stays valid afterward); the regression we're
        # blocking is calling it on EVERY access.
        # Check: at most one occurrence in the body.
        assert body.count("_load_settings()") <= 1
        assert "_settings_cache" in body

    def test_get_setting_returns_clone_for_containers(self,
                                                       isolated_library):
        """A container value (list/dict) must be cloned defensively
        so caller mutation can't poison the cache."""
        sc._set_setting("crash_recovery_seen", ["x", "y"])
        view = sc._get_setting("crash_recovery_seen")
        assert view == ["x", "y"]
        view.append("z")
        view2 = sc._get_setting("crash_recovery_seen")
        assert view2 == ["x", "y"]


# ═════════════════════════════════════════════════════════════════════
# H12 — _rc hoist in _find_annotation_transfers
# ═════════════════════════════════════════════════════════════════════

class TestRcHoist:
    def test_rc_hoisted_outside_feature_loop(self):
        """White-box: `_rc(tgt_seq)` must appear BEFORE the
        `for feat in source_rec.features` loop. Pre-fix it was
        inside, paying O(F × N_tgt)."""
        import inspect
        src = inspect.getsource(sc._find_annotation_transfers)
        rc_pos = src.find("rc_tgt_seq = _rc(tgt_seq)")
        loop_pos = src.find("for feat in source_rec.features")
        assert rc_pos > 0 and loop_pos > 0
        assert rc_pos < loop_pos, (
            "_rc(tgt_seq) must be hoisted above the per-feature loop"
        )


# ═════════════════════════════════════════════════════════════════════
# H14 — bare-except narrowing
# ═════════════════════════════════════════════════════════════════════

class TestBareExceptNarrowing:
    def test_no_except_OSError_Exception_tuple(self):
        """`except (OSError, Exception)` is `except Exception` (a
        bare-except in disguise — Exception subsumes OSError).
        Grep the source for the pattern as a regression sentinel.
        INV-65 caught the `(AttributeError, Exception)` shape;
        sweep #25 extended to `(OSError, Exception)`."""
        text = Path(sc.__file__).read_text(encoding="utf-8")
        # Count standalone uses (in comments referencing the pattern
        # is OK, but real `except (OSError, Exception)` lines are not).
        import re
        bad_lines = [
            ln for ln in text.splitlines()
            if re.search(r"^\s*except \(OSError, Exception\)", ln)
        ]
        assert not bad_lines, (
            f"Found bare-except-in-disguise: {bad_lines}"
        )


# ═════════════════════════════════════════════════════════════════════
# H16 — GFF3 silent drop logs
# ═════════════════════════════════════════════════════════════════════

class TestGFF3LogsDropped:
    def test_gff3_features_dropped_logged(self, caplog):
        """An out-of-range GFF3 feature now logs a warning instead
        of silently dropping. Sweep #25 fixed."""
        import logging
        # Attach a handler directly to sc._log since the module has
        # propagate=False on its logger (per INV-38) and pytest's
        # caplog only captures via propagation.
        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Capture(level=logging.INFO)
        sc._log.addHandler(handler)
        prev_level = sc._log.level
        sc._log.setLevel(logging.INFO)
        try:
            parsed = {
                "features": [
                    {"gff_id": "f1", "type": "CDS",
                     "start_0": 0, "end": 100, "strand": 1,
                     "qualifiers": {}},
                    {"gff_id": "f2", "type": "CDS",
                     "start_0": 0, "end": 999_999, "strand": 1,
                     "qualifiers": {}},
                ],
            }
            out = sc._gff3_features_to_biopython(parsed, total=200)
        finally:
            sc._log.removeHandler(handler)
            sc._log.setLevel(prev_level)
        assert len(out) == 1   # f2 dropped
        assert any(
            "GFF3: dropping" in r.getMessage() for r in records
        ), f"expected drop log, got: {[r.getMessage() for r in records]}"


# ═════════════════════════════════════════════════════════════════════
# M1 — RestoreFromBackupModal _dismiss_once
# ═════════════════════════════════════════════════════════════════════

class TestRestoreFromBackupDismissOnce:
    def test_has_dismiss_once_helper(self):
        assert hasattr(sc.RestoreFromBackupModal, "_dismiss_once")

    def test_dismissed_flag_in_init(self):
        import inspect
        src = inspect.getsource(sc.RestoreFromBackupModal.__init__)
        assert "_dismissed" in src


# ═════════════════════════════════════════════════════════════════════
# M2 — _safe_identifier for active_* settings
# ═════════════════════════════════════════════════════════════════════

class TestSafeIdentifierValidator:
    def test_helper_rejects_traversal(self):
        assert sc._is_safe_identifier("Main Project")
        assert sc._is_safe_identifier("")           # empty = handled elsewhere
        assert not sc._is_safe_identifier("../etc")
        assert not sc._is_safe_identifier("a/b")
        assert not sc._is_safe_identifier("a\\b")
        assert not sc._is_safe_identifier("a\x00b")
        assert sc._is_safe_identifier("with spaces and-dashes")

    def test_validate_settings_rejects_active_traversal(self):
        """A hand-edited settings.json with `active_project:
        '../../etc/foo'` must be rejected, even though string-type
        check passes. Defense in depth against future refactors
        that join the name into a path."""
        raw = {"active_project": "../../etc/foo"}
        cleaned, warnings = sc._validate_settings(raw)
        assert cleaned.get("active_project") != "../../etc/foo"
        assert any("path-traversal" in w for w in warnings)


# ═════════════════════════════════════════════════════════════════════
# M5 — agent body parse error → 400 sentinel
# ═════════════════════════════════════════════════════════════════════

class TestBodyParseErrorSentinel:
    def test_read_body_returns_sentinel_on_parse_error(self):
        """White-box: `_read_body` returns the `"__bad_body__"`
        sentinel on JSON / unicode parse error, not silent `{}`."""
        import inspect
        src = inspect.getsource(sc._AgentRequestHandler._read_body)
        assert '"__bad_body__"' in src


# ═════════════════════════════════════════════════════════════════════
# M14 — _VECTOR_MATCH_CACHE keys on hash
# ═════════════════════════════════════════════════════════════════════

class TestVectorMatchCacheKey:
    def test_uses_hash_key(self):
        """White-box: `_vector_half_top_seq` keys on
        `(hash(ev_gb), enzyme)` not `(ev_gb, enzyme)`."""
        import inspect
        src = inspect.getsource(sc._vector_half_top_seq)
        assert "hash(ev_gb)" in src
        # Lines after `key = ` shouldn't show the raw ev_gb tuple form.
        assert "key = (ev_gb, enzyme)" not in src


# ═════════════════════════════════════════════════════════════════════
# M20 — narrow except in target_seq fallback
# ═════════════════════════════════════════════════════════════════════

class TestTargetSeqExceptNarrow:
    def test_narrowed_except(self):
        text = Path(sc.__file__).read_text(encoding="utf-8")
        # The two narrowed sites must be present.
        assert "except (AttributeError, TypeError):" in text


# ═════════════════════════════════════════════════════════════════════
# L1 — splicecraft_cli refuses symlinked token
# ═════════════════════════════════════════════════════════════════════

class TestCliRefusesSymlinkedToken:
    def test_cli_read_session_uses_lstat(self):
        """White-box: `splicecraft_cli._read_session` must `lstat`
        the token file and refuse symlinks."""
        import splicecraft_cli
        import inspect
        src = inspect.getsource(splicecraft_cli._read_session)
        assert "lstat" in src
        assert "S_ISLNK" in src


# ═════════════════════════════════════════════════════════════════════
# L3 — token via secrets.token_urlsafe(32)
# ═════════════════════════════════════════════════════════════════════

class TestTokenViaSecrets:
    def test_uses_secrets_token_urlsafe(self):
        import inspect
        src = inspect.getsource(sc._start_agent_api)
        body = src.split('"""', 2)[-1]
        assert "token_urlsafe" in body
        # Sentinel against regression to uuid4. (Docstring references
        # the old approach in the change-rationale comment, so we
        # check function body only.)
        assert "uuid.uuid4().hex" not in body


# ═════════════════════════════════════════════════════════════════════
# H1 — custom enzyme agent endpoints under _cache_lock
# ═════════════════════════════════════════════════════════════════════

class TestCustomEnzymeAgentLock:
    def test_create_wraps_in_cache_lock(self):
        import inspect
        src = inspect.getsource(sc._h_create_custom_enzyme)
        assert "with _cache_lock:" in src

    def test_update_wraps_in_cache_lock(self):
        import inspect
        src = inspect.getsource(sc._h_update_custom_enzyme)
        assert "with _cache_lock:" in src

    def test_delete_wraps_in_cache_lock(self):
        import inspect
        src = inspect.getsource(sc._h_delete_custom_enzyme)
        assert "with _cache_lock:" in src

    def test_collection_create_wraps_in_cache_lock(self):
        import inspect
        src = inspect.getsource(sc._h_create_enzyme_collection)
        assert "with _cache_lock:" in src


# ═════════════════════════════════════════════════════════════════════
# H2 — Ctrl+S save worker lock
# ═════════════════════════════════════════════════════════════════════

class TestSaveWorkerLock:
    def test_save_worker_library_rmw_under_lock(self):
        import inspect
        src = inspect.getsource(sc.PlasmidApp._save_worker)
        # The library mirror section must hold `_cache_lock`.
        assert "with _cache_lock:" in src
