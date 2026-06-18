"""
test_map_autocircular — auto-circularity (2026-06-13).

The plasmid map re-fits its cell aspect (circularity) on every resize so
the circle stays round without the user pressing ,/. — terminal resize,
the draggable map/seq divider, and the F-key fullscreen toggles all fire
`PlasmidMap.on_resize`. Two guards make it safe:

  * `_detect_char_aspect_raw()` returns None when the terminal can't
    report pixel size, so a manual ,/. nudge is never clobbered to a
    guess on no-pixel terminals (tmux / SSH / xterm.js web demo).
  * an epsilon gate skips identical writes so a divider-drag resize storm
    doesn't churn the reactive / render cache (mirrors [INV-52]).

See `PlasmidMap.on_resize` / `PlasmidMap.on_mount` / the
`_detect_char_aspect_raw` + `_detect_char_aspect` pair.
"""
from __future__ import annotations

import inspect

import pytest

import splicecraft as sc


@pytest.fixture(autouse=True)
def _no_aspect_env(monkeypatch):
    # Every test here runs with $SPLICECRAFT_MAP_ASPECT cleared and the startup
    # escape-query result reset; the override / escape tests set them explicitly
    # via monkeypatch AFTER this fixture clears them.
    monkeypatch.delenv("SPLICECRAFT_MAP_ASPECT", raising=False)
    monkeypatch.setattr(sc._state, "_ESCAPE_ASPECT", None)


# ═══════════════════════════════════════════════════════════════════════════════
# The raw detector + its concrete-value wrapper
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetectCharAspect:
    def test_raw_is_none_or_in_range(self):
        # Under pytest there is usually no pixel-reporting tty, so this is
        # almost always None — but if a dev runs it under a reporting
        # terminal it must be a sane ratio, never out of band.
        raw = sc._detect_char_aspect_raw()
        assert raw is None or (isinstance(raw, float) and 0.8 <= raw <= 5.0)

    def test_wrapper_returns_float_in_range(self):
        a = sc._detect_char_aspect()
        assert isinstance(a, float) and 0.8 <= a <= 5.0

    def test_wrapper_falls_back_to_two_when_unknown(self, monkeypatch):
        monkeypatch.setattr(sc, "_detect_char_aspect_raw", lambda: None)
        assert sc._detect_char_aspect() == 2.0

    def test_wrapper_passes_through_a_known_ratio(self, monkeypatch):
        monkeypatch.setattr(sc, "_detect_char_aspect_raw", lambda: 3.3)
        assert sc._detect_char_aspect() == 3.3


# ═══════════════════════════════════════════════════════════════════════════════
# on_resize — re-fit, preserve-on-unknown, no-churn
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutoCircularityOnResize:
    def test_resize_refits_aspect_to_measured_ratio(self, monkeypatch):
        pm = sc.PlasmidMap()
        pm._aspect = 2.0
        monkeypatch.setattr(sc, "_detect_char_aspect_raw", lambda: 2.45)
        pm.on_resize(None)
        assert pm._aspect == 2.45

    def test_resize_preserves_manual_nudge_when_pixels_unknown(self, monkeypatch):
        # The case to protect: a user on a no-pixel terminal hand-tunes to
        # 2.15 with ,/. — a resize must NOT stomp it back to a guess.
        pm = sc.PlasmidMap()
        pm._aspect = 2.15
        monkeypatch.setattr(sc, "_detect_char_aspect_raw", lambda: None)
        pm.on_resize(None)
        assert pm._aspect == 2.15

    def test_resize_is_noop_when_ratio_unchanged(self, monkeypatch):
        # A divider drag fires many resizes at a constant cell ratio — the
        # epsilon gate must keep _aspect put (no reactive churn).
        pm = sc.PlasmidMap()
        pm._aspect = 2.0
        monkeypatch.setattr(sc, "_detect_char_aspect_raw", lambda: 2.0)
        pm.on_resize(None)
        assert pm._aspect == 2.0

    def test_resize_updates_on_change_above_epsilon(self, monkeypatch):
        pm = sc.PlasmidMap()
        pm._aspect = 2.0
        monkeypatch.setattr(sc, "_detect_char_aspect_raw", lambda: 2.1)
        pm.on_resize(None)
        assert pm._aspect == 2.1


# ═══════════════════════════════════════════════════════════════════════════════
# on_mount — use the measurement, keep the default when unknown
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutoCircularityOnMount:
    def test_mount_keeps_default_when_pixels_unknown(self, monkeypatch):
        monkeypatch.setattr(sc, "_detect_char_aspect_raw", lambda: None)
        pm = sc.PlasmidMap()
        pm.on_mount()
        assert pm._aspect == 2.0

    def test_mount_uses_measured_ratio(self, monkeypatch):
        monkeypatch.setattr(sc, "_detect_char_aspect_raw", lambda: 2.3)
        pm = sc.PlasmidMap()
        pm.on_mount()
        assert pm._aspect == 2.3


# ═══════════════════════════════════════════════════════════════════════════════
# White-box guards so the two safety properties can't silently regress
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutoCircularitySourceGuards:
    def test_on_resize_guards_none_and_epsilon(self):
        src = inspect.getsource(sc.PlasmidMap.on_resize)
        assert "_measured_cell_aspect" in src
        assert "is not None" in src          # never clobber on unknown
        assert "abs(" in src                 # epsilon gate vs reactive churn

    def test_on_mount_uses_raw_and_guards_none(self):
        src = inspect.getsource(sc.PlasmidMap.on_mount)
        assert "_measured_cell_aspect" in src
        assert "is not None" in src


