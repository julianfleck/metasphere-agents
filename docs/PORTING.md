# Porting Audit — Metasphere CLI → Python

Audit for Python rewrite. Generated 2026-04-07 by @cli-doc-writer.

Scope: 23 executable scripts under `scripts/` (excluding `*.bak`, `.gitkeep`, README.md). Goal: enumerate functions, flag fragility, identify cross-script invariants the rewrite MUST preserve, and rank porting priorities.

Opinionated. Honest. Long.

---

## scripts/messages (573 lines)

### Functions

| Name | Lines | Purpose | Impl | Risks |
|---|---|---|---|---|
| `gen_msg_id` | 49-51 | Mint msg ID | `msg-<epoch>-<pid>` | PID collisions within a single second are possible across hosts; not globally unique. |
| `rel_path` | 54-57 | Path relative to repo root | sed strip | Breaks if repo root has trailing slash or regex metachars. |
| `resolve_target` | 60-89 | `@.`/`@..`/`@/path/`/`@name` → abs path | case + cat | No validation that resolved path exists; `@name` silently falls back to repo root when no `scope` file. |
| `create_message` | 92-119 | Emit YAML frontmatter + body | heredoc | If `body` contains `---` it corrupts the YAML block. If body contains `$`, shell expands it. |
| `msg_field` | 122-126 | Parse YAML field | `grep ^field: \| head -1 \| sed` | Fails for multiline YAML values; strips trailing spaces. Called everywhere. |
| `update_msg_status` | 129-139 | Rewrite a field in-place | sed -i (macOS/Linux branch) | Breaks if value contains `/` or regex metachars. No atomic rename. |
| `collect_inbox` | 142-161 | Walk scope+parents for `.msg` files | nullglob loop | Upward walk stops at repo root but `$current == $REPO_ROOT*` prefix match is sloppy. |
| `cmd_inbox` | 163-227 | Print inbox with colors | loop + awk to pull body | awk body extractor uses `p==2` which silently fails if frontmatter isn't properly delimited. |
| `cmd_send` | 229-280 | Send + optionally wake recipient | calls `metasphere-events`, `metasphere-agent activity`, `wake_recipient_if_live` | Fire-and-forget error suppression (`|| true`). If `create_message` fails, outbox still gets written. Skip-wake heuristic when `AGENT==@user` is subtle. |
| `wake_recipient_if_live` | 289-346 | Inject `[wake]` notice into recipient tmux | resolves agent name, sources metasphere-tmux-submit | Hard-codes `metasphere-<name>` session convention. Only wakes @orchestrator for scope-targeted sends from repo root. |
| `cmd_reply` | 348-392 | Mark original replied + send reply | find + update_msg_status | `find` walks entire repo every reply — slow on big trees. |
| `cmd_done` | 394-435 | Mark completed, optional !done | find + update | Same find-walk perf issue. |
| `cmd_read_msg` | 437-455 | Mark as read, cat | find | Same. |
| `cmd_tree` | 457-477 | Walk .messages/ dirs | find | OK. |
| `cmd_status` | 479-506 | Agent status list OR message lifecycle | | Two unrelated functions sharing a name. |

### Dead / unused

None observed — all `cmd_*` wire to dispatch.

---

## scripts/metasphere (944 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `resolve_repo_root` | 20-37 | Find repo root, avoiding $HOME | Fallbacks assume repo in PWD; can pick wrong dir if invoked from subdir of a different repo. |
| `ok/warn/err/info/dim` | 54-58 | Color helpers | |
| `cmd_status` | 64-203 | Full system status | Shells out to launchctl/systemctl/curl/jq/sqlite3; many silent `2>/dev/null`. CAM segment count via `sqlite3 ... SELECT COUNT(*) FROM segments` without schema check. |
| `cmd_gateway_status` | 209-279 | Gateway/Telegram status | curl+jq pipeline for bot verification — depends on network. |
| `cmd_run` | 285-294 | exec gateway daemon | |
| `cmd_daemon` | 300-353 | start/stop/restart/status/logs | On Linux uses `metasphere` unit name; on macOS `com.metasphere.plist` — **inconsistent with `update` branch** which uses `metasphere-gateway` unit. |
| `cmd_ls` | 359-454 | Landscape view | jq pipeline vulnerable to empty events.jsonl → garbage output. |
| `cmd_ls_agent` | 457-516 | Per-agent view | OK. |
| `cmd_agents` | 522-547 | Flat agent list | OK. |
| `cmd_telegram_setup` | 553-578 | Interactive bot token | `read -p` — incompatible with headless use. |
| (top-level `update` branch) | 692-778 | git pull + reinstall + restart | **`local` keywords at top level** — Linux bash rejects this. Author noted it (comment at 702-704) but still used `install_source=` without `local` (good), though used `git reset --hard origin/main` unconditionally on fallback (destructive!). |
| (top-level `logs` branch) | 779-823 | Follow logs | Uses `$1 == -f \|\| $2 == -f` pattern which is ambiguous. |
| (top-level `config` branch) | 824-865 | timezone get/set | Uses `local` inside case — top-level, same issue as update. |

