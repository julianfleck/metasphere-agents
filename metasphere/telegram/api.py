"""Single source-of-truth Telegram API client.

This module is the ONLY place in the metasphere package that talks to
api.telegram.org/sendMessage. All other modules MUST go through
``send_message`` here. A single call site ensures consistent parse_mode
handling and prevents silent message drops on Markdown parse failures.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

# Telegram's hard cap is 4096 chars; leave headroom for the [part/total] marker.
CHUNK_MAX = 3900

DEFAULT_TIMEOUT = 35  # seconds; long-poll callers override


class TelegramAPIError(RuntimeError):
    """Raised when the Telegram API returns ``ok: false``."""

    def __init__(self, method: str, description: str, response: dict):
        super().__init__(f"{method}: {description}")
        self.method = method
        self.description = description
        self.response = response


@dataclass
class _Config:
    token: str
    api_base: str


def _read_env_file(path: str, key: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    return None


def _load_token() -> str:
    """Load the bot token.

    Resolution order (canonical-first; rewrite token is opt-in only):

    1. ``TELEGRAM_BOT_TOKEN`` env var (canonical @spotspotbotbot — what the
       live orchestrator and human channel use).
    2. ``~/.metasphere/config/telegram.env`` ``TELEGRAM_BOT_TOKEN``.
    3. ``TELEGRAM_BOT_TOKEN_REWRITE`` env var (explicit opt-in for a
       staging/sandbox bot during dev/testing only).
    4. ``~/.metasphere/config/telegram-rewrite.env`` ``TELEGRAM_BOT_TOKEN_REWRITE``.

    After cutover the canonical bot MUST win by default so daemons that
    inherit a clean systemd env never accidentally talk to the dev bot.
    """
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    if tok:
        return tok
    tok = _read_env_file(
        os.path.expanduser("~/.metasphere/config/telegram.env"),
        "TELEGRAM_BOT_TOKEN",
    )
    if tok:
        return tok
    tok = os.environ.get("TELEGRAM_BOT_TOKEN_REWRITE")
    if tok:
        return tok
    tok = _read_env_file(
        os.path.expanduser("~/.metasphere/config/telegram-rewrite.env"),
        "TELEGRAM_BOT_TOKEN_REWRITE",
    )
    if tok:
        return tok
    raise RuntimeError(
        "No telegram bot token found: tried TELEGRAM_BOT_TOKEN_REWRITE, "
        "TELEGRAM_BOT_TOKEN, ~/.metasphere/config/telegram.env, and "
        "~/.metasphere/config/telegram-rewrite.env"
    )


def _config() -> _Config:
    tok = _load_token()
    return _Config(token=tok, api_base=f"https://api.telegram.org/bot{tok}")


# Injection seam for tests: replace ``_http_post`` with a stub.
def _http_post(url: str, data: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    body = urllib.parse.urlencode(data, quote_via=urllib.parse.quote).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8", errors="replace")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        raise TelegramAPIError("http", f"non-JSON response: {payload[:200]}", {}) from e


def escape_html(s: str) -> str:
    """Escape ``& < >`` for ``parse_mode='HTML'`` sendMessage calls.

    Telegram's HTML parser only requires escaping these three characters in
    text content (and inside <b>/<i>/<a>). Quotes are left alone so they
    read naturally. Mirrors :func:`metasphere.format.escape_html`; the helper
    is duplicated here so callers that don't care about the format module
    don't have to import it.
    """
    if not s:
        return ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def call(method: str, **params: Any) -> dict:
    """Low-level call to a Telegram bot method. Raises on ok: false."""
    cfg = _config()
    url = f"{cfg.api_base}/{method}"
    # Drop None values so callers can pass optional params freely.
    cleaned = {k: v for k, v in params.items() if v is not None}
    resp = _http_post(url, cleaned)
    if not resp.get("ok"):
        raise TelegramAPIError(method, resp.get("description", "unknown error"), resp)
    return resp


def _split_chunks(text: str, max_len: int = CHUNK_MAX) -> List[str]:
    """Split ``text`` into <= ``max_len`` char chunks at paragraph or line breaks.

    Strategy: try paragraph (``\\n\\n``) breaks within budget, then line
    breaks, then a hard slice. Prefer the latest break that leaves the
    chunk above half-budget so we don't produce many tiny chunks.
    """
    if len(text) <= max_len:
        return [text]
    chunks: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        window = remaining[:max_len]
        # Prefer paragraph break
        cut = window.rfind("\n\n")
        if cut < max_len // 2:
            cut = window.rfind("\n")
        if cut < max_len // 2:
            cut = max_len  # hard cut
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


def send_message(
    chat_id: int | str,
    text: str,
    parse_mode: Optional[str] = None,
    message_thread_id: Optional[int] = None,
    reply_to_message_id: Optional[int] = None,
    disable_notification: Optional[bool] = None,
) -> List[dict]:
    """Send ``text`` to ``chat_id``. Auto-chunks if needed.

    Args:
        chat_id: Telegram chat id (int or str).
        text: Message body.
        parse_mode: ``None`` (default = plain text), ``"Markdown"``, or
            ``"MarkdownV2"``. Plain text avoids the entire class of
            "Bad Request: can't parse entities" silent failures.
        message_thread_id: For forum topics.
        reply_to_message_id: For replies.
        disable_notification: Silent send.

    Returns:
        List of API response payloads, one per chunk sent.

    Raises:
        TelegramAPIError: if any chunk's ``ok`` is false.
    """
    if not text:
        raise ValueError("send_message: text must be non-empty")

    chunks = _split_chunks(text)
    total = len(chunks)
    responses: List[dict] = []
    for i, chunk in enumerate(chunks, start=1):
        body = chunk if total == 1 else f"[{i}/{total}] {chunk}"
        resp = call(
            "sendMessage",
            chat_id=chat_id,
            text=body,
            parse_mode=parse_mode,
            message_thread_id=message_thread_id,
            reply_to_message_id=reply_to_message_id,
            disable_notification=disable_notification,
        )
        responses.append(resp)
    return responses


def set_message_reaction(chat_id: int | str, message_id: int, emoji: str = "👀") -> dict:
    """Acknowledge an incoming message with an emoji reaction."""
    reaction = json.dumps([{"type": "emoji", "emoji": emoji}])
    return call("setMessageReaction", chat_id=chat_id, message_id=message_id, reaction=reaction)


def get_me() -> dict:
    return call("getMe")


def _http_post_multipart(url: str, fields: dict, file_field: str, file_path: str,
                         filename: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Multipart/form-data POST. Used by sendDocument; everything else
    routes through ``_http_post``. Stdlib only — no requests dependency.
    """
    import os
    import uuid

    boundary = f"----metasphere-{uuid.uuid4().hex}"
    body = bytearray()
    for k, v in fields.items():
        if v is None:
            continue
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += f"{v}\r\n".encode()
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="{file_field}"; '
        f'filename="{os.path.basename(filename)}"\r\n'
    ).encode()
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += file_bytes
    body += f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8", errors="replace")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        raise TelegramAPIError("sendDocument", f"non-JSON response: {payload[:200]}", {}) from e


