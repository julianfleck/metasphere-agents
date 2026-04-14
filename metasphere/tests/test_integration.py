"""End-to-end integration tests for metasphere.

NOT mocked. Real file IO + real Telegram API calls (to a configured test
bot whose token lives in ~/.metasphere/config/telegram-rewrite.env).

Live telegram tests require ``METASPHERE_TEST_CHAT_ID`` to be set in the
environment; otherwise they are skipped. Gate via ``-m "not live"`` to
skip live tests entirely.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
import uuid
from pathlib import Path

import pytest

from metasphere import messages as M
from metasphere import tasks as T
from metasphere import events as E
from metasphere.paths import Paths
from metasphere.telegram import api as tg_api


REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_CHAT_ID = os.environ.get("METASPHERE_TEST_CHAT_ID")
TEST_MARKER = "INTEGRATION-TEST-METASPHERE-REWRITE"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_paths(tmp: Path) -> Paths:
    """Build a self-contained Paths bundle rooted in a temp directory."""
    repo = tmp / "repo"
    (repo / ".messages" / "inbox").mkdir(parents=True)
    (repo / ".messages" / "outbox").mkdir(parents=True)
    (tmp / "ms").mkdir()
    return Paths(root=tmp / "ms", project_root=repo, scope=repo)


# ---------------------------------------------------------------------------
# 1. End-to-end message roundtrip
# ---------------------------------------------------------------------------

def test_message_roundtrip_two_agents(tmp_path):
    paths = _make_paths(tmp_path)

    # Two simulated agents = two scope dirs under repo
    agent_a = paths.project_root / "a"
    agent_b = paths.project_root / "b"
    (agent_a / ".messages" / "outbox").mkdir(parents=True)
    (agent_b / ".messages" / "inbox").mkdir(parents=True)

    # A sends to B by absolute scope path
    a_paths = Paths(root=paths.root, project_root=paths.project_root, scope=agent_a)
    msg = M.send_message(
        target="@/b/",
        label="!info",
        body="hello from A",
        from_agent="@a",
        paths=a_paths,
        wake=False,
    )

    # File is on disk in B's inbox with correct frontmatter
    inbox_file = agent_b / ".messages" / "inbox" / f"{msg.id}.msg"
    assert inbox_file.exists(), f"expected {inbox_file} to exist"
    raw = inbox_file.read_text()
    assert raw.startswith("---\n")
    assert f"id: {msg.id}" in raw
    assert 'from: "@a"' in raw
    assert 'to: "@/b/"' in raw
    assert 'label: "!info"' in raw
    assert "status: unread" in raw
    assert "hello from A" in raw

    # collect_inbox on B sees the message
    b_inbox = M.collect_inbox(agent_b, paths.project_root)
    ids = [m.id for m in b_inbox]
    assert msg.id in ids
    found = next(m for m in b_inbox if m.id == msg.id)
    assert found.from_ == "@a"
    assert found.body.strip() == "hello from A"

    # Sender outbox copy also exists
    out = agent_a / ".messages" / "outbox" / f"{msg.id}.msg"
    assert out.exists()


# ---------------------------------------------------------------------------
# 2. End-to-end task lifecycle (incl. slug sanitization for slashes)
# ---------------------------------------------------------------------------

def test_task_lifecycle_with_slash_title(tmp_path):
    paths = _make_paths(tmp_path)

    title = "Fix scripts/messages slug bug / urgent"
    task = T.create_task(title, "!high", paths.project_root, paths.project_root)

    # Slug must not contain slashes
    assert "/" not in task.slug
    assert task.slug.startswith("fix-scripts-messages")
    active_file = paths.project_root / ".tasks" / "active" / f"{task.slug}.md"
    assert active_file.exists()

    T.start_task(task.id, "@tester-integration", paths.project_root)
    T.update_task(task.id, paths.project_root, note="midway progress")
    completed = T.complete_task(task.id, "all done", paths.project_root)

    # File moved active/ → archive/YYYY-MM-DD/
    assert not active_file.exists()
    assert completed.path is not None and completed.path.exists()
    assert completed.path.parent.parent.name == "archive"
    assert completed.status == T.STATUS_COMPLETED
    body = completed.path.read_text()
    assert "midway progress" in body
    assert "Completed: all done" in body


# ---------------------------------------------------------------------------
# 3. End-to-end telegram smoke (LIVE bot)
# ---------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.skipif(not LIVE_CHAT_ID, reason="METASPHERE_TEST_CHAT_ID not set")
def test_telegram_send_live():
    nonce = uuid.uuid4().hex[:8]
    text = f"{TEST_MARKER} single-msg nonce={nonce}"
    resps = tg_api.send_message(LIVE_CHAT_ID, text)
    assert len(resps) == 1
    r = resps[0]
    assert r.get("ok") is True, r
    mid = r["result"]["message_id"]
    cid = r["result"]["chat"]["id"]
    print(f"\n[telegram] sent single chat_id={cid} message_id={mid} nonce={nonce}")


@pytest.mark.live
@pytest.mark.skipif(not LIVE_CHAT_ID, reason="METASPHERE_TEST_CHAT_ID not set")
def test_telegram_send_live_chunked():
    nonce = uuid.uuid4().hex[:8]
    # 5KB > 3900 char chunk limit -> must split into >=2 parts
    body = (f"{TEST_MARKER} chunked nonce={nonce} " + ("x" * 100 + "\n") * 50)
    assert len(body) >= 5000
    resps = tg_api.send_message(LIVE_CHAT_ID, body)
    assert len(resps) >= 2, f"expected >=2 chunks, got {len(resps)}"
    for i, r in enumerate(resps, 1):
        assert r.get("ok") is True, r
        sent_text = r["result"]["text"]
        assert sent_text.startswith(f"[{i}/{len(resps)}] "), sent_text
        mid = r["result"]["message_id"]
        cid = r["result"]["chat"]["id"]
        print(f"\n[telegram] sent chunk {i}/{len(resps)} chat_id={cid} message_id={mid} nonce={nonce}")


# ---------------------------------------------------------------------------
# 4. Concurrent message updates
# ---------------------------------------------------------------------------

def _worker_update(args):
    msg_path, field, value, n = args
    # Re-import inside subprocess
    from metasphere import messages as M2
    for i in range(n):
        M2.update_status(Path(msg_path), field, f"{value}-{i}")
    return os.getpid()


def test_concurrent_update_status_no_torn_writes(tmp_path):
    paths = _make_paths(tmp_path)
    msg = M.send_message(
        target="@/",
        label="!info",
        body="concurrent target",
        from_agent="@a",
        paths=paths,
        wake=False,
    )
    msg_path = msg.path
    assert msg_path.exists()

    workers = 4
    iters = 25
    args = [(str(msg_path), "read_at", f"w{i}", iters) for i in range(workers)]

    with mp.Pool(workers) as pool:
        pool.map(_worker_update, args)

    # File still parses cleanly, frontmatter intact
    final = M.read_message(msg_path)
    assert final.id == msg.id
    assert final.from_ == "@a"
    assert final.label == "!info"
    assert final.body.strip() == "concurrent target"
    # The final read_at must match the form one of the workers wrote
    assert final.read_at.startswith("w") and final.read_at.endswith(f"-{iters - 1}")

    # Raw bytes: exactly one frontmatter block
    raw = msg_path.read_text()
    assert raw.count("\n---\n") == 1
    assert raw.startswith("---\n")


# ---------------------------------------------------------------------------
# 5. Cross-module: tasks + messages + events
# ---------------------------------------------------------------------------

def test_cross_module_task_message_event(tmp_path, monkeypatch):
    paths = _make_paths(tmp_path)
    # events.log_event uses paths kwarg directly when supplied; messages.send_message
    # passes paths down. Force METASPHERE_DIR so any fallback also stays in tmp.
    monkeypatch.setenv("METASPHERE_DIR", str(paths.root))
    monkeypatch.setenv("METASPHERE_PROJECT_ROOT", str(paths.project_root))
    monkeypatch.setenv("METASPHERE_SCOPE", str(paths.scope))

    task = T.create_task("Cross module test task", "!normal", paths.project_root, paths.project_root)
    E.log_event("task.create", f"created {task.id}", agent="@a",
                meta={"task_id": task.id}, paths=paths)

    msg = M.send_message(
        target="@/",
        label="!done",
        body=f"done with {task.id}",
        from_agent="@a",
        paths=paths,
        wake=False,
    )

    log = paths.events_log
    assert log.exists(), f"expected events log at {log}"
    records = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    types = [r["type"] for r in records]
    assert "task.create" in types
    assert "message.send" in types
    # task.create logged before message.send
    assert types.index("task.create") < types.index("message.send")


# ---------------------------------------------------------------------------
# 6. Frontmatter compatibility with existing bash-emitted .msg files
# ---------------------------------------------------------------------------

def test_read_existing_bash_msg_files_roundtrip(tmp_path):
    # Use checked-in fixtures (originally captured from bash-era .messages/
    # inbox/) so the test is stable regardless of live inbox state.
    fixtures = Path(__file__).parent / "fixtures" / "bash_msgs"
    files = sorted(fixtures.glob("msg-*.msg"))[:3]
    assert len(files) >= 3, f"need >=3 bash msg fixtures in {fixtures}, found {len(files)}"

    for src in files:
        msg = M.read_message(src)
        assert msg.id, f"missing id in {src}"
        assert msg.from_, f"missing from in {src}"

        dest = tmp_path / src.name
        M.write_message(msg, dest)
        roundtripped = M.read_message(dest)
        assert roundtripped.id == msg.id
        assert roundtripped.from_ == msg.from_
        assert roundtripped.to == msg.to
        assert roundtripped.label == msg.label
        assert roundtripped.status == msg.status
        assert roundtripped.body.strip() == msg.body.strip()
