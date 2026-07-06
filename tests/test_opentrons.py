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


# ── Custom labware definitions ──────────────────────────────────────────────────
class TestCustomLabware:
    _DEF = {"metadata": {"displayName": "My Custom Rack"},
            "wells": {"A1": {"depth": 40}, "A2": {"depth": 40}},
            "ordering": [["A1"], ["A2"]]}

    def _plan(self, **over):
        p = {"pipette": "p300_single", "tips": {"labware": "tiprack_300", "slot": 8},
             "labware": {"src": {"labware": "custom_rack", "slot": 1, "definition": self._DEF},
                         "dst": {"labware": "plate_24", "slot": 2}},
             "steps": [{"type": "transfer", "from": "src:A1", "to": "dst:A1", "volume": 50}]}
        p.update(over)
        return p

    def test_compiles_via_load_from_definition(self):
        proto = ot2._ot2_compile_protocol(self._plan())
        compile(proto, "<gen>", "exec")
        assert "load_labware_from_definition" in proto and "My Custom Rack" in proto
        assert "load_labware(" in proto            # dst still uses the catalog path

    def test_wells_checked_against_custom_def(self):
        # A2 is declared in the def -> ok; B1 is not -> out of range
        assert ot2._ot2_validate_plan(self._plan(
            steps=[{"type": "transfer", "from": "src:A2", "to": "dst:A1", "volume": 50}]
        ))["errors"] == []
        bad = ot2._ot2_validate_plan(self._plan(
            steps=[{"type": "transfer", "from": "src:B1", "to": "dst:A1", "volume": 50}]))
        assert any("out of range" in e for e in bad["errors"])

    def test_custom_without_wells_warns_not_errors(self):
        rep = ot2._ot2_validate_plan({"pipette": "p300_single",
            "tips": {"labware": "tiprack_300", "slot": 8},
            "labware": {"src": {"labware": "blob", "slot": 1,
                                "definition": {"metadata": {"displayName": "Blob"}}},
                        "dst": {"labware": "plate_24", "slot": 2}},
            "steps": [{"type": "transfer", "from": "src:A1", "to": "dst:A1", "volume": 50}]})
        assert rep["errors"] == []
        assert any("declares no 'wells'" in w for w in rep["warnings"])


# ── Deck visualizer ─────────────────────────────────────────────────────────────
class TestDeckVisualizer:
    def test_renders_slots_labware_and_trash(self):
        deck = ot2._ot2_render_deck({
            "tips": {"labware": "tiprack_300", "slot": 8},
            "labware": {"src": {"labware": "eppi_24", "slot": 1},
                        "dst": {"labware": "plate_24", "slot": 2}}})
        assert "TRASH" in deck                       # the fixed trash at slot 12
        assert "tiprack_300" in deck                 # tips in slot 8
        assert "src" in deck and "dst" in deck       # labware ids
        assert "plate_24" in deck
        for n in range(1, 13):                        # every deck slot labelled
            assert str(n) in deck
        assert "┌" in deck and "└" in deck            # box-drawn grid

    def test_custom_labware_labelled_custom(self):
        deck = ot2._ot2_render_deck({
            "tips": {"labware": "tiprack_300", "slot": 8},
            "labware": {"x": {"slot": 3, "definition": {"metadata": {}, "wells": {"A1": {}}}}}})
        assert "custom" in deck


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
        if path == "/robot/door/status":
            return {"data": {"status": "closed", "doorRequiredClosedForProtocol": False}}
        if path == "/calibration/status":
            return {"deckCalibration": {"status": deck,
                                        "data": {"status": {"markedBad": marked_bad}}}}
        if path == "/modules":
            return {"data": []}
        if path == "/settings":
            return {"settings": [{"id": "shortFixedTrash", "value": None}]}
        if path == "/runs":
            return {"data": runs or []}
        if path == "/calibration/pipette_offset":
            return {"data": [{"mount": "left", "pipette": "p300_single_v1"}]}
        if path == "/calibration/tip_length":
            return {"data": []}
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
            if path in ("/motors/engaged", "/robot/lights", "/robot/door/status",
                        "/calibration/status", "/modules", "/settings", "/runs",
                        "/calibration/pipette_offset", "/calibration/tip_length"):
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

    def test_library_endpoints_crud(self):
        H = self._handlers()
        assert H["save-protocol"][1] is True and H["list-protocols"][1] is False
        # protocols: save -> list -> get -> collections -> delete
        r = H["save-protocol"][0](None, {"name": "P1", "plan": _good_plan(), "collection": "Mine"})
        assert r["ok"] and r["collection"] == "Mine"
        assert any(p["name"] == "P1" for p in H["list-protocols"][0](None, {})["protocols"])
        assert H["get-protocol"][0](None, {"name": "P1"})["plan"]["pipette"] == "p300_single"
        assert any(c["name"] == "Mine"
                   for c in H["list-protocol-collections"][0](None, {})["protocol_collections"])
        assert H["delete-protocol"][0](None, {"name": "P1"})["deleted"] == "P1"
        # save-protocol rejects an invalid plan
        bad = H["save-protocol"][0](None, {"name": "X", "plan": {"steps": [{"type": "frob"}]}})
        assert isinstance(bad, tuple) and bad[1] == 400
        # custom labware: save -> list -> get -> delete
        d = {"metadata": {"displayName": "R"}, "wells": {"A1": {}}}
        assert H["save-custom-labware"][0](None, {"name": "LW1", "definition": d})["ok"]
        assert any(x["name"] == "LW1"
                   for x in H["list-custom-labware"][0](None, {})["custom_labware"])
        assert H["get-custom-labware"][0](None, {"name": "LW1"})["definition"]["wells"] == {"A1": {}}
        assert H["save-custom-labware"][0](None, {"name": "Y", "definition": {"no": 1}})[1] == 400
        assert H["delete-custom-labware"][0](None, {"name": "LW1"})["deleted"] == "LW1"

    def test_delete_protocol_is_scoped_and_case_insensitive(self):
        # audit #2 + #5: delete must remove ONE item (optionally collection-scoped),
        # case-insensitively — not strip the name from every collection.
        H = self._handlers()
        H["save-protocol"][0](None, {"name": "prep", "plan": _good_plan(), "collection": "A"})
        H["save-protocol"][0](None, {"name": "prep", "plan": _good_plan(), "collection": "B"})
        assert H["delete-protocol"][0](None, {"name": "prep", "collection": "B"})["deleted"] == "prep"
        got = H["list-protocols"][0](None, {})["protocols"]
        assert any(p["name"] == "prep" and p["collection"] == "A" for p in got)
        assert not any(p["name"] == "prep" and p["collection"] == "B" for p in got)
        assert H["delete-protocol"][0](None, {"name": "PREP"})["deleted"] == "PREP"   # case-insensitive
        assert not any(p["name"] == "prep" for p in H["list-protocols"][0](None, {})["protocols"])

    def test_save_endpoints_reject_oversized_and_malformed(self):
        # audit #6: per-item byte cap; audit #3: malformed plan types must not 500.
        H = self._handlers()
        big = {"pipette": "p300_single", "labware": {}, "tips": [],
               "steps": [{"type": "comment", "text": "x" * 600_000}]}
        assert H["save-protocol"][0](None, {"name": "big", "plan": big})[1] == 413
        r = H["save-protocol"][0](None, {"name": "m", "plan": {"tips": 5, "labware": ["x"], "steps": []}})
        assert (isinstance(r, dict) and r.get("ok")) or (isinstance(r, tuple) and r[1] in (400, 413))
        bigdef = {"wells": {"A1": {}}, "blob": "y" * 600_000}
        assert H["save-custom-labware"][0](None, {"name": "bl", "definition": bigdef})[1] == 413


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
            # build a multi-step protocol directly on the step list
            scr._steps.append({"type": "transfer", "from": "src:A1", "to": "dst:A1", "volume": 50})
            scr._steps.append({"type": "delay", "seconds": 30})
            scr._steps.append({"type": "comment", "text": "done"})
            proto = scr._compile()
            assert proto is not None
            assert "load_instrument" in proto and "p300_single" in proto
            assert "pipette.transfer(50" in proto
            assert "protocol.delay(seconds=30" in proto
            assert 'protocol.comment("done")' in proto
            # re-opening resurfaces the same instance (no duplicate on the stack)
            app.action_open_autolab()
            await pilot.pause()
            assert sum(isinstance(s, sc.AutolabScreen) for s in app.screen_stack) == 1

    async def test_escape_unwinds_to_main(self):
        import splicecraft as sc
        from textual.screen import Screen
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
            scr.query_one("#autolab-step-from", Input).value = "A1"
            scr.query_one("#autolab-step-to", Input).value = "A1"
            scr.query_one("#autolab-step-vol", Input).value = "inf"
            scr._add_step()
            assert scr._steps == []   # inf must not be added

    async def test_ui_add_distribute_step(self):
        import splicecraft as sc
        from textual.widgets import Input, Select
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab(); await pilot.pause()
            scr = app.screen
            scr.query_one("#autolab-step-type", Select).value = "distribute"
            await pilot.pause()
            scr.query_one("#autolab-step-from", Input).value = "A1"
            scr.query_one("#autolab-step-to", Input).value = "A1, A2, A3"
            scr.query_one("#autolab-step-vol", Input).value = "40"
            scr._add_step()
            assert len(scr._steps) == 1 and scr._steps[0]["type"] == "distribute"
            assert scr._steps[0]["to"] == ["dst:A1", "dst:A2", "dst:A3"]
            assert "pipette.distribute(40" in scr._compile()

    async def test_interactive_deck_place_remove_and_custom_labware(self):
        import splicecraft as sc
        from textual.widgets import Input, TabbedContent
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab(); await pilot.pause()
            scr = app.screen
            assert scr.query_one("#autolab-deck-map", sc.OT2DeckMap) is not None  # deck drawn
            # place a reservoir in slot 5 via the picker result
            scr._on_picker_result(5, {"action": "place", "labware": "reservoir_12",
                                      "nickname": "res"})
            assert scr._deck[5]["labware"] == "reservoir_12" and scr._deck[5]["id"] == "res"
            # remove a slot
            scr._on_picker_result(1, {"action": "remove"})
            assert 1 not in scr._deck
            # "new labware" jumps to the Labware tab
            scr._on_picker_result(2, {"action": "new"}); await pilot.pause()
            assert scr.query_one("#autolab-tabs", TabbedContent).active == "autolab-tab-labware"
            # create a custom labware, place it, and confirm it reaches the protocol
            scr.query_one("#autolab-lw-name", Input).value = "My Rack"
            scr.query_one("#autolab-lw-rows", Input).value = "2"
            scr.query_one("#autolab-lw-cols", Input).value = "3"
            scr._create_labware()
            assert scr._find_custom_labware_def("My Rack") is not None
            scr._on_picker_result(6, {"action": "place", "labware": "custom:My Rack",
                                      "nickname": "r"})
            assert scr._deck[6].get("definition") is not None
            scr._steps.append({"type": "transfer", "from": "r:A1", "to": "res:A1", "volume": 50})
            proto = scr._compile()
            assert proto and "load_labware_from_definition" in proto

    async def test_labware_picker_modal_mounts_and_escapes(self):
        import splicecraft as sc
        from textual.widgets import Select
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.push_screen(sc.OT2LabwarePickerModal(3)); await pilot.pause()
            assert isinstance(app.screen, sc.OT2LabwarePickerModal)
            assert app.screen.query_one("#ot2lw-select", Select) is not None
            await pilot.press("escape"); await pilot.pause()
            assert not isinstance(app.screen, sc.OT2LabwarePickerModal)

    async def test_protocol_save_load_rename_delete(self):
        import splicecraft as sc
        from textual.widgets import Input, Select
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab(); await pilot.pause()
            scr = app.screen
            scr._steps.append({"type": "transfer", "from": "src:A1", "to": "dst:A1", "volume": 50})
            scr._on_picker_result(5, {"action": "place", "labware": "reservoir_12", "nickname": "res"})
            scr.query_one("#autolab-proto-name", Input).value = "P1"
            scr._save_protocol()
            names = lambda: [n for _, n in scr._proto_index]   # noqa: E731
            assert "P1" in names()
            # mutate the live design, then load P1 back and confirm it's restored
            scr._steps = []
            scr._deck.pop(5, None)
            scr.query_one("#autolab-proto-pick", Select).value = str(names().index("P1"))
            scr._load_protocol()
            assert len(scr._steps) == 1 and 5 in scr._deck
            # rename
            scr.query_one("#autolab-proto-name", Input).value = "P2"
            scr._rename_protocol()
            assert "P2" in names() and "P1" not in names()
            # delete
            scr.query_one("#autolab-proto-pick", Select).value = str(names().index("P2"))
            scr._delete_protocol()
            assert "P2" not in names()

    async def test_duplicate_nickname_is_disambiguated(self):
        # audit #1: two deck slots must never share a nickname (else _build_plan
        # collapses the dict key and silently drops a slot's labware).
        import splicecraft as sc
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab(); await pilot.pause()
            scr = app.screen
            scr._on_picker_result(5, {"action": "place", "labware": "plate_96", "nickname": "src"})
            assert scr._deck[5]["id"] != "src" and scr._deck[5]["id"].startswith("src")
            plan = scr._build_plan()
            slots = {lw["slot"] for lw in plan["labware"].values()}
            assert 1 in slots and 5 in slots and len(plan["labware"]) >= 3

    async def test_create_labware_nonfinite_dims_no_crash(self):
        # audit #4: inf/nan in Rows/Cols must not crash the button handler.
        import math
        import splicecraft as sc
        from textual.widgets import Input
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab(); await pilot.pause()
            scr = app.screen
            scr.query_one("#autolab-lw-name", Input).value = "Weird"
            scr.query_one("#autolab-lw-rows", Input).value = "inf"
            scr.query_one("#autolab-lw-cols", Input).value = "nan"
            scr._create_labware()
            d = scr._find_custom_labware_def("Weird")
            assert d is not None and all(math.isfinite(w["x"]) for w in d["wells"].values())


