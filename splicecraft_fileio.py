"""splicecraft_fileio — single-file sequence-format I/O (Phase D, layer L2).

The plain file-format read/write core, extracted from the hub: FASTA / AB1 /
FASTQ / GFF3 ingest (path -> SeqRecord) + GenBank / GFF / FASTA / EMBL export
(record -> path), with their codec helpers. Biopython is imported lazily inside
the entry points exactly as in the hub. This is the CLEAN single-file subset —
the .dna CommercialSaaS reader/writer, the _save/_load_dna_original blob store,
the network fetch_genbank, and the zip / Plasmidsaurus member handling stay
hub-side (separate concerns / data-safety). Re-exported by the hub so `sc.<name>`
+ every call site (modals, agent endpoints, the loader) resolves unchanged.
"""
from __future__ import annotations

from copy import copy as _shallow_copy
from datetime import date as _date
from pathlib import Path

from splicecraft_logging import _log, _timed
from splicecraft_persistence import _atomic_write_text, _safe_file_size_check
from splicecraft_biology import _rc
from splicecraft_util import (
    _CONTROL_CHARS_RE, _DEFAULT_TYPE_COLORS, _feat_bounds, _safe_xml_parse,
)
from splicecraft_record import (
    _GB_LOCUS_NAME_MAX, _gb_text_to_record, _normalize_primer_seq, _record_to_gb_text,
)
from splicecraft_history import _CommercialSaaSHistoryNode, _history_human_dt


# Foreign-file ingest cap (GenBank / .dna / GFF3 the user *opens*, NOT the
# user's own data-dir JSON — that keeps the 1 GB cap above). A file passes
# the on-disk byte check and is then eagerly materialised by Biopython into
# a SeqRecord several× its size in RAM, so a ~1 GB record could OOM the
# process. No real plasmid / construct / chromosome an editor opens is this
# big; 256 MB is a generous ceiling that blocks the OOM without refusing any
# plausible file. (Chosen 2026-06-14; see attack-surface sweep.)
_GB_INGEST_MAX_BYTES = 256 * 1024 * 1024   # 256 MB

# Per-file size cap. Plasmids are typically <100 KB; anything larger is
# probably a chromosome dump or an unrelated file. Refuse rather than
# OOM the parser. 50 MB leaves headroom for huge cosmids / BACs.
_BULK_IMPORT_MAX_BYTES = 50 * 1024 * 1024

# AB1 Sanger trace files (single record, base-called via BioPython's
# `abi` SeqIO key). Plasmidsaurus zips include these and we now surface
# them as loadable entries.
_AB1_EXTS: frozenset[str] = frozenset({".ab1", ".abi"})

# FASTQ multi-read files (sequencing output). Imported as a new
# collection per file, same flow as multi-record FASTA. Quality scores
# are read but not surfaced in the plasmid map (the editor's primary
# axis is annotation, not basecall quality).
_FASTQ_EXTS: frozenset[str] = frozenset({".fastq", ".fq"})

# FASTQ multi-read import caps. Without them a 1 GB Illumina FASTQ
# (≈8 M reads) blows RAM before any guard fires, and a 100k-read FASTQ
# would create 100k library entries blowing `_BULK_IMPORT_MAX_BYTES`
# on the serialised library JSON. Plasmid editor != read aligner.
_FASTQ_MAX_READS = 1000


def _normalize_for_genbank(record):
    """Return a shallow copy of `record` with NCBI-required fields filled in.

    Idempotent — existing values are preserved. Only fills gaps. Caller's
    record is never mutated.
    """
    from datetime import datetime as _dt

    rec = _shallow_copy(record)
    anns = dict(getattr(record, "annotations", None) or {})

    anns.setdefault("molecule_type", "DNA")
    # 2026-05-27 (audit-3 H1): default to "linear" not "circular" so a
    # record imported from GFF3 / FASTA without an explicit topology
    # is NOT silently re-labelled circular on first GenBank save.
    # Topology is biologically load-bearing — getting it wrong changes
    # how every downstream tool (PCR sim, primer design, restriction
    # scan wrap detection) treats the sequence. Pre-fix all topology-
    # less imports flipped to circular on the first save.
    anns.setdefault("topology", "linear")
    anns.setdefault("data_file_division", "SYN")

    if not anns.get("date"):
        anns["date"] = _dt.now().strftime("%d-%b-%Y").upper()

    if not anns.get("accessions"):
        acc = rec.id if rec.id and rec.id != "<unknown id>" else ""
        anns["accessions"] = [acc]

    if not anns.get("organism"):
        anns["organism"] = "synthetic construct"
    if not anns.get("source"):
        anns["source"] = anns["organism"]
    if not anns.get("taxonomy"):
        anns["taxonomy"] = ["other sequences", "artificial sequences"]

    rec.annotations = anns

    # LOCUS name: spec is 16 chars; NCBI accepts up to 28 in practice.
    # Biopython itself warns if >16 but does not fail.
    # 2026-05-27 (audit-3 M2): surface a log warning + notify when
    # truncation actually happens so the user knows the LOCUS no
    # longer matches their display name.
    if rec.name and len(rec.name) > _GB_LOCUS_NAME_MAX:
        original_name = rec.name
        rec.name = rec.name[:_GB_LOCUS_NAME_MAX]
        _log.warning(
            "GenBank export: LOCUS name truncated from %d to %d chars: "
            "%r → %r",
            len(original_name), _GB_LOCUS_NAME_MAX,
            original_name, rec.name,
        )
    if not rec.name or rec.name == "<unknown name>":
        rec.name = (rec.id or "PLASMID")[:_GB_LOCUS_NAME_MAX] or "PLASMID"

    if not rec.description or rec.description == "<unknown description>":
        rec.description = rec.name

    if not rec.id or rec.id == "<unknown id>":
        rec.id = rec.name

    return rec


def _export_genbank_to_path(record, path) -> dict:
    """Write `record` to `path` as a GenBank file. Atomic + round-trip verified.

    Returns a small summary dict `{"path", "bp", "features"}` for UI reporting.

    Raises:
      OSError on filesystem failures (write, replace, fsync).
      ValueError if the round-trip parse fails or the parsed record
        disagrees with the source on sequence length, sequence content,
        or feature count — meaning the export is not byte-safe.

    The round-trip happens BEFORE the target file is touched, so a failed
    export never leaves a half-written / corrupt .gb at `path`.
    """
    from pathlib import Path as _Path

    p = _Path(path).expanduser()
    normalized = _normalize_for_genbank(record)
    text = _record_to_gb_text(normalized)

    # Round-trip verify before touching the filesystem
    try:
        parsed = _gb_text_to_record(text)
    except Exception as exc:
        raise ValueError(f"export round-trip parse failed: {exc}") from exc
    if len(parsed.seq) != len(normalized.seq):
        raise ValueError(
            f"export round-trip sequence length mismatch "
            f"({len(parsed.seq)} vs {len(normalized.seq)})"
        )
    if str(parsed.seq).upper() != str(normalized.seq).upper():
        raise ValueError("export round-trip sequence content mismatch")
    if len(parsed.features) != len(normalized.features):
        raise ValueError(
            f"export round-trip feature count mismatch "
            f"({len(parsed.features)} vs {len(normalized.features)})"
        )
    # 2026-05-27 (audit-3 H3): feature-COUNT check is necessary but
    # not sufficient. Biopython has historic edge cases where a
    # CompoundLocation flattens on write — the count survives but
    # the wrap structure is lost. Compare the SIGNATURE of every
    # feature (type + location-string + sorted qualifier items)
    # before touching disk so a flatten-on-write surfaces as a
    # round-trip failure rather than silent corruption.
    def _feature_signature(feat) -> "tuple":
        loc_str = str(getattr(feat, "location", "") or "")
        ftype   = getattr(feat, "type", "") or ""
        quals   = getattr(feat, "qualifiers", None) or {}
        # Qualifier values are typically list[str]; sort by key
        # then stringify each value list so the signature is
        # order-stable across writer round-trips.
        qual_sig = tuple(sorted(
            (k, tuple(v) if isinstance(v, (list, tuple)) else (str(v),))
            for k, v in quals.items()
        ))
        return (ftype, loc_str, qual_sig)

    src_sigs = sorted(_feature_signature(f) for f in normalized.features)
    dst_sigs = sorted(_feature_signature(f) for f in parsed.features)
    if src_sigs != dst_sigs:
        # Find the first divergence to surface in the error.
        diffs = [
            (s, d) for s, d in zip(src_sigs, dst_sigs) if s != d
        ]
        first_diff = diffs[0] if diffs else (None, None)
        raise ValueError(
            f"export round-trip feature signature mismatch "
            f"({len(diffs)} divergent features; first: "
            f"src={first_diff[0]!r} dst={first_diff[1]!r})"
        )

    _atomic_write_text(p, text)

    _log.info(
        "Exported GenBank to %s (%d bp, %d features)",
        p, len(normalized.seq), len(normalized.features),
    )
    return {"path": str(p), "bp": len(normalized.seq),
            "features": len(normalized.features)}


