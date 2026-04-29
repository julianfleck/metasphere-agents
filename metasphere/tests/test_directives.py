"""Tests for metasphere.directives."""

from __future__ import annotations

import pytest

from metasphere import directives as _dir


# ---------- parse ----------

def test_parse_empty():
    assert _dir.parse_directives("") == []
    assert _dir.parse_directives("   ") == []


def test_parse_single_directive():
    content = """\
---
date: 2026-04-12
source: @user
expires: 2026-04-19
text: Do NOT work on model diversity.
"""
    result = _dir.parse_directives(content)
    assert len(result) == 1
    d = result[0]
    assert d.date == "2026-04-12"
    assert d.source == "@user"
    assert d.expires == "2026-04-19"
    assert d.text == "Do NOT work on model diversity."


def test_parse_multiple_directives():
    content = """\
---
date: 2026-04-10
source: @user
text: Focus on consolidate fixes.
---
date: 2026-04-11
source: @orchestrator
text: All agents prefer Python CLI over bash scripts.
---
date: 2026-04-12
source: @user
text: No model diversity work.
"""
    result = _dir.parse_directives(content)
    assert len(result) == 3
    assert result[0].date == "2026-04-10"
    assert result[1].date == "2026-04-11"
    assert result[2].date == "2026-04-12"


def test_parse_multiline_text():
    content = """\
---
date: 2026-04-12
source: @user
text: First line of the directive.
  Second line continues here.
  Third line as well.
"""
    result = _dir.parse_directives(content)
    assert len(result) == 1
    assert "First line" in result[0].text
    assert "Second line" in result[0].text
    assert "Third line" in result[0].text


def test_parse_no_expires():
    content = """\
---
date: 2026-04-12
source: @user
text: Permanent directive.
"""
    result = _dir.parse_directives(content)
    assert len(result) == 1
    assert result[0].expires == ""


# ---------- expiry ----------

def test_is_expired_no_expiry():
    d = _dir.Directive(date="2026-04-12", text="test")
    assert not _dir._is_expired(d, today="2026-04-15")


def test_is_expired_future():
    d = _dir.Directive(date="2026-04-12", expires="2026-04-19", text="test")
    assert not _dir._is_expired(d, today="2026-04-15")


def test_is_expired_past():
    d = _dir.Directive(date="2026-04-12", expires="2026-04-13", text="test")
    assert _dir._is_expired(d, today="2026-04-15")


# ---------- load ----------

def test_load_no_file(tmp_paths):
    result = _dir.load_directives(tmp_paths)
    assert result == []


def test_load_filters_expired(tmp_paths):
    content = """\
---
date: 2026-04-10
source: @user
expires: 2026-04-11
text: This expired.
---
date: 2026-04-12
source: @user
text: This is active.
"""
    (tmp_paths.project_root / "DIRECTIVES.yaml").write_text(content)
    result = _dir.load_directives(tmp_paths, today="2026-04-15")
    assert len(result) == 1
    assert result[0].text == "This is active."


def test_load_respects_max_n(tmp_paths):
    blocks = []
    for i in range(20):
        blocks.append(f"---\ndate: 2026-04-{i+1:02d}\nsource: @test\ntext: Directive {i}")
    (tmp_paths.project_root / "DIRECTIVES.yaml").write_text("\n".join(blocks))
    result = _dir.load_directives(tmp_paths, max_n=5)
    assert len(result) == 5
    assert result[-1].text == "Directive 19"


# ---------- add ----------

def test_add_creates_file(tmp_paths):
    d = _dir.add_directive(tmp_paths, "Test directive", source="@user")
    assert d.source == "@user"
    assert d.text == "Test directive"
    fpath = tmp_paths.project_root / "DIRECTIVES.yaml"
    assert fpath.exists()
    reloaded = _dir.load_directives(tmp_paths)
    assert len(reloaded) == 1
    assert reloaded[0].text == "Test directive"


def test_add_appends(tmp_paths):
    _dir.add_directive(tmp_paths, "First", source="@user")
    _dir.add_directive(tmp_paths, "Second", source="@orchestrator")
    reloaded = _dir.load_directives(tmp_paths)
    assert len(reloaded) == 2
    assert reloaded[0].text == "First"
    assert reloaded[1].text == "Second"


# ---------- render ----------

def test_render_empty(tmp_paths):
    assert _dir.render_directives(tmp_paths) == ""


def test_render_formats_correctly(tmp_paths):
    _dir.add_directive(tmp_paths, "No model diversity", source="@user")
    output = _dir.render_directives(tmp_paths)
    assert "## Directives (broadcast)" in output
    assert "@user" in output
    assert "No model diversity" in output
