"""splicecraft_primer — primer / mutagenesis design (Phase D, layer L1).

The start of the primer subsystem, extracted from the hub. Seeded with the PURE
site-directed-mutagenesis core: the _MUT_* E. coli codon-usage data, the
biophysics math (_mut_tm / _mut_hairpin_dg / _mut_homodimer_dg / _mut_gc_pct /
_mut_ends_gc, via primer3 thermodynamics), the mutation parse/translate, and the
constant-outer annealing-primer design. PURE — no record/UI/I-O coupling, deps =
biology._mut_revcomp + logging only.

CATASTROPHIC-CLASS subsystem (project_primer_design_catastrophic): primers must
BIND + DISPLAY exactly where they anneal. Guarded by test_mutagenize. The
record-coupled design orchestration (_mut_design_inner/_modified_outer/_extract_cds),
the UI preview/cursor, and the binding/display (_primer_binding_sites /
_rederive_primer_binding / _paint_primer_*) stay hub-side for a dedicated effort.
Re-exported by the hub so sc.<name> + every call site resolves unchanged.
"""
from __future__ import annotations

import re
from typing import Callable as _Callable

import splicecraft_state as _state
from splicecraft_logging import _log, _timed
from splicecraft_util import _normalize_dna_for_align
from splicecraft_biology import (
    _circ_slice, _iupac_compatible, _mut_revcomp, _rc, _search_subsequence,
    _slice_circular,
)


_MUT_CODON_USAGE = {
    "GGG": ("G", 44),  "GGA": ("G", 47),  "GGT": ("G", 109), "GGC": ("G", 171),
    "GAG": ("E", 94),  "GAA": ("E", 224), "GAT": ("D", 194), "GAC": ("D", 105),
    "GTG": ("V", 135), "GTA": ("V", 59),  "GTT": ("V", 86),  "GTC": ("V", 60),
    "GCG": ("A", 197), "GCA": ("A", 108), "GCT": ("A", 55),  "GCC": ("A", 162),
    "AGG": ("R", 8),   "AGA": ("R", 7),   "AGT": ("S", 37),  "AGC": ("S", 85),
    "AAG": ("K", 62),  "AAA": ("K", 170), "AAT": ("N", 112), "AAC": ("N", 125),
    "ATG": ("M", 127), "ATA": ("I", 19),  "ATT": ("I", 156), "ATC": ("I", 93),
    "ACG": ("T", 59),  "ACA": ("T", 33),  "ACT": ("T", 41),  "ACC": ("T", 117),
    "TGG": ("W", 55),  "TGT": ("C", 30),  "TGC": ("C", 41),
    "TAT": ("Y", 86),  "TAC": ("Y", 75),
    "TTG": ("L", 61),  "TTA": ("L", 78),  "TTT": ("F", 101), "TTC": ("F", 77),
    "TCG": ("S", 41),  "TCA": ("S", 40),  "TCT": ("S", 29),  "TCC": ("S", 28),
    "CGG": ("R", 21),  "CGA": ("R", 22),  "CGT": ("R", 108), "CGC": ("R", 133),
    "CAG": ("Q", 142), "CAA": ("Q", 62),  "CAT": ("H", 81),  "CAC": ("H", 67),
    "CTG": ("L", 240), "CTA": ("L", 27),  "CTT": ("L", 61),  "CTC": ("L", 54),
    "CCG": ("P", 137), "CCA": ("P", 34),  "CCT": ("P", 43),  "CCC": ("P", 33),
    "TAA": ("*", 9),   "TAG": ("*", 0),   "TGA": ("*", 5),
}


_MUT_CODON_TO_AA = {c: aa for c, (aa, _) in _MUT_CODON_USAGE.items()}


_MUT_STOPS       = {"TAA", "TAG", "TGA"}


def _mut_aa_to_codons() -> dict:
    from collections import defaultdict
    totals = defaultdict(int)
    for c, (aa, n) in _MUT_CODON_USAGE.items():
        totals[aa] += n
    result: dict = defaultdict(list)
    for c, (aa, n) in _MUT_CODON_USAGE.items():
        if aa == "*":
            continue
        result[aa].append((c, n / totals[aa] if totals[aa] else 0.0))
    for aa in result:
        result[aa].sort(key=lambda x: -x[1])
    return dict(result)


