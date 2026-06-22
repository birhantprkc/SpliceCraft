"""Regression tests for the 2026-06-22 deep-audit fixes (shipped v1.0.91).

Each test pins a confirmed defect found in the post-modularization adversarial
audit so it cannot silently regress. All behavioural (no `inspect.getsource`),
so editing the implementation never destabilises them. The `_protect_user_data`
autouse fixture (conftest) sandboxes `XDG_DATA_HOME` and authorises writes.
"""
from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest
from Bio.Seq import Seq
from Bio.SeqFeature import FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord

import splicecraft as sc


def _circular_rec(seq: str = "ATGC" * 20) -> SeqRecord:
    rec = SeqRecord(Seq(seq), id="t", name="t")
    rec.annotations["molecule_type"] = "DNA"
    rec.annotations["topology"] = "circular"
    return rec


# ── H1: a multi-line feature /note must not corrupt the saved plasmid ──
# Pre-fix, an embedded newline serialised to GenBank that BioPython's own
# parser rejected on reload — the saved entry became unloadable.
class TestMultilineNoteRoundTrip:
    def test_single_newline_note_roundtrips(self):
        rec = _circular_rec()
        f = SeqFeature(FeatureLocation(0, 9, strand=1), type="misc_feature")
        f.qualifiers["note"] = ["Line one\nLine two"]
        rec.features.append(f)
        gb = sc._record_to_gb_text(rec)
        r2 = sc._gb_text_to_record(gb)  # must NOT raise
        assert r2.features[0].qualifiers["note"] == ["Line one", "Line two"]

    def test_caller_record_not_mutated(self):
        rec = _circular_rec()
        f = SeqFeature(FeatureLocation(0, 9, strand=1), type="misc_feature")
        f.qualifiers["note"] = ["a\nb"]
        rec.features.append(f)
        sc._record_to_gb_text(rec)
        assert rec.features[0].qualifiers["note"] == ["a\nb"]  # copy-on-write

    def test_singleline_note_unchanged(self):
        rec = _circular_rec()
        f = SeqFeature(FeatureLocation(0, 9, strand=1), type="misc_feature")
        f.qualifiers["note"] = ["one clean line"]
        rec.features.append(f)
        r2 = sc._gb_text_to_record(sc._record_to_gb_text(rec))
        assert r2.features[0].qualifiers["note"] == ["one clean line"]

    def test_crlf_and_blank_lines_collapse(self):
        rec = _circular_rec()
        f = SeqFeature(FeatureLocation(0, 9, strand=1), type="misc_feature")
        f.qualifiers["note"] = ["para one\r\n\r\npara two\nstill two"]
        rec.features.append(f)
        r2 = sc._gb_text_to_record(sc._record_to_gb_text(rec))  # no raise
        assert r2.features[0].qualifiers["note"] == [
            "para one", "para two", "still two"]


# ── M1: isoschizomers cutting the IDENTICAL bond make ONE cut, not two ──
class TestCoincidentCutCollapse:
    def test_ecori_hf_isoschizomer_one_cut(self):
        seq = "GAATTC" + "A" * 30
        assert len(sc._digest_with_enzymes(seq, ["EcoRI"], circular=True)) == 1
        assert len(sc._digest_with_enzymes(
            seq, ["EcoRI", "EcoRI-HF"], circular=True)) == 1

    def test_gatc_trio_one_cut_per_site(self):
        seq = "GATC" + "A" * 20 + "GATC" + "T" * 20
        assert len(sc._digest_with_enzymes(seq, ["DpnII"], circular=True)) == 2
        assert len(sc._digest_with_enzymes(
            seq, ["DpnII", "Sau3AI", "MboI"], circular=True)) == 2

    def test_distinct_enzymes_still_both_cut(self):
        seq = "GAATTC" + "A" * 10 + "GGATCC" + "A" * 10
        assert len(sc._digest_with_enzymes(
            seq, ["EcoRI", "BamHI"], circular=True)) == 2

    def test_merged_enzyme_attribution(self):
        seq = "GAATTC" + "A" * 30
        cuts = sc._enzyme_cuts(seq, ["EcoRI", "EcoRI-HF"], circular=True)
        assert len(cuts) == 1
        assert set(cuts[0]["enzyme"].split("/")) == {"EcoRI", "EcoRI-HF"}


