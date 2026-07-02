# Migrations

Self-contained migration scripts for prior tools / harnesses that
metasphere can import state from.

## How it works

At install time, `install.sh` walks each subdirectory under
`migrate/` and:

1. Runs `<subdir>/detect.sh`. Exit `0` ⇒ the source is present on this
   host; exit non-zero ⇒ skip.
2. On detection, prompts the operator (interactive) or proceeds with
   the default (non-interactive) — see flags below.
3. Runs `<subdir>/migrate.sh` to perform the import.

Migrations are opt-out, not opt-in: if a detector returns true the
default answer is "yes, migrate".

## Adding a new migration source

Create a new subdirectory named after the source tool, e.g.
`migrate/oldharness/`, containing:

- `detect.sh` — POSIX-ish bash, no args. Exit `0` if the source is
  present on disk (typically by checking for a known config
  directory). Must be silent on stdout/stderr.
- `migrate.sh` — performs the import. Inherits these environment
  variables from `install.sh`:
  - `METASPHERE_DIR` — the install root (default `~/.metasphere`).
  - `INTERACTIVE` — `true`/`false`. Whether stdin is a TTY and `-y`
    wasn't passed.
  - `VERBOSE` — `true`/`false`.

  May emit any output. Should be idempotent — operators re-run
  `install.sh`.
- `README.md` (optional, recommended) — what this migration imports
  and any caveats.

Both scripts must be executable (`chmod +x`).

## Skipping a migration

Pass `--no-migrate-<name>` to `install.sh` (e.g.
`--no-migrate-openclaw`) to skip a specific migration regardless of
detection. The flag works in both interactive and `-y` mode.

## Currently shipped

- [openclaw](openclaw/README.md) — import config, workspace, memory
  db, SOUL.md, and skills from the OpenClaw predecessor harness.
