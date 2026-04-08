---
id: msg-1775579361-2039571
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T16:29:21Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Document the metasphere bash CLI surface comprehensively. EXECUTE NOW.

Two deliverables:

1. /home/openclaw/Code/metasphere-agents/docs/CLI.md (user-facing, will eventually go in repo README)
2. /home/openclaw/Code/metasphere-agents/docs/PORTING.md (internal, gitignored, audit doc for the upcoming Python rewrite)

For deliverable 1 (docs/CLI.md), produce a complete reference of the metasphere CLI surface:
- One section per top-level script in /home/openclaw/Code/metasphere-agents/scripts/ (skip *.bak)
- For each script, document: purpose (1-2 sentences), all subcommands with their flags and arguments, what files it reads/writes, what environment variables it consumes, what other scripts it depends on
- Include the unified `metasphere <verb>` dispatch in scripts/metasphere
- Use clear markdown headings, code blocks for commands, and a top-level table of contents

For deliverable 2 (docs/PORTING.md), produce an honest internal audit doc enumerating each FUNCTION (not just script) currently in use that needs to be ported to Python:
- Group by script
- For each function: name, line range, purpose, current implementation summary (1-3 lines), risk areas (set -e gotchas, subshell pipes, JSON parsing via jq, parse_mode bugs, env var leaks, race conditions)
- Mark "DEAD CODE / UNUSED" sections honestly if you find unused functions
- Identify cross-script invariants that the rewrite needs to preserve
- Identify duplication (same logic in multiple scripts)
- A short "rewrite priorities" section ranking which scripts should port first by risk × value
- A "what stays bash" section for the thin tmux/REPL plumbing that does not need porting

Constraints:
- Read every file under /home/openclaw/Code/metasphere-agents/scripts/ (use ls + Read)
- Be honest about smells, dead code, half-finished features
- Length is fine — both docs can be long. Do not truncate.
- Use jq to inspect /home/openclaw/.metasphere/schedule/jobs.json for context if helpful but the docs are about the SCRIPTS not the data
- Do NOT modify any scripts. Read-only audit + write docs.

When finished:
- Write a short summary to /home/openclaw/.metasphere/agents/@cli-doc-writer/report.md with: total scripts documented, total functions enumerated for porting, top-3 risk areas, top-3 architectural smells you noticed
- Send: messages send @.. !done "cli-doc-writer: docs/CLI.md and docs/PORTING.md written, see report.md"

BEGIN. Use Read, Write, Bash (for ls, wc, jq), Glob, Grep. Do not ask permission.
