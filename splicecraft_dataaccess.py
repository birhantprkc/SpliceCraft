"""splicecraft_dataaccess — the user-data access layer (Phase D, layer 1).

First piece: `_typed_clone`, the deep-clone-on-read/write helper that enforces
the data layer's SACRED invariant #17 — a caller mutating a returned entry
can't poison the in-memory cache, and a caller mutating its argument after a
save can't leak post-save edits into the next reader. Every `_load_X` returns
`_typed_clone(cache)`; every `_save_X` re-seats the cache via
`_typed_clone(entries)`. It belongs here because that contract IS the data
layer.

This module grows to hold the domain `_load_X` / `_save_X` accessors as Phase D
moves them off the hub, so the modal / screen / agent siblings can import them
here instead of importing the hub (which would be a cycle). It imports ONLY
stdlib + lower/same-layer siblings (splicecraft_state, splicecraft_persistence,
splicecraft_logging); the hub re-exports every name (`from splicecraft_dataaccess
import _typed_clone as _typed_clone` ...) so `sc.<name>` + every existing call
site resolve unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from typing import TypeVar as _TypeVar

import splicecraft_state as _state
from splicecraft_logging import _log
from splicecraft_persistence import _safe_load_json, _safe_save_json

# Immutable types `_typed_clone` returns as-is (no copy needed). Everything else
# recurses (dict / list / tuple) or falls through to `deepcopy` for any
# unexpected type, so the sacred-#17 contract ("caller-side mutations can't
# poison the cache") is preserved end to end.
_IMMUTABLE_CLONE_TYPES = (str, int, float, bool, bytes, type(None))
_TC = _TypeVar("_TC")


def _typed_clone(obj: _TC) -> _TC:
    t = type(obj)
    if t is dict:
        return {k: _typed_clone(v) for k, v in obj.items()}  # type: ignore[return-value]
    if t is list:
        return [_typed_clone(v) for v in obj]  # type: ignore[return-value]
    if t is tuple:
        return tuple(_typed_clone(v) for v in obj)  # type: ignore[return-value]
    if isinstance(obj, _IMMUTABLE_CLONE_TYPES):
        return obj
    return deepcopy(obj)


# ── Feature colours (type → colour map) ─────────────────────────────────────
# First domain accessor cluster moved off the hub (Phase D). Clean closure:
# splicecraft_state (cache + file path + the save lock), splicecraft_persistence
# (safe load/save), the logger, and `_typed_clone` above. No migration / mirror
# / schema validation, so it's the proving cut for the accessor-extraction
# pattern. The save lock is reached as `_state._cache_lock` (the hub keeps a
# same-object `_cache_lock` alias for its own sites).


def _load_feature_colors() -> dict[str, str]:
    """Return the user's customised type → colour map. Missing file / empty
    entries → empty dict. Callers should combine this with
    ``_DEFAULT_TYPE_COLORS`` — that precedence is handled by
    ``_resolve_feature_color`` (hub-side).

    Uses ``_typed_clone`` on read so the cache contract matches every
    other library (invariant #17). Values are ``str`` (immutable) today,
    so the practical risk is nil — but using the canonical helper keeps
    the pattern honest in case a future schema bump adds a nested
    structure to the value side.
    """
    # Sweep #26: double-checked locking — cache-hit fast path is lock-free.
    cached = _state._feature_colors_cache
    if cached is not None:
        return _typed_clone(cached)
    with _state._cache_lock:
        if _state._feature_colors_cache is None:
            entries, warning = _safe_load_json(_state._FEATURE_COLORS_FILE, "Feature colors")
            if warning:
                _log.warning(warning)
            result: dict[str, str] = {}
            for e in entries:
                if not isinstance(e, dict):
                    continue
                ft  = e.get("feature_type")
                col = e.get("color")
                if isinstance(ft, str) and ft and isinstance(col, str) and col:
                    result[ft] = col
            _state._feature_colors_cache = _typed_clone(result)
        return _typed_clone(_state._feature_colors_cache)


def _save_feature_colors(mapping: dict[str, str]) -> None:
    """Persist the type → colour map. Written as a list of ``{"feature_type":
    ..., "color": ...}`` dicts so it shares the schema-envelope shape with
    the other libraries (sacred invariant #7).

    Re-seats the cache via ``_typed_clone`` so a caller that keeps
    mutating its mapping after the save doesn't leak post-save edits
    into the next reader (invariant #17, full deepcopy-on-save side).
    """
    entries = [{"feature_type": ft, "color": col}
               for ft, col in mapping.items()]
    with _state._cache_lock:
        _safe_save_json(_state._FEATURE_COLORS_FILE, entries, "Feature colors")
        _state._feature_colors_cache = _typed_clone(mapping)


# ── Enzyme collections ──────────────────────────────────────────────────────


def _load_enzyme_collections() -> list[dict]:
    """Deep-copy on read so callers can mutate freely; pitfall #17.

    Sweep #26: double-checked locking — cache-hit fast path is lock-free."""
    cached = _state._enzyme_collections_cache
    if cached is not None:
        return _typed_clone(cached)
    with _state._cache_lock:
        if _state._enzyme_collections_cache is None:
            entries, warning = _safe_load_json(
                _state._ENZYME_COLLECTIONS_FILE, "Enzyme collections",
            )
            if warning:
                _log.warning(warning)
            _state._enzyme_collections_cache = [
                e for e in entries if isinstance(e, dict)
            ]
        return _typed_clone(_state._enzyme_collections_cache)


def _save_enzyme_collections(entries: list[dict]) -> None:
    with _state._cache_lock:
        _safe_save_json(
            _state._ENZYME_COLLECTIONS_FILE, entries, "Enzyme collections",
        )
        _state._enzyme_collections_cache = _typed_clone(entries)


# ── Experiment projects ─────────────────────────────────────────────────────


def _load_experiment_projects() -> "list[dict]":
    """Return a deepcopy of the experiment-projects list so callers can
    mutate entries (rename, edit experiments list) without poisoning
    the in-memory cache (invariant #17).

    Sweep #26: double-checked locking — cache-hit fast path is lock-free.
    """
    cached = _state._experiment_projects_cache
    if cached is not None:
        return _typed_clone(cached)
    with _state._cache_lock:
        if _state._experiment_projects_cache is None:
            entries, warning = _safe_load_json(
                _state._EXPERIMENT_PROJECTS_FILE, "Experiment projects",
            )
            if warning:
                _log.warning(warning)
            _state._experiment_projects_cache = [
                e for e in entries if isinstance(e, dict)
            ]
    return _typed_clone(_state._experiment_projects_cache)


def _save_experiment_projects(entries: "list[dict]") -> None:
    """Persist the full experiment-projects list through the four-layer
    data-safety net (invariant #31). Deepcopies into the cache so caller
    mutations after save can't poison subsequent loaders (invariant #17),
    under the save lock (invariant #41 — concurrency)."""
    with _state._cache_lock:
        _safe_save_json(
            _state._EXPERIMENT_PROJECTS_FILE, entries, "Experiment projects",
        )
        _state._experiment_projects_cache = _typed_clone(entries)


# ── Gels ────────────────────────────────────────────────────────────────────


def _load_gels() -> "list[dict]":
    """Cached + deepcopy-on-read load (invariant #17). Filters non-dict
    entries defensively (hand-edited JSON / schema drift). Sweep #26
    double-checked locking: the cache-hit fast path runs without the lock
    so a worker holding it can't freeze every UI-thread `_load_gels`; only
    the cold populate-from-disk path acquires it, double-checking after."""
    cached = _state._gels_cache
    if cached is not None:
        return _typed_clone(cached)
    with _state._cache_lock:
        if _state._gels_cache is None:
            entries, warning = _safe_load_json(_state._GELS_FILE, "Gels")
            if warning:
                _log.warning(warning)
            _state._gels_cache = [e for e in entries if isinstance(e, dict)]
        return _typed_clone(_state._gels_cache)


def _save_gels(entries: "list[dict]") -> None:
    """Persist through `_safe_save_json` (atomic + four-layer data-safety).
    Takes the save lock so concurrent saves can't desync cache vs disk
    (invariant #41)."""
    with _state._cache_lock:
        _safe_save_json(_state._GELS_FILE, entries, "Gels")
        _state._gels_cache = _typed_clone(entries)
