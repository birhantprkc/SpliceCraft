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
from datetime import date as _date
from typing import TypeVar as _TypeVar

import splicecraft_state as _state
from splicecraft_logging import _log, _log_event
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


# ── Codon-table registry (MISSION-CRITICAL: _codon_optimize depends on it) ──
# The genetic-code map + builtin K12 table + the raw<->json converters +
# the load/save accessors. `_codon_raw_from_json` forces each codon's AA to
# the standard genetic code (never the stored label) so a corrupt table
# can't feed a wrong AA->codon grouping into the optimizer. `_CODON_GENETIC_CODE`
# is also used hub-side (optimizer + translation, 17 refs) via the re-export.


_CODON_GENETIC_CODE: dict[str, str] = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L",
    "CTT": "L", "CTC": "L", "CTA": "L", "CTG": "L",
    "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M",
    "GTT": "V", "GTC": "V", "GTA": "V", "GTG": "V",
    "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S",
    "CCT": "P", "CCC": "P", "CCA": "P", "CCG": "P",
    "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T",
    "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A",
    "TAT": "Y", "TAC": "Y", "TAA": "*", "TAG": "*",
    "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q",
    "AAT": "N", "AAC": "N", "AAA": "K", "AAG": "K",
    "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E",
    "TGT": "C", "TGC": "C", "TGA": "*", "TGG": "W",
    "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R",
    "GGT": "G", "GGC": "G", "GGA": "G", "GGG": "G",
}


_CODON_BUILTIN_K12: dict[str, tuple[str, int]] = {
    "GGG": ("G",  44), "GGA": ("G",  47), "GGT": ("G", 109), "GGC": ("G", 171),
    "GAG": ("E",  94), "GAA": ("E", 224), "GAT": ("D", 194), "GAC": ("D", 105),
    "GTG": ("V", 135), "GTA": ("V",  59), "GTT": ("V",  86), "GTC": ("V",  60),
    "GCG": ("A", 197), "GCA": ("A", 108), "GCT": ("A",  55), "GCC": ("A", 162),
    "AGG": ("R",   8), "AGA": ("R",   7), "AGT": ("S",  37), "AGC": ("S",  85),
    "AAG": ("K",  62), "AAA": ("K", 170), "AAT": ("N", 112), "AAC": ("N", 125),
    "ATG": ("M", 127), "ATA": ("I",  19), "ATT": ("I", 156), "ATC": ("I",  93),
    "ACG": ("T",  59), "ACA": ("T",  33), "ACT": ("T",  41), "ACC": ("T", 117),
    "TGG": ("W",  55), "TGT": ("C",  30), "TGC": ("C",  41),
    "TAT": ("Y",  86), "TAC": ("Y",  75),
    "TTG": ("L",  61), "TTA": ("L",  78), "TTT": ("F", 101), "TTC": ("F",  77),
    "TCG": ("S",  41), "TCA": ("S",  40), "TCT": ("S",  29), "TCC": ("S",  28),
    "CGG": ("R",  21), "CGA": ("R",  22), "CGT": ("R", 108), "CGC": ("R", 133),
    "CAG": ("Q", 142), "CAA": ("Q",  62), "CAT": ("H",  81), "CAC": ("H",  67),
    "CTG": ("L", 240), "CTA": ("L",  27), "CTT": ("L",  61), "CTC": ("L",  54),
    "CCG": ("P", 137), "CCA": ("P",  34), "CCT": ("P",  43), "CCC": ("P",  33),
    "TAA": ("*",   9), "TAG": ("*",   0), "TGA": ("*",   5),
}


def _codon_raw_to_json(raw: dict) -> dict:
    """Convert in-memory {codon: (aa, count)} → JSON-safe {codon: [aa, count]}."""
    return {c: [aa, int(n)] for c, (aa, n) in raw.items()}


