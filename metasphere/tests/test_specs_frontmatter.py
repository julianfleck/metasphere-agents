"""Tests for ``_parse_frontmatter`` — scalar + inline-list parsing.

Inline-list parsing was added so MISSION.md can declare
``projects: [a, b]`` for multi-project agents. The parser stays
dependency-free (no PyYAML).
"""

from __future__ import annotations

from metasphere.specs import _parse_frontmatter


def _wrap(body: str) -> str:
    return "---\n" + body + "---\n"


def test_scalar_values_preserved():
    fm = _parse_frontmatter(_wrap("name: alpha\nrole: lead\n"))
    assert fm == {"name": "alpha", "role": "lead"}


def test_inline_list_parsed_to_list():
    fm = _parse_frontmatter(_wrap("projects: [rho, rho-server]\n"))
    assert fm == {"projects": ["rho", "rho-server"]}


def test_inline_list_strips_whitespace_per_element():
    fm = _parse_frontmatter(_wrap("projects: [ a ,  b ,c]\n"))
    assert fm == {"projects": ["a", "b", "c"]}


def test_inline_list_drops_empty_entries():
    fm = _parse_frontmatter(_wrap("projects: [a, , b,]\n"))
    assert fm == {"projects": ["a", "b"]}


def test_inline_list_quoted_elements_unquoted():
    fm = _parse_frontmatter(_wrap("projects: ['a', \"b\"]\n"))
    assert fm == {"projects": ["a", "b"]}


def test_empty_inline_list_yields_empty_list():
    fm = _parse_frontmatter(_wrap("projects: []\n"))
    assert fm == {"projects": []}


def test_mixed_scalar_and_list():
    fm = _parse_frontmatter(_wrap(
        "name: alpha\nproject: solo\nprojects: [a, b]\n"
    ))
    assert fm == {
        "name": "alpha",
        "project": "solo",
        "projects": ["a", "b"],
    }


def test_no_frontmatter_returns_empty():
    assert _parse_frontmatter("no leading delimiter\n") == {}
    assert _parse_frontmatter("") == {}


def test_unterminated_frontmatter_parses_until_eof():
    # Missing closing ``---`` — parser walks to EOF rather than crashing.
    fm = _parse_frontmatter("---\nname: alpha\nrole: lead\n")
    assert fm == {"name": "alpha", "role": "lead"}


def test_malformed_line_without_colon_skipped():
    fm = _parse_frontmatter(_wrap("name: alpha\nthis is junk\nrole: lead\n"))
    assert fm == {"name": "alpha", "role": "lead"}


def test_list_with_only_whitespace_yields_empty_list():
    fm = _parse_frontmatter(_wrap("projects: [   ]\n"))
    assert fm == {"projects": []}
