"""Master Delete — wipe all user data behind a typed-YES gate.

Sacred for THIS feature:
  * No agent endpoint exposes the wipe path.
  * The sentinel-arg refuse-without-it contract holds.
  * "YES" is the exact required input (case-sensitive, no whitespace).
  * Confirm button on the second modal is cooldown-gated.
  * Re-entrancy guard prevents stacking two modal flows.
  * Lockfile + active log survive the wipe.
  * Residual sweep catches files / dirs that aren't in the named-target
    list (defense in depth for any future persistence the contributor
    forgot to register in `_USER_DATA_FILE_ATTRS`).

Naming convention: `tests/test_master_delete.py` — sister test files
already use this 1:1 subsystem naming.
"""
import pytest
from textual.widgets import Button, Input

import splicecraft as sc

pytestmark = [pytest.mark.usefixtures("_protect_user_data")]


# ── Sentinel-arg contract ──────────────────────────────────────────────────

def test_sentinel_mismatch_refused_before_any_disk_op():
    """Calling `_perform_master_delete` without the module-local sentinel
    must raise RuntimeError BEFORE any file is touched. Two independent
    blockers protect the wipe path — sentinel + no agent endpoint — and
    this is one of them."""
    # Plant a file that would be wiped if the sentinel check were
    # bypassed; assert it's still there after the refusal.
    sc._LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    sc._LIBRARY_FILE.write_text('{"_schema_version": 1, "entries": []}')
    assert sc._LIBRARY_FILE.exists()

    with pytest.raises(RuntimeError, match="sentinel mismatch"):
        sc._perform_master_delete(None, sentinel=object())
    # The wrong-sentinel call must NOT have touched the planted file.
    assert sc._LIBRARY_FILE.exists()

    # Other sentinel-shaped values are also refused — None, "yes",
    # the string "_MASTER_DELETE_SENTINEL", a random uuid, etc.
    for bad in (None, "yes", "_MASTER_DELETE_SENTINEL", 42, ""):
        with pytest.raises(RuntimeError, match="sentinel mismatch"):
            sc._perform_master_delete(None, sentinel=bad)
    assert sc._LIBRARY_FILE.exists()


def test_sentinel_passing_actually_wipes():
    """Sanity check: with the correct sentinel, the file IS removed.
    Proves the refusal in the test above is exercising the sentinel
    branch and not some unrelated guard."""
    sc._LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    sc._LIBRARY_FILE.write_text('{"_schema_version": 1, "entries": []}')
    assert sc._LIBRARY_FILE.exists()

    summary = sc._perform_master_delete(
        None, sentinel=sc._MASTER_DELETE_SENTINEL,
    )
    assert not sc._LIBRARY_FILE.exists()
    assert summary["files_removed"] >= 1
    assert summary["errors"] == 0


# ── No agent endpoint contract ─────────────────────────────────────────────

def test_no_agent_endpoint_exposes_wipe():
    """Master Delete must NOT be reachable through the agent API. The
    `_AGENT_HANDLERS` dispatch dict is the canonical exposure surface;
    any handler whose name contains 'master', 'wipe', 'nuke',
    'delete_all', or 'clean_slate' would defeat the GUI gate."""
    forbidden_substrings = (
        "master", "wipe", "nuke", "delete_all", "clean_slate",
        "purge", "destroy",
    )
    for endpoint in sc._AGENT_HANDLERS:
        low = endpoint.lower()
        for forbidden in forbidden_substrings:
            assert forbidden not in low, (
                f"Agent endpoint {endpoint!r} contains forbidden "
                f"substring {forbidden!r} — Master Delete must remain "
                "GUI-only. If the endpoint is unrelated, rename it; if "
                "the endpoint IS Master Delete, remove it (see "
                "splicecraft.py `_perform_master_delete` docstring)."
            )


# ── Comprehensive wipe coverage ────────────────────────────────────────────

