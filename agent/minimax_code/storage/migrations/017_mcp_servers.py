"""Add MCP server configuration persistence (v0.11.0 Milestone 1).

Stores user-configured external MCP servers so they survive agent
restarts.  The ``command`` column holds a JSON-encoded argv list;
``env`` holds a JSON-encoded object of extra environment variables.
Only ``stdio`` transport is wired end-to-end in this milestone; the
schema reserves ``url`` for future SSE support.
"""

from __future__ import annotations

from typing import Any

from . import run_script

VERSION = 17

DDL = r"""
CREATE TABLE IF NOT EXISTS mcp_servers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    transport   TEXT NOT NULL DEFAULT 'stdio' CHECK (transport IN ('stdio', 'sse')),
    command     TEXT,                       -- JSON list of argv strings
    url         TEXT,                       -- reserved for SSE transport
    env         TEXT,                       -- JSON object of environment overrides
    enabled     INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_enabled_updated
    ON mcp_servers (enabled, updated_at DESC);
"""


def run(conn: Any) -> None:
    run_script(conn, DDL)
