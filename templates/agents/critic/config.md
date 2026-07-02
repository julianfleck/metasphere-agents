---
name: critic
role: critic
description: Reviews code changes for correctness, security, and style
sandbox: readonly
persistent: true
auto_memory: true
---

## Triggers

- On `message.task` with label `review`: review the referenced code
- On `team.invoke` with action `review-diff`: review current git diff
