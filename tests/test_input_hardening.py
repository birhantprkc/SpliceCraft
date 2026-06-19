"""Attack-surface / input-sterilization sweep (2026-06-14).

Locks in the hardening from the fresh attack-surface review:

  * control-char / ANSI / C1 / lone-surrogate / line-separator stripping on
    user-supplied names BEFORE they are persisted and reflected to the
    terminal (`_sanitize_label`, `_sanitize_note`, the logger scrub filter,
    `_record_display_name`, the custom-enzyme + experiment-project agent
    handlers, the primer-CSV import);
  * the spellcheck markdown-link/image ReDoS fix (now O(n), not O(n^2));
  * zip member-name tightening (control bytes + lone surrogates rejected);
  * the foreign-file ingest cap (`_GB_INGEST_MAX_BYTES`, 256 MB);
  * the empty-GenBank-text guard;
  * the agent server's DNS-rebinding Host check.

All pure / handler-level (fast) bar one E2E for the Host header. The autouse
`_protect_user_data` fixture (conftest) sandboxes every data-file write.
"""
from __future__ import annotations

import socket
import threading
import time
import types
import urllib.error
import urllib.request

import pytest

import splicecraft as sc

ESC = "\x1b"        # C0 escape — terminal-injection prefix
CSI8 = "\x9b"       # 8-bit CSI (C1 block)
SURR = "\udce9"     # lone surrogate (surrogateescape of a non-UTF-8 byte)
LSEP = "\u2028"    # Unicode line separator


# ── Control-char sterilization helpers ──────────────────────────────────────

def test_sanitize_label_strips_control_c1_surrogate_separator():
    clean = sc._sanitize_label(f"p{ESC}[2J{CSI8}{SURR}{LSEP}UC19")
    assert not any(ch in clean for ch in (ESC, CSI8, SURR, LSEP))
    assert clean.startswith("p") and clean.endswith("UC19")


def test_sanitize_label_preserves_spaces_and_plus():
    # [INV-98] no-underscores rule: internal spaces + '+' survive untouched.
    assert sc._sanitize_label("FFE 6") == "FFE 6"
    assert sc._sanitize_label("pED + insert") == "pED + insert"


def test_sanitize_label_control_only_becomes_empty():
    assert sc._sanitize_label(SURR + ESC + CSI8) == ""


def test_sanitize_note_keeps_tab_newline_strips_esc():
    out = sc._sanitize_note(f"para 1\n\tindented{ESC}[31m\npara 2")
    assert "\n" in out and "\t" in out
    assert ESC not in out and CSI8 not in out


def test_log_scrub_escapes_control_bytes():
    # A terminal-escape smuggled via any logged value is rendered as a visible
    # \xNN token, never a live ESC byte that fires when the log is cat'd.
    scrubbed = sc._SurrogateScrubFilter._scrub(f"name{ESC}[2Jx")
    assert ESC not in scrubbed
    assert "\\x1b" in scrubbed


def test_log_scrub_makes_surrogate_utf8_safe():
    scrubbed = sc._SurrogateScrubFilter._scrub("a" + SURR + "b")
    scrubbed.encode("utf-8")  # must not raise (the raw value would)


def test_record_display_name_sanitizes_terminal_escape():
    rec = types.SimpleNamespace(name="locus", id="id1")
    rec._tui_display_name = f"My{ESC}[2J Plasmid"
    name = sc.PlasmidApp._record_display_name(rec)
    assert ESC not in name
    assert " " in name  # display name keeps spaces


def test_record_display_name_falls_back_through_name():
    rec = types.SimpleNamespace(name=f"loc{ESC}us", id="id1")
    name = sc.PlasmidApp._record_display_name(rec)
    assert ESC not in name and name


# ── Agent custom-enzyme + experiment-project name sterilization ─────────────

