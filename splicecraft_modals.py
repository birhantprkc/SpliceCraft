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

import re

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.await_complete import AwaitComplete
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate as _Coordinate
from textual.css.query import NoMatches
from textual.events import Click
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Input, Label, ListItem, ListView, RadioButton, RadioSet, Select, Static, TextArea

from splicecraft_dataaccess import _load_primer_collections
from splicecraft_logging import _log, _log_event
from splicecraft_widgets import _InstantPressButton


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


class _OneShotDismissScreen(Screen):
    """Mixin that makes ``dismiss()`` idempotent.

    Real terminals can deliver TWO ``Button.Pressed`` events for one
    physical click (the focus-transition + click-cycle interaction
    documented across sweeps #36 / #38 / #39). Textual's
    ``Screen.dismiss()`` (8.x) fires the result callback AND calls
    ``app.pop_screen()`` with no internal guard, so a double-fire would
    (a) re-run the caller's callback — e.g. a duplicate library save —
    and (b) pop a SECOND screen off the stack (the parent) or raise
    ``ScreenStackError``. Mixing this in BEFORE ``ModalScreen`` /
    ``Screen`` makes the first dismiss flip a one-shot flag and delegate
    to ``super().dismiss()``; every later call is an inert no-op.

    Self-contained (a class-attribute flag, no ``__init__``) so any
    modal can opt in with a one-line base change and nothing else. The
    return type matches ``Screen.dismiss`` (``AwaitComplete``) so the
    two-base MRO stays type-compatible; the no-op path returns ``None``
    at runtime (never awaited from a handler) to avoid building an
    ``AwaitComplete`` outside a running loop.
    """

    _dismissed_once: bool = False

    def dismiss(self, result=None) -> AwaitComplete:
        if self._dismissed_once:
            return None  # type: ignore[return-value]
        self._dismissed_once = True
        return super().dismiss(result)


class EditSeqDialog(ModalScreen):
    """Unified sequence-edit modal — insert (left/right), replace, or
    delete.

    Sweep #39 (2026-05-27): refactored from a single-mode dialog into
    a context-aware operations modal. User picks the mode in-modal
    via a RadioSet; the caller seeds the initial mode based on
    whether a selection or cursor is present.

    Modes (internal ``_mode`` names) — user request 2026-06-01 split
    Insert into LEFT / RIGHT and prefilled the textbox with the region.
    Ctrl+E (`action_edit_seq`) opens with a NON-destructive default —
    Replace for a selection, Insert-left for a bare cursor (never Delete,
    a hand-slip risk); the Delete KEY with no feature selected opens this
    same dialog forced to Delete mode (`_open_seq_edit_dialog`):
      * ``"insert_left"`` — insert ``new_seq`` at ``start`` (before the
        region / cursor bp). Dismisses as ``(new_seq, "insert", start,
        start)``.
      * ``"insert_right"`` — insert ``new_seq`` at ``end`` (after the
        region / cursor bp). Dismisses as ``(new_seq, "insert", end,
        end)``. A bare cursor opens as a 1-bp region ``[pos, pos+1)`` so
        left vs right is meaningful.
      * ``"replace"`` — replace ``seq[start:end]`` with ``new_seq``.
      * ``"delete"`` — drop ``seq[start:end]``. Returned as
        ``("", "replace", start, end)`` so the existing dispatch
        in ``_edit_dialog_result`` handles it as a replace-with-
        empty. ``"insert"`` is kept as a back-compat alias → insert_left.
        (Copy was dropped 2026-06-01 — five radios overflowed the dialog
        and the seq panel's Ctrl+C already copies a selection.)

    A colour-coded live WARNING line ("You are about to <action> <N> bp
    <position>") tracks the focused mode + textbox as the user types.

    Input handling:
      * ``TextArea`` instead of ``Input`` — multi-line, so paste
        of a multi-line FASTA / long fragment / spread-out paste
        from a browser works without each newline tripping
        validation.
      * Sanitization on submit (``_sanitize_pasted_sequence``):
        drop FASTA header lines (``>``), strip all whitespace,
        upper-case, U→T (RNA paste compatibility).
      * Validates the cleaned string against IUPAC alphabet.

    Returns via dismiss:
      * insert_left:  ``(new_seq, "insert", start, start)``
      * insert_right: ``(new_seq, "insert", end, end)``
      * replace:      ``(new_seq, "replace", start, end)``
      * delete:       ``("", "replace", start, end)``
      * cancel:       ``None``
    """

    _blocks_undo: bool = True   # sweep #10: app-level Ctrl+Z would race the insert

    _VALID = frozenset("ATCGNRYSWKMBDHV")   # IUPAC DNA codes
    # Insert is split into LEFT (insert before the region / cursor bp)
    # and RIGHT (insert after it) — user request 2026-06-01. The bare
    # ``"insert"`` name is kept as a back-compat alias that ``__init__``
    # maps to insert-left (old callers + boundary tests pass "insert").
    _MODE_INSERT_LEFT  = "insert_left"
    _MODE_INSERT_RIGHT = "insert_right"
    _MODE_INSERT       = "insert"          # legacy alias → insert_left
    _MODE_REPLACE = "replace"
    _MODE_DELETE  = "delete"
    # Copy was dropped 2026-06-01 — five horizontal radios overflowed the
    # dialog (user: "ui crowded and lil messy"), and copy is redundant
    # with the seq panel's Ctrl+C. The four real edit ops fit cleanly.
    _MODES        = (_MODE_INSERT_LEFT, _MODE_INSERT_RIGHT,
                     _MODE_REPLACE, _MODE_DELETE)
    _INSERT_MODES = (_MODE_INSERT_LEFT, _MODE_INSERT_RIGHT)
    _MODES_NEEDING_INPUT = (_MODE_INSERT_LEFT, _MODE_INSERT_RIGHT,
                            _MODE_REPLACE)
    _MODES_NEEDING_REGION = (_MODE_REPLACE, _MODE_DELETE)
    # Sweep #39 hardening (2026-05-27): cap pasted input at 200 kbp
    # (matches `_MAX_FEATURE_SEQ_LEN` precedent — anything longer
    # belongs as a record in the plasmid library, not as a one-shot
    # paste into the seq editor). The cap stops a paste-attack /
    # accidental megabyte-paste from freezing the TextArea + the
    # sanitiser. Counted after sanitisation so FASTA-decorated
    # pastes (headers + whitespace) get their real bp count
    # measured.
    _MAX_INPUT_BP = 200_000
    # Cap on how many bp we prefill into the textbox (2026-06-01). A
    # huge selection (e.g. the whole plasmid → Ctrl+E) would be slow to
    # render in the TextArea AND — for Replace, whose submit reads the
    # textbox — a truncated prefill would silently commit truncated
    # bases. Above the cap the box opens EMPTY: the context line + the
    # warning still convey the region size, and Delete / Copy act on the
    # region itself (never the textbox), so nothing is lost.
    _PREFILL_MAX_BP = 20_000

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Submit"),
        Binding("ctrl+enter", "submit", "Submit"),
    ]

    DEFAULT_CSS = """
    #edit-dlg {
        width: 84; max-width: 96%; min-width: 76; height: auto;
        background: $surface; border: solid $primary;
        padding: 1 2;
    }
    #edit-title {
        background: $primary-darken-2; color: $text;
        padding: 0 1; height: 1; text-align: center;
    }
    #edit-context {
        height: auto; color: $text-muted;
        margin: 1 0 0 0; padding: 0 1;
    }
    #edit-mode-row {
        height: 3; margin: 1 0 0 0;
        align: left middle;
    }
    #edit-mode-row RadioSet {
        layout: horizontal; height: 3; width: 1fr;
        border: none; padding: 0;
    }
    #edit-mode-row RadioButton {
        width: auto; padding: 0 1; margin-right: 2;
    }
    #edit-preview {
        height: 1; margin: 0 0 1 0;
        color: $accent; padding: 0 1;
    }
    #edit-label {
        margin: 0 0 0 0; padding: 0 1;
    }
    #edit-input-area {
        height: 6; margin: 0 0 1 0;
        border: solid $primary-darken-2;
    }
    #edit-err { height: 1; padding: 0 1; }
    #edit-btns { height: 3; margin-top: 1; align: right middle; }
    #edit-btns Button { margin-right: 1; }
    """

    def __init__(self, mode: str, existing: str = "",
                 start: int = 0, end: int = 0,
                 total: "int | None" = None):
        super().__init__()
        # Legacy "insert" alias → insert-left.
        if mode == self._MODE_INSERT:
            mode = self._MODE_INSERT_LEFT
        if mode not in self._MODES:
            mode = self._MODE_INSERT_LEFT
        # If the caller picked a region-required mode but didn't
        # actually supply a region, demote to insert-left so the modal
        # doesn't open in a state that can't be confirmed.
        if mode in self._MODES_NEEDING_REGION and end <= start:
            mode = self._MODE_INSERT_LEFT
        self._mode       = mode
        self._existing   = existing
        self._start      = start
        self._end        = end
        self._total      = (int(total) if total is not None
                            else (start + len(existing)))
        self._has_region = end > start
        # Sweep #39 hardening: one-shot dismiss guard — a double-
        # click on OK / Esc / button-mouse-up-after-mouse-down posts
        # two Pressed events in real terminals (see sweep #36 +
        # InstantPressButton). Without the guard the second dispatch
        # tries to dismiss an already-popped screen and could re-fire
        # the caller's _edit_dialog_result twice (which would commit
        # the same edit twice + leave two undo snapshots).
        self._dismissed: bool = False

    # ── Layout ─────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="edit-dlg"):
            yield Static(" Edit sequence ", id="edit-title")
            yield Static(self._build_context_line(), id="edit-context",
                          markup=True)
            with Horizontal(id="edit-mode-row"):
                with RadioSet(id="edit-mode"):
                    yield RadioButton(
                        "Insert left", id="edit-mode-insert_left",
                        value=(self._mode == self._MODE_INSERT_LEFT),
                    )
                    yield RadioButton(
                        "Insert right", id="edit-mode-insert_right",
                        value=(self._mode == self._MODE_INSERT_RIGHT),
                    )
                    yield RadioButton(
                        "Replace", id="edit-mode-replace",
                        value=(self._mode == self._MODE_REPLACE),
                    )
                    yield RadioButton(
                        "Delete", id="edit-mode-delete",
                        value=(self._mode == self._MODE_DELETE),
                    )
            yield Static(self._build_preview_line(), id="edit-preview",
                          markup=True)
            yield Label(
                "New sequence  (IUPAC: A T C G N R Y W S K M B D H V — "
                "paste OK; FASTA headers + whitespace + U auto-stripped):",
                id="edit-label",
            )
            yield TextArea(id="edit-input-area")
            yield Static("", id="edit-err", markup=True)
            with Horizontal(id="edit-btns"):
                # Sweep #39 hardening: InstantPressButton so a
                # single physical click registers on first try in
                # real terminals (same focus-transition workaround
                # sweep #36 applied to ColorPickerModal). Paired
                # with the dismiss-once guard so the click-cycle
                # double-fire doesn't double-dispatch the result.
                yield _InstantPressButton(
                    "OK  [Ctrl+S]", id="btn-ok",
                    variant="primary",
                )
                yield _InstantPressButton(
                    "Cancel  [Esc]", id="btn-cancel",
                )

    def _prefill_text(self) -> str:
        """Bases to seed the textbox with — the region's existing bases,
        but only when small enough that rendering is snappy AND (for
        Replace, whose submit reads the box) it round-trips losslessly.
        A larger region yields ``""`` (see ``_PREFILL_MAX_BP``)."""
        return (self._existing
                if len(self._existing) <= self._PREFILL_MAX_BP else "")

    def on_mount(self) -> None:
        # Prefill the textbox with the region's existing bases so the
        # user sees exactly what's selected / under the cursor. For
        # delete / copy it's a read-only preview of what's affected; for
        # insert / replace it's an editable starting point. Capped — a
        # very large selection opens with an empty box (see
        # `_prefill_text`).
        try:
            # Insert modes open with an EMPTY box (you're typing NEW
            # bases — a prefilled region would insert a duplicate);
            # replace / delete / copy prefill with the region.
            self.query_one("#edit-input-area", TextArea).text = (
                "" if self._mode in self._INSERT_MODES
                else self._prefill_text())
        except NoMatches:
            pass
        self._apply_mode_visibility()
        # Focus the input for Insert/Replace; focus OK for the no-input
        # modes (Delete is the default on open — a single Enter confirms
        # the prefilled deletion).
        try:
            if self._mode in self._MODES_NEEDING_INPUT:
                self.query_one("#edit-input-area", TextArea).focus()
            else:
                self.query_one("#btn-ok", Button).focus()
        except NoMatches:
            pass
        # Sync the RadioSet's keyboard-highlight cursor onto the CHECKED
        # button. Textual's `RadioSet._on_mount` runs `action_next_button`
        # which highlights index 0 regardless of which radio is
        # `value=True` — so a non-first default (Replace, or Delete via
        # the Delete key) leaves the "-selected" highlight box on Insert
        # left while a different radio is filled (user report 2026-06-01:
        # "the delete radio is selected but the first radio is
        # highlighted"). Deferred so it runs AFTER RadioSet._on_mount.
        def _sync_radio_highlight() -> None:
            try:
                rs = self.query_one("#edit-mode", RadioSet)
                idx = rs.pressed_index
                if idx >= 0:
                    rs._selected = idx
            except Exception:
                # `_selected` is private — if a future Textual drops it,
                # the highlight just stays put (purely cosmetic).
                _log.debug(
                    "EditSeqDialog: radio highlight sync skipped",
                    exc_info=True,
                )
        self.call_after_refresh(_sync_radio_highlight)

    # ── Mode + preview state ───────────────────────────────────────────────────

    def _build_context_line(self) -> str:
        """One-line summary of what the modal is operating on."""
        if self._has_region:
            length = self._end - self._start
            excerpt = self._existing[:32]
            if len(self._existing) > 32:
                excerpt += "…"
            excerpt_safe = excerpt.replace("[", r"\[")
            return (
                f"Selection: [b]{length:,} bp[/]  "
                f"({self._start + 1:,}‥{self._end:,} of "
                f"{self._total:,})   "
                f"[dim]{excerpt_safe}[/]"
            )
        return (
            f"Cursor at position [b]{self._start + 1:,}[/] of "
            f"{self._total:,} bp  [dim](no selection — pick "
            f"bases first to enable Replace / Delete / Copy)[/]"
        )

    def _build_preview_line(self) -> str:
        """The live WARNING line: "You are about to <action> <N> bp
        <position>", with the action verb, the bp count, and the
        position phrase colour-coded by action (green insert / red
        delete / yellow replace / cyan copy). Rebuilt whenever the mode
        changes or the textarea contents change (user spec 2026-06-01).

        ``#bp`` is the bases the action WRITES for the input modes
        (insert / replace — so it updates live as the user types) and
        the region size for delete / copy.
        """
        n = len(self._sanitize_pasted_sequence(self._current_input_text()))
        region = self._end - self._start
        if self._mode == self._MODE_INSERT_LEFT:
            verb, count, where, color = (
                "insert", n, "to the left of cursor", "green")
        elif self._mode == self._MODE_INSERT_RIGHT:
            verb, count, where, color = (
                "insert", n, "to the right of cursor", "green")
        elif self._mode == self._MODE_REPLACE:
            if not self._has_region:
                return ("[yellow]Replace needs a selection — switch to "
                        "an Insert mode or pick bases first.[/]")
            verb, count, where, color = "replace", n, "at cursor", "yellow"
        elif self._mode == self._MODE_DELETE:
            if not self._has_region:
                return ("[yellow]Delete needs a selection — pick "
                        "bases first.[/]")
            verb, count, where, color = "delete", region, "at cursor", "red"
        else:
            return ""
        # Colour-coded action verb + bp count + position phrase.
        return (
            f"You are about to [b {color}]{verb}[/] "
            f"[b {color}]{count:,} bp[/] [{color}]{where}[/]"
        )

    def _apply_mode_visibility(self) -> None:
        """Keep the textbox visible in EVERY mode (user request
        2026-06-01 — the modal always shows the bases being acted on).
        For insert / replace it's the editable input; for delete / copy
        it's a READ-ONLY preview of the region the action affects, so the
        label adapts to never read "New sequence" over a preview."""
        needs_input = self._mode in self._MODES_NEEDING_INPUT
        try:
            ta = self.query_one("#edit-input-area", TextArea)
            ta.read_only = not needs_input
        except NoMatches:
            pass
        try:
            lbl = self.query_one("#edit-label", Label)
            note = ("too large to preview — see the count above"
                    if len(self._existing) > self._PREFILL_MAX_BP
                    else "read-only preview")
            if self._mode == self._MODE_DELETE:
                lbl.update(f"Bases that will be deleted ({note}):")
            else:
                lbl.update(
                    "New sequence  (IUPAC: A T C G N R Y W S K M B D H V — "
                    "paste OK; FASTA headers + whitespace + U auto-stripped):"
                )
        except NoMatches:
            pass
        self._refresh_preview()

    def _current_input_text(self) -> str:
        try:
            return self.query_one("#edit-input-area", TextArea).text
        except NoMatches:
            return ""

    def _refresh_preview(self) -> None:
        try:
            self.query_one(
                "#edit-preview", Static,
            ).update(self._build_preview_line())
        except NoMatches:
            pass

    # ── Sanitization ───────────────────────────────────────────────────────────

    # Sweep #39 hardening (2026-05-27): pre-compile the sanitiser
    # regexes so a fast-typist's keystroke doesn't trigger a
    # re-compile per change. The leading position-number stripper
    # is line-anchored (only eats digits at the START of a line)
    # so a future IUPAC extension that allows in-sequence digits
    # can't be silently corrupted — and a paste like "1234ATCG"
    # (no whitespace) won't lose the leading "1234". NCBI's
    # numbered FASTA always puts the position number BEFORE
    # whitespace, so anchoring to "^\d+\s+" matches the real
    # use case without over-stripping.
    _SANITIZE_WS_RE = re.compile(r"\s+")
    _SANITIZE_LEADING_POS_RE = re.compile(r"^\d+\s+")

    def _sanitize_pasted_sequence(self, raw: str) -> str:
        """Clean up pasted text:
          * Drop FASTA header lines (lines starting with ``>``).
          * Drop NCBI-style line-leading position numbers
            (``"  60 ATCG"`` → ``"ATCG"``). Anchored to start-of-
            line so in-sequence digits are left alone for
            validation to catch.
          * Strip whitespace + newlines.
          * Upper-case, U→T (RNA→DNA).
        Returns the cleaned sequence — no validation.
        """
        if not raw:
            return ""
        out_parts: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(">"):
                continue
            # Strip NCBI-style leading position number ("60 ATCG…").
            stripped = self._SANITIZE_LEADING_POS_RE.sub("", stripped)
            out_parts.append(stripped)
        joined = "".join(out_parts)
        # Strip any remaining whitespace inside lines (spaces
        # between blocks in a 60-char-wide FASTA, tabs, etc.).
        joined = self._SANITIZE_WS_RE.sub("", joined)
        return joined.upper().replace("U", "T")

    def dismiss(self, result=None):  # type: ignore[override]
        """Sweep #39 hardening: one-shot dismiss. The Pressed
        message pump can deliver two Pressed events for a single
        physical button click in real terminals (sweep #36
        documents the focus-transition + click-cycle interaction);
        without this guard the second event would try to dismiss
        an already-popped screen and could fire the caller's
        ``_edit_dialog_result`` twice — committing the same edit
        twice and stacking two undo snapshots.

        Liskov note: base ``Screen.dismiss`` returns ``AwaitComplete``;
        we widen to ``AwaitComplete | None`` because the guard MUST
        NOT call ``super().dismiss`` again on the second event (the
        whole point of the one-shot). ``# type: ignore[override]``
        documents this is intentional — no caller awaits this
        modal's dismiss return value."""
        if self._dismissed:
            return None
        self._dismissed = True
        return super().dismiss(result)

    # ── Event wiring ──────────────────────────────────────────────────────────

    @on(RadioSet.Changed, "#edit-mode")
    def _on_mode_changed(self, event: RadioSet.Changed) -> None:
        btn = event.pressed
        if btn is None:
            return
        chosen = (btn.id or "").rsplit("-", 1)[-1]
        if chosen not in self._MODES or chosen == self._mode:
            return
        prev = self._mode
        self._mode = chosen
        # Textbox content follows the mode:
        #   * delete / copy → show the region as a read-only preview;
        #   * insert        → clear an UNTOUCHED prefill (or a preview
        #                     carried over from delete/copy) so the box
        #                     starts blank for fresh bases, but keep a
        #                     real edit the user already typed;
        #   * replace       → seed with the region if the box is empty.
        try:
            ta = self.query_one("#edit-input-area", TextArea)
            pf = self._prefill_text()
            if chosen == self._MODE_DELETE:
                ta.text = pf
            elif chosen in self._INSERT_MODES:
                if ta.text == pf or prev == self._MODE_DELETE:
                    ta.text = ""
            elif chosen == self._MODE_REPLACE:
                # Seed with the region when arriving from an insert with
                # an empty box, OR from a delete read-only preview (whose
                # text — possibly an empty huge-region placeholder — must
                # not become the replacement payload).
                if not ta.text or prev == self._MODE_DELETE:
                    ta.text = pf
        except NoMatches:
            pass
        self._apply_mode_visibility()
        # Refocus the relevant widget for fast keyboard-driven flow.
        try:
            if chosen in self._MODES_NEEDING_INPUT:
                self.query_one("#edit-input-area", TextArea).focus()
            else:
                self.query_one("#btn-ok", Button).focus()
        except NoMatches:
            pass

    @on(TextArea.Changed, "#edit-input-area")
    def _on_input_changed(self, _event: TextArea.Changed) -> None:
        raw = self._current_input_text()
        cleaned = self._sanitize_pasted_sequence(raw)
        try:
            err = self.query_one("#edit-err", Static)
        except NoMatches:
            return
        if not cleaned:
            err.update("")
        elif len(cleaned) > self._MAX_INPUT_BP:
            # Sweep #39 hardening: surface the cap LIVE so the user
            # sees the refusal as they paste, before clicking OK.
            err.update(Text(
                f"{len(cleaned):,} bp — exceeds the "
                f"{self._MAX_INPUT_BP:,} bp cap for a single edit. "
                f"Trim the paste or save as a library entry.",
                style="bold red",
            ))
        else:
            bad = sorted(
                set(c for c in cleaned if c not in self._VALID),
            )
            if bad:
                err.update(Text(
                    f"Invalid: {'  '.join(repr(c) for c in bad)} — "
                    f"only IUPAC DNA codes allowed",
                    style="bold red",
                ))
            else:
                err.update(Text(
                    f"{len(cleaned):,} bp (after stripping "
                    f"whitespace / FASTA headers)",
                    style="dim green",
                ))
        self._refresh_preview()

    # ── Submit / cancel ───────────────────────────────────────────────────────

    def action_submit(self) -> None:
        self._try_submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#btn-ok")
    def _ok(self, _) -> None:
        self._try_submit()

    @on(Button.Pressed, "#btn-cancel")
    def _cancel_btn(self, _) -> None:
        self.dismiss(None)

    def _set_err(self, msg: str, *, severity: str = "red") -> None:
        try:
            self.query_one("#edit-err", Static).update(
                Text(msg, style=f"bold {severity}"),
            )
        except NoMatches:
            pass

    def _try_submit(self) -> None:
        if self._mode in (self._MODE_REPLACE, self._MODE_DELETE) \
                and not self._has_region:
            self._set_err(
                "This mode needs a selection — "
                "switch to an Insert mode or pick bases on the canvas first.",
            )
            return
        if self._mode == self._MODE_DELETE:
            # Empty bases through the replace dispatch.
            self.dismiss(("", self._MODE_REPLACE,
                           self._start, self._end))
            return
        # Insert / Replace: sanitize + validate input.
        cleaned = self._sanitize_pasted_sequence(
            self._current_input_text(),
        )
        if not cleaned:
            self._set_err(
                "Please enter or paste a sequence.",
            )
            return
        # Sweep #39 hardening: cap pasted size BEFORE
        # validation so a 50 MB paste that's all valid IUPAC
        # still gets refused instead of locking up the
        # canvas-rebuild + the restriction-scan worker on
        # commit. 200 kbp matches `_MAX_FEATURE_SEQ_LEN` and is
        # well above realistic plasmid-edit sizes.
        if len(cleaned) > self._MAX_INPUT_BP:
            self._set_err(
                f"Input is {len(cleaned):,} bp — exceeds the "
                f"{self._MAX_INPUT_BP:,} bp cap for a single "
                f"sequence edit. Save the fragment as a feature "
                f"or plasmid library entry instead.",
            )
            return
        bad = [c for c in cleaned if c not in self._VALID]
        if bad:
            return   # live validation already shows the error
        # Map the internal mode → the (new_bases, mode, s, e) payload
        # `_edit_dialog_result` expects ("insert" at a position, or
        # "replace" of [s, e)). Insert-left inserts at the region start,
        # insert-right at the region end (= after the selection / cursor
        # bp); both reuse the App's existing "insert" dispatch.
        if self._mode == self._MODE_INSERT_LEFT:
            self.dismiss((cleaned, "insert", self._start, self._start))
        elif self._mode == self._MODE_INSERT_RIGHT:
            self.dismiss((cleaned, "insert", self._end, self._end))
        else:  # replace
            self.dismiss((cleaned, "replace", self._start, self._end))


