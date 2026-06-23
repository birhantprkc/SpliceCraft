# pyright: reportArgumentType=false, reportCallIssue=false, reportAttributeAccessIssue=false
#
# Handlers (`_h_*`) return ``dict | tuple[dict, int]`` (success payload OR
# error tuple). Tests routinely unpack one or the other after asserting a
# status code — pyright can't follow the runtime invariant and tags every
# index op as an arg-type mismatch. Negative-input tests on the sanitizer
# helpers (`_sanitize_label`, `_sanitize_feat_type`, …) deliberately pass
# wrong types to verify rejection. `MockApp` stub methods preserve the
# real `PlasmidApp` signature for duck-typing even when the body ignores
# parameters (e.g. `clear_undo`). All three classes of noise are non-bugs
# and would drown out genuine signal; the project's `pyproject.toml`
# already excludes `tests/**` from pyright analysis for the same reason.
# This file-scope pragma keeps the harness diagnostics quiet too.
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
import types
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
        # Mirror the real `_annotate_with_feature_impl`: strand=2 (double-
        # stranded ◀▶) persists via a SpliceCraft_strand qualifier, and a
        # picked colour writes the ApEinfo_*color pair.
        if strand == 2:
            qualifiers["SpliceCraft_strand"] = ["double"]
        new_color = entry.get("color")
        if isinstance(new_color, str) and new_color.strip():
            qualifiers["ApEinfo_fwdcolor"] = [new_color.strip()]
            qualifiers["ApEinfo_revcolor"] = [new_color.strip()]
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


class TestStatusVersionSkew:
    """Stale-daemon detection (petunia agent-API running-log): `status`
    surfaces the on-disk installed version next to the running one so an
    agent notices when `splicecraft update` upgraded the package under a
    still-running `--agent` daemon (which keeps serving the old code)."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        saved = dict(sc._INSTALLED_VERSION_CACHE)
        sc._INSTALLED_VERSION_CACHE.update(version=None, ts=0.0, inflight=False)
        yield
        sc._INSTALLED_VERSION_CACHE.clear()
        sc._INSTALLED_VERSION_CACHE.update(saved)

    def test_running_version_present_and_aliases_version(self):
        s = sc._h_status(MockApp(), {})
        assert s["running_version"] == sc.__version__ == s["version"]

    def test_no_live_daemon_means_no_subprocess(self, monkeypatch):
        # Pure-handler path: no live app/daemon → installed unknown, never
        # stale, and the background refresh is NOT spawned (keeps the whole
        # test suite from shelling out on every status call). Force the
        # liveness gate to "no app" — an earlier async test may have left a
        # real `_LIVE_APP_REF` set, and that gate is what decides whether the
        # off-thread refresh fires.
        monkeypatch.setattr(sc._state, "_LIVE_APP_REF",
                            types.SimpleNamespace(get=lambda: None))
        s = sc._h_status(MockApp(), {})
        assert s["installed_version"] is None
        assert s["stale"] is False
        assert sc._INSTALLED_VERSION_CACHE["inflight"] is False

    def test_stale_flag_when_installed_differs(self, monkeypatch):
        monkeypatch.setattr(sc, "_installed_version_cached",
                            lambda: "0.0.0-other")
        s = sc._h_status(MockApp(), {})
        assert s["installed_version"] == "0.0.0-other"
        assert s["stale"] is True

    def test_not_stale_when_installed_matches_running(self, monkeypatch):
        monkeypatch.setattr(sc, "_installed_version_cached",
                            lambda: sc.__version__)
        assert sc._h_status(MockApp(), {})["stale"] is False

    def test_blocking_helper_reads_query(self, monkeypatch):
        monkeypatch.setattr(sc, "_query_installed_version",
                            lambda timeout=8.0: "1.2.3")
        assert sc._installed_version_cached(blocking=True) == "1.2.3"

    def test_cache_ttl_serves_without_requery(self, monkeypatch):
        calls = []

        def _q(timeout=8.0):
            calls.append(1)
            return "5.5.5"
        monkeypatch.setattr(sc, "_query_installed_version", _q)
        v1 = sc._installed_version_cached(blocking=True)   # 1 query
        v2 = sc._installed_version_cached()                # within TTL → cache
        assert v1 == v2 == "5.5.5"
        assert len(calls) == 1


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
        # set-setting mutates persisted state; must be token-gated.
        # Regression guard for 2026-05-14 security-audit fix where
        # the @_agent_endpoint decoration was missing `write=True`.
        assert eps["set-setting"]["write"] is True


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
        # 7 is not a valid arrow type (only -1/0/1/2 are).
        result = sc._h_add_feature(
            tiny_app, {"start": 0, "end": 10, "strand": 7}
        )
        payload, status = result
        assert status == 400
        assert "strand" in payload["error"]

    def test_strand_2_double_stranded_accepted(self, tiny_app):
        # Arrow-type parity with the UI: strand=2 (double-stranded ◀▶) is
        # valid and persists the SpliceCraft_strand qualifier.
        result = sc._h_add_feature(
            tiny_app, {"start": 0, "end": 9, "strand": 2, "label": "ds"})
        assert isinstance(result, dict) and result["strand"] == 2
        feat = tiny_app._current_record.features[-1]
        assert feat.qualifiers.get("SpliceCraft_strand") == ["double"]

    def test_strand_0_arrowless_accepted(self, tiny_app):
        result = sc._h_add_feature(
            tiny_app, {"start": 0, "end": 9, "strand": 0, "label": "flat"})
        assert isinstance(result, dict) and result["strand"] == 0

    def test_color_applied(self, tiny_app):
        result = sc._h_add_feature(
            tiny_app, {"start": 0, "end": 9, "color": "#1f77b4"})
        assert isinstance(result, dict) and result["color"] == "#1f77b4"
        feat = tiny_app._current_record.features[-1]
        assert feat.qualifiers.get("ApEinfo_fwdcolor") == ["#1f77b4"]
        assert feat.qualifiers.get("ApEinfo_revcolor") == ["#1f77b4"]

    def test_invalid_color_rejected(self, tiny_app):
        result = sc._h_add_feature(
            tiny_app, {"start": 0, "end": 9, "color": "octarine"})
        payload, status = result
        assert status == 400 and "color" in payload["error"]

    def test_qualifiers_applied(self, tiny_app):
        result = sc._h_add_feature(tiny_app, {
            "start": 0, "end": 9,
            "qualifiers": {"gene": "bla", "note": ["a", "b"]}})
        assert isinstance(result, dict)
        feat = tiny_app._current_record.features[-1]
        assert feat.qualifiers.get("gene") == ["bla"]
        assert feat.qualifiers.get("note") == ["a", "b"]

    def test_qualifiers_must_be_dict(self, tiny_app):
        result = sc._h_add_feature(
            tiny_app, {"start": 0, "end": 9, "qualifiers": ["nope"]})
        payload, status = result
        assert status == 400 and "qualifiers" in payload["error"]

    def test_unknown_keys_echoed(self, tiny_app):
        result = sc._h_add_feature(
            tiny_app, {"start": 0, "end": 9, "bogus": 1})
        assert isinstance(result, dict) and result["ignored"] == ["bogus"]

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

    def test_stale_load_counter_rejects(self, tiny_app):
        """Regression guard for 2026-05-17 adversarial audit: a canvas
        swap between handler entry and `_apply` running on the UI thread
        must drop the agent edit with 409. Pre-fix, `_h_add_feature`
        would happily annotate whatever record happened to be on the
        canvas at apply time — wrong molecule corruption."""
        tiny_app._record_load_counter = 0

        # Simulate the race: between handler entry (which captures
        # counter==0) and `_apply` execution, the UI thread loads a new
        # plasmid and bumps `_record_load_counter` to 1.
        orig_call = tiny_app.call_from_thread
        def racy_call(fn, *args, **kwargs):
            tiny_app._record_load_counter = 1
            return orig_call(fn, *args, **kwargs)
        tiny_app.call_from_thread = racy_call

        result = sc._h_add_feature(
            tiny_app, {"start": 0, "end": 10, "label": "t"}
        )
        assert isinstance(result, tuple), result
        payload, status = result
        assert status == 409
        assert "canvas reloaded" in payload["error"]


class TestDeleteUpdateFeatureStaleLoadGuard:
    """Regression guards for 2026-05-17 adversarial audit: the agent
    `delete-feature` and `update-feature` endpoints used to do all
    their work inside `_apply` on the UI thread without first
    capturing `_record_load_counter`. A canvas reload between
    handler entry and the queued `_apply` execution would have the
    handler delete / update feature `idx` of the WRONG molecule —
    silent cross-record data corruption."""

    def _racy_app(self, tiny_app):
        tiny_app._record_load_counter = 0
        orig_call = tiny_app.call_from_thread
        def racy_call(fn, *args, **kwargs):
            tiny_app._record_load_counter = 1
            return orig_call(fn, *args, **kwargs)
        tiny_app.call_from_thread = racy_call
        return tiny_app

    def test_delete_feature_rejects_on_stale_counter(self, tiny_app):
        app = self._racy_app(tiny_app)
        result = sc._h_delete_feature(app, {"idx": 0})
        assert isinstance(result, tuple), result
        payload, status = result
        assert status == 409
        assert "canvas reloaded" in payload["error"]

    def test_update_feature_rejects_on_stale_counter(self, tiny_app):
        app = self._racy_app(tiny_app)
        result = sc._h_update_feature(
            app, {"idx": 0, "label": "should-not-apply"}
        )
        assert isinstance(result, tuple), result
        payload, status = result
        assert status == 409
        assert "canvas reloaded" in payload["error"]


class TestDeleteUpdateFeatureTOCTOUSignature:
    """Sweep #32 (2026-05-26) adversarial audit: delete/update
    feature handlers used to do a bounds check + access inside
    `_apply` on the UI thread, but the agent's `idx` was captured
    on the worker thread. A concurrent `_apply` from another
    request could insert a feature, shifting indices, before
    this request's `_apply` ran — the bounds check then passed
    against the post-insert state but `pm._feats[idx]` referred
    to a DIFFERENT feature than the agent saw. Silent
    cross-feature corruption. Fix captures a signature
    (start/end/type/label) in a pre-flight UI-thread call and
    re-verifies inside `_apply`; mismatch → 409 Conflict."""

    def _make_racy_app(self, tiny_app):
        """Wrap `call_from_thread` so the SECOND call (the
        `_apply` closure, after the pre-flight signature
        capture) sees a record with a NEW feature injected at
        idx 0 — simulating a concurrent agent request that
        inserted a row mid-handler."""
        from Bio.SeqFeature import SeqFeature, FeatureLocation
        orig_call = tiny_app.call_from_thread
        call_n = [0]
        shadow = SeqFeature(
            FeatureLocation(0, 1, strand=1),
            type="shadow",
            qualifiers={"label": ["injected"]},
        )
        def racy_call(fn, *args, **kwargs):
            call_n[0] += 1
            if call_n[0] == 2:
                # Inject shadow BEFORE the queued `_apply` runs.
                # The mock's `query_one("#plasmid-map", ...)`
                # re-reads `_feats` from the record on every
                # call, so this shift is observable in `_apply`.
                tiny_app._current_record.features = (
                    [shadow]
                    + list(tiny_app._current_record.features)
                )
            return orig_call(fn, *args, **kwargs)
        tiny_app.call_from_thread = racy_call
        return tiny_app

    def test_delete_feature_detects_signature_drift(self, tiny_app):
        app = self._make_racy_app(tiny_app)
        result = sc._h_delete_feature(app, {"idx": 0})
        assert isinstance(result, tuple), result
        payload, status = result
        assert status == 409, (status, payload)
        assert "changed under us" in payload["error"], payload

    def test_update_feature_detects_signature_drift(self, tiny_app):
        app = self._make_racy_app(tiny_app)
        result = sc._h_update_feature(
            app, {"idx": 0, "label": "should-not-apply"},
        )
        assert isinstance(result, tuple), result
        payload, status = result
        assert status == 409
        assert "changed under us" in payload["error"]


class TestUpdateFeatureParity:
    """update-feature reaches the same arrow-type / colour / qualifier
    controls as the edit dialog (the UI-parity gap the user flagged:
    add/update-feature must set TYPE, ARROW TYPE, colour, etc.)."""

    def _app_with_feature(self, tiny_app):
        """Append a feature and return (app, idx, getter). `idx` is the
        appended feature's row in the non-source feature list (tiny_record
        already carries features, so it is NOT 0); `getter()` returns that
        exact SeqFeature after an update."""
        sc._h_add_feature(tiny_app, {"start": 0, "end": 9, "label": "f0",
                                      "force": True})
        tiny_app._unsaved = False
        nonsrc = [f for f in tiny_app._current_record.features
                  if f.type != "source"]
        idx = len(nonsrc) - 1
        # The appended feature is the LAST in record order (source aside).
        getter = lambda: [f for f in tiny_app._current_record.features
                          if f.type != "source"][idx]
        return tiny_app, idx, getter

    def test_update_strand_to_double(self, tiny_app):
        app, idx, feat = self._app_with_feature(tiny_app)
        r = sc._h_update_feature(app, {"idx": idx, "strand": 2, "force": True})
        assert isinstance(r, dict), r
        assert feat().qualifiers.get("SpliceCraft_strand") == ["double"]

    def test_update_strand_away_from_double_clears_qualifier(self, tiny_app):
        app, idx, feat = self._app_with_feature(tiny_app)
        sc._h_update_feature(app, {"idx": idx, "strand": 2, "force": True})
        sc._h_update_feature(app, {"idx": idx, "strand": 1, "force": True})
        assert "SpliceCraft_strand" not in feat().qualifiers

    def test_update_color_then_clear(self, tiny_app):
        app, idx, feat = self._app_with_feature(tiny_app)
        sc._h_update_feature(app, {"idx": idx, "color": "#abc", "force": True})
        assert feat().qualifiers.get("ApEinfo_fwdcolor") == ["#abc"]
        sc._h_update_feature(app, {"idx": idx, "color": "", "force": True})
        assert "ApEinfo_fwdcolor" not in feat().qualifiers

    def test_update_invalid_color_rejected(self, tiny_app):
        app, idx, _ = self._app_with_feature(tiny_app)
        r = sc._h_update_feature(
            app, {"idx": idx, "color": "burgundy", "force": True})
        assert isinstance(r, tuple) and r[1] == 400

    def test_update_qualifiers_merge(self, tiny_app):
        app, idx, feat = self._app_with_feature(tiny_app)
        sc._h_update_feature(
            app, {"idx": idx, "qualifiers": {"gene": "x", "note": "y"},
                  "force": True})
        assert feat().qualifiers.get("gene") == ["x"]
        assert feat().qualifiers.get("note") == ["y"]

    def test_update_invalid_strand_rejected(self, tiny_app):
        app, idx, _ = self._app_with_feature(tiny_app)
        r = sc._h_update_feature(app, {"idx": idx, "strand": 9, "force": True})
        assert isinstance(r, tuple) and r[1] == 400


class TestNewPlasmidHandler:
    """`new-plasmid` — create a plasmid from a raw sequence (the agent
    counterpart to Ctrl+N), loaded onto the canvas but NOT auto-saved."""

    def test_creates_and_loads(self):
        app = MockApp(record=None)
        r = sc._h_new_plasmid(app, {"name": "My Construct v1",
                                     "sequence": "ATGAAA" * 8,
                                     "circular": True})
        assert r["ok"] and r["saved"] is False
        assert r["topology"] == "circular" and r["length"] == 48
        # Spaced display name preserved (not the underscored LOCUS).
        assert sc.PlasmidApp._record_display_name(
            app._current_record) == "My Construct v1"

    def test_features_applied(self):
        app = MockApp(record=None)
        r = sc._h_new_plasmid(app, {
            "name": "p", "sequence": "ATGAAATAG" * 4,
            "features": [{"start": 0, "end": 9, "name": "orf",
                          "feature_type": "CDS"}]})
        assert r["n_features"] == 1

    def test_missing_name_400(self):
        assert sc._h_new_plasmid(
            MockApp(record=None), {"sequence": "ATGC"})[1] == 400

    def test_invalid_sequence_400(self):
        assert sc._h_new_plasmid(
            MockApp(record=None), {"name": "x", "sequence": "ZZZZ"})[1] == 400

    def test_empty_sequence_400(self):
        assert sc._h_new_plasmid(
            MockApp(record=None), {"name": "x", "sequence": ""})[1] == 400

    def test_features_must_be_list(self):
        assert sc._h_new_plasmid(MockApp(record=None), {
            "name": "x", "sequence": "ATGC", "features": "nope"})[1] == 400

    def test_dirty_guard_without_force(self):
        app = MockApp(record=None)
        app._unsaved = True
        assert sc._h_new_plasmid(
            app, {"name": "x", "sequence": "ATGC"})[1] == 409

    def test_unknown_keys_echoed(self):
        r = sc._h_new_plasmid(MockApp(record=None), {
            "name": "x", "sequence": "ATGC", "bogus": 1})
        assert r["ignored"] == ["bogus"]


_SENTINEL = object()


class _UndoMockApp:
    """Minimal undo/redo surface for the wrapper-logic tests. The REAL
    snapshot semantics are covered by the UndoController tests; here we only
    pin the endpoint's 422 / 409 / stack-delta reporting."""

    def __init__(self, undo=2, redo=0, blocked=False, record=_SENTINEL):
        if record is _SENTINEL:
            record = types.SimpleNamespace(seq="ATGCATGCAT")
        self._current_record = record
        self._undo_stack = list(range(undo))
        self._redo_stack = list(range(redo))
        self._blocked = blocked

    def _undo_blocked_by_modal(self):
        return self._blocked

    def _action_undo(self):
        if self._undo_stack:
            self._redo_stack.append(self._undo_stack.pop())

    def _action_redo(self):
        if self._redo_stack:
            self._undo_stack.append(self._redo_stack.pop())

    def call_from_thread(self, fn, *a, **k):
        return fn(*a, **k)


