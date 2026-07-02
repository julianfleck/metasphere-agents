"""Uniform --help/-h handling across every metasphere CLI subcommand.

Every handler module registered in ``metasphere.cli._registry.REGISTRY``
must:

1. Accept ``--help`` / ``-h`` as the first argument and exit cleanly
   with rc=0, printing a usage message on stdout.
2. Expose a ``DESCRIPTION`` constant (one-line) used by the top-level
   ``metasphere --help`` listing.
3. Expose a ``USAGE`` constant (full --help body) used both by
   ``metasphere <subcmd> --help`` and by the auto-generated
   ``docs/CLI.md``.
"""

from __future__ import annotations

import importlib

import pytest

from metasphere.cli import _registry


SUBCOMMAND_NAMES = list(_registry.all_names())


@pytest.mark.parametrize("name", SUBCOMMAND_NAMES)
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_cli_help_exits_zero(name, flag, tmp_paths, capsys):
    handler = _registry.resolve(name)
    rc = handler([flag])
    assert rc == 0, f"{name} {flag} returned {rc}"
    out = capsys.readouterr().out
    assert out.strip(), f"{name} {flag} printed nothing on stdout"


@pytest.mark.parametrize("name", SUBCOMMAND_NAMES)
def test_cli_module_exports_description_and_usage(name):
    sc = _registry._by_name(name)
    mod = importlib.import_module(sc.module)
    desc = getattr(mod, "DESCRIPTION", None)
    usage = getattr(mod, "USAGE", None)
    assert desc and isinstance(desc, str), \
        f"{name}: missing DESCRIPTION constant"
    assert usage and isinstance(usage, str), \
        f"{name}: missing USAGE constant"
    # USAGE must not advertise the legacy ``python -m metasphere.cli.X``
    # form to humans — that's a discoverability + maintenance trap.
    assert "python -m metasphere.cli" not in usage, \
        f"{name}: USAGE leaks `python -m metasphere.cli...` form"


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_top_level_help_lists_every_subcommand(flag, capsys):
    from metasphere.cli import main as _main
    rc = _main.main([flag])
    assert rc == 0
    out = capsys.readouterr().out
    for name in SUBCOMMAND_NAMES:
        assert name in out, f"top-level --help missing subcommand: {name}"


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_status_help_does_not_run_status(flag, tmp_paths, capsys):
    """``metasphere status --help`` must print help, not query tmux/tasks.

    Regression: ``_status`` previously ignored argv and unconditionally
    rendered the live system summary, surprising users who passed
    ``--help`` expecting a usage message.
    """
    from metasphere.cli import status as _status

    rc = _status.main([flag])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Usage:" in out
    assert "Sessions:" not in out


def test_docs_check_passes_against_committed_reference():
    """`metasphere docs --check` must match the committed docs/CLI.md.

    Catches drift between handler USAGE constants and the rendered
    reference. Run `metasphere docs` to refresh.
    """
    from metasphere.cli import docs as _docs

    rc = _docs.main(["--check"])
    assert rc == 0, (
        "docs/CLI.md is out of date — run `metasphere docs` and commit"
    )
