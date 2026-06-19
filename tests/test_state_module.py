"""Guard the shared-state pattern (the Option-3 state decoupling).

Mutable process state that the monolith kept as bare module globals and mutated
at runtime (render-tier flags now; data paths / caches later) is migrating into
``splicecraft_state``, accessed by attribute (``state.X``) so the hub, every
sibling, and the tests all read and write ONE copy.

The failure this guards against is the *stale-shadow* trap: a migrated name also
exists as a hub-level global (a leftover ``_X = ...`` in splicecraft.py, or a
``from splicecraft_state import _X``), so a monkeypatch or a runtime write to one
copy silently misses readers using the other. That trap is exactly what makes
naive module extraction unsafe in this codebase, so it gets a dedicated test
that every future migration extends via ``_MIGRATED``.
"""
from __future__ import annotations

import splicecraft as sc
import splicecraft_state

# Names migrated out of the hub into splicecraft_state. APPEND to this as the
# decoupling proceeds; each entry is then held to the single-source-of-truth
# invariants below.
_MIGRATED = [
    "_ASCII_MODE",
    "_ASCII_FORCED",
    "_WIN_UTF8_CONSOLE",
    "_ESCAPE_ASPECT",
    # Phase A1: caches, generation counters, background-write coordination
    "_BLAST_CACHE_GENERATION",
    "_DANGLING_ACTIVE_COLLECTION_NAME",
    "_SPELLCHECK_ENGINE",
    "_WHATS_NEW_CACHE",
    "_collection_sync_pending",
    "_collection_sync_thread",
    "_feature_library_index_cache",
    "_features_generation",
    "_primer_usage_cache",
    "_primer_usage_cache_gen",
    "_settings_flush_pending",
    "_settings_flush_running",
    # Phase A2a: persisted-data caches
    "_codon_tables_cache", "_collections_cache", "_custom_enzymes_cache",
    "_entry_vectors_cache", "_enzyme_collections_cache", "_experiment_projects_cache",
    "_experiments_cache", "_feature_colors_cache", "_features_cache", "_gels_cache",
    "_grammars_cache", "_hmm_db_catalog_cache", "_library_cache", "_parts_bin_cache",
    "_parts_bin_collections_cache", "_primer_collections_cache", "_primers_cache",
    "_protein_collections_cache", "_protein_motifs_cache", "_settings_cache",
    # Phase A2b: one-shot backfill / name-trim flags
    "_collections_backfill_done", "_collections_origin_history_backfill_done",
    "_entry_vectors_name_trim_done", "_id_name_backfill_done",
    "_origin_history_backfill_done", "_parts_bin_collections_backfill_done",
    "_parts_bin_sequence_backfill_done", "_primer_collections_backfill_done",
    "_primers_name_trim_done",
]


def test_hub_exposes_state_module():
    """`sc._state` is the canonical handle the hub + tests patch through."""
    assert sc._state is splicecraft_state


def test_migrated_names_live_in_state():
    missing = [n for n in _MIGRATED if not hasattr(splicecraft_state, n)]
    assert not missing, f"migrated name(s) absent from splicecraft_state: {missing}"


def test_no_stale_hub_shadow():
    """A migrated name must NOT also exist in the hub's own namespace, where it
    would desync from `state.X` writes / monkeypatches."""
    shadows = [n for n in _MIGRATED if n in vars(sc)]
    assert not shadows, (
        f"`splicecraft` keeps stale shadow(s) of migrated state: {shadows}. "
        "Remove the hub-level binding and read via `_state.<name>` -- a "
        "leftover global or a by-value `from splicecraft_state import` desyncs "
        "from the live value (the trap this whole refactor exists to avoid)."
    )


def test_runtime_writer_mutates_the_shared_copy(monkeypatch):
    """`_set_ascii_mode` (a runtime writer) must flip the SAME storage readers
    see -- i.e. the state module's attribute, not a hub-local copy."""
    monkeypatch.setattr(splicecraft_state, "_ASCII_MODE", False)
    sc._set_ascii_mode(True)
    assert splicecraft_state._ASCII_MODE is True, (
        "_set_ascii_mode did not write the shared splicecraft_state copy"
    )
    sc._set_ascii_mode(False)
    assert splicecraft_state._ASCII_MODE is False
