import os
import re
import pytest
from pathlib import Path
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# Test-pollution guard
#
# Session-scoped detection of tests that wrote into the real
# ``~/.metasphere/`` tree. Mechanism: snapshot the file set at session
# start; at session end, fail if any file appeared whose first bytes
# match the ``BYTES:`` signature used by ``fake_http_get`` fixtures in
# ``test_telegram.py``. Signature-based (not mtime-based) so live
# daemons — gateway, heartbeat, schedule — writing to real dirs during
# a test run don't produce false positives.
#
# This exists because PR #3's test suite leaked
# ``~/.metasphere/attachments/555/biggest.bin`` into the production
# home directory. The per-module autouse fixture in test_telegram.py
# prevents that specific leak proactively; this conftest check
# detects any future recurrence reactively, whatever test introduces it.
# ---------------------------------------------------------------------------

_REAL_METASPHERE = Path.home() / ".metasphere"
_TEST_SIGNATURE = b"BYTES:"

#: Directories under ``~/.metasphere/`` where ANY new file appearing
#: during the test session is a red flag — these are per-project /
#: per-agent state stores that live daemons append to but never
#: CREATE new files in mid-session (except midnight rollover for the
#: stream dir, which is extremely rare during a pytest run).
_GUARDED_SUBDIRS = (
    "tasks",                 # Fix 1 canonical global tasks bucket
    "projects",              # per-project state (.tasks/, .messages/, ...)
    "attachments",           # telegram attachment downloads
    "messages",              # per-agent inboxes/outboxes
    "telegram/stream",       # daily JSONL stream
)

#: File extensions we treat as "pollution if new" under the guarded
#: dirs. ``.md`` = task/message/doc fixtures, ``.msg`` = agent inbox
#: messages, ``.lock`` = task-lock sidecars from fixture writes,
#: ``.jsonl`` = new-day stream files that would only appear mid-test
#: via a fixture (live daemons append to an existing day's file).
_POLLUTION_EXTS = (".md", ".msg", ".lock", ".jsonl", ".bin")

#: Real production chat id for Julian — the only chat that legitimately
#: appears in ``~/.metasphere/telegram/stream/*.jsonl``. Anything else
#: in an appended stream tail is a test fixture leaking into live.
_JULIAN_CHAT_ID = 228838013

#: Regex over bytes: matches the nested Telegram ``"chat":{"id":N`` shape
#: used by the poller archiver. Test-fixture lines are identified as
#: "any appended line whose chat.id != ``_JULIAN_CHAT_ID``". Allow-list
#: beats deny-list — fixture authors can pick any chat_id they want, but
#: the real prod id is a single known value.
_CHAT_ID_RE = re.compile(rb'"chat":\s*\{\s*"id"\s*:\s*(\d+)')

#: Fake unix-epoch timestamp (``date=1700000000`` ≈ 2023-11-14T22:13Z)
#: shared by effectively every fixture in this repo. Any appended
#: stream line carrying this date is pollution regardless of chat id.
_FIXTURE_DATE_MARKER = b'"date": 1700000000'


def pytest_sessionstart(session):
    if not _REAL_METASPHERE.exists():
        session._metasphere_file_snapshot = set()
        session._metasphere_stream_sizes = {}
        return
    try:
        session._metasphere_file_snapshot = {
            str(p) for p in _REAL_METASPHERE.rglob("*") if p.is_file()
        }
    except OSError:
        session._metasphere_file_snapshot = set()
    # Snapshot existing stream files' sizes so we can diff only the
    # tail added during the session (fixtures appending to an existing
    # day's JSONL look like "chat_id: 42" lines, not new files).
    stream_dir = _REAL_METASPHERE / "telegram" / "stream"
    sizes: dict[str, int] = {}
    if stream_dir.is_dir():
        try:
            for p in stream_dir.glob("*.jsonl"):
                try:
                    sizes[str(p)] = p.stat().st_size
                except OSError:
                    continue
        except OSError:
            pass
    session._metasphere_stream_sizes = sizes


