"""``metasphere update`` CLI dispatcher.

Subcommand surface::

    metasphere update                    # one-shot run (chatty)
    metasphere update --quiet            # one-shot, log only
    metasphere update --enable           # turn auto-update on + register cron job
    metasphere update --disable          # turn auto-update off + unregister job
    metasphere update --status           # print current config + last result
    metasphere update --register-job     # install/refresh cron job from current config
    metasphere update --templates        # interactive opt-in for drifted shipped templates

The actual update flow lives in :mod:`metasphere.update`. This module is
just an argv parser.
"""

from __future__ import annotations

import sys

from metasphere import paths as _paths
from metasphere import update as _update


_HELP = __doc__ or ""


def _enable() -> int:
    paths = _paths.resolve()
    cfg = _update.load_config(paths)
    cfg.enabled = True
    _update.save_config(cfg, paths)
    job = _update.register_job(cfg, paths)
    print(f"auto-update: enabled (cron: {job.cron_expr})")
    return 0


def _disable() -> int:
    paths = _paths.resolve()
    cfg = _update.load_config(paths)
    cfg.enabled = False
    _update.save_config(cfg, paths)
    _update.unregister_job(paths)
    print("auto-update: disabled")
    return 0


def _status() -> int:
    sys.stdout.write(_update.status_text())
    return 0


def _register_job() -> int:
    paths = _paths.resolve()
    cfg = _update.load_config(paths)
    job = _update.register_job(cfg, paths)
    print(f"auto-update: cron job registered ({job.cron_expr}, enabled={job.enabled})")
    return 0


def _run(quiet: bool) -> int:
    result = _update.run_update(quiet=quiet)
    return 0 if result.ok else 1


def _templates() -> int:
    return _update.run_templates_interactive()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        sys.stdout.write(_HELP)
        return 0

    quiet = False
    rest: list[str] = []
    for a in argv:
        if a == "--quiet":
            quiet = True
        else:
            rest.append(a)

    if not rest:
        return _run(quiet=quiet)
    head = rest[0]
    if head == "--enable":
        return _enable()
    if head == "--disable":
        return _disable()
    if head == "--status":
        return _status()
    if head == "--register-job":
        return _register_job()
    if head == "--templates":
        return _templates()
    if head in ("run", "now"):
        return _run(quiet=quiet)

    sys.stderr.write(f"metasphere update: unknown option: {head}\n\n")
    sys.stderr.write(_HELP)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
