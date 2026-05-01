# Changelog

All notable changes to Metasphere Agents will be documented here.

---

## 2026-04-15 — PR #12: README + CLI regression bundle

Re-implemented three CLI subcommands that the README promised but had no Python implementation after the bash→Python port; updated README to match current reality; added architecture section with routing diagram; backfilled this CHANGELOG.

- **`metasphere daemon start|stop|restart|status`** — thin wrapper over `systemctl --user` for gateway / heartbeat / schedule. One-line-per-service output; inactive (rc=3) reported but not failed. (commit 2)
- **`metasphere logs [gateway|heartbeat|schedule|events] [-f] [--lines N]`** — tail-and-follow over `~/.metasphere/logs/*.log` and dated `~/.metasphere/events/*.jsonl`. Events JSONL pretty-printed. (commit 3)
- **`metasphere config telegram`** — interactive setup wizard (getMe validation + chat-id auto-discovery via getUpdates) or non-interactive `--token --chat-id`. Writes `~/.metasphere/config/telegram.env` (chmod 0600) + `~/.metasphere/config/telegram_chat_id`. (commit 4)
- **README doc fixes**: `tasks` / `messages` examples switched to `metasphere task` / `metasphere msg`; project `--member` syntax clarified (`@name:role:persistent` 3-part); Telegram slash-command list synced to actual `BOT_COMMANDS_MANIFEST`; OpenClaw migration section replaced with `migrate-project-dirs`. (commit 1)
- **README architecture section** (mermaid routing diagram + canonical per-project layout spec). (commit 5)

## 2026-04-15 — PR #11: Project-paths cleanup + consolidator routing + paused terminal

The maintainer flagged that the operator view was seeing 7-8 STALE→escalated-user events per 15-min cycle from worldwire tasks. Bundled five related fixes:

- **Removed PR #10 migration bridges** — `load_project` / `save_project` are now canonical-only (no in-repo read fallback, no dual-write).
- **Dropped unused `project_root` params** on `_find_task_file`, `scan_active_tasks`, `scan_inbox_messages`.
- **Consolidator routes pings to `@<project>-lead`** before `task.assignee` (maintainer directive). New `_route_ping_target` resolves `project → registered lead member → agent id`; falls back to assignee if no lead.
- **`VERDICT_PAUSED`** — `status: paused` now classifies terminal before the stale window check. `apply_verdict` treats it like BLOCKED / ACTIVE (noop, no ping, no archive).
- Deployed clean; 13 PAUSED→noop, 0 escalations on the next consolidate cycle.

## 2026-04-15 — PR #10: Messages / changelog / learnings migration

Follow-up to PR #9 using the `Project` abstraction. Everything project-scoped now lives under `~/.metasphere/projects/<name>/`:

- `.messages/inbox/` + `.messages/outbox/` routed via `_canonical_messages_dir(scope, paths)`; old per-scope nested inbox walk replaced with "one inbox per project + global bucket".
- `project_changelog` / `project_learnings` write to canonical `.changelog/` / `.learnings/`.
- `_ensure_scaffold` creates canonical dirs (in-repo `.metasphere/` stays as a lightweight marker).
- Migration subcommand `metasphere migrate-project-dirs --what {messages,changelog,learnings,all}` exercises the moves.

## 2026-04-15 — PR #9: Canonical tasks layout

Fixed the root cause behind `metasphere task done <id>` raising `FileNotFoundError`: tasks lived at `<repo>/.tasks/` but the CLI was looking under `~/.metasphere/`. Added a typed `Project` dataclass with `tasks_dir(paths)` / `messages_dir(paths)` / `changelog_dir(paths)` / `learnings_dir(paths)` + `Project.for_name()` / `Project.for_cwd()` / `Project.global_scope()` factories. Tasks now routed through `_canonical_tasks_dirs(paths)` — every registered project's `.tasks/` plus the global bucket at `~/.metasphere/tasks/`. Added `metasphere migrate-project-dirs` subcommand with `--what tasks` (idempotent; refuses on conflict).

## 2026-04-15 — PR #8: Extended test-pollution guard + autouse sandbox

PR #5's `b'BYTES:'`-only signature guard missed the 2026-04-15 Fix 1 leak where 41 fake task `.md` files and 64 stream JSONL lines landed in real `~/.metasphere/`. Three-pass session-end detector: signature match (pass 1); any new file with a pollution extension (.md/.lock/.jsonl/.bin) under a guarded subdir (pass 2); stream-content allow-listing the operator's real chat_id against a regex over the `"chat":{"id":N}` shape (pass 3). Autouse fixture redirects METASPHERE_DIR + 8 home-relative module constants + 8 function `__defaults__` tuples per test — closes the ignored-Paths-arg loophole that bypassed env monkeypatch.