class MigrateDataModal(_OneShotDismissScreen, ModalScreen):
    """Migrate Data entry point — export ALL user data to one portable
    file, or import such a file into this install. Dismisses with
    ``"export"`` / ``"import"`` / ``None``."""

    _blocks_undo: bool = True
    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    #migrate-box { width: 86; max-width: 95%; height: auto; max-height: 90%;
        background: $surface; border: solid $primary; padding: 1 2; }
    #migrate-title { background: $primary-darken-2; color: $text;
        padding: 0 1; margin-bottom: 1; text-align: center; }
    #migrate-blurb { color: $text-muted; margin-bottom: 1; }
    #migrate-btns { align: right middle;  height: 3; margin-top: 1; }
    #migrate-btns Button { margin-right: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="migrate-box"):
            yield Static(" Migrate Data ", id="migrate-title")
            yield Static(
                "Move ALL your SpliceCraft data between machines as one "
                "portable, compressed file.\n\n"
                "• Export — package your entire library, collections, parts "
                "bin, primers, features, grammars, codon tables, entry "
                "vectors, settings, lab notebook, and full construction "
                "history into a single .zip.\n"
                "• Import — load such a .zip into this install. Your current "
                "data is backed up automatically first, then replaced — so a "
                "fresh install picks up exactly where you left off.",
                id="migrate-blurb", markup=False,
            )
            with Horizontal(id="migrate-btns"):
                yield Button("Export all my data", id="btn-migrate-export",
                             variant="primary")
                yield Button("Import a data file", id="btn-migrate-import",
                             variant="primary")
                yield Button("Cancel", id="btn-migrate-cancel")

    @on(Button.Pressed, "#btn-migrate-export")
    def _exp(self, _) -> None:
        self.dismiss("export")

    @on(Button.Pressed, "#btn-migrate-import")
    def _imp(self, _) -> None:
        self.dismiss("import")

    @on(Button.Pressed, "#btn-migrate-cancel")
    def _cxl(self, _) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DropdownScreen(_OneShotDismissScreen, ModalScreen):
    """Lightweight overlay showing a positioned dropdown menu.

    Uses a near-transparent backdrop so the main app stays visible — the
    dropdown looks like a real popup anchored to the menu bar, not a
    separate "screen". Click outside the box dismisses.
    """

    # Sweep #26: opening a menu shouldn't let an absent-minded Ctrl+Z
    # while the dropdown floats roll back the canvas underneath.
    _blocks_undo: bool = True

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    DropdownScreen {
        background: rgba(0, 0, 0, 0.15);
    }
    """

    def __init__(self, items: list, x: int, y: int) -> None:
        super().__init__()
        self._items = items   # (label, action_str | None)
        self._x = x
        self._y = y
        self._cursor = next(
            (i for i, (_, a) in enumerate(items) if a is not None), 0
        )

    def compose(self) -> ComposeResult:
        yield Static(
            self._build_dropdown_text(),
            id="dropdown-box",
        )

    def on_mount(self) -> None:
        inner_w = max((len(lbl) for lbl, _ in self._items), default=10) + 4
        box_h   = len(self._items) + 2
        box = self.query_one("#dropdown-box", Static)
        box.styles.offset = (self._x, self._y)
        box.styles.width  = inner_w
        box.styles.height = box_h
        box.styles.border = ("solid", "#555555")
        box.styles.background = "#1e1e1e"

    def _build_dropdown_text(self) -> Text:
        inner_w = max((len(lbl) for lbl, _ in self._items), default=10) + 4
        sep_line = "\u2500" * (inner_w - 2)
        result = Text()
        for i, (label, action) in enumerate(self._items):
            is_sep      = (label == "---")
            is_selected = (i == self._cursor and not is_sep and action is not None)
            is_disabled = (action is None and not is_sep)

            if is_sep:
                line = Text(sep_line + "\n", style="white")
            else:
                padded = f" {label:<{inner_w - 3}}"
                if is_selected:
                    line = Text(padded + "\n", style="reverse white")
                elif is_disabled:
                    line = Text(padded + "\n", style="dim white")
                else:
                    line = Text(padded + "\n", style="white")
            result.append_text(line)
        return result

    def _refresh_box(self) -> None:
        box = self.query_one("#dropdown-box", Static)
        box.update(self._build_dropdown_text())

    def on_key(self, event) -> None:
        items = self._items
        if event.key == "up":
            pos = self._cursor - 1
            while pos >= 0 and (items[pos][0] == "---" or items[pos][1] is None):
                pos -= 1
            if pos >= 0:
                self._cursor = pos
                self._refresh_box()
            event.stop()
        elif event.key == "down":
            pos = self._cursor + 1
            while pos < len(items) and (items[pos][0] == "---" or items[pos][1] is None):
                pos += 1
            if pos < len(items):
                self._cursor = pos
                self._refresh_box()
            event.stop()
        elif event.key == "enter":
            label, action = items[self._cursor]
            if action is not None:
                self.dismiss(action)
            event.stop()

    def on_click(self, event: Click) -> None:
        bx = self._x
        by = self._y
        inner_w = max((len(lbl) for lbl, _ in self._items), default=10) + 4
        bh = len(self._items) + 2
        cx, cy = event.screen_x, event.screen_y
        if bx <= cx < bx + inner_w and by <= cy < by + bh:
            row_in_box = cy - by - 1  # -1 for top border
            if 0 <= row_in_box < len(self._items):
                label, action = self._items[row_in_box]
                if label == "---" or action is None:
                    event.stop()
                    return
                self.dismiss(action)
        else:
            self.dismiss(None)
        event.stop()

    def action_cancel(self) -> None:
        self.dismiss(None)


class PlasmidFeaturePickerModal(_OneShotDismissScreen, ModalScreen):
    """Scrollable list of non-source features from a specific library entry.

    Dismisses with a feature-library-style entry dict
    ``{name, feature_type, sequence, strand, qualifiers, description}``,
    or None on cancel. No persistence side effects — the caller decides
    whether to save the picked entry or just use it to prefill a form.
    """

    BINDINGS = [
        Binding("escape", "cancel",     "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    def __init__(self, entries: list[dict], plasmid_name: str = ""):
        super().__init__()
        self._entries = list(entries)
        self._plasmid_name = plasmid_name or "plasmid"

    def compose(self) -> ComposeResult:
        with Vertical(id="featpick-dlg"):
            yield Static(f" Feature from [{self._plasmid_name}] ",
                         id="featpick-title")
            yield DataTable(id="featpick-table", cursor_type="row",
                            zebra_stripes=True)
            with Horizontal(id="featpick-btns"):
                yield Button("Select", id="btn-featpick-ok", variant="primary")
                yield Button("Cancel", id="btn-featpick-cancel")

    def on_mount(self) -> None:
        t = self.query_one("#featpick-table", DataTable)
        t.add_columns("Name", "Type", "Strand", "Length")
        for i, e in enumerate(self._entries):
            strand_str = "+" if e.get("strand", 1) == 1 else "−"
            t.add_row(
                e.get("name", "?"),
                e.get("feature_type", "?"),
                strand_str,
                f"{len(e.get('sequence', ''))} bp",
                key=str(i),
            )
        if self._entries:
            t.move_cursor(row=0)
            t.focus()

    @on(Button.Pressed, "#btn-featpick-ok")
    def _select(self, _):
        self._dismiss_cursor()

    @on(DataTable.RowSelected, "#featpick-table")
    def _row_selected(self, event):
        if event.row_key and event.row_key.value is not None:
            try:
                idx = int(event.row_key.value)
            except (TypeError, ValueError):
                return
            if 0 <= idx < len(self._entries):
                self.dismiss(dict(self._entries[idx]))

    def _dismiss_cursor(self) -> None:
        t = self.query_one("#featpick-table", DataTable)
        if t.row_count == 0:
            self.dismiss(None)
            return
        row_keys = list(t.rows.keys())
        if 0 <= t.cursor_row < len(row_keys):
            key = row_keys[t.cursor_row].value
            if key is None:
                self.dismiss(None)
                return
            try:
                idx = int(key)
            except (TypeError, ValueError):
                self.dismiss(None)
                return
            if 0 <= idx < len(self._entries):
                self.dismiss(dict(self._entries[idx]))
                return
        self.dismiss(None)

    @on(Button.Pressed, "#btn-featpick-cancel")
    def _cancel_btn(self, _):
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class UngroupScopeModal(ModalScreen):
    """Sweep #29: tiny confirm modal that asks whether `Ungroup`
    should drop the qualifier from JUST this feature or from
    every member of the group.

    Returns `{"scope": "this" | "whole"}` on Save, `None` on
    Cancel."""

    _blocks_undo: bool = True

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    #ungroup-dlg {
        width: 70; height: auto;
        background: $surface; border: solid $primary;
        padding: 1 2;
    }
    #ungroup-title {
        background: $primary-darken-2; color: $text;
        padding: 0 1; margin-bottom: 1;
        text-align: center; text-style: bold;
    }
    #ungroup-msg { color: $text-muted; height: auto;
                   margin-bottom: 1; padding: 0 1; }
    #ungroup-btns { height: 3; margin-top: 1;
                    align: right middle; }
    #ungroup-btns Button { margin-right: 1; min-width: 18; }
    """

    def __init__(self, group_id: str) -> None:
        super().__init__()
        self._group_id = str(group_id or "")
        self._dismissed: bool = False

    def _dismiss_once(self, result) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(result)

    def compose(self) -> ComposeResult:
        with Vertical(id="ungroup-dlg"):
            yield Static(" Ungroup feature ", id="ungroup-title")
            yield Static(
                f"This feature is part of group "
                f"[b]…{self._group_id[-6:]}[/b]. Choose the scope "
                f"of the ungroup operation:\n\n"
                f"  • [b]Just this[/b] — drop the qualifier from "
                f"this feature only. Other group members keep "
                f"their shared id.\n"
                f"  • [b]Whole group[/b] — dissolve the group: "
                f"every member loses the qualifier.",
                id="ungroup-msg", markup=True,
            )
            with Horizontal(id="ungroup-btns"):
                yield Button("Just this", id="btn-ungroup-this",
                             variant="primary")
                yield Button("Whole group",
                             id="btn-ungroup-whole",
                             variant="warning")
                yield Button("Cancel", id="btn-ungroup-cancel")

    @on(Button.Pressed, "#btn-ungroup-this")
    def _this(self, _) -> None:
        self._dismiss_once({"scope": "this"})

    @on(Button.Pressed, "#btn-ungroup-whole")
    def _whole(self, _) -> None:
        self._dismiss_once({"scope": "whole"})

    @on(Button.Pressed, "#btn-ungroup-cancel")
    def _cancel(self, _) -> None:
        self._dismiss_once(None)

    def action_cancel(self) -> None:
        self._dismiss_once(None)


