"""Structural pin for the 'no unguarded tmux discovery' invariant.

Background. PR #5/#6 closed a pytest-sandbox-escape leak: a sandboxed
test that sent a high-priority !task with a default wake would cold-start
a REAL claude REPL (or kill / capture / type into a live pane), because
the tmux binary was discovered with a bare ``shutil.which("tmux")`` /
literal ``["tmux", ...]`` argv that never consulted the sandbox guard.
The fix hoisted the predicate into :func:`metasphere.tmux.tmux_sandboxed`
and routed every discovery site through a resolver that hands back
:data:`metasphere.tmux.PYTEST_TMUX_SENTINEL` (a guaranteed-nonexistent
path) under pytest.

The gap this test closes. That "route every site through the guard"
property was convention-only — a new ``subprocess.run(["tmux", ...])``
anywhere in the package silently reopens the hole, and no unit test
would catch it (the offending path just wouldn't be exercised, or a
sibling test would monkeypatch over it). PR #6 itself missed exactly
one site this way: ``gateway/monitoring._tmux_list_sessions`` kept a
bare ``["tmux", ...]`` argv until this test forced it through the guard.

The invariant, stated structurally: no ``subprocess`` / ``os.exec*``
argv list anywhere in the package (outside ``tmux.py``, which *defines*
the guarded resolver) may have a bare string-literal ``"tmux"`` as its
first element. Every real exec must go through a ``_tmux_bin()``-style
resolver that returns the sentinel when :func:`tmux_sandboxed` is true.
``shutil.which("tmux")`` is deliberately NOT banned — it is the correct
body of a guarded ``_tmux_bin`` and is only ever reached after the
``tmux_sandboxed()`` check above it.

If this test fails: do not add the literal to an allowlist. Replace the
bare ``"tmux"`` argv head with a call to the module's guarded resolver
(see ``metasphere.agents._tmux_bin`` or ``gateway.monitoring._tmux_bin``
for the pattern).
"""

from __future__ import annotations

import ast
from pathlib import Path

import metasphere

# ``tmux.py`` defines the guarded resolver (_find_tmux) and legitimately
# holds the only bare references the invariant would otherwise flag.
_EXEMPT_FILES = {"tmux.py"}

# argv-bearing callables whose first list element is the program to exec.
_EXEC_CALLS = {
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("os", "execvp"),
    ("os", "execv"),
    ("os", "execvpe"),
}


def _package_source_files() -> list[Path]:
    root = Path(metasphere.__file__).parent
    return [
        p
        for p in root.rglob("*.py")
        # Tests may construct literal ["tmux", ...] argv as fixtures/asserts.
        if "tests" not in p.parts and p.name not in _EXEMPT_FILES
    ]


def _first_argv_element(call: ast.Call) -> ast.expr | None:
    """Return the first element of the argv list passed to an exec call.

    Handles both ``run(["tmux", ...], ...)`` and the ``os.execvp(path,
    ["tmux", ...])`` shape where argv is the second positional arg.
    """
    for arg in call.args:
        if isinstance(arg, ast.List) and arg.elts:
            return arg.elts[0]
    return None


def _is_exec_call(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return False
    return (func.value.id, func.attr) in _EXEC_CALLS


def _bare_tmux_argv_heads(source: str) -> list[int]:
    """Line numbers of exec calls whose argv[0] is the literal ``"tmux"``."""
    tree = ast.parse(source)
    hits: list[int] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_exec_call(node)):
            continue
        head = _first_argv_element(node)
        if isinstance(head, ast.Constant) and head.value == "tmux":
            hits.append(node.lineno)
    return hits


def test_no_unguarded_tmux_argv_heads_in_package():
    """Every tmux exec routes through the sandbox-guarded resolver.

    A bare ``["tmux", ...]`` argv head bypasses ``tmux_sandboxed()`` and
    reopens the PR #5/#6 pytest-escape leak. This scans the whole package
    (excluding ``tmux.py`` and the test tree) and fails with the exact
    file:line of any offender.
    """
    offenders: list[str] = []
    for path in _package_source_files():
        for lineno in _bare_tmux_argv_heads(path.read_text()):
            offenders.append(f"{path}:{lineno}")

    assert not offenders, (
        "Bare literal \"tmux\" argv head(s) found — these bypass the "
        "tmux_sandboxed() guard and reopen the pytest sandbox-escape leak "
        "(PR #5/#6). Route through a guarded _tmux_bin() resolver instead "
        "of the literal:\n  " + "\n  ".join(offenders)
    )


def test_invariant_scanner_detects_a_bare_head():
    """Guard the guard: the AST scanner actually flags a bare argv head
    (so a future refactor that neuters the scanner fails loudly here)."""
    sample = 'import subprocess\nsubprocess.run(["tmux", "kill-server"])\n'
    assert _bare_tmux_argv_heads(sample) == [2]


def test_invariant_scanner_ignores_guarded_resolver_calls():
    """The scanner must NOT flag the legitimate patterns: a resolver call
    as argv head, or ``shutil.which("tmux")`` inside a guarded _tmux_bin."""
    sample = (
        "import subprocess, shutil\n"
        "def _tmux_bin():\n"
        "    return shutil.which('tmux') or 'tmux'\n"
        "subprocess.run([_tmux_bin(), 'list-sessions'])\n"
    )
    assert _bare_tmux_argv_heads(sample) == []
