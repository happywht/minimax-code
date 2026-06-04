"""Seed default sub-agents.

The ``agents`` table is created empty by migration 001. The frontend
@-picker (``MessageInput``) shows agents from ``agent.list_agents``,
but without seed rows the picker would always be empty on a fresh
install. This migration inserts two default agents that match the
mock backend's hardcoded list (``general``, ``researcher``).

``INSERT OR IGNORE`` keeps the migration idempotent — re-running it
on a DB that already has these rows (e.g. the user created them via
the UI) is a no-op. The ``name`` column is UNIQUE so duplicate
inserts are silently skipped.
"""

from __future__ import annotations

from typing import Any

VERSION = 3

DDL = r"""
-- ---------------------------------------------------------------------------
-- Seed default agents
-- ---------------------------------------------------------------------------
-- Idempotent: INSERT OR IGNORE skips rows whose ``name`` already exists.
INSERT OR IGNORE INTO agents (name, system_prompt, tool_allowlist, model)
    VALUES (
        'general',
        'You are a helpful general-purpose AI assistant. Answer questions clearly and concisely. When writing code, follow best practices and include comments.',
        NULL,
        NULL
    );

INSERT OR IGNORE INTO agents (name, system_prompt, tool_allowlist, model)
    VALUES (
        'researcher',
        'You are a web research assistant. Summarize findings clearly, cite sources when possible, and structure your output with headings and bullet points.',
        '["search_files","read_file"]',
        NULL
    );
"""


def run(conn: Any) -> None:
    """Apply the seed-agents migration to ``conn``."""
    conn.executescript(DDL)


__all__ = ["DDL", "VERSION", "run"]
