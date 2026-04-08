---
id: msg-1775589933-2408580
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:25:33Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Fix the 15 wave-2 review findings + naming cleanup audit. EXECUTE NOW. python-rewrite branch. Commit in tranches.

Read first:
1. /home/openclaw/.metasphere/agents/@reviewer-quality-2/report.md (full text — 1 HIGH, 5 MED, 4 LOW, 5 NIT)
2. /home/openclaw/Code/metasphere-agents/metasphere/ (current state)
3. /home/openclaw/Code/metasphere-agents/docs/PORTING.md (invariants)

Verify branch python-rewrite first. Abort if not.

There are TWO categories of work:

## CATEGORY A — Reviewer findings (15)
Read the report and apply each suggested fix. Group your commits as:

TRANCHE A1 — HIGH (1 fix, 1 commit):
- H1: context.py _render_telegram should shell out to scripts/metasphere-telegram-stream context --history 3 (with the 1024-byte cap that the bash version has) instead of reimplementing the telegram section. Fall back to inline JSONL parsing only if the script is missing.
Commit: "metasphere/context: H1 delegate telegram section to streamer (avoid drift)"

TRANCHE A2 — MED (5 fixes, 1 commit):
- Apply each MED fix per the report
Commit: "metasphere: MED fixes from wave-2 review"

TRANCHE A3 — LOW + NIT (9 fixes, 1 commit):
Commit: "metasphere: LOW + NIT cleanup from wave-2 review"

## CATEGORY B — Naming cleanup audit
Julian directive: "i don\u2019t really like the naming conventions. why are script names and methods prefixed with metasphere_? let\u2019s send an agent to clean that up please and make sure we use well legible method and file names that make sense."

Inside the metasphere/ Python package, the metasphere_ prefix is redundant because callers do `from metasphere.X import Y`. Audit and rename:

- Definitely rename: metasphere/paths.py:26 `def metasphere_dir() -> Path:` → `def root() -> Path:` (or `home()` — pick one and apply consistently). Update every call site in metasphere/ and tests/.
- Audit ALL function names and class names under metasphere/ and metasphere/cli/ and metasphere/telegram/ for any metasphere_ / _metasphere_ prefix. If found inside the namespace, rename. If the prefix is in a string literal that refers to a bash binary (e.g. "scripts/metasphere-tmux-submit", session names "metasphere-<agent>"), KEEP — those are external identifiers.
- File names: confirm all module files are clean (paths.py, io.py, messages.py, etc — no file-level metasphere_ prefix). They already look clean from a quick scan, but verify.
- Variable names: spot-check for `_METASPHERE_*` constants. Rename if redundant.

Do NOT rename:
- The metasphere/ package itself (top-level directory name) — that IS the namespace
- Bash script names in scripts/ — those live on global PATH and the prefix is justified
- The "metasphere-<name>" tmux session naming convention (invariant 7)
- The TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_TOKEN_REWRITE env vars (those have history)

Tests must still pass after the renames. Update test imports as needed.

TRANCHE B — naming cleanup (1 commit):
Commit: "metasphere: rename metasphere_ prefixed identifiers (audit + cleanup)" with body listing every renamed symbol.

## After all 4 tranches:
- Final pytest: `python -m pytest metasphere/tests/ -v 2>&1 | tail -30` — must be all green
- `git log --oneline -10` to confirm 4 new commits on python-rewrite
- Write report at /home/openclaw/.metasphere/agents/@fixer-wave2/report.md with: tranche-by-tranche summary, naming-rename table (old → new), final test count, any deferred items
- Send: messages send @.. !done "fixer-wave2: 4 tranches landed (H/M/L+N/naming), NN tests passing, see report.md"

CRITICAL constraints:
- Stay on python-rewrite branch
- Do NOT push
- Do NOT touch scripts/ (bash side stays as-is)
- After EACH tranche commit, run pytest. Stop if anything goes red and report the failure.
- Naming cleanup is the LAST tranche so the H1/MED/LOW/NIT fixes land cleanly first, then the rename pass touches the same files predictably.

BEGIN. Use Read, Edit, Write, Bash, Glob, Grep. Do not ask permission.
