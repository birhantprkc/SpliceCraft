"""Logging primitives for SpliceCraft (layer 0).

Extracted from the monolith so any sibling can log. Holds the `splicecraft`
logger (`_log`), the surrogate/session filters, the structured-event emitter
`_log_event`, and `_repr_for_log`. Imports nothing from the rest of the package.

DELIBERATELY LEFT IN THE HUB (do not move here):
  * the `_DATA_DIR`-dependent file-handler wiring (`_default_log_path` /
    `_LOG_PATH` + the `RotatingFileHandler` setup) -- it reads the data dir;
    the hub configures THIS logger after importing it.
  * the `_action_log` / `_timed` decorators -- they call `_log_event`
    internally, and tests monkeypatch `sc._log_event`; keeping the decorators
    in the hub makes that call resolve the patchable re-export rather than a
    private copy here. See tests/test_logging.py.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid as _uuid


_SESSION_ID = _uuid.uuid4().hex[:8]

class _SessionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.session = _SESSION_ID
        return True


# Control bytes (C0 incl. ESC, DEL, the C1 block incl. 8-bit CSI 0x9b)
# in a logged value would let a crafted name / filename / accession
# smuggle a terminal-escape sequence into the log file — which then
# fires when the user `cat`s the log or pastes it into a bug report
# (or hands it to an LLM scanning it). Escape them to a visible `\xNN`
# form on the logger so no callsite can leak one regardless of how it
# logs. Cheap: the search short-circuits when no control byte is present.
_LOG_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class _SurrogateScrubFilter(logging.Filter):
    """A lone surrogate (e.g. what `os.fsdecode` yields for an undecodable
    filename under a non-UTF-8 locale) cannot be UTF-8-encoded. Beyond
    crashing a UTF-8 stream, it crashes **pytest-xdist's** worker→controller
    report serialization — `execnet` raises `UnicodeEncodeError` and the whole
    CI run `INTERNALERROR`s (pytest 9.1 serialises the captured record, which
    the rotating handler's `errors=` escaping happens too late to save). Scrub
    every record's message + args to a safe backslash-escaped form HERE, on the
    logger, so no surrogate can leave this logger regardless of handler config
    or test-capture path. Also escapes control bytes (incl. ESC) so a logged
    name can't smuggle a terminal-escape into the log file. Sweep 2026-06-14."""

    @staticmethod
    def _scrub(value: object) -> object:
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                value = value.encode("utf-8", "backslashreplace").decode("ascii")
            if _LOG_CTRL_RE.search(value):
                value = _LOG_CTRL_RE.sub(
                    lambda m: "\\x%02x" % ord(m.group()), value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._scrub(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._scrub(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: self._scrub(v) for k, v in record.args.items()}
        return True

_log = logging.getLogger("splicecraft")
# DEBUG-level logs are off by default — bump via SPLICECRAFT_DEBUG=1
# so a user reproducing a bug can capture cache-invalidation / retry /
# pre-flight events that normally don't surface. Network retry events,
# cache-clear events, and other diagnostic-only signals key off this.
if os.environ.get("SPLICECRAFT_DEBUG", "").strip().lower() in (
    "1", "true", "yes"
):
    _log.setLevel(logging.DEBUG)
else:
    _log.setLevel(logging.INFO)
_log.propagate = False
# Scrub lone surrogates on EVERY record before any handler / test-capture sees
# them — keeps pytest-xdist's report serialization from crashing CI (see class).
_log.addFilter(_SurrogateScrubFilter())


def _repr_for_log(value, max_len: int = 100) -> str:
    """Compact, bounded `repr()` for log lines. Long sequences,
    nested dicts, and accidentally-huge values are truncated so a
    single chatty log line can't blow out the rotation window. The
    prefix preserves the type ("[123 items]") so a grepper can still
    find the relevant entry. Plasmid sequences are NOT logged (callers
    avoid passing them) but this guard keeps an accidental long-string
    log call from leaking content."""
    try:
        if isinstance(value, str):
            if len(value) > max_len:
                return f"{value[:max_len // 2]!r}…[{len(value)} chars]"
            return repr(value)
        if isinstance(value, (list, tuple, set)):
            n = len(value)
            if n > 10:
                return f"<{type(value).__name__} of {n} items>"
            return repr(value)[:max_len]
        if isinstance(value, dict):
            if len(value) > 10:
                return f"<dict with {len(value)} keys>"
            return repr(value)[:max_len]
        out = repr(value)
        if len(out) > max_len:
            return out[:max_len] + f"…[{len(out)} chars]"
        return out
    except Exception:
        return "<unrepr-able>"


def _log_event(event: str, *, _stacklevel: int = 2, **fields) -> None:
    """One-line AI-parseable structured event for diagnostic logs.

    Output line shape (JSON payload after the prefix):

        2026-05-15 12:34:56,789 [a3f2c1d8] INFO  splicecraft.action_save:53321 event save.ok {"rec":"pUC19","path":"/tmp/x.gb"}

    The event payload is JSON so any downstream parser (jq, Python
    json.loads, an LLM scanning the log on the user's behalf) can
    extract every field unambiguously without regex tricks against
    embedded whitespace or quotes. `stacklevel=2` makes the
    `funcName:lineno` prefix point at the originating caller, not at
    `_log_event` itself — so even an empty-field event names the
    method that emitted it.

    Sacred invariant: every string value over 200 chars is truncated
    before encoding, so a caller that accidentally passes raw
    sequence content can't leak bases into the log file. Non-scalar
    values pass through `_repr_for_log` which truncates and adds a
    size hint.

    Use this at click handlers, key actions, save/load/annotate
    boundaries — anywhere a user-visible state change happens. The
    output goes to the rotating log file at INFO level so a user
    pasting their log into a bug report tells the reader (or an
    AI assistant) exactly what was being done when the symptom hit.

    Event-name conventions (see also CLAUDE.md invariant #43):
      * `app.<area>.<verb>`  — user actions
            e.g. `app.save.trigger`, `app.library.add`,
            `app.feature.add`, `app.click_debug.toggle`.
      * `op.<area>.<verb>`   — heavy ops emitted by `@_timed`
            e.g. `op.fetch_genbank`, `op.gibson_simulate`,
            `op.pairwise_align`, `op.blast_search`, `op.hmmscan`,
            `op.annotation_transfer`.
      * `<noun>.<verb>`      — state changes
            e.g. `save.ok` / `save.failed`, `record.loaded`,
            `undo.trigger` / `undo.refused` / `undo.empty`,
            `redo.trigger` / `redo.refused` / `redo.empty`,
            `lock.acquired` / `lock.contended` / `lock.stale` /
            `lock.released`, `shutdown.drain.ok` /
            `shutdown.drain.timeout`, `agent.write.ok` /
            `agent.write.failed`, `agent.error`.

    Performance: short-circuits to a no-op when the logger isn't
    INFO-enabled, so every callsite pays one `isEnabledFor` check
    (~100 ns) in the happy path. The `logging` machinery is
    thread-safe, so this is safe to call from `@work` workers.
    """
    if not _log.isEnabledFor(logging.INFO):
        return
    if not fields:
        _log.info("event %s", event, stacklevel=_stacklevel)
        return
    safe: dict = {}
    for k, v in fields.items():
        if isinstance(v, str):
            safe[k] = v if len(v) <= 200 else (
                v[:100] + f"…[+{len(v) - 100}]"
            )
        elif isinstance(v, (int, float, bool)) or v is None:
            safe[k] = v
        elif isinstance(v, (list, tuple, dict)):
            # Lists / tuples / dicts of primitives JSON-encode as
            # arrays / objects (better for downstream parsers than
            # a stringified repr). Bail to bounded repr only when
            # the structure is too big to fit on one log line OR
            # carries un-JSON-encodable values.
            try:
                encoded = json.dumps(v, default=str,
                                      ensure_ascii=False)
                if len(encoded) <= 300:
                    safe[k] = list(v) if isinstance(v, tuple) else v
                else:
                    safe[k] = _repr_for_log(v, max_len=200)
            except (TypeError, ValueError):
                safe[k] = _repr_for_log(v, max_len=200)
        elif isinstance(v, (bytes, bytearray)):
            # Raw byte blobs (e.g. a .dna file body) are NEVER
            # rendered — only a size tag.
            safe[k] = f"<{type(v).__name__} len={len(v)}>"
        else:
            # Catch BioPython sequence-bearing classes by name
            # (avoids importing BioPython here, which would
            # already be in sys.modules anyway). `repr(Seq)` /
            # `repr(SeqRecord)` embed the first ~55 bases — the
            # sacred invariant says we must never log sequence
            # content, so render an opaque tag instead.
            cls_name = type(v).__name__
            if cls_name in ("SeqRecord", "Seq", "MutableSeq"):
                rid = getattr(v, "id", None)
                try:
                    sz = len(v)
                except (TypeError, ValueError):
                    sz = None
                parts = [f"<{cls_name}"]
                if rid:
                    parts.append(f"id={rid}")
                if sz is not None:
                    parts.append(f"len={sz}")
                safe[k] = " ".join(parts) + ">"
            else:
                # Path, datetime, arbitrary objects — bounded
                # repr. Final defence against an unexpected type
                # that happens to embed sequence in its repr.
                safe[k] = _repr_for_log(v, max_len=200)
    try:
        payload = json.dumps(safe, default=str,
                              separators=(",", ":"),
                              ensure_ascii=False)
    except (TypeError, ValueError):
        # Final-resort fallback for non-JSON-encodable values that
        # also slip past `_repr_for_log`. Should be unreachable.
        payload = repr(safe)[:500]
    _log.info("event %s %s", event, payload, stacklevel=_stacklevel)
