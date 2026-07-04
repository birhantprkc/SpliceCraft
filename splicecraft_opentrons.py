"""splicecraft_opentrons — the Opentrons OT-2 protocol compiler + LAN client (L1).

Pure, app-free core for driving an **Opentrons OT-2** liquid handler from
SpliceCraft. Two halves, both unit-testable in isolation:

1. **The compiler** (offline, no robot, no network). Turns a plain-dict
   *transfer plan* — a pipette, some labware on deck slots, and a list of
   ``from → to`` well transfers with volumes — into a ready-to-run **Opentrons
   Python Protocol API v2** ``.py`` text, after validating it (known pipette /
   labware, in-range wells, sane volumes, unique slots). The design deliberately
   mirrors SpliceCraft's "simulate the steps" ethos: the emitted protocol is a
   real, reviewable recipe the user (or the robot's own analysis) can vet before
   anything moves.

2. **The LAN client** (online, opt-in). A stdlib-``urllib`` wrapper over the
   OT-2 ``robot-server`` HTTP API (port 31950): health / instruments / lights,
   upload-and-analyse a protocol, and a **gated** physical run. The gate is the
   whole point — ``_ot2_run_protocol`` refuses to actuate unless the robot's own
   analysis returns ``ok`` *and* the caller passes ``confirm=True``. Physical
   motion has no ``.bak``; it is treated as strictly more dangerous than a
   data-dir write.

Why a sibling, not hub code: none of this is app-coupled — it's compute +
network. The Textual plate-designer UI and the ``ot2-*`` agent endpoints stay
hub-side / in ``splicecraft_agent``; they call *into* here. This module imports
only L0 siblings (``logging`` / ``state`` / ``util``) and never reaches the hub.

SECURITY / hardening
--------------------
* The OT-2 is a **LAN** device (a private RFC-1918 address / ``.local`` name),
  so its calls deliberately bypass the public-host SSRF assertion in
  ``splicecraft_net`` (which *refuses* private/loopback hosts) — exactly as the
  BABS engine does for a local Ollama server. They are still bounded on every
  axis: explicit timeouts, a hard response-size cap, bounded poll loops, and a
  no-redirect opener (the robot never legitimately 3xx-redirects an API call).
* No SpliceCraft data-dir writes happen here — the engine is compute + network
  only. The compiler returns text; the caller decides where (if anywhere) to
  save it, through the usual chokepoint.
* The physical-run gate (analysis ``ok`` + explicit ``confirm``) means no code
  path here can move the gantry by accident.
"""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import splicecraft_logging as _logging
import splicecraft_state as _state
import splicecraft_util as _util

_log = _logging._log


# ── Robot-server connection tunables ────────────────────────────────────────────
_OT2_PORT = 31950
# The OT-2 robot-server negotiates an API version via this header; "4" is inside
# the supported range of every OT-2 build this project has tested against and is
# what the upload/analyse/run flow was validated with end-to-end.
_OT2_API_HEADER = "4"

# Timeouts (seconds). Short calls must fail fast (a powered-down robot should not
# hang a worker); the upload + analysis + run budgets are generous because
# server-side protocol analysis and real liquid handling legitimately take time.
_OT2_SHORT_TIMEOUT = 10          # health / instruments / lights / run control
_OT2_UPLOAD_TIMEOUT = 90         # POST /protocols (also kicks off analysis)
_OT2_ANALYSIS_POLL_TIMEOUT = 180 # wait for server-side analysis to complete
_OT2_RUN_POLL_TIMEOUT = 1800     # a physical run: 30 min ceiling
_OT2_POLL_INTERVAL = 1.0

# Response-size cap (bytes). A protocol analysis with hundreds of commands can be
# a few MB; 24 MB is generous vs. real payloads yet defends against a misbehaving
# endpoint streaming without end.
_OT2_JSON_MAX_BYTES = 24 * 1024 * 1024
# Upload cap: a compiled transfer protocol is small; refuse a pathologically
# large one before pushing it over the wire.
_OT2_MAX_PROTOCOL_BYTES = 4 * 1024 * 1024

# Plan sanity caps — defend the compiler + robot from garbage / runaway input.
_OT2_MAX_TRANSFERS = 10000        # far beyond any real plate-prep run
_OT2_MAX_VOLUME_UL = 100000.0     # 100 mL — reject absurd volumes (typos, Infinity)
_OT2_MAX_STEPS = 5000             # a multi-step protocol far beyond any real run
_OT2_MAX_DELAY_S = 86400.0        # 24 h — reject absurd delays

# The multi-step "protocol designer" model (a plan may carry `steps` instead of
# the legacy `transfers`). LIQUID steps need a pipette + tip rack + labware;
# control steps (delay/pause/comment) don't.
_OT2_STEP_TYPES = ("transfer", "distribute", "consolidate", "mix",
                   "delay", "pause", "comment")
_OT2_LIQUID_STEPS = ("transfer", "distribute", "consolidate", "mix")

_OT2_DEFAULT_API_LEVEL = "2.13"
_OT2_MULTIPART_BOUNDARY = "----SpliceCraftOT2FormBoundary7b3f"


class OT2Error(Exception):
    """Any OT-2 compiler / client failure with a user-facing, actionable message."""


# ── Deck catalog ────────────────────────────────────────────────────────────────
# A curated slice of the Opentrons Labware Library: the gear a SpliceCraft user
# actually reaches for (tip racks, tube racks, 6/24/48/96-well plates,
# reservoirs). Each entry carries a plate FORMAT (rows × cols) so the compiler
# can validate well references and enumerate wells; an unknown load name is not
# fatal (the robot's own analysis is the final authority) — it just skips
# geometry checks and warns. ``kind`` documents intent and lets the validator
# insist a tip rack is present.
#
# Row-major well names: row letter (A, B, …) + 1-based column. A 24-well plate /
# tube rack is 4×6 (A1…D6); a 96 is 8×12 (A1…H12); a 12-channel reservoir is
# 1×12 (A1…A12); a single-well reservoir/trash is 1×1 (A1).
_OT2_LABWARE: "dict[str, dict[str, Any]]" = {
    # tip racks
    "opentrons_96_tiprack_300ul": {"kind": "tiprack", "rows": 8, "cols": 12},
    "opentrons_96_tiprack_20ul": {"kind": "tiprack", "rows": 8, "cols": 12},
    "opentrons_96_tiprack_1000ul": {"kind": "tiprack", "rows": 8, "cols": 12},
    "opentrons_96_filtertiprack_200ul": {"kind": "tiprack", "rows": 8, "cols": 12},
    # tube racks
    "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap":
        {"kind": "tuberack", "rows": 4, "cols": 6},
    "opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap":
        {"kind": "tuberack", "rows": 4, "cols": 6},
    "opentrons_24_tuberack_generic_2ml_screwcap":
        {"kind": "tuberack", "rows": 4, "cols": 6},
    "opentrons_24_tuberack_nest_1.5ml_snapcap":
        {"kind": "tuberack", "rows": 4, "cols": 6},
    "opentrons_15_tuberack_15ml_conical": {"kind": "tuberack", "rows": 3, "cols": 5},
    "opentrons_6_tuberack_falcon_50ml_conical": {"kind": "tuberack", "rows": 2, "cols": 3},
    # well plates
    "nest_96_wellplate_100ul_pcr_full_skirt": {"kind": "wellplate", "rows": 8, "cols": 12},
    "nest_96_wellplate_2ml_deep": {"kind": "wellplate", "rows": 8, "cols": 12},
    "corning_96_wellplate_360ul_flat": {"kind": "wellplate", "rows": 8, "cols": 12},
    "corning_48_wellplate_1.6ml_flat": {"kind": "wellplate", "rows": 6, "cols": 8},
    "corning_24_wellplate_3.4ml_flat": {"kind": "wellplate", "rows": 4, "cols": 6},
    "corning_12_wellplate_6.9ml_flat": {"kind": "wellplate", "rows": 3, "cols": 4},
    "corning_6_wellplate_16.8ml_flat": {"kind": "wellplate", "rows": 2, "cols": 3},
    # reservoirs
    "nest_12_reservoir_15ml": {"kind": "reservoir", "rows": 1, "cols": 12},
    "nest_1_reservoir_195ml": {"kind": "reservoir", "rows": 1, "cols": 1},
    "agilent_1_reservoir_290ml": {"kind": "reservoir", "rows": 1, "cols": 1},
}

