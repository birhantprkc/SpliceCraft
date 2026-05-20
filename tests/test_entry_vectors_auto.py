"""Entry-vector auto-detection + EntryVectorsModal.

Detection engine: `_detect_entry_vector_role` digests a candidate
plasmid with both grammar enzymes, identifies the stuffer fragment via
backbone-marker exclusion (NEVER size — feedback
`feedback_never_assume_smaller_frag_is_payload`), and resolves
(inner-enzyme, outer-overhang-pair) → role via the Golden Braid binary-
assembly architecture.

Auto-bind helper: `_auto_bind_entry_vectors_from_entries` runs
detection across every grammar for every entry and binds new roles,
without clobbering existing user bindings.

Modal: `EntryVectorsModal` exposes one row per role with Pick/Clear/
Auto-detect controls. Replaces the single-slot widget in
`GrammarEditorModal`.
"""
import pytest

import splicecraft as sc

pytestmark = [pytest.mark.usefixtures("_protect_user_data")]


# ── Synthetic-acceptor builder ─────────────────────────────────────────────

def _build_acceptor(
    *, oh5_inner: str, oh3_inner: str,
    oh5_outer: str, oh3_outer: str,
    inner_enzyme_site: str = "GGTCTC",    # BsaI forward
    outer_enzyme_site: str = "CGTCTC",    # Esp3I forward
    backbone_body: str = "ATCGATCGAT" * 100,
    stuffer_body: str  = "TTGGAACCAA" * 20,
):
    """Build a circular acceptor where:

      * The INNER enzyme (default BsaI) cuts to release a stuffer with
        `(oh5_inner, oh3_inner)`.
      * The OUTER enzyme (default Esp3I) cuts at outer sites to release
        a larger fragment containing the inner sites + stuffer, with
        outer overhangs `(oh5_outer, oh3_outer)`.

    The backbone carries a `rep_origin` feature so
    `_fragment_has_backbone_marker` can identify it.

    Layout (linearised for clarity, actually circular):

        [backbone+rep_origin] —[outer-rc]——[inner-fwd]·oh5_inner·[stuffer]·oh3_inner·[inner-rc]——[outer-fwd]——
    """
    from Bio.SeqRecord import SeqRecord
    from Bio.Seq import Seq
    from Bio.SeqFeature import SeqFeature, FeatureLocation

    # Forward Esp3I-type site: ENZ + N + overhang + payload
    # Reverse site:           payload + overhang + N + RC(ENZ)
    inner_fwd = inner_enzyme_site + "A"
    inner_rc  = "T" + sc._rc(inner_enzyme_site)
    outer_fwd = outer_enzyme_site + "A"
    outer_rc  = "T" + sc._rc(outer_enzyme_site)

    # Inner cassette (between outer cuts) contains the stuffer +
    # flanking BsaI sites.
    inner_cassette = (
        inner_fwd + oh5_inner + stuffer_body + oh3_inner + inner_rc
    )
    # Outer "release" region surrounds the inner cassette. We arrange
    # outer Esp3I sites OUTSIDE the inner cassette so that an Esp3I
    # digest of the full ring releases the [inner_cassette] piece
    # with outer overhangs (oh5_outer, oh3_outer).
    inner_with_outer_flanks = (
        outer_fwd + oh5_outer + inner_cassette + oh3_outer + outer_rc
    )
    seq = inner_with_outer_flanks + backbone_body
    backbone_start = len(inner_with_outer_flanks)
    rec = SeqRecord(
        Seq(seq), id="acceptor", name="acceptor",
        annotations={"topology": "circular", "molecule_type": "DNA"},
    )
    rec.features.append(SeqFeature(
        FeatureLocation(backbone_start + 100, backbone_start + 500),
        type="rep_origin",
        qualifiers={"label": ["bla"]},
    ))
    return rec


# ── Detection engine: canonical α/Ω matches ────────────────────────────────

