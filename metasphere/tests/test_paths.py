from metasphere import paths as P


def test_resolve_uses_env(tmp_paths):
    r = P.resolve()
    assert r.root == tmp_paths.root
    assert r.project_root == tmp_paths.project_root
    assert r.scope == tmp_paths.scope


def test_subpaths(tmp_paths):
    assert tmp_paths.events_log == tmp_paths.root / "events" / "events.jsonl"
    assert tmp_paths.schedule_jobs == tmp_paths.root / "schedule" / "jobs.json"
    assert tmp_paths.agent_dir("@x") == tmp_paths.root / "agents" / "@x"
    assert tmp_paths.messages_dir() == tmp_paths.scope / ".messages"
    assert tmp_paths.tasks_dir() == tmp_paths.scope / ".tasks"