class SplitPositionPromptModal(ModalScreen):
    """Sweep #30: tiny modal for "split a member at a position".

    Used by the unified AddFeatureModal / FeatureEditModal — the
    user selects a row in the members table and clicks "Split".
    This pops a single-number input pre-filled with the row's
    midpoint; on Save it returns `{"pos": <int>}`.

    Validates that the user-typed position falls strictly inside
    `(rs, re)` — equal-to either endpoint would produce a zero-
    width head or tail, which `_split_member` rejects. Status
    line surfaces the validation error rather than dismissing on
    a bad value so the user can correct it inline.
    """

    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter",  "submit", "Split"),
    ]

    DEFAULT_CSS = """
    #splitpos-dlg {
        width: 60; height: auto;
        background: $surface; border: solid $primary;
        padding: 1 2;
    }
    #splitpos-title {
        background: $primary-darken-2; color: $text;
        padding: 0 1; margin-bottom: 1;
        text-align: center; text-style: bold;
    }
    #splitpos-dlg Label { color: $text-muted; margin-top: 1; }
    #splitpos-input  { margin-top: 1; }
    #splitpos-status { height: 2; margin-top: 1; }
    #splitpos-btns   { height: 3; margin-top: 1;
                       align: right middle; }
    #splitpos-btns Button { margin-right: 1; min-width: 12; }
    """

    def __init__(self, rs: int, re_: int) -> None:
        super().__init__()
        if not isinstance(rs, int) or not isinstance(re_, int):
            raise ValueError("rs and re_ must be ints")
        if rs + 1 >= re_:
            # No interior position exists — a row of width 1 can't
            # be split. Caller (the modal's Split button handler)
            # is expected to surface this BEFORE opening this
            # prompt, but we guard defensively here too.
            raise ValueError(
                f"row [{rs}, {re_}) has no interior position to "
                f"split at (need width ≥ 2)"
            )
        self._rs = rs
        self._re = re_
        self._dismissed = False

    def _dismiss_once(self, result) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(result)

    def compose(self) -> ComposeResult:
        with Vertical(id="splitpos-dlg"):
            yield Static(" Split row at position ",
                          id="splitpos-title")
            yield Label(
                f"Position must satisfy "
                f"[b]{self._rs} < pos < {self._re}[/b]. "
                f"Default = midpoint.",
                markup=True,
            )
            default = (self._rs + self._re) // 2
            yield Input(
                value=str(default),
                placeholder=f"e.g. {default}",
                id="splitpos-input",
            )
            yield Static("", id="splitpos-status", markup=True)
            with Horizontal(id="splitpos-btns"):
                yield Button("Split", id="btn-splitpos-save",
                             variant="primary")
                yield Button("Cancel", id="btn-splitpos-cancel")

    def on_mount(self) -> None:
        try:
            inp = self.query_one("#splitpos-input", Input)
            inp.focus()
            # Select all so the user can immediately type to
            # overwrite the default.
            try:
                inp.action_select_all()
            except (AttributeError, NoMatches):
                pass
        except NoMatches:
            pass

    def action_submit(self) -> None:
        self._save(None)

    @on(Button.Pressed, "#btn-splitpos-save")
    def _save(self, _) -> None:
        try:
            raw = self.query_one("#splitpos-input", Input).value
        except NoMatches:
            return
        raw = (raw or "").strip()
        if not raw:
            self._set_status(
                "[red]Enter a position between "
                f"{self._rs} and {self._re}.[/red]"
            )
            return
        try:
            pos = int(raw)
        except ValueError:
            self._set_status("[red]Position must be an integer.[/red]")
            return
        if not (self._rs < pos < self._re):
            self._set_status(
                f"[red]Position must satisfy "
                f"{self._rs} < pos < {self._re} "
                f"(got {pos}).[/red]"
            )
            return
        self._dismiss_once({"pos": pos})

    @on(Button.Pressed, "#btn-splitpos-cancel")
    def _cancel(self, _) -> None:
        self._dismiss_once(None)

    def action_cancel(self) -> None:
        self._dismiss_once(None)

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#splitpos-status", Static).update(msg)
        except NoMatches:
            pass


class StrandPickerModal(ModalScreen):
    """Sweep #30 (2026-05-26): per-row strand picker for the
    members tables. Click the strand cell of a row in the
    AddFeatureModal / FeatureEditModal members table → this modal
    opens with the row's current strand pre-selected. User picks
    one of the four valid strands; modal dismisses with
    `{"strand": <int>}` (Forward=1 / Reverse=-1 / Arrowless=0 /
    Double=2) or `None` on Cancel.

    Hardening: the only way OUT with a non-Cancel result is via
    the four hard-coded button handlers, so the returned int is
    always in `{-1, 0, 1, 2}`. No free-text input → no
    sanitization needed. Modal is `_blocks_undo=True` so a
    pending strand change can't be wiped by Ctrl+Z while the
    picker is open."""

    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    /* 2026-05-26: widened from 60 → 56 vertical stack so the
       4 strand buttons no longer overflow the dialog horizontally.
       The previous horizontal Horizontal layout pushed the last
       button (`↔ Both strands`) past the dialog border, where
       the clipped click hit-zone swallowed the first press and
       the user had to click a second time. Vertical stack keeps
       each button full-width inside the dialog so a single click
       lands cleanly. */
    #strand-pick-dlg {
        width: 56; height: auto; max-height: 90%;
        background: $surface; border: solid $primary;
        padding: 1 2;
    }
    #strand-pick-title {
        background: $primary-darken-2; color: $text;
        padding: 0 1; margin-bottom: 1;
        text-align: center; text-style: bold;
    }
    #strand-pick-dlg Label { color: $text-muted; margin-top: 1; }
    #strand-pick-btns {
        height: auto; margin-top: 1;
        align-horizontal: center;
    }
    #strand-pick-btns Button {
        width: 100%;
        margin: 0 0 1 0;
    }
    #strand-pick-cancel-row {
        height: 3; margin-top: 1;
        align: right middle;
    }
    """

    def __init__(self, current_strand: int = 0,
                 *,
                 row_idx: "int | None" = None) -> None:
        super().__init__()
        # Sanitise the current strand on the way in: any value
        # outside {-1, 0, 1, 2} falls back to arrowless (0). This
        # defends the modal against a malformed caller without
        # crashing.
        try:
            current_strand = int(current_strand)
        except (TypeError, ValueError):
            current_strand = 0
        if current_strand not in (-1, 0, 1, 2):
            current_strand = 0
        self._current  = current_strand
        self._row_idx  = row_idx
        self._dismissed: bool = False

    def _dismiss_once(self, result) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(result)

    def compose(self) -> ComposeResult:
        title_suffix = (f" — row {self._row_idx + 1}"
                        if isinstance(self._row_idx, int) else "")
        cur = self._current
        with Vertical(id="strand-pick-dlg"):
            yield Static(
                f" Pick strand{title_suffix} ",
                id="strand-pick-title",
            )
            yield Label(
                "Choose the strand this segment lives on. "
                "Forward = top (5'→3' on the sense strand); "
                "Reverse = bottom (5'→3' on the antisense / "
                "complementary strand).",
            )
            # 2026-05-26: stack vertically so each button has the
            # full dialog width — the horizontal row clipped the
            # last two buttons and made single-click unreliable.
            # Buttons use `_InstantPressButton` so a single
            # mouse-down fires `Pressed` immediately, bypassing
            # the Textual focus-transition gate that swallowed
            # the first click in a real terminal.
            with Vertical(id="strand-pick-btns"):
                yield _InstantPressButton(
                    "▶ Top (forward)",  id="btn-strand-fwd",
                    variant=("primary" if cur == 1 else "default"),
                )
                yield _InstantPressButton(
                    "◀ Bottom (reverse)", id="btn-strand-rev",
                    variant=("primary" if cur == -1 else "default"),
                )
                yield _InstantPressButton(
                    "▒ None (arrowless)", id="btn-strand-none",
                    variant=("primary" if cur == 0 else "default"),
                )
                yield _InstantPressButton(
                    "↔ Both strands", id="btn-strand-both",
                    variant=("primary" if cur == 2 else "default"),
                )
            with Horizontal(id="strand-pick-cancel-row"):
                yield _InstantPressButton(
                    "Cancel", id="btn-strand-cancel",
                )

    def on_mount(self) -> None:
        """Focus the currently-selected strand button so the user
        can hit Enter / Space to confirm without a mouse round-
        trip. Falls back to forward when no match (defensive — the
        sanitiser in __init__ already clamps invalid input)."""
        sel = {
             1: "#btn-strand-fwd",
            -1: "#btn-strand-rev",
             0: "#btn-strand-none",
             2: "#btn-strand-both",
        }.get(self._current, "#btn-strand-fwd")
        try:
            self.query_one(sel, Button).focus()
        except NoMatches:
            pass

    @on(Button.Pressed, "#btn-strand-fwd")
    def _fwd(self, _) -> None:
        self._dismiss_once({"strand": 1})

    @on(Button.Pressed, "#btn-strand-rev")
    def _rev(self, _) -> None:
        self._dismiss_once({"strand": -1})

    @on(Button.Pressed, "#btn-strand-none")
    def _none(self, _) -> None:
        self._dismiss_once({"strand": 0})

    @on(Button.Pressed, "#btn-strand-both")
    def _both(self, _) -> None:
        self._dismiss_once({"strand": 2})

    @on(Button.Pressed, "#btn-strand-cancel")
    def _cancel(self, _) -> None:
        self._dismiss_once(None)

    def action_cancel(self) -> None:
        self._dismiss_once(None)


class PrimerDuplicatesModal(_OneShotDismissScreen, ModalScreen):
    """Surfaced at startup when `primers.json` carries duplicate-
    sequence entries OR name-collision groups (same name, different
    sequences — common in `.dna` imports that replay primer entries
    across multiple plasmids with different binding-region / tail
    variants).

    Defaults to KEEP — user must explicitly click Delete to remove
    duplicates. The Keep button has initial focus so a stray Enter on
    splash dismiss doesn't silently delete primer data.

    Two cleanup passes on confirm:
      * Sequence collisions: first occurrence wins (sacred policy).
      * Name collisions: longest sequence wins (typically the variant
        carrying the full cloning tail, not a truncated binding only).

    Dismiss payload:
      True  — user confirmed deletion
      False / None — user kept everything (cancel / Escape / Keep)
    """

    _blocks_undo: bool = True   # primer-library write, no Ctrl+Z race

    BINDINGS = [
        Binding("escape", "cancel",         "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    DEFAULT_CSS = """
    #pdup-dlg {
        width: 70;
        height: auto;
        background: $surface;
        border: solid $warning;
        padding: 1 2;
    }
    #pdup-title {
        text-align: center;
        background: $warning-darken-2;
        color: $text;
        padding: 0 1;
    }
    #pdup-body {
        margin: 1 0;
        padding: 0 1;
    }
    #pdup-btns {
        height: 3;
        align: right middle;
        padding-top: 1;
    }
    #pdup-btns Button { margin-left: 1; }
    """

    def __init__(self, total: int, seq_duplicates: int,
                 name_collisions: int, final_kept: int):
        super().__init__()
        self._total = total
        self._seq_duplicates = seq_duplicates
        self._name_collisions = name_collisions
        self._final_kept = final_kept

    def compose(self) -> ComposeResult:
        to_remove = self._seq_duplicates + self._name_collisions
        lines: list[str] = []
        if self._seq_duplicates:
            # `_seq_duplicates` is the number of entries that WOULD
            # BE REMOVED, not the number that share — for a group of
            # 3 primers with the same sequence we'd remove 2 and keep
            # 1. Pre-fix this said "{2} entries share a DNA sequence"
            # which under-counts the actual sharers.
            lines.append(
                f"• {self._seq_duplicates} entr"
                f"{'ies' if self._seq_duplicates != 1 else 'y'} "
                f"would be removed for sharing a DNA sequence "
                f"(longest preserved per group)"
            )
        if self._name_collisions:
            lines.append(
                f"• {self._name_collisions} entr"
                f"{'ies' if self._name_collisions != 1 else 'y'} "
                f"would be removed for sharing a name "
                f"(longest sequence preserved per name)"
            )
        breakdown = "\n".join(lines)
        with Vertical(id="pdup-dlg"):
            yield Static(" Duplicate primers detected ", id="pdup-title")
            yield Static(
                f"Your primer library has {self._total} entries.\n"
                f"{breakdown}\n\n"
                f"Delete duplicates? Sequence collisions keep the "
                f"first occurrence; name collisions keep the entry "
                f"with the longest sequence (typically the full "
                f"primer including its cloning tail). Library would "
                f"end up with {self._final_kept} entries.",
                id="pdup-body",
            )
            with Horizontal(id="pdup-btns"):
                yield Button("Keep all", id="btn-pdup-keep",
                              variant="default")
                yield Button(
                    f"Delete {to_remove} entr"
                    f"{'ies' if to_remove != 1 else 'y'}",
                    id="btn-pdup-delete", variant="warning",
                )

    def on_mount(self) -> None:
        # Default focus on Keep so a stray Enter on splash dismiss
        # doesn't trigger a delete.
        try:
            self.query_one("#btn-pdup-keep", Button).focus()
        except NoMatches:
            pass

    @on(Button.Pressed, "#btn-pdup-keep")
    def _keep(self, _) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#btn-pdup-delete")
    def _delete(self, _) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class EditGrammarConfirmModal(ModalScreen):
    """Warning before opening `GrammarEditorModal` on a custom
    grammar. Surfaces the fact that edits propagate to every
    TU/MOD already saved under this grammar — overhang positions
    can shift, enzyme assignments change which fragments digest
    cleanly, and the persisted parts may stop chaining at the
    next cycle. Default focus on `Cancel`."""

    # Sweep #26: [INV-50] destructive confirm — Ctrl+Z above
    # the modal could race the user's grammar-edit launch.
    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    def __init__(self, grammar_name: str, n_dependents: int = 0) -> None:
        super().__init__()
        self._grammar_name = grammar_name
        self._n_dependents = n_dependents
        self._dismissed: bool = False   # [INV-50]

    def _dismiss_once(self, payload) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    def compose(self) -> ComposeResult:
        with Vertical(id="gec-dlg"):
            yield Static(" Edit Grammar? ", id="gec-title")
            from rich.markup import escape as _esc
            nm = _esc(self._grammar_name)
            if self._n_dependents > 0:
                msg = (
                    f"  You're about to edit grammar [bold]{nm}[/bold].\n"
                    f"  [yellow]{self._n_dependents}[/yellow] saved "
                    f"part{'s' if self._n_dependents != 1 else ''} "
                    f"reference{'' if self._n_dependents != 1 else 's'} "
                    f"this grammar.\n"
                    f"  Changes to enzyme, recognition, or overhang "
                    f"positions can break those parts' chaining."
                )
            else:
                msg = (
                    f"  You're about to edit grammar [bold]{nm}[/bold].\n"
                    f"  Changes to enzyme, recognition site, or overhang "
                    f"positions can\n"
                    f"  invalidate any TUs / MODs you save under this "
                    f"grammar later."
                )
            yield Static(msg, id="gec-msg", markup=True)
            with Horizontal(id="gec-btns"):
                yield Button("Cancel", id="btn-gec-no", variant="default")
                yield Button("Edit",   id="btn-gec-yes", variant="warning")

    def on_mount(self) -> None:
        try:
            self.query_one("#btn-gec-no", Button).focus()
        except NoMatches:
            pass

    @on(Button.Pressed, "#btn-gec-no")
    def _no(self, _): self._dismiss_once(False)

    @on(Button.Pressed, "#btn-gec-yes")
    def _yes(self, _): self._dismiss_once(True)

    def action_cancel(self): self._dismiss_once(False)


class _ConfirmDeleteGrammarModal(ModalScreen):
    """Confirmation prompt before `GrammarManagerModal._delete`
    actually wipes a custom grammar + its entry-vector bindings.
    Same shape as `EditGrammarConfirmModal` but with a red Delete
    button and a body that quantifies the blast radius (how many
    parts reference this grammar, what cleanup will run). Default
    focus on Cancel — protects against an accidental Enter on the
    bare Delete button in the manager."""

    # Sweep #26: [INV-50] destructive confirm — Ctrl+Z above
    # could race the impending delete cascade.
    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    def __init__(self, grammar_name: str, body_markup: str) -> None:
        super().__init__()
        self._grammar_name = grammar_name
        self._body = body_markup
        self._dismissed: bool = False   # [INV-50]

    def _dismiss_once(self, payload) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    def compose(self) -> ComposeResult:
        with Vertical(id="gec-dlg"):
            yield Static(" Delete Grammar? ", id="gec-title")
            yield Static(self._body, id="gec-msg", markup=True)
            with Horizontal(id="gec-btns"):
                yield Button("Cancel", id="btn-gec-no",
                             variant="default")
                yield Button("Delete", id="btn-gec-yes",
                             variant="error")

    def on_mount(self) -> None:
        try:
            self.query_one("#btn-gec-no", Button).focus()
        except NoMatches:
            pass

    @on(Button.Pressed, "#btn-gec-no")
    def _no(self, _): self._dismiss_once(False)

    @on(Button.Pressed, "#btn-gec-yes")
    def _yes(self, _): self._dismiss_once(True)

    def action_cancel(self): self._dismiss_once(False)


class ExperimentDeleteConfirmModal(ModalScreen):
    """Yes / No confirmation for entry deletion. Defaults focus on No
    so a stray Enter can't delete an entry. Esc → No."""

    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab",    "app.focus_next", "Next button", show=False),
    ]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body  = body
        self._dismissed: bool = False   # [INV-50]

    def _dismiss_once(self, payload: bool) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    def compose(self) -> ComposeResult:
        with Vertical(id="expdel-dlg"):
            yield Static(f" {self._title} ", id="expdel-title")
            yield Static(self._body, id="expdel-msg", markup=True)
            with Horizontal(id="expdel-btns"):
                yield Button("No", id="btn-expdel-no", variant="default")
                yield Button("Yes, delete", id="btn-expdel-yes",
                              variant="error")

    DEFAULT_CSS = """
    #expdel-dlg {
        width: 60; height: auto;
        background: $surface; border: solid $primary-darken-2;
        padding: 1 2; layout: vertical;
    }
    #expdel-title {
        background: $primary-darken-2; color: $text;
        padding: 0 1; height: 1; text-align: center;
    }
    #expdel-msg { height: auto; margin-top: 1; color: $text; }
    #expdel-btns { height: 3; margin-top: 1; align: center middle; }
    #expdel-btns Button { margin: 0 1; min-width: 14; }
    """

    def on_mount(self) -> None:
        try:
            self.query_one("#btn-expdel-no", Button).focus()
        except NoMatches:
            pass

    @on(Button.Pressed, "#btn-expdel-no")
    def _no(self, _ev) -> None:
        self._dismiss_once(False)

    @on(Button.Pressed, "#btn-expdel-yes")
    def _yes(self, _ev) -> None:
        self._dismiss_once(True)

    def action_cancel(self) -> None:
        self._dismiss_once(False)