class TestUndoRedoHandlers:
    def test_undo_no_record_422(self):
        app = _UndoMockApp(record=None)
        assert sc._h_undo(app, {})[1] == 422

    def test_redo_no_record_422(self):
        app = _UndoMockApp(record=None)
        assert sc._h_redo(app, {})[1] == 422

    def test_undo_reports_delta(self):
        app = _UndoMockApp(undo=2, redo=0)
        r = sc._h_undo(app, {})
        assert r["ok"] and r["undone"] is True
        assert r["undo_remaining"] == 1 and r["redo_available"] == 1

    def test_undo_empty_stack_is_noop_not_error(self):
        app = _UndoMockApp(undo=0)
        r = sc._h_undo(app, {})
        assert r["ok"] and r["undone"] is False and r["undo_remaining"] == 0

    def test_undo_blocked_409(self):
        app = _UndoMockApp(undo=2, blocked=True)
        assert sc._h_undo(app, {})[1] == 409

    def test_redo_reports_delta(self):
        app = _UndoMockApp(undo=0, redo=2)
        r = sc._h_redo(app, {})
        assert r["ok"] and r["redone"] is True
        assert r["redo_remaining"] == 1 and r["undo_available"] == 1

    def test_redo_blocked_409(self):
        app = _UndoMockApp(redo=1, blocked=True)
        assert sc._h_redo(app, {})[1] == 409


class TestMultiAlignHandler:
    """`multi-align` — batch pairwise alignment (the Alt+A overlay)."""

    def test_explicit_sequences(self):
        r = sc._h_multi_align(MockApp(record=None), {
            "query": "ATGCATGCATGCATGCATGC",
            "targets": [{"sequence": "ATGCATGCATGCATGCATGC", "name": "same"},
                        {"sequence": "TTTTTTTTTTGGGGGGGGGG", "name": "diff"}]})
        assert r["ok"] and len(r["alignments"]) == 2
        assert r["alignments"][0]["identity_pct"] == 100.0
        # The big gapped strings are omitted to keep the batch small.
        assert "aligned_q" not in r["alignments"][0]

    def test_per_target_errors_dont_fail_batch(self):
        r = sc._h_multi_align(MockApp(record=None), {
            "query": "ATGCATGC",
            "targets": [{"sequence": "ATGCATGC"}, {"sequence": "ZZZ"},
                        {"name": "no-such-lib-entry"}]})
        assert r["alignments"][0]["identity_pct"] == 100.0
        assert "error" in r["alignments"][1]   # non-IUPAC
        assert "error" in r["alignments"][2]   # unresolvable

    def test_no_query_no_record_422(self):
        assert sc._h_multi_align(MockApp(record=None),
                                 {"targets": [{"sequence": "AT"}]})[1] == 422

    def test_empty_targets_400(self):
        assert sc._h_multi_align(MockApp(record=None),
                                 {"query": "AT", "targets": []})[1] == 400

    def test_too_many_targets_400(self):
        assert sc._h_multi_align(MockApp(record=None), {
            "query": "AT", "targets": [{"sequence": "AT"}] * 21})[1] == 400

    def test_bad_mode_400(self):
        assert sc._h_multi_align(MockApp(record=None), {
            "query": "AT", "targets": [{"sequence": "AT"}],
            "mode": "diagonal"})[1] == 400

    def test_uses_loaded_record_as_query(self, tiny_app, tiny_record):
        r = sc._h_multi_align(tiny_app, {
            "targets": [{"sequence": str(tiny_record.seq), "name": "self"}]})
        assert r["alignments"][0]["identity_pct"] == 100.0


class TestAttachExperimentImage:
    """`attach-experiment-image` — server-side image attach to a notebook
    entry (the conftest fixture sandboxes + authorises writes)."""

    def _png(self, tmp_path):
        p = tmp_path / "fig.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        return p

    def test_attach_roundtrip(self, tmp_path):
        ce = sc._h_create_experiment(None, {"title": "E", "body_md": "x"})
        r = sc._h_attach_experiment_image(None, {
            "experiment_id": ce["id"], "path": str(self._png(tmp_path))})
        assert r["ok"] and r["filename"].endswith(".png")
        exp = next(e for e in sc._load_experiments() if e["id"] == ce["id"])
        assert "![" in exp["body_md"]   # markdown ref embedded

    def test_missing_id_400(self, tmp_path):
        assert sc._h_attach_experiment_image(
            None, {"path": str(self._png(tmp_path))})[1] == 400

    def test_unknown_experiment_404(self, tmp_path):
        assert sc._h_attach_experiment_image(None, {
            "experiment_id": "nope", "path": str(self._png(tmp_path))})[1] == 404

    def test_non_image_rejected_400(self, tmp_path):
        ce = sc._h_create_experiment(None, {"title": "E"})
        txt = tmp_path / "x.txt"
        txt.write_text("not an image")
        assert sc._h_attach_experiment_image(None, {
            "experiment_id": ce["id"], "path": str(txt)})[1] == 400


class TestTraditionalCloning:
    """`simulate-traditional-cloning` + `traditional-clone` — a real digest +
    ligation that NEVER picks the insert by fragment size (the catastrophic-
    class never-assume-the-smaller-fragment rule)."""

    # EcoRI · backbone(A×200) · BamHI · stuffer(C×60), as a circular vector.
    VECTOR = "GAATTC" + "A" * 200 + "GGATCC" + "C" * 60
    # pad · EcoRI · payload · BamHI · pad — a PCR insert with primer-added sites.
    INSERT = "GCGC" + "GAATTC" + "ATGAAACCCGGGTTTTAA" + "GGATCC" + "GCGC"
    PAYLOAD = "ATGAAACCCGGGTTTTAA"

    def _base(self):
        return {"vector_seq": self.VECTOR,
                "vector_enzymes": ["EcoRI", "BamHI"],
                "insert_seq": self.INSERT,
                "insert_enzymes": ["EcoRI", "BamHI"]}

    def test_simulate_lists_both_fragments(self):
        r = sc._h_simulate_traditional_cloning(None, self._base())
        assert r["ok"] and r["n_vector_fragments"] == 2
        # The insert ligates into BOTH fragments — both reported, neither
        # silently dropped because it's the smaller piece.
        assert len(r["products"]) == 2
        assert any(p["length"] == 230 for p in r["products"])   # backbone+insert
        # Read response omits the verbose per-feature list.
        assert "features" not in r["products"][0]

    def test_simulate_bad_enzyme_400(self):
        b = self._base()
        b["vector_enzymes"] = ["NotAnEnzyme"]
        assert sc._h_simulate_traditional_cloning(None, b)[1] == 400

    def test_simulate_type_iis_insert_422(self):
        b = self._base()
        b["insert_enzymes"] = ["BsaI"]
        assert sc._h_simulate_traditional_cloning(None, b)[1] == 422

    def test_simulate_no_cuts_422(self):
        b = self._base()
        b["vector_seq"] = "A" * 100   # no EcoRI / BamHI sites
        assert sc._h_simulate_traditional_cloning(None, b)[1] == 422

    def test_clone_ambiguous_409(self):
        # Both fragments accept the insert → refuse, list the options.
        r = sc._h_traditional_clone(None, dict(self._base(), product_name="c"))
        assert isinstance(r, tuple) and r[1] == 409
        assert "options" in r[0] and len(r[0]["options"]) == 2

    def test_clone_disambiguated_saves_circular_golden(self):
        r = sc._h_traditional_clone(
            None, dict(self._base(), product_name="myclone", vector_frag_idx=0))
        assert r["ok"] and r["vector_frag_idx"] == 0
        ent = next(e for e in sc._load_library() if e["name"] == "myclone")
        assert ent["source"] == "agent:traditional"
        rec = sc._gb_text_to_record(ent["gb_text"])
        assert rec.annotations.get("topology") == "circular"
        # GOLDEN: the saved product carries the insert payload AND the
        # backbone — a genuine assembly, not a hand-built final ([INV-127]).
        seq = str(rec.seq).upper()
        assert self.PAYLOAD in seq or self.PAYLOAD in sc._rc(seq)
        assert "A" * 150 in seq or "A" * 150 in sc._rc(seq)

    def test_clone_bad_orientation_400(self):
        r = sc._h_traditional_clone(
            None, dict(self._base(), vector_frag_idx=0, orientation="sideways"))
        assert isinstance(r, tuple) and r[1] == 400

    def test_clone_no_ligation_422(self):
        # Vector cut only by EcoRI, insert only by BamHI → no matching ends.
        r = sc._h_traditional_clone(None, {
            "vector_seq": self.VECTOR, "vector_enzymes": ["EcoRI"],
            "insert_seq": "GCGC" + "GGATCC" + "ATGCATGC" + "GGATCC" + "GCGC",
            "insert_enzymes": ["BamHI"], "product_name": "x",
            "vector_frag_idx": 0})
        assert isinstance(r, tuple) and r[1] == 422


