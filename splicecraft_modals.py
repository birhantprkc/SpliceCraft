"""splicecraft_modals — extracted ModalScreen / Screen dialog classes (Phase D).

The hub's modal/screen dialog classes, relocated here so context-limited models
(and humans) can load the UI dialog layer as a bounded unit instead of scrolling
the 100k-line hub. The hub re-exports every class
(`from splicecraft_modals import X as X`) so `sc.<Class>` and every
`push_screen(...)` / `isinstance(...)` site resolves unchanged.

Layer L4 (see tests/test_import_layers.py::_LAYER_RULES): imports textual + the
lower siblings (logging L0 / widgets L3 / dataaccess L1 / render L1) ONLY. Modals
reach the running app through Textual's `self.app` (a runtime attribute) and pop
results via `self.dismiss(...)`; any `PlasmidApp` parameter/return type hints are
quoted strings that are never evaluated (`from __future__ import annotations`), so
this module does NOT import the hub — that would be an import cycle.

Started with the dependency-closure-clean leaf dialogs (confirm / prompt / picker
modals that reference only textual + `self.app` + each other). Modals that still
call bare hub helpers stay hub-side until those helpers move to siblings.
"""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


# ── Unsaved-changes quit dialog ────────────────────────────────────────────────

class UnsavedQuitModal(ModalScreen):
    """Shown when the user tries to quit with unsaved edits."""

    _blocks_undo: bool = True   # [INV-50] destructive confirm — Ctrl+Z above

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab",    "app.focus_next", "Next button", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._dismissed: bool = False

    def _dismiss_once(self, payload) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    def compose(self) -> ComposeResult:
        with Vertical(id="quit-dlg"):
            yield Static(" Unsaved Changes ", id="quit-title")
            yield Static(
                "  You have unsaved edits. What would you like to do?",
                id="quit-msg",
            )
            with Horizontal(id="quit-btns"):
                yield Button("Save & Quit",      id="btn-save-quit", variant="primary")
                yield Button("Abandon Changes",  id="btn-abandon",   variant="error")
                yield Button("Cancel",           id="btn-cancel-quit")

    def on_mount(self) -> None:
        # Default focus on Cancel — match the "default No / safe" pattern
        # of every other confirm modal so a hammered Enter can't quit.
        self.query_one("#btn-cancel-quit", Button).focus()

    @on(Button.Pressed, "#btn-save-quit")
    def _save_quit(self, _): self._dismiss_once("save")

    @on(Button.Pressed, "#btn-abandon")
    def _abandon(self, _):   self._dismiss_once("abandon")

    @on(Button.Pressed, "#btn-cancel-quit")
    def _cancel_btn(self, _): self._dismiss_once(None)

    def action_cancel(self): self._dismiss_once(None)


class QuitConfirmModal(ModalScreen):
    """Confirm-quit modal for the no-unsaved-changes case. The unsaved
    branch goes through `UnsavedQuitModal` (with Save / Abandon / Cancel)
    instead. Default focus on `No`.
    """

    _blocks_undo: bool = True   # [INV-50] confirm modal — Ctrl+Z above

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._dismissed: bool = False

    def _dismiss_once(self, payload: bool) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    def compose(self) -> ComposeResult:
        with Vertical(id="quitcon-dlg"):
            yield Static(" Quit SpliceCraft? ", id="quitcon-title")
            yield Static(
                "  Are you sure you want to quit?",
                id="quitcon-msg",
            )
            with Horizontal(id="quitcon-btns"):
                yield Button("No",  id="btn-quitcon-no",  variant="default")
                yield Button("Yes", id="btn-quitcon-yes", variant="error")

    def on_mount(self) -> None:
        self.query_one("#btn-quitcon-no", Button).focus()

    @on(Button.Pressed, "#btn-quitcon-no")
    def _no(self, _): self._dismiss_once(False)

    @on(Button.Pressed, "#btn-quitcon-yes")
    def _yes(self, _): self._dismiss_once(True)

    def action_cancel(self): self._dismiss_once(False)
