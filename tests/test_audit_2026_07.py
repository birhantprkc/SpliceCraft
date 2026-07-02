"""Regression tests for the 2026-07-01 adversarial audit sweep.

Locks in the confirmed fixes from the whole-codebase audit:

  * BIO-1  overlapping tandem restriction sites (NotI/BstUI/HhaI-class borders)
  * REC-1  natural-sort crash on Unicode "digit" runs (superscripts)
  * NET-1  IPv4-mapped / 6to4 SSRF classification (gh-113171-independent)
  * NET-2  hardened opener scheme-lock (reject file:// / ftp:// / data:)
  * CLONE-2 codon-TSV "X" amino-acid column no longer rejects the table
  * REC-2  Babs `ollama_base` tolerates a malformed $OLLAMA_HOST port
  * REC-5  name sanitiser strips bidi/directional controls, keeps ZWJ/accents
  * AGENT-1 assemble-operon gene-count cap
  * FILEIO-2 FASTQ read-count cap (now enforced while iterating)

Pure / handler-level, fast. The autouse `_protect_user_data` fixture (conftest)
sandboxes every data-file write; nothing here touches the real data dir.
"""
from __future__ import annotations

import io
import ipaddress
import re

import pytest

import splicecraft as sc
import splicecraft_agent as _agent
import splicecraft_babs as _babs
import splicecraft_backup as _backup
import splicecraft_biology as _bio
import splicecraft_codon as _codon
import splicecraft_fileio as _fileio
import splicecraft_net as _net
import splicecraft_record as _record


# ── BIO-1: overlapping tandem restriction-site scan ─────────────────────────

def test_iter_match_starts_finds_overlapping_tandem():
    # `re.finditer` (non-overlapping) yields [0, 4]; the overlap-preserving
    # helper must yield every occurrence.
    assert list(_bio._iter_match_starts(re.compile("CGCG"), "CGCGCGCG")) == [0, 2, 4]


def test_iter_match_starts_notI_border():
    # NotI GCGGCCGC has border "GC"; two sites 6 bp apart both count.
    assert list(_bio._iter_match_starts(
        re.compile("GCGGCCGC"), "GCGGCCGCGGCCGC")) == [0, 6]


def test_iter_match_starts_no_match_terminates():
    assert list(_bio._iter_match_starts(re.compile("GAATTC"), "AAAAAA")) == []


def test_scanner_or_digest_sees_both_tandem_sites():
    # Integration: the digest primitive must report BOTH overlapping NotI cuts.
    if "NotI" not in sc._all_enzymes():
        pytest.skip("NotI not in enzyme catalog")
    cuts = _bio._enzyme_cuts("GCGGCCGCGGCCGC", ["NotI"], circular=False)
    assert len(cuts) == 2, cuts


# ── REC-1: natural sort survives Unicode "digit" runs ───────────────────────

@pytest.mark.parametrize("name", ["10²", "2⁵", "²", "x²y", "10⁶ cfu"])
def test_natural_sort_key_survives_superscripts(name):
    assert isinstance(sc._natural_sort_key(name), tuple)   # must not raise


def test_natural_sort_still_orders_ascii_numerically():
    assert sorted(["pBin10", "pBin2", "pBin1"], key=sc._natural_sort_key) == [
        "pBin1", "pBin2", "pBin10"]


# ── NET-1 / NET-2: SSRF classification + scheme lock ────────────────────────

@pytest.mark.parametrize("ip", [
    "::ffff:169.254.169.254",   # IPv4-mapped link-local (cloud metadata)
    "::ffff:127.0.0.1",         # IPv4-mapped loopback
    "::ffff:10.0.0.5",          # IPv4-mapped private
    "2002:a9fe:a9fe::",         # 6to4 embedding 169.254.169.254
    "127.0.0.1", "10.0.0.5", "169.254.169.254", "::1",
])
def test_ip_is_non_public_blocks_internal(ip):
    assert _net._ip_is_non_public(ipaddress.ip_address(ip)) is True


def test_ip_is_non_public_allows_public():
    assert _net._ip_is_non_public(ipaddress.ip_address("93.184.216.34")) is False
    assert _net._ip_is_non_public(ipaddress.ip_address("2606:2800:220:1::")) is False


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://internal.host/x", "data:text/plain,hi",
    "gopher://x/", "jar:file:///x",
])
def test_require_http_scheme_rejects_nonhttp(url):
    with pytest.raises(Exception):
        _net._require_http_scheme(url)


def test_require_http_scheme_allows_http_https():
    _net._require_http_scheme("http://example.com")       # no raise
    _net._require_http_scheme("https://example.com/db")   # no raise


# ── CLONE-2: codon TSV "X" amino-acid column ────────────────────────────────

def test_parse_codon_tsv_accepts_X_amino_acid():
    # "X" (any AA) must be ignored, not coerced to stop (which spuriously
    # rejected the whole table).
    raw = _codon._parse_codon_tsv("GCT X 100\nGCC A 80")
    assert "GCT" in raw and raw["GCT"][0] == "A"


