"""splicecraft_cloning — construction simulation (Phase D, layer L3).

The "simulate the real steps" construction helpers ([INV-127]): build real
amplicons (`_simulate_primed_amplicon`), assemble real cloned plasmids via
digest+ligation (`_simulate_cloned_plasmid`), the pUPD2 backbone stub, overhang
fusion, the Commercial-SaaS `.dna` history serialisation, and the **Gibson assembly
simulator** ([INV-85/86]): `_simulate_gibson_assembly` + its `_gibson_*` helpers
(overlap detect, body-length validate, product build, feature shift + origin-wrap
merge) and `_gibson_record_from_result`. Extracted so the cloning modal/screen
siblings can import them. Layer L3: imports biology(L0), dataaccess(L1), record(L1),
history(L2), logging(L0); used by the modals (L4). Re-exported by the hub so every
call site resolves unchanged.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation-only; the real Bio import is lazy, inside the fn
    from Bio.SeqRecord import SeqRecord

from splicecraft_biology import _rc
from splicecraft_dataaccess import (
    _BUILTIN_GRAMMARS, _GB_CODING_PART_TYPES, _GB_L0_ENZYME_SITE, _GB_PAD, _GB_SPACER,
)
from splicecraft_history import (
    _CommercialSaaSHistoryNode, _coerce_int_or_zero, _history_now_str,
)
from splicecraft_logging import _log, _timed
from splicecraft_record import _normalize_primer_seq


def _serialize_commercialsaas_history(root: "_CommercialSaaSHistoryNode | None"
                                  ) -> str:
    """Serialise a `_CommercialSaaSHistoryNode` back to UTF-8 XML text
    suitable for passing to `_pack_commercialsaas_history_payload`. Empty
    / None root yields an empty `<HistoryTree/>` document so we
    can still emit a valid history packet (e.g., to mark a
    construction with no parental input)."""
    import xml.etree.ElementTree as _ET
    tree_root = _ET.Element("HistoryTree")
    if root is not None:
        tree_root.append(root.element)
        # 2026-05-27 (audit-3 M5): also re-emit any sibling top-level
        # Node elements the parser stashed. Preserves round-trip for
        # rare multi-tree history files.
        for sibling in getattr(root, "_sibling_elements", []) or []:
            tree_root.append(sibling)
    body = _ET.tostring(tree_root, encoding="unicode")
    # CommercialSaaS's emitted XML always opens with the standard
    # declaration. Re-add it on the way out for compatibility — Python's
    # `tostring(encoding="unicode")` skips the declaration by default.
    return f'<?xml version="1.0" encoding="UTF-8"?>{body}'


def _build_amplicon_history_xml(*, name: str, seq_len: int,
                                 fwd_seq: str, rev_seq: str,
                                 start_1based: int, end_1based: int,
                                 fwd_name: str = "forward primer",
                                 rev_name: str = "reverse primer",
                                 ) -> "str | None":
    """Build a `<HistoryTree>` for a PCR amplicon made IN SpliceCraft
    that matches the element set a `.dna` import carries: an
    ``amplifyFragment`` node + an ``amplify`` InputSummary (val1/val2 =
    the amplified region) + the two primers as `<Oligo>` children. So a
    de-novo amplicon's History shows the same Primers / region detail as
    an imported one — harmonised history regardless of origin (user
    request 2026-06-01). Best-effort; returns None on failure."""
    try:
        root = _CommercialSaaSHistoryNode.new(
            name=str(name or "amplicon") + ".dna",
            seq_len=_coerce_int_or_zero(seq_len),
            circular=False, operation="amplifyFragment", node_id=0,
            date=_history_now_str(),
        )
        root.add_input_summary(
            manipulation="amplify",
            val1=_coerce_int_or_zero(start_1based),
            val2=_coerce_int_or_zero(end_1based),
        )
        if fwd_seq:
            root.add_oligo(name=fwd_name, sequence=str(fwd_seq))
        if rev_seq:
            root.add_oligo(name=rev_name, sequence=str(rev_seq))
        return _serialize_commercialsaas_history(root)
    except Exception:
        _log.debug("amplicon history: build failed for %r", name,
                    exc_info=True)
        return None


def _atg_offset_for_part(part_oh5: str, part_type: str) -> int:
    """Return the number of bases the 5' boundary of a coding-part
    feature should extend upstream to include its embedded start codon.

    GB 2.0 puts the ATG start codon inside the AATG fusion overhang
    (Pos 12→13 boundary): AATG = A + ATG, where the A is the spacer
    base and ATG is the start codon. The domesticator's forward primer
    encodes AATG as the part's 5' fusion overhang and PCR-binds at
    codon 2 of the source CDS — so the L0 part's body sequence
    (`part["sequence"]`) starts at codon 2, NOT at the ATG.

    When the part is assembled into an L1 plasmid the upstream LINK
    contributes its AATG (Pos 12 oh3) which fuses with the part's
    AATG oh5; the resulting cloned sequence reads
    ``...LINK-body...A + ATG + [codon2]...`` and the ATG sits in the
    last 3 nt of the fusion overhang. A feature annotation that only
    spans [body-start, body-end) would therefore drop the user's
    start codon — visibly broken in the L1 plasmid map.

    This helper returns ``3`` when the part needs the upstream
    extension (any coding part type with ``oh5 == AATG``: Signal
    peptide, CDS, CDS-NS, CDS-NS (CT)), and ``0`` otherwise. Returning
    a numeric offset rather than a bool lets callers do
    ``feature_start -= _atg_offset_for_part(oh5, ptype)`` without
    branching on every feature loop iteration.

    Regression guard for 2026-05-10: user reported "CDS's cloned seem
    to lose the annotation of their ATG because it also occupies the
    AATG overhang."
    """
    if not isinstance(part_oh5, str) or not isinstance(part_type, str):
        return 0
    oh = part_oh5.upper()
    # Coding types: known GB types + the MoClo-equivalent
    # ("CDS" / "C-tag") declared in the MoClo Plant grammar.
    # Custom grammars that introduce new translational part type
    # names should add them to `_GB_CODING_PART_TYPES` (the name
    # is historical — the set covers BOTH GB and MoClo by union).
    if part_type not in _GB_CODING_PART_TYPES:
        return 0
    # Detect ATG-fusion: any 4+ nt 5' overhang whose LAST 3 nt
    # are "ATG" carries the start codon (GB: AATG; hypothetical
    # NATG variants in custom grammars also qualify). MoClo
    # Plant's CDS oh5 is "AGGT" — last 3 = "GGT" ≠ ATG, so this
    # naturally returns 0 there. The `>= 4` guard enforces the
    # standard Type IIS overhang width (4 nt) and rejects a
    # degenerate 3-nt "ATG" overhang which isn't biologically
    # standard and would otherwise erroneously trigger the skip.
    if oh.endswith("ATG") and len(oh) >= 4:
        return 3
    return 0


def _fuse_overhang_body(oh5: str, body: str, part_type: str) -> str:
    """Join a 5' fusion overhang to a coding-part body, COLLAPSING the
    start-codon overlap.

    When the overhang embeds the start codon (Golden Braid / MoClo ``AATG``
    = ``A`` spacer + ``ATG`` start; any 4+ nt overhang ending in ATG) AND the
    stored body still carries that same ATG (the Domesticator-saved
    convention), drop the body's redundant leading ATG so the fused sequence
    reads ``AATG[codon2…]`` instead of ``AATG·ATG[codon2…]`` — a duplicated
    start codon that frameshifts the rest of the ORF (the 2026-05-30 'double
    ATG' bug). The designed forward primer already binds at codon 2, so this
    makes the simulated assembly match what the primers produce on the bench.

    Grammar-agnostic via `_atg_offset_for_part` — a no-op for MoClo-Plant
    ``AGGT``, non-coding parts, or a body already trimmed to codon 2 — so it
    is safe and idempotent at every site that assembles a part body behind
    its 5' overhang (Golden Braid AND MoClo)."""
    off = _atg_offset_for_part(oh5, part_type)
    if off and str(body)[:3].upper() == "ATG":
        body = body[off:]
    return oh5 + body


def _build_pupd2_backbone_stub(seed: int = 0xBACDBAC0, length: int = 420) -> str:
    """Return a deterministic ACGT string, free of BsaI/Esp3I/BsmBI sites on
    both strands, for use as a pUPD2-shaped placeholder backbone.

    Deterministic because the same insert must produce the same cloned
    sequence across sessions (otherwise the "Copy Cloned Sequence" output
    would silently drift). Seeded with a fixed constant.
    """
    import random as _random_mod
    rng = _random_mod.Random(seed)
    bases = [rng.choice("ACGT") for _ in range(length)]
    # Scrub both strands — the linear backbone becomes part of a circular
    # product, so a top-strand CGTCTC (Esp3I/BsmBI) and a bottom-strand
    # GAGACG are biologically equivalent and both must be absent.
    forbidden = ("GGTCTC", "GAGACC", "CGTCTC", "GAGACG")
    i = 0
    while i <= length - 6:
        window = "".join(bases[i:i + 6])
        if window in forbidden:
            # Flip the middle base to something that can't re-hit any
            # forbidden site; ACGT minus the current base leaves 3 choices.
            middle = i + 3
            current = bases[middle]
            for replacement in "ACGT":
                if replacement != current:
                    bases[middle] = replacement
                    break
            # Rewind a bit to catch any new site created at the boundary.
            i = max(0, i - 5)
            continue
        i += 1
    return "".join(bases)


_PUPD2_BACKBONE_STUB: str = _build_pupd2_backbone_stub()


def _simulate_primed_amplicon(
    insert: str, oh5: str, oh3: str,
    grammar: "dict | None" = None,
    part_type: str = "",
) -> str:
    """PCR amplicon top strand (5'→3'), as it would run on a pre-digest gel.

    Structure:  [pad] [enzyme site] [spacer] [oh5] [insert] [oh3]
                [rc(spacer)] [rc(enzyme site)] [rc(pad)]

    Matches the primer geometry in :func:`_design_gb_primers`. Defaults to
    Golden Braid L0 (Esp3I); pass ``grammar`` to use a different cloning
    grammar's enzyme/pad/spacer (e.g., MoClo Plant uses BsaI). Used by
    both DomesticatorModal (active grammar at design time) and
    PartsBinModal "Copy Primed Sequence" (the part's stored grammar).
    """
    g = grammar if isinstance(grammar, dict) else _BUILTIN_GRAMMARS["gb_l0"]
    pad    = g.get("pad",    _GB_PAD)
    site   = g.get("site",   _GB_L0_ENZYME_SITE)
    spacer = g.get("spacer", _GB_SPACER)
    left_tail  = pad + site + spacer
    right_tail = _rc(spacer) + _rc(site) + _rc(pad)
    # `_fuse_overhang_body` collapses the AATG-CDS start-codon overlap so the
    # amplicon matches the designed primers (which bind at codon 2) — no
    # duplicated ATG. Grammar-agnostic (GB + MoClo); no-op otherwise.
    return (left_tail + _fuse_overhang_body(oh5, insert, part_type)
            + oh3 + right_tail)


def _simulate_cloned_plasmid(insert: str, oh5: str, oh3: str,
                             part_type: str = "") -> str:
    """Simulated cloned circular plasmid, linearised at the 5' overhang.

    After the cloning grammar's enzyme cuts both the amplicon and the
    backbone, the insert fragment carries `oh5…oh3` on its 4-nt sticky
    ends and ligates into the backbone in a single orientation. The
    circular product, read starting at `oh5`, is:

        [oh5] [insert] [oh3] [backbone_body]

    The backbone here is `_PUPD2_BACKBONE_STUB` — a scrubbed placeholder
    that contains no BsaI/Esp3I sites on either strand, so the simulated
    plasmid is guaranteed not to re-cut in either L0 or L1 assembly.
    """
    return (_fuse_overhang_body(oh5, insert, part_type)
            + oh3 + _PUPD2_BACKBONE_STUB)


# ── Gibson assembly ───────────────────────────────────────────────────────────
#
# Gibson chemistry: 5' exonuclease chews back the 5' end of each fragment,
# exposing a 3' single-stranded tail. Tails that share a complementary
# homology region anneal; DNA polymerase fills gaps; DNA ligase seals
# nicks. Net result: adjacent fragments are joined seamlessly and the
# homology region appears ONCE in the product.
#
# The simulator here treats each fragment as a top-strand DNA sequence
# (5' → 3') and finds the longest exact-match suffix-of-A / prefix-of-B
# overlap at each junction. The user is responsible for designing
# primer tails that produce these overlaps — the simulator doesn't
# pretend to extend short overlaps via heuristic search. Below
# `_GIBSON_MIN_OVERLAP_BP` (15 bp by default, Gibson's commonly cited
# floor), the junction is rejected so the user can fix the design
# before committing the assembled product to the library.

_GIBSON_MIN_OVERLAP_BP = 15
_GIBSON_MAX_OVERLAP_BP = 200   # cap the suffix/prefix probe; longer is
                                # unrealistic for Gibson primer tails


def _gibson_overlap_len(a_seq: str, b_seq: str, *,
                          min_overlap: int = _GIBSON_MIN_OVERLAP_BP,
                          max_overlap: int = _GIBSON_MAX_OVERLAP_BP,
                          ) -> int:
    """Length of the longest exact-match overlap between `a_seq`'s 3'
    end and `b_seq`'s 5' end, in `[min_overlap, max_overlap]`.
    Returns 0 if no overlap of at least `min_overlap` bp matches.

    Comparison is case-insensitive. Bigger overlap preferred —
    biologically, the homology arm the user designed is the longest
    exact match, not a degenerate short one inside it.

    When `a_seq` is identical to `b_seq` (e.g. the n=1 circular
    self-circularisation probe, where the simulator passes the same
    fragment as both a and b), the probe caps at `len(a) - 1` so the
    trivial whole-string match is skipped — without the cap, the user's
    intended short homology arm at the fragment ends would be masked
    by the always-matching full string.
    """
    if min_overlap <= 0:
        min_overlap = 1
    a = a_seq.upper()
    b = b_seq.upper()
    # Whole-string match is degenerate when the two sides are the same
    # sequence — cap one shorter so the probe finds a real arm overlap.
    # For distinct sequences a full-length match is biologically legal
    # (one fragment is a prefix/suffix of the other); the downstream
    # body-length validation in `_simulate_gibson_assembly` decides.
    full_match_safe = (a != b)
    max_check = min(max_overlap, len(a), len(b))
    if not full_match_safe:
        max_check = min(max_check, len(a) - 1)
    if max_check < min_overlap:
        return 0
    for k in range(max_check, min_overlap - 1, -1):
        if a[-k:] == b[:k]:
            return k
    return 0


@_timed("op.gibson_simulate")
# ── _simulate_gibson_assembly helpers ─────────────────────────────────────
#
# The Gibson simulator was 370 lines pre-refactor. The math is
# biology-critical (junction-overlap detection, body-length validation,
# wrap-aware feature shifting, wrap-half re-merge), so the helpers are
# defined as pure functions with explicit `(input) → output` signatures
# — no shared closure state, no hidden dependencies. Behaviour is bit-
# identical to the pre-refactor version, tested by tests/test_gibson.py
# (47 cases) which passed before AND after the extraction.


def _gibson_failure(circular: bool, errors: list[str],
                       overlaps: "list[dict] | None" = None,
                       warnings: "list[str] | None" = None) -> dict:
    """Standard failure-shaped result dict for the Gibson pipeline.

    Centralising it ensures every short-circuit returns the same shape
    (success=False, empty product_seq, empty features) — a property the
    UI consumer (`GibsonAssemblyPane`) relies on."""
    return {
        "success":     False,
        "product_seq": "",
        "circular":    circular,
        "features":    [],
        "overlaps":    list(overlaps or []),
        "errors":      list(errors),
        "warnings":    list(warnings or []),
    }


def _gibson_normalize_fragments(
    fragments: list[dict],
) -> "tuple[list[dict] | None, str | None]":
    """Uppercase + whitespace-strip each fragment's sequence.

    Returns ``(norm_fragments, None)`` on success or ``(None, error_msg)``
    on the two short-circuit failure modes: empty input list, or any
    fragment that isn't a dict.

    The normalised list is a parallel structure to `fragments` —
    name / sequence / features — with the sequence cleaned. We
    intentionally don't mutate the caller's dicts."""
    if not fragments:
        return None, "No fragments supplied."
    norm_fragments: list[dict] = []
    for f in fragments:
        if not isinstance(f, dict):
            return None, "Each fragment must be a dict."
        raw = str(f.get("sequence") or "")
        # Sweep #30 (2026-05-28): map RNA U->T to match the rest of the
        # app's normalisation. A fragment pasted in RNA notation would
        # otherwise fail overlap detection against a T-notation neighbour
        # (and a downstream _rc would mangle the U, which has no entry in
        # the complement table). Main sequence-load paths already do this.
        cleaned = "".join(
            ch for ch in raw.upper().replace("U", "T") if not ch.isspace()
        )
        norm_fragments.append({
            "name":     str(f.get("name") or "?"),
            "sequence": cleaned,
            "features": list(f.get("features") or []),
        })
    return norm_fragments, None


def _gibson_detect_overlaps(
    norm_fragments: list[dict], *, min_overlap: int, circular: bool,
) -> "tuple[list[dict], list[int], list[str]]":
    """For each junction (consecutive pair plus wrap when circular),
    detect the longest exact-match suffix/prefix overlap.

    Returns ``(overlaps, overlap_lens, errors)``:
      * `overlaps`: one dict per junction (junction, from, to, length,
        seq, ok, is_wrap, rc_hint) — the UI shows the full chain even
        on partial failure, so this is populated regardless of success.
      * `overlap_lens`: parallel list of int lengths for the build step.
      * `errors`: human-readable junction-failure messages.
    """
    n = len(norm_fragments)
    n_junctions = n if circular else n - 1
    overlaps: list[dict] = []
    overlap_lens: list[int] = []
    errors: list[str] = []
    for i in range(n_junctions):
        a = norm_fragments[i]
        b = norm_fragments[(i + 1) % n]
        a_seq = a["sequence"]
        b_seq = b["sequence"]
        k = _gibson_overlap_len(a_seq, b_seq, min_overlap=min_overlap)
        ok = k >= min_overlap
        rc_hint = _gibson_rc_hint(a, b, min_overlap=min_overlap) if not ok else ""
        overlaps.append({
            "junction": i + 1,
            "from":     a["name"],
            "to":       b["name"],
            "length":   k,
            "seq":      a_seq[-k:] if k else "",
            "ok":       ok,
            "is_wrap":  (circular and i == n - 1),
            "rc_hint":  rc_hint,
        })
        if not ok:
            errors.append(
                f"Junction {i+1} ({a['name']!r} → {b['name']!r}): "
                f"no overlap ≥ {min_overlap} bp." + rc_hint
            )
        overlap_lens.append(k)
    return overlaps, overlap_lens, errors


def _gibson_rc_hint(a: dict, b: dict, *, min_overlap: int) -> str:
    """Reverse-orientation diagnostic. If the forward overlap failed,
    probe RC(b) and RC(a) at a lower threshold (min of `min_overlap`
    and 10 bp so the user sees the hint even with a high min). Returns
    `""` if neither RC orientation yields a plausible overlap.

    Returning a string so the caller can concat into the error message
    without an extra branch — keeps the failure path linear."""
    a_seq = a["sequence"]
    b_seq = b["sequence"]
    probe_min = min(min_overlap, 10)
    k_b_rc = _gibson_overlap_len(a_seq, _rc(b_seq), min_overlap=probe_min)
    k_a_rc = _gibson_overlap_len(_rc(a_seq), b_seq, min_overlap=probe_min)
    if k_b_rc >= probe_min and k_b_rc >= k_a_rc:
        return (
            f" — but reverse-complement of {b['name']!r} "
            f"yields a {k_b_rc} bp overlap; "
            f"did you mean to flip {b['name']!r}?"
        )
    if k_a_rc >= probe_min:
        return (
            f" — but reverse-complement of {a['name']!r} "
            f"yields a {k_a_rc} bp overlap; "
            f"did you mean to flip {a['name']!r}?"
        )
    return ""


def _gibson_validate_body_lengths(
    norm_fragments: list[dict], overlap_lens: list[int], *, circular: bool,
) -> list[str]:
    """Check that every fragment has body bases left after its homology
    arms. A fragment fully consumed by overlap(s) is biologically
    redundant — its bases are entirely supplied by adjacent fragments.

    Returns a list of error messages; empty list means OK.

    Three sub-cases:
      * fragments[i>0]: leading overlap from junction i-1.
      * circular n==1: self-circularisation uses overlap_lens[0] as a
        single wrap arm.
      * circular n>1: last fragment has BOTH leading + trailing arms.
    """
    n = len(norm_fragments)
    errors: list[str] = []
    for i in range(1, n):
        oh_lead = overlap_lens[i - 1]
        frag_len = len(norm_fragments[i]["sequence"])
        if oh_lead >= frag_len:
            errors.append(
                f"Fragment {norm_fragments[i]['name']!r} is consumed "
                f"by its leading {oh_lead} bp overlap "
                f"(fragment is {frag_len} bp). "
                f"Use a longer fragment or shorter overlap."
            )
    if circular and not errors:
        if n == 1:
            wrap_oh = overlap_lens[0]
            frag_len = len(norm_fragments[0]["sequence"])
            if wrap_oh >= frag_len:
                errors.append(
                    f"Fragment {norm_fragments[0]['name']!r} is fully "
                    f"consumed by its self-circularisation overlap "
                    f"({wrap_oh} ≥ {frag_len} bp)."
                )
        else:
            last_lead  = overlap_lens[n - 2]
            last_trail = overlap_lens[n - 1]
            last_len = len(norm_fragments[-1]["sequence"])
            if last_lead + last_trail >= last_len:
                errors.append(
                    f"Fragment {norm_fragments[-1]['name']!r} is fully "
                    f"consumed by its homology arms ({last_lead} + "
                    f"{last_trail} ≥ {last_len} bp). Pick a longer "
                    f"fragment or shorter overlaps."
                )
            # Fragment 0 also carries BOTH arms in a circular assembly: a
            # leading wrap arm (junction (n-1)->0) and a trailing arm
            # (junction 0->1). The i>0 loop above never validates it.
            first_lead  = overlap_lens[n - 1]
            first_trail = overlap_lens[0]
            first_len = len(norm_fragments[0]["sequence"])
            if first_lead + first_trail >= first_len:
                errors.append(
                    f"Fragment {norm_fragments[0]['name']!r} is fully "
                    f"consumed by its homology arms ({first_lead} + "
                    f"{first_trail} ≥ {first_len} bp). Pick a longer "
                    f"fragment or shorter overlaps."
                )
    return errors


def _gibson_short_fragment_warnings(
    norm_fragments: list[dict], *, min_overlap: int,
) -> list[str]:
    """Soft warning: fragments < 3× min_overlap are legal but unusual,
    flag for awareness without blocking the save."""
    warnings: list[str] = []
    for f in norm_fragments:
        if 0 < len(f["sequence"]) < 3 * min_overlap:
            warnings.append(
                f"Fragment {f['name']!r} is short "
                f"({len(f['sequence'])} bp) relative to the "
                f"{min_overlap} bp homology arms — assembly may "
                f"be hard to confirm by gel."
            )
    return warnings


def _gibson_build_product(
    norm_fragments: list[dict], overlap_lens: list[int], *, circular: bool,
) -> "tuple[str, list[int]]":
    """Concatenate fragment bodies into the product sequence, tracking
    each fragment's offset in product coordinates. The trailing copy
    of each overlap is dropped (`frag[i+1][oh_lead:]`).

    Returns ``(product_seq, offsets)`` where offsets[i] is the product
    coord at which fragments[i]'s local-pos 0 lands. For wrap fragments
    this offset can be negative-equivalent (modulo product_len)."""
    n = len(norm_fragments)
    seq_parts: list[str] = []
    offsets: list[int] = []
    first_seq = norm_fragments[0]["sequence"]
    seq_parts.append(first_seq)
    offsets.append(0)
    cursor = len(first_seq)
    for i in range(1, n):
        oh_lead = overlap_lens[i - 1]
        frag_seq = norm_fragments[i]["sequence"]
        body = frag_seq[oh_lead:]
        seq_parts.append(body)
        # Fragment i's local-pos 0 maps to product pos (cursor - oh_lead)
        # — the leading overlap bases already exist as the previous
        # fragment's tail.
        offsets.append(cursor - oh_lead)
        cursor += len(body)
    if circular:
        if n == 1:
            # Self-circularisation: drop the trailing wrap overlap from
            # the only fragment. seq_parts has one entry == fragments[0].
            wrap_oh = overlap_lens[0]
            if wrap_oh > 0:
                seq_parts[0] = seq_parts[0][:-wrap_oh]
        else:
            wrap_oh = overlap_lens[n - 1]
            if wrap_oh > 0:
                # Drop the last fragment's trailing-overlap bases —
                # they equal the first fragment's leading bases, which
                # are already at the start of the product.
                seq_parts[-1] = seq_parts[-1][:-wrap_oh]
    product_seq = "".join(seq_parts)
    return product_seq, offsets


def _gibson_shift_features(
    norm_fragments: list[dict], overlap_lens: list[int],
    offsets: list[int], product_len: int, *, circular: bool,
) -> list[dict]:
    """Shift each fragment's features into product coords. Features
    wholly inside a fragment's leading-overlap region (i > 0) are
    skipped — the preceding fragment already supplies the same bases
    at the same product coords, so emitting them again duplicates the
    annotation. For circular, features that straddle the wrap junction
    become wrap features (`end < start`) per the dict-feature
    convention; `_feat_len` / `_bp_in` handle the resulting topology."""
    shifted: list[dict] = []
    for i, f_dict in enumerate(norm_fragments):
        offset = offsets[i]
        oh_lead = overlap_lens[i - 1] if i > 0 else 0
        for feat in (f_dict.get("features") or []):
            if not isinstance(feat, dict):
                continue
            if str(feat.get("type") or "") == "source":
                continue
            try:
                s = int(feat.get("start", 0))
                e = int(feat.get("end",   0))
            except (TypeError, ValueError):
                continue
            if e <= s:
                continue
            if i > 0 and e <= oh_lead:
                # Feature lies entirely in the leading-overlap region
                # — preceding fragment already supplies its annotation
                # at the same product coords. Skip to avoid a duplicate.
                continue
            new_s = offset + s
            new_e = offset + e
            span = new_e - new_s
            if circular and product_len > 0:
                # Wrap math: modulo into product coords. The span
                # (linear length) is invariant under shift, so we
                # decide product topology from `span` not from the
                # `ms <=> me` ordering — which is ambiguous when
                # `ms == me` mod product_len.
                ms = new_s % product_len
                me_raw = new_e % product_len
                # `me == 0` with span > 0 means the feature ends
                # exactly at the product's wrap point — keep it as
                # `product_len` so the linear-form expression matches.
                me = product_len if (me_raw == 0 and span > 0) else me_raw
                if span >= product_len:
                    new_s, new_e = 0, product_len
                else:
                    new_s, new_e = ms, me
            else:
                # Linear product. Negative `new_s` means the feature's
                # 5' edge sits before the product start — the only way
                # this happens is if a middle fragment is shorter than
                # its lead+trail arms (a pathological design the
                # simulator doesn't reject up front; see the body
                # length check above which only catches single-arm
                # exhaustion). Skip rather than clamp: clamping would
                # silently shift the feature's biological coordinates,
                # whereas skip is honest about the lost annotation.
                if new_s < 0:
                    continue
                if new_e > product_len:
                    new_e = product_len
                if new_e <= new_s:
                    continue
            shifted.append({**feat, "start": new_s, "end": new_e})
    return shifted


def _gibson_merge_wrap_halves(
    shifted: list[dict], product_len: int, *, circular: bool,
) -> list[dict]:
    """Re-merge `_wrap_pair`-tagged halves: when a circular source
    plasmid's wrap feature was split by `_record_features`, both halves
    carry the same ``_wrap_pair`` id + ``_wrap_role`` (head/tail). After
    shifting, if the two halves are still adjacent across the product's
    wrap (head.start == 0 AND tail.end == product_len) we collapse them
    back into one wrap feature. For linear products or non-adjacent
    halves the split is preserved — the biological feature was severed
    by the assembly geometry."""
    out_feats: list[dict] = []
    if not (circular and product_len > 0):
        # Linear product: just strip wrap-pair sentinels and pass through.
        for f in shifted:
            out_feats.append({
                k: v for k, v in f.items()
                if not k.startswith("_wrap_")
            })
        return out_feats
    # Index pairs in one pass so we can match heads to tails O(1) below.
    pair_index: dict[str, list[dict]] = {}
    for f in shifted:
        pid = f.get("_wrap_pair")
        if pid:
            pair_index.setdefault(pid, []).append(f)
    handled_pairs: set[str] = set()
    for f in shifted:
        pid = f.get("_wrap_pair")
        if pid and pid not in handled_pairs:
            halves = pair_index.get(pid) or []
            if len(halves) == 2:
                head = next((h for h in halves
                             if h.get("_wrap_role") == "head"), None)
                tail = next((h for h in halves
                             if h.get("_wrap_role") == "tail"), None)
                if (head is not None and tail is not None
                        and head["start"] == 0
                        and tail["end"] == product_len):
                    merged = {k: v for k, v in tail.items()
                              if not k.startswith("_wrap_")}
                    merged["start"] = tail["start"]
                    merged["end"]   = head["end"]
                    out_feats.append(merged)
                    handled_pairs.add(pid)
                    continue
        # Either no wrap pair, pair already handled, or halves not
        # adjacent — strip wrap sentinels and pass through individually.
        if pid in handled_pairs:
            continue
        out_feats.append({
            k: v for k, v in f.items()
            if not k.startswith("_wrap_")
        })
    return out_feats


def _simulate_gibson_assembly(fragments: list[dict], *,
                                min_overlap: int = _GIBSON_MIN_OVERLAP_BP,
                                circular: bool = True,
                              ) -> dict:
    """Simulate a Gibson assembly of N linear top-strand fragments.

    Each ``fragments[i]`` dict shape:
        {
          "name":     str,
          "sequence": str,            # linear DNA 5' → 3'
          "features": list[dict],     # optional, in fragment-local coords
        }

    For each junction (consecutive pair plus the wrap junction when
    ``circular=True``) the longest exact-match suffix/prefix overlap
    is detected. Any junction below ``min_overlap`` bp fails the
    assembly. The product sequence has each overlap appearing once
    (the trailing copy is dropped: ``frag[i] + frag[i+1][overlap:]``).
    Features are shifted into product coordinates; features wholly
    inside a fragment's leading-overlap region are skipped (they're
    already represented by the preceding fragment's trailing copy).

    Returns ``{success, product_seq, circular, features, overlaps,
    errors, warnings}`` — see UI consumer (``GibsonAssemblyPane``)
    for the rendering convention. ``overlaps`` always has one entry
    per junction so the UI can show the full chain even on partial
    failure.

    Pre-refactor this was a 370-line monolith; the per-stage logic
    now lives in `_gibson_*` helpers (normalise → detect overlaps →
    validate body lengths → build product seq → shift features →
    re-merge wrap halves). Behaviour is bit-identical; tested by
    tests/test_gibson.py (47 cases).
    """
    norm_fragments, norm_err = _gibson_normalize_fragments(fragments)
    if norm_err is not None:
        return _gibson_failure(circular, [norm_err])
    assert norm_fragments is not None  # err None ⇒ list present

    # Reject zero-length fragments up front — they can't carry homology.
    for f in norm_fragments:
        if not f["sequence"]:
            return _gibson_failure(
                circular, [f"Fragment {f['name']!r} has no sequence."]
            )

    if len(norm_fragments) == 1 and not circular:
        # A single linear fragment doesn't need Gibson — pass through.
        f = norm_fragments[0]
        return {
            "success":     True,
            "product_seq": f["sequence"],
            "circular":    False,
            "features":    list(f["features"]),
            "overlaps":    [],
            "errors":      [],
            "warnings":    [
                "Single linear fragment — no Gibson junctions to "
                "validate. Product is the fragment as supplied.",
            ],
        }

    overlaps, overlap_lens, junction_errors = _gibson_detect_overlaps(
        norm_fragments, min_overlap=min_overlap, circular=circular,
    )
    if junction_errors:
        return _gibson_failure(circular, junction_errors, overlaps=overlaps)

    body_errors = _gibson_validate_body_lengths(
        norm_fragments, overlap_lens, circular=circular,
    )
    if body_errors:
        return _gibson_failure(circular, body_errors, overlaps=overlaps)

    warnings = _gibson_short_fragment_warnings(
        norm_fragments, min_overlap=min_overlap,
    )

    product_seq, offsets = _gibson_build_product(
        norm_fragments, overlap_lens, circular=circular,
    )
    product_len = len(product_seq)

    shifted = _gibson_shift_features(
        norm_fragments, overlap_lens, offsets, product_len,
        circular=circular,
    )
    out_feats = _gibson_merge_wrap_halves(
        shifted, product_len, circular=circular,
    )

    return {
        "success":     True,
        "product_seq": product_seq,
        "circular":    circular,
        "features":    out_feats,
        "overlaps":    overlaps,
        "errors":      [],
        "warnings":    warnings,
    }


def _gibson_record_from_result(result: dict, *, name: str) -> "SeqRecord | None":
    """Build a SeqRecord from a successful ``_simulate_gibson_assembly``
    result. Returns ``None`` when ``result["success"] is False``.

    Wrap features (``end < start``) become ``CompoundLocation`` per the
    GenBank wrap convention. Linear features land as ``FeatureLocation``.
    Strand defaults to +1 when missing.
    """
    if not result or not result.get("success"):
        return None
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord
    from Bio.SeqFeature import (
        SeqFeature, FeatureLocation, CompoundLocation,
    )
    seq_str = str(result.get("product_seq") or "")
    n = len(seq_str)
    safe_name = re.sub(r"[^A-Za-z0-9_]+", "_", name or "gibson") or "gibson"
    topology = "circular" if result.get("circular") else "linear"
    rec = SeqRecord(
        Seq(seq_str),
        id=safe_name[:16] or "gibson",
        name=safe_name[:16] or "gibson",
        description=name or "Gibson assembly",
        annotations={
            "molecule_type": "DNA",
            "topology":      topology,
        },
    )
    for f in (result.get("features") or []):
        if not isinstance(f, dict):
            continue
        try:
            s = int(f.get("start", 0))
            e = int(f.get("end",   0))
            strand = int(f.get("strand", 1) or 1)
        except (TypeError, ValueError):
            continue
        if s == e or n == 0:
            continue
        ftype = str(f.get("type") or "misc_feature")
        quals: dict = {}
        label = f.get("label")
        if label:
            quals["label"] = [str(label)]
        color = f.get("color")
        if color:
            quals["ApEinfo_fwdcolor"] = [str(color)]
            quals["ApEinfo_revcolor"] = [str(color)]
        note = f.get("note")
        if note:
            quals["note"] = [str(note)]
        # Re-emit the primer sequence so an inherited primer_bind renders
        # bound bases + 5' flap on the Gibson product (not a plain bar) —
        # `_record_features` carried it through the assembly.
        ps = f.get("primer_seq")
        if ps:
            quals["primer_seq"] = [_normalize_primer_seq(ps)]
        if e > s:
            loc = FeatureLocation(s, e, strand=strand)
        else:
            # Wrap feature: (s, n) + (0, e)
            loc = CompoundLocation([
                FeatureLocation(s, n, strand=strand),
                FeatureLocation(0, e, strand=strand),
            ])
        rec.features.append(SeqFeature(loc, type=ftype, qualifiers=quals))
    return rec
