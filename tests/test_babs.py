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
import os
import time

import pytest

import splicecraft as sc
import splicecraft_babs as B
from textual.widgets import DataTable, Input, ProgressBar

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

# The "Mythos dupe" — a real public GGUF on HuggingFace (qwen3.5-9B, 1M ctx):
# a concrete fixture for the pull-progress pipeline AND (opt-in) a genuine
# end-to-end download test of the BABS backend. MYTHOS_Q4_BYTES is its
# MTP-Q4_K_M layer size — the quant Ollama resolves by default — so the human
# readout is asserted against a real model's byte counts.
MYTHOS_MODEL = "hf.co/empero-ai/Qwythos-9B-Claude-Mythos-5-1M-GGUF"
MYTHOS_Q4_BYTES = 5_887_668_064


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

    def test_fmt_speed(self):
        assert B.fmt_speed(0) == "" and B.fmt_speed(None) == ""
        assert B.fmt_speed(-5) == "" and B.fmt_speed("x") == ""
        assert B.fmt_speed(12 * 1024 * 1024) == "12.0 MB/s"

    def test_pull_meter_mythos_readout(self):
        # Replay a Mythos-shaped pull on a synthetic clock: manifest, then the
        # big Q4 layer downloading at a steady 50 MiB/s. The readout must carry
        # bytes-pulled, percent AND a real speed so a user sees it MOVING.
        m = B.PullMeter(window=3.0)
        assert m.update({"status": "pulling manifest"}, 0.0)["bps"] is None
        rate = 50 * 1024 * 1024
        last = None
        for i in range(1, 9):
            last = m.update({"status": "pulling 671c430bf18c", "digest": "671c430bf18c",
                             "total": MYTHOS_Q4_BYTES, "completed": i * rate}, float(i))
        assert last["fraction"] is not None and 0.0 < last["fraction"] < 1.0
        assert abs(last["bps"] - rate) < 1.0                  # linear ramp ⇒ exact
        assert B.fmt_speed(last["bps"]) == "50.0 MB/s"
        assert "50.0 MB/s" in last["text"] and "%" in last["text"]
        assert "/5.5 GB" in last["text"]                      # bytes pulled / total
        # the success phase carries no bytes ⇒ no bogus trailing speed
        done = m.update({"status": "success"}, 99.0)
        assert done["bps"] is None and done["text"] == "success"

    def test_pull_meter_no_negative_speed_across_layers(self):
        # Real-world trap (observed in a live Mythos pull): a 5.5 GB layer
        # finishes, then a tiny new layer starts at a low byte count. A naive
        # (completed-prev)/dt reads a huge NEGATIVE rate (-78 MB/s was seen);
        # the meter resets its speed baseline per layer (digest changes) so a
        # negative / spiked speed can never surface.
        m = B.PullMeter()
        m.update({"status": "pulling AAA", "digest": "AAA",
                  "total": MYTHOS_Q4_BYTES, "completed": 1_000_000_000}, 0.0)
        m.update({"status": "pulling AAA", "digest": "AAA",
                  "total": MYTHOS_Q4_BYTES, "completed": MYTHOS_Q4_BYTES}, 1.0)
        info = m.update({"status": "pulling BBB", "digest": "BBB",
                         "total": 481, "completed": 481}, 1.1)
        assert info["bps"] is None                            # fresh-layer baseline
        assert "-" not in (B.fmt_speed(info["bps"]) or "")
        info2 = m.update({"status": "pulling BBB", "digest": "BBB",
                          "total": 481, "completed": 481}, 1.2)
        assert info2["bps"] is None or info2["bps"] >= 0      # never negative

    def test_pull_meter_tolerates_garbage(self):
        m = B.PullMeter()
        for obj in ({}, {"status": "x", "total": "nan", "completed": None},
                    {"total": 0, "completed": 0}, {"completed": 5}, {"total": 9}):
            info = m.update(obj, 1.0)
            assert info["bps"] is None                        # never a rate, never raises
            assert isinstance(info["text"], str)

    def test_delete_model_validates_and_bounds(self, monkeypatch):
        # Empty name → BabsError (no request). Unreachable host → OllamaUnavailable.
        with pytest.raises(B.BabsError):
            B.delete_model("")
        monkeypatch.setenv("SPLICECRAFT_OLLAMA_HOST", "http://127.0.0.1:6")  # dead port
        with pytest.raises(B.OllamaUnavailable):
            B.delete_model("ghost:1b", timeout=2)

    def test_model_collections_roundtrip(self):
        # The new data layer mirrors plasmid collections: safe-save envelope,
        # deep-copy-on-read independence (#17), case-insensitive name check.
        # (The autouse _protect_user_data fixture sandboxes + authorizes writes.)
        sc._save_model_collections([])
        assert sc._load_model_collections() == []
        colls = [{"name": "My Models", "description": "", "saved": "2026-06-29",
                  "models": [{"ref": "qwen2.5:7b", "note": "", "added": "2026-06-29"}]}]
        sc._save_model_collections(colls)
        assert sc._load_model_collections() == colls
        assert sc._find_model_collection("My Models")["models"][0]["ref"] == "qwen2.5:7b"
        assert sc._find_model_collection("nope") is None
        assert sc._model_collection_name_taken("MY MODELS") is True   # case-insensitive
        assert sc._model_collection_name_taken("other") is False
        # mutating a loaded copy must not poison the cache (#17)
        got = sc._load_model_collections()
        got[0]["models"].append({"ref": "EVIL"})
        assert len(sc._load_model_collections()[0]["models"]) == 1

    @pytest.mark.skipif(
        not os.environ.get("SPLICECRAFT_BABS_LIVE"),
        reason="live Ollama pull — set SPLICECRAFT_BABS_LIVE=1 (downloads from HuggingFace)")
    def test_qwythos_live_pull_shows_real_speed(self):
        # Genuine end-to-end: pull the Mythos GGUF THROUGH the engine, drive a
        # real PullMeter, and assert bytes advance at a measurable speed — then
        # cancel so we don't fetch the whole multi-GB blob. Staleguarded: skips
        # cleanly when Ollama is down OR the model is already cached locally.
        import threading
        if not B.ping():
            pytest.skip("no local Ollama server")
        cancel = threading.Event()
        meter = B.PullMeter()
        saw_speed = False
        max_completed = 0.0
        try:
            for obj in B.pull_stream(MYTHOS_MODEL, cancel=cancel):
                if obj.get("error"):
                    pytest.skip(f"pull error (network/model): {obj['error']}")
                info = meter.update(obj, time.monotonic())
                if info["completed"]:
                    max_completed = max(max_completed, info["completed"])
                if info["bps"] and info["bps"] > 0:
                    saw_speed = True
                if saw_speed and max_completed > 4_000_000:    # ~4 MB in ⇒ enough
                    break
        finally:
            cancel.set()
        if not saw_speed:
            pytest.skip("no live download observed (model already cached) — "
                        "`ollama rm hf.co/empero-ai/Qwythos-9B-Claude-Mythos-5-1M-GGUF` to re-test")
        assert max_completed > 0

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
                        "#babs-input", "#babs-coll-select", "#babs-models",
                        "#babs-addname", "#babs-hf", "#babs-jobs",
                        "#babs-scrape-note"):
                scr.query_one(sel)

    async def test_panes_fill_height_not_collapsed(self, monkeypatch):
        """Regression: the pane content boxes shipped with `height: 100%`, which
        resolves against the TabPane's default `height: auto` and collapses to 0
        — blanking every tab (no buttons, no table, just a black void). The
        existing compose test only checks widgets EXIST, not that they're laid
        out with non-zero size, so it missed this. Assert the panes actually
        fill the height between Header and Footer."""
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            # Chat is the initial active tab. All three content boxes share one
            # CSS rule, so a collapse here is a collapse everywhere.
            assert scr.query_one("#babs-chat-box").region.height > 20, \
                "chat pane collapsed (height: 100% inside auto TabPane?)"
            assert scr.query_one("#babs-log").region.height > 15, \
                "transcript log collapsed"
            # Model tab: switch, wait for the load worker, then pin focus inside
            # the pane so the mount-time #babs-input focus race can't yank the
            # active tab back to Chat before we measure the regions.
            tabs = scr.query_one("#babs-tabs", sc.TabbedContent)
            tabs.active = "babs-tab-model"
            for _ in range(60):
                await pilot.pause(); await asyncio.sleep(0.02)
                if scr._active_model_coll:
                    break
            scr.query_one("#babs-models").focus()
            tabs.active = "babs-tab-model"
            for _ in range(6):
                await pilot.pause(); await asyncio.sleep(0.02)
            for sel in ("#babs-model-box", "#babs-coll-select",
                        "#babs-models", "#babs-use"):
                r = scr.query_one(sel).region
                assert r.width > 0 and r.height > 0, f"{sel} collapsed: {r}"

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

    async def test_corpus_toggle_gates_on_corpus(self, monkeypatch):
        """The Corpus toggle is disabled (and forced off) without a corpus, and flips on when one
        exists — it can't promise grounding it can't deliver."""
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            monkeypatch.setattr(scr, "_corpus_available", lambda: False)
            scr._refresh_ground_button()
            btn = scr.query_one("#babs-ground")
            assert btn.disabled and scr._grounded is False
            monkeypatch.setattr(scr, "_corpus_available", lambda: True)
            scr._refresh_ground_button()
            assert not scr.query_one("#babs-ground").disabled
            scr._on_ground_toggle()
            assert scr._grounded and "on" in str(scr.query_one("#babs-ground").label)

    async def test_grounded_chat_streams_rag_bot(self, monkeypatch, tmp_path):
        """With Corpus on, a question shells rag_bot --plain and streams its cited answer into the
        bubble + history — and the plain Ollama chat path is NOT used."""
        import subprocess as _sp
        import types as _types
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        monkeypatch.setattr(B, "chat_stream", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("plain chat path used while grounded")))
        out = b"Callus forms on MS + 2,4-D [1].\n\nSources:\n  [1] (CORE) Plant growth regulators\n"

        def fake_popen(*a, **k):
            r, w = os.pipe(); os.write(w, out); os.close(w)
            p = _types.SimpleNamespace(stdout=os.fdopen(r, "rb", 0))
            p.wait = lambda timeout=None: 0
            p.terminate = lambda: None
            p.kill = lambda: None
            return p
        monkeypatch.setattr(_sp, "Popen", fake_popen)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            monkeypatch.setattr(scr, "_resolve_babs_home", lambda: tmp_path)
            monkeypatch.setattr(scr, "_corpus_available", lambda: True)
            scr._grounded = True
            scr.query_one("#babs-input", Input).value = "2,4-D for callus?"
            scr._submit_current()
            for _ in range(80):
                await pilot.pause(); await asyncio.sleep(0.03)
                if not scr._generating:
                    break
            assert not scr._generating
            ans = scr._history[-1]["content"]
            assert "Callus forms" in ans and "Sources:" in ans     # streamed + cited

    async def test_grounded_plus_agent_surfaces_conflict(self, monkeypatch, tmp_path):
        """Both Corpus and Agent on: grounded wins (corpus is a separate cited-answer
        subprocess), but the Agent toggle is NOT silently ignored — a note says agent
        actions are paused this turn."""
        import subprocess as _sp
        import types as _types
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        monkeypatch.setattr(B, "chat_stream", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("agentic/plain chat path used while grounded")))
        out = b"Answer [1].\n\nSources:\n  [1] (CORE) x\n"

        def fake_popen(*a, **k):
            r, w = os.pipe(); os.write(w, out); os.close(w)
            p = _types.SimpleNamespace(stdout=os.fdopen(r, "rb", 0))
            p.wait = lambda timeout=None: 0
            p.terminate = lambda: None
            p.kill = lambda: None
            return p
        monkeypatch.setattr(_sp, "Popen", fake_popen)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            monkeypatch.setattr(scr, "_resolve_babs_home", lambda: tmp_path)
            monkeypatch.setattr(scr, "_corpus_available", lambda: True)
            notes: "list[str]" = []
            monkeypatch.setattr(scr, "_sys_note",
                                lambda m, *a, **k: notes.append(m))
            scr._grounded = True
            scr._agent_enabled = True                # both toggles on
            scr.query_one("#babs-input", Input).value = "q?"
            scr._submit_current()
            for _ in range(80):
                await pilot.pause(); await asyncio.sleep(0.03)
                if not scr._generating:
                    break
            assert not scr._generating
            assert any("paused this turn" in n for n in notes)   # conflict surfaced
            assert "Answer" in scr._history[-1]["content"]        # grounded still ran

    async def test_setup_checklist_on_ollama_down(self, monkeypatch):
        """When Ollama is unreachable, the Chat tab shows a numbered first-run checklist
        (install Ollama → pull a model → babs-setup) instead of a bare connection error."""
        def _down(**k):
            raise B.OllamaUnavailable("connection refused")
        monkeypatch.setattr(B, "list_installed", _down)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            for _ in range(20):
                await pilot.pause(); await asyncio.sleep(0.02)
            scr = app.screen          # opened cleanly despite Ollama being down (no crash)
            cl = scr._setup_checklist("connection refused")
            assert "Install Ollama" in cl and "ollama pull" in cl and "babs-setup" in cl

    async def test_thinking_spinner_animates_until_first_token(self, monkeypatch):
        """The braille 'thinking' spinner turns while a turn is in flight — in
        both the assistant bubble and the jobs-bar — and stops the moment a
        visible token streams, so it never clobbers the answer."""
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            SPIN = sc._BABS_SPINNER
            plain = lambda w: getattr(w.visual, "plain", str(w.visual))
            # Simulate the start of a turn without a real Ollama call.
            scr._think_i = 0
            scr._got_visible = False
            scr._cur_assistant = scr._mount_bubble(
                f"[yellow]{SPIN[0]}[/yellow] thinking", "babs-asst")
            scr._generating = True
            await pilot.pause()            # let the bubble actually mount
            scr._refresh_jobs_bar()
            # Each tick advances the braille frame in the bubble + jobs-bar
            # (update() applies on the next refresh, so pause before reading).
            scr._tick_thinking()
            await pilot.pause()
            assert scr._think_i == 1
            assert SPIN[1] in plain(scr._cur_assistant)
            jb = scr.query_one("#babs-jobs-bar")
            assert SPIN[1] in plain(jb) and "answering" in plain(jb)
            scr._tick_thinking()
            await pilot.pause()
            assert scr._think_i == 2 and SPIN[2] in plain(scr._cur_assistant)
            # First visible token replaces the spinner; later ticks keep the text.
            scr._render_assistant("hello world")
            await pilot.pause()
            assert scr._got_visible is True
            scr._tick_thinking()
            await pilot.pause()
            assert "hello world" in plain(scr._cur_assistant)
            # Ending the turn freezes the spinner (tick becomes a no-op).
            scr._generating = False
            frozen = scr._think_i
            scr._tick_thinking()
            assert scr._think_i == frozen

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

    async def _open_model_tab(self, monkeypatch, pilot, app, models=None):
        """Open BABS + activate the Model tab, wait for the load worker to seed
        the default collection. Returns the BabsScreen."""
        monkeypatch.setattr(B, "list_installed",
                            lambda **k: (models if models is not None else _ONE_MODEL))
        app.action_open_babs()
        await pilot.pause(); await pilot.pause()
        scr = app.screen
        scr.query_one("#babs-tabs", sc.TabbedContent).active = "babs-tab-model"
        for _ in range(60):
            await pilot.pause(); await asyncio.sleep(0.02)
            if scr._active_model_coll:
                break
        return scr

    async def test_model_tab_seeds_default_collection(self, monkeypatch):
        # Opening the Model tab auto-creates "My Models" and files every
        # installed model into it (mirrors "the library holds all plasmids").
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            scr = await self._open_model_tab(monkeypatch, pilot, app)
            assert scr._active_model_coll == "My Models"
            assert "qwen2.5:7b" in [r for r in scr._model_refs if r]
            t = scr.query_one("#babs-models", sc._BabsModelTable)
            assert t.row_count == 1
            assert sc._find_model_collection("My Models") is not None

    async def test_model_collection_crud_and_marking(self, monkeypatch):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            scr = await self._open_model_tab(monkeypatch, pilot, app)
            # New collection
            scr._on_coll_new_name("Bio")
            await pilot.pause()
            assert scr._active_model_coll == "Bio" and scr._model_refs == [None]
            # name collision rejected (no dup created)
            scr._on_coll_new_name("my models")
            assert sum(1 for c in sc._iter_model_collections_readonly()
                       if c["name"].lower() == "my models") == 1
            # add a ref + dedupe
            assert scr._add_ref_to_active("llama3.2:3b") is True
            assert scr._add_ref_to_active("llama3.2:3b") is False     # dedupe
            scr._repopulate_after_change(); await pilot.pause()
            assert "llama3.2:3b" in scr._model_refs
            # mark + move Bio → My Models
            scr._marked_refs.add("llama3.2:3b")
            scr._move_commit(["llama3.2:3b"], "My Models")
            await pilot.pause()
            assert all(m["ref"] != "llama3.2:3b"
                       for m in sc._find_model_collection("Bio")["models"])
            assert any(m["ref"] == "llama3.2:3b"
                       for m in sc._find_model_collection("My Models")["models"])
            # rename Bio
            scr._active_model_coll = "Bio"
            scr._on_coll_renamed("Plants")
            assert sc._find_model_collection("Plants") is not None
            assert sc._find_model_collection("Bio") is None

    async def test_uninstall_unfiles_and_calls_delete(self, monkeypatch):
        calls = []
        monkeypatch.setattr(B, "delete_model", lambda ref, **k: calls.append(ref) or True)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            scr = await self._open_model_tab(monkeypatch, pilot, app)
            # an INSTALLED ref → ollama rm is called, then it's unfiled
            scr._installed_index = {"qwen2.5:7b": _ONE_MODEL[0]}
            scr._uninstall_confirm(["qwen2.5:7b"], True)
            for _ in range(50):
                await pilot.pause(); await asyncio.sleep(0.02)
                if not scr._busy_models:
                    break
            assert calls == ["qwen2.5:7b"]
            assert all(m["ref"] != "qwen2.5:7b"
                       for m in sc._find_model_collection("My Models")["models"])
            # a NOT-installed ref → just unfiled, ollama rm NOT called (staleguard)
            calls.clear()
            scr._on_coll_new_name("Wishlist")
            scr._add_ref_to_active("hf.co/some/ghost-GGUF")
            scr._installed_index = {}
            scr._uninstall_confirm(["hf.co/some/ghost-GGUF"], True)
            for _ in range(50):
                await pilot.pause(); await asyncio.sleep(0.02)
                if not scr._busy_models:
                    break
            assert calls == []        # nothing on disk → no ollama rm
            assert sc._find_model_collection("Wishlist")["models"] == []

    async def test_model_tab_staleguard_ollama_down(self, monkeypatch):
        # Ollama unreachable: the picker still creates the default collection,
        # shows any filed refs as "not pulled", and never crashes.
        def _down(**k):
            raise B.OllamaUnavailable("connection refused")
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            # pre-seed a collection with a ref so there's something to show
            sc._save_model_collections([
                {"name": "My Models", "description": "", "saved": "2026-06-29",
                 "models": [{"ref": "llama3.2:3b", "note": "", "added": "2026-06-29"}]}])
            monkeypatch.setattr(B, "list_installed", _down)
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            scr.query_one("#babs-tabs", sc.TabbedContent).active = "babs-tab-model"
            for _ in range(60):
                await pilot.pause(); await asyncio.sleep(0.02)
                if scr._active_model_coll:
                    break
            assert scr._active_model_coll == "My Models"
            assert "llama3.2:3b" in scr._model_refs          # ref shown despite Ollama down
            status = str(scr.query_one("#babs-pull-status", sc.Static).render())
            assert "Ollama" in status

    async def test_ref_and_collection_name_sanitized(self, monkeypatch):
        # Control chars / pathological length on a collection name or model ref
        # are stripped + capped (no newline can break the Select, no 1 MB paste).
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            scr = await self._open_model_tab(monkeypatch, pilot, app)
            scr._on_coll_new_name("Bio\x07\x01models" + "z" * 400)
            nm = scr._active_model_coll
            assert nm.startswith("Bio") and len(nm) <= 200
            assert all(ord(ch) >= 0x20 for ch in nm)          # no control chars
            # NUL stripped from a ref, but '/' ':' '.' '-' preserved
            assert scr._add_ref_to_active("hf.co/o\x00wner/repo-GGUF:Q4") is True
            refs = [m["ref"] for m in sc._find_model_collection(nm)["models"]]
            assert "hf.co/owner/repo-GGUF:Q4" in refs
            # an all-control-char ref sanitizes to empty → rejected, no row
            assert scr._add_ref_to_active("\x00\x07\x01") is False

    async def test_delete_last_collection_is_safe(self, monkeypatch):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            scr = await self._open_model_tab(monkeypatch, pilot, app)
            for _ in range(8):                                # delete every collection
                if not scr._active_model_coll:
                    break
                scr._on_coll_del_confirm(True)
                await pilot.pause()
            assert scr._active_model_coll == ""
            assert scr._model_refs == [None]                  # placeholder row, no crash
            scr._on_coll_new_name("Fresh")                    # New still works
            assert scr._active_model_coll == "Fresh"

    async def test_pull_progress_renders_speed_and_bytes(self, monkeypatch):
        # The pull status line must surface bytes-pulled + percent + speed (not
        # a bare percent) and advance the bar, so a big download visibly moves.
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            meter = B.PullMeter()
            meter.update({"status": "pulling X", "digest": "X",
                          "total": MYTHOS_Q4_BYTES, "completed": 0}, 0.0)
            info = meter.update({"status": "pulling X", "digest": "X",
                                 "total": MYTHOS_Q4_BYTES,
                                 "completed": MYTHOS_Q4_BYTES // 5}, 2.0)
            scr._pull_progress(info)
            await pilot.pause()
            status = str(scr.query_one("#babs-pull-status", sc.Static).render())
            assert "/s" in status and "%" in status and "GB" in status
            bar = scr.query_one("#babs-pull-bar", ProgressBar)
            assert (bar.percentage or 0) > 0


def test_babs_setup_cli_help_and_git_missing(monkeypatch, capsys):
    """`splicecraft babs-setup --help` prints its own help (exit 0); a missing `git` fails cleanly
    (exit 1) without attempting a clone."""
    import shutil
    assert sc._run_babs_setup_subcommand(["--help"]) == 0
    assert "babs-setup" in capsys.readouterr().out
    monkeypatch.setattr(shutil, "which", lambda _x: None)   # pretend git isn't installed
    assert sc._run_babs_setup_subcommand(["--dir", "/tmp/sc-no-babs-xyz"]) == 1
    assert "git" in capsys.readouterr().err.lower()


# ── Agentic Babs: the in-process tool-loop that gives internal Babs the same
#    power as the external HTTP side-door (2026-06-30) ───────────────────────────
class TestBabsAgentic:
    # — engine: chat_stream tool support —
    def test_chat_stream_passes_tools_and_surfaces_tool_calls(self, monkeypatch):
        captured = {}

        def fake_open_stream(path, payload, *, timeout, register=None):
            captured["payload"] = payload
            return object()         # opaque resp; _iter_ndjson is mocked below

        def fake_iter(resp, *, cancel, max_total, max_line):
            yield {"message": {"content": "", "tool_calls": [
                {"function": {"name": "splicecraft_call",
                              "arguments": {"endpoint": "status"}}}]},
                   "done": False}
            yield {"message": {"content": "done"}, "done": True,
                   "done_reason": "stop"}

        monkeypatch.setattr(B, "_open_stream", fake_open_stream)
        monkeypatch.setattr(B, "_iter_ndjson", fake_iter)
        tools = [{"type": "function", "function": {"name": "x", "parameters": {}}}]
        chunks = list(B.chat_stream("m", [{"role": "user", "content": "hi"}],
                                    tools=tools))
        assert captured["payload"].get("tools") == tools
        assert chunks[0]["tool_calls"][0]["function"]["name"] == "splicecraft_call"
        assert chunks[-1]["done"] is True and chunks[-1]["done_reason"] == "stop"

    def test_chat_stream_omits_tools_when_none(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(B, "_open_stream",
                            lambda p, payload, **k: captured.update(payload=payload) or object())
        monkeypatch.setattr(B, "_iter_ndjson", lambda *a, **k: iter([]))
        list(B.chat_stream("m", [{"role": "user", "content": "hi"}]))
        assert "tools" not in captured["payload"]   # plain chat is unchanged

    # — hub: the 3 meta-tools + online-search + fetch-page + catalog —
    def test_tool_manifest_shape(self):
        names = {t["function"]["name"] for t in sc._babs_tool_manifest()}
        assert names == {"splicecraft_list_endpoints",
                         "splicecraft_describe_endpoint", "splicecraft_call",
                         "splicecraft_search_online", "splicecraft_fetch_page"}

    def test_fetch_page_tool_requires_url(self):
        # The page-reader tool must take a `url` (required) so the model always
        # supplies a target; it dispatches to the read-only `read-url` endpoint.
        tool = next(t for t in sc._babs_tool_manifest()
                    if t["function"]["name"] == "splicecraft_fetch_page")
        assert set(tool["function"]["parameters"]["required"]) == {"url"}
        assert sc._AGENT_HANDLERS["read-url"][1] is False

    def test_search_tool_enum_matches_sources(self):
        # The tool's `source` enum must stay in lockstep with the endpoint map,
        # so the model can never offer a source that doesn't route anywhere.
        tool = next(t for t in sc._babs_tool_manifest()
                    if t["function"]["name"] == "splicecraft_search_online")
        enum = tool["function"]["parameters"]["properties"]["source"]["enum"]
        assert set(enum) == set(sc._BABS_SEARCH_SOURCES)
        assert "required" in tool["function"]["parameters"]
        assert set(tool["function"]["parameters"]["required"]) == {"source", "query"}

    def test_babs_search_sources_map_to_real_readonly_endpoints(self):
        # Every source must dispatch to a REGISTERED, READ-ONLY endpoint — a
        # name lookup never writes, so it must not trip the write-approval gate,
        # and a typo'd endpoint would 'unknown endpoint' at runtime.
        for src, ep in sc._BABS_SEARCH_SOURCES.items():
            entry = sc._AGENT_HANDLERS.get(ep)
            assert entry is not None, f"{src} → {ep!r} is not a registered endpoint"
            assert entry[1] is False, f"{ep!r} must be read-only (write flag set)"

    def test_list_endpoints_catalog_and_filter(self):
        full = sc._babs_list_endpoints({})
        assert full["count"] > 150 and full["count"] == len(full["endpoints"])
        primers = sc._babs_list_endpoints({"filter": "primer"})
        assert primers["count"] >= 1
        assert all("primer" in e["name"] for e in primers["endpoints"])

    def test_describe_endpoint(self):
        out = sc._babs_describe_endpoint({"endpoint": "status"})
        assert out["name"] == "status" and out["doc_full"]
        miss = sc._babs_describe_endpoint({"endpoint": "no-such"})
        assert "error" in miss

    # — hub: _run_tool_call routing (no app needed for meta-tools) —
    def test_run_tool_call_meta_and_unknown(self):
        scr = sc.BabsScreen()
        out = scr._run_tool_call(
            {"function": {"name": "splicecraft_list_endpoints", "arguments": {}}})
        assert out["count"] > 150
        out2 = scr._run_tool_call(
            {"function": {"name": "splicecraft_describe_endpoint",
                          "arguments": '{"endpoint": "status"}'}})   # JSON-string args
        assert out2["name"] == "status"
        assert "error" in scr._run_tool_call({"function": {"name": "bogus"}})

    # — hub: splicecraft_search_online routes source → the right *-search endpoint —
    def test_run_tool_call_search_online_routes_source_to_endpoint(self):
        scr = sc.BabsScreen()
        seen: dict = {}
        # Shadow the dispatcher so we assert the ROUTING without an app / network.
        scr._dispatch_agent_endpoint = (
            lambda ep, body: seen.update(ep=ep, body=body) or {"ok": True})
        for src, ep in sc._BABS_SEARCH_SOURCES.items():
            seen.clear()
            scr._run_tool_call({"function": {
                "name": "splicecraft_search_online",
                "arguments": {"source": src, "query": "GFP"}}})
            assert seen["ep"] == ep, f"source {src!r} routed to {seen.get('ep')!r}"
            assert seen["body"]["query"] == "GFP"
            assert "max_hits" not in seen["body"]        # omitted when unset
        # max_hits threads through when the model supplies it.
        seen.clear()
        scr._run_tool_call({"function": {
            "name": "splicecraft_search_online",
            "arguments": {"source": "genbank", "query": "cas9", "max_hits": 5}}})
        assert seen["ep"] == "genbank-search" and seen["body"]["max_hits"] == 5

    def test_run_tool_call_search_online_unknown_source(self):
        scr = sc.BabsScreen()
        out = scr._run_tool_call({"function": {
            "name": "splicecraft_search_online",
            "arguments": {"source": "nope", "query": "x"}}})
        assert "error" in out and "unknown search source" in out["error"]

    # — hub: splicecraft_fetch_page routes to the read-url endpoint —
    def test_run_tool_call_fetch_page_routes_to_read_url(self):
        scr = sc.BabsScreen()
        seen: dict = {}
        scr._dispatch_agent_endpoint = (
            lambda ep, body: seen.update(ep=ep, body=body) or {"ok": True})
        scr._run_tool_call({"function": {
            "name": "splicecraft_fetch_page",
            "arguments": {"url": "https://example.com"}}})
        assert seen["ep"] == "read-url"
        assert seen["body"]["url"] == "https://example.com"
        # max_chars threads through when the model supplies it.
        seen.clear()
        scr._run_tool_call({"function": {
            "name": "splicecraft_fetch_page",
            "arguments": {"url": "https://e.com", "max_chars": 4000}}})
        assert seen["body"]["max_chars"] == 4000

    def test_run_tool_call_fetch_page_missing_url(self):
        scr = sc.BabsScreen()
        out = scr._run_tool_call({"function": {
            "name": "splicecraft_fetch_page", "arguments": {"url": "  "}}})
        assert "error" in out and "url" in out["error"]

    async def test_search_online_flows_through_dispatch_to_agent_invoke(
            self, monkeypatch):
        # End-to-end wiring inside a live app: a model `splicecraft_search_online`
        # tool call must flow _run_tool_call → _dispatch_agent_endpoint →
        # _agent_invoke against the real (read-only) *-search endpoint, with NO
        # write-approval gate. `_agent_invoke` is stubbed so no network is hit.
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        monkeypatch.setattr(B, "show", lambda *a, **k: {})
        seen: dict = {}

        def fake_invoke(app, endpoint, body, source=None):
            seen.update(endpoint=endpoint, body=body, source=source)
            return {"ok": True, "source": "fpbase", "hits": []}, 200

        monkeypatch.setattr(sc, "_agent_invoke", fake_invoke)
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            assert isinstance(scr, sc.BabsScreen)
            out = scr._run_tool_call({"function": {
                "name": "splicecraft_search_online",
                "arguments": {"source": "fpbase", "query": "GFP"}}})
        assert seen["endpoint"] == "fpbase-search"
        assert seen["body"] == {"query": "GFP"}
        assert seen["source"] == "babs"                     # side-door parity
        assert out["status"] == 200 and out["result"]["ok"] is True

    # — hub: the autonomy policy gate —
    def test_dispatch_readonly_refuses_writes(self):
        scr = sc.BabsScreen()
        scr._autonomy = "readonly"
        out = scr._dispatch_agent_endpoint(
            "set-feature-color", {"feature_type": "CDS", "color": "#123456"})
        assert "error" in out and "read-only" in out["error"]

    def test_dispatch_unknown_endpoint(self):
        scr = sc.BabsScreen()
        out = scr._dispatch_agent_endpoint("no-such-endpoint", {})
        assert "error" in out and "unknown endpoint" in out["error"]

    def test_summarize_tool_result(self):
        assert "error" in sc.BabsScreen._summarize_tool_result({"error": "boom"})
        s = sc.BabsScreen._summarize_tool_result({"status": 200, "result": {"ok": True}})
        assert "200" in s
        assert "3 item" in sc.BabsScreen._summarize_tool_result(
            {"count": 3, "endpoints": []})

    # — UI: the Agent toggle + /autonomy command drive the persisted state —
    async def test_agent_toggle_and_autonomy_command(self, monkeypatch):
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        monkeypatch.setattr(B, "show", lambda *a, **k: {})
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr = app.screen
            assert isinstance(scr, sc.BabsScreen)
            assert scr._agent_enabled is False           # default off
            scr._on_agent_toggle()                       # arm it
            await pilot.pause()
            assert scr._agent_enabled is True
            scr._handle_command("/autonomy auto")        # full autonomy
            await pilot.pause()
            assert scr._autonomy == "auto" and scr._agent_enabled is True
            btn = scr.query_one("#babs-agent", sc.Button)
            assert "auto" in str(btn.label)
            scr._handle_command("/autonomy off")         # disarm via command
            await pilot.pause()
            assert scr._agent_enabled is False

    # — persistence: the session survives close → reopen —
    async def test_session_persists_across_close_reopen(self, monkeypatch):
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        monkeypatch.setattr(B, "show", lambda *a, **k: {})
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr1 = app.screen
            assert isinstance(scr1, sc.BabsScreen)
            # simulate a completed turn + arm agent mode
            scr1._history.extend([{"role": "user", "content": "hi"},
                                  {"role": "assistant", "content": "hello there"}])
            scr1._transcript.extend([("you", "hi"), ("babs", "hello there")])
            scr1._on_agent_toggle()
            await pilot.pause()
            assert scr1._agent_enabled is True
            # close Babs (Esc / dismiss)
            scr1.dismiss()
            await pilot.pause(); await pilot.pause()
            assert not isinstance(app.screen, sc.BabsScreen)
            # reopen → SAME instance reused, session restored, transcript rehydrated
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            scr2 = app.screen
            assert scr2 is scr1                       # persistent instance
            assert len(scr2._history) == 2            # chat memory survived
            assert scr2._agent_enabled is True        # agent mode survived
            log = scr2.query_one("#babs-log")
            assert len(log.children) >= 2             # transcript rehydrated into bubbles

    async def test_open_resurfaces_buried_babs(self, monkeypatch):
        monkeypatch.setattr(B, "list_installed", lambda **k: _ONE_MODEL)
        monkeypatch.setattr(B, "show", lambda *a, **k: {})
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause(); await pilot.pause()
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            babs = app.screen
            assert isinstance(babs, sc.BabsScreen)
            # bury Babs under another pushed screen
            app.push_screen(sc.Screen())
            await pilot.pause()
            assert not isinstance(app.screen, sc.BabsScreen)   # buried
            # BABS again → resurfaces the SAME instance, no duplicate
            app.action_open_babs()
            await pilot.pause(); await pilot.pause()
            assert app.screen is babs
            assert sum(isinstance(s, sc.BabsScreen) for s in app.screen_stack) == 1
