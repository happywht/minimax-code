"""Extend mcp_servers with SSE auth, OAuth, and per-tool states (v0.11.0).

Add columns needed to persist StreamableHTTP transport authentication and
per-server tool enablement.  Secrets (bearer token / OAuth client secret) are
stored in the local SQLite DB for this milestone; a future round can move them
to the OS keyring.

Idempotent by design (v0.11.0 hardening): each column is guarded with
``PRAGMA table_info`` so the migration replays cleanly over a database where
the columns already exist from a previous, partially-recorded run.
"""

from __future__ import annotations

from typing import Any

VERSION = 24

_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("bearer_token", "TEXT"),
    ("headers", "TEXT"),  # JSON object
    ("oauth_client_id", "TEXT"),
    ("oauth_client_secret", "TEXT"),
    ("oauth_scopes", "TEXT"),  # JSON list
    ("oauth_callback_port", "INTEGER"),
    ("tool_states", "TEXT"),  # JSON object {tool: enabled}
)


def run(conn: Any) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(mcp_servers)").fetchall()}
    for name, decl in _NEW_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE mcp_servers ADD COLUMN {name} {decl}")


__all__ = ["VERSION", "run"]
