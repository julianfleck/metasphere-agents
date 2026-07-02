"""Shared argv-parsing helpers for the CLI surface.

The flag-leak audit (closed end-to-end across 11 CLI surfaces between
2026-04 and 2026-05) ended with each surface defining its own
``_reject_flag_shape`` variant — same shape, slightly different
message format. This module collapses the simple two-arg variants
(value + op, single positional) into one helper so future CLI
surfaces don't need to copy-paste the pattern.

Surfaces whose helper has a legitimately different signature
(``cli.messages`` three-arg variant, ``cli.tasks`` task-id specific,
``cli.consolidate``/``trace``/``heartbeat``/``schedule`` with
combined int + flag validation) keep their local helpers.
"""

from __future__ import annotations

import sys


def reject_flag_shape(
    value: str,
    op: str,
    *,
    command: str,
    what: str = "argument",
    usage: str | None = None,
) -> int | None:
    """Return rc=2 + print error if ``value`` looks like a leaked CLI flag.

    Returns ``None`` when ``value`` is a normal positional, so callers
    can ``if rc is not None: return rc``.

    ``--help``/``-h`` are NOT intercepted here; callers should handle
    those before dispatching to this helper.

    Parameters
    ----------
    value : str
        The positional token to inspect.
    op : str
        The subcommand name (used in the error message).
    command : str
        The CLI command prefix (e.g. ``"metasphere session"``,
        ``"project"``, ``"hooks git"``).
    what : str
        What the positional is supposed to be (``"agent id"``,
        ``"project name"``, ``"path"``, …). Default: ``"argument"``.
    usage : str, optional
        A trailing usage hint appended to the error line.
    """
    if not value.startswith("-"):
        return None
    article = "an" if what[:1].lower() in "aeiou" else "a"
    msg = (
        f"{command} {op}: {value!r} looks like a CLI flag, "
        f"not {article} {what}."
    )
    if usage:
        msg = f"{msg} {usage}"
    sys.stderr.write(msg + "\n")
    return 2
