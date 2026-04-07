from metasphere.config import load_config, parse_env_file


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