def _plant_everything():
    """Populate every persisted user-data file + every user-data dir
    with a sentinel byte, including the sibling pre-update-backups
    directory and the ad-hoc subdirs. Returns the list of planted
    paths so the calling test can assert they're all gone after the
    wipe."""
    paths: list = []
    # User-data JSON files + a .bak sibling for each.
    for attr in sc._USER_DATA_FILE_ATTRS:
        p = getattr(sc, attr)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("PLANTED")
        bak = p.with_suffix(p.suffix + ".bak")
        bak.write_text("PLANTED_BAK")
        ts_bak = p.with_name(p.name + ".bak.20260520-120000")
        ts_bak.write_text("PLANTED_TS_BAK")
        paths.extend([p, bak, ts_bak])
    # Operational files.
    for attr in sc._OPERATIONAL_FILE_ATTRS:
        p = getattr(sc, attr)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("PLANTED")
        paths.append(p)
    # User-data subdirs — populate each with at least one file so
    # rmtree has work to do (an empty dir would also be removed but
    # the test is stronger with content).
    for attr in sc._USER_DATA_DIR_ATTRS:
        d = getattr(sc, attr)
        d.mkdir(parents=True, exist_ok=True)
        (d / "marker.txt").write_text("PLANTED")
        paths.append(d / "marker.txt")
    # Ad-hoc subdirs created inline elsewhere in the codebase.
    for name in ("snapshots", "lost_entries", "clipboard"):
        d = sc._state._DATA_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "marker.txt").write_text("PLANTED")
        paths.append(d / "marker.txt")
    # UI snapshots (has a constant but resolves to the same path).
    sc._UI_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    (sc._UI_SNAPSHOTS_DIR / "ui-snapshot.md").write_text("PLANTED")
    paths.append(sc._UI_SNAPSHOTS_DIR / "ui-snapshot.md")
    # Pre-update backups (sibling of DATA_DIR via the
    # SPLICECRAFT_UPDATE_BACKUP_DIR env-var override the conftest sets).
    pre = sc._resolve_pre_update_backup_dir()
    pre.mkdir(parents=True, exist_ok=True)
    (pre / "marker.txt").write_text("PLANTED")
    paths.append(pre / "marker.txt")
    # Legacy migration marker.
    (sc._state._DATA_DIR / ".migrated").write_text("PLANTED")
    paths.append(sc._state._DATA_DIR / ".migrated")
    return paths


def test_wipe_removes_every_planted_path():
    """End-to-end: plant a sentinel byte in every persisted location,
    fire the wipe, assert every planted path is gone."""
    planted = _plant_everything()
    for p in planted:
        assert p.exists(), f"Pre-condition failed: {p} not planted"

    summary = sc._perform_master_delete(
        None, sentinel=sc._MASTER_DELETE_SENTINEL,
    )

    for p in planted:
        assert not p.exists(), (
            f"{p} survived Master Delete — coverage gap"
        )
    # Sanity checks on the summary itself.
    assert summary["files_removed"] >= len(sc._USER_DATA_FILE_ATTRS)
    assert summary["dirs_removed"] >= 1
    assert summary["pre_update_removed"] is True
    assert summary["errors"] == 0


def test_wipe_resets_every_cache_to_none():
    """After the wipe, every module-level cache must be None so the
    next read sees the empty-disk state instead of stale in-memory
    contents."""
    # Force every cache to a non-None value first (the loaders
    # populate caches as a side effect of being called).
    sc._state._library_cache = [{"id": "junk"}]
    sc._state._collections_cache = [{"name": "junk"}]
    sc._state._settings_cache = {"junk": True}
    sc._state._experiments_cache = [{"id": "junk"}]
    sc._state._gels_cache = [{"id": "junk"}]
    # Plant a small set of files so the wipe has at least one
    # dimension to act on (otherwise it'd no-op and the cache reset
    # test wouldn't prove the actual wipe path is taken).
    sc._LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    sc._LIBRARY_FILE.write_text("{}")

    sc._perform_master_delete(
        None, sentinel=sc._MASTER_DELETE_SENTINEL,
    )

    for cache_attr in sc._MASTER_DELETE_CACHE_ATTRS:
        owner = sc._state if hasattr(sc._state, cache_attr) else sc
        assert getattr(owner, cache_attr) is None, (
            f"{cache_attr} was not reset after Master Delete"
        )