_MUT_AA_TO_CODONS = _mut_aa_to_codons()


_MUT_BSAI_FWD_TAIL = "CCCC" + "GGTCTCA" + "AATG"   # 15 nt; AATG = A(extra)+ATG ovhg


_MUT_BSAI_REV_TAIL = "CCCC" + "GGTCTCA" + "AACG"   # 15 nt; AACG = revcomp(CGTT)


_MUT_P3 = dict(mv_conc=50.0, dv_conc=1.5, dntp_conc=0.2, dna_conc=250.0)


def _mut_parse(s: str) -> tuple:
    """Parse a mutation string like 'W140F'. Returns (wt_aa, pos_1based, mut_aa)."""
    m = re.fullmatch(r"([A-Za-z\*])(\d+)([A-Za-z\*])", s.strip())
    if not m:
        raise ValueError(f"Cannot parse '{s}'. Use format: [WT][pos][MUT], e.g. W140F")
    return m.group(1).upper(), int(m.group(2)), m.group(3).upper()


def _mut_translate(dna: str) -> str:
    aa: list = []
    for i in range(0, len(dna) - 2, 3):
        c = dna[i:i+3].upper()
        if c in _MUT_STOPS:
            break
        aa.append(_MUT_CODON_TO_AA.get(c, "?"))
    return "".join(aa)


def _mut_tm(seq: str) -> float:
    try:
        import primer3
        return primer3.calc_tm(seq, **_MUT_P3)  # type: ignore[arg-type]
    except Exception:
        # Fall back to the crude 2×AT + 4×GC approximation when
        # primer3 is missing or raises (degenerate input, NaN config).
        # Log so a wave of failures shows up as one diagnosable
        # symptom in the bug-report bundle instead of silent
        # mis-temperature on every primer.
        _log.exception(
            "_mut_tm: primer3.calc_tm fell back to GC approximation "
            "for %d-mer", len(seq))
        gc = sum(1 for c in seq.upper() if c in "GC")
        at = sum(1 for c in seq.upper() if c in "AT")
        return 2 * at + 4 * gc


def _mut_hairpin_dg(seq: str) -> float:
    try:
        import primer3
        return primer3.calc_hairpin(seq, **_MUT_P3).dg  # type: ignore[arg-type]
    except Exception:
        _log.exception(
            "_mut_hairpin_dg: primer3.calc_hairpin raised on %d-mer; "
            "returning 0.0 (no secondary-structure penalty)", len(seq))
        return 0.0


def _mut_homodimer_dg(seq: str) -> float:
    try:
        import primer3
        return primer3.calc_homodimer(seq, **_MUT_P3).dg  # type: ignore[arg-type]
    except Exception:
        _log.exception(
            "_mut_homodimer_dg: primer3.calc_homodimer raised on "
            "%d-mer; returning 0.0", len(seq))
        return 0.0


def _mut_gc_pct(seq: str) -> float:
    s = seq.upper()
    return (s.count("G") + s.count("C")) / len(s) * 100 if seq else 0.0


def _mut_ends_gc(seq: str) -> bool:
    return bool(seq) and seq[-1].upper() in "GC"


def _mut_score_outer(anneal: str, target_tm: float = 60.0) -> float:
    t  = _mut_tm(anneal)
    gc = _mut_gc_pct(anneal)
    hp = _mut_hairpin_dg(anneal)
    return (
        abs(t - target_tm) * 2.0
        + (0 if _mut_ends_gc(anneal) else 4.0)
        + max(0, -hp - 1000) / 400.0
        + abs(gc - 50) * 0.1
    )


def _mut_design_fwd_anneal(dna: str) -> "dict | None":
    body = dna[3:]
    best = None
    for length in range(18, 28):
        anneal = body[:length]
        if len(anneal) < 18:
            continue
        s = _mut_score_outer(anneal)
        if best is None or s < best["score"]:
            best = {
                "anneal": anneal,
                "full":   _MUT_BSAI_FWD_TAIL + anneal,
                "tm_anneal": _mut_tm(anneal),
                "gc":     _mut_gc_pct(anneal),
                "score":  s,
            }
    return best


