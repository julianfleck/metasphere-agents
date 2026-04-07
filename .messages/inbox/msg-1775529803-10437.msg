---
id: msg-1775529803-10437
from: @orchestrator
to: @orchestrator
label: !done
status: unread
scope: /
created: 2026-04-07T02:43:23Z
read_at:
replied_at:
completed_at:
reply_to: 
---

FTS HOOK: metasphere-fts (bash+rg+awk, token-overlap score, inverse-length weighting) replaces per-turn cam context in metasphere-context section 6. FTS isolated: ~38ms. Full hook: ~570ms (dominated by telegram/events, not FTS). cam search remains available for explicit deeper recall. Files: scripts/metasphere-fts (new), scripts/metasphere-context (lines 117-147 replaced). Commit 7a736c2.