class TestApiTickets:
    """Regression coverage for the ProjectA constructor tickets
    (SPLICECRAFT_API_TICKETS.md): SC-B / SC-D / SC-G / SC-I."""

    def _h(self, name):
        return sc._state._AGENT_HANDLERS[name][0]

    # ── SC-B: delete-primer by name/id ──────────────────────────────────
    def test_sc_b_delete_primer_by_name(self):
        self._h("create-primer")(None, {"name": "oF1",
                                          "sequence": "ACGTACGTAC"})
        assert self._h("delete-primer")(None, {"name": "oF1"})["ok"]
        assert self._h("delete-primer")(None, {"name": "oF1"})[1] == 404

    def test_sc_b_delete_primer_ambiguous_409(self):
        self._h("create-primer")(None, {"name": "dup",
                                         "sequence": "AAAACCCCGG"})
        self._h("create-primer")(None, {"name": "dup",
                                         "sequence": "TTTTGGGGAA"})
        r = self._h("delete-primer")(None, {"name": "dup"})
        assert isinstance(r, tuple) and r[1] == 409
        assert len(r[0]["sequences"]) == 2

    def test_sc_b_delete_primer_missing_all_400(self):
        assert self._h("delete-primer")(None, {})[1] == 400

    # ── SC-D: create-primer honors `collection` (no silent MAIN landing) ──
    def test_sc_d_create_primer_honors_collection(self):
        self._h("create-primer-collection")(None, {"name": "ProjectA"})
        r = self._h("create-primer")(None, {
            "name": "pF", "sequence": "GGGGAAAACC", "collection": "ProjectA"})
        assert r["collection"] == "ProjectA"
        coll = next(c for c in sc._load_primer_collections()
                    if c["name"] == "ProjectA")
        assert any(p["name"] == "pF" for p in coll["primers"])
        # And NOT in the default/active collection.
        assert not any(p.get("name") == "pF" for p in sc._load_primers())

    def test_sc_d_create_primer_unknown_collection_404(self):
        r = self._h("create-primer")(None, {
            "name": "x", "sequence": "CCCCGGGGAA", "collection": "NoSuch"})
        assert isinstance(r, tuple) and r[1] == 404   # loud, not ok:true

    # ── SC-G: one concept, one key (aliases accepted) ───────────────────
    def test_sc_g_sequence_alias_on_rbs_strength(self):
        r = self._h("rbs-strength")(None, {
            "sequence": "AGGAGGACAACAATGAAACGT", "start": 13})
        assert isinstance(r, dict) and r.get("ok")

    def test_sc_g_sequence_alias_on_design_mutagenesis(self):
        r = self._h("design-mutagenesis")(None, {
            "sequence": "ATG" + "GCA" * 40 + "TAA", "mutation": "A5G"})
        # Accepted (not a 400 about a missing cds_dna).
        assert isinstance(r, dict) or r[1] != 400

    # ── SC-I: copy-plasmid into a (non-active) collection ───────────────
    def _seed_collections(self):
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ATGC" * 20), id="FFE1", name="FFE_1_ENTRY",
                        annotations={"molecule_type": "DNA",
                                     "topology": "circular"})
        rec._tui_display_name = "FFE 1 ENTRY"
        gb = sc._record_to_gb_text(rec)
        sc._save_collections([
            {"name": "ProjectB", "plasmids": [
                {"id": "FFE1", "name": "FFE 1 ENTRY", "gb_text": gb,
                 "size": 80}]},
            {"name": "ProjectA", "plasmids": []},
        ])

    def test_sc_i_copy_plasmid(self):
        self._seed_collections()
        r = sc._h_copy_plasmid(None, {"name": "FFE 1 ENTRY",
                                       "to": "ProjectA", "from": "ProjectB"})
        assert r["ok"] and r["to"] == "ProjectA"
        coll = next(c for c in sc._load_collections()
                    if c["name"] == "ProjectA")
        copied = next(p for p in coll["plasmids"]
                      if p["name"] == "FFE 1 ENTRY")
        # Name + features (gb_text, incl. the SC-E display stamp) preserved.
        assert "SpliceCraft-name: FFE 1 ENTRY" in copied["gb_text"]
        # Duplicate → 409, missing source → 404, missing 'to' → 400.
        assert sc._h_copy_plasmid(None, {"name": "FFE 1 ENTRY",
                                          "to": "ProjectA"})[1] == 409
        assert sc._h_copy_plasmid(None, {"name": "Nope",
                                          "to": "ProjectA"})[1] == 404
        assert sc._h_copy_plasmid(None, {"name": "FFE 1 ENTRY"})[1] == 400


class TestPrimerCollectionReadsAndMove:
    """SC-J (collection is a real partition for READS) + SC-K (move-primer,
    no create/delete data-loss footgun)."""

    def _h(self, name):
        return sc._state._AGENT_HANDLERS[name][0]

    def _seed(self):
        # 5 in the default library, 3 filed into a named collection.
        # Distinct 2-mer suffixes so the dedup-by-sequence policy keeps all 5.
        for i, suf in enumerate(["AA", "CC", "GG", "TT", "AG"]):
            self._h("create-primer")(None, {
                "name": f"m{i}", "sequence": "ACGT" * 5 + suf})
        self._h("create-primer-collection")(None, {"name": "ProjA"})
        for i, suf in enumerate(["AA", "CC", "GG"]):
            self._h("create-primer")(None, {
                "name": f"p{i}", "sequence": "TTTT" * 5 + suf,
                "collection": "ProjA"})

    # ── SC-J: list-primers / get-primer honor {collection} ──────────────
    def test_list_primers_scoped_to_collection(self):
        self._seed()
        assert self._h("list-primers")(None, {"collection": "ProjA"})["count"] == 3
        assert self._h("list-primers")(None, {})["count"] == 5    # default
        assert self._h("list-primers")(None, {"collection": "Nope"})[1] == 404

    def test_get_primer_by_name_in_collection(self):
        self._seed()
        r = self._h("get-primer")(None, {"name": "p0", "collection": "ProjA"})
        assert r["primer"]["name"] == "p0"
        assert self._h("get-primer")(None, {"name": "p0"})[1] == 404  # not default
        assert self._h("get-primer")(None,
                                      {"name": "m0"})["primer"]["name"] == "m0"

    # ── SC-K: move-primer never loses the primer ────────────────────────
    def test_move_primer_no_data_loss(self):
        self._seed()
        r = self._h("move-primer")(None, {"name": "m0", "to": "ProjA"})
        assert r["ok"] and r["from"] == "" and r["to"] == "ProjA"
        assert self._h("list-primers")(None, {})["count"] == 4          # default −1
        assert self._h("list-primers")(None, {"collection": "ProjA"})["count"] == 4
        # The primer is in ProjA, not lost.
        assert self._h("get-primer")(None,
                                     {"name": "m0", "collection": "ProjA"})["primer"]["name"] == "m0"

    def test_move_primer_errors(self):
        self._seed()
        assert self._h("move-primer")(None, {"name": "m1", "to": "Nope"})[1] == 404
        assert self._h("move-primer")(None, {"to": "ProjA"})[1] == 400  # no selector
        # Already moved → not found in default.
        self._h("move-primer")(None, {"name": "m0", "to": "ProjA"})
        assert self._h("move-primer")(None,
                                      {"name": "m0", "to": "ProjA"})[1] in (404, 409)

    def test_move_primer_refuses_default_while_named_active(self):
        # Mirror-safety guard: touching the default store while a named
        # collection is active would mis-mirror — refuse with 409.
        self._seed()
        self._h("set-active-primer-collection")(None, {"name": "ProjA"})
        r = self._h("move-primer")(None, {"name": "m1", "to": "ProjA"})
        assert isinstance(r, tuple) and r[1] == 409

    # ── delete-primer {collection}: prune a collection without data loss ──
    def test_delete_primer_from_collection_keeps_dups_elsewhere(self):
        self._seed()
        # File a copy of a default primer (same sequence) INTO ProjA — the
        # "mirror pollution" shape — then prune it from ProjA only.
        self._h("create-primer")(None, {
            "name": "dup", "sequence": "ACGT" * 5 + "AA", "collection": "ProjA"})
        assert self._h("list-primers")(None, {"collection": "ProjA"})["count"] == 4
        r = self._h("delete-primer")(None, {
            "sequence": "ACGT" * 5 + "AA", "collection": "ProjA"})
        assert r["ok"] and r["removed"] == 1 and r["collection"] == "ProjA"
        # Gone from ProjA, but the original copy survives in the default lib.
        assert self._h("list-primers")(None, {"collection": "ProjA"})["count"] == 3
        assert self._h("get-primer")(None,
                                     {"sequence": "ACGT" * 5 + "AA"})["primer"]["name"] == "m0"

    def test_delete_primer_collection_guards(self):
        self._seed()
        assert self._h("delete-primer")(None,
                                        {"name": "x", "collection": "Nope"})[1] == 404
        assert self._h("delete-primer")(None,
                                        {"name": "absent", "collection": "ProjA"})[1] == 404
        # Active named collection → refuse (mirror safety).
        self._h("set-active-primer-collection")(None, {"name": "ProjA"})
        assert self._h("delete-primer")(None,
                                        {"name": "p0", "collection": "ProjA"})[1] == 409


class TestContainerManagement:
    """rename-/delete- for primer collections + parts bins — the container
    side of full-CRUD parity. Renaming/deleting the ACTIVE container must
    follow the active-pointer and swap the live mirror ([INV-83]); deleting
    the last container is refused."""

    def _h(self, name):
        return sc._state._AGENT_HANDLERS[name][0]

    # ── primer collections ──────────────────────────────────────────────
    def test_rename_primer_collection(self):
        for nm in ("Alpha", "Beta"):
            self._h("create-primer-collection")(None, {"name": nm})
        self._h("set-active-primer-collection")(None, {"name": "Alpha"})
        self._h("create-primer")(None,
                                 {"name": "p1", "sequence": "ACGT" * 5 + "GG"})
        # rename a NON-active bin (case-insensitive lookup).
        r = self._h("rename-primer-collection")(None,
                                                {"name": "beta", "new_name": "Gamma"})
        assert r["name"] == "Gamma" and r["renamed_from"] == "Beta"
        # collision + unknown.
        assert self._h("rename-primer-collection")(None,
                                                   {"name": "Alpha", "new_name": "Gamma"})[1] == 409
        assert self._h("rename-primer-collection")(None,
                                                   {"name": "nope", "new_name": "X"})[1] == 404
        # rename the ACTIVE collection → active-pointer follows, content kept.
        self._h("rename-primer-collection")(None,
                                            {"name": "Alpha", "new_name": "A2"})
        assert sc._get_active_primer_collection_name() == "A2"
        assert self._h("list-primers")(None, {"collection": "A2"})["count"] == 1

    def test_delete_primer_collection(self):
        for nm in ("Alpha", "Beta", "Delta"):
            self._h("create-primer-collection")(None, {"name": nm})
        self._h("set-active-primer-collection")(None, {"name": "Alpha"})
        # delete a NON-active collection — active pointer untouched.
        d = self._h("delete-primer-collection")(None, {"name": "Beta"})
        assert d["deleted"] == "Beta" and d["promoted"] == ""
        assert sc._get_active_primer_collection_name() == "Alpha"
        # delete the ACTIVE collection → promote first remaining + mirror-swap.
        d2 = self._h("delete-primer-collection")(None, {"name": "Alpha"})
        assert d2["deleted"] == "Alpha" and d2["promoted"] != ""
        assert sc._get_active_primer_collection_name() == d2["promoted"]
        # can't delete the last remaining named collection.
        last = sc._load_primer_collections()[0]["name"]
        assert self._h("delete-primer-collection")(None, {"name": last})[1] == 409
        assert self._h("delete-primer-collection")(None, {"name": "ghost"})[1] == 404

    # ── parts bins ──────────────────────────────────────────────────────
    def test_rename_parts_bin(self):
        for nm in ("BinA", "BinB"):
            self._h("create-parts-bin")(None, {"name": nm})
        self._h("set-active-parts-bin")(None, {"name": "BinA"})
        self._h("create-part")(None, {
            "name": "part1", "sequence": "ACGTACGTACGT", "bin": "BinA"})
        r = self._h("rename-parts-bin")(None,
                                        {"name": "binb", "new_name": "BinG"})
        assert r["name"] == "BinG" and r["renamed_from"] == "BinB"
        assert self._h("rename-parts-bin")(None,
                                           {"name": "BinA", "new_name": "BinG"})[1] == 409
        self._h("rename-parts-bin")(None, {"name": "BinA", "new_name": "BinA2"})
        assert sc._get_active_parts_bin_name() == "BinA2"
        assert self._h("list-parts")(None, {"bin": "BinA2"})["count"] == 1

    def test_delete_parts_bin(self):
        for nm in ("BinA", "BinB", "BinD"):
            self._h("create-parts-bin")(None, {"name": nm})
        self._h("set-active-parts-bin")(None, {"name": "BinA"})
        d = self._h("delete-parts-bin")(None, {"name": "BinB"})
        assert d["deleted"] == "BinB" and d["promoted"] == ""
        d2 = self._h("delete-parts-bin")(None, {"name": "BinA"})
        assert d2["promoted"] != ""
        assert sc._get_active_parts_bin_name() == d2["promoted"]
        last = sc._load_parts_bin_collections()[0]["name"]
        assert self._h("delete-parts-bin")(None, {"name": last})[1] == 409
        assert self._h("delete-parts-bin")(None, {"name": "ghost"})[1] == 404


