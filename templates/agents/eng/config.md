---
name: eng
role: eng
description: Implements features and fixes in isolated worktrees
sandbox: scoped
persistent: true
auto_memory: true
---

## Triggers

- On `message.task` with label `implement`: implement the described change
- On `team.invoke` with action `implement-task`: implement and report
