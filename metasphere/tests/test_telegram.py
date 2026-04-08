"""Tests for metasphere.telegram.

The Telegram API is mocked by replacing ``api._http_post`` with a stub
recorder. We don't make any real network calls.
"""

from __future__ import annotations

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


# --- Chunk splitter unit -------------------------------------------------

def test_split_chunks_prefers_paragraph_break():
    text = "para1" + "x" * 2000 + "\n\n" + "para2" + "y" * 2000
    chunks = api._split_chunks(text, max_len=3900)
    # First chunk should end at the paragraph break, not mid-word
    assert chunks[0].endswith("x" * 2000) or chunks[0].endswith("para1" + "x" * 2000)
    assert all(len(c) <= 3900 for c in chunks)


def test_split_chunks_short_text_single_chunk():
    assert api._split_chunks("short") == ["short"]
