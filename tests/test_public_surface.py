"""Compat-surface guardrail for the monolith -> flat-sibling refactor.

`import splicecraft as sc` is the public surface the *entire* test suite, the
`splicecraft_cli.py` sidecar, the agent API, `release.py`, and the CLAUDE.md
sandbox protocol all depend on: ``sc._state._DATA_DIR``, ``sc._save_collections``,
``sc._authorize_writes_for_sandbox``, ``sc._rc``, the ``_h_*`` endpoints, and
every modal / screen / widget class.

The #1 failure mode of moving code out into sibling modules is a *silent*
re-export drop: a name stops resolving on ``sc`` and nothing notices until some
unrelated call path raises ``AttributeError`` at runtime, possibly in a save
path. These tests fail loudly, at import time, instead.

``public_surface_baseline.json`` was snapshotted from the pre-refactor monolith
(v1.0.84). Every name in it MUST keep resolving on ``splicecraft``. If a name is
*intentionally* removed, delete it from the baseline in the SAME commit, with a
note in the commit message -- never weaken this test to dodge a real drop.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import splicecraft as sc

_BASELINE = json.loads(
    (Path(__file__).parent / "public_surface_baseline.json").read_text(encoding="utf-8")
)

# Curated high-signal subset: the names whose silent loss would be most
# catastrophic (data-safety chokepoint, biology invariants, entry points).
# Kept explicit so a drop here yields an obvious message instead of one line
# buried in a 1800-name diff.
_SACRED = [
    # data-dir safety / persistence chokepoint (_DATA_DIR now in _state)
    "_safe_save_json", "_authorize_writes",
    "_authorize_writes_for_sandbox",
    "_save_collections", "_save_library", "_save_primers", "_save_parts_bin",
    "_save_features", "_save_custom_grammars", "_save_entry_vectors",
    "_codon_tables_save", "_save_protein_motifs", "_save_experiments",
    "_save_experiment_projects", "_save_gels", "_save_custom_enzymes",
    "_save_enzyme_collections",
    # sacred biology (module-level functions only; `_rebuild_record_with_edit`
    # is a PlasmidApp method, not a module global, so it is intentionally absent)
    "_rc", "_feat_len", "_iupac_pattern", "_scan_restriction_sites",
    "_translate_cds",
    # entry points / top app
    "PlasmidApp", "main", "__version__",
]


def test_sacred_names_present():
    """The most safety-critical names always resolve on ``splicecraft``."""
    missing = [n for n in _SACRED if not hasattr(sc, n)]
    assert not missing, (
        "Sacred compat names dropped from `import splicecraft`: "
        f"{missing}. A module move forgot to re-export them into splicecraft.py."
    )


def test_full_baseline_surface_preserved():
    """No name present on the pre-refactor module may silently vanish."""
    live = set(dir(sc))
    dropped = [n for n in _BASELINE if n not in live]
    head = dropped[:40]
    assert not dropped, (
        f"{len(dropped)} name(s) disappeared from the `splicecraft` public "
        f"surface during the refactor: {head}"
        + (" ..." if len(dropped) > 40 else "")
        + "\nRe-export them from splicecraft.py, or -- if the removal is "
        "deliberate -- drop them from tests/public_surface_baseline.json in "
        "the same commit, with a note in the commit message."
    )


def test_relocated_names_are_same_object():
    """For any sacred name now *defined* in a ``splicecraft_*`` sibling, the
    object reachable as ``sc.<name>`` must be the *identical* object the sibling
    exports -- not a stale copy or a shadowing rebind. Catches the case where a
    move re-creates a name instead of re-exporting it."""
    for name in _SACRED:
        obj = getattr(sc, name, None)
        if obj is None:
            continue
        mod = getattr(obj, "__module__", None)
        if isinstance(mod, str) and mod.startswith("splicecraft_") and mod != "splicecraft":
            try:
                sibling = importlib.import_module(mod)
            except ImportError as exc:  # pragma: no cover - defensive
                raise AssertionError(
                    f"`splicecraft.{name}` claims __module__={mod!r}, but that "
                    f"sibling could not be imported: {exc!r}"
                ) from exc
            assert getattr(sibling, name, None) is obj, (
                f"`splicecraft.{name}` is not the same object as `{mod}.{name}` "
                "-- re-export rebinding bug (the hub bound a different object "
                "than the sibling defines)."
            )
