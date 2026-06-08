"""splicecraft_biology — pure biology primitives extracted from
splicecraft.py as a controlled test of the single-file seam.

The single-file rule (entire app in splicecraft.py) is intentional;
see CLAUDE.md + docs/architecture.md. This module is the first
deliberate extraction, scoped to entities that:

1. Have NO `PlasmidApp` coupling (no `self.notify`, no Textual,
   no reactive attrs).
2. Are pure functions or top-level constants.
3. Are imported back into `splicecraft.py` so external callers
   keep `splicecraft._rc(...)` etc. unchanged.

If this stays clean (no cross-imports back into splicecraft.py, no
test churn), it's the precedent for future extractions. See
CONTRIBUTING.md's "three-test rule" for the criteria.

Sacred invariants this module owns:
  #3  — `_rc` handles full IUPAC via `_IUPAC_COMP` (not just ACGT).
  #4  — `_iupac_pattern` is bounded-LRU cached in `_PATTERN_CACHE`.
  #8  — `_feat_len` returns `(total - start) + end` when `end < start`
       so wrap features have the right length.

These are the same invariants documented in `CLAUDE.md`; the
extraction did not change them, only their physical home.
"""
from __future__ import annotations

import base64 as _base64
import functools
import gzip as _gzip
import math as _math
import re
import threading as _threading
from collections import OrderedDict


# ── IUPAC + reverse complement ────────────────────────────────────────────


_IUPAC_RE: dict[str, str] = {
    "A": "A", "C": "C", "G": "G", "T": "T",
    "R": "[AG]", "Y": "[CT]", "W": "[AT]", "S": "[CG]",
    "M": "[AC]", "K": "[GT]", "B": "[CGT]", "D": "[AGT]",
    "H": "[ACT]", "V": "[ACG]", "N": "[ACGT]",
}


# Pattern cache (sacred invariant #4). Bounded LRU so a long-lived
# process scanning many recognition sites can't grow the cache
# indefinitely; the catalog is ~120 enzymes (palindromic + RC variants
# < 256), so 256 is comfortably above steady state. Public dict so
# tests can `.clear()` and inspect membership.
_PATTERN_CACHE: "OrderedDict[str, re.Pattern[str]]" = OrderedDict()
_PATTERN_CACHE_MAX = 256


def _iupac_pattern(site: str) -> "re.Pattern[str]":
    # Sweep #22: case-fold cache key. Patterns ARE always built from
    # `site.upper()` internally, so two calls with `"gaattc"` and
    # `"GAATTC"` produce identical regex objects but pre-fix occupied
    # two separate slots — wasting one cap unit per mixed-case
    # variant. Normalize to uppercase before lookup AND store.
    #
    # 2026-05-27 (audit-5 restriction M3): reject unknown characters
    # instead of silently letting them into the regex. Pre-fix any
    # char not in `_IUPAC_RE` fell through to `c` itself — a custom
    # enzyme site like ``"GAATU"`` (RNA U typo) compiled to a literal
    # ``U`` pattern that never matched DNA, and a stray regex
    # metacharacter (``*``, ``(``, ``?``) became part of the pattern.
    # User-defined enzyme sites are an attack surface; validate here.
    key = site.upper()
    pat = _PATTERN_CACHE.get(key)
    if pat is not None:
        _PATTERN_CACHE.move_to_end(key)
        return pat
    bad = [c for c in key if c not in _IUPAC_RE]
    if bad:
        raise ValueError(
            f"recognition site {site!r} contains non-IUPAC "
            f"character(s) {', '.join(repr(c) for c in bad[:6])}"
            f"{' (truncated)' if len(bad) > 6 else ''}"
        )
    pat = re.compile("".join(_IUPAC_RE[c] for c in key))
    _PATTERN_CACHE[key] = pat
    if len(_PATTERN_CACHE) > _PATTERN_CACHE_MAX:
        _PATTERN_CACHE.popitem(last=False)
    return pat


_IUPAC_COMP = str.maketrans(
    "ACGTRYWSMKBDHVN",
    "TGCAYRWSKMVHDBN",
)

# Case-preserving ACGT complement used by the sequence-panel renderer.
_DNA_COMP_PRESERVE_CASE = str.maketrans("ACGTacgtNn", "TGCAtgcaNn")


# Tiny LRU on `_rc` — the cached value of a 200 kb plasmid's RC is
# itself ~200 kb, so a 4-entry cap is enough to cover the working set
# (current sequence + a couple of recent rotations / undo snapshots)
# without ballooning RAM. Benches show 35–113× speedup on cache hit
# for cosmid-size sequences (scripts/perf_probe.py).
@functools.lru_cache(maxsize=4)
def _rc(seq: str) -> str:
    return seq.upper().translate(_IUPAC_COMP)[::-1]


# ── Wrap-aware coordinate helpers ─────────────────────────────────────────


def _feat_len(start: int, end: int, total: int) -> int:
    """Circular-aware feature length. A wrap feature (end < start) is
    (total - start) + end bp long; a linear feature is end - start."""
    return (total - start) + end if end < start else end - start


def _seq_len(record) -> int:
    """Length of ``record.seq`` in bp, or 0 if the record has no
    sequence attached. BioPython's ``SeqRecord.seq`` is typed as
    ``Seq | MutableSeq | None`` because the dataclass allows records
    without sequences (rare — e.g. annotation-only GenBank views).
    SpliceCraft always loads records with sequence content, but
    routing length lookups through this helper sidesteps the
    ``"None" is not assignable to "Sized"`` pyright noise at every
    ``len(rec.seq)`` call site without an inline None guard."""
    seq = getattr(record, "seq", None)
    return len(seq) if seq is not None else 0


def _slice_circular(seq: str, start: int, end: int) -> str:
    """Circular-aware slice. If end > start this is a normal slice; if
    end < start the slice wraps the origin and returns seq[start:] + seq[:end].
    end == start is treated as empty (not "wrap whole plasmid") — callers
    that want the latter should pass explicit boundaries. Used by the
    primer-design helpers so a region straddling the origin can be
    primer-designed without special casing at every call site.
    """
    if end >= start:
        return seq[start:end]
    return seq[start:] + seq[:end]


# ── RNA secondary-structure free energy (Turner-2004, pure-Python) ──────────
#
# A dependency-free minimum-free-energy RNA folder + structure evaluator,
# reproducing the ViennaRNA dangles=2 model. Parameters are the standard
# Turner-2004 nearest-neighbor set, embedded as a gzip+base64 constant so
# nothing is read from disk and no compiled RNA library is required. Every
# energy term + the MFE folder are validated to match ViennaRNA exactly
# (see tests/test_rna_fold.py, which asserts against a frozen reference).
#
# Energies are centi-kcal/mol (int) internally; kcal/mol at the API edge.


_RNA_INF = 1 << 30
_RNA_MAXLOOP = 30                       # ViennaRNA default max interior-loop size
_RNA_FOLD_MAX_LEN = 600                 # O(n^3) DP — cap to bound worst-case time

# ViennaRNA pair-type order: CG=0 GC=1 GU=2 UG=3 AU=4 UA=5, no-pair=6.
_RNA_PT = {('C', 'G'): 0, ('G', 'C'): 1, ('G', 'U'): 2,
           ('U', 'G'): 3, ('A', 'U'): 4, ('U', 'A'): 5}
_RNA_BI = {'@': 0, 'A': 1, 'C': 2, 'G': 3, 'U': 4}    # 5-wide (mm/dangle)
_RNA_BI4 = {'A': 0, 'C': 1, 'G': 2, 'U': 3}           # 4-wide (int22)

