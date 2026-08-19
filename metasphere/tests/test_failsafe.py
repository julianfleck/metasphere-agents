"""Tests for metasphere.cli.failsafe — rate-limit probe + credential rotation."""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from metasphere.cli.failsafe import (
    _COOLDOWN_SECONDS,
    _cooldown_elapsed,
    _mark_rotated,
    _next_profile,
    _pane_has_rate_limit,
    probe_and_rotate,
)
from metasphere.cli.accounts import CRED_FILENAME


@pytest.fixture(autouse=True)
def _linux_credential_store(monkeypatch):
    """Credential-rotation tests exercise the non-Keychain code path."""
    import metasphere.cli.failsafe as fs

    monkeypatch.setattr(fs.sys, "platform", "linux")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profiles(tmp_path: Path, names: list[str]) -> Path:
    """Create credential profile dirs under tmp_path/accounts/."""
    accts = tmp_path / "accounts"
    for name in names:
        p = accts / name / CRED_FILENAME
        p.parent.mkdir(parents=True)
        p.write_text('{"token": "fake"}')
    return accts


def _make_live_symlink(tmp_path: Path, accounts_dir: Path, current_name: str) -> Path:
    """Create a live-cred symlink pointing at current_name inside accounts_dir."""
    target = accounts_dir / current_name / CRED_FILENAME
    live = tmp_path / "live_cred.json"
    live.symlink_to(target)
    return live


# ---------------------------------------------------------------------------
# _pane_has_rate_limit
# ---------------------------------------------------------------------------


class TestPaneHasRateLimit:
    def test_detects_rate_limit_error(self):
        assert _pane_has_rate_limit("Error: rate_limit_error occurred")

    def test_detects_429(self):
        assert _pane_has_rate_limit("HTTP 429 Too Many Requests")

    def test_detects_usage_limit(self):
        assert _pane_has_rate_limit("You have reached your usage limit for today")

    def test_detects_reached_its_limit(self):
        assert _pane_has_rate_limit("Your Claude.ai account has reached its limit")

    def test_detects_rate_limited_lowercase(self):
        assert _pane_has_rate_limit("connection is rate limited now")

    def test_no_false_positive_on_normal_output(self):
        assert not _pane_has_rate_limit(
            "Tool: Read\nFile: /path/to/file.py\nContent: hello world\n[idle]"
        )

    def test_no_false_positive_on_empty(self):
        assert not _pane_has_rate_limit("")

    def test_tail_only_inspects_last_lines(self):
        # Stuff a rate-limit keyword way above the tail window — should NOT trigger.
        long_preamble = "rate_limit_error\n" * 5 + ("normal output\n" * 200)
        assert not _pane_has_rate_limit(long_preamble)

    def test_tail_detects_near_end(self):
        # Rate-limit in last 60 lines — SHOULD trigger.
        preamble = "normal output\n" * 200
        tail_hit = preamble + "usage limit exceeded\n"
        assert _pane_has_rate_limit(tail_hit)


# ---------------------------------------------------------------------------
# _next_profile
# ---------------------------------------------------------------------------


class TestNextProfile:
    def test_returns_none_if_fewer_than_two_profiles(self, tmp_path):
        accts = _make_profiles(tmp_path, ["primary"])
        live = _make_live_symlink(tmp_path, accts, "primary")
        assert _next_profile(accts, live) is None

    def test_round_robins_from_primary_to_spare(self, tmp_path):
        accts = _make_profiles(tmp_path, ["primary", "spare"])
        live = _make_live_symlink(tmp_path, accts, "primary")
        assert _next_profile(accts, live) == "spare"

    def test_wraps_around_from_last_to_first(self, tmp_path):
        accts = _make_profiles(tmp_path, ["alpha", "beta", "gamma"])
        live = _make_live_symlink(tmp_path, accts, "gamma")
        assert _next_profile(accts, live) == "alpha"

    def test_unknown_current_picks_first(self, tmp_path):
        accts = _make_profiles(tmp_path, ["alpha", "beta"])
        # live symlink doesn't exist — current is unknown
        live = tmp_path / "missing_live.json"
        assert _next_profile(accts, live) == "alpha"

    def test_returns_none_if_no_accounts_dir(self, tmp_path):
        live = tmp_path / "live.json"
        assert _next_profile(tmp_path / "nonexistent", live) is None

    def test_middle_of_three(self, tmp_path):
        accts = _make_profiles(tmp_path, ["alpha", "beta", "gamma"])
        live = _make_live_symlink(tmp_path, accts, "beta")
        assert _next_profile(accts, live) == "gamma"


