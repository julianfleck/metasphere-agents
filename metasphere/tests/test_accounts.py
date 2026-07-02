"""Tests for ``metasphere accounts``.

Every test redirects ``ACCOUNTS_DIR`` and ``LIVE_CRED`` into ``tmp_path``
so the suite never touches the operator's real ``~/.metasphere/accounts/``
or ``~/.claude/.credentials.json``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from metasphere.cli import accounts as A


# ---------------------------------------------------------------------------
# Sandbox fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect module constants into ``tmp_path``.

    Returns a small object with helper methods to seed profiles + the
    live credentials file, so tests stay short.
    """
    accounts_dir = tmp_path / "accounts"
    live_cred = tmp_path / "live" / ".credentials.json"
    accounts_dir.mkdir()
    live_cred.parent.mkdir()

    monkeypatch.setattr(A, "ACCOUNTS_DIR", accounts_dir)
    monkeypatch.setattr(A, "LIVE_CRED", live_cred)
    # Force-Linux for tests so the platform gate doesn't short-circuit
    # us on a hypothetical macOS CI runner.
    monkeypatch.setattr(sys, "platform", "linux")

    class Sandbox:
        def __init__(self):
            self.accounts_dir = accounts_dir
            self.live_cred = live_cred

        def seed_profile(self, name: str, body: str = '{"k":"v"}', mode: int = 0o600):
            p = accounts_dir / name / A.CRED_FILENAME
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
            os.chmod(p, mode)
            return p

        def link_live(self, target: Path):
            if live_cred.exists() or live_cred.is_symlink():
                live_cred.unlink()
            os.symlink(str(target), str(live_cred))

        def write_live_file(self, body: str):
            """Materialize ``live_cred`` as a regular file (unmanaged)."""
            if live_cred.exists() or live_cred.is_symlink():
                live_cred.unlink()
            live_cred.write_text(body)

    return Sandbox()


# ---------------------------------------------------------------------------
# Platform gate
# ---------------------------------------------------------------------------


