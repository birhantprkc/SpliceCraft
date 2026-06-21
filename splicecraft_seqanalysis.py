"""splicecraft_seqanalysis — sequence analysis & part classification (Phase D, layer L3).

Read-only analysis of a loaded sequence: the six-frame ORF finder (`_find_orfs`)
and the Golden-Braid / MoClo part classifier (`_classify_part_from_plasmid` — which
cloning grammar + position + level a circular plasmid represents) plus its helpers
(`_check_vector_match` / `_vector_half_top_seq`, `_grammar_acceptor_tu_pairs` /
`_grammar_canonical_overhangs`, `_fragment_has_backbone_marker`, `_part_level_label`).

Layer L3 (same as cloning): the classifier digests the candidate plasmid via cloning's
`_excise_fragment_pair` + reads `_grammar_tu_overhangs`, so it sits at cloning's layer —
cloning never imports this module, so no cycle. Other deps are strictly lower: codon L2
(`_CODON_TABLE` / `_STOP_CODONS`), record L1 (`_gb_text_to_record`), dataaccess L1
(`_all_grammars` / `_load_entry_vectors`), biology L0 (`_rc`), logging L0.

The two classification LRU caches live in `_state` (`_VECTOR_MATCH_CACHE` /
`_ACCEPTOR_TU_PAIRS_CACHE`) so the hub-side `_after_entry_vectors_save` bust
(`globals().get(...).clear()`) and this sibling's reads/writes hit the SAME dict —
read them as `_state._VECTOR_MATCH_CACHE`, never by a stale by-value import. The caps
(`_VECTOR_MATCH_CACHE_MAX` / `_ACCEPTOR_TU_PAIRS_CACHE_MAX`) are plain consts here.
Re-exported by the hub so `sc.<name>` + every call site resolves unchanged.
"""
from __future__ import annotations

import splicecraft_state as _state
from splicecraft_biology import _rc
from splicecraft_codon import _CODON_TABLE, _STOP_CODONS
from splicecraft_record import _gb_text_to_record
from splicecraft_dataaccess import _all_grammars, _load_entry_vectors
from splicecraft_cloning import _excise_fragment_pair, _grammar_tu_overhangs
from splicecraft_logging import _log, _log_event


def _find_orfs(seq: str, *,
               min_aa: int = 30,
               include_alt_starts: bool = False,
               circular: bool = True) -> list[dict]:
    """Six-frame ORF scan. Returns
    ``[{start, end, strand, length_aa, aa_seq}, ...]`` sorted by length
    descending. Wrap-aware on circular plasmids: an ORF crossing the
    origin is reported with ``end < start``, matching the wrap-feature
    convention `_feat_len` / `_bp_in` already implement.

    `min_aa` excludes the stop codon (so ``min_aa=30`` ⇒ ORFs ≥ 30
    coded residues, i.e. ≥ 93 bp including the trailing stop).
    `include_alt_starts=True` adds GTG and TTG to the start-codon
    set — useful for bacterial genomes; off by default since most
    plasmid CDSes use ATG.
    """
    n = len(seq)
    if n < 6:
        return []
    seq_u = seq.upper()
    starts = {"ATG"}
    if include_alt_starts:
        starts |= {"GTG", "TTG"}

    orfs: list[dict] = []

    for strand in (1, -1):
        if strand == 1:
            scan_seq = (seq_u + seq_u) if circular else seq_u
        else:
            rc_seq = _rc(seq_u)
            scan_seq = (rc_seq + rc_seq) if circular else rc_seq
        scan_n = len(scan_seq)

        for frame in range(3):
            current_start = -1
            i = frame
            while i + 3 <= scan_n:
                codon = scan_seq[i:i+3]
                if current_start < 0:
                    if codon in starts:
                        current_start = i
                else:
                    if codon in _STOP_CODONS:
                        aa_len = (i - current_start) // 3
                        # Drop ORFs whose start codon falls in the
                        # second copy of the doubled scan — they're
                        # duplicates of one we already found from the
                        # first copy.
                        if (circular and current_start >= n) \
                                or aa_len < min_aa:
                            current_start = -1
                            i += 3
                            continue
                        nt_seq = scan_seq[current_start:i + 3]  # incl. stop
                        aa_seq = "".join(
                            _CODON_TABLE.get(nt_seq[k:k+3], "?")
                            for k in range(0, len(nt_seq), 3)
                        )
                        if strand == 1:
                            o_s = current_start
                            o_e = i + 3
                            if circular and o_e > n:
                                o_e -= n   # wrap: end < start
                        else:
                            p_rc = current_start
                            e_rc = i + 3
                            if circular:
                                o_s = (n - e_rc) % n
                                o_e = (n - p_rc) % n
                            else:
                                o_s = n - e_rc
                                o_e = n - p_rc
                        orfs.append({
                            "start":     o_s,
                            "end":       o_e,
                            "strand":    strand,
                            "length_aa": aa_len,
                            "aa_seq":    aa_seq,
                        })
                        current_start = -1
                i += 3

    # Dedupe identical (start, end, strand) tuples — can happen when the
    # doubled-scan cycles past the origin and re-finds the same ORF, or
    # when alt-starts inside an ATG-bounded region produce nested hits
    # that then collapse onto the same boundary.
    orfs.sort(key=lambda o: o["length_aa"], reverse=True)
    seen: set[tuple] = set()
    unique: list[dict] = []
    for o in orfs:
        k = (o["start"], o["end"], o["strand"])
        if k in seen:
            continue
        seen.add(k)
        unique.append(o)
    return unique