### Dead / unused

None outright, but several verbs alias to the same thing (`agents|ag`, `status|st`, `ls|list`, `daemon|d`, `agent|ag` — **`ag` is ambiguous: same alias for both `agents` and `agent`**, so `agent` branch wins because it comes later in the case). This is a bug.

---

## scripts/metasphere-agent (1127 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `cmd_spawn` | 69-185 | Create agent dir, write identity files | SOUL.md only written if absent — no template versioning. Parent sandbox inheritance logic subtle. |
| `cmd_update` | 191-237 | Update status file | |
| `cmd_sunset` | 243-361 | Graceful shutdown w/ learnings doc | Uses `jq -n --arg` for history json — OK. Writes learnings template, then continues as if agent is dead but doesn't actually kill session. |
| `cmd_resume` | 367-389 | Reactivate | Overwrites status blindly. |
| `infer_agent_state` | 395-462 | Read activity, produce status | **BSD/GNU `date -j -f` vs `date -d` branch** — same as other scripts; fragile. **`stat -f %m` vs `stat -c %Y`** — same issue. Returns `|`-separated string which callers split with cut (error-prone). |
| `cmd_status` | 468-570 | Single agent or all | Calls infer_agent_state per agent — O(n²) shelling out. |
| `cmd_tree` | 576-694 | Print parent→child tree | Uses a tmpfile with `trap EXIT rm` to track printed agents — EXIT trap breaks if the function is called from within another trap. Recursive shell function via `cat children \| while read` — children in a subshell, so recursion state doesn't propagate. |
| `cmd_cleanup` | 700-779 | Find stale agents | date parsing fragility again. |
| `cmd_subtree` | 785-829 | Recursive subtree printer | Inner `print_subtree` declared inside cmd; fine in bash but not idiomatic. |
| `cmd_report` | 835-889 | Write report, bubble to parent | Copies report to parent's `child_reports/`. |
| `cmd_view` | 895-958 | Show subtree stats | **`collect_subtree_stats` uses subshell trick `(...; echo) \| tail -1 \| read t a b`** — this `read` is itself in a pipe, so `t/a/b` are unset after the pipe. Dead logic (line 951). |
| `cmd_activity` | 964-1023 | Increment activity.json counters | Uses `jq ... tmp && mv` pattern without locking — race conditions on concurrent hook fires. |

### Dead / unused

- `cmd_view` contains broken subshell stats collection (line 951) — values are always empty defaults (printed as 1/0/0). Effectively dead.
- `cmd_report`, `cmd_subtree`, `cmd_view` are only reachable via `metasphere-agent <verb>` directly; not wired from the top-level `metasphere` dispatcher (which only forwards via `agent` verb fallthrough). They do get called by: nothing in the codebase.

Verified via grep — no other script invokes `metasphere-agent report|subtree|view`.

---

## scripts/metasphere-context (173 lines)

### Functions

No functions — all inline. Injects delta block per turn.

### Risks

- **Stale harness detection**: sha256s concat of files; uses `printf ... \| sort \| xargs cat` — fine but depends on `sha256sum`/`shasum` presence.
- Calls into `messages`, `tasks`, `metasphere-events`, `metasphere-telegram-stream`, `metasphere-fts` — latency is the sum of them all.
- No fallback if `$SCRIPT_DIR/messages` is missing.
- `head -c 1024` truncation on telegram context cuts mid-UTF8 — rare but real.

---

## scripts/metasphere-events (301 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `cmd_log` | 35-101 | Emit JSON line | **jq `--argjson meta` mutations** — each `--meta key=value` re-runs jq to merge; not atomic. Also `meta` key-value split `${2%%=*}` breaks if value contains `=`. |
| `cmd_tail` | 107-118 | tail + jq | Pipe to jq then head — head can close pipe causing jq broken-pipe warnings. |
| `cmd_search` | 124-133 | grep + jq | No regex escaping. |
| `cmd_since` | 139-168 | Date parse + filter | BSD/GNU date branch. |
| `cmd_context` | 174-186 | Recent N events | |
| `cmd_stats` | 192-211 | Count by type/agent | |
| `cmd_prune` | 217-233 | Drop old events | **Not atomic** — writes `.tmp` then mv; concurrent writes can lose events. |

### Dead / unused

None.

---

## scripts/metasphere-fts (114 lines)

### Functions

No functions. Single rg+awk pipeline.

### Risks

- Relies on rg; no fallback.
- alternation regex built via `IFS='|'` — if tokens contain `|`, escapes fail (but tokens are alphanum-only, so OK).
- awk scoring is a heuristic; token priority not weighted by doc frequency.
- Doesn't search past 1 line of context per match; can miss cross-line relevance.
- Corpus paths hardcoded unless `METASPHERE_FTS_CORPUS` set.

---

