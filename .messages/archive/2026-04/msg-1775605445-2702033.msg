---
id: msg-1775605445-2702033
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T23:44:05Z
read_at:
replied_at:
completed_at:
reply_to: 
---

Implement and end-to-end test the metasphere project management lifecycle, including telegram groups integration. Read docs/PROJECTS.md (already on main as the design spec). Branch on main, ~6-8 commits, tests gated, push at end (per the 'main stays in sync' rule).

DESIGN SPEC

`docs/PROJECTS.md` is the design doc, written and committed earlier today. It covers schema v2, Python API additions, CLI surface, /project slash command, per-turn context injection, and 6 open design questions. The design questions are now answered by sensible defaults (see below); proceed.

DEFAULT ANSWERS to the open design questions in PROJECTS.md:

1. Member roles: free-form text (no enum). Convention but not enforced: lead | developer | reviewer | researcher | contributor.
2. Cross-project membership: yes, an agent can belong to multiple projects. Members are stored per-project; the agent's MISSION.md is the agent's own.
3. Repo cloning: when --repo is given AND --path doesn't exist, auto-clone with a stdout confirmation message ('cloning <url> → <path>'). If --path exists and is a non-empty directory, error out and ask the user to remove --path or omit --repo.
4. Project archive: add 'status: archived' field but DEFER the CLI verb until needed.
5. Linkage to MISSION.md: when add_member is called with --persistent for a never-spawned agent, auto-write a stub MISSION.md derived from the project goal so wake_persistent works. The stub names the project, the role, and the agent_id; the user can edit it later.
6. Daemon-level keepalive: DEFER. Use existing wake idempotency + heartbeat.

SCOPE

This is the full project lifecycle, NOT just CRUD. Cover:

A. **Schema v2 + load/save with v1 fallback** (~1 commit)
   - Extend project.json: add goal, repo, members[], links{}, schema=2.
   - Old v1 projects load with members=[], goal=None, repo=None.
   - Tests: round-trip both schemas, migration on save (v1 read → v2 write).

B. **Member API + CLI** (~1 commit)
   - metasphere/project.py: add_member, remove_member, list_members, wake_members, project_for_scope, get_project (by name OR by inferred-from-cwd path)
   - metasphere/cli/project.py: add subcommands per docs/PROJECTS.md (member add|remove|list, show, wake, for, new). Lazy-import via the unified dispatcher.
   - Tests: add+remove, persistent flag auto-writes MISSION.md, wake_members iterates correctly.

C. **`metasphere project new` rich constructor** (~1 commit)
   - --path, --goal, --repo, --member @x:role[:persistent] (repeatable)
   - --repo auto-clone behavior per default-answer 3.
   - Existing init_project stays (no signature break) — new() wraps it.
   - Tests: new with all flags, new without repo, new with repo + auto-clone (mocked git), new with multiple --member, error on existing-non-empty path.

D. **project_for_scope + per-turn context injection** (~1 commit)
   - metasphere/project.py: walk-up resolver from a given path looking for .metasphere/project.json.
   - metasphere/context.py (the per-turn injection module): add a 'Project: <name>' header with goal, members (with alive/dormant marker), recent activity (last N tasks active, last commit subject if .git/).
   - Tests: nested project, no project, project at scope root, header rendering with empty members.

E. **/project slash command** (~1 commit)
   - .claude/commands/project.md per the design (wizard prompt template, NOT code).
   - Variants: /project (= new wizard), /project new, /project list, /project show <name>, /project wake <name>.
   - The wizard prompt instructs the orchestrator to walk through name, path, goal, members, optional GitHub/Linear in natural language and then run the corresponding metasphere project subcommands. Show each command before running it.
   - No tests (it's a prompt). Manual smoke: invoke /project from a fresh REPL after this lands and verify the prompt expansion.

F. **Telegram groups integration** (~1-2 commits) — THIS IS THE KEY PIECE
   - Each project gets its own Telegram forum topic (created via the existing metasphere/telegram/groups.py API). project.json gains a 'telegram_topic' field with topic_id + topic_name.
   - On `metasphere project new`, if a forum is configured (forum_id file exists), auto-create a topic named after the project. Otherwise the field stays null.
   - Routing: when a project member sends a message via metasphere msg, if the project has a topic_id AND the message scope is inside the project, ALSO mirror to the topic. Don't replace the existing fractal scope routing — additive only.
   - Per-project chat: `metasphere project chat <name> 'message'` sends directly to the project topic.
   - Wake-time: when wake_members fires for a project with a topic, post a 'project waking: members <list>, goal <goal>' announcement to the topic.
   - Tests: create_topic happens iff forum configured, project.json carries topic_id, mirror routing additive (no double-send when scope is non-project), chat command works, wake announcement fires.

G. **End-to-end test** (~1 commit)
   - metasphere/tests/test_project_lifecycle.py: a single integration test that runs the full lifecycle on a tmp_path: project new (with goal, members, persistent flag) → MISSION.md auto-written → list / show → add a task in the project scope → send a message in the project scope → wake_members (mocked tmux) → archive (status flip) → list shows status correctly. All against tmp_path, no real telegram, no real tmux.

CONSTRAINTS

- Tests stay green: `pytest -q` passes after every commit. Add ~25-40 new tests across the 7 layers.
- Use the unified `metasphere` dispatcher — every new subcommand goes through metasphere.cli.project under the existing 'project' prefix.
- Do NOT touch the telegram groups module's public API. If you need to extend it (e.g. lookup by forum-topic name → id), add new functions, don't change signatures.
- Do NOT spawn child agents. This is a single-tranche implementor.
- Push to origin/main at the end (tests green, all 6-8 commits landed).
- Time budget: aggressive. ~1-2 hours.

DELIVERABLES

- ~/.metasphere/agents/@project-impl/report.md with: commit hashes per layer, test count delta (235 → ?), how to invoke /project from the orchestrator REPL once it lands, any deferrals (and why), a worked example of `metasphere project new` for the eventual Recurse project (don't actually create it — just show the command line that would).
- `messages send @.. !done <one-line verdict>` when finished.

Note for context: Julian wants Recurse (recurse.cc, his sense-making substrate on RAGE) to be the first real project under this system. Don't create it as part of this tranche — that's a follow-up. Just make sure the system is shaped so that creating Recurse is the obvious next step.
