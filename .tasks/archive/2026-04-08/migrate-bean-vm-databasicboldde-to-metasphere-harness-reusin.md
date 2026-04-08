---
id: migrate-bean-vm-databasicboldde-to-metasphere-harness-reusin
title: Migrate bean VM (data.basicbold.de) to metasphere harness, reusing bean's existing telegram token
priority: !high
status: completed
scope: /.
created: 2026-04-07T21:31:24Z
created_by: @orchestrator
assigned_to: 
started_at: 
updated_at: 2026-04-08T11:38:26Z
completed_at: 2026-04-08T11:38:26Z
last_pinged_at: 2026-04-08T11:33:57Z
ping_count: 1
---
# Migrate bean VM (data.basicbold.de) to metasphere harness, reusing bean's existing telegram token

## Updates
- 2026-04-08T11:38:26Z Completed: bean is migrated and running metasphere — verified by bean-debug bug report from @user via wintermute today (10:15Z dual-poller fix applied to bean)
- 2026-04-07T22:01:29Z Delegated to @wintermute on data.basicbold.de host (it has machinectl/sudo access mochi). Handover doc at docs/WINTERMUTE-mochi-install.md (committed 7651ecb). Spot is firewalled inside the openclaw nspawn so cannot drive directly.

- 2026-04-07T21:31:24Z Created task