def test_parse_codon_tsv_still_flags_real_mismatch():
    with pytest.raises(ValueError):
        _codon._parse_codon_tsv("GCT L 100")   # GCT encodes Ala, not Leu


def test_parse_codon_tsv_still_treats_dot_and_star_as_stop():
    for stop in (".", "*"):
        raw = _codon._parse_codon_tsv(f"TAA {stop} 50")
        assert raw["TAA"][0] == "*"


# ── REC-2: Babs ollama_base tolerates a malformed port ──────────────────────

@pytest.mark.parametrize("bad", ["host:999999", "host:abc", "http://h:70000"])
def test_ollama_base_bad_port_falls_back(monkeypatch, bad):
    monkeypatch.delenv("SPLICECRAFT_OLLAMA_HOST", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", bad)
    assert _babs.ollama_base() == _babs.DEFAULT_OLLAMA_HOST   # must not raise


# ── REC-5: name sanitiser strips bidi overrides, keeps script joiners ───────

def test_sanitize_label_strips_bidi_override():
    out = sc._sanitize_label("safe‮malicious")
    assert "‮" not in out and "⁦" not in out


def test_sanitize_label_keeps_accents_and_zwj():
    assert sc._sanitize_label("café") == "café"
    assert "‍" in sc._sanitize_label("a‍b")   # ZWJ preserved


# ── AGENT-1: assemble-operon gene-count cap ─────────────────────────────────

def test_assemble_operon_rejects_too_many_genes():
    genes = [{"cds": "ATGAAATAA", "target_strength": 1}
             for _ in range(_agent._ASSEMBLE_OPERON_MAX_GENES + 1)]
    res = sc._h_assemble_operon(None, {"genes": genes})
    assert isinstance(res, tuple) and res[1] == 400


def test_assemble_operon_still_accepts_empty_error():
    res = sc._h_assemble_operon(None, {"genes": []})
    assert isinstance(res, tuple) and res[1] == 400


# ── FILEIO-2: FASTQ read-count cap enforced while iterating ─────────────────

def test_fastq_over_cap_raises(tmp_path):
    n = _fileio._FASTQ_MAX_READS + 1
    p = tmp_path / "big.fastq"
    p.write_text("".join(f"@r{i}\nACGT\n+\n!!!!\n" for i in range(n)),
                 encoding="utf-8")
    with pytest.raises(ValueError):
        _fileio._fastq_path_to_records(str(p))


def test_fastq_under_cap_ok(tmp_path):
    p = tmp_path / "small.fastq"
    p.write_text("".join(f"@r{i}\nACGT\n+\n!!!!\n" for i in range(3)),
                 encoding="utf-8")
    recs = _fileio._fastq_path_to_records(str(p))
    assert len(recs) == 3


# ── REC-4: parse cache keyed on a 128-bit content hash (no collision) ────────

def test_gb_cache_key_distinct_and_stable():
    a = _record._gb_cache_key("LOCUS x\n//\n")
    b = _record._gb_cache_key("LOCUS y\n//\n")
    assert a != b
    assert a == _record._gb_cache_key("LOCUS x\n//\n")   # deterministic


# ── PERSIST-4: backup-dir override refuses shared system dirs ────────────────

@pytest.mark.parametrize("bad", ["/usr", "/usr/local", "/opt", "/var/lib"])
def test_backup_dir_refuses_shared_system_dirs(monkeypatch, bad):
    monkeypatch.setenv("SPLICECRAFT_UPDATE_BACKUP_DIR", bad)
    with pytest.raises(OSError):
        _backup._resolve_pre_update_backup_dir()


def test_backup_dir_allows_dedicated_subdir(tmp_path, monkeypatch):
    dedicated = tmp_path / "sc-backups"
    monkeypatch.setenv("SPLICECRAFT_UPDATE_BACKUP_DIR", str(dedicated))
    out = _backup._resolve_pre_update_backup_dir()
    assert str(out) == str(dedicated.resolve())


# ── FILEIO-4: per-base TSV summary bounds a newline-free blob ────────────────

def test_perbase_summary_bails_on_newline_free_blob():
    blob = b"1\t" + b"9" * (_fileio._PERBASE_MAX_LINE_BYTES + 100)
    out = _fileio._summarize_perbase_tsv(io.BytesIO(blob),
                                         max_bytes=50 * 1024 * 1024)
    assert out == {}


def test_perbase_summary_ok_on_normal_tsv():
    tsv = b"pos\tref\treads_all\n1\tA\t30\n2\tC\t40\n3\tG\t10\n"
    out = _fileio._summarize_perbase_tsv(io.BytesIO(tsv), max_bytes=1 << 20)
    assert out.get("n_pos") == 3
    assert out.get("mean") == (30 + 40 + 10) / 3
