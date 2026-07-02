"""``metasphere docs`` — regenerate ``docs/CLI.md`` from the registry.

Walks ``metasphere.cli._registry.SUBCOMMANDS`` and reads each handler
module's ``DESCRIPTION`` + ``USAGE`` constants without ever calling the
handler. Emits a single Markdown document that mirrors the top-level
``metasphere --help`` output, so the published docs stay in lockstep
with whatever the registry currently routes. Run as a developer task,
not part of the runtime path.
"""

from __future__ import annotations

import sys
from pathlib import Path


DESCRIPTION = "Regenerate docs/CLI.md from each handler's DESCRIPTION + USAGE."

USAGE = """\
Usage: metasphere docs [--check] [--output PATH]

Regenerate the CLI reference document by walking the subcommand
registry and reading each handler's DESCRIPTION + USAGE constants.
Output defaults to docs/CLI.md in the metasphere-agents repo.

Options:
  --check          Compare against the existing file and exit non-zero
                   if they differ. Used in CI to catch drift between
                   handler help text and the committed reference.
  --output PATH    Write to PATH instead of docs/CLI.md.

Takes no other arguments.
"""


def _repo_root() -> Path:
    # metasphere/cli/docs.py -> repo root is two parents up. Always
    # the installed-package location (the source checkout that
    # ``pip install -e .`` is editable-linked to).
    return Path(__file__).resolve().parents[2]


def _cwd_repo_root(start: Path | None = None) -> Path | None:
    # Walk upward from ``start`` (default: cwd) looking for a
    # metasphere-agents checkout — i.e. a directory carrying both
    # ``pyproject.toml`` and ``metasphere/__init__.py``. This lets
    # ``metasphere docs`` (and any other generator that defaults
    # under the repo) write to the *invoking* worktree instead of
    # the editable-installed main checkout, which is what an
    # operator running the command from a worktree intends.
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "metasphere" / "__init__.py"
        ).is_file():
            return candidate
    return None


def _default_output() -> Path:
    return (_cwd_repo_root() or _repo_root()) / "docs" / "CLI.md"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0

    check = False
    out: Path = _default_output()
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--check":
            check = True
        elif a == "--output":
            if i + 1 >= len(argv):
                sys.stderr.write("metasphere docs: --output needs a path\n")
                return 2
            out = Path(argv[i + 1])
            i += 1
        elif a.startswith("--output="):
            out = Path(a.split("=", 1)[1])
        else:
            sys.stderr.write(f"metasphere docs: unknown arg: {a}\n\n")
            sys.stderr.write(USAGE)
            return 2
        i += 1

    from metasphere.cli._registry import render_docs_md
    rendered = render_docs_md()

    if check:
        existing = out.read_text() if out.exists() else ""
        if existing == rendered:
            sys.stdout.write(f"docs: {out} is up to date\n")
            return 0
        sys.stderr.write(f"docs: {out} is stale; run `metasphere docs`\n")
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered)
    sys.stdout.write(f"wrote {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
