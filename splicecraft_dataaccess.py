"""splicecraft_dataaccess — the user-data access layer (Phase D, layer 1).

First piece: `_typed_clone`, the deep-clone-on-read/write helper that enforces
the data layer's SACRED invariant #17 — a caller mutating a returned entry
can't poison the in-memory cache, and a caller mutating its argument after a
save can't leak post-save edits into the next reader. Every `_load_X` returns
`_typed_clone(cache)`; every `_save_X` re-seats the cache via
`_typed_clone(entries)`. It belongs here because that contract IS the data
layer.

This module grows to hold the domain `_load_X` / `_save_X` accessors as Phase D
moves them off the hub, so the modal / screen / agent siblings can import them
here instead of importing the hub (which would be a cycle). It imports ONLY
stdlib + lower/same-layer siblings (splicecraft_state, splicecraft_persistence,
splicecraft_logging); the hub re-exports every name (`from splicecraft_dataaccess
import _typed_clone as _typed_clone` ...) so `sc.<name>` + every existing call
site resolve unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from typing import TypeVar as _TypeVar

# Immutable types `_typed_clone` returns as-is (no copy needed). Everything else
# recurses (dict / list / tuple) or falls through to `deepcopy` for any
# unexpected type, so the sacred-#17 contract ("caller-side mutations can't
# poison the cache") is preserved end to end.
_IMMUTABLE_CLONE_TYPES = (str, int, float, bool, bytes, type(None))
_TC = _TypeVar("_TC")


def _typed_clone(obj: _TC) -> _TC:
    t = type(obj)
    if t is dict:
        return {k: _typed_clone(v) for k, v in obj.items()}  # type: ignore[return-value]
    if t is list:
        return [_typed_clone(v) for v in obj]  # type: ignore[return-value]
    if t is tuple:
        return tuple(_typed_clone(v) for v in obj)  # type: ignore[return-value]
    if isinstance(obj, _IMMUTABLE_CLONE_TYPES):
        return obj
    return deepcopy(obj)
