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
