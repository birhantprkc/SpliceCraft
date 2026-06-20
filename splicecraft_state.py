"""Shared mutable process state for SpliceCraft (layer 0).

Module-level flags that the monolith kept as bare globals and mutated at runtime
(`global _ASCII_MODE; _ASCII_MODE = ...`) move here, one source of truth, so the
hub, every sibling module, and the test suite all read and write the *same*
storage.

ACCESS RULE (this is a footgun if ignored):

    import splicecraft_state as state
    if state._ASCII_MODE: ...          # GOOD - reads the live value
    state._ASCII_MODE = True           # GOOD - writes the live value

    from splicecraft_state import _ASCII_MODE   # BAD - binds a STALE COPY;
                                                # later writes here are invisible
                                                # to it, and monkeypatching the
                                                # module attr won't reach it.

`tests/test_state_module.py` enforces that no module keeps such a stale shadow.
Tests monkeypatch these via the module object (`monkeypatch.setattr(sc._state,
"_ASCII_MODE", ...)`), which every reader then sees.

This module imports nothing from the rest of the package and is safe to import
from any layer.
"""
from __future__ import annotations

import threading
from collections import OrderedDict as _OrderedDict
from pathlib import Path
from typing import Any as _Any, Callable as _Callable

# ── Render tier ──────────────────────────────────────────────────────────────
# Set by `_select_render_tier()` / `_set_ascii_mode()` in splicecraft.py; read
# by the map + helix renderers (fresh every frame) and folded into the
# PlasmidMap draw-cache key so the live Settings toggle busts the cache.
#
# `_ASCII_MODE`: emit the 7-bit-ASCII density-ramp fallback instead of Unicode
# braille/glyphs (a terminal that can't emit UTF-8). Forced on by
# SPLICECRAFT_ASCII=1.
_ASCII_MODE: bool = False

# `_ASCII_FORCED`: True when ASCII was FORCED by capability (the
# SPLICECRAFT_ASCII env var, or a terminal that genuinely can't emit UTF-8)
# rather than CHOSEN via the toggle. Gates whether a saved `ascii_map`
# preference may restore braille at launch: it may not when braille literally
# can't render (mojibake), but it may when the only problem was a font missing
# the glyphs (UTF-8 fine, dots show as boxes).
_ASCII_FORCED: bool = False

# ── Terminal capability ──────────────────────────────────────────────────────
# `_WIN_UTF8_CONSOLE`: set True when `_windows_enable_utf8_console()` switched the
# Windows console output code page to UTF-8 (chcp 65001). Surfaced in the
# `startup.terminal_capabilities` event for diagnostics.
_WIN_UTF8_CONSOLE: bool = False

# `_ESCAPE_ASPECT`: the terminal's measured character-cell pixel ratio, set once
# at startup by main() (before Textual owns stdin) from a CSI 16t/14t self-report.
# Preferred over TIOCGWINSZ because many terminals answer the query yet leave the
# ioctl pixel fields at zero. None until measured (caller keeps the 2:1 default).
_ESCAPE_ASPECT: "float | None" = None


# ── Caches, generation counters, background-write coordination (Phase A1) ──
# Migrated out of the hub; accessed `_state.<name>`. Not conftest-patched.
_BLAST_CACHE_GENERATION: int = 0
_DANGLING_ACTIVE_COLLECTION_NAME: "str | None" = None
_SPELLCHECK_ENGINE: "_Any | None" = None
_WHATS_NEW_CACHE: "tuple[str, float, str] | None" = None  # (path, mtime, body_md)
_collection_sync_pending: "tuple[str, list[dict]] | None" = None
_collection_sync_thread: "threading.Thread | None" = None
_feature_library_index_cache: "tuple[int, dict[tuple[str, str], str]] | None" = None
_features_generation: int = 0
_primer_usage_cache: "dict[str, int] | None" = None
_primer_usage_cache_gen: int = 0
_settings_flush_pending: "dict | None" = None
_settings_flush_running: bool = False


