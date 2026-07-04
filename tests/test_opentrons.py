"""Tests for splicecraft_opentrons — the OT-2 protocol compiler + LAN client.

The compiler / validator / fault-detection logic is pure and fully exercised
here. The robot-server HTTP calls are network I/O and are NOT hit in CI; the
run/analyse/state paths are covered by monkeypatching the module's single JSON
transport (``_ot2_request_json`` / ``_ot2_request_multipart``), so the parsing
and — crucially — the physical-run SAFETY GATE are guarded without a robot.
Live hardware round-trips are done by hand against a real OT-2.
"""
from __future__ import annotations

import pytest

import splicecraft_opentrons as ot2


def _good_plan():
    return {
        "name": "prep",
        "pipette": "p300_single", "mount": "left",
        "tips": {"labware": "tiprack_300", "slot": 8},
        "labware": {"src": {"labware": "eppi_24", "slot": 1},
                    "dst": {"labware": "plate_24", "slot": 2}},
        "transfers": [{"from": "src:A1", "to": "dst:A1", "volume": 50},
                      {"from": "src:A2", "to": "dst:A2", "volume": 50}],
        "new_tip": "always",
    }


# ── Compiler ────────────────────────────────────────────────────────────────────
class TestCompile:
    def test_emits_valid_python(self):
        proto = ot2._ot2_compile_protocol(_good_plan())
        compile(proto, "<gen>", "exec")  # must be valid Python

    def test_expected_api_calls(self):
        proto = ot2._ot2_compile_protocol(_good_plan())
        for needle in ("from opentrons import protocol_api", "def run(protocol",
                       "load_labware", "load_instrument", "protocol.home()",
                       "pipette.transfer("):
            assert needle in proto

    def test_alias_expansion(self):
        proto = ot2._ot2_compile_protocol(_good_plan())
        assert "corning_24_wellplate_3.4ml_flat" in proto        # plate_24
        assert "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap" in proto  # eppi_24
        assert "opentrons_96_tiprack_300ul" in proto             # tiprack_300

    def test_new_tip_policy_in_output(self):
        plan = _good_plan(); plan["new_tip"] = "once"
        assert 'new_tip="once"' in ot2._ot2_compile_protocol(plan)

    def test_volumes_and_wells_mapped(self):
        proto = ot2._ot2_compile_protocol(_good_plan())
        assert "[50, 50]" in proto
        assert 'lw_src["A1"]' in proto and 'lw_dst["A2"]' in proto

    def test_over_max_volume_is_allowed(self):
        # 500 µL > P300 max (300) — the API splits it, so this is NOT an error
        plan = _good_plan()
        plan["transfers"] = [{"from": "src:A1", "to": "dst:A1", "volume": 500}]
        proto = ot2._ot2_compile_protocol(plan)
        assert "[500]" in proto

    def test_compile_rejects_invalid_plan(self):
        plan = _good_plan()
        plan["transfers"] = [{"from": "src:Z9", "to": "dst:A1", "volume": 50}]
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_compile_protocol(plan)


