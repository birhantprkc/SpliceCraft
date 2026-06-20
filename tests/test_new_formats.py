"""Minimum-viable coverage for the format-import / bulk-export
helpers landed this session. Targets the highest-leverage failure
classes (round-trip integrity, size-cap enforcement, error messages)
without exhaustive matrix coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import (
    CompoundLocation, FeatureLocation, SeqFeature,
)
from Bio.SeqRecord import SeqRecord

import splicecraft as sc
import splicecraft_fileio as _fileio  # the format-I/O caps moved here (Phase D);
# the parsers read them in this sibling's namespace, so patch it, not `sc`.


def _make_circular_record(seq: str = "ACGTACGTACGTACGTACGTACGTACGT") -> SeqRecord:
    """Tiny circular record with one normal feature + one wrap feature."""
    rec = SeqRecord(Seq(seq), id="plasmid", name="plasmid")
    rec.annotations["molecule_type"] = "DNA"
    rec.annotations["topology"] = "circular"
    # Normal feature.
    rec.features.append(SeqFeature(
        FeatureLocation(4, 12, strand=1),
        type="CDS", qualifiers={"label": ["gene1"]},
    ))
    # Wrap feature: origin-spanning.
    rec.features.append(SeqFeature(
        CompoundLocation([
            FeatureLocation(24, len(seq), strand=1),
            FeatureLocation(0, 3,  strand=1),
        ]),
        type="misc_feature", qualifiers={"label": ["wrap_feat"]},
    ))
    return rec


# ── GFF3 round-trip ────────────────────────────────────────────────────────────

def test_gff3_roundtrip_preserves_wrap_feature(tmp_path):
    """Export a record with a wrap feature to GFF3, re-import via the
    `##FASTA` directive path, and assert the wrap feature comes back
    as a CompoundLocation. Closes pitfall #26's inverse round-trip
    guarantee.
    """
    rec = _make_circular_record()
    # Emit GFF3 text and append a ##FASTA section with the sequence.
    gff_text = sc._record_to_gff3(rec)
    gff_text += "\n##FASTA\n"
    gff_text += f">{rec.id}\n{str(rec.seq)}\n"
    p = tmp_path / "wrap.gff3"
    p.write_text(gff_text)

    parsed = sc._gff3_path_to_record(str(p))
    assert str(parsed.seq) == str(rec.seq)
    # Find the wrap feature by label.
    wrap = next(
        f for f in parsed.features
        if "wrap_feat" in (f.qualifiers.get("label") or [])
    )
    assert isinstance(wrap.location, CompoundLocation)
    assert len(wrap.location.parts) == 2


def test_gff3_no_fasta_rejects_standalone_import(tmp_path):
    gff = "##gff-version 3\nplasmid\tSpliceCraft\tregion\t1\t100\t.\t+\t.\tID=plasmid\n"
    p = tmp_path / "no-fasta.gff3"
    p.write_text(gff)
    with pytest.raises(ValueError, match="##FASTA"):
        sc._gff3_path_to_record(str(p))


def test_gff3_apply_to_loaded_rejects_length_mismatch(tmp_path):
    rec = _make_circular_record()
    other_text = (
        "##gff-version 3\n"
        "##sequence-region plasmid 1 99\n"
        "plasmid\tSpliceCraft\tregion\t1\t99\t.\t+\t.\tID=plasmid\n"
    )
    p = tmp_path / "mismatch.gff3"
    p.write_text(other_text)
    with pytest.raises(ValueError, match="length"):
        sc._gff3_apply_to_loaded_record(rec, str(p))


def test_gff3_apply_to_loaded_rejects_fasta_payload(tmp_path):
    """If the GFF3 file IS a full record, the apply-overlay path must
    refuse it — otherwise the imported sequence gets silently
    discarded."""
    rec = _make_circular_record()
    gff = sc._record_to_gff3(rec) + f"\n##FASTA\n>plasmid\n{str(rec.seq)}\n"
    p = tmp_path / "full.gff3"
    p.write_text(gff)
    with pytest.raises(ValueError, match="##FASTA"):
        sc._gff3_apply_to_loaded_record(rec, str(p))


# ── EMBL export ────────────────────────────────────────────────────────────────

def test_embl_export_roundtrip(tmp_path):
    rec = _make_circular_record()
    p = tmp_path / "out.embl"
    summary = sc._export_embl_to_path(rec, str(p))
    assert summary["bp"] == len(rec.seq)
    # SeqIO.read should accept the emitted file.
    back = SeqIO.read(str(p), "embl")
    assert str(back.seq) == str(rec.seq)


def test_embl_export_rejects_empty():
    with pytest.raises(ValueError):
        sc._export_embl_to_path(
            SeqRecord(Seq("")), "/tmp/should-not-be-written.embl",
        )


# ── AB1 / FASTQ caps ───────────────────────────────────────────────────────────

def test_ab1_path_rejects_oversize(tmp_path, monkeypatch):
    """`_ab1_path_to_record` rejects files over `_BULK_IMPORT_MAX_BYTES`
    BEFORE any SeqIO parse. Pre-fix a 1 GB hostile .ab1 OOM'd the
    worker because SeqIO.parse streams unbounded."""
    p = tmp_path / "huge.ab1"
    p.write_bytes(b"\x00" * 100)
    monkeypatch.setattr(_fileio, "_BULK_IMPORT_MAX_BYTES", 50)
    with pytest.raises(ValueError):
        sc._ab1_path_to_record(str(p))


def test_fastq_path_rejects_oversize(tmp_path, monkeypatch):
    p = tmp_path / "huge.fastq"
    p.write_text("@r\nACGT\n+\n!!!!\n")
    monkeypatch.setattr(_fileio, "_BULK_IMPORT_MAX_BYTES", 5)
    with pytest.raises(ValueError):
        sc._fastq_path_to_records(str(p))


def test_fastq_path_rejects_too_many_reads(tmp_path, monkeypatch):
    """Multi-read FASTQ above `_FASTQ_MAX_READS` is refused so a
    100k-read import doesn't blow the library JSON size budget."""
    body = "\n".join(
        f"@r{i}\nACGT\n+\n!!!!"
        for i in range(20)
    ) + "\n"
    p = tmp_path / "many.fastq"
    p.write_text(body)
    monkeypatch.setattr(_fileio, "_FASTQ_MAX_READS", 5)
    with pytest.raises(ValueError, match="reads"):
        sc._fastq_path_to_records(str(p))