# Backbone-marker keywords used by `_pick_insert_fragment` to tell the
# bacterial / replication-machinery half of a digested plasmid apart
# from the cloned insert. Match is case-insensitive substring on
# feature labels + types — covers the common annotation styles
# (`rep_origin`, `Ori*`, `AmpR`, `KanR`, `cat`, `Spec`, etc.) without
# trying to be exhaustive: false negatives just trigger the size
# fallback, which is fine.
_BACKBONE_FEATURE_TYPES: frozenset[str] = frozenset({
    "rep_origin", "oriT",
})


_BACKBONE_LABEL_KEYWORDS: tuple[str, ...] = (
    "ori", "rep_origin",
    "ampr", "kanr", "specr", "specinomycin", "spectinomycin",
    "cmr", "chloramphenicol", "tetr", "tetracyclin",
    "carbr", "carbenicillin",
    "selection", "antibiotic",
)


def _fragment_has_backbone_marker(frag: dict) -> bool:
    """Return True iff ``frag``'s features include a typical
    bacterial-backbone marker (origin of replication or antibiotic
    resistance). Case-insensitive substring match on the feature's
    label / qualifier.

    Used by `_pick_insert_fragment` to avoid the "smallest fragment
    is the dropout" heuristic — that rule breaks the moment a
    stacked-TU/MOD insert outgrows its carrier vector. Looking for
    the ORIGIN/SELECTION markers is reliable because real Golden
    Braid / MoClo entry vectors annotate them, and the L0 parts
    chained INTO an insert never do."""
    for f in (frag.get("features") or []):
        if not isinstance(f, dict):
            continue
        ftype = str(f.get("type") or "").lower()
        if ftype in _BACKBONE_FEATURE_TYPES:
            return True
        label = str(f.get("label") or "").lower()
        if not label:
            continue
        for kw in _BACKBONE_LABEL_KEYWORDS:
            if kw in label:
                return True
    return False


_VECTOR_MATCH_CACHE_MAX = 64