# ── Validation ──────────────────────────────────────────────────────────────────
class TestValidate:
    def test_good_plan_clean(self):
        rep = ot2._ot2_validate_plan(_good_plan())
        assert rep["errors"] == [] and rep["warnings"] == []

    def test_slot_clash(self):
        plan = _good_plan()
        plan["labware"]["dst"]["slot"] = 8   # collides with the tip rack
        assert any("slot 8 already" in e for e in ot2._ot2_validate_plan(plan)["errors"])

    def test_trash_slot_12_rejected(self):
        plan = _good_plan()
        plan["labware"]["dst"]["slot"] = 12
        assert any("slot 12" in e or "not a labware slot" in e
                   for e in ot2._ot2_validate_plan(plan)["errors"])

    def test_out_of_range_well(self):
        plan = _good_plan()
        plan["transfers"] = [{"from": "src:E1", "to": "dst:A1", "volume": 50}]  # 24-rack has A-D
        assert any("out of range" in e for e in ot2._ot2_validate_plan(plan)["errors"])

    def test_unknown_labware_id(self):
        plan = _good_plan()
        plan["transfers"] = [{"from": "ghost:A1", "to": "dst:A1", "volume": 50}]
        assert any("unknown labware id" in e for e in ot2._ot2_validate_plan(plan)["errors"])

    def test_bad_ref_format(self):
        plan = _good_plan()
        plan["transfers"] = [{"from": "srcA1", "to": "dst:A1", "volume": 50}]
        assert any("labwareId:well" in e for e in ot2._ot2_validate_plan(plan)["errors"])

    def test_unknown_pipette(self):
        plan = _good_plan(); plan["pipette"] = "p999_imaginary"
        assert any("unknown pipette" in e for e in ot2._ot2_validate_plan(plan)["errors"])

    def test_subminimum_volume_warns_not_errors(self):
        plan = _good_plan()
        plan["transfers"] = [{"from": "src:A1", "to": "dst:A1", "volume": 5}]
        rep = ot2._ot2_validate_plan(plan)
        assert rep["errors"] == []
        assert any("below the p300_single minimum" in w for w in rep["warnings"])

    def test_nonpositive_volume_errors(self):
        plan = _good_plan()
        plan["transfers"] = [{"from": "src:A1", "to": "dst:A1", "volume": 0}]
        assert any("positive" in e for e in ot2._ot2_validate_plan(plan)["errors"])

    def test_missing_pieces(self):
        assert any("no tip rack" in e for e in
                   ot2._ot2_validate_plan({"labware": {"a": {"labware": "plate_24", "slot": 1}},
                                           "transfers": [{"from": "a:A1", "to": "a:A2",
                                                          "volume": 50}]})["errors"])
        assert any("no transfers" in e for e in
                   ot2._ot2_validate_plan({"tips": {"labware": "tiprack_300", "slot": 8},
                                           "labware": {"a": {"labware": "plate_24", "slot": 1}}}
                                          )["errors"])

    def test_unknown_labware_warns(self):
        plan = _good_plan()
        plan["labware"]["src"]["labware"] = "acme_weird_plate"
        assert any("not in the built-in catalog" in w
                   for w in ot2._ot2_validate_plan(plan)["warnings"])


# ── Hardening / edge cases ──────────────────────────────────────────────────────
class TestHardening:
    def test_too_many_transfers(self):
        plan = _good_plan()
        plan["transfers"] = [{"from": "src:A1", "to": "dst:A1", "volume": 50}
                             ] * (ot2._OT2_MAX_TRANSFERS + 1)
        assert any("too many transfers" in e for e in ot2._ot2_validate_plan(plan)["errors"])

    def test_infinite_volume_rejected(self):
        plan = _good_plan()
        plan["transfers"] = [{"from": "src:A1", "to": "dst:A1", "volume": float("inf")}]
        assert any("finite" in e for e in ot2._ot2_validate_plan(plan)["errors"])

    def test_nan_volume_rejected(self):
        plan = _good_plan()
        plan["transfers"] = [{"from": "src:A1", "to": "dst:A1", "volume": float("nan")}]
        assert any("finite" in e for e in ot2._ot2_validate_plan(plan)["errors"])

    def test_absurd_volume_rejected(self):
        plan = _good_plan()
        plan["transfers"] = [{"from": "src:A1", "to": "dst:A1", "volume": 1e9}]
        assert any("implausibly large" in e for e in ot2._ot2_validate_plan(plan)["errors"])

    def test_json_infinity_cannot_reach_compile(self):
        # json.loads accepts `Infinity` by default — it must be caught, not emitted
        import json as _json
        plan = _json.loads(
            '{"pipette":"p300_single","tips":{"labware":"tiprack_300","slot":8},'
            '"labware":{"a":{"labware":"plate_24","slot":1}},'
            '"transfers":[{"from":"a:A1","to":"a:A2","volume":Infinity}]}')
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_compile_protocol(plan)

    def test_labware_id_var_collision_safe(self):
        # two distinct ids that sanitise to the same identifier must NOT collide
        # into one variable (which would silently map a transfer to wrong labware)
        plan = {"pipette": "p300_single",
                "tips": {"labware": "tiprack_300", "slot": 8},
                "labware": {"a-b": {"labware": "plate_24", "slot": 1},
                            "a.b": {"labware": "plate_6", "slot": 2}},
                "transfers": [{"from": "a-b:A1", "to": "a.b:A1", "volume": 50}]}
        proto = ot2._ot2_compile_protocol(plan)
        compile(proto, "<gen>", "exec")               # valid python
        assert proto.count("load_labware(") == 3      # tiprack + 2 distinct labware
        assert "lw_a_b " in proto and "lw_a_b_2 " in proto

    def test_protocol_upload_size_cap(self, monkeypatch):
        # _ot2_analyze must refuse a giant protocol BEFORE any network call
        hit = {"n": 0}
        monkeypatch.setattr(ot2, "_ot2_request_multipart",
                            lambda *a, **k: hit.__setitem__("n", hit["n"] + 1) or {})
        huge = "x" * (ot2._OT2_MAX_PROTOCOL_BYTES + 1)
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_analyze("1.2.3.4", huge)
        assert hit["n"] == 0


