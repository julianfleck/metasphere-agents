# OPS

Host-level operational procedures for the metasphere harness.
Everything in this file is reproducible on any `systemd --user`-
capable host. Commands assume the repo is checked out at
`~/projects/metasphere-agents/`.

## Reaper: `npm root -g` zombies

Claude Code occasionally spawns `npm root -g` which hangs and
accumulates. The reaper is a systemd user oneshot driven by a 60s
timer that kills any `npm root -g` process whose elapsed time
exceeds 60s.

### Files

- `scripts/metasphere-reaper` — the killer (bash)
- `systemd/user/metasphere-reaper.service` — oneshot unit
- `systemd/user/metasphere-reaper.timer` — timer (every 60s)
- `scripts/test_metasphere_reaper.sh` — functional test

### Install

```bash
cd ~/projects/metasphere-agents
mkdir -p ~/.config/systemd/user
cp systemd/user/metasphere-reaper.service ~/.config/systemd/user/
cp systemd/user/metasphere-reaper.timer   ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now metasphere-reaper.timer
```

Verify:

```bash
systemctl --user list-timers metasphere-reaper.timer
systemctl --user status metasphere-reaper.timer
```

### Observe kills

Each run logs one line with an ISO timestamp and the count of PIDs
killed, to both the user-readable log file and the systemd journal:

- Log file (always readable by the running user):
  `~/.metasphere/logs/reaper.log`
- Journal: `journalctl --user -u metasphere-reaper.service`
  (requires journal read access — on some containers the user is
  not in the `systemd-journal` group, in which case the log file is
  authoritative)

Example line:

```
2026-04-21T13:05:00Z metasphere-reaper killed=2 pids=12345 12390
2026-04-21T13:06:00Z metasphere-reaper killed=0 pids=
```

Per-minute histogram of kill counts:

```bash
awk '/killed=/ {split($0, a, "killed="); split(a[2], b, " ");
     print substr($1,1,16), b[1]}' ~/.metasphere/logs/reaper.log
```

### Run the functional test

The test starts a sleep stub whose argv[0] is `npm root -g`, waits
65 seconds for its elapsed time to exceed the threshold, triggers
the reaper manually (to de-race the timer cadence), and asserts
the stub is dead and a journal line recorded the kill.

```bash
scripts/test_metasphere_reaper.sh
```

### Uninstall

```bash
systemctl --user disable --now metasphere-reaper.timer
rm ~/.config/systemd/user/metasphere-reaper.service
rm ~/.config/systemd/user/metasphere-reaper.timer
systemctl --user daemon-reload
```

### Tuning

`REAPER_THRESHOLD` (seconds) can be overridden via
`Environment=REAPER_THRESHOLD=30` in a drop-in at
`~/.config/systemd/user/metasphere-reaper.service.d/override.conf`.

## Agent session lifecycle

The gateway daemon keeps two hygiene mechanisms running so idle or
completed agents don't leave stale tmux sessions and runtime state
behind.

### Ephemeral !done cleanup

Hook point: `metasphere.messages.send_message` → invokes
`metasphere.agents.on_done_delivered(sender)` whenever the message
`label == "!done"`. This is the narrowest correct point: `!done` is
the single terminal signal in the inter-agent protocol, and the
sender is explicit (`from_agent`).

When the sender is an **ephemeral** agent (no `MISSION.md` in its
identity directory), the hook:

1. Runs `tmux kill-session -t metasphere-<sender>`. Silent no-op when
   the session is absent — ephemerals spawned via headless
   `claude -p` have no tmux pane in the first place.
2. Removes runtime pointers: `pid`, `task_id`. A future
   `metasphere agent spawn <same-name>` re-bootstraps clean.
3. Writes `status = "complete: !done delivered"`. The existing
   `metasphere.posthook.auto_close_finished_task` sweep picks this up
   and archives the backing task.
4. Preserves identity / contract artifacts: `harness.md`,
   `authority`, `responsibility`, `accountability`, `scope`,
   `parent`, `spawned_at`, `task`. These remain available for GC
   logs and later `metasphere agent contract` audits.

When the sender is a **persistent** agent (has `MISSION.md`), the
hook is a strict no-op — persistent lifecycle is governed by the
idle-TTL sweep below, not by individual `!done` messages.

Failures inside the hook are swallowed: the `!done` message is
already written, indexed, and mirrored to telegram before the hook
fires, so a bad tmux probe can never break delivery.

### Persistent idle-TTL dormancy

Hook point: `metasphere.gateway.daemon.run_daemon` loop — calls
`metasphere.agents.reap_dormant(paths, max_idle_seconds)` on the
`dormancy_interval` cadence (default 300s). Separate from the 5s
watchdog tick because sweeping tmux for every agent is more expensive
than the stuck-paste / safety-hooks checks and there is no signal
that would benefit from finer cadence.

`reap_dormant` walks every persistent agent, probes the tmux
`session_activity` timestamp, and for any session idle longer than
`dormancy_max_idle_seconds` (default 86400s = 24h):

1. Writes `status = "dormant: idle <N>s (auto-ttl at <utc>)"` to the
   agent's identity directory so `metasphere status` and a human
   reader see why the session went away.
2. Runs `tmux kill-session -t <session>`.
3. Emits an `agent.dormant` event with the idle duration and session
   name.

Persona and contract files (`MISSION.md`, `SOUL.md`,
`LEARNINGS.md`, `HEARTBEAT.md`, `authority`, `responsibility`,
`accountability`, `harness.md`) are preserved — a subsequent
`metasphere agent wake <name>` restarts cleanly from them.

### Tuning

Both cadences are injection points on `run_daemon`; override at
daemon start time:

- `dormancy_interval` — how often `reap_dormant` is called (seconds)
- `dormancy_max_idle_seconds` — per-agent idle threshold (seconds)

For testing, `reap_dormant_fn` can be injected directly (see the
`test_run_daemon_reap_dormant_fires_on_interval` gateway test).
