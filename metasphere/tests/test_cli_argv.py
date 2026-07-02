"""Tests for ``metasphere.cli._argv.reject_flag_shape``.

Consolidated helper for the flag-leak audit pattern that landed
across 4 CLI surfaces (session, project, restart, hooks git).
"""

from __future__ import annotations

import pytest

from metasphere.cli._argv import reject_flag_shape


def test_normal_positional_returns_none(capsys):
    rc = reject_flag_shape("foo", "send", command="metasphere session")
    assert rc is None
    captured = capsys.readouterr()
    assert captured.err == ""


def test_at_prefixed_agent_id_returns_none(capsys):
    # ``@agent`` is a normal positional even though it starts with @.
    rc = reject_flag_shape("@agent", "send", command="metasphere session")
    assert rc is None


def test_double_dash_flag_rejected_rc2(capsys):
    rc = reject_flag_shape("--foo", "send", command="metasphere session",
                           what="agent id")
    assert rc == 2
    err = capsys.readouterr().err
    assert "metasphere session send" in err
    assert "--foo" in err
    assert "looks like a CLI flag" in err
    assert "agent id" in err


def test_short_flag_rejected_rc2(capsys):
    rc = reject_flag_shape("-x", "info", command="hooks git",
                           what="path")
    assert rc == 2
    err = capsys.readouterr().err
    assert "hooks git info" in err
    assert "-x" in err
    assert "not a path" in err


def test_default_what_is_argument(capsys):
    reject_flag_shape("--foo", "op", command="cmd")
    err = capsys.readouterr().err
    assert "not an argument" in err


def test_vowel_what_uses_an_article(capsys):
    reject_flag_shape("--foo", "op", command="cmd", what="agent id")
    err = capsys.readouterr().err
    assert "not an agent id" in err


def test_consonant_what_uses_a_article(capsys):
    reject_flag_shape("--foo", "op", command="cmd", what="project name")
    err = capsys.readouterr().err
    assert "not a project name" in err


def test_usage_hint_appended(capsys):
    reject_flag_shape("--foo", "send", command="metasphere session",
                      what="agent id",
                      usage="Use: session send <@agent> <message>")
    err = capsys.readouterr().err
    assert "Use: session send <@agent> <message>" in err


def test_no_usage_hint_no_trailing_text(capsys):
    reject_flag_shape("--foo", "send", command="metasphere session",
                      what="agent id")
    err = capsys.readouterr().err
    # No trailing "Use: ..." segment when usage=None.
    assert "Use:" not in err


@pytest.mark.parametrize("flag", ["--help", "-h", "--bogus", "-x", "--force"])
def test_all_flag_shapes_rejected(capsys, flag):
    """``--help``/``-h`` are NOT intercepted here — callers handle that
    before dispatching. The helper treats all dash-prefixed tokens as
    flag-shaped."""
    rc = reject_flag_shape(flag, "op", command="cmd")
    assert rc == 2
    err = capsys.readouterr().err
    assert flag in err