class TestExperimentProjectCRUD:
    """SC-M-class: create/list/delete-experiment honor a ``{project}`` scope
    (a real partition, not silently filed into the active project), and
    move-experiment relocates an entry without a create/delete data-loss
    round-trip. Active-project writes stay mirror-consistent ([INV-83])."""

    def _h(self, name):
        return sc._state._AGENT_HANDLERS[name][0]

    def _seed(self):
        # Two named projects; ProjA active with one live entry.
        self._h("create-experiment-project")(None, {"name": "ProjA"})
        self._h("create-experiment-project")(None, {"name": "ProjB"})
        self._h("set-active-experiment-project")(None, {"name": "ProjA"})
        self._h("create-experiment")(None, {"title": "live A"})  # active ProjA

    # ── create-experiment {project} files into the named project ─────────
    def test_create_experiment_scoped_to_project(self):
        self._seed()
        r = self._h("create-experiment")(None,
                                         {"title": "filed B", "project": "ProjB"})
        assert r["ok"] and r["project"] == "ProjB"
        # Landed in ProjB, NOT the active ProjA.
        assert self._h("list-experiments")(None,
                                           {"project": "ProjB"})["experiments"][0]["title"] == "filed B"
        assert len(self._h("list-experiments")(None,
                                               {"project": "ProjA"})["experiments"]) == 1
        # Unknown project + bad type.
        assert self._h("create-experiment")(None,
                                            {"title": "x", "project": "Nope"})[1] == 404
        assert self._h("create-experiment")(None,
                                            {"title": "x", "project": 5})[1] == 400

    # ── list-experiments {project} is a real read partition ──────────────
    def test_list_experiments_scoped(self):
        self._seed()
        self._h("create-experiment")(None, {"title": "b1", "project": "ProjB"})
        assert len(self._h("list-experiments")(None,
                                              {"project": "ProjB"})["experiments"]) == 1
        assert len(self._h("list-experiments")(None, {})["experiments"]) == 1  # active
        assert self._h("list-experiments")(None, {"project": "Nope"})[1] == 404

    # ── delete-experiment {project} prunes the named project ─────────────
    def test_delete_experiment_scoped_and_active_guard(self):
        self._seed()
        r = self._h("create-experiment")(None,
                                         {"title": "doomed", "project": "ProjB"})
        eid = r["id"]
        d = self._h("delete-experiment")(None, {"id": eid, "project": "ProjB"})
        assert d["ok"] and d["project"] == "ProjB" and d["remaining"] == 0
        # Deleting from the ACTIVE project by name → 409 (switch away first).
        live = self._h("list-experiments")(None, {})["experiments"][0]["id"]
        assert self._h("delete-experiment")(None,
                                           {"id": live, "project": "ProjA"})[1] == 409
        # Unknown project / unknown id.
        assert self._h("delete-experiment")(None,
                                           {"id": eid, "project": "Nope"})[1] == 404
        assert self._h("delete-experiment")(None,
                                           {"id": "exp-ffffffff", "project": "ProjB"})[1] == 404

    # ── move-experiment never loses the entry ────────────────────────────
    def test_move_experiment_no_data_loss(self):
        self._seed()
        live = self._h("list-experiments")(None, {})["experiments"][0]["id"]
        r = self._h("move-experiment")(None,
                                      {"id": live, "to": "ProjB", "from": "ProjA"})
        assert r["ok"] and r["from"] == "ProjA" and r["to"] == "ProjB"
        # Gone from ProjA (active, mirror re-synced), now in ProjB.
        assert len(self._h("list-experiments")(None, {})["experiments"]) == 0
        assert len(self._h("list-experiments")(None,
                                              {"project": "ProjB"})["experiments"]) == 1

    def test_move_experiment_errors(self):
        self._seed()
        live = self._h("list-experiments")(None, {})["experiments"][0]["id"]
        assert self._h("move-experiment")(None,
                                         {"id": live, "to": "Nope"})[1] == 404   # bad dest
        assert self._h("move-experiment")(None, {"to": "ProjB"})[1] == 400        # no id
        assert self._h("move-experiment")(None, {"id": live})[1] == 400           # no dest
        # Auto-find from active, then same-project move → 409.
        assert self._h("move-experiment")(None,
                                         {"id": live, "to": "ProjA"})[1] == 409


class TestMovePlasmid:
    """The last container-CRUD parity gap: move-plasmid relocates a plasmid
    between collections atomically (no copy/delete data-loss round-trip).
    Unlike copy-plasmid it handles the ACTIVE collection on either side,
    re-staging the live plasmid_library.json mirror ([INV-83])."""

    def _ent(self, name, i):
        return {"id": f"id-{name}-{i}", "name": name, "gb_text": "", "size": 80}

    def _names(self, coll):
        c = next(c for c in sc._load_collections() if c["name"] == coll)
        return [p["name"] for p in c["plasmids"]]

    def _lib(self):
        return [p["name"] for p in sc._load_library()]

    def _seed(self):
        # CollC is the (neutral) active collection so non-active moves leave
        # the live mirror untouched; CollA/CollB hold the movable plasmids.
        sc._save_collections([
            {"name": "CollA",
             "plasmids": [self._ent("pA1", 1), self._ent("pA2", 2)],
             "saved": "2026-01-01"},
            {"name": "CollB", "plasmids": [self._ent("pB1", 1)],
             "saved": "2026-01-01"},
            {"name": "CollC", "plasmids": [], "saved": "2026-01-01"},
        ])
        sc._set_active_collection_name("CollC")
        sc._restore_library_from_active_collection()

    def test_move_between_non_active_collections(self):
        self._seed()
        r = sc._h_move_plasmid(None, {"name": "pA1", "to": "CollB"})
        assert r["ok"] and r["from"] == "CollA" and r["to"] == "CollB"
        assert self._names("CollA") == ["pA2"]
        assert "pA1" in self._names("CollB")
        assert self._lib() == []  # active CollC mirror untouched

    def test_move_into_and_out_of_active_remirrors(self):
        self._seed()
        # Into active CollC → live mirror gains the plasmid.
        sc._h_move_plasmid(None, {"name": "pB1", "to": "CollC", "from": "CollB"})
        assert "pB1" in self._names("CollC") and self._lib() == ["pB1"]
        # Out of active CollC → live mirror shrinks (L3-safe, no guard trip).
        sc._h_move_plasmid(None, {"name": "pB1", "to": "CollA"})
        assert self._lib() == [] and "pB1" in self._names("CollA")

    def test_move_plasmid_guards(self):
        self._seed()
        assert sc._h_move_plasmid(None, {"name": "ghost", "to": "CollA"})[1] == 404
        assert sc._h_move_plasmid(None, {"name": "pA2", "to": "Nope"})[1] == 404
        assert sc._h_move_plasmid(None, {"to": "CollA"})[1] == 400
        # already-in-target no-op.
        assert sc._h_move_plasmid(None,
                                  {"name": "pA2", "to": "CollA"})[1] == 409

    def test_move_plasmid_ambiguous_and_collision(self):
        sc._save_collections([
            {"name": "X", "plasmids": [self._ent("dup", 1)], "saved": "2026-01-01"},
            {"name": "Y", "plasmids": [self._ent("dup", 2)], "saved": "2026-01-01"},
        ])
        sc._set_active_collection_name("X")
        sc._restore_library_from_active_collection()
        # Same name in two collections → ambiguous without 'from'.
        assert sc._h_move_plasmid(None, {"name": "dup", "to": "Y"})[1] == 409
        # Name already present in target → refuse (no silent rename).
        assert sc._h_move_plasmid(None,
                                  {"name": "dup", "to": "Y", "from": "X"})[1] == 409


class TestDataEnvelope:
    """SC-C: the dispatcher adds a predictable `data` field to success
    responses (pure helper `_agent_data_envelope`)."""

    def test_single_content_key_unwraps_to_value(self):
        assert sc._agent_data_envelope(
            {"ok": True, "seq": "ACGT"}, 200)["data"] == "ACGT"
        assert sc._agent_data_envelope(
            {"ok": True, "library": [1, 2]}, 200)["data"] == [1, 2]

    def test_multi_content_key_is_dict(self):
        d = sc._agent_data_envelope({"ok": True, "a": 1, "b": 2}, 200)["data"]
        assert d == {"a": 1, "b": 2}

    def test_meta_excluded_originals_preserved(self):
        r = sc._agent_data_envelope(
            {"ok": True, "seq": "AC", "ignored": ["z"], "_stale": True}, 200)
        assert r["data"] == "AC"                       # meta stripped
        assert r["seq"] == "AC" and r["ignored"] == ["z"]   # non-breaking

    def test_errors_and_non2xx_untouched(self):
        assert "data" not in sc._agent_data_envelope({"error": "x"}, 400)
        assert "data" not in sc._agent_data_envelope(
            {"ok": False, "error": "y"}, 422)

    def test_existing_data_not_clobbered(self):
        assert sc._agent_data_envelope(
            {"ok": True, "data": "keep", "x": 1}, 200)["data"] == "keep"

    def test_empty_content_no_data(self):
        assert "data" not in sc._agent_data_envelope({"ok": True}, 200)


class TestRestartEndpoint:
    """SC-A: `restart` re-execs a HEADLESS daemon to pick up an update;
    refuses in a GUI session (would kill the live TUI)."""

    def test_non_headless_refused_409(self):
        called = []
        orig = sc._agent_schedule_restart
        sc._agent_schedule_restart = lambda: called.append(True)
        try:
            r = sc._h_restart(types.SimpleNamespace(_headless=False), {})
            assert isinstance(r, tuple) and r[1] == 409
            assert not called                # did NOT schedule a re-exec
        finally:
            sc._agent_schedule_restart = orig

    def test_headless_schedules_restart(self):
        called = []
        orig = sc._agent_schedule_restart
        sc._agent_schedule_restart = lambda: called.append(True)
        try:
            r = sc._h_restart(types.SimpleNamespace(_headless=True), {})
            assert r["ok"] and r["restarting"] is True
            assert r["running_version"] == sc.__version__
            assert called == [True]
        finally:
            sc._agent_schedule_restart = orig


class TestGoldenGate:
    """SC-H: Type IIS (BsaI) Golden Gate / MoClo assembly — overhang-directed,
    order-independent, a real digest + ligation."""

    @staticmethod
    def _cassette(oh5, body, oh3):
        # The canonical L0 part: BsaI sites release a body with (oh5, oh3).
        return "GGTCTC" + "A" + oh5 + body + oh3 + "A" + "GAGACC"

    def _design(self):
        A = self._cassette("GGAG", "ATGAAACCCGGGTTTACGT" * 2, "AATG")
        B = self._cassette("AATG", "TTGCATGCATGCTAGCTAG" * 2, "CGCT")
        V = self._cassette("CGCT", "GGGGCCCCAAAATTTT" * 8, "GGAG")
        return A, B, V

    def test_simulate_assembles_circle(self):
        A, B, V = self._design()
        r = sc._h_simulate_golden_gate(None, {"parts": [A, B], "vector": V})
        assert r["ok"] and r["result"]["ok"]
        assert r["result"]["circular"] and r["result"]["n_parts"] == 2
        assert r["result"]["n_residual_sites"] == 0
        assert r["result"]["warnings"] == []

    def test_order_independent(self):
        A, B, V = self._design()
        r1 = sc._h_simulate_golden_gate(None, {"parts": [A, B], "vector": V})
        r2 = sc._h_simulate_golden_gate(None, {"parts": [B, A], "vector": V})
        assert r1["result"]["length"] == r2["result"]["length"]

    def test_assemble_saves_circular_product(self):
        A, B, V = self._design()
        r = sc._h_golden_gate_assemble(
            None, {"parts": [A, B], "vector": V, "product_name": "gg1"})
        assert r["ok"] and r["saved_name"] == "gg1"
        ent = next(e for e in sc._load_library() if e["name"] == "gg1")
        assert ent["source"] == "agent:golden-gate"
        rec = sc._gb_text_to_record(ent["gb_text"])
        assert rec.annotations.get("topology") == "circular"
        seq = str(rec.seq).upper()
        for body in ("ATGAAACCCGGGTTTACGT", "TTGCATGCATGCTAGCTAG",
                     "GGGGCCCCAAAATTTT"):
            assert body in seq or body in sc._rc(seq), f"{body} missing"

    def test_non_type_iis_rejected(self):
        A, B, V = self._design()
        assert sc._h_golden_gate_assemble(
            None, {"parts": [A, B], "vector": V, "enzyme": "EcoRI",
                   "product_name": "x"})[1] == 422

    def test_empty_parts_400(self):
        _, _, V = self._design()
        assert sc._h_simulate_golden_gate(
            None, {"parts": [], "vector": V})[1] == 400

    def test_part_with_internal_site_fails(self):
        A, B, V = self._design()
        bad = self._cassette("GGAG", "ATG" + "GGTCTC" + "ATTTT", "AATG")
        r = sc._h_simulate_golden_gate(None, {"parts": [bad, B], "vector": V})
        assert r["result"]["ok"] is False and r["result"]["errors"]


