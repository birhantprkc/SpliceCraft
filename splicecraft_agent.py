"""splicecraft_agent — the data-only agent-API HTTP endpoint handlers (Phase D, layer L7).

The pure / data-only `_h_*` endpoint handlers (read queries + data mutations that
go through the sibling accessors, NOT the running app's widgets) extracted from
the hub, plus the agent-API helper layer they share: the `_agent_endpoint`
registration decorator, the response dict-builders (`_agent_*_dict` /
`_custom_enzyme_meta` / `_parts_bin_entry_summary` / `_history_node_to_dict`),
payload coercers / validators (`_coerce_int` / `_sanitize_bases` /
`_settings_validator_*` / `_agent_validate_custom_enzyme_payload`), the agent
path-safety checks, and the write-guard (`_agent_dirty_guard` / `_agent_save_or_500`).

The handlers register into `_state._AGENT_HANDLERS` (the shared registry) via the
decorator at import; the hub's HTTP server dispatches from that same dict. The
APP-COUPLED handlers (those that `query_one(PlasmidMap/SequencePanel/LibraryPanel)`
or touch `app._current_record` / `app.call_from_thread`) STAY hub-side and register
into the SAME dict through the re-exported decorator.

Reaches hub-pinned bits via _state: `_AGENT_HANDLERS` (registry), `_LIVE_APP_REF`
(live-app singleton, for save-failure notify), `_resolve_data_attr_hook`,
`_all_enzymes_hook`, `_cache_lock`. `_sanitize_path` / `_ENZYME_CUT_RANGE` were
relocated to L0 (util / biology) as prerequisites. Top layer among the siblings
(imports the whole domain stack ≤L3); nothing imports it but the hub.
"""
from __future__ import annotations

import re
from datetime import date as _date, datetime as _datetime
from pathlib import Path

import splicecraft_state as _state
from splicecraft_backup import (_list_pre_update_snapshots)
from splicecraft_biology import (_ENZYME_CUT_RANGE, _assemble_operon, _rbs_design, _rbs_strength, _rc, _rna_cofold, _rna_fold, _seq_len)
from splicecraft_cloning import (_GIBSON_MAX_OVERLAP_BP, _GIBSON_MIN_OVERLAP_BP, _scrub_gb_design, _simulate_gibson_assembly)
from splicecraft_codon import (_codon_fetch_kazusa, _codon_optimize, _codon_tables_add, _genome_build_codon_table)
from splicecraft_dataaccess import (_BUILTIN_GRAMMARS, _all_grammars, _clear_entry_vectors_for_grammar, _codon_tables_get, _codon_tables_load, _codon_tables_save, _find_gel, _find_hmm_db_entry, _find_library_entry_by_id, _get_active_collection_name, _get_active_primer_collection_name, _get_entry_vector, _get_setting, _hmm_db_name_taken, _iter_collections_readonly, _iter_library_readonly, _iter_parts_bin_readonly, _load_custom_enzymes, _load_custom_grammars, _load_entry_vectors, _load_enzyme_collections, _load_experiment_projects, _load_experiments, _load_features, _load_gels, _load_hmm_db_catalog, _load_library, _load_parts_bin, _load_primer_collections, _load_primers, _load_protein_motifs, _normalise_hmm_db_entry, _sanitize_hmm_db_id, _sanitize_hmm_db_url, _save_custom_enzymes, _save_custom_grammars, _save_enzyme_collections, _save_experiment_projects, _save_experiments, _save_features, _save_gels, _save_hmm_db_catalog, _save_parts_bin, _save_primers, _save_protein_motifs, _search_collections_library, _set_active_primer_collection_name, _set_entry_vector, _set_setting, _typed_clone)
from splicecraft_experiments import (_new_experiment_id, _normalise_experiment_entry, _sanitize_experiment_id)
from splicecraft_fileio import (_PLASMIDSAURUS_ZIP_MAX_BYTES, _export_commercialsaas_dna, _export_embl_to_path, _list_gbk_members_in_zip, _parse_commercialsaas_history)
from splicecraft_gels import (_new_gel_id, _normalise_gel_entry)
from splicecraft_history import (_HISTORY_NODE_MAX_DEPTH, _HISTORY_NODE_MAX_NODES)
from splicecraft_logging import (_log, _log_event)
from splicecraft_net import (_sanitize_accession)
from splicecraft_persistence import (_safe_file_size_check, _safe_load_json)
from splicecraft_primer import (_mut_design_inner, _mut_design_outer, _scrub_design, _scrub_qc_primers, _scrub_qc_verify)
from splicecraft_record import (_gb_text_to_record, _normalize_primer_seq)
from splicecraft_search import (_delete_hmm_db_files, _hmm_db_acquire_download_slot, _hmm_db_perform_download, _hmm_db_pressed, _hmm_db_release_download_slot)
from splicecraft_seqanalysis import (_classify_part_from_plasmid, _find_orfs)
from splicecraft_util import (_PLASMID_STATUS_VALUES, _check_export_extension, _feat_bounds, _feat_label, _normalize_collection_name, _notify_save_failure, _sanitize_feat_type, _sanitize_gel_id, _sanitize_label, _sanitize_note, _sanitize_path, _scrub_path)
from splicecraft_widgets import (_PLASMID_STATUS_COLORS)


def _custom_enzyme_meta(name: str) -> "dict | None":
    """Return the full custom-enzyme dict (incl. type, supplier) for
    ``name`` if present, else None. Used by the modal to show the
    extra columns."""
    if not isinstance(name, str) or not name:
        return None
    for entry in _load_custom_enzymes():
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    return None


def _check_agent_read_path_ancestors(path: Path) -> "str | None":
    """Tighten read-endpoint path validation by walking every parent
    component for symlinks. Mirrors the sweep-#4 hardening in
    ``_check_agent_write_path`` but for read endpoints (Plasmidsaurus
    zip ingestion, etc.).

    Returns an error message string when an ancestor symlink would
    redirect the read; None when safe. Sweep #26 (2026-05-23) —
    closes the gap audit M8 flagged: read endpoints rejected the
    target's `~user` expansion but a parent like `Documents/zips/`
    being a symlink to `/etc` would still be followed silently by
    the downstream ``os.open``.

    The path itself does NOT need to exist — symlink refusal applies
    only to existing components.
    """
    try:
        parent = path.parent
        if not parent.exists():
            # Nothing to redirect through if it doesn't exist yet.
            return None
        resolved_parent = parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return f"could not resolve parent directory: {exc}"
    try:
        lexical_parent = parent.absolute()
    except OSError as exc:
        return f"could not normalise parent directory: {exc}"
    if str(resolved_parent) != str(lexical_parent):
        return (
            f"parent path resolves through a symlink: "
            f"{lexical_parent!s} → {resolved_parent!s}"
        )
    return None


def _check_agent_write_path(path: Path) -> "str | None":
    """Tighter validation for agent write endpoints (`export-*`,
    `save`, etc.). Returns an error message string when the path is
    rejected; None when safe to write.

    Rejects:
    * Symlinks at the destination — an agent shouldn't get to write
      through a pre-placed symlink to `/etc/passwd` or similar.
    * Existing symlinks in ANY parent component up to root (TOCTOU
      defense — a racing process can't redirect the write via a
      grandparent symlink either).
    * Paths whose parent doesn't exist (forces the user to mkdir
      first rather than us auto-creating arbitrary directories).

    Audit hardening 2026-05-14: previously the agent's export
    endpoints used `_sanitize_path` only, which expands `~` and does
    nothing else — symlink-as-destination was unprotected.

    Audit sweep #4 2026-05-15: previously this only checked the
    immediate parent. A symlink at any deeper ancestor (e.g.
    `/home/<user>/Documents` → `/etc`) could redirect every write
    under it. Walk the full chain via `resolve()` so the canonical
    absolute path is what we compare; an ancestor symlink would
    yield a `resolve()` result differing from the lexical path,
    flagging the redirect.
    """
    if path.is_symlink():
        return f"refusing to write through symlink at {path!s}"
    parent = path.parent
    if not parent.exists():
        return f"parent directory does not exist: {parent!s}"
    # Resolve the parent through every symlink hop. If the result
    # differs from the lexical absolute path (modulo `..` / `.`
    # collapsing), some ancestor is a symlink.
    try:
        resolved_parent = parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return f"could not resolve parent directory: {exc}"
    # Walk every ancestor segment under the parent and lstat
    # each. `parent.resolve()` follows symlinks, so if any
    # intermediate component IS a symlink, `lexical_parent` and
    # `resolved_parent` diverge. Refuse the divergence outright —
    # the user can re-target through the resolved path if they
    # actually meant to write there.
    try:
        lexical_parent = parent.absolute()
    except OSError as exc:
        return f"could not normalise parent directory: {exc}"
    if str(resolved_parent) != str(lexical_parent):
        return (
            f"parent path resolves through a symlink: "
            f"{lexical_parent!s} → {resolved_parent!s}"
        )
    # Per-segment lstat as defense in depth — `resolve()` already
    # catches symlinks via the divergence check above, but an
    # attacker swapping a regular dir for a symlink between the
    # resolve() and the open() (TOCTOU race) would slip through.
    # Walking each ancestor with lstat at least narrows the race
    # window to the open() itself.
    cur = parent
    seen: set = set()
    while True:
        try:
            if cur.is_symlink():
                return (
                    f"ancestor directory is a symlink: {cur!s}"
                )
        except OSError:
            # A permission error mid-walk is a refusal — we can't
            # tell whether an ancestor is safe.
            return f"could not stat ancestor: {cur!s}"
        if cur.parent == cur or str(cur) in seen:
            break
        seen.add(str(cur))
        cur = cur.parent
    return None


def _coerce_int(value, *, name: str = "value") -> "int | str":
    """Type-safe int coercion for agent-API JSON payloads.

    Returns the integer on success, or a human-readable error message
    (string) on failure. Accepts ``bool`` / ``int`` / finite ``float``
    / digit ``str``. Rejects ``None`` / dict / list / NaN / +-Inf —
    all of which would either AttributeError on a downstream `.get`
    or raise ``OverflowError`` on a naked ``int(value)`` (the case
    that bit us when an agent sent ``{"max_hits": Infinity}`` and a
    downstream ``int(...)`` blew up before our range check).

    The return shape is ``int | str`` rather than the older
    ``tuple[int | None, str | None]`` so that a caller-side
    ``isinstance(result, str)`` guard narrows the value to ``int``
    automatically — no separate ``assert value is not None`` needed
    and no tuple unpacking that loses pyright's discriminated-union
    narrowing.
    """
    import math as _m
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not _m.isfinite(value):
            return f"{name!r} must be a finite number"
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return f"{name!r} must be an integer"
    return f"{name!r} must be an integer"


def _sanitize_bases(s: "str | None", *,
                     max_len: int = 1_000_000) -> "tuple[str, str | None]":
    """Validate IUPAC DNA. Returns ``(cleaned, error_msg)``;
    ``error_msg`` is None on success. Caps at 1 MB to keep
    `_rebuild_record_with_edit` from hanging on adversarial input —
    matches the agent-API body cap so the whole request would have
    been rejected upstream anyway, but the cap-check here lets non-
    HTTP callers (modal `_gather`) reuse the same bound."""
    if s is None:
        return "", "missing 'bases'"
    s = str(s).strip().upper()
    if len(s) > max_len:
        return s, f"'bases' too long ({len(s)} > {max_len})"
    invalid = [c for c in s if c not in "ACGTNRYWSMKBDHV"]
    if invalid:
        return s, f"non-IUPAC characters: {''.join(sorted(set(invalid)))!r}"
    return s, None


def _agent_endpoint(name: str, *, write: bool = False):
    """Decorator: register a handler at `/<name>`.
    Handlers take `(app, payload)` and return either a `dict` (200) or
    `(dict, status_code)`. `write=True` flags state-mutating endpoints —
    these require the bearer token AND refuse if `app._unsaved` is True
    (unless the payload has `{"force": true}`)."""
    def deco(fn):
        _state._AGENT_HANDLERS[name] = (fn, write)
        return fn
    return deco


def _agent_save_or_500(save_fn, label: str = "library"):
    """Run `save_fn()`; on OSError/RuntimeError, return
    ``({"error": "save failed: ..."}, 500)`` so the handler can
    propagate. Returns None on success — caller pattern is
    ``err = _agent_save_or_500(...); if err: return err``.

    Per sacred invariant #7, `_safe_save_json` re-raises on disk
    failure so callers can surface; agent endpoints used to let those
    exceptions bubble up as generic 500s with no actionable detail.
    Routes through `_notify_save_failure` so the in-process UI user
    also sees the failure toast (the agent path and the GUI share an
    `app` object).
    """
    try:
        save_fn()
    except (OSError, RuntimeError) as exc:
        _notify_save_failure(_state._LIVE_APP_REF.get(), label, exc)
        return ({"error": f"save failed for {label}: {exc}"}, 500)
    return None


def _agent_dirty_guard(app, payload):
    """Return None if writes may proceed, else (error_dict, 409). The
    `force` field in the payload (or `?force=1` in the query, applied
    by the request handler) overrides the dirty check."""
    if getattr(app, "_unsaved", False) and not bool(payload.get("force")):
        return ({"error":
                  "unsaved changes — pass {\"force\": true} to override",
                  "dirty": True}, 409)
    return None


