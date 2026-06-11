"""Pass-4 lenient TU classification.

`_classify_part_from_plasmid` falls through to a lenient pass when the
three strict passes (L0 position table / canonical TU boundary /
per-acceptor stuffer) all miss. The lenient pass picks the TU
candidate via backbone-marker exclusion (NEVER size — feedback
`feedback_never_assume_smaller_frag_is_payload`) and accepts if both
released overhangs come from the grammar's canonical alphabet
(positions oh5/oh3 + reverse complements).

Caught by user report 2026-05-20 on the DemoColl collection: DEMO 25-31
release TUs with `(GGAG, GTCA)` / `(GTCA, CGCT)` — valid Golden
Braid 2.0 overhangs (`GTCA` = RC of `TGAC`, a Pos 1b operator
overhang) — but neither matches the strict canonical TU boundary
`(GGAG, CGCT)`. With no entry vectors configured, pre-fix the
classifier silently returned None.
"""
import pytest
import splicecraft as sc

pytestmark = [pytest.mark.usefixtures("_protect_user_data")]


# ── Synthetic plasmid builder ──────────────────────────────────────────────

def _build_l1_release_plasmid(
    *,
    oh5: str,
    oh3: str,
    tu_body: str = "ATGAAACCCGGGTTT" * 50,        # ~750 bp
    backbone_body: str = "ATCGATCGATCG" * 100,    # ~1200 bp
    add_backbone_marker: bool = True,
    marker_type: str = "rep_origin",
):
    """Build a circular plasmid that releases its TU with the given
    `(oh5, oh3)` overhang pair on Esp3I digest. Forward Esp3I site
    flanks the 5' end of the TU; reverse Esp3I site flanks the 3' end.
    The backbone half optionally carries a `rep_origin` feature so
    `_pick_insert_fragment` can identify it via backbone-marker
    exclusion."""
    from Bio.SeqRecord import SeqRecord
    from Bio.Seq import Seq
    from Bio.SeqFeature import SeqFeature, FeatureLocation

    # Esp3I forward site cuts to release `oh5` as the 5' overhang of
    # the downstream fragment: `CGTCTC` + N + oh5 + payload...
    # Reverse-strand Esp3I site (CGTCTC on the complement →
    # `GAGACG` on the top strand) does the same on the right end:
    # ...payload + oh3 + N + GAGACG. Use `T` as the N spacer.
    left_site  = "CGTCTC" + "A"
    right_site = "T" + "GAGACG"
    tu_segment = left_site + oh5 + tu_body + oh3 + right_site
    seq = tu_segment + backbone_body
    rec = SeqRecord(
        Seq(seq), id="test", name="test",
        annotations={"topology": "circular", "molecule_type": "DNA"},
    )
    if add_backbone_marker:
        # Place the marker in the backbone half so the picker
        # excludes the backbone correctly.
        marker_start = len(tu_segment) + 100
        marker_end   = marker_start + 400
        rec.features.append(SeqFeature(
            FeatureLocation(marker_start, marker_end),
            type=marker_type,
            qualifiers={"label": ["test_marker"]},
        ))
    return rec


def _classify(rec):
    """Run the classifier on a SeqRecord — extracts the features in
    the dict shape the classifier expects."""
    seq = str(rec.seq).upper()
    circ = rec.annotations.get("topology") == "circular"
    feats = [
        {
            "start": int(f.location.start),
            "end":   int(f.location.end),
            "type":  f.type,
            "qualifiers": dict(f.qualifiers),
        }
        for f in rec.features
    ]
    return sc._classify_part_from_plasmid(
        seq, circular=circ, features=feats,
    )


# ── DemoColl DEMO 25-31 reproduction ────────────────────────────────────────────

def test_pass4_classifies_tua1_style_release():
    """The reported bug: a TU released with `(GGAG, GTCA)` (TUx1 in
    the DemoColl collection) classifies as level=1 via the lenient
    fallback. `GGAG` is canonical Pos-1 oh5; `GTCA` is RC of TGAC
    (a Pos-1b operator overhang in the GB 2.0 expanded grammar)."""
    rec = _build_l1_release_plasmid(oh5="GGAG", oh3="GTCA")
    result = _classify(rec)
    assert result is not None, "Pass-4 must classify TUx1-style release"
    assert result["level"] == 1
    assert result["lenient"] is True
    assert result["position"]["oh5"] == "GGAG"
    assert result["position"]["oh3"] == "GTCA"
    assert "GGAG→GTCA" in result["position"]["name"]
    assert result["grammar_id"] == "gb_l0"


def test_pass4_classifies_tua2_style_release():
    """TUx2 in DemoColl releases with `(GTCA, CGCT)`. Both overhangs are
    canonical GB 2.0 (`GTCA` = RC of TGAC; `CGCT` is the canonical
    Pos-N oh3)."""
    rec = _build_l1_release_plasmid(oh5="GTCA", oh3="CGCT")
    result = _classify(rec)
    assert result is not None
    assert result["level"] == 1
    assert result["lenient"] is True
    assert result["position"]["oh5"] == "GTCA"
    assert result["position"]["oh3"] == "CGCT"


# ── Safety: no false-positive classification ───────────────────────────────

def test_pass4_declines_without_backbone_marker():
    """When no fragment carries a backbone marker the lenient pass
    cannot safely commit — there's no biology signal to identify
    which half is the TU. Better to surface "no detectable grammar"
    than to wrong-tag. User feedback `feedback_never_assume_smaller_frag_is_payload`
    explicitly rules out size-based picks."""
    rec = _build_l1_release_plasmid(
        oh5="GGAG", oh3="GTCA",
        add_backbone_marker=False,
    )
    result = _classify(rec)
    assert result is None, (
        "Pass-4 must decline when no fragment has a backbone marker — "
        "otherwise an un-annotated user library entry would get "
        "silently mis-tagged"
    )


