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
import functools as _functools
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


# ── Export/collection/color/DNA-normalise pure helpers (moved, Phase D) ─────
_IUPAC_NUC_CHARS = frozenset("ACGTUMRWSYKVHDBN")


_IUPAC_NUC_PATTERN = re.compile(r"^[ACGTUMRWSYKVHDBN]+$")


_FASTA_HEADER_PATTERN = re.compile(r"^>[^\n]*\n?", re.MULTILINE)


_SCRUB_PATTERN     = re.compile(r"[\s\d]+")


_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$|^#[0-9A-Fa-f]{3}$")


_MAX_COLLECTION_NAME_LEN = 200


def _normalize_dna_for_align(seq: str) -> str:
    """Scrub FASTA header lines + whitespace + digits from ``seq``,
    uppercase, map ``U`` → ``T`` (so RNA pasted into a DNA field
    aligns instead of every base mismatching), and validate that every
    remaining character is an IUPAC nucleotide code
    (``ACGTMRWSYKVHDBN`` after the U-mapping).

    Covers the common bad-paste failure modes upstream of the C-loop:

      * FASTA pasted as-is — ``>name`` line gets stripped along with
        the embedded newlines, leaving only the sequence body.
      * GenBank ``ORIGIN`` block — leading bp position numbers, line
        wraps, spaces between every 10 bp.
      * Protein pasted in a DNA-only field — would have ``EFILPQ`` etc.
        Pre-fix Biopython chewed through these and produced a
        "no alignment" result with no clue why.
      * RNA consensus pasted into a DNA target — every ``U`` used to
        mismatch ``T`` for ~0% identity with no hint. Now mapped to
        ``T`` so the alignment is biologically meaningful.

    Returns the cleaned string. Raises ``ValueError`` on a foreign
    character (with the offending char(s) named in the message so
    the user can find them in the source).
    """
    if not seq:
        return ""
    # Two-pass scrub: FASTA headers first (line-anchored), then
    # whitespace/digits across the whole string. Order matters —
    # stripping `\n` first would let the header text leak into the
    # body and trip the IUPAC check on a leading "MYPLASMID" etc.
    s = _FASTA_HEADER_PATTERN.sub("", seq)
    s = _SCRUB_PATTERN.sub("", s).upper()
    if not s:
        return ""
    # 2026-05-27: silent RNA→DNA. The IUPAC alphabet still admits
    # ``U`` syntactically (frozenset includes it) but the aligner +
    # state classification work on DNA bases — leaving ``U`` would
    # mismatch every paired ``T``. The remap is post-uppercase so
    # both ``u`` and ``U`` in the source collapse to ``T``.
    if "U" in s:
        s = s.replace("U", "T")
    if not _IUPAC_NUC_PATTERN.match(s):
        bad = sorted(set(s) - _IUPAC_NUC_CHARS)
        raise ValueError(
            f"sequence contains non-IUPAC nucleotide character(s): "
            f"{', '.join(repr(c) for c in bad[:6])}"
            f"{' (truncated)' if len(bad) > 6 else ''}"
        )
    return s


def _safe_color_for_picker(raw) -> "str | None":
    """Filter a raw color value to something ColorPickerModal can
    safely consume. The picker assigns the value to
    `styles.background` which raises `StyleValueError` on palette
    references like `color(39)` and on malformed hex strings like
    `#OLDCOL`. Members / library entries CAN carry these
    (canvas `_parse` stamps palette refs at load time, hand-
    edited `.gb` files can carry garbage in `ApEinfo_fwdcolor`),
    so we normalize on the way INTO the picker — None means
    "Auto" / no starting color, and the picker presents its full
    palette fresh.

    Validation: must match `#RGB` or `#RRGGBB`. Anything else
    (palette refs, named colours, mangled hex) → None.

    Sweep #30 (2026-05-26 hardening): defends against the
    `StyleValueError: Invalid color value '...'` crash reported
    when opening per-row color picker on a feature that had a
    palette-ref color or a malformed hex."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    if _HEX_COLOR_RE.match(raw) is None:
        return None
    return raw


def _check_export_extension(path: Path, allowed: "tuple[str, ...]",
                              fmt: str) -> "str | None":
    """Enforce an extension whitelist on agent export targets. Without
    this an agent can write `/home/user/.bashrc` as GenBank text (which
    starts with `LOCUS` — not executable but visually hostile / footgun-
    y) or write a `.sh` extension that the user later double-clicks by
    accident. Matches the GUI ExportModal's "save as <FMT>" behaviour
    where the user can't pick an arbitrary extension."""
    suffix = path.suffix.lower()
    if suffix in allowed:
        return None
    return (
        f"refusing to write {fmt} to {path.name!r}: extension must be "
        f"one of {allowed}"
    )


def _normalize_collection_name(s: "str | None") -> "str | None":
    """Trim, strip control chars, cap length, reject blank. Returns
    None on empty input so the caller can 400 the request."""
    name = _sanitize_label(s, max_len=_MAX_COLLECTION_NAME_LEN)
    return name or None


# ── Feature-label + note-sanitise pure helpers (moved from hub, Phase D) ────
_FEAT_LABEL_DISPLAY_MAX = 28


_NOTE_CTRL_RE = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]+"
)


