from metasphere.identity import resolve_agent_id


def test_env_wins(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@foo")
    assert resolve_agent_id(tmp_paths) == "@foo"


def test_pointer_file(tmp_paths):
    tmp_paths.current_agent_file.write_text("@bar\n")
    assert resolve_agent_id(tmp_paths) == "@bar"


def test_orchestrator_dir(tmp_paths):
    (tmp_paths.agents / "@orchestrator").mkdir(parents=True)
    assert resolve_agent_id(tmp_paths) == "@orchestrator"


def test_default_user(tmp_paths):
    assert resolve_agent_id(tmp_paths) == "@user"
