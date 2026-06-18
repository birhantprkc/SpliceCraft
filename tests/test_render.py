"""Structural guard for the extracted render layer (L1, Phase B).

`splicecraft_render` holds `_Canvas` / `_BrailleCanvas` + their glyph LUTs and
depends only on `splicecraft_state` (the `_ASCII_MODE` flag) -- the first L1
module (depends on L0). Behaviour is covered by test_cross_platform.py's
TestAsciiMapFallback; this file pins the extraction itself.
"""
from __future__ import annotations

import splicecraft as sc
import splicecraft_render


def test_canvas_primitives_in_render_sibling_and_reexported():
    names = ("_Canvas", "_BrailleCanvas", "_BRAILLE_LUT", "_ASCII_DENSITY_LUT",
             "_ASCII_DENSITY_RAMP", "_ASCII_GLYPH_MAP")
    missing = [n for n in names if not hasattr(splicecraft_render, n)]
    assert not missing, f"missing from splicecraft_render: {missing}"
    for n in names:
        assert getattr(sc, n) is getattr(splicecraft_render, n), (
            f"sc.{n} is not the splicecraft_render object"
        )


def test_braille_canvas_renders_through_state(monkeypatch):
    """End-to-end: the relocated canvas still switches braille<->ASCII off the
    shared `_state._ASCII_MODE` flag (the L1->L0 dependency)."""
    canvas = sc._Canvas(4, 1)
    bc = sc._BrailleCanvas(4, 1)
    bc.set_pixel(0, 0)

    monkeypatch.setattr(sc._state, "_ASCII_MODE", False)
    braille = bc.combine(canvas).plain
    assert any(0x2800 <= ord(c) <= 0x28FF for c in braille), "braille mode lost dots"

    monkeypatch.setattr(sc._state, "_ASCII_MODE", True)
    ascii_out = bc.combine(canvas).plain
    assert all(ord(c) < 128 for c in ascii_out), f"ASCII mode leaked non-ASCII: {ascii_out!r}"
