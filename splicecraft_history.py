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

from rich.text import Text
from rich.table import Table

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


# ── History-viewer presentation: tree labels, protocol table, detail lines (Phase D)
_HISTORY_LABEL_NAME_MAX: int = 40


_HISTORY_DETAIL_LIST_MAX: int = 12


_HISTORY_PROTOCOL_INPUT_MAX: int = 8   # parts listed per step before "+N more"


_HISTORY_OP_FRIENDLY: "dict[str, str]" = {
    "insertFragment":  "assemble",
    "insert":          "insert",
    "replace":         "replace",
    "gibsonAssembly":  "Gibson",
    "amplifyFragment": "PCR",
    "editSequence":    "edit",
}


_HISTORY_OP_GLYPH: "dict[str, str]" = {
    "assemble": "⊕",
    "Gibson":   "⊕",
    "insert":   "⊕",
    "replace":  "⇄",
}


def _history_clean_name(name: str) -> str:
    """Strip the cosmetic ``.dna`` suffix history nodes carry (CommercialSaaS
    convention — see `_build_history_for_product`) for a cleaner row.
    Empty / whitespace name → ``(unnamed)`` so a row never collapses to
    blank. Does NOT escape — callers escape at render time."""
    n = (name or "").strip()
    if n[-4:].lower() == ".dna":
        n = n[:-4].strip()
    return n or "(unnamed)"


def _history_size_label(bp: int) -> str:
    """Compact length for a tree row: ``712 bp`` · ``13.9 kb`` ·
    ``1.20 Mb``. The exact base count still shows in the detail pane —
    the tree just wants something short."""
    try:
        b = int(bp)
    except (TypeError, ValueError):
        b = 0
    if b < 0:
        b = 0
    if b < 1_000:
        return f"{b} bp"
    if b < 1_000_000:
        return f"{b / 1_000:.1f} kb"
    return f"{b / 1_000_000:.2f} Mb"


_HISTORY_OP_SENTINELS: "frozenset[str]" = frozenset(
    {"invalid", "unknown", "none", "unspecified"})


def _history_op_label(op: str) -> str:
    """Friendly verb for an operation string; unknown ops pass through
    verbatim. Empty op — OR a CommercialSaaS sentinel like ``invalid``
    (its placeholder for a base/starting sequence with no recorded
    operation) — returns "" so the caller omits the tag instead of
    printing a literal "invalid"."""
    raw = (op or "").strip()
    if not raw or raw.lower() in _HISTORY_OP_SENTINELS:
        return ""
    return _HISTORY_OP_FRIENDLY.get(raw, raw)


def _history_node_signature(node: "_CommercialSaaSHistoryNode") -> str:
    """Identity key for collapsing repeated ancestor subtrees in the
    viewer. Two nodes with the same cleaned name + length + operation
    are treated as the same plasmid — repeats come from the same parent
    entry's inherited ``history_xml``, so the subtree IS identical.

    Purely cosmetic: a false match merely renders the 2nd occurrence as
    a ``↳ … (shown above)`` reference instead of redrawing it. It never
    touches stored history."""
    return (f"{_history_clean_name(node.name)}\x00{int(node.seq_len)}"
            f"\x00{(node.operation or '').strip()}")


def _history_tree_label(node: "_CommercialSaaSHistoryNode") -> str:
    """De-noised one-line tree label: ``name   size   ⊕ op`` (markup
    string; render via ``Text.from_markup``).

    The old format crammed name · N bp · circular · operation onto
    every row; the bp count + topology + raw op string repeated on all
    of them. Now the row leads with the (``.dna``-stripped) name + a
    compact size, drops "circular" (the common case — only "linear" is
    flagged), and shows a friendly op verb. Full bp / topology /
    strandedness move to the detail pane (`_history_detail_lines`).

    Names + ops are Rich-escaped (XML attrs can legally contain ``[``)
    and truncated so a hostile value can't push the column off-screen."""
    from rich.markup import escape as _esc
    raw = _history_clean_name(node.name)
    if len(raw) > _HISTORY_LABEL_NAME_MAX:
        raw = raw[: _HISTORY_LABEL_NAME_MAX - 1] + "…"
    name = _esc(raw)
    bits = [f"[b]{name}[/b]", f"[dim]{_history_size_label(node.seq_len)}[/dim]"]
    if not node.circular:
        bits.append("[dim]linear[/dim]")
    # Only nodes that actually combined inputs (have parents) carry an
    # operation tag — a raw starting material isn't the product of a
    # step, so tagging every leaf "⊕ assemble" was pure noise.
    op = _history_op_label(node.operation)
    if op and node.parents:
        glyph = _HISTORY_OP_GLYPH.get(op, "")
        tag = f"{glyph} {_esc(op)}".strip()
        bits.append(f"[cyan]{tag}[/cyan]")
    # Date+time of the step, in the universal slash-free "JUN 9 2026 14:30"
    # form, right alongside the action — undated reconstructed-lineage rows
    # just omit it (no bogus date).
    when = _history_human_dt(node.date)
    if when:
        bits.append(f"[dim]· {when}[/dim]")
    return "   ".join(bits)