# Turner-2004 parameters (ViennaRNA `params_save` dump, gzip+base64). The
# numeric values are the published experimental free energies; only the
# DEF placeholders are resolved (to a uniform -50, measured against the
# reference engine). Decoded + parsed once, lazily, on first fold/eval.
_RNA_TURNER_PARAMS_GZ_B64 = (
    "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"
    "bBQXCPV0WE+YVmeflI3VGYTsHemWMrA9c9KKpamezvc7KIineKakpCi7SyvCJaZ9NQr1NNl62lPt+W"
    "7Zx+p5/d6G9rRPXM9bNtbz+uF74n6Hel5epe/SWP4udRF6GaB7laI3/12K8T5GX4gt3m/1pO/STBY4"
    "9wXcyT77/TTdAembhfT1DDhaFD2h1Qd8FrPfZXwOgvQQ6hm+SzCvwWempDRb3R/vU6hnDd+ltT0r+C"
    "7d6hm+S888Ph37Lp2w7xtrzbLT8tQ72M1bPcN3KdNgptyeZ/p9UHGznW7P/Hcpft4jG9YIe9Im1JO+"
    "S89YzzHU09bSnraO9sx/l56rno6v487M64w0r+vuMp93fB133r6ft0fqbfbdXZ79u0T9/tzfpSXU09"
    "bSnraO9syv49KSdrLvWk97er70mHmyjeZLT17PCuy7rWS+ZCuZL9mnny8NoZ5VzJfsk3+XsN9v9Xzq"
    "7xKvp6uj36uYL9ln/y4N1J5P/V2aQj2f+7sE/d784dnXcSbUswb/0lrPCr5Lt3rm9+Oeaz5/q6etpT"
    "1tHXh/+nWcJwxsCBSMOxDCNuee98ie+4PSMXfgOevZLQl3AOka2ef7Z0OI7V3peUm4A09Zz2FIuANP"
    "3562jvb036pnrifjDjw13hl34DnrOXUJd+AJ+73Vpku4A8+Md84dePr2tHW051N/l3w9mS/82evp13"
    "ZPPT65L3xz3id7zuz9suHWjh3pdupS7sAz1rNbUu7As7enraM9w9ruievJuQPPPK/j3IFn/n5G3IEn"
    "7/dn/i6t2VvuwLO3p62jPZ/5u+TraSuZL9lK5ku2kvmSrWS+ZCuZL9lK5ku2lvmSrWS+ZCuZL9lK5k"
    "u2kvmSrWS+ZCuZL9k65kucO/DM3yXOHXjm71LEHXhiHEXcgScfn7aW9rR1tOfTf5eAO2B8sIGI3sTu"
    "Vdw8H5oNNbXFmkKUDPFlZ7LhH9nnnjTU9LWaNr0kNQ26vKlV7iENNXUH2hTpbXfNjthv8ZMQFYh4cM"
    "189Iw6KJeRZHeS9E7vF14eEJWvaQYyO9m8oSah9+Gg/VsRdbhND7XK3dqUH6fj7SE2Gs+GFoqzRUTF"
    "Z/nehKhNTd8FMuY0oiaGIp7uOaL8NKWDSF+550OzCVFyTYHAJ77sTPYOt7AkTYiqo00vSU2DLvlGe2"
    "v2oTbNSxOi9toUo0XdNZvzUjdPQlRYoDw9ogo1fTJEVdKml6Sm98DEOyFqt02fBlG2Ghtlq7FRthob"
    "ZauxUbYeG2WrsVG2Ghtlq7FRthobZWtBVHBPPj2iCjV9MkRV0qaXpKZPbKN22/QJEHV5jb3nh5x5wy"
    "M8vdea2l9VU328Kof8UqWaPqP3HEbotqaVeM+vNa3Eew6I2vVKPsHeCSDqY2p6ej8qg6hK2jTjPddl"
    "7/kbs98DUffwnt8JURWsohFRH1LT07O+bU1fq2nTSrzniChXy6yvBk8vIOpcTWHWh2l9GlHm1yDq6d"
    "qUj9YIUTV4zwFRNXjPAVG2Ghtlq7FRthobZauxUbYeG2WrsVG2FkRV0qaVeM/RRtlaEFWDpxcQ9TE1"
    "fQ9EVdKmlXjPAVFVeM9d5D1/5t53kff8mddRLvKeP/M6ykXe82e2US7ynj89ooJX8ukR9TE1/ZU2yk"
    "Xe8+du00tS0+e0US7ynj8/osKKDy4Czj0fmk2I+pCaGrgbOfc8VtPXatr0ktQUVN2kf332oTbNSxOi"
    "dttUqftn736lIu/5syPqXE1x1jexWd8JRMGs762Iero2xW9+iqhNTe+BiXdC1H6bPguibDU2ylZjo2"
    "w1NspWY6NsPTbKVmOjbC2IqqRNL0lNn9lG2VoQRV7JZ0fUx9T0PRBVSZtekpo+sY3ab9PHI8pdavGe"
    "u8uO9/xpTnO4y1N6z/NtWon33F1q8Z67Sy3ec3fZ8Z4/GaIqadNKvOfuUov33F1q8Z67y473PDMFeW"
    "v26VlfNENxl1q85+5Si/fcXWrxnrtLLd5zd9nxnj8Zoipp00q85+5Si/fcXWrxnrvLjvf8yRBlq7FR"
    "thobZeuxUbYaG2WrsVG2Ghtlq7FRthZE1eDpdZcd7/mTIaqSNq3Ee+4utXjPbTXcc/sG7vljmLLW1e"
    "I9t9Vwz2013HNbDffcVsM9t9Vwz2013HNbDffcVsM9t9Vwz2013HNbDffcVsM9t9Vwz+0buOePRFQl"
    "bVqJ99xWwz231XDPbTXcc1sN99xWwz231XDPbTXcc1sN99xWwz231XDPbTXcc1sN99xWwz231XDPbT"
    "Xcc1sP99xWwz231XDPbTXcc1sN99xWwz231XDPbTXcc1sN99xWwz231XDPbTXcc1sN99xWwz231XDP"
    "bTXcc1sN99xWwz231XDPbTXcc1sN99xWwz231XDPbTXcc1sN99xWwz231XDPbTXcc1sN99xWwz231X"
    "DPbTXcc1sN99xWwz231XDPbS3e8+YPtXjPrzWthHt+relrNW1aiff8WtNKvOeAqBo8vYCoGrjngKhK"
    "2rQS7zkgqgbvOSKqglU0IqoCXh8iqo42rcR7johytcz6avD0AqJq4J4Doipp00q854CoGrzngChbjY"
    "2y1dgoW42NstXYKFuPjbLV2ChbjY2y1dgoW42NsrUgqgZPLyCqBu45IKqSNq3Eew6Ienrv+ctvmy9f"
    "fzH6u89ff/nrp+9//PL551vtXzex0OESptzzodlQU1uqaWu60svOZMM/8s89aajpazVtetnWlHR5S6"
    "vcRRpq6vbbtB0nde/s23+kJyEqeCqn+faCsVchfab/uMR5aSgVniKiyKeavMakZeGnT8rGnrsPos61"
    "qR4f1KaXbZu2rCqZz1CU3eskewdRnf41iAo1HVhLsHR7TR+AzK+SjlG0UHpSHFF+FTivL8g+H5pNiJ"
    "Jraubiy85kwz+yzz1pQlQdbXpJahp0yTfaW7MPtWlemhC106Y3uN87Gz4p+SchKngqnx5RhZo+GaIq"
    "adNLUtN7YOKdELXXps+DKFuNjbLV2ChbjY2y1dgoW4+NstXYKFuNjbLV2ChbjY2ytSAqeCqfHlGFmj"
    "4Zoipp00tS0ye2UXtt+gyIurxG3vPVd4HPnOuonH1XT++1pvbX1NT0h6uCXqhBHfX1JTV9Ru+57nI1"
    "rcR7fq1pJd5zQFTBJ/00eyeAqA+p6WnveQ5RlbRp6j2XMPHrst8FUXtt+kSIqmAVjYj6kJqenvVta/"
    "paTZtW4j1HRLlaZn01eHoBUedqCrO+mc36TiAKZn1vRtTTtSnM+jKIqsF7DoiqwXsOiLLV2ChbjY2y"
    "1dgoW42NsvXYKFuNjbK1IKqSNq3Ee442ytaCqBo8vYCoj6npeyCqkjatxHsOiKrCex7fJPrMvR/fJP"
    "rM66j4JtFnXkfFN4k+s42KbxJ9ekQFr+TTI+pjavorbVR8k+hzt+klqelz2qj4JtHnRxSu+PTqeM8/"
    "H5pNiPqImuKxiezzWE1fq2nTy7amoOo2/auzD7WpIE2I2mnTpuvVvbNv/5GehCjvlXx+RJ2qKcz6NO"
    "NMnEEUzvreiqhna1OY9eUQFdf0Lph4J0TttenzIMpWY6NsNTbKVmOjbDU2ytZjo2w1NsrWgqhK2vSy"
    "relT2yhbC6K8V/L5EfUhNX0XRFXSppdtTZ/ZRu216TMgyl1q8Z67y473/GlOc7jLU3rP821aiffcXW"
    "rxnrtLLd5zd9nxnj8Zoipp00q85+5Si/fcXWrxnrtL2XuemYK8Nfv0rC+eobhLLd5zd6nFe+4utXjP"
    "3aUW77m7lL3nz4aoStq0Eu+5u9TiPXeXWrzn7lL2nj8bomw1NspWY6NsPTbKVmOjbDU2ylZjo2w1Ns"
    "rWgqgaPL3uUvaePxuiKmnTSrzn7lKL99xWwz23b+CeP4Ypa10t3nNbDffcVsM9t9Vwz2013HNbDffc"
    "VsM9t9Vwz2013HNbDffcVsM9t9Vwz2013HNbDffcnueePxRRlbRpJd5zWw333FbDPbfVcM9tNdxzWw"
    "333FbDPbfVcM9tNdxzWw333FbDPbfVcM9tNdxzWw333FbDPbfVcM9tPdxzWw333FbDPbfVcM9tNdxz"
    "Ww333FbDPbfVcM9tNdxzWw333FbDPbfVcM9tNdxzWw333FbDPbfVcM9tNdxzWw333FbDPbfVcM9tNd"
    "xzWw333FbDPbfVcM9tNdxzWw333FbDPbfVcM9tNdxzWw333FbDPbfVcM9tNdxzWw333FbDPbe1eM+b"
    "P9TiPb/WtBLu+bWmr9W0aSXe82tNK/GeA6Jq8PQComrgngOiKmnTSrzngKgavOeIqApW0YioCnh9iK"
    "g62rQS7zkiytUy66vB0wuIqoF7DoiqpE0r8Z4DomrwngOibDU2ylZjo2w1NspWY6NsPTbKVmOjbDU2"
    "ylZjo2w1NsrWgqgaPL2AqBq454CoStq0Eu85IOrpvecvv22+fP3FmFuVXy+/t9w7rc1659DNi9E0Pr"
    "1m6PUfA8uAJoN/NKtEO2FalsAyuZ8ZSjOK3nNNrxlrlfXaA/g28BDJEusVmRqqsWDal8k9xidrffur"
    "9dXNmt00mN7V0x3SM1vrogT8tLbMP//Lv0Iay3yN+hMq17Mm7JSQYVh/wk/YSktBAsuM+nNg3TZwdX"
    "rSE9PQtrKEYT8ZlPNlpv15uNa3/lx/atefMG329XSH9MzWuiTRdNTFmA5j6GJTPWHcdgyfDSuz5W0b"
    "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"
    "cucP4c8fmCQ5kU+e7z11/++un7H798/jmvE3AXesYxQ/8KOX5uJGTiIr/h+R7CN53+x9+///fPpEZH"
    "4YAb2I8P96gRnWzcxqNr+oW9X6stf7tnz4E9R76LD885eS70hEERnuug0KRGrmO8DdEnni9nBd5b+K"
    "bRl6+/fP7p66fvt0MM+STwBJ6kYk/NgoX2xANrzKi2wTcXtQnuFgUaNzyMJw/mCc8heY7sGSjPXBMZ"
    "N3johT9HxuraPF+kjCPP9xC+KfXf/ut3P3766dMPP9++qv/a/J/Nn//+u6/f/f3rj9dvxOe/NP+p+f"
    "Ofb48vv/v+b3/78bu/fP73nz5/bv7jf/rvn3/6wbr/4/b1/aff/eZmpP78999c///dX/4L/OvP13/9"
    "Ofzry/VfX67/am4Cv4E1IfvP9QP/m7Xz1n+1y+0/NwfmrYJ/+uOf/vh/3Sr3py9fv/ztWsH/9sc//c"
    "cfPv3jPzc//O5/fdXtV/O/WC1+uD5/WAu6/ePTP6jAcX33dTj8Bg+O3pT/8vOfb6/+9P33zdoKn689"
    "/HPz6afPzU3/n5u//VvzHz5//fzTv//PBjv9f/4HKO32yn/++4/ff/7HH79++WUt7tYmX67j47qoW/"
    "/3X/+fwM7xtKfgDEMfym3XV6vp9/P1EwLegNva+1qz//L5H59uLf7zy2/sq704+7q+FDmCt1vA4fcL"
    "FOYPuvbhd/x7pGPT37v1d4yLe/PoXAv7759/+cmX9mrt6+XShG/buufxm9frz/ArbBOsIRFuv+Lfwr"
    "b2um9w/fXV/y1MR2d4w8X/Ctu7txOqt1/9GzAQ4LD+6mz8XvwV/xYZod36Xhdqhm8Y4Vf/t6Dpjap1"
    "/fU1fi/Uwb36v8XAw/DeS/YNbqOFgV95O0BcrNuvLi5thtb+6Qtv6ybYf3P7zvzm2kP2taELURRI/c"
    "uf/vnl/weHW7qo0dMDAA=="
)


