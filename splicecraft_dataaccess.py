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


# ── Protein motifs (builtin defaults merged with user edits on read) ──────────
# `_PROTEIN_MOTIFS` (the builtin motif library) is used only by
# `_load_protein_motifs` (merge with the user's protein_motifs.json), so it
# travels here with the accessors.


# Builtin protein-motif / domain library — common tags + linkers +
# protease sites a synthetic-biology user reaches for when composing
# a recombinant protein. Stored as a module-level list so it's
# always available without needing a user-managed file. Entry shape
# mirrors the DNA feature library so the side-panel renderer can
# reuse the same row-build code.
# Sweep #16 (2026-05-21): each built-in motif carries its own unique
# ``color`` hex so the user can tell them apart at a glance in the
# motif library AND in the dithered feature bars above the AA row.
# The palette spreads visually within each family (e.g. Tags shift
# from deep blue → cyan → indigo → violet so a side-by-side stack of
# His6 + FLAG + Myc + V5 reads as four obviously different bars).
# User edits via the Edit button persist to `protein_motifs.json`
# and override these defaults.
_PROTEIN_MOTIFS: list[dict] = [
    # ── Affinity tags (cool spectrum: blue → cyan → indigo → violet) ──
    {"name": "His6",        "feature_type": "Tag",
     "sequence": "HHHHHH", "color": "#1E40AF",
     "description": "Hexahistidine affinity tag (Ni-NTA / IMAC purification)."},
    {"name": "His8",        "feature_type": "Tag",
     "sequence": "HHHHHHHH", "color": "#3B82F6",
     "description": "Octahistidine — tighter Ni-NTA binding than 6xHis."},
    {"name": "His10",       "feature_type": "Tag",
     "sequence": "HHHHHHHHHH", "color": "#60A5FA",
     "description": "Decahistidine — even tighter, for harsh-wash purification."},
    {"name": "FLAG",        "feature_type": "Tag",
     "sequence": "DYKDDDDK", "color": "#0E7490",
     "description": "FLAG tag (anti-FLAG M2 affinity purification)."},
    {"name": "3xFLAG",      "feature_type": "Tag",
     "sequence": "DYKDHDGDYKDHDIDYKDDDDK", "color": "#06B6D4",
     "description": "Triple FLAG — higher sensitivity for low-expression targets."},
    {"name": "HA",          "feature_type": "Tag",
     "sequence": "YPYDVPDYA", "color": "#67E8F9",
     "description": "Influenza hemagglutinin epitope (anti-HA antibodies)."},
    {"name": "Myc",         "feature_type": "Tag",
     "sequence": "EQKLISEEDL", "color": "#4338CA",
     "description": "c-Myc epitope tag (9E10 antibody)."},
    {"name": "V5",          "feature_type": "Tag",
     "sequence": "GKPIPNPLLGLDST", "color": "#6366F1",
     "description": "V5 epitope tag (paramyxovirus)."},
    {"name": "Strep-II",    "feature_type": "Tag",
     "sequence": "WSHPQFEK", "color": "#A78BFA",
     "description": "Strep-Tactin affinity tag (mild biotin elution)."},
    {"name": "T7",          "feature_type": "Tag",
     "sequence": "MASMTGGQQMG", "color": "#7C3AED",
     "description": "T7 leader peptide (anti-T7 monoclonal)."},
    # ── Localisation signals (warm yellows / golds) ────────────────
    {"name": "NLS (SV40)",  "feature_type": "Signal",
     "sequence": "PKKKRKV", "color": "#EAB308",
     "description": "Classical SV40 large T-antigen nuclear localisation signal."},
    {"name": "NLS (bipartite)", "feature_type": "Signal",
     "sequence": "KRPAATKKAGQAKKKK", "color": "#FBBF24",
     "description": "Nucleoplasmin bipartite NLS."},
    {"name": "NES",         "feature_type": "Signal",
     "sequence": "LPPLERLTL", "color": "#F59E0B",
     "description": "HIV-Rev nuclear export signal (CRM1-dependent)."},
    # ── Linkers (neutral greys, light → dark, warm at the end) ─────
    {"name": "GSG",         "feature_type": "Linker",
     "sequence": "GSG", "color": "#94A3B8",
     "description": "Minimal flexible linker."},
    {"name": "GGS",         "feature_type": "Linker",
     "sequence": "GGS", "color": "#64748B",
     "description": "Short flexible linker."},
    {"name": "(GGGGS)x3",   "feature_type": "Linker",
     "sequence": "GGGGSGGGGSGGGGS", "color": "#475569",
     "description": "Classic flexible linker for scFv / fusion proteins."},
    {"name": "(GGGGS)x4",   "feature_type": "Linker",
     "sequence": "GGGGSGGGGSGGGGSGGGGS", "color": "#57534E",
     "description": "Longer flexible linker for domain separation."},
    {"name": "EAAAK x3",    "feature_type": "Linker",
     "sequence": "EAAAKEAAAKEAAAK", "color": "#78716C",
     "description": "Rigid α-helical linker."},
    # ── Protease cleavage sites (reds → pinks) ──────────────────────
    {"name": "TEV",         "feature_type": "Cleavage",
     "sequence": "ENLYFQG", "color": "#B91C1C",
     "description": "TEV protease site (cuts between Q and G)."},
    {"name": "PreScission", "feature_type": "Cleavage",
     "sequence": "LEVLFQGP", "color": "#DC2626",
     "description": "HRV 3C / PreScission protease site (cuts between Q and G)."},
    {"name": "Thrombin",    "feature_type": "Cleavage",
     "sequence": "LVPRGS", "color": "#F87171",
     "description": "Thrombin cleavage site."},
    {"name": "Factor Xa",   "feature_type": "Cleavage",
     "sequence": "IEGR", "color": "#EC4899",
     "description": "Factor Xa protease site."},
    {"name": "Furin",       "feature_type": "Cleavage",
     "sequence": "RRRR", "color": "#DB2777",
     "description": "Furin recognition site (R-X-K/R-R minimal)."},
    # ── Self-cleaving 2A peptides (greens) ─────────────────────────
    {"name": "P2A",         "feature_type": "2A",
     "sequence": "GSGATNFSLLKQAGDVEENPGP", "color": "#15803D",
     "description": "Porcine teschovirus 2A self-cleaving peptide."},
    {"name": "T2A",         "feature_type": "2A",
     "sequence": "GSGEGRGSLLTCGDVEENPGP", "color": "#16A34A",
     "description": "Thosea asigna 2A peptide."},
    {"name": "E2A",         "feature_type": "2A",
     "sequence": "GSGQCTNYALLKLAGDVESNPGP", "color": "#22C55E",
     "description": "Equine rhinitis A 2A peptide."},
    {"name": "F2A",         "feature_type": "2A",
     "sequence": "GSGVKQTLNFDLLKLAGDVESNPGP", "color": "#10B981",
     "description": "Foot-and-mouth-disease virus 2A peptide."},
    # ── Common functional motifs (fuchsias / magentas) ─────────────
    {"name": "Kozak start", "feature_type": "Motif",
     "sequence": "M", "color": "#C026D3",
     "description": "Start codon (methionine) — required N-terminal."},
    {"name": "Stop",        "feature_type": "Motif",
     "sequence": "*", "color": "#A21CAF",
     "description": "Translation stop codon."},
    {"name": "FLAG+Stop",   "feature_type": "Motif",
     "sequence": "DYKDDDDK*", "color": "#D946EF",
     "description": "FLAG tag followed by stop — quick C-terminal tagging."},
]