def _codon_raw_from_json(blob: dict) -> dict:
    """Inverse of `_codon_raw_to_json`. Accepts tuples or 2-item lists.

    HARDENED (2026-06-12): the codon KEY must be a real codon and its
    amino acid is forced to the standard genetic code — never trusted from
    the stored label. A corrupted or hand-edited `codon_tables.json` (e.g.
    `ATG` mislabelled `L`, or a 2-nt key) would otherwise feed a wrong
    AA→codon grouping straight into `_codon_optimize`, silently breaking
    the round-trip / no-codon→AA-mismatch invariant the optimizer
    guarantees (a mission-critical, zero-tolerance subsystem). This mirrors
    the validation the interactive TSV importer already enforces
    (`_parse_codon_tsv`). Unparseable rows are dropped (not raised) so a
    partly-corrupt table still loads its good rows — and a dropped codon
    surfaces loudly later as a "missing amino acid" optimizer error rather
    than as silent wrong DNA."""
    out: dict = {}
    if not isinstance(blob, dict):
        return out
    for c, v in blob.items():
        if not (isinstance(v, (list, tuple)) and len(v) == 2):
            continue
        codon = str(c).upper().replace("U", "T")
        aa = _CODON_GENETIC_CODE.get(codon)
        if aa is None:
            continue                      # bad length / non-ACGT / unknown codon
        try:
            count = int(v[1])
        except (TypeError, ValueError):
            continue
        if count < 0:
            continue
        out[codon] = (aa, count)
    return out


def _codon_tables_load() -> list[dict]:
    """Load codon-table registry. Returns a deep copy so caller-side
    mutation of the `raw` dict (e.g. an in-progress Kazusa import edit)
    can't poison the cache — matches invariant #17. Seeds built-in
    E. coli K12 on first run so the library is never empty."""
    if _state._codon_tables_cache is not None:
        return _typed_clone(_state._codon_tables_cache)
    entries, warning = _safe_load_json(_state._CODON_TABLES_FILE, "Codon table library")
    if warning:
        _log.warning("Codon table library: %s", warning)
    fixed: list = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        raw = _codon_raw_from_json(e.get("raw", {}))
        if not raw:
            continue
        fixed.append({
            "name":   e.get("name", "?"),
            "taxid":  str(e.get("taxid", "")),
            "source": e.get("source", "user"),
            "added":  e.get("added", ""),
            "raw":    raw,
        })
    # Seed built-in K12 if not present
    if not any(e.get("taxid") == "83333" for e in fixed):
        fixed.insert(0, {
            "name":   "E. coli K12",
            "taxid":  "83333",
            "source": "builtin",
            "added":  _date.today().isoformat(),
            "raw":    dict(_CODON_BUILTIN_K12),
        })
        _state._codon_tables_cache = fixed
        # Module-level seed path — no `app` context to notify. Log-only
        # so a disk-full first launch surfaces in the log bundle and
        # the cache (in memory) still has the K12 seed.
        try:
            _codon_tables_save(fixed)
        except (OSError, RuntimeError):
            _log.exception("Codon tables: K12 seed save failed (in-memory "
                           "cache populated; disk write deferred)")
    else:
        _state._codon_tables_cache = fixed
    return _typed_clone(_state._codon_tables_cache)


def _codon_tables_save(entries: list[dict]) -> None:
    """Persist registry to disk via _safe_save_json (atomic, .bak).
    Re-seats the cache with a deep copy so callers that retain a reference
    to the input list (and continue mutating it) can't leak post-save
    edits into the next reader. Matches invariant #17."""
    serializable = [{
        "name":   e.get("name", "?"),
        "taxid":  str(e.get("taxid", "")),
        "source": e.get("source", "user"),
        "added":  e.get("added", ""),
        "raw":    _codon_raw_to_json(e.get("raw", {})),
    } for e in entries]
    with _state._cache_lock:
        _safe_save_json(_state._CODON_TABLES_FILE, serializable, "Codon table library")
        _state._codon_tables_cache = _typed_clone(entries)


# ── HMM database catalog (pyhmmer profile DBs) ──────────────────────────────
# Builtin catalog + id/url sanitisers + entry normaliser + load/save. The
# sanitisers + normaliser are also used by the hub HMM-management handlers
# (via the re-export).


# Custom URL accepted only if it parses as http/https with a host;
# the protocol allowlist matches `[INV-36]`'s `$SPLICECRAFT_PYPI_URL`
# override pattern.
_HMM_DB_URL_MAX_LEN = 2048


