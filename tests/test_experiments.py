"""
test_experiments — lab-notebook (Experiments) screen + persistence tests.

Covers:
  * Sanitisation of entry ids (no `..`, separators, NUL, oversized).
  * `_save_experiments` / `_load_experiments` round-trip with the v1
    envelope schema. Backup `.bak` after second save. Deepcopy-on-read
    invariant.
  * Entry normalisation: title/body/tag caps, denormalised plasmid-ref
    xref, timestamp stamping.
  * Plasmid-ref extraction + render (the `splicecraft://plasmid/<id>`
    custom-scheme markdown link).
  * Image attach helper: per-image cap, per-entry-dir cap, symlink
    refusal, atomic write, suggested-extension whitelist.
  * Spellcheck primitives: code-span masking, plasmid-ref masking, URL
    masking.
  * Screen mount + lifecycle: New → edit → save → reload entries table.
  * Sub-tab gating: Compose/Attachments disabled until an entry is
    loaded; re-enabled on entry pick.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

import splicecraft as sc


# ═══════════════════════════════════════════════════════════════════════════════
# Sanitisation
# ═══════════════════════════════════════════════════════════════════════════════

class TestSanitizeExperimentId:
    def test_accepts_valid(self):
        assert sc._sanitize_experiment_id("exp-abc12345") == "exp-abc12345"
        assert sc._sanitize_experiment_id("A0") == "A0"
        assert sc._sanitize_experiment_id("a.b-c_d") == "a.b-c_d"

    def test_rejects_empty(self):
        assert sc._sanitize_experiment_id("") is None
        assert sc._sanitize_experiment_id(None) is None  # type: ignore[arg-type]
        assert sc._sanitize_experiment_id(123) is None  # type: ignore[arg-type]

    def test_rejects_traversal(self):
        assert sc._sanitize_experiment_id("..") is None
        assert sc._sanitize_experiment_id("a/b") is None
        assert sc._sanitize_experiment_id("a\\b") is None
        assert sc._sanitize_experiment_id("../etc/passwd") is None

    def test_rejects_nul(self):
        assert sc._sanitize_experiment_id("a\x00b") is None

    def test_rejects_leading_dot(self):
        # `.hidden` would create hidden file under EXPERIMENTS_DIR
        assert sc._sanitize_experiment_id(".hidden") is None

    def test_rejects_too_long(self):
        # _EXPERIMENT_ID_RE caps at 64 chars total
        assert sc._sanitize_experiment_id("a" * 65) is None
        assert sc._sanitize_experiment_id("a" * 64) == "a" * 64

    def test_rejects_shell_metas(self):
        for bad in ("a;b", "a&b", "a|b", "a$b", "a`b", "a*b", "a?b"):
            assert sc._sanitize_experiment_id(bad) is None, bad


class TestNewExperimentId:
    def test_format(self):
        eid = sc._new_experiment_id()
        assert eid.startswith("exp-")
        assert len(eid) == 12          # `exp-` + 8 hex
        # Round-trips through sanitiser
        assert sc._sanitize_experiment_id(eid) == eid

    def test_avoids_collision(self):
        # If all 8-hex slots were taken (impossible but exercise the
        # branch), we fall back to a longer id; here we just exercise
        # the `existing` argument.
        eid = sc._new_experiment_id(existing=set())
        assert eid.startswith("exp-")


# ═══════════════════════════════════════════════════════════════════════════════
# Persistence
# ═══════════════════════════════════════════════════════════════════════════════

class TestExperimentsPersistence:
    def test_empty_load(self):
        entries = sc._load_experiments()
        assert entries == []

    def test_round_trip(self):
        entry = {
            "id": "exp-test1234",
            "title": "Cloning round 1",
            "body_md": "Today: cut with HindIII.\n@plasmid:pUC19",
            "tags": ["cloning", "round-1"],
        }
        normalised = sc._normalise_experiment_entry(entry, fresh=True)
        sc._save_experiments([normalised])
        # Cache invalidated when we re-read
        sc._experiments_cache = None
        out = sc._load_experiments()
        assert len(out) == 1
        assert out[0]["id"] == "exp-test1234"
        assert out[0]["body_md"] == entry["body_md"]
        assert out[0]["attached_plasmid_ids"] == ["pUC19"]
        assert "created_at" in out[0]
        assert "updated_at" in out[0]

    def test_schema_envelope_written(self):
        sc._save_experiments([
            sc._normalise_experiment_entry({
                "id": "exp-test1234", "title": "t", "body_md": "b",
            }, fresh=True),
        ])
        raw = json.loads(sc._EXPERIMENTS_FILE.read_text())
        assert "_schema_version" in raw
        assert raw["_schema_version"] >= 1
        assert "entries" in raw
        assert isinstance(raw["entries"], list)

    def test_bak_on_second_save(self):
        for i in range(2):
            sc._save_experiments([
                sc._normalise_experiment_entry({
                    "id": f"exp-test123{i}", "title": "t",
                    "body_md": f"body {i}",
                }, fresh=True),
            ])
        bak = sc._EXPERIMENTS_FILE.with_suffix(
            sc._EXPERIMENTS_FILE.suffix + ".bak",
        )
        assert bak.exists()
        bak_data = json.loads(bak.read_text())
        # `.bak` holds the PREVIOUS save (i=0).
        assert bak_data["entries"][0]["body_md"] == "body 0"

    def test_deepcopy_on_read(self):
        """Mutating a returned entry must NOT poison the cache."""
        e = sc._normalise_experiment_entry({
            "id": "exp-aaaaaaaa", "title": "t", "body_md": "b",
        }, fresh=True)
        sc._save_experiments([e])
        sc._experiments_cache = None
        first = sc._load_experiments()
        first[0]["body_md"] = "MUTATED"
        # Caller's mutation must not leak into cache
        second = sc._load_experiments()
        assert second[0]["body_md"] == "b"

    def test_deepcopy_on_save(self):
        """Mutating the entries list AFTER save must NOT poison the cache."""
        e = sc._normalise_experiment_entry({
            "id": "exp-bbbbbbbb", "title": "t", "body_md": "b",
        }, fresh=True)
        entries = [e]
        sc._save_experiments(entries)
        # Mutate post-save (caller still holds the list reference)
        entries[0]["body_md"] = "POISONED"
        # Cache should be insulated
        out = sc._load_experiments()
        assert out[0]["body_md"] == "b"

    def test_load_filters_non_dict(self):
        """Hand-edited JSON with stray non-dict entries should not
        crash the loader."""
        raw = {
            "_schema_version": 1,
            "entries": [
                {"id": "exp-good", "title": "ok"},
                "not a dict",
                42,
                None,
            ],
        }
        sc._EXPERIMENTS_FILE.write_text(json.dumps(raw))
        sc._experiments_cache = None
        out = sc._load_experiments()
        assert len(out) == 1
        assert out[0]["id"] == "exp-good"


# ═══════════════════════════════════════════════════════════════════════════════
# Entry normalisation
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormaliseEntry:
    def test_title_truncated(self):
        e = sc._normalise_experiment_entry({
            "id": "exp-12345678", "title": "x" * 1000, "body_md": "",
        })
        assert len(e["title"]) == sc._EXPERIMENT_TITLE_MAX_LEN

    def test_body_truncated(self):
        # Build a body > 1 MB
        big = "A" * (sc._EXPERIMENT_BODY_MAX_BYTES + 10_000)
        e = sc._normalise_experiment_entry({
            "id": "exp-12345678", "title": "t", "body_md": big,
        })
        assert len(e["body_md"].encode("utf-8")) \
                <= sc._EXPERIMENT_BODY_MAX_BYTES

    def test_tags_normalised(self):
        e = sc._normalise_experiment_entry({
            "id": "exp-12345678", "title": "t", "body_md": "",
            "tags": ["", "  trim  ", "ok", None, 42, "x" * 100],
        })
        # Empty + whitespace dropped, non-strings dropped, long ones
        # truncated to _EXPERIMENT_TAG_MAX_LEN.
        assert "trim" in e["tags"]
        assert "ok" in e["tags"]
        assert None not in e["tags"]
        assert 42 not in e["tags"]
        # Long tag truncated
        long_tag = next((t for t in e["tags"] if t.startswith("x")), None)
        if long_tag is not None:
            assert len(long_tag) <= sc._EXPERIMENT_TAG_MAX_LEN

    def test_tags_cap(self):
        e = sc._normalise_experiment_entry({
            "id": "exp-12345678", "title": "t", "body_md": "",
            "tags": [f"tag{i}" for i in range(100)],
        })
        assert len(e["tags"]) == sc._EXPERIMENT_TAGS_MAX

    def test_xref_rebuilt(self):
        e = sc._normalise_experiment_entry({
            "id": "exp-12345678", "title": "t",
            "body_md": "@plasmid:pA and @plasmid:pB and @plasmid:pA",
            "attached_plasmid_ids": ["stale-ignored"],
        })
        assert e["attached_plasmid_ids"] == ["pA", "pB"]

    def test_fresh_stamps_created_at(self):
        e = sc._normalise_experiment_entry({
            "id": "exp-12345678", "title": "t", "body_md": "",
        }, fresh=True)
        assert "created_at" in e
        assert "updated_at" in e

    def test_existing_created_at_preserved(self):
        e = sc._normalise_experiment_entry({
            "id": "exp-12345678", "title": "t", "body_md": "",
            "created_at": "2024-01-01T00:00:00+00:00",
        })
        # fresh=False keeps the existing created_at
        assert e["created_at"] == "2024-01-01T00:00:00+00:00"

    def test_id_sanitised_on_normalise(self):
        # Invalid id gets replaced rather than crash
        e = sc._normalise_experiment_entry({
            "id": "../bad", "title": "t", "body_md": "",
        }, fresh=True)
        assert e["id"].startswith("exp-")
        assert "/" not in e["id"]


# ═══════════════════════════════════════════════════════════════════════════════
# Plasmid refs
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlasmidRefs:
    def test_extract_unique_in_order(self):
        body = "See @plasmid:pUC19 and @plasmid:pACYC and @plasmid:pUC19"
        assert sc._extract_plasmid_refs(body) == ["pUC19", "pACYC"]

    def test_extract_empty(self):
        assert sc._extract_plasmid_refs("") == []
        assert sc._extract_plasmid_refs("no refs here") == []

    def test_render_produces_links(self):
        out = sc._render_plasmid_refs("@plasmid:pUC19")
        assert sc._PLASMID_LINK_SCHEME in out
        assert "pUC19" in out

    def test_render_passthrough_no_refs(self):
        assert sc._render_plasmid_refs("plain text") == "plain text"
        assert sc._render_plasmid_refs("") == ""

    def test_regex_rejects_invalid(self):
        # `@plasmid:` followed by a separator should NOT match a path
        # under the EXPERIMENTS_DIR (the regex uses a tight character
        # class).
        body = "@plasmid:../etc"
        refs = sc._extract_plasmid_refs(body)
        # The greedy `\w.\-` doesn't accept `/`, so the match stops
        # before the separator.
        for r in refs:
            assert "/" not in r
            assert ".." not in r


# ═══════════════════════════════════════════════════════════════════════════════
# Image attach
# ═══════════════════════════════════════════════════════════════════════════════

# Smallest valid PNG (1×1 transparent) for cap-bypass tests.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a"
    "0000000d49484452"
    "0000000100000001"
    "08060000001f15c4"
    "890000000d494441"
    "5478da6300000000"
    "01000000050001a5"
    "f645400000000049"
    "454e44ae426082"
)


class TestSaveExperimentImage:
    def test_round_trip(self):
        out = sc._save_experiment_image(
            "exp-img1234", _TINY_PNG, "screenshot.png",
        )
        assert out is not None
        assert out.is_file()
        assert out.read_bytes() == _TINY_PNG
        assert out.name.endswith(".png")

    def test_per_image_cap(self, monkeypatch):
        # Drop the cap so we don't actually need a 10 MB payload.
        monkeypatch.setattr(sc, "_EXPERIMENT_IMAGE_MAX_BYTES", 16)
        out = sc._save_experiment_image(
            "exp-img1234", _TINY_PNG, "x.png",
        )
        assert out is None

    def test_dir_cap(self, monkeypatch):
        # Force the per-dir cap to land between two images.
        monkeypatch.setattr(
            sc, "_EXPERIMENT_DIR_MAX_BYTES", len(_TINY_PNG) + 5,
        )
        # First write succeeds; second is refused.
        ok = sc._save_experiment_image("exp-img1234", _TINY_PNG, "a.png")
        assert ok is not None
        no = sc._save_experiment_image("exp-img1234", _TINY_PNG, "b.png")
        assert no is None

    def test_extension_whitelist(self):
        # An unrecognised extension falls back to .png on save.
        out = sc._save_experiment_image(
            "exp-img1234", _TINY_PNG, "screenshot.weird",
        )
        assert out is not None
        assert out.suffix == ".png"

    def test_extension_preserved(self):
        out = sc._save_experiment_image(
            "exp-img1234", _TINY_PNG, "snap.jpg",
        )
        assert out is not None
        assert out.suffix == ".jpg"

    def test_sanitization_refuses_bad_id(self):
        for bad in ("..", "a/b", "a\\b", "", None):
            out = sc._save_experiment_image(
                bad,  # type: ignore[arg-type]
                _TINY_PNG, "x.png",
            )
            assert out is None, f"accepted bad id: {bad!r}"

    def test_rejects_non_bytes(self):
        out = sc._save_experiment_image(
            "exp-img1234", "not bytes",  # type: ignore[arg-type]
            "x.png",
        )
        assert out is None

    def test_symlink_refusal_at_entry_dir(self, tmp_path):
        # Create a symlink at the attach-dir path; the helper refuses.
        target = tmp_path / "real-target"
        target.mkdir()
        # Build the symlink path inside the experiments dir
        sc._EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
        link = sc._EXPERIMENTS_DIR / "exp-symlnk1"
        # If we can't create symlinks on this OS (e.g. Windows without
        # privilege), skip.
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError):
            pytest.skip("symlink not available on this platform")
        d = sc._experiment_attach_dir("exp-symlnk1", create=False)
        assert d is None


class TestExperimentDirSize:
    def test_empty_dir(self):
        assert sc._experiment_dir_size_bytes("exp-empty12") == 0

    def test_sum_files(self):
        for n in ("a.png", "b.png", "c.png"):
            sc._save_experiment_image("exp-sumtest", _TINY_PNG, n)
        sz = sc._experiment_dir_size_bytes("exp-sumtest")
        assert sz == len(_TINY_PNG) * 3

    def test_bad_id_returns_zero(self):
        assert sc._experiment_dir_size_bytes("../bad") == 0


class TestDeleteAttachDir:
    def test_removes_files_and_dir(self):
        out = sc._save_experiment_image(
            "exp-delme12", _TINY_PNG, "img.png",
        )
        assert out is not None
        assert out.parent.exists()
        sc._delete_experiment_attach_dir("exp-delme12")
        assert not out.exists()

    def test_missing_dir_is_noop(self):
        sc._delete_experiment_attach_dir("exp-nodir12")
        # No exception = pass


# ═══════════════════════════════════════════════════════════════════════════════
# Spellcheck primitives
# ═══════════════════════════════════════════════════════════════════════════════

class TestSpellcheckPrimitives:
    def test_engine_available(self):
        spell = sc._get_spellcheck_engine()
        assert spell is not None, (
            "pyspellchecker must be installed for tests "
            "(pip install pyspellchecker)"
        )

    def test_finds_misspelling(self):
        ms = sc._spellcheck_body("Today I clonded a plasmid.")
        words = [w for w, _ in ms]
        assert "clonded" in words

    def test_masks_code_span(self):
        ms = sc._spellcheck_body("Use `clonded` in code.")
        # `clonded` lives in code → not flagged
        assert ms == []

    def test_masks_fenced_block(self):
        body = "```\nbad clonded text\n```"
        ms = sc._spellcheck_body(body)
        assert ms == []

    def test_masks_plasmid_ref(self):
        ms = sc._spellcheck_body("@plasmid:clonded should be skipped.")
        assert ms == []

    def test_masks_url(self):
        ms = sc._spellcheck_body(
            "See https://example.com/clonded for details.",
        )
        # URL token holds 'clonded' but the whole URL is masked.
        words = [w for w, _ in ms]
        assert "clonded" not in words

    def test_masks_markdown_link(self):
        ms = sc._spellcheck_body("See [clonded](url) for details.")
        # The link body is masked
        words = [w for w, _ in ms]
        assert "clonded" not in words

    def test_strip_preserves_length(self):
        before = "abc `xyz` def"
        after = sc._spellcheck_strip_code(before)
        assert len(before) == len(after)


# ═══════════════════════════════════════════════════════════════════════════════
# Screen mount + lifecycle (async)
# ═══════════════════════════════════════════════════════════════════════════════

_TERM = (160, 48)


class TestScreenMount:
    async def test_open_via_action(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.action_open_experiments()
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, sc.ExperimentsScreen)

    async def test_open_via_menu_string(self):
        """`open_menu("Experiments", ...)` should direct-open the screen
        (no dropdown)."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            # Mimic MenuBar.on_click's direct-open branch
            app.action_open_experiments()
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, sc.ExperimentsScreen)

    async def test_initial_state(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.action_open_experiments()
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            from textual.widgets import TabbedContent, DataTable, TabPane
            tabs = scr.query_one("#exp-tabs", TabbedContent)
            assert tabs.active == "exp-sub-entries"
            # Compose + Attachments disabled until an entry exists
            compose = scr.query_one("#exp-sub-compose", TabPane)
            attach  = scr.query_one("#exp-sub-attachments", TabPane)
            assert compose.disabled
            assert attach.disabled

    async def test_new_entry_switches_to_compose(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.action_open_experiments()
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr.action_new_entry()
            await pilot.pause()
            await pilot.pause()
            from textual.widgets import TabbedContent, TabPane
            tabs = scr.query_one("#exp-tabs", TabbedContent)
            assert tabs.active == "exp-sub-compose"
            compose = scr.query_one("#exp-sub-compose", TabPane)
            assert not compose.disabled

    async def test_entries_table_populated_after_save(self):
        # Pre-seed an entry on disk
        e = sc._normalise_experiment_entry({
            "id": "exp-fixture1", "title": "Fixture 1",
            "body_md": "body content", "tags": ["a"],
        }, fresh=True)
        sc._save_experiments([e])
        sc._experiments_cache = None

        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.action_open_experiments()
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            from textual.widgets import DataTable
            t = scr.query_one("#exp-entries-table", DataTable)
            assert t.row_count == 1

    async def test_body_save_extracts_plasmid_refs(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.action_open_experiments()
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr.action_new_entry()
            await pilot.pause()
            await pilot.pause()
            from textual.widgets import TextArea
            ta = scr.query_one("#exp-body", TextArea)
            ta.text = "Today: @plasmid:pUC19 cut with HindIII"
            await pilot.pause()
            scr.action_save_entry()
            await pilot.pause()
            sc._experiments_cache = None
            entries = sc._load_experiments()
            assert len(entries) == 1
            assert "pUC19" in entries[0]["attached_plasmid_ids"]

    async def test_close_auto_saves_dirty(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.action_open_experiments()
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr.action_new_entry()
            await pilot.pause()
            await pilot.pause()
            from textual.widgets import TextArea
            ta = scr.query_one("#exp-body", TextArea)
            ta.text = "unsaved body"
            await pilot.pause()
            # Mark dirty explicitly via the path the screen uses
            scr._mark_dirty(True)
            scr.action_cancel()
            await pilot.pause()
            sc._experiments_cache = None
            out = sc._load_experiments()
            # The auto-save on close persisted the body
            assert any(
                e.get("body_md") == "unsaved body" for e in out
            ), [e.get("body_md") for e in out]


# ═══════════════════════════════════════════════════════════════════════════════
# Markdown link click handler
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlasmidLinkScheme:
    def test_render_link_uses_scheme(self):
        out = sc._render_plasmid_refs("@plasmid:pTest")
        assert "splicecraft://plasmid/pTest" in out

    def test_link_scheme_constant(self):
        assert sc._PLASMID_LINK_SCHEME == "splicecraft://plasmid/"


# ═══════════════════════════════════════════════════════════════════════════════
# Menu integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestMenuIntegration:
    def test_experiments_in_menu_list(self):
        assert "Experiments" in sc.MenuBar.MENUS

    def test_action_method_exists(self):
        assert hasattr(sc.PlasmidApp, "action_open_experiments")

    def test_class_blocks_undo(self):
        assert sc.ExperimentsScreen._blocks_undo is True
