"""splicecraft_util — pure cross-cutting helpers (Phase D, layer L0).

Domain-agnostic utilities with no SpliceCraft dependencies (stdlib only): natural
sort, label sanitising, a DataTable cursor-row-key reader, file-extension
predicates, and an export-path splitter. Extracted from the hub so the modal /
screen / widget siblings can import them instead of calling them bare on the hub
(which would be an import cycle). Re-exported by the hub so `sc.<name>` + every
existing call site resolves unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path

from splicecraft_logging import _log, _log_event


_NATURAL_SORT_RE = re.compile(r"(\d+)")

_NATURAL_SORT_KEY_CACHE: "dict[str, tuple]" = {}

_NATURAL_SORT_CACHE_CAP: int = 4096

_FASTA_EXTS: frozenset[str] = frozenset({
    ".fa", ".fasta", ".fna", ".ffn", ".frn", ".fas", ".mpfa", ".faa",
})

_SEQ_ZIP_EXTS: frozenset[str] = frozenset({".zip"})

_CONTROL_CHARS_RE = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]+"
)


def _natural_sort_key(s: str) -> tuple:
    """Return a tuple suitable for natural / human-friendly sorting.

    Splits `s` into alternating text and integer runs so that
    `pBin2` sorts before `pBin10`, instead of the lexicographic
    `pBin1 < pBin10 < pBin2 < pBin20`. Tuples carry `(0, str)` for
    text runs and `(1, int)` for digit runs so Python's tuple
    comparison never tries to order an `int` against a `str` (which
    raises in Py3) — that mixed comparison is the gotcha that bites
    the naïve `(int_or_str, ...)` formulation when a name starts
    with a digit (like `"5kb_backbone"` vs `"pBin1"`).

    Cached: identical input strings (the common case across re-sort
    calls when filter keystrokes only change the predicate, not the
    underlying library) reuse the previously computed tuple.
    """
    cached = _NATURAL_SORT_KEY_CACHE.get(s)
    if cached is not None:
        return cached
    out: list[tuple[int, "int | str"]] = []
    for part in _NATURAL_SORT_RE.split(s.lower()):
        if not part:
            continue
        if part.isdigit():
            out.append((1, int(part)))
        else:
            out.append((0, part))
    result = tuple(out)
    if len(_NATURAL_SORT_KEY_CACHE) >= _NATURAL_SORT_CACHE_CAP:
        # Bounded cache: drop oldest insertion when at cap. dict
        # preserves insertion order, so popping the first key is the
        # cheapest FIFO eviction we can do without a full LRU dance.
        try:
            first = next(iter(_NATURAL_SORT_KEY_CACHE))
            del _NATURAL_SORT_KEY_CACHE[first]
        except StopIteration:
            pass
    _NATURAL_SORT_KEY_CACHE[s] = result
    return result


def _sanitize_label(s: "str | None", *, max_len: int = 200) -> str:
    """Clean a feature label / qualifier value: strip control chars,
    collapse to single line, trim, cap length. Empty / None / non-
    string input → empty string (callers decide the default).

    Type-strict: a dict / list / int payload value is treated as
    "missing" rather than coerced via ``str()`` — coercion would
    silently accept a JSON ``{"name": {"x": 1}}`` and store
    ``"{'x': 1}"`` as the label. Wrong by design.
    """
    if not isinstance(s, str) or not s:
        return ""
    s = _CONTROL_CHARS_RE.sub("", s).strip()
    return s[:max_len]


def _cursor_row_key(table) -> "str | None":
    """Return the value of a DataTable's cursor row key, or None when
    the table is empty or the cursor is out of bounds.

    Centralises the boilerplate `list(t.rows.keys())` + bounds-check
    that was open-coded at ~10 sites (library panel buttons, picker
    modals, primer table). Always pair with the empty-table branch in
    the caller — this helper is read-only.
    """
    if table.row_count == 0:
        return None
    row_keys = list(table.rows.keys())
    if not (0 <= table.cursor_row < len(row_keys)):
        return None
    rk = row_keys[table.cursor_row]
    return rk.value if rk else None


def _is_fasta_path(path) -> bool:
    """True if ``path`` looks like a FASTA file by extension. Accepts
    anything with a ``suffix`` attribute (``pathlib.Path`` or ``DirEntry``)
    or a plain string."""
    try:
        suffix = getattr(path, "suffix", None)
        if suffix is None:
            suffix = Path(str(path)).suffix
    except Exception:
        return False
    return suffix.lower() in _FASTA_EXTS


def _is_seq_zip_path(path) -> bool:
    """True if `path` looks like a sequencing-data archive by extension.
    Currently a `.zip` check; if vendors start shipping `.tar.gz` /
    `.7z` we add them here."""
    try:
        suffix = getattr(path, "suffix", None)
        if suffix is None:
            suffix = Path(str(path)).suffix
    except Exception:
        return False
    return suffix.lower() in _SEQ_ZIP_EXTS


def _split_default_export_path(default_path: str, fallback_filename: str
                                  ) -> "tuple[str, str]":
    """Split `default_path` into ``(parent_dir, filename)`` for the
    "save as" modals. Falls back to `Path.home()` for the dir and
    `fallback_filename` for the filename when either component is
    missing or unreadable. Centralised so each export modal handles
    a missing default the same way."""
    try:
        p = Path(default_path).expanduser() if default_path else None
    except (OSError, ValueError):
        p = None
    if p is not None and p.name:
        parent = p.parent
        filename = p.name
    else:
        parent = Path.home()
        filename = fallback_filename
    try:
        if not parent.is_dir():
            parent = Path.home()
    except OSError:
        parent = Path.home()
    return (str(parent), filename)


# ── Save-failure notifier + display formatters (moved from hub, Phase D) ────
def _notify_save_failure(app, label: str, exc: BaseException,
                          *, severity: str = "error") -> None:
    """Surface a save failure to the user via the app's notify channel.
    Wraps every `_save_*` call site that needs to recover gracefully
    when disk-full / RO-mount / EACCES bubbles out of `_safe_save_json`.

    Per sacred invariant #7, `_safe_save_json` re-raises on failure so
    callers can notify; this helper makes the notify pattern uniform
    rather than each call site composing its own message.

    `app` may be `None` (test contexts that don't run an App). Falls
    back to logging only.

    Emits a structured `save.failed` event so AI parsers of bug-report
    log dumps can correlate the failure target + exception class
    without regex-scraping the human-readable `Save failed for X` log
    line. Sweep #5 — pre-fix, 30+ save sites went through this helper
    silently from a structured-event perspective.
    """
    _log.exception("Save failed for %s", label)
    _log_event("save.failed", target=label,
                exc_type=type(exc).__name__,
                exc_msg=str(exc))
    msg = f"{label} save failed: {exc}"
    if app is None:
        return
    try:
        app.notify(msg, severity=severity, timeout=12)
    except Exception:
        # If notify itself fails (no app, no screen mounted yet) we've
        # already logged via _log.exception — that's enough.
        pass


def _format_identity_pct(pct: "float | int | None", *,
                         decimals: int = 1) -> str:
    """Format an alignment identity percentage for a compact table cell
    such that a value below 100% NEVER renders as ``"100%"``.

    The naïve ``f"{v:.1f}%"`` rounds 99.99% up to ``"100.0%"`` — which
    then reads as a perfect alignment even though `_identity_pct_color`
    (strict ``>= 100.0`` → light-blue) correctly keeps the cell *green*.
    The user flagged exactly that contradiction ("says 100% but it's
    green") for a one-bp mismatch in an 18 kb plasmid. Here, when
    rounding at ``decimals`` places would land on "100", precision is
    escalated one place at a time (capped at 4) until the rendered
    number is strictly < 100, so the same alignment shows e.g.
    ``"99.99%"`` instead. A genuine 100.0 (``n_matches == aligned_cols``)
    still renders the clean ``"100%"`` — no decimals — so a true perfect
    match is visually distinct from a near-perfect one at a glance.

    A value pathologically close to but below 100 (e.g. 99.999999, which
    rounds to "100.0000" even at 4 places) renders ``"<100%"`` rather
    than implying perfection. Non-numeric / None → ``"—"`` (no tier
    implied — matches `_identity_pct_color`'s neutral handling).
    """
    if pct is None:
        return "—"
    try:
        v = float(pct)
    except (TypeError, ValueError):
        return "—"
    # Use the SAME strict ``>= 100.0`` boundary as `_identity_pct_color`
    # so the number and the colour can never disagree about perfection.
    if v >= 100.0:
        return "100%"
    d = max(0, int(decimals))
    for places in range(d, 5):
        s = f"{v:.{places}f}"
        try:
            shown = float(s)
        except ValueError:
            break
        if shown < 100.0:
            return f"{s}%"
    return "<100%"


def _sanitize_plasmid_name(raw: str, *,
                            fallback: str = "assembly",
                            max_len: int = 60) -> str:
    """Clean a user-entered plasmid name for safe storage in the
    library / parts bin and for use as a SeqRecord ``id`` / ``name``.

    Strips control chars (including NUL — would break C-string-style
    handling in downstream tools), trims whitespace, and truncates
    to ``max_len`` chars to keep DataTable rows from blowing the
    row width. Empty / whitespace-only input falls back to
    ``fallback`` so the caller never gets a zero-length string.

    Forbidden character set is conservative: only printable ASCII +
    Unicode letters / digits / a small punctuation set (``_-+. ·:``).
    Colons are kept because the constructor uses them in source
    annotations (``constructor:gid:role``); slashes and backslashes
    are dropped because they look like paths and tools commonly
    interpret them as such.
    """
    if not isinstance(raw, str):
        raw = str(raw or "")
    # Whitespace control chars (\t \n \r \v \f) become spaces FIRST so
    # they don't silently fuse adjacent words after the control-strip
    # pass. e.g. ``"foo\tbar"`` becomes ``"foo bar"``, not ``"foobar"``.
    for ch in "\t\n\r\v\f":
        raw = raw.replace(ch, " ")
    # Drop NUL + remaining C0 control chars (\x00–\x1F + \x7F) — these
    # never belong in a user-facing identifier and break naive
    # C-string handling in some downstream tools.
    cleaned = "".join(
        ch for ch in raw
        if (ord(ch) >= 0x20 and ord(ch) != 0x7F)
    )
    # Drop path-like separators outright — a name like
    # ``../../etc/passwd`` becomes ``etc passwd`` after this filter,
    # so even a malicious agent prompt can't escape into a file path
    # via the library-save flow downstream.
    for ch in "/\\":
        cleaned = cleaned.replace(ch, " ")
    # Normalise whitespace runs to single spaces; lots of tools
    # render multiple spaces awkwardly in TUI tables.
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.strip()
    if not cleaned:
        return fallback
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned or fallback


# ── More pure helpers (moved from hub, Phase D) ─────────────────────────────
def _feat_bounds(feat, total: int) -> "tuple[int, int, int] | None":
    """Wrap-aware extraction of `(start, end, strand)` from a Biopython
    `SeqFeature`. The returned `(start, end)` follows the dict-feature
    convention: `end < start` signals an origin-spanning wrap; otherwise
    `end > start`. Returns `None` if the location has non-integer coords
    (UnknownPosition / BetweenPosition).

    For a `CompoundLocation` of exactly two parts whose outer bounds are
    `[0, ..)` and `[.., total)`, re-encodes as `(tail_start, head_end)`
    so callers can slice with `_slice_circular` and length with `_feat_len`.
    Other compound shapes flatten to outer bounds.

    Callers that read `int(feat.location.start)` / `int(feat.location.end)`
    directly silently flatten wrap features (Biopython returns `min(part.start)`
    for a CompoundLocation), so any code that later does `seq[s:e]` returns
    the BACKBONE GAP rather than the feature. Always route through this
    helper instead. See sacred invariant #9.
    """
    loc = getattr(feat, "location", None)
    if loc is None:
        return None
    # Preserve `loc.strand == None` (BioPython's "no strand info") as
    # 0 (arrowless) rather than coercing to 1. See `PlasmidMap._parse`
    # for the same fix and rationale.
    try:
        _raw_strand = getattr(loc, "strand", None)
        strand = int(_raw_strand) if _raw_strand is not None else 0
    except (TypeError, ValueError):
        strand = 0
    try:
        from Bio.SeqFeature import CompoundLocation
    except ImportError:
        CompoundLocation = None
    if CompoundLocation is not None and isinstance(loc, CompoundLocation):
        try:
            parts = sorted(loc.parts, key=lambda p: int(p.start))
            if (
                total > 0 and len(parts) == 2
                and int(parts[0].start) == 0
                and int(parts[-1].end) == total
                and int(parts[0].end) < int(parts[-1].start)
            ):
                # Origin wrap → (tail_start, head_end) so end < start.
                return int(parts[-1].start), int(parts[0].end), strand
            # Other compound shapes: outer bounds, lossy but oriented.
            return int(parts[0].start), int(parts[-1].end), strand
        except (TypeError, ValueError):
            return None
    try:
        return int(loc.start), int(loc.end), strand
    except (TypeError, ValueError):
        return None


def _name_modal_result(result: "_Any",
                       default_collection: str) -> "tuple[str, str] | None":
    """Normalise a `NamePlasmidModal` dismiss payload into
    ``(name, collection)`` — or ``None`` for cancel / empty.

    Accepts BOTH the collection-mode dict ``{"name", "collection"}`` and a
    bare name ``str`` (legacy callers + direct-dismiss tests), so the
    universal save callbacks stay robust regardless of how the modal was
    dismissed. ``collection`` defaults to ``default_collection`` when the
    payload doesn't carry one."""
    if isinstance(result, dict):
        nm = (result.get("name") or "").strip()
        if not nm:
            return None
        coll = (result.get("collection") or "").strip() or default_collection
        return (nm, coll)
    if isinstance(result, str):
        nm = result.strip()
        return (nm, default_collection) if nm else None
    return None
