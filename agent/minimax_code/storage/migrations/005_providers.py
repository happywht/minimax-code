"""Providers table — multi-provider LLM configuration.

Enables the agent to store multiple LLM providers (MiniMax, OpenAI,
智谱 GLM, DeepSeek, etc.) each with its own protocol, base URL, API
key, and model list.  The ``providers`` table replaces the hard-coded
``CANDIDATE_MODELS`` / ``MODEL_META`` dicts in ``handlers_model.py``.

Also extends ``model_prefs`` with a ``provider_id`` column so the
user's model selection records *which* provider the model belongs to.
"""

from __future__ import annotations

from typing import Any

from . import run_script

VERSION = 5

DDL = r"""
-- ---------------------------------------------------------------------------
-- providers (multi-provider LLM configuration)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS providers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    protocol    TEXT NOT NULL DEFAULT 'anthropic',
    base_url    TEXT NOT NULL,
    api_key_set INTEGER NOT NULL DEFAULT 0,
    models      TEXT NOT NULL DEFAULT '[]',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Seed: built-in MiniMax provider (Anthropic protocol).
INSERT OR IGNORE INTO providers (
    id, name, protocol, base_url, api_key_set, models, enabled,
    created_at, updated_at
) VALUES (
    'builtin-minimax',
    'MiniMax',
    'anthropic',
    'https://api.minimaxi.com/anthropic',
    0,
    '[{"id":"MiniMax-M3","name":"MiniMax-M3","context_window":200000,"supports_tools":true,"is_default":true},{"id":"MiniMax-M3-fast","name":"MiniMax-M3-fast","context_window":128000,"supports_tools":true},{"id":"MiniMax-Code","name":"MiniMax-Code","context_window":1000000,"supports_tools":true}]',
    1,
    '2026-06-06T00:00:00Z',
    '2026-06-06T00:00:00Z'
);

-- Extend model_prefs with provider_id so the user's model choice
-- records which provider the model belongs to.
-- ``ALTER TABLE … ADD COLUMN`` is a no-op if the column already exists
-- in SQLite ≥ 3.35 (which silently succeeds), but to be safe across
-- all supported versions we use a try/except in the ``run`` function.
"""


def run(conn: Any) -> None:
    """Apply the providers migration to ``conn``."""
    run_script(conn, DDL)
    # Add provider_id column — may fail if column already exists (re-run).
    try:
        conn.execute(
            "ALTER TABLE model_prefs ADD COLUMN provider_id "
            "TEXT DEFAULT 'builtin-minimax'"
        )
    except Exception:
        # Column already exists — safe to ignore.
        pass


__all__ = ["DDL", "VERSION", "run"]
