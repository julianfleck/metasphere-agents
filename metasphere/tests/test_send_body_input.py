"""Tests for the zero-quoting body-input path on the agent-facing send CLIs.

Covers the shared helper (``metasphere.cli._body.resolve_body``) and the
stdin (``-``) / ``--body-file`` wiring on ``metasphere {slack,telegram,
message} send`` (argparse) and ``metasphere msg send`` (bare positional),
asserting bodies with ( ) • backticks $ " and newlines round-trip intact
to the underlying send call with no shell quoting involved.
"""

from __future__ import annotations

import io

import pytest

from metasphere.cli import _body

# A body that breaks naive shell quoting: parens, bullet, German low-quotes,
# backticks, $, double-quotes, and internal newlines.
RICH = (
    "Summary:\n"
    "• point one (with parens)\n"
    "• „German quotes“ and `backticks` and $HOME — all literal\n"
    'a "double quoted" tail'
)


# ---------------------------------------------------------------------------
# resolve_body
# ---------------------------------------------------------------------------


class TestResolveBody:
    def test_positional_passthrough(self):
        assert _body.resolve_body("hello") == "hello"

    def test_positional_expands_shell_safe_newlines(self):
        assert _body.resolve_body(r"first\n\nsecond") == "first\n\nsecond"

    def test_positional_expands_shell_safe_crlf(self):
        assert _body.resolve_body(r"first\r\nsecond") == "first\nsecond"

    def test_positional_doubled_backslash_preserves_literal_escape(self):
        assert _body.resolve_body(r"literal \\n token") == r"literal \n token"

    def test_positional_preserves_other_backslash_sequences(self):
        assert _body.resolve_body(r"price\tpath\q") == r"price\tpath\q"

    def test_stdin_preserves_literal_newline_escape(self):
        body = r"first\nsecond"
        assert _body.resolve_body("-", stdin=io.StringIO(body)) == body

    def test_file_preserves_literal_newline_escape(self, tmp_path):
        body = r"first\nsecond"
        f = tmp_path / "escaped.txt"
        f.write_text(body, encoding="utf-8")
        assert _body.resolve_body(None, str(f)) == body

    def test_positional_rich_unchanged(self):
        assert _body.resolve_body(RICH) == RICH

    def test_stdin_sentinel(self):
        assert _body.resolve_body("-", stdin=io.StringIO(RICH)) == RICH

    def test_body_file(self, tmp_path):
        f = tmp_path / "body.txt"
        f.write_text(RICH, encoding="utf-8")
        assert _body.resolve_body(None, str(f)) == RICH

    def test_strips_exactly_one_trailing_newline(self, tmp_path):
        f = tmp_path / "b.txt"
        f.write_text("line1\nline2\n", encoding="utf-8")
        assert _body.resolve_body(None, str(f)) == "line1\nline2"

    def test_preserves_extra_trailing_blank_line(self, tmp_path):
        f = tmp_path / "b.txt"
        f.write_text("x\n\n", encoding="utf-8")  # two newlines → keep one
        assert _body.resolve_body(None, str(f)) == "x\n"

    def test_strips_crlf(self):
        assert _body.resolve_body("-", stdin=io.StringIO("hi\r\n")) == "hi"

    def test_empty_positional_raises(self):
        with pytest.raises(ValueError):
            _body.resolve_body("   ")

    def test_empty_stdin_raises(self):
        with pytest.raises(ValueError):
            _body.resolve_body("-", stdin=io.StringIO("\n"))

    def test_allow_empty(self):
        assert _body.resolve_body("", allow_empty=True) == ""

    def test_conflict_positional_and_file_raises(self, tmp_path):
        f = tmp_path / "b.txt"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(ValueError):
            _body.resolve_body("also text", str(f))

    def test_dash_with_body_file_is_not_a_conflict(self, tmp_path):
        # "-" + --body-file: the file wins, the dash is just the placeholder.
        f = tmp_path / "b.txt"
        f.write_text("from file", encoding="utf-8")
        assert _body.resolve_body("-", str(f)) == "from file"

    def test_missing_file_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            _body.resolve_body(None, str(tmp_path / "nope.txt"))


