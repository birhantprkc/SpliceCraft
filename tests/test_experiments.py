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

import json
import os

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
            "body_md": "Today: cut with HindIII.\n@pUC19",
            "tags": ["cloning", "round-1"],
        }
        normalised = sc._normalise_experiment_entry(entry, fresh=True)
        sc._save_experiments([normalised])
        # Cache invalidated when we re-read
        sc._state._experiments_cache = None
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
        raw = json.loads(sc._state._EXPERIMENTS_FILE.read_text())
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
        bak = sc._state._EXPERIMENTS_FILE.with_suffix(
            sc._state._EXPERIMENTS_FILE.suffix + ".bak",
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
        sc._state._experiments_cache = None
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
        sc._state._EXPERIMENTS_FILE.write_text(json.dumps(raw))
        sc._state._experiments_cache = None
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
            "body_md": "@pA and @pB and @pA",
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
        body = "See @pUC19 and @pACYC and @pUC19"
        assert sc._extract_plasmid_refs(body) == ["pUC19", "pACYC"]

    def test_extract_empty(self):
        assert sc._extract_plasmid_refs("") == []
        assert sc._extract_plasmid_refs("no refs here") == []

    def test_render_produces_links(self):
        out = sc._render_plasmid_refs("@pUC19")
        assert sc._PLASMID_LINK_SCHEME in out
        assert "pUC19" in out

    def test_render_passthrough_no_refs(self):
        assert sc._render_plasmid_refs("plain text") == "plain text"
        assert sc._render_plasmid_refs("") == ""

    def test_regex_rejects_path_traversal(self):
        # `@` followed by a separator or dot is rejected by the regex
        # (id must START with `[A-Za-z]`).
        assert sc._extract_plasmid_refs("@../etc") == []
        assert sc._extract_plasmid_refs("@/etc") == []
        assert sc._extract_plasmid_refs("@.hidden") == []

    def test_regex_rejects_email_suffix(self):
        # The `(?<![\w@])` lookbehind makes sure `user@example.com`
        # doesn't tag `example.com` as a plasmid.
        assert sc._extract_plasmid_refs("user@example.com") == []
        assert sc._extract_plasmid_refs("@@double") == []

    def test_legacy_format_migrated_on_load(self):
        """Bodies stored with the pre-2026-05-18 `@plasmid:<id>` /
        `@actions:<id>` format get rewritten to the single-sigil
        format on load. The `_save_experiments` mirror persists the
        migrated body to disk."""
        # Hand-write a legacy body to disk (bypasses normalise).
        sc._save_experiments([{
            "id": "exp-legacy01",
            "title": "Legacy",
            "body_md": "Today: @plasmid:pUC19 and @actions:digest",
        }])
        sc._state._experiments_cache = None
        out = sc._load_experiments()
        assert out[0]["body_md"] == "Today: @pUC19 and !digest"


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
        sc._state._EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
        link = sc._state._EXPERIMENTS_DIR / "exp-symlnk1"
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
        # Both old `@plasmid:<id>` and new `@<id>` formats are masked.
        assert sc._spellcheck_body("@plasmid:clonded should be skipped.") == []
        assert sc._spellcheck_body("@clonded should be skipped.") == []

    def test_masks_action_ref(self):
        assert sc._spellcheck_body("@actions:clonded test.") == []
        assert sc._spellcheck_body("!clonded test.") == []

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
        """Experiments menu opens the project picker first (refactor
        2026-05-18 — mirrors parts-bin); picking a project then
        pushes the ExperimentsScreen onto the stack."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.action_open_experiments()
            await pilot.pause()
            await pilot.pause()
            assert isinstance(
                app.screen, sc.ExperimentProjectsPickerModal,
            )
            # Pick the active project (the migration in App.compose
            # created Main Project and set it active).
            app.screen._open(None)
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, sc.ExperimentsScreen)

    async def test_open_via_menu_string(self):
        """`open_menu("Experiments", ...)` routes through the same
        `action_open_experiments` → picker → screen pipeline."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.action_open_experiments()
            await pilot.pause()
            await pilot.pause()
            assert isinstance(
                app.screen, sc.ExperimentProjectsPickerModal,
            )
            app.screen._open(None)
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, sc.ExperimentsScreen)

    async def test_rename_from_menu_preserves_body_and_tags(self):
        """[staleguard / data-loss regression] Opening Experiments from the
        menu loads no entry (`_current_entry is None`, compose widgets empty);
        renaming a cursor row must NOT wipe the entry's body + tags. The fix
        loads the full entry into the compose widgets before the rename
        persists, instead of rebuilding the record from the empty widgets."""
        from textual.widgets import DataTable, Input
        body = "IMPORTANT lab notes that must survive a rename."
        sc._save_experiments([sc._normalise_experiment_entry(
            {"id": "exp-dl1", "title": "Old", "body_md": body,
             "tags": ["cloning", "gel"]})])
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            app.push_screen(sc.ExperimentsScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            t = scr.query_one("#exp-entries-table", DataTable)
            t.focus()
            t.move_cursor(row=0)
            assert scr._current_entry is None      # menu-open precondition
            scr._rename_current()
            await pilot.pause()
            await pilot.pause()
            app.screen.query_one("#exprn-input", Input).value = "New"
            app.screen.action_submit()
            await pilot.pause()
            await pilot.pause()
        e2 = {e["id"]: e for e in sc._load_experiments()}["exp-dl1"]
        assert e2["title"] == "New"
        assert e2["body_md"] == body            # was silently wiped pre-fix
        assert e2["tags"] == ["cloning", "gel"]

    async def test_initial_state(self):
        # Split-pane layout (2026-05-18): entries list is always
        # visible on the left, Compose + Attachments are the two
        # right-pane tabs. With no entry loaded both tabs are
        # disabled; `tabs.active` defaults to "exp-sub-compose"
        # (the `initial=` param on TabbedContent) and Textual
        # renders it greyed out.
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            # Bypass picker — these tests exercise screen behaviour,
            # not the picker → screen pipeline (covered separately).
            app.push_screen(sc.ExperimentsScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            from textual.widgets import TabbedContent, DataTable, TabPane
            scr.query_one("#exp-tabs", TabbedContent)
            # Entries table is always-visible in the left pane.
            scr.query_one("#exp-entries-table", DataTable)
            # Compose + Attachments are disabled until an entry exists.
            compose = scr.query_one("#exp-sub-compose", TabPane)
            attach  = scr.query_one("#exp-sub-attachments", TabPane)
            assert compose.disabled
            assert attach.disabled

    async def test_new_entry_switches_to_compose(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            # Bypass picker — these tests exercise screen behaviour,
            # not the picker → screen pipeline (covered separately).
            app.push_screen(sc.ExperimentsScreen())
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
        sc._state._experiments_cache = None

        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            # Bypass picker — these tests exercise screen behaviour,
            # not the picker → screen pipeline (covered separately).
            app.push_screen(sc.ExperimentsScreen())
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
            # Bypass picker — these tests exercise screen behaviour,
            # not the picker → screen pipeline (covered separately).
            app.push_screen(sc.ExperimentsScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr.action_new_entry()
            await pilot.pause()
            await pilot.pause()
            from textual.widgets import TextArea
            ta = scr.query_one("#exp-body", TextArea)
            ta.text = "Today: @pUC19 cut with HindIII"
            await pilot.pause()
            scr.action_save_entry()
            await pilot.pause()
            sc._state._experiments_cache = None
            entries = sc._load_experiments()
            assert len(entries) == 1
            assert "pUC19" in entries[0]["attached_plasmid_ids"]

    async def test_close_with_dirty_pops_save_modal(self):
        """`action_cancel` no longer silent-saves (2026-05-18). When
        the compose buffer is dirty, it opens
        `ExperimentUnsavedChangesModal`; the user picks save / abandon
        / cancel. This test drives the Save path."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.ExperimentsScreen())
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
            scr._mark_dirty(True)
            scr.action_cancel()
            await pilot.pause()
            await pilot.pause()
            # Unsaved-changes modal is now on top.
            assert isinstance(
                app.screen, sc.ExperimentUnsavedChangesModal,
            )
            # Drive Save choice — modal dismisses with "save",
            # screen's callback persists then dismisses itself.
            app.screen.dismiss("save")
            await pilot.pause()
            await pilot.pause()
            sc._state._experiments_cache = None
            out = sc._load_experiments()
            assert any(
                e.get("body_md") == "unsaved body" for e in out
            ), [e.get("body_md") for e in out]

    async def test_close_dirty_abandon_discards_changes(self):
        """Picking Abandon dismisses the screen without persisting
        the dirty body."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.ExperimentsScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr.action_new_entry()
            await pilot.pause()
            await pilot.pause()
            sc._state._experiments_cache = None
            before = sc._load_experiments()
            before_bodies = [e.get("body_md") for e in before]
            from textual.widgets import TextArea
            ta = scr.query_one("#exp-body", TextArea)
            ta.text = "should-not-persist"
            await pilot.pause()
            scr._mark_dirty(True)
            scr.action_cancel()
            await pilot.pause()
            await pilot.pause()
            assert isinstance(
                app.screen, sc.ExperimentUnsavedChangesModal,
            )
            app.screen.dismiss("abandon")
            await pilot.pause()
            await pilot.pause()
            sc._state._experiments_cache = None
            after = sc._load_experiments()
            after_bodies = [e.get("body_md") for e in after]
            assert "should-not-persist" not in after_bodies
            assert before_bodies == after_bodies

    async def test_close_dirty_save_failure_keeps_screen(self):
        """Edge sweep — if `_persist_current` fails inside the Save
        path (disk full, RO mount), the screen must stay on top so
        the user can retry. Pre-fix the dismiss happened regardless,
        silently losing the dirty buffer."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.ExperimentsScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr.action_new_entry()
            await pilot.pause()
            await pilot.pause()
            from textual.widgets import TextArea
            ta = scr.query_one("#exp-body", TextArea)
            ta.text = "save will fail"
            await pilot.pause()
            scr._mark_dirty(True)
            # Force `_persist_current` to fail.
            original = scr._persist_current
            scr._persist_current = lambda: False  # type: ignore
            scr.action_cancel()
            await pilot.pause()
            await pilot.pause()
            assert isinstance(
                app.screen, sc.ExperimentUnsavedChangesModal,
            )
            app.screen.dismiss("save")
            await pilot.pause()
            await pilot.pause()
            # ExperimentsScreen is STILL on top — save failed but
            # we didn't lose the buffer.
            scr._persist_current = original  # type: ignore
            assert isinstance(app.screen, sc.ExperimentsScreen)
            ta2 = app.screen.query_one("#exp-body", TextArea)
            assert ta2.text == "save will fail"

    async def test_close_dirty_cancel_keeps_screen(self):
        """Picking Cancel dismisses the modal but keeps the
        `ExperimentsScreen` on top so the user can keep editing."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.ExperimentsScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr.action_new_entry()
            await pilot.pause()
            await pilot.pause()
            from textual.widgets import TextArea
            ta = scr.query_one("#exp-body", TextArea)
            ta.text = "still editing"
            await pilot.pause()
            scr._mark_dirty(True)
            scr.action_cancel()
            await pilot.pause()
            await pilot.pause()
            assert isinstance(
                app.screen, sc.ExperimentUnsavedChangesModal,
            )
            app.screen.dismiss("cancel")
            await pilot.pause()
            await pilot.pause()
            # ExperimentsScreen is back on top, NOT dismissed.
            assert isinstance(app.screen, sc.ExperimentsScreen)
            ta2 = app.screen.query_one("#exp-body", TextArea)
            assert ta2.text == "still editing"


# ═══════════════════════════════════════════════════════════════════════════════
# Edge-case sweep — long project name in top-row label
# ═══════════════════════════════════════════════════════════════════════════════

class TestProjectLabelTruncation:
    """Edge sweep 2026-05-18 — a hand-edited `active_project` in
    settings.json could be arbitrarily long. The header label must
    truncate so it doesn't push the Projects button off-screen on a
    narrow terminal."""

    async def test_long_project_name_truncates(self):
        sc._save_experiment_projects([{
            "name": "X" * 500, "description": "",
            "experiments": [], "saved": "",
        }])
        sc._set_active_project_name("X" * 500)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.ExperimentsScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            label = scr._format_project_label()
            # `accent` markup adds ~30 chars; the visible name must
            # be capped at 60 chars with an ellipsis indicator.
            assert "…" in label
            # 60-char name + markup overhead < 110 cols (modal min width)
            # so the Projects button stays on-screen.
            assert len(label) < 120


# ═══════════════════════════════════════════════════════════════════════════════
# Markdown link click handler
# ═══════════════════════════════════════════════════════════════════════════════

class TestActionsTag:
    """Single-sigil `!<id>` action token + catalog + picker modal."""

    def test_extract_action_refs_basic(self):
        refs = sc._extract_action_refs(
            "Today: !digest then !ligate"
        )
        assert refs == ["digest", "ligate"]

    def test_extract_action_refs_dedup_preserves_order(self):
        refs = sc._extract_action_refs(
            "!pcr first, !gibson, then !pcr again"
        )
        assert refs == ["pcr", "gibson"]

    def test_extract_action_refs_accepts_free_form(self):
        # Catalog is curated but not enforced.
        refs = sc._extract_action_refs("!my-custom-step")
        assert refs == ["my-custom-step"]

    def test_extract_action_refs_rejects_invalid_chars(self):
        # Space terminates the match; the part with space + after isn't
        # captured.
        refs = sc._extract_action_refs("!foo bar")
        assert refs == ["foo"]

    def test_action_regex_rejects_markdown_image(self):
        # Markdown image syntax `![alt](url)` must not match — the
        # regex requires the next char after `!` to be a letter,
        # while images have `[`.
        assert sc._extract_action_refs("![alt](image.png)") == []

    def test_action_regex_rejects_word_prefix(self):
        # `foo!bar` shouldn't tag `bar` as an action — the negative
        # lookbehind `(?<![\w!])` blocks word-adjacent matches.
        assert sc._extract_action_refs("foo!bar") == []
        assert sc._extract_action_refs("!!double") == []

    def test_normalise_extracts_action_xref(self):
        e = sc._normalise_experiment_entry({
            "id": "exp-aaaaaaaa", "title": "T",
            "body_md": "@pUC19 then !digest and !transform",
        }, fresh=True)
        assert e["attached_plasmid_ids"] == ["pUC19"]
        assert e["attached_actions"] == ["digest", "transform"]

    def test_catalog_has_expected_categories(self):
        cats = {cat for cat, _action, _desc in sc._EXPERIMENT_ACTIONS}
        # Sanity-check the catalog covers the main workflow buckets
        # so the picker shows something useful in each.
        assert {"Design", "PCR", "Assembly", "Validation"} <= cats

    def test_chip_color_constants_set(self):
        assert sc._PLASMID_CHIP_COLOR == "#9AFF80"
        assert sc._ACTIONS_CHIP_COLOR == "#C77FFF"

    async def test_in_editor_highlights_get_injected(self):
        """`_ExperimentMarkdownTextArea._build_highlight_map` must
        inject regex-based highlight tuples for `@<id>` (plasmid)
        and `!<id>` (action) tokens on top of the markdown tree-
        sitter highlights. The Rich style mappings for our two
        highlight names must land in the active theme's
        `syntax_styles`."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
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
            ta.text = "Hello @pUC19 and !digest done."
            await pilot.pause()
            await pilot.pause()
            # Theme has our two style mappings.
            styles = ta._theme.syntax_styles
            assert sc._ExperimentMarkdownTextArea._PLASMID_HL_NAME in styles
            assert sc._ExperimentMarkdownTextArea._ACTIONS_HL_NAME in styles
            # The single-line body has one plasmid + one action
            # highlight tuple attached to line 0 (plus any markdown
            # tree-sitter highlights that came from super()).
            line0 = ta._highlights[0]
            names = [name for _s, _e, name in line0]
            assert (
                sc._ExperimentMarkdownTextArea._PLASMID_HL_NAME
                in names
            )
            assert (
                sc._ExperimentMarkdownTextArea._ACTIONS_HL_NAME
                in names
            )

    async def test_backspace_at_tag_end_deletes_whole_tag(self):
        """Backspace at the end of a `@<id>` or `!<id>` token must
        delete the entire tag, not just the last character. Inside
        a tag or in normal prose, default behaviour."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
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
            ta.text = "hello @pUC19"
            # Cursor at end (after the last `9`).
            ta.cursor_location = (0, len(ta.text))
            await pilot.pause()
            ta.action_delete_left()
            await pilot.pause()
            # Whole tag gone — only the prose remains.
            assert ta.text == "hello "

    async def test_backspace_action_tag_end(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
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
            ta.text = "Today: !digest"
            ta.cursor_location = (0, len(ta.text))
            await pilot.pause()
            ta.action_delete_left()
            await pilot.pause()
            assert ta.text == "Today: "

    async def test_backspace_in_prose_is_normal(self):
        """Sanity: backspace anywhere outside a tag still deletes
        one character (no false positives from the tail regex)."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
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
            ta.text = "hello world"
            ta.cursor_location = (0, len(ta.text))
            await pilot.pause()
            ta.action_delete_left()
            await pilot.pause()
            assert ta.text == "hello worl"


class TestPlasmidLinkScheme:
    def test_render_link_uses_scheme(self):
        out = sc._render_plasmid_refs("@pTest")
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


class TestExperimentMoveToProjectCommit:
    """GUI side of `move-experiment` —
    ExperimentsScreen._commit_move_entry_to_project relocates an entry from
    the active project to another, atomically, and re-mirrors the live
    experiments.json so the moved entry leaves the notebook view."""

    def _stub(self):
        import types
        return types.SimpleNamespace(
            app=types.SimpleNamespace(notify=lambda *a, **k: None))

    def _seed(self):
        sc._save_experiment_projects([
            {"name": "ProjA", "experiments": [
                {"id": "exp-1", "title": "one"},
                {"id": "exp-2", "title": "two"}], "saved": ""},
            {"name": "ProjB", "experiments": [], "saved": ""},
        ])
        sc._set_active_project_name("ProjA")
        sc._save_experiments([{"id": "exp-1", "title": "one"},
                              {"id": "exp-2", "title": "two"}])

    def _proj(self, name):
        p = next(x for x in sc._load_experiment_projects()
                 if x["name"] == name)
        return [e["id"] for e in p["experiments"]]

    def test_move_relocates_and_remirrors(self):
        self._seed()
        ok = sc.ExperimentsScreen._commit_move_entry_to_project(
            self._stub(), "exp-1", "ProjB")
        assert ok is True
        assert self._proj("ProjA") == ["exp-2"]
        assert self._proj("ProjB") == ["exp-1"]
        # live experiments.json re-synced to the active (source) project
        assert [e["id"] for e in sc._load_experiments()] == ["exp-2"]

    def test_move_guards(self):
        self._seed()
        # entry not in the active project → no change
        assert sc.ExperimentsScreen._commit_move_entry_to_project(
            self._stub(), "exp-ghost", "ProjB") is False
        assert self._proj("ProjA") == ["exp-1", "exp-2"]
        # unknown destination project → no change
        assert sc.ExperimentsScreen._commit_move_entry_to_project(
            self._stub(), "exp-2", "Nope") is False
        assert self._proj("ProjA") == ["exp-1", "exp-2"]