def test_residual_sweep_catches_unregistered_file():
    """Defense-in-depth: a file landing under `_DATA_DIR` that isn't
    in `_USER_DATA_FILE_ATTRS` / `_OPERATIONAL_FILE_ATTRS` must still
    be wiped via the residual-sweep pass. Without this, a future
    contributor who adds a new persisted file and forgets to
    register it could quietly leak data past Master Delete."""
    sc._state._DATA_DIR.mkdir(parents=True, exist_ok=True)
    stray_file = sc._state._DATA_DIR / "rogue_future_file.json"
    stray_file.write_text('{"contributor": "forgot to register me"}')
    stray_dir = sc._state._DATA_DIR / "rogue_future_dir"
    stray_dir.mkdir()
    (stray_dir / "inner.txt").write_text("inside")

    summary = sc._perform_master_delete(
        None, sentinel=sc._MASTER_DELETE_SENTINEL,
    )

    assert not stray_file.exists()
    assert not stray_dir.exists()
    assert summary["residual_files"] >= 1
    assert summary["residual_dirs"] >= 1


def test_residual_sweep_preserves_lockfile_and_log_dir():
    """The active lockfile (held by this process) and the logs/
    directory (containing the active RotatingFileHandler target)
    must survive the wipe. Deleting them mid-process is unsafe on
    Windows and produces orphan-inode writes on POSIX."""
    sc._state._DATA_DIR.mkdir(parents=True, exist_ok=True)
    lockfile = sc._state._DATA_DIR / "splicecraft.lock"
    lockfile.write_text(str(12345))  # PID-style content
    log_dir = sc._state._DATA_DIR / "logs"
    log_dir.mkdir()
    active_log = log_dir / "splicecraft.log"
    active_log.write_text("session bytes")

    sc._perform_master_delete(
        None, sentinel=sc._MASTER_DELETE_SENTINEL,
    )

    assert lockfile.exists(), "lockfile must not be wiped"
    assert log_dir.exists(), "logs/ dir must survive"
    # Whether the active log file itself was preserved depends on
    # whether `_LOG_PATH` resolves to this exact path. In tests
    # `_LOG_PATH` is set at import time to the REAL data dir, NOT
    # tmp_path, so the residual sweep WILL delete the file inside
    # the test's tmp logs dir. Don't assert on that — the dir
    # itself surviving is what matters.


def test_wipe_idempotent_on_empty_dir():
    """Running the wipe twice in a row is safe — the second pass
    finds nothing to remove and returns summary counts of zero
    without raising."""
    sc._perform_master_delete(
        None, sentinel=sc._MASTER_DELETE_SENTINEL,
    )
    # Second pass should not raise + should report zero work done.
    summary = sc._perform_master_delete(
        None, sentinel=sc._MASTER_DELETE_SENTINEL,
    )
    assert summary["files_removed"] == 0
    assert summary["dirs_removed"] == 0
    assert summary["pre_update_removed"] is False
    assert summary["residual_files"] == 0
    assert summary["residual_dirs"] == 0
    assert summary["errors"] == 0


# ── MasterDeleteModal — typed-YES gate ─────────────────────────────────────

@pytest.mark.asyncio
async def test_master_delete_modal_starts_with_delete_disabled():
    app = sc.PlasmidApp()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        modal = sc.MasterDeleteModal(
            files_count=0, dirs_count=0, pre_update_present=False,
        )
        app.push_screen(modal)
        await pilot.pause()
        await pilot.pause()
        delete_btn = modal.query_one("#btn-md-delete", Button)
        assert delete_btn.disabled is True