# Terse, friendly aliases → canonical Opentrons load names, so a plan can say
# "plate_24" instead of "corning_24_wellplate_3.4ml_flat". A raw load name is
# passed through untouched.
_OT2_LABWARE_ALIASES: "dict[str, str]" = {
    "tiprack_300": "opentrons_96_tiprack_300ul",
    "tiprack_20": "opentrons_96_tiprack_20ul",
    "tiprack_1000": "opentrons_96_tiprack_1000ul",
    "eppi_24": "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap",
    "eppi_24_2ml": "opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap",
    "tuberack_24": "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap",
    "tuberack_15": "opentrons_15_tuberack_15ml_conical",
    "tuberack_6x50": "opentrons_6_tuberack_falcon_50ml_conical",
    "plate_96": "nest_96_wellplate_100ul_pcr_full_skirt",
    "plate_96_flat": "corning_96_wellplate_360ul_flat",
    "plate_96_deep": "nest_96_wellplate_2ml_deep",
    "plate_48": "corning_48_wellplate_1.6ml_flat",
    "plate_24": "corning_24_wellplate_3.4ml_flat",
    "plate_12": "corning_12_wellplate_6.9ml_flat",
    "plate_6": "corning_6_wellplate_16.8ml_flat",
    "reservoir_12": "nest_12_reservoir_15ml",
    "reservoir_1": "nest_1_reservoir_195ml",
}

# Pipette model → (min µL, max µL, channels). The load name is the Protocol-API
# name; GEN1 pipettes have no "_gen2" suffix (Jacques carries a ``p300_single``).
_OT2_PIPETTES: "dict[str, tuple[float, float, int]]" = {
    "p10_single": (1.0, 10.0, 1),
    "p50_single": (5.0, 50.0, 1),
    "p300_single": (30.0, 300.0, 1),
    "p1000_single": (100.0, 1000.0, 1),
    "p20_single_gen2": (1.0, 20.0, 1),
    "p300_single_gen2": (20.0, 300.0, 1),
    "p1000_single_gen2": (100.0, 1000.0, 1),
    "p20_multi_gen2": (1.0, 20.0, 8),
    "p300_multi_gen2": (20.0, 300.0, 8),
}

_OT2_MOUNTS = ("left", "right")
_OT2_NEW_TIP = ("always", "once", "never")
# OT-2 deck: slots 1-11 hold labware; slot 12 is the fixed trash.
_OT2_SLOTS = tuple(range(1, 12))

_ROW_LETTERS = "ABCDEFGHIJKLMNOP"


# ── Well geometry helpers ───────────────────────────────────────────────────────
def _ot2_resolve_labware(name: str) -> str:
    """Expand a friendly alias to its canonical Opentrons load name (pass-through
    for anything already canonical / unknown)."""
    return _OT2_LABWARE_ALIASES.get(name, name)


def _ot2_wells(rows: int, cols: int) -> "list[str]":
    """Every well name of a rows×cols labware, row-major (A1, A2, …, B1, …)."""
    return [f"{_ROW_LETTERS[r]}{c + 1}" for r in range(rows) for c in range(cols)]


def _ot2_parse_well(well: str) -> "tuple[int, int] | None":
    """Split a well like ``B7`` into (row_index, column_number), or ``None`` if it
    is not a well name at all."""
    well = well.strip().upper()
    if len(well) < 2 or well[0] not in _ROW_LETTERS or not well[1:].isdigit():
        return None
    return _ROW_LETTERS.index(well[0]), int(well[1:])


def _ot2_well_ok(load_name: str, well: str) -> "bool | None":
    """Is ``well`` valid for this labware? ``True``/``False`` when the geometry is
    known, ``None`` when the load name is unknown (can't tell — defer to the robot)."""
    info = _OT2_LABWARE.get(load_name)
    if info is None:
        return None
    parsed = _ot2_parse_well(well)
    if parsed is None:
        return False
    row, col = parsed
    return row < info["rows"] and 1 <= col <= info["cols"]


def _ot2_custom_wells(definition: Any) -> "set[str] | None":
    """Valid well names of a custom Opentrons labware definition (from its
    ``wells`` map), or ``None`` if it doesn't declare them."""
    if not isinstance(definition, dict):
        return None
    wells = definition.get("wells")
    if isinstance(wells, dict) and wells:
        return {str(k).upper() for k in wells}
    return None


def _ot2_well_ok_entry(entry: "dict[str, Any]", well: str) -> "bool | None":
    """Well validity for a loaded-labware ENTRY — a built-in load name OR a custom
    definition (``{"definition": {...}}``). ``None`` when it can't be determined."""
    definition = entry.get("definition")
    if isinstance(definition, dict):
        cw = _ot2_custom_wells(definition)
        return None if cw is None else well.strip().upper() in cw
    return _ot2_well_ok(str(entry.get("labware", "")), well)


# ── Plan normalisation ──────────────────────────────────────────────────────────
def _ot2_safe_var(label: str, prefix: str) -> str:
    """A valid, collision-resistant Python identifier for an emitted variable."""
    cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in str(label))
    if not cleaned:
        cleaned = "x"
    if not prefix and cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return prefix + cleaned


def _ot2_split_ref(ref: str) -> "tuple[str, str] | None":
    """Split a ``labwareId:well`` reference into (labware_id, well)."""
    if not isinstance(ref, str) or ":" not in ref:
        return None
    lid, _, well = ref.partition(":")
    lid, well = lid.strip(), well.strip()
    if not lid or not well:
        return None
    return lid, well


# ── Shared validation helpers (used by both the transfer + step paths) ──────────
def _ot2_ref_errors(ref: "tuple[str, str] | None", labware: "dict[str, Any]",
                    tag: str, end: str) -> "list[str]":
    """Validate a parsed ``(labware_id, well)`` reference against loaded labware."""
    if ref is None:
        return [f"{tag}: '{end}' must look like 'labwareId:well'"]
    lid, well = ref
    if lid not in labware:
        return [f"{tag}: '{end}' references unknown labware id {lid!r}"]
    if _ot2_well_ok_entry(labware[lid], well) is False:
        name = labware[lid].get("labware") or "custom labware"
        return [f"{tag}: well {well!r} is out of range for {name!r}"]
    return []


def _ot2_volume_errors(vol: Any, spec: "tuple[float, float, int] | None",
                       pipette: str, tag: str) -> "tuple[list[str], list[str]]":
    """Validate one volume. Returns ``(errors, warnings)``."""
    if not isinstance(vol, (int, float)) or isinstance(vol, bool):
        return [f"{tag}: volume must be a number, got {vol!r}"], []
    if not math.isfinite(vol):
        return [f"{tag}: volume must be a finite number, got {vol!r}"], []
    if vol <= 0:
        return [f"{tag}: volume must be positive, got {vol}"], []
    if vol > _OT2_MAX_VOLUME_UL:
        return [f"{tag}: volume {vol:g} µL is implausibly large "
                f"(max {_OT2_MAX_VOLUME_UL:g})"], []
    if spec is not None and vol < spec[0]:
        return [], [f"{tag}: {vol} µL is below the {pipette} minimum "
                    f"({spec[0]:g} µL) — dispense will be inaccurate"]
    return [], []


