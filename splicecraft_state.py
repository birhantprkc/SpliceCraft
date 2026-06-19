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
