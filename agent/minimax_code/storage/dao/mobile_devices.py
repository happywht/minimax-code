"""DAO — paired mobile devices.

Stores the public half of the device's keypair plus a friendly name
and the time of pairing. The matching auth / pairing code is in
``minimax_code.mobile`` (Phase 2); this DAO is the durable record.

Two DAOs live in this module:

* :class:`MobileDevicesDAO` — generic CRUD keyed by the database
  primary key (``id``). Kept for backwards compatibility with the
  storage smoke tests in ``test_storage.py``.
* :class:`MobileDeviceDAO`  — task-spec API keyed by the natural
  key (``device_id``). Used by the pairing manager and the
  ``mobile.*`` IPC namespace.
"""

from __future__ import annotations

import logging
from typing import Any

from ._base import apply_pagination, now_iso, row_to_dict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Async DAO
# ---------------------------------------------------------------------------


class MobileDevicesDAO:
    """Async DAO for the ``mobile_devices`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def upsert(
        self,
        *,
        id: str,
        device_id: str,
        name: str,
        public_key: str,
        paired_at: str | None = None,
        last_seen_at: str | None = None,
    ) -> dict[str, Any]:
        """Insert or update a device by ``device_id`` (natural key)."""
        row = await self._db.fetchone(
            "SELECT id FROM mobile_devices WHERE device_id = ?", (device_id,)
        )
        if row is None:
            sql = (
                "INSERT INTO mobile_devices "
                "(id, device_id, name, public_key, paired_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            )
            params = (
                id,
                device_id,
                name,
                public_key,
                paired_at or now_iso(),
                last_seen_at,
            )
            async with self._db.transaction() as conn:
                await conn.execute(sql, params)
        else:
            sql = (
                "UPDATE mobile_devices SET name = ?, public_key = ?, "
                "last_seen_at = COALESCE(?, last_seen_at) "
                "WHERE device_id = ?"
            )
            params = (name, public_key, last_seen_at, device_id)
            async with self._db.transaction() as conn:
                await conn.execute(sql, params)
        out = await self._db.fetchone(
            "SELECT * FROM mobile_devices WHERE device_id = ?", (device_id,)
        )
        return _hydrate(out)

    async def get(self, device_pk: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM mobile_devices WHERE id = ?", (device_pk,)
        )
        return _hydrate(row)

    async def get_by_device_id(self, device_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM mobile_devices WHERE device_id = ?", (device_id,)
        )
        return _hydrate(row)

    async def touch(self, device_id: str) -> dict[str, Any] | None:
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE mobile_devices SET last_seen_at = ? WHERE device_id = ?",
                (now_iso(), device_id),
            )
        return await self.get_by_device_id(device_id)

    async def list(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM mobile_devices ORDER BY paired_at DESC"
        sql, params = apply_pagination(sql, [], limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def delete(self, device_pk: str) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM mobile_devices WHERE id = ?", (device_pk,)
            )
            return cur.rowcount > 0

    async def count(self) -> int:
        row = await self._db.fetchone("SELECT COUNT(*) AS n FROM mobile_devices")
        return int(row["n"]) if row else 0

    async def delete_by_device_id(self, device_id: str) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM mobile_devices WHERE device_id = ?", (device_id,)
            )
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any] | None:
    return row_to_dict(row)


def upsert_sync(db, **fields) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    row = db.fetchone(
        "SELECT id FROM mobile_devices WHERE device_id = ?", (fields["device_id"],)
    )
    if row is None:
        sql = (
            "INSERT INTO mobile_devices "
            "(id, device_id, name, public_key, paired_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
        params = (
            fields["id"],
            fields["device_id"],
            fields["name"],
            fields["public_key"],
            fields.get("paired_at") or now_iso(),
            fields.get("last_seen_at"),
        )
    else:
        sql = (
            "UPDATE mobile_devices SET name = ?, public_key = ?, "
            "last_seen_at = COALESCE(?, last_seen_at) "
            "WHERE device_id = ?"
        )
        params = (
            fields["name"],
            fields["public_key"],
            fields.get("last_seen_at"),
            fields["device_id"],
        )
    with db.transaction() as conn:
        conn.execute(sql, params)
    out = db.fetchone(
        "SELECT * FROM mobile_devices WHERE device_id = ?", (fields["device_id"],)
    )
    return _hydrate(out)


# ---------------------------------------------------------------------------
# Device-id-keyed DAO (task-spec API)
# ---------------------------------------------------------------------------


class MobileDeviceDAO:
    """Async DAO for the ``mobile_devices`` table keyed by ``device_id``.

    The frontend and pairing manager use ``device_id`` (the natural
    identifier printed on the device label / shown in the iOS
    Settings) as the only externally visible key. The primary key
    ``id`` is an internal implementation detail.

    This class is intentionally narrow — five methods that the
    pairing flow needs. The legacy :class:`MobileDevicesDAO` (above)
    stays around for callers that already use the ``id``-keyed API.
    """

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def list_all(self) -> list[dict[str, Any]]:
        """Return every paired device, most recently paired first."""
        rows = await self._db.fetchall(
            "SELECT * FROM mobile_devices ORDER BY paired_at DESC, device_id ASC"
        )
        return [row_to_dict(r) for r in rows]

    async def get(self, device_id: str) -> dict[str, Any] | None:
        """Return the row matching ``device_id`` (``UNIQUE`` column) or ``None``."""
        row = await self._db.fetchone(
            "SELECT * FROM mobile_devices WHERE device_id = ?",
            (device_id,),
        )
        return row_to_dict(row)

    async def register(
        self,
        device_id: str,
        name: str,
        public_key: str,
    ) -> dict[str, Any]:
        """Insert a new paired device.

        ``device_id`` and ``public_key`` must be non-empty strings;
        ``name`` is a human-friendly label shown in the UI. The
        function raises :class:`ValueError` on bad input and
        :class:`sqlite3.IntegrityError` if ``device_id`` collides —
        callers should treat that as "already paired" and either
        re-pair (delete then insert) or surface a friendlier error.
        """
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id must be a non-empty string")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        if not isinstance(public_key, str) or not public_key.strip():
            raise ValueError("public_key must be a non-empty string")
        import uuid as _uuid

        new_id = f"dev_{_uuid.uuid4().hex[:12]}"
        sql = (
            "INSERT INTO mobile_devices "
            "(id, device_id, name, public_key, paired_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, NULL)"
        )
        params = (new_id, device_id, name, public_key, now_iso())
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone(
            "SELECT * FROM mobile_devices WHERE device_id = ?",
            (device_id,),
        )
        return _hydrate(row)  # type: ignore[return-value]

    async def unregister(self, device_id: str) -> bool:
        """Delete the device row. Returns ``True`` if a row was removed."""
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM mobile_devices WHERE device_id = ?",
                (device_id,),
            )
            return int(cur.rowcount or 0) > 0

    async def touch_last_seen(self, device_id: str) -> dict[str, Any] | None:
        """Bump ``last_seen_at`` to now. Returns the updated row, or
        ``None`` if the device isn't paired.

        Used as the PoC heartbeat for "remote control" — once the
        device has paired, a periodic ``mobile.touch`` keeps its
        ``last_seen_at`` fresh so the UI can flag stale pairs.
        """
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE mobile_devices SET last_seen_at = ? WHERE device_id = ?",
                (now_iso(), device_id),
            )
            if int(cur.rowcount or 0) == 0:
                return None
        row = await self._db.fetchone(
            "SELECT * FROM mobile_devices WHERE device_id = ?",
            (device_id,),
        )
        return _hydrate(row)


__all__ = [
    "MobileDevicesDAO",
    "MobileDeviceDAO",
    "upsert_sync",
]
