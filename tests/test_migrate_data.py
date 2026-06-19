"""
test_migrate_data — portable export / import of ALL user data (File ▸
Migrate Data, 2026-06-09).

The feature wraps a pre-update-style snapshot in one portable ``.zip``:
export = snapshot → atomic zip; import = unzip → `_restore_pre_update_snapshot`
(automatic pre-import backup + sha256-verified atomic replace). These tests
pin the data-safety contract: byte-for-byte round-trips (incl. the
content-addressed blob store + embedded construction history), and the
refusal of foreign / future-format / corrupt / path-traversal archives with
the live data left untouched. The migrate manifest derives from the same
`_USER_DATA_FILE_ATTRS` registry that test_smoke's classification test
enforces — so a new data file is migrated automatically.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile

import pytest

import splicecraft as sc


def _gb(name: str, seq: str) -> str:
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord
    r = SeqRecord(Seq(seq), id=name, name=name, description=name,
                  annotations={"molecule_type": "DNA", "topology": "circular"})
    return sc._record_to_gb_text(r)


def _seed() -> None:
    sc._save_collections([{"name": "Default", "plasmids": []}])
    sc._commit_library_entry_to_collection({
        "id": "p1", "name": "Plasmid 1", "size": 1200, "n_feats": 1,
        "source": "test", "added": "2026-06-09",
        "gb_text": _gb("P1", "ATGC" * 300),
        "history_xml": "<HistoryTree><Node>built</Node></HistoryTree>",
    }, "Default")
    sc._save_primers([{"name": "PR1", "sequence": "ACGTACGTACGT"}])
    sc._save_parts_bin([{"name": "part1", "type": "CDS", "sequence": "ATGAAATAA"}])


def _bust() -> None:
    for c in sc._MASTER_DELETE_CACHE_ATTRS:
        owner = sc._state if hasattr(sc._state, c) else sc
        if hasattr(owner, c):
            setattr(owner, c, None)


def _fingerprint() -> dict:
    fp = {}
    for attr in sc._USER_DATA_FILE_ATTRS:
        p = sc._resolve_state_or_hub(attr)
        if isinstance(p, sc.Path) and p.is_file():
            fp[attr] = hashlib.sha256(p.read_bytes()).hexdigest()
    bd = sc._plasmid_blob_dir()
    if bd.is_dir():
        for b in sorted(bd.rglob("*")):
            if b.is_file():
                fp["blob:" + b.name] = hashlib.sha256(b.read_bytes()).hexdigest()
    return fp


def _wipe() -> None:
    for attr in sc._USER_DATA_FILE_ATTRS:
        p = sc._resolve_state_or_hub(attr)
        if isinstance(p, sc.Path) and p.is_file():
            p.unlink()
    bd = sc._plasmid_blob_dir()
    if bd.is_dir():
        shutil.rmtree(bd)
    _bust()


class TestMigrateRoundTrip:
    def test_export_import_byte_for_byte(self, tmp_path):
        _seed()
        before = _fingerprint()
        assert len(before) >= 4
        zp = str(tmp_path / "data.zip")
        summ = sc._export_migrate_archive(zp)
        assert summ["bytes"] > 0
        assert zipfile.is_zipfile(zp)
        _wipe()
        assert not sc._state._COLLECTIONS_FILE.is_file()
        isumm = sc._import_migrate_archive(zp)
        assert isumm.get("pre_restore_snapshot"), "import must take a backup"
        assert not isumm.get("failed"), isumm.get("failed")
        _bust()
        assert _fingerprint() == before    # byte-for-byte, incl. blobs

    def test_history_and_sequences_survive(self, tmp_path):
        _seed()
        zp = str(tmp_path / "d.zip")
        sc._export_migrate_archive(zp)
        _wipe()
        sc._import_migrate_archive(zp)
        _bust()
        colls = sc._extract_entries(
            json.loads(sc._state._COLLECTIONS_FILE.read_text()), "c")[0] or []
        plasmids = next((c["plasmids"] for c in colls
                         if c.get("name") == "Default"), [])
        assert plasmids
        reh = [sc._rehydrate_entry(p) for p in plasmids]
        assert all("LOCUS" in (r.get("gb_text") or "") for r in reh)  # blobs
        assert any("HistoryTree" in (p.get("history_xml") or "")
                   for p in plasmids)

    def test_archive_layout(self, tmp_path):
        _seed()
        zp = str(tmp_path / "d.zip")
        sc._export_migrate_archive(zp)
        with zipfile.ZipFile(zp) as zf:
            names = zf.namelist()
        assert sc._MIGRATE_MARKER_NAME in names
        assert any(n.endswith("manifest.json") for n in names)
        assert any("plasmid_blobs" in n for n in names)   # sequences included

    def test_hmm_excluded_by_default_but_optional(self, tmp_path):
        _seed()
        hd = getattr(sc, "_HMM_DATABASES_DIR", None)
        if not isinstance(hd, sc.Path):
            pytest.skip("no HMM databases dir attr")
        hd.mkdir(parents=True, exist_ok=True)
        (hd / "Pfam.hmm").write_text("FAKE")
        z1 = str(tmp_path / "lean.zip")
        sc._export_migrate_archive(z1)
        with zipfile.ZipFile(z1) as zf:
            assert not any("Pfam.hmm" in n for n in zf.namelist())
        z2 = str(tmp_path / "full.zip")
        sc._export_migrate_archive(z2, include_hmm=True)
        with zipfile.ZipFile(z2) as zf:
            assert any("Pfam.hmm" in n for n in zf.namelist())


class TestMigrateRefusals:
    def test_foreign_zip_refused(self, tmp_path):
        z = str(tmp_path / "f.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("x.txt", "no")
        with pytest.raises(ValueError, match="not a SpliceCraft migrate"):
            sc._import_migrate_archive(z)

    def test_future_format_refused(self, tmp_path):
        z = str(tmp_path / "fu.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr(sc._MIGRATE_MARKER_NAME, json.dumps(
                {"format": "splicecraft-migrate", "format_version": 999}))
        with pytest.raises(ValueError, match="newer than this"):
            sc._import_migrate_archive(z)

    def test_path_traversal_refused(self, tmp_path):
        z = str(tmp_path / "ev.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr(sc._MIGRATE_MARKER_NAME, json.dumps(
                {"format": "splicecraft-migrate", "format_version": 1,
                 "snapshot_subdir": "data"}))
            zf.writestr("data/../../etc/evil", "pwn")
        with pytest.raises(ValueError, match="(?i)unsafe"):
            sc._import_migrate_archive(z)

    def test_corrupt_zip_refused_data_intact(self, tmp_path):
        _seed()
        before = _fingerprint()
        good = str(tmp_path / "g.zip")
        sc._export_migrate_archive(good)
        bad = str(tmp_path / "bad.zip")
        data = open(good, "rb").read()
        with open(bad, "wb") as f:
            f.write(data[: len(data) // 2])
        with pytest.raises((ValueError, OSError, zipfile.BadZipFile)):
            sc._import_migrate_archive(bad)
        _bust()
        assert _fingerprint() == before    # refused import left data intact

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            sc._import_migrate_archive(str(tmp_path / "nope.zip"))


class TestMigrateRegistryInvariant:
    def test_export_covers_registry(self, tmp_path):
        """Every registry FILE present on disk (minus default exclusions)
        appears in the export manifest — so a new data file added to the
        registry is migrated automatically (the futureproof invariant)."""
        _seed()
        zp = str(tmp_path / "d.zip")
        sc._export_migrate_archive(zp)
        with zipfile.ZipFile(zp) as zf:
            man = json.loads(zf.read("data/manifest.json"))
        manifest_attrs = {f["attr"] for f in man.get("files", [])}
        for attr in sc._USER_DATA_FILE_ATTRS:
            if attr in sc._MIGRATE_DEFAULT_EXCLUDE_ATTRS:
                continue
            p = sc._resolve_state_or_hub(attr)
            if isinstance(p, sc.Path) and p.is_file():
                assert attr in manifest_attrs, (
                    f"{attr} is in the user-data registry but missing from the "
                    "migrate manifest — keep _export_migrate_archive driven by "
                    "the registry.")
