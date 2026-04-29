import os

from metasphere.config import load_config, load_env_to_environ, parse_env_file


def test_parse_env_file(tmp_path):
    p = tmp_path / "x.env"
    p.write_text(
        '# comment\n'
        'export TELEGRAM_BOT_TOKEN="abc:123"\n'
        "CHAT_ID=42\n"
        "EMPTY=\n"
    )
    d = parse_env_file(p)
    assert d["TELEGRAM_BOT_TOKEN"] == "abc:123"
    assert d["CHAT_ID"] == "42"
    assert d["EMPTY"] == ""


def test_load_config(tmp_paths):
    cfg_dir = tmp_paths.config
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "telegram.env").write_text('TELEGRAM_BOT_TOKEN="canonical"\nTELEGRAM_CHAT_ID=1\n')
    (cfg_dir / "telegram-rewrite.env").write_text('TELEGRAM_BOT_TOKEN="rewrite"\nTELEGRAM_CHAT_ID=2\n')
    (cfg_dir / "other.env").write_text("FOO=bar\n")
    cfg = load_config(tmp_paths)
    assert cfg.telegram.bot_token == "canonical"
    assert cfg.telegram.rewrite_bot_token == "rewrite"
    assert cfg.telegram.chat_id == "1"
    assert cfg.telegram.rewrite_chat_id == "2"
    assert cfg.extra["FOO"] == "bar"
    assert cfg.agent_id == "@user"


# ---------- load_env_to_environ ----------

def test_load_env_to_environ_exports_keys(tmp_paths, monkeypatch):
    """Keys from config/*.env are exported via setdefault."""
    cfg_dir = tmp_paths.config
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "telegram.env").write_text("TELEGRAM_BOT_TOKEN=abc:123\n")
    (cfg_dir / "auto-update.env").write_text("AUTO_UPDATE_ENABLED=true\n")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("AUTO_UPDATE_ENABLED", raising=False)

    n = load_env_to_environ(tmp_paths)

    assert n >= 2
    assert os.environ.get("TELEGRAM_BOT_TOKEN") == "abc:123"
    assert os.environ.get("AUTO_UPDATE_ENABLED") == "true"


def test_load_env_to_environ_picks_up_bare_env_file(tmp_paths, monkeypatch):
    """The catch-all ``env`` file (no .env extension) is also read.

    Spot's ``~/.metasphere/config/env`` carries API keys without the
    suffix; if we only globbed ``*.env`` those would never reach
    ``os.environ``.
    """
    cfg_dir = tmp_paths.config
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "env").write_text("METASPHERE_OPERATOR_CHAT_ID=987654321\n")
    monkeypatch.delenv("METASPHERE_OPERATOR_CHAT_ID", raising=False)

    load_env_to_environ(tmp_paths)

    assert os.environ.get("METASPHERE_OPERATOR_CHAT_ID") == "987654321"


def test_load_env_to_environ_setdefault_does_not_overwrite(tmp_paths, monkeypatch):
    """Explicit shell-set values win over file values."""
    cfg_dir = tmp_paths.config
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "x.env").write_text("MY_VAR=from-file\n")
    monkeypatch.setenv("MY_VAR", "from-shell")

    load_env_to_environ(tmp_paths)

    assert os.environ.get("MY_VAR") == "from-shell"


def test_load_env_to_environ_no_config_dir_is_noop(tmp_path, monkeypatch):
    """Stranger install with no config dir: clean no-op."""
    from metasphere.paths import Paths
    paths = Paths(root=tmp_path / "ms", project_root=tmp_path, scope=tmp_path)
    # paths.config = paths.root / "config", which doesn't exist.
    n = load_env_to_environ(paths)
    assert n == 0


def test_load_env_to_environ_idempotent(tmp_paths, monkeypatch):
    """Running it twice is a no-op the second time (setdefault semantics)."""
    cfg_dir = tmp_paths.config
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "x.env").write_text("IDEMPOTENT_KEY=v1\n")
    monkeypatch.delenv("IDEMPOTENT_KEY", raising=False)

    n1 = load_env_to_environ(tmp_paths)
    n2 = load_env_to_environ(tmp_paths)

    assert n1 >= 1
    assert n2 == 0  # already in os.environ -> setdefault noops
    assert os.environ.get("IDEMPOTENT_KEY") == "v1"
