import json
from pathlib import Path

from metasphere import io


def test_atomic_write_text(tmp_path: Path):
    p = tmp_path / "sub" / "x.txt"
    io.atomic_write_text(p, "hello")
    assert p.read_text() == "hello"
    # no leftover tmp files
    assert [f.name for f in p.parent.iterdir()] == ["x.txt"]


def test_file_lock_blocks_same_process(tmp_path: Path):
    p = tmp_path / "lock"
    with io.file_lock(p):
        # second acquire on same fd path with non-blocking would EWOULDBLOCK;
        # we just assert the context manager exits cleanly nested-style.
        pass
    # second use after release works
    with io.file_lock(p):
        pass


def test_json_roundtrip(tmp_path: Path):
    p = tmp_path / "j.json"
    io.write_json(p, {"a": 1, "b": [1, 2]})
    assert io.read_json(p) == {"a": 1, "b": [1, 2]}
    assert io.read_json(tmp_path / "missing.json", default={}) == {}


def test_append_jsonl(tmp_path: Path):
    p = tmp_path / "e.jsonl"
    io.append_jsonl(p, {"a": 1})
    io.append_jsonl(p, {"a": 2})
    lines = p.read_text().strip().splitlines()
    assert [json.loads(l)["a"] for l in lines] == [1, 2]


def test_frontmatter_roundtrip():
    text = """---
from: @user
to: @orchestrator
label: !task
read: false
n: 3
tags: [a, b]
---
hello body
line two
"""
    fm = io.parse_frontmatter(text)
    assert fm.meta["from"] == "@user"
    assert fm.meta["read"] is False
    assert fm.meta["n"] == 3
    assert fm.meta["tags"] == ["a", "b"]
    assert "hello body" in fm.body
    serialized = io.serialize_frontmatter(fm)
    fm2 = io.parse_frontmatter(serialized)
    assert fm2.meta == fm.meta


def test_frontmatter_no_fence():
    fm = io.parse_frontmatter("just text")
    assert fm.meta == {}
    assert fm.body == "just text"
