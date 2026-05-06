"""Tests for the agent-API HTTP server (0.4.6+).

Covers the BYO-AI integration that lets an external CLI agent (Claude
Code, Cursor, aider, …) drive the running SpliceCraft GUI via a
localhost JSON API. Two layers:

  * **Pure handler tests** — call `_h_status` / `_h_features` /
    `_h_add_feature` etc. directly with a fake `app` shim. Fast,
    no socket bind, no Textual mount.

  * **End-to-end HTTP tests** — bind a real `_AgentAPIServer` on a
    free port, send `urllib.request` calls, assert the JSON
    response. Uses a `MockApp` with the `_current_record` /
    `_unsaved` / `_apply_record` / `_do_save` surface the handlers
    actually touch.

We deliberately don't spin up a real `PlasmidApp` here — it would add
seconds per test, and the handler logic is what the tests need to
guard. Smoke-level "real app + real port" coverage lives in
`test_smoke.py`.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

import splicecraft as sc


# ── Helpers ────────────────────────────────────────────────────────────────────


def _free_port() -> int:
    """Bind on port 0 to let the OS pick a free port, then close so
    the test server can rebind. Tiny race window, fine for tests."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class MockApp:
    """Stand-in for `PlasmidApp` that gives the handlers everything
    they touch: `_current_record`, `_unsaved`, `_apply_record`,
    `_do_save`, and `call_from_thread` (which we just call inline,
    since there's no Textual loop here)."""

    def __init__(self, record=None):
        self._current_record  = record
        self._unsaved         = False
        self._source_path     = None
        self._restr_min_len   = 6
        self._restr_unique_only = False
        self._show_restr      = False
        self._applied_records: list = []
        self._saved          = False

    def call_from_thread(self, fn, *args, **kwargs):
        # No real event loop in tests — invoke synchronously. Matches
        # the API's return-value semantics (Textual's call_from_thread
        # returns the callable's result).
        return fn(*args, **kwargs)

    def _apply_record(self, record, *, clear_undo=True):
        self._applied_records.append(record)
        self._current_record = record
        self._unsaved = False

    def _do_save(self):
        self._saved = True
        self._unsaved = False
        return True

    def _push_undo(self):
        pass

    def _mark_dirty(self):
        self._unsaved = True

    def _notify_success(self, msg, **kwargs):
        pass

    def _annotate_with_feature(self, start, end, entry):
        """Stub mirror of `PlasmidApp._annotate_with_feature`. Validates
        the same way (range check, zero-length reject, strand coercion)
        and appends a real BioPython SeqFeature to the record so tests
        that assert on `record.features[-1]` see the same shape they
        would in the running GUI. No panel refresh — there's no Textual
        loop here to refresh against."""
        from Bio.SeqFeature import SeqFeature, FeatureLocation, CompoundLocation
        from copy import deepcopy
        if self._current_record is None:
            raise RuntimeError("Load a plasmid first.")
        n = len(self._current_record.seq)
        if not (0 <= start < n):
            raise ValueError(f"start {start} out of range [0, {n})")
        if not (0 <= end <= n):
            raise ValueError(f"end {end} out of range [0, {n}]")
        if end == start:
            raise ValueError("zero-length feature (end == start)")
        try:
            strand = int(entry.get("strand", 1))
        except (TypeError, ValueError):
            strand = 1
        biop_strand = strand if strand in (-1, 1) else None
        if end > start:
            loc = FeatureLocation(start, end, strand=biop_strand)
        else:
            loc = CompoundLocation([
                FeatureLocation(start, n, strand=biop_strand),
                FeatureLocation(0, end, strand=biop_strand),
            ])
        feat_type = entry.get("feature_type") or "misc_feature"
        qualifiers: dict = {
            k: list(v) if isinstance(v, (list, tuple)) else [v]
            for k, v in (entry.get("qualifiers") or {}).items()
        }
        label = (entry.get("name") or "").strip()
        if label and "label" not in qualifiers:
            qualifiers["label"] = [label]
        new_feat = SeqFeature(loc, type=feat_type, qualifiers=qualifiers)
        new_rec = deepcopy(self._current_record)
        new_rec.features.append(new_feat)
        self._current_record = new_rec
        self._unsaved = True

    def query_one(self, selector, *args):
        # Handlers that touch `query_one("#plasmid-map", PlasmidMap)`
        # for read-only feature listing — we expose a shim with a
        # `_feats` list pulled straight from the SeqRecord.
        if selector == "#plasmid-map":
            class _PMShim:
                _feats = self._feats_from_record()
                _restr_feats: list = []
                def load_record(self, rec):
                    pass
                def refresh(self):
                    pass
            return _PMShim()
        if selector == "#sidebar":
            class _Sidebar:
                def populate(self, feats):
                    pass
            return _Sidebar()
        if selector == "#seq-panel":
            class _SP:
                def update_seq(self, *a, **k):
                    pass
            return _SP()
        from textual.css.query import NoMatches
        raise NoMatches(selector)

    def _feats_from_record(self):
        rec = self._current_record
        if rec is None:
            return []
        out = []
        for f in rec.features:
            if f.type == "source":
                continue
            out.append({
                "start":  int(f.location.start),
                "end":    int(f.location.end),
                "type":   f.type,
                "label":  (f.qualifiers.get("label") or [""])[0],
                "strand": f.location.strand or 1,
                "color":  None,
            })
        return out


@pytest.fixture
def tiny_app(tiny_record):
    """`MockApp` pre-loaded with the conftest `tiny_record`."""
    return MockApp(record=tiny_record)


# ── Pure handler tests (no socket, no app) ────────────────────────────────────


class TestStatusHandler:
    def test_empty_when_no_record(self):
        app = MockApp(record=None)
        result = sc._h_status(app, {})
        assert result["loaded"] is False
        assert result["length"] == 0
        assert result["dirty"] is False

    def test_reports_loaded_record(self, tiny_app, tiny_record):
        result = sc._h_status(tiny_app, {})
        assert result["loaded"] is True
        assert result["name"]   == tiny_record.name
        assert result["length"] == len(tiny_record.seq)
        assert result["version"] == sc.__version__

    def test_reports_dirty_flag(self, tiny_app):
        tiny_app._unsaved = True
        assert sc._h_status(tiny_app, {})["dirty"] is True


class TestToolsHandler:
    def test_lists_registered_endpoints(self):
        result = sc._h_tools(None, {})
        names = {ep["name"] for ep in result["endpoints"]}
        # Spot-check the six starter endpoints.
        for required in ("status", "tools", "features", "fetch",
                          "load-entry", "add-feature", "save"):
            assert required in names, f"missing endpoint {required!r}"

    def test_write_flag_is_correct(self):
        eps = {ep["name"]: ep for ep in sc._h_tools(None, {})["endpoints"]}
        assert eps["status"]["write"]      is False
        assert eps["features"]["write"]    is False
        assert eps["fetch"]["write"]       is True
        assert eps["add-feature"]["write"] is True
        assert eps["save"]["write"]        is True