def _history_reference_label(node: "_CommercialSaaSHistoryNode") -> str:
    """Compact label for the 2nd+ occurrence of a repeated ancestor —
    ``↳ name  size  (shown above)``. No operation tag (the canonical
    occurrence carries the detail); the marker tells the user the full
    subtree lives elsewhere in the tree rather than being drawn again."""
    from rich.markup import escape as _esc
    raw = _history_clean_name(node.name)
    if len(raw) > _HISTORY_LABEL_NAME_MAX:
        raw = raw[: _HISTORY_LABEL_NAME_MAX - 1] + "…"
    name = _esc(raw)
    return (f"[dim]↳[/dim] {name}   [dim]{_history_size_label(node.seq_len)}"
            f"[/dim]   [dim i](shown above)[/dim i]")


def _history_populate_tree(tree, root: "_CommercialSaaSHistoryNode",
                            node_by_id: dict) -> bool:
    """Fill a Textual ``Tree`` from a history root, de-noised:

      * **Progressive disclosure** — only the product (the root) opens
        by default; deeper generations start collapsed so a multi-step
        build doesn't dump its whole fractal on open.
      * **Dedup repeated ancestors** — a subtree seen before renders as
        a single ``↳ … (shown above)`` leaf, so a backbone reused
        across N branches is drawn once, not N times (the dominant
        noise on real GB/MoClo lineages).

    Iterative DFS with the shared depth + node caps (mirrors
    `_history_node_to_dict` / `_CommercialSaaSHistoryNode.walk` — a
    hostile deeply-nested ``.dna`` can't trip the recursion limit).
    Populates ``node_by_id`` ``{textual_node_id: hist_node}`` for the
    detail pane. Returns ``True`` if a cap truncated the render."""
    seen: "set[str]" = set()
    truncated = False
    n_seen = 0
    # Frame: (textual_parent_node, hist_node, depth). Pre-order; push
    # children reversed so the first parent pops next.
    stack: list = [(tree.root, root, 0)]
    while stack:
        parent_tnode, hist, depth = stack.pop()
        if n_seen >= _HISTORY_NODE_MAX_NODES or depth >= _HISTORY_NODE_MAX_DEPTH:
            truncated = True
            continue
        sig = _history_node_signature(hist)
        is_ref = sig in seen
        if is_ref:
            label = _history_reference_label(hist)
        else:
            seen.add(sig)
            label = _history_tree_label(hist)
        child = parent_tnode.add(Text.from_markup(label), expand=(depth == 0))
        node_by_id[child.id] = hist
        n_seen += 1
        if is_ref:
            # Reference occurrence — don't redraw the (identical) subtree.
            continue
        for p in reversed(hist.parents):
            stack.append((child, p, depth + 1))
    return truncated