@pytest.mark.parametrize("bad_input", [
    "yes",          # lowercase
    "Yes",          # title case
    "YEs",          # mixed case
    " YES",         # leading space
    "YES ",         # trailing space
    "YESS",         # extra char
    "YE",           # too short
    "",             # empty
    "YES\n",        # trailing newline
    "Y E S",        # spaces inside
    "Y\tES",        # tab inside
])
@pytest.mark.asyncio
async def test_master_delete_modal_rejects_non_yes_input(bad_input):
    """The typed-YES gate must reject every near-miss. Sacred — if
    this loosens, the whole hardening story collapses."""
    app = sc.PlasmidApp()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        modal = sc.MasterDeleteModal(
            files_count=5, dirs_count=2, pre_update_present=True,
        )
        app.push_screen(modal)
        await pilot.pause()
        await pilot.pause()
        inp = modal.query_one("#md-input", Input)
        inp.value = bad_input
        await pilot.pause()
        delete_btn = modal.query_one("#btn-md-delete", Button)
        assert delete_btn.disabled is True, (
            f"Delete button enabled for bad input {bad_input!r}"
        )


@pytest.mark.asyncio
async def test_master_delete_modal_enables_on_exact_yes():
    """Exact "YES" (case-sensitive, three chars, no whitespace)
    is the only string that enables the Delete button."""
    app = sc.PlasmidApp()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        modal = sc.MasterDeleteModal(
            files_count=5, dirs_count=2, pre_update_present=True,
        )
        app.push_screen(modal)
        await pilot.pause()
        await pilot.pause()
        inp = modal.query_one("#md-input", Input)
        inp.value = "YES"
        await pilot.pause()
        delete_btn = modal.query_one("#btn-md-delete", Button)
        assert delete_btn.disabled is False


@pytest.mark.asyncio
async def test_master_delete_modal_cancel_default_focused():
    """Default focus must be on the Cancel button so a stray Enter
    at modal level cancels rather than commits."""
    app = sc.PlasmidApp()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        modal = sc.MasterDeleteModal(
            files_count=0, dirs_count=0, pre_update_present=False,
        )
        app.push_screen(modal)
        await pilot.pause()
        await pilot.pause()
        cancel = modal.query_one("#btn-md-cancel", Button)
        assert cancel.has_focus, (
            "Cancel button must be default-focused in Stage 1"
        )


# ── MasterDeleteConfirmModal — cooldown + default-No ───────────────────────

@pytest.mark.asyncio
async def test_confirm_modal_default_focus_on_no():
    """Default focus on No, Esc → No — sacred for every destructive
    confirm modal in the app."""
    app = sc.PlasmidApp()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        modal = sc.MasterDeleteConfirmModal(
            files_count=10, dirs_count=4, pre_update_present=True,
            cooldown_s=0.5,
        )
        app.push_screen(modal)
        await pilot.pause()
        await pilot.pause()
        no_btn = modal.query_one("#btn-mdc-no", Button)
        assert no_btn.has_focus, (
            "No button must be default-focused in Stage 2"
        )


@pytest.mark.asyncio
async def test_confirm_modal_yes_button_starts_disabled():
    """The Yes button must start disabled and only enable after the
    cooldown timer expires. The countdown label is the user-visible
    proof of the cooldown."""
    app = sc.PlasmidApp()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        modal = sc.MasterDeleteConfirmModal(
            files_count=1, dirs_count=1, pre_update_present=False,
            cooldown_s=2.0,
        )
        app.push_screen(modal)
        await pilot.pause()
        await pilot.pause()
        yes_btn = modal.query_one("#btn-mdc-yes", Button)
        assert yes_btn.disabled is True
        assert "s)" in str(yes_btn.label), (
            "Yes button label should show countdown seconds"
        )


@pytest.mark.asyncio
async def test_confirm_modal_yes_button_enables_after_cooldown():
    """After the cooldown elapses, the Yes button must enable. Using
    a 0.3-second cooldown to keep the test fast."""
    app = sc.PlasmidApp()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        modal = sc.MasterDeleteConfirmModal(
            files_count=1, dirs_count=1, pre_update_present=False,
            cooldown_s=0.3,
        )
        app.push_screen(modal)
        await pilot.pause()
        # Wait out the cooldown plus a generous slack.
        await pilot.pause(delay=0.6)
        yes_btn = modal.query_one("#btn-mdc-yes", Button)
        assert yes_btn.disabled is False
        assert "(" not in str(yes_btn.label), (
            "Yes button label should drop the countdown once enabled"
        )


