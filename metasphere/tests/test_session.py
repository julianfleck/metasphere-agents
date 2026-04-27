from unittest.mock import patch

from metasphere import session as sessmod


def _fake_run(stdout: str, returncode: int = 0):
    class R:
        pass
    r = R()
    r.stdout = stdout
    r.returncode = returncode
    return r


def test_list_sessions_filters_prefix():
    sample = (
        "metasphere-foo\t1\t1700000000\t1\n"
        "metasphere-bar\t2\t1700000100\t0\n"
        "other-session\t1\t1700000200\t0\n"
    )
    with patch("metasphere.session.subprocess.run", return_value=_fake_run(sample)):
        rows = sessmod.list_sessions()
    assert {r.agent for r in rows} == {"@foo", "@bar"}
    foo = next(r for r in rows if r.agent == "@foo")
    assert foo.attached is True
    assert foo.windows == 1


def test_session_info_by_agent():
    sample = "metasphere-x\t3\t1\t0\n"
    with patch("metasphere.session.subprocess.run", return_value=_fake_run(sample)):
        info = sessmod.session_info("@x")
    assert info is not None
    assert info.name == "metasphere-x"
    assert info.windows == 3


def test_attach_missing_returns_1():
    with patch("metasphere.session.session_alive", return_value=False):
        assert sessmod.attach_to("@nope") == 1


def test_list_sessions_handles_no_tmux():
    with patch("metasphere.session.subprocess.run", side_effect=FileNotFoundError):
        assert sessmod.list_sessions() == []


def _agent_record(name: str, project: str = "") -> object:
    """Minimal AgentRecord stand-in. Only ``name`` and ``session_name``
    are read by ``_resolve_session``."""
    from metasphere.agents import AgentRecord

    return AgentRecord(
        name=name,
        scope="",
        parent="",
        status="",
        spawned_at="",
        project=project,
    )


def test_resolve_session_uses_project_scoped_name():
    """Regression: ``metasphere session stop @accelerator-programs``
    must resolve to ``metasphere-research-accelerator-programs`` when
    the agent's record carries a project, not the unscoped fallback
    ``metasphere-accelerator-programs``."""
    rec = _agent_record("@accelerator-programs", project="research")
    with patch("metasphere.session.list_agents", return_value=[rec]):
        assert sessmod._resolve_session("@accelerator-programs") == (
            "metasphere-research-accelerator-programs"
        )


def test_resolve_session_passthrough_for_raw_tmux_name():
    raw = "metasphere-research-accelerator-programs"
    # Should not even consult the agent registry.
    with patch("metasphere.session.list_agents", side_effect=AssertionError):
        assert sessmod._resolve_session(raw) == raw


def test_resolve_session_unknown_agent_falls_back_to_unscoped():
    with patch("metasphere.session.list_agents", return_value=[]):
        assert sessmod._resolve_session("@nobody") == "metasphere-nobody"


def test_resolve_session_orchestrator_uses_historical_name():
    from metasphere.gateway.session import SESSION_NAME

    with patch("metasphere.session.list_agents", return_value=[]):
        assert sessmod._resolve_session("@orchestrator") == SESSION_NAME


def test_resolve_session_accepts_bare_name_without_at():
    rec = _agent_record("@accelerator-programs", project="research")
    with patch("metasphere.session.list_agents", return_value=[rec]):
        assert sessmod._resolve_session("accelerator-programs") == (
            "metasphere-research-accelerator-programs"
        )