def _history_step_from_node(node: "_CommercialSaaSHistoryNode") -> dict:
    """Distil one build STEP from a history node that has parents.
    ``backbone`` is the entry-vector / acceptor; ``inputs`` the parts
    that went into it; ``enzyme`` the Type IIS / RE that joined them.

    Backbone detection: our builders add the acceptor first AND record
    it as ``InputSummary.name1`` — prefer that match, else fall back to
    the first parent. Imported CommercialSaaS history that doesn't follow
    the convention degrades to "first parent is the backbone", which is
    cosmetic-only (the protocol line may swap which input is labelled
    the acceptor)."""
    parents = node.parents
    sites = node.regenerated_sites
    # Collect EVERY enzyme — a restriction digest uses ≥2, and the
    # builders record one RegeneratedSite per enzyme. Pre-fix this took
    # only sites[0], so a KpnI + XbaI double digest showed just "✂ KpnI"
    # in the protocol (user report 2026-06-01). Dedup, order-preserve,
    # and drop the "(reverse insert)" orientation sentinel.
    enzymes: "list[str]" = []
    _seen_enz: set = set()
    for _s in sites:
        _nm = str(_s.get("name") or "").strip()
        if not _nm or _nm.startswith("(") or _nm in _seen_enz:
            continue
        _seen_enz.add(_nm)
        enzymes.append(_nm)
    enzyme = enzymes[0] if enzymes else ""   # back-compat single-enzyme field
    summaries = node.input_summaries
    backbone_label = (_history_clean_name(summaries[0].get("name1") or "")
                      if summaries else "")
    backbone = ""
    inputs: "list[str]" = []
    if parents:
        names = [_history_clean_name(p.name) for p in parents]
        if len(names) == 1:
            inputs = names
        else:
            bi = 0
            if backbone_label:
                for i, nm in enumerate(names):
                    if nm == backbone_label:
                        bi = i
                        break
            backbone = names[bi]
            inputs = [nm for i, nm in enumerate(names) if i != bi]
    return {
        "product":  _history_clean_name(node.name),
        "op":       _history_op_label(node.operation),
        "enzyme":   enzyme,        # first enzyme (back-compat)
        "enzymes":  enzymes,       # ALL enzymes in the digest
        "backbone": backbone,
        "inputs":   inputs,
        "seq_len":  int(node.seq_len),
        "circular": bool(node.circular),
    }


def _history_build_steps(root: "_CommercialSaaSHistoryNode") -> "list[dict]":
    """Flatten a history tree into an ordered, de-duplicated list of
    build STEPS for the protocol view. A step is any node with parents
    (an assembly / cloning op); raw leaf inputs are not steps. A reused
    sub-assembly collapses to ONE step. Ordered earliest-first (deepest
    in the tree) → final product last, so it reads like a bench recipe.

    The protocol naturally sidesteps the combinatorial leaf-duplication
    that bloats the tree: duplication lives in the leaf ancestors, and
    leaves are never steps. Iterative + capped (hostile-XML safe)."""
    by_sig: "dict[str, dict]" = {}
    depth_of: "dict[str, int]" = {}
    order: "list[str]" = []
    stack: list = [(root, 0)]
    n_seen = 0
    while stack:
        node, depth = stack.pop()
        if n_seen >= _HISTORY_NODE_MAX_NODES or depth >= _HISTORY_NODE_MAX_DEPTH:
            break
        n_seen += 1
        parents = node.parents
        if parents:
            sig = _history_node_signature(node)
            # A reused sub-assembly sorts to its EARLIEST (deepest) use.
            depth_of[sig] = max(depth_of.get(sig, 0), depth)
            if sig not in by_sig:
                by_sig[sig] = _history_step_from_node(node)
                order.append(sig)
        # Push reversed so siblings pop in natural tree order — keeps
        # the protocol reading vector-then-parts, first-listed-first.
        for p in reversed(parents):
            stack.append((p, depth + 1))
    # Deepest first (earliest build) → product (depth 0) last. Stable on
    # insertion order for ties (siblings keep tree order).
    order.sort(key=lambda s: depth_of.get(s, 0), reverse=True)
    return [by_sig[s] for s in order]