def test_darwin_refused(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    rc = A.main(["list"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "macOS is not supported" in err


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_empty(sandbox, capsys):
    rc = A.main(["list"])
    assert rc == 0
    assert capsys.readouterr().out == "(no profiles)\n"


def test_list_populated_marks_current(sandbox, capsys):
    sandbox.seed_profile("primary")
    p_spare = sandbox.seed_profile("spare")
    sandbox.link_live(p_spare)
    rc = A.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    lines = out.rstrip("\n").split("\n")
    assert lines == ["  primary", "* spare"]


def test_list_skips_dirs_without_credentials_file(sandbox, capsys):
    """A bare directory under accounts/ that lacks credentials.json
    should not be listed — the layout invariant is one cred per dir."""
    (sandbox.accounts_dir / "broken").mkdir()
    sandbox.seed_profile("primary")
    rc = A.main(["list"])
    assert rc == 0
    assert capsys.readouterr().out.rstrip("\n").split("\n") == ["  primary"]


# ---------------------------------------------------------------------------
# current
# ---------------------------------------------------------------------------


def test_current_missing_when_no_link(sandbox, capsys):
    rc = A.main(["current"])
    assert rc == 0
    assert capsys.readouterr().out == "missing\n"


def test_current_unmanaged_when_regular_file(sandbox, capsys):
    sandbox.write_live_file('{"k":"v"}')
    rc = A.main(["current"])
    assert rc == 0
    assert capsys.readouterr().out == "unmanaged\n"


def test_current_resolves_symlink_to_profile(sandbox, capsys):
    p = sandbox.seed_profile("spare")
    sandbox.link_live(p)
    rc = A.main(["current"])
    assert rc == 0
    assert capsys.readouterr().out == "spare\n"


def test_current_reports_unmanaged_when_symlink_outside_accounts(sandbox, tmp_path, capsys):
    foreign = tmp_path / "foreign.json"
    foreign.write_text('{"k":"v"}')
    sandbox.link_live(foreign)
    rc = A.main(["current"])
    assert rc == 0
    assert capsys.readouterr().out == "unmanaged\n"


# ---------------------------------------------------------------------------
# switch
# ---------------------------------------------------------------------------


def test_switch_happy_path(sandbox, capsys):
    p_primary = sandbox.seed_profile("primary")
    p_spare = sandbox.seed_profile("spare")
    sandbox.link_live(p_primary)

    rc = A.main(["switch", "spare"])
    assert rc == 0

    assert sandbox.live_cred.is_symlink()
    assert sandbox.live_cred.resolve() == p_spare.resolve()
    assert "switched to spare" in capsys.readouterr().out


def test_switch_missing_target_refuses(sandbox, capsys):
    sandbox.seed_profile("primary")
    rc = A.main(["switch", "nonexistent"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "profile 'nonexistent' not found" in err
    # No symlink created.
    assert not sandbox.live_cred.exists() and not sandbox.live_cred.is_symlink()


def test_switch_idempotent(sandbox):
    """Switching to the already-active profile is a no-op (re-renders
    the same symlink target). Operator can re-run safely."""
    p = sandbox.seed_profile("spare")
    sandbox.link_live(p)
    rc = A.main(["switch", "spare"])
    assert rc == 0
    assert sandbox.live_cred.resolve() == p.resolve()


def test_switch_replaces_existing_link_atomically(sandbox):
    """``switch`` must use os.replace on a temp link so a partial
    write never leaves the live cred in a half-state. Indirect
    coverage: after switch, the link points at the new target with
    no stale temp file in the directory."""
    p_primary = sandbox.seed_profile("primary")
    p_spare = sandbox.seed_profile("spare")
    sandbox.link_live(p_primary)
    A.main(["switch", "spare"])
    assert sandbox.live_cred.resolve() == p_spare.resolve()
    leftovers = list(sandbox.live_cred.parent.glob(".cred-swap-*"))
    assert leftovers == [], f"temp files left behind: {leftovers}"


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


def test_add_from_path(sandbox, tmp_path, capsys):
    src = tmp_path / "external.json"
    src.write_text('{"oauth":"token"}')
    rc = A.main(["add", "newprof", "--from", str(src)])
    assert rc == 0

    dest = sandbox.accounts_dir / "newprof" / A.CRED_FILENAME
    assert dest.is_file()
    assert dest.read_text() == '{"oauth":"token"}'
    assert (dest.stat().st_mode & 0o777) == 0o600


def test_add_snapshot_from_live(sandbox):
    """No --from flag: ``add`` snapshots whatever ``LIVE_CRED`` points
    at (or is, if a regular file)."""
    p = sandbox.seed_profile("spare", body='{"oauth":"current-token"}')
    sandbox.link_live(p)

    rc = A.main(["add", "snapshot"])
    assert rc == 0

    snap = sandbox.accounts_dir / "snapshot" / A.CRED_FILENAME
    assert snap.read_text() == '{"oauth":"current-token"}'
    assert (snap.stat().st_mode & 0o777) == 0o600


def test_add_refuses_overwrite_without_force(sandbox, capsys):
    sandbox.seed_profile("primary", body='{"old":"v"}')
    src = sandbox.accounts_dir / "src.json"
    src.write_text('{"new":"v"}')
    rc = A.main(["add", "primary", "--from", str(src)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "already exists" in err and "--force" in err
    # Original profile unchanged.
    body = (sandbox.accounts_dir / "primary" / A.CRED_FILENAME).read_text()
    assert body == '{"old":"v"}'


def test_add_force_overwrites(sandbox):
    sandbox.seed_profile("primary", body='{"old":"v"}')
    src = sandbox.accounts_dir / "src.json"
    src.write_text('{"new":"v"}')
    rc = A.main(["add", "primary", "--from", str(src), "--force"])
    assert rc == 0
    body = (sandbox.accounts_dir / "primary" / A.CRED_FILENAME).read_text()
    assert body == '{"new":"v"}'


def test_add_missing_source_refuses(sandbox, tmp_path, capsys):
    rc = A.main(["add", "p", "--from", str(tmp_path / "doesnotexist")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "source not found" in err
    # No partial profile materialized.
    assert not (sandbox.accounts_dir / "p" / A.CRED_FILENAME).exists()


def test_add_no_temp_files_on_success(sandbox, tmp_path):
    src = tmp_path / "external.json"
    src.write_text('{"k":"v"}')
    A.main(["add", "newprof", "--from", str(src)])
    leftovers = list((sandbox.accounts_dir / "newprof").glob(".cred-add-*"))
    assert leftovers == []


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_unmanaged_no_profiles(sandbox, capsys):
    rc = A.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "current:        unmanaged" in out
    assert "profiles:       (none)" in out


def test_status_shows_current_and_modes(sandbox, capsys):
    sandbox.seed_profile("primary", mode=0o600)
    p_spare = sandbox.seed_profile("spare", mode=0o600)
    sandbox.link_live(p_spare)

    rc = A.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "current:        spare" in out
    assert "target exists:  True" in out
    assert "primary" in out and "spare" in out
    # No mode warnings when all are 0600.
    assert "WARN" not in out


def test_status_warns_on_drifted_mode(sandbox, capsys):
    sandbox.seed_profile("primary", mode=0o644)  # too permissive
    sandbox.seed_profile("spare", mode=0o600)
    rc = A.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    # Find the line for 'primary' — must carry the warning.
    primary_line = next(line for line in out.split("\n") if "primary" in line)
    assert "WARN" in primary_line and "0644" in primary_line
    spare_line = next(line for line in out.split("\n") if "spare" in line)
    assert "WARN" not in spare_line


def test_status_target_missing_when_link_dangles(sandbox, capsys):
    p = sandbox.seed_profile("spare")
    sandbox.link_live(p)
    p.unlink()  # break the link
    rc = A.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "target exists:  False" in out


# ---------------------------------------------------------------------------
# CLI surface — main() routing
# ---------------------------------------------------------------------------


def test_no_subcommand_prints_help(sandbox, capsys):
    rc = A.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "list" in out and "switch" in out


def test_unknown_subcommand_returns_nonzero(sandbox, capsys):
    with pytest.raises(SystemExit) as exc:
        A.main(["bogus"])
    # argparse exits 2 on unknown choice.
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# Flag-shape / unsafe-name rejection (library + CLI)
#
# argparse catches a bare ``--bogus`` as unknown option, but
# ``accounts add -- --bogus`` makes it a positional. Without the
# validator, ``_profile_path('--bogus')`` would create
# ``~/.metasphere/accounts/--bogus/credentials.json`` — a ghost
# directory whose name starts with ``-`` and shadows shell globs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "   ",
        "-x",
        "--bogus",
        "--from",
        ".",
        "..",
        "foo/bar",
        "foo\\bar",
        "foo\x00bar",
    ],
)
def test_validate_profile_name_rejects(bad_name):
    with pytest.raises(ValueError):
        A._validate_profile_name(bad_name)


@pytest.mark.parametrize("good_name", ["primary", "spare", "work-2", "a.b", "_x"])
def test_validate_profile_name_accepts(good_name):
    A._validate_profile_name(good_name)


def test_add_rejects_flag_shaped_name(sandbox, capsys):
    """``accounts add -- --bogus`` must hard-fail before any FS write,
    not silently create ``accounts/--bogus/credentials.json``."""
    rc = A.main(["add", "--", "--bogus"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid profile name" in err
    assert "'--bogus'" in err
    # No ghost dir landed.
    assert list(sandbox.accounts_dir.iterdir()) == []


def test_add_rejects_path_separator(sandbox, capsys):
    rc = A.main(["add", "foo/bar"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid profile name" in err
    assert list(sandbox.accounts_dir.iterdir()) == []


def test_add_rejects_dot_names(sandbox, capsys):
    rc = A.main(["add", ".."])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid profile name" in err
    assert list(sandbox.accounts_dir.iterdir()) == []


def test_switch_rejects_flag_shaped_name(sandbox, capsys):
    """``accounts switch -- --bogus`` must hard-fail with a clean
    error before touching the live-cred symlink."""
    sandbox.seed_profile("primary")
    rc = A.main(["switch", "--", "--bogus"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid profile name" in err
    # Live cred untouched (no link created).
    assert not sandbox.live_cred.exists() and not sandbox.live_cred.is_symlink()


def test_switch_rejects_path_separator(sandbox, capsys):
    rc = A.main(["switch", "foo/bar"])
    assert rc == 2
    assert "invalid profile name" in capsys.readouterr().err