def _record_to_gff3(record) -> str:
    """Serialise `record` to GFF3 text (specification 1.26).

    GFF3 columns: seqid, source, type, start, end, score, strand, phase,
    attributes. Coordinates are 1-based inclusive — note the off-by-one
    versus SpliceCraft's internal 0-based half-open `[start, end)`. For
    each feature we emit one line per `FeatureLocation` part so wrap
    features (origin-spanning `CompoundLocation`) become two
    same-ID rows joined by a shared `ID=...` attribute, the standard
    GFF3 convention for split features. Circular records carry
    `Is_circular=true` on a synthesised top-level region row.
    """
    from urllib.parse import quote as _q

    seqid = (record.id or record.name or "plasmid").strip() or "plasmid"
    # GFF3 reserves a tighter set of seqid characters than GenBank LOCUS;
    # percent-encode anything outside `[A-Za-z0-9._:^*$@!+_?\-|]`.
    safe_seqid = _q(seqid, safe=".:_-")
    n = len(record.seq)
    is_circular = (
        (record.annotations or {}).get("topology", "").lower() == "circular"
    )

    out: list[str] = []
    out.append("##gff-version 3")
    if n:
        out.append(f"##sequence-region {safe_seqid} 1 {n}")
    # Synthesise a top-level region row so downstream consumers can see
    # the topology flag — Bio.SeqRecord doesn't surface it through the
    # features list otherwise. `region` is the GFF3 SO term for this.
    region_attrs = [f"ID={safe_seqid}"]
    if is_circular:
        region_attrs.append("Is_circular=true")
    if n:
        out.append("\t".join((
            safe_seqid, "SpliceCraft", "region",
            "1", str(n), ".", "+", ".",
            ";".join(region_attrs),
        )))

    auto_id = 0
    for feat in record.features:
        ftype = (feat.type or "misc_feature").strip() or "misc_feature"
        if ftype == "source":
            # Source features map to the synthetic `region` row above;
            # emitting both would double-list the whole-record span.
            continue
        try:
            strand_int = int(feat.location.strand or 0)
        except (AttributeError, TypeError, ValueError):
            strand_int = 0
        # NOTE: per-part strand is computed below in the parts loop
        # (see `part_gff_strand`); the feature-level strand glyph was
        # an earlier-revision artefact and is unused after the
        # mixed-strand `CompoundLocation` fix.
        # Pull a display name + extra qualifiers. Same precedence other
        # parts of the codebase use.
        name = ""
        try:
            for q in ("label", "gene", "product"):
                v = feat.qualifiers.get(q)
                if isinstance(v, list) and v:
                    name = str(v[0])
                    break
        except AttributeError:
            pass
        auto_id += 1
        feat_id = f"feat{auto_id}"
        attr_parts: list[str] = [f"ID={feat_id}"]
        if name:
            attr_parts.append(f"Name={_q(name, safe='')}")
        # All other qualifiers as Note-prefixed key/value, except the
        # ones we already mapped (label/gene/product) — except gene and
        # product can convey extra info, so emit them too as proper
        # GFF3 attributes when present.
        try:
            quals = feat.qualifiers
        except AttributeError:
            quals = {}
        for k, vlist in (quals or {}).items():
            if k in ("label",):
                continue
            if not isinstance(vlist, list):
                vlist = [str(vlist)]
            joined = ",".join(_q(str(v), safe="") for v in vlist)
            if k in ("gene", "product"):
                attr_parts.append(f"{k}={joined}")
            else:
                # GFF3 spec is strict — treat any unknown qualifier as
                # a Note (free-text). Multiple Notes get comma-joined.
                attr_parts.append(f"Note={_q(k + '=' + ','.join(str(v) for v in vlist), safe='')}")
        # Phase: CDS features default to 0 unless the qualifier
        # supplies a codon_start (1-based 1/2/3 → 0/1/2 phase).
        phase = "."
        if ftype.upper() == "CDS":
            try:
                cs = int((quals.get("codon_start", ["1"]) or ["1"])[0])
            except (TypeError, ValueError, IndexError):
                cs = 1
            phase = str(max(0, min(2, cs - 1)))
        # Iterate location parts so wrap features get one row per arc,
        # sharing the same ID — the GFF3 split-feature convention.
        # Order parts in biological 5'→3' direction so the split-feature
        # rows read in the order the ribosome (or polymerase) traverses
        # them. For forward-strand: ascending genomic start. For
        # reverse-strand: descending. A canonical-wrap forward feature
        # (`join(tail..total, 1..head)`) is already declared in 5'→3'
        # order (tail first), so sorting by start would REVERSE it; we
        # detect canonical wrap and keep it in declared order. Same for
        # reverse-strand wrap (head first, descending). Pre-2026-05-11
        # the loop iterated `parts` in declared order — fine for
        # GenBank-parsed wraps (which declare in biological order) but
        # wrong for programmatically constructed CompoundLocations.
        try:
            parts_seq = list(getattr(feat.location, "parts", None)
                              or [feat.location])
        except AttributeError:
            parts_seq = []
        try:
            is_wrap_canonical = (
                len(parts_seq) == 2
                and int(parts_seq[0].start) == 0
                and int(parts_seq[-1].end) == len(record.seq)
                and int(parts_seq[0].end) < int(parts_seq[-1].start)
            )
        except (AttributeError, TypeError, ValueError):
            is_wrap_canonical = False
        if is_wrap_canonical:
            # Tail first for + strand (biological 5'→3'); head first for - strand.
            parts = ([parts_seq[-1], parts_seq[0]] if strand_int != -1
                     else [parts_seq[0], parts_seq[-1]])
        else:
            try:
                parts_sorted = sorted(parts_seq, key=lambda p: int(p.start))
            except (AttributeError, TypeError, ValueError):
                parts_sorted = parts_seq
            parts = (parts_sorted if strand_int != -1
                     else list(reversed(parts_sorted)))
        for part in parts:
            try:
                p_s = int(part.start)
                p_e = int(part.end)
            except (AttributeError, TypeError, ValueError):
                continue
            if p_e <= p_s:
                continue
            # Per-part strand: a mixed-strand `CompoundLocation` has
            # `feat.location.strand == None` (Biopython returns None when
            # parts disagree), so the feature-level `gff_strand`
            # computation above flattened to "." and lost the per-part
            # info. Probe each `part.strand` so mixed-strand joins
            # (rare but legal in biology) emit the right +/- per arc.
            # Falls back to the feature-level strand when the part
            # itself has no strand attribute.
            part_strand = getattr(part, "strand", None)
            if part_strand is None:
                part_strand = strand_int or 0
            part_gff_strand = ("+" if part_strand == 1
                                else "-" if part_strand == -1 else ".")
            out.append("\t".join((
                safe_seqid,
                "SpliceCraft",
                ftype,
                # 1-based inclusive: start = p_s + 1; end = p_e.
                str(p_s + 1),
                str(p_e),
                ".",
                part_gff_strand,
                phase,
                ";".join(attr_parts),
            )))

    out.append("")  # trailing newline so cat-friendly tools don't
                    # complain about a missing final EOL.
    return "\n".join(out)


def _parse_gff3_text(text: str) -> dict:
    """Parse GFF3 text into a structured dict for the loader.

    Returns ``{seqid, length, is_circular, features, fasta_seq}``:
      * ``seqid``        — first seqid encountered (from sequence-region
                            directive, region row, or the first feature)
      * ``length``       — value from ``##sequence-region`` or the region
                            row, else None.
      * ``is_circular``  — True if the synthesised region row carries
                            ``Is_circular=true``.
      * ``features``     — list of dicts: ``{type, start_0, end, strand,
                            qualifiers, gff_id, phase}``. Wrap split-feat
                            rows share the same ``gff_id``; callers merge
                            them into one CompoundLocation.
      * ``fasta_seq``    — sequence string from the inline ``##FASTA``
                            directive (if present), else None.

    Coordinates are converted from GFF3 1-based inclusive to SpliceCraft
    0-based half-open. Raises ValueError on malformed input.
    """
    from urllib.parse import unquote as _u
    seqid = None
    length = None
    is_circular = False
    features: list[dict] = []
    fasta_seq: "str | None" = None
    in_fasta = False
    fasta_buf: list[str] = []

    for raw in text.splitlines():
        line = raw.rstrip("\n").rstrip("\r")
        if in_fasta:
            if line.startswith(">"):
                # 2026-05-27 (audit-3 M8): multi-record `##FASTA`
                # blocks are silently parsed as "first wins" pre-fix
                # — but the second `>` header losing its record is a
                # data-loss bug for the rare GFF3 file that bundles
                # multiple sequences. Raise loud so the user knows
                # the file isn't single-record and can split it.
                if fasta_buf:
                    raise ValueError(
                        "GFF3 ##FASTA block contains multiple records; "
                        "SpliceCraft requires a single-record ##FASTA. "
                        "Split the file and import each record "
                        "separately."
                    )
                continue
            if line:
                fasta_buf.append(line.strip())
            continue
        if not line:
            continue
        if line.startswith("##FASTA"):
            in_fasta = True
            continue
        if line.startswith("##sequence-region"):
            parts = line.split()
            if len(parts) >= 4:
                if seqid is None:
                    seqid = _u(parts[1])
                try:
                    length = int(parts[3])
                except ValueError:
                    pass
            continue
        if line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 9:
            continue
        seq_col = _u(cols[0])
        if seqid is None:
            seqid = seq_col
        ftype = cols[2].strip()
        try:
            start_1 = int(cols[3])
            end_1   = int(cols[4])
        except ValueError:
            continue
        if end_1 < start_1:
            continue
        strand = cols[6].strip()
        # Sweep #27: validate strand against the GFF3 spec
        # ({"+", "-", ".", "?"}). Pre-sweep ANY string silently mapped
        # to strand 0; a malformed GFF3 with embedded shell metas /
        # terminal escapes / HTML in the strand column would parse and
        # potentially surface in user-facing toasts unescaped. We now
        # SKIP the row (with a debug log) when the strand column isn't
        # one of the canonical four; matches the silent-skip semantics
        # GFF3 readers conventionally use for malformed records.
        if strand not in ("+", "-", ".", "?"):
            _log.debug(
                "GFF3 row skipped: invalid strand %r (must be one of "
                "'+', '-', '.', '?')", strand[:32],
            )
            continue
        strand_int = 1 if strand == "+" else (-1 if strand == "-" else 0)
        phase_s = cols[7].strip()
        try:
            phase = int(phase_s) if phase_s != "." else None
        except ValueError:
            phase = None
        attrs_raw = cols[8]
        quals: dict[str, list[str]] = {}
        gff_id = ""
        for kv in attrs_raw.split(";"):
            kv = kv.strip()
            if not kv or "=" not in kv:
                continue
            k, _, v = kv.partition("=")
            k = _u(k)
            vals = [_u(x) for x in v.split(",")]
            if k == "ID":
                gff_id = vals[0] if vals else ""
                continue
            if k == "Name":
                quals.setdefault("label", []).extend(vals)
                continue
            if k == "Is_circular":
                if ftype == "region" and vals and vals[0].lower() == "true":
                    is_circular = True
                continue
            if k == "Note":
                # `Note=key=value` round-trip (mirrors `_record_to_gff3`'s
                # encoding for unknown qualifiers).
                for v_one in vals:
                    if "=" in v_one:
                        nk, _, nv = v_one.partition("=")
                        quals.setdefault(nk, []).extend(
                            nv.split(",") if nv else [""]
                        )
                    else:
                        quals.setdefault("note", []).append(v_one)
                continue
            quals.setdefault(k, []).extend(vals)
        if ftype == "region":
            if length is None:
                length = end_1
            continue
        if ftype == "source":
            continue
        features.append({
            "type":       ftype,
            "start_0":    start_1 - 1,
            "end":        end_1,
            "strand":     strand_int,
            "qualifiers": quals,
            "gff_id":     gff_id,
            "phase":      phase,
        })
    if fasta_buf:
        fasta_seq = "".join(fasta_buf).upper()

    return {
        "seqid":       seqid or "plasmid",
        "length":      length,
        "is_circular": is_circular,
        "features":    features,
        "fasta_seq":   fasta_seq,
    }


