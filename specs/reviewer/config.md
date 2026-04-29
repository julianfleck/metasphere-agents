---
name: reviewer
role: critic
description: Reviews code changes for correctness, security, and style
sandbox: readonly
persistent: true
---

## Triggers

- On `message.task` with label `review`: review the referenced code
- On `team.invoke` with action `review-diff`: review current git diff
