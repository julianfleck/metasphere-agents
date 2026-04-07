# .messages/

Fractal inter-agent mailbox for this scope. Every directory in the project can have its own `.messages/` and an agent operating in that scope sees messages from here **plus all parent scopes** (upward visibility — no sibling visibility).

## Layout

```
.messages/
├── inbox/        # received messages, one file per message
└── outbox/       # sent messages, one file per message
```

Each message is a single file named `msg-<unix_us>-<rand>.msg`.

## File format

```
from: @sender
to: @target
label: !task
status: unread
created: 2026-04-07T05:00:00Z
parent: <reply-to-msg-id or empty>
---
Free-form message body. Multi-line is fine.
```

Fields above the `---` are an RFC822-ish header. Status transitions: `unread → read → replied → completed`. The `messages` CLI (`scripts/messages`) handles the writes — don't hand-edit unless you know what you're doing.

## Targets

| Target | Resolves to |
|---|---|
| `@.` | This directory's `.messages/inbox/` |
| `@..` | Parent directory's `.messages/inbox/` |
| `@/path/` | Absolute path from repo root |
| `@name` | Named agent at `~/.metasphere/agents/@name/` |

## Labels

`!task`, `!urgent`, `!info`, `!query`, `!done`, `!reply`. The label is metadata for the recipient — nothing in the harness enforces it, but the `messages` CLI uses it for filtering and the context-injection hook surfaces urgent/task labels first.

## Important non-property

**Messages do NOT cross hosts.** This is a local, on-disk transport. Wintermute and spot each have their own `.messages/` trees. Cross-host coordination happens via **CAM** (memory) and via **Telegram** (human-in-the-loop), not via messages. If you find yourself reaching for cross-host messaging, that's a category error — re-read `../CLAUDE.md`.