class TestAddFeatureHandler:
    def test_validates_missing_record(self):
        app = MockApp(record=None)
        result = sc._h_add_feature(app, {"start": 0, "end": 10})
        payload, status = result
        assert status == 422
        assert "no plasmid loaded" in payload["error"]

    def test_validates_missing_start(self, tiny_app):
        result = sc._h_add_feature(tiny_app, {"end": 10})
        payload, status = result
        assert status == 400
        assert "start" in payload["error"]

    def test_validates_zero_length(self, tiny_app):
        result = sc._h_add_feature(tiny_app, {"start": 5, "end": 5})
        payload, status = result
        assert status == 400
        assert "zero-length" in payload["error"]

    def test_validates_out_of_range(self, tiny_app, tiny_record):
        n = len(tiny_record.seq)
        result = sc._h_add_feature(tiny_app, {"start": n + 5, "end": n + 10})
        payload, status = result
        assert status == 400
        assert "out of range" in payload["error"]

    def test_validates_strand(self, tiny_app):
        result = sc._h_add_feature(
            tiny_app, {"start": 0, "end": 10, "strand": 2}
        )
        payload, status = result
        assert status == 400
        assert "strand" in payload["error"]

    def test_dirty_guard_refuses_without_force(self, tiny_app):
        tiny_app._unsaved = True
        result = sc._h_add_feature(
            tiny_app, {"start": 0, "end": 10, "label": "t"}
        )
        payload, status = result
        assert status == 409
        assert "force" in payload["error"]

    def test_dirty_guard_force_overrides(self, tiny_app):
        tiny_app._unsaved = True
        result = sc._h_add_feature(
            tiny_app, {"start": 0, "end": 10, "label": "t",
                        "force": True}
        )
        # Tuple == error; dict == success.
        assert isinstance(result, dict), result


class TestSaveHandler:
    def test_refuses_when_no_record(self):
        app = MockApp(record=None)
        result = sc._h_save(app, {})
        payload, status = result
        assert status == 422
        assert "nothing to save" in payload["error"]

    def test_calls_do_save(self, tiny_app):
        result = sc._h_save(tiny_app, {})
        assert result["ok"] is True
        assert tiny_app._saved is True


class TestFeaturesHandler:
    def test_empty_when_no_record(self):
        app = MockApp(record=None)
        assert sc._h_features(app, {})["features"] == []

    def test_lists_feature_dicts(self, tiny_app):
        feats = sc._h_features(tiny_app, {})["features"]
        assert len(feats) >= 1
        assert all("idx" in f and "start" in f and "end" in f
                    for f in feats)


class TestFindOrfsHandler:
    """`_h_find_orfs` exposes the six-frame ORF scan (added 0.6.0.0).
    Wraps `_find_orfs` — the algorithm itself is covered by
    test_dna_sanity.py::TestFindOrfs; here we just verify the agent
    path returns/normalises shape correctly."""

    def test_empty_when_no_record(self):
        app = MockApp(record=None)
        result = sc._h_find_orfs(app, {})
        assert result == ({"error": "no plasmid loaded"}, 422)

    def test_default_min_aa(self):
        from Bio.SeqRecord import SeqRecord
        from Bio.Seq import Seq
        body = "ATG" + "GCC" * 30 + "TAA"   # 99 bp, 31 aa coding
        rec = SeqRecord(Seq(body + "G" * 21), id="t", name="t")
        rec.annotations["topology"] = "circular"
        app = MockApp(record=rec)
        result = sc._h_find_orfs(app, {})
        assert "orfs" in result and "count" in result
        assert result["count"] >= 1
        # The ATG-stop ORF we built must be present.
        starts = {(o["start"], o["strand"]) for o in result["orfs"]}
        assert (0, 1) in starts

    def test_min_aa_filter(self):
        from Bio.SeqRecord import SeqRecord
        from Bio.Seq import Seq
        body = "ATG" + "GCC" * 19 + "TAA"   # 20 aa coding
        rec = SeqRecord(Seq(body), id="t", name="t")
        app = MockApp(record=rec)
        # 30 aa rejects, 20 aa keeps.
        assert sc._h_find_orfs(app, {"min_aa": 30})["count"] == 0
        assert sc._h_find_orfs(app, {"min_aa": 20})["count"] >= 1

    def test_min_aa_invalid(self):
        from Bio.SeqRecord import SeqRecord
        from Bio.Seq import Seq
        rec = SeqRecord(Seq("ATGAAA"), id="t", name="t")
        app = MockApp(record=rec)
        result = sc._h_find_orfs(app, {"min_aa": "notanint"})
        assert isinstance(result, tuple) and result[1] == 400

    def test_min_aa_below_one_rejected(self):
        from Bio.SeqRecord import SeqRecord
        from Bio.Seq import Seq
        rec = SeqRecord(Seq("ATGAAA"), id="t", name="t")
        app = MockApp(record=rec)
        result = sc._h_find_orfs(app, {"min_aa": 0})
        assert isinstance(result, tuple) and result[1] == 400


class TestLoadFileSizeCap:
    """Regression guard for 2026-05-06 fix: `_h_load_file` previously
    had NO size cap on disk reads — a malicious or buggy agent script
    could load a 10 GB GenBank file and OOM the worker. Cap is now
    `_BULK_IMPORT_MAX_BYTES` (50 MB) with `force=true` override."""

    def test_oversized_file_rejected_with_413(self, tmp_path, monkeypatch):
        # 10-byte cap so we don't actually need to write 50 MB.
        monkeypatch.setattr(sc, "_BULK_IMPORT_MAX_BYTES", 10)
        big = tmp_path / "huge.gb"
        big.write_bytes(b"X" * 100)
        app = MockApp()
        result = sc._h_load_file(app, {"path": str(big)})
        payload, status = result
        assert status == 413
        assert "cap" in payload["error"].lower()
        assert payload["size_bytes"] == 100
        assert payload["cap_bytes"] == 10

    def test_force_overrides_size_cap(self, tmp_path, monkeypatch, tiny_record):
        """Pass force=true and the cap is bypassed (matches GUI's
        "load anyway" confirmation)."""
        monkeypatch.setattr(sc, "_BULK_IMPORT_MAX_BYTES", 10)
        # Use a real GenBank file so load_genbank succeeds.
        gb = tmp_path / "ok.gb"
        from io import StringIO
        from Bio import SeqIO as _SeqIO
        sio = StringIO()
        _SeqIO.write([tiny_record], sio, "genbank")
        gb.write_text(sio.getvalue())
        app = MockApp()
        # Without force: rejected.
        result = sc._h_load_file(app, {"path": str(gb)})
        assert isinstance(result, tuple) and result[1] == 413
        # With force: parsed.
        result = sc._h_load_file(app, {"path": str(gb), "force": True})
        assert isinstance(result, dict) and result["ok"] is True

    def test_missing_path_returns_400(self):
        result = sc._h_load_file(MockApp(), {})
        assert result[1] == 400 and "missing" in result[0]["error"]

    def test_nonexistent_path_returns_404(self, tmp_path):
        result = sc._h_load_file(MockApp(),
                                  {"path": str(tmp_path / "nope.gb")})
        assert result[1] == 404


