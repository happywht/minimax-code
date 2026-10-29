"""Atomic claims for authenticated webhook bodies."""
from __future__ import annotations

from ..storage.dao._base import now_iso


async def claim(db, webhook_id: str, digest: str, delivery_id: str | None) -> str:
    async with db.transaction() as conn:
        now = now_iso()
        cursor = await conn.execute(
            "INSERT OR IGNORE INTO webhook_deliveries "
            "(webhook_id, body_sha256, delivery_id, state, created_at, updated_at) VALUES (?, ?, ?, 'pending', ?, ?)",
            (webhook_id, digest, delivery_id, now, now),
        )
        if cursor.rowcount:
            return "claimed"
        cursor = await conn.execute(
            "SELECT state, delivery_id FROM webhook_deliveries WHERE webhook_id=? AND body_sha256=?",
            (webhook_id, digest),
        )
        row = await cursor.fetchone()
        if row and delivery_id and row[1] == delivery_id:
            return row[0]
        return "conflict"


async def finish(db, webhook_id: str, digest: str, state: str, error_id=None, stage=None):
    async with db.transaction() as conn:
        cursor = await conn.execute(
            "UPDATE webhook_deliveries SET state=?, error_id=?, stage=?, updated_at=? "
            "WHERE webhook_id=? AND body_sha256=? AND state='pending'",
            (state, error_id, stage, now_iso(), webhook_id, digest),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("webhook delivery claim was lost")