def _gff3_features_to_biopython(
    parsed: dict, total: int,
) -> list:
    """Convert parsed GFF3 feature rows into BioPython SeqFeature
    objects. Same-`gff_id` rows are rejoined as a CompoundLocation
    (canonical wrap inverse of `_record_to_gff3`). Rows with coords
    outside ``[0, total)`` are dropped with a per-row warning log
    (sweep #25 2026-05-23 — pre-fix the drop was silent, so a GFF3
    with one bad row in a multi-part feature destroyed the entire
    feature with no user-visible signal).
    """
    from Bio.SeqFeature import (
        SeqFeature, FeatureLocation, CompoundLocation,
    )
    by_id: dict[str, list[dict]] = {}
    no_id: list[dict] = []
    for f in parsed["features"]:
        if f["gff_id"]:
            by_id.setdefault(f["gff_id"], []).append(f)
        else:
            no_id.append(f)

    out: list = []

    def _make_loc(parts: list[dict], gid: str = ""):
        # Build FeatureLocation per part, then merge into a
        # CompoundLocation when there are 2+ parts (wrap features).
        locs = []
        for p in parts:
            s, e = p["start_0"], p["end"]
            if s < 0 or e > total or e <= s:
                # Sweep #25: log instead of silent drop. Use INFO not
                # WARNING so a noisy GFF3 (e.g. coordinates relative
                # to a different reference) doesn't spam the log.
                _log.info(
                    "GFF3: dropping out-of-range feature %r "
                    "(type=%s start=%d end=%d total=%d)",
                    gid or "(no-id)", p.get("type") or "?", s, e, total,
                )
                return None
            locs.append(FeatureLocation(s, e, strand=p["strand"] or 0))
        if not locs:
            return None
        if len(locs) == 1:
            return locs[0]
        return CompoundLocation(locs)

    for gid, parts in by_id.items():
        loc = _make_loc(parts, gid=gid)
        if loc is None:
            continue
        first = parts[0]
        feat = SeqFeature(loc, type=first["type"],
                            qualifiers=dict(first["qualifiers"]))
        out.append(feat)
    for f in no_id:
        loc = _make_loc([f], gid=f.get("type") or "")
        if loc is None:
            continue
        feat = SeqFeature(loc, type=f["type"],
                            qualifiers=dict(f["qualifiers"]))
        out.append(feat)
    return out


def _gff3_path_to_record(path: str):
    """Load a GFF3 file as a SeqRecord.

    Requires an inline ``##FASTA`` directive — GFF3 alone carries no
    sequence and SpliceCraft is a sequence editor. Topology is set from
    the synthesised region row's ``Is_circular=true`` attribute (mirrors
    `_record_to_gff3`'s export convention).

    For sequence-less GFF3 files, use ``_gff3_apply_to_loaded_record``
    instead — that path treats the file as a feature-transfer overlay
    on the currently-loaded plasmid.

    Raises ValueError on malformed GFF3 or missing ``##FASTA`` section.
    """
    from pathlib import Path as _P
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord
    ok, reason = _safe_file_size_check(
        _P(path), _GB_INGEST_MAX_BYTES, "GFF3",
    )
    if not ok:
        raise ValueError(reason or "GFF3 file rejected")
    text = _P(path).read_text(encoding="utf-8", errors="replace")
    parsed = _parse_gff3_text(text)
    if parsed["fasta_seq"] is None:
        raise ValueError(
            "GFF3 file has no ##FASTA section. Standalone GFF3 import "
            "requires inline sequence; use the canvas-overlay path "
            "instead to apply features to the currently-loaded plasmid."
        )
    seq = parsed["fasta_seq"]
    total = len(seq)
    if total == 0:
        raise ValueError("GFF3 ##FASTA section is empty.")
    features = _gff3_features_to_biopython(parsed, total)
    seqid = parsed["seqid"]
    rec = SeqRecord(Seq(seq), id=seqid, name=seqid,
                     description=f"Imported from GFF3: {_P(path).name}",
                     features=features)
    rec.annotations["molecule_type"] = "DNA"
    rec.annotations["topology"]      = (
        "circular" if parsed["is_circular"] else "linear"
    )
    return rec


def _gff3_apply_to_loaded_record(record, path: str) -> int:
    """Feature-transfer mode for sequence-less GFF3 files.

    Reads features from `path` and appends them to ``record.features``
    in place. Returns the number of features added. Rejects rows whose
    coords don't fit within ``len(record.seq)`` (which the caller's
    sequence guards against tampering).

    Raises ValueError when the GFF3 file IS a complete record (has a
    ##FASTA section) — that case should use `_gff3_path_to_record`
    instead.
    """
    from pathlib import Path as _P
    ok, reason = _safe_file_size_check(
        _P(path), _GB_INGEST_MAX_BYTES, "GFF3",
    )
    if not ok:
        raise ValueError(reason or "GFF3 file rejected")
    text = _P(path).read_text(encoding="utf-8", errors="replace")
    parsed = _parse_gff3_text(text)
    if parsed["fasta_seq"] is not None:
        raise ValueError(
            "GFF3 file carries its own sequence (##FASTA). Use the "
            "standalone-import path so the imported plasmid isn't "
            "silently grafted onto the currently-loaded one."
        )
    total = len(record.seq)
    parsed_len = parsed.get("length")
    if parsed_len and parsed_len != total:
        raise ValueError(
            f"GFF3 declares length {parsed_len:,} but loaded plasmid "
            f"is {total:,} bp — coordinate frames don't match. Refusing "
            f"to apply features that would land at wrong positions."
        )
    new_feats = _gff3_features_to_biopython(parsed, total)
    record.features = list(record.features) + new_feats
    return len(new_feats)


def _export_gff_to_path(record, path) -> dict:
    """Write `record` to `path` as GFF3. Atomic write. Returns
    ``{"path", "bp", "features"}``.

    No round-trip verify (GFF3 has no canonical 1:1 reader in the
    standard library and Biopython's GFF support lives in BCBio.GFF
    which is an optional dep). The serialiser is deterministic and
    test-covered, so the same source record always produces the
    same output."""
    from pathlib import Path as _Path

    p = _Path(path).expanduser()
    text = _record_to_gff3(record)
    _atomic_write_text(p, text)
    _log.info(
        "Exported GFF3 to %s (%d bp, %d features)",
        p, len(record.seq),
        len([f for f in record.features if f.type != "source"]),
    )
    return {
        "path":     str(p),
        "bp":       len(record.seq),
        "features": len([f for f in record.features
                          if f.type != "source"]),
    }


def _export_fasta_to_path(name: str, sequence: str, path) -> dict:
    """Write `sequence` to `path` as a single-record FASTA. Atomic write.

    Returns `{"path", "bp", "name"}` on success. Raises:
      ValueError  — empty name or empty sequence.
      OSError     — filesystem failures (write, replace, fsync).

    The sequence is written on a single line (no hard-wrap at 80 chars);
    that matches what Biopython's default SeqIO writer emits for us
    elsewhere and keeps downstream `grep`/`awk` one-liners simple.
    """
    from pathlib import Path as _Path

    header = (name or "").strip()
    seq = (sequence or "").strip().upper()
    if not header:
        raise ValueError("FASTA export needs a non-empty record name.")
    if not seq:
        raise ValueError("FASTA export needs a non-empty sequence.")

    p = _Path(path).expanduser()
    _atomic_write_text(p, f">{header}\n{seq}\n")

    _log.info("Exported FASTA to %s (%s, %d bp)", p, header, len(seq))
    return {"path": str(p), "bp": len(seq), "name": header}


