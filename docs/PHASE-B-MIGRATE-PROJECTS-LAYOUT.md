# Phase B: Migrate metasphere-agents + rage-substrate to ~/projects/

This is the dangerous half of the project-layout cleanup. Phase A
(commit `29b3d28`) moved 16 legacy repos out of `~/.openclaw/workspace/`
and `~/repos/` into `~/projects/`. This phase finishes the job by
moving the two repos that couldn't be touched live: **metasphere-agents
itself** (because the orchestrator REPL was running inside it) and
**rage-substrate** (because it hosts the metasphere CLI venv that
systemd is launching).

It is intended to be run by an **external agent** (Wintermute SSH'd
into Mochi as `openclaw`) so that the local orchestrator REPL can be
torn down and respawned cleanly without the running session having to
self-terminate mid-migration.

---

## Pre-flight

Run from your laptop / Wintermute, SSH'd into Mochi:

```
ssh openclaw@<mochi-host>
```

Confirm the starting state matches expectations:

```
test -d ~/Code/metasphere-agents
test -d ~/.openclaw/workspace/repos/rage-substrate
test -d ~/projects   # should have ~16 entries from Phase A
test ! -e ~/projects/metasphere-agents
test ! -e ~/projects/rage-substrate
ls ~/.metasphere/projects.json   # registry; will be rewritten
```

If any of those fail, stop and report — Phase A may not have run
cleanly, or someone has already moved things by hand.

Also confirm there is no in-flight work that depends on a stable
orchestrator session:

```
git -C ~/Code/metasphere-agents status              # should be clean
systemctl --user is-active metasphere-gateway       # active
tmux list-sessions | grep metasphere-orchestrator   # one session
```

If `git status` is dirty, commit or stash first. The orchestrator
session will be stopped and respawned at the end, so any unsaved REPL
state is lost — that's expected.

---

## Phase B steps

### 1. Stop all metasphere systemd services

```
systemctl --user stop \
  metasphere-gateway.service \
  metasphere-heartbeat.service \
  metasphere-schedule.service
```

Verify they're stopped:

```
systemctl --user is-active metasphere-gateway metasphere-heartbeat metasphere-schedule
# expect: inactive inactive inactive
```

### 2. Kill the orchestrator tmux session

```
tmux kill-session -t metasphere-orchestrator 2>/dev/null || true
```

The current claude code REPL inside it dies here. If Julian was talking
to it, that conversation ends. The session will be respawned in step 7.

### 3. Move metasphere-agents

```
mv ~/Code/metasphere-agents ~/projects/metasphere-agents
rmdir ~/Code 2>/dev/null || true
```

### 4. Move rage-substrate

`rage-substrate` hosts the metasphere CLI venv at
`.venv/bin/metasphere`, which is what systemd's `ExecStart` points at.
Moving it requires updating systemd in lockstep (next step).

```
mv ~/.openclaw/workspace/repos/rage-substrate ~/projects/rage-substrate
```

### 5. Update systemd unit files

Six unit files reference the old paths. Patch them in place:

```
cd ~/.config/systemd/user/
sed -i \
  -e 's|/home/openclaw/Code/metasphere-agents|/home/openclaw/projects/metasphere-agents|g' \
  -e 's|/home/openclaw/\.openclaw/workspace/repos/rage-substrate|/home/openclaw/projects/rage-substrate|g' \
  metasphere-gateway.service \
  metasphere-heartbeat.service \
  metasphere-schedule.service \
  metasphere-telegram.service \
  rage-server.service \
  rage-bridge.service
```

Verify the patched paths look right:

```
grep -E "ExecStart|Environment=METASPHERE" ~/.config/systemd/user/metasphere-gateway.service
# expect both to point at ~/projects/...
```

Reload systemd:

```
systemctl --user daemon-reload
```

### 6. Update everything else that references the old paths

#### a) `~/.metasphere/bin/` symlinks

These shims point INTO the metasphere-agents repo. After the move,
every symlink is broken. Re-run the installer from the new location to
rebuild them — this is the cleanest path:

```
cd ~/projects/metasphere-agents
./install.sh --non-interactive
```

The installer will:
- Re-symlink `~/.metasphere/bin/{metasphere,tasks,messages,...}` to
  the new `~/projects/metasphere-agents/scripts/*` paths
- Write systemd unit files (idempotent — your sed patches above will
  be overwritten by the installer template, which now uses
  `$SCRIPT_DIR` and resolves to the new path automatically)
- Reload systemd-user
- NOT touch `~/.metasphere/agents/`, `messages/`, `projects/`,
  `schedule/`, `telegram/`, `state/`, `traces/`, `events/`, or `config/`

If you'd rather skip the installer and do it manually:

```
ln -sfn ~/projects/metasphere-agents/scripts/metasphere     ~/.metasphere/bin/metasphere
ln -sfn ~/projects/metasphere-agents/scripts/tasks          ~/.metasphere/bin/tasks
ln -sfn ~/projects/metasphere-agents/scripts/messages       ~/.metasphere/bin/messages
ln -sfn ~/projects/metasphere-agents/scripts/metasphere-fts ~/.metasphere/bin/metasphere-fts
# ...same for every shim in ~/.metasphere/bin/ that resolves into the repo
```

