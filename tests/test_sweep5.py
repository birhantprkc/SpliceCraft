"""test_sweep5 — regression coverage for adversarial audit sweep #5.

CLAUDE.md §45 documents the sweep. These tests lock in the data-integrity
fixes so a future refactor cannot quietly regress them: sidecar case-
collision discrimination, mandatory SHA-256 on pre-update restore,
manifest size cap, atomic `.bak` recovery, symlink refusal in
`_safe_save_json`, collision-bumped backup pruning, orphan tempfile
sweep, and the `_load_dna_original` size cap.

The `_protect_user_data` autouse fixture in `conftest.py` redirects every
`_*_FILE` constant to a temp dir, so even the few helpers that touch
module globals stay isolated from the developer's real data.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import splicecraft as sc


# ═══════════════════════════════════════════════════════════════════════════════
# T1a — sidecar case-collision (F-H1)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSidecarCaseCollision:
    """The pre-0.8.9 sidecar path was `<id>.dna`, which collided on
    case-insensitive filesystems (macOS APFS default, NTFS) between
    e.g. `pUC19` and `puc19` — silently overwriting each other's
    round-trip bytes. Fixed by case-folding the basename + appending
    an 8-char SHA-1 prefix of the raw (un-folded) id."""

    def test_case_variants_get_distinct_paths(self):
        a = sc._dna_sidecar_path("pUC19")
        b = sc._dna_sidecar_path("puc19")
        assert a != b
        # Basename starts case-folded so the path is stable on a
        # case-insensitive FS regardless of how the user typed it.
        assert a.name.startswith("puc19-")
        assert b.name.startswith("puc19-")
        # Hash discriminator differs.
        assert a.stem != b.stem

    def test_separator_sanitization_does_not_collide(self):
        # `a/b` and `a_b` both produce `a_b` after separator scrubbing —
        # without the hash discriminator they used to share a path.
        a = sc._dna_sidecar_path("a/b")
        b = sc._dna_sidecar_path("a_b")
        assert a != b

    def test_legacy_path_differs_from_canonical(self):
        eid = "pUC19"
        legacy = sc._dna_sidecar_legacy_path(eid)
        canonical = sc._dna_sidecar_path(eid)
        assert legacy != canonical
        assert legacy.name == "pUC19.dna"

    def test_path_length_capped(self):
        eid = "p" + "Z" * 2000
        p = sc._dna_sidecar_path(eid)
        # Suffix is `-<8-char-hash>.dna` = 13 chars. Total basename
        # must stay under the cap so NTFS's 260-char total path stays
        # reachable on reasonable installs.
        assert len(p.name) <= sc._DNA_SIDECAR_BASENAME_MAX

    def test_empty_id_yields_sentinel(self):
        p = sc._dna_sidecar_path("")
        assert p.name.startswith("_unknown_-") or p.name == "_unknown_.dna"

    def test_load_falls_back_to_legacy(self, tmp_path, monkeypatch):
        """_load_dna_original finds a pre-0.8.9 legacy-named sidecar
        when the canonical path is missing — migration coverage."""
        monkeypatch.setattr(sc, "_DNA_ORIGINALS_DIR", tmp_path)
        eid = "pUC19"
        legacy = sc._dna_sidecar_legacy_path(eid)
        legacy.write_bytes(b"legacy bytes")
        assert sc._load_dna_original(eid) == b"legacy bytes"

    def test_save_canonical_cleans_up_legacy(self, tmp_path, monkeypatch):
        """After a save to the canonical path, the legacy sidecar (if
        any) is unlinked so the migration is one-way and durable."""
        monkeypatch.setattr(sc, "_DNA_ORIGINALS_DIR", tmp_path)
        eid = "pUC19"
        legacy = sc._dna_sidecar_legacy_path(eid)
        legacy.write_bytes(b"legacy")
        ok = sc._save_dna_original(eid, b"fresh")
        assert ok
        assert not legacy.exists()
        canonical = sc._dna_sidecar_path(eid)
        assert canonical.read_bytes() == b"fresh"


# ═══════════════════════════════════════════════════════════════════════════════
# T1b — .bak recovery atomic (A-M2 / F-H4)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBakRecoveryAtomic:
    """`_safe_load_json` falls back to `.bak` when the main file is
    corrupt and rewrites the main file from the backup. Pre-0.8.9
    that rewrite used `shutil.copy2` — a power loss mid-recovery
    truncated the main file. Now routed through `_atomic_write_bytes`."""

    def test_recovery_routes_through_atomic_helper(self, tmp_path, monkeypatch):
        p = tmp_path / "test.json"
        sc._safe_save_json(p, [{"id": "first"}], "test")
        sc._safe_save_json(p, [{"id": "second"}], "test")
        # Corrupt the main file.
        p.write_text("not valid json {{{")
        # Track the atomic write helper.
        calls: list[Path] = []
        original = sc._atomic_write_bytes

        def tracking(path, data):
            calls.append(path)
            return original(path, data)

        monkeypatch.setattr(sc, "_atomic_write_bytes", tracking)
        entries, warning = sc._safe_load_json(p, "test")
        # Recovery returned the .bak content (the first save).
        assert entries == [{"id": "first"}]
        assert warning  # user-facing warning was generated
        # And the helper was used to rewrite the main file.
        assert p in calls
        # Main file is now valid JSON matching the recovered content.
        assert json.loads(p.read_text())["entries"] == [{"id": "first"}]


# ═══════════════════════════════════════════════════════════════════════════════
# T1c — SHA-256 mandatory on pre-update restore (A-H1)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPreUpdateSha256Mandatory:
    """`_restore_pre_update_snapshot` REFUSES restore when a manifest
    entry has no sha256 (pre-fix it silently skipped verify). The
    manifest lives in a user-writable dir so a tampered manifest with
    `sha256` stripped used to bypass invariant #39's sacred-four."""

    def _build_minimal_snapshot(self, snap_dir: Path,
                                  *, include_sha: bool) -> Path:
        snap_dir.mkdir(parents=True, exist_ok=True)
        # Drop a fake user-data file that the snapshot claims to back up.
        src = snap_dir / "plasmid_library.json"
        src.write_text(
            '{"_schema_version": 1, "entries": [{"id": "x"}]}',
            encoding="utf-8",
        )
        sha = sc._sha256_file(src) if include_sha else ""
        manifest = {
            "schema_version": sc._PRE_UPDATE_SCHEMA_VERSION,
            "from_version": "0.8.8",
            "files": [{
                "attr": "_LIBRARY_FILE",
                "name": "plasmid_library.json",
                "size": src.stat().st_size,
                # Field present but EMPTY when include_sha=False —
                # this is the tampered-manifest case the audit found.
                "sha256": sha,
            }],
            "directories": [],
        }
        (snap_dir / sc._PRE_UPDATE_MANIFEST_NAME).write_text(
            json.dumps(manifest), encoding="utf-8",
        )
        return snap_dir

    def test_missing_sha256_refused(self, tmp_path, monkeypatch):
        backup_root = tmp_path / "update-backups"
        snap_dir = backup_root / "20260515-000000-aaaaaaaa__from-0.8.8"
        self._build_minimal_snapshot(snap_dir, include_sha=False)
        live_lib = tmp_path / "data" / "plasmid_library.json"
        live_lib.parent.mkdir(parents=True, exist_ok=True)
        live_lib.write_text(
            '{"_schema_version": 1, "entries": []}',
            encoding="utf-8",
        )
        monkeypatch.setattr(sc, "_LIBRARY_FILE", live_lib)
        monkeypatch.setattr(
            sc, "_resolve_pre_update_backup_dir",
            lambda *_a, **_k: backup_root,
        )
        # Stub out the "pre-restore safety snapshot" the restore takes
        # before mutating live data — it would try to back up real
        # files; for this unit test we want only the manifest check.
        monkeypatch.setattr(
            sc, "_create_pre_update_snapshot",
            lambda *_a, **_k: tmp_path / "noop",
        )
        result = sc._restore_pre_update_snapshot(
            snap_dir.name, backup_dir=backup_root,
        )
        # Live file untouched; failure reported in the summary.
        assert any(
            "sha256" in reason and "missing" in reason.lower()
            for (_name, reason) in result.get("failed", [])
        )
        assert json.loads(live_lib.read_text())["entries"] == []

    def test_present_sha256_accepted(self, tmp_path, monkeypatch):
        backup_root = tmp_path / "update-backups"
        snap_dir = backup_root / "20260515-000000-bbbbbbbb__from-0.8.8"
        self._build_minimal_snapshot(snap_dir, include_sha=True)
        live_lib = tmp_path / "data" / "plasmid_library.json"
        live_lib.parent.mkdir(parents=True, exist_ok=True)
        live_lib.write_text(
            '{"_schema_version": 1, "entries": []}',
            encoding="utf-8",
        )
        monkeypatch.setattr(sc, "_LIBRARY_FILE", live_lib)
        monkeypatch.setattr(
            sc, "_resolve_pre_update_backup_dir",
            lambda *_a, **_k: backup_root,
        )
        monkeypatch.setattr(
            sc, "_create_pre_update_snapshot",
            lambda *_a, **_k: tmp_path / "noop",
        )
        result = sc._restore_pre_update_snapshot(
            snap_dir.name, backup_dir=backup_root,
        )
        assert "plasmid_library.json" in result.get("restored_files", [])
        assert json.loads(live_lib.read_text())["entries"] == [{"id": "x"}]


