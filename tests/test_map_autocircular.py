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

import splicecraft as sc


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
        assert "_detect_char_aspect_raw" in src
        assert "is not None" in src          # never clobber on unknown
        assert "abs(" in src                 # epsilon gate vs reactive churn

    def test_on_mount_uses_raw_and_guards_none(self):
        src = inspect.getsource(sc.PlasmidMap.on_mount)
        assert "_detect_char_aspect_raw" in src
        assert "is not None" in src
