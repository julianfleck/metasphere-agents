"""Tests for Stage A project-mention detection (metasphere/memory/mention.py)."""

import json

from metasphere.memory.mention import detect_mentioned_projects


def _register(paths, *names_or_entries):
    """Overwrite the registry with the given projects.

    Accepts bare name strings or full dict entries (to set ``aliases``).
    """
    entries = []
    for item in names_or_entries:
        if isinstance(item, str):
            entries.append({"name": item, "path": f"/repos/{item}",
                            "registered": "1970-01-01T00:00:00Z"})
        else:
            entries.append(item)
    (paths.root / "projects.json").write_text(json.dumps(entries))
    return entries


def _names(projects):
    return [p.name for p in projects]


def test_empty_prompt_returns_nothing(tmp_paths):
    _register(tmp_paths, "widget")
    assert detect_mentioned_projects("", tmp_paths) == []
    assert detect_mentioned_projects("   \n ", tmp_paths) == []


def test_exact_name_match(tmp_paths):
    _register(tmp_paths, "widget", "mesa-chat")
    out = detect_mentioned_projects("what's the status of widget today?", tmp_paths)
    assert _names(out) == ["widget"]


def test_case_insensitive(tmp_paths):
    _register(tmp_paths, "widget")
    out = detect_mentioned_projects("Check WIDGET please", tmp_paths)
    assert _names(out) == ["widget"]


def test_separator_variants_match(tmp_paths):
    """mesa-chat should match mesa.chat / mesa chat / mesachat."""
    _register(tmp_paths, "mesa-chat")
    for phrase in ("look at mesa.chat", "the mesa chat billing", "mesachat bug"):
        out = detect_mentioned_projects(phrase, tmp_paths)
        assert _names(out) == ["mesa-chat"], phrase


def test_word_boundary_no_substring_false_positive(tmp_paths):
    """A project name embedded in a larger word must not match."""
    _register(tmp_paths, "cam")
    # "cam" inside "camera"/"scammer" must not trigger.
    assert detect_mentioned_projects("the camera scammer", tmp_paths) == []
    assert _names(detect_mentioned_projects("check cam now", tmp_paths)) == ["cam"]


def test_longest_match_wins_over_sibling(tmp_paths):
    """memU-experiment must not also trip the shorter sibling memU."""
    _register(tmp_paths, "memU", "memU-experiment")
    out = detect_mentioned_projects("results from memU-experiment", tmp_paths)
    assert _names(out) == ["memU-experiment"]


def test_short_name_below_floor_is_ignored(tmp_paths):
    """A 2-char project name is below the noise floor and never matches."""
    _register(tmp_paths, "ab", "widget")
    out = detect_mentioned_projects("ab is a widget thing", tmp_paths)
    assert _names(out) == ["widget"]


def test_registry_entry_alias(tmp_paths):
    _register(tmp_paths, {
        "name": "pod-project", "path": "/repos/pod",
        "aliases": ["PD", "Pod Devices"],
        "registered": "1970-01-01T00:00:00Z",
    })
    # "PD" is 2 chars → below floor, ignored; the longer alias still matches.
    out = detect_mentioned_projects("update on Pod Devices", tmp_paths)
    assert _names(out) == ["pod-project"]


def test_central_alias_yaml(tmp_paths):
    _register(tmp_paths, "widget")
    (tmp_paths.root / "project-aliases.yaml").write_text("widget:\n  - ww-prod\n")
    out = detect_mentioned_projects("the ww-prod cutover", tmp_paths)
    assert _names(out) == ["widget"]


def test_multiple_projects_preserve_registry_order(tmp_paths):
    _register(tmp_paths, "alpha-svc", "beta-svc", "gamma-svc")
    # Mention them out of order; result follows registry order.
    out = detect_mentioned_projects("gamma-svc then alpha-svc", tmp_paths)
    assert _names(out) == ["alpha-svc", "gamma-svc"]


def test_limit_caps_results(tmp_paths):
    _register(tmp_paths, "alpha-svc", "beta-svc", "gamma-svc", "delta-svc")
    out = detect_mentioned_projects(
        "alpha-svc beta-svc gamma-svc delta-svc", tmp_paths, limit=2)
    assert len(out) == 2


def test_no_match_returns_empty(tmp_paths):
    _register(tmp_paths, "widget")
    assert detect_mentioned_projects("nothing relevant here", tmp_paths) == []


def test_malformed_registry_degrades(tmp_paths):
    (tmp_paths.root / "projects.json").write_text("{not valid json")
    # Must not raise — returns empty.
    assert detect_mentioned_projects("widget", tmp_paths) == []


def test_global_sentinel_row_skipped(tmp_paths):
    _register(tmp_paths,
              {"name": "", "path": "", "registered": "1970-01-01T00:00:00Z"},
              "widget")
    out = detect_mentioned_projects("widget status", tmp_paths)
    assert _names(out) == ["widget"]


def test_malformed_alias_yaml_degrades(tmp_paths):
    _register(tmp_paths, "widget")
    (tmp_paths.root / "project-aliases.yaml").write_text(": : not: valid: yaml: [")
    # Bad alias config must not break name matching.
    out = detect_mentioned_projects("widget", tmp_paths)
    assert _names(out) == ["widget"]