def _mut_design_rev_anneal(dna: str) -> "dict | None":
    end_rc = _mut_revcomp(dna)
    best = None
    for length in range(18, 28):
        anneal = end_rc[:length]
        if len(anneal) < 18:
            continue
        s = _mut_score_outer(anneal)
        if best is None or s < best["score"]:
            best = {
                "anneal": anneal,
                "full":   _MUT_BSAI_REV_TAIL + anneal,
                "tm_anneal": _mut_tm(anneal),
                "gc":     _mut_gc_pct(anneal),
                "score":  s,
            }
    return best


def _mut_design_outer(dna: str) -> dict:
    """Constant FWD/REV outer primers with BsaI-AATG / BsaI-AACG tails."""
    fwd = _mut_design_fwd_anneal(dna)
    rev = _mut_design_rev_anneal(dna)
    if fwd is None or rev is None:
        raise RuntimeError("CDS is too short to design outer primers (need ≥ 21 nt).")
    return {
        "fwd": fwd, "rev": rev,
        "b3_overhang": "AATG",
        "b5_overhang": "CGTT",
        "fwd_anneal_start": 3,
    }


# ── circular primer-binding re-derivation (Phase D, moved from hub) ─────────
# THE catastrophic 'where does a primer ACTUALLY land on the (circular) map'
# core: longest 3'-anchored template match, flap excluded, wrap-aware,
# hint-tie-broken. Verified by the real-plasmid golden (site-containment +
# origin-rotation invariance + multi-site hint). pos_end==total => ends at origin.
# Shortest contiguous match we'll trust as a genuine primer binding when
# re-deriving from the template (Domesticator binding is ≥18 bp; 12 is a
# safe floor that still rejects spurious short coincidences).
_PRIMER_REBIND_MIN: int = 12


def _rederive_primer_binding(primer_seq: str, strand: int, template: str,
                             total: int, hint_start: int = 0,
                             *, circular: bool = True,
                             ) -> "tuple[int, int] | None":
    """Find where a primer's annealing region ACTUALLY binds the
    (circular) ``template``, so a stale / mis-saved ``pos_start`` /
    ``pos_end`` can't park the primer off its true site on the map.

    Returns ``(pos_start, pos_end)`` in top-strand coordinates —
    half-open, with ``pos_end < pos_start`` when the binding wraps the
    origin (and ``pos_end == total`` when it ends exactly at the
    origin). Returns ``None`` when no clean binding is found, so the
    caller keeps the stored positions.

    The binding region is the LONGEST contiguous stretch at the primer's
    3' end that matches the template — forward: the primer's own 3'
    suffix on the top strand; reverse: that suffix's reverse-complement
    on the top strand (which equals a prefix of ``rc(primer)``). The 5'
    flap (enzyme site / spacer / fusion overhang) is whatever doesn't
    match. When a primer legitimately binds more than one site, the
    occurrence nearest ``hint_start`` (the stored position) wins, so the
    primer still lands where the user designed it."""
    seq = (primer_seq or "").upper()
    if not seq or not template or total <= 0:
        return None
    # Circular search space: append the wrap-around head so a binding
    # that spans the origin is found as one contiguous slice. Cap the
    # tail at the primer length so we never scan more than necessary.
    # A LINEAR template's ends don't join, so a primer can't anneal
    # across them (real-world affinity) — search the bare template.
    if circular:
        tail = template[:max(0, min(len(seq), total) - 1)]
        aug = template + tail
    else:
        aug = template
    rc = _rc(seq) if strand < 0 else ""
    max_L = min(len(seq), total)
    hint = (int(hint_start) % total) if total else 0
    for L in range(max_L, _PRIMER_REBIND_MIN - 1, -1):
        target = (seq[len(seq) - L:] if strand >= 0 else rc[:L])
        starts: list[int] = []
        i = aug.find(target)
        while i != -1 and i < total:
            starts.append(i)
            i = aug.find(target, i + 1)
        if not starts:
            continue
        # Closest occurrence to the stored hint (circular distance).
        def _cdist(p: int) -> int:
            d = abs(p - hint)
            return min(d, total - d)
        m = min(starts, key=_cdist)
        pos_end = m + L
        if pos_end > total:          # wraps the origin
            pos_end -= total
        return (m, pos_end)
    # No clean contiguous suffix — keep the stored positions (caller decides).
    # NB: we deliberately do NOT slide the whole primer to a best-offset here.
    # `_attach_pcr_primers_to_record` calls with hint_start=0, so a best-offset
    # window would sit at the origin and could anchor a non-binding primer
    # there — a mis-placed cloning primer is catastrophic. The length-short
    # *stored*-feature repair (a fragment built 1 bp short of its primer) is
    # handled in `PlasmidMap._parse`, where start/end/length are all known.
    return None


