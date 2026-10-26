"""Nullable configured profile: roots use host, trusted children inherit."""
from __future__ import annotations

VERSION = 40


def run(conn):
    conn.execute("ALTER TABLE agents ADD COLUMN execution_profile_id TEXT DEFAULT NULL")