## scripts/metasphere-gateway (877 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `resolve_repo_root` | 26-44 | Find repo root | Similar to `metasphere`; fallback walks `$HOME/Code/metasphere-agents` etc. Hard-coded paths. |
| `format_timestamp` | 56-68 | UTC + user TZ | |
| `supervisor_log` | 90-92 | Append to supervisor.log | |
| `log` | 124-126 | tee to gateway.log | |
| `session_alive` | 133-135 | tmux has-session | |
| `session_info` | 138-146 | display-message | |
| `build_initial_context` | 149-271 | Big heredoc used to be injected at session start | **Comment says this is NOT injected anymore** (line 301-309). Function is called... nowhere? **Verify: grep.** Called only by `restart_claude_in_session` in spirit, but actually the respawn-loop path doesn't inject it. Function body is orphaned — dead code. |
| `start_session` | 274-318 | tmux new + respawn loop | **tmux send-keys with literal shell** — `"exec bash -c 'while true; do claude ...; done'"`. Quoting fragile. |
| `ensure_session` | 321-339 | Liveness check + revive | Staleness check commented out as "Could add more". |
| `inject_to_session` | 342-354 | submit_to_tmux wrapper | |
| `build_precommand_context` | 361-384 | Call cam + metasphere ls | cam shellout can hang. |
| `build_message_context` | 387-401 | Wrap user message | |
| `send_telegram` | 404-421 | curl sendMessage | **`parse_mode=Markdown`** — bites on unbalanced asterisks; silent failure (curl output discarded). **This is the "missing reply" bug** that the telegram-stream path explicitly avoids. |
| `process_user_message` | 424-450 | Inject into session OR per-message claude | Per-message path does `echo $context \| claude -p ... \| tail -100` — truncates response. |
| `build_full_context` | 453-492 | Legacy per-message context | |
| `poll_telegram` | 498-543 | Long-poll updates | Delegates to `metasphere-telegram-stream once` AND `metasphere-telegram process` for slash commands — two code paths, two sources of truth. |
| `harness_hash_files`/`compute_harness_hash`/`write_harness_hash_baseline` | 557-587 | Baseline sha256 | OK. |
| `compute_config_signature` | 604-622 | BSD/GNU `stat` branch | **Previously had the `stat -f %m \|\| stat -c %Y` bug** (comment 596-603). Fixed now. |
| `restart_claude_in_session` | 627-665 | Ctrl-C, /exit, pkill, respawn | Multiple sleeps; race-prone. `pkill -P $pane_pid -f claude` could kill unrelated processes if pane_pid is wrong. |
| `check_config_changes` | 668-688 | mtime diff → restart | |
| `check_stuck_prompts` | 691-733 | Auto-approve safety-hooks; force Enter on stuck paste | Regex-matches pane content; brittle to UI changes. |
| `run_watchdog` | 739-766 | Per-tick checks | |
| `restart_orchestrator_claude` | 769-777 | CLI wrapper | |
| `daemon_mode` | 783-816 | Main loop | |

### Dead / unused

- **`build_initial_context`** (149-271) — function body is never called. Confirmed by grep: no other script calls it, and within metasphere-gateway itself it's only referenced in comments ("We deliberately do NOT inject..."). Pure dead code, ~120 lines.
- `build_message_context` (387-401) is called by `process_user_message` persistent-mode path but the result is injected via tmux — its pretty heredoc output is wasted typing effort.

---

## scripts/metasphere-git-hooks (257 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `generate_pre_commit/post_commit/post_checkout/pre_push` | 26-120 | Emit hook shell scripts | Hooks reference `~/.metasphere/bin/metasphere-events` — hardcoded path; fragile if `$METASPHERE_DIR` is overridden. |
| `cmd_install/uninstall/status` | 126-218 | Manage hooks in `.git/hooks/` | Backs up only if existing hook doesn't contain "Metasphere" — misses hooks installed by older versions with different marker. |

### Dead / unused

None.

---

## scripts/metasphere-heartbeat (336 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `notify` | 37-53 | curl sendMessage with parse_mode=Markdown | **Same markdown bug as gateway.** Prefixes `[heartbeat]`/`[URGENT]`. |
| `already_notified/mark_notified/clear_notified` | 56-74 | State file grep/append | Race condition: state file edited without locking. |
| `check_urgent_messages` | 77-97 | Walk inbox, notify on !urgent+unread | `find \| while read` — body in a subshell, but fine for this case (no state mutations needed). |
| `check_agent_status` | 100-122 | Detect waiting/blocked | Nested `while read` inside a `for` that writes `$STATE_FILE` — the `while` is in a subshell so its edits could be lost depending on flow. |
| `check_tasks` | 125-145 | Count urgent | |
| `build_agent_context` | 148-172 | Heredoc for agent heartbeat | |
| `invoke_agent_heartbeat` | 175-202 | Inject via tmux or claude -p | **`$timestamp` used but never set** (line 182) — the variable is from `heartbeat_once` but referenced across function boundary. |
| `heartbeat_once` | 205-222 | One tick | |
| `heartbeat_daemon` | 225-238 | Loop | |
| `poll_telegram` | 241-273 | Telegram long-poll (duplicate of gateway's and stream's) | Writes to `telegram_offset` (not `telegram/offset`) — **different offset file than gateway/stream!** Split-brain. |
| `combined_daemon` | 276-302 | Heartbeat + telegram loop | |