class TestReplaceSequenceSizeCap:
    """Sweep #32 adversarial audit: `_h_replace_sequence` used
    to be capped only on the input `bases` field (1 MB via
    `_sanitize_bases`). A 100 MB pre-loaded record could be
    extended by ~1 MB on every call, growing unbounded. Result
    sequence cap (50 MB) blocks the bloat path."""

    def test_replace_refuses_when_result_too_large(self, tiny_app):
        # Build a record near the 50 MB cap. The mock app's
        # `_seq_len` returns `len(rec.seq)`; rec.seq is a Bio
        # Seq. Patch it to look 50 MB without allocating.
        from unittest.mock import MagicMock
        original_seq = tiny_app._current_record.seq
        big_n = 50 * 1024 * 1024
        tiny_app._current_record = MagicMock(
            seq=MagicMock(__len__=lambda self: big_n),
        )
        # Asking to append 1 KB at the end → final = 50 MB + 1 KB
        result = sc._h_replace_sequence(tiny_app, {
            "start": big_n,
            "end":   big_n,
            "bases": "A" * 1024,
            "force": True,
        })
        # Restore for the rest of the test suite.
        tiny_app._current_record.seq = original_seq
        # Expect 413 Payload Too Large with the cap details.
        assert isinstance(result, tuple), result
        payload, status = result
        assert status == 413, (status, payload)
        assert "result sequence too large" in payload["error"]
        assert payload["limit_bp"] == 50 * 1024 * 1024
        assert payload["final_bp"] > 50 * 1024 * 1024


class TestSaveHandler:
    def test_refuses_when_no_record(self):
        app = MockApp(record=None)
        result = sc._h_save(app, {})
        payload, status = result
        assert status == 422
        assert "nothing to save" in payload["error"]

    def test_calls_do_save(self, tiny_app):
        # The tiny_app record isn't in the library and has no source
        # file, so saving CREATES a new entry — opt in with create:true
        # (the homeless-new-record save gate, snag #16).
        result = sc._h_save(tiny_app, {"create": True})
        assert result["ok"] is True
        assert result["created"] is True
        assert tiny_app._saved is True

    def test_save_gate_blocks_homeless_create(self, tiny_app):
        # Without create:true a record with no source file that isn't in
        # the library refuses to silently create an entry (snag #16 —
        # the fetch->save pollution guard).
        result = sc._h_save(tiny_app, {})
        assert isinstance(result, tuple) and result[1] == 409
        assert result[0].get("would_create") is True
        assert tiny_app._saved is False

    def test_save_echoes_ignored_keys(self, tiny_app):
        # Unknown body keys are surfaced, not silently dropped (snag #14).
        result = sc._h_save(tiny_app, {"create": True, "new_name": "x"})
        assert result["ok"] is True
        assert result["ignored"] == ["new_name"]


class TestAgentApiAuditFixes:
    """Regression coverage for the agent-API audit pass (the agent-driven
    real-world build snags): rename-plasmid, schema discovery via
    `doc_full`, the active-pointer getters, and the ignored-key echo.
    The full HTTP round-trips (cross-collection load-entry name
    preservation, rename, save-gate) are exercised by the verifier."""

    def test_rename_plasmid_requires_old_and_new(self):
        app = MockApp(record=None)
        assert sc._h_rename_plasmid(app, {"new": "x"})[1] == 400
        assert sc._h_rename_plasmid(app, {"old": "x"})[1] == 400
        assert sc._h_rename_plasmid(app, {"old": "x", "new": "  "})[1] == 400

    def test_load_entry_requires_key(self):
        app = MockApp(record=None)
        assert sc._h_load_entry(app, {})[1] == 400

    def test_get_active_parity_endpoints_registered(self):
        H = sc._state._AGENT_HANDLERS
        for ep in ("get-active-collection", "get-active-codon-table",
                   "get-active-primer-collection", "get-active-parts-bin",
                   "get-active-experiment-project",
                   "get-active-hmm-database",
                   "get-active-enzyme-collection", "rename-plasmid"):
            assert ep in H, f"missing endpoint {ep}"

    def test_tools_emits_full_docstring(self):
        eps = sc._h_tools(None, {})["endpoints"]
        assert eps and all("doc_full" in e for e in eps)
        rp = next(e for e in eps if e["name"] == "rename-plasmid")
        # doc_full carries the body schema, not just the one-line summary.
        assert "old" in rp["doc_full"] and "new" in rp["doc_full"]
        assert len(rp["doc_full"]) > len(rp["doc"])

    def test_ignored_keys_helper(self):
        assert sc._agent_ignored_keys({"force": 1, "a": 2}, set()) == ["a"]
        assert sc._agent_ignored_keys({"force": 1}, set()) == []
        assert sc._agent_ignored_keys("not-a-dict", {"x"}) == []


class TestFeaturesHandler:
    def test_empty_when_no_record(self):
        app = MockApp(record=None)
        assert sc._h_features(app, {})["features"] == []

    def test_lists_feature_dicts(self, tiny_app):
        feats = sc._h_features(tiny_app, {})["features"]
        assert len(feats) >= 1
        assert all("idx" in f and "start" in f and "end" in f
                    for f in feats)


class TestFoldRnaHandler:
    """`fold-rna` — pure-Python RNA MFE folding via the agent API. The
    handler is record-independent (folds the payload sequence), so `app`
    is unused."""

    def test_folds_stemloop(self):
        r = sc._h_fold_rna(None, {"sequence": "GGGGAAAACCCC"})
        assert r["ok"] is True
        assert r["structure"] == "((((....))))"
        assert abs(r["dg"] - (-5.40)) < 0.011
        assert r["length"] == 12

    def test_seq_alias_and_dna_t(self):
        r = sc._h_fold_rna(None, {"seq": "GGGGTTTTCCCC"})    # 'seq' alias + T->U
        assert r["structure"] == "((((....))))"

    def test_missing_sequence_400(self):
        assert sc._h_fold_rna(None, {})[1] == 400

    def test_non_string_400(self):
        assert sc._h_fold_rna(None, {"sequence": 123})[1] == 400

    def test_ambiguous_400(self):
        body, code = sc._h_fold_rna(None, {"sequence": "ACGUN"})
        assert code == 400 and "error" in body

    def test_overlength_400(self):
        assert sc._h_fold_rna(None, {"sequence": "A" * 700})[1] == 400

    def test_registered_read_only(self):
        assert "fold-rna" in sc._AGENT_HANDLERS
        _fn, write = sc._AGENT_HANDLERS["fold-rna"]
        assert write is False


class TestCofoldRnaHandler:
    """`cofold-rna` — bound-state heterodimer ΔG (anti-SD : mRNA hybrid)."""

    def test_antisd_duplex(self):
        r = sc._h_cofold_rna(None, {"seq_a": "UAAGGAGGU", "seq_b": "ACCUCCUUA"})
        assert r["ok"] is True and r["dg"] < -10.0

    def test_dna_t_accepted(self):
        assert sc._h_cofold_rna(None, {"seq_a": "GGGGTTTT", "seq_b": "AAAACCCC"})["ok"]

    def test_missing_400(self):
        assert sc._h_cofold_rna(None, {"seq_a": "ACGU"})[1] == 400

    def test_ambiguous_400(self):
        assert sc._h_cofold_rna(None, {"seq_a": "ACGUN", "seq_b": "ACGU"})[1] == 400


class TestRbsStrengthHandler:
    """`rbs-strength` — relative translation-initiation strength."""

    def test_strong_rbs(self):
        r = sc._h_rbs_strength(
            None, {"mrna": "AAUAAAAGGAGGAAUAAAUGAGCAAAGCAACU", "start": 17})
        assert r["ok"] is True and r["rel_strength"] > 1 and "spacing" in r

    def test_missing_mrna_400(self):
        assert sc._h_rbs_strength(None, {"start": 5})[1] == 400

    def test_missing_start_400(self):
        assert sc._h_rbs_strength(None, {"mrna": "AUGAAAAAA"})[1] == 400

    def test_bad_start_400(self):
        assert sc._h_rbs_strength(None, {"mrna": "AUGAAA", "start": 99})[1] == 400

    def test_bool_start_400(self):
        # JSON true must not silently coerce to start=1 (audit #5e)
        assert sc._h_rbs_strength(None, {"mrna": "AUGAAAAAA", "start": True})[1] == 400

    def test_result_is_json_serializable(self):
        # start too close to the 5' end used to return dg_total=Infinity,
        # which is invalid JSON shipped with HTTP 200 (audit #1)
        import json as _json
        r = sc._h_rbs_strength(None, {"mrna": "AAAAAAAUGAAA", "start": 5})
        assert r["ok"] is True and r["dg_total"] is None
        body = _json.dumps(r)
        assert "Infinity" not in body and "NaN" not in body


class TestDesignRbsHandler:
    """`design-rbs` — reverse-design a 5'UTR for a target strength."""

    def test_designs_to_target(self):
        r = sc._h_design_rbs(None, {"cds": "AUGAGCAAAUACUAA", "target": 5.0})
        assert r["ok"] is True and r["full"].endswith("AUGAGCAAAUACUAA")
        assert "on_target" in r and isinstance(r["spacing"], int)

    def test_missing_cds_400(self):
        assert sc._h_design_rbs(None, {"target": 5})[1] == 400

    def test_bad_target_400(self):
        assert sc._h_design_rbs(None, {"cds": "AUGAAAUAA", "target": -1})[1] == 400
        assert sc._h_design_rbs(None, {"cds": "AUGAAAUAA", "target": "x"})[1] == 400

    def test_endpoints_registered_read_only(self):
        for name in ("cofold-rna", "rbs-strength", "design-rbs"):
            assert name in sc._AGENT_HANDLERS
            assert sc._AGENT_HANDLERS[name][1] is False


class TestAssembleOperonHandler:
    """`assemble-operon` — context-aware operon assembly from CDSs."""

    GENES = [{"cds": "AUGAGCAAAUACUAA", "target_strength": 5.0, "name": "A"},
             {"cds": "AUGGCAGAAUGGUAA", "target": 2.0, "name": "B"}]

    def test_assembles(self):
        r = sc._h_assemble_operon(None, {"genes": self.GENES,
                                         "promoter": "TTGACA",
                                         "terminator": "TTTT"})
        assert r["ok"] is True
        assert "U" not in r["sequence"]
        assert [el["kind"] for el in r["layout"]] == \
            ["promoter", "rbs", "cds", "rbs", "cds", "terminator"]
        assert len(r["genes"]) == 2 and "on_target" in r["genes"][0]

    def test_missing_genes_400(self):
        assert sc._h_assemble_operon(None, {})[1] == 400
        assert sc._h_assemble_operon(None, {"genes": []})[1] == 400

    def test_bad_gene_400(self):
        bad = {"genes": [{"cds": "AU", "target": 5}]}
        assert sc._h_assemble_operon(None, bad)[1] == 400

    def test_nonfinite_target_400(self):
        # NaN / Infinity target used to be echoed -> invalid JSON @ 200 (audit #2)
        for bad in (float("inf"), float("nan")):
            r = sc._h_assemble_operon(
                None, {"genes": [{"cds": "AUGAAAUACUAA", "target_strength": bad}]})
            assert r[1] == 400

    def test_registered_read_only(self):
        assert "assemble-operon" in sc._AGENT_HANDLERS
        assert sc._AGENT_HANDLERS["assemble-operon"][1] is False