def _load_protein_motifs() -> list[dict]:
    """Return the merged protein-motif library: built-in entries
    overridden by user edits stored in `protein_motifs.json`. Deep-
    copied so callers can mutate freely.

    Sweep #26: double-checked locking — cache-hit fast path is lock-free."""
    cached = _state._protein_motifs_cache
    if cached is not None:
        return _typed_clone(cached)
    with _state._cache_lock:
        if _state._protein_motifs_cache is None:
            user_entries, warning = _safe_load_json(
                _state._PROTEIN_MOTIFS_FILE, "Protein motifs",
            )
            if warning:
                _log.warning(warning)
            user_entries = [e for e in user_entries if isinstance(e, dict)]
            user_by_name: dict[str, dict] = {
                str(e.get("name") or ""): e for e in user_entries if e.get("name")
            }
            merged: list[dict] = []
            builtin_names: set[str] = set()
            for builtin in _PROTEIN_MOTIFS:
                name = str(builtin.get("name") or "")
                builtin_names.add(name)
                merged.append(user_by_name.get(name, dict(builtin)))
            # User-added novel motifs (name NOT in builtins) append in
            # insertion order so user-defined items land predictably.
            for e in user_entries:
                name = str(e.get("name") or "")
                if name and name not in builtin_names:
                    merged.append(dict(e))
            _state._protein_motifs_cache = merged
        return _typed_clone(_state._protein_motifs_cache)


def _save_protein_motifs(entries: list[dict]) -> None:
    """Persist `entries` (user-modified motifs only — not the full
    merged list) to `protein_motifs.json`. Caller passes the list of
    entries to STORE; the merge with built-ins happens on read."""
    with _state._cache_lock:
        _safe_save_json(_state._PROTEIN_MOTIFS_FILE, entries, "Protein motifs")
        # Invalidate the cache; next _load_protein_motifs rebuilds
        # the merged list. Reseating with the user-only list would
        # leave the merged form stale.
        _state._protein_motifs_cache = None
