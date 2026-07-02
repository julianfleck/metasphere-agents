"""Shared body-input resolution for the agent-facing send CLIs.

Special characters in a message body — parens, bullets (•), German
low-quotes („ "), backticks, ``$``, double-quotes, newlines — repeatedly
break when an agent embeds them as a shell-quoted positional argument
(the recurring ``/bin/bash: eval: syntax error near unexpected token (``
papercut). These helpers add a **zero-quoting** input path: read the body
verbatim from stdin (positional ``"-"``) or a file (``--body-file PATH``),
so an agent can heredoc / pipe arbitrary content with no shell escaping
at all. The positional-string form stays for simple bodies.

Used by ``metasphere {slack,telegram,message} send`` (argparse) and
``metasphere msg send`` (bare positional parsing).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, TextIO

#: Positional sentinel meaning "read the body from stdin".
STDIN_SENTINEL = "-"

#: Shared ``--body-file`` help text so every CLI documents it identically.
BODY_FILE_HELP = (
    "Read the message body verbatim from this file (UTF-8) instead of a "
    "positional argument — no shell quoting needed for ( ) • backticks $ "
    'newlines. Use "-" as the positional body to read from stdin.'
)


def add_body_file_arg(parser: argparse.ArgumentParser) -> None:
    """Register the standard ``--body-file PATH`` flag on a send parser."""
    parser.add_argument("--body-file", default=None, metavar="PATH",
                        help=BODY_FILE_HELP)


def _strip_one_trailing_newline(s: str) -> str:
    """Drop exactly one trailing newline (``\\n`` or ``\\r\\n``).

    Heredocs / ``echo`` append a trailing newline the author rarely intends
    as part of the body, so we strip one. INTERNAL newlines — and any
    deliberate *extra* trailing blank lines — are preserved verbatim.
    """
    if s.endswith("\r\n"):
        return s[:-2]
    if s.endswith("\n"):
        return s[:-1]
    return s


def resolve_body(
    text: Optional[str],
    body_file: Optional[str] = None,
    *,
    stdin: Optional[TextIO] = None,
    allow_empty: bool = False,
) -> str:
    """Resolve a message body from the richest source the caller offered.

    Precedence: ``--body-file PATH`` > positional ``"-"`` (stdin) >
    positional ``text``. File / stdin content is read as UTF-8 verbatim
    except a single trailing newline is stripped (see
    :func:`_strip_one_trailing_newline`).

    Raises :class:`ValueError` on conflicting inputs (both a positional
    body and ``--body-file``) or an empty body (unless ``allow_empty``),
    and propagates :class:`OSError` if ``body_file`` cannot be read.
    """
    if body_file is not None:
        if text not in (None, STDIN_SENTINEL):
            raise ValueError(
                "provide the body EITHER as a positional argument OR via "
                "--body-file, not both"
            )
        body = _strip_one_trailing_newline(
            Path(body_file).read_text(encoding="utf-8")
        )
    elif text == STDIN_SENTINEL:
        stream = stdin
        if stream is None:
            import sys

            stream = sys.stdin
        body = _strip_one_trailing_newline(stream.read())
    else:
        body = text or ""

    if not allow_empty and not body.strip():
        raise ValueError("empty message body")
    return body