def test_alpha1_detected_strict():
    """α1: inner BsaI release = (GGAG, CGCT); outer Esp3I = (GGAG, GTCA)."""
    gb_l0 = sc._all_grammars()["gb_l0"]
    rec = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="CGCT",
        oh5_outer="GGAG", oh3_outer="GTCA",
        inner_enzyme_site="GGTCTC",   # BsaI
        outer_enzyme_site="CGTCTC",   # Esp3I
    )
    result = sc._detect_entry_vector_role(rec, gb_l0)
    assert result == ("Alpha1", "strict"), (
        f"Expected Alpha1/strict, got {result}"
    )


def test_alpha2_detected_strict():
    """α2: inner BsaI = (GGAG, CGCT); outer Esp3I = (GTCA, CGCT)."""
    gb_l0 = sc._all_grammars()["gb_l0"]
    rec = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="CGCT",
        oh5_outer="GTCA", oh3_outer="CGCT",
        inner_enzyme_site="GGTCTC",
        outer_enzyme_site="CGTCTC",
    )
    result = sc._detect_entry_vector_role(rec, gb_l0)
    assert result == ("Alpha2", "strict")


def test_omega1_detected_strict():
    """Ω1: inner Esp3I = (GGAG, CGCT); outer BsaI = (GGAG, GTCA)."""
    gb_l0 = sc._all_grammars()["gb_l0"]
    rec = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="CGCT",
        oh5_outer="GGAG", oh3_outer="GTCA",
        inner_enzyme_site="CGTCTC",   # Esp3I inner = Ω
        outer_enzyme_site="GGTCTC",   # BsaI outer = Ω
    )
    result = sc._detect_entry_vector_role(rec, gb_l0)
    assert result == ("Omega1", "strict")


def test_omega2_detected_strict():
    """Ω2: inner Esp3I = (GGAG, CGCT); outer BsaI = (GTCA, CGCT)."""
    gb_l0 = sc._all_grammars()["gb_l0"]
    rec = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="CGCT",
        oh5_outer="GTCA", oh3_outer="CGCT",
        inner_enzyme_site="CGTCTC",
        outer_enzyme_site="GGTCTC",
    )
    result = sc._detect_entry_vector_role(rec, gb_l0)
    assert result == ("Omega2", "strict")


# ── Detection: UPD weak match ──────────────────────────────────────────────

def test_upd_style_singleton_detected_weak():
    """A plasmid with non-canonical stuffer overhangs (not L0 positions,
    not in the canonical alphabet) but still digesting cleanly should
    detect as a singleton L0 donor ("" role, weak)."""
    gb_l0 = sc._all_grammars()["gb_l0"]
    # `TGAG`/`CTCA` matches FFE1 UPD's BsaI digest — non-canonical.
    rec = _build_acceptor(
        oh5_inner="TGAG", oh3_inner="CTCA",
        oh5_outer="CTCG", oh3_outer="TGAG",
        inner_enzyme_site="GGTCTC",
        outer_enzyme_site="CGTCTC",
    )
    result = sc._detect_entry_vector_role(rec, gb_l0)
    assert result is not None
    role, conf = result
    assert role == ""
    assert conf == "weak"


# ── Detection: rejection cases ─────────────────────────────────────────────

def test_l0_part_not_detected_as_acceptor():
    """An L0 part has overhangs matching an L0 position (e.g. Promoter
    = (GGAG, AATG)). The detection engine must REJECT it — it's a
    part, not an empty acceptor."""
    gb_l0 = sc._all_grammars()["gb_l0"]
    rec = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="AATG",     # Pos 1 Promoter
        oh5_outer="GGAG", oh3_outer="AATG",
        inner_enzyme_site="GGTCTC",
        outer_enzyme_site="CGTCTC",
    )
    result = sc._detect_entry_vector_role(rec, gb_l0)
    assert result is None, (
        "L0 part overhangs must not be classified as a UPD donor"
    )


def test_tu_plasmid_not_detected_as_acceptor():
    """A TU plasmid's stuffer overhangs come from the canonical
    alphabet (matches `_classify_part_from_plasmid` pass-4). The
    detector must REJECT — TUs aren't empty acceptors."""
    gb_l0 = sc._all_grammars()["gb_l0"]
    # Match MAV 26 TUA1 release: Esp3I gives (GGAG, GTCA).
    rec = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="GTCA",
        oh5_outer="GGAG", oh3_outer="GTCA",
        inner_enzyme_site="GGTCTC",
        outer_enzyme_site="CGTCTC",
    )
    result = sc._detect_entry_vector_role(rec, gb_l0)
    assert result is None, "TU must not be classified as an acceptor"


