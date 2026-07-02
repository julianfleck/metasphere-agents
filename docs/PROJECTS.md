# Projects + persistent agent groups

Per-project memory store and team membership, as shipped. For the
operator-level walkthrough see `~/.metasphere/CLAUDE.md` (installed
from `templates/install/CLAUDE.md`); for the live CLI surface see
[`CLI.md`](./CLI.md).

## Per-project memory

Every project carries two team-shared memory files alongside its
metadata:

| File | Holds |
|---|---|
| `~/.metasphere/projects/<name>/LEARNINGS.md` | What the team learned: incidents, lessons, debugging insights. |
| `~/.metasphere/projects/<name>/MEMORY.md` | What the team knows: facts, configs, references, ongoing state. |

These are the **primary memory store** for everyone on the team. The
per-turn context hook renders a recency-sorted window of both files
into each member's context block; the window's footer cites the
absolute on-disk path so an agent can `Read` or `grep` the full file
when uncertain.

### Capsule shape

The context hook (`metasphere/context.py::_render_project_capsule`)
emits, per resolved project:

```
## Project: <name>

### LEARNINGS

<newest entries by date desc, within byte budget>

_(N more entries omitted by recency. Full file: <absolute path> — Read or grep for older.)_

### MEMORY

<newest entries by date desc, within byte budget>

_(Full file: <absolute path> — Read or grep for older entries.)_
```

Recency sort anchors on a `YYYY-MM-DD` token in each entry's
`##` / `###` header; entries without a parseable date sort after
all dated entries in their original file order. Multi-project
agents get one section per project, split 60/40 LEARNINGS/MEMORY
within each project's slice of the section budget.

### Resolution: which projects an agent receives

Precedence, highest first:

1. **MISSION.md frontmatter** — `project: <name>` (scalar) or
   `projects: [a, b]` (list). Explicit override; supports
   multi-project.
2. **`~/.metasphere/teams.yaml`** — central agent→projects roster.
   Schema and operator notes live at the top of the shipped file
   (`templates/install/teams.yaml`).
3. **Path-nested location** — agents living under
   `~/.metasphere/projects/<P>/agents/@<id>/` resolve to `<P>`.
4. No capsule otherwise.

Name-prefix string matching (`@<project>-<role>` → `<project>` via
dash-split) was tried briefly and removed: it failed on agents
whose project name itself contains a dash
(e.g. `@polymarket-agents-research` first-dash-splits to
`polymarket`, which doesn't match project `polymarket-agents`).
`teams.yaml` is the canonical replacement.

### Runtime discipline

Per-role `AGENTS.md` templates ship with a "Project memory store"
section telling agents how to read the capsule and when to consult
the underlying files directly:

- Reach for `MEMORY.md` for facts/configs/state.
- Reach for `LEARNINGS.md` for lessons/incidents.
- Don't reflex-grep on every project query (dilutes reasoning) —
  consult the file when the answer matters AND isn't in the
  agent's own memory AND isn't in the capsule.

The auto-memory layer at `~/.claude/projects/...` is
cross-conversation residue, secondary to the project files.

### Updating an agent's project membership

Operators have three knobs:

- Edit `MISSION.md` frontmatter for per-agent overrides
  (multi-project, custom project mapping).
- Edit `~/.metasphere/teams.yaml` for the default roster. Takes
  effect on the next per-turn tick (no restart).
- Move an agent's home dir to or from
  `~/.metasphere/projects/<P>/agents/` to flip the path-nested
  fallback. Rarely needed; teams.yaml is the cleaner path.

After updating a per-role `AGENTS.md` template, existing live
agents need a re-seed to pick up the new content:

```bash
metasphere agent seed --spec <spec-name> --force
```

`--force` is required to overwrite the existing per-agent file.
