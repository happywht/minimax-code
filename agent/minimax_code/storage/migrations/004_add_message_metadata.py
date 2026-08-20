"""Add ``metadata`` JSON column to the ``messages`` table.

The v0.3.0 thinking_count channel emits per-message metadata
(thinking_count, tokens_in, tokens_out) on the trailing ``done=True``
chunk. The frontend types already define ``MessageMetadata`` and the
event wire carries it — but the database has no column to persist it.

This migration adds a nullable ``metadata`` JSON column so that
message history loaded from the database can round-trip the metadata
without losing it.
"""

from __future__ import annotations

from typing import Any

from . import run_script

VERSION = 4

DDL = r"""
-- ---------------------------------------------------------------------------
-- Add metadata JSON column to messages
-- ---------------------------------------------------------------------------
-- Stores per-message metadata such as thinking_count, token usage, etc.
-- Nullable so existing rows remain valid; only set on the final assistant
-- message of a turn.
ALTER TABLE messages ADD COLUMN metadata JSON;
"""


def run(conn: Any) -> None:
    """Apply the add-message-metadata migration to ``conn``."""
    run_script(conn, DDL)


__all__ = ["DDL", "VERSION", "run"]