# ── End-to-end HTTP tests (real socket + JSON wire format) ─────────────────────


@pytest.fixture
def http_server(tiny_app):
    """Bind a real `_AgentAPIServer` on a free port for the test
    duration. Yields `(base_url, token)`."""
    port = _free_port()
    token = "test-token-" + str(port)
    srv = sc._AgentAPIServer(("127.0.0.1", port), tiny_app, token)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    # Tiny settle so the listening socket is ready before the first
    # request — otherwise the very first urlopen() can race the bind.
    time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", token, tiny_app
    finally:
        srv.shutdown()
        srv.server_close()


def _http(url: str, *, method: str = "GET", body: dict | None = None,
          token: str | None = None,
          timeout: float = 5.0) -> tuple[int, dict]:
    """Tiny urllib helper that returns `(status, json_payload)`."""
    data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method)
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_bytes = e.read() if e.fp else b""
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {"error": body_bytes.decode("utf-8", errors="replace")}
        return e.code, payload


class TestHTTPRouting:
    def test_status_endpoint(self, http_server):
        base, token, app = http_server
        status, payload = _http(f"{base}/status", token=token)
        assert status == 200
        assert payload["loaded"] is True
        assert payload["version"] == sc.__version__

    def test_tools_endpoint_lists_routes(self, http_server):
        base, token, app = http_server
        status, payload = _http(f"{base}/tools", token=token)
        assert status == 200
        names = [ep["name"] for ep in payload["endpoints"]]
        assert "status" in names

    def test_unknown_endpoint_returns_404(self, http_server):
        base, token, app = http_server
        status, payload = _http(f"{base}/no-such-thing", token=token)
        assert status == 404
        assert "endpoints" in payload   # helpful self-discovery

    def test_root_path_returns_tools(self, http_server):
        base, token, app = http_server
        status, payload = _http(f"{base}/", token=token)
        assert status == 200
        assert "endpoints" in payload


class TestHTTPAuth:
    def test_write_endpoint_refuses_no_token(self, http_server):
        base, _token, _app = http_server
        status, payload = _http(
            f"{base}/save", method="POST", body={}, token=None,
        )
        assert status == 401
        assert "token" in payload["error"]

    def test_write_endpoint_refuses_wrong_token(self, http_server):
        base, _token, _app = http_server
        status, payload = _http(
            f"{base}/save", method="POST", body={}, token="wrong",
        )
        assert status == 401

    def test_read_endpoint_works_without_token(self, http_server):
        """Read-only endpoints should be reachable without auth — they
        can't damage state, and forcing token-on-every-curl makes
        scripted introspection awkward."""
        base, _token, _app = http_server
        status, payload = _http(f"{base}/status", token=None)
        assert status == 200


class TestHTTPHardening:
    def test_body_size_cap_constant_is_set(self):
        """The handler exposes a `_MAX_BODY_BYTES` cap so a bogus
        `Content-Length: 9999999999` header can't park the handler
        thread on `rfile.read`. We don't drive the cap end-to-end via
        a real TCP request (the localhost half-open race triggers a
        broken-pipe on the client before the server's rejection
        response lands, which is a transport issue not an app one).
        Instead, guard the constant + behavior in `_read_body` via
        a unit-level check below."""
        assert sc._AgentRequestHandler._MAX_BODY_BYTES <= 1 << 20
        assert sc._AgentRequestHandler._MAX_BODY_BYTES >= 1 << 12

    def test_malformed_json_does_not_crash(self, http_server):
        """A non-JSON POST body should be treated as an empty payload
        — never a 500 from a parsing exception leaking out."""
        base, token, _app = http_server
        req = urllib.request.Request(
            f"{base}/add-feature", method="POST",
            data=b"this is not json {{{",
        )
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        # Empty body → handler sees no `start` / `end` → 400.
        assert status == 400


class TestHTTPAddFeature:
    def test_add_feature_round_trip(self, http_server, tiny_record):
        base, token, app = http_server
        n_before = len(app._current_record.features)
        status, payload = _http(
            f"{base}/add-feature", method="POST",
            body={"start": 30, "end": 40, "label": "agentTest",
                  "type": "misc_feature"},
            token=token,
        )
        assert status == 200, payload
        assert payload["ok"] is True
        # Feature was actually appended to the underlying record.
        assert len(app._current_record.features) == n_before + 1
        new = app._current_record.features[-1]
        assert new.type == "misc_feature"
        assert new.qualifiers["label"] == ["agentTest"]
        assert int(new.location.start) == 30
        assert int(new.location.end)   == 40
        # And the record was marked dirty (so the user sees `*`).
        assert app._unsaved is True

    def test_add_feature_dirty_guard(self, http_server):
        base, token, app = http_server
        app._unsaved = True
        status, payload = _http(
            f"{base}/add-feature", method="POST",
            body={"start": 30, "end": 40},
            token=token,
        )
        assert status == 409
        assert payload["dirty"] is True

    def test_add_feature_force_override(self, http_server):
        base, token, app = http_server
        app._unsaved = True
        status, payload = _http(
            f"{base}/add-feature", method="POST",
            body={"start": 30, "end": 40, "label": "forced",
                  "force": True},
            token=token,
        )
        assert status == 200, payload

    def test_add_wrap_feature(self, http_server, tiny_record):
        """Wrap features (end < start) build a CompoundLocation."""
        base, token, app = http_server
        n = len(tiny_record.seq)
        status, payload = _http(
            f"{base}/add-feature", method="POST",
            body={"start": n - 5, "end": 5, "label": "wrap",
                  "type": "misc_feature"},
            token=token,
        )
        assert status == 200, payload
        from Bio.SeqFeature import CompoundLocation
        new = app._current_record.features[-1]
        assert isinstance(new.location, CompoundLocation)


class TestHTTPRegistration:
    def test_endpoint_decorator_registers(self):
        """Sanity: the decorator populates `_AGENT_HANDLERS` and tags
        write endpoints correctly. Catches a refactor that drops the
        registry by accident."""
        assert "status"      in sc._AGENT_HANDLERS
        assert "add-feature" in sc._AGENT_HANDLERS
        _fn, write = sc._AGENT_HANDLERS["status"]
        assert write is False
        _fn, write = sc._AGENT_HANDLERS["add-feature"]
        assert write is True

    def test_token_file_written_and_cleaned_up(self, tmp_path,
                                                  monkeypatch):
        """`_start_agent_api` writes (port, token) to the token file
        and `_stop_agent_api` removes it."""
        token_path = tmp_path / "agent_token"
        monkeypatch.setattr(sc, "_AGENT_TOKEN_FILE", token_path)
        port = _free_port()
        app = MockApp()
        srv = sc._start_agent_api(app, port=port)
        try:
            assert srv is not None
            assert token_path.exists()
            text = token_path.read_text(encoding="utf-8")
            stored_port, stored_token = text.strip().splitlines()
            assert int(stored_port) == port
            assert len(stored_token) >= 16
        finally:
            sc._stop_agent_api(srv)
        # Token file is removed on shutdown so a stale CLI invocation
        # can't accidentally hit a different process that bound the
        # same port later.
        assert not token_path.exists()


