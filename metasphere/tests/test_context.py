"""Tests for metasphere.context — per-turn context assembly + drift hash."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from metasphere import context as ctx
from metasphere import messages as _msgs
from metasphere import tasks as _tasks
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# harness_hash
# ---------------------------------------------------------------------------


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_harness_hash_matches_bash_recipe(tmp_paths: Paths):
    """Hash is computed over files under ``paths.root`` (= the dir
    the claude CLI actually reads CLAUDE.md from), not project_root.
    """
    _write(tmp_paths.root / "CLAUDE.md", "claude\n")
    _write(tmp_paths.root / ".claude" / "settings.json", "{settings}\n")
    _write(tmp_paths.root / ".claude" / "settings.local.json", "{local}\n")

    py_hash = ctx.harness_hash(tmp_paths)

    files = sorted(
        str(tmp_paths.root / rel)
        for rel in (
            "CLAUDE.md",
            ".claude/settings.json",
            ".claude/settings.local.json",
        )
    )
    h = hashlib.sha256()
    for f in files:
        h.update(Path(f).read_bytes())
    assert py_hash == h.hexdigest()


def test_harness_hash_empty_when_no_files(tmp_paths: Paths):
    assert ctx.harness_hash(tmp_paths) == ""


def test_harness_hash_reads_root_not_project_root(tmp_paths: Paths):
    """Regression for the 2026-04-16 divergence: baseline writer
    (gateway daemon with METASPHERE_REPO_ROOT set to the source repo)
    and reader (@orchestrator REPL with CWD=~/.metasphere) resolved
    different ``project_root`` values and hashed different CLAUDE.md
    files. Banner fired every inject. Fix roots both to ``paths.root``.

    Prove it: write DIFFERENT content to both project_root and root;
    hash must reflect root (which is where the claude CLI actually
    bakes in CLAUDE.md from), NOT project_root.
    """
    _write(tmp_paths.root / "CLAUDE.md", "ROOT content\n")
    _write(tmp_paths.project_root / "CLAUDE.md", "REPO content\n")

    py_hash = ctx.harness_hash(tmp_paths)
    expected = hashlib.sha256(b"ROOT content\n").hexdigest()
    assert py_hash == expected, (
        "harness_hash must hash paths.root/CLAUDE.md (what the claude "
        "CLI bakes in), not paths.project_root/CLAUDE.md"
    )


# ---------------------------------------------------------------------------
# truncate_section
# ---------------------------------------------------------------------------


def test_truncate_section_caps_long_text():
    long = "x" * 5000
    out = ctx.truncate_section(long, budget=100)
    # The cut keeps ≤ budget bytes plus the truncation marker.
    assert len(out.encode("utf-8")) < 5000
    assert "truncated" in out


def test_truncate_section_passthrough_short_text():
    assert ctx.truncate_section("hello", budget=2048) == "hello"


# ---------------------------------------------------------------------------
# Drift warning
# ---------------------------------------------------------------------------


def test_drift_warning_emitted_when_baseline_differs(tmp_paths: Paths):
    _write(tmp_paths.root / "CLAUDE.md", "claude v1\n")
    (tmp_paths.state).mkdir(parents=True, exist_ok=True)
    (tmp_paths.state / "harness_hash_baseline").write_text("deadbeef\n")

    out = ctx.build_context(tmp_paths)
    assert "Harness drift detected" in out


def test_drift_warning_silent_when_baseline_matches(tmp_paths: Paths):
    _write(tmp_paths.root / "CLAUDE.md", "claude v1\n")
    live = ctx.harness_hash(tmp_paths)
    (tmp_paths.state).mkdir(parents=True, exist_ok=True)
    (tmp_paths.state / "harness_hash_baseline").write_text(live + "\n")

    out = ctx.build_context(tmp_paths)
    assert "Harness drift detected" not in out


# ---------------------------------------------------------------------------
# build_context: section order + empty-state robustness
# ---------------------------------------------------------------------------


def test_build_context_emits_all_sections_in_order(tmp_paths: Paths):
    # Seed each data source so each section produces an identifiable header.
    # 1. Status
    agent_dir = tmp_paths.agent_dir("@orchestrator")
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "status").write_text("working: porting context.py\n")

    # 2. Drift (force a warning) — write to paths.root (where the
    # claude CLI actually bakes CLAUDE.md from; post-PR #19 the hash
    # no longer uses project_root).
    _write(tmp_paths.root / "CLAUDE.md", "claude\n")
    tmp_paths.state.mkdir(parents=True, exist_ok=True)
    (tmp_paths.state / "harness_hash_baseline").write_text("deadbeef\n")

    # 3. Telegram
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    tg_file = tmp_paths.telegram_stream / f"{today}.jsonl"
    tg_file.parent.mkdir(parents=True, exist_ok=True)
    tg_file.write_text(
        json.dumps(
            {"from": {"username": "testuser"}, "text": "hello", "date": 1775539062}
        )
        + "\n"
    )

    # 4. Messages
    _msgs.send_message(
        "@.", "!info", "test message body", "@user", paths=tmp_paths, wake=False
    )

    # 5. Tasks
    _tasks.create_task(
        "ship the port", _tasks.PRIORITY_DEFAULT, tmp_paths.scope, tmp_paths.project_root
    )

    # 6. Events
    from metasphere.events import log_event

    log_event("test.event", "hello world", paths=tmp_paths)

    out = ctx.build_context(tmp_paths)

    # Each section header must appear, in order.
    headers_in_order = [
        "# Metasphere Delta",        # status
        "## ⚠ Harness drift",        # drift
        "## Telegram (recent",       # telegram
        "## Messages",               # messages
        "## Tasks",                  # tasks
        "## Recent Events",          # events
        "## Memory Context (FTS)",   # memory
    ]
    last = -1
    for h in headers_in_order:
        idx = out.find(h)
        assert idx != -1, f"missing section header: {h}\n---\n{out}"
        assert idx > last, f"section out of order: {h}"
        last = idx


def test_build_context_empty_state_does_not_crash(tmp_paths: Paths):
    out = ctx.build_context(tmp_paths)
    # Status header is always present even with no agent dir.
    assert "Metasphere Delta" in out
    # Empty inbox / tasks / events render the "no ..." sentinels rather
    # than blowing up.
    assert "## Messages" in out
    assert "## Tasks" in out
    assert "## Recent Events" in out
    assert "## Memory Context (FTS)" in out


# --- Memory FTS: CAM wiring + query variance (2026-04-17) ------------------


def test_render_memory_fts_uses_cam_when_available(tmp_paths: Paths, monkeypatch):
    """When CamStrategy returns hits, they appear first in the output.
    TokenOverlapStrategy hits appear as fallback after cam hits."""
    from metasphere.memory.base import MemoryHit

    cam_hits = [MemoryHit(source="cam-session/test.md", score=0.95,
                           excerpt="CAM result about foo")]
    fts_hits = [MemoryHit(source="docs/fallback.md", score=0.80,
                           excerpt="FTS fallback result")]

    # Monkeypatch the strategies so no real cam/fts runs
    monkeypatch.setattr(
        "metasphere.memory.api.recall",
        lambda query, limit=10, strategies=None: cam_hits + fts_hits,
    )
    out = ctx._render_memory_fts(tmp_paths, "@test")
    assert "## Memory Context (FTS)" in out
    assert "cam-session/test.md" in out
    # CAM hit appears before FTS hit
    cam_pos = out.find("cam-session/test.md")
    fts_pos = out.find("docs/fallback.md")
    assert cam_pos < fts_pos


def test_render_memory_fts_falls_back_on_cam_failure(tmp_paths: Paths, monkeypatch):
    """When CamStrategy returns nothing (missing binary / timeout), the
    output still has token-overlap hits."""
    from metasphere.memory.base import MemoryHit

    fts_hits = [MemoryHit(source="docs/only-fts.md", score=0.7,
                           excerpt="Token overlap found this")]
    monkeypatch.setattr(
        "metasphere.memory.api.recall",
        lambda query, limit=10, strategies=None: fts_hits,
    )
    out = ctx._render_memory_fts(tmp_paths, "@test")
    assert "docs/only-fts.md" in out
    assert "Token overlap found this" in out


# --- Last-edited files section (2026-04-17) ---------------------------------


def test_last_edited_files_excludes_noise(tmp_path, monkeypatch):
    """Noise dirs (__pycache__, .git, node_modules, .venv) are excluded
    from the last-edited listing."""
    from metasphere import project as _project

    proj_path = tmp_path / "myproject"
    proj_path.mkdir()

    # Real files
    (proj_path / "src").mkdir()
    (proj_path / "src" / "main.py").write_text("code")
    (proj_path / "README.md").write_text("readme")

    # Noise files
    (proj_path / "__pycache__").mkdir()
    (proj_path / "__pycache__" / "mod.cpython-311.pyc").write_bytes(b"noise")
    (proj_path / ".git").mkdir()
    (proj_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (proj_path / "node_modules").mkdir()
    (proj_path / "node_modules" / "pkg.js").write_text("js")

    # Mock project resolution
    from types import SimpleNamespace
    fake_proj = SimpleNamespace(path=str(proj_path), name="myproject")
    monkeypatch.setattr(
        _project, "project_for_scope",
        lambda scope, paths=None: fake_proj,
    )

    from metasphere.paths import Paths
    paths = Paths(
        root=tmp_path / ".metasphere",
        scope=proj_path,
        project_root=proj_path,
    )
    (paths.root / "agents" / "@test").mkdir(parents=True)

    out = ctx._render_last_edited_files(paths)
    assert "main.py" in out
    assert "README.md" in out
    assert "__pycache__" not in out
    assert ".git" not in out
    assert "node_modules" not in out


def test_last_edited_files_respects_10_cap(tmp_path, monkeypatch):
    """Only the 10 most recently edited files are shown, even if more
    exist."""
    from metasphere import project as _project

    proj_path = tmp_path / "proj"
    proj_path.mkdir()
    for i in range(20):
        (proj_path / f"file_{i:02d}.txt").write_text(f"content {i}")

    from types import SimpleNamespace
    monkeypatch.setattr(
        _project, "project_for_scope",
        lambda scope, paths=None: SimpleNamespace(path=str(proj_path), name="proj"),
    )

    from metasphere.paths import Paths
    paths = Paths(
        root=tmp_path / ".metasphere",
        scope=proj_path,
        project_root=proj_path,
    )
    (paths.root / "agents" / "@test").mkdir(parents=True)

    out = ctx._render_last_edited_files(paths)
    # Count file lines (each starts with 2 spaces)
    file_lines = [l for l in out.splitlines() if l.startswith("  ")]
    assert len(file_lines) == 10


def test_render_project_includes_timestamps(tmp_paths: Paths, monkeypatch):
    """The project section's Recent: line includes a UTC timestamp
    when a last commit is available."""
    from metasphere import project as _project
    from types import SimpleNamespace

    proj_path = tmp_paths.scope
    # Create .git dir so the git-log branch fires
    (proj_path / ".git").mkdir(exist_ok=True)
    fake_proj = SimpleNamespace(
        path=str(proj_path), name="test-proj", goal="test goal",
        members=[], status="active",
    )
    monkeypatch.setattr(
        _project, "project_for_scope",
        lambda scope, paths=None: fake_proj,
    )
    # Simulate git log returning subject|timestamp
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0,
            stdout="fix: something|2026-04-16T20:00:00+00:00\n",
        ) if "git" in str(a) else SimpleNamespace(returncode=1, stdout=""),
    )

    out = ctx._render_project(tmp_paths)
    assert "Scope:" in out
    assert "Recent:" in out
    # Timestamp from commit should be present
    assert "2026-04-16T20:00" in out


# ---------------------------------------------------------------------------
# _render_voice_capsule — full persona injection (PR B)
# ---------------------------------------------------------------------------
#
# Pre-PR-B behaviour: capsule loaded VOICE.md or SOUL.md only, capped
# at 1500B / 40 lines. IDENTITY.md and USER.md were never injected.
# Result: persona drift as the kaomoji/warmth-marker/user-model context
# never reached the model.

def _seed_agent_dir(tmp_paths: Paths, agent: str) -> Path:
    d = tmp_paths.agent_dir(agent)
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_voice_capsule_loads_soul_only(tmp_paths: Paths):
    d = _seed_agent_dir(tmp_paths, "@orchestrator")
    (d / "SOUL.md").write_text(
        "# @orchestrator — soul\n\nCalm intensity, thinking companion.\n",
        encoding="utf-8",
    )
    out = ctx._render_voice_capsule(tmp_paths, "@orchestrator")
    assert "## Voice (who you are, how you sound)" in out
    assert "Calm intensity, thinking companion." in out
    # H1 stripped
    assert "@orchestrator — soul" not in out
    # Single section, no Identity / User-model emitted
    assert "## Identity" not in out
    assert "## User-model" not in out
    # Trailing pointer line emitted because at least one file landed
    assert "Persona files at" in out


def test_voice_capsule_loads_all_three_in_order(tmp_paths: Paths):
    d = _seed_agent_dir(tmp_paths, "@orchestrator")
    (d / "SOUL.md").write_text("# soul\n\nVOICE-LINE.\n", encoding="utf-8")
    (d / "IDENTITY.md").write_text(
        "# identity\n\n( • ‿ • ) IDENTITY-LINE.\n", encoding="utf-8"
    )
    (d / "USER.md").write_text("# user\n\nUSER-LINE.\n", encoding="utf-8")
    out = ctx._render_voice_capsule(tmp_paths, "@orchestrator")

    # All three sections present, in declared order: Voice → Identity → User-model
    voice_idx = out.index("## Voice")
    identity_idx = out.index("## Identity")
    user_idx = out.index("## User-model")
    assert voice_idx < identity_idx < user_idx

    # All three bodies landed unchanged
    assert "VOICE-LINE." in out
    assert "( • ‿ • ) IDENTITY-LINE." in out
    assert "USER-LINE." in out


def test_voice_capsule_no_truncation(tmp_paths: Paths):
    """A 100-line / 8KB SOUL.md must render in full — no 1500B / 40-line
    cap. Persona files are load-bearing and small; truncation was the
    PR-B bug."""
    d = _seed_agent_dir(tmp_paths, "@orchestrator")
    body_lines = [f"line-{i:03d} this is non-trivial persona content."
                  for i in range(100)]
    (d / "SOUL.md").write_text("# soul\n\n" + "\n".join(body_lines) + "\n",
                                 encoding="utf-8")
    out = ctx._render_voice_capsule(tmp_paths, "@orchestrator")
    # First and last lines both present — no truncation at either end.
    assert "line-000" in out
    assert "line-099" in out


def test_voice_capsule_voice_md_alias_for_soul_md(tmp_paths: Paths):
    """Backward-compat: agents that still have VOICE.md (pre-rename)
    keep working. SOUL.md is preferred when both exist."""
    d = _seed_agent_dir(tmp_paths, "@legacy")
    (d / "VOICE.md").write_text("# voice\n\nLEGACY-VOICE-LINE.\n",
                                  encoding="utf-8")
    out = ctx._render_voice_capsule(tmp_paths, "@legacy")
    assert "LEGACY-VOICE-LINE." in out
    assert "## Voice" in out


def test_voice_capsule_soul_wins_over_voice(tmp_paths: Paths):
    """If both SOUL.md and VOICE.md exist, SOUL.md is used."""
    d = _seed_agent_dir(tmp_paths, "@dual")
    (d / "SOUL.md").write_text("# soul\n\nSOUL-WINS.\n", encoding="utf-8")
    (d / "VOICE.md").write_text("# voice\n\nVOICE-LOSES.\n", encoding="utf-8")
    out = ctx._render_voice_capsule(tmp_paths, "@dual")
    assert "SOUL-WINS." in out
    assert "VOICE-LOSES." not in out


def test_voice_capsule_identity_only_no_voice(tmp_paths: Paths):
    """Stranger agent has only IDENTITY.md — Voice section is omitted,
    Identity section + pointer line still emitted."""
    d = _seed_agent_dir(tmp_paths, "@id-only")
    (d / "IDENTITY.md").write_text(
        "# identity\n\nWARMTH-LINE.\n", encoding="utf-8"
    )
    out = ctx._render_voice_capsule(tmp_paths, "@id-only")
    assert "## Voice" not in out
    assert "## Identity" in out
    assert "WARMTH-LINE." in out
    assert "Persona files at" in out


def test_voice_capsule_no_files_returns_empty(tmp_paths: Paths):
    """Stranger install with no persona files at all → empty string,
    no pointer-line orphan."""
    _seed_agent_dir(tmp_paths, "@bare")
    out = ctx._render_voice_capsule(tmp_paths, "@bare")
    assert out == ""


def test_voice_capsule_strips_only_top_h1(tmp_paths: Paths):
    """## subheaders inside the body must survive — only the H1
    (single hash) on line 1 is stripped."""
    d = _seed_agent_dir(tmp_paths, "@struct")
    (d / "SOUL.md").write_text(
        "# top h1\n\n## a subheader\n\nbody under sub.\n",
        encoding="utf-8",
    )
    out = ctx._render_voice_capsule(tmp_paths, "@struct")
    assert "top h1" not in out
    assert "## a subheader" in out
    assert "body under sub." in out


def test_voice_capsule_drops_legacy_byte_and_line_caps(tmp_paths: Paths):
    """The _VOICE_BYTE_CAP / _VOICE_LINE_CAP constants are gone —
    nothing in metasphere.context references them anymore."""
    assert not hasattr(ctx, "_VOICE_BYTE_CAP")
    assert not hasattr(ctx, "_VOICE_LINE_CAP")


# ---------------------------------------------------------------------------
# Project-scoped agent resolution — context renderers must find persona /
# mission / status / task / child_reports under
# ~/.metasphere/projects/<proj>/agents/<id>/, not just the global
# ~/.metasphere/agents/<id>/. Pre-fix: paths.agent_dir() returned only the
# global path, so persistent project-scoped agents (e.g. @explorer under
# metasphere-agents, @polymarket under polymarket-agents) silently received
# zero persona injection from PR #63 — the renderers fell off the empty
# global dir without surfacing anything.
# ---------------------------------------------------------------------------


def _seed_project_agent_dir(tmp_paths: Paths, project: str, agent: str) -> Path:
    d = tmp_paths.project_agent_dir(project, agent)
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_voice_capsule_resolves_project_scoped_agent(tmp_paths: Paths):
    d = _seed_project_agent_dir(tmp_paths, "acme", "@scoped")
    (d / "SOUL.md").write_text("# soul\n\nproject-scoped voice.\n", encoding="utf-8")
    (d / "USER.md").write_text("# user\n\nproject-scoped user-model.\n", encoding="utf-8")
    out = ctx._render_voice_capsule(tmp_paths, "@scoped")
    assert "project-scoped voice." in out
    assert "project-scoped user-model." in out


def test_mission_capsule_resolves_project_scoped_agent(tmp_paths: Paths):
    d = _seed_project_agent_dir(tmp_paths, "acme", "@scoped")
    (d / "MISSION.md").write_text(
        "# mission\n\nProject-scoped mission body line.\n", encoding="utf-8"
    )
    out = ctx._render_mission_capsule(tmp_paths, "@scoped")
    assert "## Mission" in out
    assert "Project-scoped mission body line." in out


def test_status_header_resolves_project_scoped_agent(tmp_paths: Paths):
    d = _seed_project_agent_dir(tmp_paths, "acme", "@scoped")
    (d / "status").write_text("active: persistent session", encoding="utf-8")
    out = ctx._render_status_header(tmp_paths, "@scoped")
    assert "active: persistent session" in out
    assert "_Status: unknown_" not in out


def test_voice_capsule_prefers_project_over_global(tmp_paths: Paths):
    # Both layers exist for the same id — project-scoped wins, matching
    # paths.find_agent_dir's tie-break.
    proj_d = _seed_project_agent_dir(tmp_paths, "acme", "@dual")
    (proj_d / "SOUL.md").write_text("# soul\n\nPROJECT-VOICE.\n", encoding="utf-8")
    glob_d = _seed_agent_dir(tmp_paths, "@dual")
    (glob_d / "SOUL.md").write_text("# soul\n\nGLOBAL-VOICE.\n", encoding="utf-8")
    out = ctx._render_voice_capsule(tmp_paths, "@dual")
    assert "PROJECT-VOICE." in out
    assert "GLOBAL-VOICE." not in out
