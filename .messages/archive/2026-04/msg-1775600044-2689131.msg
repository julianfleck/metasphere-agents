---
id: msg-1775600044-2689131
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T22:14:04Z
read_at:
replied_at:
completed_at:
reply_to: 
---

Add configurable auto-updates for metasphere. The goal: hosts running metasphere (spot, soon bean, future) keep themselves current with main without manual intervention. Branch directly on main, 3-4 commits, no push, tests gated.

WHAT EXISTS

`metasphere update` already exists as a bash subcommand in ~/.metasphere/bin/metasphere (and forwards to scripts/metasphere). Read scripts/metasphere lines 692-790 — it git pulls --ff-only with reset --hard origin/main fallback, copies scripts to ~/.metasphere/bin/, restarts gateway/daemon. It does NOT currently re-pip-install the python package, which it should after the cutover (`pip install -e` from the venv if pyproject.toml changed). Fix that as part of this tranche.

WHAT TO ADD

1. **Config flag** at ~/.metasphere/config/auto-update.env:
   AUTO_UPDATE_ENABLED=true|false           # default false on existing installs, true on fresh installs
   AUTO_UPDATE_INTERVAL=daily|hourly|6h     # default daily
   AUTO_UPDATE_BRANCH=main                  # default main; allow override for testing
   AUTO_UPDATE_RESTART_DAEMONS=true|false   # default true; lets users opt out of daemon restart
   AUTO_UPDATE_NOTIFY=true|false            # default true; sends a telegram message on success/failure

2. **Scheduler integration**: register the update as a job in ~/.metasphere/cron/jobs.json (or wherever metasphere schedule reads its jobs). Cron expression derived from AUTO_UPDATE_INTERVAL:
   - daily   → '0 4 * * *'   (4am local — least likely to interrupt human work)
   - hourly  → '0 * * * *'
   - 6h      → '0 */6 * * *'
   - custom  → if AUTO_UPDATE_INTERVAL looks like a cron expression, use it verbatim
   The job command should call `metasphere update --quiet` (which you'll add as a flag — quiet mode skips the chatty git output and only logs to ~/.metasphere/logs/auto-update.log).

3. **Update wrapper enhancements** in scripts/metasphere update branch:
   - --quiet flag (logs to ~/.metasphere/logs/auto-update.log, no stdout chatter)
   - After git pull, detect if pyproject.toml or any metasphere/*.py changed; if yes, run `<venv>/python -m pip install -e .` to re-register console scripts
   - After update, run `pytest -m 'not live' -q` to verify; if it fails, log + skip the daemon restart + send telegram alert (DON'T leave the host on broken main)
   - Telegram notification on success: 'auto-update: spot now at <new-hash>, was <old-hash>, N commits applied: <subjects>'
   - Telegram notification on failure: 'auto-update FAILED on spot: <reason>, last good <hash>, daemons NOT restarted'

4. **Install integration**: install.sh writes a default auto-update.env with AUTO_UPDATE_ENABLED=true on FRESH installs (i.e. when ~/.metasphere/config/auto-update.env doesn't exist). On existing installs leave the existing setting alone. install.sh also calls `metasphere update --register-job` (new flag) which installs/updates the cron job from the current config.

5. **Manual control commands**:
   metasphere update                    # one-shot, current behavior + the new --quiet/install/test logic
   metasphere update --enable           # write AUTO_UPDATE_ENABLED=true to auto-update.env, register job
   metasphere update --disable          # write AUTO_UPDATE_ENABLED=false, unregister job
   metasphere update --status           # print current config + last update time + last update result
   metasphere update --register-job     # install/refresh the cron entry from current config (used by install.sh)

CONSTRAINTS

- Tests stay green (216 passed, 4 deselected). Add tests for the new config parsing + job registration + dry-run update flow. Tests live under metasphere/tests/test_update.py (new file).
- Use the unified `metasphere` dispatcher — the update logic should land in a python module under metasphere/update.py with metasphere/cli/update.py as the dispatcher entry. The bash branch in scripts/metasphere can stay as a fallback shim that just exec'd the python entry, OR be retired (your call — if retiring, add the full bash implementation to metasphere/update.py first).
- Do NOT actually trigger an auto-update during testing. Use a tmp_path fixture for the venv and monkeypatch git/pip calls.
- One commit per logical layer: 1=auto-update.env config + parser, 2=metasphere/update.py + python port of bash logic, 3=schedule integration + --register-job, 4=install.sh wiring + tests + docs.
- Time budget: aggressive. If the bash → python port hits an edge case, leave the bash version alone in scripts/metasphere and just have the python entry call out to it via subprocess.

SECURITY

- Auto-update pulls from origin main of whatever git remote the install was cloned from. If origin is hijacked, the host is compromised. Mitigation: pin the SSH known_hosts entry for github.com (or whatever the origin is) at install time, fail-safe if the host key changes. Document this in docs/AUTO-UPDATE.md.
- AUTO_UPDATE_BRANCH override is a footgun (could point at an attacker branch). Document but don't restrict — it's useful for staging.

DOCS

- docs/AUTO-UPDATE.md: how to enable, disable, check status, and roll back. Include the bean VM example (metasphere update --enable + --register-job after wintermute finishes the install).

When done: write ~/.metasphere/agents/@auto-update/report.md with commit hashes, the new config schema, the job registration sample, and any deferred items. `messages send @.. !done <one-line verdict>`.