class TestHardeningSweep2:
    """Edge-case hardening from the 2026-07-04 fresh audit of the OT-2 subsystem."""

    def test_normalize_survives_malformed_plan_types(self):
        # audit #3: tips as scalar, labware as list, transfers as scalar must not raise.
        for bad in [{"tips": 5}, {"labware": ["x"]}, {"transfers": 7},
                    {"tips": True, "labware": 3, "transfers": "no"}]:
            p = ot2._ot2_normalize_plan(bad)
            assert isinstance(p["tips"], list) and isinstance(p["labware"], dict)

    def test_labware_def_clamps_non_finite_and_bounds(self):
        # audit #4: inf/nan dims -> finite coords, no crash; rows/cols clamp.
        import math
        d = ot2._ot2_build_labware_def("X", float("inf"), float("nan"),
                                       spacing=float("inf"), depth=float("nan"),
                                       volume=float("-inf"))
        assert len(d["wells"]) >= 1
        for w in d["wells"].values():
            assert all(math.isfinite(w[k]) for k in ("x", "y", "z", "depth", "diameter"))
        assert len(ot2._ot2_build_labware_def("Z", 0, 0)["wells"]) == 1
        rmax = len(ot2._ROW_LETTERS)
        assert len(ot2._ot2_build_labware_def("B", 999, 999)["wells"]) == rmax * 99

    def test_deck_from_plan_survives_garbage_and_drops_bad_slots(self):
        # audit #3/#7: garbage types + out-of-range/trash slots don't crash or leak.
        import splicecraft as sc
        d = sc.AutolabScreen._deck_from_plan({"tips": 5, "labware": ["x"], "steps": "no"})
        assert isinstance(d, dict) and d
        d2 = sc.AutolabScreen._deck_from_plan(
            {"labware": {"a": {"labware": "plate_24", "slot": 12},
                         "b": {"labware": "eppi_24", "slot": 99},
                         "c": {"labware": "eppi_24", "slot": 3}}})
        assert set(d2) == {3}


# ── Run control: pause / resume / stop (2026-07-04) ─────────────────────────────
class TestRunControl:
    def test_action_map_and_aliases(self, monkeypatch):
        sent = {}
        def fake_json(host, path, *, method="GET", payload=None, **kw):
            sent["path"] = path
            sent["action"] = (payload or {})["data"]["actionType"]
            return {"data": {}}
        monkeypatch.setattr(ot2, "_ot2_request_json", fake_json)
        ot2._ot2_run_action("h", "R1", "pause")
        assert sent["path"] == "/runs/R1/actions" and sent["action"] == "pause"
        ot2._ot2_run_action("h", "R1", "resume")
        assert sent["action"] == "play"       # resume maps to play
        ot2._ot2_run_action("h", "R1", "cancel")
        assert sent["action"] == "stop"       # cancel is an alias for stop
        ot2._ot2_stop_run("h", "R1")
        assert sent["action"] == "stop"       # stop_run delegates to the action layer

    def test_unknown_action_raises(self):
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_run_action("h", "R1", "frobnicate")

    def test_run_control_resolves_active_run(self, monkeypatch):
        calls = {}
        def fake_json(host, path, *, method="GET", payload=None, **kw):
            if path == "/runs":
                return {"data": [{"id": "RUN9", "current": True}]}
            calls["path"] = path
            calls["action"] = (payload or {})["data"]["actionType"]
            return {"data": {}}
        monkeypatch.setattr(ot2, "_ot2_request_json", fake_json)
        res = ot2._ot2_run_control("h", "pause")   # no run_id -> resolve the active run
        assert res["ok"] and res["run_id"] == "RUN9" and res["action"] == "pause"
        assert calls["path"] == "/runs/RUN9/actions"

    def test_run_control_no_active_run_raises(self, monkeypatch):
        monkeypatch.setattr(ot2, "_ot2_active_run", lambda host: None)
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_run_control("h", "stop")


# ── Identity-linking: ordered wells + plate map (2026-07-04) ────────────────────
class TestEntryWells:
    def test_catalog_geometry_row_major(self):
        w = ot2._ot2_entry_wells({"labware": "eppi_24"})   # 4x6 -> A1..D6, row-major
        assert w[0] == "A1" and w[6] == "B1" and len(w) == 24

    def test_alias_plate96(self):
        assert len(ot2._ot2_entry_wells({"labware": "plate_96"})) == 96

    def test_custom_def_sorted(self):
        d = {"wells": {"B1": {}, "A1": {}, "A2": {}}}
        assert ot2._ot2_entry_wells({"definition": d}) == ["A1", "A2", "B1"]

    def test_unknown_labware_empty(self):
        assert ot2._ot2_entry_wells({"labware": "no_such_labware"}) == []