def send_document(chat_id: int | str, file_path: str,
                  caption: Optional[str] = None,
                  filename: Optional[str] = None) -> dict:
    """Upload ``file_path`` to ``chat_id`` via sendDocument.

    Args:
        chat_id: target chat.
        file_path: local path to the file to upload.
        caption: optional message caption shown beneath the file.
        filename: override the filename Telegram displays. Defaults to
            the basename of ``file_path``.
    """
    cfg = _config()
    url = f"{cfg.api_base}/sendDocument"
    name = filename or file_path.rsplit("/", 1)[-1]
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption
    resp = _http_post_multipart(url, fields, "document", file_path, name)
    if not resp.get("ok"):
        raise TelegramAPIError("sendDocument", resp.get("description", "unknown error"), resp)
    return resp


def _cc_outbound_to_orchestrator(body: str) -> None:
    """Best-effort: mirror an outbound telegram send into @orchestrator's
    inbox as a !info message. No-op when the sender is @orchestrator
    (or unset). Never raises — the telegram send is authoritative, and
    a failure to CC must not break the user-facing send path.

    This is the only place that writes the CC. Callers that need to
    both send to Telegram and CC the orchestrator should go through
    :func:`send_with_cc`; the doc path additionally calls this helper
    directly after its multipart send.
    """
    sender = (os.environ.get("METASPHERE_AGENT_ID") or "").strip()
    if not sender or sender == "@orchestrator":
        return
    try:
        from metasphere import messages as _messages  # lazy to avoid cycle

        cc_body = f"[telegram-cc from {sender}]\n{body}"
        _messages.send_message(
            "@orchestrator",
            "!info",
            cc_body,
            from_agent=sender,
            wake=False,
        )
    except Exception:  # noqa: BLE001 — CC is best-effort
        pass


def send_with_cc(
    chat_id: int | str,
    text: Optional[str] = None,
    *,
    parse_mode: Optional[str] = None,
    message_thread_id: Optional[int] = None,
    reply_to_message_id: Optional[int] = None,
    disable_notification: Optional[bool] = None,
    document_path: Optional[str] = None,
    caption: Optional[str] = None,
    filename: Optional[str] = None,
) -> List[dict] | dict:
    """Single choke-point wrapper that sends to Telegram AND mirrors
    the content into @orchestrator's inbox as !info when the sender
    agent is not @orchestrator.

    Exactly one of ``text`` or ``document_path`` must be provided.
    For text sends this forwards to :func:`send_message`; for document
    sends it forwards to :func:`send_document` (``caption``/``filename``
    are honored only in that branch).

    The CC fires only on a successful telegram send — if the send
    raises, nothing is written to the orchestrator inbox. The CC
    itself is best-effort and never raises.
    """
    if document_path is not None:
        if text is not None:
            raise ValueError("send_with_cc: pass text OR document_path, not both")
        resp = send_document(chat_id, document_path, caption=caption, filename=filename)
        cc_body = caption if caption else f"[sent document: {document_path}]"
        _cc_outbound_to_orchestrator(cc_body)
        return resp

    if text is None:
        raise ValueError("send_with_cc: text or document_path required")
    responses = send_message(
        chat_id,
        text,
        parse_mode=parse_mode,
        message_thread_id=message_thread_id,
        reply_to_message_id=reply_to_message_id,
        disable_notification=disable_notification,
    )
    _cc_outbound_to_orchestrator(text)
    return responses