# ═══════════════════════════════════════════════════════════════════════════════
# $SPLICECRAFT_MAP_ASPECT pin — the escape hatch for terminals that can't
# report pixel size (the xterm.js web demo, tmux, some SSH)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnvAspectOverride:
    def test_parses_valid_in_range(self, monkeypatch):
        monkeypatch.setenv("SPLICECRAFT_MAP_ASPECT", "2.35")
        assert sc._env_aspect_override() == 2.35

    def test_none_when_unset(self):
        assert sc._env_aspect_override() is None

    def test_ignores_out_of_range(self, monkeypatch):
        monkeypatch.setenv("SPLICECRAFT_MAP_ASPECT", "99")
        assert sc._env_aspect_override() is None

    def test_ignores_garbage(self, monkeypatch):
        monkeypatch.setenv("SPLICECRAFT_MAP_ASPECT", "wide")
        assert sc._env_aspect_override() is None

    def test_mount_adopts_pin_over_measurement(self, monkeypatch):
        # Even if the terminal WOULD report a different ratio, the pin wins.
        monkeypatch.setenv("SPLICECRAFT_MAP_ASPECT", "2.35")
        monkeypatch.setattr(sc, "_detect_char_aspect_raw", lambda: 1.8)
        pm = sc.PlasmidMap()
        pm.on_mount()
        assert pm._aspect == 2.35

    def test_resize_does_not_autofit_over_pin(self, monkeypatch):
        monkeypatch.setenv("SPLICECRAFT_MAP_ASPECT", "2.35")
        monkeypatch.setattr(sc, "_detect_char_aspect_raw", lambda: 1.8)
        pm = sc.PlasmidMap()
        pm._aspect = 2.35
        pm.on_resize(None)
        assert pm._aspect == 2.35  # honored the pin, ignored the measurement

    def test_source_gates_mount_and_resize_on_override(self):
        assert "_env_aspect_override" in inspect.getsource(sc.PlasmidMap.on_mount)
        assert "_env_aspect_override" in inspect.getsource(sc.PlasmidMap.on_resize)


# ═══════════════════════════════════════════════════════════════════════════════
# CSI 16t / 14t self-query — the terminal reporting its OWN cell pixel size,
# parsed by the pure _parse_cell_size_report (the I/O lives in
# _query_cell_aspect_via_escape, which isn't unit-tested — it does real tty I/O)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEscapeQueryParse:
    def test_cell_size_report_16t(self):
        # ESC [ 6 ; height ; width t → height/width directly
        assert sc._parse_cell_size_report(b"\x1b[6;32;14t") == round(32 / 14, 3)

    def test_text_area_report_14t_with_grid(self):
        # ESC [ 4 ; height_px ; width_px t ÷ cols×rows:
        # 480px / 30 rows = 16 tall · 640px / 80 cols = 8 wide → 2.0
        assert sc._parse_cell_size_report(b"\x1b[4;480;640t", cols=80, rows=30) == 2.0

    def test_14t_ignored_without_grid(self):
        # No grid → a text-area report can't become a per-cell ratio.
        assert sc._parse_cell_size_report(b"\x1b[4;480;640t") is None

    def test_16t_wins_over_14t_when_both_present(self):
        buf = b"\x1b[6;30;15t\x1b[4;999;999t"
        assert sc._parse_cell_size_report(buf, cols=80, rows=30) == 2.0

    def test_no_report(self):
        assert sc._parse_cell_size_report(b"random noise, no CSI report") is None

    def test_out_of_range_rejected(self):
        assert sc._parse_cell_size_report(b"\x1b[6;100;10t") is None  # 10.0 > 5.0

    def test_zero_dims_rejected(self):
        assert sc._parse_cell_size_report(b"\x1b[6;0;14t") is None


class TestMeasuredCellAspectPrecedence:
    def test_prefers_escape_over_ioctl(self, monkeypatch):
        monkeypatch.setattr(sc._state, "_ESCAPE_ASPECT", 2.3)
        monkeypatch.setattr(sc, "_detect_char_aspect_raw", lambda: 1.9)
        assert sc._measured_cell_aspect() == 2.3

    def test_falls_back_to_ioctl_when_no_escape(self, monkeypatch):
        monkeypatch.setattr(sc._state, "_ESCAPE_ASPECT", None)
        monkeypatch.setattr(sc, "_detect_char_aspect_raw", lambda: 1.9)
        assert sc._measured_cell_aspect() == 1.9

    def test_none_when_neither_available(self, monkeypatch):
        monkeypatch.setattr(sc._state, "_ESCAPE_ASPECT", None)
        monkeypatch.setattr(sc, "_detect_char_aspect_raw", lambda: None)
        assert sc._measured_cell_aspect() is None

    def test_resize_uses_escape_measurement(self, monkeypatch):
        monkeypatch.setattr(sc._state, "_ESCAPE_ASPECT", 2.3)
        pm = sc.PlasmidMap()
        pm._aspect = 2.0
        pm.on_resize(None)
        assert pm._aspect == 2.3
