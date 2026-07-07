"""Background Learning agent endpoints (learn-start / -status / -results / -list).

The crawl ENGINE lives in the babs repo (bb_learn.py, tested there); these cover the SpliceCraft
side: registration + write flags, the human-armed ``allow_online_lookups`` gate on ``learn-start``
(so an autonomous agent can't self-arm a crawl), slug path-safety, and the read-only session
readers. No real launch — ``learn-start`` is only exercised disarmed (403), with an invalid topic,
or with the launcher monkeypatched, so the suite never spawns a crawl or touches the network."""
import json
import pathlib

import splicecraft as sc

H = sc._state._AGENT_HANDLERS
_LEARN = ("learn-start", "learn-status", "learn-results", "learn-list")


def _call(endpoint, payload, app=None):
    return H[endpoint][0](app, payload)


def test_learn_endpoints_registered_with_write_flags():
    for e in _LEARN:
        assert e in H, f"{e} not registered in _AGENT_HANDLERS"
    assert H["learn-start"][1] is True                 # write -> the in-app Babs ask-gates it
    assert not any(H[e][1] for e in _LEARN[1:])         # status/results/list are read-only


def test_slug_is_safe():
    assert sc._learn_slug("T7 expression in E. coli!") == "learn_t7_expression_in_e_coli"
    assert sc._learn_slug("").startswith("learn_")                 # never empty -> always loadable
    s = sc._learn_slug("../../etc/passwd")
    assert s.startswith("learn_") and "/" not in s and "." not in s   # path-traversal defanged


def test_learn_start_refuses_when_disarmed(monkeypatch):
    monkeypatch.setattr(sc, "_get_setting", lambda k, d=None: False)
    body, status = _call("learn-start", {"topic": "widget synthesis"})
    assert status == 403
    assert "disarmed" in body["error"] and "allow_online_lookups" in body["error"]


def test_learn_start_validates_topic_when_armed(monkeypatch):
    monkeypatch.setattr(sc, "_get_setting", lambda k, d=None: True)
    assert _call("learn-start", {})[1] == 400                    # missing topic
    assert _call("learn-start", {"topic": "   "})[1] == 400      # blank
    assert _call("learn-start", {"topic": "x" * 201})[1] == 400  # too long


def test_learn_start_launches_and_caps_when_armed(monkeypatch):
    monkeypatch.setattr(sc, "_get_setting", lambda k, d=None: True)
    monkeypatch.setattr(sc, "_learn_resolve_babs_home", lambda: pathlib.Path("/tmp/babs"))
    calls = {}

    def fake_launch(topic, max_docs=None, max_minutes=None):
        calls.update(topic=topic, max_docs=max_docs, max_minutes=max_minutes)
        return {"slug": sc._learn_slug(topic), "session_dir": "/x", "pid": 1234,
                "log": "/x.log", "topic": topic}

    monkeypatch.setattr(sc, "_launch_learning_session", fake_launch)
    body = _call("learn-start", {"topic": "T7 expression", "max_docs": 999, "max_minutes": 999})
    assert body["ok"] and body["slug"] == "learn_t7_expression"
    assert body["max_docs"] == 200 and body["max_minutes"] == 240   # capped at the hub ceilings
    assert calls["max_docs"] == 200 and calls["max_minutes"] == 240  # the cap reaches the launcher


def test_learn_status_slug_validation(monkeypatch):
    monkeypatch.setattr(sc, "_learn_resolve_babs_home", lambda: pathlib.Path("/tmp/nope"))
    assert _call("learn-status", {"slug": "../etc/passwd"})[1] == 400
    assert _call("learn-status", {"slug": "not_a_learn_slug"})[1] == 400
    body, status = _call("learn-status", {"slug": "learn_absent"})
    assert status == 404 and body["status"] == "unknown"


def test_learn_status_reads_progress(tmp_path, monkeypatch):
    sess = tmp_path / "knowledge_base" / "packs" / "learn_topic" / "_session"
    sess.mkdir(parents=True)
    (sess / "progress.json").write_text(json.dumps({"kept": 3, "status": "running"}))
    monkeypatch.setattr(sc, "_learn_resolve_babs_home", lambda: tmp_path)
    body = _call("learn-status", {"slug": "learn_topic"})
    assert body["slug"] == "learn_topic" and body["kept"] == 3 and body["status"] == "running"