# ═══════════════════════════════════════════════════════════════════════════════
# T1d — Pre-update manifest size cap (A-H2)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPreUpdateManifestCap:
    """Manifests are pure metadata (~2–16 KB in practice). The cap
    rejects planted multi-GB manifests that would OOM the launch-time
    `_list_pre_update_snapshots` walk."""

    def test_oversized_manifest_skipped_in_listing(self, tmp_path):
        snap = tmp_path / "20260515-000000-cccccccc__from-0.8.8"
        snap.mkdir()
        manifest = snap / sc._PRE_UPDATE_MANIFEST_NAME
        # 5 MB > the 4 MB cap. Use a non-JSON body so even if the cap
        # were bypassed, the manifest wouldn't be parseable.
        manifest.write_text("x" * (5 * 1024 * 1024))
        result = sc._list_pre_update_snapshots(tmp_path)
        # Oversized manifest path filtered before parse.
        assert result == []

    def test_normal_manifest_listed(self, tmp_path):
        snap = tmp_path / "20260515-000000-dddddddd__from-0.8.8"
        snap.mkdir()
        manifest = snap / sc._PRE_UPDATE_MANIFEST_NAME
        manifest.write_text(json.dumps({
            "schema_version": sc._PRE_UPDATE_SCHEMA_VERSION,
            "from_version": "0.8.8",
            "files": [],
            "directories": [],
        }))
        result = sc._list_pre_update_snapshots(tmp_path)
        assert len(result) == 1
        assert result[0]["id"] == snap.name