# ── primer-CHECK binding finder (Phase D, moved from hub) ──────────────────
# Mismatch-tolerant 3'-anchored binding-site list (which sites a primer binds, with
# identity %) + the confidence glyph. Verified by the real-plasmid golden
# (site lists + origin-rotation invariance). Uses the biology align primitives.
_PRIMER_CHECK_SEED_LEN  = 12     # exact 3'-anchor required for a binding call


_PRIMER_CHECK_MAX_SITES = 200    # per-template binding-site cap (repeat guard)


def _primer_binding_sites(
    primer: str, top: str, total: int, *,
    circular: bool = True,
    seed_len: int = _PRIMER_CHECK_SEED_LEN,
    min_identity_pct: float = 0.0,
    max_sites: int = _PRIMER_CHECK_MAX_SITES,
) -> "list[dict]":
    """3'-anchored binding sites of `primer` on the top strand `top`
    (length `total`, ASSUMED pre-normalised — uppercase IUPAC, no whitespace).

    A site requires an EXACT match over the primer's 3'-terminal `seed_len`
    bases (clamped to the primer length); identity is then computed over the
    FULL primer. Returns sites sorted best-first (identity desc)::

        {"strand": +1 | -1,   # +1 forward (primer == top-strand sense),
                              # -1 reverse (primer anneals TO the top strand)
         "foot_start": int,   # 0-based footprint start on the top strand,
                              # canonical [0, total); footprint spans
                              # [foot_start, foot_start+length) around the circle
         "length": int,       # primer length
         "ident_pct": float,  # full-primer identity 0..100
         "mismatches": int}

    The primer's 3' end is the HIGH-coord edge of a forward footprint and the
    foot_start (LOW-coord) edge of a reverse footprint — so an amplicon runs
    from a forward site's foot_start to a reverse site's foot_start+length.

    Raises ValueError (via `_normalize_dna_for_align`) on a foreign character
    in `primer`.
    """
    P = _normalize_dna_for_align(primer or "")
    L = len(P)
    if L == 0 or total <= 0 or L > total:
        return []
    seed = max(1, min(int(seed_len), L))
    anchor = P[-seed:]
    try:
        # Exact 3'-anchor hits on BOTH strands, wrap-aware, via the tested
        # matcher. A '+' hit is the 3' end of a FORWARD-role primer; a '-' hit
        # (rc(anchor) on the top strand) is the 3' end of a REVERSE-role primer.
        hits = _search_subsequence(
            top, anchor, max_mismatches=0,
            circular=circular, both_strands=True,
        )
    except ValueError:
        return []
    iupac_ok = _iupac_compatible
    sites: "list[dict]" = []
    seen: "set[tuple[int, int]]" = set()
    for h in hits:
        hs, he, strand = h["start"], h["end"], h["strand"]
        if strand == "+":
            foot_start = he - L           # 5' edge of the forward footprint
            s_strand = 1
        else:
            foot_start = hs               # 3'/left edge of the reverse footprint
            s_strand = -1
        if circular:
            window = _circ_slice(top, foot_start, L, total)
        else:
            if foot_start < 0 or foot_start + L > total:
                continue                  # primer hangs off a linear end
            window = top[foot_start:foot_start + L]
        if len(window) != L:
            continue
        oriented = window if s_strand == 1 else _rc(window)
        mm = 0
        for i in range(L):
            if not iupac_ok(P[i], oriented[i]):
                mm += 1
        ident = 100.0 * (L - mm) / L
        if ident < min_identity_pct:
            continue
        canon = foot_start % total
        key = (canon, s_strand)
        if key in seen:
            continue
        seen.add(key)
        sites.append({
            "strand":     s_strand,
            "foot_start": canon,
            "length":     L,
            "ident_pct":  ident,
            "mismatches": mm,
        })
        if len(sites) >= max_sites:
            break
    sites.sort(key=lambda s: (-s["ident_pct"], s["foot_start"]))
    return sites