def _vector_half_top_seq(ev_gb: str, enzyme: str) -> "str | None":
    """Cached helper: return the entry vector's vector-half top_seq
    after digesting with ``enzyme``. Returns None on parse / digest
    failure or when the EV is non-circular (digest needs a ring).
    Cache is keyed by ``(ev_gb, enzyme)`` so a saved EV's digest is
    re-used across `_check_vector_match` calls within a classification
    run.

    Non-circular EVs are explicitly skipped (returns None) — without
    the topology check, a linearised EV file would dispatch through
    `_excise_fragment_pair(circular=True)` and produce nonsense
    fragments. Better to fail closed than report a phantom match.

    Sweep #25 (2026-05-23): key is `(hash(ev_gb), enzyme)` not
    `(ev_gb, enzyme)`. Pre-fix the key held the full gb_text string
    (potentially multi-MB); at 64 entries × 5 MB worst-case
    a steady-state cache could pin ~320 MB just in tuple keys.
    `hash(str)` is deterministic per-process so cache hits work
    correctly within a session. Collisions are functionally
    indistinguishable from a miss (we'd just recompute) — Python
    `hash(str)` collisions are vanishingly rare across the small
    enzyme inputs we feed.
    """
    key = (hash(ev_gb), enzyme)
    if key in _state._VECTOR_MATCH_CACHE:
        return _state._VECTOR_MATCH_CACHE[key]
    result: "str | None" = None
    try:
        ev_rec = _gb_text_to_record(ev_gb)
        topology = (
            getattr(ev_rec, "annotations", {}) or {}
        ).get("topology", "")
        if str(topology).lower() != "circular":
            # Refuse linearised entry vectors — `_excise_fragment_pair`
            # with `circular=True` on a linear sequence misreports
            # cuts at the joined ends.
            _log.debug(
                "_vector_half_top_seq: skipping non-circular EV "
                "(topology=%r)", topology,
            )
        else:
            ev_seq = str(ev_rec.seq).upper()
            ev_frags, ev_err = _excise_fragment_pair(
                ev_seq, [enzyme], circular=True,
            )
            if ev_err is None and len(ev_frags) == 2:
                ev_vector_half = max(
                    ev_frags,
                    key=lambda f: len(f.get("top_seq") or ""),
                )
                result = (
                    ev_vector_half.get("top_seq") or ""
                ).upper() or None
    except Exception:
        _log.debug(
            "_vector_half_top_seq: digest failed for enzyme %r", enzyme,
        )
    # Bounded LRU-ish: drop one arbitrary entry when over cap. Insertion
    # order eviction (Python 3.7+ dict preserves insertion order) gives
    # us FIFO behaviour without an OrderedDict import.
    if len(_state._VECTOR_MATCH_CACHE) >= _VECTOR_MATCH_CACHE_MAX:
        try:
            _state._VECTOR_MATCH_CACHE.pop(next(iter(_state._VECTOR_MATCH_CACHE)))
        except StopIteration:
            pass
    _state._VECTOR_MATCH_CACHE[key] = result
    return result


def _check_vector_match(
    gid: str, enzyme: str, user_vector_frag: dict,
) -> "dict | None":
    """Compare the user's vector half (the larger digest fragment)
    against every entry vector configured for grammar ``gid``. Returns
    ``{role, name, matches: True}`` for the first vector half that's
    rotationally identical (in either orientation), or ``None`` when
    no entry vector is configured for ``gid`` / no match is found.

    The comparison uses the same ``enzyme`` that produced the user's
    digest so the two vector halves are directly comparable. Rotation-
    invariance is handled by checking whether the user's top_seq is a
    substring of the entry vector's doubled top_seq — fragments from
    rotationally equivalent rings have the same content but may start
    at different positions in the linearised representation. The same
    test is run against the EV's reverse-complement-doubled seq so an
    RC-saved user plasmid (the same biological ring written with the
    other strand on top) still matches.

    Used by `_classify_part_from_plasmid` to surface "this plasmid was
    cloned into your configured Alpha1 entry vector" so Load Part can
    confirm the user's expected destination matches before saving.
    """
    user_vec_seq = (user_vector_frag.get("top_seq") or "").upper()
    if not user_vec_seq:
        return None
    for ev in _load_entry_vectors():
        if ev.get("grammar_id") != gid:
            continue
        ev_gb = ev.get("gb_text") or ""
        if not ev_gb:
            continue
        ev_vec_seq = _vector_half_top_seq(ev_gb, enzyme)
        if not ev_vec_seq or len(user_vec_seq) != len(ev_vec_seq):
            continue
        if user_vec_seq in (ev_vec_seq + ev_vec_seq):
            return {
                "role":    ev.get("role") or "",
                "name":    ev.get("name", "?"),
                "matches": True,
            }
        # Reverse-strand orientation: the user may have saved the
        # plasmid with the other strand on top, which makes the
        # digest's top_seq the RC of the canonical vector half. Try
        # the RC-doubled form too — same rotation-invariant check.
        try:
            rc_doubled = _rc(ev_vec_seq)
            if rc_doubled and user_vec_seq in (rc_doubled + rc_doubled):
                return {
                    "role":    ev.get("role") or "",
                    "name":    ev.get("name", "?"),
                    "matches": True,
                }
        except Exception:
            _log.debug(
                "_check_vector_match: RC fallback failed for ev %r",
                ev.get("name"),
            )
            continue
    return None