class ExperimentUnsavedChangesModal(ModalScreen):
    """Three-way prompt shown when the user tries to leave the
    `ExperimentsScreen` with a dirty compose buffer.

    Dismiss payload:
      ``"save"``    — save then exit
      ``"abandon"`` — exit without saving
      ``"cancel"``  — stay on screen (default; Esc + initial focus)
    """

    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel",         "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    DEFAULT_CSS = """
    #expunsaved-dlg {
        width: 64; height: auto;
        background: $surface; border: solid $warning;
        padding: 1 2; layout: vertical;
    }
    #expunsaved-title {
        background: $warning; color: $text;
        padding: 0 1; height: 1; text-style: bold; text-align: center;
    }
    #expunsaved-msg { height: auto; margin-top: 1; color: $text; }
    #expunsaved-btns { height: 3; margin-top: 1; align: center middle; }
    #expunsaved-btns Button { margin: 0 1; min-width: 18; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._dismissed: bool = False   # [INV-50]

    def _dismiss_once(self, payload: str) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    def compose(self) -> ComposeResult:
        with Vertical(id="expunsaved-dlg"):
            yield Static(" Unsaved changes ", id="expunsaved-title")
            yield Static(
                "The current entry has [b]unsaved changes[/b]. "
                "What would you like to do?",
                id="expunsaved-msg", markup=True,
            )
            with Horizontal(id="expunsaved-btns"):
                yield Button("Close", id="btn-expunsaved-cancel",
                             variant="default")
                yield Button("Save changes",
                             id="btn-expunsaved-save",
                             variant="primary")
                yield Button("Abandon and exit",
                             id="btn-expunsaved-abandon",
                             variant="error")

    def on_mount(self) -> None:
        # Default focus on Cancel/Close so a stray Enter can't
        # discard work or auto-save unintended changes.
        try:
            self.query_one(
                "#btn-expunsaved-cancel", Button,
            ).focus()
        except NoMatches:
            pass

    @on(Button.Pressed, "#btn-expunsaved-cancel")
    def _cancel_btn(self, _ev) -> None:
        self._dismiss_once("cancel")

    @on(Button.Pressed, "#btn-expunsaved-save")
    def _save_btn(self, _ev) -> None:
        self._dismiss_once("save")

    @on(Button.Pressed, "#btn-expunsaved-abandon")
    def _abandon_btn(self, _ev) -> None:
        self._dismiss_once("abandon")

    def action_cancel(self) -> None:
        self._dismiss_once("cancel")


class SpellcheckModal(_OneShotDismissScreen, ModalScreen):
    """Spellcheck sidebar for the active entry's body. Lists
    misspellings + suggestions; per-row buttons replace, add-to-
    dictionary, or skip.

    Dismiss payload: ``(replacements, dict_additions)`` where
    ``replacements`` is a ``{original_word: chosen_replacement}`` map
    and ``dict_additions`` is a list of words to append to the user's
    custom dictionary. The caller applies both — this modal is purely
    UX, no persistence side-effects.
    """

    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "done",            "Done"),
        Binding("tab",    "app.focus_next",  "Next", show=False),
    ]

    DEFAULT_CSS = """
    #spell-dlg {
        width: 100; height: 36;
        background: $surface; border: solid $primary-darken-2;
        padding: 1 2; layout: vertical;
    }
    #spell-title {
        background: $primary-darken-2; color: $text;
        padding: 0 1; height: 1; text-align: center;
    }
    #spell-hint   { height: 1; color: $text-muted; margin-top: 1; }
    #spell-table  {
        height: 1fr; min-height: 8; margin-top: 1;
        border: solid $primary-darken-2;
    }
    #spell-suggestions { height: 1; color: $accent; margin-top: 1; }
    #spell-btns { height: 3; margin-top: 1; align: right middle; }
    #spell-btns Button { margin-right: 1; min-width: 14; }
    """

    def __init__(self,
                  misspellings: "list[tuple[str, list[str]]]") -> None:
        super().__init__()
        self._misspellings = list(misspellings)
        self._replacements: "dict[str, str]" = {}
        self._dict_additions: "list[str]" = []

    def compose(self) -> ComposeResult:
        with Vertical(id="spell-dlg"):
            yield Static(
                f" Spellcheck — {len(self._misspellings)} "
                f"unknown words ",
                id="spell-title",
            )
            yield Static(
                "[dim]Click a row to see suggestions. Replace applies "
                "the first suggestion; Add-to-dictionary keeps the word "
                "for future runs.[/]",
                id="spell-hint", markup=True,
            )
            yield DataTable(id="spell-table",
                              cursor_type="row",
                              zebra_stripes=True)
            yield Static(
                "[dim]Pick a row to see suggestions.[/]",
                id="spell-suggestions", markup=True,
            )
            with Horizontal(id="spell-btns"):
                yield Button("Replace", id="btn-spell-replace",
                              variant="primary", disabled=True)
                yield Button("Add to dictionary",
                              id="btn-spell-add", disabled=True)
                yield Button("Skip", id="btn-spell-skip", disabled=True)
                yield Button("Done", id="btn-spell-done")

    def on_mount(self) -> None:
        try:
            t = self.query_one("#spell-table", DataTable)
            t.add_columns("Word", "Suggestions", "Status")
        except NoMatches:
            return
        for word, sugs in self._misspellings:
            sug_str = ", ".join(sugs[:3]) if sugs else "—"
            t.add_row(
                word,
                Text(sug_str, no_wrap=True, overflow="ellipsis"),
                "",
                key=word,
            )
        if t.row_count > 0:
            try:
                t.focus()
            except Exception:
                pass

    @on(DataTable.RowSelected, "#spell-table")
    def _on_row(self, event) -> None:
        if event.row_key is None:
            return
        word = event.row_key.value or ""
        sugs = next(
            (s for w, s in self._misspellings if w == word), [],
        )
        try:
            sug_widget = self.query_one("#spell-suggestions", Static)
        except NoMatches:
            return
        if sugs:
            sug_widget.update(
                f"[accent]Suggestions for '{word}':[/] "
                f"{', '.join(sugs[:5])}",
            )
        else:
            sug_widget.update(
                f"[dim]No suggestions for '{word}'.[/]",
            )
        for bid in ("btn-spell-replace", "btn-spell-add",
                    "btn-spell-skip"):
            try:
                self.query_one(f"#{bid}", Button).disabled = False
            except NoMatches:
                pass

    @on(Button.Pressed, "#btn-spell-replace")
    def _replace(self, _ev) -> None:
        word, sugs = self._get_selected()
        if not word:
            return
        if not sugs:
            self.app.notify(
                "No suggestion to replace with.", severity="warning",
            )
            return
        self._replacements[word] = sugs[0]
        self._mark_done(word, f"→ {sugs[0]}")

    @on(Button.Pressed, "#btn-spell-add")
    def _add(self, _ev) -> None:
        word, _sugs = self._get_selected()
        if not word:
            return
        self._dict_additions.append(word.lower())
        self._mark_done(word, "+ dict")

    @on(Button.Pressed, "#btn-spell-skip")
    def _skip(self, _ev) -> None:
        word, _ = self._get_selected()
        if not word:
            return
        self._mark_done(word, "skipped")

    @on(Button.Pressed, "#btn-spell-done")
    def _done(self, _ev) -> None:
        self.action_done()

    def action_done(self) -> None:
        self.dismiss((self._replacements, self._dict_additions))

    def _get_selected(self) -> "tuple[str, list[str]]":
        try:
            t = self.query_one("#spell-table", DataTable)
        except NoMatches:
            return ("", [])
        if t.cursor_row < 0 or t.row_count == 0:
            return ("", [])
        try:
            row_key = t.coordinate_to_cell_key(
                _Coordinate(t.cursor_row, 0),
            ).row_key
            word = row_key.value or ""
        except Exception:
            return ("", [])
        sugs = next(
            (s for w, s in self._misspellings if w == word), [],
        )
        return (word, sugs)

    def _mark_done(self, word: str, status: str) -> None:
        try:
            t = self.query_one("#spell-table", DataTable)
            for i in range(t.row_count):
                try:
                    row_key = t.coordinate_to_cell_key(
                        _Coordinate(i, 0),
                    ).row_key
                except Exception:
                    continue
                if row_key.value == word:
                    try:
                        t.update_cell_at(_Coordinate(i, 2), status)
                    except Exception:
                        pass
                    if i + 1 < t.row_count:
                        try:
                            t.move_cursor(row=i + 1)
                        except Exception:
                            pass
                    break
        except NoMatches:
            pass


class SynthesisUnsavedChangesModal(ModalScreen):
    """Save / Abandon / Cancel prompt when leaving a dirty synthesis
    buffer. Mirrors the ExperimentUnsavedChangesModal pattern."""

    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    #suc-dlg {
        width: 60; height: 12;
        background: $surface; padding: 1 2;
        border: solid $primary-darken-2;
    }
    #suc-title {
        background: $warning; color: black;
        padding: 0 1; height: 1; text-align: center;
    }
    #suc-body { height: 1fr; margin-top: 1; }
    #suc-btns { height: 3; align: right middle; }
    #suc-btns Button { margin-left: 1; min-width: 12; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._dismissed: bool = False   # [INV-50]

    def _dismiss_once(self, payload) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    def compose(self) -> ComposeResult:
        with Vertical(id="suc-dlg"):
            yield Static(" Unsaved fragment ", id="suc-title")
            yield Static(
                "The current fragment has unsaved edits. "
                "Save before continuing?",
                id="suc-body",
            )
            with Horizontal(id="suc-btns"):
                yield Button("Save", id="btn-suc-save", variant="primary")
                yield Button("Abandon", id="btn-suc-abandon",
                              variant="error")
                yield Button("Cancel", id="btn-suc-cancel")

    def on_mount(self) -> None:
        try:
            self.query_one("#btn-suc-cancel", Button).focus()
        except NoMatches:
            pass

    @on(Button.Pressed, "#btn-suc-save")
    def _save(self, _) -> None:
        self._dismiss_once("save")

    @on(Button.Pressed, "#btn-suc-abandon")
    def _abandon(self, _) -> None:
        self._dismiss_once("abandon")

    @on(Button.Pressed, "#btn-suc-cancel")
    def _cancel_btn(self, _) -> None:
        self._dismiss_once(None)

    def action_cancel(self) -> None:
        self._dismiss_once(None)


class SynthesisReplaceDnaConfirmModal(ModalScreen):
    """Confirm before an 'Optimize → DNA' hand-off overwrites unsaved
    DNA-tab edits. Dismisses ``True`` (replace) or ``False`` (cancel)."""

    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    #srd-dlg {
        width: 62; height: auto;
        background: $surface; padding: 1 2;
        border: solid $warning;
    }
    #srd-title {
        background: $warning-darken-2; color: $text; padding: 0 1; height: 1; text-align: center;
    }
    #srd-body { margin: 1 0; }
    #srd-btns { height: 3; align: right middle; }
    #srd-btns Button { margin-left: 1; min-width: 12; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._dismissed: bool = False   # [INV-50]

    def _dismiss_once(self, payload) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    def compose(self) -> ComposeResult:
        with Vertical(id="srd-dlg"):
            yield Static(" Replace DNA fragment? ", id="srd-title")
            yield Static(
                "The DNA tab has unsaved edits. Optimizing this protein "
                "will replace them with the new CDS.",
                id="srd-body",
            )
            with Horizontal(id="srd-btns"):
                yield Button("Replace", id="btn-srd-replace",
                              variant="warning")
                yield Button("Cancel", id="btn-srd-cancel")

    def on_mount(self) -> None:
        try:
            self.query_one("#btn-srd-cancel", Button).focus()
        except NoMatches:
            pass

    @on(Button.Pressed, "#btn-srd-replace")
    def _replace(self, _) -> None:
        self._dismiss_once(True)

    @on(Button.Pressed, "#btn-srd-cancel")
    def _cancel_btn(self, _) -> None:
        self._dismiss_once(False)

    def action_cancel(self) -> None:
        self._dismiss_once(False)


class NewMotifModal(_OneShotDismissScreen, ModalScreen):
    """Add a new custom protein motif. Dismisses with
    ``{name, sequence, feature_type, color, description}`` on Save (the
    caller persists via `_protein_motif_upsert`), or None on Cancel."""

    _blocks_undo: bool = True
    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    #nm-dlg { width: 72; height: auto; max-height: 90%;
              background: $surface; border: solid $primary; padding: 1 2; }
    #nm-title { background: $primary-darken-2; color: $text; padding: 0 1;
                margin-bottom: 1; text-align: center; text-style: bold; }
    #nm-dlg Static.nm-label { color: $text-muted; margin-top: 1; }
    #nm-desc { height: 5; border: solid $primary-darken-2; }
    #nm-status { height: auto; margin-top: 1; color: $text-muted; }
    #nm-btns { height: 3; margin-top: 1; align: right middle; }
    #nm-btns Button { margin-right: 1; min-width: 12; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="nm-dlg"):
            yield Static(" New protein motif ", id="nm-title")
            yield Static("Name", classes="nm-label")
            yield Input(placeholder="e.g. My tag", id="nm-name")
            yield Static("Amino-acid sequence", classes="nm-label")
            yield Input(placeholder="e.g. HHHHHH", id="nm-seq")
            yield Static("Type", classes="nm-label")
            yield Input(value="Motif", id="nm-type")
            yield Static("Color (hex, optional)", classes="nm-label")
            yield Input(placeholder="#1E40AF", id="nm-color")
            yield Static("Description (optional)", classes="nm-label")
            yield TextArea(id="nm-desc")
            yield Static("", id="nm-status", markup=True)
            with Horizontal(id="nm-btns"):
                yield Button("Save", id="btn-nm-save", variant="primary")
                yield Button("Cancel", id="btn-nm-cancel")

    def _status(self, msg: str) -> None:
        try:
            self.query_one("#nm-status", Static).update(msg)
        except NoMatches:
            pass

    def _val(self, wid: str) -> str:
        try:
            return self.query_one(wid, Input).value.strip()
        except NoMatches:
            return ""

    @on(Button.Pressed, "#btn-nm-save")
    def _save(self, _) -> None:
        name = self._val("#nm-name")
        seq = self._val("#nm-seq").upper()
        if not name:
            self._status("[red]Name is required.[/red]")
            return
        if not seq:
            self._status("[red]Amino-acid sequence is required.[/red]")
            return
        bad = sorted({c for c in seq if c not in "ACDEFGHIKLMNPQRSTVWY*"})
        if bad:
            self._status(
                f"[red]Non-canonical amino acids: {''.join(bad)}[/red]")
            return
        try:
            desc = self.query_one("#nm-desc", TextArea).text.strip()
        except NoMatches:
            desc = ""
        self.dismiss({
            "name":         name,
            "sequence":     seq,
            "feature_type": self._val("#nm-type") or "Motif",
            "color":        self._val("#nm-color"),
            "description":  desc,
        })

    @on(Button.Pressed, "#btn-nm-cancel")
    def _cancel(self, _) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AminoAcidPickerModal(_OneShotDismissScreen, ModalScreen):
    """Tiny picker shown when the user clicks an AA in the Mutagenize
    preview. Returns the selected one-letter AA on dismiss, or None on
    cancel. The WT amino at the clicked position is filtered out so
    the user can't pick a no-op mutation."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    # 20 standard amino acids + stop. Ordered alphabetically by one-letter.
    _AA_CATALOG: list[tuple[str, str, str]] = [
        ("A", "Ala", "Alanine"),       ("C", "Cys", "Cysteine"),
        ("D", "Asp", "Aspartate"),     ("E", "Glu", "Glutamate"),
        ("F", "Phe", "Phenylalanine"), ("G", "Gly", "Glycine"),
        ("H", "His", "Histidine"),     ("I", "Ile", "Isoleucine"),
        ("K", "Lys", "Lysine"),        ("L", "Leu", "Leucine"),
        ("M", "Met", "Methionine"),    ("N", "Asn", "Asparagine"),
        ("P", "Pro", "Proline"),       ("Q", "Gln", "Glutamine"),
        ("R", "Arg", "Arginine"),      ("S", "Ser", "Serine"),
        ("T", "Thr", "Threonine"),     ("V", "Val", "Valine"),
        ("W", "Trp", "Tryptophan"),    ("Y", "Tyr", "Tyrosine"),
        ("*", "***", "Stop codon"),
    ]

    def __init__(self, position: int, wt_aa: str) -> None:
        super().__init__()
        self._position = position
        self._wt_aa    = (wt_aa or "").upper()
        self._choices: list[str] = [
            a for (a, _, _) in self._AA_CATALOG if a != self._wt_aa
        ]

    def compose(self) -> ComposeResult:
        with Vertical(id="aa-pick-box"):
            yield Static(f" Mutate {self._wt_aa}{self._position}  →  ? ",
                         id="aa-pick-title")
            yield Label("[dim]Pick the replacement amino acid. "
                        "Esc to cancel.[/dim]", markup=True)
            items: list = []
            for (a, tl, fn) in self._AA_CATALOG:
                if a == self._wt_aa:
                    continue
                items.append(ListItem(Label(
                    f"[bold]{a}[/bold]   {tl}   [dim]{fn}[/dim]",
                    markup=True,
                )))
            yield ListView(*items, id="aa-pick-list")
            with Horizontal(id="aa-pick-btns"):
                yield Button("Cancel  [Esc]", id="btn-aa-pick-cancel")

    def on_mount(self) -> None:
        try:
            self.query_one("#aa-pick-list", ListView).focus()
        except NoMatches:
            pass

    @on(ListView.Selected, "#aa-pick-list")
    def _selected(self, _event) -> None:
        lv = self.query_one("#aa-pick-list", ListView)
        if lv.index is None or lv.index >= len(self._choices):
            return
        self.dismiss(self._choices[lv.index])

    @on(Button.Pressed, "#btn-aa-pick-cancel")
    def _cancel_btn(self, _) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PrimerSaveModal(ModalScreen):
    """Prompt for per-oligo names + target primer collection after a
    Primer3 design run produces results.

    2026-05-21: replaces the two-step "fill name inputs then click
    Save to Library" flow. Pressing Design now auto-opens this modal
    so naming + collection routing happens in one place.

    Each oligo in ``oligos`` gets its own name `Input` (scrollable
    when N > visible rows, so a 6-primer mutagenesis set still fits).
    The collection `Select` lists every existing primer collection
    plus a "+ New collection…" sentinel that prompts the user for a
    fresh name. Defaults to the currently-active primer collection.

    Sacred — preserves user-typed names verbatim (no underscore-for-
    space substitution). See feedback_no_underscores_in_names.

    Dismiss payload:
      ``dict`` with keys ``names: list[str]`` and ``collection: str`` on Save.
      ``None`` on Cancel.
    """

    _blocks_undo: bool = True   # carries Inputs; pitfall #41 / [INV-41]

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    _NEW_COLLECTION_SENTINEL = "__new__"

    def __init__(self, oligos: "list[dict]",
                 *, default_collection: str = "") -> None:
        super().__init__()
        # Each oligo dict carries at minimum: default_name (str), label
        # (e.g. "Forward", "Reverse", "Inner Fwd"). Optional: sequence,
        # tm — shown in the right-hand hint column so the user knows
        # which oligo they're naming.
        self._oligos = oligos
        self._default_collection = default_collection or ""
        self._dismissed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="primer-save-dlg"):
            yield Static(" Save primers to library ",
                         id="primer-save-title")
            yield Label(
                f"Name each of the {len(self._oligos)} oligo"
                f"{'s' if len(self._oligos) != 1 else ''} below, "
                "then pick the primer collection. Spaces in names "
                "are preserved.",
                id="primer-save-help",
            )
            with VerticalScroll(id="primer-save-list"):
                for i, ol in enumerate(self._oligos):
                    label = str(ol.get("label") or f"Oligo {i+1}")
                    default = str(ol.get("default_name") or label)
                    tm = ol.get("tm")
                    seq = ol.get("sequence") or ""
                    hint_parts = []
                    if isinstance(tm, (int, float)):
                        hint_parts.append(f"Tm {tm:.1f}°C")
                    if seq:
                        hint_parts.append(f"{len(seq)} nt")
                    hint = " · ".join(hint_parts)
                    with Vertical(classes="primer-save-row"):
                        yield Label(f"[bold]{label}[/]"
                                    + (f"  [dim]{hint}[/]" if hint else ""),
                                    classes="primer-save-row-label",
                                    markup=True)
                        yield Input(
                            value=default,
                            placeholder=label,
                            id=f"primer-save-name-{i}",
                            classes="primer-save-name-input",
                        )
            yield Label("Save to primer collection:",
                        id="primer-save-coll-label")
            yield Select(
                options=self._collection_options(),
                value=self._default_collection
                      or Select.BLANK,
                id="primer-save-collection",
                allow_blank=True,
            )
            with Horizontal(id="primer-save-buttons"):
                yield Button("Save", id="primer-save-ok",
                             variant="primary")
                yield Button("Cancel", id="primer-save-cancel",
                             variant="default")

    def _collection_options(self) -> "list[tuple[str, str]]":
        """Build Select options: every existing primer collection +
        a "+ New collection…" sentinel that triggers a fresh-name
        sub-prompt on Save."""
        opts: list[tuple[str, str]] = []
        for c in _load_primer_collections():
            name = c.get("name")
            if isinstance(name, str) and name:
                opts.append((name, name))
        opts.append(("+ New collection…", self._NEW_COLLECTION_SENTINEL))
        return opts

    def _gather_names(self) -> "list[str] | None":
        names: list[str] = []
        for i in range(len(self._oligos)):
            try:
                inp = self.query_one(f"#primer-save-name-{i}", Input)
            except NoMatches:
                return None
            # SACRED: do NOT strip() or replace internal whitespace —
            # the user gets exactly what they typed. Leading/trailing
            # spaces are still trimmed via .strip() at the boundary
            # because they're almost always typos, not intent.
            val = inp.value.strip()
            if not val:
                self.app.notify(
                    f"Oligo {i+1}: name cannot be empty.",
                    severity="warning",
                )
                inp.focus()
                return None
            names.append(val)
        # Duplicate-name check within THIS batch — saving "F" + "F"
        # would collide downstream; surface here so the user can fix
        # it before the collision resolver fires.
        seen: set[str] = set()
        for n in names:
            if n in seen:
                self.app.notify(
                    f"Duplicate name '{n}' in this batch — "
                    "give each oligo a unique name.",
                    severity="warning",
                )
                return None
            seen.add(n)
        return names

    def _resolve_collection(self) -> "str | None":
        """Return the chosen primer-collection name, or None if the
        user cancelled the new-collection sub-prompt. Sentinel value
        triggers an inline NewCollectionNameModal."""
        try:
            sel = self.query_one("#primer-save-collection", Select)
        except NoMatches:
            return None
        val = sel.value
        if val == Select.BLANK or val is None:
            self.app.notify("Pick a primer collection.", severity="warning")
            return None
        if isinstance(val, str):
            return val
        return None

    @on(Button.Pressed, "#primer-save-ok")
    def _save_btn(self, _) -> None:
        names = self._gather_names()
        if names is None:
            return
        coll = self._resolve_collection()
        if coll is None:
            return
        if coll == self._NEW_COLLECTION_SENTINEL:
            # Inline sub-prompt: ask for a new collection name, then
            # commit. The new collection is created at commit time
            # (not now) so a cancel here doesn't litter empty
            # collections.
            def _on_new_name(new_name) -> None:
                if not isinstance(new_name, str) or not new_name.strip():
                    return
                self._commit(names, new_name.strip(), create=True)
            self.app.push_screen(
                _PrimerCollectionNameModal(taken=self._existing_coll_names()),
                callback=_on_new_name,
            )
            return
        self._commit(names, coll, create=False)

    def _existing_coll_names(self) -> "set[str]":
        out: set[str] = set()
        for c in _load_primer_collections():
            name = c.get("name")
            if isinstance(name, str):
                out.add(name)
        return out

    def _commit(self, names: "list[str]", collection: str,
                *, create: bool) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss({
            "names":      names,
            "collection": collection,
            "create":     create,
        })

    @on(Button.Pressed, "#primer-save-cancel")
    def _cancel_btn(self, _) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(None)

    def action_cancel(self) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(None)