### Dead / unused

- `heartbeat_daemon` never called; `combined_daemon` is dispatched instead. Dead.

---

## scripts/metasphere-identity (38 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `resolve_agent_id` | 10-33 | Resolution order | `cat ... \| tr -d '[:space:]'` may strip legitimate whitespace in multi-word IDs (but `@name` format has no spaces, OK). |

Good, simple. Direct port.

---

## scripts/metasphere-migrate (705 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `detect_openclaw` | 44-132 | Print detection report | |
| `extract_telegram_token` | 138-172 | jq over 5+ fallback paths | |
| `extract_github_token` | 174-190 | | **Never called** — extracted but nothing uses the result. Dead. |
| `migrate_telegram` | 192-214 | Extract + verify + write | |
| `migrate_soul` | 220-302 | Copy workspace files + per-agent profiles | |
| `write_persona_index` | 304-344 | Emit persona-index.md | |
| `migrate_cron` | 350-440 | jq transform openclaw jobs.json → metasphere jobs.json | Complex jq; disabled jobs silently dropped. No test for malformed source. |
| `migrate_skills` | 446-475 | Symlink skills | |
| `migrate_memory` | 481-506 | Register sqlite path + copy legacy files | |
| `parse_sessions` | 512-537 | Run `cam index` | |
| `disable_openclaw` | 543-572 | Stop launchd/systemd | |
| `run_migration` | 578-631 | Orchestrate | All steps `\|\| true` — silent failures aggregate. |
| `prompt_disable` | 637-657 | Interactive | |

### Dead / unused

- `extract_github_token` — defined, not called.

---

## scripts/metasphere-posthook (120 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `route_to_telegram` | 28-93 | Extract last assistant text, send | **`tac` not portable** (missing on macOS). Uses awk to find last assistant line, then jq to extract text — two parsers, two chances to fail. De-dupe via single hash file — per-agent not per-session, so multi-agent will stomp. `stop_hook_active` guard is good. |

Inline post-hash logic. No other functions.

### Risks

- Turn counter from activity.json read via jq without locking.

### Dead

None.

---

## scripts/metasphere-project (353 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `cmd_init` | 29-62 | Scaffold .metasphere/, register | Upserts by path, but `jq` registration uses `now` (jq's seconds-since-epoch) not quoted timestamp — mixes types. |
| `cmd_list` | 68-102 | List projects | |
| `cmd_status` | 108-164 | Per-project status | Walks up from cwd looking for `.metasphere`. |
| `cmd_changelog` | 170-233 | Git + events + agent | Writes header to stdout, then silently — **never actually writes `$changelog_file`** despite the mkdir. Header goes to stdout only. Bug. |
| `cmd_learnings` | 239-304 | Aggregate learnings | **`has_learnings` tracking inverted**: sets `has_learnings=true` then tests `if ! $has_learnings` which is never true — the agent header never prints. Bug. |

### Dead / unused

- `cmd_changelog` output never persists.
- `cmd_learnings` agent section header never fires.

---

## scripts/metasphere-schedule (587 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `gen_id` | 49-51 | `job-<ts>-<pid>` | |
| `parse_time` | 54-87 | `@in:*m` etc | BSD/GNU date branch. |
| `parse_repeat` | 90-117 | | |
| `cmd_list` | 120-177 | Dual-schema pretty print | |
| `cmd_add` | 180-239 | Create job | command shortcut expansion for `send`/`status`/etc — prefix match only. |
| `cmd_remove` | 242-260 | Filter | |
| `cron_should_fire` | 267-346 | Python croniter or bash fallback | Bash fallback honors tz via `TZ=$tz date` but the fallback matcher has no "N minutes ago" window — relies on the 60 s gating via `last_fired_at`. Can still miss ticks if scheduler runs less often than once/min. |
| `_cron_field_match` | 350-382 | Single field matcher | Recursive — allocates a subshell per comma-split. Slow. |
| `cmd_run` | 385-504 | Iterate, fire, rewrite jobs.json | **Previously had a subshell bug** (comment 392-394) causing all jobs to be wiped — now uses process substitution. **Defensive check at 498**: refuses to write if input was non-empty and output is empty. Good guardrail. `eval "$full_cmd"` for legacy — **arbitrary-code-execution via scheduled job**. |
| `cmd_daemon` | 507-515 | Loop | |
| `cmd_message` | 518-531 | Shortcut for send scheduling | |

### Dead / unused

None.

### Risks

- File-based atomic update is single-writer. If two schedule runs overlap they clobber each other.
- Cron field matching doesn't handle named months/weekdays, `@reboot`, etc.
- Agent mapping logic (444-452) is hardcoded.

