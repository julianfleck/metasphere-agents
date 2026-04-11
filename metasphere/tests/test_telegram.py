"""Tests for metasphere.telegram.

The Telegram API is mocked by replacing ``api._http_post`` with a stub
recorder. We don't make any real network calls.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
from typing import List

import pytest

from metasphere.telegram import api, archiver, poller


# --- Fixtures -------------------------------------------------------------

@pytest.fixture
def fake_post(monkeypatch):
    """Replace api._http_post with a recorder that returns canned responses."""
    calls: List[dict] = []
    queue: List[dict] = []

    def fake(url, data, timeout=35):
        calls.append({"url": url, "data": dict(data)})
        if queue:
            return queue.pop(0)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(api, "_http_post", fake)
    # Pretend we have a token so _config() works.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_REWRITE", "TEST:TOKEN")
    fake.calls = calls  # type: ignore
    fake.queue = queue  # type: ignore
    return fake


# --- send_message --------------------------------------------------------

def test_send_message_plain_text_strips_parse_mode(fake_post):
    api.send_message(123, "hello world")
    assert len(fake_post.calls) == 1
    data = fake_post.calls[0]["data"]
    assert data["text"] == "hello world"
    assert data["chat_id"] == 123
    # Default parse_mode is None and gets dropped from the request entirely
    assert "parse_mode" not in data


def test_send_message_explicit_parse_mode_passes_through(fake_post):
    api.send_message(123, "*bold*", parse_mode="MarkdownV2")
    assert fake_post.calls[0]["data"]["parse_mode"] == "MarkdownV2"


def test_send_message_chunks_at_3900_boundary(fake_post):
    # Build a message > 3900 chars with paragraph breaks so the splitter
    # has somewhere to cut.
    para = "a" * 1000
    text = "\n\n".join([para] * 5)  # ~5004 chars
    assert len(text) > api.CHUNK_MAX

    api.send_message(123, text)

    # Should have produced 2+ chunks, each <= CHUNK_MAX (plus marker)
    assert len(fake_post.calls) >= 2
    for i, c in enumerate(fake_post.calls, start=1):
        body = c["data"]["text"]
        assert body.startswith(f"[{i}/")
        # marker plus content fits within Telegram's hard cap
        assert len(body) <= 4096


def test_send_message_single_chunk_no_marker(fake_post):
    api.send_message(123, "short message")
    assert fake_post.calls[0]["data"]["text"] == "short message"
    assert "[1/" not in fake_post.calls[0]["data"]["text"]


def test_send_message_raises_on_api_error(fake_post):
    fake_post.queue.append({"ok": False, "description": "Bad Request: chat not found"})
    with pytest.raises(api.TelegramAPIError) as ei:
        api.send_message(999, "hi")
    assert "chat not found" in str(ei.value)
    assert ei.value.method == "sendMessage"


def test_send_message_empty_text_rejected(fake_post):
    with pytest.raises(ValueError):
        api.send_message(1, "")


def test_send_message_html_parse_mode(fake_post):
    api.send_message(123, "<b>hi</b>", parse_mode="HTML")
    assert fake_post.calls[0]["data"]["parse_mode"] == "HTML"
    assert fake_post.calls[0]["data"]["text"] == "<b>hi</b>"


def test_escape_html_round_trip():
    assert api.escape_html("a < b > c & d") == "a &lt; b &gt; c &amp; d"
    assert api.escape_html("") == ""
    # Ampersand first to avoid double escape
    assert api.escape_html("&lt;") == "&amp;lt;"


# --- Offset persistence --------------------------------------------------

def test_offset_round_trip(tmp_path):
    p = tmp_path / "offset"
    poller.save_offset(42, str(p))
    assert poller.load_offset(str(p)) == 42


def test_offset_atomic_no_partial_write(tmp_path):
    """Concurrent writers should never leave the file empty or partial."""
    p = tmp_path / "offset"
    poller.save_offset(1, str(p))

    errors: List[Exception] = []

    def writer(start: int):
        try:
            for i in range(start, start + 50):
                poller.save_offset(i, str(p))
                # And read it back; it should always parse to a valid int.
                v = poller.load_offset(str(p))
                assert isinstance(v, int)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(s,)) for s in (100, 200, 300)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    # Final value should be a valid int
    assert isinstance(poller.load_offset(str(p)), int)


def test_offset_load_missing_returns_zero(tmp_path):
    assert poller.load_offset(str(tmp_path / "nope")) == 0


# --- Archiver ------------------------------------------------------------

def test_archive_message_appends_jsonl(tmp_path):
    msg = {"message_id": 1, "text": "hi", "from": {"username": "u"}, "chat": {"id": 5}, "date": 0}
    path = archiver.archive_message(msg, base_dir=str(tmp_path))
    assert os.path.exists(path)
    with open(path) as f:
        lines = f.readlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["text"] == "hi"


def test_archive_concurrent_appends_no_corruption(tmp_path):
    """Many threads appending must not interleave bytes within a line."""
    base = str(tmp_path)

    def worker(n: int):
        for i in range(20):
            archiver.archive_message(
                {"message_id": n * 100 + i, "text": f"t{n}-{i}", "from": {"username": f"u{n}"}, "chat": {"id": 1}, "date": 0},
                base_dir=base,
            )

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Find today's file
    stream = os.path.join(base, "stream")
    files = os.listdir(stream)
    assert len(files) == 1
    with open(os.path.join(stream, files[0])) as f:
        lines = f.readlines()
    assert len(lines) == 100
    # Every line must be valid JSON (no interleaving)
    for line in lines:
        parsed = json.loads(line)
        assert "text" in parsed


def test_save_latest_atomic(tmp_path):
    msg = {"message_id": 7, "text": "hello", "from": {"username": "u"}, "chat": {"id": 1}, "date": 1234}
    archiver.save_latest(msg, base_dir=str(tmp_path))
    with open(tmp_path / "latest.json") as f:
        data = json.load(f)
    assert data["message_id"] == 7
    assert data["from"] == "u"
    assert data["text"] == "hello"


# --- Reply / reaction capture --------------------------------------------

def test_poller_captures_reply_to_fields():
    """An update with reply_to_message should surface id + preview."""
    payload = {
        "update_id": 1001,
        "message": {
            "message_id": 50,
            "chat": {"id": 1, "is_forum": False},
            "from": {"username": "j0lian"},
            "text": "yes, do it",
            "date": 1700000000,
            "reply_to_message": {
                "message_id": 42,
                "text": "Should I ship the patch now?",
                "from": {"username": "orchestrator"},
            },
        },
    }
    u = poller.Update.from_payload(payload)
    assert u.kind == "message"
    assert u.message_id == 50
    assert u.reply_to_message_id == 42
    assert u.reply_to_text_preview == "Should I ship the patch now?"


def test_poller_reply_preview_truncates_at_100_chars():
    long = "x" * 250
    payload = {
        "update_id": 2,
        "message": {
            "message_id": 2,
            "chat": {"id": 1},
            "from": {"username": "j0lian"},
            "text": "ok",
            "reply_to_message": {"message_id": 1, "text": long},
        },
    }
    u = poller.Update.from_payload(payload)
    assert u.reply_to_text_preview is not None
    assert len(u.reply_to_text_preview) == 100


def test_poller_message_without_reply_has_none_reply_fields():
    payload = {
        "update_id": 3,
        "message": {
            "message_id": 3,
            "chat": {"id": 1},
            "from": {"username": "j0lian"},
            "text": "hi",
        },
    }
    u = poller.Update.from_payload(payload)
    assert u.reply_to_message_id is None
    assert u.reply_to_text_preview is None


def test_poller_parses_message_reaction_update():
    """message_reaction payloads should produce kind=='reaction' Updates."""
    payload = {
        "update_id": 9001,
        "message_reaction": {
            "chat": {"id": -1001, "is_forum": True},
            "message_id": 17,
            "user": {"id": 99, "username": "j0lian"},
            "date": 1700000123,
            "old_reaction": [],
            "new_reaction": [{"type": "emoji", "emoji": "\U0001f44d"}],
        },
    }
    u = poller.Update.from_payload(payload)
    assert u.kind == "reaction"
    assert u.reaction_target_message_id == 17
    assert u.reaction_emojis == ["\U0001f44d"]
    assert u.from_username == "j0lian"
    assert u.chat_id == -1001


def test_poller_allowed_updates_includes_message_reaction():
    """get_updates must request message_reaction explicitly."""
    assert "message_reaction" in poller.ALLOWED_UPDATES


def test_get_updates_passes_allowed_updates(monkeypatch):
    """The JSON payload to getUpdates must list message_reaction."""
    captured = {}

    def fake_call(method, **params):
        captured["method"] = method
        captured["params"] = params
        return {"ok": True, "result": []}

    monkeypatch.setattr(poller.api, "call", fake_call)
    poller.get_updates(offset=0, timeout=1)
    assert captured["method"] == "getUpdates"
    allowed = json.loads(captured["params"]["allowed_updates"])
    assert "message_reaction" in allowed
    assert "message" in allowed


# --- Archiver: reply / reaction round-trip -------------------------------

def test_archive_message_lifts_reply_to_fields(tmp_path):
    """archive_message should surface reply_to_* at the top level."""
    msg = {
        "message_id": 60,
        "text": "replying now",
        "from": {"username": "j0lian"},
        "chat": {"id": 5},
        "date": 1700000000,
        "reply_to_message": {
            "message_id": 42,
            "text": "What do you think?",
        },
    }
    path = archiver.archive_message(msg, base_dir=str(tmp_path))
    with open(path) as f:
        lines = f.readlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["reply_to_message_id"] == 42
    assert row["reply_to_text_preview"] == "What do you think?"
    # Nested field is preserved (non-destructive enrichment)
    assert row["reply_to_message"]["message_id"] == 42
    # The input dict wasn't mutated
    assert "reply_to_message_id" not in msg


def test_archive_message_without_reply_unchanged(tmp_path):
    msg = {
        "message_id": 1,
        "text": "hi",
        "from": {"username": "u"},
        "chat": {"id": 1},
        "date": 0,
    }
    path = archiver.archive_message(msg, base_dir=str(tmp_path))
    with open(path) as f:
        row = json.loads(f.readline())
    assert "reply_to_message_id" not in row
    assert "reply_to_text_preview" not in row


def test_archive_reaction_round_trip(tmp_path):
    path = archiver.archive_reaction(
        target_message_id=17,
        emojis=["\U0001f44d"],
        from_username="j0lian",
        chat_id=-1001,
        date=1700000000,
        base_dir=str(tmp_path),
    )
    with open(path) as f:
        row = json.loads(f.readline())
    assert row["kind"] == "reaction"
    assert row["reaction_target_message_id"] == 17
    assert row["reactions"] == [{"emoji": "\U0001f44d", "from": "j0lian"}]


# --- telegram_context() rendering ----------------------------------------

def test_telegram_context_renders_reply_indicator(tmp_path):
    msg = {
        "message_id": 61,
        "text": "yes please",
        "from": {"username": "j0lian"},
        "chat": {"id": 5},
        "date": 1700000000,
        "reply_to_message": {
            "message_id": 42,
            "text": "Should I ship the patch now?",
        },
    }
    archiver.archive_message(msg, base_dir=str(tmp_path))
    out = archiver.telegram_context(history=5, base_dir=str(tmp_path))
    assert "\u21a9 replying to:" in out
    assert "Should I ship the patch now?" in out


def test_telegram_context_renders_reaction_line(tmp_path):
    archiver.archive_reaction(
        target_message_id=99,
        emojis=["\U0001f525"],
        from_username="j0lian",
        chat_id=-1001,
        date=1700000000,
        base_dir=str(tmp_path),
    )
    out = archiver.telegram_context(history=5, base_dir=str(tmp_path))
    assert "reaction:" in out
    assert "\U0001f525" in out
    assert "@j0lian" in out
    assert "msg-99" in out


def test_telegram_context_reply_preview_truncates_at_60_chars(tmp_path):
    long_quote = "a" * 200
    msg = {
        "message_id": 1,
        "text": "ok",
        "from": {"username": "j0lian"},
        "chat": {"id": 1},
        "date": 1700000000,
        "reply_to_message": {"message_id": 99, "text": long_quote},
    }
    archiver.archive_message(msg, base_dir=str(tmp_path))
    out = archiver.telegram_context(history=5, base_dir=str(tmp_path))
    # 60 chars of 'a' plus the trailing ellipsis marker
    assert ("a" * 60 + "...") in out


def test_telegram_context_backcompat_old_rows_without_new_fields(tmp_path):
    """Rows written before reply_to / reactions existed must still render."""
    import os as _os

    stream = _os.path.join(str(tmp_path), "stream")
    _os.makedirs(stream, exist_ok=True)
    # Write an old-shape row directly to today's file.
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    with open(_os.path.join(stream, f"{today}.jsonl"), "w") as f:
        f.write(json.dumps({
            "message_id": 1,
            "text": "legacy row",
            "from": {"username": "j0lian"},
            "chat": {"id": 1},
            "date": 1700000000,
        }) + "\n")
    out = archiver.telegram_context(history=5, base_dir=str(tmp_path))
    assert "legacy row" in out
    assert "\u21a9" not in out  # No reply indicator when field absent
    assert "reaction:" not in out


# --- Chunk splitter unit -------------------------------------------------

def test_split_chunks_prefers_paragraph_break():
    text = "para1" + "x" * 2000 + "\n\n" + "para2" + "y" * 2000
    chunks = api._split_chunks(text, max_len=3900)
    # First chunk should end at the paragraph break, not mid-word
    assert chunks[0].endswith("x" * 2000) or chunks[0].endswith("para1" + "x" * 2000)
    assert all(len(c) <= 3900 for c in chunks)


def test_split_chunks_short_text_single_chunk():
    assert api._split_chunks("short") == ["short"]