# ── M2: every backup writer/deleter honours the L2 write chokepoint ──
class TestBackupChokepoint:
    def test_restore_pre_update_refuses_unauthorized(self, monkeypatch):
        monkeypatch.setattr(sc._state, "_SAVES_AUTHORIZED", False)
        with pytest.raises(RuntimeError, match="not authoris"):
            sc._restore_pre_update_snapshot("latest")

    def test_snapshot_data_files_refuses_unauthorized(self, monkeypatch,
                                                       tmp_path):
        monkeypatch.setattr(sc._state, "_SAVES_AUTHORIZED", False)
        with pytest.raises(RuntimeError, match="not authoris"):
            sc._snapshot_data_files(tmp_path)

    def test_import_migrate_archive_refuses_unauthorized(self, monkeypatch,
                                                         tmp_path):
        monkeypatch.setattr(sc._state, "_SAVES_AUTHORIZED", False)
        z = tmp_path / "x.zip"
        z.write_bytes(b"PK\x03\x04")  # gate fires before any file validation
        with pytest.raises(RuntimeError, match="not authoris"):
            sc._import_migrate_archive(z)


# ── L3: $SPLICECRAFT_UPDATE_BACKUP_DIR can't point at a wipe-catastrophic dir ──
class TestBackupDirOverrideGuard:
    def test_home_rejected(self, monkeypatch):
        monkeypatch.setenv("SPLICECRAFT_UPDATE_BACKUP_DIR", str(Path.home()))
        with pytest.raises(OSError, match="Refusing"):
            sc._resolve_pre_update_backup_dir()

    def test_root_rejected(self, monkeypatch):
        monkeypatch.setenv("SPLICECRAFT_UPDATE_BACKUP_DIR", "/")
        with pytest.raises(OSError, match="Refusing"):
            sc._resolve_pre_update_backup_dir()

    def test_normal_dir_ok(self, monkeypatch, tmp_path):
        d = tmp_path / "backups"
        monkeypatch.setenv("SPLICECRAFT_UPDATE_BACKUP_DIR", str(d))
        assert sc._resolve_pre_update_backup_dir() == d.resolve()


# ── M3: the .dna Notes XML reader is billion-laughs-hardened ──
class TestDnaNotesXmlHardening:
    def _dna(self, xml: str) -> bytes:
        return (sc._build_commercialsaas_cookie_packet()
                + sc._build_commercialsaas_packet(
                    sc._COMMERCIALSAAS_PACKET_NOTES, xml.encode("utf-8")))

    def test_billion_laughs_refused_not_detonated(self):
        bomb = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<!DOCTYPE Notes [<!ENTITY a "AAAAAAAAAA">'
            '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
            '<Notes><Created>&c;</Created></Notes>')
        # Refused (DOCTYPE) → None, without expanding the entities.
        assert sc._extract_commercialsaas_file_date(self._dna(bomb)) is None

    def test_normal_notes_date_still_parses(self):
        xml = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<Notes><Created>2026.06.09</Created></Notes>')
        assert sc._extract_commercialsaas_file_date(
            self._dna(xml)) == "2026-06-09"


# ── M5: the hardened opener refuses non-public download hosts (SSRF) ──
class TestSsrfHostGuard:
    @pytest.mark.parametrize("url", [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1/", "http://10.0.0.5/",
        "http://192.168.1.10/", "http://[::1]/",
    ])
    def test_private_hosts_rejected(self, url):
        with pytest.raises(urllib.error.URLError):
            sc._assert_public_host(url)

    def test_public_ip_allowed(self):
        sc._assert_public_host("https://8.8.8.8/")  # must NOT raise

    def test_no_host_rejected(self):
        with pytest.raises(urllib.error.URLError):
            sc._assert_public_host("file:///etc/passwd")


# ── L1: New-Plasmid stamps the human display name (no underscores) ──
class TestNewPlasmidDisplayName:
    def test_build_record_stamps_tui_display_name(self):
        rec = sc.NewPlasmidModal._build_record(
            None, "my plasmid v1", "ATGC" * 10, True, [])
        assert rec._tui_display_name == "my plasmid v1"
        # The LOCUS still collapses to underscores + caps (round-trip safety) —
        # the display name lives in the stamp, not the LOCUS.
        assert rec.name == "my_plasmid_v1"


# ── L2: the NCBI fetchers clamp the accession before any network call ──
class TestFetchAccessionSanitised:
    def test_fetch_genbank_rejects_bad_accession(self):
        with pytest.raises(ValueError, match="invalid NCBI accession"):
            sc.fetch_genbank("L09137; rm -rf /")

    def test_fetch_protein_rejects_bad_accession(self):
        with pytest.raises(ValueError, match="invalid NCBI accession"):
            sc.fetch_protein("../../etc/passwd")


# ── Namespace hygiene: the user-data registries are ONE shared object ──
# backup, Master Delete, and migrate all drive from these; a hub/sibling drift
# would let them disagree on what counts as user data (data-loss class).
class TestBackupRegistryIdentity:
    def test_registries_same_object_across_hub_and_sibling(self):
        import splicecraft_backup as _bk
        for attr in ("_USER_DATA_FILE_ATTRS", "_USER_DATA_DIR_ATTRS",
                     "_OPERATIONAL_FILE_ATTRS"):
            assert getattr(sc, attr) is getattr(_bk, attr), (
                f"{attr} drifted between the hub and splicecraft_backup")