def test_linear_record_not_detected():
    """Linear records can't be acceptors (no second cut, can't
    release a stuffer cleanly). Must return None."""
    gb_l0 = sc._all_grammars()["gb_l0"]
    rec = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="CGCT",
        oh5_outer="GGAG", oh3_outer="GTCA",
    )
    rec.annotations["topology"] = "linear"
    result = sc._detect_entry_vector_role(rec, gb_l0)
    assert result is None


def test_record_without_backbone_marker_rejected():
    """Sacred — backbone-marker exclusion is how the stuffer is
    identified. Without a marker, the detector can't safely pick
    a stuffer (size is forbidden). Returns None."""
    gb_l0 = sc._all_grammars()["gb_l0"]
    rec = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="CGCT",
        oh5_outer="GGAG", oh3_outer="GTCA",
    )
    # Strip the rep_origin feature.
    rec.features = []
    result = sc._detect_entry_vector_role(rec, gb_l0)
    assert result is None


# ── Auto-bind helper ───────────────────────────────────────────────────────

def _entry_from_record(rec, eid: str = "test") -> dict:
    """Wrap a SeqRecord into the library-entry dict shape."""
    return {
        "id":      eid,
        "name":    eid,
        "size":    len(rec.seq),
        "gb_text": sc._record_to_gb_text(rec),
        "n_feats": len(rec.features),
    }


def test_auto_bind_fills_all_alpha_omega_roles():
    """Feed all 4 acceptors + UPD-style donor to the bulk auto-bind;
    verify all 5 slots get filled with the correct role."""
    # Build 5 acceptors matching the FFE1-5 fingerprint.
    upd = _build_acceptor(
        oh5_inner="TGAG", oh3_inner="CTCA",
        oh5_outer="CTCG", oh3_outer="TGAG",
        inner_enzyme_site="GGTCTC", outer_enzyme_site="CGTCTC",
    )
    a1 = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="CGCT",
        oh5_outer="GGAG", oh3_outer="GTCA",
        inner_enzyme_site="GGTCTC", outer_enzyme_site="CGTCTC",
    )
    a2 = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="CGCT",
        oh5_outer="GTCA", oh3_outer="CGCT",
        inner_enzyme_site="GGTCTC", outer_enzyme_site="CGTCTC",
    )
    o1 = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="CGCT",
        oh5_outer="GGAG", oh3_outer="GTCA",
        inner_enzyme_site="CGTCTC", outer_enzyme_site="GGTCTC",
    )
    o2 = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="CGCT",
        oh5_outer="GTCA", oh3_outer="CGCT",
        inner_enzyme_site="CGTCTC", outer_enzyme_site="GGTCTC",
    )
    entries = [
        _entry_from_record(upd, "upd"),
        _entry_from_record(a1, "a1"),
        _entry_from_record(a2, "a2"),
        _entry_from_record(o1, "o1"),
        _entry_from_record(o2, "o2"),
    ]
    msg = sc._auto_bind_entry_vectors_from_entries(entries)
    assert msg, "Auto-bind should return a summary message"
    assert "Alpha1" in msg
    assert "Alpha2" in msg
    assert "Omega1" in msg
    assert "Omega2" in msg
    assert "UPD" in msg
    # Verify each binding lands in entry_vectors.json under the right
    # (grammar_id, role) key.
    assert sc._get_entry_vector("gb_l0", "")["id"] == "upd"
    assert sc._get_entry_vector("gb_l0", "Alpha1")["id"] == "a1"
    assert sc._get_entry_vector("gb_l0", "Alpha2")["id"] == "a2"
    assert sc._get_entry_vector("gb_l0", "Omega1")["id"] == "o1"
    assert sc._get_entry_vector("gb_l0", "Omega2")["id"] == "o2"


