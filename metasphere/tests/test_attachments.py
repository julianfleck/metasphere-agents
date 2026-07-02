"""Tests for the optional voice-transcription path in attachments.py.

Parse / download / render coverage for non-voice kinds lives in
``test_telegram.py``. This module focuses on the slice the voice
extra introduced: faster-whisper detection, inline rendering of
transcripts, and the .oga retention sweep.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from metasphere.telegram import attachments as _atts


@pytest.fixture(autouse=True)
def _fake_telegram_token(monkeypatch):
    # ``download_attachment`` resolves ``api._config()`` to build the file
    # URL even when ``http_get`` is injected — in CI (no token in env, no
    # config file under HOME) that raises before the fake reaches it.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST:TOKEN")


def _voice_msg(file_id: str = "voice-abc", duration: int = 3) -> dict:
    return {
        "voice": {
            "file_id": file_id,
            "duration": duration,
            "mime_type": "audio/ogg",
            "file_size": 1234,
        },
    }


def _fake_call_factory(file_path: str = "voice/file_1.oga"):
    def _call(method: str, **kwargs):
        assert method == "getFile"
        return {"ok": True, "result": {"file_path": file_path}}
    return _call


def _fake_http_get(url: str, timeout: float) -> bytes:
    # Marker bytes the session-end pollution guard ignores when read
    # back from the sandbox tmp_path.
    return b"OggS-fake-voice-bytes"


def test_parse_attachments_captures_voice_duration():
    refs = _atts.parse_attachments(_voice_msg(duration=7))
    assert len(refs) == 1
    assert refs[0].kind == "voice"
    assert refs[0].duration == 7


def test_transcription_skipped_when_faster_whisper_absent(tmp_path, monkeypatch):
    """No faster-whisper installed → no transcript, but the path still
    lands on disk and the rendered block carries the install hint."""
    monkeypatch.setattr(_atts, "_load_whisper_model", lambda: None)

    refs = _atts.parse_attachments(_voice_msg())
    results = _atts.download_attachments(
        message_id=42,
        refs=refs,
        root=tmp_path / "attachments",
        http_get=_fake_http_get,
        call_fn=_fake_call_factory(),
    )

    assert len(results) == 1
    item = results[0]
    assert item.kind == "voice"
    assert item.path is not None and item.path.exists()
    assert item.transcript is None
    assert item.language is None
    assert item.transcription_status == "unavailable"

    block = _atts.render_attachment_block(results)
    assert "(transcription unavailable — install faster-whisper)" in block
    # Path line preserved so the operator can still play the .oga back.
    assert str(item.path) in block


def test_transcript_rendered_inline_when_model_present(tmp_path, monkeypatch):
    """faster-whisper present → emit [voice <dur>s <lang>] + transcript."""
    fake_segment = SimpleNamespace(text=" hello there ")
    fake_info = SimpleNamespace(language="en")

    class _FakeModel:
        def transcribe(self, path):
            assert Path(path).exists(), "transcribe called before download landed"
            return iter([fake_segment]), fake_info

    monkeypatch.setattr(_atts, "_load_whisper_model", lambda: _FakeModel())

    refs = _atts.parse_attachments(_voice_msg(duration=3))
    results = _atts.download_attachments(
        message_id=99,
        refs=refs,
        root=tmp_path / "attachments",
        http_get=_fake_http_get,
        call_fn=_fake_call_factory(),
    )

    assert len(results) == 1
    item = results[0]
    assert item.transcription_status == "ok"
    assert item.transcript == "hello there"
    assert item.language == "en"

    block = _atts.render_attachment_block(results)
    lines = block.splitlines()
    assert lines[0] == "[attachments]"
    assert "[voice 3s en]" in lines
    idx = lines.index("[voice 3s en]")
    assert lines[idx + 1] == "hello there"
    # Inline form replaces the path line — no "- voice:" prefix should
    # appear when a transcript landed.
    assert not any(l.startswith("- voice:") for l in lines)


def test_transcription_failure_appends_note(tmp_path, monkeypatch):
    """Model loaded but .transcribe() raises → degrade to path + reason."""
    class _BrokenModel:
        def transcribe(self, path):
            raise RuntimeError("decoder boom")

    monkeypatch.setattr(_atts, "_load_whisper_model", lambda: _BrokenModel())

    refs = _atts.parse_attachments(_voice_msg())
    results = _atts.download_attachments(
        message_id=7,
        refs=refs,
        root=tmp_path / "attachments",
        http_get=_fake_http_get,
        call_fn=_fake_call_factory(),
    )

    item = results[0]
    assert item.transcript is None
    assert item.transcription_status and item.transcription_status.startswith("failed:")

    block = _atts.render_attachment_block(results)
    assert "transcription failed: decoder boom" in block


def test_download_attachment_returns_error_when_no_token(tmp_path, monkeypatch):
    """Token resolution failure must not propagate — the function's
    docstring promises it never raises, so callers can render a context
    block note even on a misconfigured host."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN_REWRITE", raising=False)
    # Point HOME away from any real ~/.metasphere/config so the env-file
    # branches of ``_load_token`` also miss.
    monkeypatch.setenv("HOME", str(tmp_path / "no-home"))

    ref = _atts.AttachmentRef(kind="document", file_id="doc-1")
    result = _atts.download_attachment(
        ref,
        tmp_path / "dest",
        http_get=_fake_http_get,
        call_fn=_fake_call_factory("documents/file_1.pdf"),
    )

    assert result.path is None
    assert result.error is not None
    assert result.error.startswith("config:")
    assert "telegram bot token" in result.error.lower()