class TestPlanMetadataPassthrough:
    def test_map_and_collection_preserved(self):
        plan = {"pipette": "p300_single",
                "labware": {"src": {"labware": "eppi_24", "slot": 1,
                                    "collection": "FFE",
                                    "map": {"A1": {"id": "x1", "name": "pA"}}}},
                "steps": []}
        p = ot2._ot2_normalize_plan(plan)
        assert p["labware"]["src"]["collection"] == "FFE"
        assert p["labware"]["src"]["map"]["A1"]["name"] == "pA"

    def test_map_non_dict_ignored(self):
        p = ot2._ot2_normalize_plan(
            {"labware": {"s": {"labware": "eppi_24", "slot": 1, "map": 5}}, "steps": []})
        assert "map" not in p["labware"]["s"]


# ── Concentration normalisation (2026-07-04) ────────────────────────────────────
class TestNormalize:
    def test_target_ng_basic(self):
        items = [{"name": "a", "well": "A1", "concentration": 100.0},
                 {"name": "b", "well": "A2", "concentration": 50.0}]
        r = ot2._ot2_normalize_volumes(items, target_ng=200.0)
        assert r[0]["sample_ul"] == 2.0 and r[0]["achieved_ng"] == 200.0 and r[0]["ok"]
        assert r[1]["sample_ul"] == 4.0

    def test_target_conc_with_diluent(self):
        items = [{"name": "a", "well": "A1", "concentration": 100.0}]
        r = ot2._ot2_normalize_volumes(items, target_conc=20.0, final_volume=50.0)
        assert r[0]["sample_ul"] == 10.0 and r[0]["diluent_ul"] == 40.0
        assert r[0]["achieved_conc"] == 20.0

    def test_low_conc_clamps_and_warns(self):
        r = ot2._ot2_normalize_volumes([{"name": "a", "well": "A1", "concentration": 5.0}],
                                       target_ng=1000.0, max_vol=100.0)
        assert r[0]["sample_ul"] == 100.0 and r[0]["warning"] and r[0]["ok"]

    def test_high_conc_below_floor_warns(self):
        r = ot2._ot2_normalize_volumes([{"name": "a", "well": "A1", "concentration": 1000.0}],
                                       target_ng=100.0, min_vol=30.0)
        assert r[0]["sample_ul"] == 30.0 and r[0]["warning"]

    def test_invalid_concentration_skipped(self):
        r = ot2._ot2_normalize_volumes(
            [{"name": "a", "concentration": 0}, {"name": "b", "concentration": "n/a"}],
            target_ng=200.0)
        assert all(not x["ok"] and x["warning"] for x in r)

    def test_contradictory_or_missing_target_raises(self):
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_normalize_volumes([], target_ng=10, target_conc=10)
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_normalize_volumes([])

    def test_conc_mode_needs_final_volume(self):
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_normalize_volumes([{"name": "a", "concentration": 10}], target_conc=5)

    def test_non_finite_target_raises(self):
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_normalize_volumes([], target_ng=float("inf"))

    def test_normalize_steps_diluent_then_sample(self):
        norm = ot2._ot2_normalize_volumes(
            [{"name": "a", "well": "A1", "concentration": 100.0}],
            target_conc=20.0, final_volume=50.0)
        steps = ot2._ot2_normalize_steps(norm, src_id="src", dst_id="dst",
                                         dst_wells=["A1", "A2"], diluent_ref="buf:A1")
        assert len(steps) == 2
        assert steps[0]["from"] == "buf:A1" and steps[0]["volume"] == 40.0   # diluent first
        assert steps[1]["from"] == "src:A1" and steps[1]["to"] == "dst:A1"
        assert steps[1]["volume"] == 10.0

    def test_normalize_steps_compile(self):
        norm = ot2._ot2_normalize_volumes(
            [{"name": "a", "well": "A1", "concentration": 100.0}], target_ng=200.0)
        steps = ot2._ot2_normalize_steps(norm, src_id="src", dst_id="dst", dst_wells=["A1"])
        plan = {"pipette": "p300_single", "tips": {"labware": "tiprack_300", "slot": 8},
                "labware": {"src": {"labware": "eppi_24", "slot": 1},
                            "dst": {"labware": "plate_24", "slot": 2}}, "steps": steps}
        assert "pipette.transfer(2.0" in ot2._ot2_compile_protocol(plan)

    def test_cherrypick_steps_skip_bad_wells(self):
        picks = [{"well": "A1"}, {"well": "B3"}, {"well": None}]
        steps = ot2._ot2_cherrypick_steps(picks, src_id="src", dst_id="dst",
                                          dst_wells=["A1", "A2"], volume=5)
        assert len(steps) == 2
        assert steps[0]["from"] == "src:A1" and steps[0]["to"] == "dst:A1"
        assert steps[1]["from"] == "src:B3" and steps[1]["to"] == "dst:A2"


# ── Resource pre-flight: source volumes + time estimate (2026-07-04) ────────────
class TestPreflight:
    def test_source_volumes_aggregate_steps(self):
        plan = {"pipette": "p300_single", "tips": {"labware": "tiprack_300", "slot": 8},
                "labware": {"src": {"labware": "eppi_24", "slot": 1},
                            "dst": {"labware": "plate_24", "slot": 2}},
                "steps": [{"type": "transfer", "from": "src:A1", "to": "dst:A1", "volume": 30},
                          {"type": "transfer", "from": "src:A1", "to": "dst:A2", "volume": 50},
                          {"type": "distribute", "from": "src:B1",
                           "to": ["dst:A1", "dst:A2"], "volume": 40}]}
        s = ot2._ot2_plan_summary(plan)
        assert s["source_volumes"]["src:A1"] == 80.0
        assert s["source_volumes"]["src:B1"] == 80.0   # 40 uL to each of 2 dests
        assert s["est_seconds"] > 0

    def test_source_volumes_legacy_transfers(self):
        s = ot2._ot2_plan_summary(_good_plan())
        assert s["source_volumes"] == {"src:A1": 50.0, "src:A2": 50.0}
        assert s["est_seconds"] > 0

    def test_mix_draws_nothing(self):
        plan = {"pipette": "p300_single", "tips": {"labware": "tiprack_300", "slot": 8},
                "labware": {"dst": {"labware": "plate_24", "slot": 2}},
                "steps": [{"type": "mix", "at": "dst:A1", "volume": 100, "repetitions": 3}]}
        assert ot2._ot2_plan_summary(plan)["source_volumes"] == {}


# ── New agent endpoints (2026-07-04) ────────────────────────────────────────────
class TestOT2NewAgentEndpoints:
    def _handlers(self):
        import splicecraft as sc
        return sc._AGENT_HANDLERS

    def test_registered_with_write_flags(self):
        H = self._handlers()
        assert "ot2-run-control" in H and H["ot2-run-control"][1] is True
        assert "ot2-normalize" in H and H["ot2-normalize"][1] is False
        assert "ot2-plate-map" in H and H["ot2-plate-map"][1] is False

    def test_run_control_needs_host_and_action(self):
        H = self._handlers()
        assert H["ot2-run-control"][0](None, {})[1] == 400
        assert H["ot2-run-control"][0](None, {"host": "1.2.3.4"})[1] == 400
        assert H["ot2-run-control"][0](None, {"host": "1.2.3.4", "action": "frob"})[1] == 400

    def test_run_control_no_active_run_409(self, monkeypatch):
        monkeypatch.setattr(ot2, "_ot2_active_run", lambda host: None)
        r = self._handlers()["ot2-run-control"][0](None, {"host": "1.2.3.4", "action": "pause"})
        assert r[1] == 409

    def test_run_control_sends_action(self, monkeypatch):
        monkeypatch.setattr(ot2, "_ot2_active_run", lambda host: "RUNZ")
        sent = {}
        monkeypatch.setattr(ot2, "_ot2_run_action",
                            lambda host, rid, action: sent.update(rid=rid, action=action) or {})
        r = self._handlers()["ot2-run-control"][0](None, {"host": "1.2.3.4", "action": "pause"})
        assert r["ok"] and r["run_id"] == "RUNZ" and sent["action"] == "pause"

    def test_normalize_endpoint_uses_pipette_floor(self):
        r = self._handlers()["ot2-normalize"][0](None, {
            "items": [{"name": "a", "well": "A1", "concentration": 1000.0}],
            "target_ng": 100.0, "pipette": "p300_single"})
        assert r["normalized"][0]["sample_ul"] == 30.0 and r["warnings"]

    def test_normalize_endpoint_emits_steps(self):
        r = self._handlers()["ot2-normalize"][0](None, {
            "items": [{"name": "a", "well": "A1", "concentration": 100.0}],
            "target_ng": 200.0, "src": "src", "dst": "dst", "dst_labware": "plate_96"})
        assert "steps" in r and r["steps"][0]["from"] == "src:A1"

    def test_normalize_endpoint_guards(self):
        H = self._handlers()
        assert H["ot2-normalize"][0](None, {})[1] == 400   # no items
        assert H["ot2-normalize"][0](None,
                                     {"items": [{"name": "a", "concentration": 10}]})[1] == 400

    def test_plate_map_endpoint(self):
        import splicecraft as sc
        colls = sc._load_collections()
        colls.append({"name": "OT2MAP", "plasmids": [
            {"id": "id1", "name": "pOne"}, {"id": "id2", "name": "pTwo"}]})
        sc._save_collections(colls)
        r = self._handlers()["ot2-plate-map"][0](None,
                                                  {"collection": "OT2MAP", "labware": "eppi_24"})
        assert r["ok"] and r["map"]["A1"] == {"id": "id1", "name": "pOne"}
        assert r["map"]["A2"]["name"] == "pTwo" and r["n"] == 2 and r["overflow"] == 0

    def test_plate_map_guards(self):
        H = self._handlers()
        assert H["ot2-plate-map"][0](None, {"collection": "NoSuchColl", "labware": "eppi_24"})[1] == 404
        import splicecraft as sc
        colls = sc._load_collections()
        if not any(c.get("name") == "OT2MAP2" for c in colls):
            colls.append({"name": "OT2MAP2", "plasmids": [{"id": "i", "name": "p"}]})
            sc._save_collections(colls)
        assert H["ot2-plate-map"][0](None, {"collection": "OT2MAP2", "labware": "bogus"})[1] == 400


