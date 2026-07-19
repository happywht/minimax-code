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

from ...models import default_model
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
#: honest if the table is ever truncated by hand). Sourced from the
#: data-driven registry (:func:`minimax_code.models.default_model`, the
#: fusion of grok's ``xai-grok-models`` from R45) so the storage seed
#: fallback, the DAO None-fallback, and the IPC layer all share a single
#: baked-in document — edit ``DEFAULT_MODELS_JSON`` to change the global
#: default (R46 wiring; the migration SQL seed is a one-shot snapshot).
DEFAULT_MODEL = default_model()


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

    async def get_current(self) -> dict[str, Any]:
        """Return the user's currently-selected model, provider, and effort override.

        Returns ``{"model_id": ..., "provider_id": ..., "reasoning_effort": ...}``.
        ``reasoning_effort`` is ``None`` when no override is stored (the
        pre-R61 default — use the model's own effort). Falls back to
        defaults if the row is absent.
        """
        row = await self._db.fetchone(
            "SELECT current_model, provider_id, reasoning_effort "
            "FROM model_prefs WHERE id = ?",
            (_PK,),
        )
        if row is None:
            return {
                "model_id": DEFAULT_MODEL,
                "provider_id": "builtin-minimax",
                "reasoning_effort": None,
            }
        model = row["current_model"] if hasattr(row, "keys") else row[0]
        provider = (
            row["provider_id"]
            if hasattr(row, "keys") and "provider_id" in (row.keys() if hasattr(row, "keys") else [])
            else "builtin-minimax"
        )
        effort = (
            row["reasoning_effort"]
            if hasattr(row, "keys") and "reasoning_effort" in (row.keys() if hasattr(row, "keys") else [])
            else None
        )
        model = str(model) if model else DEFAULT_MODEL
        provider = str(provider) if provider else "builtin-minimax"
        effort = str(effort) if effort else None
        return {"model_id": model, "provider_id": provider, "reasoning_effort": effort}

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

    async def set_reasoning_effort(self, effort: str | None) -> dict[str, Any]:
        """Persist a reasoning-effort override; return the updated row.

        ``effort`` is stored verbatim — the IPC layer owns token
        validation (and canonicalises the ``max`` alias of ``xhigh``
        before reaching here). ``None`` clears the override so the
        next turn falls back to the model's own default effort
        (the pre-R61 behaviour). Mirrors :meth:`set_current`'s shape
        (write + touch ``updated_at`` + return full row) so the
        caller gets a uniform "what changed" response.
        """
        stored = str(effort) if effort else None
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE model_prefs SET reasoning_effort = ?, "
                "updated_at = ? WHERE id = ?",
                (stored, now_iso(), _PK),
            )
        return await self.get_state()


# ---------------------------------------------------------------------------
# Sync helpers (used by ad-hoc CLI / scripts / tests that don't want
# the asyncio wrapper).
# ---------------------------------------------------------------------------


def get_current_sync(db) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Sync helper — read the current model + provider + effort from a stdlib ``Database``."""
    row = db.fetchone(
        "SELECT current_model, provider_id, reasoning_effort "
        "FROM model_prefs WHERE id = ?",
        (_PK,),
    )
    if row is None:
        return {
            "model_id": DEFAULT_MODEL,
            "provider_id": "builtin-minimax",
            "reasoning_effort": None,
        }
    model = row["current_model"] if hasattr(row, "keys") else row[0]
    provider = (
        row["provider_id"]
        if hasattr(row, "keys") and "provider_id" in (row.keys() if hasattr(row, "keys") else [])
        else "builtin-minimax"
    )
    effort = (
        row["reasoning_effort"]
        if hasattr(row, "keys") and "reasoning_effort" in (row.keys() if hasattr(row, "keys") else [])
        else None
    )
    model = str(model) if model else DEFAULT_MODEL
    provider = str(provider) if provider else "builtin-minimax"
    effort = str(effort) if effort else None
    return {"model_id": model, "provider_id": provider, "reasoning_effort": effort}


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


def set_reasoning_effort_sync(db, effort: str | None) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Sync helper — write a reasoning-effort override and return the row.

    Mirrors :meth:`ModelPrefsDAO.set_reasoning_effort` for the sync path
    (CLI / ad-hoc scripts). ``None`` clears the override.
    """
    stored = str(effort) if effort else None
    with db.transaction() as conn:
        conn.execute(
            "UPDATE model_prefs SET reasoning_effort = ?, updated_at = ? WHERE id = ?",
            (stored, now_iso(), _PK),
        )
    row = db.fetchone("SELECT * FROM model_prefs WHERE id = ?", (_PK,))
    return row_to_dict(row) or {}


__all__ = [
    "DEFAULT_MODEL",
    "ModelPrefsDAO",
    "get_current_sync",
    "set_current_sync",
    "set_reasoning_effort_sync",
]