# ═══════════════════════════════════════════════════════════════════════════════
# T1f — Backup glob includes collision-bumped files (F-H3)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackupGlobBumped:
    """Pre-0.8.9 `_backup_filename_pattern` matched only the base
    `<file>.bak.<ts>` form, so collision-bumped `.bak.<ts>.<N>` files
    (emitted by `_safe_save_json` when two saves landed in the same
    wall-second) leaked forever — slow-burn disk fill on rapid Ctrl+S."""

    def test_iter_backups_finds_bumped(self, tmp_path):
        target = tmp_path / "library.json"
        base = tmp_path / "library.json.bak.20260515-123456"
        bump1 = tmp_path / "library.json.bak.20260515-123456.1"
        bump2 = tmp_path / "library.json.bak.20260515-123456.2"
        unrelated = tmp_path / "library.json.bak.broken-name"
        for p in (base, bump1, bump2, unrelated):
            p.write_text("{}")
        found = sc._iter_backups(target)
        names = {p.name for p in found}
        assert base.name in names
        assert bump1.name in names
        assert bump2.name in names
        # The unrelated name doesn't match the ts pattern.
        assert unrelated.name not in names

    def test_prune_removes_bumped(self, tmp_path):
        target = tmp_path / "library.json"
        # Mix of base + bumped, deliberately more than the keep count.
        for i in range(12):
            ts = f"20260515-{i:06d}"
            (tmp_path / f"library.json.bak.{ts}").write_text("{}")
            (tmp_path / f"library.json.bak.{ts}.1").write_text("{}")
        sc._prune_backups(target, keep=5)
        survivors = list(tmp_path.glob("library.json.bak.*"))
        assert len(survivors) == 5