def test_custom_enzyme_payload_sanitizes_text_fields():
    out = sc._agent_validate_custom_enzyme_payload({
        "name":     f"Eco{ESC}[31mRX",
        "site":     "GAATTC",
        "fwd_cut":  1, "rev_cut": 5,
        "type":     f"II{CSI8}5",
        "supplier": f"acme{ESC}co",
    })
    assert isinstance(out, dict), out
    for field in ("name", "type", "supplier"):
        assert ESC not in out[field] and CSI8 not in out[field]
    assert out["site"] == "GAATTC"  # unchanged


def test_experiment_project_create_sanitizes_name():
    res = sc._h_create_experiment_project(None, {"name": f"Proj{ESC}[2JX"})
    assert isinstance(res, dict) and res.get("ok") is True, res
    assert ESC not in res["name"]


# ── Primer CSV import name sterilization ────────────────────────────────────

def test_primer_csv_import_sanitizes_name(tmp_path):
    csv_path = tmp_path / "primers.csv"
    csv_path.write_text(
        "Name,Sequence\n" f"fwd{ESC}[2J,ACGTACGTACGTACGTACGT\n",
        encoding="utf-8",
    )
    res = sc._import_primers_from_csv(str(csv_path))
    assert res["primers"], res["skipped"]
    assert ESC not in res["primers"][0]["name"]


# ── Spellcheck markdown link/image ReDoS ────────────────────────────────────

def test_spellcheck_strip_is_linear_on_bracket_run():
    bomb = "[" * 60000  # O(n^2) form took seconds here; linear form ~1 ms
    t0 = time.perf_counter()
    out = sc._spellcheck_strip_code(bomb)
    assert time.perf_counter() - t0 < 1.0
    assert len(out) == len(bomb)  # offset alignment preserved


def test_spellcheck_strip_still_masks_links_and_images():
    masked = sc._spellcheck_strip_code("see [text](http://x/y) and ![a](u) ok")
    assert "see " in masked and " ok" in masked
    assert "text" not in masked and "http" not in masked


# ── Zip member-name tightening ──────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "sample\x07.gbk",    # bell (C0)
    "sample\rfoo.gbk",   # carriage return
    "sample\x9bfoo.gbk",  # C1 8-bit CSI
    "sample\udce9.gbk",  # lone surrogate
])
def test_is_safe_zip_member_name_rejects_control_and_surrogate(bad):
    assert sc._is_safe_zip_member_name(bad) is False


def test_is_safe_zip_member_name_accepts_normal():
    assert sc._is_safe_zip_member_name("run/34XK5N_sample.gbk") is True


# ── Foreign-file ingest cap + empty-GenBank guard ───────────────────────────

def test_gb_ingest_cap_is_256mb_and_below_json_cap():
    assert sc._GB_INGEST_MAX_BYTES == 256 * 1024 * 1024
    # MUST stay below the data-dir JSON cap so the two never get conflated.
    assert sc._GB_INGEST_MAX_BYTES < sc._state._SAFE_LOAD_JSON_MAX_BYTES


def test_gb_text_to_record_empty_raises_clean():
    with pytest.raises(ValueError):
        sc._gb_text_to_record("")


# ── Agent server DNS-rebinding Host check (E2E) ─────────────────────────────

class _TinyApp:
    _current_record = None
    _unsaved = False


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def host_server():
    port = _free_port()
    srv = sc._AgentAPIServer(("127.0.0.1", port), _TinyApp(), f"tok-{port}")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    time.sleep(0.05)
    try:
        yield port
    finally:
        srv.shutdown()
        srv.server_close()


def _get_status(port, path, host_header=None) -> int:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method="GET")
    if host_header is not None:
        req.add_header("Host", host_header)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_agent_rejects_nonloopback_host(host_server):
    # DNS-rebinding: connect to the loopback IP, send an attacker Host header.
    assert _get_status(host_server, "/tools", "evil.example.com") == 403
    assert _get_status(host_server, "/", "attacker.test") == 403


def test_agent_allows_loopback_host(host_server):
    assert _get_status(host_server, "/tools") == 200  # default 127.0.0.1 Host
    assert _get_status(host_server, "/tools", f"localhost:{host_server}") == 200