def _history_protocol_step_cells(
        root: "_CommercialSaaSHistoryNode") -> "list[tuple[str, str]]":
    """``[(number, content_markup), …]`` — one pair per de-duplicated
    build step, earliest first. ``number`` is like ``"2."``; ``content``
    is the recipe body read left → right:

        <op>  <ingredients> into <backbone>  →  <product>   ✂ <enzymes>

    The forward arrow points AT the product (ingredients → result), the
    acceptor/vector is tagged ``into``, ``✂`` marks the enzymes, and an
    in-place edit (single input == product) collapses to one labelled
    product. A bare record with no steps returns a single
    ``("", <placeholder>)`` pair. Splitting the number from the content
    lets `_history_protocol_renderable`'s table hang-indent WRAPPED lines
    under the content rather than under the next step's number (user
    nitpick 2026-06-01)."""
    from rich.markup import escape as _esc
    steps = _history_build_steps(root)
    if not steps:
        return [("",
                 "[dim]Single record — no construction steps recorded.[/dim]")]
    cells: "list[tuple[str, str]]" = []
    for i, s in enumerate(steps, 1):
        product = _esc(s["product"])
        ins = s["inputs"]
        backbone = s["backbone"]
        verb = _esc(s["op"]) if s.get("op") else ""
        enzymes = s.get("enzymes") or ([s["enzyme"]] if s.get("enzyme") else [])
        enz_tag = (
            f"   [magenta]✂ {' + '.join(_esc(e) for e in enzymes)}[/magenta]"
            if enzymes else ""
        )
        # In-place edit: a single input that IS the product reads
        # redundantly as "X → X" — show one labelled product instead.
        if len(ins) == 1 and not backbone and ins[0] == s["product"]:
            cells.append(
                (f"{i}.",
                 f"[b]{product}[/b]  [dim]· edited[/dim]{enz_tag}"))
            continue
        # Ingredients = inputs (+ "into <backbone>" for the acceptor).
        chunks: "list[str]" = []
        if ins:
            shown = ins[:_HISTORY_PROTOCOL_INPUT_MAX]
            joined = " + ".join(_esc(x) for x in shown)
            extra = len(ins) - len(shown)
            if extra > 0:
                joined += f" [dim]+{extra} more[/dim]"
            chunks.append(joined)
        if backbone:
            lead = "[dim]into[/dim] " if chunks else ""
            chunks.append(f"{lead}[cyan]{_esc(backbone)}[/cyan]")
        prefix = f"[dim]{verb}[/dim]  " if verb else ""
        if chunks:
            content = (f"{prefix}{' '.join(chunks)}  [b]→[/b]  "
                       f"[b]{product}[/b]{enz_tag}")
        else:
            content = f"{prefix}[b]{product}[/b]{enz_tag}"
        cells.append((f"{i}.", content))
    return cells


_HISTORY_PROTOCOL_LEGEND = (
    "[dim]ingredients  [b]→[/b]  product"
    "       [cyan]into[/cyan] = acceptor / backbone"
    "       [magenta]✂[/magenta] = enzymes[/dim]"
)


def _history_protocol_renderable(root: "_CommercialSaaSHistoryNode"):
    """Rich renderable for a viewer's protocol pane: the symbol legend
    above a borderless 2-column table (right-justified step-number gutter
    | recipe content). The content column wraps WITHIN its own width, so
    a long step's wrapped tail hangs-indents under the content instead of
    dropping back under the next step's number (user nitpick 2026-06-01).
    Falls back to a bare placeholder Text when there are no steps."""
    from rich.table import Table
    from rich.text import Text
    from rich.console import Group
    cells = _history_protocol_step_cells(root)
    if len(cells) == 1 and cells[0][0] == "":
        return Text.from_markup(cells[0][1])          # placeholder, no legend
    gutter = max((len(num) for num, _ in cells), default=2)
    table = Table(show_header=False, box=None, pad_edge=False,
                   padding=(0, 1, 0, 0), expand=True)
    table.add_column(justify="right", no_wrap=True, width=gutter,
                      style="bold")
    table.add_column(ratio=1, overflow="fold")
    for num, content in cells:
        table.add_row(num, Text.from_markup(content))
    return Group(Text.from_markup(_HISTORY_PROTOCOL_LEGEND), Text(""), table)


