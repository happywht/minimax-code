"""DAO — LLM providers.

Each provider represents an LLM API endpoint with its own protocol
(anthropic / openai), base URL, API key, and model list. The DAO
handles CRUD plus the convenience ``list_models`` that flattens all
enabled providers' model arrays into a single list with ``provider_id``
and ``protocol`` annotations.
"""

from __future__ import annotations

import uuid
from typing import Any

from ._base import dumps_json, loads_json, now_iso, row_to_dict

# ---------------------------------------------------------------------------
# Async DAO
# ---------------------------------------------------------------------------


class ProviderDAO:
    """Async DAO for the ``providers`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def list(self) -> list[dict[str, Any]]:
        """Return all providers ordered by name."""
        rows = await self._db.fetchall(
            "SELECT * FROM providers ORDER BY name"
        )
        result: list[dict[str, Any]] = []
        for r in rows:
            d = row_to_dict(r)
            if d:
                d["models"] = loads_json(d.get("models")) or []
                result.append(d)
        return result

    async def get(self, provider_id: str) -> dict[str, Any] | None:
        """Return a single provider by ID, or ``None``."""
        row = await self._db.fetchone(
            "SELECT * FROM providers WHERE id = ?",
            (provider_id,),
        )
        if row is None:
            return None
        d = row_to_dict(row)
        if d:
            d["models"] = loads_json(d.get("models")) or []
        return d

    async def create(
        self,
        *,
        name: str,
        protocol: str,
        base_url: str,
        models: list[dict[str, Any]] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Insert a new provider and return the row."""
        pid = f"provider-{uuid.uuid4().hex[:12]}"
        now = now_iso()
        models_json = dumps_json(models or [])
        api_key_set = 0
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT INTO providers "
                "(id, name, protocol, base_url, api_key_set, models, "
                "enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, name, protocol, base_url, api_key_set, models_json,
                 1 if enabled else 0, now, now),
            )
        result = await self.get(pid)
        return result or {}

    async def update(
        self,
        provider_id: str,
        *,
        name: str | None = None,
        protocol: str | None = None,
        base_url: str | None = None,
        models: list[dict[str, Any]] | None = None,
        enabled: bool | None = None,
        api_key_set: int | None = None,
    ) -> dict[str, Any]:
        """Update selected fields of a provider; return the updated row."""
        sets: list[str] = []
        params: list[Any] = []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if protocol is not None:
            sets.append("protocol = ?")
            params.append(protocol)
        if base_url is not None:
            sets.append("base_url = ?")
            params.append(base_url)
        if models is not None:
            sets.append("models = ?")
            params.append(dumps_json(models))
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(1 if enabled else 0)
        if api_key_set is not None:
            sets.append("api_key_set = ?")
            params.append(api_key_set)
        if not sets:
            result = await self.get(provider_id)
            return result or {}
        sets.append("updated_at = ?")
        params.append(now_iso())
        params.append(provider_id)
        async with self._db.transaction() as conn:
            await conn.execute(
                f"UPDATE providers SET {', '.join(sets)} WHERE id = ?",
                params,
            )
        result = await self.get(provider_id)
        return result or {}

    async def delete(self, provider_id: str) -> None:
        """Delete a provider by ID.

        Does **not** clean up the keyring — the caller (IPC handler)
        is responsible for calling ``secrets.clear_provider_key``.
        """
        async with self._db.transaction() as conn:
            await conn.execute(
                "DELETE FROM providers WHERE id = ?",
                (provider_id,),
            )

    async def list_models(self) -> list[dict[str, Any]]:
        """Flatten all enabled providers' model lists.

        Each model dict is annotated with ``provider_id``,
        ``provider_name``, and ``protocol`` from the owning provider.
        Returns a flat list suitable for the ``model.list`` IPC method.
        """
        providers = await self.list()
        out: list[dict[str, Any]] = []
        for p in providers:
            if not p.get("enabled"):
                continue
            for m in p.get("models") or []:
                entry = dict(m)
                entry["provider_id"] = p["id"]
                entry["provider_name"] = p["name"]
                entry["provider"] = p["name"]
                entry["protocol"] = p.get("protocol", "anthropic")
                out.append(entry)
        return out


__all__ = ["ProviderDAO"]