# ── AUTOLAB run-control buttons + live progress (2026-07-04) ─────────────────────
class TestAutolabRunControlUI:
    async def test_buttons_dispatch_and_progress(self, monkeypatch):
        import splicecraft as sc
        from textual.widgets import Button, Static, Input
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            for bid in ("autolab-pause", "autolab-resume", "autolab-abort"):
                assert scr.query_one(f"#{bid}", Button) is not None
            calls = []
            monkeypatch.setattr(scr, "_worker_run_control",
                                lambda host, action: calls.append((host, action)))
            scr._on_run_control("pause")            # no host -> warn, no dispatch
            assert calls == []
            scr.query_one("#autolab-host", Input).value = "1.2.3.4"
            scr._on_run_control("stop")
            assert calls == [("1.2.3.4", "stop")]
            # a live run snapshot captures the run id + renders the progress line
            scr._render_state({"reachable": True, "health": {"name": "X"}, "ok": True,
                               "run": {"id": "RUN5", "status": "running", "command_count": 3,
                                       "current_command": {"commandType": "aspirate",
                                                           "at": {"wellName": "A1"}}}})
            assert scr._active_run_id == "RUN5"
            assert scr.query_one("#autolab-run-progress", Static) is not None
            body = scr._run_progress_text
            assert "RUN" in body and "aspirate" in body and "A1" in body
            scr._run_done({"ran": True, "run_status": "succeeded", "crashed": False})
            assert scr._active_run_id is None


# ── AUTOLAB Library tab: bind / cherry-pick / normalise / provenance ────────────
class TestAutolabLibrary:
    async def test_bind_and_cherry_pick(self):
        import splicecraft as sc
        from textual.widgets import Select
        colls = sc._load_collections()
        colls.append({"name": "AUTOLABCOLL", "plasmids": [
            {"id": "p1", "name": "pOne", "size": 100, "n_feats": 0, "gb_text": "", "source": ""},
            {"id": "p2", "name": "pTwo", "size": 100, "n_feats": 0, "gb_text": "", "source": ""}]})
        sc._save_collections(colls)
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            scr._refresh_library_tab()
            scr.query_one("#autolab-lib-coll", Select).value = "AUTOLABCOLL"
            scr.query_one("#autolab-lib-slot", Select).value = "1"   # eppi_24 'src'
            scr._bind_collection()
            assert scr._bound_slot == 1
            assert scr._deck[1]["map"]["A1"]["id"] == "p1"
            assert scr._deck[1]["collection"] == "AUTOLABCOLL"
            scr.query_one("#autolab-lib-dst", Select).value = "2"    # plate_24 'dst'
            n0 = len(scr._steps)
            scr._cherry_pick()
            assert len(scr._steps) == n0 + 2
            assert scr._steps[-2]["from"] == "src:A1" and scr._steps[-2]["to"] == "dst:A1"
            assert scr._steps[-1]["from"] == "src:A2"
            # the identity map round-trips through the compiled plan metadata
            plan = ot2._ot2_normalize_plan(scr._build_plan())
            assert plan["labware"]["src"]["collection"] == "AUTOLABCOLL"

    async def test_normalise_and_provenance(self):
        import splicecraft as sc
        from textual.widgets import Select, Input, TextArea
        colls = sc._load_collections()
        colls.append({"name": "NORMCOLL", "plasmids": [
            {"id": "n1", "name": "pHi", "size": 100, "n_feats": 0, "gb_text": "", "source": ""},
            {"id": "n2", "name": "pLo", "size": 100, "n_feats": 0, "gb_text": "", "source": ""}]})
        sc._save_collections(colls)
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            scr._refresh_library_tab()
            scr.query_one("#autolab-lib-coll", Select).value = "NORMCOLL"
            scr.query_one("#autolab-lib-slot", Select).value = "1"
            scr._bind_collection()
            scr.query_one("#autolab-lib-dst", Select).value = "2"
            scr.query_one("#autolab-lib-conc", TextArea).text = "A1 = 100\nA2 = 50"
            scr.query_one("#autolab-lib-mode", Select).value = "ng"
            scr.query_one("#autolab-lib-target", Input).value = "200"
            scr._normalize_build()
            assert any(s.get("from") == "src:A1" for s in scr._steps)
            n_before = len(sc._load_experiments())
            scr.query_one("#autolab-lib-title", Input).value = "My OT-2 build"
            scr._log_to_notebook()
            entries = sc._load_experiments()
            assert len(entries) == n_before + 1
            logged = [e for e in entries if e.get("title") == "My OT-2 build"]
            assert logged and "@n1" in logged[0]["body_md"]

    async def test_bind_guards_no_selection(self):
        import splicecraft as sc
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            scr._bind_collection()           # nothing selected -> no crash, no bind
            assert scr._bound_slot is None
            scr._cherry_pick()               # no bound plate -> no crash, no steps
            assert scr._steps == []


# ── Hardening sweep #3 (2026-07-04): audit of the run-control/identity/normalise batch ──
class TestHardeningSweep3:
    def test_normalize_overfill_final_volume_warns(self):
        # target_ng + final_volume, stock too dilute -> sample > final -> diluent 0 + warn
        r = ot2._ot2_normalize_volumes([{"name": "a", "well": "A1", "concentration": 50.0}],
                                       target_ng=3000.0, final_volume=20.0, max_vol=300.0)
        assert r[0]["sample_ul"] == 60.0 and r[0]["diluent_ul"] == 0.0
        assert r[0]["warning"] and "final volume" in r[0]["warning"]

    def test_parse_well_rejects_superscript_no_crash(self):
        assert ot2._ot2_parse_well("A²") is None      # ² is isdigit-true, isdecimal-false
        # a custom def with such a key no longer crashes the well ordering
        wells = ot2._ot2_entry_wells({"definition": {"wells": {"A²": {}, "A1": {}}}})
        assert "A1" in wells

    def test_normalize_item_cap(self):
        many = [{"name": str(i), "well": "A1", "concentration": 100.0}
                for i in range(ot2._OT2_MAX_ITEMS + 1)]
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_normalize_volumes(many, target_ng=200.0)

    def test_normalize_validates_min_vol_resolution(self):
        base = [{"name": "a", "well": "A1", "concentration": 100.0}]
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_normalize_volumes(base, target_ng=100.0, resolution=float("inf"))
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_normalize_volumes(base, target_ng=100.0, min_vol=float("inf"))
        with pytest.raises(ot2.OT2Error):    # inverted min/max
            ot2._ot2_normalize_volumes(base, target_ng=100.0, min_vol=50.0, max_vol=30.0)

    def test_run_id_sanitised(self):
        assert ot2._ot2_valid_run_id("3f2a-9b_1.2") == "3f2a-9b_1.2"
        for bad in ("x/y", "../etc", "a b", "z" * 200, ""):
            with pytest.raises(ot2.OT2Error):
                ot2._ot2_valid_run_id(bad)
        with pytest.raises(ot2.OT2Error):     # rejected before any request is built
            ot2._ot2_run_action("h", "../../danger", "pause")

    def test_agent_normalize_warns_missing_diluent(self):
        import splicecraft as sc
        H = sc._AGENT_HANDLERS
        body = {"items": [{"name": "a", "well": "A1", "concentration": 100.0}],
                "target_conc": 20.0, "final_volume": 50.0,
                "src": "src", "dst": "dst", "dst_labware": "plate_96"}
        r = H["ot2-normalize"][0](None, body)
        assert any("diluent" in w.lower() for w in r["warnings"])
        r2 = H["ot2-normalize"][0](None, {**body, "diluent_ref": "buf:A1"})
        assert not any("no 'diluent_ref'" in w for w in r2["warnings"])


class TestAutolabLibraryHardening:
    async def test_normalize_refuses_missing_diluent(self):
        import splicecraft as sc
        from textual.widgets import Select, Input, TextArea
        colls = sc._load_collections()
        colls.append({"name": "DILCOLL", "plasmids": [
            {"id": "d1", "name": "pD", "size": 100, "n_feats": 0, "gb_text": "", "source": ""}]})
        sc._save_collections(colls)
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            scr._refresh_library_tab()
            scr.query_one("#autolab-lib-coll", Select).value = "DILCOLL"
            scr.query_one("#autolab-lib-slot", Select).value = "1"
            scr._bind_collection()
            scr.query_one("#autolab-lib-dst", Select).value = "2"
            scr.query_one("#autolab-lib-conc", TextArea).text = "A1 = 100"
            scr.query_one("#autolab-lib-mode", Select).value = "conc"
            scr.query_one("#autolab-lib-target", Input).value = "20"
            scr.query_one("#autolab-lib-final", Input).value = "50"
            n0 = len(scr._steps)
            scr._normalize_build()             # no diluent well set -> refuse
            assert len(scr._steps) == n0
            scr.query_one("#autolab-lib-diluent", Input).value = "buf:A1"
            scr._normalize_build()             # now it builds
            assert len(scr._steps) > n0

    async def test_bound_slot_restored_on_load(self):
        import splicecraft as sc
        from textual.widgets import Select
        colls = sc._load_collections()
        colls.append({"name": "LOADCOLL", "plasmids": [
            {"id": "l1", "name": "pL", "size": 100, "n_feats": 0, "gb_text": "", "source": ""}]})
        sc._save_collections(colls)
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            scr._refresh_library_tab()
            scr.query_one("#autolab-lib-coll", Select).value = "LOADCOLL"
            scr.query_one("#autolab-lib-slot", Select).value = "1"
            scr._bind_collection()
            assert scr._bound_slot == 1
            plan = scr._build_plan()
            scr._bound_slot = None             # simulate a reload losing the pointer
            scr._deck = {8: {"kind": "tips", "labware": "tiprack_300"}}
            scr._apply_plan(plan)
            assert scr._bound_slot is not None
            assert scr._deck[scr._bound_slot].get("collection") == "LOADCOLL"