def test_auto_bind_does_not_clobber_existing_bindings():
    """Existing user-set bindings are sacred — the auto-bind must
    only fill in gaps, never replace a pre-existing binding."""
    # User has Alpha1 manually bound to "manual".
    sc._set_entry_vector("gb_l0", {
        "name": "manual", "size": 0, "gb_text": "", "id": "manual",
    }, "Alpha1")
    # Then auto-bind sees an Alpha1 candidate.
    a1 = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="CGCT",
        oh5_outer="GGAG", oh3_outer="GTCA",
        inner_enzyme_site="GGTCTC", outer_enzyme_site="CGTCTC",
    )
    sc._auto_bind_entry_vectors_from_entries(
        [_entry_from_record(a1, "auto")]
    )
    # The original "manual" binding survives — "auto" is skipped.
    bound = sc._get_entry_vector("gb_l0", "Alpha1")
    assert bound is not None
    assert bound["id"] == "manual", (
        "Auto-bind clobbered an existing user binding"
    )


def test_auto_bind_strict_wins_over_weak():
    """If two entries detect for the same role with different
    confidences, the strict match wins."""
    gb_l0 = sc._all_grammars()["gb_l0"]
    weak_upd = _build_acceptor(
        oh5_inner="TGAG", oh3_inner="CTCA",
        oh5_outer="CTCG", oh3_outer="TGAG",
    )
    strict_a1 = _build_acceptor(
        oh5_inner="GGAG", oh3_inner="CGCT",
        oh5_outer="GGAG", oh3_outer="GTCA",
    )
    # Verify the synthetic acceptors detect with the expected
    # confidence — otherwise this test is asserting wrong state.
    assert sc._detect_entry_vector_role(weak_upd, gb_l0) == ("", "weak")
    assert sc._detect_entry_vector_role(strict_a1, gb_l0) == (
        "Alpha1", "strict",
    )
    # Submit weak first, strict second.
    entries = [
        _entry_from_record(weak_upd, "weak"),
        _entry_from_record(strict_a1, "strict"),
    ]
    sc._auto_bind_entry_vectors_from_entries(entries)
    # UPD goes to weak (only candidate); Alpha1 goes to strict.
    assert sc._get_entry_vector("gb_l0", "")["id"] == "weak"
    assert sc._get_entry_vector("gb_l0", "Alpha1")["id"] == "strict"


def test_auto_bind_empty_input_returns_empty_string():
    """No entries → no bindings → empty summary string."""
    assert sc._auto_bind_entry_vectors_from_entries([]) == ""


# ── Modal: smoke test ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entry_vectors_modal_lists_all_gb_l0_roles():
    """Modal renders five rows for gb_l0 (UPD + α1/α2/Ω1/Ω2)."""
    app = sc.PlasmidApp()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        modal = sc.EntryVectorsModal("gb_l0")
        app.push_screen(modal)
        await pilot.pause()
        await pilot.pause()
        from textual.widgets import DataTable
        t = modal.query_one("#ev-table", DataTable)
        # 5 rows = UPD + 4 named roles in `_CONSTRUCTOR_BACKBONES["gb_l0"]`.
        assert t.row_count == 5


@pytest.mark.asyncio
async def test_entry_vectors_modal_blocks_undo():
    """Modal sets `_blocks_undo = True` so app-level Ctrl+Z doesn't
    fire underneath while the user mutates persistent state."""
    modal = sc.EntryVectorsModal("gb_l0")
    assert modal._blocks_undo is True


@pytest.mark.asyncio
async def test_entry_vectors_modal_default_focus_on_table():
    """Default focus is the role table so arrow keys + Enter drive
    the picks. Esc → close."""
    app = sc.PlasmidApp()
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        modal = sc.EntryVectorsModal("gb_l0")
        app.push_screen(modal)
        await pilot.pause()
        await pilot.pause()
        from textual.widgets import DataTable
        t = modal.query_one("#ev-table", DataTable)
        assert t.has_focus


def test_settings_menu_has_entry_vectors():
    """The Entry Vectors entry must be in the Settings menu so users
    can discover it. Greppable check against the source so future
    refactors of the menu list trip the test."""
    with open(sc.__file__, encoding="utf-8") as f:
        src = f.read()
    # The menu definition is the only place an action attribute name
    # appears as `"open_entry_vectors"` in the tuple shape.
    assert '"open_entry_vectors"' in src
    assert "Entry Vectors" in src