class TestExportGffHandler:
    """`_h_export_gff` writes the loaded record to disk as GFF3."""

    def test_no_record_returns_422(self):
        app = MockApp(record=None)
        result = sc._h_export_gff(app, {"path": "/tmp/x.gff3"})
        assert result == ({"error": "no plasmid loaded"}, 422)

    def test_missing_path_returns_400(self, tiny_record):
        app = MockApp(record=tiny_record)
        result = sc._h_export_gff(app, {})
        assert isinstance(result, tuple) and result[1] == 400

    def test_writes_file(self, tiny_record, tmp_path):
        app = MockApp(record=tiny_record)
        out = tmp_path / "tiny.gff3"
        result = sc._h_export_gff(app, {"path": str(out)})
        assert isinstance(result, dict)
        assert result["ok"] is True
        assert out.exists()
        assert out.read_text().startswith("##gff-version 3")


class TestTransferAnnotationsHandler:
    """`_h_transfer_annotations` walks a source library entry's
    features and matches them onto the loaded record by sequence
    identity. Defaults to dry-run so an agent can inspect the
    proposed transfers before committing."""

    def test_no_record_loaded_returns_422(self):
        app = MockApp(record=None)
        result = sc._h_transfer_annotations(app, {"source_id": "x"})
        assert result == ({"error": "no plasmid loaded"}, 422)

    def test_missing_source_id_returns_400(self, tiny_record):
        app = MockApp(record=tiny_record)
        result = sc._h_transfer_annotations(app, {})
        assert isinstance(result, tuple) and result[1] == 400

    def test_source_not_in_library_returns_404(self, tiny_record,
                                                  isolated_library):
        sc._save_library([])
        app = MockApp(record=tiny_record)
        result = sc._h_transfer_annotations(
            app, {"source_id": "ghost"},
        )
        assert isinstance(result, tuple) and result[1] == 404

    def test_dry_run_returns_transfers_without_applying(
        self, tiny_record, isolated_library
    ):
        # Source library entry mirrors the loaded record so every
        # feature finds itself.
        sc._save_library([{
            "id":      "src",
            "name":    "src",
            "size":    len(tiny_record.seq),
            "n_feats": len(tiny_record.features),
            "added":   "2026-05-06",
            "gb_text": sc._record_to_gb_text(tiny_record),
        }])
        app = MockApp(record=tiny_record)
        before = len(tiny_record.features)
        result = sc._h_transfer_annotations(
            app, {"source_id": "src", "dry_run": True},
        )
        assert isinstance(result, dict)
        assert result["applied"] is False
        # Transfer count: only the >= min_len features. tiny_record
        # has a 27-bp CDS and a 30-bp misc_feature; min_len defaults
        # to 30 so only the misc_feature qualifies. Whatever the
        # exact count, the record itself must NOT have been mutated.
        assert len(tiny_record.features) == before


class TestDiffPlasmidHandler:
    """`_h_diff_plasmid` runs `_pairwise_align` between the loaded record
    and a target library entry. Mirrors the GUI diff flow for agent
    consumption — the result dict is the same shape `AlignmentScreen`
    consumes."""

    def test_no_record_loaded_returns_422(self):
        app = MockApp(record=None)
        result = sc._h_diff_plasmid(app, {"target_id": "x"})
        assert result == ({"error": "no plasmid loaded"}, 422)

    def test_missing_target_id_returns_400(self, tiny_record):
        app = MockApp(record=tiny_record)
        result = sc._h_diff_plasmid(app, {})
        assert isinstance(result, tuple) and result[1] == 400

    def test_invalid_mode_rejected(self, tiny_record):
        app = MockApp(record=tiny_record)
        result = sc._h_diff_plasmid(app, {"target_id": "x", "mode": "wat"})
        assert isinstance(result, tuple) and result[1] == 400

    def test_target_not_in_library_returns_404(self, tiny_record,
                                                  isolated_library):
        sc._save_library([])
        app = MockApp(record=tiny_record)
        result = sc._h_diff_plasmid(app, {"target_id": "ghost"})
        assert isinstance(result, tuple) and result[1] == 404

    def test_successful_diff_returns_alignment(self, tiny_record,
                                                  isolated_library):
        # Load a target into the library; diff against current record.
        sc._save_library([{
            "id":      "tgt",
            "name":    "tgt",
            "size":    len(tiny_record.seq),
            "n_feats": 0,
            "added":   "2026-05-06",
            "gb_text": sc._record_to_gb_text(tiny_record),
        }])
        app = MockApp(record=tiny_record)
        result = sc._h_diff_plasmid(app, {"target_id": "tgt"})
        assert isinstance(result, dict)
        assert result["ok"] is True
        assert result["target_id"] == "tgt"
        # Self-vs-self: 100% identity.
        r = result["result"]
        assert r["identity_pct"] == 100.0
        assert r["n_mismatches"] == 0

    def test_circular_rotation_auto_detected_for_circular_target(
            self, tiny_record, isolated_library,
    ):
        """When the target's topology annotation is `circular`, the
        endpoint runs the seed-kmer rotation probe and reports the
        offset alongside the alignment result. Regression for
        2026-05-14 audit finding."""
        # `tiny_record` is annotated `topology=circular` per conftest.
        sc._save_library([{
            "id":      "tgt",
            "name":    "tgt",
            "size":    len(tiny_record.seq),
            "n_feats": 0,
            "added":   "2026-05-06",
            "gb_text": sc._record_to_gb_text(tiny_record),
        }])
        app = MockApp(record=tiny_record)
        result = sc._h_diff_plasmid(app, {"target_id": "tgt"})
        assert result["ok"] is True
        assert result["circular"] is True
        # Self-vs-self at the same origin: no rotation needed.
        assert result["rotation_offset"] == 0

    def test_circular_rotation_can_be_forced(self, tiny_record,
                                                isolated_library):
        """A linear target with `circular: true` in the payload runs
        the rotation probe regardless of annotation."""
        from Bio.SeqRecord import SeqRecord
        # Re-stamp as linear so auto-detect would skip rotation.
        linear_rec = SeqRecord(
            tiny_record.seq, id=tiny_record.id, name=tiny_record.name,
            features=list(tiny_record.features),
            annotations={"molecule_type": "DNA", "topology": "linear"},
        )
        sc._save_library([{
            "id":      "tgt",
            "name":    "tgt",
            "size":    len(linear_rec.seq),
            "n_feats": 0,
            "added":   "2026-05-06",
            "gb_text": sc._record_to_gb_text(linear_rec),
        }])
        app = MockApp(record=tiny_record)
        result = sc._h_diff_plasmid(
            app, {"target_id": "tgt", "circular": True},
        )
        assert result["ok"] is True
        assert result["circular"] is True

    def test_circular_rotation_can_be_disabled(self, tiny_record,
                                                isolated_library):
        """`circular: false` skips the rotation even for circular
        targets — preserves the pre-0.8.4 behaviour when callers want
        it."""
        sc._save_library([{
            "id":      "tgt",
            "name":    "tgt",
            "size":    len(tiny_record.seq),
            "n_feats": 0,
            "added":   "2026-05-06",
            "gb_text": sc._record_to_gb_text(tiny_record),
        }])
        app = MockApp(record=tiny_record)
        result = sc._h_diff_plasmid(
            app, {"target_id": "tgt", "circular": False},
        )
        assert result["ok"] is True
        assert result["circular"] is False
        assert result["rotation_offset"] == 0


class TestPlasmidsaurusEndpoints:
    """Plasmidsaurus zip alignment endpoints — list-plasmidsaurus-members
    + align-plasmidsaurus-zip.

    Both endpoints take a real path on disk; the tests synthesize a
    minimal zip with one `.gbk` member so the parse + alignment
    pipeline can exercise them without a network round-trip.
    """

    def _make_zip(self, tmp_path, record, member_name: str = "run.gbk"):
        """Build a single-member `.zip` containing the given record as
        GenBank text. Returns the path."""
        import zipfile
        zip_path = tmp_path / "plasmidsaurus.zip"
        gb_text = sc._record_to_gb_text(record)
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr(member_name, gb_text)
        return zip_path

    def test_list_members_returns_gbk_files(self, tiny_record, tmp_path):
        zip_path = self._make_zip(tmp_path, tiny_record)
        result = sc._h_list_plasmidsaurus_members(
            MockApp(), {"path": str(zip_path)},
        )
        assert isinstance(result, dict)
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["members"][0]["name"] == "run.gbk"
        assert result["members"][0]["size"] > 0

    def test_list_members_missing_path_returns_400(self):
        result = sc._h_list_plasmidsaurus_members(MockApp(), {})
        assert isinstance(result, tuple)
        assert result[1] == 400

    def test_list_members_nonexistent_path_returns_400(self, tmp_path):
        # Sweep #25 (2026-05-23): collapsed 422 → uniform 400 (FS
        # oracle reduction — see `_h_list_plasmidsaurus_members`).
        result = sc._h_list_plasmidsaurus_members(
            MockApp(), {"path": str(tmp_path / "does-not-exist.zip")},
        )
        assert isinstance(result, tuple)
        assert result[1] == 400

    def test_list_members_non_zip_rejected(self, tmp_path):
        # Sweep #25: collapsed 422 → 400.
        bogus = tmp_path / "not-a-zip.zip"
        bogus.write_text("hello world")
        result = sc._h_list_plasmidsaurus_members(
            MockApp(), {"path": str(bogus)},
        )
        assert isinstance(result, tuple)
        assert result[1] == 400

    def test_list_members_filters_non_gbk(self, tiny_record, tmp_path):
        """Members with non-`.gbk`/`.gb`/`.genbank` extensions are
        skipped so the agent gets the same picker view the UI uses."""
        import zipfile
        zip_path = tmp_path / "mixed.zip"
        gb_text = sc._record_to_gb_text(tiny_record)
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("run.gbk",     gb_text)
            zf.writestr("readme.txt",  "ignore me")
            zf.writestr("data.csv",    "a,b,c")
        result = sc._h_list_plasmidsaurus_members(
            MockApp(), {"path": str(zip_path)},
        )
        assert result["ok"] is True
        assert {m["name"] for m in result["members"]} == {"run.gbk"}

    def test_align_self_vs_self_100pct(self, tiny_record, tmp_path,
                                          isolated_library):
        zip_path = self._make_zip(tmp_path, tiny_record)
        sc._save_library([{
            "id":      "tgt",
            "name":    "tgt",
            "size":    len(tiny_record.seq),
            "n_feats": 0,
            "added":   "2026-05-06",
            "gb_text": sc._record_to_gb_text(tiny_record),
        }])
        result = sc._h_align_plasmidsaurus_zip(
            MockApp(),
            {
                "path":      str(zip_path),
                "member":    "run.gbk",
                "target_id": "tgt",
            },
        )
        assert isinstance(result, dict)
        assert result["ok"] is True
        assert result["target_id"] == "tgt"
        # Self-vs-self: 100% identity, no rotation needed.
        assert result["result"]["identity_pct"] == 100.0
        assert result["rotation_offset"] == 0
        # `tiny_record` is circular so the endpoint auto-detected it.
        assert result["circular"] is True

    def test_align_resolves_target_by_name(self, tiny_record, tmp_path,
                                             isolated_library):
        """`target_name` is a fallback when the agent doesn't know
        the id. Mirrors `_h_delete_from_library`'s name-based lookup
        contract — the library-entry's display name is the lookup
        key, while the returned `target_name` is the parsed LOCUS
        name from the gb_text (matches `_h_diff_plasmid`)."""
        zip_path = self._make_zip(tmp_path, tiny_record)
        sc._save_library([{
            "id":      "tgt",
            "name":    "Looked Up By Name",
            "size":    len(tiny_record.seq),
            "n_feats": 0,
            "added":   "2026-05-06",
            "gb_text": sc._record_to_gb_text(tiny_record),
        }])
        result = sc._h_align_plasmidsaurus_zip(
            MockApp(),
            {
                "path":        str(zip_path),
                "member":      "run.gbk",
                "target_name": "Looked Up By Name",
            },
        )
        assert result["ok"] is True
        # The lookup matched by display name; returned `target_id` is
        # the library entry's id, `target_name` is the parsed LOCUS
        # name from the gb_text (TEST001 here per `tiny_record`).
        assert result["target_id"] == "tgt"
        assert result["target_name"] == "TEST001"

    def test_align_missing_target_returns_404(self, tiny_record,
                                                 tmp_path,
                                                 isolated_library):
        zip_path = self._make_zip(tmp_path, tiny_record)
        sc._save_library([])
        result = sc._h_align_plasmidsaurus_zip(
            MockApp(),
            {
                "path":      str(zip_path),
                "member":    "run.gbk",
                "target_id": "ghost",
            },
        )
        assert isinstance(result, tuple)
        assert result[1] == 404

    def test_align_missing_member_returns_400(self, tiny_record,
                                                 tmp_path):
        zip_path = self._make_zip(tmp_path, tiny_record)
        result = sc._h_align_plasmidsaurus_zip(
            MockApp(),
            {"path": str(zip_path), "target_id": "x"},
        )
        assert isinstance(result, tuple)
        assert result[1] == 400

    def test_align_unknown_zip_member_returns_422(self, tiny_record,
                                                     tmp_path,
                                                     isolated_library):
        zip_path = self._make_zip(tmp_path, tiny_record)
        sc._save_library([{
            "id":      "tgt",
            "name":    "tgt",
            "size":    len(tiny_record.seq),
            "n_feats": 0,
            "added":   "2026-05-06",
            "gb_text": sc._record_to_gb_text(tiny_record),
        }])
        result = sc._h_align_plasmidsaurus_zip(
            MockApp(),
            {
                "path":      str(zip_path),
                "member":    "not-in-zip.gbk",
                "target_id": "tgt",
            },
        )
        assert isinstance(result, tuple)
        assert result[1] == 422

    def test_align_invalid_mode_rejected(self, tiny_record, tmp_path):
        zip_path = self._make_zip(tmp_path, tiny_record)
        result = sc._h_align_plasmidsaurus_zip(
            MockApp(),
            {
                "path":      str(zip_path),
                "member":    "run.gbk",
                "target_id": "tgt",
                "mode":      "fast",
            },
        )
        assert isinstance(result, tuple)
        assert result[1] == 400


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

    def test_empty_seq_returns_empty_orf_list(self):
        """Regression guard for 2026-05-06: an empty `rec.seq` used to
        traverse `(rec.annotations or {})` — fine — but
        `_find_orfs(seq="")` itself returned `[]`; the agent path
        wrapper is now explicit about the shortcut so a missing /
        empty annotations dict can't surprise."""
        from Bio.SeqRecord import SeqRecord
        from Bio.Seq import Seq
        rec = SeqRecord(Seq(""), id="empty", name="empty")
        # Force a missing annotations attr to mimic a freshly-built
        # record from a partial Biopython parse.
        rec.annotations = None
        app = MockApp(record=rec)
        result = sc._h_find_orfs(app, {})
        assert result == {"orfs": [], "count": 0}