# ── Calibration + safe gantry motion (2026-07-04) ───────────────────────────────
class TestCalibrationMotion:
    def test_home_payload(self, monkeypatch):
        sent = {}
        monkeypatch.setattr(ot2, "_ot2_request_json",
                            lambda host, path, *, method="GET", payload=None, **kw:
                            sent.update(path=path, method=method, payload=payload) or {"data": {}})
        ot2._ot2_home("h")
        assert sent["path"] == "/robot/home" and sent["method"] == "POST"
        assert sent["payload"] == {"target": "robot"}

    def test_state_reports_pipette_calibration(self, monkeypatch):
        _fake_transport(monkeypatch)     # left pipette HAS an offset calibration
        st = ot2._ot2_state("h")
        assert st["calibration"]["pipettes_calibrated"] == {"left": True}
        assert st["faults"] == []        # a missing pipette cal is NOT a hard fault

    def test_state_uncalibrated_pipette_not_a_fault(self, monkeypatch):
        def fake(host, path, **kw):
            if path == "/health":
                return {"name": "F"}
            if path == "/instruments":
                return {"data": [{"mount": "left", "instrumentModel": "p300_single",
                                  "ok": True, "data": {}}]}
            if path == "/calibration/pipette_offset":
                return {"data": []}      # none calibrated
            return {"data": []}
        monkeypatch.setattr(ot2, "_ot2_request_json", fake)
        st = ot2._ot2_state("h")
        assert st["calibration"]["pipettes_calibrated"] == {"left": False}
        assert st["ok"] is True          # still safe to POSITION-check

    def test_position_check_compile_move_only(self):
        plan = {"pipette": "p300_single", "tips": {"labware": "tiprack_300", "slot": 8},
                "labware": {"src": {"labware": "eppi_24", "slot": 1}}}
        proto = ot2._ot2_compile_position_check(plan)
        compile(proto, "<gen>", "exec")
        assert "move_to" in proto and ".top()" in proto
        for banned in ("aspirate", "dispense", "pick_up_tip", "transfer(", "mix("):
            assert banned not in proto

    def test_position_check_needs_labware(self):
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_compile_position_check({"pipette": "p300_single"})

    def test_labware_offsets_from_analysis(self):
        plan = {"labware": {"src": {"labware": "eppi_24", "slot": 1,
                                    "offset": {"x": 1.0, "y": 2.0, "z": 0.5}}}}
        offs = ot2._ot2_labware_offsets(
            [{"definitionUri": "opentrons/eppi/1", "location": {"slotName": "1"}}], plan)
        assert offs == [{"definitionUri": "opentrons/eppi/1", "location": {"slotName": "1"},
                         "vector": {"x": 1.0, "y": 2.0, "z": 0.5}}]

    def _ok_analysis(self, monkeypatch, labware=None):
        monkeypatch.setattr(ot2, "_ot2_analyze", lambda host, txt, **kw: {
            "result": "ok", "protocol_id": "P", "analysis_id": "A", "status": "completed",
            "errors": [], "commands": [], "pipettes": [], "labware": labware or []})

    def _run_succeeds_state(self, monkeypatch, calibrated):
        monkeypatch.setattr(ot2, "_ot2_state", lambda host, **kw: {
            "reachable": True, "faults": [], "ok": True,
            "calibration": {"pipettes_calibrated": {"left": calibrated}},
            "run": {"status": "succeeded", "id": "R", "current_command": None,
                    "command_count": 1, "failed_commands": [], "errors": []}})

    def test_run_blocks_uncalibrated_pipette(self, monkeypatch):
        self._ok_analysis(monkeypatch)
        monkeypatch.setattr(ot2, "_ot2_state", lambda host, **kw: {
            "reachable": True, "faults": [], "ok": True,
            "calibration": {"pipettes_calibrated": {"left": False}}})
        res = ot2._ot2_run_protocol("h", "print(1)", confirm=True)
        assert res["ran"] is False and res["reason"] == "pipette-not-calibrated"

    def test_run_applies_labware_offsets(self, monkeypatch):
        self._ok_analysis(monkeypatch, labware=[
            {"definitionUri": "opentrons/eppi/1", "location": {"slotName": "1"}}])
        self._run_succeeds_state(monkeypatch, calibrated=True)
        sent = {}
        def fake_json(host, path, *, method="GET", payload=None, **kw):
            if path == "/runs":
                sent["payload"] = payload
            return {"data": {"id": "R"}}
        monkeypatch.setattr(ot2, "_ot2_request_json", fake_json)
        plan = {"pipette": "p300_single", "tips": {"labware": "tiprack_300", "slot": 8},
                "labware": {"src": {"labware": "eppi_24", "slot": 1,
                                    "offset": {"x": 1.0, "y": 2.0, "z": 0.5}},
                            "dst": {"labware": "plate_24", "slot": 2}},
                "transfers": [{"from": "src:A1", "to": "dst:A1", "volume": 50}]}
        proto = ot2._ot2_compile_protocol(plan)
        res = ot2._ot2_run_protocol("h", proto, confirm=True, offset_plan=plan)
        assert res["ran"] is True
        assert sent["payload"]["data"]["labwareOffsets"][0]["vector"] == {"x": 1.0, "y": 2.0, "z": 0.5}

    def test_position_check_run_allows_uncalibrated(self, monkeypatch):
        self._ok_analysis(monkeypatch)
        self._run_succeeds_state(monkeypatch, calibrated=False)   # uncalibrated pipette
        monkeypatch.setattr(ot2, "_ot2_request_json",
                            lambda host, path, *, method="GET", payload=None, **kw:
                            {"data": {"id": "R"}})
        plan = {"pipette": "p300_single", "tips": {"labware": "tiprack_300", "slot": 8},
                "labware": {"src": {"labware": "eppi_24", "slot": 1}}}
        res = ot2._ot2_run_position_check("h", plan, confirm=True)
        assert res["ran"] is True and res["position_check"] is True


class TestCalibrationAgentEndpoints:
    def _h(self):
        import splicecraft as sc
        return sc._AGENT_HANDLERS

    def test_registered_with_write_flags(self):
        H = self._h()
        assert H["ot2-calibration"][1] is False
        assert H["ot2-home"][1] is True
        assert H["ot2-position-check"][1] is True

    def test_calibration_endpoint(self, monkeypatch):
        monkeypatch.setattr(ot2, "_ot2_state", lambda host, **kw: {
            "reachable": True, "faults": [], "ok": True, "instruments": [],
            "calibration": {"deck_status": "OK", "marked_bad": False,
                            "pipettes_calibrated": {"left": False}, "tip_lengths": 0}})
        r = self._h()["ot2-calibration"][0](None, {"host": "1.2.3.4"})
        assert r["ready"] is False and r["needs_calibration"] == ["left"] and r["deck_ok"] is True

    def test_home_endpoint(self, monkeypatch):
        monkeypatch.setattr(ot2, "_ot2_home", lambda host: {"data": {}})
        assert self._h()["ot2-home"][0](None, {"host": "1.2.3.4"})["homed"]
        assert self._h()["ot2-home"][0](None, {})[1] == 400

    def test_position_check_confirm_gate(self, monkeypatch):
        monkeypatch.setattr(ot2, "_ot2_analyze", lambda host, txt, **kw: {
            "result": "ok", "protocol_id": "P", "analysis_id": "A", "status": "completed",
            "errors": [], "commands": [], "pipettes": [], "labware": []})
        body = {"host": "1.2.3.4", "pipette": "p300_single",
                "tips": {"labware": "tiprack_300", "slot": 8},
                "labware": {"src": {"labware": "eppi_24", "slot": 1}}}
        r = self._h()["ot2-position-check"][0](None, body)
        assert r["ran"] is False and r["reason"] == "confirm-required"

    def test_position_check_needs_labware(self):
        r = self._h()["ot2-position-check"][0](None, {"host": "1.2.3.4", "pipette": "p300_single"})
        assert isinstance(r, tuple) and r[1] == 400


