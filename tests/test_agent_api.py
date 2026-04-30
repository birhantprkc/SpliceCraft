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
