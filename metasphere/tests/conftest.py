import os
import pytest
from pathlib import Path
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# Test-pollution guard
#
# Session-scoped detection of tests that wrote into the real
# ``~/.metasphere/`` tree. Mechanism: snapshot the file set at session
# start; at session end, fail if any file appeared whose first bytes
# match the ``BYTES:`` signature used by ``fake_http_get`` fixtures in
# ``test_telegram.py``. Signature-based (not mtime-based) so live
# daemons — gateway, heartbeat, schedule — writing to real dirs during
# a test run don't produce false positives.
#
# This exists because PR #3's test suite leaked
# ``~/.metasphere/attachments/555/biggest.bin`` into the production
# home directory. The per-module autouse fixture in test_telegram.py
# prevents that specific leak proactively; this conftest check
# detects any future recurrence reactively, whatever test introduces it.
# ---------------------------------------------------------------------------

_REAL_METASPHERE = Path.home() / ".metasphere"
_TEST_SIGNATURE = b"BYTES:"


def pytest_sessionstart(session):
    if not _REAL_METASPHERE.exists():
        session._metasphere_file_snapshot = set()
        return
    try:
        session._metasphere_file_snapshot = {
            str(p) for p in _REAL_METASPHERE.rglob("*") if p.is_file()
        }
    except OSError:
        session._metasphere_file_snapshot = set()


def pytest_sessionfinish(session, exitstatus):
    snapshot = getattr(session, "_metasphere_file_snapshot", None)
    if snapshot is None or not _REAL_METASPHERE.exists():
        return
    leaked: list[str] = []
    try:
        for p in _REAL_METASPHERE.rglob("*"):
            if not p.is_file():
                continue
            s = str(p)
            if s in snapshot:
                continue
            try:
                head = p.read_bytes()[: len(_TEST_SIGNATURE)]
            except OSError:
                continue
            if head == _TEST_SIGNATURE:
                leaked.append(s)
    except OSError:
        return
    if leaked:
        pytest.exit(
            "TEST POLLUTION: files with fake_http_get signature "
            "(b'BYTES:') leaked into real ~/.metasphere/:\n"
            + "\n".join(f"  {p}" for p in leaked),
            returncode=1,
        )


@pytest.fixture
def tmp_paths(tmp_path: Path, monkeypatch) -> Paths:
    root = tmp_path / "metasphere"
    repo = tmp_path / "repo"
    scope = tmp_path / "repo"
    for p in (root, repo, scope):
        p.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("METASPHERE_DIR", str(root))
    monkeypatch.setenv("METASPHERE_PROJECT_ROOT", str(repo))
    monkeypatch.setenv("METASPHERE_SCOPE", str(scope))
    monkeypatch.delenv("METASPHERE_AGENT_ID", raising=False)
    return Paths(root=root, project_root=repo, scope=scope)