# ---------------------------------------------------------------------------
# probe_and_rotate integration
# ---------------------------------------------------------------------------


class TestProbeAndRotate:
    """End-to-end tests using monkeypatching to redirect fs + subprocess."""

    def _paths_stub(self):
        return SimpleNamespace(state=Path("/tmp/__failsafe_test_state__"))

    def test_no_rotation_when_no_rate_limit(self, tmp_path):
        accts = _make_profiles(tmp_path, ["primary", "spare"])
        live = _make_live_symlink(tmp_path, accts, "primary")

        with (
            patch("metasphere.cli.failsafe._capture_pane", return_value="[idle]"),
            patch("metasphere.cli.failsafe.ACCOUNTS_DIR", accts, create=True),
            patch("metasphere.cli.accounts.ACCOUNTS_DIR", accts),
            patch("metasphere.cli.accounts.LIVE_CRED", live),
        ):
            # reset cooldown
            import metasphere.cli.failsafe as fs
            fs._last_rotation_ts = float("-inf")
            result = probe_and_rotate("fake-session", self._paths_stub())

        assert result is False
        assert live.resolve() == (accts / "primary" / CRED_FILENAME).resolve()

    def test_rotates_on_rate_limit_signal(self, tmp_path):
        accts = _make_profiles(tmp_path, ["primary", "spare"])
        live = _make_live_symlink(tmp_path, accts, "primary")

        pane_with_limit = "Tool: Bash\nOutput: ...\nrate_limit_error: quota exceeded"

        with (
            patch("metasphere.cli.failsafe._capture_pane", return_value=pane_with_limit),
            patch("metasphere.cli.failsafe.ACCOUNTS_DIR", accts, create=True),
            patch("metasphere.cli.accounts.ACCOUNTS_DIR", accts),
            patch("metasphere.cli.accounts.LIVE_CRED", live),
            patch("metasphere.events.log_event", side_effect=Exception("skip")),
        ):
            import metasphere.cli.failsafe as fs
            fs._last_rotation_ts = float("-inf")
            result = probe_and_rotate("fake-session", self._paths_stub())

        assert result is True
        # symlink now points at spare
        resolved = live.resolve()
        assert resolved == (accts / "spare" / CRED_FILENAME).resolve()

    def test_cooldown_prevents_second_rotation(self, tmp_path):
        accts = _make_profiles(tmp_path, ["primary", "spare"])
        live = _make_live_symlink(tmp_path, accts, "primary")

        pane_with_limit = "usage limit exceeded"

        import metasphere.cli.failsafe as fs

        with (
            patch("metasphere.cli.failsafe._capture_pane", return_value=pane_with_limit),
            patch("metasphere.cli.accounts.ACCOUNTS_DIR", accts),
            patch("metasphere.cli.accounts.LIVE_CRED", live),
            patch("metasphere.events.log_event", side_effect=Exception("skip")),
        ):
            fs._last_rotation_ts = float("-inf")
            r1 = probe_and_rotate("fake-session", self._paths_stub())
            # cooldown now active — second call must be skipped
            r2 = probe_and_rotate("fake-session", self._paths_stub())

        assert r1 is True
        assert r2 is False

    def test_skipped_on_single_profile(self, tmp_path):
        accts = _make_profiles(tmp_path, ["primary"])
        live = _make_live_symlink(tmp_path, accts, "primary")

        pane_with_limit = "rate_limit_error"

        import metasphere.cli.failsafe as fs
        with (
            patch("metasphere.cli.failsafe._capture_pane", return_value=pane_with_limit),
            patch("metasphere.cli.accounts.ACCOUNTS_DIR", accts),
            patch("metasphere.cli.accounts.LIVE_CRED", live),
        ):
            fs._last_rotation_ts = float("-inf")
            result = probe_and_rotate("fake-session", self._paths_stub())

        assert result is False

    def test_skipped_on_darwin(self, tmp_path):
        import metasphere.cli.failsafe as fs
        fs._last_rotation_ts = 0.0
        with patch("metasphere.cli.failsafe.sys") as mock_sys:
            mock_sys.platform = "darwin"
            result = probe_and_rotate("fake-session", self._paths_stub())
        assert result is False

    def test_rotate_log_attributes_detecting_agent(self, tmp_path):
        """When triggered by a non-orchestrator pane, the rotate event
        must record the detecting agent so we can trace which agent's
        pane caught the signal — not always '@orchestrator'."""
        accts = _make_profiles(tmp_path, ["primary", "spare"])
        live = _make_live_symlink(tmp_path, accts, "primary")

        pane_with_limit = "rate_limit_error: quota exceeded"

        import metasphere.cli.failsafe as fs
        with (
            patch("metasphere.cli.failsafe._capture_pane", return_value=pane_with_limit),
            patch("metasphere.cli.accounts.ACCOUNTS_DIR", accts),
            patch("metasphere.cli.accounts.LIVE_CRED", live),
            patch("metasphere.events.log_event") as mock_log,
            patch("metasphere.posthook._resolve_chat_id", return_value=None),
        ):
            fs._last_rotation_ts = float("-inf")
            result = probe_and_rotate(
                "fake-session", self._paths_stub(), agent="@widget-eng"
            )

        assert result is True
        assert mock_log.called
        # Find the rotate event — log_event may have been called multiple
        # times across the path; we want the failsafe.rotate one.
        rotate_calls = [
            c for c in mock_log.call_args_list
            if c.args and c.args[0] == "failsafe.rotate"
        ]
        assert len(rotate_calls) == 1
        assert rotate_calls[0].kwargs["agent"] == "@widget-eng"
        assert "@widget-eng" in rotate_calls[0].args[1]

    def test_skip_unmanaged_log_attributes_detecting_agent(self, tmp_path):
        """skip_unmanaged event also names the detecting agent."""
        accts = _make_profiles(tmp_path, ["primary", "spare"])
        live = tmp_path / "live_cred.json"
        live.write_text('{"token": "real"}')

        pane_with_limit = "rate_limit_error"

        import metasphere.cli.failsafe as fs
        with (
            patch("metasphere.cli.failsafe._capture_pane", return_value=pane_with_limit),
            patch("metasphere.cli.accounts.ACCOUNTS_DIR", accts),
            patch("metasphere.cli.accounts.LIVE_CRED", live),
            patch("metasphere.events.log_event") as mock_log,
        ):
            fs._last_rotation_ts = float("-inf")
            result = probe_and_rotate(
                "fake-session", self._paths_stub(), agent="@widget-eng"
            )

        assert result is False
        skip_calls = [
            c for c in mock_log.call_args_list
            if c.args and c.args[0] == "failsafe.skip_unmanaged"
        ]
        assert len(skip_calls) == 1
        assert skip_calls[0].kwargs["agent"] == "@widget-eng"

    def test_skipped_when_live_cred_is_unmanaged_file(self, tmp_path):
        """If LIVE_CRED is a regular file (not a symlink), rotation
        would clobber the original credentials without backup.  The
        failsafe must bail in that state."""
        accts = _make_profiles(tmp_path, ["primary", "spare"])
        # LIVE_CRED is a real file, NOT a symlink — the unmanaged state.
        live = tmp_path / "live_cred.json"
        original_content = '{"token": "real-original-creds"}'
        live.write_text(original_content)

        pane_with_limit = "rate_limit_error: quota exceeded"

        import metasphere.cli.failsafe as fs
        with (
            patch("metasphere.cli.failsafe._capture_pane", return_value=pane_with_limit),
            patch("metasphere.cli.accounts.ACCOUNTS_DIR", accts),
            patch("metasphere.cli.accounts.LIVE_CRED", live),
            patch("metasphere.events.log_event") as mock_log,
        ):
            fs._last_rotation_ts = float("-inf")
            result = probe_and_rotate("fake-session", self._paths_stub())

        assert result is False
        # Original file content untouched, still a real file.
        assert live.is_file() and not live.is_symlink()
        assert live.read_text() == original_content
        # Skip event was logged.
        assert mock_log.called
        assert mock_log.call_args[0][0] == "failsafe.skip_unmanaged"