_BUILTIN_HMM_DB_CATALOG: "tuple[dict, ...]" = (
    {
        "id":          "pfam-a",
        "name":        "Pfam-A",
        "url":         "https://ftp.ebi.ac.uk/pub/databases/Pfam/"
                       "current_release/Pfam-A.hmm.gz",
        "version_url": "https://ftp.ebi.ac.uk/pub/databases/Pfam/"
                       "current_release/Pfam.version.gz",
        "format":      "hmm-gz",
        "builtin":     True,
        "description": "Pfam-A: curated protein family HMMs from EBI "
                       "(~300 MB download, ~3 GB on disk after hmmpress).",
    },
    {
        "id":          "ncbifam",
        "name":        "NCBIfam",
        "url":         "https://ftp.ncbi.nlm.nih.gov/hmm/current/"
                       "NCBIfam.HMM.gz",
        # NCBIfam doesn't publish a one-line version file; we use
        # the source file's HTTP Last-Modified header as the version
        # signature via the version-check helper's fallback path.
        "version_url": "",
        "format":      "hmm-gz",
        "builtin":     True,
        "description": "NCBIfam: HMMs for prokaryotic protein families "
                       "(~600 MB download, ~4 GB on disk).",
    },
)


def _typed_clone_hmm_catalog(entries: "list | None") -> list:
    """Deepcopy-on-read for the catalog cache. Mirrors the pattern
    every other persisted-list helper uses (cf. `[PIT-17]`)."""
    if entries is None:
        return []
    return [dict(e) for e in entries if isinstance(e, dict)]