## 2026-04-15 — PR #5: Session-scoped pollution guard (signature-based)

First defensive layer after PR #3's fake `BYTES:biggest.bin` fixture leaked into `~/.metasphere/attachments/`. `pytest_sessionstart` snapshots the real-home file set; `pytest_sessionfinish` fails the suite if any new file with that exact head appeared. Signature-based so live gateway/heartbeat/schedule daemons writing to real dirs during a test run don't false-positive.

## 2026-04-14 — PRs #6 + #7: Single Telegram handler, single poller

- **PR #6** — extracted per-update handling into `metasphere/telegram/handler.py`. Before, `metasphere/cli/telegram.py::_handle_update` had the attachments/archive/debug-log logic but the production `metasphere-gateway` systemd service ran `metasphere/gateway/daemon.py::_poll_once`, which silently filtered `if u.text and u.chat_id is not None` and dropped every photo. Both call sites now route through `handler.handle_update`. Per-update try/except so a handler exception advances the offset instead of re-driving.
- **PR #7** — collapsed three parallel poll loops (`cli/telegram.py::cmd_poll` + `cmd_once` + `heartbeat.py` combined-daemon thread) into `telegram/poller.py::run_poll_iteration`. Deleted the CLI poll subcommands, the `--with-telegram-poll` heartbeat flag, and the `HEARTBEAT_WITH_TELEGRAM_POLL` env var. Gateway daemon is now the single production poller.

## 2026-04-14 — PRs #3 + #4: Telegram attachments + debug instrumentation

- **PR #3** — poller now parses every top-level media object (photo array + any dict with `file_id` — document, audio, video, voice, video_note, animation, sticker), calls `getFile`, downloads bytes to `~/.metasphere/attachments/<message_id>/`, renders an `[attachments]` block appended to the injected orchestrator payload. No MIME-type filter; Claude decides what to do with each file. Photo thumbnails pick largest; filenames sanitized to `[A-Za-z0-9._-]`.
- **PR #4** — JSONL debug log at `~/.metasphere/state/telegram_debug.log` records `post_parse` / `early_return` / `archive_error` / `pre_inject` for each update. Archiver errors caught and logged instead of killing the inject path. Autouse sandbox fixture in `test_telegram.py` prevents future `attachments/` pollution.

## 2026-04-14 — PR #2: Consolidator exempts persistent agents from liveness GC

`_gc_ephemeral_agents` keyed persistence on `MISSION.md` alone, but bootstrap writes `persona-index.md` → `SOUL.md` → `MISSION.md` sequentially. 9 newly-bootstrapped persistent personas got reaped mid-bootstrap as "dead". Widened the skip predicate to `MISSION.md OR persona-index.md` in both `_is_persistent_agent` and `_gc_ephemeral_agents`. Ephemerals unaffected.

## 2026-04-08 to 2026-04-13 — Python port + lifecycle hardening

Pre-PR era: incremental commits through the bash → Python port and associated hardening. Highlights:

- **Python CLI cutover**: Legacy bash `scripts/*` retired; `metasphere <subcmd>` is the canonical entry point. Context hook, posthook (Stop), task/message/update modules all ported. Unified `metasphere` binary; individual script symlinks removed.
- **Telegram bridge hardening**: HTML parse_mode + bold rendering; mobile-first card layout for `/tasks` + `/schedule`; `/session restart`, `/projects`, `/schedule` slash commands; ack-reaction flow (👀 → 👍 on orchestrator reply); `setMessageReaction` retries; telegram-groups non-interactive setup.
- **Task lifecycle refactor**: `consolidate.py` STALE/ACTIVE/BLOCKED/UNOWNED/DONE verdicts; dated archive buckets; `tasks require project+assignee`; `--project <name>` filter; `task describe` verb; `task list --condensed` for all-projects view.
- **Scheduler / daemon polish**: schedule daemon hardening; cron-fire research monitors; heartbeat model-binding audit; agent `--model` flag; persistent vs ephemeral agent model.
- **Directives + broadcast channel**: `DIRECTIVES.yaml` as a broadcast channel; `metasphere agent verify @name`.

Full commit-level history: `git log --since=2026-04-07 --until=2026-04-14 --oneline --first-parent main`.

---

## [2026-04-07T00:37:00Z] — Telegram Bridge + CAM Integration

**Context:** Human-in-the-loop via Telegram; user can intervene on every turn.

