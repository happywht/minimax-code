"""DAO — model preferences.

A *model preference* is the user-global choice of which LLM model the
agent should default to. It's a single-row table (``id = 1``) and the
DAO is a thin wrapper around the obvious ``SELECT`` / ``UPDATE``.

The candidate list (the models the user can pick from) is *not*
stored in the database — it's hard-coded in
:mod:`minimax_code.ipc.handlers_model` for the PoC. This DAO is
deliberately only responsible for the **current selection** so the
UI can read it back on boot and write it on toggle.
"""

from __future__ import annotations

from typing import Any

from ._base import now_iso, row_to_dict


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: The single row's primary key. The ``CHECK (id = 1)`` constraint on
#: the table enforces it at the storage level too.
_PK = 1

#: Default model that the migration seeds and the DAO falls back to
#: if the row is somehow missing (it never should be — the migration
#: inserts it idempotently — but the fallback keeps the IPC layer
#: honest if the table is ever truncated by hand).
DEFAULT_MODEL = "MiniMax-M3"


# ---------------------------------------------------------------------------
# Async DAO
# ---------------------------------------------------------------------------


class ModelPrefsDAO:
    """Async DAO for the single-row ``model_prefs`` table.

    The DAO does not own validation of the *candidate set* — the IPC
    handler is the only caller and it validates against the hard-coded
    candidate list before delegating here. This keeps the DAO small
    and side-effect free; it never rejects a model name on its own.
    """

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def get_current(self) -> dict[str, str]:
        """Return the user's currently-selected model and provider.

        Returns ``{"model_id": ..., "provider_id": ...}``.
        Falls back to defaults if the row is absent.
        """
        row = await self._db.fetchone(
            "SELECT current_model, provider_id FROM model_prefs WHERE id = ?",
            (_PK,),
        )
        if row is None:
            return {"model_id": DEFAULT_MODEL, "provider_id": "builtin-minimax"}
        model = row["current_model"] if hasattr(row, "keys") else row[0]
        provider = (
            row["provider_id"]
            if hasattr(row, "keys") and "provider_id" in (row.keys() if hasattr(row, "keys") else [])
            else "builtin-minimax"
        )
        model = str(model) if model else DEFAULT_MODEL
        provider = str(provider) if provider else "builtin-minimax"
        return {"model_id": model, "provider_id": provider}

    async def get_state(self) -> dict[str, Any]:
        """Return the full ``{id, current_model, updated_at}`` row.

        Used by tests and by callers that want to display the
        ``updated_at`` timestamp (e.g. "last changed at" tooltip).
        """
        row = await self._db.fetchone(
            "SELECT * FROM model_prefs WHERE id = ?",
            (_PK,),
        )
        if row is None:
            # Should not happen in practice — the migration seeds the
            # row. Return a synthetic empty dict so the IPC layer
            # can still send a meaningful response if the row was
            # deleted out from under us.
            return {
                "id": _PK,
                "current_model": DEFAULT_MODEL,
                "updated_at": None,
            }
        return row_to_dict(row) or {}

    async def set_current(
        self, model: str, provider_id: str | None = None
    ) -> dict[str, Any]:
        """Persist a new current model (and optionally provider); return the updated row.

        ``model`` is stored verbatim. ``provider_id`` defaults to
        ``"builtin-minimax"`` when omitted.
        """
        pid = provider_id or "builtin-minimax"
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE model_prefs SET current_model = ?, provider_id = ?, "
                "updated_at = ? WHERE id = ?",
                (str(model), pid, now_iso(), _PK),
            )
        return await self.get_state()


# ---------------------------------------------------------------------------
# Sync helpers (used by ad-hoc CLI / scripts / tests that don't want
# the asyncio wrapper).
# ---------------------------------------------------------------------------


def get_current_sync(db) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Sync helper — read the current model + provider from a stdlib ``Database``."""
    row = db.fetchone(
        "SELECT current_model, provider_id FROM model_prefs WHERE id = ?",
        (_PK,),
    )
    if row is None:
        return {"model_id": DEFAULT_MODEL, "provider_id": "builtin-minimax"}
    model = row["current_model"] if hasattr(row, "keys") else row[0]
    provider = (
        row["provider_id"]
        if hasattr(row, "keys") and "provider_id" in (row.keys() if hasattr(row, "keys") else [])
        else "builtin-minimax"
    )
    model = str(model) if model else DEFAULT_MODEL
    provider = str(provider) if provider else "builtin-minimax"
    return {"model_id": model, "provider_id": provider}


def set_current_sync(db, model: str, provider_id: str | None = None) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Sync helper — write a new current model and return the row."""
    pid = provider_id or "builtin-minimax"
    with db.transaction() as conn:
        conn.execute(
            "UPDATE model_prefs SET current_model = ?, provider_id = ?, "
            "updated_at = ? WHERE id = ?",
            (str(model), pid, now_iso(), _PK),
        )
    row = db.fetchone("SELECT * FROM model_prefs WHERE id = ?", (_PK,))
    return row_to_dict(row) or {}


__all__ = [
    "DEFAULT_MODEL",
    "ModelPrefsDAO",
    "get_current_sync",
    "set_current_sync",
]
