"""Sub-character rendering primitives (layer 1).

`_Canvas` (a mutable char/style grid) and `_BrailleCanvas` (a 2x4-dot
sub-character canvas using Unicode braille U+2800-U+28FF, with a 7-bit-ASCII
density-ramp fallback) plus their glyph lookup tables. Pure rendering: depends
only on `splicecraft_state` (the `_ASCII_MODE` flag) and Rich's `Text`; imports
nothing from a higher layer.
"""
from __future__ import annotations

from rich.text import Text

import splicecraft_state as _state


class _Canvas:
    """A mutable 2-D character grid that renders to a Rich Text object."""

    def __init__(self, width: int, height: int):
        self.w = width
        self.h = height
        self._chars:  list[list[str]] = [[" "] * width for _ in range(height)]
        self._styles: list[list[str]] = [[""]  * width for _ in range(height)]

    def put(self, col: int, row: int, ch: str, style: str = ""):
        if 0 <= col < self.w and 0 <= row < self.h:
            self._chars[row][col]  = ch
            self._styles[row][col] = style

    def put_text(self, col: int, row: int, text: str, style: str = ""):
        for j, ch in enumerate(text):
            self.put(col + j, row, ch, style)

    def copy(self) -> "_Canvas":
        """Deep copy of the grid (rows copied, cells are immutable str). Backs
        the circular map's base-canvas cache: a selection re-paint copies the
        cached base and re-tints only the selected feature, instead of a full
        redraw of every feature."""
        c = _Canvas(self.w, self.h)
        c._chars  = [row[:] for row in self._chars]
        c._styles = [row[:] for row in self._styles]
        return c


# Pre-built lookup table for braille glyphs U+2800..U+28FF. The combine
# loop in `_BrailleCanvas.render` writes one cell per (col, row) — on a
# 160×48 canvas that's ~7,000 chr() calls per render. CPython's small-int
# char cache stops at chr(255), so braille codepoints miss it. Indexing
# into a pre-built list saves ~1–2 ms/frame on the circular-map render.
_BRAILLE_LUT: list[str] = [chr(0x2800 + i) for i in range(256)]

# ASCII fallback glyphs for the braille canvas, used when `_ASCII_MODE`
# is on (a terminal that can't emit UTF-8 — see `_select_render_tier`).
# Each braille cell packs up to 8 dots; we map the dot popcount (0–8)
# onto a light→dark 7-bit-ASCII density ramp so a dense feature arc
# reads darker than a thin backbone line. Pure ASCII renders on
# literally any ANSI terminal, so the map (and the DNA helix, which
# shares this canvas) degrade legibly instead of turning to mojibake.
# Same pre-built-LUT trick as `_BRAILLE_LUT` to keep `combine` hot-loop
# cheap (one index, no per-cell popcount).
_ASCII_DENSITY_RAMP = " .:-=+*#@"   # 9 levels, indexed by popcount 0..8
_ASCII_DENSITY_LUT: list[str] = [
    _ASCII_DENSITY_RAMP[bin(i).count("1")] for i in range(256)
]

# When `_ASCII_MODE` is on the density LUT above handles the braille dot
# layer, but the map also OVERLAYS Unicode glyphs on the text canvas —
# block fills, strand arrowheads, the ⚠ weak-site marker, the centre
# crosshair / box-drawing, and any accented letters in feature labels.
# `_BrailleCanvas.combine` transliterates those to 7-bit ASCII via this
# map (anything unmapped → '?') so the WHOLE map — not just the dots —
# stays mojibake-free on a non-UTF-8 terminal.
_ASCII_GLYPH_MAP = {
    "█": "#", "▓": "#", "▒": "#", "░": ".",   # block fills
    "▌": "#", "▐": "#", "▏": "|", "▕": "|",
    "▶": ">", "◀": "<", "▲": "^", "▼": "v",   # strand arrowheads
    "►": ">", "◄": "<", "→": ">", "←": "<",
    "⚠": "!", "✓": "v", "✗": "x",             # status marks
    "·": ".", "•": "*", "◆": "*", "●": "o", "○": "o",
    "┼": "+", "─": "-", "│": "|", "├": "+", "┤": "+",   # box drawing
    "┌": "+", "┐": "+", "└": "+", "┘": "+", "┬": "+", "┴": "+",
    "═": "=", "║": "|", "╫": "+", "╪": "+",
}


