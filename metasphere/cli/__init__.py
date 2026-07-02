"""Per-subcommand shims behind the ``metasphere`` CLI entrypoint.

Each module in this package backs one top-level subcommand (``metasphere
agent``, ``metasphere msg``, ...). Modules expose a ``main(argv)`` (or
``handle(argv)``) callable plus two constants — ``DESCRIPTION`` and
``USAGE`` — that ``metasphere.cli._registry`` reads when composing the
top-level help and ``docs/CLI.md``. Real logic lives in sibling
``metasphere.*`` modules; the shims only parse argv and dispatch.
"""
