---
id: metasphere-status-warn-when-metasphere-telegramservice-and-m
title: "metasphere status: warn when metasphere-telegram.service AND metasphere-gateway.service both enabled (dual-poller race)"
priority: !normal
status: pending
scope: /.
project: default
created: 2026-04-08T10:35:47Z
created_by: @orchestrator
assigned_to: 
started_at: 
updated_at: 2026-04-08T13:44:11Z
completed_at: 
last_pinged_at: 2026-04-08T12:02:22Z
ping_count: 2
---
# metasphere status: warn when metasphere-telegram.service AND metasphere-gateway.service both enabled (dual-poller race)

## Description

Companion to the install.sh dual-poller fix. 'metasphere status' should detect at runtime if both metasphere-telegram.service AND metasphere-gateway.service are enabled, and emit a yellow warning: '⚠ metasphere-telegram.service AND metasphere-gateway.service are both enabled — they will race getUpdates and swallow ~50% of inbound messages. Disable one: systemctl --user disable metasphere-telegram.service'. Defense-in-depth so even if a host slipped through the install.sh fix, the operator notices. Implementation: in metasphere/cli/status.py, check 'systemctl --user is-enabled' for both, warn if both return enabled.

## Updates

- 2026-04-08T10:35:47Z Created task