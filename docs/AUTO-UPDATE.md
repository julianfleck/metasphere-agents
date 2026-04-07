# Auto-Updates

Hosts running metasphere (spot, bean, future) keep themselves current with
`origin/main` without manual intervention. The mechanism is:

1. A config file (`~/.metasphere/config/auto-update.env`) controls behavior.
2. A cron job (`metasphere-auto-update` in `~/.metasphere/schedule/jobs.json`)
   fires `metasphere update --quiet` on the configured cadence.
3. `metasphere update` pulls main, re-pip-installs the python package if
   changed, runs the test gate, restarts the gateway daemon, and sends a
   telegram notification on success/failure.

## Quickstart

```bash
# Status
metasphere update --status

# Enable (writes config + registers cron job)
metasphere update --enable

# Disable
metasphere update --disable

# Force a one-shot update right now
metasphere update              # chatty
metasphere update --quiet      # logs to ~/.metasphere/logs/auto-update.log

# Reinstall the cron entry from current config
metasphere update --register-job
```

## Configuration

`~/.metasphere/config/auto-update.env`:

| Key | Default | Notes |
|---|---|---|
| `AUTO_UPDATE_ENABLED` | `true` (fresh installs) / `false` (existing) | Master switch. Affects `enabled` flag of the cron job. |
| `AUTO_UPDATE_INTERVAL` | `daily` | One of `daily` (4am), `hourly`, `6h`, or a literal 5-field cron expression (e.g. `*/15 * * * *`). |
| `AUTO_UPDATE_BRANCH` | `main` | Git branch to track. Override is a footgun — see Security. |
| `AUTO_UPDATE_RESTART_DAEMONS` | `true` | If false, the gateway daemon is not restarted after update. |
| `AUTO_UPDATE_NOTIFY` | `true` | Send a telegram message on every update with commit subjects (success) or reason (failure). Suppressed when no commits applied. |

## What runs on each tick

The cron job dispatches `metasphere update --quiet`, which:

1. Records `git rev-parse HEAD` (the "from" hash).
2. Calls `bash scripts/metasphere update` — owns the `git pull --ff-only`
   (with `reset --hard origin/main` fallback), the symlink/copy of scripts
   into `~/.metasphere/bin/`, and the gateway/daemon restart.
3. Re-runs `pip install -e .` if `pyproject.toml` or any `metasphere/*.py`
   changed since the "from" hash.
4. Runs `pytest -m 'not live' -q` (when wired through the python entry).
   On failure: skips the daemon restart and sends a telegram alert. The
   host is left on the new hash but daemons stay on the previous code
   path until the human intervenes.
5. Writes `~/.metasphere/state/auto-update.state.json` with last result.
6. Sends a telegram notification if `AUTO_UPDATE_NOTIFY=true`.

## Logs and state

| Path | Purpose |
|---|---|
| `~/.metasphere/logs/auto-update.log` | Rolling log of every `--quiet` run. |
| `~/.metasphere/state/auto-update.state.json` | Last run timestamp + last `UpdateResult` (ok, hashes, commit count, reason). |
| `~/.metasphere/schedule/jobs.json` | Cron registry. The auto-update entry has `id=metasphere-auto-update`. |

## Bean VM example

Once wintermute finishes installing metasphere on the bean VM:

```bash
ssh bean
metasphere update --enable
metasphere update --register-job   # idempotent; install.sh already does this
metasphere update --status
```

The first scheduled tick lands at 04:00 local. To force an immediate run:

```bash
metasphere update
```

## Rollback

```bash
metasphere update --disable        # stop the cron job
cd ~/Code/metasphere-agents        # or wherever the install source lives
git reset --hard <known-good-hash>
metasphere update                  # re-runs scripts/pip install on the
                                   # rolled-back tree
```

State (`auto-update.state.json`) holds the previous good hash for
reference.

## Security

Auto-update pulls from `origin main` of whatever git remote was cloned at
install time. **If origin is hijacked, the host is compromised.**
Mitigations:

- Pin the SSH `known_hosts` entry for github.com (or whichever origin) at
  install time. Future installer work will fail-safe if the host key
  rotates.
- `AUTO_UPDATE_BRANCH` allows pointing the host at any branch — useful
  for staging, dangerous in production. Only override on hosts you
  control end-to-end.
- The test gate is the last line of defense: a broken main does not
  restart the daemons. Watch the telegram alerts.
