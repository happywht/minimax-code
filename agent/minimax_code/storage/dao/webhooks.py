"""DAO — webhook configuration.

CRUD operations for the ``webhooks`` table plus a lookup-by-path
method used by the inbound HTTP handler to match incoming requests
to registered webhook endpoints.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ._base import (
    apply_pagination,
    dumps_json,
    loads_json,
    now_iso,
    row_to_dict,
)

logger = logging.getLogger(__name__)


class WebhookDAO:
    """Async DAO for the ``webhooks`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        source: str,
        url_path: str | None = None,
        secret: str | None = None,
        action_type: str = "send-message",
        action_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row_id = f"wh_{uuid.uuid4().hex[:10]}"
        path = url_path or f"/hooks/{row_id}"
        now = now_iso()
        config_json = dumps_json(action_config or {})
        sql = (
            "INSERT INTO webhooks "
            "(id, name, source, url_path, secret, enabled, "
            "action_type, action_config, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)"
        )
        params = (row_id, name, source, path, secret, action_type, config_json, now, now)
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone(
            "SELECT * FROM webhooks WHERE id = ?", (row_id,)
        )
        return _hydrate(row)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, webhook_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM webhooks WHERE id = ?", (webhook_id,)
        )
        return _hydrate(row) if row else None

    async def get_by_path(self, url_path: str) -> dict[str, Any] | None:
        """Lookup by inbound URL path (used by the HTTP handler)."""
        row = await self._db.fetchone(
            "SELECT * FROM webhooks WHERE url_path = ? AND enabled = 1",
            (url_path,),
        )
        return _hydrate(row) if row else None

    async def list_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        where, params = _filters(source=source, enabled=enabled)
        sql = f"SELECT * FROM webhooks {where} ORDER BY created_at DESC"
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def count(
        self,
        *,
        source: str | None = None,
    ) -> int:
        where, params = _filters(source=source)
        row = await self._db.fetchone(
            f"SELECT COUNT(*) AS n FROM webhooks {where}", tuple(params)
        )
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def update(
        self,
        webhook_id: str,
        *,
        name: str | None = None,
        source: str | None = None,
        secret: str | None = None,
        enabled: bool | None = None,
        action_type: str | None = None,
        action_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sets: list[str] = []
        params: list[Any] = []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if source is not None:
            sets.append("source = ?")
            params.append(source)
        if secret is not None:
            sets.append("secret = ?")
            params.append(secret)
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(1 if enabled else 0)
        if action_type is not None:
            sets.append("action_type = ?")
            params.append(action_type)
        if action_config is not None:
            sets.append("action_config = ?")
            params.append(dumps_json(action_config))
        if not sets:
            return (await self.get(webhook_id)) or {"id": webhook_id}
        sets.append("updated_at = ?")
        params.append(now_iso())
        params.append(webhook_id)
        sql = f"UPDATE webhooks SET {', '.join(sets)} WHERE id = ?"
        async with self._db.transaction() as conn:
            await conn.execute(sql, tuple(params))
        row = await self._db.fetchone(
            "SELECT * FROM webhooks WHERE id = ?", (webhook_id,)
        )
        return _hydrate(row)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete(self, webhook_id: str) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM webhooks WHERE id = ?", (webhook_id,)
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Regenerate secret
    # ------------------------------------------------------------------

    async def regenerate_secret(self, webhook_id: str) -> dict[str, Any]:
        import secrets as _secrets

        new_secret = _secrets.token_hex(32)
        return await self.update(webhook_id, secret=new_secret)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any]:
    d = row_to_dict(row)
    if d is None:
        return {"id": "unknown"}
    # Normalise SQLite boolean.
    if "enabled" in d:
        d["enabled"] = bool(d["enabled"])
    # Parse JSON config.
    raw = d.get("action_config")
    if isinstance(raw, str):
        try:
            d["action_config"] = loads_json(raw)
        except Exception:
            d["action_config"] = {}
    return d


def _filters(
    *,
    source: str | None = None,
    enabled: bool | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    if enabled is not None:
        clauses.append("enabled = ?")
        params.append(1 if enabled else 0)
    if not clauses:
        return "", []
    return "WHERE " + " AND ".join(clauses), params


__all__ = ["WebhookDAO"]
