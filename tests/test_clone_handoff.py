"""
test_clone_handoff — Synthesis "Clone Fragment" → cloning-workflow handoff.

Covers the reworked flow (2026-06-09):
  * Entry-vector acceptor validation (`_gb_text_acceptor_cut_count`,
    `_entry_vector_is_valid_acceptor`) — the guard that stops a clone
    silently degrading to the bare-fragment stub on a fresh install.
  * The two L0 deliverables are DISTINCT and full-length: the primed
    linear fragment and the cloned full plasmid are never the bare
    unprimed insert (the user-reported "same fragment 3×" regression).
  * `CloneMethodChooserModal` opens on Clone Fragment with NO premature
    library save; picking a grammar routes to the Domesticator prefilled
    byte-exact; an unbound grammar pops the entry-vector picker.
  * Gibson / Traditional route to the Constructor with the fragment
    pre-pasted on the matching tab.
  * The DNA + Protein Clear buttons exist and reset their editor.
"""
from __future__ import annotations

import random

import pytest

import splicecraft as sc

from Bio.Seq import Seq as _Seq
from Bio.SeqRecord import SeqRecord as _SeqRecord


_TERM = (200, 50)

# A small coding insert (ATG … stop), no internal Esp3I/BsaI.
_INSERT = ("ATGGCAAGCGGTGGTAGCGGTTCTGGTAGCGGTAGCGGTAGCGGTAGCGGTAGC"
           "AAAGAACTGAAAGCAGAACTGGAAGCACTGAAAGCAGAACTGGGTGGTAGC"
           "GATGAAGCAGCAAAAGCAGAAGCAGAAGCAAAAGCAGAGGCAGAAGCATAA")


def _scrub(s: str) -> str:
    for site in ("CGTCTC", "GAGACG", "GGTCTC", "GAGACC"):
        s = s.replace(site, "CTGCAG")
    return s


def _make_acceptor_gb(n_esp3i: int = 2) -> str:
    """Build a circular gb_text acceptor with exactly ``n_esp3i`` Esp3I
    (CGTCTC) sites flanking a dropout, in an otherwise Esp3I-free
    ~1.4 kb backbone. With ≥2 inward sites the IIS clone simulation
    produces a real plasmid; with <2 it must be rejected as an acceptor."""
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord
    rng = random.Random(0xBEEF)
    backbone = _scrub("".join(rng.choice("ACGT") for _ in range(1400)))
    dropout = _scrub("".join(rng.choice("ACGT") for _ in range(160)))
    if n_esp3i >= 2:
        # Two inward Esp3I sites (left CGTCTC, right GAGACG = CGTCTC on
        # the bottom strand) flanking the dropout — the canonical UPD
        # layout, releasing 4-nt overhangs.
        cassette = "CGTCTCA" + "CTCG" + dropout + "TGAG" + "AGAGACG"
    elif n_esp3i == 1:
        cassette = "CGTCTCA" + "CTCG" + dropout
    else:
        cassette = dropout
    seq = backbone[:700] + cassette + backbone[700:]
    rec = SeqRecord(Seq(seq), id="TESTUPD", name="TESTUPD",
                    description="synthetic L0 acceptor",
                    annotations={"molecule_type": "DNA", "topology": "circular"})
    return sc._record_to_gb_text(rec)


# ═══════════════════════════════════════════════════════════════════════════════
# Entry-vector acceptor validation (unit)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAcceptorValidation:
    def _g(self):
        return sc._BUILTIN_GRAMMARS["gb_l0"]

    def test_two_site_vector_is_valid_acceptor(self):
        gb = _make_acceptor_gb(2)
        n = sc._gb_text_acceptor_cut_count(gb, self._g())
        assert n >= 2, f"expected ≥2 Esp3I cuts, got {n}"
        assert sc._entry_vector_is_valid_acceptor({"gb_text": gb}, self._g())

    def test_one_site_vector_is_rejected(self):
        gb = _make_acceptor_gb(1)
        assert sc._gb_text_acceptor_cut_count(gb, self._g()) < 2
        assert not sc._entry_vector_is_valid_acceptor({"gb_text": gb}, self._g())

    def test_no_site_vector_is_rejected(self):
        gb = _make_acceptor_gb(0)
        assert sc._gb_text_acceptor_cut_count(gb, self._g()) == 0
        assert not sc._entry_vector_is_valid_acceptor({"gb_text": gb}, self._g())

    def test_empty_and_none_are_rejected(self):
        assert sc._gb_text_acceptor_cut_count("", self._g()) == 0
        assert not sc._entry_vector_is_valid_acceptor(None, self._g())
        assert not sc._entry_vector_is_valid_acceptor({}, self._g())

    def test_unknown_enzyme_grammar_is_rejected(self):
        gb = _make_acceptor_gb(2)
        assert sc._gb_text_acceptor_cut_count(gb, {"enzyme": "NotAnEnzyme"}) == 0
        assert sc._gb_text_acceptor_cut_count(gb, {}) == 0

    def test_garbage_gb_text_does_not_raise(self):
        # Never raises into the picker — returns 0 on unparseable input.
        assert sc._gb_text_acceptor_cut_count("not a genbank file", self._g()) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Two deliverables are distinct + full-length (unit)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCloneDeliverables:
    def _part_with_vector(self):
        g = sc._BUILTIN_GRAMMARS["gb_l0"]
        gb = _make_acceptor_gb(2)
        sc._set_entry_vector("gb_l0", {
            "name": "TESTUPD", "size": 0, "source": "test",
            "id": "TESTUPD", "gb_text": gb,
        })
        cds_type = next((p["type"] for p in g["positions"]
                         if p["type"] in (g.get("coding_types") or [])),
                        g["positions"][0]["type"])
        d = sc._design_gb_primers(_INSERT, 0, len(_INSERT), cds_type,
                                  codon_raw=None, grammar=g)
        assert not d.get("error"), d.get("error")
        part = {
            "name": "TCDS", "type": d["part_type"], "position": d["position"],
            "oh5": d["oh5"], "oh3": d["oh3"], "backbone": "TESTUPD", "marker": "—",
            "sequence": d["insert_seq"], "fwd_primer": d["fwd_full"],
            "rev_primer": d["rev_full"], "fwd_primer_name": "TCDS-DOM-1-F",
            "rev_primer_name": "TCDS-DOM-1-R", "fwd_tm": d["fwd_tm"],
            "rev_tm": d["rev_tm"], "grammar": "gb_l0",
        }
        return part, d["insert_seq"]

    def test_primed_fragment_is_not_the_bare_insert(self):
        part, insert = self._part_with_vector()
        fr = sc._part_to_primed_fragment_seqrecord(part, name="FRAG")
        frs = str(fr.seq).upper()
        assert frs != insert
        assert insert[6:-6] in frs            # full body preserved
        assert "CGTCTC" in frs or "GAGACG" in frs   # carries the enzyme site
        assert sum(1 for f in fr.features if f.type == "primer_bind") == 2
        assert fr.annotations.get("topology") == "linear"

    def test_cloned_plasmid_is_full_and_not_stub(self):
        part, insert = self._part_with_vector()
        cl = sc._part_to_cloned_seqrecord(part)
        cls = str(cl.seq).upper()
        stub = sc._simulate_cloned_plasmid(insert, part["oh5"], part["oh3"],
                                           part["type"])
        assert cls != stub, "clone degraded to the pUPD2 stub fallback"
        assert cl.annotations.get("topology") == "circular"
        assert len(cls) > len(insert) + 1000      # full plasmid, not truncated
        assert insert[6:-6] in cls                 # the part is intact

    def test_clone_fragment_and_insert_all_distinct(self):
        # The exact "saves the same unprimed fragment 3×" regression guard.
        part, insert = self._part_with_vector()
        fr = str(sc._part_to_primed_fragment_seqrecord(part, name="F").seq).upper()
        cl = str(sc._part_to_cloned_seqrecord(part).seq).upper()
        assert len({insert, fr, cl}) == 3

    def test_lifted_operon_genes_survive_into_clone(self):
        """A lifted operon's source genes (luxC/luxD/… stamped as
        `insert_feats`) must carry all the way into the FINAL CLONE, not
        just the primed fragment. Pre-fix the cloned plasmid collapsed a
        multi-gene operon into a single part-spanning block and silently
        dropped every gene (the fragment kept them, the clone didn't) —
        user-reported "luxC..luxG features gone after lift→clone"."""
        part, insert = self._part_with_vector()
        L = len(insert)
        # Two source genes in INSERT coordinates, anchored by sequence.
        src_feats = [
            {"start": 3,      "end": L // 2 - 3, "type": "CDS",
             "strand": 1, "label": "luxA"},
            {"start": L // 2, "end": L - 3,      "type": "CDS",
             "strand": 1, "label": "luxB"},
        ]
        assert sc._stamp_insert_feats_on_part(part, insert, src_feats) == 2

        def _labels(rec):
            return {(f.qualifiers.get("label") or [f.type])[0]
                    for f in rec.features}

        frag_labels = _labels(
            sc._part_to_primed_fragment_seqrecord(part, name="F"))
        clone_labels = _labels(sc._part_to_cloned_seqrecord(part))
        # The fragment already carried them; the clone must too (parity).
        assert {"luxA", "luxB"} <= frag_labels
        assert {"luxA", "luxB"} <= clone_labels, (
            f"clone dropped lifted genes; labels={sorted(clone_labels)}")

    def test_clone_without_insert_feats_is_unchanged(self):
        """No `insert_feats` on the part → the feature-carry step is a
        no-op (the helper refuses to invent annotations), so a plain
        clone still works and gains no phantom features."""
        part, _ = self._part_with_vector()
        assert "insert_feats" not in part
        cl = sc._part_to_cloned_seqrecord(part)
        # Only the part-spanning block + the two domestication primers.
        assert sc._seq_len(cl) > 0
        assert all(f.type != "CDS" for f in cl.features
                   if (f.qualifiers.get("label") or [""])[0]
                   in ("luxA", "luxB"))


