"""Minimum-viable coverage for the agent endpoints added this
session: grammar / primer / parts / feature-library CRUD. Each
test exercises the happy path + at least one error class so a
regression in payload validation surfaces in CI."""

from __future__ import annotations

import pytest

import splicecraft as sc


class _MockApp:
    """Minimal app stub for testing agent endpoints that route writes
    through `app.call_from_thread`. The real PlasmidApp uses the call
    to hop to the Textual UI thread; in tests we run inline."""

    def call_from_thread(self, fn):
        return fn()


@pytest.fixture
def mock_app():
    return _MockApp()


# ── Cloning grammar CRUD ──────────────────────────────────────────────────────


def _make_grammar(gid: str = "test_grammar") -> dict:
    return {
        "id":              gid,
        "name":            "Test Grammar",
        "enzyme":          "BsaI",
        "level_up_enzyme": "BpiI",
        "site":            "GGTCTC",
        "spacer":          "N",
        "pad":             "GCGC",
        "forbidden_sites": {"BsaI": "GGTCTC"},
        "positions": [
            {"name": "Pos 1", "type": "Promoter",
             "oh5": "GGAG", "oh3": "AATG", "color": "green"},
            {"name": "Pos 2", "type": "CDS",
             "oh5": "AATG", "oh3": "GCTT", "color": "yellow"},
        ],
        "coding_types": ["CDS"],
        "type_to_insdc": {"CDS": "CDS", "Promoter": "promoter"},
    }


def test_grammar_create_get_delete_roundtrip():
    g = _make_grammar()
    res = sc._h_create_grammar(None, g)
    assert res == {"ok": True, "grammar_id": "test_grammar"}

    got = sc._h_get_grammar(None, {"grammar_id": "test_grammar"})
    assert got["ok"]
    assert got["grammar"]["name"] == "Test Grammar"
    assert got["grammar"]["editable"]

    listed = sc._h_list_grammars(None, {})
    ids = {row["id"] for row in listed["grammars"]}
    assert "test_grammar" in ids
    assert "gb_l0" in ids   # built-ins present

    deleted = sc._h_delete_grammar(None, {"grammar_id": "test_grammar"})
    assert deleted == {"ok": True, "grammar_id": "test_grammar"}


def test_grammar_create_refuses_builtin_id():
    g = _make_grammar(gid="gb_l0")
    res = sc._h_create_grammar(None, g)
    assert isinstance(res, tuple) and res[1] == 400
    assert "built-in" in res[0]["error"]


def test_grammar_update_unknown_returns_404():
    g = _make_grammar(gid="nonexistent_grammar_xyz")
    res = sc._h_update_grammar(None, g)
    assert isinstance(res, tuple) and res[1] == 404


def test_grammar_delete_builtin_refused():
    res = sc._h_delete_grammar(None, {"grammar_id": "gb_l0"})
    assert isinstance(res, tuple) and res[1] == 400


# ── Primer CRUD ───────────────────────────────────────────────────────────────


def test_primer_create_list_get_delete(mock_app):
    res = sc._h_create_primer(mock_app, {
        "name":     "test_primer_fwd",
        "sequence": "ATGCATGCATGCATGCATGC",
        "tm":       62.5,
        "status":   "Designed",
    })
    assert res["ok"]
    assert res["name"] == "test_primer_fwd"

    listed = sc._h_list_primers(mock_app, {})
    assert listed["count"] >= 1

    got = sc._h_get_primer(mock_app, {"sequence": "ATGCATGCATGCATGCATGC"})
    assert got["ok"]
    assert got["primer"]["name"] == "test_primer_fwd"
    assert got["primer"]["tm"] == 62.5

    res = sc._h_delete_primer(mock_app, {
        "sequence": "ATGCATGCATGCATGCATGC",
    })
    assert res["ok"]


def test_primer_create_duplicate_returns_409(mock_app):
    sc._h_create_primer(mock_app, {
        "name": "p1", "sequence": "ACGTACGTACGTACGTACGT",
    })
    res = sc._h_create_primer(mock_app, {
        "name": "p2", "sequence": "ACGTACGTACGTACGTACGT",
    })
    assert isinstance(res, tuple) and res[1] == 409
    assert "existing_name" in res[0]


def test_primer_get_missing_returns_404():
    res = sc._h_get_primer(None, {"sequence": "NONEXISTENT_SEQ_ATGC"})
    assert isinstance(res, tuple) and res[1] == 404


# ── Parts bin CRUD ─────────────────────────────────────────────────────────────


def test_part_create_update_delete_roundtrip(mock_app):
    part = {
        "name":     "test_part_cds",
        "grammar":  "gb_l0",
        "type":     "CDS",
        "position": "PC",
        "level":    0,
        "oh5":      "AATG",
        "oh3":      "GCTT",
        "sequence": "ATGGCAACG" * 5,
    }
    res = sc._h_create_part(mock_app, part)
    assert res["ok"]
    assert res["name"] == "test_part_cds"

    got = sc._h_get_part(mock_app, {"name": "test_part_cds"})
    assert got["ok"]
    assert got["part"]["grammar"] == "gb_l0"

    # Update
    part["oh5"] = "GGAG"
    res = sc._h_update_part(mock_app, part)
    assert res["ok"]

    res = sc._h_delete_part(mock_app, {
        "name": "test_part_cds", "grammar": "gb_l0",
    })
    assert res["ok"]
    assert res["deleted"] == 1


def test_part_create_duplicate_returns_409(mock_app):
    part = {
        "name":     "dup_part",
        "grammar":  "gb_l0",
        "type":     "CDS",
        "level":    0,
        "sequence": "ATGGCA" * 5,
    }
    sc._h_create_part(mock_app, part)
    res = sc._h_create_part(mock_app, part)
    assert isinstance(res, tuple) and res[1] == 409


def test_part_update_unknown_returns_404(mock_app):
    part = {
        "name":     "ghost_part",
        "grammar":  "gb_l0",
        "type":     "CDS",
        "level":    0,
        "sequence": "ATG" * 10,
    }
    res = sc._h_update_part(mock_app, part)
    assert isinstance(res, tuple) and res[1] == 404


# ── Feature library CRUD ──────────────────────────────────────────────────────


def test_feature_library_create_get_delete():
    res = sc._h_create_feature_library(None, {
        "name":         "test_promoter",
        "feature_type": "promoter",
        "strand":       1,
        "color":        "green",
        "sequence":     "AAGCTTCCAGTACAGGCTTGCAGTAGCT",
    })
    assert res["ok"]

    got = sc._h_get_feature_library(None, {"name": "test_promoter"})
    assert got["ok"]
    assert got["feature"]["feature_type"] == "promoter"

    listed = sc._h_list_feature_library(None, {})
    assert any(r["name"] == "test_promoter" for r in listed["features"])

    res = sc._h_delete_feature_library(None, {
        "name": "test_promoter", "feature_type": "promoter",
    })
    assert res["ok"]


def test_feature_library_create_duplicate_returns_409():
    f = {
        "name":         "dup_feat",
        "feature_type": "CDS",
        "strand":       1,
        "sequence":     "ATGGCA" * 6,
    }
    sc._h_create_feature_library(None, f)
    res = sc._h_create_feature_library(None, f)
    assert isinstance(res, tuple) and res[1] == 409


def test_feature_library_update_unknown_returns_404():
    res = sc._h_update_feature_library(None, {
        "name":     "ghost_feat",
        "sequence": "AAATTT",
    })
    assert isinstance(res, tuple) and res[1] == 404
