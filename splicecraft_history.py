"""CommercialSaaS .dna history model + provenance date helpers (layer 2).

`_CommercialSaaSHistoryNode` (one node in the .dna <HistoryTree> lineage) plus
the pure history helpers it and the codec share: `_HISTORY_MONTHS`,
`_history_now_str` (machine storage stamp), `_history_human_dt` (the universal
slash-free "JUN 9 2026 14:30" render), and `_coerce_int_or_zero`.

Self-contained: ElementTree and datetime are imported function-locally; depends
on no other splicecraft module. The .dna codec FUNCTIONS that build and
serialise these trees stay in the hub (they couple to file I/O).
"""
from __future__ import annotations

import re


# Hardcoded (NOT `strftime('%b')`, which is locale-dependent) so the History
# tab's dates render the same "JUN" everywhere.
_HISTORY_MONTHS: tuple[str, ...] = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)


def _history_now_str() -> str:
    """Current LOCAL date+time as a compact, sortable storage stamp
    (``YYYY-MM-DDTHH:MM``) for a history node's ``date`` attribute. Stored
    machine-clean and rendered to the universal, slash-free "JUN 9 2026 14:30"
    by `_history_human_dt` (no MM/DD-vs-DD/MM ambiguity — user request
    2026-06-10). Stamped by the fresh-build callers, mirroring how
    `_build_commercialsaas_notes_packet` calls `datetime.now()` itself rather
    than baking non-determinism into the `_CommercialSaaSHistoryNode.new`
    constructor."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def _history_human_dt(stamp: str) -> str:
    """Render a stored history timestamp as the universal "JUN 9 2026 14:30"
    (uppercase 3-letter month, no leading-zero day, 24-hour clock). Accepts
    our ISO storage form AND the CommercialSaaS Notes ``YYYY.MM.DD`` date, so
    an imported file-level date renders too. Returns "" on empty/unparseable
    input — an undated node (reconstructed lineage / starting material whose
    real date is unknown) shows no date rather than a bogus one."""
    # `str(...)` + clamp: the `date` attr is always a string off XML, but a
    # hostile/garbage history_xml could carry an over-long or odd value, and a
    # direct caller might pass a non-string. The regex is start-anchored with
    # bounded quantifiers (no ReDoS); the clamp just caps the strip/scan cost.
    s = str(stamp or "").strip()[:40]
    if not s:
        return ""
    m = re.match(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})"
                 r"(?:[T ](\d{1,2}):(\d{2}))?", s)
    if not m:
        return ""
    year, mon, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mon <= 12 and 1 <= day <= 31):
        return ""
    out = f"{_HISTORY_MONTHS[mon - 1]} {day} {year}"
    if m.group(4) is not None:
        hh, mm = int(m.group(4)), int(m.group(5))
        if 0 <= hh <= 23 and 0 <= mm <= 59:   # drop a garbage time, keep date
            out += f" {hh:02d}:{mm:02d}"
    return out


class _CommercialSaaSHistoryNode:
    """One node in the CommercialSaaS `<HistoryTree>`.

    Wraps an `xml.etree.ElementTree.Element` so unknown attributes
    and child elements survive a parse → modify → serialise cycle.
    Typed properties expose the well-known fields; raw access is
    available via ``self.element`` for anything else.

    Construction modes:
      * ``_CommercialSaaSHistoryNode(element)`` — wrap an existing element
        (used by the parser).
      * ``_CommercialSaaSHistoryNode.new(name=..., seq_len=..., …)`` —
        create a fresh node with the canonical attributes set,
        ready to attach to a parent or use as the tree root.
    """

    __slots__ = ("element", "_sibling_elements")

    def __init__(self, element) -> None:
        self.element = element
        # 2026-05-27 (audit-3 M5): optional sibling top-level
        # `<Node>` elements preserved by `_parse_commercialsaas_history`
        # when the source file carried more than one. Empty list by
        # default; the serializer round-trips them back into the
        # output `<HistoryTree>`.
        self._sibling_elements: list = []

    @classmethod
    def new(cls, *, name: str, seq_len: int, circular: bool,
              operation: str, node_id: int = 0,
              strandedness: str = "double",
              date: str = "") -> "_CommercialSaaSHistoryNode":
        import xml.etree.ElementTree as _ET
        el = _ET.Element("Node")
        el.set("name", str(name))
        el.set("type", "DNA")
        el.set("seqLen", str(int(seq_len)))
        el.set("strandedness", str(strandedness))
        el.set("ID", str(int(node_id)))
        el.set("circular", "1" if circular else "0")
        el.set("operation", str(operation))
        # Optional creation timestamp (our ISO storage form, see
        # `_history_now_str`): fresh-build callers stamp it so the History tab
        # shows WHEN each step happened; reconstructed lineage / starting
        # material passes "" (real date unknown). Round-trips like any other
        # attr — and SnapGene/CommercialSaaS tolerates the extra attribute.
        if date:
            el.set("date", str(date))
        return cls(el)

    # ── Typed getters ────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self.element.get("name", "") or ""

    @property
    def operation(self) -> str:
        return self.element.get("operation", "") or ""

    @property
    def date(self) -> str:
        """Creation timestamp for this step — our ISO storage form, or a
        CommercialSaaS Notes-style ``YYYY.MM.DD`` date carried in on import.
        Empty for reconstructed lineage / starting material with no recorded
        date; `_history_human_dt` renders it to "JUN 9 2026 14:30"."""
        return self.element.get("date", "") or ""

    @property
    def seq_len(self) -> int:
        try:
            return int(self.element.get("seqLen", "0"))
        except (TypeError, ValueError):
            return 0

    @property
    def circular(self) -> bool:
        return self.element.get("circular") == "1"

    @property
    def node_id(self) -> int:
        try:
            return int(self.element.get("ID", "0"))
        except (TypeError, ValueError):
            return 0

    @property
    def resurrectable(self) -> bool:
        """CommercialSaaS marks parent nodes as ``resurrectable="1"`` when
        the original fragment can be re-extracted from the history
        (i.e., a downstream user could reconstruct the parent
        plasmid). Defaults to False for the top-level result node."""
        return self.element.get("resurrectable") == "1"

    @property
    def parents(self) -> "list[_CommercialSaaSHistoryNode]":
        """Direct parent fragments (one level down). Use
        :meth:`walk` for a full traversal."""
        return [_CommercialSaaSHistoryNode(c)
                for c in self.element.findall("Node")]

    @property
    def regenerated_sites(self) -> "list[dict]":
        """Restriction sites that the cloning operation preserved
        or recreated. Each entry is a plain dict ``{name, pos,
        siteCount}`` so callers don't have to wrap individual
        elements."""
        out: list[dict] = []
        for el in self.element.findall("RegeneratedSite"):
            out.append({
                "name":      el.get("name", ""),
                "pos":       _coerce_int_or_zero(el.get("pos")),
                "siteCount": _coerce_int_or_zero(el.get("siteCount")),
            })
        return out

    @property
    def input_summaries(self) -> "list[dict]":
        out: list[dict] = []
        for el in self.element.findall("InputSummary"):
            out.append({
                "manipulation": el.get("manipulation", ""),
                "name1":        el.get("name1", ""),
                "name2":        el.get("name2", ""),
                "val1":         _coerce_int_or_zero(el.get("val1")),
                "val2":         _coerce_int_or_zero(el.get("val2")),
                "siteCount1":   _coerce_int_or_zero(el.get("siteCount1")),
                "siteCount2":   _coerce_int_or_zero(el.get("siteCount2")),
            })
        return out

    @property
    def oligos(self) -> "list[dict]":
        """PCR primers recorded on an ``amplifyFragment`` history node.
        CommercialSaaS stores them as ``<Oligo name= sequence= …>``
        children — the amplify ``InputSummary`` itself carries no
        name1/name2 (only the val1/val2 amplified-region coordinates),
        which is why an un-parsed amplify step reads as a bare "amplify
        ? ↔ ?". Returns ``{name, sequence, description}`` per oligo so
        the detail pane can name the primers that made the product."""
        out: list[dict] = []
        for el in self.element.findall("Oligo"):
            out.append({
                "name":        el.get("name", "") or "",
                "sequence":    el.get("sequence", "") or "",
                "description": el.get("description", "") or "",
            })
        return out

    # ── Mutation ─────────────────────────────────────────────────

    def add_parent(self, parent: "_CommercialSaaSHistoryNode") -> None:
        """Attach ``parent`` as a child of this node. CommercialSaaS's
        convention: parent fragments hang off the result node so
        the tree reads "result → parents → grandparents …" as you
        descend. Caller is responsible for setting `parent.node_id`
        to a value unique within the tree."""
        self.element.append(parent.element)

    def add_regenerated_site(self, name: str, pos: int,
                              site_count: int = 1) -> None:
        import xml.etree.ElementTree as _ET
        el = _ET.SubElement(self.element, "RegeneratedSite")
        el.set("name", str(name))
        el.set("pos", str(int(pos)))
        el.set("siteCount", str(int(site_count)))

    def add_input_summary(self, *, manipulation: str,
                            name1: str = "", name2: str = "",
                            val1: int = 0, val2: int = 0,
                            site_count1: int = 1,
                            site_count2: int = 1) -> None:
        import xml.etree.ElementTree as _ET
        el = _ET.SubElement(self.element, "InputSummary")
        el.set("manipulation", str(manipulation))
        el.set("name1", str(name1))
        el.set("name2", str(name2))
        el.set("val1", str(int(val1)))
        el.set("val2", str(int(val2)))
        el.set("siteCount1", str(int(site_count1)))
        el.set("siteCount2", str(int(site_count2)))

    def add_oligo(self, *, name: str, sequence: str,
                   description: str = "") -> None:
        """Record a PCR primer on this node as an `<Oligo>` child —
        SnapGene/CommercialSaaS's representation. Lets SpliceCraft-
        generated PCR history carry the SAME primer detail a `.dna`
        import does (`oligos` reads them back; the History detail's
        Primers block renders them) — harmonised history regardless of
        whether the plasmid was imported or built de-novo in SpliceCraft
        (user request 2026-06-01)."""
        import xml.etree.ElementTree as _ET
        el = _ET.SubElement(self.element, "Oligo")
        el.set("name", str(name))
        el.set("sequence", str(sequence))
        if description:
            el.set("description", str(description))

    # ── Traversal ────────────────────────────────────────────────

    def walk(self):
        """Pre-order depth-first traversal: yields self, then each
        child node's traversal. Useful for "find every node with
        operation X" / "count parent fragments" / etc. Returns a
        generator of `_CommercialSaaSHistoryNode`.

        Iterative (stack-based) so a hostile `.dna` file with a 1000+
        deep nested `<Node><Node>...` history can't trip the CPython
        recursion limit. The total node count is still bounded by the
        LZMA-decompression cap (`_COMMERCIALSAAS_HISTORY_MAX_XML`).
        """
        stack: list = [self]
        while stack:
            node = stack.pop()
            yield node
            # Push children in reverse so the first child pops next,
            # preserving the original pre-order traversal sequence.
            stack.extend(reversed(node.parents))


def _coerce_int_or_zero(s) -> int:
    """Best-effort int coercion; falls back to 0. Used by history
    attribute getters where a malformed XML attribute shouldn't
    blow up the whole tree traversal."""
    try:
        return int(s) if s is not None else 0
    except (TypeError, ValueError):
        return 0
