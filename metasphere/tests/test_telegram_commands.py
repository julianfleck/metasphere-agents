"""Focused tests for side-effect boundaries in Telegram slash commands."""

from pathlib import Path

import pytest

from metasphere.telegram import commands


CTX = commands.Context(chat_id=1, from_user="tester")


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
