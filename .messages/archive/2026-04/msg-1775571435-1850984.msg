---
id: msg-1775571435-1850984
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T14:17:15Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Generate MISSION.md files for persistent agents from openclaw jobs.json. EXECUTE NOW.

1. Read /home/openclaw/.metasphere/schedule/jobs.json. It is a JSON array of 16 enabled jobs all with agent_id='main'. Each has: name, cron_expr, tz, payload_message.

2. Group jobs by their conceptual role (the prefix in the name, e.g. 'polymarket:trading-run' -> @polymarket; 'research-monitor:brand-mentions' -> @research-brand-mentions; 'spot:autonomous-exploration' -> @explorer; etc.). The Morning briefing job is @briefing.

3. For each conceptual agent, create the dir if missing at /home/openclaw/.metasphere/agents/@<name>/ and write a MISSION.md file with structure:
   # MISSION — @<name>
   ## Role
   <one paragraph summary derived from the job name + payload>
   ## Schedule
   <cron expr> (<tz>)
   ## Operating Instructions
   <verbatim payload_message from the job, in a fenced block>

4. Group multi-job agents (polymarket has 3, research-monitor has 8) by collecting all their jobs into ONE MISSION.md per role-prefix with a Schedule section that lists all the cron entries.

5. Do NOT touch existing dirs that already have a non-empty MISSION.md.

6. Write a summary to /home/openclaw/.metasphere/agents/@mission-writer/report.md listing each agent created, what jobs went into it, and any anomalies.

7. Send: messages send @.. !done 'mission-writer: created N persistent agent missions, see report'

BEGIN. Use Read, Write, Bash, jq. Do not ask permission.