def _rna_pairtype(a, b):
    return _RNA_PT.get((a, b), 6)


def _rna_pairtable(db):
    """Dot-bracket -> partner array (pt[k]=partner or -1). Raises on
    imbalance or a stray glyph so a malformed structure fails loud."""
    pt = [-1] * len(db)
    st = []
    for k, c in enumerate(db):
        if c == '(':
            st.append(k)
        elif c == ')':
            if not st:
                raise ValueError(f"unbalanced ')' at {k}")
            o = st.pop()
            pt[o] = k
            pt[k] = o
        elif c != '.':
            raise ValueError(f"bad dot-bracket char {c!r} at {k}")
    if st:
        raise ValueError(f"unbalanced '(' at {st}")
    return pt


def _rna_reshape(flat, shape):
    n = 1
    for d in shape:
        n *= d
    if len(flat) != n:
        raise ValueError(f"reshape {shape}: expected {n}, got {len(flat)}")

    def build(fl, sh):
        if len(sh) == 1:
            return list(fl)
        step = 1
        for d in sh[1:]:
            step *= d
        return [build(fl[k * step:(k + 1) * step], sh[1:]) for k in range(sh[0])]
    return build(flat, shape)


def _rna_resolve_def(tbl, val):
    for i, x in enumerate(tbl):
        if x is None:
            tbl[i] = val
        elif isinstance(x, list):
            _rna_resolve_def(x, val)


def _rna_parse_params(text):
    """Parse a ViennaRNA params file (as text) into {section: flat list}
    plus {special-section: {seq: energy}} for the tri/tetra/hexaloops."""
    secs, special, cur = {}, {}, None
    SPECIAL = {'Hexaloops', 'Tetraloops', 'Triloops'}
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith('##') or s.startswith('/*'):
            continue
        if s.startswith('#'):
            cur = s[1:].strip()
            special[cur] = {} if cur in SPECIAL else None
            if cur not in SPECIAL:
                secs[cur] = []
            continue
        if cur is None:
            continue
        if cur in SPECIAL:
            parts = s.split()
            if len(parts) >= 2 and parts[0].isalpha():
                special[cur][parts[0].replace('T', 'U').upper()] = int(parts[1])
            continue
        s = re.sub(r'/\*.*?\*/', ' ', s)
        for t in s.split():
            if t == 'INF':
                secs[cur].append(_RNA_INF)
            elif t == 'DEF':
                secs[cur].append(None)
            elif '.' in t:
                secs[cur].append(float(t))
            else:
                secs[cur].append(int(t))
    return secs, special