---

## scripts/metasphere-session (364 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `session_name` | 56-59 | `metasphere-${agent#@}` | |
| `cmd_start` | 65-198 | tmux new + claude + init prompt | Init prompt is a huge string (~60 lines) submitted via submit_to_tmux. Slower than wake's bare MISSION.md model. Doesn't use respawn loop — session dies when claude exits. |
| `cmd_attach/send/list/stop` | 204-308 | Trivial wrappers | `cmd_stop` sends `/exit`, sleeps 1, then kills — race if agent is mid-tool. |

### Dead / unused

None. Overlaps heavily with metasphere-wake (non-respawn vs respawn variant).

---

## scripts/metasphere-spawn (223 lines)

### Functions

None. Linear script.

### Risks

- `HARNESS` markdown generated inline — if `$TASK` contains backticks or `$`, heredoc interpolation fires.
- Launches claude via nested subshell + nohup + disown. pid capture racy (`sleep 0.2`).
- **`messages send "@$SCOPE_PATH" ...`** (172) — `@` + path, but `resolve_target` in messages expects `@/path/` or `@name`. This is sending to `@/path` (missing trailing slash in the case pattern?). The `@*` branch actually catches it — treats it as agent name with scope fallback to repo root. Subtle.

---

## scripts/metasphere-telegram (457 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `tg_api` | 46-50 | curl POST wrapper | |
| `send_message` | 58-73 | parse_mode optional, defaults plain | Good — note explicit comment about parse-entity bugs. |
| `get_updates` | 76-84 | long poll | |
| `process_message` | 88-345 | Slash command router | 250-line case statement. Each branch shells out to a helper. `reply` and `reply_code` nested functions use `--data-urlencode` — good. **Some branches use `reply "...` with asterisks** (e.g. `/agents`, 172) that **would break** if they used Markdown parse_mode, but `reply` doesn't pass parse_mode — OK. |
| `cmd_poll` | 348-384 | daemon mode | Same dual-parser issue as other telegram scripts. |
| `cmd_notify/cmd_send/cmd_info` | 387-416 | Trivial | **`cmd_notify` uses markdown via `*Notification from ...*`** but `send_message` passes no parse_mode — the asterisks render literally. Minor cosmetic. |

### Dead / unused

None.

---

## scripts/metasphere-telegram-groups (340 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `tg_api` | 54-61 | | |
| `create_topic` | 64-101 | createForumTopic API | Double-call fallback `2>/dev/null \|\| tg_api ...` — if first succeeds but returns non-ok, still runs second. Buggy retry. |
| `send_to_topic` | 104-138 | sendMessage with `message_thread_id` + `parse_mode=Markdown` | **Markdown parse mode bug** again. |
| `list_topics` | 141-154 | jq + pretty print | |
| `get_link` | 157-173 | Build `https://t.me/c/...` | |
| `setup_forum` | 176-224 | Interactive wizard | |
| `create_workspace` | 227-249 | Helper used by /link in metasphere-telegram | Only caller is `metasphere-telegram` line 269. |
| `process_forum_command` | 252-285 | Mini slash-command dispatcher for topics | **Never called** — grep shows no invocation outside its own dispatch case. Dead (or usable via `process-cmd` verb, which is not documented anywhere else). |

### Dead / unused

- `process_forum_command` — reachable only via `metasphere-telegram-groups process-cmd` verb, which nothing calls.

---

## scripts/metasphere-telegram-stream (446 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `get_offset/save_offset` | 35-42 | | |
| `archive_message` | 45-51 | Append to daily JSONL | |
| `index_to_cam` | 54-76 | Pipe to `cam index` | Silent failures. |
| `save_latest` | 79-89 | jq project latest.json | |
| `_tg_stream_send_chunk` | 92-105 | curl sendMessage plain | |
| `inject_to_orchestrator` | 110-124 | tmux submit to orchestrator session | Sources metasphere-tmux-submit inside the function. |
| `poll_once` | 127-197 | Polling loop body | **`while read` in a pipe after `jq`** — state (offset, etc) in subshell. Offset save happens inside the loop; the subshell loses offset mutations for the parent shell — but save_offset writes to disk so it's persistent. OK. |
| `get_latest` | 200-206 | | |
| `format_context` | 209-297 | Recent N messages | BSD/GNU `date -v-1d`/`-d yesterday` branch. |
| `send_message` | 300-385 | Chunked send with 3900-char split | Good — handles 4096 limit. **Chunk marker prefix eats into budget** but math isn't adjusted (uses CHUNK_MAX for slice *and* adds `[n/N] ` prefix). Can exceed 4096 on extreme cases. |
| `daemon_mode` | 388-401 | Loop | |

### Dead / unused

None.

---

