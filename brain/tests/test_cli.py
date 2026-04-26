"""Unit tests for brain/cli.py — pure helpers + argparse dispatch.

cmd_draft, cmd_post, cmd_verify shell out / hit HTTP and are not
covered here. cmd_explore is covered via dispatch only (run_explore
is monkeypatched).
"""

import sys

import pytest

import cli


# ─────────────────────────────────────────────────────────────────────
# Region + drug listing
# ─────────────────────────────────────────────────────────────────────


def test_list_regions_returns_sorted_stems():
    regions = cli.list_regions()
    assert regions == sorted(regions)
    # All canonical regions should be present (worldsim landed b42029b).
    for r in ("pfc", "amygdala", "accumbens", "hippocampus", "dmn", "worldsim"):
        assert r in regions


def test_list_drugs_returns_sorted_stems():
    drugs = cli.list_drugs()
    assert drugs == sorted(drugs)
    for d in ("kage", "moebius", "ice9"):
        assert d in drugs


def test_load_region_reads_file_contents():
    text = cli.load_region("pfc")
    assert text  # non-empty


def test_load_region_exits_on_unknown_name():
    with pytest.raises(SystemExit):
        cli.load_region("nonexistent-region")


def test_load_drug_reads_file_contents():
    text = cli.load_drug("kage")
    assert text


def test_load_drug_exits_on_unknown_name():
    with pytest.raises(SystemExit):
        cli.load_drug("nonexistent-drug")


# ─────────────────────────────────────────────────────────────────────
# System prompt assembly
# ─────────────────────────────────────────────────────────────────────


def test_assemble_system_prompt_includes_region_and_shape():
    prompt = cli.assemble_system_prompt("pfc", drugs=[])
    assert cli.MOLTBOOK_SHAPE in prompt
    # Region content should also be present.
    assert cli.load_region("pfc") in prompt


def test_assemble_system_prompt_appends_drugs_with_headers():
    prompt = cli.assemble_system_prompt("pfc", drugs=["kage", "moebius"])
    assert "--- DRUG: KAGE ---" in prompt
    assert "--- DRUG: MOEBIUS ---" in prompt
    # Shape must come before the first drug section (drugs append last).
    assert prompt.index(cli.MOLTBOOK_SHAPE) < prompt.index("--- DRUG: KAGE ---")


def test_assemble_system_prompt_no_drugs_has_no_drug_section():
    prompt = cli.assemble_system_prompt("amygdala", drugs=[])
    assert "--- DRUG:" not in prompt


# ─────────────────────────────────────────────────────────────────────
# derive_title
# ─────────────────────────────────────────────────────────────────────


def test_derive_title_uses_explicit_when_given():
    assert cli.derive_title("body text", explicit="My Title") == "My Title"


def test_derive_title_truncates_explicit_to_300():
    long = "x" * 400
    assert cli.derive_title("body", explicit=long) == "x" * 300


def test_derive_title_uses_first_nonempty_line():
    body = "\n\n  first real line  \nsecond line\n"
    assert cli.derive_title(body) == "first real line"


def test_derive_title_truncates_first_line_to_300():
    body = "y" * 400 + "\nshort"
    assert cli.derive_title(body) == "y" * 300


def test_derive_title_handles_single_line_body():
    assert cli.derive_title("just one line") == "just one line"


# ─────────────────────────────────────────────────────────────────────
# argparse dispatch
# ─────────────────────────────────────────────────────────────────────


def test_main_no_subcommand_exits(capsys):
    with pytest.raises(SystemExit):
        cli.main([])


def test_main_unknown_subcommand_exits():
    with pytest.raises(SystemExit):
        cli.main(["bogus"])


def test_main_regions_subcommand_lists_regions(capsys):
    cli.main(["regions"])
    out = capsys.readouterr().out.strip().splitlines()
    assert out == cli.list_regions()


def test_main_drugs_subcommand_lists_drugs(capsys):
    cli.main(["drugs"])
    out = capsys.readouterr().out.strip().splitlines()
    assert out == cli.list_drugs()


def test_main_explore_dispatches_with_dry_run(monkeypatch):
    captured = {}

    def fake_run_explore(**kwargs):
        captured.update(kwargs)

    # cmd_explore does `from explore import run_explore` at call time;
    # patch on the explore module that conftest puts on sys.path.
    import explore

    monkeypatch.setattr(explore, "run_explore", fake_run_explore)

    cli.main(["explore", "--dry-run", "--state-file", "/tmp/s.json"])

    assert captured["dry_run"] is True
    assert captured["state_file"] == "/tmp/s.json"
    assert captured["model"] == cli.DEFAULT_MODEL


def test_main_explore_passes_model_override(monkeypatch):
    captured = {}

    def fake_run_explore(**kwargs):
        captured.update(kwargs)

    import explore

    monkeypatch.setattr(explore, "run_explore", fake_run_explore)

    cli.main(["explore", "--model", "claude-haiku-4-5-20251001"])
    assert captured["model"] == "claude-haiku-4-5-20251001"
    assert captured["dry_run"] is False