def _ot2_mix_errors(mix: Any, spec: "tuple[float, float, int] | None",
                    pipette: str, tag: str) -> "list[str]":
    """Validate a ``[repetitions, volume]`` mix spec (for transfer mix_before/after)."""
    if not (isinstance(mix, (list, tuple)) and len(mix) == 2):
        return [f"{tag}: must be [repetitions, volume]"]
    reps, vol = mix
    errs: "list[str]" = []
    if not isinstance(reps, int) or isinstance(reps, bool) or reps <= 0:
        errs.append(f"{tag}: repetitions must be a positive integer, got {reps!r}")
    errs += _ot2_volume_errors(vol, spec, pipette, tag)[0]
    return errs


def _ot2_normalize_step(step: Any, default_new_tip: str) -> "dict[str, Any]":
    """Normalise one designer step into canonical form (parse refs, coerce types).
    Unknown / malformed steps normalise to ``{"type": <as-given>}`` and are flagged
    by ``_ot2_validate_steps``, never silently dropped."""
    if not isinstance(step, dict):
        return {"type": ""}
    stype = str(step.get("type", "")).lower().strip()
    # distribute / consolidate reuse ONE tip by default ("once"); a transfer
    # follows the plan-level default. An explicit step `new_tip` always wins.
    _nt_default = "once" if stype in ("distribute", "consolidate") else default_new_tip
    nt = str(step.get("new_tip", _nt_default)).lower()
    s: "dict[str, Any]" = {"type": stype,
                           "new_tip": nt if nt in _OT2_NEW_TIP else _nt_default}

    def _reflist(v: Any) -> "list[tuple[str, str] | None]":
        if isinstance(v, str):
            v = [v]
        return [_ot2_split_ref(str(x)) for x in v] if isinstance(v, list) else []

    def _mix(v: Any) -> "list[Any] | None":
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return [v[0], v[1]]
        if isinstance(v, dict):
            return [v.get("reps", v.get("repetitions")), v.get("volume", v.get("vol"))]
        return None

    if stype == "transfer":
        s["from"], s["to"] = step.get("from"), step.get("to")
        s["_src"] = _ot2_split_ref(str(step.get("from", "")))
        s["_dst"] = _ot2_split_ref(str(step.get("to", "")))
        s["volume"] = step.get("volume")
        s["mix_before"] = _mix(step.get("mix_before"))
        s["mix_after"] = _mix(step.get("mix_after"))
        s["blow_out"] = bool(step.get("blow_out"))
        s["touch_tip"] = bool(step.get("touch_tip"))
    elif stype == "distribute":
        s["from"], s["to"] = step.get("from"), step.get("to")
        s["_src"] = _ot2_split_ref(str(step.get("from", "")))
        s["_dsts"] = _reflist(step.get("to"))
        s["volume"] = step.get("volume")
    elif stype == "consolidate":
        s["from"], s["to"] = step.get("from"), step.get("to")
        s["_srcs"] = _reflist(step.get("from"))
        s["_dst"] = _ot2_split_ref(str(step.get("to", "")))
        s["volume"] = step.get("volume")
    elif stype == "mix":
        at = step.get("at", step.get("from"))
        s["at"] = at
        s["_at"] = _ot2_split_ref(str(at or ""))
        s["volume"] = step.get("volume")
        s["repetitions"] = step.get("repetitions", step.get("reps"))
    elif stype == "delay":
        s["seconds"] = step.get("seconds")
        s["message"] = step.get("message") or step.get("msg")
    elif stype == "pause":
        s["message"] = step.get("message") or step.get("msg")
    elif stype == "comment":
        s["text"] = str(step.get("text") or step.get("message") or "")
    return s


def _ot2_normalize_plan(plan: "dict[str, Any]") -> "dict[str, Any]":
    """Coerce a raw plan dict into the canonical form the validator + compiler
    consume. Idempotent. Does NOT validate — that's ``_ot2_validate_plan``.

    Accepted input (all but ``labware`` + ``transfers`` optional)::

        {
          "name": "My prep",
          "pipette": "p300_single",          # or {"model": ..., "mount": ...}
          "mount": "left",                    # default "left"
          "tips": {"labware": "tiprack_300", "slot": 8},   # or a list
          "labware": {"src": {"labware": "eppi_24", "slot": 1},
                      "dst": {"labware": "plate_24", "slot": 2}},
          "transfers": [{"from": "src:A1", "to": "dst:A1", "volume": 50}, ...],
          "new_tip": "always",               # always | once | never
          "api_level": "2.13"
        }
    """
    if not isinstance(plan, dict):
        raise OT2Error("plan must be a dict")
    p: "dict[str, Any]" = {}

    pip = plan.get("pipette", "p300_single")
    if isinstance(pip, dict):
        p["pipette"] = str(pip.get("model", "p300_single"))
        mount = pip.get("mount", plan.get("mount", "left"))
    else:
        p["pipette"] = str(pip)
        mount = plan.get("mount", "left")
    p["mount"] = str(mount).lower()

    tips_in = plan.get("tips", [])
    if isinstance(tips_in, dict):
        tips_in = [tips_in]
    tips: "list[dict[str, Any]]" = []
    for t in tips_in or []:
        if isinstance(t, dict):
            tips.append({"labware": _ot2_resolve_labware(str(t.get("labware", ""))),
                         "slot": t.get("slot")})
    p["tips"] = tips

    labware: "dict[str, dict[str, Any]]" = {}
    for lid, lw in (plan.get("labware") or {}).items():
        if isinstance(lw, dict):
            entry: "dict[str, Any]" = {
                "labware": _ot2_resolve_labware(str(lw.get("labware", ""))),
                "slot": lw.get("slot")}
            if isinstance(lw.get("definition"), dict):
                entry["definition"] = lw["definition"]   # custom Opentrons labware def
            labware[str(lid)] = entry
    p["labware"] = labware

    transfers: "list[dict[str, Any]]" = []
    for t in (plan.get("transfers") or []):
        if not isinstance(t, dict):
            continue
        src = _ot2_split_ref(str(t.get("from", "")))
        dst = _ot2_split_ref(str(t.get("to", "")))
        transfers.append({
            "from": t.get("from"), "to": t.get("to"), "volume": t.get("volume"),
            "_src": src, "_dst": dst,
        })
    p["transfers"] = transfers

    steps_in = plan.get("steps")
    nt_default = str(plan.get("new_tip", "always")).lower()
    nt_default = nt_default if nt_default in _OT2_NEW_TIP else "always"
    p["steps"] = ([_ot2_normalize_step(s, nt_default) for s in steps_in]
                  if isinstance(steps_in, list) else [])
    # The multi-step `steps` designer model takes precedence over the legacy
    # `transfers` list whenever the caller supplies it (even an empty list).
    # IDEMPOTENT: a canonical plan already carries `uses_steps` (validate + compile
    # both re-normalise), so trust it — otherwise a legacy transfers plan, whose
    # normalised `steps` is [], would flip into steps mode on the second pass.
    p["uses_steps"] = (bool(plan.get("uses_steps")) if "uses_steps" in plan
                       else isinstance(steps_in, list))

    new_tip = str(plan.get("new_tip", "always")).lower()
    p["new_tip"] = new_tip if new_tip in _OT2_NEW_TIP else "always"
    p["api_level"] = str(plan.get("api_level", _OT2_DEFAULT_API_LEVEL))
    p["metadata"] = {
        "name": str(plan.get("name") or "SpliceCraft transfer"),
        "author": str(plan.get("author") or "SpliceCraft"),
        "description": str(plan.get("description")
                           or "Generated by SpliceCraft from a transfer plan."),
    }
    return p