class _RNAModel:
    """Turner-2004 energy model + Zuker MFE folder. Construct once via
    `_rna_model()`; all methods are pure (thread-safe to share)."""

    def __init__(self, par_text):
        secs, special = _rna_parse_params(par_text)
        self.stack = _rna_reshape(secs['stack'], (7, 7))
        self.hairpin = secs['hairpin']
        self.bulge = secs['bulge']
        self.internal = secs['internal']
        self.mm_hairpin = _rna_reshape(secs['mismatch_hairpin'], (7, 5, 5))
        self.mm_internal = _rna_reshape(secs['mismatch_internal'], (7, 5, 5))
        self.mm_internal_1n = _rna_reshape(secs['mismatch_internal_1n'], (7, 5, 5))
        self.mm_internal_23 = _rna_reshape(secs['mismatch_internal_23'], (7, 5, 5))
        self.mm_multi = _rna_reshape(secs['mismatch_multi'], (7, 5, 5))
        self.mm_ext = _rna_reshape(secs['mismatch_exterior'], (7, 5, 5))
        self.dangle5 = _rna_reshape(secs['dangle5'], (7, 5))
        self.dangle3 = _rna_reshape(secs['dangle3'], (7, 5))
        self.int11 = _rna_reshape(secs['int11'], (7, 7, 5, 5))
        self.int21 = _rna_reshape(secs['int21'], (7, 7, 5, 5, 5))
        self.int22 = _rna_reshape(secs['int22'], (6, 6, 4, 4, 4, 4))
        misc = secs['Misc']
        self.terminalAU = misc[2]
        self.lxc = misc[4]
        ml = secs['ML_params']
        self.ml_base, self.ml_close, self.ml_branch = ml[0], ml[2], ml[4]
        self.tetra = special['Tetraloops']
        self.tri = special['Triloops']
        self.hexa = special['Hexaloops']
        # DEF -> -50 (a uniform default, measured against the reference)
        for tbl in (self.stack, self.mm_hairpin, self.mm_internal,
                    self.mm_internal_1n, self.mm_internal_23, self.mm_multi,
                    self.mm_ext, self.dangle5, self.dangle3,
                    self.int11, self.int21, self.int22):
            _rna_resolve_def(tbl, -50)

    # ---- loop energies (centi-kcal) ----
    def _loop_init(self, table, size):
        if size <= 30:
            return table[size]
        # ViennaRNA's C `(int)` cast TRUNCATES toward zero (no half-up
        # rounding); matching it keeps large-loop (>30 nt) energies exact
        # to the cent. A prior `+ 0.5` here diverged by +0.01 kcal on big
        # loops (the validation suite's max loop was 22 nt, so it never
        # caught it — found in the 2026-06-07 adversarial audit).
        return table[30] + int(self.lxc * _math.log(size / 30.0))

    def _stem_d2(self, t, s5i, s3i, mm):
        if s5i and s3i:
            e = mm[t][s5i][s3i]
        elif s5i:
            e = self.dangle5[t][s5i]
        elif s3i:
            e = self.dangle3[t][s3i]
        else:
            e = 0
        if t not in (0, 1):
            e += self.terminalAU
        return e

    def energy_stack(self, s, i, j):
        return self.stack[_rna_pairtype(s[i], s[j])][_rna_pairtype(s[j - 1], s[i + 1])]

    def energy_hairpin(self, s, i, j):
        size = j - i - 1
        if size < 3:
            return _RNA_INF
        sub = s[i:j + 1]
        if size == 3 and sub in self.tri:
            return self.tri[sub]
        if size == 4 and sub in self.tetra:
            return self.tetra[sub]
        if size == 6 and sub in self.hexa:
            return self.hexa[sub]
        t = _rna_pairtype(s[i], s[j])
        e = self._loop_init(self.hairpin, size)
        if size == 3:
            if t not in (0, 1):
                e += self.terminalAU
        else:
            e += self.mm_hairpin[t][_RNA_BI[s[i + 1]]][_RNA_BI[s[j - 1]]]
        return e

    def energy_bulge(self, s, i, j, a, b):
        size = (a - i - 1) + (j - b - 1)
        e = self._loop_init(self.bulge, size)
        if size == 1:
            e += self.stack[_rna_pairtype(s[i], s[j])][_rna_pairtype(s[b], s[a])]
        else:
            for (x, y) in ((s[i], s[j]), (s[a], s[b])):
                if _rna_pairtype(x, y) not in (0, 1):
                    e += self.terminalAU
        return e

    def energy_internal(self, s, i, j, a, b):
        n1 = a - i - 1
        n2 = j - b - 1
        t1 = _rna_pairtype(s[i], s[j])
        t2 = _rna_pairtype(s[b], s[a])
        v, is_special = None, True
        if n1 == 1 and n2 == 1:
            v = self.int11[t1][t2][_RNA_BI[s[i + 1]]][_RNA_BI[s[j - 1]]]
        elif n1 == 1 and n2 == 2:
            v = self.int21[t1][t2][_RNA_BI[s[i + 1]]][_RNA_BI[s[b + 1]]][_RNA_BI[s[j - 1]]]
        elif n1 == 2 and n2 == 1:
            v = self.int21[t2][t1][_RNA_BI[s[b + 1]]][_RNA_BI[s[i + 1]]][_RNA_BI[s[a - 1]]]
        elif n1 == 2 and n2 == 2:
            v = self.int22[t1][t2][_RNA_BI4[s[i + 1]]][_RNA_BI4[s[a - 1]]][
                _RNA_BI4[s[b + 1]]][_RNA_BI4[s[j - 1]]]
        else:
            is_special = False
        if is_special and v is not None:
            return v
        size = n1 + n2
        e = self._loop_init(self.internal, size)
        e += min(300, abs(n1 - n2) * 60)        # NINIO asymmetry (m=60, max=300)
        if n1 == 1 or n2 == 1:
            mm = self.mm_internal_1n
        elif (n1, n2) in ((2, 3), (3, 2)):
            mm = self.mm_internal_23
        else:
            mm = self.mm_internal
        e += mm[t1][_RNA_BI[s[i + 1]]][_RNA_BI[s[j - 1]]]
        e += mm[t2][_RNA_BI[s[b + 1]]][_RNA_BI[s[a - 1]]]
        return e

    def energy_multiloop(self, s, i, j, inner):
        e = self.ml_close + self.ml_branch * (len(inner) + 1)
        e += self.ml_base * ((j - i - 1) - sum(b - a + 1 for (a, b) in inner))
        for (a, b) in inner:
            t = _rna_pairtype(s[a], s[b])
            e += self._stem_d2(t, _RNA_BI[s[a - 1]], _RNA_BI[s[b + 1]], self.mm_multi)
        tc = _rna_pairtype(s[j], s[i])
        e += self._stem_d2(tc, _RNA_BI[s[j - 1]], _RNA_BI[s[i + 1]], self.mm_multi)
        return e

    def _inner_pairs(self, pt, i, j):
        res, k = [], i + 1
        while k < j:
            if pt[k] > k:
                res.append((k, pt[k]))
                k = pt[k] + 1
            else:
                k += 1
        return res

    def _energy_enclosed(self, s, pt, i, j):
        inner = self._inner_pairs(pt, i, j)
        if not inner:
            return self.energy_hairpin(s, i, j)
        if len(inner) == 1:
            (a, b) = inner[0]
            lg, rg = a - i - 1, j - b - 1
            if lg == 0 and rg == 0:
                e = self.energy_stack(s, i, j)
            elif lg == 0 or rg == 0:
                e = self.energy_bulge(s, i, j, a, b)
            else:
                e = self.energy_internal(s, i, j, a, b)
            return e + self._energy_enclosed(s, pt, a, b)
        e = self.energy_multiloop(s, i, j, inner)
        for (a, b) in inner:
            e += self._energy_enclosed(s, pt, a, b)
        return e

    def eval_structure(self, s, db):
        pt = _rna_pairtable(db)
        n = len(s)
        # Fail loud on a structurally-valid bracketing that's chemically
        # impossible: a non-canonical pair (the energy tables have no
        # type-6 entry — would IndexError) or a hairpin loop < 3 nt (would
        # leak the _RNA_INF sentinel as a finite ~1e7 energy). The MFE
        # folder never emits either; this guards the eval-only path.
        for a in range(n):
            b = pt[a]
            if b > a:
                if _rna_pairtype(s[a], s[b]) == 6:
                    raise ValueError(
                        f"structure has a non-canonical pair at {a},{b} "
                        f"({s[a]}·{s[b]})")
                if (b - a - 1 < 3
                        and not any(pt[x] > x for x in range(a + 1, b))):
                    raise ValueError(
                        f"structure has an infeasible hairpin loop "
                        f"(<3 nt) closed at {a},{b}")
        total, k = 0, 0
        while k < n:
            if pt[k] > k:
                i, j = k, pt[k]
                t = _rna_pairtype(s[i], s[j])
                s5 = _RNA_BI[s[i - 1]] if i - 1 >= 0 else 0
                s3 = _RNA_BI[s[j + 1]] if j + 1 < n else 0
                total += self._stem_d2(t, s5, s3, self.mm_ext)
                total += self._energy_enclosed(s, pt, i, j)
                k = j + 1
            else:
                k += 1
        if total >= _RNA_INF:
            raise ValueError("structure contains an infeasible loop")
        return total / 100.0

    # ---- d2 helix-end contributions for the folder ----
    def _d2_ext(self, s, i, j, n):
        t = _rna_pairtype(s[i], s[j])
        s5 = _RNA_BI[s[i - 1]] if i > 0 else 0
        s3 = _RNA_BI[s[j + 1]] if j + 1 < n else 0
        return self._stem_d2(t, s5, s3, self.mm_ext)

    def _d2_ml(self, s, i, j, n):
        t = _rna_pairtype(s[i], s[j])
        s5 = _RNA_BI[s[i - 1]] if i > 0 else 0
        s3 = _RNA_BI[s[j + 1]] if j + 1 < n else 0
        return self._stem_d2(t, s5, s3, self.mm_multi)

    def _d2_ml_close(self, s, i, j):
        tc = _rna_pairtype(s[j], s[i])
        return self._stem_d2(tc, _RNA_BI[s[j - 1]], _RNA_BI[s[i + 1]], self.mm_multi)

    def _loop_e(self, s, i, j, p, q):
        lg, rg = p - i - 1, j - q - 1
        if lg == 0 and rg == 0:
            return self.energy_stack(s, i, j)
        if lg == 0 or rg == 0:
            return self.energy_bulge(s, i, j, p, q)
        return self.energy_internal(s, i, j, p, q)

    def fold(self, s):
        """Minimum-free-energy fold -> (dot_bracket, dg_kcal)."""
        n = len(s)
        if n < 5:
            return '.' * n, 0.0
        INF, ML = _RNA_INF, _RNA_MAXLOOP
        a, b = self.ml_close, self.ml_branch
        V = [[INF] * n for _ in range(n)]
        M = [[INF] * n for _ in range(n)]
        M1 = [[INF] * n for _ in range(n)]
        for d in range(3, n):
            for i in range(0, n - d):
                j = i + d
                if _rna_pairtype(s[i], s[j]) != 6:
                    best = self.energy_hairpin(s, i, j)
                    pmax = min(j - 1, i + ML + 1)
                    for p in range(i + 1, pmax + 1):
                        lg = p - i - 1
                        qmin = max(p + 1, j - 1 - (ML - lg))
                        for q in range(qmin, j):
                            if V[p][q] >= INF or _rna_pairtype(s[p], s[q]) == 6:
                                continue
                            cand = self._loop_e(s, i, j, p, q) + V[p][q]
                            if cand < best:
                                best = cand
                    base = a + b + self._d2_ml_close(s, i, j)
                    for u in range(i + 2, j - 1):
                        if M[i + 1][u] < INF and M1[u + 1][j - 1] < INF:
                            cand = base + M[i + 1][u] + M1[u + 1][j - 1]
                            if cand < best:
                                best = cand
                    V[i][j] = best
                m1 = M1[i][j - 1] if M1[i][j - 1] < INF else INF
                if V[i][j] < INF:
                    cand = V[i][j] + b + self._d2_ml(s, i, j, n)
                    if cand < m1:
                        m1 = cand
                M1[i][j] = m1
                m = M[i][j - 1] if M[i][j - 1] < INF else INF
                for k in range(i, j):
                    if V[k][j] < INF:
                        cand = V[k][j] + b + self._d2_ml(s, k, j, n)
                        if cand < m:
                            m = cand
                for u in range(i, j):
                    if M[i][u] < INF and M1[u + 1][j] < INF:
                        cand = M[i][u] + M1[u + 1][j]
                        if cand < m:
                            m = cand
                M[i][j] = m
        F = [0] * n
        for j in range(1, n):
            best = F[j - 1]
            for i in range(0, j):
                if V[i][j] < INF:
                    prev = F[i - 1] if i > 0 else 0
                    cand = prev + V[i][j] + self._d2_ext(s, i, j, n)
                    if cand < best:
                        best = cand
            F[j] = best
        pairs = []
        self._tb_ext(s, n, V, M, M1, F, n - 1, pairs)
        db = ['.'] * n
        for (x, y) in pairs:
            db[x], db[y] = '(', ')'
        return ''.join(db), F[n - 1] / 100.0

    def _tb_ext(self, s, n, V, M, M1, F, j, pairs):
        while j > 0:
            if F[j] == F[j - 1]:
                j -= 1
                continue
            for i in range(0, j):
                if V[i][j] >= _RNA_INF:
                    continue
                prev = F[i - 1] if i > 0 else 0
                if F[j] == prev + V[i][j] + self._d2_ext(s, i, j, n):
                    pairs.append((i, j))
                    self._tb_V(s, n, V, M, M1, i, j, pairs)
                    j = i - 1
                    break
            else:
                break

    def _tb_V(self, s, n, V, M, M1, i, j, pairs):
        if V[i][j] == self.energy_hairpin(s, i, j):
            return
        pmax = min(j - 1, i + _RNA_MAXLOOP + 1)
        for p in range(i + 1, pmax + 1):
            lg = p - i - 1
            qmin = max(p + 1, j - 1 - (_RNA_MAXLOOP - lg))
            for q in range(qmin, j):
                if V[p][q] >= _RNA_INF or _rna_pairtype(s[p], s[q]) == 6:
                    continue
                if V[i][j] == self._loop_e(s, i, j, p, q) + V[p][q]:
                    pairs.append((p, q))
                    self._tb_V(s, n, V, M, M1, p, q, pairs)
                    return
        base = self.ml_close + self.ml_branch + self._d2_ml_close(s, i, j)
        for u in range(i + 2, j - 1):
            if M[i + 1][u] < _RNA_INF and M1[u + 1][j - 1] < _RNA_INF and \
               V[i][j] == base + M[i + 1][u] + M1[u + 1][j - 1]:
                self._tb_M(s, n, V, M, M1, i + 1, u, pairs)
                self._tb_M1(s, n, V, M, M1, u + 1, j - 1, pairs)
                return
        raise RuntimeError(f"V traceback failed at {i},{j}")

    def _tb_M(self, s, n, V, M, M1, i, j, pairs):
        if j > i and M[i][j] == M[i][j - 1]:
            self._tb_M(s, n, V, M, M1, i, j - 1, pairs)
            return
        for k in range(i, j):
            if V[k][j] < _RNA_INF and \
               M[i][j] == V[k][j] + self.ml_branch + self._d2_ml(s, k, j, n):
                pairs.append((k, j))
                self._tb_V(s, n, V, M, M1, k, j, pairs)
                return
        for u in range(i, j):
            if M[i][u] < _RNA_INF and M1[u + 1][j] < _RNA_INF and \
               M[i][j] == M[i][u] + M1[u + 1][j]:
                self._tb_M(s, n, V, M, M1, i, u, pairs)
                self._tb_M1(s, n, V, M, M1, u + 1, j, pairs)
                return
        raise RuntimeError(f"M traceback failed at {i},{j}")

    def _tb_M1(self, s, n, V, M, M1, i, j, pairs):
        if j > i and M1[i][j] == M1[i][j - 1]:
            self._tb_M1(s, n, V, M, M1, i, j - 1, pairs)
            return
        if V[i][j] < _RNA_INF and \
           M1[i][j] == V[i][j] + self.ml_branch + self._d2_ml(s, i, j, n):
            pairs.append((i, j))
            self._tb_V(s, n, V, M, M1, i, j, pairs)
            return
        raise RuntimeError(f"M1 traceback failed at {i},{j}")

    # ---- bound-state heterodimer (cofold) ----
    def _junction(self, s, i, j, cut):
        """Energy of a cut-spanning pair's duplex junction — replaces the
        hairpin for inter-strand pairs. The two cut-facing bases dangle
        across the backbone break, scored via the exterior mismatch table
        in the inward (reversed-pair) orientation."""
        s5 = _RNA_BI[s[j - 1]] if j - 1 >= cut else 0
        s3 = _RNA_BI[s[i + 1]] if i + 1 < cut else 0
        return self._stem_d2(_rna_pairtype(s[j], s[i]), s5, s3, self.mm_ext)

    def cofold(self, a_seq, b_seq):
        """Bound-state heterodimer free energy of strands A & B (kcal/mol).
        Concatenates A+B with an inter-strand cut and computes the BOUND
        complex (DuplexInit always paid — the ribosome-bound state).
        Matches ViennaRNA RNAcofold on binding duplexes; intra-strand
        structure inside the duplex (the footprint) is forbidden, which is
        the constrained bound state the translation-initiation model
        uses. Energy only (no traceback)."""
        s = a_seq + b_seq
        cut = len(a_seq)
        n = len(s)
        if n < 2 or cut == 0 or cut == n:
            return 0.0
        INF, MLP = _RNA_INF, _RNA_MAXLOOP
        a, b = self.ml_close, self.ml_branch
        V = [[INF] * n for _ in range(n)]
        M = [[INF] * n for _ in range(n)]
        M1 = [[INF] * n for _ in range(n)]
        for d in range(1, n):
            for i in range(0, n - d):
                j = i + d
                if _rna_pairtype(s[i], s[j]) != 6:
                    if i < cut <= j:
                        best = self._junction(s, i, j, cut)
                    elif j - i - 1 >= 3:
                        best = self.energy_hairpin(s, i, j)
                    else:
                        best = INF
                    pmax = min(j - 1, i + MLP + 1)
                    for p in range(i + 1, pmax + 1):
                        lg = p - i - 1
                        qmin = max(p + 1, j - 1 - (MLP - lg))
                        for q in range(qmin, j):
                            if V[p][q] >= INF or _rna_pairtype(s[p], s[q]) == 6:
                                continue
                            if (i < cut <= p) or (q < cut <= j):
                                continue        # loop's unpaired stretch spans the cut
                            c = self._loop_e(s, i, j, p, q) + V[p][q]
                            if c < best:
                                best = c
                    base = a + b + self._d2_ml_close(s, i, j)
                    for u in range(i + 2, j - 1):
                        if M[i + 1][u] < INF and M1[u + 1][j - 1] < INF:
                            c = base + M[i + 1][u] + M1[u + 1][j - 1]
                            if c < best:
                                best = c
                    V[i][j] = best
                m1 = M1[i][j - 1] if M1[i][j - 1] < INF else INF
                if V[i][j] < INF:
                    c = V[i][j] + b + self._d2_ml(s, i, j, n)
                    if c < m1:
                        m1 = c
                M1[i][j] = m1
                m = M[i][j - 1] if M[i][j - 1] < INF else INF
                for k in range(i, j):
                    if V[k][j] < INF:
                        c = V[k][j] + b + self._d2_ml(s, k, j, n)
                        if c < m:
                            m = c
                for u in range(i, j):
                    if M[i][u] < INF and M1[u + 1][j] < INF:
                        c = M[i][u] + M1[u + 1][j]
                        if c < m:
                            m = c
                M[i][j] = m
        F = [0] * n
        for j in range(1, n):
            best = F[j - 1]
            for i in range(0, j):
                if V[i][j] < INF:
                    prev = F[i - 1] if i > 0 else 0
                    c = prev + V[i][j] + self._d2_ext(s, i, j, n)
                    if c < best:
                        best = c
            F[j] = best
        # + DuplexInit (Turner-2004 Misc, +4.10 kcal) for the bound state.
        return (F[n - 1] + 410) / 100.0


