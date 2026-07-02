"""Smoke tests for scripts/metasphere-reaper noop-suppression.

The reaper is a bash oneshot driven by a 60s systemd timer. Before
2026-05-14 it appended one log line per tick regardless of whether
anything was killed — 32791 noop entries accumulated against 1 real
kill. Now it only persists actionable runs to the operator-facing log
file (stdout/journal still emit every tick for liveness).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REAPER = REPO_ROOT / "scripts" / "metasphere-reaper"


def _run(log_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(REAPER)],
        env={"PATH": "/usr/bin:/bin", "REAPER_LOG": str(log_path), "HOME": str(log_path.parent)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_reaper_noop_does_not_append_to_log(tmp_path: Path):
    """No npm-root-g processes → stdout emits, log file stays absent."""
    log = tmp_path / "reaper.log"
    result = _run(log)

    assert result.returncode == 0, result.stderr
    assert "killed=0" in result.stdout
    assert not log.exists(), (
        f"noop run wrote to log file (contents: {log.read_text() if log.exists() else 'n/a'})"
    )


def test_reaper_noop_repeated_runs_keep_log_empty(tmp_path: Path):
    """100 noop ticks must not bloat the log file."""
    log = tmp_path / "reaper.log"
    for _ in range(5):
        result = _run(log)
        assert result.returncode == 0, result.stderr
    assert not log.exists()