def _export_embl_to_path(record, path) -> dict:
    """Write `record` to `path` as EMBL flatfile via BioPython's SeqIO.

    EMBL is the European Nucleotide Archive's flatfile format — same
    feature-table model as GenBank, different text layout. Round-trip
    via SeqIO is straightforward; the writer preserves features,
    qualifiers, and circular topology. Atomic write.

    Returns ``{path, bp, features}`` on success. Raises:
      ValueError — record has no sequence.
      OSError    — filesystem failures (write, replace, fsync).
    """
    from pathlib import Path as _Path
    from io import StringIO
    from Bio import SeqIO

    if record is None or not getattr(record, "seq", None):
        raise ValueError("EMBL export needs a record with a sequence.")
    bp = len(record.seq)
    if bp == 0:
        raise ValueError("EMBL export needs a non-empty sequence.")

    # 2026-05-27 (audit-3 H6): route through `_normalize_for_genbank`
    # before the EMBL writer. Pre-fix EMBL export bypassed the
    # normalisation step that fills in molecule_type / topology /
    # accessions, so records constructed without these fields (GFF3
    # import, programmatically-built records) would raise mid-write
    # from inside Biopython's EMBL writer. EMBL's required-fields
    # set is a superset of GenBank's so the same normaliser covers it.
    normalized = _normalize_for_genbank(record)
    buf = StringIO()
    SeqIO.write([normalized], buf, "embl")
    text = buf.getvalue()

    p = _Path(path).expanduser()
    _atomic_write_text(p, text)

    n_feats = len([f for f in (record.features or [])
                   if f.type != "source"])
    _log.info("Exported EMBL to %s (%d bp, %d features)", p, bp, n_feats)
    return {"path": str(p), "bp": bp, "features": n_feats}


def _is_ab1_path(path) -> bool:
    try:
        suffix = getattr(path, "suffix", None)
        if suffix is None:
            suffix = Path(str(path)).suffix
    except Exception:
        return False
    return suffix.lower() in _AB1_EXTS


def _is_fastq_path(path) -> bool:
    try:
        suffix = getattr(path, "suffix", None)
        if suffix is None:
            suffix = Path(str(path)).suffix
    except Exception:
        return False
    return suffix.lower() in _FASTQ_EXTS