_RNA_MODEL_SINGLETON = None
_RNA_MODEL_LOCK = _threading.Lock()


def _rna_model():
    # Double-checked locking: the lock-free fast path serves the common
    # case (singleton already built); the lock serialises the one-time
    # build so concurrent first-requests (the threaded agent server) can't
    # each parse a model. Construction is pure + idempotent, so the lock is
    # belt-and-braces, but it makes the "thread-safe singleton" real.
    global _RNA_MODEL_SINGLETON
    if _RNA_MODEL_SINGLETON is None:
        with _RNA_MODEL_LOCK:
            if _RNA_MODEL_SINGLETON is None:
                text = _gzip.decompress(
                    _base64.b64decode(_RNA_TURNER_PARAMS_GZ_B64)).decode('ascii')
                _RNA_MODEL_SINGLETON = _RNAModel(text)
    return _RNA_MODEL_SINGLETON


def _rna_normalize(seq):
    if not isinstance(seq, str):
        raise ValueError("sequence must be a string")
    s = seq.strip().upper().replace('T', 'U')
    if not s:
        raise ValueError("empty sequence")
    bad = set(s) - {'A', 'C', 'G', 'U'}
    if bad:
        raise ValueError(f"RNA folding needs unambiguous A/C/G/U; got {sorted(bad)}")
    return s


