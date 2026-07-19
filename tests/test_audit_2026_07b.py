"""Regression tests for the 2026-07-07 adversarial audit sweep.

Locks in the confirmed fixes from the fresh whole-codebase audit (distinct from
the 2026-07-01 sweep in `test_audit_2026_07.py`):

  * DATA-1  restore-from-backup of a MIRROR file (plasmid_library / primers /
            parts_bin) is written THROUGH into the owning active collection, so
            the recovery no longer silently reverts on the next launch.
  * BIO-1   an empty / whitespace-only enzyme recognition site is refused
            instead of compiling to `re.compile("")` (a cut at every base).
  * BIO-2   two coincident top-strand cuts collapse to one boundary instead of
            emitting a phantom full-length fragment (a wrong digest / clone).
  * NET-1   `fetch` joins the heavy-endpoint concurrency gate.
  * XML-1   a DOCTYPE whose SYSTEM literal contains `>` can't smuggle an
            internal-subset entity bomb past `_safe_xml_parse`.

(The codon alternate-genetic-code fix is covered in `test_codon.py`.)

Pure / handler-level, fast. The autouse `_protect_user_data` fixture (conftest)
sandboxes every data-file write; nothing here touches the real data dir.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

import splicecraft as sc
import splicecraft_biology as _bio
from splicecraft_util import _safe_xml_parse


# ── DATA-1: restore-through-to-collection ───────────────────────────────────
def test_restore_mirror_reconciles_into_active_collection():
    """A restored `plasmid_library.json` must be persisted into the active
    collection (its source of truth), else `_restore_library_from_active_
    collection` reverts it on the next launch — the recovery silently undoes."""
    sc._save_collections([{"name": "C1", "plasmids": [{"name": "P_old"}]}])
    sc._set_active_collection_name("C1")
    # Simulate a raw restore: the library MIRROR file is overwritten with the
    # backup's entries and its cache busted, but the owning collection is NOT
    # touched (exactly what `_restore_from_backup` does today).
    sc._safe_save_json(sc._state._LIBRARY_FILE, [{"name": "P_restored"}],
                       "Plasmid library")
    sc._state._library_cache = None
    # Pre-reconcile the collection still holds the OLD entry → would revert.
    assert [p["name"] for p in sc._find_collection("C1")["plasmids"]] == ["P_old"]
    # Reconcile writes the restored library THROUGH into the collection.
    sc._reconcile_mirror_after_restore(sc._state._LIBRARY_FILE)
    assert [p["name"] for p in sc._find_collection("C1")["plasmids"]] == ["P_restored"]


def test_reconcile_is_noop_for_non_mirror_target():
    """A non-mirror target (collections.json is itself the source of truth)
    must not raise and must not disturb the active collection."""
    sc._save_collections([{"name": "C1", "plasmids": [{"name": "P"}]}])
    sc._set_active_collection_name("C1")
    sc._reconcile_mirror_after_restore(sc._state._COLLECTIONS_FILE)
    assert [p["name"] for p in sc._find_collection("C1")["plasmids"]] == ["P"]


def test_restore_experiments_reconciles_into_active_project():
    """2026-07-18 extension of DATA-1: `experiments.json` is a MIRROR of the
    active project's `experiments` list in `experiment_projects.json`. A raw
    restore that overwrites only the mirror must be written THROUGH into the
    active project, else the next project-open reverts it (the recovery
    silently undoes). The reconcile previously covered library / primers /
    parts_bin but not experiments."""
    sc._save_experiment_projects([{
        "name": "Proj1",
        "experiments": [{"id": "e_old", "title": "old"}],
    }])
    sc._set_active_project_name("Proj1")
    # Raw restore of the experiments MIRROR file (cache busted); the owning
    # project is NOT touched — exactly what `_restore_from_backup` does.
    sc._safe_save_json(sc._state._EXPERIMENTS_FILE,
                       [{"id": "e_restored", "title": "restored"}],
                       "Experiments")
    sc._state._experiments_cache = None
    # Pre-reconcile the project still holds the OLD entry → would revert.
    assert [e["id"] for e in sc._find_project("Proj1")["experiments"]] == ["e_old"]
    # Reconcile writes the restored experiments THROUGH into the project.
    sc._reconcile_mirror_after_restore(sc._state._EXPERIMENTS_FILE)
    assert [e["id"] for e in sc._find_project("Proj1")["experiments"]] == ["e_restored"]


# ── BIO-1: empty / whitespace enzyme recognition site ───────────────────────
def test_empty_recognition_site_is_refused():
    for bad in ("", "   ", "\t"):
        with pytest.raises(ValueError):
            _bio._iupac_pattern(bad)


def test_enzyme_cuts_skips_empty_site_no_phantom_cuts():
    """A corrupt custom enzyme with an empty site is SKIPPED, not applied at
    every base (`_enzyme_cuts_impl` wraps `_iupac_pattern` in except-ValueError)."""
    seq = "ATGCATGCATGCATGC"
    real_hook = sc._state._all_enzymes_hook
    sc._state._all_enzymes_hook = lambda: {"Bad": ("", 1, 1)}
    sc._ENZYME_CUTS_CACHE.clear()
    try:
        cuts = _bio._enzyme_cuts(seq, ["Bad"], circular=False)
    finally:
        sc._state._all_enzymes_hook = real_hook
        sc._ENZYME_CUTS_CACHE.clear()
    assert cuts == []   # empty site → NO cuts (not len(seq) phantom cuts)


# ── BIO-2: coincident top-strand cuts ───────────────────────────────────────
def test_fragments_from_coincident_top_cuts_no_phantom_fulllength():
    """Two enzymes severing the SAME top bond (different overhangs) collapse to
    one boundary → one linearised fragment, not two phantom full-length ones."""
    seq = "AAAACCCCGGGGTTTT"   # n = 16
    cuts = [
        {"top": 4, "bot": 8, "overhang_seq": "CCCC", "kind": "5'", "enzyme": "E1"},
        {"top": 4, "bot": 4, "overhang_seq": "", "kind": "blunt", "enzyme": "E2"},
    ]  # coincident top (4), different bottom
    frags = _bio._fragments_from_cuts(seq, cuts, circular=True)
    assert len(frags) == 1                       # NOT 2 phantom fragments
    assert len(frags[0]["top_seq"]) == len(seq)  # single linearised ring


def test_fragments_distinct_cuts_still_two_fragments():
    """Non-coincident cuts are unaffected (the de-dup only triggers on a tie)."""
    seq = "AAAACCCCGGGGTTTT"
    cuts = [{"top": 4, "bot": 6, "overhang_seq": "CC", "kind": "5'", "enzyme": "E1"},
            {"top": 12, "bot": 14, "overhang_seq": "TT", "kind": "5'", "enzyme": "E2"}]
    frags = _bio._fragments_from_cuts(seq, cuts, circular=True)
    assert len(frags) == 2


# ── NET-1: fetch heavy-endpoint gate ────────────────────────────────────────
def test_fetch_in_heavy_endpoints():
    assert "fetch" in sc._AGENT_HEAVY_ENDPOINTS


# ── XML-1: DOCTYPE `>`-in-literal internal-subset bypass ────────────────────
def test_xml_doctype_gt_in_literal_still_refuses_internal_subset():
    bomb = ('<!DOCTYPE x SYSTEM "a>b" ['
            '<!ENTITY lol "LOL">'
            ']><x>&lol;</x>')
    with pytest.raises(ET.ParseError):
        _safe_xml_parse(bomb, allow_dtd=True)


def test_xml_clean_external_doctype_with_gt_in_literal_still_parses():
    ok = '<!DOCTYPE x SYSTEM "a>b"><x>hi</x>'
    root = _safe_xml_parse(ok, allow_dtd=True)
    assert root.tag == "x"
