import json
import multiprocessing as mp
import shutil
import subprocess

import pytest

from metasphere.events import log_event


def _worker(i):
    from metasphere.events import log_event
    log_event("test", f"msg-{i}", agent="@w", meta={"i": i})


def test_log_event_schema(tmp_paths):
    rec = log_event("boot", "hello", agent="@x", meta={"k": 1})
    # Required schema fields: id, timestamp, type, message, agent, scope, meta
    assert set(rec.keys()) >= {
        "id", "timestamp", "type", "message", "agent", "scope", "meta",
    }
    assert rec["type"] == "boot"
    assert rec["message"] == "hello"
    assert rec["agent"] == "@x"
    assert rec["meta"] == {"k": 1}
    assert rec["id"].startswith("evt-")
    assert rec["timestamp"].endswith("Z")
    assert rec["scope"]  # non-empty

    line = tmp_paths.events_log.read_text().strip()
    parsed = json.loads(line)
    assert parsed["id"] == rec["id"]
    assert parsed["timestamp"] == rec["timestamp"]
    assert parsed["scope"] == rec["scope"]


def test_log_event_defaults_meta_and_agent(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@defaulted")
    rec = log_event("tick", "no-meta")
    assert rec["meta"] == {}
    assert rec["agent"] == "@defaulted"


def test_log_event_concurrent(tmp_paths):
    ctx = mp.get_context("fork")
    procs = [ctx.Process(target=_worker, args=(i,)) for i in range(20)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    lines = tmp_paths.events_log.read_text().strip().splitlines()
    assert len(lines) == 20
    parsed = [json.loads(l) for l in lines]
    assert sorted(p["meta"]["i"] for p in parsed) == list(range(20))
    # All have the required fields.
    for p in parsed:
        assert {"id", "timestamp", "type", "message", "agent", "scope", "meta"} <= set(p.keys())


def test_log_event_jq_roundtrip(tmp_paths):
    jq = shutil.which("jq")
    if not jq:
        pytest.skip("jq not installed")
    log_event("jq.test", "hello jq", agent="@x", meta={"n": 7})
    out = subprocess.check_output(
        [jq, "-r", ".timestamp + \"|\" + .id + \"|\" + .type + \"|\" + .agent + \"|\" + .scope",
         str(tmp_paths.events_log)],
        text=True,
    ).strip().splitlines()[-1]
    ts, eid, typ, agent, scope = out.split("|")
    assert ts and ts != "null"
    assert eid.startswith("evt-")
    assert typ == "jq.test"
    assert agent == "@x"
    assert scope and scope != "null"
