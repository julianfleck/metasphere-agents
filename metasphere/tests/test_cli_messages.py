"""Tests for ``metasphere msg send`` CLI shim (cli/messages.py).

Focused on the flag-shaped-positional reject guard added 2026-05-05
after two agents in the same morning both shipped malformed messages
by confabulating ``--to`` / ``--body`` flags. The shim is purely
positional —
``send <target> <label> <body...>`` — so a literal ``--to`` was
silently accepted as the target, the real target became the label,
and the body landed with the leading ``--body`` token still in it.
The guard hard-fails on either positional starting with ``--`` so
the corruption can't ship.
"""

from __future__ import annotations

import io
from unittest import mock

import pytest

from metasphere.cli import messages as cli_msgs


def test_send_rejects_flag_shaped_target(capsys, tmp_paths):
    """First positional starting with ``--`` is rejected. Reproduces
    the historical confabulation: agent invokes ``send --to
    @orchestrator --body "..."`` thinking those are flags; without
    the guard, the unpack writes ``to: --to`` to disk."""
    rc = cli_msgs._cmd_send(["--to", "@orchestrator", "--body", "some text"])
    assert rc == 1
    _, err = capsys.readouterr()
    assert "--to" in err
    assert "looks like a flag" in err
    assert "msg send @target !label" in err.lower() or (
        "messages send @target !label" in err
    )


def test_send_rejects_flag_shaped_label(capsys, tmp_paths):
    """Second positional starting with ``--`` (target was real but the
    LABEL got a flag-shape) is also rejected. Defensive symmetry: the
    same confabulation could land in either slot depending on how the
    agent shuffled args."""
    rc = cli_msgs._cmd_send(["@orchestrator", "--body", "some text"])
    assert rc == 1
    _, err = capsys.readouterr()
    assert "--body" in err
    assert "looks like a flag" in err


def test_send_correct_positional_shape_succeeds(capsys, tmp_paths):
    """Sanity: the canonical positional shape still works — target and
    label are real, body is non-empty. Guard must not regress the
    legitimate path."""
    sent_calls: list[tuple] = []

    fake_msg = mock.MagicMock()
    fake_msg.id = "msg-test-12345"
    fake_msg.scope = "/"

    def fake_send_message(target, label, body, agent, paths=None):
        sent_calls.append((target, label, body, agent))
        return fake_msg

    with mock.patch.object(cli_msgs._msgs, "send_message", side_effect=fake_send_message):
        rc = cli_msgs._cmd_send(["@orchestrator", "!info", "all good", "more", "words"])

    assert rc == 0
    assert len(sent_calls) == 1
    target, label, body, _agent = sent_calls[0]
    assert target == "@orchestrator"
    assert label == "!info"
    assert body == "all good more words"


def test_send_body_containing_double_dash_mid_text_succeeds(capsys, tmp_paths):
    """A body argument with ``--`` mid-text (legitimate prose / em-dash
    rendering / cli-example quoting) must not be rejected. The guard
    only inspects target and label; body tokens are body content."""
    sent_calls: list[tuple] = []
    fake_msg = mock.MagicMock()
    fake_msg.id = "msg-test-67890"
    fake_msg.scope = "/"

    def fake_send_message(target, label, body, agent, paths=None):
        sent_calls.append((target, label, body, agent))
        return fake_msg

    with mock.patch.object(cli_msgs._msgs, "send_message", side_effect=fake_send_message):
        rc = cli_msgs._cmd_send([
            "@orchestrator", "!info",
            "ran", "git", "log", "--oneline", "--since=yesterday",
            "—", "5", "commits",
        ])

    assert rc == 0
    assert sent_calls[0][2] == "ran git log --oneline --since=yesterday — 5 commits"


@pytest.mark.parametrize(
    "bad_target",
    ["-x", "-tag", "--to", "--body"],
)
def test_send_rejects_single_dash_target(capsys, tmp_paths, bad_target):
    """Tighten the existing ``--`` guard to ``-`` — no legitimate
    target ever starts with a dash (targets are ``@name``, ``!label``,
    or ``<scope-rel>``). Catches the short-flag confabulation shape
    (``-x``, ``-tag``) the same way ``--`` catches long-flag shapes."""
    rc = cli_msgs._cmd_send([bad_target, "!info", "body text"])
    assert rc == 1
    _, err = capsys.readouterr()
    assert bad_target in err
    assert "looks like a flag" in err


@pytest.mark.parametrize(
    "op,handler,args",
    [
        ("reply", cli_msgs._cmd_reply, ["--bogus", "response"]),
        ("done", cli_msgs._cmd_done, ["--bogus", "note"]),
        ("read", cli_msgs._cmd_read, ["--bogus"]),
        ("status", cli_msgs._cmd_status, ["--bogus"]),
    ],
)
def test_msg_id_ops_reject_flag_shape(capsys, tmp_paths, op, handler, args):
    """``reply`` / ``done`` / ``read`` / ``status`` previously raised an
    uncaught ``FileNotFoundError`` (ugly traceback) when given a
    flag-shaped msg-id. Reject up front with a clean stderr line so
    the user sees what went wrong, not a Python stacktrace."""
    rc = handler(args)
    assert rc == 1
    _, err = capsys.readouterr()
    assert "--bogus" in err
    assert "looks like a flag" in err
    assert op in err