## scripts/metasphere-tmux-submit (174 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `_mts_find_tmux` | 27-40 | Locate tmux binary | Hard-codes homebrew paths. |
| `_mts_has_pending_paste` | 44-50 | grep pane for `[Pasted text #` | Only last 5 lines captured. |
| `submit_to_tmux` | 60-117 | Literal-mode line-by-line send | **Core primitive**, relatively solid. `send-keys -l` bypasses bracketed-paste, good. Uses `C-j` between lines, `Enter` at end. Retry loop checks for placeholder. |
| `tmux_submit_watchdog` | 122-143 | External recovery | |

### Dead / unused

None.

---

## scripts/metasphere-trace (318 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `gen_trace_id` | 36-38 | | |
| `cmd_run` | 44-148 | `eval "$command" > stdout 2> stderr` | **eval on user input** — same ACE risk as schedule. Error detection via `grep -i 'error\|failed\|exception\|fatal'` — wildly noisy (any help text containing "error" triggers). |
| `notify_reviewers` | 154-175 | Walk scope for @reviewer, send | |
| `cmd_list/cmd_show/cmd_search` | 181-276 | jq/grep over index.jsonl | |

### Dead / unused

None.

---

## scripts/metasphere-wake (204 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `is_persistent_agent` | 37-40 | MISSION.md exists | |
| `session_name_for` | 42-45 | | |
| `list_persistent` | 47-60 | | |
| `show_status` | 62-79 | activity-age | `date +%s - activity` — OK. |
| `wake_agent` | 81-172 | tmux new + respawn loop + 15s readiness poll | **Same `exec bash -c 'while...'` quoting as gateway.** Readiness poll via `capture-pane \| grep "bypass permissions"` — brittle to claude UI wording changes. |

### Dead / unused

None.

---

## scripts/tasks (531 lines)

### Functions

| Name | Lines | Purpose | Risks |
|---|---|---|---|
| `gen_task_id` | 43-84 | Hierarchical naming | Complex branch logic: project/agent/name depending on context. Fallback uses `$RANDOM` (limited range). |
| `rel_path` | 87-90 | | Same as messages. |
| `create_task` | 93-136 | YAML + md template | Same `---`-in-body hazard. |
| `task_field/update_task_field/append_update` | 139-172 | YAML field parse/edit | BSD/GNU sed -i branch. `append_update` uses `sed` to insert after `^## Updates$` — fragile to markdown variation. |
| `collect_tasks` | 175-200 | Walk scope+parents | Same bugs as `collect_inbox`. |
| `find_task` | 203-215 | find by id | Walks entire repo. |
| `cmd_list/new/start/update/done/show/tree` | 217-463 | CRUD | |

### Dead / unused

None.

---

## Cross-script invariants

Things the Python rewrite MUST preserve (bash and python sides will coexist during migration):

1. **Message file format** (YAML frontmatter + body, single `---` delimiter, specific fields: `id`, `from`, `to`, `label`, `status`, `scope`, `created`, `read_at`, `replied_at`, `completed_at`, `reply_to`). Status lifecycle: `unread → read → replied → completed`. IDs: `msg-<epoch>-<pid>`.

2. **Task file format** (YAML frontmatter + body): `id`, `title`, `priority`, `status`, `scope`, `created`, `created_by`, `assigned_to`, `started_at`, `completed_at`. Status: `pending → in-progress → completed` (`blocked` also valid). Priorities: `!urgent !high !normal !low`. Directory: `.tasks/active/` → `.tasks/completed/` on done.

3. **Agent directory layout** under `~/.metasphere/agents/@<name>/`: `SOUL.md`, `MISSION.md` (presence = persistent), `HEARTBEAT.md`, `LEARNINGS.md`, `status`, `scope`, `task`, `parent`, `sandbox`, `children`, `spawned_at`, `updated_at`, `activity.json`, `session.log`, `learnings/`, `history/`, `reports/`, `child_reports/`, `harness.md`, `output.log`, `pid`.

4. **Agent status values** (parsed by case-matches across multiple scripts): `spawned`, `active`, `active:*`, `working:*`, `waiting:*`, `blocked:*`, `idle`, `idle:*`, `stale:*`, `complete`, `complete:*`, `sunset`, `sunset:*`, `failed:*`, `unknown`.

5. **Scope resolution**: messages/tasks visible = current scope + all parent scopes up to `$REPO_ROOT`. Address forms: `@.`, `@..`, `@/path/`, `@name`. Named agents resolve via `~/.metasphere/agents/@name/scope`.

6. **Event log format** (`~/.metasphere/events/events.jsonl`): `{id, timestamp, type, message, agent, scope, meta}` one per line. Indexed duplicately at `events/index/<type>.log` and `events/index/agents/<name>.log`. Consumed by many callers via jq — schema is load-bearing.

7. **Tmux session naming convention**: `metasphere-<name>` (no `@`). `metasphere-orchestrator` is special. Wake/kill/attach/check all depend on this exact format.

8. **Harness-hash baseline file** (`~/.metasphere/state/harness_hash_baseline`): content-sha256 of CLAUDE.md + .claude/settings(.local).json + scripts/metasphere-context, concatenated in sorted-by-path order. Consumed by metasphere-context for drift detection. Any rewrite of either side must match.

