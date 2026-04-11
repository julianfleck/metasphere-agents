import re

from metasphere import paths as P


def test_resolve_uses_env(tmp_paths):
    r = P.resolve()
    assert r.root == tmp_paths.root
    assert r.project_root == tmp_paths.project_root
    assert r.scope == tmp_paths.scope


def test_subpaths(tmp_paths):
    # events_log now rotates daily — assert it lives under the events dir
    # with a date-stamped name (events-YYYY-MM-DD.jsonl).
    assert tmp_paths.events_log.parent == tmp_paths.root / "events"
    assert re.match(r"events-\d{4}-\d{2}-\d{2}\.jsonl", tmp_paths.events_log.name)
    assert tmp_paths.schedule_jobs == tmp_paths.root / "schedule" / "jobs.json"
    assert tmp_paths.agent_dir("@x") == tmp_paths.root / "agents" / "@x"
    assert tmp_paths.messages_dir() == tmp_paths.scope / ".messages"
    assert tmp_paths.tasks_dir() == tmp_paths.scope / ".tasks"


def test_events_log_is_dated(tmp_paths):
    # Property recomputes per access so a long-lived process picks up the
    # new file at midnight without restart. Name shape is the contract.
    name = tmp_paths.events_log.name
    assert re.fullmatch(r"events-\d{4}-\d{2}-\d{2}\.jsonl", name)
