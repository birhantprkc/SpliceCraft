"""test_babs — the BABS chat tab (Ollama-backed assistant).

Two halves:
  * TestBabsEngine — the app-free `splicecraft_babs` engine (Ollama/HF HTTP +
    chat protocol). Pure functions + bounded stream parsing; no real network
    (HF uses an injected fake opener; the NDJSON reader is fed bytes directly).
  * TestBabsUI — the real `BabsScreen` driven through `PlasmidApp` via the BABS
    menu action, with the engine's network calls monkeypatched. Verifies the
    streaming chat worker (incl. <think>-stripping), history, slash commands,
    and the model tab.

No network, no real files (the autouse `_protect_user_data` fixture sandboxes
the data dir; monkeypatches target the `splicecraft_babs` sibling namespace per
the [CONV] patch-the-sibling rule).
"""
import asyncio
import json

import pytest

import splicecraft as sc
import splicecraft_babs as B
from textual.widgets import DataTable, Input

_TERM = (170, 50)


# ── fakes ──────────────────────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self, n=-1):
        d, self._b = self._b, b""
        return d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    def __init__(self, payload):
        self.payload = payload

    def open(self, req, timeout=None):
        return _FakeResp(self.payload)


class _ByteStream:
    """Minimal file-like for _iter_ndjson: hands out `read(n)` chunks."""
    def __init__(self, data):
        self._buf = data

    def read(self, n):
        chunk, self._buf = self._buf[:n], self._buf[n:]
        return chunk


def _fake_chat(model, messages, **kwargs):
    for piece in ["Hello ", "<think>secret reasoning</think>", "**world**"]:
        yield {"content": piece, "thinking": "", "done": False, "error": None}
    yield {"content": "", "thinking": "", "done": True, "error": None}


_ONE_MODEL = [{"name": "qwen2.5:7b", "size": 4_600_000_000, "family": "qwen2",
               "params": "7.6B", "quant": "Q4_K_M", "modified": ""}]


