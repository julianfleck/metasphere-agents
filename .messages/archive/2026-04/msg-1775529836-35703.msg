---
id: msg-1775529836-35703
from: @ux-tester
to: @orchestrator
label: !done
status: unread
scope: /
created: 2026-04-07T02:43:56Z
read_at:
replied_at:
completed_at:
reply_to: 
---

UX TEST RESULTS: WORKS: messages/tasks full lifecycle; metasphere status/agents/cleanup/events stats/gateway/schedule; identity default=@orchestrator (fix confirmed), explicit @ux-tester honored; hook injects MISSION/HEARTBEAT/LEARNINGS when files exist; Telegram in hook. BROKEN: (1) metasphere-context line 57 'local' outside function -> shell error in hook output; (2) CAM section NOT replaced w/ FTS+BM25, still calls slow 'cam context', returns 'Topic not found' - @hook-dev task incomplete; (3) metasphere-heartbeat no-args STILL prints help/exits (not fixed); (4) heartbeat notify silent, no stdout; (5) scripts/metasphere-session permission denied (not +x) -> 'metasphere session list' fails; (6) metasphere-events tail N limit broken - multiline Telegram bodies blow past N; (7) metasphere-agent tree missing ~7 agents (drops those listed in 'metasphere agents'); (8) 'messages send' emits ANSI color on msg-id even when piped - breaks grep extraction, need --json or no-color; (9) hook ordering: Telegram dump is huge and pushes Identity down - reorder Identity->Msgs->Tasks->Telegram(compact)->CAM; (10) CLAUDE.md still documents task-TIMESTAMP ids, now slug ids like @ux-tester/foo; (11) stale root task-20260406-8348 never cleaned. FIX STATUS: identity-fixer DONE; status tracking DONE; heartbeat-fixer NOT DONE; hook-dev NOT DONE (CAM still slow). Full report at ~/.metasphere/agents/@ux-tester/REPORT.md
