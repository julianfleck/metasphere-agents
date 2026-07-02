"""metasphere.telegram — Telegram bridge subpackage.

Telegram bridge subpackage. Supports an optional secondary bot token
(TELEGRAM_BOT_TOKEN_REWRITE) for staging environments.

Key design rules:
- api.send_message is the ONLY way to call sendMessage in this package.
- parse_mode defaults to None (plain text). No silent Markdown failures.
- Long messages auto-chunk on paragraph/line boundaries below 3900 chars.
- Offset persistence is atomic (tmp+rename) under fcntl lock.
"""

from .api import TelegramAPIError, send_message

__all__ = ["TelegramAPIError", "send_message"]