class _BrailleCanvas:
    """
    Sub-character resolution canvas using Unicode braille (U+2800–U+28FF).

    Each terminal cell (col, row) encodes a 2-wide × 4-tall dot grid —
    8 pixels per character cell.  Braille dot layout:

        px%2=0  px%2=1
        dot1    dot4    ← py%4=0   (bits 0, 3)
        dot2    dot5    ← py%4=1   (bits 1, 4)
        dot3    dot6    ← py%4=2   (bits 2, 5)
        dot7    dot8    ← py%4=3   (bits 6, 7)

    Codepoint = 0x2800 + bitmask of active dots.
    Colors: higher-priority write wins per cell.
    """

    _DOT_BITS: list[list[int]] = [
        [0, 3],
        [1, 4],
        [2, 5],
        [6, 7],
    ]

    def __init__(self, cols: int, rows: int):
        self.cols = cols
        self.rows = rows
        self._bits:   list[list[int]] = [[0]  * cols for _ in range(rows)]
        self._colors: list[list[str]] = [[" "] * cols for _ in range(rows)]
        self._prio:   list[list[int]] = [[0]  * cols for _ in range(rows)]

    def set_pixel(self, px: int, py: int,
                  color: str = "", priority: int = 1) -> None:
        col, row = px // 2, py // 4
        if not (0 <= col < self.cols and 0 <= row < self.rows):
            return
        self._bits[row][col] |= 1 << self._DOT_BITS[py % 4][px % 2]
        if color and priority >= self._prio[row][col]:
            self._colors[row][col] = color
            self._prio[row][col]   = priority

    def copy(self) -> "_BrailleCanvas":
        """Deep copy of the dot/colour/priority grids (rows copied, cells are
        immutable). Backs the circular map's base-canvas cache — see
        ``_Canvas.copy``."""
        bc = _BrailleCanvas(self.cols, self.rows)
        bc._bits   = [row[:] for row in self._bits]
        bc._colors = [row[:] for row in self._colors]
        bc._prio   = [row[:] for row in self._prio]
        return bc

    def combine(self, text_canvas: "_Canvas") -> Text:
        """
        Return a Rich Text object.
        Non-space cells from *text_canvas* are drawn on top;
        braille pixels fill the rest.
        Consecutive blank cells are batched into a single append call.

        A space cell that carries a style (e.g. the inner space of a
        feature-bar label painted ``"bold black on color(46)"``) is
        treated as styled content, NOT folded into the blank-run —
        otherwise the cell's background colour is stripped and the
        label space renders as a default-bg cell (visible as a black
        gap inside the green bar). Regression guard for the 2026-05-22
        "transit peptide bar has black gaps" report.
        """
        result = Text(no_wrap=True, overflow="crop")
        rows   = min(self.rows, text_canvas.h)
        cols   = min(self.cols, text_canvas.w)
        tc_chars  = text_canvas._chars
        tc_styles = text_canvas._styles
        bc_bits   = self._bits
        bc_colors = self._colors
        # Pick the glyph table once per render (not per cell): ASCII
        # density ramp on a non-UTF-8 terminal, braille otherwise.
        lut = _ASCII_DENSITY_LUT if _state._ASCII_MODE else _BRAILLE_LUT
        ascii_overlay = _state._ASCII_MODE   # also fold overlay glyphs → ASCII
        for row in range(rows):
            blank_run = 0
            tc_row  = tc_chars[row]
            tcs_row = tc_styles[row]
            bc_bits_row   = bc_bits[row]
            bc_colors_row = bc_colors[row]
            for col in range(cols):
                tc_ch = tc_row[col]
                tc_st = tcs_row[col]
                # Truly blank cell — space char, no style, no braille
                # — folds into the blank-run for efficient append.
                if tc_ch == " " and not tc_st and not bc_bits_row[col]:
                    blank_run += 1
                    continue
                if blank_run:
                    result.append(" " * blank_run)
                    blank_run = 0
                if tc_ch != " " or tc_st:
                    # Non-space char, OR a styled space — emit from
                    # text_canvas so the style (and any background
                    # colour it carries) is preserved. In ASCII mode,
                    # fold any non-ASCII overlay glyph down to a 7-bit
                    # equivalent ('?' if unmapped) so the map can't emit
                    # raw UTF-8 on a terminal that can't render it.
                    ch_out = tc_ch
                    if ascii_overlay and tc_ch > "\x7f":
                        ch_out = _ASCII_GLYPH_MAP.get(tc_ch, "?")
                    if tc_st:
                        result.append(ch_out, style=tc_st)
                    else:
                        result.append(ch_out)
                else:
                    # Unstyled space with braille pixels underneath —
                    # render the braille glyph.
                    ch = lut[bc_bits_row[col]]
                    c  = bc_colors_row[col]
                    if c != " ":
                        result.append(ch, style=c)
                    else:
                        result.append(ch)
            if blank_run:
                result.append(" " * blank_run)
            if row < rows - 1:
                result.append("\n")
        return result
