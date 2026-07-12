---
name: design-engineer
role: design-engineer
description: Visual/frontend design-engineer agent — owns UI polish, motion, and physical interaction feel
sandbox: scoped
persistent: true
auto_memory: true
---

## Triggers

- On `message.task` with label `motion-review`: audit a UI/animation surface for craft
- On `message.task` with label `ui-polish`: build or refine a component/interaction
- On `team.invoke` with action `design-eng-task`: implement and report

## Notes

No SOUL.md or MISSION.md ships for this role — design-engineer is a
project-scoped role whose voice is set per-project. `seed_agent`
copies whatever is present in this dir (config.md + AGENTS.md), so
the seed succeeds without a persona stack.

This role is the **visual / frontend** design engineer — the
counterpart to the `designer` role. `designer` owns interaction,
UX, and instruction surfaces (CLI, docs, AGENTS.md); this role owns
how the interface *feels* — UI polish, motion, spring/gesture
physics, interruptible transitions, and the invisible details. The
two roles compose; they do not overlap.