9. **Activity counter schema** (`~/.metasphere/agents/@<name>/activity.json`): `{messages_sent, messages_received, commands_run, turns, last_activity}`. Bumped via `metasphere-agent activity --sent|--received|--command|--turn`.

10. **Schedule file schema** (`~/.metasphere/schedule/jobs.json`): dual schema — legacy `{id, command, full_command, next_run, repeat_secs, repeat_human, created}` and cron `{id, source, source_id, agent_id, name, enabled, kind:"cron", cron_expr, tz, payload_kind, payload_message, model, session_target, wake_mode, imported_at, next_run, command, full_command, last_fired_at}`. **Don't wipe the cron side when porting.**

11. **Telegram offset file**: `~/.metasphere/telegram/offset` (canonical, used by gateway + telegram-stream). **`~/.metasphere/telegram_offset` (note: no subdir) is ALSO used by metasphere-heartbeat — split-brain.** Rewrite must consolidate to one.

12. **Config files**:
    - `~/.metasphere/config/telegram.env` — `TELEGRAM_BOT_TOKEN=...` shell-sourced
    - `~/.metasphere/config/telegram_chat_id` — raw chat id
    - `~/.metasphere/config/telegram_forum_id` — supergroup id
    - `~/.metasphere/config/timezone` — TZ name
    - `~/.metasphere/config/openclaw_workspace` — path pointer (legacy)
    - `~/.metasphere/config/openclaw_memory_db` — sqlite path (legacy)

13. **Sandbox levels**: `none`, `scoped`, `nobash`, `readonly`. Mapped to `--allowedTools` at session start. Child can only be same or stricter than parent.

14. **Log paths**: `~/.metasphere/logs/{gateway.log,supervisor.log,metasphere.log,metasphere.error.log}`.

15. **Tmux paste-submission protocol**: `send-keys -l` literal typing, `C-j` between lines, Enter at end, poll `capture-pane` for `[Pasted text #` placeholder, retry Enter up to 3 times. Non-negotiable — this is the result of painful empirical debugging.

16. **Message wake protocol**: after `cmd_send`, if sender is not `@user` and recipient tmux session is alive, inject `[wake] new <label> from <from>: <body:200>` into recipient's session. `@user` skip exists because `telegram-stream` already injects via `inject_to_orchestrator`.

---

## Duplication

1. **YAML field parsing** — `msg_field` in messages (122), `task_field` in tasks (139). Same grep+sed pattern. Also ad-hoc greps in `metasphere-heartbeat` (79-82) and `metasphere` (161, 179).

2. **Relative path computation** — `rel_path` in both messages (54) and tasks (87). Identical.

3. **Scope walking** — `collect_inbox` in messages (142) and `collect_tasks` in tasks (175). Same while-loop, same stop condition, same bugs.

4. **Color setup block** — identical (or near-identical) `if [[ -t 1 ]]` color-code blocks in: messages, metasphere, metasphere-agent, metasphere-migrate, metasphere-project, metasphere-schedule, metasphere-session, metasphere-trace, tasks, metasphere-telegram-groups. Ten copies.

5. **Identity resolution** — `metasphere-identity` exists and is sourced by messages + tasks + metasphere-events. But `metasphere-context`, `metasphere-posthook`, `metasphere-gateway`, `metasphere-spawn`, `metasphere-wake`, `metasphere-telegram-stream` all just read `$METASPHERE_AGENT_ID` directly with ad-hoc defaults — inconsistent.

6. **Repo root resolution** — `resolve_repo_root` re-implemented in metasphere (20) and metasphere-gateway (26). Similar but not identical logic. Others fall back to `git rev-parse \|\| pwd`.

7. **Tmux binary finding** — metasphere-gateway (100-112), metasphere-session (24-33), metasphere-tmux-submit (27-40). Three implementations of "find tmux on this host."

8. **Telegram config loading** — `source ~/.metasphere/config/telegram.env` and `cat telegram_chat_id` repeated in metasphere, metasphere-gateway, metasphere-heartbeat, metasphere-telegram, metasphere-telegram-stream, metasphere-telegram-groups. Six copies.

9. **Telegram long-polling** — implemented three times: metasphere-gateway (498), metasphere-heartbeat (241), metasphere-telegram-stream (127), plus metasphere-telegram (348). Four implementations. Heartbeat's uses a different offset file than the others.

10. **`curl sendMessage` wrappers** — metasphere-gateway's `send_telegram` (Markdown, broken), metasphere-heartbeat's `notify` (Markdown, broken), metasphere-telegram's `send_message` (plain, correct), metasphere-telegram-stream's `_tg_stream_send_chunk` (plain, correct), metasphere-telegram-groups's `send_to_topic` (Markdown, broken). Five copies, three of them buggy.

11. **BSD/GNU date branches** — metasphere-agent (419), metasphere-schedule (73-75), metasphere-events (147-155), metasphere-telegram-stream (66, 230). Should be one helper.