def _rna_fold(seq, *, max_len=_RNA_FOLD_MAX_LEN):
    """Fold an RNA/DNA sequence to its minimum-free-energy secondary
    structure. Returns (dot_bracket, dg_kcal_per_mol). DNA T is read as
    U. Raises ValueError on empty / ambiguous / over-length input."""
    s = _rna_normalize(seq)
    if len(s) > max_len:
        raise ValueError(f"sequence too long to fold ({len(s)} > {max_len} nt cap)")
    return _rna_model().fold(s)


def _rna_mfe(seq, *, max_len=_RNA_FOLD_MAX_LEN):
    """Minimum free energy (kcal/mol) only — the structure is discarded."""
    return _rna_fold(seq, max_len=max_len)[1]


def _rna_eval_structure(seq, dot_bracket):
    """Free energy (kcal/mol) of a GIVEN secondary structure on `seq`."""
    s = _rna_normalize(seq)
    if len(s) != len(dot_bracket):
        raise ValueError("sequence / structure length mismatch")
    return _rna_model().eval_structure(s, dot_bracket)


_RNA_COFOLD_MAX_LEN = 400               # combined A+B length cap (O(n^3) DP)


def _rna_cofold(seq_a, seq_b, *, max_len=_RNA_COFOLD_MAX_LEN):
    """Bound-state heterodimer free energy (kcal/mol) of two strands — the
    ΔG of strand B bound to strand A (e.g. the 16S anti-SD tail hybridized
    to an mRNA window). DNA `T` is read as `U`. The bound state is forced
    (DuplexInit always paid), matching ViennaRNA RNAcofold on binding
    duplexes; a weak / non-complementary pair returns a high (unfavorable)
    ΔG rather than reporting 'unbound'. Raises ValueError on empty /
    ambiguous / over-length input."""
    a = _rna_normalize(seq_a)
    b = _rna_normalize(seq_b)
    if len(a) + len(b) > max_len:
        raise ValueError(
            f"combined length too long to cofold "
            f"({len(a) + len(b)} > {max_len} nt cap)")
    return _rna_model().cofold(a, b)