# ── Validation ──────────────────────────────────────────────────────────────────
def _ot2_validate_transfers(p: "dict[str, Any]", spec: Any,
                            errors: "list[str]", warnings: "list[str]") -> None:
    """Legacy transfer-list validation (a plan with `transfers`, not `steps`)."""
    n = len(p["transfers"])
    if n == 0:
        errors.append("no transfers: add at least one entry to 'transfers'")
    elif n > _OT2_MAX_TRANSFERS:
        errors.append(f"too many transfers ({n}); the max is {_OT2_MAX_TRANSFERS}")
    # Capped so a pathological count can't make validation itself O(huge).
    for i, t in enumerate(p["transfers"][:_OT2_MAX_TRANSFERS], 1):
        tag = f"transfer #{i}"
        errors += _ot2_ref_errors(t["_src"], p["labware"], tag, "from")
        errors += _ot2_ref_errors(t["_dst"], p["labware"], tag, "to")
        verr, vwarn = _ot2_volume_errors(t.get("volume"), spec, p["pipette"], tag)
        errors += verr
        warnings += vwarn
    if p["new_tip"] == "always":
        tip_capacity = 96 * len([t for t in p["tips"] if t.get("labware")])
        if tip_capacity and n > tip_capacity:
            warnings.append(f"{n} transfers with new_tip='always' need {n} tips "
                            f"but only {tip_capacity} are loaded — add tip racks or use "
                            "new_tip='once'")


def _ot2_validate_steps(p: "dict[str, Any]", spec: Any,
                        errors: "list[str]", warnings: "list[str]") -> None:
    """Multi-step designer validation (a plan with `steps`)."""
    n = len(p["steps"])
    if n == 0:
        errors.append("no steps: add at least one entry to 'steps'")
    elif n > _OT2_MAX_STEPS:
        errors.append(f"too many steps ({n}); the max is {_OT2_MAX_STEPS}")
    lw, pip = p["labware"], p["pipette"]
    for i, s in enumerate(p["steps"][:_OT2_MAX_STEPS], 1):
        st = s["type"]
        tag = f"step #{i} ({st or '?'})"
        if st not in _OT2_STEP_TYPES:
            errors.append(f"step #{i}: unknown step type {st!r} "
                          f"(known: {', '.join(_OT2_STEP_TYPES)})")
            continue
        if st == "transfer":
            errors += _ot2_ref_errors(s["_src"], lw, tag, "from")
            errors += _ot2_ref_errors(s["_dst"], lw, tag, "to")
            verr, vwarn = _ot2_volume_errors(s.get("volume"), spec, pip, tag)
            errors += verr
            warnings += vwarn
            for mk in ("mix_before", "mix_after"):
                if s.get(mk) is not None:
                    errors += _ot2_mix_errors(s[mk], spec, pip, f"{tag} {mk}")
        elif st == "distribute":
            errors += _ot2_ref_errors(s["_src"], lw, tag, "from")
            if not s["_dsts"]:
                errors.append(f"{tag}: 'to' must be a non-empty list of wells")
            for j, r in enumerate(s["_dsts"], 1):
                errors += _ot2_ref_errors(r, lw, f"{tag} to[{j}]", "to")
            verr, vwarn = _ot2_volume_errors(s.get("volume"), spec, pip, tag)
            errors += verr
            warnings += vwarn
        elif st == "consolidate":
            if not s["_srcs"]:
                errors.append(f"{tag}: 'from' must be a non-empty list of wells")
            for j, r in enumerate(s["_srcs"], 1):
                errors += _ot2_ref_errors(r, lw, f"{tag} from[{j}]", "from")
            errors += _ot2_ref_errors(s["_dst"], lw, tag, "to")
            verr, vwarn = _ot2_volume_errors(s.get("volume"), spec, pip, tag)
            errors += verr
            warnings += vwarn
        elif st == "mix":
            errors += _ot2_ref_errors(s["_at"], lw, tag, "at")
            verr, vwarn = _ot2_volume_errors(s.get("volume"), spec, pip, tag)
            errors += verr
            warnings += vwarn
            reps = s.get("repetitions")
            if not isinstance(reps, int) or isinstance(reps, bool) or reps <= 0:
                errors.append(f"{tag}: repetitions must be a positive integer, got {reps!r}")
        elif st == "delay":
            secs = s.get("seconds")
            if (not isinstance(secs, (int, float)) or isinstance(secs, bool)
                    or not math.isfinite(secs) or secs <= 0):
                errors.append(f"{tag}: seconds must be a positive number, got {secs!r}")
            elif secs > _OT2_MAX_DELAY_S:
                errors.append(f"{tag}: delay {secs:g}s is implausibly long "
                              f"(max {_OT2_MAX_DELAY_S:g}s)")
        elif st == "comment":
            if not str(s.get("text", "")).strip():
                warnings.append(f"{tag}: empty comment")
        # pause: an optional message — nothing to validate


def _ot2_validate_plan(plan: "dict[str, Any]") -> "dict[str, list[str]]":
    """Validate a plan (raw or canonical). Returns ``{"errors": [...],
    "warnings": [...]}``. Errors block compilation; warnings don't (e.g. a
    sub-minimum volume the pipette can't dispense accurately, or an unrecognised
    load name the robot will have the final say on). Handles both the legacy
    `transfers` list and the multi-step `steps` designer model."""
    p = _ot2_normalize_plan(plan)
    errors: "list[str]" = []
    warnings: "list[str]" = []

    spec = _OT2_PIPETTES.get(p["pipette"])
    if spec is None:
        errors.append(f"unknown pipette model {p['pipette']!r} "
                      f"(known: {', '.join(sorted(_OT2_PIPETTES))})")
    if p["mount"] not in _OT2_MOUNTS:
        errors.append(f"mount must be 'left' or 'right', got {p['mount']!r}")

    used_slots: "dict[int, str]" = {}

    def _check_slot(slot: Any, what: str) -> None:
        if not isinstance(slot, int) or isinstance(slot, bool):
            errors.append(f"{what}: slot must be an integer 1-11, got {slot!r}")
            return
        if slot not in _OT2_SLOTS:
            errors.append(f"{what}: slot {slot} is not a labware slot "
                          "(use 1-11; slot 12 is the fixed trash)")
            return
        if slot in used_slots:
            errors.append(f"{what}: slot {slot} already holds {used_slots[slot]}")
        else:
            used_slots[slot] = what

    # A protocol of only control steps (delay / pause / comment) needs no tips or
    # labware; require them only when a liquid-handling operation is present.
    needs_liquid = (any(s["type"] in _OT2_LIQUID_STEPS for s in p["steps"])
                    if p["uses_steps"] else bool(p["transfers"]))
    if needs_liquid and not p["tips"]:
        errors.append("no tip rack: add at least one entry to 'tips'")
    for t in p["tips"]:
        _check_slot(t.get("slot"), f"tip rack {t.get('labware')!r}")
        if t.get("labware") and t["labware"] not in _OT2_LABWARE:
            warnings.append(f"tip rack {t['labware']!r} not in the built-in catalog "
                            "— the robot's analysis will validate it")
        elif t.get("labware") and _OT2_LABWARE[t["labware"]]["kind"] != "tiprack":
            errors.append(f"{t['labware']!r} is not a tip rack")

    if needs_liquid and not p["labware"]:
        errors.append("no labware: add at least one entry to 'labware'")
    for lid, lw in p["labware"].items():
        _check_slot(lw.get("slot"), f"labware {lid!r}")
        if isinstance(lw.get("definition"), dict):
            if _ot2_custom_wells(lw["definition"]) is None:
                warnings.append(f"custom labware (id {lid!r}) declares no 'wells' — well "
                                "checks skipped; the robot will validate it")
        elif lw.get("labware") and lw["labware"] not in _OT2_LABWARE:
            warnings.append(f"labware {lw['labware']!r} (id {lid!r}) not in the built-in "
                            "catalog — well checks skipped; the robot will validate it")

    if p["uses_steps"]:
        _ot2_validate_steps(p, spec, errors, warnings)
    else:
        _ot2_validate_transfers(p, spec, errors, warnings)
    return {"errors": errors, "warnings": warnings}


