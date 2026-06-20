"""Real-plasmid regression tests for `_rederive_primer_binding` — the catastrophic
'where does a primer ACTUALLY land on the circular map' core
([[project_primer_design_catastrophic]]).

Uses a REAL .dna plasmid fixture (FFE 1, 2579 bp circular). Designs forward /
reverse / origin-wrapping / ends-at-origin primers (a 5' flap + a 3' annealing
window taken from a KNOWN site) and asserts two invariants that don't depend on
predicting coincidental flap matches:

  1. SITE CONTAINMENT — the re-derived binding CONTAINS the designed annealing
     window (the primer lands at the site we built it from).
  2. ORIGIN-ROTATION INVARIANCE — rotating the plasmid origin by R shifts every
     binding by exactly -R (mod n); a primer binds the same PHYSICAL site no
     matter where the origin sits, including rotations that drop the origin
     adjacent to / inside / at the end of a binding site.

Plus a synthetic two-site template for the multi-site hint (nearest-occurrence)
rule and its rotation invariance. `pos_end == total` denotes "ends exactly at the
origin" (NOT 0), so rotation arithmetic keeps pos_end in [1, total].
"""
from __future__ import annotations

import re

import pytest

import splicecraft as sc

_FIXTURE = "tests/FFE 1 ENTRY UPD.dna"
_FLAP = "CAGGAAACAGCTATGAC"          # synthetic 5' flap


@pytest.fixture(scope="module")
def template() -> str:
    rec = sc.load_genbank(_FIXTURE)
    assert rec.annotations.get("topology") == "circular"
    return str(rec.seq).upper()


def _rot(seq: str, R: int) -> str:
    R %= len(seq)
    return seq[R:] + seq[:R]


def _anneal_at(T: str, p: int, L: int) -> str:
    n = len(T)
    return T[p:p + L] if p + L <= n else T[p:] + T[:(p + L) - n]


def _covered(s: int, e: int, n: int) -> set:
    if e == s:
        return set()
    if e > s:
        return set(range(s, e))
    return set(range(s, n)) | set(range(0, e))   # wrap (e in [1, n))


def _contains_window(b, p: int, L: int, n: int) -> bool:
    s, e = b
    cov = _covered(s, n, n) if e == n else _covered(s, e, n)
    return all(((p + k) % n) in cov for k in range(L))


def _shift(b, R: int, n: int):
    # pos_start in [0, n-1]; pos_end in [1, n] (n == "ends exactly at the origin")
    if b is None:
        return None
    return ((b[0] - R) % n, ((b[1] - 1 - R) % n) + 1)


# label, p, L, strand
_SITES = [
    ("fwd_mid", 600, 22, +1), ("rev_mid", 600, 22, -1),
    ("fwd_origin_adjacent", 3, 20, +1), ("rev_origin_adjacent", 3, 20, -1),
    ("fwd_wraps_origin", -9, 24, +1), ("rev_wraps_origin", -9, 24, -1),
    ("fwd_ends_at_origin", -18, 18, +1), ("fwd_long", 1200, 30, +1),
    ("rev_short_floor", 900, sc._PRIMER_REBIND_MIN, -1),
]


def _primer_for(T, p, L, strand):
    p %= len(T)
    an = _anneal_at(T, p, L)
    return p, (_FLAP + an if strand >= 0 else _FLAP + sc._rc(an))


@pytest.mark.parametrize("label,p,L,strand", _SITES, ids=[s[0] for s in _SITES])
def test_binding_lands_at_designed_site(template, label, p, L, strand):
    """The primer re-derives to a binding that CONTAINS the window it was built from."""
    T = template
    n = len(T)
    p, primer = _primer_for(T, p, L, strand)
    b = sc._rederive_primer_binding(primer, strand, T, n, hint_start=p, circular=True)
    assert b is not None, f"{label}: no binding found"
    assert _contains_window(b, p, L, n), f"{label}: {b} does not contain [{p},{p+L})"