# ── Persisted-data caches (Phase A2a) ──────────────────────────────
# Migrated from the hub; conftest._patch + the Master-Delete reset target
# them here automatically. Accessed `_state.<name>`.
_codon_tables_cache: "list | None" = None
_collections_cache: "list | None" = None
_custom_enzymes_cache: "list | None" = None
_entry_vectors_cache: "list | None" = None
_enzyme_collections_cache: "list | None" = None
_experiment_projects_cache: "list | None" = None
_experiments_cache: "list | None" = None
_feature_colors_cache: "dict[str, str] | None" = None
_features_cache: "list | None" = None
_gels_cache: "list | None" = None
_grammars_cache: "list | None" = None
_hmm_db_catalog_cache: "list | None" = None
_library_cache: "list | None" = None
_parts_bin_cache: "list | None" = None
_parts_bin_collections_cache: "list | None" = None
_primer_collections_cache: "list | None" = None
_primers_cache: "list | None" = None
_protein_collections_cache: "list | None" = None
_protein_motifs_cache: "list | None" = None
_settings_cache: "dict | None" = None


# ── One-shot backfill / name-trim flags (Phase A2b) ──
_collections_backfill_done: bool = False
_collections_origin_history_backfill_done: bool = False
_entry_vectors_name_trim_done: bool = False
_id_name_backfill_done: bool = False
_origin_history_backfill_done: bool = False
_parts_bin_collections_backfill_done: bool = False
_parts_bin_sequence_backfill_done: bool = False
_primer_collections_backfill_done: bool = False
_primers_name_trim_done: bool = False


# ── The save-authorization chokepoint flag (Phase A2c) ──
# Flipped by `_authorize_writes` / `_authorize_writes_for_sandbox`; checked
# by `_refuse_unauthorized_write` / `_refuse_unauthorized_delete` (gating
# every _save_*). The single guard protecting real user data from an
# unsandboxed import. Writer + checks both go through _state → always in sync.
_SAVES_AUTHORIZED: bool = False
_SAVES_AUTHORIZED_REASON: str = ""


# ── Data directory (Phase B-prep) ──────────────────────────────────
# The resolved user-data dir. COMPUTED by the hub at import time
# (`_state._DATA_DIR = _user_data_dir()`); placeholder here so readers + the
# conftest sandbox patch resolve it. Everything path-related hangs off this.
_DATA_DIR: Path = None  # type: ignore[assignment]
# `_DNA_ORIGINALS_DIR` — the .dna CommercialSaaS-roundtrip sidecar dir (a
# `_DATA_DIR`-derived sub-dir). Migrated here (Phase D, the fileio-extraction
# prerequisite) so the fileio sibling can reach it without an upward hub import;
# the hub sets the real `_DATA_DIR/dna_originals` at import. Enumerated BY NAME in
# the hub's `_USER_DATA_DIR_ATTRS` + resolved via `_resolve_state_or_hub` (checks
# _state first), so master-delete / snapshot / housekeeping still cover it.
_DNA_ORIGINALS_DIR: Path = None  # type: ignore[assignment]

# ── Per-file data paths (Phase B-prep) — the hub sets the real `_DATA_DIR/...`
# values at import; declared here (typed Path) so every `_state._X_FILE` reader
# type-checks and pyright knows the attribute. Placeholder None until the hub runs.
_AGENT_TOKEN_FILE: Path = None  # type: ignore[assignment]
_CODON_TABLES_FILE: Path = None  # type: ignore[assignment]
_COLLECTIONS_FILE: Path = None  # type: ignore[assignment]
_CUSTOM_ENZYMES_FILE: Path = None  # type: ignore[assignment]
_DATA_VERSION_FILE: Path = None  # type: ignore[assignment]
_ENTRY_VECTORS_FILE: Path = None  # type: ignore[assignment]
_ENZYME_COLLECTIONS_FILE: Path = None  # type: ignore[assignment]
_EXPERIMENT_PROJECTS_FILE: Path = None  # type: ignore[assignment]
_EXPERIMENTS_FILE: Path = None  # type: ignore[assignment]
_FEATURE_COLORS_FILE: Path = None  # type: ignore[assignment]
_FEATURES_FILE: Path = None  # type: ignore[assignment]
_GELS_FILE: Path = None  # type: ignore[assignment]
_GRAMMARS_FILE: Path = None  # type: ignore[assignment]
_HMM_DB_CATALOG_FILE: Path = None  # type: ignore[assignment]
_LIBRARY_FILE: Path = None  # type: ignore[assignment]
_PARTS_BIN_COLLECTIONS_FILE: Path = None  # type: ignore[assignment]
_PARTS_BIN_FILE: Path = None  # type: ignore[assignment]
_PRIMER_COLLECTIONS_FILE: Path = None  # type: ignore[assignment]
_PRIMERS_FILE: Path = None  # type: ignore[assignment]
_PROTEIN_COLLECTIONS_FILE: Path = None  # type: ignore[assignment]
_PROTEIN_MOTIFS_FILE: Path = None  # type: ignore[assignment]
_SETTINGS_FILE: Path = None  # type: ignore[assignment]
_sc_version: str = ""  # set by the hub from __version__ (splicecraft_record stamp)


