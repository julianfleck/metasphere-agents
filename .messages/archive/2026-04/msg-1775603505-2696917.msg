---
id: msg-1775603505-2696917
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T23:11:45Z
read_at:
replied_at:
completed_at:
reply_to: 
---

Read-only audit: is the metasphere-agents repo ready for public publication on github.com/julianfleck/metasphere-agents? It already exists publicly but Julian wants to know if the current state is in shape to actively promote / share. Time budget: 15min. NO commits, NO spawns.

CHECKLIST

1. **License**: there is no LICENSE / COPYING file in the repo root. Block? Recommendation?

2. **Hardcoded personal data**:
   - LIVE_CHAT_ID = '228838013' in metasphere/tests/test_integration{,_2}.py — Julian's actual telegram chat id. Public users would have his id baked in. **Strong block.**
   - test_context.py fixture mentions @j0lian by name. Probably fine (it's a fixture username) but flag for awareness.
   - cutover/apply.sh, cutover/rollback.sh, cutover/README.md, docs/PROJECTS.md all hardcode '/home/openclaw/Code/metasphere-agents'. These should be parameterized ($REPO_ROOT or detect from script location).
   - Are there other paths/usernames/tokens leaking? grep `/home/openclaw/`, `openclaw@`, `j0lian`, hostnames, IPs, etc.

3. **README quality**: README.md is 289 lines — does it cover (a) what the project IS, (b) install path that works for a stranger, (c) quickstart, (d) architecture diagram or layout, (e) contributing guidelines, (f) prerequisites (claude-code, python 3.11+, tmux, telegram bot setup). What's missing?

4. **Install path for a fresh stranger**:
   - Read install.sh top to bottom. Does it work on a fresh Debian/Ubuntu VM with NO openclaw history?
   - Does it require any tokens/secrets at install time, or can a user defer telegram setup?
   - The openclaw migration code: gracefully no-ops on hosts without ~/.openclaw, or does it crash?
   - Fresh-install smoke: `./install.sh` on a tmp_path simulation? Don't actually execute on this host — just static-read it.

5. **Tests for a stranger**: `pytest -m 'not live'` from a clean clone — does it pass? What does it require beyond stdlib? (We use stdlib + pytest + croniter, I think.) Any tests that depend on this host's state?

6. **Secrets**: grep the repo for token-shaped strings. Anything matching `ghp_`, `sk-or-`, bot tokens (numeric:alphanumeric), `AAGy`, `AAFs`. Do NOT print actual tokens in your report — just file:line + a redacted hash. The systemd unit drop-ins on this host have several API keys but those live in ~/.config/, NOT in the repo — confirm separation.

7. **docs/ quality**: CLI.md, PORTING.md, AUTO-UPDATE.md, WINTERMUTE-mochi-install.md, PROJECTS.md (uncommitted draft). PORTING.md is internal-audit-flavored ('what should be ported from bash'). Should it be moved to docs/internal/ or kept? CLI.md is user-facing — is it complete after the cli-unify rewrite? Does it document the unified `metasphere <subcommand>` form, or still the legacy metasphere-* shims?

8. **Sensitive files in git**: anything in `git ls-files` that shouldn't be public? Lookout for .env, *.key, anything under config/, anything that looks like a secret file. (The .gitignore already excludes settings.local.json.)

9. **CHANGELOG.md / version**: pyproject says version='0.1.0'. Is there a CHANGELOG entry for the python rewrite? Does it announce that 0.1.0 is the python harness, not the bash one?

10. **Fresh-clone smoke (paper, not actual clone)**: walk through what a fresh user would do:
    `git clone … && cd metasphere-agents && ./install.sh && metasphere status`. At each step, what could break? What's the failure mode?

11. **Project name + branding consistency**: the repo is 'metasphere-agents' but the CLI is 'metasphere', the runtime is '~/.metasphere/', commits say 'metasphere'. README says 'Metasphere Agents'. Is the naming consistent enough that a stranger isn't confused?

12. **CLAUDE.md / MISSION.md / SOUL.md**: these are checked into the repo for the orchestrator to read. They contain Julian-specific operational instructions ('Heartbeat Turn Etiquette', 'Memory Hygiene', 'Legacy OpenClaw Integration'). Are they appropriate for public consumption? Or should they be moved to a private overlay?

OUTPUT

A single ~/.metasphere/agents/@publish-audit/report.md with:

- One-line verdict at top: 'publish-ready / blockers / not-ready'
- BLOCKERS section: anything that MUST be fixed before publication (LICENSE, LIVE_CHAT_ID, hardcoded /home/openclaw/, exposed secrets). Each with file:line and the proposed fix.
- WARNINGS section: things that should be fixed but won't embarrass on day 1 (docs gaps, naming inconsistencies, internal-audit docs in user-facing tree).
- RECOMMENDATIONS section: stuff for a v0.2 polish pass.
- A 'fresh-stranger-walkthrough' transcript of what `git clone && ./install.sh && metasphere status` would actually do, with the failure modes annotated.
- One-line verdict repeated at bottom.

When done: `messages send @.. !done <verdict>`. NO fixes — diagnosis only.