The installer is the safer call.

#### b) `~/.metasphere/schedule/jobs.json`

The schedule daemon has 4 hardcoded openclaw paths. Patch them:

```
sed -i \
  -e 's|/home/openclaw/\.openclaw/workspace/repos/rage-substrate|/home/openclaw/projects/rage-substrate|g' \
  -e 's|/home/openclaw/Code/metasphere-agents|/home/openclaw/projects/metasphere-agents|g' \
  ~/.metasphere/schedule/jobs.json
```

Verify:

```
grep -E "(openclaw.workspace|Code/metasphere)" ~/.metasphere/schedule/jobs.json
# expect: no output
```

#### c) `~/.metasphere/projects.json` registry

Update the metasphere-agents entry:

```
python3 -c "
import json
from pathlib import Path
p = Path.home() / '.metasphere' / 'projects.json'
data = json.loads(p.read_text())
for entry in data:
    if entry['name'] == 'metasphere-agents':
        entry['path'] = '/home/openclaw/projects/metasphere-agents'
p.write_text(json.dumps(data, indent=2) + '\n')
print('updated registry')
"
```

Also create the per-project metadata file:

```
mkdir -p ~/.metasphere/projects/metasphere-agents
cat > ~/.metasphere/projects/metasphere-agents/project.json <<'EOF'
{
  "schema": 2,
  "name": "metasphere-agents",
  "path": "/home/openclaw/projects/metasphere-agents",
  "created": "2026-04-08T11:01:53Z",
  "status": "active",
  "goal": "Self-improving agent harness — repo is both framework and first test subject.",
  "repo": "https://github.com/julianfleck/metasphere-agents",
  "members": [
    {"id": "@julian", "role": "owner", "persistent": true},
    {"id": "@orchestrator", "role": "lead", "persistent": true}
  ],
  "links": {},
  "telegram_topic": null
}
EOF
```

And the same for rage-substrate (Phase A skipped it):

```
mkdir -p ~/.metasphere/projects/rage-substrate
cat > ~/.metasphere/projects/rage-substrate/project.json <<'EOF'
{
  "schema": 2,
  "name": "rage-substrate",
  "path": "/home/openclaw/projects/rage-substrate",
  "created": "2026-04-08T19:35:00Z",
  "status": "active",
  "goal": "RAGE substrate — recurse engine; currently also hosts the metasphere CLI venv.",
  "repo": null,
  "members": [{"id": "@julian", "role": "owner", "persistent": true}],
  "links": {},
  "telegram_topic": null
}
EOF

python3 -c "
import json
from pathlib import Path
p = Path.home() / '.metasphere' / 'projects.json'
data = json.loads(p.read_text())
data.append({
    'name': 'rage-substrate',
    'path': '/home/openclaw/projects/rage-substrate',
    'registered': '2026-04-08T19:35:00Z',
})
p.write_text(json.dumps(data, indent=2) + '\n')
"
```

#### d) Move/rename the auto-memory dir for the moved cwd

Claude Code keys `~/.claude/projects/<dir>/memory/` by a hash of the
project's cwd. After moving metasphere-agents, the running orchestrator
session will create a new dir at the new path and the old memory files
become orphaned.

Preserve them:

```
OLD=~/.claude/projects/-home-openclaw-Code-metasphere-agents
NEW=~/.claude/projects/-home-openclaw-projects-metasphere-agents
if [ -d "$OLD" ] && [ ! -e "$NEW" ]; then
  mv "$OLD" "$NEW"
  echo "renamed claude project dir"
elif [ -d "$OLD" ] && [ -d "$NEW" ]; then
  # both exist — merge memory/ from old into new, leave the rest
  if [ -d "$OLD/memory" ]; then
    mkdir -p "$NEW/memory"
    cp -rn "$OLD/memory"/* "$NEW/memory/" 2>/dev/null
  fi
  echo "merged memory dir; old session jsonls left in place"
fi
```

This preserves `MEMORY.md` and the per-fact `*.md` files from
`~/.claude/projects/-home-openclaw-Code-metasphere-agents/memory/`.

#### e) Drop the in-repo `.metasphere/project.json`

Per Julian's rule that `~/.metasphere/projects/<name>/project.json`
is the canonical metadata location, the old in-repo copy is debt:

```
cd ~/projects/metasphere-agents
git rm .metasphere/project.json
git commit -m "metasphere: drop in-repo project.json (canonical lives in ~/.metasphere/projects/)"
```

Don't push yet — there may be other cleanup commits in the same
batch (next step).

### 7. Restart everything

```
systemctl --user start \
  metasphere-gateway.service \
  metasphere-heartbeat.service \
  metasphere-schedule.service

systemctl --user is-active metasphere-gateway metasphere-heartbeat metasphere-schedule
# expect: active active active
```