class TestLoadFileSizeCap:
    """Regression guard for 2026-05-06 fix: `_h_load_file` previously
    had NO size cap on disk reads — a malicious or buggy agent script
    could load a 10 GB GenBank file and OOM the worker. Cap is now
    `_BULK_IMPORT_MAX_BYTES` (50 MB) with `force=true` override."""

    def test_oversized_file_rejected_with_400(self, tmp_path, monkeypatch):
        # Sweep #25 (2026-05-23): size-cap response collapsed
        # 413 → uniform 400 (FS oracle reduction — error body no
        # longer carries `size_bytes` / `cap_bytes`; details in logs).
        monkeypatch.setattr(sc, "_BULK_IMPORT_MAX_BYTES", 10)
        big = tmp_path / "huge.gb"
        big.write_bytes(b"X" * 100)
        app = MockApp()
        result = sc._h_load_file(app, {"path": str(big)})
        payload, status = result
        assert status == 400
        assert "log" in payload["error"].lower()

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
        # Sweep #25 (2026-05-23): size-cap response collapsed 413 → 400
        # (FS oracle reduction). Without force still rejected; with
        # force still parses.
        result = sc._h_load_file(app, {"path": str(gb)})
        assert isinstance(result, tuple) and result[1] == 400
        # With force: parsed.
        result = sc._h_load_file(app, {"path": str(gb), "force": True})
        assert isinstance(result, dict) and result["ok"] is True

    def test_missing_path_returns_400(self):
        result = sc._h_load_file(MockApp(), {})
        assert result[1] == 400 and "missing" in result[0]["error"]

    def test_nonexistent_path_returns_400(self, tmp_path):
        # Sweep #25: collapsed 404 → 400 (FS oracle reduction).
        result = sc._h_load_file(MockApp(),
                                  {"path": str(tmp_path / "nope.gb")})
        assert result[1] == 400


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

    def test_read_endpoint_requires_token(self, http_server):
        """Sweep #25 (2026-05-23): bearer token required on ALL
        endpoints, not just writers. The earlier "reads can't damage
        state" assumption ignored that several read endpoints
        (hmmscan, blast, list-library) leak filesystem state or
        consume CPU/RAM — concrete attack surface for any co-
        resident local process. `tools` stays unauthenticated as the
        self-describe entry point."""
        base, _token, _app = http_server
        # Without token: 401.
        status, _payload = _http(f"{base}/status", token=None)
        assert status == 401
        # `tools` stays open so clients can self-describe.
        status, _payload = _http(f"{base}/tools", token=None)
        assert status == 200
        # With token: 200.
        status, _payload = _http(f"{base}/status", token=_token)
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

    def test_deeply_nested_json_body_400(self, http_server):
        """A deeply-nested JSON body makes `json.loads` raise RecursionError
        (a RuntimeError, NOT a JSONDecodeError). `_read_body` must catch it and
        the dispatcher must return a clean 400 — pre-fix it escaped the handler
        and dropped the connection with a worker-thread traceback. The body
        stays under `_MAX_BODY_BYTES` so the server reads it fully (no half-open
        race) and the failure is purely in the parse step."""
        base, token, _app = http_server
        depth = 50_000
        data = (b"[" * depth) + (b"]" * depth)  # ~100 KB, well under 1 MiB
        req = urllib.request.Request(
            f"{base}/add-feature", method="POST", data=data,
        )
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
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
        monkeypatch.setattr(sc._state, "_AGENT_TOKEN_FILE", token_path)
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

    def test_hmmscan_400_on_missing_path(self, http_server):
        # Sweep #11 (2026-05-20): hmmscan no longer surfaces a 404
        # distinct from 400 — that error differential was a
        # filesystem-state oracle for unauthenticated local
        # processes. All file-not-acceptable responses (not found,
        # symlink, not a regular file, oversize) collapse to a
        # single generic 400 with detail logged for the user.
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/hmmscan", method="POST",
            body={"query": "MAEELFKWILR" * 5,
                  "hmm_path": "/no/such/file.hmm"},
            token=token,
        )
        assert status == 400
        assert "not acceptable" in (payload.get("error") or "").lower()

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

    def test_list_restriction_sites_rejects_non_string_enzymes(
            self, http_server, tiny_record):
        """Regression guard for 2026-05-17 audit fix: every element of
        `enzymes` must be a string. Pre-fix a mixed-type list like
        ``[1, 2.5, null]`` built a set whose ``not in`` check silently
        filtered every hit to zero — agents got an empty result with
        no signal that their payload was malformed."""
        base, token, _ = http_server
        body_json = '{"enzymes": [1, 2.5, null]}'
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

    def test_list_restriction_sites_accepts_all_string_enzymes(
            self, http_server, tiny_record):
        """Positive case for the 2026-05-17 type check: a well-formed
        all-string enzymes list must NOT 400."""
        base, token, _ = http_server
        body_json = '{"enzymes": ["EcoRI", "BamHI"]}'
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
        assert status == 200


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
        # 200 (saved), 422 (no record), or 409 (the homeless-new-record
        # create gate, snag #16) are all valid non-crash responses; what
        # we care about is that the handler didn't AttributeError on a
        # None body.
        assert status in (200, 422, 409)

    def test_handle_rejects_non_dict_json(self, http_server):
        # Sweep #25 (2026-05-23): non-dict / malformed JSON body now
        # 400s explicitly (was: silently normalised to {} which
        # masked caller serialisation bugs). The handler still can't
        # 500 from a `.get()` on a list — the dispatcher catches the
        # bad shape before reaching the handler.
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
        # Either accepted (200) or rejected (400/422) — never a 500.
        assert status in (200, 400, 422)


# ── Plasmid status endpoints (added 2026-05-05 for v1.0) ──────────────────────


