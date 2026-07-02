"""metasphere — multi-agent orchestration harness.

File-based, no SQLite, atomic writes + flock for concurrency.
"""

# Auto-export operator config to os.environ at import time so any
# downstream code (CLI, tests, daemon) sees keys defined in
# ~/.metasphere/config/*.env (and the bare ``env`` file) without a
# separate bootstrap step. Uses ``os.environ.setdefault`` semantics:
# explicit shell-set values still win, so operators can override
# per-invocation via ``KEY=value cmd ...``. Wrapped in a broad
# try/except because import-time side effects must never break the
# import — a malformed config file produces zero env keys, not a
# crash.
try:
    from .config import load_env_to_environ as _load_env_to_environ
    _load_env_to_environ()
except Exception:
    pass