# ── Well geometry ───────────────────────────────────────────────────────────────
class TestGeometry:
    def test_wells_counts(self):
        assert len(ot2._ot2_wells(8, 12)) == 96
        assert len(ot2._ot2_wells(4, 6)) == 24
        assert ot2._ot2_wells(2, 3) == ["A1", "A2", "A3", "B1", "B2", "B3"]

    def test_well_ok_per_format(self):
        rack24 = "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap"
        assert ot2._ot2_well_ok(rack24, "D6") is True
        assert ot2._ot2_well_ok(rack24, "E1") is False   # only A-D
        assert ot2._ot2_well_ok(rack24, "A7") is False   # only 1-6
        assert ot2._ot2_well_ok("corning_6_wellplate_16.8ml_flat", "B3") is True
        assert ot2._ot2_well_ok("corning_6_wellplate_16.8ml_flat", "C1") is False
        assert ot2._ot2_well_ok("acme_unknown", "A1") is None  # unknown -> defer to robot


# ── Client pure helpers ─────────────────────────────────────────────────────────
class TestClientPure:
    def test_base_url(self):
        assert ot2._ot2_base_url("192.168.1.56") == "http://192.168.1.56:31950"
        assert ot2._ot2_base_url("opentrons.local:31950") == "http://opentrons.local:31950"
        assert ot2._ot2_base_url("http://1.2.3.4:31950") == "http://1.2.3.4:31950"
        assert ot2._ot2_base_url("1.2.3.4") == "http://1.2.3.4:31950"

    def test_base_url_rejects_https_and_empty(self):
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_base_url("https://1.2.3.4")
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_base_url("")

    def test_user_agent(self):
        assert ot2._ot2_user_agent().startswith("SpliceCraft/")


# ── Fault detection ─────────────────────────────────────────────────────────────
class TestFaultDetection:
    def test_clean_state_no_faults(self):
        assert ot2._ot2_detect_faults({"reachable": True, "instruments": [{"ok": True}]}) == []

    def test_unreachable(self):
        assert ot2._ot2_detect_faults({"reachable": False}) == ["unreachable"]

    def test_instrument_fault(self):
        f = ot2._ot2_detect_faults({"reachable": True,
                                    "instruments": [{"mount": "left", "model": "p300", "ok": False}]})
        assert any("instrument fault" in x for x in f)

    def test_bad_calibration(self):
        f = ot2._ot2_detect_faults({"reachable": True,
                                    "calibration": {"deck_status": "BAD_CALIBRATION"}})
        assert any("deck calibration" in x for x in f)
        f2 = ot2._ot2_detect_faults({"reachable": True,
                                     "calibration": {"deck_status": "OK", "marked_bad": True}})
        assert any("marked bad" in x for x in f2)

    def test_run_and_command_failure(self):
        f = ot2._ot2_detect_faults({"reachable": True, "run": {
            "status": "failed", "errors": [{"detail": "hit the plate"}],
            "failed_commands": [{"commandType": "aspirate", "error": "overpressure",
                                 "at": {"wellName": "A1"}}]}})
        assert any("run failed: hit the plate" in x for x in f)
        assert any("command failed: aspirate" in x for x in f)


# ── State / analysis / gate (monkeypatched transport, no network) ───────────────
def _fake_transport(monkeypatch, *, instrument_ok=True, deck="OK", marked_bad=False,
                    runs=None):
    def fake_json(host, path, **kw):
        if path == "/health":
            return {"name": "Fake", "api_version": "4", "robot_model": "OT-2 Standard"}
        if path == "/instruments":
            return {"data": [{"mount": "left", "instrumentModel": "p300_single",
                              "ok": instrument_ok,
                              "data": {"min_volume": 30, "max_volume": 300, "channels": 1}}]}
        if path == "/motors/engaged":
            return {"x": {"enabled": True}}
        if path == "/robot/lights":
            return {"on": True}
        if path == "/calibration/status":
            return {"deckCalibration": {"status": deck,
                                        "data": {"status": {"markedBad": marked_bad}}}}
        if path == "/modules":
            return {"data": []}
        if path == "/settings":
            return {"settings": [{"id": "shortFixedTrash", "value": None}]}
        if path == "/runs":
            return {"data": runs or []}
        raise AssertionError(f"unexpected path {path}")
    monkeypatch.setattr(ot2, "_ot2_request_json", fake_json)