class _PrimerCollectionNameModal(ModalScreen):
    """Sub-prompt: ask the user for a new primer-collection name when
    they pick "+ New collection…" in the save modal. Light-weight
    inline prompt — full collection-management lives elsewhere."""

    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, *, taken: "set[str]") -> None:
        super().__init__()
        self._taken = taken
        self._dismissed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="primer-coll-name-dlg"):
            yield Static(" New primer collection ",
                         id="primer-coll-name-title")
            yield Label("Name (spaces allowed):")
            yield Input(placeholder="e.g. 'PCR validation primers'",
                        id="primer-coll-name-input")
            yield Static("", id="primer-coll-name-status", markup=True)
            with Horizontal(id="primer-coll-name-buttons"):
                yield Button("Create", id="primer-coll-name-ok",
                             variant="primary")
                yield Button("Cancel", id="primer-coll-name-cancel",
                             variant="default")

    @on(Input.Changed, "#primer-coll-name-input")
    def _on_changed(self, event: Input.Changed) -> None:
        val = (event.value or "").strip()
        try:
            status = self.query_one("#primer-coll-name-status", Static)
        except NoMatches:
            return
        if not val:
            status.update("")
        elif val in self._taken:
            status.update("[yellow]Name already in use.[/]")
        else:
            status.update("[green]OK[/]")

    @on(Button.Pressed, "#primer-coll-name-ok")
    def _ok(self, _) -> None:
        if self._dismissed:
            return
        try:
            val = self.query_one("#primer-coll-name-input", Input).value.strip()
        except NoMatches:
            val = ""
        if not val:
            self.app.notify("Enter a name.", severity="warning")
            return
        if val in self._taken:
            self.app.notify("Name already in use.", severity="warning")
            return
        self._dismissed = True
        self.dismiss(val)

    @on(Button.Pressed, "#primer-coll-name-cancel")
    def _cancel_btn(self, _) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(None)

    def action_cancel(self) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(None)


