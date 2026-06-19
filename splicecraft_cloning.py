"""splicecraft_cloning — construction simulation (Phase D, layer L3).

The "simulate the real steps" construction helpers ([INV-127]): build real
amplicons (`_simulate_primed_amplicon`), assemble real cloned plasmids via
digest+ligation (`_simulate_cloned_plasmid`), the pUPD2 backbone stub, overhang
fusion, and the Commercial-SaaS `.dna` history serialisation. Extracted so the
cloning modal/screen siblings can import them. Layer L3: imports biology(L0),
dataaccess(L1), history(L2), logging(L0); used by the modals (L4). Re-exported by
the hub so every call site resolves unchanged.
"""
from __future__ import annotations

import re

from splicecraft_biology import _rc
from splicecraft_dataaccess import (
    _BUILTIN_GRAMMARS, _GB_CODING_PART_TYPES, _GB_L0_ENZYME_SITE, _GB_PAD, _GB_SPACER,
)
from splicecraft_history import (
    _CommercialSaaSHistoryNode, _coerce_int_or_zero, _history_now_str,
)
from splicecraft_logging import _log


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
