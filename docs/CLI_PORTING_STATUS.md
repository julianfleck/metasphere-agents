# CLI Porting Status — 2026-04-14

Snapshot of the `metasphere <subcommand>` dispatcher after the final
bash→Python cutover. This document supersedes the older
`docs/BASH_TO_PY_PARITY.md` matrix (which described the *migration in
progress*); parity is now reached.

> **TL;DR** — every subcommand in `metasphere/cli/main.py:REGISTRY`
> resolves to a pure-Python handler. Zero entries route to a
> `not ported` stub, zero handlers shell out to `scripts/metasphere*`.
> The only subprocess calls left inside `metasphere/` are for `git`,
> `tmux`, `systemctl`/`launchctl`, and `curl`-replacement HTTP via
> `urllib` — none of which are bash shell-outs for feature logic.

## Registry status

| Subcommand | Handler | Tests | Status |
|---|---|---|---|
| `status` | `metasphere.cli.main:_status` → `metasphere.status:summary` | covered via integration | ported |
| `ls` | `metasphere.cli.ls:main` | `test_cli_ls.py` (8 tests) | **ported today** |
| `agent` | `metasphere.cli.agents:main` | `test_agents.py`, `test_cli_dispatch.py` | ported |
| `msg` | `metasphere.cli.messages:main` | `test_messages.py` | ported |
| `task` | `metasphere.cli.tasks:main` | `test_cli_dispatch.py`, many integration | ported |
| `telegram` | `metasphere.cli.telegram:main` (+ `telegram_groups:main` sub) | `test_project_telegram.py` | ported |
| `hooks posthook` | `metasphere.cli.posthook:main` | `test_posthook.py` | ported |
| `hooks context` | `metasphere.cli.context:main` | `test_context.py` | ported |
| `hooks git` | `metasphere.cli.git_hooks:main` | `test_git_hooks.py` | ported |
| `schedule` | `metasphere.cli.schedule:main` | `test_schedule.py` | ported |
| `heartbeat` | `metasphere.cli.heartbeat:main` | `test_heartbeat.py` | ported |
| `memory` | `metasphere.cli.memory:main` | `test_memory.py` | ported |
| `trace` | `metasphere.cli.trace:main` | `test_trace.py` | ported |
| `session` | `metasphere.cli.session:main` | `test_session.py` | ported |
| `sessions` | `metasphere.cli.sessions:main` | (covered via `session`) | ported |
| `project` | `metasphere.cli.project:main` | `test_project*.py` (6 files) | ported |
| `gateway` | `metasphere.cli.gateway:main` | `test_gateway.py` | ported |
| `update` | `metasphere.cli.update:main` | `test_update.py` | ported |
| `consolidate` | `metasphere.cli.consolidate:main` | `test_consolidate.py` | ported |

## Audit findings (2026-04-14)

### 1. `_not_ported` stub — removed

Previously `ls` routed to `metasphere.cli.main:_not_ported`, which
printed an error and exited 1. Today's commit wires `ls` to
`metasphere.cli.ls:main` and deletes the stub function + its registry
entry. The dispatcher's docstring now records that no stub is
currently in place; a future removal of a bash-only feature that
lacks a Python port should add a fresh stub explicitly rather than
re-animate the old one.

### 2. No more bash shell-outs for feature logic

`grep -rn 'subprocess' metasphere/cli/` returns **zero hits**. The
subprocess calls under `metasphere/*.py` (outside `cli/`) are:

- `paths.py` — `git rev-parse --show-toplevel` (repo root resolution)
- `update.py` — `git fetch/pull/rev-parse` (legitimate git ops)
- `agents.py`, `session.py`, `tmux.py` — `tmux` session management
- `project.py` — `git clone` + `git log` for project bootstrap /
  changelog
- `schedule.py` — systemd/launchctl wire-up for cron-style firing
- `heartbeat.py` — systemd/launchctl service probe

All of these are the *correct* use of subprocess (binding to an
external daemon/tool, not re-invoking a bash script of ours).

### 3. `scripts/` directory — one Python utility left

```
scripts/
└── migrate_task_frontmatter.py   # one-shot frontmatter migration
```