class TestPlasmidStatusEndpoints:
    def test_list_plasmid_statuses(self, http_server):
        base, token, _ = http_server
        status, payload = _http(f"{base}/list-plasmid-statuses", token=token)
        assert status == 200
        assert payload["ok"] is True
        # Strict canonical vocabulary — DESIGNING / CLONING /
        # SEQUENCING / VERIFIED / ERROR (the last added in v0.9.24
        # for failed-clone tracking, INV-76).
        assert set(payload["statuses"]) == {
            "DESIGNING", "CLONING", "SEQUENCING", "VERIFIED", "ERROR"
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
    chew on without fixture sprawl. Column widths match SeqIO's own
    LOCUS-line formatter so Biopython parses it without warning."""
    return ("LOCUS       test                      10 bp    DNA     "
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
                          "active_grammar"):
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

    def test_set_setting_unknown_key_after_linear_layout_removed(
            self, http_server):
        # `linear_layout` was removed from the allowlist 2026-05-08
        # — flag is the only linear layout. Setting it through the
        # agent now returns an "unknown key" error, NOT the choice-
        # validator's "must be one of …" error.
        base, token, _ = http_server
        status, payload = _http(
            f"{base}/set-setting", method="POST",
            body={"key": "linear_layout", "value": "flag"}, token=token,
        )
        assert status == 400
        assert "unknown" in payload["error"].lower()

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


# ── Simulator agent endpoints (2026-05-17 release) ────────────────────────────
#
# `simulate-pcr` and `simulate-gel` are pure read-only wrappers around
# the SimulatorScreen's underlying functions. Tested at the handler
# layer (no HTTP round-trip) — input validation is the bulk of the
# surface; the underlying physics is covered by `tests/test_simulator.py`.


class TestSimulatePcrHandler:

    _SEQ = ("ATGCGATCGATCGATCGCGT"   # fwd binding site 0..20
            + "A" * 60
            + "GCATCGTAGCTAGCTGATCG") # rev-rc binding site 80..100
    _FWD = "ATGCGATCGATCGATCGCGT"
    _REV = "CGATCAGCTAGCTACGATGC"     # = rc("GCATCGTAGCTAGCTGATCG")

    def test_happy_path_linear(self):
        resp = sc._h_simulate_pcr(None, {
            "template_seq": self._SEQ,
            "fwd_primer":   self._FWD,
            "rev_primer":   self._REV,
            "circular":     False,
        })
        assert isinstance(resp, dict)
        assert resp["ok"] is True
        assert resp["n"] == 1
        assert resp["capped"] is False
        assert resp["amplicons"][0]["length"] == 100
        assert resp["amplicons"][0]["wraps"] is False

    def test_circular_wrap_amplicon(self):
        # Place fwd-binding-site near end, rev-binding-site near start;
        # amplicon must cross the origin.
        # seq pos 20..40: ATGCGATCGATCGATCGCGT  (the rev-binding target)
        # seq pos 50..70: GCATCGTAGCTAGCTGATCG  (the fwd-binding site)
        seq = ("A" * 20 + self._FWD + "A" * 10
                + "GCATCGTAGCTAGCTGATCG" + "A" * 30)
        resp = sc._h_simulate_pcr(None, {
            "template_seq": seq,
            "fwd_primer":   "GCATCGTAGCTAGCTGATCG",
            "rev_primer":   sc._rc(self._FWD),
            "circular":     True,
            "max_amplicon": 200,
        })
        assert isinstance(resp, dict) and resp["ok"] is True
        wrap_amps = [a for a in resp["amplicons"] if a["wraps"]]
        assert wrap_amps, "expected a wrapping amplicon"

    def test_no_match_returns_empty(self):
        resp = sc._h_simulate_pcr(None, {
            "template_seq": "ATGC" * 100,
            "fwd_primer":   "AAAAAAAAAAAAAAAA",
            "rev_primer":   "TTTTTTTTTTTTTTTT",
        })
        assert resp["ok"] is True
        assert resp["n"] == 0
        assert resp["amplicons"] == []

    def test_missing_template_seq_returns_400(self):
        payload, status = sc._h_simulate_pcr(None, {
            "fwd_primer": self._FWD, "rev_primer": self._REV,
        })
        assert status == 400
        assert "template_seq" in payload["error"]

    def test_non_string_template_returns_400(self):
        payload, status = sc._h_simulate_pcr(None, {
            "template_seq": 123,
            "fwd_primer":   self._FWD,
            "rev_primer":   self._REV,
        })
        assert status == 400

    def test_template_over_cap_returns_413(self):
        payload, status = sc._h_simulate_pcr(None, {
            "template_seq": "A" * (sc._PCR_MAX_TEMPLATE_BP + 1),
            "fwd_primer":   self._FWD,
            "rev_primer":   self._REV,
        })
        assert status == 413
        assert "template_seq" in payload["error"]

    def test_missing_primer_returns_400(self):
        payload, status = sc._h_simulate_pcr(None, {
            "template_seq": self._SEQ,
            "fwd_primer":   self._FWD,
        })
        assert status == 400

    def test_short_primer_returns_400(self):
        payload, status = sc._h_simulate_pcr(None, {
            "template_seq": self._SEQ,
            "fwd_primer":   "ATGCG",
            "rev_primer":   self._REV,
        })
        assert status == 400
        assert "at least" in payload["error"]

    def test_long_primer_returns_400(self):
        payload, status = sc._h_simulate_pcr(None, {
            "template_seq": self._SEQ,
            "fwd_primer":   "A" * (sc._PCR_MAX_PRIMER_LEN + 1),
            "rev_primer":   self._REV,
        })
        assert status == 400
        assert "at most" in payload["error"]

    def test_non_acgt_primer_returns_400(self):
        for bad in ("NNNNNNNNNNNNNNN", "ATGCGATCGAT-GATCG", "atgcga"):
            payload, status = sc._h_simulate_pcr(None, {
                "template_seq": self._SEQ,
                "fwd_primer":   bad if not bad.islower() else bad.upper() + "X",
                "rev_primer":   self._REV,
            })
            assert status == 400, (bad, payload)

    def test_max_amplicon_out_of_range_returns_400(self):
        for bad in (0, -1, sc._PCR_AMPLICON_HARD_CAP + 1):
            payload, status = sc._h_simulate_pcr(None, {
                "template_seq": self._SEQ,
                "fwd_primer":   self._FWD,
                "rev_primer":   self._REV,
                "max_amplicon": bad,
            })
            assert status == 400, (bad, payload)

    def test_max_amplicon_non_int_returns_400(self):
        payload, status = sc._h_simulate_pcr(None, {
            "template_seq": self._SEQ,
            "fwd_primer":   self._FWD,
            "rev_primer":   self._REV,
            "max_amplicon": "not-an-int",
        })
        assert status == 400

    def test_empty_primer_strings_return_400(self):
        for fwd, rev in [("", self._REV), (self._FWD, ""), ("   ", "  ")]:
            payload, status = sc._h_simulate_pcr(None, {
                "template_seq": self._SEQ,
                "fwd_primer":   fwd,
                "rev_primer":   rev,
            })
            assert status == 400, (fwd, rev, payload)


class TestSimulateGelHandler:

    def test_happy_path_ladder(self):
        resp = sc._h_simulate_gel(None, {
            "lanes": [{"source": "ladder", "detail": "1 kb"}],
            "agarose_pct": 1.0,
        })
        assert isinstance(resp, dict)
        assert resp["ok"] is True
        assert len(resp["lanes"]) == 1
        assert len(resp["lanes"][0]["bands"]) > 0
        # Every band has bp + form + mobility + row.
        for b in resp["lanes"][0]["bands"]:
            assert {"bp", "form", "mobility", "row"} <= set(b.keys())
            assert 0.0 <= b["mobility"] <= 1.0
            assert 0 <= b["row"] < resp["height"]

    def test_plasmid_lane_with_circular_template(self):
        resp = sc._h_simulate_gel(None, {
            "lanes": [{"source": "plasmid", "detail": ""}],
            "template_seq": "AT" * 1500,
            "template_circular": True,
            "agarose_pct": 1.0,
        })
        assert resp["ok"] is True
        # Circular uncut → SC + nicked = 2 bands.
        assert len(resp["lanes"][0]["bands"]) == 2
        forms = {b["form"] for b in resp["lanes"][0]["bands"]}
        assert "supercoiled" in forms
        assert "nicked" in forms

    def test_digest_lane(self):
        resp = sc._h_simulate_gel(None, {
            "lanes": [{"source": "digest", "detail": "EcoRI"}],
            "template_seq": "GAATTC" + "A" * 100 + "GAATTC" + "A" * 50,
            "template_circular": True,
            "agarose_pct": 1.0,
        })
        assert resp["ok"] is True
        # Two EcoRI sites on a circular template → 2 fragments.
        assert len(resp["lanes"][0]["bands"]) >= 2

    def test_pcr_lane_with_amplicon(self):
        resp = sc._h_simulate_gel(None, {
            "lanes": [{"source": "pcr", "detail": ""}],
            "pcr_amplicon": {"length": 800, "wraps": False,
                              "amplicon_seq": "A" * 800,
                              "start": 0, "end": 800,
                              "fwd_seq": "A" * 20, "rev_seq": "T" * 20,
                              "gc_pct": 0.0, "fwd_tm": None,
                              "rev_tm": None},
        })
        assert resp["ok"] is True
        assert len(resp["lanes"][0]["bands"]) == 1
        assert resp["lanes"][0]["bands"][0]["bp"] == 800

    def test_include_image_returns_text(self):
        resp = sc._h_simulate_gel(None, {
            "lanes": [{"source": "ladder", "detail": "1 kb"}],
            "include_image": True,
            "height": 10, "lane_width": 5,
        })
        assert resp["ok"] is True
        assert "image" in resp
        assert isinstance(resp["image"], str)
        assert "\n" in resp["image"]   # multi-row rendering

    def test_missing_lanes_returns_400(self):
        payload, status = sc._h_simulate_gel(None, {})
        assert status == 400

    def test_empty_lanes_returns_400(self):
        payload, status = sc._h_simulate_gel(None, {"lanes": []})
        assert status == 400

    def test_lanes_not_list_returns_400(self):
        payload, status = sc._h_simulate_gel(None, {
            "lanes": {"source": "ladder"},   # dict, not list
        })
        assert status == 400

    def test_too_many_lanes_returns_400(self):
        lanes = [{"source": "empty"}] * (sc._GEL_MAX_LANES + 1)
        payload, status = sc._h_simulate_gel(None, {"lanes": lanes})
        assert status == 400

    def test_lane_missing_source_returns_400(self):
        payload, status = sc._h_simulate_gel(None, {
            "lanes": [{"name": "no-source"}],
        })
        assert status == 400
        assert "source" in payload["error"]

    def test_lane_unknown_source_returns_400(self):
        payload, status = sc._h_simulate_gel(None, {
            "lanes": [{"source": "gibberish"}],
        })
        assert status == 400

    def test_lane_non_dict_returns_400(self):
        payload, status = sc._h_simulate_gel(None, {
            "lanes": ["not-a-dict"],
        })
        assert status == 400

    def test_lane_detail_too_long_returns_400(self):
        payload, status = sc._h_simulate_gel(None, {
            "lanes": [{"source": "digest", "detail": "X" * 300}],
        })
        assert status == 400

    def test_lane_detail_wrong_type_returns_400(self):
        payload, status = sc._h_simulate_gel(None, {
            "lanes": [{"source": "ladder", "detail": 42}],
        })
        assert status == 400

    def test_agarose_out_of_range_returns_400(self):
        for bad in (0, 0.05, 11.0, -1.0):
            payload, status = sc._h_simulate_gel(None, {
                "lanes": [{"source": "ladder", "detail": "1 kb"}],
                "agarose_pct": bad,
            })
            assert status == 400, (bad, payload)

    def test_agarose_non_numeric_returns_400(self):
        payload, status = sc._h_simulate_gel(None, {
            "lanes": [{"source": "ladder", "detail": "1 kb"}],
            "agarose_pct": "high",
        })
        assert status == 400

    def test_height_out_of_range_returns_400(self):
        for bad in (sc._GEL_HEIGHT_MIN - 1, sc._GEL_HEIGHT_MAX + 1, 0, -10):
            payload, status = sc._h_simulate_gel(None, {
                "lanes": [{"source": "ladder", "detail": "1 kb"}],
                "height": bad,
            })
            assert status == 400, (bad, payload)

    def test_lane_width_out_of_range_returns_400(self):
        payload, status = sc._h_simulate_gel(None, {
            "lanes": [{"source": "ladder", "detail": "1 kb"}],
            "lane_width": sc._GEL_LANE_WIDTH_MAX + 1,
        })
        assert status == 400

    def test_template_over_cap_returns_413(self):
        payload, status = sc._h_simulate_gel(None, {
            "lanes": [{"source": "plasmid", "detail": ""}],
            "template_seq": "A" * (sc._PCR_MAX_TEMPLATE_BP + 1),
        })
        assert status == 413

    def test_template_wrong_type_returns_400(self):
        payload, status = sc._h_simulate_gel(None, {
            "lanes": [{"source": "plasmid"}],
            "template_seq": 12345,
        })
        assert status == 400

    def test_pcr_amplicon_wrong_type_returns_400(self):
        payload, status = sc._h_simulate_gel(None, {
            "lanes": [{"source": "pcr"}],
            "pcr_amplicon": "not-a-dict",
        })
        assert status == 400

    def test_pcr_lane_without_amplicon_returns_empty_bands(self):
        # Not a validation error — gel renders a lane with no bands
        # and the user sees an empty column. Mirrors UI behaviour.
        resp = sc._h_simulate_gel(None, {
            "lanes": [{"source": "pcr"}],
        })
        assert resp["ok"] is True
        assert resp["lanes"][0]["bands"] == []


class TestSimulatorAgentRegistration:
    """Both new endpoints must be registered as READ-ONLY (write=False)
    so an unauthenticated caller can run simulations without a token —
    matches `simulate-gibson` semantics."""

    def test_simulate_pcr_registered_read_only(self):
        eps = {ep["name"]: ep for ep in sc._h_tools(None, {})["endpoints"]}
        assert "simulate-pcr" in eps
        assert eps["simulate-pcr"]["write"] is False

    def test_simulate_gel_registered_read_only(self):
        eps = {ep["name"]: ep for ep in sc._h_tools(None, {})["endpoints"]}
        assert "simulate-gel" in eps
        assert eps["simulate-gel"]["write"] is False


class TestAddCodonTableGenome:
    """`add-codon-table` source='genome' branch. The NCBI Datasets fetch is
    monkeypatched (no network); validation + save wiring are exercised."""

    def test_missing_accession_and_taxid(self):
        result = sc._h_add_codon_table(None, {"source": "genome"})
        payload, status = result
        assert status == 400
        assert "accession" in payload["error"]

    def test_bad_mode_rejected(self):
        result = sc._h_add_codon_table(
            None, {"source": "genome", "taxid": "1423", "mode": "best"})
        payload, status = result
        assert status == 400
        assert "mode" in payload["error"]

    def test_happy_path_builds_and_saves(self, monkeypatch):
        fake_raw = {"GCT": ("A", 100), "ATG": ("M", 30), "TAA": ("*", 5)}

        def fake_build(query, mode, timeout=60.0):
            return fake_raw, "built ok", {
                "accession": "GCF_TEST.1",
                "taxid": query if str(query).isdigit() else "",
                "organism": "Testus organismus",
                "stats": {"mode": mode, "n_cds_total": 7, "n_codons": 135},
            }
        # _h_add_codon_table moved to splicecraft_agent — patch the sibling
        # namespace it resolves the genome builder in (not the hub re-export).
        monkeypatch.setattr(
            "splicecraft_agent._genome_build_codon_table", fake_build)
        result = sc._h_add_codon_table(
            None, {"source": "genome", "taxid": "1423", "mode": "heg"})
        assert result["ok"] is True
        assert result["entry"]["source"] == "genome"
        assert result["entry"]["taxid"] == "1423"
        got = sc._codon_tables_get("1423")
        assert got is not None and got["source"] == "genome"
        assert got["name"] == "Testus organismus"   # default from organism

    def test_build_failure_returns_502(self, monkeypatch):
        monkeypatch.setattr(
            "splicecraft_agent._genome_build_codon_table",
            lambda q, m, timeout=60.0: (None, "no such assembly", None))
        result = sc._h_add_codon_table(
            None, {"source": "genome", "accession": "GCF_000000000.0"})
        payload, status = result
        assert status == 502
        assert "no such assembly" in payload["error"]


class TestAddCodonTableFile:
    """`add-codon-table` source='file' branch — offline build from a local CDS
    FASTA on disk. Exercises path-safety, validation, and the real save (no
    network); the builder runs for real against an inline CDS file."""

    _CDS = (">lcl|X_cds_1 [gene=rplB] [protein=50S ribosomal protein L2]\n"
            "ATGAAAGCTCGTTGGTGT\n"
            ">lcl|X_cds_2 [gene=dnaA] [protein=replication initiator]\n"
            "ATGTGGGCTAAA\n")

    def test_missing_path_rejected(self):
        payload, status = sc._h_add_codon_table(None, {"source": "file"})
        assert status == 400 and "path" in payload["error"]

    def test_bad_mode_rejected(self, tmp_path):
        p = tmp_path / "cds.fna"
        p.write_text(self._CDS)
        payload, status = sc._h_add_codon_table(
            None, {"source": "file", "path": str(p), "mode": "best"})
        assert status == 400 and "mode" in payload["error"]

    def test_happy_path_builds_and_saves(self, tmp_path):
        p = tmp_path / "ecoli_cds.fna"
        p.write_text(self._CDS)
        result = sc._h_add_codon_table(
            None, {"source": "file", "path": str(p), "mode": "genome"})
        assert result["ok"] is True
        assert result["entry"]["source"] == "file"
        assert result["entry"]["name"] == "ecoli_cds"   # stem -> organism default
        key = result["entry"]["taxid"] or result["entry"]["name"]
        got = sc._codon_tables_get(key)
        assert got is not None and got["source"] == "file"

    def test_nonexistent_file_returns_4xx(self, tmp_path):
        payload, status = sc._h_add_codon_table(
            None, {"source": "file", "path": str(tmp_path / "nope.fna"),
                   "mode": "genome"})
        assert status in (400, 502)   # build returns no data / clean read error
