#!/usr/bin/env python3
"""splicecraft-cli — drive a running SpliceCraft GUI session from any
external CLI agent (Claude Code, Cursor, aider, hand-rolled scripts).

Connects to the localhost JSON API exposed by `splicecraft --agent-api`.
Reads connection details (port + bearer token) from
``~/.local/share/splicecraft/agent_token`` (or
``$SPLICECRAFT_DATA_DIR/agent_token`` when overridden), so the running
GUI is always the destination — no flag-fiddling required.

Stdlib-only by design: imports complete in ~50 ms (vs ~1.5 s for the
GUI module), so an AI agent firing dozens of commands per session
doesn't pay startup cost on every call.

Examples::

    splicecraft-cli status
    splicecraft-cli features
    splicecraft-cli fetch L09137
    splicecraft-cli load-entry pUC19
    splicecraft-cli add-feature 100 200 --label lacZ --type CDS --strand 1
    splicecraft-cli save

Use ``splicecraft-cli tools`` to list every endpoint the running
session exposes (handy when wiring a new tool surface for the agent).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6701
TOKEN_FILENAME = "agent_token"


def _data_dir() -> Path:
    """Resolve the SpliceCraft user-data directory the same way the
    GUI does — env override first, then the platform default. We keep
    a hand-rolled fallback so the CLI doesn't pull in `platformdirs`
    just for one path lookup (keeps imports stdlib-only)."""
    override = os.environ.get("SPLICECRAFT_DATA_DIR")
    if override:
        return Path(override).expanduser()
    try:
        from platformdirs import user_data_dir   # noqa: WPS433 (lazy)
        return Path(user_data_dir("splicecraft", appauthor=False,
                                    roaming=False))
    except ImportError:
        return Path.home() / ".local" / "share" / "splicecraft"


def _token_file() -> Path:
    return _data_dir() / TOKEN_FILENAME


def _read_session() -> tuple[str, int, str]:
    """Return `(host, port, token)` from the running session's token
    file. Exits with a helpful message if no session is up."""
    f = _token_file()
    if not f.exists():
        sys.exit(
            f"No SpliceCraft session found.\n"
            f"  Expected token file: {f}\n"
            f"  Start the GUI with: splicecraft --agent-api"
        )
    lines = f.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) < 2:
        sys.exit(
            f"Malformed token file at {f} "
            f"(expected `port\\ntoken`)."
        )
    try:
        port = int(lines[0].strip())
    except ValueError:
        sys.exit(f"Malformed port in {f}: {lines[0]!r}")
    return DEFAULT_HOST, port, lines[1].strip()


def _request(endpoint: str, method: str = "GET",
              payload: "dict | None" = None,
              timeout: float = 30.0) -> dict:
    host, port, token = _read_session()
    url = f"http://{host}:{port}/{endpoint}"
    data = (json.dumps(payload or {}).encode("utf-8")
            if method == "POST" else None)
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        try:
            err_payload = json.loads(body) if body else {}
            msg = err_payload.get("error", body or exc.reason)
        except json.JSONDecodeError:
            msg = body or exc.reason
        sys.exit(f"Error: {msg} (HTTP {exc.code})")
    except urllib.error.URLError as exc:
        sys.exit(
            f"Could not reach SpliceCraft at {host}:{port} ({exc.reason}).\n"
            f"  Is the GUI still running with --agent-api?"
        )
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError:
        return {"raw": body}


# ── Subcommand handlers ────────────────────────────────────────────────────────
# Each subcommand maps 1:1 to a `/<endpoint>` on the server. Keep these
# thin — the server is the source of truth for validation / error messages.

def _emit_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_status(args) -> None:
    _emit_json(_request("status"))


def cmd_tools(args) -> None:
    result = _request("tools")
    if args.json:
        _emit_json(result)
        return
    for ep in result.get("endpoints", []):
        flag = "WRITE" if ep.get("write") else "READ "
        print(f"  {flag}  {ep['name']:14}  {ep.get('doc', '')}")


def cmd_features(args) -> None:
    result = _request("features")
    feats = result.get("features", [])
    if args.json:
        _emit_json(feats)
        return
    if not feats:
        print("(no features)")
        return
    for f in feats:
        strand = ("+" if f["strand"] == 1
                   else "-" if f["strand"] == -1 else ".")
        label = f.get("label") or ""
        print(
            f"  [{f['idx']:3}] {f.get('type','?'):14} "
            f"{f['start'] + 1:>7,}..{f['end']:<7,} {strand}  {label}"
        )


def cmd_fetch(args) -> None:
    payload = {"accession": args.accession}
    if args.force:
        payload["force"] = True
    _emit_json(_request("fetch", "POST", payload, timeout=60))


def cmd_load_entry(args) -> None:
    payload = {"name": args.name}
    if args.force:
        payload["force"] = True
    _emit_json(_request("load-entry", "POST", payload))


def cmd_add_feature(args) -> None:
    payload = {
        "start":  args.start,
        "end":    args.end,
        "label":  args.label,
        "type":   args.type,
        "strand": args.strand,
    }
    if args.force:
        payload["force"] = True
    _emit_json(_request("add-feature", "POST", payload))


def cmd_save(args) -> None:
    _emit_json(_request("save", "POST"))


# ── Argparse wiring ────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="splicecraft-cli",
        description=(__doc__ or "").splitlines()[0] if __doc__ else "",
    )
    sub = parser.add_subparsers(dest="cmd", required=True,
                                  metavar="COMMAND")

    p_status = sub.add_parser("status",
                                help="Show what's loaded + dirty flag.")
    p_status.set_defaults(fn=cmd_status)

    p_tools = sub.add_parser("tools",
                                help="List all endpoints the session exposes.")
    p_tools.add_argument("--json", action="store_true",
                          help="Emit JSON instead of a table")
    p_tools.set_defaults(fn=cmd_tools)

    p_feat = sub.add_parser("features",
                              help="List features on the loaded record.")
    p_feat.add_argument("--json", action="store_true",
                         help="Emit JSON instead of a table")
    p_feat.set_defaults(fn=cmd_features)

    p_fetch = sub.add_parser("fetch",
                               help="Fetch a GenBank record from NCBI.")
    p_fetch.add_argument("accession",
                          help="GenBank accession (e.g. L09137).")
    p_fetch.add_argument("--force", action="store_true",
                          help="Override unsaved-changes guard.")
    p_fetch.set_defaults(fn=cmd_fetch)

    p_load = sub.add_parser("load-entry",
                              help="Load a plasmid library entry by name.")
    p_load.add_argument("name", help="Library entry name or id.")
    p_load.add_argument("--force", action="store_true",
                         help="Override unsaved-changes guard.")
    p_load.set_defaults(fn=cmd_load_entry)

    p_add = sub.add_parser(
        "add-feature",
        help="Add a feature to the loaded record.",
    )
    p_add.add_argument("start", type=int, help="0-based start bp.")
    p_add.add_argument("end",   type=int,
                        help="0-based end bp (exclusive). For wrap "
                             "features, pass end < start.")
    p_add.add_argument("--label", default="",
                        help="Feature label (qualifier).")
    p_add.add_argument("--type", default="misc_feature",
                        help="GenBank feature type (CDS, promoter, …).")
    p_add.add_argument("--strand", type=int, default=1,
                        choices=[-1, 0, 1],
                        help="1=forward (default), -1=reverse, 0=both.")
    p_add.add_argument("--force", action="store_true",
                        help="Override unsaved-changes guard.")
    p_add.set_defaults(fn=cmd_add_feature)

    p_save = sub.add_parser("save",
                              help="Save the loaded record (file + library).")
    p_save.set_defaults(fn=cmd_save)

    return parser


def main(argv=None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
