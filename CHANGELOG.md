# Changelog

## 2026-08-22 — audit draft (metasphere-agents)

_Since 2026-07-20 — 7 commits._

### Fixes

- `10671e3` fix(tmux): confirmed-submit re-fires eaten Enter so unmarked human input lands
- `518a7de` fix(consolidate): drain aged pinned !task/!query so they stop piling up (#25)
- `1af7b05` fix(gateway): make managed-session project_root cwd-independent (kill affordance flicker) (#24)
- `16d00b5` fix(context): cap + de-noise the per-turn Messages section (#23)
- `f1ddc2c` fix(context): de-noise FTS fresh signal + anchor no-hits affordance to project_root (#22)

### Chores

- `e7bb6a9` chore(gitignore): ignore recurring 0-byte /-l junk stray (#20)

### Other

- `1046c6a` Merge pull request #28 from julianfleck/fix/tmux-confirmed-submit-race
