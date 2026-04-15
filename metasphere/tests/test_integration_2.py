"""Wave-2 cross-module end-to-end integration tests for metasphere/.

Builds on metasphere/tests/test_integration.py but pushes harder on
cross-module interop, real on-disk data, and the higher-level subsystems
(spawn, schedule, heartbeat, posthook, gateway, trace, memory).

Live telegram tests use a configured test bot (token in
~/.metasphere/config/telegram-rewrite.env). They additionally require
``METASPHERE_TEST_CHAT_ID`` to be set in the environment; otherwise
they are skipped.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import threading
import time
import uuid
from pathlib import Path

import pytest

from metasphere import agents as A
from metasphere import events as E
from metasphere import heartbeat as H
from metasphere import memory as MEM
from metasphere import messages as M
from metasphere import posthook as PH
from metasphere import schedule as S
from metasphere import tasks as T
from metasphere import trace as TR
from metasphere.gateway import daemon as GD
from metasphere.io import read_frontmatter_file, write_frontmatter_file
from metasphere.paths import Paths
from metasphere.telegram import api as tg_api


REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_CHAT_ID = os.environ.get("METASPHERE_TEST_CHAT_ID")
TEST_MARKER = "INTEGRATION-TEST-2"


def _make_paths(tmp: Path) -> Paths:
    repo = tmp / "repo"
    (repo / ".messages" / "inbox").mkdir(parents=True)
    (repo / ".messages" / "outbox").mkdir(parents=True)
    (repo / ".tasks" / "active").mkdir(parents=True)
    (repo / ".tasks" / "completed").mkdir(parents=True)
    (tmp / "ms").mkdir()
    return Paths(root=tmp / "ms", project_root=repo, scope=repo)


# ---------------------------------------------------------------------------
# 1. spawn → message → reply chain
# ---------------------------------------------------------------------------

def test_spawn_message_reply_chain(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path)
    monkeypatch.setenv("METASPHERE_SPAWN_NO_EXEC", "1")
    monkeypatch.setenv("METASPHERE_DIR", str(paths.root))
    monkeypatch.setenv("METASPHERE_PROJECT_ROOT", str(paths.project_root))
    monkeypatch.setenv("METASPHERE_SCOPE", str(paths.scope))

    # Orchestrator inbox lives at repo root scope.
    rec = A.spawn_ephemeral(
        "@chain-child",
        "/",
        "do a chain test",
        parent="@orchestrator",
        paths=paths,
    )
    assert rec.name == "@chain-child"

    # Child sends a message addressed back to the orchestrator at root scope.
    child_paths = Paths(root=paths.root, project_root=paths.project_root, scope=Path(rec.scope))
    sent = M.send_message(
        target="@/",
        label="!info",
        body="hello orchestrator from chain-child",
        from_agent="@chain-child",
        paths=child_paths,
        wake=False,
    )

    # Orchestrator collects its inbox at the root scope.
    inbox = M.collect_inbox(paths.project_root, paths.project_root)
    found = next(m for m in inbox if m.id == sent.id)
    assert found.from_ == "@chain-child"

    # Orchestrator replies. reply_to_message marks the original replied
    # and emits a !reply with reply_to set.
    reply = M.reply_to_message(
        sent.id, "ack from orchestrator", "@orchestrator", paths=paths
    )
    assert reply.reply_to == sent.id
    assert reply.label == "!reply"

    # Original on disk is now status: replied.
    refreshed = M.read_message(found.path)
    assert refreshed.status == M.STATUS_REPLIED


# ---------------------------------------------------------------------------
# 2. Backward compat: bash-emitted .msg + .md files round-trip
# ---------------------------------------------------------------------------

def test_bash_msg_files_roundtrip(tmp_path):
    # Use checked-in fixtures so the test is stable regardless of live
    # inbox state.
    fixtures = Path(__file__).parent / "fixtures" / "bash_msgs"
    files = sorted(fixtures.glob("msg-*.msg"))[:5]
    assert len(files) >= 5, f"need >=5 bash msg fixtures in {fixtures}"

    for src in files:
        msg = M.read_message(src)
        assert msg.id and msg.from_, f"missing required field in {src}"
        dest = tmp_path / src.name
        M.write_message(msg, dest)
        rt = M.read_message(dest)
        assert rt.id == msg.id
        assert rt.from_ == msg.from_
        assert rt.to == msg.to
        assert rt.label == msg.label
        assert rt.status == msg.status
        assert rt.body.strip() == msg.body.strip()


def test_bash_task_md_files_roundtrip(tmp_path):
    # Use checked-in fixtures so the test is stable regardless of live
    # active-tasks state.
    fixtures = Path(__file__).parent / "fixtures" / "bash_tasks"
    files = sorted(fixtures.glob("*.md"))[:5]
    assert len(files) >= 3, f"need >=3 bash task fixtures in {fixtures}"

    for src in files:
        fm = read_frontmatter_file(src)
        assert fm.meta.get("id"), f"missing id in {src}"
        dest = tmp_path / src.name
        write_frontmatter_file(dest, fm)
        rt = read_frontmatter_file(dest)
        assert rt.meta.get("id") == fm.meta.get("id")
        assert rt.meta.get("title") == fm.meta.get("title")
        assert rt.body.strip() == fm.body.strip()


# ---------------------------------------------------------------------------
# 3. Live telegram smoke
# ---------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.skipif(not LIVE_CHAT_ID, reason="METASPHERE_TEST_CHAT_ID not set")
def test_telegram_live_single():
    nonce = uuid.uuid4().hex[:8]
    text = f"{TEST_MARKER} single nonce={nonce}"
    resps = tg_api.send_message(LIVE_CHAT_ID, text)
    assert len(resps) == 1
    assert resps[0].get("ok") is True, resps[0]
    mid = resps[0]["result"]["message_id"]
    print(f"\n[telegram] single message_id={mid} nonce={nonce}")


@pytest.mark.live
@pytest.mark.skipif(not LIVE_CHAT_ID, reason="METASPHERE_TEST_CHAT_ID not set")
def test_telegram_live_chunked_6kb():
    nonce = uuid.uuid4().hex[:8]
    body = (
        f"{TEST_MARKER} chunked nonce={nonce} "
        + ("y" * 120 + "\n") * 50
    )
    assert len(body) >= 6000
    resps = tg_api.send_message(LIVE_CHAT_ID, body)
    assert len(resps) == 2, f"expected exactly 2 chunks, got {len(resps)}"
    for i, r in enumerate(resps, 1):
        assert r.get("ok") is True, r
        sent = r["result"]["text"]
        assert sent.startswith(f"[{i}/2] "), sent
        mid = r["result"]["message_id"]
        print(f"\n[telegram] chunk {i}/2 message_id={mid} nonce={nonce}")


# ---------------------------------------------------------------------------
# 4. Schedule daemon dry-run
# ---------------------------------------------------------------------------

@pytest.mark.real_corpus
def test_schedule_load_and_dry_run():
    jobs = S.load_jobs()
    assert len(jobs) >= 1, "expected at least 1 scheduled job"
    now = int(time.time())
    for j in jobs:
        assert j.cron_expr, f"job {j.id} missing cron_expr"
        # No exception evaluating any job for any synthetic time.
        S.cron_should_fire(j.cron_expr, j.tz or "UTC", j.last_fired_at, now=now)
        target = S.resolve_target_agent(j)
        assert target.startswith("@") and len(target) > 1


# ---------------------------------------------------------------------------
# 5. Memory recall
# ---------------------------------------------------------------------------

@pytest.mark.real_corpus
def test_memory_recall_real_corpus():
    hits = MEM.recall("polymarket trading")
    assert len(hits) >= 1, "expected ≥1 hit for polymarket recall"
    txt = MEM.context_for("rewrite cleanup", budget_chars=1024)
    assert txt, "expected non-empty context block"
    assert len(txt) <= 1024


# ---------------------------------------------------------------------------
# 6. Heartbeat one-shot dry-run
# ---------------------------------------------------------------------------

def test_heartbeat_once_dry_run(tmp_path):
    paths = _make_paths(tmp_path)
    H.heartbeat_once(paths=paths, invoke_agent=False)
    state = paths.state / "heartbeat_last_run"
    assert state.exists(), f"expected state file at {state}"
    assert "alive at" in state.read_text()


# ---------------------------------------------------------------------------
# 7. Posthook simulation w/ dedupe
# ---------------------------------------------------------------------------

def test_posthook_routes_and_dedupes(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path)
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    # chat_id config so route_to_telegram has a target.
    paths.config.mkdir(parents=True, exist_ok=True)
    (paths.config / "telegram_chat_id").write_text("9999\n")

    transcript = tmp_path / "transcript.jsonl"
    msg_obj = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "fake assistant text"}]},
    }
    transcript.write_text(json.dumps(msg_obj) + "\n")

    calls: list[tuple] = []

    def fake_send(chat_id, text, **kw):
        calls.append((chat_id, text))
        return [{"ok": True, "result": {"message_id": 1, "text": text}}]

    monkeypatch.setattr(tg_api, "send_message", fake_send)

    payload = json.dumps({"transcript_path": str(transcript)}).encode()
    rc1 = PH.run_posthook(payload, paths=paths)
    assert rc1 == 0
    assert len(calls) == 1
    assert calls[0][1] == "fake assistant text"

    # Same payload again — dedupe must short-circuit.
    rc2 = PH.run_posthook(payload, paths=paths)
    assert rc2 == 0
    assert len(calls) == 1, f"expected dedupe; got {len(calls)} calls"


# ---------------------------------------------------------------------------
# 8. Gateway daemon does not flap on poll error
# ---------------------------------------------------------------------------

def test_gateway_daemon_survives_poll_error(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path)

    # Skip the real ensure_session — we don't have tmux in tests.
    monkeypatch.setattr(GD, "ensure_session", lambda p: None)
    monkeypatch.setattr(GD, "run_watchdog", lambda p: None)

    iters = {"n": 0}

    def fake_poll():
        iters["n"] += 1
        if iters["n"] == 2:
            raise RuntimeError("simulated transient poll failure")
        return 0

    def stop_after_3():
        return iters["n"] >= 3

    sleeps: list[float] = []

    GD.run_daemon(
        paths=paths,
        poll_interval=0,
        watchdog_interval=0,
        stop=stop_after_3,
        poll_fn=fake_poll,
        sleep_fn=lambda s: sleeps.append(s),
    )
    assert iters["n"] >= 3, f"daemon exited after {iters['n']} iters"


# ---------------------------------------------------------------------------
# 9. Concurrent updates on the same .msg
# ---------------------------------------------------------------------------

def _worker_update(args):
    msg_path, value, n = args
    from metasphere import messages as M2
    for i in range(n):
        M2.update_status(Path(msg_path), "read_at", f"{value}-{i}")
    return os.getpid()


def test_concurrent_msg_updates(tmp_path):
    paths = _make_paths(tmp_path)
    msg = M.send_message(
        target="@/",
        label="!info",
        body="concurrent target",
        from_agent="@a",
        paths=paths,
        wake=False,
    )
    args = [(str(msg.path), f"w{i}", 25) for i in range(4)]
    with mp.Pool(4) as pool:
        pool.map(_worker_update, args)

    final = M.read_message(msg.path)
    assert final.id == msg.id
    assert final.body.strip() == "concurrent target"
    assert final.read_at.startswith("w") and final.read_at.endswith("-24")
    raw = msg.path.read_text()
    assert raw.startswith("---\n")
    assert raw.count("\n---\n") == 1


# ---------------------------------------------------------------------------
# 10. Trace capture roundtrip
# ---------------------------------------------------------------------------

def test_trace_capture_and_search(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path)
    monkeypatch.setenv("METASPHERE_DIR", str(paths.root))

    nonce = uuid.uuid4().hex[:8]
    tr = TR.capture_trace(["echo", f"hello-{nonce}"], paths=paths)
    assert tr.exit_code == 0
    out = Path(tr.stdout_file).read_text()
    assert f"hello-{nonce}" in out

    hits = TR.search_traces(f"hello-{nonce}", paths=paths)
    assert any(h.id == tr.id for h in hits), f"trace {tr.id} not found via search"
