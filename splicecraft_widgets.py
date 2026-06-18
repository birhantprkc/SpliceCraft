"""Self-contained Textual widget primitives (layer 3).

`_InstantPressButton` (fires Pressed on mouse-down, used by StrandPickerModal)
and the xterm-256 color grid (`_XtermColorGrid` + its `_xterm_index_to_hex` /
`_ANSI16_HEX` palette helpers, used by ColorPickerModal). Pure Textual/Rich
widgets -- no dependency on hub state, persistence, or any other splicecraft
module.
"""
from __future__ import annotations

import functools as _functools

from rich.text import Text
from textual.events import MouseDown
from textual.widgets import Button, Static


class _InstantPressButton(Button):
    """Button that fires `Pressed` on mouse-DOWN rather than
    waiting for the Click cycle to complete.

    Sweep #31 (2026-05-26): works around the Textual real-terminal
    bug where a click on a non-focused widget gets eaten by
    focus-transition, and `Button.Pressed` only fires on the
    SECOND press (the first lands during focus-shift, the second
    actually triggers Click). Pilot's synthetic clicks bypass
    that gate so the bug never surfaces in tests — but it does
    on a real WSL2 / xterm session. Posting `Pressed` on
    mouse-down + stopping the event means a single physical
    click always wins, regardless of which widget held focus
    beforehand.

    Used by `StrandPickerModal` so picking ▶ / ◀ / ▒ / ↔ is
    one-and-done. Drop-in replacement for `Button`; @on(Button
    .Pressed, ...) handlers in the owning modal still see the
    same `Pressed` message, so dispatch wiring is unchanged."""

    async def _on_mouse_down(self, event: MouseDown) -> None:
        if event.button != 1:
            return
        if self.disabled or not self.display:
            return
        event.stop()
        self.post_message(self.Pressed(self))


_ANSI16_HEX: list[str] = [
    "#000000", "#800000", "#008000", "#808000",
    "#000080", "#800080", "#008080", "#C0C0C0",
    "#808080", "#FF0000", "#00FF00", "#FFFF00",
    "#0000FF", "#FF00FF", "#00FFFF", "#FFFFFF",
]


@_functools.lru_cache(maxsize=256)
def _xterm_index_to_hex(idx: int) -> str:
    """Convert an xterm-256 color index (0..255) to the closest 24-bit RGB
    hex. Matches the xterm default palette — terminals may remap these but
    the vast majority follow the spec. Cube levels use the canonical
    ``[0, 95, 135, 175, 215, 255]`` ramp; grayscale uses
    ``8 + 10 * k`` for k in 0..23.

    LRU-cached at maxsize=256 (entire palette) — `_XtermColorGrid.render`
    calls this 256× per mount and the output is deterministic."""
    idx = max(0, min(255, int(idx)))
    if idx < 16:
        return _ANSI16_HEX[idx]
    if idx < 232:
        n = idx - 16
        levels = (0, 95, 135, 175, 215, 255)
        r = levels[(n // 36) % 6]
        g = levels[(n // 6)  % 6]
        b = levels[ n        % 6]
        return f"#{r:02X}{g:02X}{b:02X}"
    v = 8 + 10 * (idx - 232)
    return f"#{v:02X}{v:02X}{v:02X}"


class _XtermColorGrid(Static):
    """Single-Static replacement for the 256 individual color-cell
    Buttons that used to make up the xterm grid in ColorPickerModal.

    Profiling on a T480s baseline showed `ColorPickerModal.push_screen`
    settling in ~2 s, dominated by mounting 256 Buttons + iterating
    them in `on_mount` to set per-cell `styles.background`. Each
    Button is a full Textual widget with its own CSS pipeline; 256
    of them blow past the cost of any color picker should ever
    impose. This widget paints the entire grid as one Rich Text
    canvas (each cell = 3 spaces with a coloured background) and
    hit-tests clicks to a cell index via integer arithmetic — three
    orders of magnitude fewer widgets, ~30× faster modal mount.

    Layout (each cell 3 chars wide × 1 row tall, matches the
    legacy `.colorpick-xterm-cell` CSS rule):

      row 0       — 16 ANSI cells          (cells 0-15)
      rows 1..6   — 216-color cube         (cells 16-231, 36/row)
      row 7       — 24 grayscale cells     (cells 232-255)

    `cell_at(x, y)` is the inverse mapping used by
    `ColorPickerModal._cell_index_at` for click + drag.
    """

    DEFAULT_CSS = """
    _XtermColorGrid {
        height: 8;
        width: auto;
    }
    """

    _CELL_W = 3   # widget-relative pixel width per cell (matches old CSS)

    def render(self) -> Text:
        t = Text(no_wrap=True, overflow="crop")
        cell = " " * self._CELL_W
        # Row 0: 16 ANSI
        for i in range(16):
            t.append(cell, style=f"on {_xterm_index_to_hex(i)}")
        t.append("\n")
        # Rows 1..6: 216-cube
        for row in range(6):
            for col in range(36):
                idx = 16 + row * 36 + col
                t.append(cell, style=f"on {_xterm_index_to_hex(idx)}")
            t.append("\n")
        # Row 7: 24 grayscale
        for i in range(232, 256):
            t.append(cell, style=f"on {_xterm_index_to_hex(i)}")
        return t

    def cell_at(self, x: int, y: int) -> "int | None":
        """Convert widget-relative `(x, y)` to xterm cell index or
        `None` if the click landed outside any cell. `x` is in
        cells (Textual character columns); divide by `_CELL_W`."""
        col = x // self._CELL_W
        if col < 0:
            return None
        if y == 0:
            return col if 0 <= col < 16 else None
        if 1 <= y <= 6:
            return 16 + (y - 1) * 36 + col if 0 <= col < 36 else None
        if y == 7:
            return 232 + col if 0 <= col < 24 else None
        return None