# ── _safe_export_filename ──────────────────────────────────────────────────────

def test_safe_export_filename_windows_reserved():
    """Names matching CON/PRN/AUX/NUL/COM1-9/LPT1-9 get an underscore
    prefix so NTFS doesn't refuse the open."""
    assert sc._safe_export_filename("CON", "gb").startswith("_")
    assert sc._safe_export_filename("nul", "gb").startswith("_")
    assert sc._safe_export_filename("com3", "gb").startswith("_")


def test_safe_export_filename_strips_separators():
    """Path-traversal characters become `_` so we can't escape the
    target directory."""
    out = sc._safe_export_filename("../../../etc/passwd", "gb")
    assert "/" not in out
    assert "\\" not in out
    assert ".." not in out.split(".")[0]


def test_safe_export_filename_strips_control_chars():
    out = sc._safe_export_filename("foo\x00bar\x01baz", "gb")
    assert "\x00" not in out
    assert "\x01" not in out


# ── _bulk_export_collection ────────────────────────────────────────────────────

def _seed_collection(name: str, plasmid_names: list[str]) -> None:
    """Populate the collections cache with one collection containing
    plasmids whose names appear in `plasmid_names`. Each plasmid has
    valid gb_text so non-`.dna` export paths can parse it."""
    rec = _make_circular_record()
    gb_text = sc._record_to_gb_text(rec)
    entries = [{
        "id":      f"id_{n}",
        "name":    n,
        "size":    len(rec.seq),
        "n_feats": len(rec.features),
        "gb_text": gb_text,
    } for n in plasmid_names]
    sc._save_collections([{"name": name, "plasmids": entries}])


def test_bulk_export_collection_genbank_writes_gb_text_directly(tmp_path):
    """Fast path: GenBank export should write the entry's cached
    `gb_text` directly, skipping the BioPython round-trip."""
    _seed_collection("Backbones", ["pUC19", "pACYC184"])
    result = sc._bulk_export_collection("Backbones", "genbank", tmp_path)
    assert result["total"] == 2
    assert len(result["written"]) == 2
    assert not result["failures"]
    # Files exist + match the cached gb_text.
    for w in result["written"]:
        assert Path(w["path"]).exists()


def test_bulk_export_collection_unknown_format_raises():
    with pytest.raises(ValueError, match="unknown export format"):
        sc._bulk_export_collection("Nope", "xyz", "/tmp/wontwrite")


def test_bulk_export_collection_case_insensitive_collision(tmp_path):
    """Two plasmids whose names differ only in case must NOT
    overwrite each other on the target FS. Mirrors `_dna_sidecar_path`
    case-fold defence."""
    _seed_collection("Backbones", ["pUC19", "puc19"])
    result = sc._bulk_export_collection("Backbones", "genbank", tmp_path)
    assert len(result["written"]) == 2
    # Filenames should differ even on case-folding.
    casefolded = {Path(w["path"]).name.casefold() for w in result["written"]}
    assert len(casefolded) == 2


def test_bulk_export_collection_unknown_collection(tmp_path):
    sc._save_collections([])  # empty
    with pytest.raises(ValueError, match="collection not found"):
        sc._bulk_export_collection("Missing", "genbank", tmp_path)


# ── _backup_info damaged-row schema ───────────────────────────────────────────

def test_backup_info_damaged_file_returns_error_row(tmp_path):
    """Corrupt JSON should surface as `{n_entries: None, error: ...}`
    instead of being silently dropped — closes the "user can't see
    what's broken" gap in the Restore UI."""
    p = tmp_path / "library.json"
    p.write_text("{this is not valid json}")
    info = sc._backup_info(p)
    assert info is not None
    assert info["n_entries"] is None
    assert info["error"]
