"""Structural + behaviour guard for the extracted widget primitives (L3).

`splicecraft_widgets` holds self-contained Textual primitives (`_InstantPressButton`,
the xterm-256 `_XtermColorGrid` + its palette helpers). Pure Textual/Rich, no hub
coupling.
"""
from __future__ import annotations

import splicecraft as sc
import splicecraft_widgets


def test_widget_primitives_in_sibling_and_reexported():
    names = ("_InstantPressButton", "_ANSI16_HEX", "_xterm_index_to_hex", "_XtermColorGrid")
    missing = [n for n in names if not hasattr(splicecraft_widgets, n)]
    assert not missing, f"missing from splicecraft_widgets: {missing}"
    for n in names:
        assert getattr(sc, n) is getattr(splicecraft_widgets, n), (
            f"sc.{n} is not the splicecraft_widgets object"
        )


def test_xterm_palette_helper():
    assert sc._xterm_index_to_hex(0) == sc._ANSI16_HEX[0]
    for idx in (16, 100, 231, 232, 255):
        h = sc._xterm_index_to_hex(idx)
        assert h.startswith("#") and len(h) == 7, h
    assert sc._xterm_index_to_hex(999) == sc._xterm_index_to_hex(255)   # clamps


def test_xterm_grid_render_and_hittest():
    g = sc._XtermColorGrid()
    assert g.render().plain.count("\n") == 7        # 8 rows of cells
    assert g.cell_at(0, 0) == 0                       # first ANSI cell
    assert g.cell_at(0, 7) == 232                     # first grayscale cell
    assert g.cell_at(0, 99) is None                   # off-grid