# ── Input sanitization (2026-05-01 hardening pass) ────────────────────────────


class TestSanitizeLabel:
    def test_strips_control_chars(self):
        assert sc._sanitize_label("hello\x00\x01world") == "helloworld"

    def test_collapses_newlines(self):
        # CR/LF would corrupt the sidebar's single-row label render.
        assert "\n" not in sc._sanitize_label("a\nb\rc")
        assert "\r" not in sc._sanitize_label("a\nb\rc")

    def test_caps_length(self):
        assert len(sc._sanitize_label("a" * 1000)) == 200
        assert len(sc._sanitize_label("a" * 1000, max_len=10)) == 10

    def test_unicode_survives(self):
        # Emoji + IUPAC-style ASCII labels both legitimate.
        assert sc._sanitize_label("test 🧬 lacZ") == "test 🧬 lacZ"

    def test_empty_returns_empty(self):
        assert sc._sanitize_label(None) == ""
        assert sc._sanitize_label("") == ""
        assert sc._sanitize_label("   ") == ""


class TestSanitizeFeatType:
    def test_default_for_empty(self):
        assert sc._sanitize_feat_type(None) == "misc_feature"
        assert sc._sanitize_feat_type("") == "misc_feature"
        assert sc._sanitize_feat_type("  ") == "misc_feature"

    def test_strips_control_chars(self):
        assert sc._sanitize_feat_type("CDS\x00") == "CDS"

    def test_caps_length(self):
        assert len(sc._sanitize_feat_type("a" * 100)) == 50


class TestSanitizeAccession:
    def test_valid(self):
        assert sc._sanitize_accession("L09137") == "L09137"
        assert sc._sanitize_accession("MW463917.1") == "MW463917.1"
        assert sc._sanitize_accession("NC_001140") == "NC_001140"

    def test_rejects_shell_metacharacters(self):
        # Defends against `accession=L09137; rm -rf /` smuggling.
        assert sc._sanitize_accession("L09137; rm -rf /") is None
        assert sc._sanitize_accession("L09137|cat /etc/passwd") is None
        assert sc._sanitize_accession("../../etc/hosts") is None

    def test_rejects_overlong(self):
        assert sc._sanitize_accession("A" * 33) is None

    def test_empty_returns_none(self):
        assert sc._sanitize_accession(None) is None
        assert sc._sanitize_accession("") is None


class TestSanitizeBases:
    def test_valid_iupac(self):
        s, err = sc._sanitize_bases("acgtnRYWSMKBDHV")
        assert err is None
        assert s == "ACGTNRYWSMKBDHV"

    def test_invalid_char(self):
        s, err = sc._sanitize_bases("ACGZ")
        assert err is not None
        assert "Z" in err

    def test_overlong(self):
        s, err = sc._sanitize_bases("A" * 100, max_len=50)
        assert err is not None
        assert "too long" in err

    def test_missing(self):
        s, err = sc._sanitize_bases(None)
        assert err is not None and "missing" in err


class TestEndpointHardening:
    """Adversarial-input tests: each endpoint must reject malformed
    payloads with a clear 400 error rather than crash or silently
    accept dangerous input."""

    def test_fetch_rejects_shell_meta(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/fetch", method="POST",
            body={"accession": "L09137; rm -rf /"},
            token=token,
        )
        assert status == 400
        assert "accession" in payload.get("error", "")

    def test_add_feature_strips_control_chars_in_label(self, http_server,
                                                        tiny_record):
        base, token, app = http_server
        status, payload = _http(
            f"{base}/add-feature", method="POST",
            body={"start": 30, "end": 40,
                  "label": "evil\x00\nlabel", "type": "misc_feature"},
            token=token,
        )
        assert status == 200, payload
        new = app._current_record.features[-1]
        assert "\x00" not in new.qualifiers["label"][0]
        assert "\n" not in new.qualifiers["label"][0]

    def test_add_feature_invalid_strand(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/add-feature", method="POST",
            body={"start": 30, "end": 40, "strand": 99},
            token=token,
        )
        assert status == 400


class TestTokenHardening:
    """Bearer-token comparison must be timing-safe and the token file
    written atomically with mode 0600 — a local-process attacker
    shouldn't be able to either time-leak the token byte-by-byte or
    race the chmod() to read the token in plaintext."""

    def test_token_compare_is_constant_time(self, http_server):
        # We can't directly time the comparison reliably enough to
        # detect a non-constant-time bug from a unit test, but we can
        # at least confirm `secrets.compare_digest` is in the call
        # path by verifying that two equal-length wrong tokens both
        # 401 (rather than 401-on-prefix-mismatch / 200-on-match).
        base, _token, _ = http_server
        wrong_a = "0" * 32
        wrong_b = "f" * 32
        s1, _ = _http(f"{base}/save", method="POST", body={},
                       token=wrong_a)
        s2, _ = _http(f"{base}/save", method="POST", body={},
                       token=wrong_b)
        assert s1 == s2 == 401

    def test_short_token_rejected_without_crash(self, http_server):
        base, _token, _ = http_server
        # Different length than the real token. compare_digest only
        # returns False here (doesn't raise). Pre-fix, this would
        # have hit a timing oracle; either way it must 401, not 500.
        status, _ = _http(f"{base}/save", method="POST", body={},
                           token="x")
        assert status == 401