# ── The compiler ────────────────────────────────────────────────────────────────
def _ot2_emit_step(s: "dict[str, Any]", id_var: "dict[str, str]") -> "list[str]":
    """Emit the Protocol API v2 line(s) for one validated designer step."""
    st = s["type"]

    def well(ref: "tuple[str, str]") -> str:
        return f"{id_var[ref[0]]}[{json.dumps(ref[1])}]"

    def wells(refs: "list[Any]") -> str:
        return "[" + ", ".join(well(r) for r in refs) + "]"

    kw: "list[str]" = []
    if st in ("transfer", "distribute", "consolidate"):
        kw.append(f"new_tip={json.dumps(s['new_tip'])}")
    if st == "transfer":
        if s.get("mix_before"):
            kw.append(f"mix_before=({s['mix_before'][0]!r}, {s['mix_before'][1]!r})")
        if s.get("mix_after"):
            kw.append(f"mix_after=({s['mix_after'][0]!r}, {s['mix_after'][1]!r})")
        if s.get("blow_out"):
            kw.append("blow_out=True")
            kw.append('blowout_location="destination well"')
        if s.get("touch_tip"):
            kw.append("touch_tip=True")
        opts = "".join(f", {k}" for k in kw)
        return [f"    pipette.transfer({s['volume']!r}, {well(s['_src'])}, "
                f"{well(s['_dst'])}{opts})"]
    if st == "distribute":
        opts = "".join(f", {k}" for k in kw)
        return [f"    pipette.distribute({s['volume']!r}, {well(s['_src'])}, "
                f"{wells(s['_dsts'])}{opts})"]
    if st == "consolidate":
        opts = "".join(f", {k}" for k in kw)
        return [f"    pipette.consolidate({s['volume']!r}, {wells(s['_srcs'])}, "
                f"{well(s['_dst'])}{opts})"]
    if st == "mix":
        # A standalone mix needs a tip: pick up, mix, drop (self-contained).
        return ["    pipette.pick_up_tip()",
                f"    pipette.mix({s['repetitions']!r}, {s['volume']!r}, {well(s['_at'])})",
                "    pipette.drop_tip()"]
    if st == "delay":
        msg = f", msg={json.dumps(s['message'])}" if s.get("message") else ""
        return [f"    protocol.delay(seconds={s['seconds']!r}{msg})"]
    if st == "pause":
        return [f"    protocol.pause({json.dumps(s['message']) if s.get('message') else ''})"]
    if st == "comment":
        return [f"    protocol.comment({json.dumps(s['text'])})"]
    return []


def _ot2_compile_protocol(plan: "dict[str, Any]") -> str:
    """Compile a plan into Opentrons Protocol API v2 Python text.

    Handles both the legacy `transfers` list and the multi-step `steps` designer
    model. Raises ``OT2Error`` (listing every problem) if the plan does not
    validate — a caller should surface those rather than ship a broken protocol.
    """
    p = _ot2_normalize_plan(plan)
    report = _ot2_validate_plan(p)
    if report["errors"]:
        raise OT2Error("invalid plan:\n  - " + "\n  - ".join(report["errors"]))

    md = p["metadata"]
    out: "list[str]" = [
        "from opentrons import protocol_api",
        "",
        "metadata = {",
        f"    \"protocolName\": {json.dumps(md['name'])},",
        f"    \"author\": {json.dumps(md['author'])},",
        f"    \"description\": {json.dumps(md['description'])},",
        f"    \"apiLevel\": {json.dumps(p['api_level'])},",
        "}",
        "",
        "# Generated by SpliceCraft. Validated against the built-in deck catalog",
        "# before emit; ALWAYS re-check with the robot's built-in analysis (or the",
        "# Opentrons App's simulate) before running on hardware.",
        "",
        "def run(protocol: protocol_api.ProtocolContext):",
    ]

    tip_vars: "list[str]" = []
    for t in p["tips"]:
        var = _ot2_safe_var(str(t["slot"]), "tips_")
        tip_vars.append(var)
        out.append(f"    {var} = protocol.load_labware("
                   f"{json.dumps(t['labware'])}, {t['slot']})")

    id_var: "dict[str, str]" = {}
    used_vars = set(tip_vars)
    for lid, lw in p["labware"].items():
        var = _ot2_safe_var(lid, "lw_")
        if var in used_vars:          # distinct ids can sanitise to the same name
            k = 2
            while f"{var}_{k}" in used_vars:
                k += 1
            var = f"{var}_{k}"
        used_vars.add(var)
        id_var[lid] = var
        if isinstance(lw.get("definition"), dict):
            # custom labware: embed the Opentrons definition (a plain dict → a
            # valid Python literal) and load it from that.
            out.append(f"    {var} = protocol.load_labware_from_definition("
                       f"{lw['definition']!r}, {lw['slot']})")
        else:
            out.append(f"    {var} = protocol.load_labware("
                       f"{json.dumps(lw['labware'])}, {lw['slot']})")

    out.append(
        f"    pipette = protocol.load_instrument("
        f"{json.dumps(p['pipette'])}, {json.dumps(p['mount'])}, "
        f"tip_racks=[{', '.join(tip_vars)}])"
    )
    out.append("    protocol.home()")

    if p["uses_steps"]:
        for s in p["steps"]:
            out.extend(_ot2_emit_step(s, id_var))
    else:
        volumes = [t["volume"] for t in p["transfers"]]
        srcs = [f"{id_var[t['_src'][0]]}[{json.dumps(t['_src'][1])}]" for t in p["transfers"]]
        dsts = [f"{id_var[t['_dst'][0]]}[{json.dumps(t['_dst'][1])}]" for t in p["transfers"]]
        out.append("    pipette.transfer(")
        out.append(f"        {volumes!r},")
        out.append("        [" + ", ".join(srcs) + "],")
        out.append("        [" + ", ".join(dsts) + "],")
        out.append(f"        new_tip={json.dumps(p['new_tip'])},")
        out.append("    )")
    return "\n".join(out) + "\n"


