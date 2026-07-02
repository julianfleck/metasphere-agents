---
name: designer
role: designer
description: Interaction-designer agent — owns CLI/slash-command/docs clarity
sandbox: scoped
persistent: true
auto_memory: true
---

## Triggers

- On `message.task` with label `design-review`: audit a surface for clarity
- On `team.invoke` with action `design-task`: design and report

## Notes

No SOUL.md or MISSION.md ships for this role — designer is a
project-scoped role whose voice is set per-project. `seed_agent`
copies whatever is present in this dir (config.md + AGENTS.md), so
the seed succeeds without a persona stack.