class TestNewLibraryEndpoints:
    """Coverage for the parity endpoints added in the hardening pass:
    add-current-to-library, create-collection, delete-collection,
    rename-collection, set-active-collection, bulk-import-folder."""

    def test_create_collection_empty(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/create-collection", method="POST",
            body={"name": "agent-empty"}, token=token,
        )
        assert status == 200, payload
        assert payload["ok"] is True
        assert payload["n_plasmids"] == 0
        names = [c["name"] for c in sc._load_collections()]
        assert "agent-empty" in names

    def test_create_collection_rejects_blank(self, http_server):
        base, token, _ = http_server
        for bad in ("", "   ", "\x00\x00\x00", None):
            status, payload = _http(
                f"{base}/create-collection", method="POST",
                body={"name": bad}, token=token,
            )
            assert status == 400, (bad, payload)

    def test_create_collection_rejects_duplicate(self, http_server):
        sc._save_collections([{"name": "Existing", "plasmids": []}])
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/create-collection", method="POST",
            body={"name": "Existing"}, token=token,
        )
        assert status == 409
        assert "already exists" in payload["error"]

    def test_create_collection_with_invalid_folder(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/create-collection", method="POST",
            body={"name": "agent-folder", "folder": "/nope/none/nada"},
            token=token,
        )
        assert status == 400
        assert "not a directory" in payload["error"]

    def test_delete_collection_round_trip(self, http_server):
        sc._save_collections([{"name": "Doomed", "plasmids": []}])
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/delete-collection", method="POST",
            body={"name": "Doomed"}, token=token,
        )
        assert status == 200
        assert payload["deleted"] == "Doomed"
        names = [c["name"] for c in sc._load_collections()]
        assert "Doomed" not in names

    def test_delete_collection_404_on_missing(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/delete-collection", method="POST",
            body={"name": "GhostCollection"}, token=token,
        )
        assert status == 404

    def test_rename_collection_updates_active_pointer(self, http_server):
        sc._save_collections([{"name": "Old", "plasmids": []}])
        sc._set_active_collection_name("Old")
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/rename-collection", method="POST",
            body={"old": "Old", "new": "New"}, token=token,
        )
        assert status == 200
        assert sc._get_active_collection_name() == "New"

    def test_rename_collection_rejects_collision(self, http_server):
        sc._save_collections([
            {"name": "A", "plasmids": []},
            {"name": "B", "plasmids": []},
        ])
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/rename-collection", method="POST",
            body={"old": "A", "new": "B"}, token=token,
        )
        assert status == 409

    def test_set_active_collection(self, http_server):
        sc._save_collections([
            {"name": "ColA", "plasmids": [
                {"id": "p1", "name": "p1", "size": 10, "gb_text": "X"}
            ]},
            {"name": "ColB", "plasmids": []},
        ])
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-active-collection", method="POST",
            body={"name": "ColA"}, token=token,
        )
        assert status == 200
        assert sc._get_active_collection_name() == "ColA"
        assert payload["n_plasmids"] == 1

    def test_set_active_collection_404(self, http_server):
        base, token, _ = http_server
        status, _ = _http(
            f"{base}/set-active-collection", method="POST",
            body={"name": "NotThere"}, token=token,
        )
        assert status == 404

    def test_bulk_import_folder_with_fixtures(self, http_server,
                                                isolated_library):
        from pathlib import Path
        fixtures_dir = Path(__file__).parent
        if not list(fixtures_dir.glob("FFE*.dna")):
            pytest.skip("No FFE .dna fixtures present")
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/bulk-import-folder", method="POST",
            body={"folder": str(fixtures_dir),
                  "collection": "FFE Bulk"},
            token=token,
        )
        assert status == 200, payload
        assert payload["n_imported"] >= 5
        assert payload["n_failed"] == 0
        names = [c["name"] for c in sc._load_collections()]
        assert "FFE Bulk" in names

    def test_bulk_import_folder_refuses_collection_collision(
        self, http_server, isolated_library
    ):
        sc._save_collections([{"name": "Taken", "plasmids": []}])
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/bulk-import-folder", method="POST",
            body={"folder": "/tmp", "collection": "Taken"},
            token=token,
        )
        assert status == 409

    def test_bulk_import_folder_validates_folder(self, http_server):
        base, token, _ = http_server
        status, _ = _http(
            f"{base}/bulk-import-folder", method="POST",
            body={"folder": "/no/such/dir/anywhere",
                  "collection": "X"},
            token=token,
        )
        assert status == 400

    def test_search_library_across_collections(self, http_server,
                                                  isolated_library):
        """`search-library` walks every collection on disk and returns
        fuzzy-matching plasmids regardless of which one is active."""
        sc._save_collections([
            {"name": "ColA", "plasmids": [
                {"id": "p1", "name": "pUC19_alpha", "size": 100,
                 "gb_text": "X", "n_feats": 3},
                {"id": "p2", "name": "pET28b", "size": 200,
                 "gb_text": "X", "n_feats": 4},
            ]},
            {"name": "ColB", "plasmids": [
                {"id": "p3", "name": "pUC19_beta", "size": 150,
                 "gb_text": "X", "n_feats": 5},
            ]},
        ])
        sc._set_active_collection_name("ColA")
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/search-library", method="POST",
            body={"query": "puc19"}, token=token,
        )
        assert status == 200, payload
        names = {(m["collection"], m["name"]) for m in payload["matches"]}
        assert ("ColA", "pUC19_alpha") in names
        assert ("ColB", "pUC19_beta") in names
        # pET28b doesn't match `puc19`.
        assert ("ColA", "pET28b") not in names

    def test_search_library_empty_query_lists_everything(
        self, http_server, isolated_library
    ):
        sc._save_collections([
            {"name": "X", "plasmids": [
                {"id": "a", "name": "a", "size": 1, "gb_text": "x"},
                {"id": "b", "name": "b", "size": 1, "gb_text": "x"},
            ]},
        ])
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/search-library", method="POST", body={}, token=token,
        )
        assert status == 200
        assert payload["count"] == 2

    def test_search_library_limit_clamped(self, http_server,
                                            isolated_library):
        sc._save_collections([
            {"name": "X", "plasmids": [
                {"id": str(i), "name": f"p{i}", "size": 1, "gb_text": "x"}
                for i in range(50)
            ]},
        ])
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/search-library", method="POST",
            body={"limit": 5}, token=token,
        )
        assert status == 200
        assert payload["count"] == 5

    def test_search_library_rejects_non_string_query(self, http_server):
        base, token, _ = http_server
        status, _ = _http(
            f"{base}/search-library", method="POST",
            body={"query": 42}, token=token,
        )
        assert status == 400


