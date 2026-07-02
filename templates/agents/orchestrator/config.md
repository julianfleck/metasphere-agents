---
name: orchestrator
role: orchestrator
description: Top-level coordinator — special-cased, persona seeded by install.sh
sandbox: none
persistent: true
auto_memory: true
---

## Triggers

The orchestrator's persona is seeded by `install.sh` as a heredoc,
not via `metasphere agent seed --spec orchestrator`. This config.md
exists so `list_specs()` discovers the role uniformly and the seeder
does not crash when an operator does invoke `--spec orchestrator` by
hand — in that case, only `AGENTS.md` is copied (no SOUL/MISSION in
this dir), and the operator is expected to author the persona files
directly.
