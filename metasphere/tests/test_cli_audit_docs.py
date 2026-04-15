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
