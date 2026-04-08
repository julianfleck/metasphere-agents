---
id: msg-1775579291-2037148
from: @user
to: @orchestrator
label: !query
status: unread
scope: /
created: 2026-04-07T16:28:11Z
read_at:
replied_at:
completed_at:
reply_to: 
---

Yes to python, no to sqlite. I still want observability so we are doing this file based. Let’s start it now and run it in parallel with a different telegram token:  8604616317:AAGyOrpVJf0V1aZqT7JzHxnf5yQ0t8n4Ke8

You should be able to directly test with that token. 

Let’s start by documenting the cli surface thoroughly in the repo readme and a parallel doc that we won’t publish but that outlines each function that is currently used and that should be ported. Then we will review this for better architectural patterns/robustness/security/maintainability, then we start the refactor.