# ── Persistence-engine config (Phase B-main) ───────────────────────
# Tunables for the domain-agnostic save/load engine (`_safe_save_json`,
# `_prune_backups`, `_safe_load_json`, …, extracted to
# splicecraft_persistence). They live here — not in the engine sibling —
# because the test suite monkeypatches them (`monkeypatch.setattr(_state,
# "_BACKUP_RETENTION_COUNT", 3)`) to exercise pruning/oversize paths without
# fabricating gigabyte files, and a single shared copy keeps the engine's
# reads and the test's writes in sync (a by-value re-export would desync).

# Schema-envelope version stamped into every data file ({"_schema_version":
# N, "entries": [...]}). Legacy bare-list files (pre-0.3.1) load + get rewrapped
# as an envelope on next save.
_CURRENT_SCHEMA_VERSION: int = 1

# Per-path observed schema versions. When `_safe_load_json` reads a file
# written by a NEWER SpliceCraft (schema_version > current), it stashes the
# observed version here keyed by absolute path string so the next
# `_safe_save_json` for that path preserves it rather than demoting the stamp
# (which would make a future migrator double-migrate already-current fields).
# MUTABLE + shared between load + save → must be one copy here.
_OBSERVED_SCHEMA_VERSIONS: "dict[str, int]" = {}

# Multi-generation backup retention. Each `_safe_save_json` keeps the prior
# file as `<file>.bak` (latest) + `<file>.bak.YYYYMMDD-HHMMSS` (rotating); the
# newest `_BACKUP_RETENTION_COUNT` timestamped generations survive.
_BACKUP_RETENTION_COUNT: int = 10
# Aggregate byte ceiling across ALL timestamped backups of ONE base file.
# After count-pruning, `_prune_backups` drops oldest backups until the total
# falls under this cap — but never below `_BACKUP_MIN_KEEP` newest generations
# (a large single file must keep some rollback points).
_BACKUP_TOTAL_SIZE_CAP_BYTES: int = 1024 * 1024 * 1024   # 1 GB per base file
_BACKUP_MIN_KEEP: int = 2   # never byte-prune below this many newest backups

# Lost-entries spillover (the shrink-guard + raw-bytes recovery copies) lives
# in a sibling `lost_entries/` dir, two-stage-pruned like the backups.
_LOST_ENTRIES_DIR_NAME: str = "lost_entries"
_LOST_ENTRIES_RETENTION_COUNT: int = 5
_LOST_ENTRIES_TOTAL_SIZE_CAP_BYTES: int = 500 * 1024 * 1024   # 500 MB

# Read-back validation threshold. After writing a save's temp file,
# `_safe_save_json` re-reads it before the atomic swap; files at/under this
# size get a full `json.loads` + entry-count check, larger files a cheap tail
# check so the hot path doesn't eat a multi-second re-parse + RAM spike.
_SAVE_READBACK_FULL_PARSE_MAX_BYTES: int = 32 * 1024 * 1024   # 32 MB

# Data-dir JSON load cap (the user's OWN library/collections, distinct from the
# foreign-file ingest cap). 1 GB so accidental cap-trips are rare; the
# `_safe_save_json` OVERSIZE GUARD handles the remaining edge case so a trip
# can't nuke data.
_SAFE_LOAD_JSON_MAX_BYTES: int = 1024 * 1024 * 1024   # 1 GB

# Daily pre-update snapshot subdir (under the data dir). The engine's
# `_safe_load_json` reads this name to skip the snapshots tree when scanning
# for backups; hub snapshot code owns the retention/size tunables.
_SNAPSHOT_DIR_NAME: str = "snapshots"

