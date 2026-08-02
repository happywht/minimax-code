"""Extend mcp_servers with SSE auth, OAuth, and per-tool states (v0.11.0).

Adds columns needed to persist StreamableHTTP transport authentication and
per-server tool enablement.  Secrets (bearer token / OAuth client secret) are
stored in the local SQLite DB for this milestone; a future round can move them
to the OS keyring.
"""

from __future__ import annotations

from typing import Any

VERSION = 24

DDL = r"""
ALTER TABLE mcp_servers ADD COLUMN bearer_token TEXT;
ALTER TABLE mcp_servers ADD COLUMN headers TEXT;                  -- JSON object
ALTER TABLE mcp_servers ADD COLUMN oauth_client_id TEXT;
ALTER TABLE mcp_servers ADD COLUMN oauth_client_secret TEXT;
ALTER TABLE mcp_servers ADD COLUMN oauth_scopes TEXT;             -- JSON list
ALTER TABLE mcp_servers ADD COLUMN oauth_callback_port INTEGER;
ALTER TABLE mcp_servers ADD COLUMN tool_states TEXT;              -- JSON object {tool: enabled}
"""


def run(conn: Any) -> None:
    conn.executescript(DDL)
