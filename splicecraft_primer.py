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

from splicecraft_logging import _log
from splicecraft_biology import _mut_revcomp, _rc


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
