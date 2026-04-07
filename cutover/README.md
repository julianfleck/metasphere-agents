# cutover/

Flips the existing bash @orchestrator session onto the new
`metasphere/` Python harness. Reversible.

## Apply

```
bash /home/openclaw/Code/metasphere-agents/cutover/apply.sh
```

What it touches:

- `~/.metasphere/bin/<name>` for every CLI listed in `apply.sh` — backs up
  the existing script to `~/.metasphere/bin.backup-cutover-<DATE>/` and
  replaces it with a one-line shim that `exec`s
  `python -m metasphere.cli.<module>`.
- `~/.config/systemd/user/metasphere-heartbeat.service` ExecStart →
  `python -m metasphere.cli.heartbeat daemon 300`
- `~/.config/systemd/user/metasphere-telegram.service` ExecStart →
  `python -m metasphere.cli.telegram poll`
- `~/.config/systemd/user/metasphere-schedule.service` ExecStart →
  `python -m metasphere.cli.schedule daemon`
- `systemctl --user daemon-reload` + restart of those three units.
- `/home/openclaw/Code/metasphere-agents/.claude/settings.local.json`
  Stop hook → `python -m metasphere.cli.posthook`,
  UserPromptSubmit hook → `python -m metasphere.cli.context`.

The original ExecStart lines and `settings.local.json` are saved inside
the same backup directory so `rollback.sh` is a single command.

## Rollback

```
bash /home/openclaw/Code/metasphere-agents/cutover/rollback.sh
```

By default it uses the most recent `~/.metasphere/bin.backup-cutover-*`
directory; pass an explicit path as `$1` to pin a specific snapshot.

## Post-merge follow-up: retire `~/.metasphere/bin/` shims

`apply.sh` installs thin shell shims at `~/.metasphere/bin/metasphere-*`
that just `exec` into the venv binaries. Now that the package is
installed via `pip install -e .` and the entry points (declared in
`pyproject.toml [project.scripts]`) put `metasphere-*` directly on
`PATH`, these shims are vestigial.

After `python-rewrite` merges to `main` and the cutover has been stable
for a release cycle, the shim directory can be removed:

```
rm -rf ~/.metasphere/bin
# (and drop the install step from cutover/apply.sh)
```

The shims live OUTSIDE the repo so they were intentionally NOT touched
in the cleanup-final pass.