class PrimerTypePickerModal(ModalScreen):
    """Pick which mode the Primer Designer should open in.

    Pushed from `action_open_primer_design`; on dismiss with one
    of `{"detection", "cloning", "goldenbraid", "generic"}`, the
    designer opens with that tab active. None on Cancel.
    """

    _blocks_undo: bool = True

    DEFAULT_CSS = """
    #primer-type-dlg {
        width: 64;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: solid $accent;
        padding: 1 2;
    }
    #primer-type-title {
        width: 100%;
        height: 1;
        background: $accent;
        color: $surface;
        text-style: bold;
        content-align: center middle;
        padding: 0 1;
    }
    #primer-type-help {
        width: 100%;
        margin: 1 0;
        color: $text-muted;
        padding: 0 1;
    }
    #primer-type-buttons {
        width: 100%;
        height: auto;
        align: center middle;
    }
    #primer-type-buttons Button {
        width: 100%;
        margin: 0 0 1 0;
    }
    /* Mode-specific accents — green = create / start (Detection),
       cyan = cloning, orange = Golden Braid (matches the GB
       overhang block in the results legend), gray = Generic /
       Cancel = panel-lighten so it sits below the primaries. */
    #primer-type-detection { background: $success; }
    #primer-type-detection:hover { background: $success-lighten-1; }
    #primer-type-cloning { background: #4CC4FF; color: $surface; }
    #primer-type-cloning:hover { background: #80D8FF; }
    #primer-type-goldenbraid { background: #FFB347; color: $surface; }
    #primer-type-goldenbraid:hover { background: #FFCB80; }
    #primer-type-generic { background: $primary; }
    #primer-type-generic:hover { background: $primary-lighten-1; }
    #primer-type-cancel {
        background: $panel-lighten-2;
        margin-top: 1;
    }
    #primer-type-cancel:hover { background: $panel-lighten-3; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._dismissed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="primer-type-dlg"):
            yield Static(" What kind of primers? ",
                         id="primer-type-title")
            yield Label(
                "Pick the workflow. The Primer Designer opens "
                "on the matching tab; your selection (if any) "
                "auto-fills the Start/End coordinates — and if "
                "it spans a full feature, the Feature dropdown "
                "is selected so you can press Design directly.",
                id="primer-type-help",
            )
            with Vertical(id="primer-type-buttons"):
                yield Button("Detection  —  diagnostic PCR",
                             id="primer-type-detection",
                             variant="primary")
                yield Button("Cloning  —  RE tails + GCGC flap",
                             id="primer-type-cloning")
                yield Button("Golden Braid  —  L0 domestication",
                             id="primer-type-goldenbraid")
                yield Button("Generic  —  binding only",
                             id="primer-type-generic")
                yield Button("Cancel", id="primer-type-cancel",
                             variant="default")

    @on(Button.Pressed, "#primer-type-detection")
    def _pick_det(self, _) -> None:
        self._commit("detection")

    @on(Button.Pressed, "#primer-type-cloning")
    def _pick_clo(self, _) -> None:
        self._commit("cloning")

    @on(Button.Pressed, "#primer-type-goldenbraid")
    def _pick_gb(self, _) -> None:
        self._commit("goldenbraid")

    @on(Button.Pressed, "#primer-type-generic")
    def _pick_gen(self, _) -> None:
        self._commit("generic")

    @on(Button.Pressed, "#primer-type-cancel")
    def _cancel_btn(self, _) -> None:
        self.action_cancel()

    def _commit(self, mode: str) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(mode)

    def action_cancel(self) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(None)


class _PrimerCollectionDeleteConfirmModal(_OneShotDismissScreen, ModalScreen):
    """Confirm deleting a PRIMER collection. Default focus on [No] (handslip
    guard, like every destructive confirm in the app). Dismisses True (delete)
    or False (keep)."""

    _blocks_undo: bool = True   # [INV-50] destructive confirm
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab", "app.focus_next", "Next", show=False),
    ]
    DEFAULT_CSS = """
    _PrimerCollectionDeleteConfirmModal { align: center middle; }
    #pcolldel-dlg { width: 66; height: auto; background: $surface;
                    border: solid $error; padding: 1 2; }
    #pcolldel-title { background: $error; color: $text; padding: 0 1;
                      margin-bottom: 1; text-align: center; }
    #pcolldel-btns { height: 3; align: right middle; margin-top: 1; }
    #pcolldel-btns Button { margin-left: 2; }
    """

    def __init__(self, name: str, n_primers: int) -> None:
        super().__init__()
        self._name = name
        self._n = n_primers

    def compose(self) -> ComposeResult:
        from rich.markup import escape as _esc
        plural = "" if self._n == 1 else "s"
        with Vertical(id="pcolldel-dlg"):
            yield Static(" Delete primer collection ", id="pcolldel-title")
            yield Static(
                f"  Delete primer collection [bold]{_esc(self._name)}[/bold]?\n"
                f"  ({self._n} primer{plural})\n\n"
                f"  [dim]A backup is written to\n"
                f"  primer_collections.json.bak before the change.[/dim]",
                id="pcolldel-msg", markup=True)
            with Horizontal(id="pcolldel-btns"):
                yield Button("No", id="btn-pcolldel-no", variant="default")
                yield Button("Yes, delete", id="btn-pcolldel-yes",
                             variant="error")

    def on_mount(self) -> None:
        try:
            self.query_one("#btn-pcolldel-no", Button).focus()
        except NoMatches:
            pass

    @on(Button.Pressed, "#btn-pcolldel-no")
    def _no(self, _) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#btn-pcolldel-yes")
    def _yes(self, _) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class _PrimerMoveCopyModal(_OneShotDismissScreen, ModalScreen):
    """Pick a destination primer collection to move / copy primers into.
    Dismisses with the chosen collection name (str) or None on cancel."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab", "app.focus_next", "Next", show=False),
    ]
    DEFAULT_CSS = """
    _PrimerMoveCopyModal { align: center middle; }
    #pmove-box { width: 60; height: auto; max-height: 80%;
                 background: $surface; border: solid $accent; padding: 1 2; }
    #pmove-title { background: $accent-darken-2; color: $text; padding: 0 1; margin-bottom: 1; text-align: center; }
    #pmove-table { height: 14; margin-bottom: 1; }
    #pmove-btns { height: 3; align: right middle; }
    """

    def __init__(self, mode: str, collections: "list[str]",
                 count: int) -> None:
        super().__init__()
        self._mode = mode
        self._collections = list(collections or [])
        self._count = count

    def compose(self) -> ComposeResult:
        verb = "Copy" if self._mode == "copy" else "Move"
        plural = "s" if self._count != 1 else ""
        with Vertical(id="pmove-box"):
            yield Static(f" {verb} {self._count} primer{plural} to… ",
                         id="pmove-title")
            yield DataTable(id="pmove-table", cursor_type="row")
            with Horizontal(id="pmove-btns"):
                yield Button("Cancel", id="btn-pmove-cancel")

    def on_mount(self) -> None:
        try:
            t = self.query_one("#pmove-table", DataTable)
        except NoMatches:
            return
        t.add_column("Destination collection")
        for name in self._collections:
            t.add_row(name, key=name)
        t.focus()

    @on(DataTable.RowSelected, "#pmove-table")
    def _row(self, event: DataTable.RowSelected) -> None:
        rk = event.row_key
        name = rk.value if rk else None
        if isinstance(name, str) and name:
            self.dismiss(name)

    @on(Button.Pressed, "#btn-pmove-cancel")
    def _cancel_btn(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AmpliconSaveModal(ModalScreen):
    """Name a PCR amplicon and pick the collection to save the linear
    fragment into. Dismisses with ``{"name": str, "collection": str}``
    on confirm, or ``None`` on cancel.

    Hardened:
      * ``_blocks_undo`` — the name Input holds focus; a stray Ctrl+Z
        under it would otherwise revert the canvas.
      * One-shot ``_dismissed`` guard — a double-fire ``Pressed`` (real
        terminals can emit two for one physical click) can't dismiss
        twice / double-save.
      * Empty / whitespace name → refused with a status line, never a
        silent save under a blank name.
      * The collection list is guaranteed non-empty: the caller passes
        the active collection, which is de-duped to the front so the
        ``Select`` (``allow_blank=False``) always mounts with a valid
        value even if `collections.json` is empty / unreadable.
    """

    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel",         "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    DEFAULT_CSS = """
    #ampsave-dlg {
        width: 70; height: auto; max-height: 90%;
        background: $surface; border: solid $primary; padding: 1 2;
    }
    #ampsave-title {
        background: $primary-darken-2; color: $text;
        padding: 0 1; margin-bottom: 1; text-align: center;
    }
    #ampsave-dlg Label   { color: $text-muted; margin-top: 1; }
    #ampsave-name        { margin-top: 1; }
    #ampsave-collection  { margin-top: 1; }
    #ampsave-status      { height: 1; margin-top: 1; }
    #ampsave-btns        { align: right middle;  height: 3; margin-top: 1; }
    #ampsave-btns Button { margin-right: 1; min-width: 10; }
    """

    def __init__(self, *, default_name: str,
                 collections: "list[str]",
                 active_collection: "str | None",
                 title: str = "Save amplicon to library",
                 name_label: str = "Amplicon name",
                 parts_bins: "list[str] | None" = None,
                 active_bin: "str | None" = None,
                 fragment_default_name: "str | None" = None,
                 fragment_collections: "list[str] | None" = None,
                 fragment_active_collection: "str | None" = None,
                 fragment_label: str = "Fragment name") -> None:
        super().__init__()
        # Title + name-label are parametrised (back-compat defaults) so the
        # same hardened name+collection dialog serves the PCR amplicon save
        # AND the Domesticator cloned-plasmid save (dual-save flow).
        self._title = (title or "Save amplicon to library").strip() \
            or "Save amplicon to library"
        self._name_label = (name_label or "Amplicon name").strip() \
            or "Amplicon name"
        self._default_name = (default_name or "").strip() or "PCR amplicon"
        # Guarantee the active collection is present + first so the
        # Select always has a valid value; de-dup while preserving
        # order. Empty / blank names dropped. Final fallback: "Default".
        names: list[str] = []
        for raw in [active_collection, *collections]:
            nm = (raw or "").strip()
            if nm and nm not in names:
                names.append(nm)
        if not names:
            names = ["Default"]
        self._collections = names
        self._active = names[0]
        # Optional parts-bin picker (dual-save: choose the part's bin in
        # the SAME dialog). None → no bin field, so the amplicon path is
        # byte-unchanged. Normalised like collections: active first,
        # de-duped, never empty.
        if parts_bins is None:
            self._parts_bins: "list[str] | None" = None
            self._active_bin: "str | None" = None
        else:
            bins: list[str] = []
            for raw in [active_bin, *parts_bins]:
                nm = (raw or "").strip()
                if nm and nm not in bins:
                    bins.append(nm)
            self._parts_bins = bins or ["Main Parts Bin"]
            self._active_bin = self._parts_bins[0]
        # Optional primed-fragment fields (dual-save, 2026-06-02): name +
        # place the LINEAR primed fragment independently of the clone.
        # None → no fragment row, so the amplicon / clone-only paths are
        # byte-unchanged. Collections normalised like the clone's: active
        # first, de-duped, never empty.
        self._fragment_label = (fragment_label or "Fragment name").strip() \
            or "Fragment name"
        if fragment_default_name is None:
            self._fragment_default_name: "str | None" = None
            self._fragment_collections: "list[str]" = []
            self._fragment_active: "str | None" = None
        else:
            self._fragment_default_name = (
                (fragment_default_name or "").strip() or "FRAG-fragment")
            f_src = (fragment_collections
                     if fragment_collections is not None else collections)
            f_names: list[str] = []
            for raw in [fragment_active_collection, *(f_src or [])]:
                nm = (raw or "").strip()
                if nm and nm not in f_names:
                    f_names.append(nm)
            self._fragment_collections = f_names or list(self._collections)
            self._fragment_active = self._fragment_collections[0]
        self._dismissed = False

    def _dismiss_once(self, result) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(result)

    def compose(self) -> ComposeResult:
        with Vertical(id="ampsave-dlg"):
            yield Static(f" {self._title} ", id="ampsave-title")
            yield Label(f"{self._name_label}:")
            yield Input(value=self._default_name, id="ampsave-name",
                          placeholder=self._name_label)
            yield Label("Save to collection:")
            yield Select(
                [(n, n) for n in self._collections],
                value=self._active,
                allow_blank=False,
                id="ampsave-collection",
            )
            if self._fragment_default_name is not None:
                yield Label(f"{self._fragment_label}:")
                yield Input(value=self._fragment_default_name,
                            id="ampsave-frag-name",
                            placeholder=self._fragment_label)
                yield Label("Save fragment to collection:")
                yield Select(
                    [(n, n) for n in self._fragment_collections],
                    value=self._fragment_active,
                    allow_blank=False,
                    id="ampsave-frag-collection",
                )
            if self._parts_bins is not None:
                yield Label("Store part in parts bin:")
                yield Select(
                    [(n, n) for n in self._parts_bins],
                    value=self._active_bin,
                    allow_blank=False,
                    id="ampsave-bin",
                )
            yield Static("", id="ampsave-status", markup=True)
            with Horizontal(id="ampsave-btns"):
                yield Button("Save", id="btn-ampsave-ok", variant="primary")
                yield Button("Cancel", id="btn-ampsave-cancel")

    def on_mount(self) -> None:
        try:
            self.query_one("#ampsave-name", Input).focus()
        except NoMatches:
            pass

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#ampsave-status", Static).update(msg)
        except NoMatches:
            pass

    @on(Button.Pressed, "#btn-ampsave-ok")
    def _ok(self, _) -> None:
        self._submit()

    @on(Input.Submitted, "#ampsave-name")
    def _submitted(self, _) -> None:
        self._submit()

    def _submit(self) -> None:
        try:
            name = self.query_one("#ampsave-name", Input).value.strip()
            coll_w = self.query_one("#ampsave-collection", Select)
        except NoMatches:
            return
        if not name:
            self._set_status("[red]Enter an amplicon name.[/red]")
            return
        coll = coll_w.value
        if not isinstance(coll, str) or not coll.strip():
            coll = self._active
        result = {"name": name, "collection": coll}
        # Optional primed-fragment fields (dual-save). When the fragment
        # row is present its name must be non-empty too — never save a
        # fragment under a blank name (parity with the clone-name check).
        if self._fragment_default_name is not None:
            try:
                frag_name = self.query_one(
                    "#ampsave-frag-name", Input).value.strip()
            except NoMatches:
                frag_name = ""
            if not frag_name:
                self._set_status("[red]Enter a fragment name.[/red]")
                return
            frag_coll = self._fragment_active
            try:
                fv = self.query_one("#ampsave-frag-collection", Select).value
                if isinstance(fv, str) and fv.strip():
                    frag_coll = fv
            except NoMatches:
                pass
            result["frag_name"] = frag_name
            result["frag_collection"] = frag_coll or ""
        # Optional parts-bin choice (dual-save). Absent → caller keeps
        # the active bin.
        if self._parts_bins is not None:
            chosen_bin = self._active_bin
            try:
                bv = self.query_one("#ampsave-bin", Select).value
                if isinstance(bv, str) and bv.strip():
                    chosen_bin = bv
            except NoMatches:
                pass
            result["bin"] = chosen_bin or ""
        self._dismiss_once(result)

    @on(Button.Pressed, "#btn-ampsave-cancel")
    def _cancel_btn(self, _) -> None:
        self._dismiss_once(None)

    def action_cancel(self) -> None:
        self._dismiss_once(None)


class UnsavedNavigateModal(ModalScreen):
    """Shown when the user tries to navigate (e.g. Back to Collections)
    with unsaved edits. Sibling of `UnsavedQuitModal` — kept separate
    because the button labels and verb differ ("go back" vs "quit"),
    and the wording matters for users to understand the consequence.

    Dismisses with ``"save"`` (caller saves then proceeds), ``"discard"``
    (caller reverts the in-memory record from the library copy then
    proceeds), or ``None`` (cancel — stay).
    """

    _blocks_undo: bool = True   # [INV-50] destructive confirm — Ctrl+Z above

    BINDINGS = [
        Binding("escape", "cancel",     "Cancel"),
        Binding("tab",    "app.focus_next", "Next button", show=False),
    ]

    def __init__(self, action_phrase: str = "leave"):
        super().__init__()
        self._action_phrase = action_phrase
        self._dismissed: bool = False   # [INV-50]

    def _dismiss_once(self, payload) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    def compose(self) -> ComposeResult:
        with Vertical(id="navunsv-dlg"):
            yield Static(" Unsaved Changes ", id="navunsv-title")
            yield Static(
                f"  The loaded plasmid has unsaved edits.\n"
                f"  Save before you {self._action_phrase}?",
                id="navunsv-msg",
            )
            with Horizontal(id="navunsv-btns"):
                yield Button("Save",            id="btn-navunsv-save",
                             variant="primary")
                yield Button("Discard Changes", id="btn-navunsv-discard",
                             variant="error")
                yield Button("Cancel",          id="btn-navunsv-cancel")

    @on(Button.Pressed, "#btn-navunsv-save")
    def _save(self, _):     self._dismiss_once("save")

    @on(Button.Pressed, "#btn-navunsv-discard")
    def _discard(self, _):  self._dismiss_once("discard")

    @on(Button.Pressed, "#btn-navunsv-cancel")
    def _cancel_btn(self, _): self._dismiss_once(None)

    def action_cancel(self): self._dismiss_once(None)


class AnnotationTransferModal(_OneShotDismissScreen, ModalScreen):
    """Preview + confirm annotation transfer from a source plasmid
    onto the loaded record.

    Built from a list of transfer dicts produced by
    `_find_annotation_transfers` — the table shows feature label /
    target coords / strand / length so the user can sanity-check
    before any features land on their construct. "Apply all" adds
    every listed feature to the current record (the matcher already
    skipped exact-coord duplicates, so re-running on an already-
    annotated target is a no-op for visible duplicates).

    Dismiss payload:
      None       — cancelled
      list[dict] — the transfers the user accepted (same shape the
                   modal received). Caller turns them into SeqFeatures
                   and `_apply_record`s the result.
    """

    # Sweep #26: Apply-all triggers `_apply_record` (canvas mutation
    # + history push). Block app-level Ctrl+Z so a stray keystroke
    # over the preview doesn't roll back the canvas.
    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel",         "Cancel"),
        Binding("tab",    "app.focus_next", "Next",   show=False),
    ]

    def __init__(self, source_label: str, target_label: str,
                 transfers: list[dict]) -> None:
        super().__init__()
        self._source_label = source_label
        self._target_label = target_label
        self._transfers    = list(transfers)

    def compose(self) -> ComposeResult:
        with Vertical(id="annot-box"):
            yield Static(" Transfer annotations ", id="annot-title")
            yield Label(
                f"From: {self._source_label}    →    "
                f"Into: {self._target_label}"
            )
            yield Label(
                f"{len(self._transfers)} feature(s) matched by sequence"
                if self._transfers
                else "No features matched. Sequences too divergent?"
            )
            yield DataTable(id="annot-table", cursor_type="row",
                            zebra_stripes=True)
            with Horizontal(id="annot-btns"):
                yield Button("Apply all", id="btn-annot-apply",
                             variant="primary",
                             disabled=not self._transfers)
                yield Button("Cancel", id="btn-annot-cancel")

    def on_mount(self) -> None:
        try:
            t = self.query_one("#annot-table", DataTable)
        except NoMatches:
            return
        t.add_columns("Label", "Type", "Target start",
                      "Target end", "Strand", "Length (bp)")
        for tr in self._transfers:
            # Display is GenBank-style 1-based inclusive. Internal
            # coords are 0-based half-open: start += 1, end stays
            # (exclusive end == inclusive end numerically). Wrap
            # features (target_end < target_start) get a "(wrap)"
            # tag so the user can tell origin-spanning at a glance.
            wrap = (tr["target_end"] < tr["target_start"])
            start_disp = f"{tr['target_start'] + 1:,}"
            end_disp   = (f"{tr['target_end']:,} (wrap)"
                          if wrap else f"{tr['target_end']:,}")
            t.add_row(
                Text(tr["label"], no_wrap=True, overflow="ellipsis"),
                tr["type"],
                start_disp,
                end_disp,
                "+" if tr["target_strand"] == 1 else "-",
                f"{tr['length']:,}",
            )

    @on(Button.Pressed, "#btn-annot-apply")
    def _apply(self, _) -> None:
        self.dismiss(self._transfers)

    @on(Button.Pressed, "#btn-annot-cancel")
    def _cancel_btn(self, _) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class LargeFileConfirmModal(ModalScreen):
    """Confirm before loading a large record (file or NCBI fetch).

    Shown whenever the on-disk size or the parsed sequence length
    crosses the "this is going to take a noticeable while" threshold.
    Default focus is on **No** so a tap of Enter (or Esc) bails — the
    user has to deliberately reach for Yes to commit. Dismisses True
    (load) or False (cancel).

    `description` is a short context line that explains what's about
    to load (file path, accession, member name) so the user can tell
    "yes, this is the chromosome I asked for" from "wait, that's not
    what I expected".
    """

    # Sweep #26: loading a 50+ MB file is destructive to the canvas
    # state — block app-level Ctrl+Z while the user reads the prompt.
    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel",         "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    DEFAULT_CSS = """
    #lfc-dlg {
        width: 72; height: auto; max-height: 60%;
        background: #1c1c1c; border: solid $warning; padding: 1 2;
    }
    #lfc-title { background: $warning-darken-2; color: $text;
                  padding: 0 1; margin-bottom: 1; text-align: center; }
    #lfc-msg   { margin-bottom: 1; }
    #lfc-btns  { align: center middle;  height: 3; margin-top: 1; }
    #lfc-btns Button { margin-right: 1; min-width: 14; }
    """

    def __init__(self, description: str,
                 size_text: str = "",
                 *, threshold_text: str = "") -> None:
        super().__init__()
        self.description    = description
        self.size_text      = size_text
        self.threshold_text = threshold_text
        self._dismissed: bool = False   # [INV-50]

    def _dismiss_once(self, payload: bool) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    def compose(self) -> ComposeResult:
        from rich.markup import escape as _esc
        with Vertical(id="lfc-dlg"):
            yield Static(" Large record — confirm load ", id="lfc-title")
            body  = f"  About to load:\n  [bold]{_esc(self.description)}[/]\n"
            if self.size_text:
                body += f"\n  Size: {_esc(self.size_text)}\n"
            if self.threshold_text:
                body += f"  [dim]{_esc(self.threshold_text)}[/]\n"
            body += (
                "\n  Loading large records can take several seconds and use "
                "significant memory. Continue?"
            )
            yield Static(body, id="lfc-msg", markup=True)
            with Horizontal(id="lfc-btns"):
                yield Button("No (default)", id="btn-lfc-no",
                              variant="default")
                yield Button("Yes, load",    id="btn-lfc-yes",
                              variant="warning")

    def on_mount(self) -> None:
        # Default focus is No so a stray Enter bails out rather than
        # commits to a multi-second load.
        self.query_one("#btn-lfc-no", Button).focus()

    @on(Button.Pressed, "#btn-lfc-no")
    def _no(self, _) -> None:
        self._dismiss_once(False)

    @on(Button.Pressed, "#btn-lfc-yes")
    def _yes(self, _) -> None:
        self._dismiss_once(True)

    def action_cancel(self) -> None:
        self._dismiss_once(False)


