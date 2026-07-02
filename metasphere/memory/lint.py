"""Lint the hand-curated ``MEMORY.md`` index for hygiene drift.

The auto-memory index loads into *every* agent session, so it has a hard
budget: the harness emits a "MEMORY.md is N lines and KKB, only part was
loaded" warning once it grows past roughly one screen. The drift that
gets it there is mechanical and recurring — agents append fat index lines
that duplicate their backing topic file verbatim, and topic files get
deleted out from under their index link. Catching that today means an
agent eyeballing the file each cycle; this turns it into a deterministic,
exit-code-bearing check that a person, a posthook, or CI can run.

It also catches the *reverse* of a dead link — an orphan topic file that
exists on disk but is named by no index entry. Recall surfaces the index
into every session, so a topic file with no index pointer is effectively
unreachable: the fact is captured but never loaded. These accrue when an
agent writes the topic file and forgets the one-line ``MEMORY.md`` stub
the harness asks for.

A third hygiene check flags topic files whose frontmatter ``description``
is itself overlong. ``--suggest``/``--apply`` rewrite a bloated index
line *toward* that description, so when the description exceeds the line
budget the index entry can never be made to fit and auto-compression
silently no-ops. Surfacing the file aims the fix at the root cause (the
topic file's frontmatter) rather than the un-shrinkable index line.

Report-only by design: shortening an index line means re-distilling the
entry's hook, dropping a dead link means deciding whether the target
should be rewritten or removed, and an orphan file might want either an
index stub or deletion — all judgement calls the backing topic file
informs, so lint never mutates ``MEMORY.md`` or the topic files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .auto import _LINK_RE, _default_memory_root

# The memory convention documented in the harness reminder: "Keep index
# entries to one line under ~200 chars". Long *titles* legitimately push
# past this, so the default is a soft ceiling, not a hard rule.
DEFAULT_MAX_LINE = 200

# The index file itself is never an orphan candidate; topic memories are
# identified positively by their YAML frontmatter (see ``_is_topic_file``),
# which excludes logs like ``night-log.md`` without hardcoding their names.
_INDEX_NAME = "MEMORY.md"


@dataclass
class LongLine:
    lineno: int
    length: int
    preview: str
    # A ready-to-paste replacement built from the linked topic file's
    # frontmatter ``description`` (the canonical one-line summary), or
    # ``None`` when no description is available. Populated only when
    # ``lint_index(..., suggest=True)``.
    suggestion: str | None = None
    suggestion_length: int | None = None


@dataclass
class DeadLink:
    lineno: int
    title: str
    rel_path: str


@dataclass
class OrphanFile:
    rel_path: str


@dataclass
class LongDescription:
    """A topic file whose frontmatter ``description`` exceeds ``max_line``.

    The index line for an entry embeds the description inside
    ``- [<desc>](file.md) — <hook>``, so a description longer than the
    line budget *guarantees* an overlong index line and makes ``--apply``
    a no-op (the description-swap can never come out shorter than the
    budget). Flagging it points the fix at the root cause — the topic
    file's own frontmatter — rather than at the un-shrinkable index line.
    """

    rel_path: str
    length: int
    preview: str


@dataclass
class LintReport:
    path: Path
    exists: bool
    size_bytes: int = 0
    line_count: int = 0
    index_entries: int = 0
    max_line: int = DEFAULT_MAX_LINE
    long_lines: list[LongLine] = field(default_factory=list)
    dead_links: list[DeadLink] = field(default_factory=list)
    orphan_files: list[OrphanFile] = field(default_factory=list)
    long_descriptions: list[LongDescription] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # ``long_descriptions`` is deliberately *not* a gate: a >max-line
        # description already forces its index line over budget (caught by
        # ``long_lines``), so gating here would be redundant for the common
        # case and would make the lint permanently red on any mature store
        # whose facts simply have rich summaries. It is surfaced as guidance
        # — where to trim so ``--apply`` can finally shrink the entry — not
        # as a pass/fail violation.
        return (
            self.exists
            and not self.long_lines
            and not self.dead_links
            and not self.orphan_files
        )


def _is_topic_file(path: Path) -> bool:
    """A topic memory is a ``*.md`` file opening with YAML frontmatter.

    The frontmatter probe (first non-empty line is ``---``) distinguishes
    fact files from the index and from free-form logs such as
    ``night-log.md``, and avoids reading large logs in full.
    """
    if path.name == _INDEX_NAME:
        return False
    try:
        with path.open(encoding="utf-8") as fh:
            for raw in fh:
                stripped = raw.strip()
                if not stripped:
                    continue  # tolerate leading blank lines
                return stripped == "---"
    except (OSError, UnicodeDecodeError):
        return False
    return False  # empty file


def _topic_description(path: Path) -> str | None:
    """Return the ``description:`` value from a topic file's frontmatter.

    The memory convention defines frontmatter ``description`` as the
    "one-line summary used to decide relevance during recall" — exactly
    the role an index hook plays — so it is the canonical source for a
    compressed index entry. Returns ``None`` when the file has no
    frontmatter description (only the first frontmatter block is read).
    """
    try:
        with path.open(encoding="utf-8") as fh:
            first = fh.readline()
            if first.strip() != "---":
                return None
            for raw in fh:
                stripped = raw.strip()
                if stripped == "---":
                    break  # end of frontmatter
                if stripped.startswith("description:"):
                    value = stripped[len("description:"):].strip()
                    # Frontmatter scalars may be single- or double-quoted.
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1].strip()
                    return value or None
    except (OSError, UnicodeDecodeError):
        return None
    return None


def _suggest_line(line: str, root: Path) -> str | None:
    """Rewrite an overlong index line using the topic file's description.

    Replaces only the *first* link's bloated title with the linked
    file's frontmatter ``description``, preserving the link target and
    the entire trailing hook (dates, ``[[cross-links]]``) verbatim — the
    cross-link graph the convention asks agents to maintain survives. The
    result may still exceed ``max_line`` when the bloat lives in the tail
    rather than the title; the caller flags that so the agent can trim
    further by hand.
    """
    match = _LINK_RE.search(line)
    if not match:
        return None
    rel_path = match.group(2)
    description = _topic_description((root / rel_path).resolve())
    if not description:
        return None
    new_link = f"[{description}]({rel_path})"
    return line[: match.start()] + new_link + line[match.end():]


@dataclass
class ApplyResult:
    path: Path
    exists: bool
    applied: int = 0
    # Overlong lines whose suggestion was *not* a strict improvement
    # (no backing description, or the description-swap came out no shorter
    # because the bloat lives in the tail) — left untouched for a human.
    skipped: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    # (lineno, old_length, new_length) for each rewritten line.
    changes: list[tuple[int, int, int]] = field(default_factory=list)


def _count_index_entries(text: str) -> int:
    return sum(1 for line in text.splitlines() if _LINK_RE.search(line))


def apply_suggestions(
    root: Path | None = None,
    *,
    max_line: int = DEFAULT_MAX_LINE,
) -> ApplyResult:
    """Rewrite overlong ``MEMORY.md`` lines in place with their suggestions.

    Only lines whose description-swap is a *strict* length improvement are
    rewritten; an entry whose bloat lives in the tail (so the swap comes
    out no shorter) or that has no backing description is left verbatim for
    a human to trim. The rewrite is non-lossy by construction — it swaps
    only the bloated title for the topic file's canonical frontmatter
    ``description`` and preserves the link target plus the entire trailing
    hook (dates, ``[[cross-links]]``).

    A shrink-detection guard refuses the write if the rewrite would change
    the *number* of index entries (it only ever edits lines in place, never
    adds or drops them), so a regex/encoding surprise can't silently
    truncate the index.
    """
    root = (root or _default_memory_root()).resolve()
    index = root / "MEMORY.md"
    if not index.is_file():
        return ApplyResult(path=index, exists=False)

    text = index.read_text(encoding="utf-8")
    # split("\n") (not splitlines) round-trips a trailing newline exactly,
    # so a file ending in "\n" is rewritten byte-identically apart from the
    # lines we deliberately change.
    lines = text.split("\n")
    result = ApplyResult(
        path=index, exists=True, bytes_before=len(text.encode("utf-8"))
    )

    for i, line in enumerate(lines):
        if not _LINK_RE.search(line) or len(line) <= max_line:
            continue
        suggestion = _suggest_line(line, root)
        if suggestion is None or len(suggestion) >= len(line):
            result.skipped += 1
            continue
        result.changes.append((i + 1, len(line), len(suggestion)))
        lines[i] = suggestion
        result.applied += 1

    new_text = "\n".join(lines)
    result.bytes_after = len(new_text.encode("utf-8"))

    if result.applied and _count_index_entries(new_text) != _count_index_entries(text):
        raise RuntimeError(
            "apply aborted: rewrite changed the index entry count "
            "(expected an in-place edit only) — MEMORY.md left untouched"
        )
    if result.applied:
        index.write_text(new_text, encoding="utf-8")
    return result


def render_apply(result: ApplyResult) -> str:
    """Human-readable summary of an :func:`apply_suggestions` run."""
    if not result.exists:
        return f"MEMORY.md not found at {result.path}"
    kb_before = result.bytes_before / 1024
    kb_after = result.bytes_after / 1024
    saved = result.bytes_before - result.bytes_after
    out = [
        f"applied {result.applied} rewrite(s), skipped {result.skipped} "
        f"(no-win/no-description)",
        f"MEMORY.md: {kb_before:.1f}KB → {kb_after:.1f}KB "
        f"(saved {saved / 1024:.1f}KB)",
    ]
    for lineno, old_len, new_len in result.changes:
        out.append(f"  L{lineno}: {old_len} → {new_len} chars")
    return "\n".join(out)


def lint_index(
    root: Path | None = None,
    *,
    max_line: int = DEFAULT_MAX_LINE,
    suggest: bool = False,
) -> LintReport:
    """Inspect ``<root>/MEMORY.md`` for overlong index lines and dead links.

    ``root`` defaults to the auto-memory directory resolved the same way
    :class:`~metasphere.memory.auto.AutoMemoryStrategy` resolves it.
    """
    root = (root or _default_memory_root()).resolve()
    index = root / "MEMORY.md"
    if not index.is_file():
        return LintReport(path=index, exists=False, max_line=max_line)

    text = index.read_text(encoding="utf-8")
    lines = text.splitlines()
    report = LintReport(
        path=index,
        exists=True,
        size_bytes=len(text.encode("utf-8")),
        line_count=len(lines),
        max_line=max_line,
    )

    for i, line in enumerate(lines, start=1):
        if not _LINK_RE.search(line):
            continue  # only list-item index entries carry a (file.md) link
        report.index_entries += 1
        length = len(line)
        if length > max_line:
            preview = line[:80] + ("…" if len(line) > 80 else "")
            entry = LongLine(lineno=i, length=length, preview=preview)
            if suggest:
                suggestion = _suggest_line(line, root)
                if suggestion is not None:
                    entry.suggestion = suggestion
                    entry.suggestion_length = len(suggestion)
            report.long_lines.append(entry)

    # Dead-link scan: every (file.md) target must resolve to a real file
    # under the memory root (same containment guard auto-memory enforces).
    for i, line in enumerate(lines, start=1):
        for match in _LINK_RE.finditer(line):
            title, rel_path = match.group(1), match.group(2)
            target = (root / rel_path).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                report.dead_links.append(DeadLink(lineno=i, title=title, rel_path=rel_path))
                continue
            if not target.is_file():
                report.dead_links.append(DeadLink(lineno=i, title=title, rel_path=rel_path))

    # Orphan scan: topic files on disk that no index entry names. Recall
    # loads the index, so an unreferenced fact file is never surfaced.
    #
    # Membership is tested against the literal ``(<name>)`` link target
    # rather than ``_LINK_RE``: a title carrying nested brackets (e.g.
    # "Use `[idle]` sigil") defeats the title group, but the parenthesised
    # target is unambiguous, and the leading ``(`` anchors the name so a
    # short filename can't match inside a longer one.
    #
    # The same pass flags topic files whose frontmatter ``description``
    # exceeds ``max_line``: that description is what ``--suggest``/``--apply``
    # compress an index line *toward*, so when it is itself overlong the
    # index line can never fit and auto-compression is a dead end. Surfacing
    # the file points the fix at the root cause instead of the symptom.
    for path in sorted(root.glob("*.md")):
        if not _is_topic_file(path):
            continue
        if f"({path.name})" not in text:
            report.orphan_files.append(OrphanFile(rel_path=path.name))
        description = _topic_description(path)
        if description is not None and len(description) > max_line:
            preview = description[:80] + ("…" if len(description) > 80 else "")
            report.long_descriptions.append(
                LongDescription(rel_path=path.name, length=len(description), preview=preview)
            )

    return report


def render(report: LintReport) -> str:
    """Human-readable lint summary."""
    if not report.exists:
        return f"MEMORY.md not found at {report.path}"

    kb = report.size_bytes / 1024
    out = [
        f"MEMORY.md: {report.line_count} lines, {kb:.1f}KB, "
        f"{report.index_entries} index entries (max-line={report.max_line})",
    ]
    if report.long_lines:
        out.append(f"\n{len(report.long_lines)} overlong index line(s):")
        for ll in report.long_lines:
            out.append(f"  L{ll.lineno}: {ll.length} chars — {ll.preview}")
            if ll.suggestion is not None:
                fit = "✓" if (ll.suggestion_length or 0) <= report.max_line else "still over"
                out.append(f"    ↳ suggest ({ll.suggestion_length} chars, {fit}): {ll.suggestion}")
    if report.dead_links:
        out.append(f"\n{len(report.dead_links)} dead link(s):")
        for dl in report.dead_links:
            out.append(f"  L{dl.lineno}: [{dl.title}]({dl.rel_path}) → missing")
    if report.orphan_files:
        out.append(f"\n{len(report.orphan_files)} orphan topic file(s) (no index entry):")
        for of in report.orphan_files:
            out.append(f"  {of.rel_path}")
    if report.long_descriptions:
        out.append(
            f"\n{len(report.long_descriptions)} topic file(s) with an overlong "
            f"frontmatter description (root cause of un-shrinkable index lines):"
        )
        for ld in report.long_descriptions:
            out.append(f"  {ld.rel_path}: {ld.length} chars — {ld.preview}")
    if report.ok:
        out.append("\nclean ✓")
    return "\n".join(out)