def _feat_label_full(feat) -> str:
    """Canonical, UNtruncated feature label — the value the feature data
    model (`PlasmidMap._parse`) stores and that cloning / export / the
    agent API carry forward. Whitespace is collapsed and a generic
    "unnamed protein product; <gene>" note is reduced to <gene>.

    DISPLAY callers truncate to their own width (the map + seq-panel via
    `_feat_decorated_label`, the sidebar via its own `[:14]`); `_feat_label`
    is the `_FEAT_LABEL_DISPLAY_MAX`-char display wrapper. Baking that cap
    in here used to corrupt persisted data — a lifted operon's six genes
    ("…; luxC" … "…; luxG") all collapsed to the identical
    "unnamed protein product; lux" because the gene letter sits at char 29,
    past the cap, so the cloned plasmid stored six indistinguishable CDS
    bars (user-reported)."""
    for q in ("label", "gene", "product", "standard_name", "note", "bound_moiety"):
        if q in feat.qualifiers:
            v = feat.qualifiers[q]
            # Biopython normally wraps qualifier values in a 1+ element
            # list, but malformed GenBank files can produce empty lists
            # or bare strings. Guard both.
            if isinstance(v, list):
                if not v:
                    continue
                s = v[0]
            else:
                s = v
            if not isinstance(s, str):
                continue
            # Collapse whitespace characters (newline, tab, vertical tab)
            # into single spaces so a multi-line /note="…" qualifier
            # doesn't break the sidebar row or clobber the map label.
            # Then strip and fall through if the result is empty.
            s = " ".join(s.split())
            if s:
                return _surface_placeholder_gene(s)
    return feat.type


def _feat_label(feat) -> str:
    """Display label: `_feat_label_full` truncated to the map / sidebar
    width cap (`_FEAT_LABEL_DISPLAY_MAX`). Tested at 28 chars by
    `test_real_label_truncates_at_28`."""
    return _feat_label_full(feat)[:_FEAT_LABEL_DISPLAY_MAX]


def _sanitize_note(s: "str | None", *, max_len: int = 8000) -> str:
    """Clean a feature ``/note`` body: strip dangerous control bytes
    (preserves `\\t` and `\\n` so multi-paragraph Markdown survives),
    cap at `max_len` characters, trim trailing whitespace.

    Type-strict like `_sanitize_label` — a JSON dict / int payload
    becomes empty rather than `str()`-coerced. Empty / None / non-
    string input → empty string. The 8 KB cap matches typical
    GenBank ``/note`` conventions and prevents adversarial / accidental
    pasted blobs from bloating `.gb` exports or stalling the Markdown
    parser. Callers split on blank-line paragraphs after sanitizing,
    so the cap applies to the combined note body, not per-line.
    """
    if not isinstance(s, str) or not s:
        return ""
    s = _NOTE_CTRL_RE.sub("", s).rstrip()
    return s[:max_len]


# ── Placeholder-gene helper (completes the _feat_label closure, Phase D) ────
_GENERIC_PRODUCT_PLACEHOLDERS: tuple[str, ...] = (
    "unnamed protein product",
)


def _surface_placeholder_gene(s: str) -> str:
    """Reduce a generic ``"unnamed protein product; luxC"`` placeholder to
    its trailing identifier (``"luxC"``). A bare placeholder with nothing
    meaningful after it is returned unchanged. Case-insensitive match on
    the placeholder; the surfaced remainder keeps its original case."""
    low = s.lower()
    for ph in _GENERIC_PRODUCT_PLACEHOLDERS:
        if low.startswith(ph):
            rest = s[len(ph):].lstrip(" \t;:,-/|")
            return rest if rest else s
    return s


# ── Primer-Tm + single-record-pick pure helpers (moved, Phase D) ────────────
@_functools.lru_cache(maxsize=512)
def _primer_tm_safe(seq: str) -> "float | None":
    """Memoized, defensive primer3 Tm calculation. Returns None if
    primer3 is unavailable, the seq is outside the calc's useful
    range (5..200 bp), or the underlying call raises. Caller
    renders a `—` placeholder for None.

    Cached because `PrimerEditModal._seq_changed` repaints the
    stats line on every keystroke — a user typing/backspacing
    re-hits the same intermediate strings, and the nearest-
    neighbor thermodynamics is the slow part of the path.
    """
    s = (seq or "").upper()
    if not (5 <= len(s) <= 200):
        return None
    try:
        import primer3
        return float(primer3.calc_tm(s))
    except (ImportError, OSError, ValueError, RuntimeError, TypeError):
        return None


def _pick_single_record(records: list, source: str):
    """Given a list of SeqRecords, return the single one if there's exactly
    one, else raise ValueError with a user-friendly message. Used by both
    NCBI fetch and file load so the error text is consistent.
    """
    if not records:
        raise ValueError(
            f"{source} contained no GenBank records. Is it a valid .gb/.gbk file?"
        )
    if len(records) > 1:
        ids = ", ".join(r.id for r in records[:3])
        more = f" (and {len(records) - 3} more)" if len(records) > 3 else ""
        raise ValueError(
            f"{source} contains {len(records)} records — SpliceCraft loads "
            f"one plasmid at a time. Split the file or extract a single "
            f"record first (found: {ids}{more})."
        )
    return records[0]
