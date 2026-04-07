"""metasphere — Python rewrite of the bash harness.

File-based, no SQLite, atomic writes + flock for concurrency.
Runs in parallel with the canonical bash scripts via a separate
Telegram bot token (see config.telegram_rewrite_token).
"""

__version__ = "0.1.0"