The gateway daemon will respawn the orchestrator tmux session on its
next iteration (it calls `ensure_session` at boot). Verify:

```
sleep 5
tmux list-sessions | grep metasphere-orchestrator
# expect: metasphere-orchestrator: 1 windows ...
```

If that doesn't appear, check:

```
journalctl --user -u metasphere-gateway -n 50 --no-pager
```

### 8. Smoke test

From inside the new repo path:

```
cd ~/projects/metasphere-agents
git status                            # clean (or just the .metasphere/project.json deletion)
~/.metasphere/bin/metasphere status   # should run, show new repo path
~/.metasphere/bin/tasks list          # should list tasks under the new path
python -m pytest metasphere/tests/ -x -q   # 352 passing
```

Send a test telegram message TO the bot from your phone. It should:
- Get a 👀 reaction within ~3s (gateway poller alive)
- Get an orchestrator response
- Have its 👀 replaced with 👍 once the response lands

If all four checks pass, the migration is good.

### 9. Retire ~/.openclaw

Now that nothing in `~/.metasphere/` or `~/projects/` references
`~/.openclaw/`, rename it for safekeeping:

```
mv ~/.openclaw ~/.openclaw-backup
rm ~/workspace 2>/dev/null  # the symlink is now pointing at a stale path
```

Final sanity check — nothing under ~/.metasphere should still mention
the old paths:

```
grep -r "Code/metasphere-agents\|openclaw/workspace" \
  ~/.config/systemd/user/ \
  ~/.metasphere/bin/ \
  ~/.metasphere/config/ \
  ~/.metasphere/schedule/ \
  ~/.metasphere/projects.json 2>/dev/null
# expect: no output
```

### 10. Commit + push

```
cd ~/projects/metasphere-agents
git push   # the project.json deletion commit from step 6e
```

Send Julian a telegram confirming Phase B is done, with: the new
HEAD commit, the four smoke-test results, and any caveats.

---

## Rollback

If anything in steps 3–7 fails and the system is unrecoverable, the
rollback is mostly mechanical:

```
# Stop services
systemctl --user stop metasphere-gateway metasphere-heartbeat metasphere-schedule
tmux kill-session -t metasphere-orchestrator 2>/dev/null

# Move things back
mv ~/projects/metasphere-agents ~/Code/metasphere-agents
mkdir -p ~/.openclaw/workspace/repos
mv ~/projects/rage-substrate ~/.openclaw/workspace/repos/rage-substrate

# Revert systemd patches (you wrote them with sed; reverse the substitutions)
sed -i \
  -e 's|/home/openclaw/projects/metasphere-agents|/home/openclaw/Code/metasphere-agents|g' \
  -e 's|/home/openclaw/projects/rage-substrate|/home/openclaw/.openclaw/workspace/repos/rage-substrate|g' \
  ~/.config/systemd/user/metasphere-*.service \
  ~/.config/systemd/user/rage-*.service
systemctl --user daemon-reload

# Revert schedule jobs.json the same way
sed -i \
  -e 's|/home/openclaw/projects/metasphere-agents|/home/openclaw/Code/metasphere-agents|g' \
  -e 's|/home/openclaw/projects/rage-substrate|/home/openclaw/.openclaw/workspace/repos/rage-substrate|g' \
  ~/.metasphere/schedule/jobs.json

# Rebuild bin shims pointing back at the old paths
cd ~/Code/metasphere-agents && ./install.sh --non-interactive

# Restart
systemctl --user start metasphere-gateway metasphere-heartbeat metasphere-schedule
```

---

## What this doc deliberately doesn't touch

- **`~/skills/`** — separate cleanup. The legacy openclaw skills there
  may or may not be portable to `~/.claude/skills/`. Investigate
  per-skill before migrating.
- **`~/.cam/`** — kept (CAM is still in use)
- **`~/.agents/`** — flagged as suspicious; investigate origin before
  deleting
- **Top-level scratch files in `~/`** (`bench*.py`, `test_ws*.py`, etc.)
  — leave alone, they're not the harness's problem
- **The 17th project entry** in `~/.metasphere/projects.json` for
  `metasphere-agents` — just changes path; no schema change
- **`~/Code/.messages/` scope leak** — Phase A already deleted it; no
  action needed

---

## Notes for the executor

- Run **everything as the `openclaw` user**, not root.
- All paths in this doc are absolute or `$HOME`-rooted, no `~` shortcuts
  inside heredocs (those don't expand inside single-quoted heredocs).
- The installer at step 6a assumes the `--non-interactive` flag exists.
  If it doesn't, run `./install.sh </dev/null` or just answer the
  prompts. Either is fine.
- If the orchestrator session doesn't respawn after step 7, the gateway
  daemon's `ensure_session` may need a kick:
  `systemctl --user restart metasphere-gateway` and re-check.
- Don't push the commit from step 6e until **after** the smoke tests
  in step 8 pass. If you push first and tests fail, you've made the
  rollback messier than it needs to be.
- Estimated wall time: 5–10 minutes including verification. The
  longest single step is `pytest`.