def _primer_check_confidence(pct: "float | int | None") -> "tuple[str, str]":
    """Map a primer-binding / amplicon identity to a (glyph, Rich-colour)
    confidence badge for the Primer Check results table. Mirrors the
    alignment-status tiers so ✓/⚠/~/✗ read consistently app-wide."""
    if not isinstance(pct, (int, float)):
        return ("?", "white")
    v = float(pct)
    if v >= 99.999:
        return ("✓", "bright_cyan")
    if v >= 90.0:
        return ("✓", "green")
    if v >= 75.0:
        return ("⚠", "yellow")
    if v >= 60.0:
        return ("~", "dark_orange")
    return ("✗", "red")


# ── generic primer design (Phase D, moved from hub) ────────────────────────
# Tm + binding-region selection + the cloning / detection / generic primer
# designers (primer3 thermodynamics; enzyme catalog via _state._all_enzymes_hook).
# Verified by the real-plasmid design golden (byte-identical output). The GB /
# domestication-scrub designers (_design_gb_primers / _scrub_*) stay hub-side.
def _primer_tm(seq: str) -> "float | None":
    """Melting temperature (°C, 1 dp) of an oligo — primer3's nearest-neighbour
    model when available, else the 2(A+T)+4(G+C) rule. Module-level so the CSV
    import (and any caller) can compute a Tm without the local ``_calc_tm``
    closures the design / .dna paths use. Returns None for empty input."""
    s = (seq or "").strip().upper()
    if not s:
        return None
    try:
        import primer3
        return round(float(primer3.calc_tm(s)), 1)
    except Exception:
        gc = sum(1 for c in s if c in "GC")
        at = sum(1 for c in s if c in "AT")
        return float(2 * at + 4 * gc)


# Hard cap on the TOTAL synthesised oligo length: the 5' tail (pad + enzyme
# site + spacer + overhang) PLUS the 3' binding region. Oligo-synthesis cost
# is driven by the whole oligo and standard (cheap) synthesis tops out around
# here, so each design grows its binding region to reach `target_tm` but
# never past `_PRIMER_MAX_OLIGO_LEN − len(tail)` (2026-06-09, user spec).
# For a low-GC (AT-rich — e.g. codon-optimised for a low-GC host like
# E. faecium) part this lets the binding extend well past the old fixed
# 25 nt so it can actually reach ~60 °C; for a high-GC part the "closest to
# target" pick stays short. If even the capped binding can't reach
# `target_tm`, the closest is returned + flagged (`_GB_TM_OFFTARGET_MARGIN`)
# rather than bloating the oligo past the synthesis budget.
_PRIMER_MAX_OLIGO_LEN = 50


def _binding_max_len(tail_len: int, min_len: int = 18) -> int:
    """Largest binding-region length that keeps the TOTAL oligo
    (`tail_len` + binding) within `_PRIMER_MAX_OLIGO_LEN`. Never returns
    below `min_len`: an over-long tail can't shrink the binding below the
    minimum usable annealing length — that (rare) design is surfaced by the
    downstream low-Tm advisory instead of an unusably short arm."""
    return max(min_len, _PRIMER_MAX_OLIGO_LEN - max(0, int(tail_len)))


