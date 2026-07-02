"""``metasphere questions`` — read view of the "needs from the operator" ledger.

Pretty-prints ``$METASPHERE_DIR/state/QUESTIONS.md`` — the
orchestrator-maintained canonical list of decisions / manual actions
blocking work across all projects (proposal
internal design notes §2). The morning briefing and
the heartbeat reminder both *render* from this file; this command is the
pull-on-demand companion so the list can be inspected without waiting for
a ping.

Read-only: it parses and renders, never writes. The orchestrator owns the
file. Parsing reuses :func:`metasphere.heartbeat.parse_questions` so the
CLI, the heartbeat reminder, and the briefing can never disagree on what
counts as a live, flagged item.
"""

from __future__ import annotations

DESCRIPTION = "Show the cross-project 'needs from the operator' question ledger."

USAGE = """\
Usage: metasphere questions [<filter>]

With no arguments, prints every live 🔴/🟡/🟢 item from QUESTIONS.md,
grouped by project and flag-sorted (🔴 blocking first). Filters:

  metasphere questions red      Only 🔴 blocking items.
  metasphere questions amber    Only 🟡 needed-soon items.
  metasphere questions green    Only 🟢 FYI items.
  metasphere questions open     🔴 + 🟡 (everything that isn't FYI).

Source: $METASPHERE_DIR/state/QUESTIONS.md (orchestrator-maintained).
Read-only — this command never edits the ledger.
"""


import datetime as _dt
import os
import sys

from metasphere import paths as _paths

_RED, _AMBER, _GREEN = "🔴", "🟡", "🟢"
_FLAG_ORDER = {_RED: 0, _AMBER: 1, _GREEN: 2}
_FLAG_BY_NAME = {
    "red": {_RED},
    "amber": {_AMBER},
    "yellow": {_AMBER},
    "green": {_GREEN},
    "open": {_RED, _AMBER},
}


def _stale_days_threshold() -> float:
    try:
        return float(os.environ.get("METASPHERE_QUESTIONS_STALE_DAYS", "7"))
    except ValueError:
        return 7.0


def _staleness_note(path, *, now: _dt.datetime | None = None) -> str | None:
    """Warn when the ledger file hasn't been touched in a while.

    The ledger is canonically *maintained* (items cleared the moment the
    operator answers), so its mtime is a fair proxy for "is anyone keeping this
    current." When it's gone untouched past ``METASPHERE_QUESTIONS_STALE_DAYS``
    (default 7), the read view says so — otherwise a long-resolved item reads
    as a live, blocking question. Returns ``None`` (no note) on any stat
    hiccup or a non-positive threshold, so this never blocks the render.
    """
    threshold = _stale_days_threshold()
    if threshold <= 0:
        return None
    try:
        mtime = _dt.datetime.fromtimestamp(
            path.stat().st_mtime, _dt.timezone.utc
        )
    except OSError:
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    age_days = (now - mtime).total_seconds() / 86400.0
    if age_days < threshold:
        return None
    return (
        f"⚠️  Ledger last updated {int(age_days)}d ago "
        f"({mtime.date().isoformat()}) — may be stale; items below "
        "may already be resolved."
    )


def _render(items: list, *, header_path, stale_note: str | None = None) -> str:
    """Render parsed Question items grouped by project, flag-sorted."""
    if not items:
        base = "QUESTIONS.md: no matching items."
        return f"{stale_note}\n{base}" if stale_note else base

    # Stable group order: first appearance of each project, then flag/text
    # order within a group (🔴 first). parse_questions yields items in file
    # order, so first-seen preserves the authored project ordering.
    project_order: list[str] = []
    by_project: dict[str, list] = {}
    for q in items:
        if q.project not in by_project:
            by_project[q.project] = []
            project_order.append(q.project)
        by_project[q.project].append(q)

    reds = sum(1 for q in items if q.flag == _RED)
    ambers = sum(1 for q in items if q.flag == _AMBER)
    greens = sum(1 for q in items if q.flag == _GREEN)
    head_bits = []
    if reds:
        head_bits.append(f"{reds} 🔴 blocking")
    if ambers:
        head_bits.append(f"{ambers} 🟡 soon")
    if greens:
        head_bits.append(f"{greens} 🟢 fyi")
    head = " · ".join(head_bits) if head_bits else f"{len(items)} items"

    out = [f"Needs from the operator — {head}"]
    if stale_note:
        out.append(stale_note)
    out.append("")
    for project in project_order:
        out.append(f"## {project or '(no project)'}")
        group = sorted(
            by_project[project],
            key=lambda q: (_FLAG_ORDER.get(q.flag, 9),),
        )
        for q in group:
            # ``q.text`` already carries the trailing ``(YYYY-MM-DD)`` when one
            # was authored (parse_questions extracts ``raised`` but leaves the
            # text intact), so don't re-append it here.
            out.append(f"  {q.flag} {q.text}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_questions(filter_: str | None = None) -> tuple[str, int]:
    """Return (rendered_text, rc). Shared by the CLI and the telegram view.

    ``filter_`` is one of red/amber/yellow/green/open or None (all). An
    unknown filter is treated as None (show everything) so a typo degrades
    to the full list rather than an error.
    """
    from metasphere import heartbeat as _hb

    paths = _paths.resolve()
    p = _hb._questions_file(paths)
    if not p.is_file():
        return (f"QUESTIONS.md not found at {p}.", 0)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        return (f"Could not read {p}: {exc}", 1)

    items = _hb.parse_questions(text)
    keep = _FLAG_BY_NAME.get((filter_ or "").strip().lower())
    if keep is not None:
        items = [q for q in items if q.flag in keep]
    stale_note = _staleness_note(p)
    return (_render(items, header_path=p, stale_note=stale_note), 0)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    filter_ = argv[0] if argv else None
    body, rc = render_questions(filter_)
    if rc == 0:
        print(body, end="" if body.endswith("\n") else "\n")
    else:
        print(body, file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