def test_learn_results_dedupes_and_sorts_by_score(tmp_path, monkeypatch):
    pack = tmp_path / "knowledge_base" / "packs" / "learn_topic"
    pack.mkdir(parents=True)
    rows = [{"doc_id": "A", "title": "a", "relevance_score": 0.4},
            {"doc_id": "A", "title": "a", "relevance_score": 0.4},   # dup doc -> collapsed
            {"doc_id": "B", "title": "b", "relevance_score": 0.9}]
    (pack / "corpus.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(sc, "_learn_resolve_babs_home", lambda: tmp_path)
    body = _call("learn-results", {"slug": "learn_topic"})
    assert body["count"] == 2 and [d["doc_id"] for d in body["results"]] == ["B", "A"]


def test_learn_list_scans_only_learn_packs(tmp_path, monkeypatch):
    for slug, kept in (("learn_alpha", 2), ("learn_beta", 5)):
        sess = tmp_path / "knowledge_base" / "packs" / slug / "_session"
        sess.mkdir(parents=True)
        (sess / "anchor.json").write_text(json.dumps({"topic": slug}))
        (sess / "progress.json").write_text(json.dumps({"kept": kept, "status": "done"}))
    (tmp_path / "knowledge_base" / "packs" / "plant_tc").mkdir(parents=True)   # not a learn pack
    monkeypatch.setattr(sc, "_learn_resolve_babs_home", lambda: tmp_path)
    body = _call("learn-list", {})
    assert {s["slug"] for s in body["sessions"]} == {"learn_alpha", "learn_beta"}


def test_learn_endpoints_error_without_babs(monkeypatch):
    monkeypatch.setattr(sc, "_learn_resolve_babs_home", lambda: None)
    assert _call("learn-list", {})[1] == 400
    assert _call("learn-results", {"slug": "learn_x"})[1] == 400
    assert _call("learn-status", {"slug": "learn_x"})[1] == 400


def test_learn_status_reconciles_dead_pid(tmp_path, monkeypatch):
    # a killed / crashed / OOM'd session leaves progress.json frozen at "running"; reconcile against
    # the recorded pid so the reader shows "stopped" instead of a forever-"running" lie
    sess = tmp_path / "knowledge_base" / "packs" / "learn_topic" / "_session"
    sess.mkdir(parents=True)
    (sess / "progress.json").write_text(json.dumps({"status": "running", "kept": 2, "pid": 2147480000}))
    monkeypatch.setattr(sc, "_learn_resolve_babs_home", lambda: tmp_path)
    assert _call("learn-status", {"slug": "learn_topic"})["status"] == "stopped"


def test_learn_start_refuses_duplicate_running_session(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(sc, "_get_setting", lambda k, d=None: True)
    monkeypatch.setattr(sc, "_learn_resolve_babs_home", lambda: tmp_path)
    slug = sc._learn_slug("t7 expression")
    sess = tmp_path / "knowledge_base" / "packs" / slug / "_session"
    sess.mkdir(parents=True)
    (sess / "progress.json").write_text(json.dumps({"status": "running", "pid": os.getpid()}))  # alive
    body, status = _call("learn-start", {"topic": "t7 expression"})
    assert status == 409 and "already running" in body["error"]


def test_learn_status_survives_nondict_progress(tmp_path, monkeypatch):
    # a corrupt/truncated progress.json that parses to a JSON list (not an object) must not crash the
    # reader — it used to reach ``{**prog}`` -> TypeError; now it reads as absent -> 404 unknown
    sess = tmp_path / "knowledge_base" / "packs" / "learn_topic" / "_session"
    sess.mkdir(parents=True)
    (sess / "progress.json").write_text("[1, 2, 3]")
    monkeypatch.setattr(sc, "_learn_resolve_babs_home", lambda: tmp_path)
    body, status = _call("learn-status", {"slug": "learn_topic"})
    assert status == 404 and body["status"] == "unknown"


def test_learn_results_skips_nondict_and_bad_lines(tmp_path, monkeypatch):
    # a corpus.jsonl line that is valid JSON but not an object (list / scalar) used to AttributeError
    # on rec.get; invalid-JSON lines are skipped too. Only the real records survive.
    pack = tmp_path / "knowledge_base" / "packs" / "learn_topic"
    pack.mkdir(parents=True)
    (pack / "corpus.jsonl").write_text("\n".join([
        '{"doc_id": "A", "title": "a", "relevance_score": 0.5}',
        '["not", "an", "object"]',   # valid JSON, not a dict -> skipped (no crash)
        '42',                         # valid JSON scalar -> skipped
        'not json at all',            # invalid JSON -> skipped
        '{"doc_id": "B", "title": "b", "relevance_score": 0.9}']))
    monkeypatch.setattr(sc, "_learn_resolve_babs_home", lambda: tmp_path)
    body = _call("learn-results", {"slug": "learn_topic"})
    assert body["count"] == 2 and [d["doc_id"] for d in body["results"]] == ["B", "A"]


def test_learn_list_survives_nondict_session_files(tmp_path, monkeypatch):
    # a truthy non-dict anchor/progress.json ([...]) used to AttributeError on (x or {}).get; now
    # such a session reads as absent and is skipped, not crashed
    sess = tmp_path / "knowledge_base" / "packs" / "learn_bad" / "_session"
    sess.mkdir(parents=True)
    (sess / "anchor.json").write_text('["junk"]')
    (sess / "progress.json").write_text('["junk"]')
    monkeypatch.setattr(sc, "_learn_resolve_babs_home", lambda: tmp_path)
    assert _call("learn-list", {})["sessions"] == []


def test_learn_pid_alive_assumes_alive_off_posix(monkeypatch):
    # on Windows os.kill(pid, 0) routes through TerminateProcess and would KILL the pid, so off-POSIX
    # we must assume-alive WITHOUT ever probing
    monkeypatch.setattr(sc.os, "name", "nt")

    def _boom(*a, **k):
        raise AssertionError("os.kill must not be called off-POSIX")

    monkeypatch.setattr(sc.os, "kill", _boom)
    assert sc._learn_pid_alive(999999) is True