# ── engine ──────────────────────────────────────────────────────────────────────
class TestBabsEngine:
    def test_strip_think(self):
        assert B.strip_think("<think>x</think>hi") == "hi"
        assert B.strip_think("ans<think>cut") == "ans"          # unclosed tolerated
        assert B.strip_think("plain") == "plain"

    def test_think_stripper_streaming_split_tags(self):
        ts = B.ThinkStripper()
        out = (ts.feed("hel") + ts.feed("lo <thi") + ts.feed("nk>hidden</thi")
               + ts.feed("nk> world") + ts.flush())
        assert out == "hello world"          # tag swallowed, lstrip after close
        assert "hidden" not in out

    def test_think_stripper_passthrough(self):
        ts = B.ThinkStripper()
        assert ts.feed("a") + ts.feed("**b**") + ts.flush() == "a**b**"

    def test_lifebar_depletes_and_trims(self):
        empty = B.lifebar([], reserve_tokens=10, num_ctx=4096)
        assert empty["pct"] >= 95 and empty["turns"] == 0 and empty["level"] == "ok"
        hist = [{"role": "user", "content": "x" * 100},
                {"role": "assistant", "content": "y" * 100}]
        used = B.lifebar(hist, reserve_tokens=10, num_ctx=4096)
        assert used["remaining"] <= empty["remaining"] and used["turns"] == 1
        big = [{"role": "user", "content": "z" * 80000},
               {"role": "assistant", "content": "z" * 80000}] * 3
        n0 = len(big)
        assert B.trim_history(big, reserve_tokens=100, num_ctx=4096) is True
        assert len(big) < n0

    def test_build_messages(self):
        msgs = B.build_messages("SYS", [{"role": "user", "content": "prev"}], "now")
        assert msgs[0] == {"role": "system", "content": "SYS"}
        assert msgs[-1] == {"role": "user", "content": "now"}
        assert len(msgs) == 3
        # empty system falls back to the Babs persona
        assert B.build_messages("", [], "q")[0]["content"] == B.BABS_SYSTEM

    def test_md_to_rich_styles_and_escapes(self):
        r = B.md_to_rich("# Head\n- item **bold** and `code`\nplain [red]x[/red]")
        assert "[b]Head[/b]" in r
        assert "[cyan]code[/cyan]" in r and "[b]bold[/b]" in r
        assert "•" in r
        assert r"\[red]" in r          # user markup neutralised
        fenced = B.md_to_rich("t\n```py\nx=1\n```\nu")
        assert "[dim cyan]x=1[/dim cyan]" in fenced and "```" not in fenced

    def test_parse_command_and_toggle(self):
        assert B.parse_command("/model qwen2.5:7b") == ("model", "qwen2.5:7b")
        assert B.parse_command("quit") == ("exit", "")
        assert B.parse_command("/HELP") == ("help", "")
        assert B.toggle_value("on", False) is True
        assert B.toggle_value("off", True) is False
        assert B.toggle_value("", True) is False          # bare flips

    def test_ollama_base_parsing(self, monkeypatch):
        monkeypatch.delenv("SPLICECRAFT_OLLAMA_HOST", raising=False)
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        assert B.ollama_base() == "http://127.0.0.1:11434"
        monkeypatch.setenv("OLLAMA_HOST", "myhost:1234")
        assert B.ollama_base() == "http://myhost:1234"
        monkeypatch.setenv("OLLAMA_HOST", "https://gpu.box")
        assert B.ollama_base() == "https://gpu.box:443"
        monkeypatch.setenv("OLLAMA_HOST", "::::garbage")
        assert B.ollama_base() == "http://127.0.0.1:11434"   # degrade, don't raise

    def test_fmt_size_and_clamp(self):
        assert B.fmt_size(4_683_087_332) == "4.4 GB"
        assert B.fmt_size(None) == "?"
        clamped, trunc = B.clamp_prompt("a" * (B.MAX_PROMPT_CHARS + 5))
        assert trunc and len(clamped) == B.MAX_PROMPT_CHARS

    def test_pull_progress_fraction(self):
        assert B.pull_progress_fraction({"total": 100, "completed": 50}) == 0.5
        assert B.pull_progress_fraction({"status": "pulling manifest"}) is None
        assert B.pull_progress_fraction({"total": 0, "completed": 0}) is None

    def test_hf_search_sorted_normalized(self):
        opener = _FakeOpener([
            {"id": "TheBloke/Foo-GGUF", "downloads": 1000, "likes": 5},
            {"id": "Bar/Baz-GGUF", "downloads": 9000, "likes": 1},
            {"garbage": True},
        ])
        res = B.hf_search_gguf("foo", opener=opener)
        assert [r["id"] for r in res] == ["Bar/Baz-GGUF", "TheBloke/Foo-GGUF"]
        assert res[0]["pull"] == "hf.co/Bar/Baz-GGUF"
        assert B.hf_search_gguf("", opener=opener) == []

    def test_iter_ndjson_parses_and_bounds(self):
        data = b'{"a":1}\n{"b":2}\ngarbage\n{"c":3}'
        got = list(B._iter_ndjson(_ByteStream(data), cancel=None,
                                  max_total=10**9, max_line=10**6))
        assert got == [{"a": 1}, {"b": 2}, {"c": 3}]     # bad line skipped, not fatal
        with pytest.raises(B.BabsError):
            list(B._iter_ndjson(_ByteStream(b"x" * 100), cancel=None,
                                max_total=10**9, max_line=10))
        with pytest.raises(B.BabsError):
            list(B._iter_ndjson(_ByteStream(b"a\n" * 100), cancel=None,
                                max_total=10, max_line=10**6))

    def test_iter_ndjson_cancel(self):
        class _C:
            def is_set(self):
                return True
        assert list(B._iter_ndjson(_ByteStream(b'{"a":1}\n'), cancel=_C(),
                                   max_total=10**9, max_line=10**6)) == []

    def test_iter_ndjson_skips_non_dict(self):
        # A stray scalar / array line must not reach the dict-expecting consumers.
        data = b'42\n{"a":1}\n["x"]\n"str"\n{"b":2}'
        got = list(B._iter_ndjson(_ByteStream(data), cancel=None,
                                  max_total=10**9, max_line=10**6))
        assert got == [{"a": 1}, {"b": 2}]

    def test_http_error_message_extracts_body(self):
        class _E:
            code = 404
            def read(self, n):
                return json.dumps({"error": "model 'foo' not found"}).encode()
        msg = B._http_error_message(_E())
        assert "404" in msg and "not found" in msg

    def test_ollama_base_ipv6_bracketed(self, monkeypatch):
        monkeypatch.delenv("SPLICECRAFT_OLLAMA_HOST", raising=False)
        monkeypatch.setenv("OLLAMA_HOST", "[::1]:11434")
        assert B.ollama_base() == "http://[::1]:11434"

    def test_model_is_installed_latest_tolerant(self, monkeypatch):
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        assert B.model_is_installed("qwen2.5:7b") is True
        assert B.model_is_installed("qwen2.5", _ONE_MODEL) is False  # different tag
        assert B.model_is_installed("nope:1b", _ONE_MODEL) is False

    def test_ping_false_without_server(self, monkeypatch):
        def _boom(**k):
            raise B.OllamaUnavailable("nope")
        monkeypatch.setattr(B, "list_installed", _boom)
        assert B.ping() is False


