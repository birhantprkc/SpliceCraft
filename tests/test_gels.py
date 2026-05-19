"""
test_gels — saved agarose-gel snapshots.

Covers the data layer that backs `GelLibraryModal`:

  * `_load_gels` / `_save_gels` round-trip + cache + deepcopy
    invariant #17.
  * `_normalise_gel_entry`: name cap, notes cap, lane cap, lane-
    field caps, agarose clamp (0.3 – 5.0), NaN/inf rejection, id
    sanitisation.
  * `_sanitize_gel_id` rejects empty / NUL / `..` / `/` / `\\`.
  * `_find_gel` + `_gel_name_taken` dup-name guard.
  * `_extract_gel_refs` deduplicates and rejects email-style /
    double-sigil false positives.
  * Legacy-migration on body (covered in `test_experiments.py` —
    here we lock the gel-specific xref).
"""
from __future__ import annotations

import pytest

import splicecraft as sc


# ═══════════════════════════════════════════════════════════════════════════════
# Round-trip + cache hygiene
# ═══════════════════════════════════════════════════════════════════════════════

class TestGelsRoundTrip:
    """`_save_gels` / `_load_gels` preserve schema including forward-
    compat unknown keys (`_plugin_data`)."""

    def test_empty_load(self):
        assert sc._load_gels() == []

    def test_round_trip_preserves_fields(self):
        entries = [{
            "id":   "gel-aaaaaaaa",
            "name": "Friday digest",
            "lanes": [
                {"name": "Ladder", "source": "ladder", "detail": "1 kb"},
                {"name": "Digest", "source": "digest", "detail": "EcoRI"},
            ],
            "agarose_pct": 1.0,
            "notes": "",
            "created_at": "2026-05-19T12:00:00-04:00",
            "updated_at": "2026-05-19T12:00:00-04:00",
            "_plugin_data": {"some_plugin": {"x": 1}},
        }]
        sc._save_gels(entries)
        loaded = sc._load_gels()
        assert loaded == entries

    def test_load_deepcopies(self):
        sc._save_gels([{
            "id": "gel-aaaaaaaa", "name": "G", "lanes": [],
            "agarose_pct": 1.0, "notes": "",
            "created_at": "", "updated_at": "",
        }])
        first = sc._load_gels()
        first[0]["name"] = "MUTATED"
        second = sc._load_gels()
        assert second[0]["name"] == "G"

    def test_save_deepcopies(self):
        entries = [{
            "id": "gel-aaaaaaaa", "name": "G", "lanes": [],
            "agarose_pct": 1.0, "notes": "",
            "created_at": "", "updated_at": "",
        }]
        sc._save_gels(entries)
        entries[0]["name"] = "MUTATED"
        loaded = sc._load_gels()
        assert loaded[0]["name"] == "G"


# ═══════════════════════════════════════════════════════════════════════════════
# Id sanitisation
# ═══════════════════════════════════════════════════════════════════════════════

class TestGelIdSanitisation:
    def test_accepts_well_formed(self):
        assert sc._sanitize_gel_id("gel-aaaaaaaa") == "gel-aaaaaaaa"
        assert sc._sanitize_gel_id("Friday_digest") == "Friday_digest"
        assert sc._sanitize_gel_id("A.B-1") == "A.B-1"

    def test_rejects_empty_or_none(self):
        assert sc._sanitize_gel_id("") is None
        assert sc._sanitize_gel_id(None) is None
        assert sc._sanitize_gel_id(42) is None

    def test_rejects_path_meta(self):
        assert sc._sanitize_gel_id("../etc/passwd") is None
        assert sc._sanitize_gel_id("a/b") is None
        assert sc._sanitize_gel_id("a\\b") is None
        assert sc._sanitize_gel_id("a\x00b") is None
        assert sc._sanitize_gel_id(".hidden") is None

    def test_rejects_oversize(self):
        assert sc._sanitize_gel_id("a" * 65) is None


