# Bash → Python Cutover Parity Matrix

Live binaries are in `~/.metasphere/bin/`. The Python entry point
is the `metasphere` console script (installed via `pyproject.toml`).
Dispatcher at `metasphere/cli/main.py`.

| Live binary | Current impl | Python equivalent | Parity | Gap / action |
|---|---|---|---|---|
| `tasks` | bash copy (`scripts/tasks`) | `metasphere task` → `metasphere/cli/tasks.py` | **NO** | Missing archive/YYYY-MM-DD, `archive` alias, `updated_at`, lifecycle line. Mirror cbb6f0e in T2. |
| `messages` | bash copy | `metasphere msg` → `metasphere/cli/messages.py` | audit-needed | Verify feature parity before T4 swap. |
| `metasphere` | symlink → `scripts/metasphere` (944 lines bash) | `metasphere` (venv entry) dispatcher | partial | `metasphere status` and `metasphere ls` still shell out to legacy bash via `_legacy_bash` in `cli/main.py`. OK for cutover; follow-up port. |
| `metasphere-agent` | bash (37KB) | `metasphere agent` → `metasphere/cli/agents.py` | audit-needed | Biggest bash file; likely feature-rich (spawn/wake/list/status). Shim only after module parity confirmed. |
| `metasphere-context` | bash | `metasphere hooks context` → `cli/context.py` | audit-needed | Used by UserPromptSubmit hook. |
| `metasphere-events` | symlink → `scripts/metasphere-events` | — | **MISSING** | No Python port. Used by tasks/messages CLI for event logging. Keep bash for now, revisit. |
| `metasphere-fts` | 188-byte wrapper | — | N/A | Tiny shim around ripgrep/fts indexer. Leave alone. |
| `metasphere-gateway` | bash (32KB, copy) | `metasphere gateway daemon` → `metasphere/gateway/daemon.py` | **NEEDS VALIDATION** | systemd unit runs this. T5 swap contingent on daemon having feature parity (tmux lifecycle, watchdog, stuck-prompt detection, slash-command manifest publish, `METASPHERE_GATEWAY_INVOKED` env-flag). |
| `metasphere-gateway.bak` | backup | — | N/A | Delete in T7. |
| `metasphere-git-hooks` | bash | `metasphere hooks git` → `cli/git_hooks.py` | audit-needed | |
| `metasphere-heartbeat` | bash | `metasphere heartbeat` → `cli/heartbeat.py` | audit-needed | |
| `metasphere-identity` | symlink → bash | `metasphere/identity.py` | audit-needed | Small, safe. |
| `metasphere-migrate` | symlink → bash | — | **MISSING** | No Python port. Leave as bash. |
| `metasphere-posthook` | bash | `metasphere hooks posthook` → `cli/posthook.py` | **DONE** | Already wired in `.claude/settings.local.json` Stop hook. Live binary still bash — swap in T4/T6. |
| `metasphere-project` | bash | `metasphere project` → `cli/project.py` | audit-needed | Projects CLI works for `metasphere project list` per task brief. |
| `metasphere-schedule` | bash | `metasphere schedule` → `cli/schedule.py` | audit-needed | |
| `metasphere-session` | bash | `metasphere session` → `cli/session.py` | audit-needed | |
| `metasphere-spawn` | bash | `metasphere agent spawn` → `cli/agents.py` | audit-needed | Surfaced via `metasphere-spawn` script; parity with `agent spawn`? |
| `metasphere-telegram` | bash | `metasphere telegram` → `cli/telegram.py` | audit-needed | |
| `metasphere-telegram-groups` | bash | `metasphere telegram groups` → `cli/telegram_groups.py` | audit-needed | |
| `metasphere-telegram-stream` | symlink → bash | — | audit-needed | Stream subcommand; may live in `telegram/stream.py`? |
| `metasphere-tmux-submit` | symlink → bash | — | **MISSING** | Leave as bash. |
| `metasphere-trace` | bash | `metasphere trace` → `cli/trace.py` | audit-needed | |
| `metasphere-wake` | bash | `metasphere agent wake` → `cli/agents.py` | audit-needed | |

## Cutover order (risk-sorted, low → high)

1. `tasks` — small, isolated, well-tested module (T2+T3).
2. `messages` — slightly larger, also well-tested (T4).
3. `metasphere-posthook`, `metasphere-heartbeat`, `metasphere-schedule` — stateless hooks, safe.
4. `metasphere-telegram`, `metasphere-telegram-groups` — CLI invocations used by gateway.
5. `metasphere-agent`, `metasphere-spawn`, `metasphere-wake` — persona/session-affecting.
6. `metasphere-gateway` — **highest risk**. systemd-managed, orchestrates everything (T5).

## Not porting in this cutover

- `metasphere-events` (still referenced by bash tasks/messages)
- `metasphere-migrate`
- `metasphere-tmux-submit`
- `metasphere-fts`
- `metasphere status` / `metasphere ls` (legacy bash via `_legacy_bash` shim — already handled)
