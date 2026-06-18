"""Shared mutable process state for SpliceCraft (layer 0).

Module-level flags that the monolith kept as bare globals and mutated at runtime
(`global _ASCII_MODE; _ASCII_MODE = ...`) move here, one source of truth, so the
hub, every sibling module, and the test suite all read and write the *same*
storage.

ACCESS RULE (this is a footgun if ignored):

    import splicecraft_state as state
    if state._ASCII_MODE: ...          # GOOD - reads the live value
    state._ASCII_MODE = True           # GOOD - writes the live value

    from splicecraft_state import _ASCII_MODE   # BAD - binds a STALE COPY;
                                                # later writes here are invisible
                                                # to it, and monkeypatching the
                                                # module attr won't reach it.

`tests/test_state_module.py` enforces that no module keeps such a stale shadow.
Tests monkeypatch these via the module object (`monkeypatch.setattr(sc._state,
"_ASCII_MODE", ...)`), which every reader then sees.

This module imports nothing from the rest of the package and is safe to import
from any layer.
"""
from __future__ import annotations

# ── Render tier ──────────────────────────────────────────────────────────────
# Set by `_select_render_tier()` / `_set_ascii_mode()` in splicecraft.py; read
# by the map + helix renderers (fresh every frame) and folded into the
# PlasmidMap draw-cache key so the live Settings toggle busts the cache.
#
# `_ASCII_MODE`: emit the 7-bit-ASCII density-ramp fallback instead of Unicode
# braille/glyphs (a terminal that can't emit UTF-8). Forced on by
# SPLICECRAFT_ASCII=1.
_ASCII_MODE: bool = False

# `_ASCII_FORCED`: True when ASCII was FORCED by capability (the
# SPLICECRAFT_ASCII env var, or a terminal that genuinely can't emit UTF-8)
# rather than CHOSEN via the toggle. Gates whether a saved `ascii_map`
# preference may restore braille at launch: it may not when braille literally
# can't render (mojibake), but it may when the only problem was a font missing
# the glyphs (UTF-8 fine, dots show as boxes).
_ASCII_FORCED: bool = False