def _under_guarded_subdir(path: Path) -> bool:
    try:
        rel = path.relative_to(_REAL_METASPHERE)
    except ValueError:
        return False
    rel_str = str(rel)
    for sub in _GUARDED_SUBDIRS:
        if rel_str == sub or rel_str.startswith(sub + "/"):
            return True
    return False


def pytest_sessionfinish(session, exitstatus):
    snapshot = getattr(session, "_metasphere_file_snapshot", None)
    if snapshot is None or not _REAL_METASPHERE.exists():
        return
    leaked: list[str] = []

    # Pass 1: legacy signature check — any file whose head is b"BYTES:"
    # (leaked from test_telegram.py's fake_http_get fixture).
    try:
        for p in _REAL_METASPHERE.rglob("*"):
            if not p.is_file():
                continue
            s = str(p)
            if s in snapshot:
                continue
            try:
                head = p.read_bytes()[: len(_TEST_SIGNATURE)]
            except OSError:
                continue
            if head == _TEST_SIGNATURE:
                leaked.append(f"{s} (fake_http_get signature)")
    except OSError:
        return

    # Pass 2: new-file check under guarded subdirs. Any brand-new file
    # with a pollution-shaped extension is almost certainly a test
    # fixture — the Fix 1 2026-04-15 incident caught 41 ``.md`` task
    # files this way.
    try:
        for p in _REAL_METASPHERE.rglob("*"):
            if not p.is_file():
                continue
            s = str(p)
            if s in snapshot:
                continue
            if not _under_guarded_subdir(p):
                continue
            if p.suffix.lower() in _POLLUTION_EXTS:
                leaked.append(f"{s} (new file under guarded subdir)")
    except OSError:
        pass

    # Pass 3: content-injection check on existing stream JSONL files.
    # Live daemons APPEND to today's stream; tests that forget to
    # sandbox ``archiver.DEFAULT_DIR`` will also append, inserting
    # lines whose chat_id / username matches a test fixture.
    sizes = getattr(session, "_metasphere_stream_sizes", {}) or {}
    stream_dir = _REAL_METASPHERE / "telegram" / "stream"
    if stream_dir.is_dir():
        try:
            for p in stream_dir.glob("*.jsonl"):
                sp = str(p)
                before = sizes.get(sp)
                if before is None:
                    # Brand-new stream file; caught by pass 2 above.
                    continue
                try:
                    now = p.stat().st_size
                except OSError:
                    continue
                if now <= before:
                    continue
                try:
                    with open(p, "rb") as f:
                        f.seek(before)
                        tail = f.read()
                except OSError:
                    continue
                # Pass 3a: any chat.id other than Julian's real one is
                # a fixture. This is allow-list, not deny-list — the
                # rule doesn't drift when someone adds a new fixture
                # with a new chat_id.
                for m in _CHAT_ID_RE.finditer(tail):
                    try:
                        cid = int(m.group(1))
                    except ValueError:
                        continue
                    if cid != _JULIAN_CHAT_ID:
                        leaked.append(
                            f"{sp} (fixture line appended: chat.id={cid}, "
                            f"only {_JULIAN_CHAT_ID} is allowed)"
                        )
                        break
                # Pass 3b: fake unix-epoch timestamp, regardless of
                # chat.id. Every fixture in this repo uses 1700000000.
                if _FIXTURE_DATE_MARKER in tail:
                    leaked.append(
                        f"{sp} (fixture line appended: date=1700000000)"
                    )
        except OSError:
            pass

    if leaked:
        pytest.exit(
            "TEST POLLUTION detected under real ~/.metasphere/:\n"
            + "\n".join(f"  {p}" for p in leaked)
            + "\n\nHint: redirect ATTACHMENTS_ROOT / DEBUG_LOG_PATH / "
              "archiver.DEFAULT_DIR / METASPHERE_DIR to tmp_path before "
              "invoking any handler/archiver/tasks code.",
            returncode=1,
        )