# ═══════════════════════════════════════════════════════════════════════════════
# T1g — _safe_save_json refuses symlinked targets (F-M3)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafeSaveJsonSymlinkRefusal:
    """A symlinked `_LIBRARY_FILE` pointing at `/etc/passwd` used to
    let the backup-read step copy the link target into a user-readable
    `.bak`. Refuse symlinks up front."""

    def test_symlink_target_refused(self, tmp_path):
        real = tmp_path / "real.json"
        real.write_text(
            '{"_schema_version": 1, "entries": []}', encoding="utf-8",
        )
        link = tmp_path / "link.json"
        try:
            link.symlink_to(real)
        except OSError:
            pytest.skip("OS does not support symlinks in this tmp_path")
        with pytest.raises(OSError, match="symlink"):
            sc._safe_save_json(link, [{"id": "X"}], "test")
        # Link target was not touched.
        assert json.loads(real.read_text())["entries"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# T1j — _load_dna_original size cap (A-M1)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadDnaOriginalSizeCap:
    """`_save_dna_original` was already capped at 50 MB; the read side
    used to be unbounded. A hand-edited or filesystem-corrupted sidecar
    could OOM the export path."""

    def test_oversized_sidecar_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sc, "_DNA_ORIGINALS_DIR", tmp_path)
        eid = "big"
        target = sc._dna_sidecar_path(eid)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * (sc._DNA_SIDECAR_MAX_BYTES + 100))
        assert sc._load_dna_original(eid) is None

    def test_normal_sidecar_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sc, "_DNA_ORIGINALS_DIR", tmp_path)
        eid = "small"
        target = sc._dna_sidecar_path(eid)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"small content")
        assert sc._load_dna_original(eid) == b"small content"


# ═══════════════════════════════════════════════════════════════════════════════
# T1k — Orphan tmp sweep (F-M2)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrphanTmpSweep:
    """Pre-0.8.9 a SIGKILL between `mkstemp` and `os.replace` left
    leftover `.tmp` / `.migrating` / `.restoring` files in `_DATA_DIR`
    forever. `_sweep_orphan_tmp_files` collects them — age-gated to
    1 h so legitimate in-flight writes are never collected."""

    def _stale_file(self, path: Path) -> None:
        path.write_text("garbage")
        stale = time.time() - (sc._ORPHAN_TMP_MIN_AGE_S + 60)
        os.utime(path, (stale, stale))

    def test_old_orphan_with_dot_tmp_suffix_removed(self, tmp_path):
        old = tmp_path / ".library.json.abc.tmp"
        self._stale_file(old)
        removed = sc._sweep_orphan_tmp_files(tmp_path)
        assert removed >= 1
        assert not old.exists()

    def test_old_orphan_with_tmp_underscore_prefix_removed(self, tmp_path):
        old = tmp_path / ".tmp_abc"
        self._stale_file(old)
        sc._sweep_orphan_tmp_files(tmp_path)
        assert not old.exists()

    def test_recent_orphan_kept(self, tmp_path):
        new = tmp_path / ".library.json.fresh.tmp"
        new.write_text("garbage")
        sc._sweep_orphan_tmp_files(tmp_path)
        assert new.exists()

    def test_user_file_with_tmp_substring_untouched(self, tmp_path):
        kept = tmp_path / "notes.txt"
        kept.write_text("important user data")
        old = time.time() - (24 * 3600)
        os.utime(kept, (old, old))
        sc._sweep_orphan_tmp_files(tmp_path)
        assert kept.exists()
        assert kept.read_text() == "important user data"