def _pick_binding_region(seq: str, target_tm: float = 60.0,
                         min_len: int = 18, max_len: int = 25) -> tuple[str, float]:
    """Return the prefix of `seq` (length min_len..max_len) whose Tm is
    closest to `target_tm`. Uses primer3-py's SantaLucia Tm calculation.

    Returns (binding_sequence, tm). If primer3-py is not installed, falls
    back to a crude 2+4 rule estimate.
    """
    # Type the dispatcher as a `(str) -> float` Callable so pyright can
    # accept both primer3.calc_tm (which has a richly-typed signature
    # with extra defaulted kwargs) and the fallback approximation
    # below. Using two separate names avoids the param-name mismatch
    # pyright flags when a `def _tm(s)` re-defines the same binding.
    def _tm_fallback(s: str) -> float:
        gc = sum(1 for c in s.upper() if c in "GC")
        at = sum(1 for c in s.upper() if c in "AT")
        return float(2 * at + 4 * gc)
    _tm: "_Callable[..., float]"
    try:
        import primer3
        _tm = primer3.calc_tm
    except ImportError:
        _tm = _tm_fallback

    # Defensive init: if the caller forgot the len(seq) >= min_len guard,
    # the loop below won't execute and we'd otherwise return Tm=0 with a
    # too-short binding. Compute Tm for whatever is there so downstream
    # validation (low Tm, short primer) still trips honestly.
    best_seq = seq[:max(min_len, 1)]
    best_tm  = _tm(best_seq) if best_seq else 0.0
    best_diff = float("inf")
    # Pick the length whose Tm is CLOSEST to target — minimising
    # |tm - target|. When `target_tm` is unreachable within the
    # [min_len, max_len] window (too high for an AT-rich arm even at
    # max_len, too low for a GC-rich arm even at min_len), the nearest
    # achievable Tm wins — the longest candidate when the target sits
    # above the whole window, the shortest when below — so the caller
    # always gets the next-best binding instead of a failure.
    for n in range(min_len, min(max_len + 1, len(seq) + 1)):
        candidate = seq[:n]
        tm = _tm(candidate)
        diff = abs(tm - target_tm)
        if diff < best_diff:
            best_seq, best_tm, best_diff = candidate, tm, diff
    return best_seq, best_tm