def _parse_fasta_single(path: str) -> tuple[str, str]:
    """Parse a FASTA file that must contain **exactly one** record and
    return ``(record_id, sequence)``.

    Multi-record FASTA files are rejected: the domesticator / parts bin
    flow only makes sense for a single part, so we surface a helpful
    error rather than silently picking the first record.

    Raises ``ValueError`` with a user-friendly message on any failure
    (read errors, zero records, multiple records, empty or non-IUPAC
    sequence). The sequence is upper-cased on success and validated
    against the IUPAC alphabet plus ``-``/``*``/``X`` for gap / stop /
    unknown.

    Size + symlink guard via ``_safe_file_size_check`` matches the
    `OpenFileModal` ingest pattern: rejects symlinks outright and
    refuses files larger than ``_BULK_IMPORT_MAX_BYTES`` (50 MB) so a
    multi-GB FASTA piped into the Domesticator's picker doesn't OOM
    the worker. Single-record FASTAs at the 50 MB ceiling already
    represent a 50 Mb sequence — bigger than this app supports.
    """
    from Bio import SeqIO
    ok, reason = _safe_file_size_check(
        Path(path), _BULK_IMPORT_MAX_BYTES, "FASTA",
    )
    if not ok:
        raise ValueError(reason or "FASTA file rejected by size check.")
    try:
        records = list(SeqIO.parse(path, "fasta"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Failed to read FASTA: {exc}") from exc
    if not records:
        raise ValueError("No FASTA records found in file.")
    if len(records) > 1:
        raise ValueError(
            f"Multi-sequence FASTA not supported ({len(records)} records "
            "found). Please provide a single-record FASTA."
        )
    rec = records[0]
    seq = str(rec.seq).upper()
    if not seq:
        raise ValueError("FASTA record has empty sequence.")
    valid = set("ACGTURYMKSWBDHVN-X*")
    bad = sorted(set(seq) - valid)
    if bad:
        raise ValueError(
            f"Non-IUPAC characters in sequence: {''.join(bad[:8])}"
        )
    return (rec.id or "fasta", seq)


def _fasta_path_to_record(path: str):
    """Parse a single-record FASTA at `path` into a `SeqRecord` ready
    to feed `_apply_record`.

    Defaults to `topology="linear"` because FASTA carries no topology
    annotation and assuming circular would mis-orient the user when
    they imported a chromosome chunk; the map's view-mode toggle
    flips to circular if the user knows otherwise. `molecule_type`
    defaults to ``"DNA"`` — protein FASTA is rejected upstream by
    `_parse_fasta_single`'s IUPAC check (which accepts ``X``/``*``
    but matches the codebase's plasmid-centric assumption that DNA
    is the right molecule).

    Raises whatever `_parse_fasta_single` raises for malformed input;
    callers are expected to surface the exception text via the modal
    status line.
    """
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord
    rec_id, seq = _parse_fasta_single(path)
    rec = SeqRecord(Seq(seq), id=rec_id, name=rec_id,
                     description=f"Imported from FASTA: {Path(path).name}")
    rec.annotations["molecule_type"] = "DNA"
    rec.annotations["topology"]      = "linear"
    return rec


def _ab1_path_to_record(path: str):
    """Parse a Sanger trace `.ab1` / `.abi` file into a `SeqRecord`.

    BioPython's `SeqIO.parse(path, "abi")` base-calls the trace
    automatically and produces a single SeqRecord with quality scores
    in `letter_annotations["phred_quality"]`. We force `topology=linear`
    because Sanger reads are linear sequencing fragments — circular
    interpretation would mis-orient the user.

    Size + symlink check via `_safe_file_size_check` at entry — typical
    AB1 traces are 200 KB to a few MB, but a hostile / corrupted file
    could be arbitrarily large. The cap mirrors `_BULK_IMPORT_MAX_BYTES`
    (50 MB) used by every other import path.

    Raises ValueError on malformed traces / missing base-call channels
    or files exceeding the size cap.
    """
    from Bio import SeqIO
    ok, reason = _safe_file_size_check(
        Path(path), _BULK_IMPORT_MAX_BYTES, "AB1",
    )
    if not ok:
        raise ValueError(reason or "AB1 trace rejected")
    try:
        records = list(SeqIO.parse(path, "abi"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not parse AB1 trace: {exc}") from exc
    if not records:
        raise ValueError(f"no base-called sequence in {Path(path).name}")
    if len(records) > 1:
        raise ValueError(
            f"AB1 file contains {len(records)} records; expected one"
        )
    rec = records[0]
    # AB1 records leave `name` / `id` as the sample name, which is fine.
    # Force molecule_type/topology so downstream library/save paths see
    # the same fields as every other linear import.
    rec.annotations["molecule_type"] = "DNA"
    rec.annotations["topology"]      = "linear"
    if not rec.description or rec.description == rec.id:
        rec.description = f"Imported from AB1 trace: {Path(path).name}"
    return rec


def _fastq_path_to_records(path: str) -> "list":
    """Parse a multi-read `.fastq` / `.fq` file into a list of
    `SeqRecord` objects. Each read becomes one record. Topology is
    forced to linear (reads are by definition linear fragments).

    Designed to feed `_handle_multi_record_fasta`'s collection-import
    flow — FASTQ becomes a new collection with one entry per read.
    Quality scores survive on `letter_annotations` but are not
    surfaced in the plasmid map (which is annotation-axis, not
    quality-axis).

    Two caps protect against pathological inputs:
      * File size capped at `_BULK_IMPORT_MAX_BYTES` (50 MB) via
        `_safe_file_size_check` — a multi-GB Illumina FASTQ would
        OOM `SeqIO.parse(...)`'s `list(...)` wrapper otherwise.
      * Read-count capped at `_FASTQ_MAX_READS` (1000) — even at the
        50 MB file cap a typical Illumina lane delivers ≫1000 short
        reads; the plasmid library wasn't designed to hold raw
        sequencing reads. Users with large datasets belong on the
        Plasmidsaurus tab (consensus-only) or in a dedicated aligner.

    Raises ValueError on malformed input, zero records, oversize, or
    read-count over cap.
    """
    from Bio import SeqIO
    ok, reason = _safe_file_size_check(
        Path(path), _BULK_IMPORT_MAX_BYTES, "FASTQ",
    )
    if not ok:
        raise ValueError(reason or "FASTQ file rejected")
    try:
        records = list(SeqIO.parse(path, "fastq"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not parse FASTQ: {exc}") from exc
    if not records:
        raise ValueError(f"no reads in {Path(path).name}")
    if len(records) > _FASTQ_MAX_READS:
        raise ValueError(
            f"FASTQ contains {len(records):,} reads; cap is "
            f"{_FASTQ_MAX_READS:,} per file. Split the file or use a "
            f"dedicated read aligner — the plasmid library isn't "
            f"designed for raw sequencing reads."
        )
    for rec in records:
        rec.annotations["molecule_type"] = "DNA"
        rec.annotations["topology"]      = "linear"
    return records



# ── CommercialSaaS .dna codec (Phase D, moved from hub) ─────────────────────
# Pure byte<->record codec for the SnapGene-style .dna binary format: the
# packet TLV reader/writer, history-XML extract/pack/inject, the from-scratch
# .dna emit (cookie/dna/features/notes packets), and the augment-from-packets
# recovery path. No data-dir writes, no network (the blob store, the loader,
# and fetch_genbank stay hub-side). Lazy struct/lzma/xml.etree/primer3 inside
# the fns exactly as in the hub.
# ── .dna packet I/O (low-level) ──────────────────────────────────────────────
#
# Binary format: each packet is a TLV — 1-byte type + 4-byte
# big-endian length + N bytes payload. The cookie packet (0x09)
# MUST come first; everything after is in implementation order.
# BioPython parses ~5 packet types (cookie, dna, primers, notes,
# features); the rest are silently dropped on read and impossible
# to write at all (BioPython has no .dna writer).
#
# These helpers go below the BioPython layer: they walk the raw
# byte stream, surface every packet (known or not), and let us
# round-trip files by splicing modified packets back into the
# original byte stream — preserving every packet we don't yet
# understand. Packet-type catalog (in progress) lives in
# `scripts/commercialsaas_inspect.py`'s ``KNOWN_PACKETS``.

_COMMERCIALSAAS_PACKET_HISTORY  = 0x07   # xz-compressed <HistoryTree> XML
_COMMERCIALSAAS_PACKET_COOKIE   = 0x09   # 8-byte format magic + 3 shorts
_COMMERCIALSAAS_HISTORY_MAX_XML = 32 * 1024 * 1024   # 32 MB hard cap on
                                                # decompressed payload —
                                                # protects against
                                                # decompression-bomb
                                                # crafted .dna files.


def _iter_commercialsaas_packets(data: bytes):
    """Yield ``(type_byte, length, payload_bytes)`` for every packet
    in a CommercialSaaS .dna byte stream. Raises ``ValueError`` on a
    declared-length overrun (audit-3 H2) OR a missing/wrong cookie
    packet at offset 0 (audit-3 M9, 2026-05-27).

    Empty input yields nothing (back-compat with test fixtures that
    pass `b""`). Non-empty input MUST start with a valid cookie
    packet — type byte 0x09, length 14, payload prefixed with the
    8-byte format magic. Pre-fix a malformed file lacking the cookie
    still parsed past the iterator and downstream consumers happily
    extracted features from junk — now the load refuses upfront so
    the user sees a clear "not a .dna file" error.

    Stops cleanly on EOF where a header doesn't fit (5-byte header;
    a partial trailer is treated as benign end-of-stream rather
    than a malformed-file error)."""
    import struct as _struct
    offset = 0
    n = len(data)
    # 2026-05-27 (audit-3 M9): cookie-packet validation gate.
    # Refuse files that don't start with the 0x09 cookie carrying the
    # 8-byte format magic. Empty input is allowed (yields nothing) so
    # back-compat tests keep working.
    if n > 0:
        if n < 5 + 8:
            raise ValueError(
                "CommercialSaaS .dna file too short for cookie packet "
                f"({n} bytes; need at least 13)"
            )
        if data[0] != _COMMERCIALSAAS_PACKET_COOKIE:
            raise ValueError(
                f"CommercialSaaS .dna file does not start with the "
                f"cookie packet (first byte 0x{data[0]:02X}, expected "
                f"0x{_COMMERCIALSAAS_PACKET_COOKIE:02X}). Not a valid "
                f".dna file."
            )
        if not data[5:5 + len(_COMMERCIALSAAS_COOKIE_MAGIC)].startswith(
                _COMMERCIALSAAS_COOKIE_MAGIC):
            raise ValueError(
                "CommercialSaaS .dna cookie packet payload doesn't "
                "carry the expected format magic. File is corrupt "
                "or not a valid .dna file."
            )
    while offset < n:
        if offset + 5 > n:
            # Trailing < 5 bytes: ambiguous whether benign EOF or
            # silent truncation. Treat as benign (the cookie + length
            # framing is designed to tolerate this — pre-2026-05-27
            # behaviour preserved here on purpose).
            return
        type_byte = data[offset]
        length = _struct.unpack(">I", data[offset + 1:offset + 5])[0]
        payload_start = offset + 5
        payload_end   = payload_start + length
        if payload_end > n:
            raise ValueError(
                f"CommercialSaaS packet length overrun at offset "
                f"{offset}: type=0x{type_byte:02X} declared {length} "
                f"bytes but only {n - payload_start} bytes remain. "
                f"File is truncated or corrupted; refusing to load "
                f"to avoid round-trip data loss."
            )
        yield (type_byte, length, data[payload_start:payload_end])
        offset = payload_end


def _build_commercialsaas_packet(type_byte: int, payload: bytes) -> bytes:
    """Serialise a single packet to bytes — type + 4-byte BE length +
    payload. Used by the writer + history-replace paths."""
    import struct as _struct
    if not (0 <= type_byte <= 0xFF):
        raise ValueError(f"packet type byte out of range: {type_byte!r}")
    if len(payload) > 0xFFFFFFFF:
        raise ValueError(f"payload too large for 32-bit length: "
                         f"{len(payload)} bytes")
    return bytes([type_byte]) + _struct.pack(">I", len(payload)) + payload


def _extract_commercialsaas_history_xml(data: bytes) -> "str | None":
    """Find the 0x07 history packet in a .dna byte stream and return
    its decompressed XML (UTF-8 text) — or ``None`` if no history
    packet exists. Decompression is xz (LZMA); a malformed payload
    raises ``ValueError`` rather than returning a truncated string.

    Streaming decompression with a per-call cap defeats decompression
    bombs: a 10 MB compressed payload that expands to gigabytes is
    aborted at ``_COMMERCIALSAAS_HISTORY_MAX_XML`` rather than
    OOM-ing the worker. The decoder reads up to ``cap + 1`` bytes; if
    it fills, we know the input exceeded the cap.
    """
    import lzma as _lzma
    cap = _COMMERCIALSAAS_HISTORY_MAX_XML
    for type_byte, length, payload in _iter_commercialsaas_packets(data):
        if type_byte != _COMMERCIALSAAS_PACKET_HISTORY:
            continue
        try:
            decoder = _lzma.LZMADecompressor()
            # Read up to cap + 1; if the decoder's not exhausted, we know
            # the input was bigger than the cap.
            decompressed = decoder.decompress(payload, max_length=cap + 1)
        except _lzma.LZMAError as exc:
            raise ValueError(
                f".dna history packet (0x07) is not valid xz: {exc}"
            ) from exc
        if len(decompressed) > cap or not decoder.eof:
            raise ValueError(
                f".dna history XML too large after decompression: "
                f">{cap:,} bytes (cap {cap:,})"
            )
        try:
            return decompressed.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f".dna history XML is not valid UTF-8: {exc}"
            ) from exc
    return None


def _pack_commercialsaas_history_payload(xml_text: str) -> bytes:
    """Inverse of `_extract_commercialsaas_history_xml`'s decompression
    step: xz-compress the XML so it can be written as a 0x07
    payload. Uses the default LZMA preset (matches what real
    CommercialSaaS files appear to use). Caller is responsible for
    wrapping in `_build_commercialsaas_packet(0x07, …)`."""
    import lzma as _lzma
    encoded = xml_text.encode("utf-8")
    return _lzma.compress(encoded)


def _inject_commercialsaas_history(data: bytes, new_xml: "str | None") -> bytes:
    """Replace (or insert / remove) the 0x07 history packet in a
    .dna byte stream while preserving every other packet verbatim.
    Returns a new bytes object — input is not mutated.

    - ``new_xml`` non-empty string  → replace existing 0x07 (or
      insert a fresh one immediately after the cookie packet).
    - ``new_xml`` ``None`` or empty → strip the history packet
      entirely. Result is a valid .dna without history.

    Insertion position: immediately after the cookie packet. Real
    CommercialSaaS files position 0x07 in implementation-defined order
    among the metadata packets; we follow the convention of "as
    early as possible after cookie" since that's where CommercialSaaS
    seems to write it on round-trip."""
    new_packet: "bytes | None" = None
    if new_xml:
        payload = _pack_commercialsaas_history_payload(new_xml)
        new_packet = _build_commercialsaas_packet(_COMMERCIALSAAS_PACKET_HISTORY,
                                              payload)
    # Collect once so we can decide where to insert based on whether
    # an existing history packet is present. Two-pass keeps the
    # in-place-replace and the after-cookie-insert paths from
    # competing — the prior single-pass version emitted a new
    # packet at the cookie position AND skipped the existing 0x07
    # later, effectively moving the history slot forward in the
    # file (regression caught 2026-05-06).
    packets = list(_iter_commercialsaas_packets(data))
    has_history = any(t == _COMMERCIALSAAS_PACKET_HISTORY for t, _, _ in packets)
    out: list[bytes] = []
    emitted = False
    for type_byte, _length, payload in packets:
        if type_byte == _COMMERCIALSAAS_PACKET_HISTORY:
            # Replace in place (or skip if removing).
            if new_packet is not None and not emitted:
                out.append(new_packet)
                emitted = True
            continue
        out.append(_build_commercialsaas_packet(type_byte, payload))
        if (not has_history
                and type_byte == _COMMERCIALSAAS_PACKET_COOKIE
                and new_packet is not None
                and not emitted):
            # No existing history — insert the new one immediately
            # after the cookie.
            out.append(new_packet)
            emitted = True
    # Edge case: file had no cookie AND no history (malformed?);
    # append the new history at the end so it's not lost.
    if new_packet is not None and not emitted:
        out.append(new_packet)
    return b"".join(out)


# ── CommercialSaaS .dna packet writers (Phase 3 — from-scratch .dna emit) ──────────
#
# Builds a minimum-viable `.dna` file from a SeqRecord: cookie +
# DNA + features + notes + (optional) history. Other packet types
# real CommercialSaaS files carry — primers, alignments, custom enzymes,
# enzyme visibility, additional sequence properties, etc. — are
# omitted; CommercialSaaS Viewer treats most of them as optional metadata
# and fills sensible defaults on first open. Round-trip fidelity
# for those cosmetic packets is a Phase 5 hardening item.
#
# Bytes read by `_iter_commercialsaas_packets` should round-trip back to
# bytes written here (with re-derived DNA / features XML), so the
# writer is testable against the existing reader without needing
# CommercialSaaS Viewer in the loop.

_COMMERCIALSAAS_PACKET_DNA      = 0x00
_COMMERCIALSAAS_PACKET_PRIMERS  = 0x05
_COMMERCIALSAAS_PACKET_NOTES    = 0x06
_COMMERCIALSAAS_PACKET_ADDPROPS = 0x08
_COMMERCIALSAAS_PACKET_FEATURES = 0x0A

# Cookie payload: 8-byte format magic + 3 unsigned shorts
# (seqType, exportVersion, importVersion). Values cribbed from
# real outputs of the commercial editor (`00 01 00 0f 00 13` =
# seqType 1, exp 15, imp 19) so files we emit advertise the same
# versions the current commercial release writes — gives us the best
# chance of being accepted without "old format" warnings. The 8-byte
# magic is stored hex-encoded so the trademarked string doesn't appear
# verbatim in source.
_COMMERCIALSAAS_COOKIE_MAGIC      = bytes.fromhex("536e617047656e65")  # 8 bytes
_COMMERCIALSAAS_COOKIE_SEQ_TYPE   = 1
_COMMERCIALSAAS_COOKIE_EXP_VER    = 15
_COMMERCIALSAAS_COOKIE_IMP_VER    = 19


def _build_commercialsaas_cookie_packet() -> bytes:
    """Build the 0x09 cookie packet — 14 bytes: 8-byte format magic + 3
    unsigned shorts."""
    import struct as _struct
    payload = _COMMERCIALSAAS_COOKIE_MAGIC + _struct.pack(
        ">HHH", _COMMERCIALSAAS_COOKIE_SEQ_TYPE,
        _COMMERCIALSAAS_COOKIE_EXP_VER, _COMMERCIALSAAS_COOKIE_IMP_VER,
    )
    return _build_commercialsaas_packet(_COMMERCIALSAAS_PACKET_COOKIE, payload)


def _build_commercialsaas_dna_packet(seq: str, *, circular: bool) -> bytes:
    """Build the 0x00 DNA packet — 1-byte flags + N-byte ASCII
    sequence. Real CommercialSaaS files appear to use lowercase bases
    in the payload; we lowercase to match the convention. Flag
    bit 0x01 = circular; other bits cleared (their meaning is
    not fully documented and CommercialSaaS defaults them on read).

    2026-05-27 (audit-3 H5): validate the sequence up front instead
    of relying on a bare ``.encode("ASCII")`` strict crash. A stray
    unicode char (BOM, en-dash, full-width letter) used to raise
    ``UnicodeEncodeError`` mid-write after the cookie packet was
    already serialised — the user saw an opaque traceback and lost
    the save. Now raises a clear ValueError BEFORE any output is
    emitted, naming the offending character so the user can find it.
    """
    if not isinstance(seq, str):
        raise ValueError(
            f"DNA packet sequence must be str (got {type(seq).__name__})"
        )
    bad: list[str] = []
    for ch in seq:
        if ord(ch) > 127:
            bad.append(ch)
            if len(bad) >= 3:
                break
    if bad:
        raise ValueError(
            f"DNA packet sequence contains non-ASCII character(s) "
            f"{', '.join(repr(c) for c in bad)}"
            f"{' (truncated)' if len(bad) >= 3 else ''}"
            f" — strip whitespace/unicode artefacts before export."
        )
    flags = 0x01 if circular else 0x00
    payload = bytes([flags]) + seq.lower().encode("ascii", "strict")
    return _build_commercialsaas_packet(_COMMERCIALSAAS_PACKET_DNA, payload)


def _build_commercialsaas_features_packet_from_record(record) -> bytes:
    """Build the 0x0A features packet by serialising every non-source
    feature from the record into CommercialSaaS's XML schema. Features
    with `CompoundLocation` parts emit one `<Segment>` per part
    (e.g., wrap features get 2 segments; spliced CDSes get one per
    exon)."""
    import xml.etree.ElementTree as _ET
    real_feats = [f for f in record.features if f.type != "source"]
    root = _ET.Element("Features",
                          nextValidID=str(len(real_feats)))
    for i, feat in enumerate(real_feats):
        attrs = {
            "recentID": str(i),
            "name":     _commercialsaas_feat_name(feat),
            "type":     feat.type or "misc_feature",
            "allowSegmentOverlaps": "0",
            "consecutiveTranslationNumbering": "1",
        }
        # Strand → CommercialSaaS's `directionality` attribute.
        # Forward = "1", reverse = "2", omit for unknown / unstranded.
        strand = feat.location.strand
        if strand == 1:
            attrs["directionality"] = "1"
        elif strand == -1:
            attrs["directionality"] = "2"
        feat_el = _ET.SubElement(root, "Feature", attrs)
        # Color: derive from `_DEFAULT_TYPE_COLORS` so newly-written
        # features get a sensible default that matches what SpliceCraft
        # renders. CommercialSaaS's library-wide colour map differs slightly,
        # but it gracefully accepts any 6-digit hex.
        color = _DEFAULT_TYPE_COLORS.get(feat.type or "", "#a6acb3")
        # Segments: one per CompoundLocation part; one for simple.
        for part in _commercialsaas_iter_location_parts(feat.location):
            start_1based = int(part.start) + 1
            end_1based   = int(part.end)
            _ET.SubElement(feat_el, "Segment", {
                "range": f"{start_1based}-{end_1based}",
                "color": color,
                "type":  "standard",
            })
        # Qualifiers: skip `label` (already in the `name` attribute).
        # Skip `translation` for non-CDS features (it's CDS-only and
        # CommercialSaaS re-derives it). Otherwise emit each value as a
        # `<V text=>` (or `<V int=>` when the value is an integer).
        for qname, qvals in (feat.qualifiers or {}).items():
            if qname == "label":
                continue
            q_el = _ET.SubElement(feat_el, "Q", {"name": qname})
            for v in (qvals if isinstance(qvals, list) else [qvals]):
                if isinstance(v, bool):
                    # Treat as int for the few CommercialSaaS attrs that use
                    # 0/1; bool subclasses int so isinstance comes
                    # first.
                    _ET.SubElement(q_el, "V", {"int": str(int(v))})
                elif isinstance(v, int):
                    _ET.SubElement(q_el, "V", {"int": str(v)})
                else:
                    # Sweep #11 (2026-05-20): strip control chars from
                    # the text attribute before serialising. Pre-fix
                    # hostile qualifier values containing `\x01`,
                    # `\x02`, etc. produced XML that BioPython
                    # accepted on write but `_safe_xml_parse` would
                    # choke on during round-trip — the file's
                    # `_augment_dna_record_from_packets` then silently
                    # dropped the colour + label overrides, losing
                    # the user's feature-styling work. Mirrors
                    # `_commercialsaas_feat_name` (line ~8230) which
                    # already strips controls from the feature label.
                    sanitised = "".join(
                        c if (c >= " " or c in "\t\n")
                        else " "
                        for c in str(v)
                    )
                    _ET.SubElement(q_el, "V", {"text": sanitised})
    body = _ET.tostring(root, encoding="unicode")
    xml = '<?xml version="1.0"?>' + body
    return _build_commercialsaas_packet(_COMMERCIALSAAS_PACKET_FEATURES,
                                     xml.encode("utf-8"))


def _build_commercialsaas_notes_packet(record) -> bytes:
    """Build the 0x06 notes packet from the SeqRecord's metadata.
    Always sets <Type>Synthetic</Type> (matches what CommercialSaaS writes
    for newly-built constructions); fills <Created> + <LastModified>
    from today's date, and <CreatedBy> as 'SpliceCraft' so users can
    tell at a glance which file came from where."""
    import xml.etree.ElementTree as _ET
    from datetime import datetime as _dt
    now = _dt.now()
    root = _ET.Element("Notes")
    _ET.SubElement(root, "Type").text = "Synthetic"
    _ET.SubElement(root, "ConfirmedExperimentally").text = "0"
    created = _ET.SubElement(root, "Created", {
        "UTC": now.strftime("%H:%M:%S"),
    })
    created.text = now.strftime("%Y.%m.%d")
    modified = _ET.SubElement(root, "LastModified", {
        "UTC": now.strftime("%H:%M:%S"),
    })
    modified.text = now.strftime("%Y.%m.%d")
    _ET.SubElement(root, "CreatedBy").text = "SpliceCraft"
    # Description from the record (mapped to `<Comments>` per BioPython
    # parser convention).
    desc = getattr(record, "description", "") or ""
    if desc and desc != "<unknown description>":
        _ET.SubElement(root, "Comments").text = str(desc)
    body = _ET.tostring(root, encoding="unicode")
    return _build_commercialsaas_packet(_COMMERCIALSAAS_PACKET_NOTES,
                                     body.encode("utf-8"))


def _extract_commercialsaas_file_date(data: bytes) -> "str | None":
    """Best-effort: pull the source file's own date (normalised to ISO
    ``YYYY-MM-DD``) from the CommercialSaaS Notes packet's ``<Created>`` (or
    ``<LastModified>``) element — the inverse of the ``<Created>`` that
    `_build_commercialsaas_notes_packet` writes. Lets an imported plasmid's
    top History entry show WHEN the source file was made (user request
    2026-06-10), since the construction-history nodes themselves carry no
    per-step dates. Returns None when absent / unparseable — never raises."""
    try:
        import xml.etree.ElementTree as _ET
        for type_byte, _length, payload in _iter_commercialsaas_packets(data):
            if type_byte != _COMMERCIALSAAS_PACKET_NOTES:
                continue
            try:
                # Parse from BYTES so an `<?xml … encoding=…?>` declaration in
                # a real file's notes is handled (a str with a decl raises).
                root = _ET.fromstring(payload)
            except _ET.ParseError:
                return None
            for tag in ("Created", "LastModified"):
                el = root.find(tag)
                text = (el.text or "").strip() if el is not None else ""
                if text:
                    iso = text.replace(".", "-")      # "2026.06.09" → ISO
                    if _history_human_dt(iso):         # validates it parses
                        return iso
            return None      # notes present but no usable date
    except Exception:
        _log.debug("commercialsaas file-date extract failed", exc_info=True)
    return None


def _commercialsaas_feat_name(feat) -> str:
    """Return the display name for a feature in the CommercialSaaS
    convention: prefer the `/label` qualifier, fall back to the
    feature's type. Strips control bytes for safety."""
    quals = feat.qualifiers or {}
    label = (quals.get("label") or quals.get("product") or [feat.type or "?"])
    name = str(label[0] if isinstance(label, list) else label)
    return _CONTROL_CHARS_RE.sub("", name)[:200] or "feature"


def _build_commercialsaas_addprops_packet_default() -> bytes:
    """Build a default 0x08 AdditionalSequenceProperties packet —
    Upstream/DownstreamStickiness="0" (= blunt) and Upstream/Downstream
    Modification=FivePrimePhosphorylated. Real CommercialSaaS files
    emit this even on circular plasmids where end-stickiness has no
    biological meaning; it's part of the editor's standard packet
    inventory and Viewer reads it for the "Sequence Properties"
    inspector. Pinning the 289-byte default matches all three FFE_*
    fixtures byte-for-byte.

    SpliceCraft doesn't currently model strand stickiness or end
    modifications, so a constant default is the right output —
    extending this when we gain a richer linear-fragment model is a
    future hardening item."""
    xml = (
        "<AdditionalSequenceProperties>"
        "<UpstreamStickiness>0</UpstreamStickiness>"
        "<DownstreamStickiness>0</DownstreamStickiness>"
        "<UpstreamModification>FivePrimePhosphorylated</UpstreamModification>"
        "<DownstreamModification>FivePrimePhosphorylated</DownstreamModification>"
        "</AdditionalSequenceProperties>"
    )
    return _build_commercialsaas_packet(_COMMERCIALSAAS_PACKET_ADDPROPS,
                                       xml.encode("utf-8"))


def _build_commercialsaas_primers_packet_default() -> bytes:
    """Build a minimum 0x05 Primers packet — root ``<Primers>`` with a
    single ``<HybridizationParams>`` child carrying the defaults the
    commercial editor writes on save (10 bp min continuous match, 1
    mismatch allowed, 40°C min Tm, 5'-end matching with 15 bp). No
    actual ``<Primer>`` entries — primer features (``primer_bind``)
    are still emitted via the 0x0A features packet.

    Real CommercialSaaS files always carry this packet even when no
    user-tracked primers exist — emitting it lets our from-scratch
    files mirror the editor's expected packet inventory and stops the
    Primers panel from defaulting to "(empty)" instead of the user's
    configured hybridization defaults. Three of the FFE_* test
    fixtures all carry exactly this 217-byte default; pinning it byte-
    for-byte gives us a regression target for ``CommercialSaaS Viewer``
    acceptance.
    """
    xml = (
        '<?xml version="1.0"?>'
        '<Primers nextValidID="0">'
        '<HybridizationParams '
        'minContinuousMatchLen="10" '
        'allowMismatch="1" '
        'minMeltingTemperature="40" '
        'showAdditionalFivePrimeMatches="1" '
        'minimumFivePrimeAnnealing="15"'
        '/>'
        '</Primers>'
    )
    return _build_commercialsaas_packet(_COMMERCIALSAAS_PACKET_PRIMERS,
                                       xml.encode("utf-8"))


def _commercialsaas_iter_location_parts(location):
    """Yield `(start, end)` simple parts for a feature location.
    Handles both `SimpleLocation` (single part) and `CompoundLocation`
    (multi-part; emits one part per sub-location)."""
    parts = getattr(location, "parts", None)
    if parts:
        for p in parts:
            yield p
    else:
        yield location


@_timed("op.write_commercialsaas_dna")
def _write_commercialsaas_dna_bytes(record, *,
                                 history_xml: "str | None" = None) -> bytes:
    """Return a complete `.dna` byte stream from a SeqRecord.

    Packet order: cookie → DNA → features → notes → (optional)
    history. CommercialSaaS tolerates implementation-defined order for
    non-cookie packets; this order matches what real CommercialSaaS
    output writes and what BioPython's parser handles smoothly.

    The result is round-trippable through `_iter_commercialsaas_packets`,
    re-parseable by BioPython's commercialsaas reader (sequence + features
    + topology + notes), and — if Phase 5 validation succeeds —
    accepted by CommercialSaaS Viewer. Until that validation is done,
    treat the writer as "expected to work; please test against
    CommercialSaaS Viewer and report rejections".
    """
    if record is None:
        raise ValueError("record is None")
    seq = str(getattr(record, "seq", "") or "")
    if not seq:
        raise ValueError("record has empty sequence")
    annotations = getattr(record, "annotations", None) or {}
    is_circ = (annotations.get("topology", "") or "").lower() == "circular"
    parts: list[bytes] = []
    parts.append(_build_commercialsaas_cookie_packet())
    parts.append(_build_commercialsaas_dna_packet(seq, circular=is_circ))
    parts.append(_build_commercialsaas_features_packet_from_record(record))
    parts.append(_build_commercialsaas_notes_packet(record))
    # Default 0x08 AdditionalSequenceProperties — strand stickiness +
    # end modifications. Real files carry this even on circular
    # plasmids where end-stickiness is meaningless; emitting the same
    # 289-byte default keeps the Sequence Properties inspector from
    # falling back to "(empty)" when Viewer reads our output.
    parts.append(_build_commercialsaas_addprops_packet_default())
    # Default 0x05 Primers packet — every real CommercialSaaS file
    # carries this with the same `HybridizationParams` defaults even
    # when no user primers are tracked. Emitting it ourselves keeps
    # the from-scratch output's packet inventory aligned with what the
    # editor produces, so Viewer can render the Primers panel cleanly
    # and never has to fall back to "(empty)" defaults.
    parts.append(_build_commercialsaas_primers_packet_default())
    if history_xml:
        payload = _pack_commercialsaas_history_payload(history_xml)
        parts.append(_build_commercialsaas_packet(
            _COMMERCIALSAAS_PACKET_HISTORY, payload))
    return b"".join(parts)


def _augment_dna_record_from_packets(
    rec, data: bytes,
) -> list[dict]:
    """Recover info BioPython's ``.dna`` parser drops:
      * **per-feature colours** from the 0x0A Features packet
        (``<Segment color="#RRGGBB"/>`` attributes). BioPython parses
        ``<Feature>`` name/type/location but throws the colour away —
        SpliceCraft then falls back to its rotating ``_FEATURE_PALETTE``,
        which gives correct-but-unfamiliar colours that don't match
        what the user saw in the original editor. This helper stamps
        ``ApEinfo_revcolor`` + ``ApEinfo_fwdcolor`` qualifiers on every
        non-source feature so the colour-read path in
        ``PlasmidMap._parse`` picks them up.
      * **primer sequence stamps** on every ``primer_bind`` feature so
        the seq panel renders them with the full primer machinery
        (flap detection, weak-primer arrow, partial-binding tooltip).
        Derived from the bound region's bases — forward primers take
        the top-strand sequence directly; reverse primers take the
        reverse-complement. Skipped if BioPython already provided a
        ``primer_seq`` (defensive — future BioPython versions may
        decode the 0x05 packet themselves).
      * **standalone <Primer> entries** from the 0x05 Primers packet,
        when present. Most user-saved ``.dna`` files keep the 0x05
        packet at its empty default (just ``HybridizationParams``), but
        files that the user has run primer-design on inside the editor
        carry real entries here; we surface them into ``primers.json``
        so the user's primer library mirrors what they had in the
        source file.

    Mutates ``rec`` in place. Returns a list of primer dicts (the
    ``primers.json`` shape) — one per primer_bind feature plus one per
    standalone 0x05 ``<Primer>`` entry, with duplicates by sequence
    already collapsed within this call. The caller (``_apply_record``)
    dedupes against the existing primer DB before persisting.
    """
    import xml.etree.ElementTree as _ET

    # Local Tm calculator — primer3 if available, 2+4 fallback otherwise.
    # Captured once at the top of the augment so we don't pay the import
    # cost for every primer entry we build below. Imported primers get a
    # computed Tm so the Primer Library table renders the same way it
    # does for designed primers (it does `f"{tm:.1f}°C"` on the value).
    try:
        import primer3 as _primer3
        def _calc_tm(s: str) -> float:
            try:
                return float(_primer3.calc_tm(s))
            except Exception:
                # primer3 occasionally barfs on weird sequences (very
                # short, contains N, etc.); fall back to the 2+4 rule.
                # Log so a wave of import-time degenerate primers
                # surfaces as a diagnosable bundle entry instead of
                # silent mis-Tm on every imported primer.
                _log.exception(
                    "import _calc_tm: primer3.calc_tm fell back to "
                    "GC approximation for %d-mer", len(s))
                gc = sum(1 for c in s.upper() if c in "GC")
                at = sum(1 for c in s.upper() if c in "AT")
                return float(2 * at + 4 * gc)
    except ImportError:
        def _calc_tm(s: str) -> float:
            gc = sum(1 for c in s.upper() if c in "GC")
            at = sum(1 for c in s.upper() if c in "AT")
            return float(2 * at + 4 * gc)

    feature_colors: list[str] = []
    feature_names_from_xml: list[str] = []
    standalone_primers: list[dict] = []

    for type_byte, _length, payload in _iter_commercialsaas_packets(data):
        if type_byte == _COMMERCIALSAAS_PACKET_FEATURES:
            try:
                root = _safe_xml_parse(payload.decode("utf-8"))
            except (_ET.ParseError, UnicodeDecodeError, ValueError):
                _log.warning("dna augment: 0x0A features packet parse failed")
                continue
            if root is None:
                continue
            for feat_el in root.findall(".//Feature"):
                seg = feat_el.find("Segment")
                feature_colors.append(
                    seg.get("color", "") if seg is not None else ""
                )
                # Capture the raw XML `name` attribute too. BioPython's
                # `.dna` parser has been observed to mangle whitespace
                # in feature names (GH #17, a user 2026-05-13:
                # spaces replaced with backslashes after import). We
                # pin the label to whatever the XML actually contains
                # so the user sees what their authoring tool wrote,
                # not whatever BioPython did on the way in. Strip the
                # control-char set we'd refuse to write anyway —
                # NUL / CR / LF would break a single-row sidebar
                # render — but SPACES + every other printable char
                # survive verbatim.
                xml_name = feat_el.get("name", "") or ""
                xml_name = _CONTROL_CHARS_RE.sub("", xml_name)[:200]
                feature_names_from_xml.append(xml_name)
        elif type_byte == _COMMERCIALSAAS_PACKET_PRIMERS:
            try:
                root = _safe_xml_parse(payload.decode("utf-8"))
            except (_ET.ParseError, UnicodeDecodeError, ValueError):
                _log.warning("dna augment: 0x05 primers packet parse failed")
                continue
            if root is None:
                continue
            today = _date.today().isoformat()
            for prim_el in root.findall(".//Primer"):
                pseq = (prim_el.get("sequence") or "").upper().replace("U", "T")
                if not pseq:
                    continue
                pname = (prim_el.get("name") or "").strip()
                if not pname:
                    pname = f"primer_{len(standalone_primers) + 1}"
                standalone_primers.append({
                    "name":        pname,
                    "sequence":    pseq,
                    "tm":          round(_calc_tm(pseq), 1),
                    "primer_type": "imported",
                    "source":      ".dna import",
                    "pos_start":   None,
                    "pos_end":     None,
                    "strand":      None,
                    "date":        today,
                    "status":      "Imported",
                })

    # Stamp colour qualifiers + override feature labels on features
    # by enumeration order. The 0x0A packet only carries the features
    # the editor itself created; any `source` row in the SeqRecord
    # comes from BioPython's LOCUS parsing (not the 0x0A packet) so
    # it doesn't consume a slot. Out-of-order or extra features are
    # tolerated — we stop when we run off the end of either list.
    #
    # Label override: BioPython's `.dna` parser has been observed to
    # mangle whitespace in feature names (GH #17 — user-typed
    # "Integration Seq" or "Lambda T0 Terminator" comes out with
    # backslashes inserted around the spaces). We pin
    # `qualifiers["label"]` to the raw XML name attribute so the
    # user sees what their authoring tool wrote, not whatever
    # BioPython produced. Skipped when the XML name is empty (some
    # third-party .dna writers omit the attribute), in which case
    # BioPython's parsed label survives untouched.
    feat_idx = 0
    for f in rec.features:
        if f.type == "source":
            continue
        # Color stamp (existing behaviour).
        if feat_idx < len(feature_colors):
            c = feature_colors[feat_idx]
            if c and isinstance(c, str):
                c = c.strip()
                # Defensive: only accept plausible CSS hex colours
                # so a malformed packet can't sneak arbitrary strings
                # into our qualifiers.
                if c.startswith("#") and len(c) in (4, 7):
                    f.qualifiers["ApEinfo_revcolor"] = [c]
                    f.qualifiers["ApEinfo_fwdcolor"] = [c]
        # Label override from raw XML.
        if feat_idx < len(feature_names_from_xml):
            xml_name = feature_names_from_xml[feat_idx]
            if xml_name:
                f.qualifiers["label"] = [xml_name]
        feat_idx += 1
        if (feat_idx >= len(feature_colors)
                and feat_idx >= len(feature_names_from_xml)):
            break

    # Build primer DB entries from the primer_bind features. Two
    # sources for the primer sequence:
    #   * `primer_seq` qualifier already on the feature (the round-trip
    #     case — splicecraft stamps this when it writes a .dna, and ApE
    #     uses the same convention). USE this verbatim so a primer
    #     carrying a 5' flap keeps its full length in the DB; deriving
    #     from the bound region would drop the flap.
    #   * Otherwise derive from the bound region (forward primers take
    #     the top strand directly; reverse primers take the RC).
    # Pre-2026-05-10 the code `continue`d on the qualifier-present
    # branch, which skipped the DB-entry append — so any .dna file
    # round-tripped through splicecraft (or exported from a tool that
    # stamps primer_seq) lost its primers from the imported DB.
    seq_str = str(rec.seq).upper() if getattr(rec, "seq", None) else ""
    n = len(seq_str)
    today = _date.today().isoformat()
    primer_bind_entries: list[dict] = []
    for f in rec.features:
        if f.type != "primer_bind":
            continue
        try:
            bounds = _feat_bounds(f, n)
        except (TypeError, ValueError, AttributeError):
            bounds = None
        if bounds is None:
            continue
        start, end, strand = bounds
        strand = strand or 1
        # Wrap-aware slice: origin-spanning primer_bind features have
        # `end < start` after `_feat_bounds` normalisation. Pre-fix the
        # raw `int(f.location.start)/.end` flattened the `CompoundLocation`
        # to (min, max) and `seq_str[0:n]` stamped the WHOLE plasmid as
        # primer_seq. Rare in practice (most primers don't cross the
        # origin) but biologically wrong when it happens.
        if end < start:
            if not (0 <= start < n and 0 <= end <= n):
                continue
            sliced_top = seq_str[start:] + seq_str[:end]
        else:
            if not (0 <= start < end <= n):
                continue
            sliced_top = seq_str[start:end]
        existing_pseq = f.qualifiers.get("primer_seq", [])
        if existing_pseq and isinstance(existing_pseq, list):
            bound_seq = _normalize_primer_seq(existing_pseq[0])
            if not bound_seq:
                bound_seq = sliced_top
                if strand < 0:
                    bound_seq = _rc(bound_seq)
        else:
            bound_seq = sliced_top
            if strand < 0:
                bound_seq = _rc(bound_seq)
            f.qualifiers["primer_seq"] = [bound_seq]
        labels = f.qualifiers.get("label", [])
        pname = str(labels[0]).strip() if labels else f"primer_{start}_{end}"
        primer_bind_entries.append({
            "name":        pname,
            "sequence":    bound_seq,
            "tm":          round(_calc_tm(bound_seq), 1),
            "primer_type": "imported",
            "source":      ".dna import",
            "pos_start":   start,
            "pos_end":     end,
            "strand":      strand,
            "date":        today,
            "status":      "Imported",
        })

    # Merge + dedupe by sequence (case-insensitive). Standalone 0x05
    # entries come first so their explicit ``<Primer name="...">`` wins
    # over an auto-generated ``primer_<start>_<end>`` name when the
    # same sequence appears in both places.
    merged: list[dict] = []
    seen: set[str] = set()
    for entry in standalone_primers + primer_bind_entries:
        key = (entry["sequence"] or "").upper()
        if not key or key in seen:
            continue
        merged.append(entry)
        seen.add(key)
    return merged


@_timed("op.parse_commercialsaas_history")
def _parse_commercialsaas_history(xml_text: str) -> "_CommercialSaaSHistoryNode | None":
    """Parse `<HistoryTree>` XML into a node tree. Returns the root
    `<Node>` (the result plasmid) or ``None`` if the XML is empty /
    has no nodes. Raises ``ValueError`` on malformed XML.

    Routes through `_safe_xml_parse` to defang billion-laughs / DOCTYPE
    entity-expansion attacks: .dna files come from external sources
    (collaborators, online repositories, scraped archives) and the
    history XML packet is the most attacker-controlled payload in the
    binary stream.
    """
    import xml.etree.ElementTree as _ET
    if not xml_text or not xml_text.strip():
        return None
    try:
        root = _safe_xml_parse(xml_text)
    except _ET.ParseError as exc:
        raise ValueError(f"Invalid .dna history XML: {exc}") from exc
    if root.tag != "HistoryTree":
        raise ValueError(
            f"Expected root <HistoryTree>, got <{root.tag}>"
        )
    nodes = root.findall("Node")
    if not nodes:
        return None
    if len(nodes) > 1:
        # CommercialSaaS's convention is one top-level Node per file. If we
        # see more, take the first and warn. 2026-05-27 (audit-3 M5):
        # stash the sibling raw elements on the wrapper so the serialise
        # path can re-emit them — pre-fix the siblings were dropped on
        # round-trip even though we held a reference, costing the user
        # any provenance trees the source file carried alongside the
        # primary tree.
        _log.warning(
            "CommercialSaaS history has %d top-level <Node> elements; "
            "expected 1. Using the first; preserving %d sibling(s) "
            "for round-trip.", len(nodes), len(nodes) - 1,
        )
    wrapper = _CommercialSaaSHistoryNode(nodes[0])
    wrapper._sibling_elements = list(nodes[1:])
    return wrapper
