import os
import pytest
from pathlib import Path
from metasphere.paths import Paths


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