12. **Agent dir iteration** — `for d in "$METASPHERE_DIR/agents"/@*/` appears in ~15 places across the codebase.

13. **Initial-prompt / harness generation** — metasphere-gateway (`build_initial_context`, dead), metasphere-session (`cmd_start` inline), metasphere-spawn (inline heredoc), metasphere-wake (intentionally empty — relies on CLAUDE.md). Four different views of "what should a fresh agent see."

---

## Rewrite priorities

Ranked by (risk × value × consolidation win). Top first.

1. **messages + tasks + metasphere-identity + metasphere-events** — the fractal coordination primitives. High call volume (every turn via context hook), high duplication (YAML parsing, scope walking, color setup), high risk (sed -i on YAML, find across repo on every reply/done). Porting these first unlocks a shared Python library for YAML/scope/identity/event that every other script can later depend on. **Justification: foundation; shared model classes; biggest duplication win.**

2. **metasphere-schedule** — contains `eval` on stored commands (ACE), dual schema, cron matching that forks python3 anyway, no locking on jobs.json. Value: high (cron is load-bearing for autonomous ops); risk: critical (wipe-all bug was recent). **Justification: python-native apscheduler/croniter replaces Python-shelled-from-bash trick.**

3. **metasphere-gateway** — the big one. 877 lines, contains dead code (`build_initial_context`, ~120 lines), broken Markdown telegram path (swallows errors), duplicates polling/watchdog/send logic. But it's also the daemon every other script depends on. Port AFTER messages+tasks+events so the Python gateway can use them. **Justification: most complex, highest ROI, but needs foundations.**

4. **metasphere-telegram-stream + metasphere-telegram + metasphere-telegram-groups** — consolidate into a single `metasphere.telegram` package. Share one offset file, one chunker, one sendMessage (plain-text default, with explicit opt-in to MarkdownV2). Kill the three buggy Markdown implementations. **Justification: stop the "missing reply" bug class permanently.**

5. **metasphere-agent** — 1127 lines, complex state inference, broken subshell math in `cmd_view`, O(n²) per-agent shelling in `cmd_status`. Dead `cmd_report`/`cmd_subtree`/`cmd_view`. Port as a python `AgentRegistry` class, cutting ~30% via deletion. **Justification: biggest script; cleanup upside.**

6. **metasphere-heartbeat** — split-brain offset file, undefined `$timestamp`, markdown notify bug, `heartbeat_daemon` dead. Small enough to rewrite cleanly. **Justification: fix offset split; dedupe notification state.**

7. **metasphere-context + metasphere-posthook + metasphere-fts** — per-turn hooks. Small, hot-path, must be fast. Port together as a single `metasphere.hooks` module. Replace metasphere-fts with a real FTS backend (sqlite FTS5 or whoosh) if the rewrite is going file-based anyway (which the project memory says it is). **Justification: latency matters; small surface; clean seam.**

8. **metasphere-trace** — `eval` on user input; noisy error detection. Rewrite with proper subprocess capture and structured error classification. **Justification: ACE fix + usefulness.**

9. **metasphere-project** — both `cmd_changelog` and `cmd_learnings` are broken (output-to-stdout-only and inverted-flag bugs respectively). Nobody's noticed because nobody uses them. Port or delete. **Justification: either make it work or remove it.**

10. **metasphere-migrate** — one-shot utility, low risk. Port only if Python is already in the stack; otherwise leave. **Justification: runs once per host.**

11. **metasphere-agent's activity tracker** — can be a tiny `ActivityCounter` class behind a file lock. Trivial port.

---

## What stays bash

Thin subprocess-wrangling glue with no logic worth moving:

- **scripts/metasphere-tmux-submit** — pure tmux+shell plumbing. The `send-keys -l` + C-j + Enter protocol was painfully tuned. A Python reimplementation would just shell out to `tmux` anyway — no win. Keep as-is and call from Python via subprocess.

- **scripts/metasphere-git-hooks** — generates hook scripts that themselves need to be bash (git requires executable hooks; bash is the least-worst choice for a "shell out to metasphere-events" one-liner). The install/uninstall side could be Python but it's small enough (257 lines) that porting is not worth the cost.

- **scripts/metasphere-wake** and **scripts/metasphere-session** `cmd_start` tmux bring-up steps — the respawn loop (`exec bash -c 'while true; do claude ...; done'`) and the readiness poll (`capture-pane \| grep "bypass permissions"`) are inherently shell territory. A Python wrapper that forks tmux commands is strictly worse than a thin bash script.

- **install.sh** (not in scope of this audit but relevant) — stays bash.

- **Generated git hooks** (not scripts/ but emitted by metasphere-git-hooks) — must stay bash.

- **metasphere-identity when sourced** — if the Python rewrite wants to stay inter-op with bash callers during migration, identity resolution needs to still be sourceable. Keep a 10-line bash shim that delegates to the Python impl via stdout-capture, or preserve the existing shim until all bash callers are gone.

Everything else is fair game for Python.
