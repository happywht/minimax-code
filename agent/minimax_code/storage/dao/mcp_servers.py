"""DAO — MCP server configuration persistence.

Each row stores one external MCP server recipe (stdio command, env,
enabled flag, …).  The IPC layer uses this DAO for CRUD and the
MCP registry for runtime connections.
"""

from __future__ import annotations

import logging
from typing import Any

from ._base import dumps_json, loads_json, now_iso, row_to_dict

logger = logging.getLogger(__name__)

_TRANSPORT_CHOICES = {"stdio", "sse"}


def _hydrate(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if d is None:
        return None
    d["enabled"] = bool(d.get("enabled", 1))
    d["command"] = loads_json(d.get("command"))
    d["env"] = loads_json(d.get("env"))
    return d


class McpServersDAO:
    """Async DAO for the ``mcp_servers`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def create(
        self,
        *,
        id: str,
        name: str,
        transport: str = "stdio",
        command: list[str] | None = None,
        url: str | None = None,
        env: dict[str, str] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Insert a new MCP server config and return the persisted row."""
        if transport not in _TRANSPORT_CHOICES:
            raise ValueError(f"transport must be one of {_TRANSPORT_CHOICES}")
        now = now_iso()
        sql = (
            "INSERT INTO mcp_servers (id, name, transport, command, url, env, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        async with self._db.transaction() as conn:
            await conn.execute(
                sql,
                (
                    id,
                    name,
                    transport,
                    dumps_json(command),
                    url,
                    dumps_json(env),
                    1 if enabled else 0,
                    now,
                    now,
                ),
            )
        row = await self._db.fetchone("SELECT * FROM mcp_servers WHERE id = ?", (id,))
        return _hydrate(row)

    async def get(self, server_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM mcp_servers WHERE id = ?", (server_id,))
        return _hydrate(row)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM mcp_servers WHERE name = ?", (name,))
        return _hydrate(row)

    async def update(
        self,
        server_id: str,
        *,
        name: str | None = None,
        transport: str | None = None,
        command: list[str] | None = None,
        url: str | None = None,
        env: dict[str, str] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update a persisted MCP server config."""
        sets: list[str] = []
        params: list[Any] = []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if transport is not None:
            if transport not in _TRANSPORT_CHOICES:
                raise ValueError(f"transport must be one of {_TRANSPORT_CHOICES}")
            sets.append("transport = ?")
            params.append(transport)
        if command is not None:
            sets.append("command = ?")
            params.append(dumps_json(command))
        if url is not None:
            sets.append("url = ?")
            params.append(url)
        if env is not None:
            sets.append("env = ?")
            params.append(dumps_json(env))
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(1 if enabled else 0)
        if not sets:
            return await self.get(server_id)
        sets.append("updated_at = ?")
        params.append(now_iso())
        params.append(server_id)
        sql = f"UPDATE mcp_servers SET {', '.join(sets)} WHERE id = ?"
        async with self._db.transaction() as conn:
            await conn.execute(sql, tuple(params))
        return await self.get(server_id)

    async def delete(self, server_id: str) -> bool:
        """Delete a persisted MCP server config."""
        async with self._db.transaction() as conn:
            cur = await conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
            return cur.rowcount > 0

    async def list(
        self,
        *,
        enabled: bool | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """List persisted MCP server configs."""
        where: list[str] = []
        params: list[Any] = []
        if enabled is not None:
            where.append("enabled = ?")
            params.append(1 if enabled else 0)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"SELECT * FROM mcp_servers {where_sql} ORDER BY updated_at DESC"
        if limit is not None and limit > 0:
            sql = f"{sql} LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset or 0)])
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]


__all__ = ["McpServersDAO"]
