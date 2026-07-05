"""Self-checks for the autouse live-process-spawn guard in conftest.py.

The guard is a structural backstop (sibling to the tmux sandbox guard and
the filesystem-pollution detector): it fails a test loudly rather than let
it spawn ``claude``/``systemctl``/``launchctl`` against the live host. These
tests pin that behaviour both ways — the guard bites on denylisted commands
and stays out of the way of everything else — so a future conftest refactor
can't neuter it without turning one of these red.
"""

import subprocess
import sys

import pytest

from metasphere.tests.conftest import (
    _LIVE_SPAWN_DENYLIST,
    _spawn_argv0_basename,
)


@pytest.mark.parametrize("cmd", [
    ["systemctl", "--user", "status", "metasphere-gateway"],
    ["claude", "-p", "--allowedTools", "Read"],
    ["launchctl", "load", "/tmp/x.plist"],
    # ssh: cmd_spot() in telegram/commands.py spawns this against
    # METASPHERE_REMOTE_HOST — a live cross-host connection, same class as
    # the systemctl restarts. No test mocks it today (convention-only), so
    # the structural guard is what actually keeps a future /spot test off
    # the wire.
    ["ssh", "-p", "22", "-o", "BatchMode=yes", "host", "metasphere status"],
])
def test_guard_blocks_denylisted_argv(cmd):
    with pytest.raises(AssertionError, match="live process"):
        subprocess.run(cmd, capture_output=True)


def test_guard_blocks_denylisted_via_popen():
    with pytest.raises(AssertionError, match="live process"):
        subprocess.Popen(["claude", "-p"])


def test_guard_blocks_via_absolute_path():
    # basename resolution: a fully-qualified path still trips the guard.
    with pytest.raises(AssertionError, match="live process"):
        subprocess.run(["/usr/bin/systemctl", "--user", "daemon-reload"])


def test_guard_blocks_shell_string_form():
    with pytest.raises(AssertionError, match="live process"):
        subprocess.run("systemctl --user restart metasphere-gateway", shell=True)


def test_guard_allows_non_denylisted_command_to_run_for_real():
    # A harmless, always-present command that is NOT on the denylist must
    # pass straight through to the real subprocess implementation.
    proc = subprocess.run(
        [sys.executable, "-c", "print('ok')"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "ok"


@pytest.mark.parametrize("args,expected", [
    (["systemctl", "status"], "systemctl"),
    (["/usr/bin/systemctl", "status"], "systemctl"),
    ("claude -p", "claude"),
    (b"launchctl load x", "launchctl"),
    ([], ""),
    ("", ""),
    (None, ""),
    (123, ""),
])
def test_spawn_argv0_basename_parsing(args, expected):
    assert _spawn_argv0_basename(args) == expected


def test_denylist_covers_the_live_host_control_commands():
    # Guards against an accidental denylist edit dropping a command.
    assert {"claude", "systemctl", "launchctl", "ssh"} <= _LIVE_SPAWN_DENYLIST
