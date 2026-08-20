"""Audit log — 100 % tool-call traceability.

Every tool dispatch in ``AgentCore._dispatch_tool`` produces a durable,
queryable audit record.  The table is append-only (no UPDATE path)
so records are immutable evidence.

v0.5.0 — Security Sandbox.
"""

from __future__ import annotations

from typing import Any

from . import run_script

VERSION = 6

DDL = r"""
-- ---------------------------------------------------------------------------
-- audit_log (immutable tool-call trail)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id            TEXT PRIMARY KEY,       -- 'aud_' + uuid hex
    session_id    TEXT,                   -- FK sessions.id (nullable for system calls)
    tool_name     TEXT NOT NULL,
    tool_args     TEXT,                   -- JSON string of sanitized args
    permission    TEXT,                   -- 'allow' | 'deny' | 'ask' | NULL
    result_status TEXT NOT NULL,          -- 'success' | 'fail' | 'timeout' | 'denied'
    exit_code     INTEGER,               -- for exec_command; NULL for other tools
    duration_ms   INTEGER,               -- wall-clock milliseconds
    error         TEXT,                   -- truncated error string
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_tool    ON audit_log(tool_name);
CREATE INDEX IF NOT EXISTS idx_audit_time    ON audit_log(created_at DESC);
"""


def run(conn: Any) -> None:
    """Apply the audit_log migration to ``conn``."""
    run_script(conn, DDL)


__all__ = ["DDL", "VERSION", "run"]
