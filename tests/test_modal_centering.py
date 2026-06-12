"""Modal dialogs must sit centered in the terminal — both axes.

Regression guard for the 2026-06-12 finding that removing per-class
`align: center middle` overrides un-centered `_OneShotDismissScreen`-based
modals (the global `ModalScreen` rule alone wasn't governing them).
"""
import pytest
import splicecraft as sc

_SIZE = (160, 48)


def _offset(app, dlg_id):
    dlg = app.screen.query_one(dlg_id)
    r = dlg.region
    sw, sh = app.size.width, app.size.height
    return (r.x - (sw - r.width) // 2, r.y - (sh - r.height) // 2, r, sw, sh)


async def _check(app, dlg_id):
    dx, dy, r, sw, sh = _offset(app, dlg_id)
    print(f"\n{dlg_id}: region={r} screen={sw}x{sh} offset=({dx},{dy})")
    return dx, dy


async def test_settings_centered():
    app = sc.PlasmidApp()
    async with app.run_test(size=_SIZE) as pilot:
        await pilot.pause(); await pilot.pause()
        app.push_screen(sc.SettingsModal())
        await pilot.pause(); await pilot.pause()
        dx, dy = await _check(app, "#set-dlg")
        assert abs(dx) <= 1 and abs(dy) <= 1, f"off-center ({dx},{dy})"


async def test_gname_centered():
    app = sc.PlasmidApp()
    async with app.run_test(size=_SIZE) as pilot:
        await pilot.pause(); await pilot.pause()
        app.push_screen(sc.GroupNamePromptModal("", title=" New ", prompt="Name:"))
        await pilot.pause(); await pilot.pause()
        dx, dy = await _check(app, "#gname-dlg")
        assert abs(dx) <= 1 and abs(dy) <= 1, f"off-center ({dx},{dy})"


async def test_opm_centered():
    app = sc.PlasmidApp()
    async with app.run_test(size=_SIZE) as pilot:
        await pilot.pause(); await pilot.pause()
        app.push_screen(sc._OperonProteinModal(title=" Paste "))
        await pilot.pause(); await pilot.pause()
        dx, dy = await _check(app, "#opm-dlg")
        assert abs(dx) <= 1 and abs(dy) <= 1, f"off-center ({dx},{dy})"