# ── Ribosome binding site strength (E. coli translation initiation) ─────────
#
# A biophysically-grounded RELATIVE estimate of translation-initiation
# strength, built on the validated RNA folder + cofold. The STRUCTURAL
# energies are exact (`_rna_fold` / `_rna_cofold`, validated to the cent vs
# ViennaRNA); the constants below — the Boltzmann factor β, the
# spacing-penalty curve, and the start-codon ΔG — are literature-standard
# empirical CALIBRATION values, not first-principles. So only RATIOS
# between RBSs are meaningful: this is a tuning / ranking score, NOT an
# absolute expression rate. Validated by relative ranking on the canonical
# determinants (SD strength, 5'UTR occlusion, spacing, start codon), not
# against an absolute thermodynamic oracle.
#
#   ΔG_total = ΔG_hybrid(best SD register) + ΔG_start + ΔG_spacing − ΔG_mRNA
#   strength ∝ exp(−β · ΔG_total)

_RBS_ANTI_SD = 'ACCUCCUUA'         # E. coli 16S rRNA 3' tail (anti-SD), 5'->3'
_RBS_BETA = 0.45                   # mol/kcal — apparent Boltzmann factor (calibration)
_RBS_OPT_SPACING = 5               # optimal SD-to-start aligned spacing (nt)
_RBS_WINDOW = 35                   # nt up/downstream of the start folded for ΔG_mRNA
_RBS_SPACING_SCAN = range(3, 13)   # aligned-spacing registers scanned for the SD
# start-codon : initiator-tRNA(fMet) hybridisation ΔG (kcal/mol, favourable);
# a non-canonical start gets 0 (no favourable initiation). Calibration.
_RBS_START_DG = {'AUG': -1.19, 'GUG': -0.075, 'UUG': -0.075, 'CUG': -0.03,
                 'AUU': -0.03, 'AUC': -0.03, 'AUA': -0.03}


def _rbs_spacing_penalty(d):
    """ΔG penalty (kcal/mol) for an SD-to-start spacing of `d` nt deviating
    from the ~5-nt optimum. Asymmetric: too-short (steric clash with the
    ribosome) is penalised far harder than too-long (entropic). Calibration."""
    if d == _RBS_OPT_SPACING:
        return 0.0
    if d < _RBS_OPT_SPACING:
        return 0.20 * (_RBS_OPT_SPACING - d) ** 2
    return 0.05 * (d - _RBS_OPT_SPACING) ** 2


def _rbs_strength(mrna, start_pos):
    """Relative E. coli translation-initiation strength of the ribosome
    binding site preceding the start codon at `start_pos` (0-based) in
    `mrna` (RNA or DNA; T read as U). Returns a dict::

        {dg_total, dg_mrna, dg_hybrid, spacing, rel_strength}

    `rel_strength` ∝ exp(−β·dg_total): only RATIOS between RBSs are
    meaningful (a ranking score, not an absolute rate). Captures
    SD:anti-SD complementarity, the 5'UTR structure that occludes the site
    (incl. the upstream standby region, via the folded window), the
    SD-to-start spacing, and the start codon. Raises ValueError on bad
    input; returns rel_strength 0.0 when the start is too close to the 5'
    end for an SD to fit."""
    s = mrna.strip().upper().replace('T', 'U') if isinstance(mrna, str) else None
    if not s:
        raise ValueError("mRNA must be a non-empty string")
    bad = set(s) - {'A', 'C', 'G', 'U'}
    if bad:
        raise ValueError(f"mRNA needs unambiguous A/C/G/U; got {sorted(bad)}")
    if (not isinstance(start_pos, int) or isinstance(start_pos, bool)
            or not (0 <= start_pos <= len(s) - 3)):
        raise ValueError(f"start_pos {start_pos!r} out of range for length {len(s)}")
    dg_start = _RBS_START_DG.get(s[start_pos:start_pos + 3], 0.0)
    w0 = max(0, start_pos - _RBS_WINDOW)
    w1 = min(len(s), start_pos + _RBS_WINDOW)
    dg_mrna = _rna_mfe(s[w0:w1])
    best = None
    best_d, best_hybrid = 0, 0.0
    for d in _RBS_SPACING_SCAN:
        end = start_pos - d
        begin = end - len(_RBS_ANTI_SD)
        if begin < 0:
            continue
        dg_h = _rna_cofold(s[begin:end], _RBS_ANTI_SD)
        dg_f = dg_h + dg_start + _rbs_spacing_penalty(d)
        if best is None or dg_f < best:
            best, best_d, best_hybrid = dg_f, d, dg_h
    if best is None:                       # start too close to the 5' end
        # dg_total is None (not float('inf')) so the dict is JSON-valid —
        # `Infinity` is not legal JSON and broke the agent endpoint.
        return {'dg_total': None, 'dg_mrna': round(dg_mrna, 2),
                'dg_hybrid': None, 'spacing': None, 'rel_strength': 0.0}
    dg_total = best - dg_mrna
    return {'dg_total': round(dg_total, 2), 'dg_mrna': round(dg_mrna, 2),
            'dg_hybrid': round(best_hybrid, 2), 'spacing': best_d,
            'rel_strength': round(_math.exp(-_RBS_BETA * dg_total), 3)}


# Graded Shine-Dalgarno library (complementarity to the anti-SD, strong → none)
# + spacer lengths, for reverse RBS design. The forward model ranks them;
# this just spans the strength range so a target can be matched.
_RBS_DESIGN_SD_LADDER = ['UAAGGAGGU', 'AAGGAGGU', 'AGGAGGA', 'AGGAGG', 'UAAGGAG',
                         'GGAGGU', 'AGGAG', 'GGAGG', 'AGGA', 'GAGGA', 'AGAGA',
                         'AAGAA', 'ACAUA', '']
_RBS_DESIGN_SPACERS = {4: 'AAUA', 5: 'AAUAA', 6: 'AACAAU', 7: 'AACAAUA',
                       8: 'AACAAUAA', 9: 'AACAAUAAU'}
_RBS_DESIGN_UPSTREAM = 'UUAAUUAAUU'      # low-structure 5' context (standby region)
_RBS_DESIGN_MAX_CDS = 50000              # cds length cap (the design does
#                                          O(len) passes — bounds worst-case cost)
_RBS_DESIGN_COARSE_SPACER = 6            # spacer length used to rank SDs in the
#                                          coarse pass of the coarse-to-fine search