@_timed("op.primer3.detection_design")
def _design_detection_primers(
    template_seq: str,
    target_start: int,
    target_end: int,
    product_min: int = 450,
    product_max: int = 550,
    target_tm: float = 60.0,
    primer_len: int = 25,
) -> dict:
    """Design diagnostic PCR primers WITHIN a selected region using Primer3.

    Both primers bind INSIDE the region (target_start..target_end) and the
    amplicon is product_min..product_max bp. This is the standard approach
    for detection/screening primers: you pick a gene or feature and want a
    ~500 bp diagnostic band from within it.

    Uses SEQUENCE_INCLUDED_REGION (not SEQUENCE_TARGET) so Primer3 places
    both primers inside the selected region rather than trying to flank it.

    Returns a dict with keys: fwd_seq, rev_seq, fwd_tm, rev_tm, fwd_pos,
    rev_pos, product_size, or an 'error' key on failure.
    """
    import primer3
    seq   = template_seq.upper()
    total = len(seq)
    wraps = target_end < target_start

    # Primer3 is linear-only. For a wrap region we rotate the template
    # so the region becomes contiguous at [0, region_len), run Primer3,
    # then unrotate the returned positions via (coord + rotation) % total.
    if wraps:
        rotation    = target_start
        p3_seq      = seq[target_start:] + seq[:target_start]
        region_len  = (total - target_start) + target_end
        p3_start    = 0
    else:
        rotation    = 0
        p3_seq      = seq
        region_len  = target_end - target_start
        p3_start    = target_start

    if region_len < 1:
        return {"error": "Target region is empty."}
    if region_len < product_min:
        return {
            "error": f"Region ({region_len} bp) is shorter than minimum "
                     f"product size ({product_min} bp). Select a larger "
                     f"region or reduce the product size."
        }

    try:
        result = primer3.design_primers(
            seq_args={
                "SEQUENCE_TEMPLATE": p3_seq,
                # INCLUDED_REGION: primers must bind WITHIN this region.
                # This is the key difference from SEQUENCE_TARGET (which
                # would require primers to sit OUTSIDE the target).
                "SEQUENCE_INCLUDED_REGION": [p3_start, region_len],
            },
            global_args={
                "PRIMER_TASK": "generic",
                "PRIMER_PICK_LEFT_PRIMER": 1,
                "PRIMER_PICK_RIGHT_PRIMER": 1,
                # primer_len is the OPTIMAL length — Primer3 will expand
                # or contract within the min/max range to find the best Tm.
                "PRIMER_OPT_SIZE": primer_len,
                "PRIMER_MIN_SIZE": max(15, primer_len - 8),
                "PRIMER_MAX_SIZE": min(36, primer_len + 8),
                "PRIMER_OPT_TM": target_tm,
                "PRIMER_MIN_TM": target_tm - 3,
                "PRIMER_MAX_TM": target_tm + 3,
                "PRIMER_PRODUCT_SIZE_RANGE": [[product_min, product_max]],
                "PRIMER_NUM_RETURN": 1,
            },
        )
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        # Sweep #25 (2026-05-23): `(OSError, Exception)` is `Exception`
        # since `Exception` subsumes `OSError` — that tuple was a
        # bare-except in disguise (the INV-65 grep for `(AttributeError,
        # Exception)` missed the `(OSError, Exception)` shape).
        # Narrowed to the actual Primer3 failure modes: missing lib
        # (OSError), bad params (ValueError), and the misc errors the
        # C wrapper raises (RuntimeError / KeyError / TypeError when
        # the result dict shape doesn't match).
        return {"error": f"Primer3 rejected parameters: {exc}"}

    n_found = result.get("PRIMER_PAIR_NUM_RETURNED", 0)
    if n_found == 0:
        explain = result.get("PRIMER_LEFT_EXPLAIN", "")
        return {"error": f"Primer3 found no valid pair. {explain}"}

    fwd_pos = result["PRIMER_LEFT_0"]     # (start, length) on p3_seq
    rev_pos = result["PRIMER_RIGHT_0"]    # (start, length) — start is 3' end on p3_seq

    # Unrotate positions back to original-template coordinates. Apply
    # `% total` ONLY when the target region wraps origin (Primer3 ran
    # on a rotated template). On the linear path, `rotation == 0` and
    # the modulo is a no-op for most values BUT silently flips a primer
    # 3'-ending at exact bp `total - 1` into `rev_end = total % total = 0`
    # — which `_add_selected_to_map` then reads as a wrap-encoded primer
    # (`p_end < p_start`) and stamps a wrap CompoundLocation with a 0-bp
    # head part. Mirrors `_design_cloning_primers_raw` (gate-on-wraps).
    if wraps:
        fwd_start = (fwd_pos[0] + rotation) % total
        fwd_end   = (fwd_pos[0] + fwd_pos[1] + rotation) % total
        rev_start = (rev_pos[0] - rev_pos[1] + 1 + rotation) % total
        rev_end   = (rev_pos[0] + 1 + rotation) % total
    else:
        fwd_start = fwd_pos[0]
        fwd_end   = fwd_pos[0] + fwd_pos[1]
        rev_start = rev_pos[0] - rev_pos[1] + 1
        rev_end   = rev_pos[0] + 1

    return {
        "fwd_seq":      result["PRIMER_LEFT_0_SEQUENCE"],
        "rev_seq":      result["PRIMER_RIGHT_0_SEQUENCE"],
        "fwd_tm":       round(result["PRIMER_LEFT_0_TM"], 1),
        "rev_tm":       round(result["PRIMER_RIGHT_0_TM"], 1),
        "fwd_pos":      (fwd_start, fwd_end),
        "rev_pos":      (rev_start, rev_end),
        "product_size": result["PRIMER_PAIR_0_PRODUCT_SIZE"],
    }


