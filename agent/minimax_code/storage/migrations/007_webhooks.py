"""Webhook configuration storage.

Stores inbound webhook endpoints (GitHub / Gitee push events,
custom webhooks) and their action mappings (trigger code-review,
send-message, etc.).

v0.5.0 — Security Sandbox.
"""

from __future__ import annotations

from typing import Any

from . import run_script

VERSION = 7

DDL = r"""
-- ---------------------------------------------------------------------------
-- webhooks (inbound endpoint configuration)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS webhooks (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    source        TEXT NOT NULL,              -- 'github'|'gitee'|'custom'
    url_path      TEXT NOT NULL UNIQUE,       -- '/hooks/wh_xxx'
    secret        TEXT,                       -- HMAC verification key
    enabled       INTEGER NOT NULL DEFAULT 1,
    action_type   TEXT NOT NULL,              -- 'code-review'|'send-message'
    action_config TEXT NOT NULL DEFAULT '{}', -- JSON config
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhooks_source ON webhooks(source);
CREATE INDEX IF NOT EXISTS idx_webhooks_enabled ON webhooks(enabled);
"""


def run(conn: Any) -> None:
    """Apply the webhooks migration to ``conn``."""
    run_script(conn, DDL)


__all__ = ["DDL", "VERSION", "run"]
