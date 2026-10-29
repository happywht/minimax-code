"""DAO — remote run hosts (M5: Linux 长驻执行端).

One row per registered Linux machine. SSH credentials are *referenced*
(identity file path), never stored — the planning principle is that
secrets live in the OS keyring / the user's SSH setup, not in our
database.
"""

from __future__ import annotations

import uuid
from typing import Any

from ._base import apply_pagination, now_iso, parse_order_by, row_to_dict

_SORTABLE = ("name", "created_at", "updated_at", "last_probe_at")


def _hydrate(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if d is None:
        return None
    d["enabled"] = bool(d.get("enabled", 1))
    d["last_probe_ok"] = None if d.get("last_probe_ok") is None else bool(d.get("last_probe_ok"))
    d.setdefault("identity_file", "")
    d.setdefault("agent_version", "")
    return d


class HostsDAO:
    """Async DAO for the ``hosts`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def create(
        self,
        *,
        name: str,
        hostname: str,
        username: str,
        port: int = 22,
        identity_file: str = "",
        remote_port: int = 8765,
    ) -> dict[str, Any]:
        host_id = f"host_{uuid.uuid4().hex[:10]}"
        now = now_iso()
        sql = (
            "INSERT INTO hosts (id, name, hostname, port, username, identity_file, "
            "enabled, created_at, updated_at, remote_port) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)"
        )
        async with self._db.transaction() as conn:
            await conn.execute(
                sql, (host_id, name, hostname, port, username, identity_file, now, now, remote_port)
            )
        row = await self._db.fetchone("SELECT * FROM hosts WHERE id = ?", (host_id,))
        return _hydrate(row)

    async def get(self, host_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM hosts WHERE id = ?", (host_id,))
        return _hydrate(row)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM hosts WHERE name = ?", (name,))
        return _hydrate(row)

    async def list(
        self,
        *,
        enabled: bool | None = None,
        limit: int | None = None,
        offset: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if enabled is not None:
            where.append("enabled = ?")
            params.append(1 if enabled else 0)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"SELECT * FROM hosts {where_sql} ORDER BY {parse_order_by(order_by, _SORTABLE)}"
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def update(
        self,
        host_id: str,
        *,
        name: str | None = None,
        hostname: str | None = None,
        username: str | None = None,
        port: int | None = None,
        identity_file: str | None = None,
        enabled: bool | None = None,
        remote_port: int | None = None,
    ) -> dict[str, Any] | None:
        sets: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("name", name), ("hostname", hostname), ("username", username),
            ("port", port), ("identity_file", identity_file), ("remote_port", remote_port),
        ):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value)
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(1 if enabled else 0)
        if not sets:
            return await self.get(host_id)
        sets.append("updated_at = ?")
        params.append(now_iso())
        params.append(host_id)
        async with self._db.transaction() as conn:
            await conn.execute(
                f"UPDATE hosts SET {', '.join(sets)} WHERE id = ?", tuple(params)
            )
        return await self.get(host_id)

    async def delete(self, host_id: str) -> bool:
        cur = await self._db.execute("DELETE FROM hosts WHERE id = ?", (host_id,))
        return cur.rowcount > 0

    async def record_probe(
        self, host_id: str, *, ok: bool, agent_version: str = ""
    ) -> None:
        """Persist the latest connection-diagnosis outcome."""
        await self._db.execute(
            "UPDATE hosts SET last_probe_at = ?, last_probe_ok = ?, "
            "agent_version = ?, updated_at = ? WHERE id = ?",
            (now_iso(), 1 if ok else 0, agent_version, now_iso(), host_id),
        )


__all__ = ["HostsDAO"]
