"""Tests for ``metasphere audit-docs``.

The audit shells out to ``git log`` — tests use real ``git init`` in
tmp_path so the git integration is exercised end-to-end without
network or real-home dependencies. The ``_notify_orchestrator`` hook
is stubbed so no messages leak into the sandboxed ``.messages/``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from metasphere.cli import audit_docs as A


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    )


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("# Seed\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "seed: initial")
    return path


def _register(tmp_paths, name: str, repo: Path):
    import json as _json
    reg_file = tmp_paths.root / "projects.json"
    reg = _json.loads(reg_file.read_text())
    reg.append({
        "name": name, "path": str(repo),
        "registered": "1970-01-01T00:00:00Z",
    })
    reg_file.write_text(_json.dumps(reg))
    pdir = tmp_paths.projects / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "project.json").write_text(_json.dumps({
        "schema": 2, "name": name, "path": str(repo),
        "created": "1970-01-01T00:00:00Z", "status": "active",
    }))


def test_changelog_newest_date_iso_bracket(tmp_path):
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(
        "# Changelog\n\n"
        "## [2026-04-15T12:00:00Z] — latest\n\n"
        "## [2026-04-10T00:00:00Z] — earlier\n"
    )
    assert A._changelog_newest_date(cl) == "2026-04-15"


def test_changelog_newest_date_bare_iso(tmp_path):
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text("## 2026-04-15 — something\n\n## 2026-04-10 — older\n")
    assert A._changelog_newest_date(cl) == "2026-04-15"


def test_changelog_newest_date_missing(tmp_path):
    assert A._changelog_newest_date(tmp_path / "nope.md") is None


def test_git_log_since_parses_subject_and_files(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    (repo / "src.py").write_text("x = 1\n")
    _git(repo, "add", "src.py")
    _git(repo, "commit", "-q", "-m", "feat(x): add src")
    records = A._git_log_since(repo, "2020-01-01")
    assert len(records) == 2  # seed + feat
    subjects = [r["subject"] for r in records]
    assert "feat(x): add src" in subjects


def test_classify_subject():
    assert A._classify_subject("feat(tasks): x") == "feat"
    assert A._classify_subject("fix: thing") == "fix"
    assert A._classify_subject("refactor(project): y") == "refactor"
    assert A._classify_subject("docs(readme): z") == "docs"
    assert A._classify_subject("random message") == "other"


def test_staleness_flags_keyword_match():
    records = [
        {"sha": "abc1234", "subject": "feat(cli): new subcommand", "files": []},
        {"sha": "def5678", "subject": "fix: typo", "files": []},
    ]
    flags = A._staleness_flags(records)
    assert len(flags) == 1
    assert "abc1234" in flags[0]
    assert "subcommand" in flags[0] or "cli" in flags[0]


def test_staleness_flags_path_match():
    records = [{
        "sha": "aaa0000", "subject": "chore: cleanup",
        "files": ["metasphere/cli/newthing.py"],
    }]
    flags = A._staleness_flags(records)
    assert len(flags) == 1
    assert "metasphere/cli/" in flags[0]


def test_run_audit_unknown_project(tmp_paths, capsys):
    rc, path = A._run_audit("nonexistent", paths=tmp_paths, notify=False)
    assert rc == 2
    assert path == Path()
    assert "unknown project" in capsys.readouterr().err


def test_run_audit_produces_report_with_no_staleness(tmp_path, tmp_paths):
    repo = _make_repo(tmp_path / "proj-a")
    (repo / "CHANGELOG.md").write_text("## 2020-01-01 — ancient\n")
    _register(tmp_paths, "proj-a", repo)

    rc, path = A._run_audit(
        "proj-a", paths=tmp_paths,
        output_dir=tmp_path / "audits", notify=False,
    )
    assert rc == 0  # no staleness flags (seed commit is `seed: initial`)
    assert path.is_file()
    assert "proj-a" in path.read_text()


def test_run_audit_flags_staleness_and_returns_1(tmp_path, tmp_paths):
    repo = _make_repo(tmp_path / "proj-b")
    (repo / "CHANGELOG.md").write_text("## 2020-01-01 — ancient\n")
    (repo / "cli.py").write_text("x = 1\n")
    _git(repo, "add", "cli.py")
    _git(repo, "commit", "-q", "-m", "feat(cli): add new subcommand")
    _register(tmp_paths, "proj-b", repo)

    captured_msgs = []

    def fake_sender(target, label, body, from_agent, **kw):
        captured_msgs.append({"target": target, "label": label, "body": body})

    # Patch the notifier's sender.
    rc, path = A._run_audit(
        "proj-b", paths=tmp_paths,
        output_dir=tmp_path / "audits", notify=False,
    )
    assert rc == 1  # staleness flag raised
    report = path.read_text()
    assert "README staleness flags" in report
    assert "add new subcommand" in report


def test_cli_entry_writes_report(tmp_path, tmp_paths, capsys, monkeypatch):
    repo = _make_repo(tmp_path / "proj-c")
    (repo / "CHANGELOG.md").write_text("## 2020-01-01 — ancient\n")
    _register(tmp_paths, "proj-c", repo)

    rc = A.main(["--project", "proj-c",
                 "--output", str(tmp_path / "out"),
                 "--no-notify"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "report" in out
    assert "proj-c.md" in out


# --- register-cron subcommand ---------------------------------------------


def test_register_cron_adds_one_job_per_project(tmp_path, tmp_paths):
    """Given three registered projects, register-cron adds three
    audit-docs jobs with the canonical 0 18 * * * expression.
    """
    import json as _json
    from metasphere import schedule as _schedule

    # tmp_paths conftest already seeded "testproj"; add two more.
    for name in ("alpha", "beta"):
        _register(tmp_paths, name, _make_repo(tmp_path / name))

    added = A._register_cron(tmp_paths, metasphere_bin="/fake/bin/metasphere")
    # testproj + alpha + beta = 3
    assert set(added) == {"audit-docs:testproj", "audit-docs:alpha", "audit-docs:beta"}

    jobs = _schedule.load_jobs(tmp_paths)
    audit_jobs = [j for j in jobs if j.id.startswith("audit-docs:")]
    assert len(audit_jobs) == 3
    for j in audit_jobs:
        assert j.cron_expr == "0 18 * * *"
        assert j.enabled is True
        assert j.kind == "cron"
        assert j.payload_kind == "command"
        assert "audit-docs --project" in j.command
        assert j.agent_id == "audit-docs"


def test_register_cron_idempotent(tmp_paths, tmp_path):
    """Running register-cron twice doesn't duplicate entries."""
    from metasphere import schedule as _schedule
    A._register_cron(tmp_paths, metasphere_bin="/x/m")
    first = len(_schedule.load_jobs(tmp_paths))
    added = A._register_cron(tmp_paths, metasphere_bin="/x/m")
    assert added == []
    second = len(_schedule.load_jobs(tmp_paths))
    assert first == second