# ---------------------------------------------------------------------------
# Autouse sandbox
#
# Tests that use ``tmp_paths`` get their METASPHERE_DIR pointed at a
# per-test tmp dir automatically. Tests that DON'T use ``tmp_paths``
# (e.g. ``test_integration.py::_make_paths`` which builds Paths directly
# and ignores env) historically silently hit the real ``~/.metasphere/``
# and polluted live state stores. This fixture closes that gap: every
# test starts with METASPHERE_DIR pointed at ``tmp_path/_ms_sandbox``
# and every home-relative module-level constant monkeypatched to the
# same sandbox.
#
# LEARNING (2026-04-15): module-level ``os.path.expanduser("~/...")``
# constants are invisible to METASPHERE_DIR monkeypatch — they're
# evaluated once at import time. Any test that imports a module with
# such a constant and calls a function defaulting to it will write to
# real ``~/.metasphere/`` regardless of env. Must patch each module
# directly.
# ---------------------------------------------------------------------------

#: Modules whose home-relative constants we redirect in the autouse
#: fixture. Each entry: (import path, attribute name, lambda from
#: sandbox root → override value).
_HOME_CONSTANTS = (
    ("metasphere.telegram.archiver", "DEFAULT_DIR",
     lambda root: str(root / "telegram")),
    ("metasphere.telegram.attachments", "ATTACHMENTS_ROOT",
     lambda root: root / "attachments"),
    ("metasphere.telegram.attachments", "DEBUG_LOG_PATH",
     lambda root: root / "state" / "telegram_debug.log"),
    ("metasphere.telegram.poller", "DEFAULT_OFFSET_PATH",
     lambda root: str(root / "telegram" / "offset")),
    ("metasphere.telegram.commands", "METASPHERE_DIR",
     lambda root: str(root)),
    ("metasphere.cli.telegram", "CHAT_ID_FILE",
     lambda root: str(root / "config" / "telegram_chat_id_rewrite")),
    ("metasphere.cli.telegram", "CHAT_ID_FILE_CANONICAL",
     lambda root: str(root / "config" / "telegram_chat_id")),
    ("metasphere.cli.telegram", "CONTACTS_FILE",
     lambda root: str(root / "config" / "telegram_contacts.json")),
)


#: Functions whose ``__defaults__`` tuple needs a home-relative entry
#: swapped for the sandbox. Each tuple is
#: ``(module path, function name, default-name, resolver)`` and only
#: the matching default is rewritten — other defaults pass through.
#:
#: This exists because ``def f(x, base_dir=DEFAULT_DIR)`` captures the
#: value of ``DEFAULT_DIR`` at function-definition time (module import).
#: Monkeypatching the module-level ``DEFAULT_DIR`` attribute afterwards
#: doesn't change what a ``f()`` call with no ``base_dir=`` kwarg uses.
_FUNCTION_DEFAULTS = (
    ("metasphere.telegram.archiver", "archive_message", "base_dir",
     lambda root: str(root / "telegram")),
    ("metasphere.telegram.archiver", "archive_reaction", "base_dir",
     lambda root: str(root / "telegram")),
    ("metasphere.telegram.archiver", "save_latest", "base_dir",
     lambda root: str(root / "telegram")),
    ("metasphere.telegram.archiver", "telegram_context", "base_dir",
     lambda root: str(root / "telegram")),
    ("metasphere.telegram.archiver", "archive_outgoing", "base_dir",
     lambda root: str(root / "telegram")),
    ("metasphere.telegram.poller", "load_offset", "path",
     lambda root: str(root / "telegram" / "offset")),
    ("metasphere.telegram.poller", "save_offset", "path",
     lambda root: str(root / "telegram" / "offset")),
    ("metasphere.telegram.poller", "run_poll_iteration", "offset_path",
     lambda root: str(root / "telegram" / "offset")),
)