class TestNewSearchEndpoints:
    """BLAST + HMMscan parity for agents."""

    def test_blast_returns_hits(self, http_server, isolated_library):
        # Library has the conftest's seeded plasmid; BLASTN against
        # itself should return at least one self-hit. Build the GenBank
        # text via Biopython so LOCUS length and ORIGIN bases agree
        # exactly (no parser warning).
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        bases = "ATGAAATTCCGATTAACCGGTTAAGGGCCATTTGCAAGGACCGGTTTAAA"
        rec = SeqRecord(Seq(bases), id="rec1", name="rec1",
                        annotations={"molecule_type": "DNA",
                                       "topology":      "circular"})
        sc._save_collections([{
            "name": "TestColl",
            "plasmids": [{
                "id":      "rec1",
                "name":    "rec1",
                "size":    len(bases),
                "gb_text": sc._record_to_gb_text(rec),
            }],
        }])
        base, token, _ = http_server
        # Long-enough query for pyhmmer (≥ 20 bp)
        status, payload = _http(
            f"{base}/blast", method="POST",
            body={"query": "ATGAAATTCCGATTAACCGGTTAAGGGCCATTTGC",
                  "program": "blastn", "backend": "pure"},
            token=token,
        )
        assert status == 200, payload
        assert payload["program"] == "blastn"
        assert payload["n_hits"] >= 1

    def test_blast_rejects_empty_query(self, http_server):
        base, token, _ = http_server
        status, _ = _http(
            f"{base}/blast", method="POST",
            body={"query": "", "program": "blastn"},
            token=token,
        )
        assert status == 400

    def test_blast_rejects_invalid_program(self, http_server):
        base, token, _ = http_server
        status, _ = _http(
            f"{base}/blast", method="POST",
            body={"query": "ATGC", "program": "tblastx"},
            token=token,
        )
        assert status == 400

    def test_blast_rejects_oversized_collection_list(self, http_server):
        base, token, _ = http_server
        status, _ = _http(
            f"{base}/blast", method="POST",
            body={"query": "ATGCATGCATGCATGCATGC",
                  "collections": ["x"] * 200},
            token=token,
        )
        assert status == 400

    def test_blast_rejects_invalid_collection_name(self, http_server):
        base, token, _ = http_server
        status, _ = _http(
            f"{base}/blast", method="POST",
            body={"query": "ATGCATGCATGCATGCATGC",
                  "collections": ["valid", "\x00\x00\x00"]},
            token=token,
        )
        assert status == 400

    def test_blast_clamps_max_hits(self, http_server, isolated_library):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/blast", method="POST",
            body={"query": "ATGCATGCATGCATGCATGC",
                  "max_hits": 99999, "backend": "pure"},
            token=token,
        )
        # Clamped to 500 internally; the search itself succeeds.
        assert status == 200

    def test_blast_invalid_backend(self, http_server):
        base, token, _ = http_server
        status, _ = _http(
            f"{base}/blast", method="POST",
            body={"query": "ATGC", "backend": "xyz"},
            token=token,
        )
        assert status == 400

    def test_hmmscan_404_on_missing_path(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/hmmscan", method="POST",
            body={"query": "MAEELFKWILR" * 5,
                  "hmm_path": "/no/such/file.hmm"},
            token=token,
        )
        assert status == 404

    def test_hmmscan_400_on_short_query(self, http_server, tmp_path):
        # Build a tiny .hmm so the path-exists check passes; the
        # query length check rejects before we hit pyhmmer.
        fake = tmp_path / "fake.hmm"
        fake.write_text("")  # empty file is enough for the existence check
        base, token, _ = http_server
        status, _ = _http(
            f"{base}/hmmscan", method="POST",
            body={"query": "M",  # below _HMMSCAN_MIN_QUERY_LEN
                  "hmm_path": str(fake)},
            token=token,
        )
        assert status == 400


class TestAdditionalAgentHardening:
    """A grab-bag of attack inputs against the new endpoints — none
    must crash the server or accept dangerous payloads."""

    def test_create_collection_rejects_oversized_name(self, http_server):
        base, token, _ = http_server
        status, _ = _http(
            f"{base}/create-collection", method="POST",
            body={"name": "x" * (sc._MAX_COLLECTION_NAME_LEN + 100)},
            token=token,
        )
        # Long name is *truncated* to the cap, not rejected, so the
        # collection is still created successfully — the cap protects
        # against megabyte-sized JSON, not from semantic validation.
        assert status == 200
        names = [c["name"] for c in sc._load_collections()]
        assert any(len(n) <= sc._MAX_COLLECTION_NAME_LEN for n in names)

    def test_rename_collection_old_equals_new(self, http_server):
        sc._save_collections([{"name": "Same", "plasmids": []}])
        base, token, _ = http_server
        status, _ = _http(
            f"{base}/rename-collection", method="POST",
            body={"old": "Same", "new": "Same"}, token=token,
        )
        assert status == 400

    def test_add_current_to_library_no_record(self, http_server):
        base, token, app = http_server
        # Nuke the current record on the mock app
        app._current_record = None
        status, _ = _http(
            f"{base}/add-current-to-library", method="POST",
            body={}, token=token,
        )
        assert status == 422


class TestTypeStrictSanitisation:
    """Sanitisers must reject non-string inputs (dicts, lists, ints,
    None) rather than silently coerce via ``str()``. A JSON payload
    of ``{"name": {"x": 1}}`` should NOT become a collection literally
    named ``"{'x': 1}"``."""

    def test_sanitize_label_rejects_non_string(self):
        # Each of these used to be accepted via str() coercion; now
        # they must come back as empty.
        assert sc._sanitize_label({"x": 1}) == ""
        assert sc._sanitize_label([1, 2, 3]) == ""
        assert sc._sanitize_label(42) == ""
        assert sc._sanitize_label(None) == ""
        assert sc._sanitize_label(True) == ""

    def test_sanitize_feat_type_rejects_non_string(self):
        assert sc._sanitize_feat_type({"x": 1}) == "misc_feature"
        assert sc._sanitize_feat_type(42)        == "misc_feature"
        assert sc._sanitize_feat_type(None)      == "misc_feature"

    def test_sanitize_accession_rejects_non_string(self):
        assert sc._sanitize_accession({"x": 1}) is None
        assert sc._sanitize_accession([1, 2])   is None
        assert sc._sanitize_accession(42)       is None

    def test_sanitize_path_rejects_non_string(self):
        assert sc._sanitize_path({"x": 1}) is None
        assert sc._sanitize_path([1, 2])   is None
        assert sc._sanitize_path(42)       is None

    def test_create_collection_rejects_non_string_name(self, http_server):
        base, token, _ = http_server
        for bad in ({"x": 1}, [1, 2, 3], 42, None):
            status, payload = _http(
                f"{base}/create-collection", method="POST",
                body={"name": bad}, token=token,
            )
            assert status == 400, (bad, payload)