class TestStampInsertFeatsAnchoring:
    """`_stamp_insert_feats_on_part` must survive the Domesticator's
    synonymous cures. `_design_gb_primers` returns a *mutated* insert
    (the curing rides on the primers), so a verbatim match against the
    source fails — pre-fix that silently stamped ZERO features and a
    lifted operon then cloned as one block instead of its genes."""

    def _src_and_feats(self, length=1000):
        import random
        rng = random.Random(7)
        src = "".join(rng.choice("ACGT") for _ in range(length))
        feats = [
            {"start": 3, "end": length // 2, "type": "CDS",
             "strand": 1, "label": "luxA"},
            {"start": length // 2, "end": length - 3, "type": "CDS",
             "strand": 1, "label": "luxB"},
        ]
        return src, feats

    def test_exact_insert_still_stamps(self):
        src, feats = self._src_and_feats()
        part = {"name": "op", "sequence": src}        # uncured = exact
        assert sc._stamp_insert_feats_on_part(part, src, feats) == 2

    def test_cured_insert_is_anchored_by_near_match(self):
        src, feats = self._src_and_feats()
        cured = list(src)
        for p in (120, 480, 830):                     # 3 cures, like 3 SOE junctions
            cured[p] = "A" if cured[p] != "A" else "G"
        cured = "".join(cured)
        assert src.count(cured) == 0                   # verbatim match fails
        part = {"name": "op", "sequence": cured}
        assert sc._stamp_insert_feats_on_part(part, src, feats) == 2
        # Anchored at offset 0 — coords unshifted.
        got = {f["label"]: (f["start"], f["end"])
               for f in part["insert_feats"]}
        assert got["luxA"] == (3, 500)

    def test_unrelated_lookalike_is_refused(self):
        # An insert that's ~heavily divergent must NOT be force-anchored
        # (catastrophic-class: never place a feature at a guessed coord).
        src, feats = self._src_and_feats(length=300)
        import random
        rng = random.Random(99)
        garbage = "".join(rng.choice("ACGT") for _ in range(300))
        part = {"name": "op", "sequence": garbage}
        assert sc._stamp_insert_feats_on_part(part, src, feats) == 0

    def _codon_optimized(self, length=450):
        # A ~33% rewritten copy of a random source — no clean sequence anchor
        # survives (like a heavily codon-optimized CDS).
        import random
        rng = random.Random(0xF15)
        src = "".join(rng.choice("ACGT") for _ in range(length))
        rec = list(src)
        for p in range(0, length, 3):
            rec[p] = next(c for c in "ACGT" if c != rec[p])
        return src, "".join(rec)

    def test_recoded_offset_trusts_byte_identical_template(self):
        src, insert = self._codon_optimized()
        assert sc._best_insert_offset(src, insert) is None        # no anchor
        # Identical template + region → KNOWN offset (whole-seq and sub-region).
        assert sc._recoded_insert_offset(src, (0, 450), src, insert) == 0
        assert sc._recoded_insert_offset(
            src, (50, 200), src, insert[50:200]) == 50

    def test_recoded_offset_refuses_without_solid_provenance(self):
        src, insert = self._codon_optimized()
        # No marker, a different template, a length-changing transform, and a
        # wrapping region all refuse — never a guessed coordinate.
        assert sc._recoded_insert_offset("", (0, 450), src, insert) is None
        assert sc._recoded_insert_offset(
            "ACGT" * 113, (0, 450), src, insert) is None
        assert sc._recoded_insert_offset(src, (0, 450), src, insert[:200]) is None
        assert sc._recoded_insert_offset(src, (300, 50), src, insert) is None

    def test_stamp_codon_optimized_part_via_provenance(self):
        # End-to-end: a codon-optimized part with design provenance keeps its
        # genes even though NO sequence anchor exists; the transient markers
        # are consumed, never persisted.
        src, insert = self._codon_optimized()
        part = {"sequence": insert,
                "_recoded_src": src, "_recoded_region": (0, 450)}
        feats = [{"start": 0, "end": 150, "type": "CDS", "label": "g1",
                  "strand": 1},
                 {"start": 150, "end": 300, "type": "CDS", "label": "g2",
                  "strand": -1}]
        assert sc._stamp_insert_feats_on_part(part, src, feats) == 2
        got = {x["label"]: x for x in part["insert_feats"]}
        assert set(got) == {"g1", "g2"}
        assert got["g2"]["strand"] == -1                  # strand preserved
        assert "_recoded_src" not in part                 # consumed, not saved
        assert "_recoded_region" not in part

    def test_stamp_codon_optimized_without_marker_still_refused(self):
        # The catastrophic-class guard holds: the same heavily-rewritten insert
        # WITHOUT a provenance marker is refused (the fallback only trusts a
        # byte-identical recorded template).
        src, insert = self._codon_optimized()
        part = {"sequence": insert}                        # no marker
        feats = [{"start": 0, "end": 150, "type": "CDS", "label": "x"}]
        assert sc._stamp_insert_feats_on_part(part, src, feats) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Clone Fragment handoff routing (async / Pilot)
# ═══════════════════════════════════════════════════════════════════════════════

def _bind_test_vector():
    sc._set_entry_vector("gb_l0", {
        "name": "TESTUPD", "size": 0, "source": "test",
        "id": "TESTUPD", "gb_text": _make_acceptor_gb(2),
    })


async def _open_synthesis_with_seq(app, pilot, seq):
    for _ in range(6):
        await pilot.pause()
    while len(app.screen_stack) > 1:
        app.pop_screen()
        for _ in range(2):
            await pilot.pause()
    app.action_open_synthesis()
    for _ in range(6):
        await pilot.pause()
    ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
    ed._seq = seq
    return app.screen