class TestAutolabCalibrateUI:
    async def test_offsets_set_clear_and_roundtrip(self):
        import splicecraft as sc
        from textual.widgets import Select, Input, Static
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            scr._refresh_calib_tab()
            scr.query_one("#autolab-off-slot", Select).value = "1"   # eppi_24 'src'
            scr.query_one("#autolab-off-x", Input).value = "1.5"
            scr.query_one("#autolab-off-y", Input).value = "-0.5"
            scr.query_one("#autolab-off-z", Input).value = "0.2"
            scr._set_offset()
            assert scr._deck[1]["offset"] == {"x": 1.5, "y": -0.5, "z": 0.2}
            plan = scr._build_plan()
            assert plan["labware"]["src"]["offset"] == {"x": 1.5, "y": -0.5, "z": 0.2}
            assert sc.AutolabScreen._deck_from_plan(plan)[1]["offset"]["x"] == 1.5
            scr._clear_offset()
            assert "offset" not in scr._deck[1]
            scr._render_calibration({"reachable": True, "calibration": {
                "deck_status": "OK", "pipettes_calibrated": {"left": False}, "tip_lengths": 0}})
            assert scr.query_one("#autolab-calib-status", Static) is not None

    async def test_position_check_needs_arm(self):
        import splicecraft as sc
        from textual.widgets import Input
        called = []
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            scr.query_one("#autolab-host", Input).value = "1.2.3.4"
            scr._worker_position_check = lambda host, plan: called.append(host)  # type: ignore[method-assign]
            scr._on_position_check()            # not armed -> no dispatch
            assert called == []


# ── Calibration/motion hardening sweep (audit of the batch, 2026-07-04) ──────────
class TestCalibrationMotionHardening:
    def test_offset_magnitude_cap_rejects(self):
        # |z| beyond the safety cap RAISES rather than driving a descent below top
        plan = {"labware": {"src": {"labware": "eppi_24", "slot": 1,
                                    "offset": {"x": 0, "y": 0, "z": -40}}}}
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_labware_offsets([{"definitionUri": "u", "location": {"slotName": "1"}}], plan)
        plan["labware"]["src"]["offset"] = {"x": 1.0, "y": -0.5, "z": 0.5}    # in-range: fine
        offs = ot2._ot2_labware_offsets([{"definitionUri": "u", "location": {"slotName": "1"}}], plan)
        assert offs[0]["vector"] == {"x": 1.0, "y": -0.5, "z": 0.5}

    def test_offset_skips_non_dict_analysis_items(self):
        plan = {"labware": {"src": {"labware": "eppi_24", "slot": 1,
                                    "offset": {"x": 1.0, "y": 0, "z": 0}}}}
        offs = ot2._ot2_labware_offsets(
            ["garbage", None, {"definitionUri": "u", "location": {"slotName": "1"}}], plan)
        assert len(offs) == 1

    def test_position_check_rejects_bad_slot(self):
        for bad in ([1, 2], "1; drop", 99, True, None):
            with pytest.raises(ot2.OT2Error):
                ot2._ot2_compile_position_check(
                    {"pipette": "p300_single", "labware": {"x": {"labware": "eppi_24", "slot": bad}}})

    def test_position_check_wells_deduped_and_capped(self):
        plan = {"pipette": "p300_single", "labware": {"x": {"labware": "eppi_24", "slot": 1}}}
        assert ot2._ot2_compile_position_check(plan, wells=["A1", "A1", "A1"]).count("move_to") == 1
        big = ["A1"] * (ot2._OT2_MAX_POSCHECK_WELLS + 500)
        assert "move_to" in ot2._ot2_compile_position_check(plan, wells=big)   # no O(n^2) hang

    def test_run_fails_closed_on_unknown_calibration(self, monkeypatch):
        monkeypatch.setattr(ot2, "_ot2_analyze", lambda host, txt, **kw: {
            "result": "ok", "protocol_id": "P", "analysis_id": "A", "status": "completed",
            "errors": [], "commands": [], "pipettes": [], "labware": []})
        monkeypatch.setattr(ot2, "_ot2_state", lambda host, **kw: {
            "reachable": True, "faults": [], "ok": True,
            "calibration": {"pipettes_calibrated": {}}})     # couldn't read instruments
        res = ot2._ot2_run_protocol("h", "print(1)", confirm=True)
        assert res["ran"] is False and res["reason"] == "calibration-unknown"

    def test_base_url_malformed_host_raises_ot2error(self):
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_base_url("http://[")     # ValueError from urlsplit -> OT2Error, not a 500

    def test_agent_calibration_strict_deck(self, monkeypatch):
        import splicecraft as sc
        for deck, ready in (("OK", True), ("IDENTITY", False), (None, False)):
            monkeypatch.setattr(ot2, "_ot2_state", lambda host, _d=deck, **kw: {
                "reachable": True, "faults": [], "ok": True, "instruments": [],
                "calibration": {"deck_status": _d, "marked_bad": False,
                                "pipettes_calibrated": {"left": True}, "tip_lengths": 1}})
            r = sc._AGENT_HANDLERS["ot2-calibration"][0](None, {"host": "1.2.3.4"})
            assert r["deck_ok"] is ready and r["ready"] is ready

    def test_agent_home_blocks_during_run(self, monkeypatch):
        import splicecraft as sc
        monkeypatch.setattr(ot2, "_ot2_active_run", lambda host: "RUN1")
        r = sc._AGENT_HANDLERS["ot2-home"][0](None, {"host": "1.2.3.4"})
        assert isinstance(r, tuple) and r[1] == 409

    def test_agent_position_check_no_slot_400(self):
        import splicecraft as sc
        r = sc._AGENT_HANDLERS["ot2-position-check"][0](None, {
            "host": "1.2.3.4", "pipette": "p300_single",
            "labware": {"src": {"labware": "eppi_24"}}})     # labware but NO slot
        assert isinstance(r, tuple) and r[1] == 400

    async def test_ui_set_offset_rejects_out_of_range(self):
        import splicecraft as sc
        from textual.widgets import Select, Input
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            scr._refresh_calib_tab()
            scr.query_one("#autolab-off-slot", Select).value = "1"
            scr.query_one("#autolab-off-z", Input).value = "-40"
            scr._set_offset()
            assert "offset" not in scr._deck[1]              # rejected, not stored


# ── New: motor disengage + door telemetry + extra run-gate interlocks ────────────
class TestMotorDisengage:
    def test_disengage_builds_request(self, monkeypatch):
        sent = {}
        def fake_json(host, path, *, method="GET", payload=None, **kw):
            sent.update(path=path, method=method, payload=payload)
            return {"ok": True}
        monkeypatch.setattr(ot2, "_ot2_request_json", fake_json)
        ot2._ot2_disengage("h")
        assert sent["path"] == "/motors/disengage" and sent["method"] == "POST"
        assert sent["payload"] == {"axes": list(ot2._OT2_ALL_AXES)}

    def test_disengage_custom_axes_normalised(self, monkeypatch):
        sent = {}
        monkeypatch.setattr(ot2, "_ot2_request_json",
                            lambda host, path, *, method="GET", payload=None, **kw:
                            sent.update(payload=payload) or {})
        ot2._ot2_disengage("h", axes=["X", " y "])
        assert sent["payload"] == {"axes": ["x", "y"]}

    def test_disengage_empty_axes_falls_back_to_all(self, monkeypatch):
        sent = {}
        monkeypatch.setattr(ot2, "_ot2_request_json",
                            lambda host, path, *, method="GET", payload=None, **kw:
                            sent.update(payload=payload) or {})
        ot2._ot2_disengage("h", axes=[])
        assert sent["payload"] == {"axes": list(ot2._OT2_ALL_AXES)}


class TestDoorTelemetry:
    def test_state_surfaces_door(self, monkeypatch):
        _fake_transport(monkeypatch)
        st = ot2._ot2_state("h")
        assert st["door"] == {"status": "closed", "required_closed": False}

    def test_door_open_required_closed(self, monkeypatch):
        base = {
            "/health": {"name": "F"}, "/instruments": {"data": []},
            "/motors/engaged": {}, "/robot/lights": {"on": False},
            "/robot/door/status": {"data": {"status": "open",
                                            "doorRequiredClosedForProtocol": True}},
            "/calibration/status": {"deckCalibration": {"status": "OK"}},
            "/modules": {"data": []}, "/settings": {"settings": []}, "/runs": {"data": []},
            "/calibration/pipette_offset": {"data": []}, "/calibration/tip_length": {"data": []},
        }
        def fake(host, path, **kw):
            if path in base:
                return base[path]
            raise AssertionError(path)
        monkeypatch.setattr(ot2, "_ot2_request_json", fake)
        st = ot2._ot2_state("h")
        assert st["door"] == {"status": "open", "required_closed": True}


class TestPipetteMatch:
    def test_base_strips_version_and_gen(self):
        assert ot2._ot2_pipette_base("p300_single_v1.5") == "p300_single"
        assert ot2._ot2_pipette_base("p20_single_gen2") == "p20_single"
        assert ot2._ot2_pipette_base("p1000_single") == "p1000_single"
        assert ot2._ot2_pipette_base(None) == ""

    def test_no_mismatch_when_matched(self):
        assert ot2._ot2_pipette_mismatch(
            [{"mount": "left", "pipetteName": "p300_single"}],
            [{"mount": "left", "model": "p300_single_v1.5"}]) == []

    def test_wrong_model_flagged(self):
        m = ot2._ot2_pipette_mismatch(
            [{"mount": "left", "pipetteName": "p300_single"}],
            [{"mount": "left", "model": "p20_single_gen2"}])
        assert len(m) == 1 and "p20_single is attached" in m[0]

    def test_absent_mount_flagged(self):
        m = ot2._ot2_pipette_mismatch(
            [{"mount": "right", "pipetteName": "p300_single"}],
            [{"mount": "left", "model": "p300_single_v1.5"}])
        assert len(m) == 1 and "nothing is attached" in m[0]

    def test_junk_items_ignored(self):
        assert ot2._ot2_pipette_mismatch([None, {"mount": "left"}], ["nope", 5]) == []