def _patch_function_default(monkeypatch, module, fn_name, param_name, new_value):
    """Rebuild ``fn.__defaults__`` with ``param_name`` replaced.

    Introspects the function signature to find ``param_name``'s position
    within the defaults tuple (which aligns with the trailing positional-
    -or-keyword params that have defaults), then creates a new tuple
    with just that slot swapped.
    """
    import inspect
    fn = getattr(module, fn_name, None)
    if fn is None or not callable(fn):
        return
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return
    # Defaults tuple aligns with positional-or-keyword params that have
    # defaults, in their source order.
    defaulted = [
        p for p in sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                       inspect.Parameter.POSITIONAL_ONLY)
        and p.default is not inspect.Parameter.empty
    ]
    if not defaulted:
        return
    names = [p.name for p in defaulted]
    if param_name not in names:
        return
    idx = names.index(param_name)
    current = fn.__defaults__ or ()
    if idx >= len(current):
        return
    new_defaults = current[:idx] + (new_value,) + current[idx + 1:]
    # Use monkeypatch to restore on test teardown.
    monkeypatch.setattr(fn, "__defaults__", new_defaults)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_corpus: test deliberately reads live ~/.metasphere/ "
        "state (schedule jobs, memory corpus, etc). Opts out of the "
        "autouse sandbox fixture. Do NOT use for tests that WRITE.",
    )


@pytest.fixture(autouse=True)
def _autosandbox_metasphere_dir(request, tmp_path, monkeypatch):
    """Every test starts with METASPHERE_DIR + all home-relative module
    constants + function ``__defaults__`` pointed at a fresh
    ``tmp_path/_ms_sandbox``.

    Tests that request ``tmp_paths`` still get their own setenv (last
    writer wins — ``tmp_paths`` runs AFTER this one if requested
    explicitly, so its METASPHERE_DIR for ``tmp_path/metasphere``
    overrides this sandbox). This fixture only matters for tests that
    DON'T opt into ``tmp_paths``.

    Tests marked ``@pytest.mark.real_corpus`` opt out entirely — they
    read live ``~/.metasphere/`` state (schedule jobs, memory corpus).
    Read-only by contract; if one of them ever writes, the session-end
    guard will catch it.
    """
    if request.node.get_closest_marker("real_corpus") is not None:
        yield
        return
    import importlib
    sandbox = tmp_path / "_ms_sandbox"
    sandbox.mkdir(exist_ok=True)
    monkeypatch.setenv("METASPHERE_DIR", str(sandbox))
    monkeypatch.delenv("METASPHERE_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("METASPHERE_SCOPE", raising=False)
    # Pass 1: monkeypatch module-level constants.
    for module_path, attr, resolver in _HOME_CONSTANTS:
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue
        try:
            monkeypatch.setattr(mod, attr, resolver(sandbox))
        except AttributeError:
            continue
    # Pass 2: monkeypatch compiled-in ``__defaults__`` for functions
    # that bind home-relative paths at definition time.
    for module_path, fn_name, param_name, resolver in _FUNCTION_DEFAULTS:
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue
        _patch_function_default(monkeypatch, mod, fn_name, param_name, resolver(sandbox))
    yield


@pytest.fixture
def tmp_paths(tmp_path: Path, monkeypatch) -> Paths:
    root = tmp_path / "metasphere"
    repo = tmp_path / "repo"
    scope = tmp_path / "repo"
    for p in (root, repo, scope):
        p.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("METASPHERE_DIR", str(root))
    monkeypatch.setenv("METASPHERE_PROJECT_ROOT", str(repo))
    monkeypatch.setenv("METASPHERE_SCOPE", str(scope))
    monkeypatch.delenv("METASPHERE_AGENT_ID", raising=False)
    return Paths(root=root, project_root=repo, scope=scope)
