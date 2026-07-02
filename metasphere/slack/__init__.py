"""Slack surface adapter.

Mirrors the shape of :mod:`metasphere.telegram` for the Slack
ecosystem: ``api`` for outbound WebClient calls, ``handler`` for
inbound event routing under slack-bolt, ``poller`` for the Socket
Mode lifecycle, and ``adapter`` for the gateway-daemon
:class:`SurfaceAdapter` Protocol.

Tokens live at ``~/.metasphere/config/<surface_id>.env`` (e.g.
``slack-relay.env``) with ``SLACK_BOT_TOKEN=xoxb-...`` and
``SLACK_APP_TOKEN=xapp-...``. Per-surface env vars (``SLACK_BOT_TOKEN``
+ ``SLACK_APP_TOKEN``) override the file when set.
"""