class TestRunGateInterlocks:
    def _ok_analysis(self, monkeypatch, pipettes=None):
        monkeypatch.setattr(ot2, "_ot2_analyze", lambda host, txt, **kw: {
            "result": "ok", "protocol_id": "P", "analysis_id": "A", "status": "completed",
            "errors": [], "commands": [], "pipettes": pipettes or [], "labware": []})

    def _succeeds_state(self, monkeypatch, **extra):
        st = {"reachable": True, "faults": [], "ok": True, "door": {}, "instruments": [],
              "lights": False, "calibration": {"pipettes_calibrated": {"left": True}},
              "run": {"status": "succeeded", "failed_commands": [], "errors": []}}
        st.update(extra)
        monkeypatch.setattr(ot2, "_ot2_state", lambda host, **kw: st)
        monkeypatch.setattr(ot2, "_ot2_request_json",
                            lambda host, path, *, method="GET", payload=None, **kw:
                            {"data": {"id": "R"}})

    def test_gate_blocks_door_open(self, monkeypatch):
        self._ok_analysis(monkeypatch)
        monkeypatch.setattr(ot2, "_ot2_state", lambda host, **kw: {
            "reachable": True, "faults": [], "ok": True,
            "door": {"status": "open", "required_closed": True},
            "calibration": {"pipettes_calibrated": {"left": True}}})
        res = ot2._ot2_run_protocol("h", "print(1)", confirm=True)
        assert res["ran"] is False and res["reason"] == "door-open"

    def test_gate_allows_open_door_when_switch_disabled(self, monkeypatch):
        self._ok_analysis(monkeypatch)
        self._succeeds_state(monkeypatch, door={"status": "open", "required_closed": False})
        monkeypatch.setattr(ot2, "_ot2_set_lights", lambda host, on: {"on": on})
        res = ot2._ot2_run_protocol("h", "print(1)", confirm=True)
        assert res["ran"] is True and res.get("crashed") is False

    def test_gate_blocks_pipette_mismatch(self, monkeypatch):
        self._ok_analysis(monkeypatch,
                          pipettes=[{"mount": "left", "pipetteName": "p300_single"}])
        monkeypatch.setattr(ot2, "_ot2_state", lambda host, **kw: {
            "reachable": True, "faults": [], "ok": True, "door": {},
            "instruments": [{"mount": "left", "model": "p20_single_gen2"}],
            "calibration": {"pipettes_calibrated": {"left": True}}})
        res = ot2._ot2_run_protocol("h", "print(1)", confirm=True)
        assert res["ran"] is False and res["reason"] == "pipette-mismatch"
        assert "p20_single" in res["detail"]

    def test_run_turns_lights_on_and_restores(self, monkeypatch):
        self._ok_analysis(monkeypatch)
        self._succeeds_state(monkeypatch, lights=False)
        lights = []
        monkeypatch.setattr(ot2, "_ot2_set_lights",
                            lambda host, on: lights.append(on) or {"on": on})
        res = ot2._ot2_run_protocol("h", "print(1)", confirm=True)
        assert res["run_status"] == "succeeded"
        assert lights == [True, False]        # on at start, restored (off) at end

    def test_run_lights_indicator_can_be_disabled(self, monkeypatch):
        self._ok_analysis(monkeypatch)
        self._succeeds_state(monkeypatch, lights=True)
        lights = []
        monkeypatch.setattr(ot2, "_ot2_set_lights",
                            lambda host, on: lights.append(on) or {"on": on})
        ot2._ot2_run_protocol("h", "print(1)", confirm=True, indicator_lights=False)
        assert lights == []

    def test_timeout_stops_the_run(self, monkeypatch):
        self._ok_analysis(monkeypatch)
        monkeypatch.setattr(ot2, "_ot2_state", lambda host, **kw: {
            "reachable": True, "faults": [], "ok": True, "door": {}, "instruments": [],
            "lights": False, "calibration": {"pipettes_calibrated": {"left": True}},
            "run": {"status": "running", "failed_commands": [], "errors": []}})
        monkeypatch.setattr(ot2, "_ot2_request_json",
                            lambda host, path, *, method="GET", payload=None, **kw:
                            {"data": {"id": "R"}})
        monkeypatch.setattr(ot2, "_ot2_set_lights", lambda host, on: {"on": on})
        stopped = []
        monkeypatch.setattr(ot2, "_ot2_stop_run", lambda host, rid: stopped.append(rid))
        monkeypatch.setattr(ot2, "_OT2_RUN_POLL_TIMEOUT", -1)   # already past deadline
        with pytest.raises(ot2.OT2Error):
            ot2._ot2_run_protocol("h", "print(1)", confirm=True)
        assert stopped == ["R"]               # SAFETY: run halted, not left moving


class TestOT2ControlEndpoints:
    def _H(self):
        import splicecraft as sc
        return sc._AGENT_HANDLERS

    def test_lights_and_disengage_registered_write(self):
        H = self._H()
        for n in ("ot2-lights", "ot2-disengage"):
            assert n in H and H[n][1] is True

    def test_lights_requires_host(self):
        r = self._H()["ot2-lights"][0](None, {})
        assert isinstance(r, tuple) and r[1] == 400

    def test_lights_toggles(self, monkeypatch):
        sent = {}
        monkeypatch.setattr(ot2, "_ot2_set_lights",
                            lambda host, on: sent.update(host=host, on=on) or {"on": on})
        r = self._H()["ot2-lights"][0](None, {"host": "1.2.3.4", "on": False})
        assert r == {"ok": True, "on": False}
        assert sent == {"host": "1.2.3.4", "on": False}

    def test_lights_default_on(self, monkeypatch):
        monkeypatch.setattr(ot2, "_ot2_set_lights", lambda host, on: {"on": on})
        assert self._H()["ot2-lights"][0](None, {"host": "h"})["on"] is True

    def test_disengage_blocked_during_run(self, monkeypatch):
        monkeypatch.setattr(ot2, "_ot2_active_run", lambda host: "R1")
        r = self._H()["ot2-disengage"][0](None, {"host": "h"})
        assert isinstance(r, tuple) and r[1] == 409

    def test_disengage_calls_engine(self, monkeypatch):
        monkeypatch.setattr(ot2, "_ot2_active_run", lambda host: None)
        sent = {}
        monkeypatch.setattr(ot2, "_ot2_disengage",
                            lambda host, *, axes=None: sent.update(host=host, axes=axes))
        r = self._H()["ot2-disengage"][0](None, {"host": "h", "axes": ["x", "y"]})
        assert r["ok"] and r["disengaged"] == ["x", "y"] and sent["axes"] == ["x", "y"]