def _rbs_design(cds, target_strength, *, upstream=_RBS_DESIGN_UPSTREAM):
    """Design a 5'UTR (Shine-Dalgarno + spacer) preceding `cds` (which must
    begin with the start codon) to achieve a target RELATIVE RBS strength.
    Searches a graded SD × spacer library, scores each construct with
    `_rbs_strength`, and returns the design closest to `target_strength`::

        {utr, full, sd, spacing, rel_strength, dg_total,
         achievable_min, achievable_max, on_target}

    Strength is relative (see `_rbs_strength`). A target outside the
    CDS-achievable range yields the nearest achievable design and
    `on_target=False`. Raises ValueError on bad input."""
    c = cds.strip().upper().replace('T', 'U') if isinstance(cds, str) else ''
    if not c or (set(c) - {'A', 'C', 'G', 'U'}):
        raise ValueError("cds must be a non-empty A/C/G/U(T) string")
    if len(c) < 3:
        raise ValueError("cds must include the start codon (>= 3 nt)")
    if len(c) > _RBS_DESIGN_MAX_CDS:
        raise ValueError(
            f"cds too long ({len(c)} > {_RBS_DESIGN_MAX_CDS} nt cap)")
    if (isinstance(target_strength, bool)
            or not isinstance(target_strength, (int, float))
            or not _math.isfinite(target_strength)
            or target_strength < 0):
        raise ValueError(
            "target_strength must be a finite non-negative number")
    up = (upstream or '').strip().upper().replace('T', 'U')
    if set(up) - {'A', 'C', 'G', 'U'}:
        raise ValueError("upstream must be A/C/G/U(T)")
    # Coarse-to-fine search. A full 14 SD × 6 spacer = 84-eval scan costs
    # ~5-7 s. Instead: (coarse) rank every SD at one representative spacer —
    # 14 evals — then (fine) scan every spacer for the SD that brackets the
    # target plus its two ladder neighbours — ~15 evals. The fine pass
    # covers the target's neighbourhood densely, so the achievable extreme
    # NEAR the target (the one that drives `on_target`) is exact; the FAR
    # extreme is an estimate. ~29 evals total (~2.5-3x faster).
    best = None
    lo, hi = float('inf'), -1.0
    spacers = _RBS_DESIGN_SPACERS
    coarse_len = _RBS_DESIGN_COARSE_SPACER

    def _consider(sd, slen, sp):
        nonlocal best, lo, hi
        utr = up + sd + sp
        r = _rbs_strength(utr + c, len(utr))
        v = r['rel_strength']
        lo, hi = min(lo, v), max(hi, v)
        if best is None or abs(v - target_strength) < abs(
                best['rel_strength'] - target_strength):
            best = {'utr': utr, 'sd': sd, 'spacing': slen,
                    'rel_strength': v, 'dg_total': r['dg_total']}
        return v

    coarse = []
    for i, sd in enumerate(_RBS_DESIGN_SD_LADDER):
        coarse.append((_consider(sd, coarse_len, spacers[coarse_len]), i))
    coarse.sort(key=lambda x: abs(x[0] - target_strength))
    best_i = coarse[0][1]
    last = len(_RBS_DESIGN_SD_LADDER) - 1
    for i in {best_i, max(0, best_i - 1), min(last, best_i + 1)}:
        sd = _RBS_DESIGN_SD_LADDER[i]
        for slen, sp in spacers.items():
            if slen == coarse_len:
                continue                     # already scored in the coarse pass
            _consider(sd, slen, sp)
    assert best is not None              # the SD ladder is non-empty -> always set
    best['full'] = best['utr'] + c
    best['achievable_min'] = round(lo, 3)
    best['achievable_max'] = round(hi, 3)
    best['on_target'] = lo <= target_strength <= hi
    return best


def _assemble_operon(genes, *, promoter='', terminator='',
                     leader=_RBS_DESIGN_UPSTREAM):
    """Assemble a contiguous bacterial operon — promoter + (RBS + CDS) per
    gene + terminator — CONTEXT-AWARE: each RBS is reverse-designed against
    the REAL upstream sequence (the promoter, or the preceding gene's 3'
    end), so the achieved in-context strength tracks the target. (Designing
    each RBS in isolation then concatenating does NOT — the upstream can
    occlude it.) When a gene's target is unreachable in its context (e.g.
    the previous CDS's 3' end sequesters the SD), the nearest achievable is
    used and that gene's `on_target` is False — a real, useful signal.

    `genes`: a non-empty list of dicts {cds, target_strength, name?} — each
    `cds` begins with the start codon (DNA `T` read as `U`). `leader` is
    the low-structure 5' standby used when there is no promoter. Returns::

        {sequence, layout, genes}

    `sequence` is DNA (T). `layout` is the ordered element map
    [{kind, name, start, end}] (kind ∈ promoter/rbs/cds/terminator) where
    `sequence[start:end]` is EXACTLY that element — contiguous, no gaps or
    overlaps. `genes` is the per-gene report [{name, target, cds_len, rbs,
    spacing, rel_strength, on_target}]. Raises ValueError on bad input."""
    if not isinstance(genes, (list, tuple)) or not genes:
        raise ValueError("genes must be a non-empty list")

    def _norm(x, label):
        x = (x or '').strip().upper().replace('T', 'U')
        if set(x) - {'A', 'C', 'G', 'U'}:
            raise ValueError(f"{label} must be A/C/G/U(T)")
        return x
    prom = _norm(promoter, 'promoter')
    term = _norm(terminator, 'terminator')
    lead = _norm(leader, 'leader')

    assembled = prom
    layout, anchors = [], []
    if prom:
        layout.append({'kind': 'promoter', 'name': 'promoter',
                       'start': 0, 'end': len(prom)})
    for i, g in enumerate(genes):
        if not isinstance(g, dict):
            raise ValueError(f"gene {i} must be a dict")
        cds = g.get('cds')
        if not isinstance(cds, str):
            raise ValueError(f"gene {i}: missing string 'cds'")
        cds = cds.strip().upper().replace('T', 'U')
        target = g.get('target_strength', g.get('target'))
        name = str(g.get('name') or f"gene{i + 1}")
        if assembled:
            ctx = assembled[-_RBS_WINDOW:]
            d = _rbs_design(cds, target, upstream=ctx)    # validates cds + target
            rbs = d['utr'][len(ctx):]                      # strip the already-present upstream
        else:
            d = _rbs_design(cds, target, upstream=lead)
            rbs = d['utr']                                 # the leader is the operon's 5' start
        rbs_start = len(assembled)
        layout.append({'kind': 'rbs', 'name': f"{name} RBS",
                       'start': rbs_start, 'end': rbs_start + len(rbs)})
        assembled += rbs
        cds_start = len(assembled)
        layout.append({'kind': 'cds', 'name': name,
                       'start': cds_start, 'end': cds_start + len(cds)})
        assembled += cds
        anchors.append({'name': name, 'target': target, 'cds_len': len(cds),
                        'rbs': rbs.replace('U', 'T'), 'spacing': d['spacing'],
                        'cds_start': cds_start})
    if term:
        layout.append({'kind': 'terminator', 'name': 'terminator',
                       'start': len(assembled), 'end': len(assembled) + len(term)})
        assembled += term

    report = []
    for a in anchors:
        try:
            rel = _rbs_strength(assembled, a['cds_start'])['rel_strength']
        except ValueError:
            rel = None
        tgt = a['target']
        on = (rel is not None and isinstance(tgt, (int, float))
              and abs(rel - tgt) <= 0.25 * max(tgt, 1e-9))
        report.append({'name': a['name'], 'target': tgt, 'cds_len': a['cds_len'],
                       'rbs': a['rbs'], 'spacing': a['spacing'],
                       'rel_strength': rel, 'on_target': on})
    return {'sequence': assembled.replace('U', 'T'), 'layout': layout,
            'genes': report}


# What is intentionally NOT extracted (yet):
#
#   _scan_restriction_sites + _scan_restriction_sites_impl — depend on
#   `_NEB_ENZYMES` (giant dict), `_RESTR_SCAN_CACHE`, and several module-
#   level worker-side helpers. Not pure.
#
#   _translate_cds — depends on `_GENETIC_CODE` and Biopython's translate.
#   Could move but increases the import surface meaningfully.
#
#   _feat_bounds — touches Biopython's `SeqFeature` / `CompoundLocation`
#   types; extracting would force splicecraft_biology to import Biopython
#   eagerly at module load, costing ~250 ms of startup time on every
#   `splicecraft-cli` call (which only imports for type hints today).
#
#   _bp_in — a METHOD on PlasmidMap, not a module-level function.
#
# Future extractions are welcome but must pass the three-test rule
# in CONTRIBUTING.md: no PlasmidApp coupling, reduces complexity at
# the call site, every existing test passes unchanged.
