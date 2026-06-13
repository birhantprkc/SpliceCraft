"""Button hover-tooltip infrastructure (2026-06-13):

  * the global Settings "Show button tooltips" toggle hides/restores ALL
    button tooltips (stash/restore via `_tip_base`), persisted + live-applied;
  * the `_BUTTON_TOOLTIPS` registry + inline `tooltip=` are the two coverage
    sources;
  * a staleguard that every `Button(...)` is tooltip-covered (currently xfail
    while coverage rolls out — flip to enforced when complete).
"""
from __future__ import annotations

import ast
import pathlib

import pytest

import splicecraft as sc

_SRC = pathlib.Path(sc.__file__).read_text()


def _buttons_without_tooltips() -> "list[str]":
    """AST-scan splicecraft.py for ``Button(...)`` calls; return descriptors for
    those WITHOUT coverage — no inline ``tooltip=`` AND ``id`` not in
    ``_BUTTON_TOOLTIPS``. A button with neither an id nor an inline tooltip is
    reported by source line. This is both the staleguard and the worklist."""
    tree = ast.parse(_SRC)
    registry = set(sc._BUTTON_TOOLTIPS)
    missing: "list[str]" = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = getattr(f, "id", None) or getattr(f, "attr", None)
        if name != "Button":
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        if "tooltip" in kw:
            continue                       # inline tooltip → covered
        idv = kw.get("id")
        if isinstance(idv, ast.Constant) and isinstance(idv.value, str):
            if idv.value not in registry:
                missing.append(idv.value)
        else:
            missing.append(f"<no-id @line {node.lineno}>")
    return missing


class TestButtonTooltipToggle:
    """The Settings 'Show button tooltips' toggle + the applier."""

    async def test_toggle_hides_then_restores_tooltips(self):
        from textual.widgets import Button
        app = sc.PlasmidApp()
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            await pilot.pause()            # let the on_mount applier run
            tipped = [b for b in app.screen.query(Button) if b.tooltip]
            assert tipped, "no main-screen button has a tooltip to test"
            b = tipped[0]
            original = b.tooltip
            # OFF → every button tooltip cleared.
            app._show_button_tooltips = False
            app._apply_button_tooltip_visibility()
            await pilot.pause()
            assert all(bb.tooltip is None for bb in app.screen.query(Button)), \
                "some button tooltip survived the OFF toggle"
            # ON → restored to the captured base (no loss).
            app._show_button_tooltips = True
            app._apply_button_tooltip_visibility()
            await pilot.pause()
            assert b.tooltip == original, "tooltip not restored on ON toggle"

    async def test_applier_is_safe_on_half_mounted_and_empty(self):
        """Best-effort: applying with no current screen / a scope with no
        buttons must not raise."""
        app = sc.PlasmidApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # an arbitrary widget scope with no Buttons under it
            from textual.widgets import Static
            try:
                empty = app.screen.query(Static).first()
            except Exception:
                empty = None
            if empty is not None:
                app._apply_button_tooltip_visibility(empty)   # must not raise
            await pilot.pause()

    def test_setting_round_trips(self):
        # Sandboxed by the autouse `_protect_user_data` fixture.
        sc._set_setting("show_button_tooltips", False)
        assert sc._get_setting("show_button_tooltips", True) is False
        sc._set_setting("show_button_tooltips", True)
        assert sc._get_setting("show_button_tooltips", True) is True


class TestButtonTooltipCoverage:
    """Hard staleguard: EVERY `Button(...)` must carry a tooltip — an inline
    ``tooltip=`` (dynamic-id buttons) or a `_BUTTON_TOOLTIPS` entry (static id).
    A new button shipped tooltip-less fails this test, so coverage never rots."""

    def test_every_button_has_a_tooltip(self):
        missing = _buttons_without_tooltips()
        assert not missing, (
            f"{len(missing)} button(s) lack a tooltip — add an inline tooltip= "
            f"(dynamic id) or a _BUTTON_TOOLTIPS entry (static id). "
            f"Offenders: {sorted(set(missing))}"
        )
