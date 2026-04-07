# core/

Reserved namespace for first-class metasphere modules that aren't shell scripts. Currently a scaffold; the populated subsystems live under `../scripts/`.

## `core/messaging/`
Future home for a structured messaging library (file format spec, validators, indexers) that the `messages` shell CLI in `../scripts/` will eventually depend on. Today it's only a `.messages/inbox/` placeholder so the directory has a fractal scope of its own.

## `core/memory/`
Future home for the CAM integration layer — search, sync, recall — beyond the thin shell wrapper currently in `~/.metasphere/bin/`. Today it's only a `.messages/inbox/` placeholder.

When promoting a script from `../scripts/` to a `core/` module, prefer Python or a real binary over bash, write tests alongside, and update `../scripts/<script>` to be a thin entrypoint that delegates here. Keep the bash CLIs as the user-facing surface.
