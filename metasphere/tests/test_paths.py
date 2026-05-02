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


def test_find_agent_dir_returns_none_when_neither_exists(tmp_paths):
    # No project-scoped or global dir for @ghost — caller learns nothing
    # exists and decides whether to fall back.
    assert tmp_paths.find_agent_dir("@ghost") is None


def test_find_agent_dir_returns_global_when_only_global_exists(tmp_paths):
    d = tmp_paths.agent_dir("@global-only")
    d.mkdir(parents=True)
    assert tmp_paths.find_agent_dir("@global-only") == d


def test_find_agent_dir_returns_project_when_only_project_exists(tmp_paths):
    d = tmp_paths.project_agent_dir("acme", "@scoped")
    d.mkdir(parents=True)
    assert tmp_paths.find_agent_dir("@scoped") == d


def test_find_agent_dir_prefers_project_over_global(tmp_paths):
    # Two agents share an id: one registered project-scoped under acme/,
    # one as a global stub. Project-scoped wins (matches
    # metasphere.agents._find_agent_dir's tie-break).
    proj = tmp_paths.project_agent_dir("acme", "@dual")
    proj.mkdir(parents=True)
    glob = tmp_paths.agent_dir("@dual")
    glob.mkdir(parents=True)
    assert tmp_paths.find_agent_dir("@dual") == proj


def test_find_agent_dir_normalizes_missing_at_prefix(tmp_paths):
    d = tmp_paths.project_agent_dir("acme", "@bare")
    d.mkdir(parents=True)
    # Caller passes the agent id without the leading "@"
    assert tmp_paths.find_agent_dir("bare") == d
