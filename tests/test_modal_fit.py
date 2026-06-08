"""test_modal_fit — the operon-flow modals must fit a small (T480s-class)
terminal. A maximized Windows Terminal on a 1080p T480s is ~110-130 cols,
but a non-maximized / large-font window can be ~80; we target 80x24.
"""
import pytest
import splicecraft as sc

_MIN = (80, 24)
_SIZES = [(120, 30), (100, 30), _MIN]


def _within(widget, app):
    r = widget.region
    return (r.x >= 0 and r.y >= 0
            and r.right <= app.size.width and r.bottom <= app.size.height)


def _report(app, dlg_id):
    scr = app.screen
    dlg = scr.query_one(dlg_id)
    bad = [] if _within(dlg, app) else [f"dialog {dlg.region} vs {app.size}"]
    for b in scr.query("Button"):
        if not _within(b, app):
            bad.append(f"button {b.label!s} {b.region}")
    return bad


@pytest.mark.parametrize("size", _SIZES)
async def test_operon_paste_modal_fits(size):
    app = sc.PlasmidApp()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        await pilot.pause()
        app.push_screen(sc._OperonProteinModal(title=" Paste protein "))
        await pilot.pause()
        await pilot.pause()
        bad = _report(app, "#opm-dlg")
        assert not bad, f"clips at {size}: {bad}"


@pytest.mark.parametrize("size", _SIZES)
async def test_operon_name_modal_fits(size):
    app = sc.PlasmidApp()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        await pilot.pause()
        app.push_screen(sc.GroupNamePromptModal(
            "", title=" New collection ", prompt="Name:"))
        await pilot.pause()
        await pilot.pause()
        bad = _report(app, "#gname-dlg")
        assert not bad, f"clips at {size}: {bad}"