# ═══════════════════════════════════════════════════════════════════════════════
# Normalisation
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormaliseGelEntry:
    def test_fresh_stamps_timestamps(self):
        e = sc._normalise_gel_entry({"name": "G"}, fresh=True)
        assert "created_at" in e
        assert "updated_at" in e

    def test_existing_created_at_preserved(self):
        e = sc._normalise_gel_entry({
            "name": "G",
            "created_at": "2024-01-01T00:00:00+00:00",
        })
        assert e["created_at"] == "2024-01-01T00:00:00+00:00"

    def test_caps_name_length(self):
        e = sc._normalise_gel_entry({"name": "X" * 500})
        assert len(e["name"]) == sc._GEL_NAME_MAX_LEN

    def test_blank_name_defaults_to_untitled(self):
        e = sc._normalise_gel_entry({"name": "   "})
        assert e["name"] == "Untitled gel"

    def test_caps_notes_length(self):
        e = sc._normalise_gel_entry({"name": "G", "notes": "X" * 5000})
        assert len(e["notes"]) == sc._GEL_NOTES_MAX_LEN

    def test_clamps_agarose_low(self):
        e = sc._normalise_gel_entry({"name": "G", "agarose_pct": 0.0})
        assert e["agarose_pct"] == sc._GEL_AGAROSE_MIN

    def test_clamps_agarose_high(self):
        e = sc._normalise_gel_entry({"name": "G", "agarose_pct": 99.0})
        assert e["agarose_pct"] == sc._GEL_AGAROSE_MAX

    def test_rejects_nan_agarose(self):
        e = sc._normalise_gel_entry({"name": "G", "agarose_pct": float("nan")})
        assert e["agarose_pct"] == 1.0

    def test_rejects_inf_agarose(self):
        e = sc._normalise_gel_entry({"name": "G", "agarose_pct": float("inf")})
        assert e["agarose_pct"] == 1.0

    def test_rejects_non_numeric_agarose(self):
        e = sc._normalise_gel_entry({"name": "G", "agarose_pct": "bogus"})
        assert e["agarose_pct"] == 1.0

    def test_caps_lane_count(self):
        e = sc._normalise_gel_entry({
            "name": "G",
            "lanes": [{"name": f"L{i}", "source": "empty", "detail": ""}
                      for i in range(100)],
        })
        assert len(e["lanes"]) == sc._GEL_LANES_MAX

    def test_caps_lane_fields(self):
        e = sc._normalise_gel_entry({
            "name": "G",
            "lanes": [{"name": "X" * 500,
                       "source": "Y" * 500,
                       "detail": "Z" * 500}],
        })
        lane = e["lanes"][0]
        assert len(lane["name"])   == sc._GEL_LANE_NAME_MAX_LEN
        assert len(lane["source"]) == sc._GEL_LANE_SOURCE_MAX_LEN
        assert len(lane["detail"]) == sc._GEL_LANE_DETAIL_MAX_LEN

    def test_drops_non_dict_lanes(self):
        e = sc._normalise_gel_entry({
            "name": "G",
            "lanes": [
                "junk-string",
                None,
                42,
                {"name": "real", "source": "empty", "detail": ""},
            ],
        })
        assert len(e["lanes"]) == 1
        assert e["lanes"][0]["name"] == "real"

    def test_non_list_lanes_becomes_empty(self):
        e = sc._normalise_gel_entry({"name": "G", "lanes": "not-a-list"})
        assert e["lanes"] == []

    def test_replaces_invalid_id(self):
        # `..` is rejected by `_sanitize_gel_id`; the normaliser
        # falls back to a fresh `gel-<hex>` so the entry never gets
        # an unsanitised id.
        e = sc._normalise_gel_entry({"id": "..", "name": "G"})
        assert e["id"].startswith("gel-")
        assert "/" not in e["id"]
        assert ".." not in e["id"]


# ═══════════════════════════════════════════════════════════════════════════════
# Find + name-taken guard
# ═══════════════════════════════════════════════════════════════════════════════

class TestFindAndNameGuard:
    def test_find_returns_entry(self):
        sc._save_gels([{
            "id": "gel-aaaaaaaa", "name": "A", "lanes": [],
            "agarose_pct": 1.0, "notes": "",
            "created_at": "", "updated_at": "",
        }])
        assert sc._find_gel("gel-aaaaaaaa")["name"] == "A"

    def test_find_returns_none_for_missing(self):
        assert sc._find_gel("gel-missing") is None

    def test_find_returns_none_for_invalid_id(self):
        assert sc._find_gel("../etc") is None
        assert sc._find_gel("") is None

    def test_name_taken_dedup(self):
        sc._save_gels([{
            "id": "gel-aaaaaaaa", "name": "Friday digest", "lanes": [],
            "agarose_pct": 1.0, "notes": "",
            "created_at": "", "updated_at": "",
        }])
        assert sc._gel_name_taken("Friday digest") is True
        assert sc._gel_name_taken("Friday digest ") is True   # stripped
        assert sc._gel_name_taken("friday digest") is False   # case-sensitive
        assert sc._gel_name_taken("") is False
        assert sc._gel_name_taken(None) is False


# ═══════════════════════════════════════════════════════════════════════════════
# Gel-ref extraction (`&<id>` in experiment body)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGelRefExtraction:
    def test_basic(self):
        refs = sc._extract_gel_refs("Today: &runA then &runB")
        assert refs == ["runA", "runB"]

    def test_dedup_preserves_order(self):
        refs = sc._extract_gel_refs("&pcr first, &gibson, then &pcr again")
        assert refs == ["pcr", "gibson"]

    def test_rejects_word_prefix(self):
        # `foo&bar` shouldn't tag `bar` — the negative lookbehind
        # `(?<![\w&])` blocks word-adjacent matches.
        assert sc._extract_gel_refs("foo&bar") == []

    def test_rejects_double_sigil(self):
        assert sc._extract_gel_refs("&&double") == []

    def test_rejects_first_char_non_letter(self):
        # `&1` (numeric start) and `&-foo` (dash start) rejected.
        assert sc._extract_gel_refs("&1abc") == []
        assert sc._extract_gel_refs("&-foo") == []

    def test_empty_body(self):
        assert sc._extract_gel_refs("") == []
        assert sc._extract_gel_refs(None) == []     # type: ignore[arg-type]

    def test_normalise_extracts_gel_xref(self):
        e = sc._normalise_experiment_entry({
            "id": "exp-test1234", "title": "t",
            "body_md": "Ran &myGel today and &myGel again later.",
        }, fresh=True)
        assert e["attached_gel_ids"] == ["myGel"]