# Blob-store dehydration hooks (Phase B-main). The persistence engine's single
# write chokepoint (`_safe_save_json`) replaces inline `gb_text` with a
# content-addressed `gb_ref` for the library + collections files. The transform
# lives in the hub-side blob subsystem; the hub registers it here at import
# (`_state._dehydrate_entries_hook = _dehydrate_entries`) so the engine can call
# it WITHOUT importing the hub — keeping the engine sibling's deps to stdlib +
# _state + logger. None only during the import window (no save runs then); a
# registration regression is caught loudly by test_blob_store, not silently.
_dehydrate_entries_hook: "_Callable[[list], list] | None" = None
_dehydrate_collections_hook: "_Callable[[list], list] | None" = None


# ── The data-layer save lock (Phase D) ─────────────────────────────────────
# Every `_save_*` JSON helper grabs this around its disk-write + cache-
# reassignment pair. The disk write alone is atomic (POSIX rename) and the
# Python assignment alone is atomic (GIL), but the PAIR is not — without the
# lock two concurrent saves could land their `os.replace`s in order A→B while
# their cache reassignments land B→A, leaving a `_*_cache` pointing at older
# state than disk. RLock (not Lock) because save chains nest re-entrantly
# (`_save_library` → `_sync_active_collection_plasmids` → `_save_collections`).
# Lives here (the canonical home) so the dataaccess sibling's accessors reach it
# as `_state._cache_lock`; the hub keeps a same-object alias `_cache_lock` so its
# 129 `with _cache_lock:` sites + the inspect.getsource assertions stay valid.
_cache_lock = threading.RLock()


# ── Post-save side-effect hooks (Phase D) ──────────────────────────────────
# Some `_save_X` accessors trigger DOMAIN side-effects beyond the disk write +
# cache reseat — e.g. saving custom enzymes rebuilds the restriction
# `_SCAN_CATALOG` + busts the enzyme caches so the new enzyme shows up in
# scans. Those effects stay hub-side (they reach the scanner + hub caches); the
# hub registers them here at import, and the now-in-sibling accessor fires the
# hook after its write. None until registered; an accessor that finds None (the
# import window) just skips the side-effect — no save runs before registration,
# and test_enzyme_collections guards the registration so a regression is loud.
_after_custom_enzyme_save_hook: "_Callable[[], None] | None" = None
# Saving entry vectors busts the EV digest + acceptor-TU caches (a reconfigured
# vector set changes which overhangs/stuffers match) — hub-side, via this hook.
_after_entry_vectors_save_hook: "_Callable[[], None] | None" = None
# Saving cloning grammars busts the assembly-fragment + EV-role-detect caches (a
# grammar enzyme change shifts fragment overhangs / role detection) — hub-side.
_after_custom_grammars_save_hook: "_Callable[[], None] | None" = None

# ── Restriction-scanner runtime state (Phase D — the scanner + digest engine
# move to splicecraft_biology; their caches + catalog access live here so the L0
# sibling reaches them without an upward import to the hub). The two LRU caches
# are mutated in-place (.get / .move_to_end / .popitem / [k]= / .clear) and NEVER
# reassigned, so the hub keeps live aliases AND the custom-enzyme bust
# (`_resolve_state_or_hub` → `.clear()`) finds them here. `_SCAN_CATALOG` itself
# IS reassigned by the hub's `_rebuild_scan_catalog` (test_sweep25 H4 pins the
# atomic `globals()["_SCAN_CATALOG"]` form), so it stays the hub global and the
# sibling reads it fresh through `_scan_catalog_hook`.
_RESTR_SCAN_CACHE: "_OrderedDict[tuple, list]" = _OrderedDict()
_RESTR_SCAN_CACHE_MAX: int = 4
_ENZYME_CUTS_CACHE: "_OrderedDict[tuple, list[dict]]" = _OrderedDict()
_ENZYME_CUTS_CACHE_MAX: int = 16
# Getters the hub registers at import so the sibling scanner reaches hub-side
# data: the fresh `_SCAN_CATALOG`, and the combined `_all_enzymes()` view
# (built-in + custom — reads dataaccess, so the function itself stays hub-side).
# Typed non-optional (the scanner calls them on every scan, unguarded) with a
# fail-loud default rather than None: registration happens during hub import,
# before any scan/digest runs, so this default is never hit at runtime — but if
# it ever were, raising beats silently scanning an empty catalog (silent-biology
# hazard).
def _scanner_hook_unregistered():
    raise RuntimeError(
        "restriction-scanner _state hook called before the hub registered it")