No `.sh` files, no bash entry points. `scripts/metasphere` and its
siblings were removed in `dea85c2` / `f5590eb`.

### 4. `metasphere/cli/_shims.py` — still needed

Forwards the legacy `metasphere-*` console-script entrypoints to the
unified dispatcher. These entries are referenced from `pyproject.toml`
and used by shell profiles, systemd units, and `.claude/settings.*`
hook configuration on deployed hosts. Not safe to delete until every
caller migrates to `metasphere <subcommand>`. Zero behaviour change;
warns only on TTY.

Leave in place. Track caller migration in a separate follow-up.

### 5. Help string vs registry

`metasphere --help` mentions every top-level registry key. After the
`ls` wiring the "legacy bash" parenthetical on `status` and `ls` was
dropped. Re-audited: zero drift between `_HELP` and `REGISTRY`.

### 6. `install.sh`

Stays bash — it's a one-line-curl bootstrapper that has to run before
Python is guaranteed installed. Feature logic inside it
(`check_dependencies`, `install_python_package`, systemd unit write)
is plausibly portable to `metasphere/install.py`, but the business
value is low (users run `install.sh` once per host) and the risk is
non-trivial (it runs before the venv exists). **Not in scope.**

## What `metasphere status` covers vs the old bash

Port preserves the structural information users relied on:

- sessions: list of alive tmux sessions + which is attached
- tasks: count of active tasks visible from the current scope
- schedule: count of enabled cron-style jobs
- projects: count of initialised projects
- orchestrator gateway: alive/idle probe

Gaps vs the old bash `cmd_status`, left as non-blockers:

- [ ] **Telegram bot-getMe liveness probe** (~15 LOC, touches
  `metasphere/telegram/` — needs HTTP call + error handling)
- [ ] **CAM (Collective Agent Memory) presence/version** (~10 LOC,
  `shutil.which('cam')` + parse `cam --version`)
- [ ] **Last-Telegram-message preview** (~10 LOC, read
  `~/.metasphere/telegram/latest.json`)
- [ ] **Unread-messages-in-inbox count** (~15 LOC, walk
  `.messages/inbox/*.msg` + parse frontmatter for `status: unread`)

All four are cosmetic polish. None affect correctness or automation.
Suggested one-shot if a human wants to close them in a single future
PR (est. **~50 LOC + tests**, single file touch on
`metasphere/status.py`).

## What the port of `ls` covers

`metasphere/cli/ls.py`, ~350 LOC, two modes:

- `metasphere ls` — top-level landscape: projects, last-3 events,
  agents grouped by status, tasks summary, pending-messages count.
- `metasphere ls @agent` — deep dive on a named agent: status, task,
  scope (with `~` collapsing), sandbox, children, last-5 events
  filtered to that agent, orchestrator-session liveness.

Improvements vs the bash version:

1. Uses `list_projects()` `status == 'missing'` field as the "is this
   project actually there?" signal, instead of the bash heuristic of
   checking for `$path/.metasphere/` directory — which produced false
   positives for projects whose on-disk home lives under
   `~/.metasphere/projects/<name>/`.
2. Event formatting delegates to `metasphere.events.tail_events()`
   (same formatter used by `metasphere hooks context` + watchdog
   logs), replacing the inline `jq` pipeline.
3. Agent-filtered event tail walks dated `events-YYYY-MM-DD.jsonl`
   files newest-first rather than grepping the legacy flat file.
4. Timezone honouring (`~/.metasphere/config/timezone`) uses
   `zoneinfo` for correctness instead of `TZ=... date +%H:%M`.

## Remaining follow-up (none blocking)

- [ ] Close the four `metasphere status` cosmetic gaps (≈50 LOC).
- [ ] Migrate remaining `metasphere-*` console-script callers over to
  the `metasphere <subcommand>` form, then delete `cli/_shims.py`.
  Non-trivial: audit shell profiles, systemd units, hook JSON across
  deployed hosts first.
- [ ] `metasphere/update.py` is still the only place in `metasphere/`
  that shells out to `git` for mutation (not just resolution). Its
  subprocess use is correct; flagged here only so future greps don't
  surprise.