@_agent_endpoint("get-sequence")
def _h_get_sequence(app, payload):
    """Return DNA from `[start, end)`. Body: ``{start, end, bottom?}``.
    `bottom: true` returns the reverse-complement (5'→3' on the
    bottom strand). Wrap-aware: `end < start` is interpreted as a
    span that wraps the origin."""
    rec = getattr(app, "_current_record", None)
    if rec is None:
        return ({"error": "no plasmid loaded"}, 422)
    seq = str(rec.seq).upper()
    n   = len(seq)
    try:
        start = int(payload["start"])
        end   = int(payload["end"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return ({"error": "missing or invalid 'start'/'end'"}, 400)
    if not (0 <= start <= n) or not (0 <= end <= n):
        return ({"error": f"start/end out of range [0, {n}]"}, 400)
    if end >= start:
        sub = seq[start:end]
    else:
        sub = seq[start:] + seq[:end]
    if bool(payload.get("bottom")):
        sub = _rc(sub)
    return {
        "ok":     True,
        "start":  start,
        "end":    end,
        "bottom": bool(payload.get("bottom")),
        "length": len(sub),
        "seq":    sub,
    }


@_agent_endpoint("fold-rna")
def _h_fold_rna(app, payload):
    """Fold a sequence to its minimum-free-energy RNA secondary structure.
    Body: ``{sequence}`` (alias ``{seq}``). DNA ``T`` is read as RNA
    ``U``. Returns ``{ok, structure, dg, length}`` — `structure` is the
    dot-bracket MFE secondary structure and `dg` the free energy in
    kcal/mol. Pure-Python Turner-2004 nearest-neighbor model (no external
    dependency); inputs must be unambiguous A/C/G/U(T) and within the
    folding length cap, else 400 with the reason."""
    seq = payload.get("sequence")
    if seq is None:
        seq = payload.get("seq")
    if not isinstance(seq, str):
        return ({"error": "missing or non-string 'sequence'"}, 400)
    try:
        structure, dg = _rna_fold(seq)
    except ValueError as exc:
        return ({"error": str(exc)}, 400)
    return {
        "ok":        True,
        "structure": structure,
        "dg":        round(dg, 2),
        "length":    len(structure),
    }


@_agent_endpoint("cofold-rna")
def _h_cofold_rna(app, payload):
    """Bound-state heterodimer ΔG of two strands. Body: ``{seq_a, seq_b}``.
    Returns ``{ok, dg}`` — the free energy (kcal/mol) of strand B bound to
    strand A (e.g. a 16S anti-SD : mRNA hybrid; DNA ``T`` read as ``U``).
    Pure-Python Turner-2004 cofold (no external dependency); ambiguous /
    over-length input → 400 with the reason."""
    a = payload.get("seq_a")
    b = payload.get("seq_b")
    if not isinstance(a, str) or not isinstance(b, str):
        return ({"error": "missing or non-string 'seq_a' / 'seq_b'"}, 400)
    try:
        dg = _rna_cofold(a, b)
    except ValueError as exc:
        return ({"error": str(exc)}, 400)
    return {"ok": True, "dg": round(dg, 2)}


@_agent_endpoint("rbs-strength")
def _h_rbs_strength(app, payload):
    """Relative E. coli translation-initiation strength of a ribosome
    binding site. Body: ``{mrna, start}`` — `start` is the 0-based index of
    the start codon (DNA ``T`` read as ``U``). Returns ``{ok, dg_total,
    dg_mrna, dg_hybrid, spacing, rel_strength}``. `rel_strength` is RELATIVE
    (only ratios between RBSs are meaningful — a biophysically-grounded
    ranking score, not an absolute rate). Bad input → 400."""
    mrna = payload.get("mrna")
    if not isinstance(mrna, str):
        return ({"error": "missing or non-string 'mrna'"}, 400)
    if isinstance(payload.get("start"), bool):    # JSON true/false ≠ an index
        return ({"error": "'start' must be an integer, not a boolean"}, 400)
    try:
        start = int(payload["start"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return ({"error": "missing or invalid 'start' (0-based int)"}, 400)
    try:
        result = _rbs_strength(mrna, start)
    except ValueError as exc:
        return ({"error": str(exc)}, 400)
    return {"ok": True, **result}


@_agent_endpoint("design-rbs")
def _h_design_rbs(app, payload):
    """Reverse-design a 5'UTR (Shine-Dalgarno + spacer) for a target
    relative RBS strength. Body: ``{cds, target}`` — `cds` begins with the
    start codon, `target` is a non-negative relative strength (on the
    `rbs-strength` scale); optional ``{upstream}`` (5' context). Returns
    ``{ok, utr, full, sd, spacing, rel_strength, dg_total,
    achievable_min, achievable_max, on_target}`` — the design closest to
    the target (nearest achievable + `on_target=false` if out of range).
    Runs ~80 fold/cofold evaluations, so it blocks for a few seconds. Bad
    input → 400."""
    cds = payload.get("cds")
    if not isinstance(cds, str):
        return ({"error": "missing or non-string 'cds'"}, 400)
    target = payload.get("target")
    if not isinstance(target, (int, float)) or isinstance(target, bool):
        return ({"error": "missing or invalid 'target' (non-negative number)"}, 400)
    kw = {}
    up = payload.get("upstream")
    if isinstance(up, str):
        kw["upstream"] = up
    try:
        result = _rbs_design(cds, target, **kw)
    except ValueError as exc:
        return ({"error": str(exc)}, 400)
    return {"ok": True, **result}


@_agent_endpoint("assemble-operon")
def _h_assemble_operon(app, payload):
    """Assemble a contiguous bacterial operon from CDSs, each RBS
    reverse-designed (context-aware) to its target relative strength.
    Body: ``{genes: [{cds, target_strength, name?}, ...]}`` + optional
    ``{promoter, terminator}`` (DNA). Returns ``{ok, sequence, layout,
    genes}`` — `sequence` is DNA; `layout` tiles it (promoter / rbs / cds /
    terminator); `genes` reports target / achieved rel_strength /
    on_target. A target unreachable in its assembled context yields the
    nearest achievable + on_target=false. Bad input → 400."""
    genes = payload.get("genes")
    if not isinstance(genes, list) or not genes:
        return ({"error": "missing or empty 'genes' list"}, 400)
    kw = {}
    for key in ("promoter", "terminator"):
        val = payload.get(key)
        if isinstance(val, str):
            kw[key] = val
    try:
        result = _assemble_operon(genes, **kw)
    except (ValueError, TypeError) as exc:
        return ({"error": str(exc)}, 400)
    return {"ok": True, **result}


_EXPORT_DNA_EXTS     = (".dna",)


_EXPORT_EMBL_EXTS    = (".embl",)


@_agent_endpoint("export-commercialsaas", write=True)
def _h_export_commercialsaas(app, payload):
    """Write the current record to `path` as CommercialSaaS `.dna`.
    Body: ``{path}``. Requires the loaded record to be in the active
    library (`.dna` writer keys off the library entry's id / sidecar);
    returns 422 otherwise. Splice mode (preserves all original packets
    + updated history) when a sidecar exists; from-scratch mode
    (sequence + features + notes + history only — cosmetic packets
    omitted) otherwise. Mirrors `action_export_commercialsaas`."""
    rec = getattr(app, "_current_record", None)
    if rec is None:
        return ({"error": "no plasmid loaded"}, 422)
    path = _sanitize_path(payload.get("path"))
    if path is None:
        return ({"error": "missing 'path'"}, 400)
    if (ext_err := _check_export_extension(
            path, _EXPORT_DNA_EXTS, "CommercialSaaS .dna")) is not None:
        return ({"error": ext_err}, 400)
    err = _check_agent_write_path(path)
    if err is not None:
        return ({"error": err}, 403)
    rec_id = getattr(rec, "id", "") or ""
    # Sweep #26: targeted clone via `_find_library_entry_by_id`.
    entry = _find_library_entry_by_id(rec_id) if rec_id else None
    if entry is None:
        return ({"error": (
            "loaded plasmid is not in the library; .dna export keys "
            "off the library entry's id / sidecar. Add to library "
            "first, or use export-genbank."
        )}, 422)
    try:
        out_path = _export_commercialsaas_dna(entry, path)
    except (OSError, ValueError) as exc:
        return ({"error": f"export failed: {_scrub_path(str(exc))}"}, 500)
    try:
        size = Path(out_path).stat().st_size
    except OSError:
        size = 0
    return {"ok": True, "path": str(out_path), "bytes": size}


@_agent_endpoint("export-embl", write=True)
def _h_export_embl(app, payload):
    """Write the current record to `path` as EMBL flatfile. Body: ``{path}``.
    EMBL is a near-equivalent of GenBank — same feature model, different
    text layout. Useful for tools that consume EMBL directly (ENA
    submissions, some annotation pipelines). Returns ``{ok, path, bp,
    features}``."""
    rec = getattr(app, "_current_record", None)
    if rec is None:
        return ({"error": "no plasmid loaded"}, 422)
    path = _sanitize_path(payload.get("path"))
    if path is None:
        return ({"error": "missing 'path'"}, 400)
    if (ext_err := _check_export_extension(
            path, _EXPORT_EMBL_EXTS, "EMBL")) is not None:
        return ({"error": ext_err}, 400)
    err = _check_agent_write_path(path)
    if err is not None:
        return ({"error": err}, 403)
    try:
        result = _export_embl_to_path(rec, path)
    except (OSError, ValueError) as exc:
        return ({"error": f"export failed: {_scrub_path(str(exc))}"}, 500)
    return {"ok": True, **result}


@_agent_endpoint("list-library")
def _h_list_library(app, payload):
    """Plasmid library entries. Returns name, id, length (bp),
    n_features, topology, source for each. Subset of the on-disk JSON
    tuned for an agent's "what plasmids do I have" question.

    Sweep #26 (2026-05-25): fixed projection bug. Pre-fix the handler
    read non-existent JSON fields (`sequence`, `features`, `topology`,
    `source_path`) and returned `{length: 0, n_features: 0, topology:
    "", source_path: ""}` for every entry — the on-disk schema uses
    `size`, `n_feats`, `source` and topology lives in the GenBank
    LOCUS line. Now projects the real fields and pulls topology out
    of the LOCUS line when available (defaults to "circular" — the
    overwhelming majority of library entries).
    """
    out: list[dict] = []
    # Sweep #25 (2026-05-23): read-only iterator — pre-fix paid full
    # library deepcopy just to project six fields. Build the output
    # list from immutable reads.
    for e in _iter_library_readonly():
        # Topology: scan the LOCUS line for "circular" or "linear".
        # Library entries omit explicit topology in the JSON — we get
        # it from the embedded gb_text. Default to "circular" since
        # ~99% of stored plasmids are circular and assembling that
        # default avoids a regex pass on every row.
        topology = "circular"
        gb_text = e.get("gb_text") or ""
        if gb_text:
            head = gb_text[:200]
            if "linear" in head.lower():
                topology = "linear"
        out.append({
            "name":        e.get("name", ""),
            "id":          e.get("id", ""),
            "length":      int(e.get("size") or 0),
            "n_features":  int(e.get("n_feats") or 0),
            "topology":    topology,
            "source":      e.get("source", "") or "",
        })
    return {"library": out, "count": len(out)}


@_agent_endpoint("list-collections")
def _h_list_collections(app, payload):
    """Plasmid collection buckets. Returns name + plasmid count for
    each collection, and which one is currently active."""
    # Sweep #25 (2026-05-23): read-only iterator — pre-fix paid full
    # ~160 MB collections.json deepcopy just to project name +
    # n_plasmids per collection.
    cols = _iter_collections_readonly()
    active = _get_active_collection_name()
    return {
        "active": active,
        "collections": [
            {"name": c.get("name", ""),
             "n_plasmids": len(c.get("plasmids", []) or [])}
            for c in cols
        ],
    }


@_agent_endpoint("search-library")
def _h_search_library(app, payload):
    """Cross-collection plasmid name search. Body: ``{query?: str,
    limit?: int}``. Empty `query` returns the first `limit` plasmids
    across all collections (default `limit` = 200, capped at 1000).
    Each match is ``{collection, name, id, size, status, n_feats}``.

    Use `set-active-collection` + `load-entry` (or the GUI
    equivalent) to actually load a hit. The fuzzy matcher is the
    same one the library panel's in-collection search uses, so a
    query of `puc` matches `pUC19`."""
    query = payload.get("query")
    if query is not None and not isinstance(query, str):
        return ({"error": "'query' must be a string"}, 400)
    # Cap query length to 200 chars so an agent's runaway prompt
    # can't pin the fuzzy matcher (audit hardening 2026-05-14).
    if query is not None and len(query) > 200:
        return ({"error": "'query' exceeds 200-char cap"}, 400)
    raw_limit = payload.get("limit", 200)
    limit = _coerce_int(raw_limit, name="limit")
    if isinstance(limit, str):
        return ({"error": limit}, 400)
    limit = max(1, min(1000, limit))
    matches = _search_collections_library(query or "", limit=limit)
    return {"matches": matches, "count": len(matches)}


@_agent_endpoint("list-plasmidsaurus-members")
def _h_list_plasmidsaurus_members(app, payload):
    """List the GenBank-format members of a Plasmidsaurus result
    zip. Body: ``{path}``. Returns ``{members: [{name, size}, ...]}``.

    Cap-protected — zips above `_PLASMIDSAURUS_ZIP_MAX_BYTES`
    (500 MB), members above `_PLASMIDSAURUS_MEMBER_MAX_BYTES` and
    listings beyond `_PLASMIDSAURUS_MAX_MEMBERS` are refused or
    skipped to keep the picker snappy and resistant to malformed
    archives. Symlinks at the path are rejected via
    `_safe_file_size_check` upstream.

    Read-only — does not extract or align anything; the agent uses
    the returned member name with `align-plasmidsaurus-zip` to run
    the actual alignment.
    """
    raw_path = payload.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return ({"error": "missing 'path'"}, 400)
    path = _sanitize_path(raw_path)
    if path is None:
        return ({"error": "could not sanitize 'path'"}, 400)
    # Sweep #26 (2026-05-23): defense-in-depth ancestor symlink walk.
    # Pre-sweep an attacker-placed symlink at any parent (e.g.
    # `~/Documents` → `/etc`) was silently followed by the
    # downstream `os.open`.
    anc_err = _check_agent_read_path_ancestors(path)
    if anc_err is not None:
        _log.warning("agent list-plasmidsaurus-members: %s", anc_err)
        return ({"error": "zip rejected (see splicecraft log)"}, 400)
    # Sweep #25 (2026-05-23): collapse path-shape errors to a uniform
    # 400 so the differentiated error responses don't act as a
    # filesystem-state oracle (pre-fix an unauthenticated caller
    # could probe arbitrary paths via "not found" / "could not open
    # zip" / "zip too large" responses). Logs still carry detail.
    ok, reason = _safe_file_size_check(
        path, _PLASMIDSAURUS_ZIP_MAX_BYTES, "Plasmidsaurus zip",
    )
    if not ok:
        _log.warning("agent list-plasmidsaurus-members: rejected (%s): %s",
                     reason or "unsafe", path)
        return ({"error": "zip rejected (see splicecraft log)"}, 400)
    try:
        members = _list_gbk_members_in_zip(path)
    except (ValueError, OSError) as exc:
        _log.warning(
            "agent list-plasmidsaurus-members: walk failed (%s)", exc,
        )
        return ({"error": "zip rejected (see splicecraft log)"}, 400)
    return {"ok": True, "path": str(path),
            "members": members, "count": len(members)}


@_agent_endpoint("find-orfs")
def _h_find_orfs(app, payload):
    """Six-frame ORF scan over the loaded record. Body:
    ``{min_aa?: int, include_alt_starts?: bool}``. Defaults: 30 aa,
    ATG only. Returns ``{orfs: [{start, end, strand, length_aa, aa_seq}, ...]}``
    sorted by length descending. ORFs that cross the origin on a
    circular plasmid are reported with `end < start` — the same
    wrap-feature convention used elsewhere."""
    rec = getattr(app, "_current_record", None)
    if rec is None:
        return ({"error": "no plasmid loaded"}, 422)
    min_aa = _coerce_int(payload.get("min_aa", 30), name="min_aa")
    if isinstance(min_aa, str):
        return ({"error": min_aa}, 400)
    if min_aa < 1:
        return ({"error": "'min_aa' must be ≥ 1"}, 400)
    alt = bool(payload.get("include_alt_starts", False))
    seq = str(rec.seq) if rec.seq is not None else ""
    if not seq:
        return {"orfs": [], "count": 0}
    annotations = getattr(rec, "annotations", None) or {}
    is_circular = annotations.get("topology") == "circular"
    orfs = _find_orfs(
        seq,
        min_aa=min_aa,
        include_alt_starts=alt,
        circular=is_circular,
    )
    return {"orfs": orfs, "count": len(orfs)}


@_agent_endpoint("list-codon-tables")
def _h_list_codon_tables(app, payload):
    """Available codon usage tables. Returns name, taxid, source
    (builtin/kazusa/user) and date added for each entry, plus the
    `active_taxid` field showing which is currently the persisted
    default (read by SynthesisScreen's `_init_codon_table`). Use
    the taxid as the `table` arg to ``optimize-protein``."""
    return {
        "tables": [
            {"name":   e.get("name", "?"),
             "taxid":  e.get("taxid", ""),
             "source": e.get("source", "user"),
             "added":  e.get("added", "")}
            for e in _codon_tables_load()
        ],
        "active_taxid": str(
            _get_setting("active_codon_table", "") or "",
        ),
    }


@_agent_endpoint("set-active-codon-table", write=True)
def _h_set_active_codon_table(app, payload):
    """Persist the active codon-table preference. Body:
    ``{taxid: str}`` — pass empty string to clear (Synthesis then
    falls back to the first registry entry, K12 by convention).

    Sweep #20: the Synthesis protein tab honors this setting on
    open via `_init_codon_table`, so an agent pre-setting the
    user's preferred organism (e.g. "83333" for E. coli K12,
    "559292" for S. cerevisiae) means the next time the user
    opens Synthesis they're already in the right table. Unknown
    taxids are rejected 404; empty string is accepted (clears)."""
    taxid_raw = payload.get("taxid")
    if taxid_raw is None:
        return ({"error": "missing 'taxid'"}, 400)
    if not isinstance(taxid_raw, str):
        return ({"error": "'taxid' must be a string"}, 400)
    taxid = taxid_raw.strip()
    if taxid:
        # Verify the taxid resolves to a real registry entry —
        # otherwise the setting is dead weight.
        if _codon_tables_get(taxid) is None:
            return ({"error":
                      f"no codon table with taxid {taxid!r}; "
                      f"see list-codon-tables"}, 404)
    _set_setting("active_codon_table", taxid)
    _log_event(
        "settings.changed",
        key="active_codon_table",
        value=taxid, via="agent",
    )
    return {"ok": True, "active_taxid": taxid}


@_agent_endpoint("optimize-protein")
def _h_optimize_protein(app, payload):
    """Codon-optimize a 1-letter AA sequence to DNA using a codon
    table from the registry. Body: ``{protein, table?, stops?}`` where
    `table` is a taxid (defaults to E. coli K12 = 83333) and `stops`
    (0–3, default 1) is how many stop codons to append when `protein`
    has no trailing '*'. A trailing '*' run in `protein` (1–3) is honored
    verbatim and overrides `stops`; a single stop is TAA, 2–3 are
    frequency-matched to the table's stop usage. Read-only — doesn't
    touch the loaded record."""
    protein = _sanitize_label(payload.get("protein"),
                                max_len=10_000).upper()
    if not protein:
        return ({"error": "missing 'protein'"}, 400)
    invalid_aa = [c for c in protein if c not in "ACDEFGHIKLMNPQRSTVWY*"]
    if invalid_aa:
        return ({"error":
                  f"non-canonical amino acids in 'protein': "
                  f"{''.join(sorted(set(invalid_aa)))!r}"}, 400)
    stops_raw = payload.get("stops", 1)
    try:
        stops = int(stops_raw)
    except (TypeError, ValueError):
        return ({"error": "'stops' must be an integer between 0 and 3"}, 400)
    if not 0 <= stops <= 3:
        return ({"error": "'stops' must be between 0 and 3"}, 400)
    taxid = _sanitize_accession(payload.get("table")) or "83333"
    entry = _codon_tables_get(taxid)
    if entry is None:
        return ({"error": f"no codon table with taxid {taxid!r}; "
                          f"see list-codon-tables"}, 404)
    try:
        dna = _codon_optimize(protein, entry["raw"], stops=stops)
    except ValueError as exc:
        return ({"error": str(exc)}, 400)
    return {
        "ok":     True,
        "protein":      protein,
        "table":        taxid,
        "table_name":   entry.get("name", "?"),
        "dna":          dna,
        "length":       len(dna),
        "n_codons":     len(dna) // 3,
    }


@_agent_endpoint("list-plasmid-statuses")
def _h_list_plasmid_statuses(app, payload):
    """Return the canonical workflow-status vocabulary. Discoverability
    helper so agents don't have to hard-code `DESIGNING`/`CLONING`/
    `SEQUENCING`/`VERIFIED` strings."""
    return {
        "ok":       True,
        "statuses": list(_PLASMID_STATUS_VALUES),
        "colors":   dict(_PLASMID_STATUS_COLORS),
    }


@_agent_endpoint("list-entry-vectors")
def _h_list_entry_vectors(app, payload):
    """Return every grammar that currently has an assigned entry vector.
    Each item carries ``{grammar_id, name, size, source}`` — `gb_text`
    is omitted from the listing to keep the response small; fetch it
    via `get-entry-vector` for a specific grammar."""
    out = []
    for e in _load_entry_vectors():
        out.append({
            "grammar_id": e.get("grammar_id", ""),
            "name":       e.get("name", ""),
            "size":       e.get("size", 0),
            "source":     e.get("source", ""),
        })
    return {"ok": True, "entry_vectors": out}


@_agent_endpoint("get-entry-vector")
def _h_get_entry_vector(app, payload):
    """Return the entry vector for a grammar. Body: ``{grammar_id}``.
    Includes the full `gb_text` in the response so the agent can parse
    + render it without a follow-up call. Returns `null` for `vector`
    if the grammar has no assigned vector."""
    gid = payload.get("grammar_id")
    if not isinstance(gid, str) or not gid:
        return ({"error": "missing or non-string 'grammar_id'"}, 400)
    vec = _get_entry_vector(gid)
    return {"ok": True, "grammar_id": gid, "vector": vec}


def _agent_grammar_dict(payload: dict) -> "dict | str":
    """Coerce + validate a payload describing a cloning grammar.
    Returns the cleaned grammar dict or a string error message.

    Shape-checks each field type without auto-defaulting missing
    ones; the agent must pass everything it wants persisted. Tighter
    than the UI because we don't have an editing modal to fix up
    on save."""
    gid = payload.get("id")
    if not isinstance(gid, str) or not gid.strip():
        return "missing or non-string 'id'"
    if gid in _BUILTIN_GRAMMARS:
        return f"refusing to overwrite built-in grammar {gid!r}"
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return "missing or non-string 'name'"
    enzyme = payload.get("enzyme")
    if not isinstance(enzyme, str) or not enzyme.strip():
        return "missing or non-string 'enzyme'"
    site = payload.get("site")
    if not isinstance(site, str) or not site.strip():
        return "missing or non-string 'site'"
    spacer = payload.get("spacer", "")
    pad = payload.get("pad", "")
    if not isinstance(spacer, str) or not isinstance(pad, str):
        return "'spacer' and 'pad' must be strings"
    forb = payload.get("forbidden_sites", {})
    if not isinstance(forb, dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in forb.items()):
        return "'forbidden_sites' must be {enzyme: site} string→string"
    positions = payload.get("positions")
    if not isinstance(positions, list) or not positions:
        return "missing non-empty 'positions' list"
    cleaned_positions: list[dict] = []
    for i, pos in enumerate(positions):
        if not isinstance(pos, dict):
            return f"positions[{i}] is not a dict"
        for k in ("name", "type", "oh5", "oh3"):
            if not isinstance(pos.get(k), str):
                return f"positions[{i}] missing string field {k!r}"
        cleaned_positions.append({
            "name": _sanitize_label(pos["name"]),
            "type": _sanitize_label(pos["type"]),
            "oh5":  pos["oh5"].upper(), "oh3": pos["oh3"].upper(),
            "color": _sanitize_label(pos.get("color")) or "white",
        })
    coding_types = payload.get("coding_types") or []
    if not isinstance(coding_types, list) or not all(
            isinstance(x, str) for x in coding_types):
        return "'coding_types' must be a list of strings"
    type_to_insdc = payload.get("type_to_insdc") or {}
    if not isinstance(type_to_insdc, dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in type_to_insdc.items()):
        return "'type_to_insdc' must be {part_type: insdc_type} strings"
    return {
        "id":              gid.strip(),
        "name":            _sanitize_label(name),
        "enzyme":          _sanitize_label(enzyme),
        "level_up_enzyme": (payload.get("level_up_enzyme")
                              if isinstance(payload.get("level_up_enzyme"), str)
                              else ""),
        "site":            site.strip().upper(),
        "spacer":          spacer,
        "pad":             pad,
        "forbidden_sites": dict(forb),
        "positions":       cleaned_positions,
        "coding_types":    list(coding_types),
        "type_to_insdc":   dict(type_to_insdc),
        "catalog":         [],
        "editable":        True,
    }


@_agent_endpoint("list-grammars")
def _h_list_grammars(app, payload):
    """Return every grammar (built-in + custom). Each item carries
    ``{id, name, enzyme, level_up_enzyme, editable, n_positions,
    catalog_size}``. Fetch the full grammar via `get-grammar`."""
    out = []
    for gid, g in _all_grammars().items():
        out.append({
            "id":              gid,
            "name":            g.get("name", gid),
            "enzyme":          g.get("enzyme", ""),
            "level_up_enzyme": g.get("level_up_enzyme", ""),
            "editable":        bool(g.get("editable")),
            "n_positions":     len(g.get("positions") or []),
            "catalog_size":    len(g.get("catalog") or []),
        })
    return {"ok": True, "grammars": out}


@_agent_endpoint("get-grammar")
def _h_get_grammar(app, payload):
    """Return the full grammar dict for `grammar_id`. Body:
    ``{grammar_id}``."""
    gid = payload.get("grammar_id")
    if not isinstance(gid, str) or not gid:
        return ({"error": "missing or non-string 'grammar_id'"}, 400)
    grammars = _all_grammars()
    g = grammars.get(gid)
    if g is None:
        return ({"error": f"unknown grammar id {gid!r}"}, 404)
    return {"ok": True, "grammar": g}


@_agent_endpoint("create-grammar", write=True)
def _h_create_grammar(app, payload):
    """Create a new custom grammar. Body: every field in the schema
    listed above. Returns 400 on validation failure, 409 if a grammar
    with the same id already exists. Built-in ids are reserved."""
    g = _agent_grammar_dict(payload)
    if isinstance(g, str):
        return ({"error": g}, 400)
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        custom = _load_custom_grammars()
        if any(e.get("id") == g["id"] for e in custom):
            return ({"error": (
                f"grammar id {g['id']!r} already exists; use update-grammar"
            )}, 409)
        custom.append(g)
        if (err := _agent_save_or_500(
                lambda: _save_custom_grammars(custom),
                "cloning_grammars")) is not None:
            return err
    return {"ok": True, "grammar_id": g["id"]}


@_agent_endpoint("update-grammar", write=True)
def _h_update_grammar(app, payload):
    """Replace an existing custom grammar by id. Built-in ids are
    refused (mirrors the UI's `editable=False` gate)."""
    g = _agent_grammar_dict(payload)
    if isinstance(g, str):
        return ({"error": g}, 400)
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        custom = _load_custom_grammars()
        for i, e in enumerate(custom):
            if e.get("id") == g["id"]:
                # Preserve the existing catalog list — `_agent_grammar_dict`
                # always sets `catalog: []`, but the user might have built
                # one up via Parts Bin. Keep it unless the payload
                # explicitly clears it.
                if "catalog" not in payload:
                    g["catalog"] = list(e.get("catalog") or [])
                custom[i] = g
                if (err := _agent_save_or_500(
                        lambda: _save_custom_grammars(custom),
                        "cloning_grammars")) is not None:
                    return err
                return {"ok": True, "grammar_id": g["id"]}
    return ({"error": f"unknown grammar id {g['id']!r}"}, 404)


@_agent_endpoint("delete-grammar", write=True)
def _h_delete_grammar(app, payload):
    """Delete a custom grammar by id. Built-ins are refused. Also
    clears any entry vector bound to the grammar so a future
    create-grammar with the same id starts fresh."""
    gid = payload.get("grammar_id")
    if not isinstance(gid, str) or not gid:
        return ({"error": "missing or non-string 'grammar_id'"}, 400)
    if gid in _BUILTIN_GRAMMARS:
        return ({"error": (
            f"refusing to delete built-in grammar {gid!r}"
        )}, 400)
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        custom = _load_custom_grammars()
        new_list = [e for e in custom if e.get("id") != gid]
        if len(new_list) == len(custom):
            return ({"error": f"unknown grammar id {gid!r}"}, 404)
        if (err := _agent_save_or_500(
                lambda: _save_custom_grammars(new_list),
                "cloning_grammars")) is not None:
            return err
    # Also clear the bound entry vector if any. Failure here is
    # non-fatal — the orphan record gets pruned next save.
    try:
        _set_entry_vector(gid, None)
    except (OSError, RuntimeError):
        pass
    return {"ok": True, "grammar_id": gid}


@_agent_endpoint("list-primers")
def _h_list_primers(app, payload):
    """Return the primer library. Each item: ``{name, sequence,
    source, tm, status, type, date, notes}``. Sequence + notes can
    be long; clients on small terminals should paginate via the
    `limit` + `offset` body params."""
    raw_limit = payload.get("limit")
    raw_offset = payload.get("offset")
    # Sweep #35 (2026-05-26): clamp pagination to sane bounds and
    # reject JSON floats / booleans. Python's list slicing already
    # tolerates out-of-range slice indices, so pre-fix `int(1e308)`
    # didn't actually DoS, but it's tidier to validate at the
    # boundary than rely on slice clamping. Booleans pass
    # `isinstance(x, int)` in Python, so they're filtered out
    # explicitly.
    _LIST_PRIMERS_LIMIT_MAX = 10_000
    def _coerce_pagination(v, default: int, cap: int) -> int:
        if isinstance(v, bool) or not isinstance(v, int):
            return default
        return max(0, min(v, cap))
    limit = _coerce_pagination(
        raw_limit, default=1000, cap=_LIST_PRIMERS_LIMIT_MAX,
    )
    offset = _coerce_pagination(
        raw_offset, default=0, cap=_LIST_PRIMERS_LIMIT_MAX,
    )
    entries = _load_primers()
    sliced = entries[offset:offset + limit]
    out = []
    for e in sliced:
        out.append({
            "name":     e.get("name", ""),
            "sequence": e.get("sequence", ""),
            "source":   e.get("source", ""),
            "tm":       e.get("tm"),
            "status":   e.get("status", ""),
            "type":     e.get("type", ""),
            "date":     e.get("date", ""),
            "notes":    e.get("notes", ""),
        })
    return {"ok": True, "primers": out, "count": len(out),
            "total": len(entries)}


@_agent_endpoint("get-primer")
def _h_get_primer(app, payload):
    """Return a primer-library entry by exact (case-insensitive)
    sequence match. Body: ``{sequence}``. Returns 404 if no match
    — the agent should `list-primers` first to discover what's
    available."""
    seq = payload.get("sequence")
    if not isinstance(seq, str) or not seq.strip():
        return ({"error": "missing or non-string 'sequence'"}, 400)
    target = seq.strip().upper()
    for e in _load_primers():
        if (e.get("sequence") or "").upper() == target:
            return {"ok": True, "primer": e}
    return ({"error": "no primer with that sequence"}, 404)


_PRIMER_STATUS_VALUES: tuple = ("Designed", "Ordered", "Validated")


def _agent_primer_dict(payload: dict) -> "dict | str":
    """Coerce + validate a primer payload. Returns the cleaned dict
    or a string error. Used by both create and update paths.

    Sequence is the canonical identity field — `_dedupe_primers_by_sequence`
    treats two primers with the same uppercase sequence as duplicates
    and keeps the first.
    """
    name = _sanitize_label(payload.get("name"))
    if not name:
        return "missing or non-string 'name'"
    raw_seq = payload.get("sequence")
    if not isinstance(raw_seq, str) or not raw_seq.strip():
        return "missing or non-string 'sequence'"
    seq, seq_err = _sanitize_bases(raw_seq.upper())
    if seq_err:
        return f"'sequence' rejected: {seq_err}"
    if not seq:
        return "'sequence' empty after sanitisation"
    if len(seq) > 500:
        return "'sequence' too long (max 500 bp)"
    source = payload.get("source", "")
    if source is not None and not isinstance(source, str):
        return "'source' must be a string"
    status = payload.get("status", "Designed")
    if status and status not in _PRIMER_STATUS_VALUES:
        return (f"'status' must be one of {_PRIMER_STATUS_VALUES}")
    ptype = payload.get("type", "")
    if ptype is not None and not isinstance(ptype, str):
        return "'type' must be a string"
    notes = _sanitize_note(payload.get("notes", ""))
    tm = payload.get("tm")
    if tm is not None and not isinstance(tm, (int, float)):
        return "'tm' must be a number or null"
    return {
        "name":     name,
        "sequence": seq,
        "source":   _sanitize_label(source, max_len=300),
        "status":   status or "Designed",
        "type":     _sanitize_label(ptype),
        "tm":       tm,
        "notes":    notes or "",
        "date":     _datetime.now().strftime("%Y-%m-%d"),
    }


@_agent_endpoint("create-primer", write=True)
def _h_create_primer(app, payload):
    """Add a primer to the persistent library. Body: ``{name,
    sequence, source?, status?, type?, tm?, notes?}``. Duplicate
    sequences (case-insensitive) return 409 — use `update-primer`
    on the existing entry or pick a fresh sequence."""
    p = _agent_primer_dict(payload)
    if isinstance(p, str):
        return ({"error": p}, 400)
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        entries = _load_primers()
        target = p["sequence"]
        for e in entries:
            if (e.get("sequence") or "").upper() == target:
                return ({"error": (
                    f"primer with sequence {target!r} already exists "
                    f"(name {e.get('name', '?')!r}); use update-primer "
                    f"or delete-primer first."
                ), "existing_name": e.get("name", "")}, 409)
        entries.insert(0, p)  # MRU at index 0
        if (err := _agent_save_or_500(
                lambda: _save_primers(entries),
                "primers")) is not None:
            return err
    return {"ok": True, "name": p["name"], "sequence": p["sequence"]}


@_agent_endpoint("delete-primer", write=True)
def _h_delete_primer(app, payload):
    """Remove a primer from the library by exact sequence match.
    Body: ``{sequence}``. Returns 404 if no match. The library
    write triggers the standard `.bak` rotation, so a misclick
    can be recovered via Settings → Restore from backup."""
    seq = payload.get("sequence")
    if not isinstance(seq, str) or not seq.strip():
        return ({"error": "missing or non-string 'sequence'"}, 400)
    target = seq.strip().upper()
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        entries = _load_primers()
        new_list = [e for e in entries
                    if (e.get("sequence") or "").upper() != target]
        if len(new_list) == len(entries):
            return ({"error": f"no primer with sequence {target!r}"}, 404)
        if (err := _agent_save_or_500(
                lambda: _save_primers(new_list),
                "primers")) is not None:
            return err
    return {"ok": True, "removed": len(entries) - len(new_list)}


@_agent_endpoint("list-primer-collections")
def _h_list_primer_collections(app, payload):
    """Every named primer collection. Each item: ``{name, n_primers}``.
    The unnamed "default" collection (= top-level primers.json) is
    surfaced as ``{name: "", n_primers: <count>}`` if it has content."""
    colls = _load_primer_collections()
    out = []
    # Top-level primers — surfaced as the "default" collection.
    top = _load_primers()
    if top:
        out.append({"name": "", "n_primers": len(top)})
    for c in colls:
        out.append({
            "name":      c.get("name", "?"),
            "n_primers": len(c.get("primers", []) or []),
        })
    return {"ok": True, "primer_collections": out,
            "active": _get_active_primer_collection_name() or ""}


@_agent_endpoint("set-active-primer-collection", write=True)
def _h_set_active_primer_collection(app, payload):
    """Switch the active primer collection. Body: ``{name}`` where
    `name` is one of the collections returned by
    `list-primer-collections` (empty string = default top-level).
    Routes through `_set_active_primer_collection_name` so the
    mirror discipline (pitfall #10) keeps the live primers.json in
    sync with the chosen collection's contents."""
    name = payload.get("name")
    if name is None:
        return ({"error": "missing 'name' (use \"\" for default)"}, 400)
    if not isinstance(name, str):
        return ({"error": "'name' must be a string"}, 400)
    name = name.strip()
    valid_names = {c.get("name") for c in _load_primer_collections()}
    valid_names.add("")  # default sentinel
    if name not in valid_names:
        return ({"error": (
            f"unknown primer collection {name!r}; "
            f"valid: {sorted(n for n in valid_names if n)}"
        )}, 404)

    if (err := _agent_save_or_500(
            lambda: _set_active_primer_collection_name(name or None),
            "primer_collections")) is not None:
        return err
    return {"ok": True, "active": name}


@_agent_endpoint("list-hmm-databases")
def _h_list_hmm_databases(app, payload):
    """Every registered HMM database. Each item: ``{id, name, ready,
    builtin}`` (`ready` = pressed/usable on disk). `active` is the
    picked id used by hmmscan."""
    out = []
    for e in _load_hmm_db_catalog():
        eid = e.get("id", "")
        out.append({
            "id":      eid,
            "name":    e.get("name", eid),
            "ready":   _hmm_db_pressed(eid),
            "builtin": bool(e.get("builtin")),
        })
    return {"ok": True, "hmm_databases": out,
            "active": (_get_setting("hmm_db_active_id", "") or "")}


@_agent_endpoint("get-hmm-database")
def _h_get_hmm_database(app, payload):
    """Detail for one HMM database by `id`."""
    eid = payload.get("id")
    if not isinstance(eid, str) or not eid.strip():
        return ({"error": "missing 'id'"}, 400)
    eid = eid.strip()
    for e in _load_hmm_db_catalog():
        if e.get("id") == eid:
            return {"ok": True, "id": eid, "name": e.get("name", eid),
                    "ready": _hmm_db_pressed(eid),
                    "builtin": bool(e.get("builtin")),
                    "url": e.get("url", ""),
                    "version": e.get("version", "")}
    return ({"error": "hmm database not found"}, 404)


@_agent_endpoint("delete-hmm-database", write=True)
def _h_delete_hmm_database(app, payload):
    """Delete the DOWNLOADED files for an HMM database by `id`
    (un-downloads it; the catalog entry stays so it can be re-fetched).
    Files removal is L2-gated via `_delete_hmm_db_files`."""
    eid = payload.get("id")
    if not isinstance(eid, str) or not eid.strip():
        return ({"error": "missing 'id'"}, 400)
    eid = eid.strip()
    if eid not in {e.get("id") for e in _load_hmm_db_catalog()}:
        return ({"error": "hmm database not found"}, 404)
    try:
        removed = _delete_hmm_db_files(eid)
    except (OSError, RuntimeError) as exc:
        _log.exception("agent delete-hmm-database failed")
        return ({"error": f"delete failed: {exc}"}, 500)
    _log_event("hmm_db.delete", id=eid, files_removed=removed, via="agent")
    return {"ok": True, "id": eid, "files_removed": removed}


@_agent_endpoint("add-hmm-database", write=True)
def _h_add_hmm_database(app, payload):
    """Register a CUSTOM HMM database in the catalog — the headless
    equivalent of the GUI catalog modal's "Add" form
    (`HmmDbAddEditModal`). Body: ``{name, url, version_url?,
    description?}``.

    ``url`` must be a well-formed ``http(s)://`` link (no whitespace)
    pointing at a gzipped HMMER3 ``.hmm.gz``; ``http`` is downloaded only
    if the `hmm_db_allow_http` setting is on (enforced later, at download
    time). The id is derived from the name exactly as the form does
    (``name.replace(" ", "_").lower()`` → `_sanitize_hmm_db_id`); a
    colliding id (incl. a builtin) or a taken display name is refused
    (409). This REGISTERS the entry only — call `download-hmm-database`
    to fetch + index the files. Catalog write is L2-gated via
    `_save_hmm_db_catalog`."""
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return ({"error": "missing or empty 'name'"}, 400)
    name = name.strip()
    url = payload.get("url")
    if not isinstance(url, str):
        return ({"error": "missing 'url'"}, 400)
    url = url.strip()
    if _sanitize_hmm_db_url(url) is None:
        return ({"error": "invalid 'url' — must be a well-formed "
                 "http(s):// link with no whitespace"}, 400)
    vurl = payload.get("version_url", "")
    if not isinstance(vurl, str):
        return ({"error": "'version_url' must be a string"}, 400)
    vurl = vurl.strip()
    if vurl and _sanitize_hmm_db_url(vurl) is None:
        return ({"error": "invalid 'version_url' — must be http(s):// "
                 "or omitted"}, 400)
    desc = payload.get("description", "")
    if not isinstance(desc, str):
        return ({"error": "'description' must be a string"}, 400)
    entry_id = _sanitize_hmm_db_id(name.replace(" ", "_").lower())
    if entry_id is None:
        return ({"error": "name can't be reduced to a valid id "
                 "(needs letters / digits / `_` / `-`)"}, 400)
    if _find_hmm_db_entry(entry_id) is not None:
        return ({"error": f"an entry with id '{entry_id}' already exists"},
                409)
    if _hmm_db_name_taken(name):
        return ({"error": f"display name '{name}' is taken"}, 409)
    normalised = _normalise_hmm_db_entry({
        "id":          entry_id,
        "name":        name[:200],
        "url":         url,
        "version_url": vurl,
        "format":      "hmm-gz",
        "builtin":     False,
        "description": desc.strip(),
    })
    if normalised is None:
        return ({"error": "entry failed validation"}, 400)
    catalog = _load_hmm_db_catalog()
    catalog.append(normalised)
    err = _agent_save_or_500(
        lambda: _save_hmm_db_catalog(catalog), "HMM database catalog")
    if err:
        return err
    _log_event("hmm_db.add", id=entry_id, via="agent")
    return {"ok": True, "id": entry_id,
            "name": normalised.get("name", entry_id)}


@_agent_endpoint("download-hmm-database", write=True)
def _h_download_hmm_database(app, payload):
    """Download + index an HMM database by `id` so hmmscan can use it —
    the headless equivalent of the GUI catalog modal's Download button.
    Body: ``{id}`` (a builtin like `pfam-a` / `ncbifam`, or a custom
    entry from `add-hmm-database`; see `list-hmm-databases`).

    Runs SYNCHRONOUSLY: the request blocks through download → decompress
    → hmmpress, so a builtin (Pfam-A ~300 MB, NCBIfam ~600 MB) can take
    minutes — size your client timeout accordingly. The cross-process
    `_HMM_DB_DOWNLOAD_INFLIGHT` slot returns 409 if the same DB is
    already downloading (e.g. a GUI download in flight). Files land in
    `<DATA_DIR>/hmm_databases/<id>/` exactly as a GUI download leaves
    them — on success the DB reports `ready: true` in
    `list-hmm-databases` and becomes selectable via
    `set-active-hmm-database`. Everything below the slot guard runs in
    the shared, L2-gated `_hmm_db_perform_download`."""
    eid = payload.get("id")
    if not isinstance(eid, str) or not eid.strip():
        return ({"error": "missing 'id'"}, 400)
    eid = eid.strip()
    entry = _find_hmm_db_entry(eid)
    if entry is None:
        return ({"error": "hmm database not found"}, 404)
    if not entry.get("url"):
        return ({"error": "catalog entry has no download URL"}, 400)
    if not _hmm_db_acquire_download_slot(eid):
        return ({"error": "a download for this database is already "
                 "running"}, 409)
    try:
        result = _hmm_db_perform_download(entry)
        _log_event("hmm_db.downloaded", id=eid,
                   n_profiles=result["n_profiles"],
                   pressed=result["pressed"], bytes=result["bytes"],
                   via="agent")
        return {"ok": True, "id": eid, "name": entry.get("name", eid),
                "n_profiles": result["n_profiles"],
                "pressed": result["pressed"], "version": result["version"],
                "sha256": result["sha256"], "bytes": result["bytes"],
                "ready": _hmm_db_pressed(eid)}
    except (OSError, ValueError, RuntimeError) as exc:
        _log.exception("agent download-hmm-database failed for %r", eid)
        _log_event("hmm_db.download.failed", id=eid,
                   error=str(exc)[:200], via="agent")
        return ({"error": f"download failed: {_scrub_path(str(exc))}"}, 500)
    except Exception as exc:
        _log.exception("agent download-hmm-database crashed for %r", eid)
        _log_event("hmm_db.download.crashed", id=eid,
                   exc_type=type(exc).__name__, via="agent")
        return ({"error": "download failed: unexpected error "
                 "(see splicecraft log)"}, 500)
    finally:
        _hmm_db_release_download_slot(eid)


def _settings_validator_bool(value):
    if isinstance(value, bool):
        return value, None
    return None, "must be a boolean"


def _settings_validator_int_range(lo: int, hi: int):
    def _v(value):
        # Sweep #9 (2026-05-19): reject bool BEFORE coercing.
        # `_coerce_int(True)` returns 1 (Python truthiness), but
        # invariant #41 documents strict bool-vs-int separation
        # for settings — `_validate_settings` enforces it on the
        # disk-load path, this validator now does the same for
        # the agent-API path. Without this, `set-setting
        # min_primer_binding=true` silently succeeded with value 1.
        if isinstance(value, bool):
            return None, "must be int (got bool)"
        result = _coerce_int(value, name="value")
        if isinstance(result, str):
            return None, result
        if not (lo <= result <= hi):
            return None, f"must be in [{lo}, {hi}]"
        return result, None
    return _v


def _settings_validator_min_len_4_or_6(value):
    result = _coerce_int(value, name="value")
    if isinstance(result, str):
        return None, result
    if result not in (4, 6):
        return None, "must be 4 or 6"
    return result, None


def _settings_validator_custom_enzymes_csv(value):
    """Comma-separated list of enzyme names, each in the combined
    catalog (built-in NEB ∪ user-added custom enzymes). Whitespace
    tolerated; duplicates silently collapsed.

    Defensive parse — we'd rather drop an unknown name than reject the
    whole list, so a typo or a HF-variant rename doesn't strand the
    user. Returns the canonicalised CSV form (sorted, deduped, valid
    names only)."""
    if value is None or value == "":
        return "", None
    if not isinstance(value, str):
        return None, "must be a comma-separated string of enzyme names"
    raw_names = [
        s.strip() for s in value.replace(";", ",").split(",")
        if s and s.strip()
    ]
    catalog = _state._all_enzymes_hook()
    valid: list[str] = []
    for nm in raw_names:
        if nm in catalog and nm not in valid:
            valid.append(nm)
    return ",".join(sorted(valid)), None


def _settings_validator_collection_name(value):
    """Empty string / null is allowed to clear the active collection.
    Otherwise route through `_normalize_collection_name`."""
    if value is None or value == "":
        return "", None
    norm = _normalize_collection_name(value)
    if norm is None:
        return None, "invalid collection name"
    return norm, None


def _settings_validator_grammar_id(value):
    if not isinstance(value, str) or not value:
        return None, "must be a non-empty string"
    # Cap defensively; grammar IDs are short identifiers.
    if len(value) > 100:
        return None, "too long (max 100 chars)"
    return value, None


# Allowlist of user-facing toggle settings the agent may read / write.
# Infrastructure caches (`last_known_latest`, `last_seen_version`,
# `last_update_check_ts`, `hmm_db_path`) are deliberately excluded —
# those are session bookkeeping, not user preferences.
_AGENT_SETTINGS_ALLOWLIST: "dict[str, tuple]" = {
    # key: (validator, default)
    "show_feature_tooltips": (_settings_validator_bool,                  False),
    "click_debug":           (_settings_validator_bool,                  False),
    "check_updates":         (_settings_validator_bool,                  True),
    "show_restr":            (_settings_validator_bool,                  False),
    "restr_unique_only":     (_settings_validator_bool,                  True),
    "show_connectors":       (_settings_validator_bool,                  False),
    "restr_min_len":         (_settings_validator_min_len_4_or_6,        6),
    "restr_custom_enzymes":  (_settings_validator_custom_enzymes_csv,    ""),
    "restr_use_custom_list": (_settings_validator_bool,                  False),
    "min_primer_binding":    (_settings_validator_int_range(1, 60),      15),
    "active_collection":     (_settings_validator_collection_name,       ""),
    "active_enzyme_collection": (_settings_validator_collection_name,     ""),
    "active_grammar":        (_settings_validator_grammar_id,            "gb_l0"),
    "constructor_filter_by_grammar": (_settings_validator_bool,           True),
}


@_agent_endpoint("get-settings")
def _h_get_settings(app, payload):
    """Return every allowlisted user-toggle setting with its current
    value, default, and validator hint. Useful for an agent that
    wants to inspect which toggles are exposed before writing."""
    out = {}
    for key, (_validator, default) in _AGENT_SETTINGS_ALLOWLIST.items():
        out[key] = {
            "value":   _get_setting(key, default),
            "default": default,
        }
    return {"ok": True, "settings": out}


@_agent_endpoint("set-setting", write=True)
def _h_set_setting(app, payload):
    """Persist a single user-toggle setting. Body: ``{key, value}``.
    `key` must be in the allowlist (see `get-settings`); `value` is
    type-checked + range-checked per the key's validator. Persists
    via `_set_setting`. Live in-memory app state mirrors are NOT
    refreshed here — the change takes effect on the next session
    restart for most toggles. (The GUI re-applies certain toggles
    immediately via dedicated action methods; mirroring that
    semantically across the agent surface would require dispatching
    each key to a UI-thread handler. Out of scope for v1.)

    Marked ``write=True`` so it requires the agent-API bearer token
    same as the other mutating endpoints (closes inconsistency where
    settings.json could be mutated token-free)."""
    key = payload.get("key")
    if not isinstance(key, str) or not key:
        return ({"error": "missing or non-string 'key'"}, 400)
    if key not in _AGENT_SETTINGS_ALLOWLIST:
        return ({"error": f"unknown setting {key!r}",
                  "available": list(_AGENT_SETTINGS_ALLOWLIST)}, 400)
    validator, _default = _AGENT_SETTINGS_ALLOWLIST[key]
    cleaned, err = validator(payload.get("value"))
    if err is not None:
        return ({"error": f"{key!r}: {err}"}, 400)
    _set_setting(key, cleaned)
    return {"ok": True, "key": key, "value": cleaned}


def _parts_bin_entry_summary(p: dict) -> dict:
    """Compact view of a parts-bin row for list endpoints. Drops the
    `gb_text` blob so a 500-entry bin doesn't return 50+ MB; agents
    that need full text call `get-part`."""
    return {
        "name":     p.get("name", ""),
        "type":     p.get("type", ""),
        "level":    p.get("level", 0),
        "position": p.get("position", ""),
        "grammar":  p.get("grammar", ""),
        "oh5":      p.get("oh5", ""),
        "oh3":      p.get("oh3", ""),
        "size":     int(p.get("size") or len(str(p.get("sequence") or ""))),
    }


@_agent_endpoint("list-parts")
def _h_list_parts(app, payload):
    """List parts in the active parts bin. Optional body:
    ``{grammar?: str, level?: int, position?: str}`` filters. Returns
    compact rows (no `gb_text`); call `get-part` for full content."""
    grammar = payload.get("grammar")
    level   = payload.get("level")
    position = payload.get("position")
    if level is not None:
        lvl = _coerce_int(level, name="level")
        if isinstance(lvl, str):
            return ({"error": lvl}, 400)
    else:
        lvl = None
    if grammar is not None and not isinstance(grammar, str):
        return ({"error": "'grammar' must be string"}, 400)
    if position is not None and not isinstance(position, str):
        return ({"error": "'position' must be string"}, 400)
    rows = []
    # Sweep #26: readonly iter — `_parts_bin_entry_summary` builds a
    # fresh dict from `p.get(...)` reads.
    for p in _iter_parts_bin_readonly():
        if grammar and (p.get("grammar") or "") != grammar:
            continue
        if lvl is not None and int(p.get("level") or 0) != lvl:
            continue
        if position and (p.get("position") or "") != position:
            continue
        rows.append(_parts_bin_entry_summary(p))
    return {"ok": True, "parts": rows, "count": len(rows)}


@_agent_endpoint("get-part")
def _h_get_part(app, payload):
    """Fetch a single part by `name` (and optional `grammar` to
    disambiguate when two grammars carry a part with the same name).
    Returns the full entry including `gb_text` + `sequence` so the
    agent can parse it locally."""
    name = _sanitize_label(payload.get("name"), max_len=200)
    if not name:
        return ({"error": "missing 'name'"}, 400)
    grammar = payload.get("grammar")
    if grammar is not None and not isinstance(grammar, str):
        return ({"error": "'grammar' must be string"}, 400)
    for p in _load_parts_bin():
        if p.get("name") != name:
            continue
        if grammar and (p.get("grammar") or "") != grammar:
            continue
        return {"ok": True, "part": p}
    return ({"error": f"no part named {name!r}"
              + (f" in grammar {grammar!r}" if grammar else "")}, 404)


def _agent_part_dict(payload: dict) -> "dict | str":
    """Coerce + validate a parts-bin payload. Returns the cleaned
    dict or a string error message. Mirrors `PartEditModal`'s save
    path: every field except `gb_text` and `sequence` is optional;
    one of those must be present so the part has actual content."""
    name = _sanitize_label(payload.get("name"), max_len=200)
    if not name:
        return "missing or non-string 'name'"
    grammar = payload.get("grammar", "")
    if grammar is not None and not isinstance(grammar, str):
        return "'grammar' must be a string"
    sequence = payload.get("sequence")
    gb_text  = payload.get("gb_text")
    if not isinstance(sequence, str) and not isinstance(gb_text, str):
        return "must provide 'sequence' or 'gb_text'"
    if sequence is not None and not isinstance(sequence, str):
        return "'sequence' must be a string"
    if gb_text is not None and not isinstance(gb_text, str):
        return "'gb_text' must be a string"
    clean_seq = ""
    if isinstance(sequence, str):
        clean_seq, seq_err = _sanitize_bases(sequence.upper())
        if seq_err:
            return f"'sequence' rejected: {seq_err}"
    elif isinstance(gb_text, str):
        try:
            rec = _gb_text_to_record(gb_text)
            clean_seq = str(rec.seq).upper()
        except Exception as exc:
            return f"gb_text parse failed: {exc}"
    if not clean_seq:
        return "no usable sequence after sanitisation"
    ptype = payload.get("type", "")
    if ptype is not None and not isinstance(ptype, str):
        return "'type' must be a string"
    level = payload.get("level", 0)
    if not isinstance(level, (int, float)) or isinstance(level, bool):
        return "'level' must be a number"
    position = payload.get("position", "")
    if position is not None and not isinstance(position, str):
        return "'position' must be a string"
    oh5 = payload.get("oh5", "")
    oh3 = payload.get("oh3", "")
    for label, val in (("oh5", oh5), ("oh3", oh3)):
        if val is not None and not isinstance(val, str):
            return f"{label!r} must be a string"
    notes = _sanitize_note(payload.get("notes", ""))
    return {
        "name":      name,
        "grammar":   _sanitize_label(grammar),
        "type":      _sanitize_label(ptype),
        "level":     int(level),
        "position":  _sanitize_label(position),
        "oh5":       (oh5 or "").upper(),
        "oh3":       (oh3 or "").upper(),
        "sequence":  clean_seq,
        "size":      len(clean_seq),
        "gb_text":   gb_text if isinstance(gb_text, str) else "",
        "notes":     notes or "",
        "date":      _datetime.now().strftime("%Y-%m-%d"),
    }


@_agent_endpoint("create-part", write=True)
def _h_create_part(app, payload):
    """Add a part to the active parts bin. Body: see `_agent_part_dict`
    for the schema. Returns 409 if a part with the same name + grammar
    already exists — use `update-part` or pick a different name."""
    p = _agent_part_dict(payload)
    if isinstance(p, str):
        return ({"error": p}, 400)
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        entries = _load_parts_bin()
        for e in entries:
            if (e.get("name") == p["name"]
                    and (e.get("grammar") or "") == p["grammar"]):
                return ({"error": (
                    f"part {p['name']!r} already exists in grammar "
                    f"{p['grammar']!r}; use update-part or rename."
                )}, 409)
        entries.append(p)
        if (err := _agent_save_or_500(
                lambda: _save_parts_bin(entries),
                "parts_bin")) is not None:
            return err
    return {"ok": True, "name": p["name"], "grammar": p["grammar"]}


@_agent_endpoint("update-part", write=True)
def _h_update_part(app, payload):
    """Update an existing part. Body: `{name, grammar?, ...new fields}`.
    Lookup is by `(name, grammar)`; if grammar is omitted matches the
    first part by name across all grammars."""
    p_new = _agent_part_dict(payload)
    if isinstance(p_new, str):
        return ({"error": p_new}, 400)
    target_grammar = p_new["grammar"]
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        entries = _load_parts_bin()
        for i, e in enumerate(entries):
            if e.get("name") != p_new["name"]:
                continue
            if target_grammar and (e.get("grammar") or "") != target_grammar:
                continue
            # Preserve original date so update doesn't masquerade as add.
            p_new["date"] = e.get("date") or p_new["date"]
            entries[i] = p_new
            if (err := _agent_save_or_500(
                    lambda: _save_parts_bin(entries),
                    "parts_bin")) is not None:
                return err
            return {"ok": True, "name": p_new["name"],
                    "grammar": p_new["grammar"]}
    return ({"error": (
        f"no part {p_new['name']!r}"
        + (f" in grammar {target_grammar!r}" if target_grammar else "")
    )}, 404)


def _agent_feature_dict(payload: dict) -> "dict | str":
    """Coerce + validate a feature-library payload. Schema:
    `{name, sequence, feature_type?, strand?, color?, notes?}`.
    Sequence is the canonical identity field for dedupe purposes.
    """
    name = _sanitize_label(payload.get("name"), max_len=200)
    if not name:
        return "missing or non-string 'name'"
    raw_seq = payload.get("sequence")
    if not isinstance(raw_seq, str) or not raw_seq.strip():
        return "missing or non-string 'sequence'"
    seq, seq_err = _sanitize_bases(raw_seq.upper())
    if seq_err:
        return f"'sequence' rejected: {seq_err}"
    if not seq:
        return "'sequence' empty after sanitisation"
    if len(seq) > 100_000:
        return "'sequence' too long (max 100 kb)"
    ftype = payload.get("feature_type", "misc_feature")
    if ftype is not None and not isinstance(ftype, str):
        return "'feature_type' must be a string"
    strand = payload.get("strand", 1)
    if isinstance(strand, bool) or not isinstance(strand, (int, float)):
        return "'strand' must be -1, 0, or 1"
    if int(strand) not in (-1, 0, 1):
        return "'strand' must be -1, 0, or 1"
    color = payload.get("color")
    if color is not None and not isinstance(color, str):
        return "'color' must be a string"
    notes = _sanitize_note(payload.get("notes", ""))
    return {
        "name":         name,
        "sequence":     seq,
        "feature_type": _sanitize_feat_type(ftype),
        "strand":       int(strand),
        "color":        _sanitize_label(color),
        "notes":        notes or "",
    }


@_agent_endpoint("list-feature-library")
def _h_list_feature_library(app, payload):
    """Return the feature library. Each item: ``{name, feature_type,
    strand, sequence_length, color}``. Sequences themselves can be
    long; agents needing the raw bases call `get-feature-library`.

    Named `list-feature-library` (not `list-features`) to avoid
    collision with the record-level feature endpoints (`add-feature`,
    `delete-feature`, `update-feature`, `get-feature`) which all
    operate on the LOADED record, not the persistent snippet library."""
    rows = []
    for e in _load_features():
        rows.append({
            "name":            e.get("name", ""),
            "feature_type":    e.get("feature_type", ""),
            "strand":          int(e.get("strand", 1)),
            "sequence_length": len(e.get("sequence", "") or ""),
            "color":           e.get("color", ""),
        })
    return {"ok": True, "features": rows, "count": len(rows)}


@_agent_endpoint("get-feature-library")
def _h_get_feature_library(app, payload):
    """Fetch a feature-library entry by (name, feature_type). Body:
    `{name, feature_type?}`. If feature_type is omitted, matches the
    first entry by name. Returns the full entry including `sequence`
    + `notes` so the agent can use it for downstream substring/BLAST
    matches.

    Named `get-feature-library` to disambiguate from `get-feature`
    which fetches a feature on the loaded record by idx."""
    name = _sanitize_label(payload.get("name"), max_len=200)
    if not name:
        return ({"error": "missing or non-string 'name'"}, 400)
    ftype = payload.get("feature_type")
    if ftype is not None and not isinstance(ftype, str):
        return ({"error": "'feature_type' must be a string"}, 400)
    for e in _load_features():
        if e.get("name") != name:
            continue
        if ftype and (e.get("feature_type") or "") != ftype:
            continue
        return {"ok": True, "feature": e}
    return ({"error": (
        f"no feature named {name!r}"
        + (f" of type {ftype!r}" if ftype else "")
    )}, 404)


@_agent_endpoint("create-feature-library", write=True)
def _h_create_feature_library(app, payload):
    """Add a feature snippet to the persistent feature library. Body:
    see `_agent_feature_dict` for the schema. Returns 409 if a
    feature with the same (name, feature_type) already exists — pick
    a different name or use `update-feature-library`.

    Named `create-feature-library` to disambiguate from `add-feature`
    which adds a feature to the loaded record."""
    f = _agent_feature_dict(payload)
    if isinstance(f, str):
        return ({"error": f}, 400)
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        entries = _load_features()
        for e in entries:
            if (e.get("name") == f["name"]
                    and (e.get("feature_type") or "") == f["feature_type"]):
                return ({"error": (
                    f"feature {f['name']!r} (type {f['feature_type']!r}) "
                    f"already exists; use update-feature or pick a "
                    f"different name."
                )}, 409)
        entries.append(f)
        if (err := _agent_save_or_500(
                lambda: _save_features(entries),
                "features")) is not None:
            return err
    return {"ok": True, "name": f["name"],
            "feature_type": f["feature_type"]}


@_agent_endpoint("update-feature-library", write=True)
def _h_update_feature_library(app, payload):
    """Update a feature-library entry, looked up by (name,
    feature_type). Body: every field in `_agent_feature_dict`'s
    schema; missing fields preserve existing values.

    Named `update-feature-library` (not `update-feature`) because
    `update-feature` is already taken by the record-level handler
    that edits features on the loaded plasmid map."""
    name = _sanitize_label(payload.get("name"), max_len=200)
    if not name:
        return ({"error": "missing or non-string 'name'"}, 400)
    ftype = payload.get("feature_type")
    if ftype is not None and not isinstance(ftype, str):
        return ({"error": "'feature_type' must be a string"}, 400)
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        entries = _load_features()
        target_idx = None
        for i, e in enumerate(entries):
            if e.get("name") != name:
                continue
            if ftype and (e.get("feature_type") or "") != ftype:
                continue
            target_idx = i
            break
        if target_idx is None:
            return ({"error": (
                f"no feature named {name!r}"
                + (f" of type {ftype!r}" if ftype else "")
            )}, 404)
        # Merge: take existing entry as base, overlay user-provided
        # fields. `_agent_feature_dict` requires sequence — patch it
        # to use the existing sequence if the payload omits it.
        base = entries[target_idx]
        merged = {**base, **payload}
        # Default sequence/feature_type from existing if missing.
        if "sequence" not in payload:
            merged["sequence"] = base.get("sequence", "")
        if "feature_type" not in payload:
            merged["feature_type"] = base.get("feature_type", "misc_feature")
        f = _agent_feature_dict(merged)
        if isinstance(f, str):
            return ({"error": f}, 400)
        entries[target_idx] = f
        if (err := _agent_save_or_500(
                lambda: _save_features(entries),
                "features")) is not None:
            return err
    return {"ok": True, "name": f["name"],
            "feature_type": f["feature_type"]}


@_agent_endpoint("delete-feature-library", write=True)
def _h_delete_feature_library(app, payload):
    """Remove a feature-library entry by (name, feature_type). Body:
    `{name, feature_type?}`. Named `delete-feature-library` so it
    doesn't collide with `delete-feature` (which removes a feature
    from the loaded record)."""
    name = _sanitize_label(payload.get("name"), max_len=200)
    if not name:
        return ({"error": "missing or non-string 'name'"}, 400)
    ftype = payload.get("feature_type")
    if ftype is not None and not isinstance(ftype, str):
        return ({"error": "'feature_type' must be a string"}, 400)
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        entries = _load_features()
        new_list = [
            e for e in entries
            if not (e.get("name") == name
                    and (not ftype or (e.get("feature_type") or "") == ftype))
        ]
        if len(new_list) == len(entries):
            return ({"error": (
                f"no feature named {name!r}"
                + (f" of type {ftype!r}" if ftype else "")
            )}, 404)
        if (err := _agent_save_or_500(
                lambda: _save_features(new_list),
                "features")) is not None:
            return err
    return {"ok": True, "removed": len(entries) - len(new_list)}


@_agent_endpoint("classify-part")
def _h_classify_part(app, payload):
    """Classify a candidate part by digest-overhang matching, without
    persisting. Body: ``{sequence, circular?: bool = true,
    features?: list}`` — same interface `_classify_part_from_plasmid`
    uses internally. Returns the (grammar, position, type, level,
    oh5, oh3) tuple if a match is found, or `match=null` if not.
    """
    seq = payload.get("sequence")
    if not isinstance(seq, str) or not seq.strip():
        return ({"error": "missing or non-string 'sequence'"}, 400)
    seq_clean = "".join(ch for ch in seq.upper()
                          if ch in "ACGTRYWSMKBDHVN")
    if not seq_clean:
        return ({"error": "no IUPAC bases in 'sequence'"}, 400)
    # Cap input size so a multi-MB paste can't pin the classifier on
    # multiple-grammar digest loops.
    if len(seq_clean) > 1_000_000:
        return ({"error": "sequence exceeds 1 Mbp cap for classifier"},
                413)
    circular = bool(payload.get("circular", True))
    raw_feats = payload.get("features") or []
    if not isinstance(raw_feats, list):
        return ({"error": "'features' must be a list"}, 400)
    # Sweep #26: defense-in-depth length cap on the features list.
    # Pre-fix the only upstream bound was the 1 MiB body cap — a
    # payload of ~50k micro-dicts would saturate the classifier's
    # inner loops without exceeding the body cap.
    if len(raw_feats) > 10_000:
        return ({"error":
                  "'features' too long (max 10,000 entries)"}, 413)
    feats = [f for f in raw_feats if isinstance(f, dict)]
    try:
        result = _classify_part_from_plasmid(
            seq_clean, circular=circular, features=feats,
        )
    except Exception as exc:
        _log.exception("agent classify-part: classifier failed")
        return ({"error": f"classification failed: {_scrub_path(str(exc))}"},
                500)
    return {"ok": True, "match": result}


@_agent_endpoint("add-codon-table", write=True)
def _h_add_codon_table(app, payload):
    """Add or replace a codon-usage table. Body either fetches from
    Kazusa or stamps a raw dict directly:

      * Fetch:   ``{taxid: str, name?: str, source: "kazusa"}``
      * Genome:  ``{source: "genome", accession|taxid: str,
                   mode?: "heg"|"genome", name?: str}``
      * Raw:     ``{name: str, taxid?: str, raw: {<codon>: count, ...}}``

    The Kazusa fetch is size-capped at `_KAZUSA_MAX_RESPONSE_BYTES`
    and timeout-bounded; the genome build downloads the CDS from the
    NCBI Datasets API (size-capped at `_NCBI_CDS_ZIP_MAX_BYTES`,
    default ``mode="heg"`` → ribosomal-protein bias); the raw path
    caps at 64 codons (the standard genetic code) and validates each
    value is a non-negative int.
    """
    source = payload.get("source", "user")
    if not isinstance(source, str):
        return ({"error": "'source' must be string"}, 400)
    if source == "kazusa":
        taxid_raw = payload.get("taxid")
        if not isinstance(taxid_raw, str) or not taxid_raw.strip():
            return ({"error": "'taxid' required for kazusa source"}, 400)
        taxid = _sanitize_label(taxid_raw, max_len=32)
        if not taxid or not taxid.isdigit():
            return ({"error": "'taxid' must be a digit string"}, 400)
        name_in = _sanitize_label(payload.get("name"), max_len=200)
        try:
            raw, msg = _codon_fetch_kazusa(taxid)
        except Exception as exc:
            _log.exception("agent add-codon-table: Kazusa fetch failed")
            return ({"error": f"Kazusa fetch failed: {exc}"}, 502)
        if raw is None:
            return ({"error": msg or "Kazusa returned no data"}, 502)
        display = name_in or f"Species (taxid {taxid})"
        try:
            entry = _codon_tables_add(display, taxid, raw, source="kazusa")
        except (OSError, RuntimeError) as exc:
            _log.exception("agent add-codon-table: save failed")
            return ({"error": f"save failed: {exc}"}, 500)
        return {"ok": True, "entry": {
            "name":   entry["name"],
            "taxid":  entry["taxid"],
            "source": entry["source"],
        }}
    if source == "genome":
        # Build from an NCBI genome's CDS — accession (GCF_…/GCA_…) or taxid.
        query_raw = payload.get("accession") or payload.get("taxid")
        if not isinstance(query_raw, str) or not query_raw.strip():
            return ({"error": "'accession' or 'taxid' required for genome "
                              "source"}, 400)
        query = _sanitize_accession(query_raw) or ""
        if not query:
            return ({"error": "invalid 'accession' or 'taxid'"}, 400)
        mode = payload.get("mode", "heg")
        if mode not in ("heg", "genome"):
            return ({"error": "'mode' must be 'heg' or 'genome'"}, 400)
        name_in = _sanitize_label(payload.get("name"), max_len=200)
        try:
            raw, msg, meta = _genome_build_codon_table(query, mode)
        except Exception as exc:
            _log.exception("agent add-codon-table: genome build failed")
            return ({"error": f"genome build failed: {exc}"}, 502)
        if raw is None or meta is None:
            return ({"error": msg or "genome build returned no data"}, 502)
        display = (name_in or meta.get("organism")
                   or meta.get("accession") or "Genome codon table")
        try:
            entry = _codon_tables_add(display, meta.get("taxid", ""), raw,
                                      source="genome")
        except (OSError, RuntimeError) as exc:
            _log.exception("agent add-codon-table: save failed")
            return ({"error": f"save failed: {exc}"}, 500)
        return {"ok": True, "message": msg, "entry": {
            "name":   entry["name"],
            "taxid":  entry["taxid"],
            "source": entry["source"],
        }}
    # Raw path.
    name_in = _sanitize_label(payload.get("name"), max_len=200)
    if not name_in:
        return ({"error": "missing 'name'"}, 400)
    raw = payload.get("raw")
    if not isinstance(raw, dict):
        return ({"error": "'raw' must be a dict of {codon: count}"}, 400)
    if len(raw) > 64:
        return ({"error": "'raw' has more than 64 codons"}, 400)
    valid = set("ACGTU")
    cleaned: dict = {}
    for codon, count in raw.items():
        if not isinstance(codon, str) or len(codon) != 3:
            return ({"error": f"bad codon {codon!r} — must be 3-char str"},
                    400)
        if set(codon.upper()) - valid:
            return ({"error": f"non-IUPAC codon {codon!r}"}, 400)
        n = _coerce_int(count, name=f"raw[{codon!r}]")
        if isinstance(n, str):
            return ({"error": n}, 400)
        if n < 0:
            return ({"error": f"negative count for codon {codon!r}"}, 400)
        cleaned[codon.upper().replace("U", "T")] = n
    taxid_raw = payload.get("taxid", "")
    taxid = (_sanitize_label(taxid_raw, max_len=32) if taxid_raw else "")
    try:
        entry = _codon_tables_add(name_in, taxid, cleaned, source="user")
    except (OSError, RuntimeError) as exc:
        _log.exception("agent add-codon-table: save failed")
        return ({"error": f"save failed: {exc}"}, 500)
    return {"ok": True, "entry": {
        "name":   entry["name"],
        "taxid":  entry["taxid"],
        "source": entry["source"],
    }}


@_agent_endpoint("delete-codon-table", write=True)
def _h_delete_codon_table(app, payload):
    """Remove a codon-usage table by `taxid` or `name`. Built-in
    tables (source='builtin') cannot be removed."""
    key_raw = payload.get("taxid") or payload.get("name")
    if not isinstance(key_raw, str) or not key_raw.strip():
        return ({"error": "missing 'taxid' or 'name'"}, 400)
    key = key_raw.strip().lower()
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        entries = _codon_tables_load()
        target = None
        for e in entries:
            if (str(e.get("taxid") or "").lower() == key
                    or str(e.get("name") or "").lower() == key):
                target = e
                break
        if target is None:
            return ({"error": f"no codon table for {key_raw!r}"}, 404)
        if (target.get("source") or "") == "builtin":
            return ({"error": "built-in codon tables cannot be removed"}, 409)
        kept = [e for e in entries
                if (e.get("taxid") or e.get("name")) !=
                   (target.get("taxid") or target.get("name"))]
        try:
            _codon_tables_save(kept)
        except (OSError, RuntimeError) as exc:
            _log.exception("agent delete-codon-table: save failed")
            return ({"error": f"save failed: {exc}"}, 500)
    return {"ok": True, "removed": {
        "name":   target.get("name", ""),
        "taxid":  target.get("taxid", ""),
    }}


def _history_node_to_dict(node) -> "dict | None":
    """Convert a `_CommercialSaaSHistoryNode` tree to a plain-dict JSON
    shape. Mirrors the viewer's per-node fields so an agent gets the
    same surface the UI sees without driving a TUI.

    Sweep #10 (2026-05-20): iterative DFS with explicit depth + node
    caps. Pre-fix this recursed through `node.parents`, blowing the
    Python recursion limit (~1000) on hostile `.dna` imports carrying
    deeply-nested `<HistoryTree>` blocks (32 MB cap can yield 3M+
    nodes). Sibling helpers `walk`, `_history_node_count`,
    `HistoryScreen.populate` were already iterative for this exact
    reason; this one was overlooked.
    """
    if node is None:
        return None

    def _shell(n) -> dict:
        return {
            "name":       getattr(n, "name", "") or "",
            "operation":  getattr(n, "operation", "") or "",
            "seq_len":    int(getattr(n, "seq_len", 0) or 0),
            "circular":   bool(getattr(n, "circular", False)),
            "regenerated_sites": list(
                getattr(n, "regenerated_sites", []) or []
            ),
            "input_summaries":   list(
                getattr(n, "input_summaries", []) or []
            ),
            "parents":    [],
        }

    root_dict = _shell(node)
    # Stack frame: (source_node, target_dict_under_construction, depth).
    stack: list = [(node, root_dict, 0)]
    n_seen = 1
    truncated = False
    while stack:
        src, dst, depth = stack.pop()
        if depth >= _HISTORY_NODE_MAX_DEPTH:
            # Bail out the parents-walk at this branch — root_dict
            # still carries the upstream shape. Mark on dst so the
            # caller surfaces a "history truncated" hint.
            dst["_truncated"] = "depth_cap"
            truncated = True
            continue
        for p in (getattr(src, "parents", []) or []):
            if n_seen >= _HISTORY_NODE_MAX_NODES:
                dst["_truncated"] = "node_cap"
                truncated = True
                break
            child_dict = _shell(p)
            dst["parents"].append(child_dict)
            n_seen += 1
            stack.append((p, child_dict, depth + 1))
    if truncated:
        root_dict["_truncated_at_node"] = n_seen
    return root_dict


@_agent_endpoint("get-history")
def _h_get_history(app, payload):
    """Return the construction-history tree for a library entry as
    nested JSON. Body: ``{name}`` (or ``{id}``). Returns ``history=null``
    when the entry has no recorded history (NOT a 404)."""
    name = _sanitize_label(payload.get("name"), max_len=200)
    eid_raw = payload.get("id")
    eid = (_sanitize_label(eid_raw, max_len=200)
           if isinstance(eid_raw, str) else "")
    if not name and not eid:
        return ({"error": "missing 'name' or 'id'"}, 400)
    entries = _load_library()
    entry = None
    for e in entries:
        if eid and e.get("id") == eid:
            entry = e
            break
        if name and e.get("name") == name:
            entry = e
            break
    if entry is None:
        return ({"error": f"no entry matching name={name!r} id={eid!r}"},
                404)
    xml = entry.get("history_xml") or ""
    if not xml:
        return {"ok": True, "history": None,
                "name": entry.get("name"), "id": entry.get("id")}
    try:
        root = _parse_commercialsaas_history(xml)
    except ValueError as exc:
        _log.warning("agent get-history: malformed history_xml: %s", exc)
        return ({"error": f"malformed history XML: {exc}"}, 422)
    return {
        "ok": True,
        "name": entry.get("name"),
        "id":   entry.get("id"),
        "history": _history_node_to_dict(root),
    }


@_agent_endpoint("check-primer-duplicates")
def _h_check_primer_duplicates(app, payload):
    """Scan the primer library for duplicate sequences. Returns a
    list of groups where multiple entries share the same canonical
    primer sequence — useful for an agent to flag before saving a
    designed primer. Empty list when the library is clean.

    Read-only (no `write=True`) — running it via the agent doesn't
    mutate state. The on-launch `PrimerDuplicatesModal` (sweep #3)
    does the dedupe, but it's splash-time only; this endpoint lets
    an agent inspect anytime.
    """
    entries = _load_primers()
    by_seq: dict = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        seq = _normalize_primer_seq(e.get("sequence") or "")
        if not seq:
            continue
        by_seq.setdefault(seq, []).append({
            "name":     e.get("name", ""),
            "sequence": seq,
            "tm":       e.get("tm"),
            "primer_type": e.get("primer_type", ""),
            "source":   e.get("source", ""),
        })
    duplicates = [grp for grp in by_seq.values() if len(grp) > 1]
    return {
        "ok": True,
        "duplicates": duplicates,
        "n_groups": len(duplicates),
        "n_total_entries": len(entries),
    }


@_agent_endpoint("list-pre-update-snapshots")
def _h_list_pre_update_snapshots(app, payload):
    """List pre-update snapshots created by `splicecraft update`.
    Returns each snapshot's id, from_version, mtime, and counts —
    same data the `--restore-pre-update` CLI shows."""
    snaps = _list_pre_update_snapshots()
    rows = []
    for s in snaps:
        rows.append({
            "id":                  s.get("id", ""),
            "path":                str(s.get("path", "")),
            "mtime":               s.get("mtime"),
            "from_version":        s.get("from_version", "?"),
            "from_python_version": s.get("from_python_version", "?"),
            "from_platform":       s.get("from_platform", "?"),
            "schema_version":      s.get("schema_version", 1),
            "n_files":             s.get("n_files", 0),
            "n_dirs":              s.get("n_dirs", 0),
            "total_size":          s.get("total_size", 0),
        })
    return {"ok": True, "snapshots": rows, "count": len(rows)}


@_agent_endpoint("simulate-gibson")
def _h_simulate_gibson(app, payload):
    """Dry-run a Gibson assembly without saving. Body:
    ``{fragments: [{name, sequence, features?}, ...],
        min_overlap?: int = 15, circular?: bool = true}``.

    Returns the full simulator result dict (overlaps, errors,
    warnings, product features, product sequence). Read-only — pair
    with ``gibson-assemble`` to commit.
    """
    fragments = payload.get("fragments")
    if not isinstance(fragments, list):
        return ({"error": "'fragments' must be a list"}, 400)
    if len(fragments) > 64:
        return ({"error": "too many fragments (max 64)"}, 400)
    cleaned: list[dict] = []
    for i, f in enumerate(fragments):
        if not isinstance(f, dict):
            return ({"error": f"fragment[{i}] is not a dict"}, 400)
        seq = f.get("sequence")
        if not isinstance(seq, str):
            return ({"error": f"fragment[{i}].sequence missing or "
                      "non-string"}, 400)
        if len(seq) > 1_000_000:
            return ({"error": f"fragment[{i}] exceeds 1 Mbp"}, 413)
        name = _sanitize_label(f.get("name"), max_len=80) or f"F{i+1}"
        feats_raw = f.get("features") or []
        if not isinstance(feats_raw, list):
            return ({"error": f"fragment[{i}].features must be a list"},
                    400)
        cleaned.append({
            "name":     name,
            "sequence": seq,
            "features": [x for x in feats_raw if isinstance(x, dict)],
        })
    min_overlap = _coerce_int(
        payload.get("min_overlap", _GIBSON_MIN_OVERLAP_BP),
        name="min_overlap",
    )
    if isinstance(min_overlap, str):
        return ({"error": min_overlap}, 400)
    # 2026-05-27 (audit-5 GB M3): bump min_overlap floor from 1 to
    # 10 bp at the agent endpoint. Pre-fix `min_overlap=1` was
    # accepted — a chain of four fragments all ending in `A` and
    # starting with `A` would "assemble" into junk with a 1-bp
    # overlap. Real Gibson assemblies use 20-40 bp homology; 10 bp
    # is already permissive while refusing the obvious abuse.
    _GIBSON_AGENT_MIN_FLOOR = 10
    if not (_GIBSON_AGENT_MIN_FLOOR <= min_overlap <= _GIBSON_MAX_OVERLAP_BP):
        return ({"error": f"min_overlap out of range "
                  f"[{_GIBSON_AGENT_MIN_FLOOR}, "
                  f"{_GIBSON_MAX_OVERLAP_BP}] (1-9 bp overlaps would "
                  f"accept biologically meaningless assemblies)"}, 400)
    circular = bool(payload.get("circular", True))
    try:
        result = _simulate_gibson_assembly(
            cleaned, min_overlap=min_overlap, circular=circular,
        )
    except Exception as exc:
        _log.exception("agent simulate-gibson: simulator failed")
        return ({"error": f"simulator failed: {exc}"}, 500)
    return {"ok": True, "result": result}


_AGENT_MUT_RE = re.compile(r"^([A-Z])(\d{1,5})([A-Z\*])$")


@_agent_endpoint("design-mutagenesis")
def _h_design_mutagenesis(app, payload):
    """Design SOE-PCR primers for a single-site mutation. Body:
    ``{cds_dna, mutation, codon_taxid?}``. `mutation` is a string
    like ``"W140F"`` (WT-aa, 1-based position, mutant-aa).

    Returns ``{outer, inner}`` — the outer + inner primer pairs
    `_mut_design_outer` / `_mut_design_inner` produce. The agent is
    free to save the result via `update-primer` or by setting up a
    Constructor save flow.
    """
    cds_dna = payload.get("cds_dna")
    if not isinstance(cds_dna, str) or not cds_dna.strip():
        return ({"error": "missing or non-string 'cds_dna'"}, 400)
    cds_clean = "".join(ch for ch in cds_dna.upper()
                          if ch in "ACGTRYWSMKBDHVN")
    if not cds_clean:
        return ({"error": "no IUPAC bases in 'cds_dna'"}, 400)
    if len(cds_clean) > 30_000:
        return ({"error": "'cds_dna' exceeds 30 kbp cap"}, 413)
    if len(cds_clean) % 3 != 0:
        return ({"error": f"'cds_dna' length {len(cds_clean)} is not a "
                  "multiple of 3 (CDS must be whole codons)"}, 400)
    mut_raw = payload.get("mutation")
    if not isinstance(mut_raw, str):
        return ({"error": "missing or non-string 'mutation'"}, 400)
    m = _AGENT_MUT_RE.match(mut_raw.strip())
    if m is None:
        return ({"error": f"mutation {mut_raw!r} doesn't match the "
                  "pattern '<WT-aa><1-based-pos><mut-aa>' "
                  "(e.g. 'W140F')"}, 400)
    wt_aa, pos_str, mut_aa = m.group(1), m.group(2), m.group(3)
    pos = int(pos_str)
    if pos < 1:
        return ({"error": "mutation position must be >= 1"}, 400)
    if (pos - 1) * 3 + 3 > len(cds_clean):
        return ({"error": f"mutation position {pos} is past the end of "
                  f"the {len(cds_clean) // 3}-aa CDS"}, 400)
    codon_taxid = payload.get("codon_taxid")
    codon_raw = None
    if codon_taxid is not None:
        if not isinstance(codon_taxid, str):
            return ({"error": "'codon_taxid' must be string"}, 400)
        entry = _codon_tables_get(codon_taxid)
        if entry is None:
            return ({"error": f"unknown codon_taxid {codon_taxid!r}"},
                    404)
        codon_raw = entry.get("raw")
    try:
        outer = _mut_design_outer(cds_clean)
        inner = _mut_design_inner(cds_clean, pos, mut_aa, wt_aa,
                                    codon_table=codon_raw)
    except (ValueError, RuntimeError) as exc:
        return ({"error": f"mutagenesis design failed: {exc}"}, 422)
    except Exception as exc:
        _log.exception("agent design-mutagenesis: unexpected failure")
        return ({"error": f"unexpected failure: {exc}"}, 500)
    return {"ok": True, "mutation": mut_raw, "outer": outer, "inner": inner}


def _record_to_scrub_feats(record) -> "list[dict]":
    """Build scrub-compatible feature dicts (type/start/end/strand, plus
    codon_start/transl_table/_exons for CDS) from a SeqRecord for the
    headless agent path — the subset of `PlasmidMap._parse` that
    `_scrub_design` consumes, using the hardened `_feat_bounds` for
    wrap-aware coordinates. CDS features carry their reading frame so the
    scrub stays protein-preserving without a mounted UI."""
    total = _seq_len(record)
    out: "list[dict]" = []
    for feat in getattr(record, "features", []) or []:
        if feat.type == "source":
            continue
        b = _feat_bounds(feat, total)
        if b is None:
            continue
        start, end, strand = b
        d: dict = {"type": feat.type, "start": start, "end": end,
                   "strand": strand or 1, "label": _feat_label(feat)}
        loc = getattr(feat, "location", None)
        try:
            from Bio.SeqFeature import CompoundLocation
            # Spliced CDS (multi-part join beyond a 2-part origin wrap): pass
            # exon parts so `_translate_cds` splices introns before checking
            # synonymy.
            if isinstance(loc, CompoundLocation) and len(loc.parts) > 2:
                d["_exons"] = [(int(p.start), int(p.end)) for p in loc.parts]
        except (ImportError, TypeError, ValueError):
            pass
        if feat.type.upper() == "CDS":
            try:
                cs = int((feat.qualifiers.get("codon_start", ["1"]) or ["1"])[0])
                if cs in (2, 3):
                    d["codon_start"] = cs
            except (TypeError, ValueError, IndexError):
                pass
            try:
                tt = int((feat.qualifiers.get("transl_table", ["1"])
                          or ["1"])[0])
                if tt and tt != 1:
                    d["transl_table"] = tt
            except (TypeError, ValueError, IndexError):
                pass
        out.append(d)
    return out


@_agent_endpoint("scrub-plasmid")
def _h_scrub_plasmid(app, payload):
    """Plan a clone-free restriction-site scrub. Body:
    ``{seq?, features?, enzymes?, overlap?, method?, circular?, codon_taxid?}``.

    With no ``seq``, scrubs the plasmid currently on the canvas (using its
    CDS features so the cure stays synonymous / protein-preserving). When
    ``seq`` is given, pass ``features`` (a list of ``{type,start,end,strand,
    codon_start?,transl_table?}`` dicts) to protect overlapping CDSes —
    WITHOUT them every site is treated as non-coding and a coding site could
    change a protein. ``enzymes`` is a list of names (default BsaI/Esp3I/BbsI);
    ``overlap`` is ``"improved"`` (default) or ``"classic"``; ``codon_taxid``
    (a registered codon-usage table id) makes coding cures prefer that host's
    frequent synonymous codons.

    ``method`` picks the re-circularization route: ``"quikchange"`` (default —
    one whole-plasmid amplicon that self-circularises) or ``"golden_braid"``
    (split into BsaI-tailed PCR fragments that a Golden Gate reaction
    reassembles seamlessly; BsaI is force-cured as the assembly enzyme).

    QuikChange returns ``{ok, method, enzymes, cured_seq, edits,
    sites_removed, sites_skipped, n_rounds, rounds, warnings}``. Golden Braid
    returns ``{ok, method, enzyme, cured_seq, edits, sites_removed,
    sites_skipped, n_fragments, fragments, verified, warnings, errors}`` —
    each fragment carrying its BsaI-tailed primer pair + junction overhangs.
    Design-only: never mutates the canvas or any file.
    """
    seq = payload.get("seq")
    feats: "list[dict]" = []
    if seq is not None:
        if not isinstance(seq, str) or not seq.strip():
            return ({"error": "'seq' must be a non-empty string"}, 400)
        feats_in = payload.get("features", [])
        if feats_in is None:
            feats_in = []
        if not isinstance(feats_in, list):
            return ({"error": "'features' must be a list of feature dicts"}, 400)
        feats = feats_in
    else:
        rec = getattr(app, "_current_record", None)
        if rec is None:
            return ({"error": "no 'seq' provided and no plasmid is loaded"}, 400)
        seq = str(rec.seq)
        try:
            feats = _record_to_scrub_feats(rec)
        except Exception:
            _log.exception("scrub endpoint: feature extraction failed")
            feats = []
    if len(seq) > 1_000_000:
        return ({"error": "'seq' exceeds the 1 Mbp cap"}, 413)
    enzymes = payload.get("enzymes")
    if enzymes is not None and not isinstance(enzymes, list):
        return ({"error": "'enzymes' must be a list of enzyme names"}, 400)
    overlap = payload.get("overlap", "improved")
    if overlap not in ("improved", "classic"):
        return ({"error": "'overlap' must be 'improved' or 'classic'"}, 400)
    method = payload.get("method", "quikchange")
    if method not in ("quikchange", "golden_braid"):
        return ({"error": "'method' must be 'quikchange' or 'golden_braid'"}, 400)
    circular = payload.get("circular", True)
    if not isinstance(circular, bool):
        return ({"error": "'circular' must be a boolean"}, 400)
    codon_taxid = payload.get("codon_taxid")
    codon_raw = None
    if codon_taxid is not None:
        if not isinstance(codon_taxid, str):
            return ({"error": "'codon_taxid' must be a string"}, 400)
        entry = _codon_tables_get(codon_taxid)
        if entry is None:
            return ({"error": f"unknown codon_taxid {codon_taxid!r}"}, 404)
        codon_raw = entry.get("raw")
    rounds: list = []
    try:
        if method == "golden_braid":
            plan = _scrub_gb_design(seq, feats, enzymes, circular=circular,
                                    codon_raw=codon_raw)
        else:
            plan = _scrub_design(seq, feats, enzymes, circular=circular,
                                 codon_raw=codon_raw)
            if plan.get("ok"):
                for i, cl in enumerate(plan.get("clusters", []), 1):
                    rounds.append(_scrub_qc_primers(
                        plan["cured_seq"], cl["positions"],
                        circular=circular, overlap=overlap, round_no=i))
                if any(not r.get("error") for r in rounds):
                    qv_ok, _qv = _scrub_qc_verify(
                        plan.get("orig_seq", ""), plan["cured_seq"],
                        rounds, len(plan["cured_seq"]))
                    plan["verified"] = qv_ok
    except Exception as exc:
        _log.exception("agent scrub-plasmid: unexpected failure")
        return ({"error": f"unexpected failure: {exc}"}, 500)
    if method == "golden_braid":
        return {
            "ok": plan.get("ok", False),
            "method": "golden_braid",
            "enzyme": plan.get("enzyme", "BsaI"),
            "cured_seq": plan.get("cured_seq", ""),
            "edits": plan.get("edits", []),
            "sites_removed": plan.get("sites_removed", []),
            "sites_skipped": plan.get("sites_skipped", []),
            "n_fragments": plan.get("n_fragments", 0),
            "fragments": plan.get("fragments", []),
            "verified": plan.get("verified", False),
            "warnings": plan.get("warnings", []),
            "errors": plan.get("errors", []),
        }
    return {
        "ok": plan.get("ok", False),
        "method": "quikchange",
        "verified": plan.get("verified", False),
        "enzymes": plan.get("enzymes", []),
        "cured_seq": plan.get("cured_seq", ""),
        "edits": plan.get("edits", []),
        "sites_removed": plan.get("sites_removed", []),
        "sites_skipped": plan.get("sites_skipped", []),
        "n_rounds": plan.get("n_rounds", 0),
        "rounds": rounds,
        "warnings": plan.get("warnings", []),
    }


@_agent_endpoint("get-experiment")
def _h_get_experiment(app, payload):
    """Fetch one notebook entry (full body + metadata) by id.
    Body: ``{id: str}``."""
    eid = _sanitize_experiment_id(payload.get("id"))
    if eid is None:
        return ({"error": "missing or invalid 'id'"}, 400)
    for e in _load_experiments():
        if e.get("id") == eid:
            return {"experiment": _typed_clone(e)}
    return ({"error": f"no experiment with id {eid!r}"}, 404)


@_agent_endpoint("create-experiment", write=True)
def _h_create_experiment(app, payload):
    """Create a new notebook entry in the active project. Body:
    ``{title?: str = "Untitled entry",
        body_md?: str = "",
        tags?: list[str] = []}``.

    Routes through `_normalise_experiment_entry` so size caps + tag
    dedup + plasmid/action/gel xref extraction apply automatically.
    Returns the freshly-stamped entry id."""
    title = payload.get("title") or "Untitled entry"
    body  = payload.get("body_md") or ""
    tags  = payload.get("tags") or []
    if not isinstance(tags, list):
        return ({"error": "'tags' must be a list of strings"}, 400)
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        entries = _load_experiments()
        existing_ids: set[str] = {
            e.get("id") for e in entries if e.get("id")  # type: ignore[misc]
        }
        new_id = _new_experiment_id(existing_ids)
        entry = _normalise_experiment_entry({
            "id":      new_id,
            "title":   title if isinstance(title, str) else "",
            "body_md": body  if isinstance(body, str)  else "",
            "tags":    tags,
        }, fresh=True)
        entries.append(entry)
        err = _agent_save_or_500(
            lambda: _save_experiments(entries), "Experiments",
        )
        if err:
            return err
    _log_event("experiments.new", eid=new_id, via="agent")
    return {"ok": True, "id": new_id, "experiment": _typed_clone(entry)}


@_agent_endpoint("update-experiment", write=True)
def _h_update_experiment(app, payload):
    """Update an existing notebook entry. Body:
    ``{id: str, title?: str, body_md?: str, tags?: list[str]}``.
    Fields not supplied keep their prior value. Re-normalises so
    body-byte cap / tag dedup / xref extraction stay consistent."""
    eid = _sanitize_experiment_id(payload.get("id"))
    if eid is None:
        return ({"error": "missing or invalid 'id'"}, 400)
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        entries = _load_experiments()
        for i, e in enumerate(entries):
            if e.get("id") != eid:
                continue
            merged = dict(e)
            if "title" in payload:
                t = payload["title"]
                merged["title"] = t if isinstance(t, str) else ""
            if "body_md" in payload:
                b = payload["body_md"]
                merged["body_md"] = b if isinstance(b, str) else ""
            if "tags" in payload:
                tg = payload["tags"]
                if not isinstance(tg, list):
                    return ({"error": "'tags' must be a list of strings"}, 400)
                merged["tags"] = tg
            entries[i] = _normalise_experiment_entry(merged, fresh=False)
            err = _agent_save_or_500(
                lambda: _save_experiments(entries), "Experiments",
            )
            if err:
                return err
            _log_event("experiments.save", eid=eid, via="agent")
            return {"ok": True, "experiment": _typed_clone(entries[i])}
    return ({"error": f"no experiment with id {eid!r}"}, 404)


@_agent_endpoint("create-experiment-project", write=True)
def _h_create_experiment_project(app, payload):
    """Create a new (empty) experiment project. Body:
    ``{name: str, description?: str = ""}``. Active-project pointer
    is NOT modified by this endpoint — call
    ``set-active-experiment-project`` afterwards if you want to
    work in the new project."""
    name = _sanitize_label(payload.get("name"), max_len=200)
    if not name:
        return ({"error": "missing 'name'"}, 400)
    desc = _sanitize_note(payload.get("description"))
    # Sweep #26: RMW under `_state._cache_lock`. Pre-fix the name-collision
    # check ran outside the lock so two concurrent creates with the
    # same name could both pass and both append.
    with _state._cache_lock:
        projs = _load_experiment_projects()
        if any((p.get("name") or "").strip() == name for p in projs):
            return ({"error":
                      f"project named {name!r} already exists"}, 409)
        projs.append({
            "name":        name,
            "description": desc,
            "experiments": [],
            "saved":       _date.today().isoformat(),
        })
        err = _agent_save_or_500(
            lambda: _save_experiment_projects(projs),
            "Experiment projects",
        )
        if err:
            return err
    _log_event("project.created", name=name, via="agent")
    return {"ok": True, "name": name}


@_agent_endpoint("list-gels")
def _h_list_gels(app, payload):
    """List every saved gel snapshot. Returns id, name, lane count,
    agarose %, and timestamps per gel (omits per-lane detail — fetch
    via ``get-gel``)."""
    out: "list[dict]" = []
    for g in _load_gels():
        lanes = g.get("lanes") or []
        n_lanes = len(lanes) if isinstance(lanes, list) else 0
        out.append({
            "id":          g.get("id", ""),
            "name":        g.get("name", ""),
            "n_lanes":     n_lanes,
            "agarose_pct": g.get("agarose_pct", 1.0),
            "created_at":  g.get("created_at", ""),
            "updated_at":  g.get("updated_at", ""),
        })
    return {"gels": out}


@_agent_endpoint("get-gel")
def _h_get_gel(app, payload):
    """Fetch one saved gel snapshot (full lane payload + notes)."""
    gid = _sanitize_gel_id(payload.get("id"))
    if gid is None:
        return ({"error": "missing or invalid 'id'"}, 400)
    entry = _find_gel(gid)
    if entry is None:
        return ({"error": f"no gel with id {gid!r}"}, 404)
    return {"gel": _typed_clone(entry)}


@_agent_endpoint("create-gel", write=True)
def _h_create_gel(app, payload):
    """Save a new gel snapshot. Body:
    ``{name: str, lanes: list[dict], agarose_pct?: float = 1.0,
        notes?: str = ""}``.
    Routes through `_normalise_gel_entry` (caps + clamps). Returns
    the freshly-stamped id."""
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return ({"error": "missing 'name'"}, 400)
    name = name.strip()
    lanes = payload.get("lanes") or []
    if not isinstance(lanes, list):
        return ({"error": "'lanes' must be a list of dicts"}, 400)
    # Sweep #26 (2026-05-25): hold `_state._cache_lock` across the load +
    # mutate + save so concurrent agent calls can't both pass the
    # name-collision check and both append. Pre-fix two simultaneous
    # `create-gel` calls each loaded the same snapshot, each appended,
    # each saved — the second save's `_typed_clone(entries)` overwrote
    # the first's gel from the cache. RLock allows the nested re-entry
    # from `_save_gels`'s own locking.
    with _state._cache_lock:
        gels = _load_gels()
        if any((g.get("name") or "").strip() == name for g in gels):
            return ({"error": f"gel named {name!r} already exists"}, 409)
        existing_gel_ids: set[str] = {
            g.get("id") for g in gels if g.get("id")  # type: ignore[misc]
        }
        new_id = _new_gel_id(existing_gel_ids)
        entry = _normalise_gel_entry({
            "id":          new_id,
            "name":        name,
            "lanes":       lanes,
            "agarose_pct": payload.get("agarose_pct", 1.0),
            "notes":       payload.get("notes", ""),
        }, fresh=True)
        gels.append(entry)
        err = _agent_save_or_500(lambda: _save_gels(gels), "Gels")
    if err:
        return err
    _log_event("gel.created", gid=new_id, name=name, via="agent")
    return {"ok": True, "id": new_id, "gel": _typed_clone(entry)}


@_agent_endpoint("update-gel", write=True)
def _h_update_gel(app, payload):
    """Replace a saved gel's fields by id. Body:
    ``{id: str, name?: str, lanes?: list[dict],
        agarose_pct?: float, notes?: str}``. Fields not supplied
    keep their prior value. Re-normalises through `_normalise_gel_entry`."""
    gid = _sanitize_gel_id(payload.get("id"))
    if gid is None:
        return ({"error": "missing or invalid 'id'"}, 400)
    # Sweep #26: RMW under `_state._cache_lock` so concurrent renames /
    # field-merges don't drop each other's updates.
    with _state._cache_lock:
        gels = _load_gels()
        for i, g in enumerate(gels):
            if g.get("id") != gid:
                continue
            merged = dict(g)
            if "name" in payload:
                nm = payload["name"]
                if not isinstance(nm, str) or not nm.strip():
                    return ({"error":
                              "'name' must be a non-empty string"}, 400)
                new_name = nm.strip()
                if new_name != merged.get("name") and any(
                    (other.get("name") or "").strip() == new_name
                    for j, other in enumerate(gels) if j != i
                ):
                    return ({"error":
                              f"gel named {new_name!r} already exists"}, 409)
                merged["name"] = new_name
            if "lanes" in payload:
                ln = payload["lanes"]
                if not isinstance(ln, list):
                    return ({"error": "'lanes' must be a list"}, 400)
                merged["lanes"] = ln
            if "agarose_pct" in payload:
                merged["agarose_pct"] = payload["agarose_pct"]
            if "notes" in payload:
                nt = payload["notes"]
                merged["notes"] = nt if isinstance(nt, str) else ""
            gels[i] = _normalise_gel_entry(merged, fresh=False)
            err = _agent_save_or_500(lambda: _save_gels(gels), "Gels")
            if err:
                return err
            _log_event("gel.renamed", gid=gid, via="agent") \
                if "name" in payload else None
            return {"ok": True, "gel": _typed_clone(gels[i])}
    return ({"error": f"no gel with id {gid!r}"}, 404)


@_agent_endpoint("delete-gel", write=True)
def _h_delete_gel(app, payload):
    """Delete a saved gel snapshot by id."""
    gid = _sanitize_gel_id(payload.get("id"))
    if gid is None:
        return ({"error": "missing or invalid 'id'"}, 400)
    # Sweep #26: RMW under `_state._cache_lock` so a concurrent create-gel
    # can't insert into the snapshot we read but didn't persist.
    with _state._cache_lock:
        gels = _load_gels()
        new_gels = [g for g in gels if g.get("id") != gid]
        if len(new_gels) == len(gels):
            return ({"error": f"no gel with id {gid!r}"}, 404)
        err = _agent_save_or_500(
            lambda: _save_gels(new_gels), "Gels",
        )
    if err:
        return err
    _log_event("gel.deleted", gid=gid, via="agent")
    return {"ok": True, "id": gid, "remaining": len(new_gels)}


@_agent_endpoint("list-protein-motifs")
def _h_list_protein_motifs(app, payload):
    """List the merged protein-motif library (built-ins + user
    overrides). Each entry carries name, feature_type, sequence,
    color, description. Sourced from `_load_protein_motifs`."""
    return {"motifs": [
        {
            "name":         m.get("name", ""),
            "feature_type": m.get("feature_type", "Motif"),
            "sequence":     m.get("sequence", ""),
            "color":        m.get("color", ""),
            "description":  m.get("description", ""),
        }
        for m in _load_protein_motifs()
    ]}


@_agent_endpoint("set-protein-motif", write=True)
def _h_set_protein_motif(app, payload):
    """Create or override a protein motif. Body:
    ``{name: str, sequence: str, feature_type?: str = "Motif",
        color?: str = "", description?: str = ""}``.

    Persists only the user-modified entries (built-ins stay in code);
    `_load_protein_motifs` merges on read. If `name` matches a
    built-in, the user override replaces it in the merged list."""
    raw_name = payload.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return ({"error": "missing 'name'"}, 400)
    # Sweep #26: defense-in-depth — route name + feature_type +
    # description + color through `_sanitize_label` so an agent
    # cannot smuggle terminal escape codes into the on-disk JSON.
    name = _sanitize_label(raw_name.strip(), max_len=200)
    if not name:
        return ({"error": "missing 'name'"}, 400)
    seq = payload.get("sequence")
    if not isinstance(seq, str) or not seq.strip():
        return ({"error": "missing 'sequence'"}, 400)
    seq = seq.strip().upper()
    # Same single-letter AA validation as `_h_optimize_protein`.
    invalid = [c for c in seq if c not in "ACDEFGHIKLMNPQRSTVWY*"]
    if invalid:
        return ({"error":
                  "non-canonical amino acids in 'sequence': "
                  f"{''.join(sorted(set(invalid)))!r}"}, 400)
    raw_ftype = payload.get("feature_type") or "Motif"
    ftype = (
        _sanitize_label(raw_ftype, max_len=200) or "Motif"
        if isinstance(raw_ftype, str) else "Motif"
    )
    raw_color = payload.get("color") or ""
    color = _sanitize_label(raw_color, max_len=64) if isinstance(raw_color, str) else ""
    raw_desc = payload.get("description") or ""
    desc = _sanitize_label(raw_desc, max_len=2000) if isinstance(raw_desc, str) else ""
    # Sweep #26: RMW under `_state._cache_lock` so concurrent set-protein-motif
    # calls can't both load the user file, both append/override, and
    # both save — second save wins.
    with _state._cache_lock:
        # Load existing user file directly (NOT the merged list, which
        # includes built-ins). `_load_protein_motifs` merges on read, so
        # we re-read the raw user file to know which entries to persist.
        try:
            user_entries, _ = _safe_load_json(
                _state._PROTEIN_MOTIFS_FILE, "Protein motifs",
            )
        except Exception as exc:
            _log.exception("agent set-protein-motif: load failed")
            return ({"error": f"load failed: {exc}"}, 500)
        user_entries = [
            e for e in user_entries if isinstance(e, dict)
        ]
        # Drop any existing entry with the same name (copy-on-write).
        user_entries = [
            e for e in user_entries if e.get("name") != name
        ]
        user_entries.append({
            "name":         name,
            "feature_type": ftype,
            "sequence":     seq,
            "color":        color,
            "description":  desc,
        })
        err = _agent_save_or_500(
            lambda: _save_protein_motifs(user_entries),
            "Protein motifs",
        )
        if err:
            return err
    _log_event("synthesis.protein.motif_edit", name=name, via="agent")
    return {"ok": True, "name": name}


@_agent_endpoint("delete-protein-motif", write=True)
def _h_delete_protein_motif(app, payload):
    """Delete a user-stored protein motif override. Built-in
    motifs cannot be deleted — only user overrides. If `name`
    matches a built-in but the user has not edited it, returns 404
    (nothing to delete). If the user has overridden a built-in,
    deleting the override restores the original built-in entry."""
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return ({"error": "missing 'name'"}, 400)
    name = name.strip()
    # Sweep #26: RMW under `_state._cache_lock`.
    with _state._cache_lock:
        try:
            user_entries, _ = _safe_load_json(
                _state._PROTEIN_MOTIFS_FILE, "Protein motifs",
            )
        except Exception as exc:
            _log.exception("agent delete-protein-motif: load failed")
            return ({"error": f"load failed: {exc}"}, 500)
        user_entries = [
            e for e in user_entries if isinstance(e, dict)
        ]
        new_entries = [e for e in user_entries if e.get("name") != name]
        if len(new_entries) == len(user_entries):
            return ({"error":
                      f"no user-stored override for {name!r} "
                      "(built-ins cannot be deleted)"}, 404)
        err = _agent_save_or_500(
            lambda: _save_protein_motifs(new_entries),
            "Protein motifs",
        )
        if err:
            return err
    _log_event(
        "synthesis.protein.motif_edit",
        name=name, action="delete", via="agent",
    )
    return {"ok": True, "name": name}


_ENZYME_IUPAC_ALPHABET = frozenset("ACGTRYSWKMBDHVN")


def _agent_validate_custom_enzyme_payload(payload: dict) -> "dict | str":
    """Validate + canonicalise an agent-supplied custom-enzyme dict.
    Returns the persistable dict on success or an error string on
    failure — mirrors `AddCustomEnzymeModal._validate` so the agent
    surface accepts the same shape the UI does."""
    name = _sanitize_label(payload.get("name"), max_len=200)
    if not name:
        return "missing or non-string 'name'"
    if not (1 <= len(name) <= 64):
        return "'name' must be 1-64 characters"
    site = payload.get("site")
    if not isinstance(site, str) or not site.strip():
        return "missing or non-string 'site'"
    site = site.strip().upper()
    if not (4 <= len(site) <= 30):
        return "'site' must be 4-30 characters"
    bad = set(site) - _ENZYME_IUPAC_ALPHABET
    if bad:
        return f"'site' has non-IUPAC characters: {''.join(sorted(bad))!r}"
    fwd_raw = payload.get("fwd_cut")
    rev_raw = payload.get("rev_cut")
    if fwd_raw is None or rev_raw is None:
        return "missing 'fwd_cut' or 'rev_cut'"
    try:
        fwd_cut = int(fwd_raw)
        rev_cut = int(rev_raw)
    except (TypeError, ValueError):
        return "'fwd_cut' and 'rev_cut' must be integers"
    # Sweep #26: shared `_ENZYME_CUT_RANGE` constant — was hardcoded
    # ±30 in two places.
    lo, hi = -_ENZYME_CUT_RANGE, len(site) + _ENZYME_CUT_RANGE
    if not (lo <= fwd_cut <= hi) or not (lo <= rev_cut <= hi):
        return f"cut positions must be in {lo}..{hi}"
    ftype = _sanitize_label(payload.get("type"), max_len=64) or "other"
    supplier = _sanitize_label(payload.get("supplier"), max_len=64)
    return {
        "name":     name,
        "site":     site,
        "fwd_cut":  fwd_cut,
        "rev_cut":  rev_cut,
        "type":     ftype,
        "supplier": supplier,
    }


@_agent_endpoint("list-custom-enzymes")
def _h_list_custom_enzymes(app, payload):
    """List every user-added custom enzyme. Built-in NEB enzymes are
    NOT included — fetch the combined view via list-restriction-sites
    or `_state._all_enzymes_hook()` introspection. Each item carries name, site,
    fwd_cut, rev_cut, type, supplier."""
    return {"ok": True, "enzymes": _load_custom_enzymes()}


@_agent_endpoint("get-custom-enzyme")
def _h_get_custom_enzyme(app, payload):
    """Return a single custom enzyme by `name`. Body: ``{name}``."""
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return ({"error": "missing or non-string 'name'"}, 400)
    name = name.strip()
    meta = _custom_enzyme_meta(name)
    if meta is None:
        return ({"error": f"unknown custom enzyme {name!r}"}, 404)
    return {"ok": True, "enzyme": meta}


@_agent_endpoint("create-custom-enzyme", write=True)
def _h_create_custom_enzyme(app, payload):
    """Add a new custom enzyme. Body:
    ``{name, site, fwd_cut, rev_cut, type?, supplier?}``. Returns 409
    if `name` collides with an existing built-in OR custom enzyme.
    Mirrors `AddCustomEnzymeModal` validation rules.

    Sweep #25 (2026-05-23) — Built-in NEB enzyme names are
    intentionally reserved: ``create-custom-enzyme`` refuses any
    name in ``_state._all_enzymes_hook()``, and ``update-custom-enzyme`` only
    matches existing CUSTOM entries (not built-ins). The earlier
    "add a custom enzyme with the same name to override" note
    in this docstring was aspirational — the actual implementation
    treats built-in names as read-only. Users wanting an alternate
    cut convention for a known enzyme name should append a suffix
    (e.g. ``BsaI-isoschiz``) instead.

    Sweep #25 (2026-05-23): collision-check + load + append + save
    wrapped in ``_state._cache_lock`` so two concurrent agent calls can't
    both pass the collision check on an identical cache snapshot and
    then both append (silently dropping the first writer's entry on
    the second save). RLock allows re-entry from ``_save_custom_enzymes``.
    Matches the worker-side RMW pattern from INV-51.
    """
    payload_or_err = _agent_validate_custom_enzyme_payload(payload)
    if isinstance(payload_or_err, str):
        return ({"error": payload_or_err}, 400)
    with _state._cache_lock:
        if payload_or_err["name"] in _state._all_enzymes_hook():
            return ({"error":
                      f"enzyme {payload_or_err['name']!r} already exists; "
                      "use update-custom-enzyme to modify"}, 409)
        entries = _load_custom_enzymes()
        entries.append(payload_or_err)
        if (err := _agent_save_or_500(
                lambda: _save_custom_enzymes(entries),
                "Custom enzymes")) is not None:
            return err
    _log_event("custom_enzyme.added",
                name=payload_or_err["name"], via="agent")
    return {"ok": True, "name": payload_or_err["name"]}


@_agent_endpoint("update-custom-enzyme", write=True)
def _h_update_custom_enzyme(app, payload):
    """Replace an existing custom enzyme by `name`. Built-in NEB
    enzymes are not editable via this endpoint (refuse with 400) —
    add a custom enzyme with the same name to override.

    Sweep #25: full RMW under ``_state._cache_lock`` (see
    ``_h_create_custom_enzyme`` docstring for rationale).
    """
    payload_or_err = _agent_validate_custom_enzyme_payload(payload)
    if isinstance(payload_or_err, str):
        return ({"error": payload_or_err}, 400)
    name = payload_or_err["name"]
    with _state._cache_lock:
        entries = _load_custom_enzymes()
        for i, e in enumerate(entries):
            if e.get("name") == name:
                entries[i] = payload_or_err
                if (err := _agent_save_or_500(
                        lambda: _save_custom_enzymes(entries),
                        "Custom enzymes")) is not None:
                    return err
                return {"ok": True, "name": name}
    return ({"error": f"unknown custom enzyme {name!r}"}, 404)


@_agent_endpoint("delete-custom-enzyme", write=True)
def _h_delete_custom_enzyme(app, payload):
    """Delete a custom enzyme by `name`. Built-in NEB enzymes are
    refused. Any enzyme collection that referenced the deleted name
    keeps the stale row — `_active_enzyme_allowed_set` filters
    against `_state._all_enzymes_hook()` so the stale name is dropped at
    scan time without separate housekeeping.

    Sweep #25: full RMW under ``_state._cache_lock`` (see
    ``_h_create_custom_enzyme`` docstring for rationale).
    """
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return ({"error": "missing or non-string 'name'"}, 400)
    name = name.strip()
    with _state._cache_lock:
        entries = _load_custom_enzymes()
        new_entries = [e for e in entries if e.get("name") != name]
        if len(new_entries) == len(entries):
            return ({"error": f"unknown custom enzyme {name!r}"}, 404)
        if (err := _agent_save_or_500(
                lambda: _save_custom_enzymes(new_entries),
                "Custom enzymes")) is not None:
            return err
    _log_event("custom_enzyme.deleted", name=name, via="agent")
    return {"ok": True, "name": name}


@_agent_endpoint("list-enzyme-collections")
def _h_list_enzyme_collections(app, payload):
    """List every enzyme collection (named subset of the master
    catalog). Each item carries ``{name, enzymes: [str, ...]}``."""
    return {"ok": True, "collections": _load_enzyme_collections()}


@_agent_endpoint("create-enzyme-collection", write=True)
def _h_create_enzyme_collection(app, payload):
    """Create a new enzyme collection. Body:
    ``{name, enzymes?: [str, ...] = []}``. Returns 409 if a collection
    with the same name already exists. Unknown enzyme names are
    accepted at create time (they're filtered at scan time by
    `_active_enzyme_allowed_set` so the user can add custom enzymes
    later and have them participate retroactively)."""
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return ({"error": "missing or non-string 'name'"}, 400)
    name = name.strip()
    enzymes = payload.get("enzymes") or []
    if not isinstance(enzymes, list) or not all(
            isinstance(e, str) for e in enzymes):
        return ({"error": "'enzymes' must be a list of strings"}, 400)
    # Sweep #25: full RMW under `_state._cache_lock` (see
    # `_h_create_custom_enzyme` docstring for rationale).
    with _state._cache_lock:
        entries = _load_enzyme_collections()
        if any(e.get("name") == name for e in entries):
            return ({"error":
                      f"enzyme collection {name!r} already exists; "
                      "use update-enzyme-collection to modify"}, 409)
        entries.append({"name": name, "enzymes": sorted(set(enzymes))})
        if (err := _agent_save_or_500(
                lambda: _save_enzyme_collections(entries),
                "Enzyme collections")) is not None:
            return err
    return {"ok": True, "name": name}


@_agent_endpoint("clear-entry-vectors-for-grammar", write=True)
def _h_clear_entry_vectors_for_grammar(app, payload):
    """Drop every entry-vector binding for one grammar id. Body:
    ``{grammar_id: str}``. Used by grammar-delete flows; agents
    rarely need this directly but it round-trips for symmetry."""
    gid = payload.get("grammar_id")
    if not isinstance(gid, str) or not gid:
        return ({"error": "missing 'grammar_id'"}, 400)
    try:
        n = _clear_entry_vectors_for_grammar(gid)
    except (OSError, RuntimeError) as exc:
        return ({"error": f"clear failed: {exc}"}, 500)
    _log_event(
        "entry_vector.cleared",
        grammar=gid, n_cleared=n, via="agent",
    )
    return {"ok": True, "grammar_id": gid, "n_cleared": n}
