"""Live standalone team owners. Durable rows remain authoritative after restart."""
from __future__ import annotations

import asyncio

_active: dict[str, asyncio.Task] = {}


def register(run_id: str, task: asyncio.Task) -> None:
    _active[run_id] = task


def unregister(run_id: str, task: asyncio.Task) -> None:
    if _active.get(run_id) is task:
        _active.pop(run_id, None)


def cancel(run_id: str) -> bool:
    task = _active.get(run_id)
    if task is None or task.done():
        return False
    if not task.cancelling():
        task.cancel()
    return True