def test_register_cron_filter_by_project(tmp_paths, tmp_path):
    for name in ("alpha", "beta"):
        _register(tmp_paths, name, _make_repo(tmp_path / name))
    added = A._register_cron(
        tmp_paths, only_project="alpha",
        metasphere_bin="/x/m",
    )
    assert added == ["audit-docs:alpha"]


def test_register_cron_unknown_project_raises(tmp_paths):
    with pytest.raises(ValueError):
        A._register_cron(tmp_paths, only_project="no-such-thing")


def test_register_cron_dry_run_does_not_write(tmp_paths, tmp_path):
    from metasphere import schedule as _schedule
    for name in ("alpha", "beta"):
        _register(tmp_paths, name, _make_repo(tmp_path / name))
    added = A._register_cron(tmp_paths, dry_run=True, metasphere_bin="/x/m")
    assert set(added) == {"audit-docs:testproj", "audit-docs:alpha", "audit-docs:beta"}
    # Nothing persisted.
    jobs = _schedule.load_jobs(tmp_paths)
    assert not any(j.id.startswith("audit-docs:") for j in jobs)


def test_register_cron_cli_entry(tmp_paths, tmp_path, capsys):
    rc = A.main(["register-cron", "--metasphere-bin", "/x/m", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "would add" in out
    assert "audit-docs:testproj" in out


def test_main_rejects_invocation_without_project_or_subcommand(capsys):
    with pytest.raises(SystemExit):
        A.main([])


# --- P3: same-day --since handling --------------------------------------


def test_normalize_since_bare_date_pins_utc_midnight():
    """Bare ``YYYY-MM-DD`` must be pinned to UTC midnight so git's
    local-TZ interpretation can't push same-day UTC commits out.
    """
    assert A._normalize_since("2026-04-15") == "2026-04-15 00:00:00 +0000"


def test_normalize_since_non_date_passes_through():
    """Non-date strings are git-relative (``2 days ago``) or explicit
    timestamps; leave those unchanged.
    """
    assert A._normalize_since("2 days ago") == "2 days ago"
    assert (A._normalize_since("2026-04-15T12:00:00Z")
            == "2026-04-15T12:00:00Z")


def test_audit_includes_same_day_commits(tmp_path, tmp_paths):
    """Regression for the 2026-04-15 evening symptom: an audit run ON
    2026-04-15 with ``since=2026-04-15`` must include commits made
    earlier that same day. Previously, git's local-TZ interpretation
    could push the cutoff past UTC same-day commits, returning 'no
    new commits' despite prior merges that day.
    """
    import datetime as _dt
    import time as _time
    repo = _make_repo(tmp_path / "proj-sameday")
    _register(tmp_paths, "proj-sameday", repo)

    # Commit with today's UTC date, pinned via GIT_*_DATE env so the
    # test is reproducible across test-host TZs.
    today_iso = _dt.date.today().isoformat()
    env = {
        "GIT_AUTHOR_DATE":    f"{today_iso}T08:00:00+0000",
        "GIT_COMMITTER_DATE": f"{today_iso}T08:00:00+0000",
    }
    (repo / "new.py").write_text("x = 1\n")
    import subprocess as _sp
    _sp.run(["git", "-C", str(repo), "add", "new.py"], check=True)
    _sp.run(
        ["git", "-C", str(repo), "commit", "-q", "-m",
         "feat(cli): same-day commit for audit test"],
        check=True, env={**__import__("os").environ, **env},
    )

    # Audit with since=today should include the same-day commit.
    rc, path = A._run_audit(
        "proj-sameday", paths=tmp_paths,
        output_dir=tmp_path / "audits",
        notify=False, since_override=today_iso,
    )
    # rc is 1 (staleness flag on the cli: subject) rather than 0 means
    # the commit was seen — that's the assertion we care about.
    report = path.read_text()
    assert "same-day commit for audit test" in report, (
        f"same-day commit missed — report:\n{report}"
    )


def test_cli_since_flag_passed_through(tmp_path, tmp_paths, capsys):
    """``metasphere audit-docs --project X --since 2026-04-15`` routes
    the flag through to ``_run_audit``. Pin a synthetic old commit
    and a new commit; audit with ``--since=<old>`` → report mentions
    both.
    """
    import datetime as _dt
    import subprocess as _sp, os as _os
    repo = _make_repo(tmp_path / "proj-flag")
    _register(tmp_paths, "proj-flag", repo)

    today_iso = _dt.date.today().isoformat()
    # A commit dated 2 days ago.
    two_days_ago = (_dt.date.today() - _dt.timedelta(days=2)).isoformat()
    env = {
        **_os.environ,
        "GIT_AUTHOR_DATE":    f"{two_days_ago}T12:00:00+0000",
        "GIT_COMMITTER_DATE": f"{two_days_ago}T12:00:00+0000",
    }
    (repo / "older.py").write_text("y = 2\n")
    _sp.run(["git", "-C", str(repo), "add", "older.py"], check=True)
    _sp.run(
        ["git", "-C", str(repo), "commit", "-q", "-m",
         "feat: older commit"],
        check=True, env=env,
    )

    rc = A.main([
        "--project", "proj-flag",
        "--since", two_days_ago,
        "--output", str(tmp_path / "out"),
        "--no-notify",
    ])
    assert rc in (0, 1)
    # Locate the written report by pattern.
    outs = list((tmp_path / "out").rglob("proj-flag.md"))
    assert outs, "report not written"
    content = outs[0].read_text()
    assert "older commit" in content