# ═══════════════════════════════════════════════════════════════════════════════
# Highlight + chip color
# ═══════════════════════════════════════════════════════════════════════════════

class TestGelHighlight:
    def test_chip_color_constant(self):
        assert sc._GEL_CHIP_COLOR == "#FFB347"

    async def test_in_editor_highlight_for_gel_ref(self):
        """`_ExperimentMarkdownTextArea._build_highlight_map` must
        inject the gel highlight name when a `&<id>` token is
        present."""
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.ExperimentsScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr.action_new_entry()
            await pilot.pause()
            await pilot.pause()
            ta = scr.query_one(
                "#exp-body", sc._ExperimentMarkdownTextArea,
            )
            ta.text = "Ran &myGel today."
            await pilot.pause()
            await pilot.pause()
            styles = ta._theme.syntax_styles
            assert (
                sc._ExperimentMarkdownTextArea._GEL_HL_NAME
                in styles
            )
            line0 = ta._highlights[0]
            names = [name for _s, _e, name in line0]
            assert (
                sc._ExperimentMarkdownTextArea._GEL_HL_NAME
                in names
            )

    async def test_backspace_at_gel_tag_end(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.ExperimentsScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr.action_new_entry()
            await pilot.pause()
            await pilot.pause()
            ta = scr.query_one(
                "#exp-body", sc._ExperimentMarkdownTextArea,
            )
            ta.text = "saw &myGel"
            ta.cursor_location = (0, len(ta.text))
            await pilot.pause()
            ta.action_delete_left()
            await pilot.pause()
            assert ta.text == "saw "


# ═══════════════════════════════════════════════════════════════════════════════
# Spellcheck masks gel refs
# ═══════════════════════════════════════════════════════════════════════════════

class TestSpellcheckMask:
    def test_masks_gel_ref(self):
        # Skip if pyspellchecker isn't available.
        try:
            from spellchecker import SpellChecker  # noqa: F401
        except ImportError:
            pytest.skip("pyspellchecker not installed")
        assert sc._spellcheck_body("&clonded test.") == []


# ═══════════════════════════════════════════════════════════════════════════════
# Click-to-open: Ctrl+G + double-click open the right modal
# ═══════════════════════════════════════════════════════════════════════════════

class TestClickToOpen:
    async def test_ctrl_g_no_tag_under_cursor_notifies(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.ExperimentsScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr.action_new_entry()
            await pilot.pause()
            await pilot.pause()
            ta = scr.query_one(
                "#exp-body", sc._ExperimentMarkdownTextArea,
            )
            ta.text = "just prose, no tag here"
            ta.cursor_location = (0, 5)
            await pilot.pause()
            # No modal opens, no exception — notify-only.
            scr.action_go_to_tag()
            await pilot.pause()
            assert isinstance(app.screen, sc.ExperimentsScreen)

    async def test_ctrl_g_on_gel_tag_opens_library(self):
        # Seed a gel so the open path succeeds.
        sc._save_gels([{
            "id": "gel-aaaaaaaa", "name": "test", "lanes": [],
            "agarose_pct": 1.0, "notes": "",
            "created_at": "", "updated_at": "",
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.ExperimentsScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr.action_new_entry()
            await pilot.pause()
            await pilot.pause()
            ta = scr.query_one(
                "#exp-body", sc._ExperimentMarkdownTextArea,
            )
            ta.text = "saw &gel-aaaaaaaa today"
            ta.cursor_location = (0, 6)   # on `g` of `&gel-...`
            await pilot.pause()
            scr.action_go_to_tag()
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, sc.GelLibraryModal)

    async def test_ctrl_g_on_action_tag_opens_actions_picker(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.ExperimentsScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr.action_new_entry()
            await pilot.pause()
            await pilot.pause()
            ta = scr.query_one(
                "#exp-body", sc._ExperimentMarkdownTextArea,
            )
            ta.text = "today: !digest then !ligate"
            ta.cursor_location = (0, 8)   # on `d` of `!digest`
            await pilot.pause()
            scr.action_go_to_tag()
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, sc.ActionsPickerModal)

    async def test_ctrl_g_on_missing_gel_notifies(self):
        """An `&unknown` ref where the gel doesn't exist must surface
        a friendly notify rather than opening an empty modal."""
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.ExperimentsScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr.action_new_entry()
            await pilot.pause()
            await pilot.pause()
            ta = scr.query_one(
                "#exp-body", sc._ExperimentMarkdownTextArea,
            )
            ta.text = "saw &ghost-gel today"
            ta.cursor_location = (0, 8)   # on `g` of `&ghost-gel`
            await pilot.pause()
            scr.action_go_to_tag()
            await pilot.pause()
            await pilot.pause()
            # No modal opened.
            assert isinstance(app.screen, sc.ExperimentsScreen)