def test_pass4_declines_when_both_fragments_have_markers():
    """If BOTH fragments carry backbone markers (annotation noise),
    pass-4 cannot identify the TU via exclusion. Decline rather
    than guess."""
    rec = _build_l1_release_plasmid(oh5="GGAG", oh3="GTCA")
    # Inject a second rep_origin into the TU body so both fragments
    # have markers.
    from Bio.SeqFeature import SeqFeature, FeatureLocation
    rec.features.append(SeqFeature(
        FeatureLocation(100, 400),
        type="rep_origin",
        qualifiers={"label": ["bogus_extra_origin"]},
    ))
    result = _classify(rec)
    assert result is None, (
        "Pass-4 must decline when both fragments have backbone markers"
    )


def test_pass4_declines_for_non_canonical_overhangs():
    """If an overhang is NOT in the grammar's canonical alphabet
    (positions oh5/oh3 + their RCs), pass-4 must decline. Otherwise
    any 2-fragment Type IIS digest with random overhangs would
    classify as TU. `AATT` is not in the GB 2.0 alphabet."""
    rec = _build_l1_release_plasmid(oh5="AATT", oh3="GGGG")
    result = _classify(rec)
    assert result is None


# ── Regression: strict passes still win ────────────────────────────────────

def test_strict_l0_position_match_still_wins():
    """A clean L0 part (canonical (Pos.oh5, Pos.oh3) pair) must
    still classify as level=0 via the strict pass — the lenient
    fallback only fires after the strict passes all fail."""
    # Pos 1 (Promoter) in gb_l0: (GGAG, AATG)
    rec = _build_l1_release_plasmid(oh5="GGAG", oh3="AATG")
    result = _classify(rec)
    assert result is not None
    assert result["level"] == 0, (
        "Canonical L0 overhangs must hit the strict pass, not the lenient one"
    )
    assert result.get("lenient") is not True


def test_strict_canonical_tu_boundary_still_wins():
    """A TU released with the canonical (Pos 1 oh5, Pos N oh3)
    pair = `(GGAG, CGCT)` for gb_l0 hits the strict pass-2 (not
    pass-4). The result must NOT carry the `lenient` flag."""
    rec = _build_l1_release_plasmid(oh5="GGAG", oh3="CGCT")
    result = _classify(rec)
    assert result is not None
    assert result["level"] == 1
    # Strict TU boundary match — lenient flag absent / falsy.
    assert result.get("lenient") is not True


# ── Helper: canonical-overhang set ─────────────────────────────────────────

def test_grammar_canonical_overhangs_includes_rcs():
    """`_grammar_canonical_overhangs` must include every position
    oh5/oh3 AND their reverse complements. Required so a fragment
    released with `GTCA` (RC of TGAC) matches a grammar that lists
    TGAC in its position table."""
    grammars = sc._all_grammars()
    gb_l0 = grammars["gb_l0"]
    canon = sc._grammar_canonical_overhangs(gb_l0)
    # Forward overhangs from positions:
    assert "GGAG" in canon
    assert "CGCT" in canon
    assert "AATG" in canon
    # RC fallbacks (sacred for non-canonical orientation matches):
    assert "CTCC" in canon, "RC of GGAG missing — pass-4 would miss TUx1"
    assert "AGCG" in canon, "RC of CGCT missing"
    # In GB 2.0 expanded, TGAC is a Pos 1b operator overhang; GTCA
    # (RC) is what the user's TUx1 releases.
    if "TGAC" in canon:
        assert "GTCA" in canon


def test_grammar_canonical_overhangs_skips_non_4bp():
    """Defensive: malformed overhangs (wrong length, non-ACGT chars)
    must be skipped without raising. A grammar surfacing a bad
    overhang string can't poison the canonical set."""
    grammar = {
        "positions": [
            {"oh5": "GGAG", "oh3": "AATG"},     # valid
            {"oh5": "ABC", "oh3": "AAAA"},      # too short
            {"oh5": "GGAGX", "oh3": "AATGX"},   # too long
            {"oh5": "NNNN", "oh3": "GGAG"},     # non-ACGT
            {"oh5": "", "oh3": ""},             # empty
        ],
    }
    canon = sc._grammar_canonical_overhangs(grammar)
    assert "GGAG" in canon
    assert "AATG" in canon
    assert "CTCC" in canon  # RC of GGAG
    assert "CATT" in canon  # RC of AATG
    assert "ABC" not in canon
    assert "GGAGX" not in canon
    assert "NNNN" not in canon


# ── Real DemoColl DEMO files (when locally present) ─────────────────────────────

@pytest.mark.skipif(
    not __import__("pathlib").Path(
        "/home/seb/EdenCollection/DNA Files/"
        "DEMO 26 TUx1 PAtHsfA-AtReporter-THSP.dna"
    ).exists(),
    reason="DemoColl DEMO files not present on this machine — see fixture path",
)
def test_eden_demo_26_classifies():
    """End-to-end: the user's DEMO 26 .dna file (TUx1 with
    `(GGAG, GTCA)` release) must classify as TU. Skipped on CI
    where the file isn't present."""
    rec = sc.load_genbank(
        "/home/seb/EdenCollection/DNA Files/"
        "DEMO 26 TUx1 PAtHsfA-AtReporter-THSP.dna"
    )
    result = _classify(rec)
    assert result is not None, "DEMO 26 must classify (pass-4 lenient)"
    assert result["level"] == 1
    assert result["lenient"] is True