**Changes:**
- Created `metasphere-telegram` - Bot command handler (/status, /inbox, /tasks, /send, /cam)
- Created `metasphere-telegram-stream` - Stream archival + CAM indexing
- Created `metasphere-heartbeat` - Proactive monitoring daemon
- Updated `metasphere-context` to inject last Telegram message first
- Telegram messages archived to `~/.metasphere/telegram/stream/YYYY-MM-DD.jsonl`
- Messages indexed into CAM for searchable history

**Architecture:**
```
User (Telegram) ←→ metasphere-telegram-stream ←→ Agent Mesh
                          ↓
                    CAM (searchable)
                          ↓
                    Context Injection
```

**Features:**
- Bidirectional: human → agents, agents → human
- Last message always in agent context (user can intervene)
- Proactive notifications for urgent messages, blocked agents
- Stream archived locally + indexed to CAM

**Files created:**
- `scripts/metasphere-telegram`
- `scripts/metasphere-telegram-stream`
- `scripts/metasphere-heartbeat`

---

## [2026-04-07T00:20:00Z] — Self-Evolution Bootstrap

**Context:** Rewrote claude.md for operational self-evolution; session continued from context compaction.

**Changes:**
- Rewrote `claude.md` from specification doc to operational instructions
  - Added Evolution Loop based on Karpathy's AutoResearch pattern
  - Added SPIRAL cognitive loop documentation
  - Added Quick Reference for scripts, labels, priorities
  - Added Self-evolution protocol
- Updated @orchestrator identity files at `~/.metasphere/agents/@orchestrator/`
  - `LEARNINGS.md`: Session insights (fractal scoping, file-based coordination, hooks)
  - `MISSION.md`: Clear success criteria with phase checkboxes
  - `HEARTBEAT.md`: Current operational status
- Fixed hook path in `.claude/settings.json` (was pointing to wrong directory)
- Verified all scripts working: messages, tasks, metasphere-spawn, metasphere-context

**Learnings captured:**
1. Karpathy's AutoResearch: tight feedback loops > extensive planning
2. Fractal scoping with upward visibility creates natural information flow
3. File-based coordination beats API calls (git-friendly, inspectable, durable)
4. Context injection via hooks gives agents immediate awareness

**Files touched:** `claude.md`, `CHANGELOG.md`, `.claude/settings.json`, `~/.metasphere/agents/@orchestrator/*`

---

## [2026-04-06T23:30:00Z] — Renamed to Metasphere Agents

**Context:** Project renamed for clarity and installability on any machine.

**Changes:**
- Renamed from fractal-agents to metasphere-agents
- Runtime directory: `~/.metasphere/`
- Added installation instructions to claude.md
- Updated all CLI commands to use `metasphere-` prefix
- Prepared for GitHub remote at julianfleck/metasphere-agents

**Impact:** Project is now installable on any VM/computer.

**Files touched:** `claude.md`, `overview.yaml`, `CHANGELOG.md`

---

## [2026-04-06T23:15:00Z] — Added Git Versioning Backbone

**Context:** Git requested as backbone for tracking agent developments across machines.

**Changes:**
- Added comprehensive Git integration section to claude.md
- Defined auto-commit triggers (session_complete, summary_updated, decision_made, task_completed)
- Added git hooks for agent coordination (post-commit notifications)
- Specified merge strategies for concurrent agent work
- Integrated with CAM's existing GitHub sync mechanism

**Impact:** Enables full audit trail of agent activity with cross-machine sync.

**Files touched:** `claude.md`

---

## [2026-04-06T23:13:47Z] — Initial Project Bootstrap

**Context:** Anthropic cut OpenClaw API access; need lightweight replacement using Claude Code.

**Changes:**
- Created `claude.md` with full architecture specification
- Documented SPIRAL agentic loop (Sample → Pursue → Integrate → Reflect → Abstract → Loop)
- Defined virtual filesystem structure for agent/memory coordination
- Integrated Collective Agent Memory (CAM) for knowledge substrate
- Added Claude Code hook patterns (SessionStart, PreToolUse, Stop)
- Created directory structure (docs/, input/, .claude/)
- Wrote initial research notes with external sources
- Created `overview.yaml` project ledger

**Impact:** Project now has solid architectural foundation for MVP development.

**Files touched:** `claude.md`, `overview.yaml`, `docs/research/2026-04-06/01-initial-research.md`

---

## Research Sources

- [Multi-Agent Systems & AI Orchestration Guide 2026](https://www.codebridge.tech/articles/mastering-multi-agent-orchestration-coordination-is-the-new-scale-frontier)
- [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks)
- [Claude Agent SDK Hooks](https://platform.claude.com/docs/en/agent-sdk/hooks)
- ~/Code/collective-agent-memory (CAM architecture)
- ~/Code/writing/ (SPIRAL, semantic zooming, RAGE concepts)