class CollectionDeleteConfirmModal(ModalScreen):
    """Confirm-on-delete modal for collections — different copy from
    LibraryDeleteConfirmModal (which talks about library entries).

    Default focus on [No] to protect against handslip-deletes.
    Dismisses True (delete) or False (keep)."""

    _blocks_undo: bool = True   # [INV-50] destructive confirm — Ctrl+Z above

    BINDINGS = [
        Binding("escape", "cancel",     "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    def __init__(self, name: str, n_plasmids: int) -> None:
        super().__init__()
        self.coll_name = name
        self.n_plas = n_plasmids
        # [INV-50] dismiss-once guard against double-click / double-Enter
        # races landing two `dismiss()` calls on a destructive modal.
        self._dismissed: bool = False

    def compose(self) -> ComposeResult:
        plural = "" if self.n_plas == 1 else "s"
        with Vertical(id="colldel-dlg"):
            yield Static(" Delete collection ", id="colldel-title")
            yield Static(
                f"  Delete collection [bold]{self.coll_name}[/bold]?\n"
                f"  ({self.n_plas} plasmid{plural})\n\n"
                f"  [dim]A backup is written to\n"
                f"  collections.json.bak before the change.[/dim]",
                id="colldel-msg", markup=True,
            )
            with Horizontal(id="colldel-btns"):
                yield Button("No",          id="btn-colldel-no",  variant="default")
                yield Button("Yes, delete", id="btn-colldel-yes", variant="error")

    def on_mount(self) -> None:
        self.query_one("#btn-colldel-no", Button).focus()

    def _dismiss_once(self, payload: bool) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    @on(Button.Pressed, "#btn-colldel-no")
    def _no(self, _) -> None:
        self._dismiss_once(False)

    @on(Button.Pressed, "#btn-colldel-yes")
    def _yes(self, _) -> None:
        self._dismiss_once(True)

    def action_cancel(self) -> None:
        self._dismiss_once(False)


class ScaryDeleteConfirmModal(ModalScreen):
    """Second-stage confirmation for collection delete — deliberately
    visually loud (red border + warning banner + emphatic copy) to make
    the user pause. Default focus on [No] like every confirm modal in
    the app. Dismisses True (delete) or False (keep)."""

    _blocks_undo: bool = True   # [INV-50] destructive confirm — Ctrl+Z above

    BINDINGS = [
        Binding("escape", "cancel",     "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    def __init__(self, name: str, n_plasmids: int) -> None:
        super().__init__()
        self.coll_name = name
        self.n_plas = n_plasmids
        # [INV-50] dismiss-once guard
        self._dismissed: bool = False

    def _dismiss_once(self, payload: bool) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    def compose(self) -> ComposeResult:
        plural = "" if self.n_plas == 1 else "s"
        with Vertical(id="scarydel-dlg"):
            yield Static(
                "  ⚠   ARE YOU ABSOLUTELY SURE?   ⚠  ",
                id="scarydel-title", markup=False,
            )
            yield Static(
                f"\n  This will [bold red]permanently delete[/bold red] the "
                f"collection\n"
                f"  [bold]{self.coll_name}[/bold] and its "
                f"[bold red]{self.n_plas} plasmid{plural}[/bold red].\n\n"
                f"  [yellow]The plasmids inside will also be removed from\n"
                f"  the library mirror.[/yellow]\n\n"
                f"  A backup of [italic]collections.json[/italic] is written "
                f"to\n"
                f"  [italic]collections.json.bak[/italic] in your data "
                f"directory —\n"
                f"  recover from it manually if you change your mind.\n",
                id="scarydel-msg", markup=True,
            )
            with Horizontal(id="scarydel-btns"):
                yield Button("No, keep it", id="btn-scarydel-no",
                             variant="default")
                yield Button("Yes, delete forever", id="btn-scarydel-yes",
                             variant="error")

    def on_mount(self) -> None:
        self.query_one("#btn-scarydel-no", Button).focus()

    @on(Button.Pressed, "#btn-scarydel-no")
    def _no(self, _) -> None:
        self._dismiss_once(False)

    @on(Button.Pressed, "#btn-scarydel-yes")
    def _yes(self, _) -> None:
        self._dismiss_once(True)

    def action_cancel(self) -> None:
        self._dismiss_once(False)


class MasterDeleteModal(_OneShotDismissScreen, ModalScreen):
    """Stage 1 of the Master Delete flow.

    Gates the destructive button behind a typed challenge: the user
    must type exactly ``YES`` (case-sensitive, no whitespace, no
    normalisation) into the Input field before the Delete button
    activates. Cancel is default-focused; Esc → cancel. Dismisses
    ``True`` (proceed to confirm modal) or ``False`` (abort).

    Design contract — this is the LEAST destructive of the two
    Master Delete modals. Even hitting "Delete" here only opens
    Stage 2; nothing on disk is touched until the Stage 2 button
    fires AFTER its 3 s cool-down expires.

    Sacred — must satisfy ALL of:
      * input matches `"YES"` byte-for-byte (case-sensitive); "yes",
        " YES", "YES ", "YESS", "" all keep Delete disabled.
      * Delete button starts disabled — no race where the user can
        click it before the first `Input.Changed` lands.
      * Cancel is the default-focused button so a stray Enter at the
        modal level cancels rather than commits.
      * `_blocks_undo = True` so app-level Ctrl+Z under the modal
        doesn't fire on whatever canvas is loaded underneath.
      * No keyboard binding for "delete" — only the visible button
        with an explicit click commits the user to Stage 2.
    """

    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel",         "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    DEFAULT_CSS = """
    #md-dlg {
        width: 78; height: auto; max-height: 90%;
        background: #1c1c1c; border: solid $error; padding: 1 2;
    }
    #md-title {
        background: $error-darken-1; color: $text;
        padding: 0 1; margin-bottom: 1; text-align: center;
    }
    #md-warn { margin-bottom: 1; }
    #md-scope-label { margin-top: 1; color: $warning; }
    #md-scope { color: $text; margin-bottom: 1; }
    #md-prompt { margin-top: 1; color: $warning; }
    #md-input { margin-bottom: 1; }
    #md-status { margin-bottom: 1; }
    #md-btns { align: center middle;  height: 3; margin-top: 1; }
    #md-btns Button { margin-right: 1; min-width: 22; }
    """

    # Exact required input. Sacred — DO NOT loosen to a casefold or
    # `.strip()` compare. A user typing "yes" hasn't deliberately
    # spelled out the affirmative; they've muscle-memoried it.
    _REQUIRED_INPUT = "YES"

    def __init__(self, *,
                  files_count: int,
                  dirs_count: int,
                  pre_update_present: bool) -> None:
        super().__init__()
        # Pre-computed scope counts. Caller (the action handler)
        # runs the enumeration once on the main thread before
        # pushing the modal so the user sees the same numbers in
        # both stages.
        self._files_count = int(files_count)
        self._dirs_count = int(dirs_count)
        self._pre_update_present = bool(pre_update_present)

    def compose(self) -> ComposeResult:
        with Vertical(id="md-dlg"):
            yield Static(
                "  ⚠   MASTER DELETE — WIPE ALL USER DATA   ⚠  ",
                id="md-title", markup=False,
            )
            yield Static(
                "  This will [bold red]permanently and "
                "irreversibly[/bold red] delete every plasmid,\n"
                "  collection, experiment, gel, primer, part, "
                "feature, grammar,\n"
                "  codon table, custom setting and saved "
                "preference in this\n"
                "  SpliceCraft data directory.\n\n"
                "  There is [bold red]no built-in restore[/bold red]. "
                "Pre-update snapshots,\n"
                "  daily backups, .bak files, and lost-entries "
                "spillover\n"
                "  will [bold red]all be wiped[/bold red] too — true "
                "clean slate.",
                id="md-warn", markup=True,
            )
            scope_lines = [
                f"  • {self._files_count} user-data file(s) (plus "
                "every `.bak` sibling)",
                f"  • {self._dirs_count} user-data directory tree(s)",
            ]
            if self._pre_update_present:
                scope_lines.append(
                    "  • Sibling [italic]pre-update-backups[/italic] "
                    "directory (your last recovery copy)"
                )
            scope_lines.append(
                "  • Rotated log backups (active log stays open)"
            )
            yield Static(
                "  Scope of this deletion:", id="md-scope-label",
            )
            yield Static(
                "\n".join(scope_lines),
                id="md-scope", markup=True,
            )
            yield Static(
                f"  To enable the Delete button, type exactly  "
                f"[bold]{self._REQUIRED_INPUT}[/bold]  (case-sensitive)\n"
                f"  in the field below. Anything else keeps the "
                f"button disabled.",
                id="md-prompt", markup=True,
            )
            yield Input(
                value="",
                placeholder='type "YES" (case-sensitive, no spaces) to enable',
                id="md-input",
            )
            yield Static(
                "[dim]Delete button stays disabled until input "
                "matches.[/dim]",
                id="md-status", markup=True,
            )
            with Horizontal(id="md-btns"):
                yield Button(
                    "Cancel (default)", id="btn-md-cancel",
                    variant="default",
                )
                yield Button(
                    "Delete (disabled)", id="btn-md-delete",
                    variant="error", disabled=True,
                )

    def on_mount(self) -> None:
        # Cancel is the default focus so a stray Enter at the modal
        # level fires Cancel, NOT Delete. The Input below grabs
        # focus only after we explicitly Tab into it — see
        # invariant on Cancel-default-focus across destructive modals.
        try:
            self.query_one("#btn-md-cancel", Button).focus()
        except NoMatches:
            pass

    @on(Input.Changed, "#md-input")
    def _on_input_changed(self, event: Input.Changed) -> None:
        # Strict byte-for-byte compare. No `.strip()`, no `.upper()`,
        # no `.casefold()` — the user must type the exact 3 chars.
        # If we ever loosened this it would defeat the whole point
        # of the typed challenge.
        try:
            btn = self.query_one("#btn-md-delete", Button)
            status = self.query_one("#md-status", Static)
        except NoMatches:
            return
        if event.value == self._REQUIRED_INPUT:
            btn.disabled = False
            btn.label = "Delete"
            status.update(
                "[bold green]✓ Match — Delete button enabled.[/bold green] "
                "[dim](next stage has a 3 s cool-down)[/dim]"
            )
        else:
            btn.disabled = True
            btn.label = "Delete (disabled)"
            if event.value == "":
                status.update(
                    "[dim]Delete button stays disabled until "
                    "input matches.[/dim]"
                )
            else:
                # Helpful but not too helpful — tell the user the
                # value didn't match without revealing the exact
                # mismatch (case, whitespace, extra chars).
                status.update(
                    "[red]✗ Doesn't match. Type the exact word "
                    f"[/red][bold]{self._REQUIRED_INPUT}[/bold]"
                    "[red] — case-sensitive, no spaces.[/red]"
                )

    @on(Button.Pressed, "#btn-md-cancel")
    def _cancel_btn(self, _) -> None:
        _log_event("masterdelete.stage1.cancel")
        self.dismiss(False)

    @on(Button.Pressed, "#btn-md-delete")
    def _delete_btn(self, _) -> None:
        # Final guard — even if a stale `Input.Changed` missed a
        # disable, re-check the value here so the only path that
        # dismisses True has a verified match. Pulls the live
        # widget value (not a stashed one) so a fast retype-and-
        # click sequence still validates the visible string.
        try:
            inp = self.query_one("#md-input", Input)
        except NoMatches:
            self.dismiss(False)
            return
        if inp.value != self._REQUIRED_INPUT:
            # Re-disable + status nudge.
            try:
                btn = self.query_one("#btn-md-delete", Button)
                btn.disabled = True
                btn.label = "Delete (disabled)"
            except NoMatches:
                pass
            try:
                self.query_one("#md-status", Static).update(
                    "[red]✗ Input no longer matches — type "
                    f"[/red][bold]{self._REQUIRED_INPUT}[/bold]"
                    "[red] exactly.[/red]"
                )
            except NoMatches:
                pass
            return
        _log_event("masterdelete.stage1.confirmed")
        self.dismiss(True)

    def action_cancel(self) -> None:
        _log_event("masterdelete.stage1.cancel", reason="esc")
        self.dismiss(False)


class MasterDeleteResultModal(_OneShotDismissScreen, ModalScreen):
    """Stage 3 — post-wipe summary + restart nudge. One button
    ("OK, I'll restart") so the user can't accidentally dismiss the
    confirmation while they're still reading. Dismisses ``None``.
    """

    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "ok", "OK"),
        Binding("enter",  "ok", "OK", show=False),
    ]

    DEFAULT_CSS = """
    #mdr-dlg {
        width: 70; height: auto; max-height: 70%;
        background: #1c1c1c; border: solid $accent; padding: 1 2;
    }
    #mdr-title {
        background: $accent-darken-2; color: $text;
        padding: 0 1; margin-bottom: 1; text-align: center;
    }
    #mdr-msg { margin-bottom: 1; }
    #mdr-btns { align: center middle;  height: 3; margin-top: 1; }
    #mdr-btns Button { margin-right: 1; min-width: 22; }
    """

    def __init__(self, summary: "dict[str, int | bool]") -> None:
        super().__init__()
        self._summary = dict(summary or {})

    def compose(self) -> ComposeResult:
        s = self._summary
        files = int(s.get("files_removed", 0) or 0)
        dirs = int(s.get("dirs_removed", 0) or 0)
        logs = int(s.get("log_files_removed", 0) or 0)
        residual_f = int(s.get("residual_files", 0) or 0)
        residual_d = int(s.get("residual_dirs", 0) or 0)
        errors = int(s.get("errors", 0) or 0)
        pre = bool(s.get("pre_update_removed", False))
        residual_line = (
            f"  [dim]+ {residual_f} extra file(s) and "
            f"{residual_d} extra dir(s) caught by the "
            "residual-sweep pass.[/dim]\n"
            if (residual_f or residual_d) else ""
        )
        err_line = (
            f"\n  [yellow]⚠ {errors} path(s) could not be removed "
            "— see the log file for details.[/yellow]"
            if errors else ""
        )
        with Vertical(id="mdr-dlg"):
            yield Static(
                "  Master Delete complete  ",
                id="mdr-title", markup=False,
            )
            yield Static(
                f"  [bold]{files}[/bold] user-data file(s) removed.\n"
                f"  [bold]{dirs}[/bold] directory tree(s) removed.\n"
                f"  [bold]{logs}[/bold] rotated log backup(s) "
                "removed.\n"
                f"  Pre-update backups: "
                + ("[bold]wiped[/bold]" if pre else "[dim]none "
                   "present[/dim]") +
                ".\n"
                f"{residual_line}"
                f"{err_line}\n\n"
                "  Some panels may still show stale state in this\n"
                "  session — quit and restart SpliceCraft for a\n"
                "  fully clean canvas.",
                id="mdr-msg", markup=True,
            )
            with Horizontal(id="mdr-btns"):
                yield Button(
                    "OK", id="btn-mdr-ok", variant="primary",
                )

    def on_mount(self) -> None:
        try:
            self.query_one("#btn-mdr-ok", Button).focus()
        except NoMatches:
            pass

    @on(Button.Pressed, "#btn-mdr-ok")
    def _ok(self, _) -> None:
        self.dismiss(None)

    def action_ok(self) -> None:
        self.dismiss(None)


class RenamePlasmidModal(_OneShotDismissScreen, ModalScreen):
    """Prompt for a new name for a library entry.

    Tab cycles between the Input and Save/Cancel buttons.
    Dismisses with the new name (a non-empty string) or None on cancel.
    Input validation (non-empty, trimmed, collision check) lives in the
    app-side handler — the modal just collects a value.
    """

    _blocks_undo: bool = True   # Input editing; result mutates library entry

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    def __init__(self, current_name: str, entry_id: str):
        super().__init__()
        self.current_name = current_name
        self.entry_id     = entry_id

    def compose(self) -> ComposeResult:
        from rich.markup import escape as _md_escape
        with Vertical(id="rename-dlg"):
            yield Static(" Rename plasmid ", id="rename-title")
            # Escape — `current_name` is user-controlled and Label
            # interprets Rich markup by default, so a name like
            # "TU [draft]" would otherwise eat the trailing bracket
            # as a malformed tag. Same hygiene as NamePlasmidModal's
            # status line + the History viewer's parent-name render.
            yield Label(
                f"Current name:  {_md_escape(self.current_name)}",
            )
            yield Label("New name:")
            yield Input(
                value=self.current_name,
                placeholder="enter a new name",
                id="rename-input",
            )
            yield Static("", id="rename-status", markup=True)
            with Horizontal(id="rename-btns"):
                yield Button("Save",   id="btn-rename-save",   variant="primary")
                yield Button("Cancel", id="btn-rename-cancel")

    def on_mount(self) -> None:
        # Default focus on the Input, text pre-selected via select_on_focus
        # (Textual Input defaults to selecting all when focused, which is
        # what you want for a rename — typing replaces the old name).
        inp = self.query_one("#rename-input", Input)
        inp.focus()

    @on(Button.Pressed, "#btn-rename-save")
    def _save(self, _):
        self._try_submit()

    @on(Input.Submitted, "#rename-input")
    def _submitted(self, _):
        self._try_submit()

    def _try_submit(self) -> None:
        new_name = self.query_one("#rename-input", Input).value.strip()
        status   = self.query_one("#rename-status", Static)
        if not new_name:
            status.update("[red]Name cannot be empty.[/red]")
            return
        if new_name == self.current_name:
            # No-op rename — treat as cancel so the app doesn't bother writing.
            self.dismiss(None)
            return
        self.dismiss(new_name)

    @on(Button.Pressed, "#btn-rename-cancel")
    def _cancel_btn(self, _):
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class MinPrimerBindingModal(_OneShotDismissScreen, ModalScreen):
    """Prompt for the `min_primer_binding` threshold (integer bp).

    Replaces the old preset-cycle action with a free-typed integer
    input. Validates against `_settings_validator_int_range(1, 60)` —
    the same range the agent endpoint enforces — so the modal can't
    persist a value the loader would reject on next launch. Dismisses
    with the chosen integer (1–60) on submit, or `None` on cancel /
    no-change.
    """

    _blocks_undo: bool = True   # Input editing; result becomes settings value

    BINDINGS = [
        Binding("escape", "cancel",         "Cancel"),
        Binding("tab",    "app.focus_next", "Next",   show=False),
    ]

    DEFAULT_CSS = """
    #mpb-dlg {
        width: 56; height: auto;
        background: $surface; border: solid $primary; padding: 1 2;
    }
    #mpb-title  { background: $primary-darken-2; color: $text;
                  padding: 0 1; margin-bottom: 1; text-align: center; }
    #mpb-help   { height: auto; color: $text-muted; margin-bottom: 1; }
    #mpb-input  { margin-top: 0; margin-bottom: 1; }
    #mpb-status { height: 1; color: $text-muted; }
    #mpb-btns   { align: center middle;  height: 3; margin-top: 1; }
    #mpb-btns Button { margin-right: 1; }
    """

    _MIN_BP = 1
    _MAX_BP = 60

    def __init__(self, current_value: int) -> None:
        super().__init__()
        self.current_value = int(current_value)

    def compose(self) -> ComposeResult:
        with Vertical(id="mpb-dlg"):
            yield Static(" Minimum primer binding length ", id="mpb-title")
            yield Static(
                "Primers whose bound region is shorter than this "
                "threshold are flagged with a yellow ⚠ on the "
                "sequence panel. Allowed range: "
                f"{self._MIN_BP}–{self._MAX_BP} bp.",
                id="mpb-help",
            )
            yield Label("Threshold (bp):")
            yield Input(
                value=str(self.current_value),
                placeholder=f"{self._MIN_BP}–{self._MAX_BP}",
                id="mpb-input",
            )
            yield Static("", id="mpb-status", markup=True)
            with Horizontal(id="mpb-btns"):
                yield Button("Apply",  id="btn-mpb-apply",  variant="primary")
                yield Button("Cancel", id="btn-mpb-cancel")

    def on_mount(self) -> None:
        # Default focus on the input so the user can immediately type a
        # new value; the existing text is preselected (Textual default)
        # so a single keystroke replaces the displayed number.
        self.query_one("#mpb-input", Input).focus()

    @on(Button.Pressed, "#btn-mpb-apply")
    def _apply_btn(self, _) -> None:
        self._try_submit()

    @on(Input.Submitted, "#mpb-input")
    def _submitted(self, _) -> None:
        self._try_submit()

    def _try_submit(self) -> None:
        raw    = self.query_one("#mpb-input", Input).value.strip()
        status = self.query_one("#mpb-status", Static)
        if not raw:
            status.update("[red]Enter a value.[/red]")
            return
        try:
            value = int(raw)
        except ValueError:
            status.update("[red]Must be an integer.[/red]")
            return
        if not (self._MIN_BP <= value <= self._MAX_BP):
            status.update(
                f"[red]Out of range — pick "
                f"{self._MIN_BP}–{self._MAX_BP}.[/red]"
            )
            return
        if value == self.current_value:
            # No-op — treat as cancel so we don't bother re-stamping
            # primers + writing settings for an unchanged value.
            self.dismiss(None)
            return
        self.dismiss(value)

    @on(Button.Pressed, "#btn-mpb-cancel")
    def _cancel_btn(self, _) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PartsBinDeleteConfirmModal(_OneShotDismissScreen, ModalScreen):
    """Confirm-on-delete modal for the parts bin. Adapts its message,
    title, and primary-button label to the count of selected parts so
    the user always knows whether they're about to commit a single
    delete or a bulk delete.

    For single delete: shows the part name in bold.
    For multi delete: shows the count + a name preview (first 3, with
    "(+N more)" tail when over 3).

    Default focus on [No] so a stray Enter bails out — same handslip
    protection as `LibraryDeleteConfirmModal`. Dismisses True on
    confirm, False on cancel / Escape.
    """

    _blocks_undo: bool = True   # [INV-50] destructive confirm — Ctrl+Z above

    BINDINGS = [
        Binding("escape", "cancel",         "Cancel"),
        Binding("tab",    "app.focus_next", "Next button", show=False),
    ]

    DEFAULT_CSS = """
    #partsdel-dlg {
        width: 76; max-width: 95%; min-width: 56;
        height: auto; max-height: 90%;
        background: $surface; border: solid $error; padding: 1 2;
    }
    #partsdel-title { background: $error-darken-2; color: $text;
                      padding: 0 1; margin-bottom: 1; text-align: center; }
    #partsdel-msg   { height: auto; margin-bottom: 1; }
    #partsdel-btns  { align: center middle;  height: 3; margin-top: 1; }
    #partsdel-btns Button { margin-right: 1; min-width: 16; }
    """

    def __init__(self, names: list[str]) -> None:
        super().__init__()
        self._names = list(names)

    def compose(self) -> ComposeResult:
        from rich.markup import escape as _esc
        n = len(self._names)
        # Preview: first three names with markup escaped — a part
        # called `[red]boom[/]` shouldn't reformat the dialog.
        preview = ", ".join(_esc(name) for name in self._names[:3])
        if n > 3:
            preview += f" (+{n - 3} more)"
        if n == 1:
            title = " Remove part from bin "
            body = (
                f"  Remove [bold]{_esc(self._names[0])}[/bold] "
                f"from the parts bin?\n\n"
                f"  [dim]This cannot be undone from within the app. "
                f"A backup (.bak) of the parts-bin file is kept.[/dim]"
            )
            yes_label = "Yes, remove"
        else:
            # Bulk-delete: render the count in red bold so it's
            # impossible to miss before clicking Yes. The title +
            # button label echo the count too as belt + braces.
            title = f" Remove {n} parts from bin "
            body = (
                f"  Remove [bold red]{n}[/bold red] parts from "
                f"the parts bin?\n"
                f"  [dim]({preview})[/dim]\n\n"
                f"  [dim]This cannot be undone from within the app. "
                f"A backup (.bak) of the parts-bin file is kept.[/dim]"
            )
            yes_label = f"Yes, remove all {n}"
        with Vertical(id="partsdel-dlg"):
            yield Static(title, id="partsdel-title")
            yield Static(body, id="partsdel-msg", markup=True)
            with Horizontal(id="partsdel-btns"):
                yield Button("No",   id="btn-partsdel-no",
                              variant="default")
                yield Button(yes_label, id="btn-partsdel-yes",
                              variant="error")

    def on_mount(self) -> None:
        self.query_one("#btn-partsdel-no", Button).focus()

    @on(Button.Pressed, "#btn-partsdel-no")
    def _no(self, _) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#btn-partsdel-yes")
    def _yes(self, _) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ExactCopyConfirmModal(ModalScreen):
    """Prompt the user when a load batch contains exact duplicates of
    existing entries (same name AND same content). Two-way choice:

      * "Skip duplicates" — drop the duplicates from the load; existing
        entries unchanged. Default focus.
      * "Keep as COPY"    — append " COPY" (or " COPY 2", etc.) to each
        duplicate's name so it lands alongside the existing entry.

    Default focus on Skip so a stray Enter on splash dismiss doesn't
    silently double-add data. Generic across subsystems — used by parts
    bin, plasmid library, primers, experiments, collections.

    Dismiss payload:
      True  — Keep as COPY (caller renames + saves)
      False — Skip duplicates (caller drops them from the batch)
      None  — Escape (treated as skip, same as False)

    See ``_classify_collisions`` for the upstream pure classifier and
    ``_ensure_unique_copy_name`` for the rename helper.
    """

    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel",         "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    DEFAULT_CSS = """
    #copydlg-dlg {
        width: 80; max-width: 95%; min-width: 60;
        height: auto; max-height: 90%;
        background: $surface; border: solid $warning; padding: 1 2;
    }
    #copydlg-title { background: $warning-darken-2; color: $text;
                     padding: 0 1; margin-bottom: 1; text-align: center; }
    #copydlg-msg   { height: auto; margin-bottom: 1; }
    #copydlg-btns  { height: 3; margin-top: 1; align: right middle; }
    #copydlg-btns Button { margin-left: 1; min-width: 18; }
    """

    def __init__(self, entity: str, names: "list[str]") -> None:
        """``entity`` — singular noun for the modal copy ("part",
        "plasmid", "primer", "experiment", "collection"). ``names`` —
        list of duplicate entry names; first three are previewed.
        """
        super().__init__()
        self._entity = entity
        self._names  = list(names)
        # Sweep #10 (2026-05-20): rapid double-click between
        # button-press and the async pop_screen completing can fire
        # the same handler twice with a stale `dismiss` call slipping
        # through. `_dismissed` gates every exit path so the modal
        # callback is invoked exactly once.
        self._dismissed: bool = False

    def compose(self) -> ComposeResult:
        from rich.markup import escape as _esc
        n = len(self._names)
        preview = ", ".join(_esc(name) for name in self._names[:3])
        if n > 3:
            preview += f" (+{n - 3} more)"
        ent = _esc(self._entity)
        plural = "" if n == 1 else "s"
        body = (
            f"  [bold red]{n}[/bold red] {ent}{plural} in this load "
            f"match {'an existing entry' if n == 1 else 'existing entries'} "
            f"by both name AND content (exact duplicate"
            f"{plural}).\n"
            f"  [dim]({preview})[/dim]\n\n"
            f"  • [b]Skip[/b] — drop the duplicate"
            f"{plural} from the load (default).\n"
            f"  • [b]Keep as COPY[/b] — append [b]“COPY”[/b] "
            f"to the name so {'it' if n == 1 else 'they'} can coexist "
            f"alongside the original{plural}.\n"
        )
        with Vertical(id="copydlg-dlg"):
            yield Static(
                f" Exact duplicate{plural} detected ", id="copydlg-title",
            )
            yield Static(body, id="copydlg-msg", markup=True)
            with Horizontal(id="copydlg-btns"):
                yield Button(
                    f"Skip duplicate{plural}",
                    id="btn-copydlg-skip", variant="default",
                )
                yield Button(
                    "Keep as COPY",
                    id="btn-copydlg-keep", variant="warning",
                )

    def on_mount(self) -> None:
        try:
            self.query_one("#btn-copydlg-skip", Button).focus()
        except NoMatches:
            pass

    def _dismiss_once(self, payload) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    @on(Button.Pressed, "#btn-copydlg-skip")
    def _skip(self, _) -> None:
        _log_event(
            "collision.exact_copy.dismiss",
            entity=self._entity, n=len(self._names), choice="skip",
        )
        self._dismiss_once(False)

    @on(Button.Pressed, "#btn-copydlg-keep")
    def _keep(self, _) -> None:
        _log_event(
            "collision.exact_copy.dismiss",
            entity=self._entity, n=len(self._names), choice="keep",
        )
        self._dismiss_once(True)

    def action_cancel(self) -> None:
        _log_event(
            "collision.exact_copy.dismiss",
            entity=self._entity, n=len(self._names), choice="esc",
        )
        self._dismiss_once(False)


class NameCollisionModal(ModalScreen):
    """Prompt the user when a load batch contains entries whose name
    matches an existing entry but whose CONTENT differs. Three-way
    choice:

      * "Keep original"  — drop the new entries; existing entries
        unchanged. Default focus (safest — preserves user data).
      * "Overwrite"      — replace existing entries with the new content
        (matched by name).
      * "Cancel load"    — abort the entire load; nothing saved on
        either side.

    Used across subsystems (parts bin, plasmid library, primers,
    experiments, collections, gels) so a "load with a familiar name but
    different bytes" never silently clobbers or silently drops data.

    Dismiss payload (str):
      "keep"      — keep originals; new entries dropped
      "overwrite" — replace existing with new
      "cancel"    — abort the whole load (caller does NOTHING)
      None        — Escape (treated as cancel)

    See ``_classify_collisions`` for the upstream pure classifier.
    """

    _blocks_undo: bool = True

    BINDINGS = [
        Binding("escape", "cancel",         "Cancel"),
        Binding("tab",    "app.focus_next", "Next", show=False),
    ]

    DEFAULT_CSS = """
    #namecoll-dlg {
        width: 84; max-width: 95%; min-width: 64;
        height: auto; max-height: 90%;
        background: $surface; border: solid $warning; padding: 1 2;
    }
    #namecoll-title { background: $warning-darken-2; color: $text;
                      padding: 0 1; margin-bottom: 1; text-align: center; }
    #namecoll-msg   { height: auto; margin-bottom: 1; }
    #namecoll-btns  { height: 3; margin-top: 1; align: right middle; }
    #namecoll-btns Button { margin-left: 1; min-width: 18; }
    """

    def __init__(self, entity: str, names: "list[str]") -> None:
        """``entity`` — singular noun for the modal copy ("part",
        "plasmid", "primer", "experiment", "collection"). ``names`` —
        list of colliding entry names; first three are previewed.
        """
        super().__init__()
        self._entity = entity
        self._names  = list(names)
        # Sweep #10 (2026-05-20): double-fire guard — see
        # ExactCopyConfirmModal for rationale.
        self._dismissed: bool = False

    def compose(self) -> ComposeResult:
        from rich.markup import escape as _esc
        n = len(self._names)
        preview = ", ".join(_esc(name) for name in self._names[:3])
        if n > 3:
            preview += f" (+{n - 3} more)"
        ent = _esc(self._entity)
        plural = "" if n == 1 else "s"
        body = (
            f"  [bold red]{n}[/bold red] {ent}{plural} in this load "
            f"share a name with {'an existing entry' if n == 1 else 'existing entries'} "
            f"but the [b]content is different[/b].\n"
            f"  [dim]({preview})[/dim]\n\n"
            f"  • [b]Keep original[/b] — drop the new "
            f"{'entry' if n == 1 else 'entries'}; existing data unchanged "
            f"(default).\n"
            f"  • [b]Overwrite[/b] — replace the existing "
            f"{'entry' if n == 1 else 'entries'} with the new content.\n"
            f"  • [b]Cancel[/b] — abort the entire load; nothing saved.\n"
        )
        with Vertical(id="namecoll-dlg"):
            yield Static(
                f" Name collision{plural} (different content) ",
                id="namecoll-title",
            )
            yield Static(body, id="namecoll-msg", markup=True)
            with Horizontal(id="namecoll-btns"):
                yield Button(
                    "Keep original",
                    id="btn-namecoll-keep", variant="default",
                )
                yield Button(
                    "Overwrite",
                    id="btn-namecoll-overwrite", variant="warning",
                )
                yield Button(
                    "Cancel load",
                    id="btn-namecoll-cancel", variant="error",
                )

    def on_mount(self) -> None:
        try:
            self.query_one("#btn-namecoll-keep", Button).focus()
        except NoMatches:
            pass

    def _dismiss_once(self, payload: str) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    @on(Button.Pressed, "#btn-namecoll-keep")
    def _keep(self, _) -> None:
        _log_event(
            "collision.name_collision.dismiss",
            entity=self._entity, n=len(self._names), choice="keep",
        )
        self._dismiss_once("keep")

    @on(Button.Pressed, "#btn-namecoll-overwrite")
    def _overwrite(self, _) -> None:
        _log_event(
            "collision.name_collision.dismiss",
            entity=self._entity, n=len(self._names),
            choice="overwrite",
        )
        self._dismiss_once("overwrite")

    @on(Button.Pressed, "#btn-namecoll-cancel")
    def _cancel_btn(self, _) -> None:
        _log_event(
            "collision.name_collision.dismiss",
            entity=self._entity, n=len(self._names), choice="cancel",
        )
        self._dismiss_once("cancel")

    def action_cancel(self) -> None:
        _log_event(
            "collision.name_collision.dismiss",
            entity=self._entity, n=len(self._names), choice="esc",
        )
        self._dismiss_once("cancel")


class LibraryDeleteConfirmModal(ModalScreen):
    """Generic delete-confirmation modal. Used by the plasmid library,
    primer library, and any future list that needs handslip protection.

    Default focus is on [No]. Tab cycles between [No] and [Yes, remove].
    Escape dismisses as False (cancel).
    """

    _blocks_undo: bool = True   # [INV-50] destructive confirm — Ctrl+Z above

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab",    "app.focus_next", "Next button", show=False),
    ]

    def __init__(self, name: str, size: int, entry_id: str):
        super().__init__()
        self.entry_name = name
        self.entry_size = size
        self.entry_id   = entry_id
        # [INV-50] dismiss-once guard
        self._dismissed: bool = False

    def _dismiss_once(self, payload: bool) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.dismiss(payload)

    def compose(self) -> ComposeResult:
        size_str = f" ({self.entry_size:,} bp)" if self.entry_size > 0 else ""
        with Vertical(id="libdel-dlg"):
            yield Static(" Remove from library ", id="libdel-title")
            yield Static(
                f"  Remove [bold]{self.entry_name}[/bold]"
                f"{size_str} from the library?\n\n"
                f"  [dim]This cannot be undone from within the app.\n"
                f"  A backup (.bak) of the library file is kept.[/dim]",
                id="libdel-msg",
                markup=True,
            )
            with Horizontal(id="libdel-btns"):
                yield Button("No",           id="btn-libdel-no",  variant="default")
                yield Button("Yes, remove",  id="btn-libdel-yes", variant="error")

    def on_mount(self) -> None:
        self.query_one("#btn-libdel-no", Button).focus()

    @on(Button.Pressed, "#btn-libdel-no")
    def _no(self, _):
        self._dismiss_once(False)

    @on(Button.Pressed, "#btn-libdel-yes")
    def _yes(self, _):
        self._dismiss_once(True)

    def action_cancel(self) -> None:
        self._dismiss_once(False)
