---
name: monitor
role: monitor
description: Scheduled monitoring — health checks, anomaly detection, alerts
sandbox: scoped
persistent: true
---

## Triggers

- On `schedule.cron_fire`: check configured targets
- On `team.invoke` with action `status-report`: produce status summary