_ACCEPTOR_TU_PAIRS_CACHE_MAX = 64


def _grammar_acceptor_tu_pairs(
    grammar_id: str, enzyme: str,
) -> "list[tuple[str, str, str, str]]":
    """Return ``[(role, ev_name, oh5, oh3), ...]`` — the stuffer's
    overhang pair released by digesting each configured entry vector
    for ``grammar_id`` with ``enzyme``.

    The L0 → L1 entry vector for Golden Braid has four canonical
    roles (Alpha1 / Alpha2 / Omega1 / Omega2), each receiving a TU
    in a different orientation. When BsaI digests the assembled TU
    plasmid (a TU INSIDE one of those acceptors), the released
    insert carries that acceptor's specific overhang pair — which
    is the SAME pair as the stuffer's overhangs in the empty
    acceptor itself. Pre-2026-05-13 the classifier only knew the
    canonical (Promoter.oh5, Terminator.oh3) pair via
    `_grammar_tu_overhangs`, so a TU in Alpha2 / Omega1 / Omega2
    silently failed to classify (the bug a user hit on DEMO-25 in
    alpha-2).

    Singleton entry vectors (role == "") are skipped — those are L0
    acceptors (pUPD2 et al.), not TU acceptors, and the L0 position
    table check upstream covers them.

    Result is cached per ``(grammar_id, enzyme)`` and invalidated by
    `_save_entry_vectors`. A multi-select Load Part batch that
    classifies 50 plasmids against 2 grammars × 2 enzymes was
    re-digesting every configured EV 200× without this cache.

    Failures (no gb_text, parse error, digest yields ≠ 2 fragments)
    are logged at warning level so a misconfigured EV surfaces in
    the diagnostic bundle — the user otherwise sees their TU silently
    fall through to None classification with no hint why.
    """
    cache_key = (grammar_id, enzyme)
    cached = _state._ACCEPTOR_TU_PAIRS_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    out: "list[tuple[str, str, str, str]]" = []
    for ev in _load_entry_vectors():
        if ev.get("grammar_id") != grammar_id:
            continue
        role = ev.get("role") or ""
        if not role:
            continue   # singleton L0 vector — not a TU acceptor
        ev_gb = ev.get("gb_text") or ""
        ev_name = ev.get("name") or "?"
        if not ev_gb:
            _log.warning(
                "_grammar_acceptor_tu_pairs: entry vector %r "
                "(role=%r, grammar=%r) has no gb_text — skipping",
                ev_name, role, grammar_id,
            )
            continue
        try:
            record = _gb_text_to_record(ev_gb)
            ev_seq = str(getattr(record, "seq", "") or "").upper()
            if not ev_seq:
                _log.warning(
                    "_grammar_acceptor_tu_pairs: entry vector %r "
                    "parsed to empty sequence — skipping",
                    ev_name,
                )
                continue
            frags, err = _excise_fragment_pair(
                ev_seq, [enzyme], circular=True,
            )
        except Exception:
            _log.exception(
                "_grammar_acceptor_tu_pairs: digest failed for "
                "ev=%r role=%r enzyme=%r", ev_name, role, enzyme,
            )
            continue
        if err is not None or len(frags) != 2:
            _log.info(
                "_grammar_acceptor_tu_pairs: ev=%r role=%r digest "
                "with %r produced %d fragment(s) (err=%r) — skipped",
                ev_name, role, enzyme, len(frags), err,
            )
            continue
        # Stuffer = the smaller fragment (the placeholder lacZα or
        # ccdB cassette that gets replaced by the assembled TU). Its
        # 5' / 3' overhangs ARE the TU-boundary overhangs for this
        # acceptor.
        insert = min(frags, key=lambda f: len(f.get("top_seq", "")))
        oh5 = (insert.get("left")  or {}).get(
            "overhang_seq", "",
        ).upper()
        oh3 = (insert.get("right") or {}).get(
            "overhang_seq", "",
        ).upper()
        if oh5 and oh3:
            out.append((role, ev_name, oh5, oh3))
    # Sweep #26: FIFO-evict oldest entry if at cap.
    if len(_state._ACCEPTOR_TU_PAIRS_CACHE) >= _ACCEPTOR_TU_PAIRS_CACHE_MAX:
        try:
            _state._ACCEPTOR_TU_PAIRS_CACHE.pop(next(iter(_state._ACCEPTOR_TU_PAIRS_CACHE)))
        except (StopIteration, KeyError):
            pass
    _state._ACCEPTOR_TU_PAIRS_CACHE[cache_key] = list(out)
    return out


