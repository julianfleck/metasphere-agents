"""metasphere.telegram — Telegram bridge subpackage.

Parallel-track rewrite of scripts/metasphere-telegram and
scripts/metasphere-telegram-stream. Uses the REWRITE bot token
(TELEGRAM_BOT_TOKEN_REWRITE) so it can run alongside the live bash bot
without colliding.

Critical invariants:
- api.send_message is the ONLY way to call sendMessage in this package.
- parse_mode defaults to None (plain text). No silent Markdown failures.
- Long messages auto-chunk on paragraph/line boundaries below 3900 chars.
- Offset persistence is atomic (tmp+rename) under fcntl lock.
"""

from .api import TelegramAPIError, send_message

__all__ = ["TelegramAPIError", "send_message"]