@pytest.mark.asyncio
async def test_confirm_modal_zero_cooldown_starts_enabled():
    """A 0-second cooldown (used in tests that don't care to wait)
    should start with the button immediately enabled."""
    app = sc.PlasmidApp()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        modal = sc.MasterDeleteConfirmModal(
            files_count=1, dirs_count=1, pre_update_present=False,
            cooldown_s=0.0,
        )
        app.push_screen(modal)
        await pilot.pause()
        await pilot.pause()
        yes_btn = modal.query_one("#btn-mdc-yes", Button)
        assert yes_btn.disabled is False


# ── Re-entrancy guard ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_action_master_delete_reentrancy_guard():
    """Calling `action_master_delete` while the flow is already
    running must NOT push a second modal. The guard flag is
    flipped True at entry; until a callback clears it, repeated
    invocations are no-ops with a warning notification."""
    app = sc.PlasmidApp()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        app.action_master_delete()
        await pilot.pause()
        await pilot.pause()
        stack_depth_after_first = len(app.screen_stack)
        # Second invocation — should be rejected by the guard,
        # screen stack must not grow.
        app.action_master_delete()
        await pilot.pause()
        await pilot.pause()
        assert len(app.screen_stack) == stack_depth_after_first, (
            "Second action_master_delete pushed a second modal — "
            "re-entrancy guard failed"
        )


# ── Menu wiring ─────────────────────────────────────────────────────────────

def test_master_delete_in_file_menu():
    """Master Delete must appear in the File menu so users can find
    it through the normal UI path. Reading the raw menu list keeps
    the test independent of dropdown rendering quirks."""
    import re

    # Find the File-menu list block in splicecraft.py — the only
    # source of truth for the menu wiring. Greppable, robust to
    # surrounding edits because the lookup is anchored on the
    # `"File": [` line and `master_delete` action name.
    with open(sc.__file__, encoding="utf-8") as f:
        src = f.read()
    # Cheaper than parsing the entire module — just confirm the
    # action name appears within the File-menu block. The block
    # is the contiguous run between `"File": [` and the matching `]`.
    file_idx = src.find('"File": [')
    assert file_idx != -1, "Could not find File menu block in source"
    # Match the first `]` at the line-start indent level — same indent
    # as `"File": [` (12 spaces inside the menus dict in
    # `open_menu`).
    end_match = re.search(r"\n {12}\],\n", src[file_idx:])
    assert end_match is not None
    block = src[file_idx:file_idx + end_match.start()]
    assert "master_delete" in block, (
        "Master Delete action must appear in the File menu"
    )


def test_no_keyboard_binding_for_master_delete():
    """Master Delete is intentionally menu-only — no keyboard
    binding anywhere in the codebase should point at
    `action_master_delete`. A binding would defeat the whole
    "you have to deliberately reach for it" safety story."""
    import re
    with open(sc.__file__, encoding="utf-8") as f:
        src = f.read()
    # Look for the canonical Textual `Binding(...)` shape pointing
    # at the master-delete action. This string can't appear anywhere
    # except in a key-binding declaration.
    bindings = re.findall(
        r"Binding\([^)]*master_delete[^)]*\)", src,
    )
    assert not bindings, (
        f"Found keyboard bindings for master_delete: {bindings}. "
        "Master Delete must be menu-only."
    )


# ── Public sentinel surface ────────────────────────────────────────────────

def test_sentinel_is_module_local_object():
    """The sentinel is a bare `object()` — not a string, int, or
    anything else an attacker could synthesise without having a
    reference. Module-local means tests in this file can import it
    via `sc._MASTER_DELETE_SENTINEL`, but agents over HTTP cannot
    forge it."""
    s = sc._MASTER_DELETE_SENTINEL
    assert type(s) is object
    assert s is sc._MASTER_DELETE_SENTINEL  # identity stable across reads


