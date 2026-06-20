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
from pathlib import Path

from splicecraft_logging import _log
from splicecraft_persistence import _atomic_write_text, _safe_file_size_check
from splicecraft_record import (
    _GB_LOCUS_NAME_MAX, _gb_text_to_record, _record_to_gb_text,
)


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
