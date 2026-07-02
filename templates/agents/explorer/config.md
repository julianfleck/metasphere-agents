---
name: explorer
role: explorer
description: Open-ended investigation on autonomous loops — surfaces unexpected signal
sandbox: scoped
persistent: true
auto_memory: true
---

## Triggers

- On `schedule.cron_fire`: check configured targets
- On `team.invoke` with action `status-report`: produce status summary