# ── AUTOLAB live telemetry visualization ────────────────────────────────────────
class TestAutolabTelemetry:
    async def test_new_widgets_present(self):
        import splicecraft as sc
        from textual.widgets import Static, ProgressBar, Button
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            for wid, cls in (("#autolab-conn", Static), ("#autolab-crash-banner", Static),
                             ("#autolab-progress-bar", ProgressBar),
                             ("#autolab-status-panel", Static),
                             ("#autolab-lights-on", Button), ("#autolab-lights-off", Button),
                             ("#autolab-disengage", Button)):
                assert scr.query_one(wid, cls) is not None, wid

    async def test_status_panel_renders_telemetry(self):
        import splicecraft as sc
        from textual.widgets import Static
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            snap = {"reachable": True, "ok": True,
                    "health": {"name": "Jacques", "fw_version": "v1.1", "api_version": "26"},
                    "lights": True, "door": {"status": "open", "required_closed": True},
                    "motors": {"x": {"enabled": True}},
                    "instruments": [{"mount": "left", "model": "p300_single", "ok": True,
                                     "min_volume": 30, "max_volume": 300}],
                    "calibration": {"deck_status": "OK"}}
            scr._render_status_panel(snap)
            body = str(scr.query_one("#autolab-status-panel", Static).render())
            assert "Jacques" in body and "lights on" in body
            assert "door OPEN" in body and "p300_single" in body
            assert "motors engaged" in body and "deck cal: OK" in body
            scr._set_conn_badge(True, "Jacques", "v1.1")
            assert "Jacques" in str(scr.query_one("#autolab-conn", Static).render())

    async def test_log_not_flooded_by_repeated_ticks(self):
        import splicecraft as sc
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            scr._reset_run_telemetry()
            scr._log_lines = []
            snap = {"reachable": True, "ok": True, "health": {"name": "J"},
                    "run": {"id": "R", "status": "running", "command_total": 5,
                            "current_command": {"commandType": "aspirate",
                                                "at": {"wellName": "A1"}}}}
            for _ in range(10):
                scr._render_state(snap)
            # only the single running-transition line was logged, not 10 full dumps
            assert len(scr._log_lines) <= 2

    async def test_crash_banner_toggles(self):
        import splicecraft as sc
        from textual.widgets import Static
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            banner = scr.query_one("#autolab-crash-banner", Static)
            scr._set_crash_banner(["pipette overpressure", "x", "y"])
            assert banner.has_class("shown")
            body = str(banner.render())
            assert "overpressure" in body and "+2 more" in body
            scr._set_crash_banner([])
            assert not banner.has_class("shown")

    async def test_progress_bar_determinate_when_total_known(self):
        import splicecraft as sc
        from textual.widgets import ProgressBar
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            scr._analysis_total = 20
            scr._update_progress_bar({"status": "running", "command_total": 8})
            bar = scr.query_one("#autolab-progress-bar", ProgressBar)
            assert bar.display is True and bar.total == 20 and bar.progress == 8

    async def test_disengage_button_guarded_during_run(self, monkeypatch):
        import splicecraft as sc
        from textual.widgets import Input
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            called = []
            monkeypatch.setattr(scr, "_worker_disengage", lambda host: called.append(host))
            scr.query_one("#autolab-host", Input).value = "1.2.3.4"
            scr._active_run_id = "R1"          # a run is in flight
            scr._on_disengage()
            assert called == []                # refused because a run is active

    async def test_lights_button_routes(self, monkeypatch):
        import splicecraft as sc
        from textual.widgets import Button
        app = sc.PlasmidApp()
        async with app.run_test() as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            got = []
            monkeypatch.setattr(scr, "_on_lights", lambda on: got.append(on))
            scr.on_button_pressed(Button.Pressed(scr.query_one("#autolab-lights-off", Button)))
            assert got == [False]

    async def test_deck_map_state_and_render(self):
        import splicecraft as sc
        app = sc.PlasmidApp()
        async with app.run_test(size=(120, 50)) as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            scr._deck = {
                8: {"kind": "tips", "labware": "tiprack_300"},
                1: {"kind": "labware", "labware": "eppi_24", "id": "src"},
                2: {"kind": "labware", "labware": "plate_96", "id": "dst"}}
            scr._bound_slot = 2
            scr._refresh_deck()
            await pilot.pause()
            m = scr.query_one("#autolab-deck-map", sc.OT2DeckMap)
            pal = m._palette()
            # bay = (bg, fg, accent, tag, name)
            assert m._bay(8, pal)[3] == "tips" and "tiprack" in m._bay(8, pal)[4]
            assert m._bay(2, pal)[3] == "bound"               # library-bound plate
            assert m._bay(1, pal)[3] == "" and "src" in m._bay(1, pal)[4]
            assert m._bay(3, pal)[4] == "empty"               # unoccupied bay
            assert m._bay(12, pal)[3] == "trash"
            text = m.render().plain
            assert "tiprack" in text and "src" in text        # labware drawn in
            assert "┌" in text and "┼" in text and "└" in text  # ONE connected grid
            scr._bound_slot = None                            # unbind → plain occupied
            scr._refresh_deck()                               # push state into the map
            assert m._bay(2, m._palette())[3] == ""

    async def test_deck_map_click_maps_to_bay(self):
        import splicecraft as sc
        from textual.geometry import Offset
        app = sc.PlasmidApp()
        async with app.run_test(size=(120, 50)) as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            m = scr.query_one("#autolab-deck-map", sc.OT2DeckMap)
            m.render()                          # sets _cw from the widget width
            cw, ch = m._cw, m._ch
            assert m._slot_at(1, 1) == 10                       # top-left bay
            assert m._slot_at(cw + 3, 1) == 11                  # 2nd column, top row
            assert m._slot_at(1, 4 * (ch + 1)) == 1             # 1st column, bottom row
            got = []
            m._on_bay = got.append

            class _Click:
                def get_content_offset(self, _w):
                    return Offset(1, 1)
            m.on_click(_Click())
            assert got == [10]                                 # click routed to the bay

    async def test_scale_deck_sets_cell_height(self):
        import splicecraft as sc
        app = sc.PlasmidApp()
        async with app.run_test(size=(120, 60)) as pilot:
            app.action_open_autolab()
            await pilot.pause()
            scr = app.screen
            scr._scale_deck()                    # must not raise; sizes the bays
            m = scr.query_one("#autolab-deck-map", sc.OT2DeckMap)
            assert 2 <= m._ch <= 6

    async def test_deck_map_wide_char_name_keeps_alignment(self):
        import splicecraft as sc
        from rich.cells import cell_len
        app = sc.PlasmidApp()
        async with app.run_test(size=(120, 50)) as pilot:
            app.action_open_autolab(); await pilot.pause()
            scr = app.screen
            scr._deck = {1: {"kind": "labware", "labware": "plate_96",
                             "id": "🧬wide-名前-nick"}}
            scr._refresh_deck(); await pilot.pause()
            m = scr.query_one("#autolab-deck-map", sc.OT2DeckMap)
            widths = {cell_len(ln) for ln in m.render().plain.split("\n")}
            assert len(widths) == 1        # every row equal DISPLAY width despite wide chars

    async def test_deck_map_narrow_terminal_no_wrap(self):
        import splicecraft as sc
        app = sc.PlasmidApp()
        async with app.run_test(size=(22, 30)) as pilot:   # very narrow
            app.action_open_autolab(); await pilot.pause()
            scr = app.screen
            m = scr.query_one("#autolab-deck-map", sc.OT2DeckMap)
            t = m.render()
            assert t.no_wrap is True and t.overflow == "crop"
            assert len(t.plain.split("\n")) == 4 * m._ch + 5   # grid intact, no wrap


# ── Hardening sweep (edge-case audit follow-up) ─────────────────────────────────
class TestNewCodeHardening:
    def test_run_commands_survives_non_dict_response(self, monkeypatch):
        monkeypatch.setattr(ot2, "_ot2_request_json",
                            lambda host, path, **kw: ["not", "a", "dict"])
        out, total = ot2._ot2_run_commands("h", "R")
        assert out == [] and total == 0                # no AttributeError

    def test_run_commands_rejects_bool_and_absurd_total(self, monkeypatch):
        monkeypatch.setattr(ot2, "_ot2_request_json", lambda host, path, **kw: {
            "data": [{"id": "c1", "status": "succeeded"}], "meta": {"totalLength": True}})
        _, total = ot2._ot2_run_commands("h", "R")
        assert total == 1 and total is not True        # bool excluded
        monkeypatch.setattr(ot2, "_ot2_request_json", lambda host, path, **kw: {
            "data": [], "meta": {"totalLength": 10 ** 9}})
        _, total = ot2._ot2_run_commands("h", "R")
        assert total == 0                              # absurd value falls back

    def test_state_door_non_dict_ignored(self, monkeypatch):
        base = {
            "/health": {"name": "F"}, "/instruments": {"data": []},
            "/motors/engaged": {}, "/robot/lights": {"on": False},
            "/robot/door/status": ["hostile", "list"],         # non-dict
            "/calibration/status": {"deckCalibration": {"status": "OK"}},
            "/modules": {"data": []}, "/settings": {"settings": []}, "/runs": {"data": []},
            "/calibration/pipette_offset": {"data": []}, "/calibration/tip_length": {"data": []},
        }
        monkeypatch.setattr(ot2, "_ot2_request_json",
                            lambda host, path, **kw: base.get(path, {"data": []}))
        st = ot2._ot2_state("h")
        assert st["reachable"] and "door" not in st    # skipped, no crash

    def test_run_stops_on_monitor_exception(self, monkeypatch):
        monkeypatch.setattr(ot2, "_ot2_analyze", lambda host, txt, **kw: {
            "result": "ok", "protocol_id": "P", "analysis_id": "A", "status": "completed",
            "errors": [], "commands": [], "pipettes": [], "labware": []})
        healthy = {"reachable": True, "faults": [], "ok": True, "door": {}, "instruments": [],
                   "lights": False, "calibration": {"pipettes_calibrated": {"left": True}}}
        def state(host, **kw):
            if kw.get("run_id"):
                raise ValueError("malformed robot json mid-run")
            return healthy
        monkeypatch.setattr(ot2, "_ot2_state", state)
        monkeypatch.setattr(ot2, "_ot2_request_json",
                            lambda host, path, *, method="GET", payload=None, **kw:
                            {"data": {"id": "R"}})
        monkeypatch.setattr(ot2, "_ot2_set_lights", lambda host, on: {"on": on})
        stopped = []
        monkeypatch.setattr(ot2, "_ot2_stop_run", lambda host, rid: stopped.append(rid))
        with pytest.raises(ValueError):
            ot2._ot2_run_protocol("h", "print(1)", confirm=True)
        assert stopped == ["R"]        # SAFETY: run halted before the exception propagated

    def test_disengage_validates_and_caps_axes(self, monkeypatch):
        import splicecraft as sc
        monkeypatch.setattr(ot2, "_ot2_active_run", lambda host: None)
        sent = {}
        monkeypatch.setattr(ot2, "_ot2_disengage",
                            lambda host, *, axes=None: sent.update(axes=axes))
        r = sc._AGENT_HANDLERS["ot2-disengage"][0](
            None, {"host": "h", "axes": ["X", " z ", "garbage", "q", "b", "c", "a"]})
        # unknowns dropped, normalised, capped at 6 (7th cut) → x, z, b, c
        assert sent["axes"] == ["x", "z", "b", "c"]
        assert r["disengaged"] == ["x", "z", "b", "c"]      # echo matches what was sent

    def test_disengage_all_invalid_axes_defaults_all(self, monkeypatch):
        import splicecraft as sc
        monkeypatch.setattr(ot2, "_ot2_active_run", lambda host: None)
        sent = {}
        monkeypatch.setattr(ot2, "_ot2_disengage",
                            lambda host, *, axes=None: sent.update(axes=axes))
        r = sc._AGENT_HANDLERS["ot2-disengage"][0](None, {"host": "h", "axes": ["junk", 5]})
        assert sent["axes"] is None and r["disengaged"] == list(ot2._OT2_ALL_AXES)

    def test_lights_endpoint_survives_non_dict_response(self, monkeypatch):
        import splicecraft as sc
        monkeypatch.setattr(ot2, "_ot2_set_lights", lambda host, on: ["not", "dict"])
        r = sc._AGENT_HANDLERS["ot2-lights"][0](None, {"host": "h", "on": True})
        assert r == {"ok": True, "on": True}           # falls back to requested state
