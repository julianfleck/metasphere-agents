"""Focused tests for side-effect boundaries in Telegram slash commands."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from metasphere.telegram import commands


CTX = commands.Context(chat_id=1, from_user="tester")


@pytest.mark.parametrize("args", ["restart extra", "restart --force"])
def test_session_restart_rejects_surplus_before_effect(monkeypatch, args):
    monkeypatch.setattr(
        "metasphere.gateway.session.restart_session",
        lambda *a, **kw: pytest.fail("malformed restart reached backend"),
    )

    result = commands.cmd_session(args, CTX)

    assert "Usage: /session [restart|status]" in result
    assert "unexpected" in result


@pytest.mark.parametrize("args", ["status extra", "status --full"])
def test_session_status_rejects_surplus_before_command(monkeypatch, args):
    monkeypatch.setattr(
        commands,
        "_run",
        lambda *a, **kw: pytest.fail("malformed status reached command"),
    )

    result = commands.cmd_session(args, CTX)

    assert "Usage: /session [restart|status]" in result
    assert "unexpected" in result


@pytest.mark.parametrize("args", ["", "restart"])
def test_session_restart_keeps_default_and_explicit_behavior(
    monkeypatch, args
):
    sentinel_paths = object()
    calls = []
    monkeypatch.setattr("metasphere.paths.resolve", lambda: sentinel_paths)
    monkeypatch.setattr(
        "metasphere.gateway.session.restart_session",
        lambda reason, paths: calls.append((reason, paths)) or True,
    )

    result = commands.cmd_session(args, CTX)

    assert result == "Recreated orchestrator session and REPL."
    assert calls == [("Telegram /session restart", sentinel_paths)]


def test_session_status_keeps_exact_behavior(monkeypatch):
    calls = []

    def fake_run(argv, *, timeout):
        calls.append((argv, timeout))
        return "gateway status"

    monkeypatch.setattr(commands, "_run", fake_run)

    result = commands.cmd_session("status", CTX)

    assert result == "gateway status"
    assert calls == [
        ([
            "systemctl", "--user", "status", "metasphere-gateway",
            "--no-pager",
        ], 5),
    ]


@pytest.mark.parametrize(
    "args, detail",
    [
        ("seed researcher @agent extra", "unexpected argument: extra"),
        ("seed researcher @agent --bogus", "unexpected flag: --bogus"),
        ("seed researcher @agent --project", "--project requires a value"),
        (
            "seed researcher @agent --project one --project two",
            "unexpected flag: --project",
        ),
    ],
)
def test_team_seed_rejects_malformed_args_before_lookup(
    monkeypatch, args, detail
):
    monkeypatch.setattr(
        "metasphere.specs.get_spec",
        lambda *a, **kw: pytest.fail("malformed argv must not look up a spec"),
    )

    result = commands.cmd_team(args, CTX)

    assert isinstance(result, str)
    assert "Usage: /team seed" in result
    assert detail in result


def test_team_seed_passes_exact_project_value(monkeypatch, tmp_path):
    sentinel_spec = object()
    seen = {}
    monkeypatch.setattr("metasphere.specs.get_spec", lambda name: sentinel_spec)

    def fake_seed(agent_id, spec, *, project_name=""):
        seen.update(
            agent_id=agent_id, spec=spec, project_name=project_name,
        )
        return Path(tmp_path) / "seeded"

    monkeypatch.setattr("metasphere.specs.seed_agent", fake_seed)

    result = commands.cmd_team(
        "seed researcher @agent --project alpha", CTX,
    )

    assert isinstance(result, commands.Reply)
    assert seen == {
        "agent_id": "@agent",
        "spec": sentinel_spec,
        "project_name": "alpha",
    }


def test_team_seed_unsafe_spec_never_seeds(monkeypatch):
    monkeypatch.setattr(
        "metasphere.specs.seed_agent",
        lambda *a, **kw: pytest.fail("unsafe spec must not seed"),
    )

    result = commands.cmd_team("seed /tmp/outside @agent", CTX)

    assert isinstance(result, str)
    assert "not found" in result


def test_team_rejects_unbalanced_shell_quoting():
    result = commands.cmd_team('seed "unterminated', CTX)

    assert isinstance(result, str)
    assert "Invalid /team syntax" in result


@pytest.mark.parametrize(
    "args, patched, detail",
    [
        ("assign @agent task extra", "metasphere.tasks.dispatch_task", "unexpected argument"),
        ("assign @agent task --bogus", "metasphere.tasks.dispatch_task", "unexpected flag"),
        ("assign @agent task --project", "metasphere.tasks.dispatch_task", "requires a value"),
        ("wake @agent extra", "metasphere.agents.wake_persistent", "unexpected argument"),
        ("wake @agent --force", "metasphere.agents.wake_persistent", "unexpected flag"),
        ("specs extra", "metasphere.specs.list_specs", "unexpected argument"),
        ("status alpha extra", "metasphere.agents.list_agents", "unexpected argument"),
        ("status --all", "metasphere.agents.list_agents", "unexpected flag"),
    ],
)
def test_team_subcommands_reject_surplus_before_side_effect(
    monkeypatch, args, patched, detail
):
    monkeypatch.setattr(
        patched,
        lambda *a, **kw: pytest.fail("malformed argv reached command backend"),
    )

    result = commands.cmd_team(args, CTX)

    assert isinstance(result, str)
    assert "Usage: /team" in result
    assert detail in result


def test_team_assign_passes_exact_project_value(monkeypatch):
    seen = {}

    class Task:
        id = "task-1"

    def fake_dispatch(**kwargs):
        seen.update(kwargs)
        return {"task": Task(), "agent": None}

    monkeypatch.setattr("metasphere.tasks.dispatch_task", fake_dispatch)

    result = commands.cmd_team(
        'assign @agent "do the work" --project alpha', CTX,
    )

    assert isinstance(result, commands.Reply)
    assert seen == {
        "title": "do the work",
        "agent_id": "@agent",
        "project": "alpha",
    }


def test_team_assign_html_escapes_confirmation_fields(monkeypatch):
    task = SimpleNamespace(id="task<&>")
    monkeypatch.setattr(
        "metasphere.tasks.dispatch_task",
        lambda **kwargs: {"task": task, "agent": None},
    )

    result = commands.cmd_team(
        'assign @agent "review <draft> & reply" --project alpha', CTX,
    )

    assert isinstance(result, commands.Reply)
    assert result.parse_mode == "HTML"
    assert "task&lt;&amp;&gt;" in result.text
    assert "review &lt;draft&gt; &amp; reply" in result.text
    assert "review <draft>" not in result.text


def test_specs_html_escapes_loaded_metadata(monkeypatch):
    spec = SimpleNamespace(
        name="critic<&>", role="role<&>", description="desc <&>",
        sandbox="scope<&>",
    )
    monkeypatch.setattr("metasphere.specs.list_specs", lambda: [spec])

    result = commands.cmd_specs("", CTX)

    assert isinstance(result, commands.Reply)
    assert result.parse_mode == "HTML"
    assert "critic&lt;&amp;&gt;" in result.text
    assert "desc &lt;&amp;&gt;" in result.text
    assert "critic<&>" not in result.text


@pytest.mark.parametrize("handler", ["team", "agents"])
def test_agent_status_html_escapes_persisted_metadata(
    monkeypatch, tmp_path, handler
):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "spec").write_text("spec<&>\n")
    record = SimpleNamespace(
        is_persistent=True,
        session_name="session",
        project="project<&>",
        agent_dir=agent_dir,
        status="status<&>",
        name="@agent",
    )
    other = SimpleNamespace(
        is_persistent=True,
        session_name="other-session",
        project="other",
        agent_dir=agent_dir,
        status="ok",
        name="@other",
    )
    monkeypatch.setattr(
        "metasphere.agents.list_agents", lambda *a, **kw: [record, other],
    )
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)

    result = (
        commands.cmd_team("status", CTX)
        if handler == "team"
        else commands.cmd_agents("", CTX)
    )

    assert isinstance(result, commands.Reply)
    assert result.parse_mode == "HTML"
    assert "project&lt;&amp;&gt;" in result.text
    assert "spec&lt;&amp;&gt;" in result.text
    assert "status&lt;&amp;&gt;" in result.text
    assert "project<&>" not in result.text


def test_tasks_empty_html_escapes_known_project_names(monkeypatch):
    monkeypatch.setattr(
        "metasphere.tasks.active_tasks_across_projects", lambda paths: [],
    )
    monkeypatch.setattr(
        "metasphere.project.list_projects",
        lambda **kwargs: [SimpleNamespace(name="project<&>")],
    )

    result = commands.cmd_tasks("", CTX)

    assert isinstance(result, commands.Reply)
    assert result.parse_mode == "HTML"
    assert "project&lt;&amp;&gt;" in result.text
    assert "project<&>" not in result.text


def test_tasks_project_html_escapes_heading(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "metasphere.project._find_project", lambda name, paths: tmp_path,
    )
    monkeypatch.setattr("metasphere.tasks.list_tasks", lambda scope, repo: [])

    result = commands.cmd_tasks("project<&>", CTX)

    assert isinstance(result, commands.Reply)
    assert result.parse_mode == "HTML"
    assert "<b>Tasks (project&lt;&amp;&gt;)</b>" in result.text
    assert "project<&>" not in result.text