_scan_catalog_hook: "_Callable[[], list]" = _scanner_hook_unregistered
_all_enzymes_hook: "_Callable[[], dict]" = _scanner_hook_unregistered
# Resolver returning the active primer-collection NAME (stored in settings, read
# hub-side via `_get_setting`). The sibling's `_sync_active_primer_collection_primers`
# mirror needs it but must not pull the settings layer in, so the hub registers
# its `_get_active_primer_collection_name` here. None → mirror is a no-op.
_active_primer_collection_name_hook: "_Callable[[], object] | None" = None

# ── Library + collections hooks (Phase D — the 160 MB collections.json path) ──
# The thin `_load_library`/`_save_library`/`_load_collections`/`_save_collections`
# accessors move to splicecraft_dataaccess, but the DANGEROUS logic stays
# hub-side and is reached through these hooks: the `_ensure_*` migration (its
# backfills pull GenBank parse/serialise — not movable), the async active-
# collection mirror (`_sync_active_collection_plasmids` + its `_collection_sync_*`
# worker subsystem), and the post-save cache-busts (primer-usage / k-mer). The
# hub registers each at import. The sibling `_load_*` fire the ensure hook then
# return `_typed_clone(_state._X_cache)`; the sibling `_save_*` write + reseat +
# fire the mirror (in-lock) + after-save (post-lock) hooks.
_ensure_library_hook: "_Callable[[], None] | None" = None
_ensure_collections_hook: "_Callable[[], None] | None" = None
_sync_active_collection_plasmids_hook: "_Callable[..., None] | None" = None
_after_library_save_hook: "_Callable[[], None] | None" = None
_after_collections_save_hook: "_Callable[[], None] | None" = None
# parts_bin / parts_bin_collections — identical shape to library/collections
# (`_ensure_*` migration + sequence backfill, the active-parts-bin mirror
# `_sync_active_parts_bin_parts` (synchronous — bins are small), and the
# assembly-fragment-cache bust) all STAY hub-side; reached via these hooks.
_ensure_parts_bin_hook: "_Callable[[], None] | None" = None
_ensure_parts_bin_collections_hook: "_Callable[[], None] | None" = None
_sync_active_parts_bin_parts_hook: "_Callable[..., None] | None" = None
_after_parts_bin_save_hook: "_Callable[[], None] | None" = None
_after_parts_bin_collections_save_hook: "_Callable[[], None] | None" = None
# experiments — `_load_experiments` applies the legacy tag-format migration per
# body via this hook (the migrator stays hub-side; it's shared with the editor
# body-readers). `_save_experiments` mirrors into the active experiment project
# via the synchronous `_sync_active_project_experiments` (hub-side, inside the
# lock — sweep #9: RLock re-entry, no experiments.json/projects.json drift).
_migrate_experiment_body_hook: "_Callable[[str], str] | None" = None
_sync_active_project_experiments_hook: "_Callable[..., None] | None" = None
# settings — `_load_settings` type-validates via `_validate_settings` (+ its
# schema / safe-identifier web) and `_set_setting` schedules the coalesced
# background disk flush (`_settings_flush_worker` daemon thread). Both stay
# hub-side (hot path + a write subsystem + UI-failure notify), reached via these.
_validate_settings_hook: "_Callable[[dict], tuple[dict, list[str]]] | None" = None
_settings_schedule_flush_hook: "_Callable[[dict], None] | None" = None
# loader — the fileio `.dna` loader stamps the source file's date onto a dateless
# construction-history root via `_stamp_history_root_date` (hub-side: it serialises
# through `_serialize_commercialsaas_history`, which lives in cloning L3 — above
# fileio L2, so it can't move down). Best-effort + cosmetic: the safe default just
# returns the history XML unchanged, so an unregistered hook degrades gracefully
# (no crash, no data loss) rather than failing loud. The hub registers the real fn
# at import, before any load.
def _stamp_history_root_date_passthrough(hist_xml, date_str):
    return hist_xml
_stamp_history_root_date_hook: "_Callable[..., object]" = _stamp_history_root_date_passthrough