@pytest.mark.parametrize("R", [0, 1, 137, 859, 1289, 2572, 2578, 600, 611, 2575])
@pytest.mark.parametrize("label,p,L,strand", _SITES, ids=[s[0] for s in _SITES])
def test_binding_is_origin_rotation_invariant(template, label, p, L, strand, R):
    """Rotating the origin by R shifts the binding by exactly -R (mod n)."""
    T = template
    n = len(T)
    p, primer = _primer_for(T, p, L, strand)
    b0 = sc._rederive_primer_binding(primer, strand, T, n, hint_start=p, circular=True)
    bR = sc._rederive_primer_binding(primer, strand, _rot(T, R), n,
                                     hint_start=(p - R) % n, circular=True)
    assert bR == _shift(b0, R, n), (
        f"{label} R={R}: rotated binding {bR} != shift-of-original {_shift(b0, R, n)}")


# ── synthetic two-site template: the hint must pick the NEAREST occurrence ─────
_SITE = "GACTACAAGGACGACG"   # 16-mer, twice; spacers keep both match lengths equal
_SYN = ("AT" * 40) + _SITE + ("AG" * 60) + _SITE + ("TA" * 40)
_SYN_OCC = sorted(m.start() for m in re.finditer(f"(?={re.escape(_SITE)})", _SYN))
_SYN_PRIMER = "CAGGAAAC" + _SITE


def _nearest(occ, hint, n):
    return min(occ, key=lambda o: min(abs(o - hint), n - abs(o - hint)))


@pytest.mark.parametrize("hint", [0, 80, 216, 311])
def test_multisite_hint_picks_nearest_occurrence(hint):
    n = len(_SYN)
    assert len(_SYN_OCC) == 2
    b = sc._rederive_primer_binding(_SYN_PRIMER, +1, _SYN, n, hint_start=hint, circular=True)
    assert b is not None and b[0] == _nearest(_SYN_OCC, hint, n), (
        f"hint={hint}: bound {b}, nearest occurrence start={_nearest(_SYN_OCC, hint, n)}")


@pytest.mark.parametrize("R", [0, 50, 156, 309])
def test_multisite_pick_is_rotation_invariant(R):
    n = len(_SYN)
    hint = _SYN_OCC[0]
    b0 = sc._rederive_primer_binding(_SYN_PRIMER, +1, _SYN, n, hint_start=hint, circular=True)
    bR = sc._rederive_primer_binding(_SYN_PRIMER, +1, _rot(_SYN, R), n,
                                     hint_start=(hint - R) % n, circular=True)
    assert bR == _shift(b0, R, n)


# ── _primer_binding_sites (the mismatch-tolerant primer-CHECK finder) ──────────
def _froze(site):
    return tuple(sorted((k, round(v, 6) if isinstance(v, float) else v)
                        for k, v in site.items()))


def _site_set(sites, n, shift=0):
    out = set()
    for s in sites:
        d = dict(s)
        d["foot_start"] = (d["foot_start"] + shift) % n
        out.add(_froze(d))
    return out


def _mut(s, i, ch):
    return s[:i] + ch + s[i + 1:]


@pytest.mark.parametrize("R", [0, 1, 137, 1289, 2572, 600, 2569])
@pytest.mark.parametrize("kind", ["fwd_exact", "rev_exact", "fwd_1mm", "fwd_wrap", "rev_wrap"])
def test_primer_binding_sites_rotation_invariant(template, kind, R):
    """The mismatch-tolerant binding-site list is origin-rotation invariant:
    un-rotating the rotated sites' foot_start by +R recovers the original SET
    (robust to identity-tie ordering)."""
    T = sc._normalize_dna_for_align(template)
    n = len(T)
    ex = (T[600:624] if 600 + 24 <= n else T[600:] + T[:600 + 24 - n])
    primer = {
        "fwd_exact": ex,
        "rev_exact": sc._rc(ex),
        "fwd_1mm": _mut(T[300:324], 5, {"A": "C", "C": "A", "G": "T", "T": "G"}[T[305]]),
        "fwd_wrap": (T[n - 10:] + T[:16]),
        "rev_wrap": sc._rc(T[n - 10:] + T[:16]),
    }[kind]
    s0 = sc._primer_binding_sites(primer, T, n, circular=True)
    assert s0, f"{kind}: expected at least one binding site"
    sR = sc._primer_binding_sites(primer, _rot(T, R), n, circular=True)
    assert _site_set(sR, n, shift=R) == _site_set(s0, n), (
        f"{kind} R={R}: rotated site-set differs from the original")
