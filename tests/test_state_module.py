"""Guard the shared-state pattern (the Option-3 state decoupling).

Mutable process state that the monolith kept as bare module globals and mutated
at runtime (render-tier flags now; data paths / caches later) is migrating into
``splicecraft_state``, accessed by attribute (``state.X``) so the hub, every
sibling, and the tests all read and write ONE copy.

The failure this guards against is the *stale-shadow* trap: a migrated name also
exists as a hub-level global (a leftover ``_X = ...`` in splicecraft.py, or a
``from splicecraft_state import _X``), so a monkeypatch or a runtime write to one
copy silently misses readers using the other. That trap is exactly what makes
naive module extraction unsafe in this codebase, so it gets a dedicated test
that every future migration extends via ``_MIGRATED``.
"""
from __future__ import annotations

import splicecraft as sc
import splicecraft_state

# Names migrated out of the hub into splicecraft_state. APPEND to this as the
# decoupling proceeds; each entry is then held to the single-source-of-truth
# invariants below.
_MIGRATED = [
    "_ASCII_MODE",
    "_ASCII_FORCED",
    "_WIN_UTF8_CONSOLE",
    "_ESCAPE_ASPECT",
]


def test_hub_exposes_state_module():
    """`sc._state` is the canonical handle the hub + tests patch through."""
    assert sc._state is splicecraft_state


def test_migrated_names_live_in_state():
    missing = [n for n in _MIGRATED if not hasattr(splicecraft_state, n)]
    assert not missing, f"migrated name(s) absent from splicecraft_state: {missing}"


def test_no_stale_hub_shadow():
    """A migrated name must NOT also exist in the hub's own namespace, where it
    would desync from `state.X` writes / monkeypatches."""
    shadows = [n for n in _MIGRATED if n in vars(sc)]
    assert not shadows, (
        f"`splicecraft` keeps stale shadow(s) of migrated state: {shadows}. "
        "Remove the hub-level binding and read via `_state.<name>` -- a "
        "leftover global or a by-value `from splicecraft_state import` desyncs "
        "from the live value (the trap this whole refactor exists to avoid)."
    )


def test_runtime_writer_mutates_the_shared_copy(monkeypatch):
    """`_set_ascii_mode` (a runtime writer) must flip the SAME storage readers
    see -- i.e. the state module's attribute, not a hub-local copy."""
    monkeypatch.setattr(splicecraft_state, "_ASCII_MODE", False)
    sc._set_ascii_mode(True)
    assert splicecraft_state._ASCII_MODE is True, (
        "_set_ascii_mode did not write the shared splicecraft_state copy"
    )
    sc._set_ascii_mode(False)
    assert splicecraft_state._ASCII_MODE is False
