"""Allow the explicit Streamable HTTP MCP transport without rewriting config values."""
from __future__ import annotations

from typing import Any

VERSION = 45
DISABLE_FOREIGN_KEYS = True

# sqlite_master SQL emitted by the unchanged formal 017 CREATE + 024 ALTERs.
# Keep its exact spelling/comments: stripping SQL whitespace could erase changes
# inside quoted defaults. External equivalent rewrites also fail closed; indexes
# and triggers are separate schema objects and remain independently preserved.
_FORMAL_SCHEMA_SQL = """CREATE TABLE mcp_servers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    transport   TEXT NOT NULL DEFAULT 'stdio' CHECK (transport IN ('stdio', 'sse')),
    command     TEXT,                       -- JSON list of argv strings
    url         TEXT,                       -- reserved for SSE transport
    env         TEXT,                       -- JSON object of environment overrides
    enabled     INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
""" + (
    ", bearer_token TEXT, headers TEXT, oauth_client_id TEXT, oauth_client_secret TEXT, "
    "oauth_scopes TEXT, oauth_callback_port INTEGER, tool_states TEXT)"
)


def run(conn: Any) -> None:
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] or not conn.in_transaction:
        raise RuntimeError("MCP schema rebuild must use the migration runner transaction")
    columns = (
        "id,name,transport,command,url,env,enabled,created_at,updated_at,"
        "bearer_token,headers,oauth_client_id,oauth_client_secret,oauth_scopes,"
        "oauth_callback_port,tool_states"
    )
    if [row[1] for row in conn.execute("PRAGMA table_xinfo(mcp_servers)")] != columns.split(","):
        raise RuntimeError("Unsupported MCP server columns; refusing a lossy schema rebuild")
    shape = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='mcp_servers'"
    ).fetchone()
    if shape is None or shape[0] != _FORMAL_SCHEMA_SQL:
        raise RuntimeError("Unsupported MCP server table definition; refusing a lossy schema rebuild")
    objects = conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name='mcp_servers' "
        "AND type IN ('index','trigger') AND sql IS NOT NULL"
    ).fetchall()
    conn.execute("""CREATE TABLE mcp_servers_v45 (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        transport TEXT NOT NULL DEFAULT 'stdio'
            CHECK (transport IN ('stdio', 'sse', 'streamable_http')),
        command TEXT,
        url TEXT,
        env TEXT,
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        bearer_token TEXT,
        headers TEXT,
        oauth_client_id TEXT,
        oauth_client_secret TEXT,
        oauth_scopes TEXT,
        oauth_callback_port INTEGER,
        tool_states TEXT
    )""")
    conn.execute(
        f"INSERT INTO mcp_servers_v45(rowid,{columns}) SELECT rowid,{columns} FROM mcp_servers"
    )
    conn.execute("DROP TABLE mcp_servers")
    conn.execute("ALTER TABLE mcp_servers_v45 RENAME TO mcp_servers")
    for row in objects:
        conn.execute(row[0])