def _classify_part_from_plasmid(
    seq: str,
    *,
    circular: bool,
    features: "list[dict] | None" = None,
) -> "dict | None":
    """Identify which cloning grammar + position + level a circular
    plasmid holds, by trying each grammar's primary and secondary
    Type IIS enzymes and matching the released fragment's overhangs
    against either the grammar's L0 position table or its TU boundary
    overhangs (`_grammar_tu_overhangs`).

    SACRED INVARIANT: the (oh5, oh3) overhang pair released by the
    digest is the **only** input used to determine position type.
    The classifier never overrides this from feature labels, plasmid
    name, source filename, or any other heuristic — the user's
    biological molecule has ONE legal position per overhang pair, so
    the lookup is unambiguous and the code path must reflect that.
    Callers can re-tag manually via the Parts Bin Edit modal if they
    really need to, but the classifier itself stays mechanical.

    Detection cases (per grammar, in registry order, for each
    enzyme; **both fragments** are tried in each pass so a library
    entry without backbone-marker annotations still classifies
    correctly when the insert outgrew the backbone):

      * Either fragment's overhangs match an L0 position → ``level=0``
        (L0 part, regardless of which side of the cycle produced it).
      * Either fragment's overhangs match the canonical TU boundary
        OR a configured entry vector's stuffer pair → ``level=1`` (TU).

    Pre-2026-05-13 the classifier picked a single "insert" fragment
    via `_pick_insert_fragment` and inferred level from enzyme
    parity ("primary release ⇒ MOD"). Both heuristics broke for
    real-world plasmids:

      * `_pick_insert_fragment` falls through to "smallest fragment"
        when no `rep_origin`/`selection_marker` features are present;
        for a TU whose body is larger than its alpha backbone
        (common for any cassette ~2 kbp+) the backbone half got
        picked as the "insert" and its mirrored overhangs matched
        nothing. DEMO 26 in the DemoColl collection: 3250 bp body with
        the correct (GGAG, GTCA) overhangs, 1850 bp backbone with
        the mirrored (GTCA, GGAG) — the body was discarded.

      * Enzyme parity assumed the splicecraft convention
        (Esp3I = primary = L0 release, BsaI = secondary = L1
        release). The actual pDGB1 / GB 2.0 convention used by
        Sarrion-Perdigones 2013 and the user's DemoColl collection has
        these REVERSED (BsaI = L0 release from pUPD2, Esp3I = L1
        release from α-vectors). Under that convention, the user's
        TUs were being mis-classified as MOD (level=2).

    Auto MOD (level=2) detection from overhangs alone is unreliable
    across both conventions — a fragment with TU-boundary overhangs
    could be a TU or a MOD depending on which enzyme cycle the lab
    uses, and we can't tell from the overhangs alone. TUs that are
    actually MODs in the user's lab can be re-tagged via the
    Parts Bin Edit modal.

    Returns ``None`` when no grammar gives a clean (exactly 2-fragment)
    digest with recognised overhangs. Otherwise returns
    ``{grammar_id, grammar_name, level, position, insert, vector,
       release_enzyme, entry_vector}``.

    Linear records skip the digest — a linear "part" can't be cleanly
    excised, and the overhangs in the .gb annotation are the source
    of truth in that case.
    """
    if not seq or not circular:
        return None
    grammars = _all_grammars()
    for gid, grammar in grammars.items():
        primary   = grammar.get("enzyme")
        secondary = (grammar.get("level_up_enzyme")
                     or grammar.get("enzyme"))
        for enzyme_role, enzyme in (
            ("primary",   primary),
            ("secondary", secondary),
        ):
            if not isinstance(enzyme, str) or not enzyme:
                continue
            # Skip the secondary pass entirely when it's the same as
            # the primary (custom grammar that omits `level_up_enzyme`)
            # — we'd just re-run the same digest with the same outcome.
            if enzyme_role == "secondary" and enzyme == primary:
                continue
            try:
                frags, err = _excise_fragment_pair(
                    seq, [enzyme], circular=True,
                    features=features,
                    source_label=grammar.get("name", gid),
                )
            except Exception:
                _log.exception(
                    "_classify_part_from_plasmid: %s digest failed for %s",
                    enzyme_role, gid,
                )
                continue
            if err is not None or len(frags) != 2:
                continue
            # Try BOTH fragments — sized assumptions about which half
            # is the "insert" break when the user's library entries
            # don't carry rep_origin/selection-marker annotations
            # AND the assembled cassette is larger than its carrier
            # backbone (DEMO 26 family, 2026-05-13). For each fragment,
            # match its overhangs against the L0 position table, the
            # canonical TU boundary, and the per-acceptor stuffer
            # pairs; the FIRST match wins, with the other fragment
            # becoming the vector half by elimination.
            for insert_idx, insert in enumerate(frags):
                vector = frags[1 - insert_idx]
                oh5 = (insert.get("left")  or {}).get(
                    "overhang_seq", "",
                ).upper()
                oh3 = (insert.get("right") or {}).get(
                    "overhang_seq", "",
                ).upper()
                if not oh5 or not oh3:
                    continue
                # First check: the L0 position table. Both pre- and
                # post-cloning L0 parts land here — only the enzyme
                # differs depending on the lab's GB convention.
                for pos in (grammar.get("positions") or []):
                    pos_oh5 = str(pos.get("oh5", "")).upper()
                    pos_oh3 = str(pos.get("oh3", "")).upper()
                    if pos_oh5 == oh5 and pos_oh3 == oh3:
                        return {
                            "grammar_id":     gid,
                            "grammar_name":   grammar.get("name", gid),
                            "level":          0,
                            "position":       dict(pos),
                            "insert":         insert,
                            "vector":         vector,
                            "release_enzyme": enzyme,
                            "entry_vector":   _check_vector_match(
                                gid, enzyme, vector,
                            ),
                        }
                # Second check: canonical TU boundary overhangs
                # (Pos 1's oh5 + last position's oh3). Matches a TU
                # assembled into the canonical L1 acceptor — the
                # orientation that lines up with the grammar's L0
                # positions.
                tu_start, tu_end = _grammar_tu_overhangs(grammar)
                if (tu_start and tu_end
                        and tu_start.upper() == oh5
                        and tu_end.upper()   == oh3):
                    position = {
                        "name":  _part_level_label(1),
                        "type":  _part_level_label(1),
                        "oh5":   tu_start,
                        "oh3":   tu_end,
                        "color": "white",
                    }
                    return {
                        "grammar_id":     gid,
                        "grammar_name":   grammar.get("name", gid),
                        "level":          1,
                        "position":       position,
                        "insert":         insert,
                        "vector":         vector,
                        "release_enzyme": enzyme,
                        "entry_vector":   _check_vector_match(
                            gid, enzyme, vector,
                        ),
                    }
                # Third check: per-acceptor TU boundary overhangs.
                # Each configured entry vector's stuffer carries a
                # specific (oh5, oh3) pair; a TU assembled into
                # that acceptor will release with the same pair.
                # DEMO-25 in alpha-2 hit this pass (2026-05-13).
                for role, ev_name, acc_oh5, acc_oh3 in (
                    _grammar_acceptor_tu_pairs(gid, enzyme)
                ):
                    if acc_oh5 == oh5 and acc_oh3 == oh3:
                        label = _part_level_label(1)
                        position = {
                            "name":  f"{label} ({role})",
                            "type":  label,
                            "oh5":   acc_oh5,
                            "oh3":   acc_oh3,
                            "color": "white",
                        }
                        return {
                            "grammar_id":     gid,
                            "grammar_name":   grammar.get("name", gid),
                            "level":          1,
                            "position":       position,
                            "insert":         insert,
                            "vector":         vector,
                            "release_enzyme": enzyme,
                            "entry_vector":   _check_vector_match(
                                gid, enzyme, vector,
                            ),
                        }
            # Pass 4 (2026-05-20): lenient TU detection for L1
            # acceptors that release outside the canonical (Pos 1
            # oh5, Pos N oh3) pair but use overhangs from the
            # grammar's own canonical alphabet. Caught by user
            # report on the DemoColl collection DEMO 25-31 (TUx1/TUx2
            # acceptors) — TUx1 releases the TU with (GGAG, GTCA);
            # TUx2 with (GTCA, CGCT). Both pairs are valid GB 2.0
            # overhangs (GTCA = RC(TGAC), a Pos 1b operator
            # overhang in the GB 2.0 expanded grammar) but neither
            # matches the strict TU boundary and neither lab has
            # entry vectors configured in `entry_vectors.json`.
            #
            # Pick the TU candidate via `_pick_insert_fragment`'s
            # backbone-marker exclusion — NEVER by fragment size.
            # Size is unreliable for L1+ TUs (an assembled cassette
            # commonly outgrows the alpha-vector backbone), and
            # the user has explicitly called out this assumption
            # as a class of bugs (2026-05-20).
            #
            # Skip pass-4 when no fragment carries a clear backbone
            # marker — pass-4 cannot safely commit to one fragment
            # being the TU without that biology signal, and a
            # wrong-tag is worse than the existing "no detectable
            # grammar" outcome.
            backbone_marked = [
                _fragment_has_backbone_marker(f) for f in frags
            ]
            if sum(backbone_marked) != 1:
                # Either both have markers (annotation noise) or
                # neither does (un-annotated entry). Either way,
                # pass-4 can't safely commit. Fall through to the
                # next grammar/enzyme.
                continue
            tu_idx = 0 if not backbone_marked[0] else 1
            tu_candidate = frags[tu_idx]
            vector_candidate = frags[1 - tu_idx]
            oh5 = (tu_candidate.get("left") or {}).get(
                "overhang_seq", "",
            ).upper()
            oh3 = (tu_candidate.get("right") or {}).get(
                "overhang_seq", "",
            ).upper()
            if not oh5 or not oh3:
                continue
            canonical = _grammar_canonical_overhangs(grammar)
            if oh5 not in canonical or oh3 not in canonical:
                continue
            position = {
                "name":  f"TU ({oh5}→{oh3})",
                "type":  _part_level_label(1),
                "oh5":   oh5,
                "oh3":   oh3,
                "color": "white",
            }
            _log_event(
                "classify.lenient_tu",
                grammar=gid, enzyme=enzyme,
                oh5=oh5, oh3=oh3,
            )
            return {
                "grammar_id":     gid,
                "grammar_name":   grammar.get("name", gid),
                "level":          1,
                "position":       position,
                "insert":         tu_candidate,
                "vector":         vector_candidate,
                "release_enzyme": enzyme,
                "entry_vector":   _check_vector_match(
                    gid, enzyme, vector_candidate,
                ),
                "lenient":        True,
            }
    return None


