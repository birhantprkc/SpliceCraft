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
