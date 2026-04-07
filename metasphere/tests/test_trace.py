import json
from pathlib import Path

from metasphere.trace import (
    capture_trace,
    list_traces,
    prune_traces,
    search_traces,
)


def test_capture_success(tmp_paths):
    t = capture_trace(["echo", "hello"], paths=tmp_paths)
    assert t.exit_code == 0
    assert not t.error_detected
    assert Path(t.stdout_file).read_text().strip() == "hello"
    index = tmp_paths.root / "traces" / "index.jsonl"
    assert index.exists()
    line = index.read_text().splitlines()[-1]
    assert json.loads(line)["id"] == t.id


def test_capture_failure_marks_error(tmp_paths):
    t = capture_trace("exit 3", paths=tmp_paths, shell=True)
    assert t.exit_code == 3
    assert t.error_detected
    assert t.error_type == "exit_code"


def test_capture_stderr_pattern(tmp_paths):
    t = capture_trace("echo 'fatal: boom' >&2", paths=tmp_paths, shell=True)
    assert t.error_detected
    assert "fatal" in t.error_summary.lower()


def test_list_and_search(tmp_paths):
    capture_trace(["echo", "alpha"], paths=tmp_paths)
    capture_trace(["echo", "beta"], paths=tmp_paths)
    rows = list_traces(paths=tmp_paths)
    assert len(rows) == 2
    hits = search_traces("alpha", paths=tmp_paths)
    assert len(hits) == 1
    assert "alpha" in hits[0].command


def test_prune(tmp_paths):
    capture_trace(["echo", "x"], paths=tmp_paths)
    # nothing to prune yet (today's dir)
    assert prune_traces(0, paths=tmp_paths) >= 0
    # fabricate an old dir
    old = tmp_paths.root / "traces" / "1999-01-01"
    old.mkdir(parents=True)
    (old / "junk.stdout").write_text("x")
    removed = prune_traces(1, paths=tmp_paths)
    assert removed >= 1
    assert not old.exists()


def test_capture_string_no_shell_expansion(tmp_paths, monkeypatch):
    monkeypatch.delenv("UNSET_VAR", raising=False)
    t = capture_trace("echo $UNSET_VAR done", paths=tmp_paths)
    assert t.exit_code == 0
    out = Path(t.stdout_file).read_text().strip()
    # Without shell, $UNSET_VAR is passed literally, not expanded.
    assert out == "$UNSET_VAR done"