def _sanitize_hmm_db_id(raw: object) -> "str | None":
    """Return a sanitised id, or None on reject. Same rule set as
    `_sanitize_experiment_id` / `_sanitize_gel_id`: non-empty, no NUL,
    no path separators / shell metas, no `..`, ≤64 chars. The id
    doubles as the on-disk directory name."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or len(s) > 64:
        return None
    if "\x00" in s or ".." in s:
        return None
    if "/" in s or "\\" in s:
        return None
    for ch in s:
        # Allow ASCII letters, digits, underscore, hyphen ONLY.
        # Python's `str.isalnum()` is Unicode-permissive (accepts
        # `é`, `ñ`, CJK, etc.) — we explicitly check the codepoint
        # range so the id stays safe as a filesystem path component
        # on every OS. Sweep #28 hardening: a unicode-looking id
        # could pass cross-platform path checks on one OS but break
        # on another, or differ between the bytes-on-disk and what
        # the user typed (NFC vs NFD normalisation).
        cp = ord(ch)
        is_lower = (0x61 <= cp <= 0x7A)   # a-z
        is_upper = (0x41 <= cp <= 0x5A)   # A-Z
        is_digit = (0x30 <= cp <= 0x39)   # 0-9
        is_safe  = (ch == "_" or ch == "-")
        if not (is_lower or is_upper or is_digit or is_safe):
            return None
    return s


def _sanitize_hmm_db_url(raw: object) -> "str | None":
    """http(s) URL ≤ `_HMM_DB_URL_MAX_LEN`, otherwise None. Mirrors
    `[INV-36]`'s `$SPLICECRAFT_PYPI_URL` validator.

    Sweep #28: reject if the RAW input carries any whitespace /
    control char ANYWHERE — even at the trailing edge. A leading /
    trailing newline on a copy-pasted URL could trick a downstream
    parser into header-smuggling. The check fires before `.strip()`
    so we surface "you pasted a URL with hidden whitespace" rather
    than silently scrubbing it."""
    if not isinstance(raw, str):
        return None
    if any(ch.isspace() or ord(ch) < 0x20 for ch in raw):
        return None
    if not raw or len(raw) > _HMM_DB_URL_MAX_LEN:
        return None
    if not (raw.startswith("http://") or raw.startswith("https://")):
        return None
    return raw


def _normalise_hmm_db_entry(entry: dict, *, builtin_default: bool = False
                              ) -> "dict | None":
    """Return a fresh dict shaped to the catalog schema, or None if
    the entry is unsalvageable. Idempotent — safe to apply to entries
    loaded from disk and to entries built in-process."""
    if not isinstance(entry, dict):
        return None
    entry_id = _sanitize_hmm_db_id(entry.get("id"))
    if entry_id is None:
        return None
    url = _sanitize_hmm_db_url(entry.get("url"))
    if url is None:
        return None
    name_raw = entry.get("name")
    if not isinstance(name_raw, str) or not name_raw.strip():
        name = entry_id
    else:
        name = name_raw.strip()[:200]
    version_url_raw = entry.get("version_url")
    if isinstance(version_url_raw, str) and version_url_raw.strip():
        version_url = _sanitize_hmm_db_url(version_url_raw) or ""
    else:
        version_url = ""
    fmt = entry.get("format") or "hmm-gz"
    if fmt not in ("hmm-gz", "hmm"):
        fmt = "hmm-gz"
    desc_raw = entry.get("description")
    description = (desc_raw.strip()[:500]
                   if isinstance(desc_raw, str) else "")
    return {
        "id":          entry_id,
        "name":        name,
        "url":         url,
        "version_url": version_url,
        "format":      fmt,
        "builtin":     bool(entry.get("builtin", builtin_default)),
        "description": description,
    }


def _load_hmm_db_catalog() -> list:
    """Return the registered HMM-DB catalog (builtins + user-added).
    Deep-clone on read so callers can freely mutate without affecting
    the cache (cf. `[PIT-17]`).

    First-load merges the built-in defaults into whatever is on disk;
    subsequent loads return whatever was saved. Built-ins are still
    re-injected if the user has removed them (defensive — the user
    UI doesn't let you delete a builtin, but a hand-edited
    catalog.json shouldn't permanently nuke them either).
    """
    with _state._cache_lock:
        if _state._hmm_db_catalog_cache is None:
            entries, warning = _safe_load_json(
                _state._HMM_DB_CATALOG_FILE, "HMM database catalog",
            )
            if warning:
                _log.warning("HMM database catalog: %s", warning)
            normalised: list[dict] = []
            seen_ids: set[str] = set()
            for entry in entries:
                norm = _normalise_hmm_db_entry(entry)
                if norm is None:
                    continue
                if norm["id"] in seen_ids:
                    continue
                seen_ids.add(norm["id"])
                normalised.append(norm)
            # Re-inject any built-in the user removed (or that wasn't
            # there on first launch). User overrides for builtin URL
            # are preserved.
            for builtin in _BUILTIN_HMM_DB_CATALOG:
                if builtin["id"] not in seen_ids:
                    normalised.append(
                        _normalise_hmm_db_entry(
                            dict(builtin), builtin_default=True,
                        ) or {}
                    )
                    seen_ids.add(builtin["id"])
            _state._hmm_db_catalog_cache = normalised
        return _typed_clone_hmm_catalog(_state._hmm_db_catalog_cache)


def _save_hmm_db_catalog(entries: list) -> None:
    """Persist the catalog. Routed through `_safe_save_json` for the
    L2 chokepoint + atomic-write + .bak chain. Cache reseat under
    `_cache_lock` per `[INV-50]`."""
    cleaned: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        n = _normalise_hmm_db_entry(entry)
        if n is None or n["id"] in seen:
            continue
        seen.add(n["id"])
        cleaned.append(n)
    with _state._cache_lock:
        _safe_save_json(
            _state._HMM_DB_CATALOG_FILE, cleaned, "HMM database catalog",
        )
        _state._hmm_db_catalog_cache = _typed_clone_hmm_catalog(cleaned)


# ── Custom restriction enzymes ──────────────────────────────────────────────
# `_save_custom_enzymes` triggers a DOMAIN side-effect — rebuild the restriction
# `_SCAN_CATALOG` + bust the enzyme caches so a new / redefined custom enzyme
# shows up in scans (a silent-biology hazard if skipped). That side-effect lives
# hub-side (it reaches the scanner) and runs via the registered hook below.


def _load_custom_enzymes() -> list[dict]:
    """Deep-copy on read (pitfall #17).

    Sweep #26: double-checked locking — cache-hit fast path is
    lock-free so an unrelated lock-holder can't freeze UI reads."""
    cached = _state._custom_enzymes_cache
    if cached is not None:
        return _typed_clone(cached)
    with _state._cache_lock:
        if _state._custom_enzymes_cache is None:
            entries, warning = _safe_load_json(
                _state._CUSTOM_ENZYMES_FILE, "Custom enzymes",
            )
            if warning:
                _log.warning(warning)
            _state._custom_enzymes_cache = [
                e for e in entries if isinstance(e, dict)
            ]
        return _typed_clone(_state._custom_enzymes_cache)


def _save_custom_enzymes(entries: list[dict]) -> None:
    with _state._cache_lock:
        _safe_save_json(
            _state._CUSTOM_ENZYMES_FILE, entries, "Custom enzymes",
        )
        _state._custom_enzymes_cache = _typed_clone(entries)
    # Rebuild `_SCAN_CATALOG` + bust the enzyme caches so the new custom enzyme
    # shows up in the next restriction scan — hub-side effect, fired via the
    # registered hook. None only during the import window (no save runs then).
    hook = getattr(_state, "_after_custom_enzyme_save_hook", None)
    if hook is not None:
        hook()


# ── Entry vectors (Golden-Braid / MoClo acceptor plasmids) ──────────────────
# `_save_entry_vectors`' post-save side-effect (bust the EV digest + acceptor-TU
# caches) stays hub-side via `_state._after_entry_vectors_save_hook`. The
# name-trim backfill is a pure helper that travels with the cluster.


def _backfill_entry_vector_names(
        entries: list[dict]) -> "tuple[list[dict], int]":
    """Trim leading / trailing whitespace from each entry vector's
    `name` field. Returns `(entries, n_changed)`. Idempotent.

    **Hardening:** only mutates entries that already HAVE a `name`
    key — never adds the field where missing. Skips non-dict entries
    + non-string names (defensive against hand-edited JSON)."""
    n_changed = 0
    for e in entries:
        if not isinstance(e, dict) or "name" not in e:
            continue
        raw = e["name"]
        if not isinstance(raw, str):
            continue
        trimmed = raw.strip()
        if trimmed != raw:
            e["name"] = trimmed
            n_changed += 1
    return entries, n_changed


def _load_entry_vectors() -> list[dict]:
    with _state._cache_lock:
        if _state._entry_vectors_cache is None:
            entries, warning = _safe_load_json(
                _state._ENTRY_VECTORS_FILE, "Entry vectors",
            )
            if warning:
                _log.warning(warning)
            _state._entry_vectors_cache = [
                e for e in entries
                if isinstance(e, dict)
                and isinstance(e.get("grammar_id"), str)
            ]
        if not _state._entry_vectors_name_trim_done:
            _state._entry_vectors_cache, n_changed = (
                _backfill_entry_vector_names(_state._entry_vectors_cache)
            )
            _state._entry_vectors_name_trim_done = True
            if n_changed > 0:
                _log.info(
                    "entry-vectors: name trim backfill cleaned %d "
                    "entries", n_changed,
                )
                _log_event(
                    "entry_vectors.name_trim_backfill",
                    n_changed=n_changed,
                )
                save_target = _state._entry_vectors_cache
            else:
                save_target = None
        else:
            save_target = None
    if save_target is not None:
        try:
            _save_entry_vectors(save_target)
        except (OSError, RuntimeError):
            _log.exception(
                "entry-vectors: name trim backfill save failed",
            )
    assert _state._entry_vectors_cache is not None
    return _typed_clone(_state._entry_vectors_cache)


def _save_entry_vectors(entries: list[dict]) -> None:
    with _state._cache_lock:
        _safe_save_json(_state._ENTRY_VECTORS_FILE, entries, "Entry vectors")
        _state._entry_vectors_cache = _typed_clone(entries)
    # Bust the EV digest + acceptor-TU caches (hub-side: a reconfigured vector
    # set changes which overhangs/stuffers match) via the registered hook.
    hook = getattr(_state, "_after_entry_vectors_save_hook", None)
    if hook is not None:
        hook()


# ── Cloning grammars (Golden-Braid L0 / MoClo level rules) ──────────────────
# `_save_custom_grammars`' post-save side-effect (bust the assembly-fragment +
# EV-role-detect caches) stays hub-side via `_state._after_custom_grammars_save_hook`.


def _load_custom_grammars() -> list[dict]:
    """Sweep #26: double-checked locking — cache-hit fast path is lock-free."""
    cached = _state._grammars_cache
    if cached is not None:
        return _typed_clone(cached)
    with _state._cache_lock:
        if _state._grammars_cache is None:
            entries, warning = _safe_load_json(_state._GRAMMARS_FILE, "Cloning grammars")
            if warning:
                _log.warning(warning)
            entries = [e for e in entries if isinstance(e, dict) and isinstance(e.get("id"), str)]
            _state._grammars_cache = entries
        return _typed_clone(_state._grammars_cache)


def _save_custom_grammars(entries: list[dict]) -> None:
    with _state._cache_lock:
        _safe_save_json(_state._GRAMMARS_FILE, entries, "Cloning grammars")
        _state._grammars_cache = _typed_clone(entries)
    # Bust the assembly-fragment digest + EV-role-detect caches (hub-side: a
    # grammar enzyme / level_up_enzyme change shifts fragment overhangs + role
    # detection) via the registered hook.
    hook = getattr(_state, "_after_custom_grammars_save_hook", None)
    if hook is not None:
        hook()


# ── Primers + primer collections (re-entrant mirror, INV-50) ────────────────
# The primer library + its collections layer + the active-collection mirror +
# the dedup/name-trim helpers move as ONE group: `_save_primers` mirrors into
# the active collection INSIDE the `_state._cache_lock` block (so the mirror
# can't drift, sweep #10 / INV-50) and the mirror re-enters `_save_primer_
# collections` via the re-entrant RLock — which only works with the whole
# group sibling-internal. The active-collection NAME (a setting) is resolved
# hub-side via `_state._active_primer_collection_name_hook` to avoid pulling
# the settings layer in.


def _backfill_primer_names(
        entries: list[dict]) -> "tuple[list[dict], int]":
    """Trim leading / trailing whitespace from each primer entry's
    `name` field. Returns `(entries, n_changed)`. Idempotent.
    Sequence + tm fields are left untouched — only `name` is the
    user-facing label that participates in `==` matching.

    **Hardening:** only mutates entries that already HAVE a `name`
    key — never adds the field where missing. Skips non-dict entries
    + non-string names (defensive against hand-edited JSON)."""
    n_changed = 0
    for e in entries:
        if not isinstance(e, dict) or "name" not in e:
            continue
        raw = e["name"]
        if not isinstance(raw, str):
            continue
        trimmed = raw.strip()
        if trimmed != raw:
            e["name"] = trimmed
            n_changed += 1
    return entries, n_changed


def _load_primers() -> list[dict]:
    """Load + cache primers, with one-shot name-trim backfill on
    first read (PIT-36 whitespace-trim invariant applied to the
    primer subsystem too). Deep-copy on read per invariant #17."""
    with _state._cache_lock:
        if _state._primers_cache is None:
            entries, warning = _safe_load_json(
                _state._PRIMERS_FILE, "Primer library",
            )
            if warning:
                _log.warning(warning)
            _state._primers_cache = [e for e in entries if isinstance(e, dict)]
        if not _state._primers_name_trim_done:
            _state._primers_cache, n_changed = _backfill_primer_names(
                _state._primers_cache,
            )
            _state._primers_name_trim_done = True
            if n_changed > 0:
                _log.info(
                    "primers: name trim backfill cleaned %d entries",
                    n_changed,
                )
                _log_event(
                    "primers.name_trim_backfill", n_changed=n_changed,
                )
                save_target = _state._primers_cache
            else:
                save_target = None
        else:
            save_target = None
    if save_target is not None:
        try:
            _save_primers(save_target)
        except (OSError, RuntimeError):
            _log.exception(
                "primers: name trim backfill save failed",
            )
    assert _state._primers_cache is not None
    return _typed_clone(_state._primers_cache)


def _dedupe_primers_by_sequence(entries: list[dict]) -> list[dict]:
    """Collapse primer entries whose ``sequence`` field duplicates an
    earlier entry's sequence (case-insensitive). The first entry of
    each unique sequence wins — callers that prepend MRU at index 0
    therefore keep their newest copy, while older duplicates are
    silently dropped.

    The primer-library policy across the app has always been "one entry
    per unique sequence":

      * ``PrimerDesignScreen._save_primers_btn`` rejects a designed
        primer when its sequence already exists.
      * ``_apply_record`` and ``_bulk_import_folder`` dedupe
        .dna-imported primers against the existing library before
        appending.

    Pre-2026-05-10 those dedupe paths only filtered NEW additions —
    duplicates that snuck into ``primers.json`` from earlier sessions
    (manual edits, imports before the dedupe landed, etc.) stayed
    forever. Calling this helper on every save means the next write
    of ``primers.json`` cleans up legacy duplicates without the user
    having to do anything.

    Entries lacking a usable ``sequence`` (None / empty / non-string)
    are kept verbatim — losing them silently would be worse than
    leaving the user with a one-off "unknown" row to investigate.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            out.append(e)
            continue
        raw_seq = e.get("sequence")
        if not isinstance(raw_seq, str):
            out.append(e)
            continue
        key = raw_seq.strip().upper()
        if not key:
            out.append(e)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _save_primers(entries: list[dict]) -> None:
    # Dedupe by sequence on every save (sacred policy: one entry per
    # unique sequence). See `_dedupe_primers_by_sequence`. Note that
    # NAME collisions are NOT auto-collapsed here — `.dna` imports
    # legitimately stash same-name different-sequence variants, and
    # silently dropping any would lose primer data. Name-collision
    # cleanup is opt-in via `PrimerDuplicatesModal` at startup.
    # The primer-usage cache stays valid here because adding/removing
    # primer-library entries doesn't change which plasmids carry
    # `primer_bind` features — that's `_save_library`/`_save_collections`'
    # job to invalidate.
    deduped = _dedupe_primers_by_sequence(entries)
    with _state._cache_lock:
        _safe_save_json(_state._PRIMERS_FILE, deduped, "Primer library")
        # Deep-copy on save so the cache is independent of the caller's
        # reference — pre-fix `list(entries)` shared dict refs with the
        # caller, so subsequent caller mutations (e.g. an aborted modal)
        # leaked back into the next `_load_primers`.
        _state._primers_cache = _typed_clone(deduped)
        # Mirror into the active primer collection (collections layer
        # for primers, same pattern as plasmid library →
        # collections.json mirror, pitfall #10). Inside the lock so
        # the mirror file can't drift from the live file (sweep #10,
        # [INV-50]). _save_primer_collections re-acquires the RLock.
        _sync_active_primer_collection_primers(deduped)


def _load_primer_collections() -> list[dict]:
    """Deep-copy on read so callers can mutate freely; pitfall #17.
    One-shot name-trim backfill on first load: walks every collection
    and applies `_backfill_primer_names` to its embedded `primers`
    list (parallel to `_ensure_collections_cache_populated_and_migrated`
    for the library, and to the same backfill applied to the live
    `primers.json` in `_load_primers`).
    """
    with _state._cache_lock:
        if _state._primer_collections_cache is None:
            entries, warning = _safe_load_json(
                _state._PRIMER_COLLECTIONS_FILE, "Primer collections",
            )
            if warning:
                _log.warning(warning)
            _state._primer_collections_cache = [
                e for e in entries if isinstance(e, dict)
            ]
        if not _state._primer_collections_backfill_done:
            n_changed_total = 0
            for pc in _state._primer_collections_cache:
                if not isinstance(pc, dict):
                    continue
                primers = pc.get("primers")
                if not isinstance(primers, list):
                    continue
                _, n = _backfill_primer_names(primers)
                n_changed_total += n
            _state._primer_collections_backfill_done = True
            if n_changed_total > 0:
                _log.info(
                    "primer-collections: name trim backfill cleaned "
                    "%d embedded primer(s)", n_changed_total,
                )
                _log_event(
                    "primer_collections.name_trim_backfill",
                    n_changed=n_changed_total,
                )
                save_target = _state._primer_collections_cache
            else:
                save_target = None
        else:
            save_target = None
    if save_target is not None:
        try:
            _save_primer_collections(save_target)
        except (OSError, RuntimeError):
            _log.exception(
                "primer-collections: name trim backfill save failed",
            )
    assert _state._primer_collections_cache is not None
    return _typed_clone(_state._primer_collections_cache)


def _save_primer_collections(entries: list[dict]) -> None:
    with _state._cache_lock:
        _safe_save_json(
            _state._PRIMER_COLLECTIONS_FILE, entries, "Primer collections",
        )
        _state._primer_collections_cache = _typed_clone(entries)


def _sync_active_primer_collection_primers(entries: list[dict]) -> None:
    """Mirror the live primer library into the active collection so the
    on-disk record never drifts from `primers.json`. Silent no-op if no
    collection is active or the active name has been deleted. Caller
    MUST already hold `_cache_lock` (this re-acquires via RLock for
    `_save_primer_collections`). See `[INV-50]` for the save-chain
    lock-release gap that this design avoids.
    """
    _get_name = getattr(_state, "_active_primer_collection_name_hook", None)
    name = _get_name() if _get_name is not None else None
    if not name:
        return
    snapshot = [_typed_clone(e) for e in entries if isinstance(e, dict)]
    colls = _load_primer_collections()
    for c in colls:
        if c.get("name") == name:
            c["primers"] = snapshot
            _save_primer_collections(colls)
            return
