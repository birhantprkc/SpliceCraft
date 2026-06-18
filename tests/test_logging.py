"""End-to-end + structural guards for the extracted logging layer (Phase B).

`splicecraft_logging` (layer 0) owns the logging primitives (`_log`,
`_log_event`, `_repr_for_log`, the filters, `_SESSION_ID`, `_LOG_CTRL_RE`); the
hub keeps the `_DATA_DIR`-dependent file-handler wiring and the
`_action_log` / `_timed` decorators (whose internal `_log_event` call must
resolve the hub's patchable re-export).

The semi-silent regression this guards: the handler ends up mis-wired and
structured events stop reaching the log file -- which the rest of the suite
would NOT catch, since no other test asserts the log is actually written.
"""
from __future__ import annotations

import logging

import splicecraft as sc
import splicecraft_logging


def test_primitives_live_in_logging_sibling():
    names = ("_log", "_log_event", "_repr_for_log", "_SessionFilter",
             "_SurrogateScrubFilter", "_SESSION_ID", "_LOG_CTRL_RE")
    missing = [n for n in names if not hasattr(splicecraft_logging, n)]
    assert not missing, f"missing from splicecraft_logging: {missing}"
    # re-exported into the hub as the SAME objects (not stale copies)
    for n in ("_log", "_log_event", "_repr_for_log", "_SessionFilter",
              "_SurrogateScrubFilter", "_LOG_CTRL_RE"):
        assert getattr(sc, n) is getattr(splicecraft_logging, n), (
            f"sc.{n} is not the splicecraft_logging object"
        )


def test_decorators_stay_in_hub():
    # `_action_log` / `_timed` must remain hub-defined so their internal
    # `_log_event` resolves the patchable re-export -- the monkeypatch path the
    # event-capture tests rely on. If they ever move, that path breaks silently.
    assert sc._action_log.__module__ == "splicecraft"
    assert sc._timed.__module__ == "splicecraft"


def test_log_event_reaches_a_file(tmp_path):
    """End-to-end: a `_log_event` lands in an actual log file via the shared
    logger. Guards the semi-silent 'structured logging silently stops'
    regression that no other test would catch."""
    log_file = tmp_path / "probe.log"
    h = logging.FileHandler(log_file, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(message)s"))
    splicecraft_logging._log.addHandler(h)
    try:
        sc._log_event("test.probe", token="ZZ-marker-42")
    finally:
        splicecraft_logging._log.removeHandler(h)
        h.close()
    text = log_file.read_text(encoding="utf-8")
    assert "test.probe" in text and "ZZ-marker-42" in text


def test_action_log_routes_through_patchable_log_event(monkeypatch):
    """A test that patches `sc._log_event` must intercept events emitted via the
    `@_action_log` decorator -- i.e. the decorator (in the hub) resolves the
    patchable re-export, not a private copy in the sibling."""
    captured: list = []
    monkeypatch.setattr(sc, "_log_event", lambda e, **f: captured.append((e, f)))

    class _Dummy:
        _current_record = None

        @sc._action_log("test.action")
        def go(self):
            return 42

    assert _Dummy().go() == 42
    assert any(e == "test.action" for e, _ in captured), (
        "@_action_log did not route through the patchable sc._log_event"
    )


def test_log_event_never_logs_sequence_content(tmp_path):
    """Sacred invariant (preserved across the extraction): a Seq/SeqRecord field
    is rendered as an opaque ``<Seq len=N>`` tag, never its bases."""
    class Seq:  # mimics Bio.Seq.Seq by class name -- the guard keys on __name__
        def __init__(self, s): self._s = s
        def __len__(self): return len(self._s)
        def __repr__(self): return f"Seq('{self._s}')"   # would leak bases if logged raw

    log_file = tmp_path / "seq.log"
    h = logging.FileHandler(log_file, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(message)s"))
    splicecraft_logging._log.addHandler(h)
    try:
        sc._log_event("test.seq", payload=Seq("ACGTACGTACGT"))
    finally:
        splicecraft_logging._log.removeHandler(h)
        h.close()
    text = log_file.read_text(encoding="utf-8")
    assert "ACGTACGT" not in text, "sequence bases leaked into the log!"
    assert "<Seq" in text and "len=12" in text
