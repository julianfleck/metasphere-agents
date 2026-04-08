---
id: installsh-stopdisablemask-metasphere-telegramservice-when-ga
title: "install.sh: stop+disable+mask metasphere-telegram.service when gateway is in use (dual-poller race fix)"
priority: !high
status: pending
scope: /.
project: default
created: 2026-04-08T10:35:46Z
created_by: @orchestrator
assigned_to: 
started_at: 
updated_at: 2026-04-08T13:44:10Z
completed_at: 
last_pinged_at: 2026-04-08T12:02:18Z
ping_count: 2
---
# install.sh: stop+disable+mask metasphere-telegram.service when gateway is in use (dual-poller race fix)

## Description

On a fresh metasphere install, install.sh enables BOTH metasphere-telegram.service AND metasphere-gateway.service. Both services poll Telegram getUpdates on the same bot token, racing for incoming messages. Result: ~50% of inbound messages get swallowed by whichever poller wins the race, with no error to the user. Bug found on bean (bug-debug report from @user via wintermute, fixed manually 2026-04-08 10:15Z). Spot was also affected, fixed manually same day. Fix: when install.sh sets up metasphere-gateway.service, it must also stop+disable+mask metasphere-telegram.service so the dual-poller race cannot recur on next boot. Add idempotent logic so re-running install.sh doesn't break working hosts.

## Updates

- 2026-04-08T10:35:46Z Created task