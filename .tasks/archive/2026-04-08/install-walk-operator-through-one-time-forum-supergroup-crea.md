---
id: install-walk-operator-through-one-time-forum-supergroup-crea
title: "Install: walk operator through one-time forum supergroup creation"
priority: !high
status: completed
scope: /.
created: 2026-04-08T00:42:30Z
created_by: @orchestrator
assigned_to: 
started_at: 
updated_at: 2026-04-08T11:07:08Z
completed_at: 2026-04-08T11:07:08Z
---
# Install: walk operator through one-time forum supergroup creation

## Updates
- 2026-04-08T11:07:08Z Completed: completed in a94cac8: telegram-groups non-interactive setup + verify subcommands

- 2026-04-08T00:42:30Z Created task

## Spec

During `metasphere install` (or first run if no forum_id is configured), walk the operator through the one-time bootstrap of a Telegram forum supergroup that the bot can use as its workspace. This is a Telegram Bot API hard limit: bots cannot create supergroups themselves, only topics inside an existing one.

Steps the installer should take the operator through (with copyable instructions):

1. "Open Telegram → New Group → name it (e.g. 'Metasphere'). Make it a **supergroup** with **Topics enabled** (Group Settings → Edit → Topics)."
2. "Add the bot (@<botname>) as a member, then promote it to **admin** with at least: Manage Topics, Send Messages, Pin Messages."
3. "Send any message in the group, then forward it to @RawDataBot (or @username_to_id_bot). Copy the `chat.id` it returns — it starts with `-100`."
4. Prompt: paste the id. Validate by calling `getChat` via the bot API and checking `is_forum: true` and the bot's admin status. Loop with a clear error if validation fails (not a supergroup, topics disabled, bot not admin, etc.).
5. Write to `~/.metasphere/config/telegram_forum_id`. Done.
6. Offer: "Create your first project topic now? [y/N]" → if yes, run the project new wizard.

Make this non-blocking: `metasphere install --skip-telegram-forum` should also work, deferring the setup.

The non-interactive path from @groups-noninteractive lands first; this task layers the install-time UX on top.