# ── UI (driven through the real app + menu action) ─────────────────────────────
class TestBabsUI:
    def test_babs_is_last_menu_item(self):
        assert sc.MenuBar.MENUS[-1] == "BABS"

    async def test_screen_opens_and_composes(self, monkeypatch):
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            assert isinstance(scr, sc.BabsScreen)
            for sel in ("#babs-log", "#babs-ctx-bar", "#babs-jobs-bar",
                        "#babs-input", "#babs-installed", "#babs-hf",
                        "#babs-jobs", "#babs-scrape-note"):
                scr.query_one(sel)

    async def test_chat_turn_strips_think_and_commits_history(self, monkeypatch):
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        monkeypatch.setattr(B, "chat_stream", _fake_chat)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            scr.query_one("#babs-input", Input).value = "hi there"
            scr._submit_current()
            for _ in range(80):
                await pilot.pause()
                await asyncio.sleep(0.03)
                if not scr._generating:
                    break
            assert not scr._generating
            assert len(scr._history) == 2
            answer = scr._history[1]["content"]
            assert "secret" not in answer and "world" in answer
            assert scr._transcript[-1][0] == "babs"

    async def test_slash_commands(self, monkeypatch):
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            scr._history.append({"role": "user", "content": "a"})
            scr._history.append({"role": "assistant", "content": "b"})
            scr._handle_command("/temp 0.7")
            assert abs(scr._temp - 0.7) < 1e-9
            scr._handle_command("/think on")
            assert scr._show_think is True
            scr._handle_command("/reset")
            assert scr._history == []
            scr._handle_command("/help")     # no crash
            scr._handle_command("/bogus")    # unknown → note, no crash

    # ── re-embed warning + scraper staleguard / keep-alive indicator ──────────
    def test_babs_embed_model_env(self, monkeypatch):
        monkeypatch.delenv("BABS_EMBED_MODEL", raising=False)
        assert sc.BabsScreen._babs_embed_model() == "nomic-embed-text"
        monkeypatch.setenv("BABS_EMBED_MODEL", "mxbai-embed-large")
        assert sc.BabsScreen._babs_embed_model() == "mxbai-embed-large"

    def test_job_running_staleguard(self):
        scr = sc.BabsScreen()
        # A pid that can't be alive → not running, never raises.
        assert scr._job_running({"pid": 2**31 - 1, "token": "x"}) is False
        # token absent → cmdline staleguard is a no-op (True), liveness decides.
        assert sc.BabsScreen._cmdline_has(99999999, None) is True

    def test_fmt_elapsed(self):
        import time as _t
        assert sc.BabsScreen._fmt_elapsed(None) == "0s"
        assert sc.BabsScreen._fmt_elapsed(_t.monotonic() - 90).startswith("1m")

    async def test_reembed_modal_default_no(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            result = {}
            await app.push_screen(sc.BabsReembedModal("m", "nomic-embed-text"),
                                  lambda v: result.setdefault("v", v))
            await pilot.pause(); await pilot.pause()
            modal = app.screen
            assert isinstance(modal, sc.BabsReembedModal)
            assert app.focused is modal.query_one("#babs-reembed-no")  # default No
            await pilot.press("escape")                                 # Esc → No
            await pilot.pause()
            assert result.get("v") is False

    async def test_apply_model_offers_reembed_when_babs_present(self, monkeypatch):
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            import pathlib
            monkeypatch.setattr(scr, "_resolve_babs_home", lambda: pathlib.Path("/tmp"))
            scr._apply_model("some-new-model")
            await pilot.pause(); await pilot.pause()
            assert isinstance(app.screen, sc.BabsReembedModal)
            await pilot.press("escape")    # No → back to chat, no re-ingest
            await pilot.pause()
            assert isinstance(app.screen, sc.BabsScreen)

    async def test_keepalive_indicator_shows_running(self, monkeypatch):
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            scr._jobs.insert(0, {"pid": 1, "kind": "get-paper", "label": "rice",
                                 "log": "", "started": "", "started_mono": 0.0,
                                 "token": "tok", "running": True})
            monkeypatch.setattr(scr, "_job_running", lambda job: True)  # force running
            scr._tick_indicator()
            await pilot.pause()
            ind = scr.query_one("#babs-ingest-indicator", sc.Static)
            text = str(ind.render())
            assert "ingest running" in text and "rice" in text
            assert scr._indicator_idle_shown is False

    async def test_model_tab_lists_installed(self, monkeypatch):
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            scr._load_installed()
            for _ in range(40):
                await pilot.pause(); await asyncio.sleep(0.02)
                if scr._installed_names:
                    break
            assert "qwen2.5:7b" in scr._installed_names
            t = scr.query_one("#babs-installed", DataTable)
            assert t.row_count == 1
