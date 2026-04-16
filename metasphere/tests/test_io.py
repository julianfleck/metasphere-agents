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


def test_frontmatter_iso8601_unquoted(tmp_path):
    """ISO-8601 timestamp values must round-trip without quotes (bash compat)."""
    p = tmp_path / "msg.md"
    fm = io.Frontmatter(meta={"created": "2026-04-07T20:43:23Z"}, body="hi\n")
    io.write_frontmatter_file(p, fm)
    raw = p.read_text()
    assert "created: 2026-04-07T20:43:23Z" in raw
    assert '"2026-04-07T20:43:23Z"' not in raw
    fm2 = io.read_frontmatter_file(p)
    assert fm2.meta["created"] == "2026-04-07T20:43:23Z"


def test_format_scalar_quotes_at_sigil():
    """Bare ``@agent`` must be quoted so strict YAML loaders don't treat it
    as a tag/alias. Regression: unquoted ``assigned_to: @orchestrator``
    values broke the render pipeline downstream."""
    assert io._format_scalar("@orchestrator") == '"@orchestrator"'
    assert io._format_scalar("@agent_with_underscore") == '"@agent_with_underscore"'


def test_format_scalar_quotes_bang_sigil():
    """Bare ``!label`` / ``!priority`` must also be quoted."""
    assert io._format_scalar("!high") == '"!high"'
    assert io._format_scalar("!task") == '"!task"'


def test_format_scalar_plain_strings_still_bare():
    """Regression guard: only sigil-led strings get quoted."""
    assert io._format_scalar("hello") == "hello"
    assert io._format_scalar("pending") == "pending"


def test_frontmatter_sigil_roundtrip(tmp_path):
    """Write a task-like frontmatter with @-agent and !-priority and verify
    that (a) the on-disk file has the values quoted and (b) the parser
    returns the original strings."""
    p = tmp_path / "task.md"
    fm = io.Frontmatter(
        meta={
            "id": "t1",
            "assigned_to": "@orchestrator",
            "created_by": "@alice",
            "priority": "!high",
            "status": "pending",
        },
        body="body\n",
    )
    io.write_frontmatter_file(p, fm)
    raw = p.read_text()
    assert 'assigned_to: "@orchestrator"' in raw
    assert 'created_by: "@alice"' in raw
    assert 'priority: "!high"' in raw
    fm2 = io.read_frontmatter_file(p)
    assert fm2.meta["assigned_to"] == "@orchestrator"
    assert fm2.meta["created_by"] == "@alice"
    assert fm2.meta["priority"] == "!high"
    assert fm2.meta["status"] == "pending"


# --- Escape-doubling regression (P0 2026-04-16) ----------------------------
#
# A task titled "Sunset /projects/writing-openclaw/ — Julian directive.\n
# FACTS:\n..." grew to 2.6MB of backslashes after ~30 consolidate ticks.
# Root cause: _format_scalar used json.dumps to write strings with
# "needs quoting" chars, but _parse_scalar stripped the outer quotes
# WITHOUT running json.loads — so each write-read-write cycle doubled
# every backslash. The fix makes _parse_scalar symmetric: json.loads
# on double-quoted values, with a bare-strip fallback for JSON-invalid
# legacy content.


def test_frontmatter_roundtrip_stable_with_embedded_newline(tmp_path):
    """A title with an embedded \\n round-trips byte-stable after
    N iterations — no escape-doubling. Allows one body-newline
    normalization on the first tick, then requires strict stability.
    """
    p = tmp_path / "task.md"
    title = "Sunset project — multi-line.\nFACTS:\n- one\n- two"
    fm = io.Frontmatter(meta={"id": "t1", "title": title}, body="body\n")

    sizes: list[int] = []
    for _ in range(10):
        io.write_frontmatter_file(p, fm)
        text = p.read_text()
        sizes.append(len(text))
        fm = io.parse_frontmatter(text)

    # From tick 1 onward the file size MUST be stable — that is the
    # escape-doubling invariant. (Tick 0 may differ by a one-shot body
    # newline normalization that's orthogonal to this bug.)
    assert len(set(sizes[1:])) == 1, (
        f"frontmatter size unstable from tick 1: {sizes} "
        f"(escape-doubling regression)"
    )
    # And the title still equals the original after 10 round-trips.
    assert fm.meta["title"] == title


def test_frontmatter_roundtrip_stable_with_backslash_and_unicode(tmp_path):
    """Belt-and-suspenders: literal backslashes, quotes, and non-ASCII
    chars all round-trip byte-stable.
    """
    p = tmp_path / "task.md"
    title = r'Windows path C:\Users\foo with "quotes" and em — dash and \u2014'
    fm = io.Frontmatter(meta={"id": "t1", "title": title}, body="body\n")

    sizes: list[int] = []
    for _ in range(6):
        io.write_frontmatter_file(p, fm)
        text = p.read_text()
        sizes.append(len(text))
        fm = io.parse_frontmatter(text)

    # After the first write the size may or may not match the literal
    # source, but all subsequent writes must be stable (no doubling).
    assert len(set(sizes[1:])) == 1, f"unstable round-trip: {sizes}"
    assert fm.meta["title"] == title


def test_frontmatter_bare_backslash_legacy_fallback(tmp_path):
    """Double-quoted values that are json-INVALID (e.g. a stray bare
    backslash from a bash-writer predecessor) must not raise — they
    fall back to the old strip-quotes behavior so we don't regress on
    existing on-disk content."""
    p = tmp_path / "task.md"
    # This is intentionally json-invalid: a lone backslash inside
    # double quotes is an illegal escape sequence.
    p.write_text('---\nid: t1\ntitle: "hello\\world"\n---\nbody\n')
    fm = io.read_frontmatter_file(p)
    # Fallback behavior: outer quotes stripped, content preserved.
    assert fm.meta["title"] == r"hello\world"
