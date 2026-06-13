"""Demo mode (2026-06-13): the `$SPLICECRAFT_DEMO` sandbox for the web demo.

The load-bearing safety property: a demo session must NEVER resolve its data
dir to a real library (it forces an ephemeral tempdir and ignores
`$SPLICECRAFT_DATA_DIR` / `$XDG`). Plus the web tier's defence-in-depth
lockdown (refuse file-open / NCBI / agent API, with a polite excuse).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import splicecraft as sc


class _FakeApp:
    def __init__(self):
        self.notes: list = []

    def notify(self, msg, **kw):
        self.notes.append((msg, kw))


class TestDemoModeDetection:
    def test_resolve_demo_mode(self, monkeypatch):
        for v in ("local", "full", "1", "true", "yes", "on", "LOCAL"):
            monkeypatch.setenv("SPLICECRAFT_DEMO", v)
            assert sc._resolve_demo_mode() == "local", v
        for v in ("web", "public", "restrained", "kiosk", "WEB"):
            monkeypatch.setenv("SPLICECRAFT_DEMO", v)
            assert sc._resolve_demo_mode() == "web", v
        for v in ("", "  ", "nonsense", "0", "off"):
            monkeypatch.setenv("SPLICECRAFT_DEMO", v)
            assert sc._resolve_demo_mode() is None, repr(v)
        monkeypatch.delenv("SPLICECRAFT_DEMO", raising=False)
        assert sc._resolve_demo_mode() is None


class TestDemoDataDirSandbox:
    """THE safety boundary: demo mode forces an ephemeral tempdir and ignores
    every real-path env override, so a demo can't read/clobber a real library.
    Verified in a subprocess because `_DATA_DIR` is computed at import time."""

    def _probe(self, demo_value: str) -> str:
        # Point SPLICECRAFT_DATA_DIR at a (fake) "real" path; demo mode MUST
        # ignore it and resolve `_DATA_DIR` to a tempdir instead.
        code = (
            "import splicecraft as sc, tempfile, pathlib, sys\n"
            "dd = pathlib.Path(sc._DATA_DIR).resolve()\n"
            "tmp = pathlib.Path(tempfile.gettempdir()).resolve()\n"
            "assert sc._DEMO_MODE in ('local','web'), sc._DEMO_MODE\n"
            "assert dd.is_relative_to(tmp), ('not under tmp', dd)\n"
            "assert 'fake-real-library' not in str(dd), ('LEAKED real path', dd)\n"
            "assert f'splicecraft-demo-{sc._DEMO_MODE}' in dd.name, dd.name\n"
            "print(dd)\n"
        )
        env = {
            "SPLICECRAFT_DEMO": demo_value,
            "SPLICECRAFT_DATA_DIR": "/some/fake-real-library",
            "PATH": __import__("os").environ.get("PATH", ""),
            "HOME": __import__("os").environ.get("HOME", ""),
        }
        out = subprocess.run([sys.executable, "-c", code], env=env,
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, (
            f"demo={demo_value} sandbox check FAILED:\n{out.stderr}")
        return out.stdout.strip()

    def test_web_demo_ignores_data_dir_override(self):
        dd = self._probe("web")
        assert dd and "fake-real-library" not in dd

    def test_local_demo_ignores_data_dir_override(self):
        dd = self._probe("local")
        assert dd and "fake-real-library" not in dd


class TestWebDemoLockdown:
    def test_refuse_only_fires_in_web(self, monkeypatch):
        app = _FakeApp()
        monkeypatch.setattr(sc, "_DEMO_MODE", "web")
        assert sc._demo_web_refuse(app, "Fetching from NCBI") is True
        assert app.notes and "web demo" in app.notes[-1][0].lower()
        # local + off → never blocks, never notifies
        monkeypatch.setattr(sc, "_DEMO_MODE", "local")
        app2 = _FakeApp()
        assert sc._demo_web_refuse(app2, "Opening files") is False
        assert not app2.notes
        monkeypatch.setattr(sc, "_DEMO_MODE", None)
        assert sc._demo_web_refuse(_FakeApp(), "x") is False

    def test_agent_api_refused_in_web_demo(self, monkeypatch):
        monkeypatch.setattr(sc, "_DEMO_MODE", "web")
        assert sc._start_agent_api(_FakeApp()) is None

    def test_demo_helpers(self, monkeypatch):
        monkeypatch.setattr(sc, "_DEMO_MODE", "web")
        assert sc._demo_active() and sc._demo_is_web()
        monkeypatch.setattr(sc, "_DEMO_MODE", "local")
        assert sc._demo_active() and not sc._demo_is_web()
        monkeypatch.setattr(sc, "_DEMO_MODE", None)
        assert not sc._demo_active() and not sc._demo_is_web()

    def test_egress_blocked_in_web(self, monkeypatch):
        import urllib.error
        monkeypatch.setattr(sc, "_DEMO_MODE", "web")
        for fn, args in [
            (sc.fetch_genbank, ("L09137",)),
            (sc._fetch_latest_pypi_version_ex, ()),
            (sc._ncbi_taxid_search, ("coli",)),
            (sc._codon_fetch_kazusa, ("83333",)),
            (sc._build_hardened_url_opener, ()),
        ]:
            with __import__("pytest").raises(urllib.error.URLError):
                fn(*args)
        # ...and NOT blocked outside web demo (helper is a no-op).
        monkeypatch.setattr(sc, "_DEMO_MODE", None)
        sc._demo_block_network("x")  # must not raise

    def test_master_delete_refused_in_web(self, monkeypatch):
        monkeypatch.setattr(sc, "_DEMO_MODE", "web")
        out = sc._perform_master_delete(
            _FakeApp(), sentinel=sc._MASTER_DELETE_SENTINEL)
        assert out["files_removed"] == 0 and out["dirs_removed"] == 0


class TestWebDemoComputeCap:
    async def test_apply_record_caps_oversize_in_web(self, monkeypatch):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        monkeypatch.setattr(sc, "_DEMO_MODE", "web")
        monkeypatch.setattr(sc, "_DEMO_WEB_MAX_BP", 200)
        big = SeqRecord(Seq("ACGT" * 100), id="big", name="big",   # 400 bp > 200
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        app = sc.PlasmidApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            before = app._current_record
            app._apply_record(big)        # over cap → refused, no load
            await pilot.pause()
            assert app._current_record is before, "oversize record loaded in web demo"
            # under-cap record loads fine
            small = SeqRecord(Seq("ACGT" * 10), id="small", name="small",
                              annotations={"molecule_type": "DNA",
                                           "topology": "circular"})
            app._apply_record(small)
            await pilot.pause()
            assert app._current_record is not None
            assert "small" in str(getattr(app._current_record, "id", ""))


class TestDemoSeed:
    async def test_seed_populates_demo_library(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._seed_demo_data()
            for _ in range(3):
                await pilot.pause()
        colls = sc._load_collections()
        names = {c.get("name") for c in colls}
        assert "Demo plasmids" in names, names
        demo = next(c for c in colls if c.get("name") == "Demo plasmids")
        assert demo.get("plasmids"), "demo collection empty"
        assert sc._get_active_collection_name() == "Demo plasmids"