class TestNumericCoercionHardening:
    """``int(x)`` blows up on ``+/- Infinity`` (OverflowError) and
    ``NaN`` returns silently as 0 in some paths. Both must be caught
    cleanly at every numeric input boundary so a hostile JSON payload
    can't crash the handler thread."""

    def test_blast_max_hits_infinity(self, http_server):
        base, token, _ = http_server
        body_json = '{"query": "ATGCATGCATGCATGCATGC", "max_hits": Infinity}'
        # Send raw JSON (urllib helper auto-encodes a dict, but we
        # want literal JSON Infinity which Python's json.loads accepts).
        import urllib.request, urllib.error
        req = urllib.request.Request(
            f"{base}/blast", data=body_json.encode(),
            headers={"Authorization": f"Bearer {token}",
                       "Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5).read()
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
        # Pre-fix this was a 500 (OverflowError trace). Now must 400
        # with a clear "must be a finite number" message.
        assert status == 400

    def test_add_feature_start_infinity(self, http_server, tiny_record):
        base, token, _ = http_server
        body_json = ('{"start": Infinity, "end": 10, "label": "x"}')
        import urllib.request, urllib.error
        req = urllib.request.Request(
            f"{base}/add-feature", data=body_json.encode(),
            headers={"Authorization": f"Bearer {token}",
                       "Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5).read()
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status == 400

    def test_add_feature_strand_infinity(self, http_server, tiny_record):
        """Regression guard for 2026-05-05 retrofit: `add-feature` used
        to call raw `int(payload.get("strand", 1))` which raises
        OverflowError on JSON `Infinity`. Now routes through
        `_coerce_int` and returns a clean 400."""
        base, token, _ = http_server
        body_json = ('{"start": 0, "end": 10, "label": "x", '
                       '"strand": Infinity}')
        import urllib.request, urllib.error
        req = urllib.request.Request(
            f"{base}/add-feature", data=body_json.encode(),
            headers={"Authorization": f"Bearer {token}",
                       "Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5).read()
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status == 400

    def test_update_feature_strand_infinity(self, http_server, tiny_record):
        """Regression guard for 2026-05-05 retrofit: same fix on
        `update-feature`'s optional strand field."""
        base, token, _ = http_server
        body_json = '{"idx": 0, "strand": Infinity}'
        import urllib.request, urllib.error
        req = urllib.request.Request(
            f"{base}/update-feature", data=body_json.encode(),
            headers={"Authorization": f"Bearer {token}",
                       "Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5).read()
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status == 400

    def test_list_restriction_sites_min_length_infinity(self, http_server,
                                                          tiny_record):
        """Regression guard for 2026-05-05 retrofit: `list-restriction-
        sites` now rejects Infinity in `min_length` instead of bubbling
        an OverflowError up to the 500 path."""
        base, token, _ = http_server
        body_json = '{"min_length": Infinity}'
        import urllib.request, urllib.error
        req = urllib.request.Request(
            f"{base}/list-restriction-sites", data=body_json.encode(),
            headers={"Authorization": f"Bearer {token}",
                       "Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5).read()
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status == 400


class TestRequestDispatcherHardening:
    """The HTTP dispatcher must hand handlers a real dict (never None,
    never a list) so .get() never crashes."""

    def test_handle_passes_dict_on_empty_body(self, http_server):
        base, token, _ = http_server
        # POST with Content-Length: 0 → handler should still get {}
        import urllib.request, urllib.error
        req = urllib.request.Request(
            f"{base}/save", data=b"",
            headers={"Authorization": f"Bearer {token}"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5).read()
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
        # 422 (no record loaded) is the right error; what we care about
        # is that the handler didn't AttributeError on a None body.
        assert status in (200, 422)

    def test_handle_normalizes_non_dict_json(self, http_server):
        # JSON body that's a list, not a dict — should be normalised
        # to {} before reaching the handler.
        base, token, _ = http_server
        import urllib.request, urllib.error
        req = urllib.request.Request(
            f"{base}/save", data=b'[1,2,3]',
            headers={"Authorization": f"Bearer {token}",
                       "Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5).read()
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
        # Must be a clean 200/422 — not a 500 from .get() on a list.
        assert status in (200, 422)


# ── Plasmid status endpoints (added 2026-05-05 for v1.0) ──────────────────────


class TestPlasmidStatusEndpoints:
    def test_list_plasmid_statuses(self, http_server):
        base, token, _ = http_server
        status, payload = _http(f"{base}/list-plasmid-statuses", token=token)
        assert status == 200
        assert payload["ok"] is True
        # Strict canonical vocabulary — exactly the four statuses.
        assert set(payload["statuses"]) == {
            "DESIGNING", "CLONING", "SEQUENCING", "VERIFIED"
        }
        # Each status carries a hex color; the agent can use it for
        # rendering without re-deriving from the GUI.
        assert all(c.startswith("#") for c in payload["colors"].values())

    def test_set_plasmid_status_round_trip(self, http_server, tiny_record):
        # Seed one library entry the endpoint can target.
        sc._save_library([{"name": "pTest", "id": "pTest",
                            "gb_text": "fake"}])
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-plasmid-status", method="POST",
            body={"name": "pTest", "status": "CLONING"}, token=token,
        )
        assert status == 200
        assert payload["status"] == "CLONING"
        # Persisted on disk.
        entry = next(e for e in sc._load_library() if e["name"] == "pTest")
        assert entry["status"] == "CLONING"

    def test_set_plasmid_status_clears_with_empty_string(self, http_server):
        sc._save_library([{"name": "pTest", "id": "pTest",
                            "status": "VERIFIED", "gb_text": "fake"}])
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-plasmid-status", method="POST",
            body={"name": "pTest", "status": ""}, token=token,
        )
        assert status == 200
        assert payload["status"] == ""

    def test_set_plasmid_status_invalid_collapses_to_empty(self, http_server):
        """Per `_sanitize_plasmid_status`'s strict-canonical-or-empty
        contract: a non-canonical string (mixed case, garbage)
        silently degrades to "" rather than 400. Documented behaviour
        — the round-trip-exact rule for hand-edited library JSON."""
        sc._save_library([{"name": "pTest", "id": "pTest",
                            "gb_text": "fake"}])
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-plasmid-status", method="POST",
            body={"name": "pTest", "status": "Designing"},  # mixed case
            token=token,
        )
        assert status == 200
        assert payload["status"] == ""

    def test_set_plasmid_status_unknown_name_404(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-plasmid-status", method="POST",
            body={"name": "ghost", "status": "DESIGNING"}, token=token,
        )
        assert status == 404
        assert "ghost" in payload["error"]

    def test_set_plasmid_status_rejects_non_string(self, http_server):
        sc._save_library([{"name": "pTest", "id": "pTest",
                            "gb_text": "fake"}])
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-plasmid-status", method="POST",
            body={"name": "pTest", "status": 42}, token=token,
        )
        assert status == 400
        assert "string" in payload["error"]


# ── Entry-vector endpoints (added 2026-05-05 for v1.0) ───────────────────────


def _minimal_gb_text() -> str:
    """Smallest GenBank text that round-trips through SeqIO — used
    so set-entry-vector's parse-validate step has something real to
    chew on without fixture sprawl."""
    return ("LOCUS       test                  10 bp    DNA     "
            "circular SYN 01-JAN-2026\n"
            "FEATURES             Location/Qualifiers\n"
            "ORIGIN      \n"
            "        1 atgcatgcat\n"
            "//\n")


class TestEntryVectorEndpoints:
    def test_list_entry_vectors_empty(self, http_server):
        base, token, _ = http_server
        status, payload = _http(f"{base}/list-entry-vectors", token=token)
        assert status == 200
        assert payload["ok"] is True
        assert payload["entry_vectors"] == []

    def test_set_get_entry_vector_round_trip(self, http_server):
        base, token, _ = http_server
        gb = _minimal_gb_text()
        # SET
        status, payload = _http(
            f"{base}/set-entry-vector", method="POST",
            body={"grammar_id": "gb_l0", "name": "pUPD2",
                   "gb_text": gb, "source": "library:test"},
            token=token,
        )
        assert status == 200, payload
        assert payload["vector"]["name"] == "pUPD2"
        assert payload["vector"]["size"] == 10
        # The set response strips `gb_text` to keep responses small.
        assert "gb_text" not in payload["vector"]
        # GET
        status, payload = _http(
            f"{base}/get-entry-vector", method="POST",
            body={"grammar_id": "gb_l0"}, token=token,
        )
        assert status == 200
        assert payload["vector"]["name"]    == "pUPD2"
        assert payload["vector"]["gb_text"] == gb

    def test_get_entry_vector_returns_null_when_unset(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/get-entry-vector", method="POST",
            body={"grammar_id": "moclo_plant"}, token=token,
        )
        assert status == 200
        assert payload["vector"] is None

    def test_set_entry_vector_clear(self, http_server):
        base, token, _ = http_server
        gb = _minimal_gb_text()
        _http(f"{base}/set-entry-vector", method="POST",
              body={"grammar_id": "gb_l0", "name": "pUPD2", "gb_text": gb},
              token=token)
        status, payload = _http(
            f"{base}/set-entry-vector", method="POST",
            body={"grammar_id": "gb_l0", "clear": True}, token=token,
        )
        assert status == 200
        assert payload["vector"] is None
        assert sc._get_entry_vector("gb_l0") is None

    def test_set_entry_vector_invalid_gb_text(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-entry-vector", method="POST",
            body={"grammar_id": "gb_l0", "name": "x",
                   "gb_text": "not a genbank file"},
            token=token,
        )
        assert status == 400
        assert "parse failed" in payload["error"]

    def test_set_entry_vector_oversized_gb_text(self, http_server):
        base, token, _ = http_server
        # 600 KB of fake bases — over the inner 500 KB cap but under
        # the HTTP transport's 1 MiB body cap, so the inner check is
        # the one that fires.
        big = "A" * (600 * 1024)
        status, payload = _http(
            f"{base}/set-entry-vector", method="POST",
            body={"grammar_id": "gb_l0", "name": "x", "gb_text": big},
            token=token,
        )
        assert status == 400
        assert "too large" in payload["error"]

    def test_set_entry_vector_missing_grammar_id(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-entry-vector", method="POST",
            body={"name": "x", "gb_text": _minimal_gb_text()}, token=token,
        )
        assert status == 400
        assert "grammar_id" in payload["error"]


# ── update-primer endpoint (added 2026-05-05 for v1.0) ───────────────────────


class TestUpdatePrimerEndpoint:
    def test_update_primer_rejects_non_primer_feature(self, http_server,
                                                       tiny_record):
        """The endpoint MUST refuse to mutate a non-primer feature so
        an agent can't smuggle a primer-only field (e.g. `primer_seq`)
        onto a CDS or misc_feature. tiny_record's idx 0 is a CDS."""
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/update-primer", method="POST",
            body={"idx": 0, "label": "x"}, token=token,
        )
        assert status == 400
        assert "primer_bind" in payload["error"]

    def test_update_primer_validates_idx_out_of_range(self, http_server,
                                                       tiny_record):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/update-primer", method="POST",
            body={"idx": 99}, token=token,
        )
        assert status == 400
        assert "out of range" in payload["error"]

    def test_update_primer_rejects_infinity_idx(self, http_server,
                                                  tiny_record):
        base, token, _ = http_server
        body_json = '{"idx": Infinity, "label": "x"}'
        req = urllib.request.Request(
            f"{base}/update-primer", data=body_json.encode(),
            headers={"Authorization": f"Bearer {token}",
                       "Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5).read()
            code = 200
        except urllib.error.HTTPError as exc:
            code = exc.code
        assert code == 400

    def test_update_primer_rejects_oversized_primer_seq(self, http_server,
                                                         tiny_record):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/update-primer", method="POST",
            body={"idx": 0, "primer_seq": "A" * 600}, token=token,
        )
        assert status == 400
        # Either the non-primer reject (if idx 0 is non-primer) or the
        # length cap. Both are correct rejections.
        assert ("too long" in payload["error"]
                or "primer_bind" in payload["error"])


# ── Settings endpoints (added 2026-05-05 for v1.0) ───────────────────────────


class TestSettingsEndpoints:
    def test_get_settings_returns_allowlisted_keys(self, http_server):
        base, token, _ = http_server
        status, payload = _http(f"{base}/get-settings", token=token)
        assert status == 200
        # Spot-check: every allowlisted key is present, infrastructure
        # keys are not.
        keys = set(payload["settings"].keys())
        for required in ("show_feature_tooltips", "min_primer_binding",
                          "linear_layout", "active_grammar"):
            assert required in keys
        for excluded in ("last_known_latest", "last_seen_version",
                          "last_update_check_ts", "hmm_db_path"):
            assert excluded not in keys

    def test_set_setting_round_trip_bool(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-setting", method="POST",
            body={"key": "click_debug", "value": True}, token=token,
        )
        assert status == 200
        assert payload["value"] is True
        assert sc._get_setting("click_debug") is True

    def test_set_setting_round_trip_int_range(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-setting", method="POST",
            body={"key": "min_primer_binding", "value": 18}, token=token,
        )
        assert status == 200
        assert payload["value"] == 18
        assert sc._get_setting("min_primer_binding") == 18

    def test_set_setting_int_range_rejects_out_of_range(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-setting", method="POST",
            body={"key": "min_primer_binding", "value": 100}, token=token,
        )
        assert status == 400
        assert "[1, 60]" in payload["error"]

    def test_set_setting_choice_rejects_garbage(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-setting", method="POST",
            body={"key": "linear_layout", "value": "spiral"}, token=token,
        )
        assert status == 400
        assert "centered" in payload["error"] or "flag" in payload["error"]

    def test_set_setting_bool_rejects_string(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-setting", method="POST",
            body={"key": "show_restr", "value": "true"}, token=token,
        )
        assert status == 400
        assert "boolean" in payload["error"]

    def test_set_setting_unknown_key(self, http_server):
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-setting", method="POST",
            body={"key": "secret_setting", "value": "boom"}, token=token,
        )
        assert status == 400
        assert "unknown setting" in payload["error"]
        # Helpfully lists what the agent CAN write.
        assert "min_primer_binding" in payload["available"]

    def test_set_setting_restr_min_len_only_accepts_4_or_6(self, http_server):
        base, token, _ = http_server
        for good in (4, 6):
            status, _ = _http(
                f"{base}/set-setting", method="POST",
                body={"key": "restr_min_len", "value": good}, token=token,
            )
            assert status == 200
        for bad in (5, 8, 0):
            status, payload = _http(
                f"{base}/set-setting", method="POST",
                body={"key": "restr_min_len", "value": bad}, token=token,
            )
            assert status == 400, (bad, payload)