def _ot2_plan_summary(plan: "dict[str, Any]") -> "dict[str, Any]":
    """A compact, human/agent-friendly digest of a plan (counts, volume, tips)."""
    p = _ot2_normalize_plan(plan)
    report = _ot2_validate_plan(p)

    def _num(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    if p["uses_steps"]:
        liquid = [s for s in p["steps"] if s["type"] in _OT2_LIQUID_STEPS]
        total_vol = sum(s.get("volume") for s in liquid if _num(s.get("volume")))
        n_steps = len(p["steps"])
        n_transfers = len([s for s in p["steps"] if s["type"] == "transfer"])
        tips_needed = len(liquid)   # each liquid step uses ~1 tip
        step_types: "dict[str, int]" = {}
        for s in p["steps"]:
            step_types[s["type"]] = step_types.get(s["type"], 0) + 1
    else:
        total_vol = sum(t["volume"] for t in p["transfers"] if _num(t["volume"]))
        n_steps = len(p["transfers"])
        n_transfers = len(p["transfers"])
        tips_needed = (n_transfers if p["new_tip"] == "always"
                       else (1 if p["new_tip"] == "once" and p["transfers"] else 0))
        step_types = {"transfer": n_transfers} if n_transfers else {}
    return {
        "name": p["metadata"]["name"],
        "pipette": p["pipette"],
        "mount": p["mount"],
        "labware": {lid: (lw.get("labware") or "custom")
                    for lid, lw in p["labware"].items()},
        "steps": n_steps,
        "transfers": n_transfers,
        "step_types": step_types,
        "total_volume_ul": total_vol,
        "new_tip": p["new_tip"],
        "tips_needed": tips_needed,
        "valid": not report["errors"],
        "errors": report["errors"],
        "warnings": report["warnings"],
    }


# ── Deck visualizer ─────────────────────────────────────────────────────────────
# The OT-2 deck, physical layout (front row 1-2-3 at the bottom, trash at 12):
#     10  11  12(trash)
#      7   8   9
#      4   5   6
#      1   2   3
_OT2_DECK_LAYOUT = [[10, 11, 12], [7, 8, 9], [4, 5, 6], [1, 2, 3]]


def _ot2_short_labware(name: str) -> str:
    """A compact display label for a labware load name — its friendly alias if
    one exists, else a truncated load name."""
    if not name:
        return ""
    rev = {v: k for k, v in _OT2_LABWARE_ALIASES.items()}
    return (rev.get(name) or name)[:13]


def _ot2_render_deck(plan: "dict[str, Any]") -> str:
    """Render the OT-2 deck as a Unicode grid (slots 1-11 + the fixed trash at 12),
    showing which labware sits in each slot — a text 'deck map' à la the Opentrons
    Protocol Designer."""
    p = _ot2_normalize_plan(plan)
    occ: "dict[int, tuple[str, str]]" = {}
    for t in p["tips"]:
        s = t.get("slot")
        if isinstance(s, int) and not isinstance(s, bool):
            occ[s] = ("tips", _ot2_short_labware(str(t.get("labware", ""))))
    for lid, lw in p["labware"].items():
        s = lw.get("slot")
        if isinstance(s, int) and not isinstance(s, bool):
            label = "custom" if lw.get("definition") else _ot2_short_labware(str(lw.get("labware", "")))
            occ[s] = (lid, label)
    w = 14

    def clip(s: str) -> str:
        return (" " + s)[:w].ljust(w)

    def cell(slot: int) -> "list[str]":
        if slot == 12:
            return [clip("12  TRASH"), clip(""), clip("fixed trash")]
        who, name = occ.get(slot, ("", ""))
        return [clip(str(slot)), clip(who), clip(name)]

    def hbar(left: str, mid: str, right: str) -> str:
        return left + ("─" * w + mid) * 2 + "─" * w + right

    lines = [hbar("┌", "┬", "┐")]
    for ri, row in enumerate(_OT2_DECK_LAYOUT):
        cells = [cell(s) for s in row]
        for li in range(3):
            lines.append("│" + "│".join(cells[c][li] for c in range(3)) + "│")
        lines.append(hbar("├", "┼", "┤") if ri < len(_OT2_DECK_LAYOUT) - 1
                     else hbar("└", "┴", "┘"))
    return "\n".join(lines)


def _ot2_build_labware_def(name: str, rows: int, cols: int, *,
                           category: str = "wellPlate", spacing: float = 9.0,
                           x_off: float = 14.38, y_off: float = 11.24,
                           diameter: float = 6.5, depth: float = 14.0,
                           volume: float = 200.0, x_dim: float = 127.76,
                           y_dim: float = 85.48, z_dim: float = 15.0) -> "dict[str, Any]":
    """Generate a structurally-valid Opentrons labware definition for a REGULAR
    rows×cols grid (well plate / tube rack / reservoir), with SLAS-footprint
    defaults. A1 is back-left; wells march right (columns) and toward the front
    (rows). ALWAYS analyse on the robot before running — the geometry defaults
    are generic, not a substitute for the official Labware Creator's calibration."""
    rows = max(1, min(int(rows), len(_ROW_LETTERS)))
    cols = max(1, min(int(cols), 99))
    ordering: "list[list[str]]" = []
    wells: "dict[str, Any]" = {}
    for c in range(cols):
        col: "list[str]" = []
        for r in range(rows):
            wn = f"{_ROW_LETTERS[r]}{c + 1}"
            col.append(wn)
            wells[wn] = {"depth": depth, "totalLiquidVolume": volume,
                         "shape": "circular", "diameter": diameter,
                         "x": round(x_off + c * spacing, 2),
                         "y": round(y_dim - y_off - r * spacing, 2),
                         "z": round(z_dim - depth, 2)}
        ordering.append(col)
    load_name = ("".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")
                 or "custom_labware")
    return {
        "ordering": ordering,
        "brand": {"brand": "SpliceCraft", "brandId": []},
        "metadata": {"displayName": name, "displayCategory": category,
                     "displayVolumeUnits": "µL", "tags": []},
        "dimensions": {"xDimension": x_dim, "yDimension": y_dim, "zDimension": z_dim},
        "wells": wells,
        "groups": [{"metadata": {"wellBottomShape": "flat"}, "wells": list(wells)}],
        "parameters": {"format": "irregular", "quirks": [],
                       "isMagneticModuleCompatible": False,
                       "loadName": load_name, "isTiprack": False},
        "namespace": "custom_beta", "version": 1, "schemaVersion": 2,
        "cornerOffsetFromSlot": {"x": 0.0, "y": 0.0, "z": 0.0},
    }


# ── LAN client (OT-2 robot-server, port 31950) ──────────────────────────────────
def _ot2_user_agent() -> str:
    return f"SpliceCraft/{_state._sc_version or '?'} (OT-2 client)"


def _ot2_base_url(host: str) -> str:
    """Normalise a user-supplied host into ``http://HOST:31950``.

    Accepts a bare IP / hostname (``192.168.1.56``, ``opentrons.local``), an
    ``host:port`` pair, or a full ``http://…`` URL. Only plain HTTP to the
    robot-server is supported.
    """
    host = (host or "").strip()
    if not host:
        raise OT2Error("no OT-2 host given")
    if "://" in host:
        parts = urllib.parse.urlsplit(host)
        if parts.scheme not in ("http", ""):
            raise OT2Error(f"unsupported scheme {parts.scheme!r} — the OT-2 API is HTTP")
        netloc = parts.netloc or parts.path
    else:
        netloc = host
    if ":" not in netloc:
        netloc = f"{netloc}:{_OT2_PORT}"
    return f"http://{netloc}"


class _OT2NoRedirect(urllib.request.HTTPRedirectHandler):
    """The robot-server never legitimately redirects an API call; treat a 3xx as
    an error rather than silently following it off-box."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise urllib.error.HTTPError(
            newurl, code, f"unexpected redirect to {newurl}", headers, fp)


def _ot2_opener() -> "urllib.request.OpenerDirector":
    # A plain opener (NOT splicecraft_net's public-host-asserting hardened one):
    # the OT-2 lives on a private LAN address the hardened opener refuses. Same
    # deliberate local-service bypass the BABS Ollama client uses.
    return urllib.request.build_opener(_OT2NoRedirect())


def _ot2_read_capped(resp: Any, max_bytes: int) -> bytes:
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise OT2Error(f"OT-2 response exceeded the {max_bytes}-byte cap")
    return data


def _ot2_request_json(host: str, path: str, *, method: str = "GET",
                      payload: "dict[str, Any] | None" = None,
                      timeout: float = _OT2_SHORT_TIMEOUT,
                      max_bytes: int = _OT2_JSON_MAX_BYTES) -> "dict[str, Any]":
    """One JSON call to the robot-server. Raises ``OT2Error`` on any transport /
    HTTP / decode failure with an actionable message."""
    url = _ot2_base_url(host) + path
    headers = {"Opentrons-Version": _OT2_API_HEADER, "User-Agent": _ot2_user_agent()}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _ot2_opener().open(req, timeout=timeout) as resp:
            body = _ot2_read_capped(resp, max_bytes)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(64 * 1024).decode("utf-8", "replace")
        except Exception:
            pass
        raise OT2Error(f"OT-2 {method} {path} → HTTP {exc.code}"
                       + (f": {detail[:400]}" if detail else "")) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise OT2Error(f"cannot reach OT-2 at {host} ({exc}). Is it powered on and "
                       "on this network?") from exc
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise OT2Error(f"OT-2 {method} {path} returned non-JSON") from exc


def _ot2_request_multipart(host: str, path: str, *, filename: str, file_bytes: bytes,
                           timeout: float = _OT2_UPLOAD_TIMEOUT,
                           max_bytes: int = _OT2_JSON_MAX_BYTES) -> "dict[str, Any]":
    """POST a protocol file as ``multipart/form-data`` (field name ``files``)."""
    safe_name = "".join(ch for ch in filename if ch.isalnum() or ch in "._-") or "protocol.py"
    boundary = _OT2_MULTIPART_BOUNDARY
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="files"; filename="{safe_name}"\r\n'.encode(),
        b"Content-Type: text/x-python\r\n\r\n",
        file_bytes, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    headers = {
        "Opentrons-Version": _OT2_API_HEADER,
        "User-Agent": _ot2_user_agent(),
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    req = urllib.request.Request(_ot2_base_url(host) + path, data=body,
                                 headers=headers, method="POST")
    try:
        with _ot2_opener().open(req, timeout=timeout) as resp:
            raw = _ot2_read_capped(resp, max_bytes)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(64 * 1024).decode("utf-8", "replace")
        except Exception:
            pass
        raise OT2Error(f"OT-2 protocol upload → HTTP {exc.code}"
                       + (f": {detail[:400]}" if detail else "")) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise OT2Error(f"cannot reach OT-2 at {host} ({exc})") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise OT2Error("OT-2 protocol upload returned non-JSON") from exc


def _ot2_health(host: str) -> "dict[str, Any]":
    """Robot identity + software versions (also the reachability probe)."""
    return _ot2_request_json(host, "/health")


def _ot2_instruments(host: str) -> "dict[str, Any]":
    """Attached pipettes / instruments as the robot reports them."""
    return _ot2_request_json(host, "/instruments")


def _ot2_set_lights(host: str, on: bool) -> "dict[str, Any]":
    """Toggle the status light — a zero-motion control check."""
    return _ot2_request_json(host, "/robot/lights", method="POST", payload={"on": bool(on)})


# ── Telemetry / fault detection ─────────────────────────────────────────────────
# The OT-2 REST API does not stream live gantry XYZ, so "position" is tracked at
# the PROTOCOL level: which command the robot is currently executing and — on a
# crash — which command FAILED and why. Combined with the peripheral state
# (instrument OK flags + specs, motor engagement, deck-calibration health, module
# sensor readings, robot settings/variables, reachability) this gives direct,
# immediate crash detection: a pipette that hits labware fails its move / pick-up
# command and the run goes to ``failed`` with an error the monitor surfaces the
# instant it polls. All of it is read-only — nothing here actuates.

def _ot2_try_json(host: str, path: str, *,
                  timeout: float = _OT2_SHORT_TIMEOUT) -> "tuple[dict[str, Any] | None, str | None]":
    """Best-effort GET: ``(data, None)`` or ``(None, error)`` — never raises, so
    one dead endpoint can't sink a whole state snapshot."""
    try:
        return _ot2_request_json(host, path, timeout=timeout), None
    except OT2Error as exc:
        return None, str(exc)


def _ot2_run_commands(host: str, rid: str, *, page_length: int = 200) -> "list[dict[str, Any]]":
    """Per-command status for a run (capped). Each item carries the command id,
    type, status, error detail (or ``None``) and a brief position hint
    (labware / well / mount) — the closest thing to 'where the pipette is'."""
    data, _ = _ot2_try_json(host, f"/runs/{rid}/commands?pageLength={int(page_length)}")
    out: "list[dict[str, Any]]" = []
    for c in ((data or {}).get("data") or []):
        params = c.get("params") or {}
        at = {k: params[k] for k in ("labwareId", "wellName", "pipetteId", "mount")
              if k in params}
        err = c.get("error")
        out.append({
            "id": c.get("id"),
            "commandType": c.get("commandType"),
            "status": c.get("status"),
            "error": (err.get("detail") if isinstance(err, dict) else err),
            "at": at or None,
        })
    return out


def _ot2_active_run(host: str) -> "str | None":
    """The id of the current run, if any — so the monitor can watch a run started
    from the Opentrons App too, not just one SpliceCraft launched."""
    data, _ = _ot2_try_json(host, "/runs")
    runs = (data or {}).get("data") or []
    for r in runs:
        if r.get("current"):
            return r.get("id")
    for r in reversed(runs):
        if r.get("status") in ("running", "idle", "paused", "finishing",
                               "blocked-by-open-door"):
            return r.get("id")
    return None


def _ot2_run_state(host: str, rid: str) -> "dict[str, Any]":
    """Live run state: overall status, the current command (position), any failed
    commands, and run-level errors."""
    data, err = _ot2_try_json(host, f"/runs/{rid}")
    if data is None:
        return {"id": rid, "status": "unknown", "error": err, "errors": [],
                "current_command": None, "failed_commands": [], "command_count": 0}
    d = data.get("data") or {}
    cmds = _ot2_run_commands(host, rid)
    running = [c for c in cmds if c["status"] == "running"]
    progressed = [c for c in cmds if c["status"] in ("running", "succeeded")]
    current = running[0] if running else (progressed[-1] if progressed else None)
    return {
        "id": rid,
        "status": d.get("status"),
        "errors": d.get("errors") or [],
        "current_command": current,
        "failed_commands": [c for c in cmds if c["status"] == "failed"],
        "command_count": len(cmds),
    }


def _ot2_detect_faults(state: "dict[str, Any]") -> "list[str]":
    """Pure crash/fault extraction from a state snapshot — the direct signals a
    physical mishap produces: unreachable robot, a pipette subsystem reporting
    not-ok, bad/singular deck calibration, a module in an error state, a failed
    run, or a specific failed command (with where it failed)."""
    faults: "list[str]" = list(state.get("faults", []))
    if not state.get("reachable", False):
        return faults or ["unreachable"]
    for inst in state.get("instruments", []):
        if inst.get("ok") is False:
            faults.append(f"instrument fault: {inst.get('mount')} "
                          f"{inst.get('model')} reports not-ok")
    cal = state.get("calibration") or {}
    if cal.get("deck_status") not in (None, "OK", "IDENTITY"):
        faults.append(f"deck calibration status {cal.get('deck_status')}")
    if cal.get("marked_bad"):
        faults.append("deck calibration marked bad")
    for m in state.get("modules", []):
        s = str(m.get("status") or "").lower()
        if "error" in s or "fault" in s:
            faults.append(f"module {m.get('id') or m.get('moduleType')}: {m.get('status')}")
    run = state.get("run") or {}
    if run.get("status") == "failed":
        errs = run.get("errors") or []
        detail = errs[0].get("detail") if errs and isinstance(errs[0], dict) else None
        faults.append(f"run failed: {detail or 'see run errors'}")
    for c in run.get("failed_commands", []):
        where = f" at {c.get('at')}" if c.get("at") else ""
        faults.append(f"command failed: {c.get('commandType')} — {c.get('error')}{where}")
    return faults


def _ot2_state(host: str, *, run_id: "str | None" = None) -> "dict[str, Any]":
    """A full snapshot of everything the robot exposes — reachability + versions,
    pipette OK flags + volume specs, motor engagement, deck / instrument
    calibration health, attached modules and their sensor readings, the status
    light, robot settings (variables), and (for the active run or ``run_id``) live
    run / command state — with detected ``faults`` and an ``ok`` verdict."""
    state: "dict[str, Any]" = {"host": host, "reachable": False, "faults": []}
    health, herr = _ot2_try_json(host, "/health")
    if health is None:
        state["faults"].append(f"unreachable: {herr}")
        state["ok"] = False
        return state
    state["reachable"] = True
    state["health"] = {k: health.get(k) for k in
                       ("name", "api_version", "fw_version", "system_version", "robot_model")}

    instruments: "list[dict[str, Any]]" = []
    idata, _ = _ot2_try_json(host, "/instruments")
    for it in ((idata or {}).get("data") or []):
        d = it.get("data") or {}
        instruments.append({
            "mount": it.get("mount"),
            "model": it.get("instrumentModel") or it.get("instrumentName"),
            "ok": it.get("ok"),
            "min_volume": d.get("min_volume"),
            "max_volume": d.get("max_volume"),
            "channels": d.get("channels"),
        })
    state["instruments"] = instruments

    motors, _ = _ot2_try_json(host, "/motors/engaged")
    state["motors"] = motors or {}

    lights, _ = _ot2_try_json(host, "/robot/lights")
    state["lights"] = (lights or {}).get("on")

    cal, _ = _ot2_try_json(host, "/calibration/status")
    if cal:
        dc = cal.get("deckCalibration") or {}
        marked = ((dc.get("data") or {}).get("status") or {}).get("markedBad")
        state["calibration"] = {"deck_status": dc.get("status"), "marked_bad": bool(marked)}

    modules: "list[dict[str, Any]]" = []
    mdata, _ = _ot2_try_json(host, "/modules")
    for m in ((mdata or {}).get("data") or []):
        modules.append({"id": m.get("id"), "moduleType": m.get("moduleType"),
                        "status": m.get("status"), "data": m.get("data")})
    state["modules"] = modules

    sdata, _ = _ot2_try_json(host, "/settings")
    if sdata:
        state["settings"] = {s.get("id"): s.get("value")
                             for s in (sdata.get("settings") or []) if s.get("id")}

    if run_id is None:
        run_id = _ot2_active_run(host)
    if run_id:
        state["run"] = _ot2_run_state(host, run_id)

    state["faults"] = _ot2_detect_faults(state)
    state["ok"] = not state["faults"]
    return state


def _ot2_stop_run(host: str, rid: str) -> "dict[str, Any]":
    """Halt a run (stop action) — used to abort the instant a fault is detected."""
    return _ot2_request_json(host, f"/runs/{rid}/actions", method="POST",
                             payload={"data": {"actionType": "stop"}})


def _ot2_monitor(host: str, *, run_id: "str | None" = None,
                 on_state: "Any" = None, interval: float = _OT2_POLL_INTERVAL,
                 max_seconds: float = _OT2_RUN_POLL_TIMEOUT,
                 stop: "Any" = None) -> "dict[str, Any]":
    """Poll robot state until the (active) run reaches a terminal status, a fault
    is detected, ``stop()`` returns truthy, or ``max_seconds`` elapses — calling
    ``on_state(snapshot)`` each tick. Returns the final snapshot (with a
    ``stopped_reason``). Read-only: it observes, it never actuates."""
    deadline = _util._monotonic() + max_seconds
    while True:
        snap = _ot2_state(host, run_id=run_id)
        if on_state:
            try:
                on_state(snap)
            except Exception:
                pass
        if snap.get("faults"):
            snap["stopped_reason"] = "fault"
            return snap
        run = snap.get("run") or {}
        if run.get("status") in ("succeeded", "failed", "stopped"):
            snap["stopped_reason"] = f"run-{run.get('status')}"
            return snap
        if stop and stop():
            snap["stopped_reason"] = "cancelled"
            return snap
        if _util._monotonic() > deadline:
            snap["stopped_reason"] = "timeout"
            return snap
        time.sleep(interval)


def _ot2_analyze(host: str, protocol_text: str, *,
                 filename: str = "splicecraft_protocol.py") -> "dict[str, Any]":
    """Upload a protocol and wait for the robot's server-side analysis to finish.

    Returns ``{"protocol_id", "analysis_id", "status", "result", "errors",
    "commands", "pipettes", "labware"}``. ``result`` is ``"ok"`` / ``"not-ok"``;
    on ``not-ok`` the ``errors`` list explains why (this is the pre-flight the
    physical-run gate insists on).
    """
    if len(protocol_text.encode("utf-8")) > _OT2_MAX_PROTOCOL_BYTES:
        raise OT2Error("protocol is too large to upload "
                       f"({_OT2_MAX_PROTOCOL_BYTES // (1024 * 1024)} MB max)")
    up = _ot2_request_multipart(host, "/protocols", filename=filename,
                                file_bytes=protocol_text.encode("utf-8"))
    prot = up.get("data", {})
    pid = prot.get("id")
    summaries = prot.get("analysisSummaries") or []
    aid = summaries[-1]["id"] if summaries else None
    if not pid or not aid:
        raise OT2Error("OT-2 upload did not return a protocol/analysis id")

    deadline = _util._monotonic() + _OT2_ANALYSIS_POLL_TIMEOUT
    adata: "dict[str, Any]" = {}
    while True:
        adata = _ot2_request_json(host, f"/protocols/{pid}/analyses/{aid}").get("data", {})
        if adata.get("status") == "completed":
            break
        if _util._monotonic() > deadline:
            raise OT2Error("OT-2 analysis did not complete within "
                           f"{_OT2_ANALYSIS_POLL_TIMEOUT}s")
        time.sleep(_OT2_POLL_INTERVAL)

    return {
        "protocol_id": pid,
        "analysis_id": aid,
        "status": adata.get("status"),
        "result": adata.get("result"),
        "errors": adata.get("errors", []),
        "commands": adata.get("commands", []),
        "pipettes": adata.get("pipettes", []),
        "labware": adata.get("labware", []),
    }


def _ot2_run_protocol(host: str, protocol_text: str, *, confirm: bool = False,
                      filename: str = "splicecraft_protocol.py", poll: bool = True,
                      on_state: "Any" = None, stop_on_fault: bool = True) -> "dict[str, Any]":
    """Analyse, then (only if it passed AND the caller confirmed) run a protocol
    on real hardware, monitoring state throughout for a crash.

    The gate is deliberate and non-negotiable: this refuses to move the gantry
    unless ``_ot2_analyze`` returns ``result == "ok"`` *and* ``confirm=True`` was
    passed. It also refuses to start on an already-faulted robot (pre-flight
    ``_ot2_state`` check — bad calibration, a pipette subsystem error, etc.).

    While the run executes it polls a full ``_ot2_state`` snapshot each tick,
    calls ``on_state(snapshot)`` (for a live UI / log), and — the instant a fault
    is detected (a failed command, an instrument fault, …) — records it and, if
    ``stop_on_fault``, halts the run. The result carries ``crashed`` plus the
    ``faults`` / ``failed_commands`` that explain what went wrong and where.
    """
    analysis = _ot2_analyze(host, protocol_text, filename=filename)
    if analysis["result"] != "ok":
        return {"ran": False, "reason": "analysis-failed", **analysis}
    if not confirm:
        return {"ran": False, "reason": "confirm-required",
                "detail": "physical run needs confirm=True (analysis passed)",
                **analysis}

    pre = _ot2_state(host)
    if pre.get("faults"):
        return {"ran": False, "reason": "robot-unhealthy",
                "faults": pre["faults"], "state": pre, **analysis}

    created = _ot2_request_json(host, "/runs", method="POST",
                                payload={"data": {"protocolId": analysis["protocol_id"]}})
    rid = created.get("data", {}).get("id")
    if not rid:
        raise OT2Error("OT-2 did not return a run id")
    _log.info("[ot2] starting physical run %s on %s", rid, host)
    _ot2_request_json(host, f"/runs/{rid}/actions", method="POST",
                      payload={"data": {"actionType": "play"}})

    if not poll:
        return {"ran": True, "run_id": rid, "run_status": "running", **analysis}

    deadline = _util._monotonic() + _OT2_RUN_POLL_TIMEOUT
    while True:
        snap = _ot2_state(host, run_id=rid)
        if on_state:
            try:
                on_state(snap)
            except Exception:
                pass
        run = snap.get("run") or {}
        status = run.get("status", "unknown")
        faults = snap.get("faults") or []

        if faults and status != "succeeded":
            # A crash mid-flight. The robot already halts on a hard move error
            # (status 'failed'); for any other detected fault, stop it ourselves.
            if stop_on_fault and status not in ("failed", "stopped"):
                try:
                    _ot2_stop_run(host, rid)
                except OT2Error:
                    pass
            _log.warning("[ot2] fault during run %s: %s", rid, faults)
            return {"ran": True, "run_id": rid, "run_status": status, "crashed": True,
                    "faults": faults, "failed_commands": run.get("failed_commands") or [],
                    "run_errors": run.get("errors") or [], "state": snap, **analysis}

        if status in ("succeeded", "failed", "stopped"):
            _log.info("[ot2] run %s finished: %s", rid, status)
            return {"ran": True, "run_id": rid, "run_status": status, "crashed": False,
                    "faults": [], "failed_commands": run.get("failed_commands") or [],
                    "run_errors": run.get("errors") or [], "state": snap, **analysis}

        if _util._monotonic() > deadline:
            raise OT2Error(f"OT-2 run {rid} did not finish within {_OT2_RUN_POLL_TIMEOUT}s")
        time.sleep(_OT2_POLL_INTERVAL)
