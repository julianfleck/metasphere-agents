"""Tests for the unified ``metasphere`` CLI dispatcher."""

from __future__ import annotations

import importlib
import io
import sys

import pytest

from metasphere.cli import _shims, main as main_mod


def _run(argv, capsys):
    rc = main_mod.main(argv)
    out, err = capsys.readouterr()
    return rc, out, err


def test_top_level_help_lists_subcommands(capsys):
    rc, out, _ = _run(["--help"], capsys)
    assert rc == 0
    for sub in [
        "agent", "msg", "task", "telegram", "hooks",
        "schedule", "heartbeat", "memory", "trace",
        "session", "project", "gateway", "status", "ls",
    ]:
        assert sub in out


def test_no_args_prints_help(capsys):
    rc, out, _ = _run([], capsys)
    assert rc == 0
    assert "Usage: metasphere" in out


def test_unknown_subcommand_exits_2(capsys):
    rc, _, err = _run(["bogusquux"], capsys)
    assert rc == 2
    assert "unknown subcommand" in err


def test_registry_resolves_every_entry():
    """Lazy-import every registry target — catches typos before runtime."""
    failures = []
    for key, target in main_mod.REGISTRY.items():
        try:
            mod_name, func_name = target.split(":")
            mod = importlib.import_module(mod_name)
            assert callable(getattr(mod, func_name)), f"{target} not callable"
        except Exception as e:  # pragma: no cover
            failures.append(f"{key} -> {target}: {e}")
    assert not failures, failures


@pytest.mark.parametrize(
    "subcmd",
    [
        ["agent"],
        ["agent", "spawn"],
        ["agent", "wake"],
        ["msg"],
        ["task"],
        ["telegram"],
        ["telegram", "groups"],
        ["hooks"],
        ["hooks", "posthook"],
        ["hooks", "context"],
        ["hooks", "git"],
        ["schedule"],
        ["heartbeat"],
        ["memory"],
        ["trace"],
        ["session"],
        ["project"],
        ["gateway"],
    ],
)
def test_subcommand_help_does_not_crash(subcmd, capsys):
    """Each subcommand --help must exit cleanly (rc 0 or 1) and not raise."""
    try:
        rc = main_mod.main([*subcmd, "--help"])
    except SystemExit as e:
        rc = int(e.code or 0)
    assert rc in (0, 1, 2), f"unexpected rc={rc} for {subcmd}"
    capsys.readouterr()  # drain


def test_hooks_unknown_subcommand_exits_2(capsys):
    rc, _, err = _run(["hooks", "nope"], capsys)
    assert rc == 2
    assert "unknown subcommand" in err


def test_telegram_groups_routes_to_groups_module(monkeypatch, capsys):
    called = {}

    def fake_main(argv):
        called["argv"] = list(argv)
        return 0

    import metasphere.cli.telegram_groups as tg
    monkeypatch.setattr(tg, "main", fake_main)
    rc = main_mod.main(["telegram", "groups", "--help"])
    assert rc == 0
    assert called["argv"] == ["--help"]


def test_telegram_root_routes_to_telegram_module(monkeypatch):
    called = {}

    def fake_main(argv):
        called["argv"] = list(argv)
        return 0

    import metasphere.cli.telegram as tel
    monkeypatch.setattr(tel, "main", fake_main)
    rc = main_mod.main(["telegram", "send", "@x", "hi"])
    assert rc == 0
    assert called["argv"] == ["send", "@x", "hi"]


def test_shim_forwards_with_prefix(monkeypatch):
    captured = {}

    def fake_main(argv):
        captured["argv"] = list(argv)
        return 0

    monkeypatch.setattr(main_mod, "main", fake_main)
    monkeypatch.setattr(sys, "argv", ["metasphere-posthook", "--dry-run"])
    rc = _shims.metasphere_posthook()
    assert rc == 0
    assert captured["argv"] == ["hooks", "posthook", "--dry-run"]


def test_shim_silent_when_stderr_not_tty(monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "main", lambda argv: 0)
    monkeypatch.setattr(sys, "argv", ["metasphere-spawn"])
    # capsys replaces stderr with a non-TTY StringIO
    rc = _shims.metasphere_spawn()
    assert rc == 0
    _, err = capsys.readouterr()
    assert err == ""


def test_shim_warns_on_tty(monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "main", lambda argv: 0)
    monkeypatch.setattr(sys, "argv", ["metasphere-spawn"])

    class FakeTTY(io.StringIO):
        def isatty(self):
            return True

    fake = FakeTTY()
    monkeypatch.setattr(sys, "stderr", fake)
    rc = _shims.metasphere_spawn()
    assert rc == 0
    assert "deprecated" in fake.getvalue()
    assert "metasphere agent spawn" in fake.getvalue()