def test_prune_old_voice_files_drops_only_aged_oga(tmp_path):
    root = tmp_path / "attachments"
    (root / "111").mkdir(parents=True)
    fresh = root / "111" / "fresh.oga"
    aged = root / "111" / "aged.oga"
    keep = root / "111" / "doc.pdf"  # different ext: must survive
    for p in (fresh, aged, keep):
        p.write_bytes(b"x")

    now = time.time()
    # Backdate ``aged`` past the 7-day cutoff; leave ``fresh`` at now.
    old_mtime = now - (8 * 86400)
    import os
    os.utime(aged, (old_mtime, old_mtime))

    removed = _atts.prune_old_voice_files(root, now=now)

    assert removed == 1
    assert fresh.exists()
    assert keep.exists()
    assert not aged.exists()


def test_prune_skipped_when_transcription_unavailable(tmp_path, monkeypatch):
    """No transcript → no sweep. Operators who haven't installed
    faster-whisper get to keep their audio indefinitely."""
    root = tmp_path / "attachments"
    (root / "1").mkdir(parents=True)
    aged = root / "1" / "old.oga"
    aged.write_bytes(b"x")
    import os
    old_mtime = time.time() - (30 * 86400)
    os.utime(aged, (old_mtime, old_mtime))

    monkeypatch.setattr(_atts, "_load_whisper_model", lambda: None)
    _atts.download_attachments(
        message_id=2,
        refs=_atts.parse_attachments(_voice_msg()),
        root=root,
        http_get=_fake_http_get,
        call_fn=_fake_call_factory(),
    )

    # 30-day-old .oga still on disk because no transcript succeeded.
    assert aged.exists()


def test_debug_log_appends_jsonl_record(tmp_path):
    """Each call appends one timestamped JSONL line to the target path."""
    import json

    log = tmp_path / "telegram_debug.log"
    _atts.debug_log({"stage": "post_parse", "n": 1}, path=log)
    _atts.debug_log({"stage": "pre_inject", "n": 2}, path=log)

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["stage"] == "post_parse" and first["n"] == 1
    assert "ts" in first  # UTC timestamp injected by debug_log


def test_debug_log_rotates_past_max_bytes(tmp_path, monkeypatch):
    """Once the live log crosses the cap it rotates to a single ``.1``
    sibling and the next write starts a fresh file — bounding total
    on-disk use to ~2× the cap."""
    log = tmp_path / "telegram_debug.log"
    backup = tmp_path / "telegram_debug.log.1"
    # Tiny cap so a couple of records trip rotation deterministically.
    monkeypatch.setattr(_atts, "DEBUG_LOG_MAX_BYTES", 200)

    # Write enough records to exceed 200 bytes and trigger one rotation.
    for i in range(20):
        _atts.debug_log({"stage": "post_parse", "i": i, "pad": "x" * 20}, path=log)

    assert backup.exists(), "expected a rotated .1 backup once cap exceeded"
    # Live file never exceeds the cap (it may be absent right after a
    # rotation, before the next append recreates it).
    if log.exists():
        assert log.stat().st_size < _atts.DEBUG_LOG_MAX_BYTES
    # The newest record is always retained — in the live file if one
    # exists, otherwise in the just-rotated backup.
    newest_file = log if log.exists() else backup
    assert '"i": 19' in newest_file.read_text(encoding="utf-8").splitlines()[-1]
    # Only one backup generation is ever kept (no .2, .3, ...).
    assert not (tmp_path / "telegram_debug.log.2").exists()


def test_debug_log_never_raises_on_bad_event(tmp_path):
    """A non-serializable event must not propagate — logging is
    best-effort and the poller's main path must survive it."""
    log = tmp_path / "telegram_debug.log"

    class _Unjsonable:
        pass

    # default=str in debug_log makes even odd objects serializable, so this
    # should write rather than raise; the contract is simply "never raises".
    _atts.debug_log({"stage": "post_parse", "obj": _Unjsonable()}, path=log)
    assert log.exists()