class TestStateParsing:
    def test_healthy_snapshot(self, monkeypatch):
        _fake_transport(monkeypatch)
        st = ot2._ot2_state("fakehost")
        assert st["reachable"] and st["ok"] and st["faults"] == []
        assert st["instruments"][0]["max_volume"] == 300
        assert "shortFixedTrash" in st["settings"]

    def test_snapshot_flags_instrument_fault(self, monkeypatch):
        _fake_transport(monkeypatch, instrument_ok=False)
        st = ot2._ot2_state("fakehost")
        assert not st["ok"]
        assert any("instrument fault" in f for f in st["faults"])

    def test_snapshot_flags_bad_calibration(self, monkeypatch):
        _fake_transport(monkeypatch, deck="SINGULARITY")
        st = ot2._ot2_state("fakehost")
        assert any("deck calibration" in f for f in st["faults"])

    def test_unreachable_snapshot(self, monkeypatch):
        def boom(host, path, **kw):
            raise ot2.OT2Error("connection refused")
        monkeypatch.setattr(ot2, "_ot2_request_json", boom)
        st = ot2._ot2_state("fakehost")
        assert st["reachable"] is False and st["ok"] is False


class TestRunStateParsing:
    def test_parses_current_and_failed(self, monkeypatch):
        def fake_json(host, path, **kw):
            if path == "/runs/R1":
                return {"data": {"status": "failed", "errors": [{"detail": "crash"}]}}
            if path.startswith("/runs/R1/commands"):
                return {"data": [
                    {"id": "c1", "commandType": "pickUpTip", "status": "succeeded"},
                    {"id": "c2", "commandType": "aspirate", "status": "failed",
                     "error": {"detail": "overpressure"}, "params": {"wellName": "A1"}},
                ]}
            raise AssertionError(path)
        monkeypatch.setattr(ot2, "_ot2_request_json", fake_json)
        rs = ot2._ot2_run_state("h", "R1")
        assert rs["status"] == "failed"
        assert rs["current_command"]["commandType"] == "pickUpTip"
        assert rs["failed_commands"][0]["error"] == "overpressure"
        assert rs["failed_commands"][0]["at"] == {"wellName": "A1"}


class TestAnalyzeAndGate:
    def _patch_analysis(self, monkeypatch, *, result="ok", errors=None):
        def fake_multipart(host, path, **kw):
            return {"data": {"id": "PID", "analysisSummaries": [{"id": "AID"}]}}
        def fake_json(host, path, **kw):
            if path == "/protocols/PID/analyses/AID":
                return {"data": {"status": "completed", "result": result,
                                 "errors": errors or [], "commands": [{"commandType": "home"}],
                                 "pipettes": [], "labware": []}}
            raise AssertionError(path)
        monkeypatch.setattr(ot2, "_ot2_request_multipart", fake_multipart)
        monkeypatch.setattr(ot2, "_ot2_request_json", fake_json)

    def test_analyze_ok(self, monkeypatch):
        self._patch_analysis(monkeypatch)
        res = ot2._ot2_analyze("h", "print(1)")
        assert res["result"] == "ok" and res["protocol_id"] == "PID"

    def test_gate_blocks_when_analysis_fails(self, monkeypatch):
        self._patch_analysis(monkeypatch, result="not-ok",
                             errors=[{"detail": "bad labware"}])
        res = ot2._ot2_run_protocol("h", "print(1)", confirm=True)
        assert res["ran"] is False and res["reason"] == "analysis-failed"

    def test_gate_blocks_without_confirm(self, monkeypatch):
        self._patch_analysis(monkeypatch)
        res = ot2._ot2_run_protocol("h", "print(1)", confirm=False)
        assert res["ran"] is False and res["reason"] == "confirm-required"

    def test_gate_blocks_unhealthy_robot(self, monkeypatch):
        # analysis passes + confirm given, but the robot is pre-faulted -> no run
        def fake_multipart(host, path, **kw):
            return {"data": {"id": "PID", "analysisSummaries": [{"id": "AID"}]}}
        monkeypatch.setattr(ot2, "_ot2_request_multipart", fake_multipart)

        def fake_json(host, path, **kw):
            if path == "/protocols/PID/analyses/AID":
                return {"data": {"status": "completed", "result": "ok", "errors": [],
                                 "commands": [], "pipettes": [], "labware": []}}
            if path == "/instruments":   # pipette subsystem fault
                return {"data": [{"mount": "left", "instrumentModel": "p300_single",
                                  "ok": False, "data": {}}]}
            if path == "/health":
                return {"name": "Fake"}
            if path in ("/motors/engaged", "/robot/lights", "/calibration/status",
                        "/modules", "/settings", "/runs"):
                return {"data": []}
            raise AssertionError(path)
        monkeypatch.setattr(ot2, "_ot2_request_json", fake_json)
        res = ot2._ot2_run_protocol("h", "print(1)", confirm=True)
        assert res["ran"] is False and res["reason"] == "robot-unhealthy"
        assert any("instrument fault" in f for f in res["faults"])