# ── End-to-end pilot: full UI flow ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_pilot_flow_wipes_planted_library(monkeypatch):
    """End-to-end: spin up `PlasmidApp`, plant a library entry,
    invoke `action_master_delete`, drive the modal flow with pilot,
    assert the library file is gone afterwards.

    Speeds up the cooldown timer (1 s instead of 3 s) so the test
    doesn't add 3 s to suite runtime. The cooldown LOGIC is covered
    by `test_confirm_modal_yes_button_*` above; this test is about
    the end-to-end integration, not the timer."""
    # Speed up the cooldown for the test.
    monkeypatch.setattr(sc, "_MASTER_DELETE_CONFIRM_COOLDOWN_S", 0.3)

    # Plant a library entry on disk. Has every field the LibraryPanel
    # _refresh_table path reads (`size`, `circular`, etc.) so the app
    # boots cleanly with the entry visible.
    sc._LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    sc._LIBRARY_FILE.write_text(
        '{"_schema_version": 1, "entries": ['
        '{"id": "PLANT", "name": "planted", "size": 100, '
        '"circular": true, "gb_text": "DUMMY"}]}'
    )
    assert sc._LIBRARY_FILE.exists()

    app = sc.PlasmidApp()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        await pilot.pause()

        # Open the Master Delete flow programmatically (matches the
        # menu callback `_menu_action("master_delete")`).
        app.action_master_delete()
        await pilot.pause()
        await pilot.pause()

        # Stage 1: type YES + click Delete.
        modal1 = app.screen
        assert isinstance(modal1, sc.MasterDeleteModal)
        inp = modal1.query_one("#md-input", Input)
        inp.value = "YES"
        await pilot.pause()
        delete_btn = modal1.query_one("#btn-md-delete", Button)
        assert delete_btn.disabled is False
        await pilot.click("#btn-md-delete")
        await pilot.pause()
        await pilot.pause()

        # Stage 2: wait out the cooldown, then click Yes.
        modal2 = app.screen
        assert isinstance(modal2, sc.MasterDeleteConfirmModal)
        await pilot.pause(delay=0.6)
        yes_btn = modal2.query_one("#btn-mdc-yes", Button)
        assert yes_btn.disabled is False
        await pilot.click("#btn-mdc-yes")
        await pilot.pause()
        await pilot.pause()

        # Stage 3: result modal shows up; library file is gone.
        assert isinstance(app.screen, sc.MasterDeleteResultModal)
        assert not sc._LIBRARY_FILE.exists()
        await pilot.click("#btn-mdr-ok")
        await pilot.pause()

        # Re-entrancy flag must be clear so a follow-up call works.
        assert app._master_delete_in_progress is False


# ── Target enumeration: must include every documented data path ───────────

def test_file_target_enumeration_covers_every_user_data_attr():
    """Sanity-check the target enumeration: every name in
    `_USER_DATA_FILE_ATTRS` + `_OPERATIONAL_FILE_ATTRS` must show
    up in the file-target list. Without this, a new persisted file
    could be added to the canonical tuple but silently dropped by
    the wipe path."""
    targets = sc._master_delete_file_targets()
    target_set = {str(p) for p in targets}
    for attr in sc._USER_DATA_FILE_ATTRS + sc._OPERATIONAL_FILE_ATTRS:
        expected = getattr(sc, attr)
        assert str(expected) in target_set, (
            f"{attr} ({expected}) not enumerated by "
            "`_master_delete_file_targets`"
        )


def test_dir_target_enumeration_covers_every_user_data_attr():
    """Same coverage check for the directory targets."""
    targets = sc._master_delete_dir_targets()
    target_set = {str(p) for p in targets}
    for attr in sc._USER_DATA_DIR_ATTRS:
        expected = getattr(sc, attr)
        assert str(expected) in target_set, (
            f"{attr} ({expected}) not enumerated by "
            "`_master_delete_dir_targets`"
        )


def test_dir_target_enumeration_covers_adhoc_subdirs():
    """The ad-hoc subdirs created inline (snapshots / lost_entries /
    clipboard / ui_snapshots) must also be in the wipe-target list."""
    targets = sc._master_delete_dir_targets()
    target_set = {str(p) for p in targets}
    for name in ("snapshots", "lost_entries", "clipboard"):
        expected = sc._state._DATA_DIR / name
        assert str(expected) in target_set, (
            f"{name}/ subdir not enumerated"
        )
    assert str(sc._UI_SNAPSHOTS_DIR) in target_set