def _grammar_canonical_overhangs(grammar: dict) -> "set[str]":
    """Set of every 4-bp overhang the grammar's position table knows,
    plus each one's reverse complement.

    Pre-2026-05-20 the classifier hard-coded the canonical TU
    boundary as ``(positions[0].oh5, positions[-1].oh3)`` — for
    `gb_l0` that's ``(GGAG, CGCT)``. Real Golden Braid α/Ω
    acceptors release TUs with NON-canonical pairs drawn from the
    same overhang alphabet (e.g. pDGB3 α1 releases with
    ``(GGAG, GTCA)`` — the second overhang is RC of TGAC, a Pos 1b
    operator from the GB 2.0 expanded grammar). This helper
    exposes the full alphabet so the lenient pass-4 of
    `_classify_part_from_plasmid` can recognise these without
    requiring the user to pre-configure entry vectors.

    Includes reverse complements because Type IIS overhangs read
    differently depending on which strand the cut surface is being
    described from — a released fragment whose 5' overhang on the
    top strand is ``GTCA`` has ``TGAC`` on the bottom strand, and
    both are equally "this overhang".
    """
    out: "set[str]" = set()
    for pos in (grammar.get("positions") or []):
        if not isinstance(pos, dict):
            continue
        for key in ("oh5", "oh3"):
            v = str(pos.get(key, "") or "").upper()
            if v and len(v) == 4 and all(c in "ACGT" for c in v):
                out.add(v)
                out.add(_rc(v))
    return out


def _part_level_label(level: int) -> str:
    """Map an integer level to the user-facing label used in the Parts
    Bin tabs and notify strings: L0 → 'L0', 1 → 'TU', ≥2 → 'MOD'."""
    if level <= 0:
        return "L0"
    if level == 1:
        return "TU"
    return "MOD"