@pytest.mark.parametrize(
    "op,handler,args,patch_target",
    [
        ("reply", cli_msgs._cmd_reply, ["msg-missing-9999", "response"],
         "reply_to_message"),
        ("done", cli_msgs._cmd_done, ["msg-missing-9999", "note"],
         "mark_done"),
        ("read", cli_msgs._cmd_read, ["msg-missing-9999"],
         "mark_read"),
    ],
)
def test_msg_id_ops_clean_error_for_missing(
    capsys, tmp_paths, op, handler, args, patch_target,
):
    """Real (non-flag) msg-id that doesn't exist on disk: previously
    surfaced as an uncaught ``FileNotFoundError`` traceback. Now caught
    → rc=1 + the error message on stderr."""
    def raise_nf(*_a, **_kw):
        raise FileNotFoundError(f"message {args[0]} not found")

    with mock.patch.object(cli_msgs._msgs, patch_target, side_effect=raise_nf):
        rc = handler(args)

    assert rc == 1
    _, err = capsys.readouterr()
    assert args[0] in err
    assert "not found" in err


# ---------------------------------------------------------------------------
# reply / done body-source parity (--body-file + stdin `-`)
#
# Regression: `msg reply <id> --body-file X` and `msg reply <id> -` used to
# `" ".join(rest)` the trailing tokens straight into the body, so the FLAG
# STRING shipped as the message and the real content was dropped silently —
# and it fired exactly on the rich/long replies agents reach for --body-file
# to send. `send` already routed through resolve_body; these give reply/done
# the same sources. (4th recurrence 2026-06-30, on a live operator-facing relay.)
# ---------------------------------------------------------------------------


def test_reply_reads_body_file_not_literal_flag(capsys, tmp_paths, tmp_path):
    """`reply <id> --body-file PATH` round-trips the FILE content — not the
    literal string '--body-file PATH' (the silent content-loss bug)."""
    body_path = tmp_path / "reply-body.txt"
    body_path.write_text("the real reply — multi\nline & special $chars\n",
                         encoding="utf-8")
    seen: list[str] = []
    fake = mock.MagicMock(id="msg-reply-1")

    def fake_reply(orig, body, agent, paths=None):
        seen.append(body)
        return fake

    with mock.patch.object(cli_msgs._msgs, "reply_to_message", side_effect=fake_reply):
        rc = cli_msgs._cmd_reply(["msg-orig-123", "--body-file", str(body_path)])

    assert rc == 0
    # File content (one trailing newline stripped), NOT the flag token.
    assert seen == ["the real reply — multi\nline & special $chars"]
    assert "--body-file" not in seen[0]


def test_reply_reads_stdin_dash(capsys, tmp_paths, monkeypatch):
    """`reply <id> -` reads the body verbatim from stdin, not literal '-'."""
    monkeypatch.setattr("sys.stdin", io.StringIO("piped reply body\n"))
    seen: list[str] = []

    def fake_reply(orig, body, agent, paths=None):
        seen.append(body)
        return mock.MagicMock(id="msg-reply-2")

    with mock.patch.object(cli_msgs._msgs, "reply_to_message", side_effect=fake_reply):
        rc = cli_msgs._cmd_reply(["msg-orig-123", "-"])

    assert rc == 0
    assert seen == ["piped reply body"]


def test_reply_inline_text_still_works(capsys, tmp_paths):
    """Parity must not regress the simple inline path."""
    seen: list[str] = []

    def fake_reply(orig, body, agent, paths=None):
        seen.append(body)
        return mock.MagicMock(id="msg-reply-3")

    with mock.patch.object(cli_msgs._msgs, "reply_to_message", side_effect=fake_reply):
        rc = cli_msgs._cmd_reply(["msg-orig-123", "all", "good", "here"])

    assert rc == 0
    assert seen == ["all good here"]


def test_reply_missing_body_file_errors_loudly(capsys, tmp_paths, tmp_path):
    """A --body-file that can't be read is a LOUD rc=1 error — never a
    silent send of the path string."""
    missing = tmp_path / "nope.txt"
    with mock.patch.object(cli_msgs._msgs, "reply_to_message") as send:
        rc = cli_msgs._cmd_reply(["msg-orig-123", "--body-file", str(missing)])
    assert rc == 1
    send.assert_not_called()
    _, err = capsys.readouterr()
    assert "body-file" in err


def test_done_reads_body_file(capsys, tmp_paths, tmp_path):
    """`done <id> --body-file PATH` routes the note through the file too."""
    note_path = tmp_path / "note.txt"
    note_path.write_text("resolution note from file\n", encoding="utf-8")
    seen: list[str] = []

    def fake_done(orig, note, agent, paths=None):
        seen.append(note)
        return None

    with mock.patch.object(cli_msgs._msgs, "mark_done", side_effect=fake_done):
        rc = cli_msgs._cmd_done(["msg-orig-123", "--body-file", str(note_path)])

    assert rc == 0
    assert seen == ["resolution note from file"]


def test_done_no_note_still_allowed(capsys, tmp_paths):
    """`done <id>` with no note stays valid — the note is optional, so the
    empty-body guard must NOT fire for done (unlike reply/send)."""
    seen: list[str] = []

    def fake_done(orig, note, agent, paths=None):
        seen.append(note)
        return None

    with mock.patch.object(cli_msgs._msgs, "mark_done", side_effect=fake_done):
        rc = cli_msgs._cmd_done(["msg-orig-123"])

    assert rc == 0
    assert seen == [""]
