# persona-index — recurse

Lazy-load index for the recurse project's identity files. Read the
relevant entry on demand; do not load everything at session start.

| File | Read when |
|---|---|
| `SOUL.md` | You need recurse's identity, voice, values, or to disambiguate from Recurse Center |
| `MISSION.md` | You need the goal, the four work tracks, where things live on disk, or success criteria |
| `HEARTBEAT.md` | You need current focus / active state of the project |
| `LEARNINGS.md` | You need accumulated insights about working in/on recurse |
| `.metasphere/project.json` | You need machine-readable project metadata (members, telegram topic, schema version) |

## External references

- Writing/research repo: `~/.openclaw/workspace/repos/writing/projects/2025-recurse/`
- Substrate repo: `~/.openclaw/workspace/repos/rage-substrate/`
- Live RAGE database: `databases/divergence-engines.db` (served by
  `rage-server.service` on data.basicbold.de)
- Auto-memory pointer: `~/.claude/projects/-home-openclaw-Code-metasphere-agents/memory/recurse_project.md`
