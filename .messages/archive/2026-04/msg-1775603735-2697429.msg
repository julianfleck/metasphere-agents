---
id: msg-1775603735-2697429
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T23:15:35Z
read_at:
replied_at:
completed_at:
reply_to: 
---

Land all 4 blockers + W3 from ~/.metasphere/agents/@publish-audit/report.md as one tranche on main, push at end. Also generalize the Julian-specific docs (CLAUDE.md, identity templates) while preserving the patterns. Branch on main, one commit per logical layer, tests gated, final push.

WORK ITEMS

**B1: Add LICENSE (MIT)**. Drop a standard MIT License file at repo root. Author 'Julian Fleck'. Year 2026. Single commit.

**B2: Strip the hardcoded chat ID from live tests.**
- metasphere/tests/test_integration.py:29 — `LIVE_CHAT_ID = '228838013'` → read from `os.environ.get('METASPHERE_TEST_CHAT_ID')`. If env var unset, `pytest.skip('METASPHERE_TEST_CHAT_ID not set')` at module level (or test level — module level is cleaner). Same for test_integration_2.py:39.
- Same file: any docstring references to '@metabotmetabotmetabot' → '<configurable test bot>' or just 'a configured test bot'. The marker constant TEST_MARKER stays.
- Bonus W8: test_context.py:124 fixture username 'j0lian' → 'testuser'. One-line fix; roll into the same commit.
- Verify: `pytest metasphere/tests/ -q` still 235 passed / 4 deselected (live tests stay deselected by default).

**B3: De-hardcode /home/openclaw/Code/metasphere-agents from cutover scripts + docs/PROJECTS.md.**
- cutover/apply.sh: replace REPO_SETTINGS path with `REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; REPO_SETTINGS="$REPO_ROOT/.claude/settings.local.json"`. Same for the `pip install -e` line (line 50ish).
- cutover/rollback.sh: same fix.
- cutover/README.md: replace the 3 absolute path mentions with `./cutover/apply.sh` (relative to repo root) and a note that the script must be run from inside a clone.
- docs/PROJECTS.md: this file is currently UNTRACKED (per git status). It's the design proposal I drafted earlier. Two options: (a) commit it but with absolute paths replaced by `<repo-root>` placeholders, OR (b) move it to docs/ as a tracked design doc with placeholders. Pick (a) — the doc has value as a public design artifact. Replace all 5 `/home/openclaw/Code/metasphere-agents` hits with `<repo-root>` or generic example paths.
- Verify: cutover/apply.sh detects its own location correctly when invoked as `./cutover/apply.sh` from the repo root AND as `bash /tmp/clone/cutover/apply.sh` from elsewhere.

**Generalize Julian-specific identity docs** (per Julian's directive 'Julian specific docs should be made more general but still contain the same patterns'):
- **CLAUDE.md**: this file is loaded by Claude Code on every turn for any agent operating in this repo. It currently says 'You are operating as the @orchestrator agent', references Julian's cognitive framework, has a 'Legacy OpenClaw Integration' section specific to Julian's spot host, mentions @j0lian, etc. Generalize:
  - Remove the assumption that the resident agent is @orchestrator on Julian's host. Frame it as 'You are an agent operating in the metasphere harness. If a MISSION.md exists for your agent identity ($METASPHERE_AGENT_ID), read that for your specific role.'
  - Keep ALL the patterns: SPIRAL loop, evolution loop, fractal scoping, memory hygiene, heartbeat etiquette, daily logs first-class, lead-don't-ask, completion protocol, message labels, task priorities, status values. These are the value of the file.
  - Replace 'Julian' / 'j0lian' with 'the user' or 'the operator'.
  - The 'Legacy OpenClaw Integration' section: keep as a generic 'Legacy Harness Migration' framing — explain that on hosts with a prior tinyclaw/openclaw install, the metasphere-context hook may inject persona files from `~/.openclaw/workspace/`. But strip the spot-specific paths and the @j0lian biographical bits.
  - The 'Operational Context' table at the top hardcodes 'You are operating as the @orchestrator agent' / 'Scope = / (repo root)' / 'Runtime = ~/.metasphere/'. Replace with placeholders / 'see your MISSION.md'.
- **No SOUL.md / IDENTITY.md / USER.md changes** — those files do NOT exist in the repo (they live in ~/.metasphere/agents/@orchestrator/ on each host). Verified via `git ls-files | grep -E 'SOUL|IDENTITY|USER'` → empty. The repo-side equivalents would be in templates/. Check templates/ for any Julian-specific identity templates and generalize those too.
- **persona-index.md**: the repo-tracked one (if any). Same treatment.
- **README.md**: scan for any 'Julian' / 'j0lian' / 'spot' references and generalize. The clone URL is fine (it's the canonical repo).

**W3 (rolled into B3 via PROJECTS.md fix above).**

CONSTRAINTS

- Tests stay green: 235 passed, 4 deselected. Verify after each commit.
- One commit per logical layer. Recommend: 1=LICENSE, 2=B2 chat ID + W8 fixture, 3=B3 cutover paths + PROJECTS.md, 4=CLAUDE.md generalization, 5=README scan + any templates/ generalization. 5 commits.
- Push to origin/main at the end of the tranche (per the new 'main stays in sync' rule). NO force push.
- Do NOT touch the warnings I left for v0.2 polish (CLAUDE.md → docs/internal/ relocation, PORTING.md move, README full rewrite for unified CLI surface, CHANGELOG entry, repo-root detritus cleanup, docs/WINTERMUTE move). Those are a separate tranche after Julian decides on v0.2 tone. The CLAUDE.md generalization above is content-level only — DO NOT move the file.
- Time budget: aggressive. ~1hr total per the audit estimate.

When done: `messages send @.. !done <one-line verdict with commit range and push confirmation>` + write report at ~/.metasphere/agents/@publish-fix/report.md.