# ── Agent endpoints (ot2-compile / -status / -analyze / -run) ───────────────────
class TestAgentEndpoints:
    def _handlers(self):
        import splicecraft as sc
        return sc._AGENT_HANDLERS

    def test_registered_with_write_flags(self):
        H = self._handlers()
        for name in ("ot2-compile", "ot2-status", "ot2-analyze", "ot2-run"):
            assert name in H, f"{name} not registered"
        assert H["ot2-run"][1] is True       # physical actuation is a write endpoint
        assert H["ot2-compile"][1] is False
        assert H["ot2-status"][1] is False
        assert H["ot2-analyze"][1] is False

    def test_compile_endpoint_offline(self):
        res = self._handlers()["ot2-compile"][0](None, _good_plan())
        assert res["valid"] is True and "protocol" in res
        assert "load_instrument" in res["protocol"]
        assert res["summary"]["transfers"] == 2

    def test_compile_endpoint_reports_invalid(self):
        plan = _good_plan()
        plan["transfers"] = [{"from": "src:Z9", "to": "dst:A1", "volume": 50}]
        res = self._handlers()["ot2-compile"][0](None, plan)
        assert res["valid"] is False and "protocol" not in res and res["errors"]

    def test_host_required_guards(self):
        H = self._handlers()
        for name in ("ot2-status", "ot2-analyze", "ot2-run"):
            r = H[name][0](None, {})
            assert isinstance(r, tuple) and r[1] == 400, name

    def test_run_endpoint_confirm_gate(self, monkeypatch):
        # host present but confirm defaults false -> no motion, no network past analyse
        monkeypatch.setattr(ot2, "_ot2_analyze", lambda host, txt, **kw: {
            "result": "ok", "protocol_id": "P", "analysis_id": "A", "status": "completed",
            "errors": [], "commands": [], "pipettes": [], "labware": []})
        res = self._handlers()["ot2-run"][0](None, {"host": "1.2.3.4", **_good_plan()})
        assert res["ran"] is False and res["reason"] == "confirm-required"


# ── AUTOLAB toolbar screen ──────────────────────────────────────────────────────
class TestAutolabScreen:
    def test_menu_item_registered(self):
        import splicecraft as sc
        assert "AUTOLAB" in sc.MenuBar.MENUS
        assert sc.MenuBar.MENUS[-1] == "BABS"  # AUTOLAB inserted before BABS (test_babs)

    async def test_open_and_compile(self):
        import splicecraft as sc
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            assert isinstance(app.screen, sc.AutolabScreen)
            scr = app.screen
            # default deck (P300/tiprack_300/eppi_24/plate_24) + one transfer
            scr._transfers.append({"from": "src:A1", "to": "dst:A1", "volume": 50})
            proto = scr._compile()
            assert proto is not None
            assert "load_instrument" in proto and "p300_single" in proto
            # re-opening resurfaces the same instance (no duplicate on the stack)
            app.action_open_autolab()
            await pilot.pause()
            assert sum(isinstance(s, sc.AutolabScreen) for s in app.screen_stack) == 1

    async def test_escape_unwinds_to_main(self):
        import splicecraft as sc
        from textual.screen import Screen
        from textual.widgets import Static
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            base = len(app.screen_stack)
            # AUTOLAB's own Escape pops back to the main screen
            app.action_open_autolab()
            await pilot.pause()
            assert isinstance(app.screen, sc.AutolabScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, sc.AutolabScreen)
            assert len(app.screen_stack) == base
            # app-level fallback: a screen that binds NO escape still pops, so the
            # user can never get stuck in a modal (real modals have focusable
            # content, so give the probe a focusable Button)
            from textual.widgets import Button
            class _Bare(Screen):
                def compose(self):
                    yield Button("x")
            app.push_screen(_Bare())
            await pilot.pause()
            assert isinstance(app.screen, _Bare)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, _Bare)
            assert len(app.screen_stack) == base

    async def test_ui_rejects_nonfinite_volume(self):
        import splicecraft as sc
        from textual.widgets import Input
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab(); await pilot.pause()
            scr = app.screen
            scr.query_one("#autolab-from", Input).value = "A1"
            scr.query_one("#autolab-to", Input).value = "A1"
            scr.query_one("#autolab-vol", Input).value = "inf"
            scr._add_transfer()
            assert scr._transfers == []   # inf must not be added
