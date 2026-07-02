"""Tests for ``metasphere addressbook sync-slack`` (slack client mocked)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from metasphere import contacts as _contacts
from metasphere.cli import addressbook as _cli
from metasphere.paths import Paths


@pytest.fixture(autouse=True)
def _wire_paths(tmp_paths: Paths, monkeypatch):
    """set_surface_names resolves paths via contacts.resolve → point at tmp."""
    from metasphere import contacts as _c

    monkeypatch.setattr(_c, "resolve", lambda: tmp_paths)
    _contacts.clear_cache()
    yield
    _contacts.clear_cache()


def test_sync_slack_populates_surface_map(tmp_paths, capsys):
    members = [{"id": "U1", "name": "bob"}, {"id": "U2", "name": "alice"}]
    with patch("metasphere.slack.api.list_users", return_value=members):
        rc = _cli.main(["sync-slack", "--surface", "slack-explorer"])
    assert rc == 0
    # written under the surface_type 'slack' (standalone reverse map)
    assert _contacts.reverse_lookup("U1", "slack", tmp_paths) == "bob"
    assert _contacts.reverse_lookup("U2", "slack-explorer", tmp_paths) == "alice"
    assert "synced 2" in capsys.readouterr().out


def test_sync_slack_missing_scope_is_noop(tmp_paths, capsys):
    """Empty user list (missing_scope upstream) → 0 synced, exit 0, no crash."""
    with patch("metasphere.slack.api.list_users", return_value=[]):
        rc = _cli.main(["sync-slack"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "users:read" in err