# ---------------------------------------------------------------------------
# slack send wiring
# ---------------------------------------------------------------------------


class TestSlackSend:
    def _capture(self, monkeypatch):
        from metasphere.slack import api as slack_api
        captured = {}

        def fake_send_with_cc(surface_id, channel, text, **kw):
            captured["text"] = text
            captured["channel"] = channel

        monkeypatch.setattr(slack_api, "send_with_cc", fake_send_with_cc)
        return captured

    def test_positional(self, monkeypatch):
        from metasphere.cli import slack as cli
        cap = self._capture(monkeypatch)
        rc = cli.main(["send", RICH, "--channel", "C1"])
        assert rc == 0
        assert cap["text"] == RICH

    def test_stdin(self, monkeypatch):
        from metasphere.cli import slack as cli
        cap = self._capture(monkeypatch)
        monkeypatch.setattr("sys.stdin", io.StringIO(RICH))
        rc = cli.main(["send", "-", "--channel", "C1"])
        assert rc == 0
        assert cap["text"] == RICH

    def test_body_file(self, monkeypatch, tmp_path):
        from metasphere.cli import slack as cli
        cap = self._capture(monkeypatch)
        f = tmp_path / "b.txt"
        f.write_text(RICH, encoding="utf-8")
        rc = cli.main(["send", "--channel", "C1", "--body-file", str(f)])
        assert rc == 0
        assert cap["text"] == RICH

    def test_empty_positional_errors(self, monkeypatch):
        from metasphere.cli import slack as cli
        self._capture(monkeypatch)
        assert cli.main(["send", "  ", "--channel", "C1"]) == 2


# ---------------------------------------------------------------------------
# message send wiring
# ---------------------------------------------------------------------------


class TestMessageSend:
    def _capture(self, monkeypatch):
        from metasphere.cli import message as cli
        captured = {}
        monkeypatch.setattr(
            cli, "_dispatch",
            lambda surface_id, chat_id, text, **kw: captured.update(text=text) or 0,
        )
        return captured

    def test_stdin(self, monkeypatch):
        from metasphere.cli import message as cli
        cap = self._capture(monkeypatch)
        monkeypatch.setattr("sys.stdin", io.StringIO(RICH))
        rc = cli.main(["send", "-", "--surface", "slack-x", "--chat-id", "C1"])
        assert rc == 0
        assert cap["text"] == RICH

    def test_body_file(self, monkeypatch, tmp_path):
        from metasphere.cli import message as cli
        cap = self._capture(monkeypatch)
        f = tmp_path / "b.txt"
        f.write_text(RICH, encoding="utf-8")
        rc = cli.main(["send", "--surface", "slack-x", "--chat-id", "C1",
                       "--body-file", str(f)])
        assert rc == 0
        assert cap["text"] == RICH


# ---------------------------------------------------------------------------
# telegram send wiring
# ---------------------------------------------------------------------------