def _history_detail_lines(hist: "_CommercialSaaSHistoryNode") -> "list[str]":
    """Full detail block for the selected history node — shared by both
    viewers so the modal and the full screen never drift. Every dynamic
    field is Rich-escaped and long lists are capped."""
    from rich.markup import escape as _esc
    name_disp = (f"[b]{_esc(_history_clean_name(hist.name))}[/b]"
                 if hist.name else "[dim](unnamed)[/]")
    op_raw = hist.operation
    op_friendly = _history_op_label(op_raw)
    if op_friendly:
        op_disp = (f"[cyan]{_esc(op_friendly)}[/]  "
                   f"[dim]({_esc(op_raw)})[/]")
    else:
        # Empty, or a CommercialSaaS sentinel ("invalid" / "unknown")
        # for a base / starting sequence with no recorded operation —
        # don't echo the literal sentinel.
        op_disp = "[dim](no operation recorded)[/]"
    strandedness = hist.element.get("strandedness") or "?"
    lines: "list[str]" = [name_disp, ""]
    lines.append("[b]Properties[/]")
    lines.append(f"  Length:        {hist.seq_len:,} bp")
    lines.append("  Topology:      "
                 f"{'circular' if hist.circular else 'linear'}")
    lines.append(f"  Strandedness:  {_esc(strandedness)}")
    lines.append(f"  Node ID:       {hist.node_id}")
    lines.append("")
    lines.append("[b]Operation[/]")
    lines.append(f"  {op_disp}")
    if hist.resurrectable:
        lines.append("  [green]✓ resurrectable[/] "
                     "[dim](parent can be re-extracted)[/]")
    when = _history_human_dt(hist.date)
    if when:
        lines.append("")
        lines.append("[b]Date[/]")
        lines.append(f"  {when}")
    sites = hist.regenerated_sites
    if sites:
        lines.append("")
        lines.append("[b]Regenerated sites[/]")
        shown = sites[:_HISTORY_DETAIL_LIST_MAX]
        joined = ", ".join(
            f"{_esc(str(s['name']) or '(unnamed)')}@{s['pos']}"
            for s in shown
        )
        if len(sites) > _HISTORY_DETAIL_LIST_MAX:
            joined += f", … (+{len(sites) - _HISTORY_DETAIL_LIST_MAX} more)"
        lines.append(f"  {joined}")
    sums = hist.input_summaries
    if sums:
        lines.append("")
        lines.append("[b]Inputs[/]")
        for sm in sums[:_HISTORY_DETAIL_LIST_MAX]:
            manip = _esc(str(sm.get('manipulation') or "(unknown)"))
            n1 = str(sm.get('name1') or "")
            n2 = str(sm.get('name2') or "")
            if n1 or n2:
                lines.append(
                    f"  {manip}  ({_esc(n1 or '?')} ↔ {_esc(n2 or '?')})")
            else:
                # No name pair — e.g. an `amplify` (PCR) step, which
                # records its detail as val1/val2 (the amplified region)
                # + <Oligo> primers (the Primers block below), not
                # name1/name2. Don't render a bare "? ↔ ?".
                v1 = _coerce_int_or_zero(sm.get('val1'))
                v2 = _coerce_int_or_zero(sm.get('val2'))
                region = (f"  [dim](region {v1:,}–{v2:,})[/dim]"
                          if (v1 or v2) else "")
                lines.append(f"  {manip}{region}")
        if len(sums) > _HISTORY_DETAIL_LIST_MAX:
            lines.append(
                f"  [dim]… (+{len(sums) - _HISTORY_DETAIL_LIST_MAX} more)[/]")
    # Primers — CommercialSaaS records PCR oligos as <Oligo> children on
    # an amplify node; surface name + sequence so a PCR step shows WHICH
    # primers made it (the detail was in the .dna all along) instead of a
    # bare "amplify".
    oligos = hist.oligos
    if oligos:
        lines.append("")
        lines.append("[b]Primers[/]")
        for o in oligos[:_HISTORY_DETAIL_LIST_MAX]:
            nm = _esc(o.get("name") or "(unnamed)")
            seq = o.get("sequence") or ""
            seq_disp = (_esc(seq[:40]) + ("…" if len(seq) > 40 else "")
                        if seq else "")
            lines.append(
                f"  {nm}" + (f"   [dim]{seq_disp}[/dim]" if seq_disp else ""))
        if len(oligos) > _HISTORY_DETAIL_LIST_MAX:
            lines.append(
                f"  [dim]… (+{len(oligos) - _HISTORY_DETAIL_LIST_MAX} more)[/]")
    parents = hist.parents
    lines.append("")
    if parents:
        lines.append(f"[b]Parents ({len(parents)})[/]")
        shown_p = parents[:_HISTORY_DETAIL_LIST_MAX]
        joined = ", ".join(
            _esc(_history_clean_name(p.name)) for p in shown_p
        )
        if len(parents) > _HISTORY_DETAIL_LIST_MAX:
            joined += f", … (+{len(parents) - _HISTORY_DETAIL_LIST_MAX} more)"
        lines.append(f"  {joined}")
    else:
        lines.append("[dim](leaf — no recorded parents)[/]")
    return lines


_HISTORY_NODE_MAX_DEPTH = 500


_HISTORY_NODE_MAX_NODES = 100_000