class TestCloneFragmentHandoff:
    @pytest.mark.asyncio
    async def test_chooser_opens_with_no_premature_save(self):
        _bind_test_vector()
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            syn = await _open_synthesis_with_seq(app, pilot, _INSERT)
            before = len(sc._load_library())
            syn.action_clone_fragment()
            for _ in range(5):
                await pilot.pause()
            assert type(app.screen).__name__ == "CloneMethodChooserModal"
            assert len(sc._load_library()) == before   # NOTHING saved yet

    @pytest.mark.asyncio
    async def test_grammar_choice_routes_to_domesticator_byte_exact(self):
        _bind_test_vector()
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            syn = await _open_synthesis_with_seq(app, pilot, _INSERT)
            before = len(sc._load_library())
            syn.action_clone_fragment()
            for _ in range(5):
                await pilot.pause()
            app.screen.dismiss({"method": "grammar", "grammar_id": "gb_l0"})
            for _ in range(8):
                await pilot.pause()
            stack = [type(s).__name__ for s in app.screen_stack]
            assert "PartsBinModal" in stack
            assert type(app.screen).__name__ == "DomesticatorModal"
            ta = app.screen.query_one("#dom-direct-seq", sc.TextArea)
            assert ta.text.upper() == _INSERT      # byte-exact, no missed bases
            assert len(sc._load_library()) == before   # still no premature save

    @pytest.mark.asyncio
    async def test_unbound_grammar_pops_entry_vector_picker(self):
        sc._set_entry_vector("gb_l0", None)         # fresh-install: no vector
        # ...but a plasmid in the library to pick an acceptor from.
        sc._save_library([{
            "id": "acc1", "name": "acceptor", "gb_text": _make_acceptor_gb(2),
            "size": 0, "n_feats": 0, "source": "test", "added": "2026-06-09",
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            syn = await _open_synthesis_with_seq(app, pilot, _INSERT)
            syn.action_clone_fragment()
            for _ in range(5):
                await pilot.pause()
            app.screen.dismiss({"method": "grammar", "grammar_id": "gb_l0"})
            for _ in range(6):
                await pilot.pause()
            assert type(app.screen).__name__ == "PlasmidPickerModal"

    @pytest.mark.asyncio
    async def test_empty_library_no_vector_guides_user(self):
        # Fresh install: no entry vector AND no plasmids → don't dead-end on
        # an empty picker; stay in Synthesis (the user is told to fetch an
        # acceptor first) and write nothing.
        sc._set_entry_vector("gb_l0", None)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            syn = await _open_synthesis_with_seq(app, pilot, _INSERT)
            assert sc._load_library() == []
            syn.action_clone_fragment()
            for _ in range(5):
                await pilot.pause()
            app.screen.dismiss({"method": "grammar", "grammar_id": "gb_l0"})
            for _ in range(6):
                await pilot.pause()
            assert type(app.screen).__name__ == "SynthesisScreen"
            assert sc._load_library() == []


class TestConstructorSeed:
    @pytest.mark.asyncio
    async def test_gibson_route_prefills_paste_box(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app.push_screen(sc.ConstructorModal(
                seed_seq=_INSERT, seed_name="F1", seed_method="gibson"))
            for _ in range(10):
                await pilot.pause()
            tabs = app.screen.query_one("#ctor-tabs", sc.TabbedContent)
            assert tabs.active == "ctor-tab-gibson"
            assert app.screen.query_one("#gib-pcr-seq", sc.TextArea).text.upper() == _INSERT

    @pytest.mark.asyncio
    async def test_traditional_route_prefills_paste_box(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app.push_screen(sc.ConstructorModal(
                seed_seq=_INSERT, seed_name="F1", seed_method="traditional"))
            for _ in range(10):
                await pilot.pause()
            tabs = app.screen.query_one("#ctor-tabs", sc.TabbedContent)
            assert tabs.active == "ctor-tab-traditional"
            assert app.screen.query_one("#trad-pcr-seq", sc.TextArea).text.upper() == _INSERT


def _clone_region_plasmid():
    """A varied circular plasmid so cloning-primer Tm lands near 60."""
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord
    b = "ACGT"
    seq = "".join(b[(i * 7 + (i * i) // 11 + i // 3) % 4] for i in range(600))
    rec = SeqRecord(Seq(seq), id="CloneRegionTest",
                    name="Clone Region Test",
                    annotations={"molecule_type": "DNA",
                                 "topology": "circular"})
    return rec, seq


async def _load_clone_region_plasmid(app, pilot):
    rec, seq = _clone_region_plasmid()
    for _ in range(6):
        await pilot.pause()
    while len(app.screen_stack) > 1:
        app.pop_screen()
        for _ in range(2):
            await pilot.pause()
    app._apply_record(rec)
    for _ in range(6):
        await pilot.pause()
    return rec, seq


class TestCloneRegion:
    """One-click "Clone selected region" (File ▸ Clone selected region):
    an arbitrary seq-panel highlight is PCR-tailed with two restriction
    sites and dropped into the Constructor's Traditional tab as a fully-
    configured donor."""

    def test_designs_tailed_amplicon_binding_region(self):
        _, seq = _clone_region_plasmid()
        d = sc._design_cloning_primers(seq, 100, 400, "EcoRI", "BamHI")
        assert not d.get("error"), d
        # Catastrophic-class: the forward primer's 3' binding IS the
        # region's 5' end (a cloning primer must anneal where it claims).
        assert d["fwd_binding"] == d["insert_seq"][:len(d["fwd_binding"])]
        assert d["fwd_full"].startswith("GCGC" + d["site_5"])
        amplicon = ("GCGC" + d["site_5"] + d["insert_seq"]
                    + d["site_3"] + sc._rc("GCGC"))
        # The added enzyme sites must be present so the later digest can
        # release the insert.
        assert "GAATTC" in amplicon and "GGATCC" in amplicon

    @pytest.mark.asyncio
    async def test_flow_seeds_constructor_with_configured_donor(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (100, 400)          # mark arbitrary DNA
            app.action_clone_region()
            for _ in range(6):
                await pilot.pause()
            assert isinstance(app.screen, sc.CloneRegionEnzymeModal)
            app.screen.dismiss({"enz5": "EcoRI", "enz3": "BamHI"})
            for _ in range(16):
                await pilot.pause()
            assert isinstance(app.screen, sc.ConstructorModal)
            pane = app.screen.query_one(sc.TraditionalCloningPane)
            donors = [s for s in pane._lane_inserts
                      if s.get("mode") == "pcr"]
            assert donors, "no PCR donor seeded into the Traditional lane"
            assert donors[0]["enz_left"] == "EcoRI"
            assert donors[0]["enz_right"] == "BamHI"
            assert "GAATTC" in donors[0]["pcr_seq"]
            saved = {p.get("name") for p in sc._load_primers()}
            assert any(n and n.endswith("-F") for n in saved)
            assert any(n and n.endswith("-R") for n in saved)

    @pytest.mark.asyncio
    async def test_no_selection_warns_no_modal(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = None
            sp._sel_range = None
            app.action_clone_region()
            for _ in range(4):
                await pilot.pause()
            assert not isinstance(app.screen, sc.CloneRegionEnzymeModal)

    @pytest.mark.asyncio
    async def test_short_region_warns_no_modal(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (100, 110)          # 10 bp < 18
            app.action_clone_region()
            for _ in range(4):
                await pilot.pause()
            assert not isinstance(app.screen, sc.CloneRegionEnzymeModal)

    # ── Hardening (adversarial review F1/F2/F3): catastrophic-class refusals ──

    async def _clone_refused(self, app, pilot, sel, enz5, enz3):
        sp = app.query_one("#seq-panel", sc.SequencePanel)
        sp._user_sel = sel
        sp._sel_range = None
        app.action_clone_region()
        for _ in range(6):
            await pilot.pause()
        if not isinstance(app.screen, sc.CloneRegionEnzymeModal):
            return True    # gated before the modal — also a refusal
        app.screen.dismiss({"enz5": enz5, "enz3": enz3})
        for _ in range(10):
            await pilot.pause()
        return not isinstance(app.screen, sc.ConstructorModal)

    def test_type_iis_detector(self):
        assert sc._enzyme_is_type_iis("BsaI")
        assert sc._enzyme_is_type_iis("BsmBI")
        assert not sc._enzyme_is_type_iis("EcoRI")
        assert not sc._enzyme_is_type_iis("BamHI")

    @pytest.mark.asyncio
    async def test_type_iis_enzyme_refused(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            assert await self._clone_refused(app, pilot, (100, 400),
                                             "BsaI", "BamHI")

    @pytest.mark.asyncio
    async def test_short_region_overlapping_primers_refused(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            assert await self._clone_refused(app, pilot, (100, 118),
                                             "HindIII", "SalI")

    @pytest.mark.asyncio
    async def test_internal_recognition_site_refused(self):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        b = "ACGT"
        sl = list("".join(b[(i * 7 + i // 3) % 4] for i in range(600)))
        sl[200:206] = list("GAATTC")            # EcoRI site INSIDE [100,400)
        rec = SeqRecord(Seq("".join(sl)), id="Int", name="Int",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app._apply_record(rec)
            for _ in range(6):
                await pilot.pause()
            assert await self._clone_refused(app, pilot, (100, 400),
                                             "EcoRI", "BamHI")

    @pytest.mark.asyncio
    async def test_region_features_carry_into_donor(self):
        """The cloned region's own features must ride into the Traditional PCR
        donor (and thence the product) — not vanish into a featureless insert."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        b = "ACGT"
        seq = "".join(b[(i * 7 + i // 3) % 4] for i in range(600))
        rec = SeqRecord(Seq(seq), id="R", name="R",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features.append(SeqFeature(FeatureLocation(150, 250, strand=1),
                            type="CDS", qualifiers={"label": ["TU-CDS"]}))
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app._apply_record(rec)
            for _ in range(6):
                await pilot.pause()
            app.query_one("#seq-panel", sc.SequencePanel)._user_sel = (100, 400)
            app.action_clone_region()
            for _ in range(6):
                await pilot.pause()
            assert isinstance(app.screen, sc.CloneRegionEnzymeModal)
            app.screen.dismiss({"enz5": "EcoRI", "enz3": "BamHI"})
            for _ in range(16):
                await pilot.pause()
            pane = app.screen.query_one(sc.TraditionalCloningPane)
            donors = [s for s in pane._lane_inserts if s.get("mode") == "pcr"]
            assert donors, "no PCR donor seeded"
            pf = donors[0].get("pcr_features") or []
            assert any(f.get("label") == "TU-CDS" for f in pf), \
                f"region feature not carried into the donor: {pf}"

    def test_clone_region_shortcut_bound(self):
        """Alt+Shift+P opens the selection→cloning-pipeline hub, without
        clobbering the existing Alt+Shift+C (Capture → Feature library)
        binding."""
        binds = {(b.key, b.action) for b in sc.PlasmidApp.BINDINGS
                 if hasattr(b, "key")}
        assert ("alt+shift+p", "send_selection_to_pipeline") in binds
        assert ("alt+shift+c", "capture_to_features") in binds

    def test_clone_region_modal_prefills_pcr_name(self):
        """The enzyme modal carries a "PCR-…" amplicon-name default (mirrors
        Synthesis's "FRAG-…"); the bare default is just "PCR-"."""
        m = sc.CloneRegionEnzymeModal(default_name="PCR-pUC19 100-400")
        assert m._default_name == "PCR-pUC19 100-400"
        assert sc.CloneRegionEnzymeModal()._default_name == "PCR-"

    def test_gather_region_feats_carries_all_spanning(self):
        """ALL features overlapping the region — fully-inside AND partially-
        spanning either edge — ride into the clone, clipped to region-local
        coords. Pseudo overlays (site/recut/source) + non-overlapping features
        are skipped."""
        feats = [
            {"type": "CDS",  "label": "inside",     "start": 120, "end": 180, "strand": 1},
            {"type": "CDS",  "label": "span_left",  "start": 50,  "end": 150, "strand": 1},
            {"type": "CDS",  "label": "span_right", "start": 180, "end": 260, "strand": 1},
            {"type": "CDS",  "label": "outside",    "start": 300, "end": 400, "strand": 1},
            {"type": "site", "label": "EcoRI",      "start": 130, "end": 136, "strand": 0},
        ]
        out = sc.PlasmidApp._gather_region_feats(feats, 100, 200)
        labels = {f["label"] for f in out}
        assert {"inside", "span_left", "span_right"} <= labels
        assert "outside" not in labels and "EcoRI" not in labels
        sl = next(f for f in out if f["label"] == "span_left")
        assert sl["start"] == 0 and sl["end"] == 50      # 50..150 clipped to region

    @pytest.mark.asyncio
    async def test_custom_amplicon_name_flows_to_donor(self):
        """The PCR-name textbox value rides through to the seeded donor row so
        the user can name the amplicon they're about to clone + save."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (100, 400)
            app.action_clone_region()
            for _ in range(6):
                await pilot.pause()
            assert isinstance(app.screen, sc.CloneRegionEnzymeModal)
            app.screen.dismiss({"enz5": "EcoRI", "enz3": "BamHI",
                                "name": "PCR-myInsert"})
            for _ in range(16):
                await pilot.pause()
            pane = app.screen.query_one(sc.TraditionalCloningPane)
            donors = [s for s in pane._lane_inserts if s.get("mode") == "pcr"]
            assert donors, "no PCR donor seeded"
            assert donors[0]["pcr_name"] == "PCR-myInsert"

    @pytest.mark.asyncio
    async def test_modal_name_sanitized_and_capped(self):
        """The modal strips control bytes from a pasted name and caps its
        length before dismissing — a hostile/giant name can't bloat the donor
        row, primer names, or the toast."""
        from textual.widgets import Input as _Input
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(4):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            captured: dict = {}
            app.push_screen(sc.CloneRegionEnzymeModal(default_name="PCR-x"),
                            lambda r: captured.update(r=r))
            for _ in range(4):
                await pilot.pause()
            modal = app.screen
            assert isinstance(modal, sc.CloneRegionEnzymeModal)
            modal.query_one("#cre-name", _Input).value = (
                "PCR-\x00\x07ab\x1f" + "Z" * 80)
            modal._submit()
            for _ in range(6):
                await pilot.pause()
            nm = captured["r"]["name"]
            assert all(c not in nm for c in "\x00\x07\x1f")
            assert nm.startswith("PCR-ab")          # control bytes excised
            assert len(nm) <= 64                     # capped

    @pytest.mark.asyncio
    async def test_clone_build_blank_name_falls_back_to_auto(self):
        """A name that's only whitespace / control bytes (e.g. handed in via
        the agent API) collapses to the auto "<plasmid> <start>-<end>" label
        rather than naming the donor an empty/garbage string."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (100, 400)
            app.action_clone_region()
            for _ in range(6):
                await pilot.pause()
            assert isinstance(app.screen, sc.CloneRegionEnzymeModal)
            app.screen.dismiss({"enz5": "EcoRI", "enz3": "BamHI",
                                "name": "  \x00\x07  "})
            for _ in range(16):
                await pilot.pause()
            pane = app.screen.query_one(sc.TraditionalCloningPane)
            donors = [s for s in pane._lane_inserts if s.get("mode") == "pcr"]
            assert donors
            nm = donors[0]["pcr_name"]
            assert all(c not in nm for c in "\x00\x07")
            assert "101-400" in nm                   # auto label (start+1..end)

    # ── Wrap-aware carryover + amplicon library save (origin-wrap regression) ──

    def test_gather_region_feats_wrap_selection(self):
        """An ORIGIN-SPANNING selection (start > end) carries ALL its features,
        tiled into one [0, span) amplicon coordinate — the confirmed regression
        was `_gather_region_feats` returning [] for any wrap selection, so every
        insert annotation was silently dropped while the vector's stayed."""
        total = 2000
        feats = [{"type": "promoter",   "label": "Pdemo",   "start": 1700, "end": 1900, "strand": 1},
                 {"type": "CDS",        "label": "GeneX",  "start": 1900, "end": 2000, "strand": 1},
                 {"type": "terminator", "label": "Tdemo", "start": 0,    "end": 300,  "strand": 1},
                 {"type": "CDS",        "label": "OUTSIDE",  "start": 600,  "end": 800,  "strand": 1}]
        g = sc.PlasmidApp._gather_region_feats(feats, 1700, 300, total)
        got = [(f["label"], f["start"], f["end"]) for f in g]
        assert got == [("Pdemo", 0, 200), ("GeneX", 200, 300),
                       ("Tdemo", 300, 600)], got    # OUTSIDE excluded

    def test_gather_region_feats_wrap_without_total_is_legacy_empty(self):
        """3-arg callers (no `total`) keep the old behaviour: a wrap selection
        still returns [] — the new wrap handling is strictly opt-in via `total`,
        so nothing that passed the old contract changes."""
        feats = [{"type": "CDS", "label": "X", "start": 1700, "end": 1900, "strand": 1}]
        assert sc.PlasmidApp._gather_region_feats(feats, 1700, 300) == []

    def test_gather_region_feats_wrap_feature_split(self):
        """A feature that itself wraps the origin, in a NON-wrap selection that
        contains both its arcs but not the origin, is genuinely two disjoint
        pieces (the origin sits OUTSIDE the selection) — carried as two arcs."""
        total = 2000
        feats = [{"type": "CDS", "label": "WF", "start": 1980, "end": 40, "strand": 1}]
        g = sc.PlasmidApp._gather_region_feats(feats, 10, 1990, total)
        labels = [f["label"] for f in g]
        assert labels.count("WF") == 2                  # both arcs carried
        assert all(0 <= f["start"] <= f["end"] <= 1980 for f in g)

    def test_gather_region_feats_wrap_feature_in_wrap_selection_merges(self):
        """A feature that itself wraps the origin AND sits fully inside a wrap
        selection is rebased as ONE contiguous piece (not two seam-split bars),
        and keeps its frame qualifiers — the rebase is a rigid translation, so
        `codon_start` / `transl_table` stay valid (regardless of strand)."""
        total = 2000
        feats = [{"type": "CDS", "label": "WrapCDS", "start": 1950, "end": 100,
                  "strand": -1, "codon_start": 2, "transl_table": 11}]
        g = sc.PlasmidApp._gather_region_feats(feats, 1700, 300, total)
        assert len(g) == 1, g                            # merged, not split
        (m,) = g
        assert (m["start"], m["end"]) == (250, 400)      # [fs-s, (total-s)+fe)
        assert m["strand"] == -1
        assert m["codon_start"] == 2 and m["transl_table"] == 11
        # A CLIPPED origin-wrapping feature stays conservative (no frame hint).
        clip = [{"type": "CDS", "label": "Clip", "start": 1950, "end": 300,
                 "strand": 1, "codon_start": 1}]
        gc = sc.PlasmidApp._gather_region_feats(clip, 1700, 200, total)
        assert all("codon_start" not in f for f in gc)

    def test_build_clone_region_amplicon_entry(self):
        """The amplicon library entry (issue 2/3): kind=amplicon, carries the
        region features AND both run primers as primer_bind features, with a
        construction-history XML."""
        _, seq = _clone_region_plasmid()
        d = sc._design_cloning_primers(seq, 100, 400, "EcoRI", "BamHI")
        assert not d.get("error"), d
        amplicon = ("GCGC" + d["site_5"] + d["insert_seq"]
                    + d["site_3"] + sc._rc("GCGC"))
        lead = 4 + len(d["site_5"])
        region_feats = [{"type": "CDS", "label": "TU", "color": "yellow",
                         "strand": 1, "start": lead, "end": lead + 60}]
        entry = sc._build_clone_region_amplicon_entry(
            amplicon, region_feats, name="PCR-myTU 100-400",
            fwd_full=d["fwd_full"], rev_full=d["rev_full"],
            fwd_tm=d.get("fwd_tm"), rev_tm=d.get("rev_tm"),
            fwd_name="PCR-myTU 100-400-F", rev_name="PCR-myTU 100-400-R",
            start_1based=101, end_1based=400)
        assert entry["kind"] == "amplicon"
        assert entry["name"] == "PCR-myTU 100-400"
        assert entry.get("history_xml")
        rec = sc._gb_text_to_record(entry["gb_text"])
        labelled = [(f.qualifiers.get("label", [""])[0], f.type)
                    for f in rec.features]
        assert ("TU", "CDS") in labelled                       # region feature
        pbinds = [lab for lab, t in labelled if t == "primer_bind"]
        assert len(pbinds) == 2, labelled                      # both run primers

    @pytest.mark.asyncio
    async def test_wrap_selection_flow_carries_features_and_saves_amplicon(self):
        """End-to-end: an origin-spanning selection seeds a donor that DOES
        carry its features + primers, and the named amplicon lands in the
        library as a kind=amplicon entry with its primer_bind features."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        b = "ACGT"
        seq = "".join(b[(i * 7 + i // 3) % 4] for i in range(600))
        rec = SeqRecord(Seq(seq), id="W", name="W",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        # Features placed so a [500, 100) selection WRAPS the origin.
        rec.features.append(SeqFeature(FeatureLocation(500, 600, strand=1),
                            type="promoter", qualifiers={"label": ["Pdemo"]}))
        rec.features.append(SeqFeature(FeatureLocation(0, 100, strand=1),
                            type="CDS", qualifiers={"label": ["GeneX"]}))
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app._apply_record(rec)
            for _ in range(6):
                await pilot.pause()
            app.query_one("#seq-panel", sc.SequencePanel)._user_sel = (500, 100)
            app.action_clone_region()
            for _ in range(6):
                await pilot.pause()
            assert isinstance(app.screen, sc.CloneRegionEnzymeModal)
            app.screen.dismiss({"enz5": "EcoRI", "enz3": "BamHI",
                                "name": "PCR-wrapTU"})
            for _ in range(18):
                await pilot.pause()
            pane = app.screen.query_one(sc.TraditionalCloningPane)
            donor = next(s for s in pane._lane_inserts if s.get("mode") == "pcr")
            pf = {f.get("label") for f in donor.get("pcr_features") or []}
            assert {"Pdemo", "GeneX"} <= pf, f"wrap features dropped: {pf}"
            assert donor.get("pcr_primers", {}).get("fwd_seq")   # primers threaded
            # The named amplicon is now a real library entry…
            amp = next((e for e in sc._iter_library_readonly()
                        if e.get("name") == "PCR-wrapTU"), None)
            assert amp is not None, "amplicon not saved to the library"
            assert sc._entry_kind(amp) == "amplicon"
            ar = sc._gb_text_to_record(amp["gb_text"])
            atypes = [f.type for f in ar.features]
            assert atypes.count("primer_bind") == 2          # …with its primers
            assert {(f.qualifiers.get("label", [""])[0]) for f in ar.features} \
                >= {"Pdemo", "GeneX"}                        # …and its features


def _clean_seq(n: int, bad: "list[str]", seed: int) -> str:
    """Random ACGT of length n with none of the `bad` motifs (site-free filler)."""
    r = random.Random(seed)
    s = "".join(r.choice("ACGT") for _ in range(n))
    while any(b in s for b in bad):
        s = "".join(r.choice("ACGT") for _ in range(n))
    return s


class TestCloneRegionEnzymePicker:
    """Phases 1-3: the cut-site picker classifies enzymes for the region,
    suggests a viable pair (insert-safe + vector-compatible), and can pre-seed
    the destination vector as the Constructor backbone."""

    _BAD = ["GGATCC", "AAGCTT", "GAATTC", "GTCGAC", "GAGCTC", "CTCGAG",
            "GCGGCCGC"]   # BamHI/HindIII/EcoRI/SalI/SacI/XhoI/NotI

    def test_classify_in_insert_safe_type_iis(self):
        ins = "ACGT" * 5 + "GAATTC" + "ACGT" * 10      # EcoRI site inside
        cls = sc._classify_cloning_enzymes(ins)
        assert cls["EcoRI"] == "in_insert"
        assert cls["BamHI"] == "safe"
        assert cls["BsaI"] == "type_iis"

    def test_suggest_pair_avoids_in_region_enzyme(self):
        ins = "ACGT" * 5 + "GAATTC" + "ACGT" * 10
        pair = sc._suggest_cloning_pair(ins)
        assert pair and "EcoRI" not in pair and pair[0] != pair[1]

    def test_suggest_pair_vector_aware(self):
        ins = "ACGTTGCAACGTTGCAACGT" * 3              # no curated sites
        vec = (_clean_seq(800, self._BAD, 1) + "GGATCC" + _clean_seq(20, self._BAD, 2)
               + "AAGCTT" + _clean_seq(800, self._BAD, 3))   # only BamHI + HindIII
        pair = sc._suggest_cloning_pair(ins, vec)
        assert pair and set(pair) == {"BamHI", "HindIII"}, pair

    def test_suggest_pair_none_when_no_safe_enzyme(self):
        # A region carrying EVERY curated recognition site (N→A so degenerate
        # sites still match) → no insert-safe enzyme → no suggestion.
        sites = [sc._NEB_ENZYMES[n][0].replace("N", "A")
                 for n in sc._CLONING_RE_NAMES]
        ins = "ACGTACGT".join(sites)
        assert sc._suggest_cloning_pair(ins) is None

    def test_options_annotated_and_usable_first(self):
        ins = "ACGT" * 5 + "GAATTC" + "ACGT" * 10
        opts = sc._cloning_enzyme_options(ins)
        lab = {v: l for l, v in opts}
        assert "✗" in lab["EcoRI"]                     # site inside selection
        assert "Type IIS" in lab["BsaI"]               # can't add-cut-sites
        vals = [v for _l, v in opts]
        assert vals.index("BamHI") < vals.index("EcoRI")   # usable sorts first

    def test_options_vector_marks_absent_enzyme(self):
        ins = "ACGTTGCAACGTTGCAACGT" * 3
        vec = (_clean_seq(800, self._BAD, 4) + "GGATCC" + _clean_seq(20, self._BAD, 5)
               + "AAGCTT" + _clean_seq(800, self._BAD, 6))   # no XhoI
        lab = {v: l for l, v in sc._cloning_enzyme_options(ins, vec)}
        assert "⚠ not in vector" in lab["XhoI"]
        assert "⚠" not in lab["BamHI"]                 # present in the vector

    def test_pair_hint_suggests_then_silent(self):
        ins = "ACGT" * 5 + "GAATTC" + "ACGT" * 10
        vec = (_clean_seq(800, self._BAD, 7) + "GGATCC" + _clean_seq(20, self._BAD, 8)
               + "AAGCTT" + _clean_seq(800, self._BAD, 9))
        hint = sc.TraditionalCloningPane._pair_hint(ins, vec)
        assert "Try" in hint and ("BamHI" in hint and "HindIII" in hint)
        assert sc.TraditionalCloningPane._pair_hint("", vec) == ""   # no insert

    @pytest.mark.asyncio
    async def test_modal_defaults_to_safe_pair_and_marks(self):
        # A region with an EcoRI site → the picker must default OFF EcoRI.
        body = _clean_seq(800, [b for b in self._BAD if b != "GAATTC"], 10)
        seq = body[:300] + "GAATTC" + body[306:]
        rec = _SeqRecord(_Seq(seq), id="ES", name="ES",
                         annotations={"molecule_type": "DNA", "topology": "circular"})
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app._apply_record(rec)
            for _ in range(6):
                await pilot.pause()
            app.query_one("#seq-panel", sc.SequencePanel)._user_sel = (200, 500)
            app.action_clone_region()
            for _ in range(8):
                await pilot.pause()
            m = app.screen
            assert isinstance(m, sc.CloneRegionEnzymeModal)
            from textual.widgets import Select as _Select
            v5 = m.query_one("#cre-enz5", _Select).value
            v3 = m.query_one("#cre-enz3", _Select).value
            assert v5 != "EcoRI" and v3 != "EcoRI" and v5 != v3

    @pytest.mark.asyncio
    async def test_picked_vector_seeds_backbone_into_constructor(self):
        vec = (_clean_seq(1500, self._BAD, 11) + "GGATCC" + _clean_seq(20, self._BAD, 12)
               + "AAGCTT" + _clean_seq(1500, self._BAD, 13))   # BamHI + HindIII
        vrec = _SeqRecord(_Seq(vec), id="DV", name="DV",
                          annotations={"molecule_type": "DNA", "topology": "circular"})
        sc._commit_library_entry_to_collection(
            {"id": "DV", "name": "Dest Vector", "size": len(vec), "kind": "plasmid",
             "source": "import", "added": "2026-06-10",
             "gb_text": sc._record_to_gb_text(vrec)},
            sc._get_active_collection_name() or "Default")
        src = _SeqRecord(_Seq(_clean_seq(800, self._BAD, 14)), id="SS", name="SS",
                         annotations={"molecule_type": "DNA", "topology": "circular"})
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app._apply_record(src)
            for _ in range(6):
                await pilot.pause()
            app.query_one("#seq-panel", sc.SequencePanel)._user_sel = (100, 500)
            app.action_clone_region()
            for _ in range(8):
                await pilot.pause()
            m = app.screen
            assert isinstance(m, sc.CloneRegionEnzymeModal)
            assert m._vector_choices, "vector did not reach the modal picker"
            vid = m._vector_choices[0][1]
            from textual.widgets import Select as _Select
            m.query_one("#cre-vector", _Select).value = vid
            for _ in range(6):
                await pilot.pause()
            m._submit()
            for _ in range(20):
                await pilot.pause()
            assert isinstance(app.screen, sc.ConstructorModal)
            pane = app.screen.query_one(sc.TraditionalCloningPane)
            assert any(s.get("mode") == "pcr" for s in pane._lane_inserts)
            assert any(s.get("role") == "backbone" and s.get("source_entry_id") == vid
                       for s in pane._lane_inserts)


def _plasmid_with_feat():
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord
    from Bio.SeqFeature import SeqFeature, FeatureLocation
    b = "ACGT"
    seq = "".join(b[(i * 7 + i // 3) % 4] for i in range(600))
    rec = SeqRecord(Seq(seq), id="FeatP", name="Feat P",
                    annotations={"molecule_type": "DNA",
                                 "topology": "circular"})
    rec.features.append(SeqFeature(FeatureLocation(120, 200, strand=1),
                        type="misc_feature", qualifiers={"label": ["MyFeat"]}))
    return rec, seq


class TestFeatureRichCopy:
    """Copying a selection stashes its features (rebased) on the app, and a
    matching paste into the Synthesis editor carries them in."""

    def test_gather_region_feats_rebases_and_clips(self):
        feats = [{"start": 120, "end": 200, "type": "misc_feature",
                  "label": "In", "color": "cyan", "strand": 1},
                 {"start": 250, "end": 350, "type": "gene", "label": "Stradl"},
                 {"start": 50, "end": 90, "type": "CDS", "label": "Out"},
                 {"start": 5, "end": 5, "type": "x"}]
        g = sc.PlasmidApp._gather_region_feats(feats, 100, 300)
        assert any(f["label"] == "In" and f["start"] == 20 and f["end"] == 100
                   for f in g)
        assert any(f["label"] == "Stradl" and f["start"] == 150
                   and f["end"] == 200 for f in g)        # clipped to span
        assert not any(f["label"] == "Out" for f in g)    # outside the span

    def test_render_keys_carried_only_when_contained(self):
        # Fully-contained CDS keeps codon_start / transl_table (reading
        # frame); a CLIPPED CDS drops them — its codon_start is relative to
        # the original off-selection start, so it'd mis-frame after rebasing
        # (adversarial review F4).
        feats = [{"start": 120, "end": 200, "type": "CDS", "label": "Cont",
                  "codon_start": 2, "transl_table": 11},
                 {"start": 50, "end": 150, "type": "CDS", "label": "Clip",
                  "codon_start": 3}]
        g = sc.PlasmidApp._gather_region_feats(feats, 100, 300)
        cont = next(f for f in g if f["label"] == "Cont")
        clip = next(f for f in g if f["label"] == "Clip")
        assert cont.get("codon_start") == 2 and cont.get("transl_table") == 11
        assert "codon_start" not in clip

    @pytest.mark.asyncio
    async def test_copy_then_synthesis_paste_carries_features(self):
        from textual.events import Paste
        rec, seq = _plasmid_with_feat()
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app._apply_record(rec)
            for _ in range(6):
                await pilot.pause()
            app.query_one("#seq-panel", sc.SequencePanel)._user_sel = (100, 300)
            app.action_copy_selection()
            for _ in range(4):
                await pilot.pause()
            cr = getattr(app, "_copied_region", None)
            assert cr and cr["seq"] == seq[100:300]
            assert any(f["label"] == "MyFeat" and f["start"] == 20
                       and f["end"] == 100 for f in cr["feats"])
            app.action_open_synthesis()
            for _ in range(10):
                await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.focus()
            for _ in range(2):
                await pilot.pause()
            try:
                ev = Paste(cr["seq"])
            except TypeError:
                ev = Paste(text=cr["seq"])
            ed.on_paste(ev)
            for _ in range(6):
                await pilot.pause()
            assert len(ed._seq) == 200
            assert any(f.get("label") == "MyFeat" and f.get("start") == 20
                       and f.get("end") == 100 for f in ed._feats)

    @pytest.mark.asyncio
    async def test_bottom_strand_copy_carries_no_features(self):
        rec, _ = _plasmid_with_feat()
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app._apply_record(rec)
            for _ in range(6):
                await pilot.pause()
            app.query_one("#seq-panel", sc.SequencePanel)._user_sel = (100, 300)
            app.action_copy_selection_bottom()
            for _ in range(4):
                await pilot.pause()
            assert getattr(app, "_copied_region", "x") is None

    @pytest.mark.asyncio
    async def test_wrap_selection_copy_carries_features(self):
        """A top-strand copy of an ORIGIN-SPANNING selection carries its
        features too (copy is now wrap-aware, mirroring the clone path) — the
        bases were already joined wrap-aware, the features used to be dropped."""
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        b = "ACGT"
        seq = "".join(b[(i * 7 + i // 3) % 4] for i in range(600))
        rec = SeqRecord(Seq(seq), id="WC", name="WC",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec.features.append(SeqFeature(FeatureLocation(550, 600, strand=1),
                            type="CDS", qualifiers={"label": ["Tail"]}))
        rec.features.append(SeqFeature(FeatureLocation(0, 50, strand=1),
                            type="CDS", qualifiers={"label": ["Head"]}))
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app._apply_record(rec)
            for _ in range(6):
                await pilot.pause()
            app.query_one("#seq-panel", sc.SequencePanel)._user_sel = (500, 100)
            app.action_copy_selection()
            for _ in range(4):
                await pilot.pause()
            cr = getattr(app, "_copied_region", None)
            assert cr and cr["seq"] == (seq[500:] + seq[:100]).upper()
            labels = {f["label"] for f in cr["feats"]}
            assert {"Tail", "Head"} <= labels, labels


class TestSynthesisClearButtons:
    @pytest.mark.asyncio
    async def test_dna_clear_empties_editor(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            syn = await _open_synthesis_with_seq(app, pilot, "ATGAAACGTTAA")
            syn.query_one("#btn-syn-dna-clear", sc.Button).press()
            for _ in range(4):
                await pilot.pause()
            assert syn.query_one("#syn-editor", sc.SynthesisEditor).get_state()[0] == ""

    @pytest.mark.asyncio
    async def test_protein_tab_has_clear_button(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            for _ in range(6):
                await pilot.pause()
            while len(app.screen_stack) > 1:
                app.pop_screen()
                for _ in range(2):
                    await pilot.pause()
            app.action_open_synthesis()
            for _ in range(6):
                await pilot.pause()
            app.screen.query_one("#syn-tabs", sc.TabbedContent).active = "syn-tab-protein"
            for _ in range(5):
                await pilot.pause()
            assert app.screen.query_one("#btn-syn-protein-clear", sc.Button) is not None


class TestSelectionPipelineHub:
    """Alt+Shift+P selection→cloning-pipeline hub: a chooser routes the
    highlighted region to Traditional / Golden Braid / MoClo / Gibson, with
    its features carried through (Phase 0 Gibson seed_amplicon + Phase 1 hub)."""

    @pytest.mark.asyncio
    async def test_hub_opens_method_chooser(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (100, 400)
            app.action_send_selection_to_pipeline()
            for _ in range(6):
                await pilot.pause()
            assert isinstance(app.screen, sc.CloneMethodChooserModal)

    @pytest.mark.asyncio
    async def test_hub_traditional_routes_to_enzyme_picker(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._user_sel = (100, 400)
            app._dispatch_selection_to_pipeline(
                {"method": "traditional", "grammar_id": ""},
                100, 400, app._record_load_counter)
            for _ in range(6):
                await pilot.pause()
            assert isinstance(app.screen, sc.CloneRegionEnzymeModal)

    @pytest.mark.asyncio
    async def test_hub_gibson_carries_features_to_lane(self):
        """Gibson branch drops the region into the Gibson lane as a fragment
        WITH its CDS features (the Phase 0 seed_amplicon path) — region-local
        rebasing keeps an in-range CDS, drops RE overlays."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            pm._feats = [
                {"start": 150, "end": 300, "strand": 1,
                 "type": "CDS", "label": "demoCDS"},
                {"start": 120, "end": 126, "strand": 1,
                 "type": "resite", "label": "EcoRI"},      # overlay → dropped
            ]
            app._dispatch_selection_to_pipeline(
                {"method": "gibson", "grammar_id": ""},
                100, 400, app._record_load_counter)
            for _ in range(10):
                await pilot.pause()
            assert isinstance(app.screen, sc.ConstructorModal)
            pane = app.screen.query_one("#ctor-gib-pane", sc.GibsonAssemblyPane)
            assert pane._lane, "region not seeded into the Gibson lane"
            labels = {f.get("label") for f in pane._lane[-1]["features"]}
            assert "demoCDS" in labels
            assert "EcoRI" not in labels

    @pytest.mark.asyncio
    async def test_hub_record_swap_aborts_dispatch(self):
        """A canvas swap (stale entry_counter) while the chooser blocked must
        abort — never pair the coords with the wrong record."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await _load_clone_region_plasmid(app, pilot)
            app._dispatch_selection_to_pipeline(
                {"method": "gibson", "grammar_id": ""},
                100, 400, app._record_load_counter - 1)   # simulate a swap
            for _ in range(4):
                await pilot.pause()
            assert not isinstance(app.screen, sc.ConstructorModal)


class TestCloneNamingAndRowCarry:
    """Guards for the parts-bin → Save-to-Collection clone path:
      * the clone keeps the part's HUMAN (spaced) display name, not the
        underscored GenBank LOCUS (the "still producing underscores" bug);
      * the clone carries the part's `insert_feats` (the genes), which the
        parts-bin table-row projection (`_all_rows`) MUST include — dropping
        it there silently fused a lifted operon into one block."""

    def _vector(self):
        sc._set_entry_vector("gb_l0", {
            "name": "TESTUPD", "size": 0, "source": "test",
            "id": "TESTUPD", "gb_text": _make_acceptor_gb(2),
        })

    def _part(self, name="My Operon (domesticated) COPY 3"):
        self._vector()
        g = sc._BUILTIN_GRAMMARS["gb_l0"]
        ct = next((p["type"] for p in g["positions"]
                   if p["type"] in (g.get("coding_types") or [])),
                  g["positions"][0]["type"])
        d = sc._design_gb_primers(_INSERT, 0, len(_INSERT), ct,
                                  codon_raw=None, grammar=g)
        L = len(d["insert_seq"])
        part = {
            "name": name, "type": d["part_type"], "position": d["position"],
            "oh5": d["oh5"], "oh3": d["oh3"], "sequence": d["insert_seq"],
            "fwd_primer": d["fwd_full"], "rev_primer": d["rev_full"],
            "fwd_tm": d["fwd_tm"], "rev_tm": d["rev_tm"], "grammar": "gb_l0",
            "insert_feats": [
                {"start": 3, "end": L // 2 - 3, "type": "CDS",
                 "strand": 1, "label": "geneA"},
                {"start": L // 2, "end": L - 3, "type": "CDS",
                 "strand": 1, "label": "geneB"},
            ],
        }
        return part

    def test_clone_keeps_spaced_display_name(self):
        cl = sc._part_to_cloned_seqrecord(self._part())
        # The SeqRecord LOCUS is sanitised (underscores), but the display
        # name the library/canvas show must be the original spaced name.
        assert "_" in cl.name              # LOCUS is sanitised
        assert getattr(cl, "_tui_display_name", None) == \
            "My Operon (domesticated) COPY 3"

    def test_clone_carries_insert_feats_genes(self):
        cl = sc._part_to_cloned_seqrecord(self._part())
        labels = {(f.qualifiers.get("label") or [f.type])[0]
                  for f in cl.features}
        assert {"geneA", "geneB"} <= labels

    def test_row_projection_includes_insert_feats(self):
        # The exact regression: a part saved with insert_feats must surface
        # them in the parts-bin row projection, or the row-driven clone
        # drops the genes. Drives the real `_all_rows` via the bin file.
        part = self._part(name="RowOperon")
        sc._save_parts_bin([part])
        screen = sc.PartsBinModal()
        screen._active_level = 0          # L0 tab
        rows = screen._all_rows()
        match = next((r for r in rows if r.get("name") == "RowOperon"), None)
        assert match is not None
        assert match.get("insert_feats"), \
            "row projection dropped insert_feats — genes will fuse on clone"
        assert len(match["insert_feats"]) == 2

    def test_clone_carries_all_run_primers(self):
        # Every primer that built the plasmid — the flank pair AND the
        # internal SOE/cure primers — must land on the clone, not just the
        # two flanks. Internal primers bind the cured insert body.
        part = self._part(name="OperonAllPrimers")
        ins = part["sequence"]
        part["run_primers"] = [
            {"name": "DOM-1-F", "seq": "GCGCCGTCTCA" + ins[:25], "strand": 1},
            {"name": "DOM-1-R", "seq": "GCGCCGTCTCA" + sc._rc(ins[-25:]),
             "strand": -1},
            {"name": "DOM-2-F", "seq": ins[30:55], "strand": 1},   # internal
            {"name": "DOM-2-R", "seq": sc._rc(ins[30:55]), "strand": -1},
        ]
        cl = sc._part_to_cloned_seqrecord(part)
        names = {(f.qualifiers.get("label") or [""])[0]
                 for f in cl.features if f.type == "primer_bind"}
        assert {"DOM-1-F", "DOM-1-R", "DOM-2-F", "DOM-2-R"} <= names, names

    def test_row_projection_includes_run_primers(self):
        part = self._part(name="RowRunPrimers")
        part["run_primers"] = [{"name": "p1", "seq": "ACGT" * 6, "strand": 1}]
        sc._save_parts_bin([part])
        screen = sc.PartsBinModal()
        screen._active_level = 0
        rows = screen._all_rows()
        match = next((r for r in rows if r.get("name") == "RowRunPrimers"), None)
        assert match is not None
        assert match.get("run_primers"), \
            "row projection dropped run_primers — only flanks reach the clone"


# ═══════════════════════════════════════════════════════════════════════════════
# domesticate-part agent endpoint (P0-1 + P1-5)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDomesticatePartEndpoint:
    """P0-1 / P1-5 (agent-API feedback): the `domesticate-part` agent endpoint
    runs the REAL Synthesis-tab L0 clone (digest the entry vector + ligate
    the insert) and saves the L0 plasmid with construction lineage — and
    FAILS LOUD (422) instead of silently stubbing a pUPD2 backbone when no
    compatible entry vector is configured."""

    def _configure_vector_and_design(self):
        g = sc._BUILTIN_GRAMMARS["gb_l0"]
        sc._set_entry_vector("gb_l0", {
            "name": "TESTUPD", "size": 0, "source": "test",
            "id": "TESTUPD", "gb_text": _make_acceptor_gb(2),
        })
        cds_type = next((p["type"] for p in g["positions"]
                         if p["type"] in (g.get("coding_types") or [])),
                        g["positions"][0]["type"])
        d = sc._design_gb_primers(_INSERT, 0, len(_INSERT), cds_type,
                                  codon_raw=None, grammar=g)
        assert not d.get("error"), d.get("error")
        return d

    def test_clones_saves_and_attaches_lineage(self):
        d = self._configure_vector_and_design()
        r = sc._h_domesticate_part(None, {
            "sequence": d["insert_seq"], "oh5": d["oh5"], "oh3": d["oh3"],
            "name": "AgentTCDS", "grammar": "gb_l0", "type": d["part_type"],
            "fwd_primer": d["fwd_full"], "rev_primer": d["rev_full"]})
        assert isinstance(r, dict) and r["ok"], r
        ent = next(e for e in sc._load_library() if e["name"] == "AgentTCDS")
        assert ent["source"] == "agent:domesticate"
        rec = sc._gb_text_to_record(ent["gb_text"])
        assert rec.annotations.get("topology") == "circular"
        cls = str(rec.seq).upper()
        # REAL clone, not the pUPD2 stub: full plasmid carrying the insert.
        stub = sc._simulate_cloned_plasmid(d["insert_seq"], d["oh5"], d["oh3"],
                                           d["part_type"]).upper()
        assert cls != stub, "domesticate-part degraded to the stub backbone"
        assert d["insert_seq"][6:-6] in cls
        assert len(cls) > len(d["insert_seq"]) + 1000
        # L0 lineage attached: insertFragment root (not a flat createDocument).
        h = sc._h_get_history(None, {"name": "AgentTCDS"})
        assert h["history"] is not None
        assert h["history"]["operation"] == "insertFragment"

    def test_two_tier_vector_nests_from_category_overhangs(self):
        # Two-tier hardening (2026-07-13): call domesticate-part with the part's
        # CATEGORY overhangs (Promoter GGAG/AATG) and NO primers into a UPD
        # acceptor whose EXTERNAL overhangs are CTCG/TGAG. The endpoint resolves
        # the external pair from the vector and nests, so the clone succeeds
        # with the category overhang PRESERVED inside the external one
        # (…CTCG·GGAG·insert…) — not a one-tier amplicon that finds no dropout.
        sc._set_entry_vector("gb_l0", {
            "name": "TESTUPD", "size": 0, "source": "test",
            "id": "TESTUPD", "gb_text": _make_acceptor_gb(2)})   # CTCG/TGAG
        r = sc._h_domesticate_part(None, {
            "sequence": _INSERT, "oh5": "GGAG", "oh3": "AATG",
            "name": "TwoTierProm", "grammar": "gb_l0", "type": "Promoter"})
        assert isinstance(r, dict) and r["ok"], r
        ent = next(e for e in sc._load_library() if e["name"] == "TwoTierProm")
        cls = str(sc._gb_text_to_record(ent["gb_text"]).seq).upper()
        doubled = cls + cls   # rotation-invariant substring search (circular)
        # external CTCG wraps the category GGAG, which precedes the insert body
        assert "CTCGGGAG" in doubled, "external entry overhang not nested"
        assert ("GGAG" + _INSERT[:20]) in doubled, \
            "category overhang not preserved before the insert"

    def test_no_entry_vector_fails_loud(self):
        sc._set_entry_vector("gb_l0", None)            # fresh-install: no vector
        r = sc._h_domesticate_part(None, {
            "sequence": _INSERT, "oh5": "GGAG", "oh3": "AATG",
            "name": "NoVec", "grammar": "gb_l0"})
        assert isinstance(r, tuple) and r[1] == 422, r
        assert "entry vector" in r[0]["error"].lower()
        # No stub leaked into the library.
        assert not any(e.get("name") == "NoVec" for e in sc._load_library())

    def test_allow_stub_false_raises_when_tiers_fail(self):
        # A configured-but-USELESS vector (poly-A: no Esp3I site to cut, and
        # none of the part's overhangs to splice against) makes both real-
        # clone tiers fail — so allow_stub=False must RAISE (the agent path)
        # while allow_stub=True (the GUI default) still returns the
        # unbreakable stub record.
        sc._set_entry_vector("gb_l0", {
            "name": "EMPTY", "id": "EMPTY", "size": 0, "source": "test",
            "gb_text": sc._record_to_gb_text(_SeqRecord(
                _Seq("A" * 1500), id="EMPTY", name="EMPTY",
                annotations={"molecule_type": "DNA", "topology": "circular"})),
        })
        bad = {"name": "X", "sequence": _INSERT,
               "oh5": "GGAG", "oh3": "AATG", "grammar": "gb_l0"}
        with pytest.raises(ValueError):
            sc._part_to_cloned_seqrecord(bad, allow_stub=False)
        assert sc._part_to_cloned_seqrecord(bad, allow_stub=True) is not None

    def test_validation(self):
        # sequence / oh5 / oh3 / name / grammar are all required up front,
        # each before the entry-vector check (no vector setup needed).
        assert sc._h_domesticate_part(
            None, {"oh5": "GGAG", "oh3": "AATG", "name": "x"})[1] == 400
        assert sc._h_domesticate_part(
            None, {"sequence": _INSERT, "oh3": "AATG", "name": "x"})[1] == 400
        assert sc._h_domesticate_part(
            None, {"sequence": _INSERT, "oh5": "GGAG", "oh3": "AATG"})[1] == 400
        assert sc._h_domesticate_part(
            None, {"sequence": _INSERT, "oh5": "GGAG", "oh3": "AATG",
                   "name": "x", "grammar": "nope"})[1] == 400
        # Absurd overhang length is rejected (≤50 nt).
        assert sc._h_domesticate_part(
            None, {"sequence": _INSERT, "oh5": "A" * 51, "oh3": "AATG",
                   "name": "x"})[1] == 400

    def test_whitespace_collection_is_clean_400(self):
        # A whitespace-only `collection` is a clean 400 "invalid 'collection'"
        # — not a confusing 409 naming None as a collection (code-review).
        d = self._configure_vector_and_design()
        r = sc._h_domesticate_part(None, {
            "sequence": d["insert_seq"], "oh5": d["oh5"], "oh3": d["oh3"],
            "name": "x", "grammar": "gb_l0", "collection": "   "})
        assert isinstance(r, tuple) and r[1] == 400, r

    def test_distinct_long_names_get_distinct_ids(self):
        # Two distinct names sharing a 32-char prefix must NOT collide on the
        # canonical id (the endpoint inserts directly, bypassing add_entry's
        # id-dedup, so a truncated-id collision would make load-by-id return
        # the wrong plasmid) — code-review CONFIRMED bug.
        d = self._configure_vector_and_design()
        long_a = "AlphaConstruct_VeryLongDescriptiveName_A"  # share 32 prefix
        long_b = "AlphaConstruct_VeryLongDescriptiveName_B"
        r1 = sc._h_domesticate_part(None, {
            "sequence": d["insert_seq"], "oh5": d["oh5"], "oh3": d["oh3"],
            "name": long_a, "grammar": "gb_l0", "type": d["part_type"]})
        r2 = sc._h_domesticate_part(None, {
            "sequence": d["insert_seq"], "oh5": d["oh5"], "oh3": d["oh3"],
            "name": long_b, "grammar": "gb_l0", "type": d["part_type"]})
        assert r1["ok"] and r2["ok"], (r1, r2)
        assert r1["saved_id"] != r2["saved_id"]
        # Both retrievable by their distinct ids.
        assert sc._find_library_entry_by_id(r1["saved_id"]) is not None
        assert sc._find_library_entry_by_id(r2["saved_id"]) is not None

    def test_routes_to_non_active_collection(self):
        # Honor a non-active `collection` as a real destination (agent-API
        # feedback): the L0 files into THAT collection like copy-plasmid {to},
        # not the active one, and never touches the active live mirror.
        d = self._configure_vector_and_design()
        sc._save_collections([
            {"name": "Active", "plasmids": [], "saved": "2026-01-01"},
            {"name": "Dest", "plasmids": [], "saved": "2026-01-01"},
        ])
        sc._set_active_collection_name("Active")
        sc._restore_library_from_active_collection()
        r = sc._h_domesticate_part(None, {
            "sequence": d["insert_seq"], "oh5": d["oh5"], "oh3": d["oh3"],
            "name": "RoutedCDS", "grammar": "gb_l0", "type": d["part_type"],
            "collection": "Dest"})
        assert isinstance(r, dict) and r["ok"], r
        assert r["collection"] == "Dest"
        colls = {c["name"]: c for c in sc._load_collections()}
        assert any(e.get("name") == "RoutedCDS"
                   for e in colls["Dest"]["plasmids"]), colls
        # NOT in the active collection nor its live mirror.
        assert not any(e.get("name") == "RoutedCDS"
                       for e in colls["Active"]["plasmids"])
        assert not any(e.get("name") == "RoutedCDS" for e in sc._load_library())

    def test_unknown_collection_is_404(self):
        d = self._configure_vector_and_design()
        r = sc._h_domesticate_part(None, {
            "sequence": d["insert_seq"], "oh5": d["oh5"], "oh3": d["oh3"],
            "name": "x", "grammar": "gb_l0", "collection": "NoSuchColl"})
        assert isinstance(r, tuple) and r[1] == 404, r


class TestSynthL0FragmentButton:
    """Synthesis-tab "L0 Fragment" button → SynthL0FragmentModal → nested
    fragment loaded into the DNA buffer, ready to order for synthesis
    (2026-07-13). Uses the CTCG/TGAG two-tier acceptor from _bind_test_vector."""

    _CDS = "ATG" + "GAACTGAAAGCAGGT" * 8 + "TAA"

    @pytest.mark.asyncio
    async def test_button_opens_modal(self):
        _bind_test_vector()
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            syn = await _open_synthesis_with_seq(app, pilot, self._CDS)
            syn.action_make_l0_fragment()
            for _ in range(5):
                await pilot.pause()
            assert type(app.screen).__name__ == "SynthL0FragmentModal"

    @pytest.mark.asyncio
    async def test_wraps_buffer_with_nested_overhangs(self):
        _bind_test_vector()
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            syn = await _open_synthesis_with_seq(app, pilot, self._CDS)
            syn.action_make_l0_fragment()
            for _ in range(5):
                await pilot.pause()
            # pick the CDS position — category AATG/GCTT nests inside CTCG/TGAG
            app.screen.dismiss({"oh5": "AATG", "oh3": "GCTT",
                                "part_type": "CDS", "label": "CDS"})
            for _ in range(6):
                await pilot.pause()
            ed = syn.query_one("#syn-editor", sc.SynthesisEditor)
            frag = (ed.get_state()[0] or "").upper()
            # buffer now holds the synthesis-ready nested L0 fragment …
            assert frag.startswith("GCGCCGTCTCACTCGAATG"), frag[:24]
            # … whose Esp3I cut exposes the vector's external overhangs
            digs = sc._digest_with_enzymes(frag, ["Esp3I"], circular=False)
            ins = [f for f in digs if f["left"]["kind"] != "linear"
                   and f["right"]["kind"] != "linear"]
            assert ins and ins[0]["left"]["overhang_seq"] == "CTCG"
            assert ins[0]["right"]["overhang_seq"] == "TGAG"

    @pytest.mark.asyncio
    async def test_cancel_leaves_buffer_untouched(self):
        _bind_test_vector()
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            syn = await _open_synthesis_with_seq(app, pilot, self._CDS)
            syn.action_make_l0_fragment()
            for _ in range(5):
                await pilot.pause()
            app.screen.dismiss(None)                       # cancel = no-op
            for _ in range(6):
                await pilot.pause()
            ed = syn.query_one("#syn-editor", sc.SynthesisEditor)
            assert (ed.get_state()[0] or "").upper() == self._CDS