class TestTelegramSend:
    def _capture(self, monkeypatch):
        from metasphere.telegram import api as tg_api, archiver as tg_arch
        captured = {}

        def fake_send_with_cc(chat_id, text, **kw):
            captured["text"] = text
            captured["chat_id"] = chat_id

        monkeypatch.setattr(tg_api, "send_with_cc", fake_send_with_cc)
        monkeypatch.setattr(tg_arch, "archive_outgoing", lambda *a, **k: None)
        # Run as a non-orchestrator? No — orchestrator avoids the "[agent]"
        # body prefix so the round-trip is exact. Stub the explicit-send marker.
        monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
        monkeypatch.setenv("METASPHERE_SUPPRESS_TELEGRAM_DEPRECATION", "1")
        monkeypatch.setattr(
            "metasphere.posthook.mark_orchestrator_explicit_send",
            lambda *a, **k: None,
        )
        return captured

    def test_positional(self, monkeypatch):
        from metasphere.cli import telegram as cli
        cap = self._capture(monkeypatch)
        rc = cli.main(["send", RICH, "--chat-id", "12345"])
        assert rc == 0
        assert cap["text"] == RICH

    def test_stdin(self, monkeypatch):
        from metasphere.cli import telegram as cli
        cap = self._capture(monkeypatch)
        monkeypatch.setattr("sys.stdin", io.StringIO(RICH))
        rc = cli.main(["send", "-", "--chat-id", "12345"])
        assert rc == 0
        assert cap["text"] == RICH

    def test_body_file_with_chat_id(self, monkeypatch, tmp_path):
        from metasphere.cli import telegram as cli
        cap = self._capture(monkeypatch)
        f = tmp_path / "b.txt"
        f.write_text(RICH, encoding="utf-8")
        rc = cli.main(["send", "--chat-id", "12345", "--body-file", str(f)])
        assert rc == 0
        assert cap["text"] == RICH

    def test_body_file_rejects_text_positional(self, monkeypatch, tmp_path):
        from metasphere.cli import telegram as cli
        self._capture(monkeypatch)
        f = tmp_path / "b.txt"
        f.write_text("x", encoding="utf-8")
        # A non-@ positional alongside --body-file is ambiguous → error.
        assert cli.main(["send", "extra text", "--chat-id", "12345",
                         "--body-file", str(f)]) == 2


# ---------------------------------------------------------------------------
# msg send wiring
# ---------------------------------------------------------------------------


class TestMsgSend:
    def _capture(self, monkeypatch):
        from metasphere.cli import messages as cli
        captured = {}

        class _Msg:
            id = "msg-1"
            scope = "/"

        monkeypatch.setattr(
            cli._msgs, "send_message",
            lambda target, label, body, agent, paths=None: (
                captured.update(body=body, target=target, label=label) or _Msg()
            ),
        )
        return captured

    def test_positional(self, monkeypatch):
        from metasphere.cli import messages as cli
        cap = self._capture(monkeypatch)
        rc = cli.main(["send", "@alice", "!info", RICH])
        assert rc == 0
        # A single positional arg is joined verbatim (newlines preserved). The
        # real-world breakage is the SHELL splitting an unquoted body into many
        # argv words, which `" ".join` then collapses — see the multi-word case.
        assert cap["body"] == RICH

    def test_positional_multiword_join_collapses(self, monkeypatch):
        from metasphere.cli import messages as cli
        cap = self._capture(monkeypatch)
        # Simulates the shell having split the body into separate argv words
        # (what happens without quoting): the legacy join uses single spaces.
        rc = cli.main(["send", "@alice", "!info", "two", "words"])
        assert rc == 0
        assert cap["body"] == "two words"

    def test_stdin_preserves_newlines(self, monkeypatch):
        from metasphere.cli import messages as cli
        cap = self._capture(monkeypatch)
        monkeypatch.setattr("sys.stdin", io.StringIO(RICH))
        rc = cli.main(["send", "@alice", "!info", "-"])
        assert rc == 0
        assert cap["body"] == RICH

    def test_body_file_preserves_newlines(self, monkeypatch, tmp_path):
        from metasphere.cli import messages as cli
        cap = self._capture(monkeypatch)
        f = tmp_path / "b.txt"
        f.write_text(RICH, encoding="utf-8")
        rc = cli.main(["send", "@alice", "!info", "--body-file", str(f)])
        assert rc == 0
        assert cap["body"] == RICH

    def test_body_file_bad_arity_errors(self, monkeypatch):
        from metasphere.cli import messages as cli
        self._capture(monkeypatch)
        assert cli.main(["send", "@alice", "!info", "--body-file"]) == 1