@_timed("op.primer3.cloning_design")
def _design_cloning_primers_raw(
    template_seq: str,
    start: int,
    end: int,
    site_5: str,
    site_3: str,
    name_5: str = "5'site",
    name_3: str = "3'site",
    target_tm: float = 60.0,
    padding: str = "GCGC",
) -> dict:
    """Design cloning primers with arbitrary recognition-site tails + padding.

    Accepts raw site sequences (not just NEB enzyme names) so users can
    enter custom cutter sequences.

    Structure (5'→3'):
        Forward: [padding] [5' site]    [binding region →]
        Reverse: [padding] [RC 3' site] [← binding region RC]

    Returns dict with keys: fwd_full, rev_full, fwd_binding, rev_binding,
    fwd_tm, rev_tm, re_5prime, re_3prime, site_5, site_3, insert_seq,
    fwd_pos, rev_pos, or 'error'.
    """
    site_5 = site_5.upper()
    site_3 = site_3.upper()
    if not site_5 or not set(site_5) <= set("ACGTRYWSMKBDHVN"):
        return {"error": f"Invalid 5' site sequence: {site_5!r}"}
    if not site_3 or not set(site_3) <= set("ACGTRYWSMKBDHVN"):
        return {"error": f"Invalid 3' site sequence: {site_3!r}"}

    total  = len(template_seq)
    insert = _slice_circular(template_seq.upper(), start, end)
    wraps  = end < start
    if len(insert) < 18:
        return {"error": "Region too short (< 18 bp)."}

    # Cap each binding so the TOTAL oligo (tail + binding) stays within
    # `_PRIMER_MAX_OLIGO_LEN`; tail = padding + RE site.
    fwd_bind, fwd_tm = _pick_binding_region(
        insert, target_tm, max_len=_binding_max_len(len(padding) + len(site_5)))
    rev_bind, rev_tm = _pick_binding_region(
        _rc(insert), target_tm,
        max_len=_binding_max_len(len(padding) + len(site_3)))

    fwd_full = padding + site_5 + fwd_bind
    rev_full = padding + _rc(site_3) + rev_bind

    if wraps:
        fwd_pos = (start, (start + len(fwd_bind)) % total)
        rev_pos = ((end - len(rev_bind)) % total, end)
    else:
        fwd_pos = (start, start + len(fwd_bind))
        rev_pos = (end - len(rev_bind), end)

    return {
        "fwd_full":    fwd_full,
        "rev_full":    rev_full,
        "fwd_binding": fwd_bind,
        "rev_binding": rev_bind,
        "fwd_tm":      round(fwd_tm, 1),
        "rev_tm":      round(rev_tm, 1),
        "re_5prime":   name_5,
        "re_3prime":   name_3,
        "site_5":      site_5,
        "site_3":      site_3,
        "insert_seq":  insert,
        "fwd_pos":     fwd_pos,
        "rev_pos":     rev_pos,
    }


def _design_cloning_primers(
    template_seq: str,
    start: int,
    end: int,
    re_5prime: str,
    re_3prime: str,
    target_tm: float = 60.0,
    padding: str = "GCGC",
) -> dict:
    """Design cloning primers using enzyme names from the combined
    catalog (built-in NEB ∪ user-added custom). Delegates to
    _design_cloning_primers_raw after looking up recognition sites."""
    catalog = _state._all_enzymes_hook()
    if re_5prime not in catalog:
        return {"error": f"Unknown enzyme: {re_5prime}"}
    if re_3prime not in catalog:
        return {"error": f"Unknown enzyme: {re_3prime}"}
    site_5, _, _ = catalog[re_5prime]
    site_3, _, _ = catalog[re_3prime]
    return _design_cloning_primers_raw(
        template_seq, start, end, site_5, site_3,
        name_5=re_5prime, name_3=re_3prime,
        target_tm=target_tm, padding=padding,
    )


@_timed("op.primer3.generic_design")
def _design_generic_primers(
    template_seq: str,
    start: int,
    end: int,
    target_tm: float = 60.0,
) -> dict:
    """Design simple binding primers (no tails, no RE sites, no overhangs).

    Forward primer: optimal binding region at the start of the region.
    Reverse primer: optimal binding region at the end (reverse-complement).
    """
    total  = len(template_seq)
    insert = _slice_circular(template_seq.upper(), start, end)
    wraps  = end < start
    if len(insert) < 18:
        return {"error": "Region too short (< 18 bp)."}
    # No tail (binding-only primers) → the whole oligo IS the binding, so it
    # may grow up to the full `_PRIMER_MAX_OLIGO_LEN`.
    fwd_bind, fwd_tm = _pick_binding_region(
        insert, target_tm, max_len=_binding_max_len(0))
    rev_bind, rev_tm = _pick_binding_region(
        _rc(insert), target_tm, max_len=_binding_max_len(0))
    if wraps:
        fwd_pos = (start, (start + len(fwd_bind)) % total)
        rev_pos = ((end - len(rev_bind)) % total, end)
    else:
        fwd_pos = (start, start + len(fwd_bind))
        rev_pos = (end - len(rev_bind), end)
    return {
        "fwd_seq":  fwd_bind,
        "rev_seq":  rev_bind,
        "fwd_tm":   round(fwd_tm, 1),
        "rev_tm":   round(rev_tm, 1),
        "fwd_pos":  fwd_pos,
        "rev_pos":  rev_pos,
    }
