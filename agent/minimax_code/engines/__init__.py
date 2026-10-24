"""Pluggable agent engines (M4) — native (default) + Codex adapter."""

from __future__ import annotations

import asyncio
from typing import Any

from .base import CAPABILITY_KEYS, EngineInfo
from .codex import CodexEngine
from .native import NativeEngine

# Registry order is display order — native first (default engine).
_ENGINES: list[Any] = [NativeEngine(), CodexEngine()]


async def list_engines() -> list[EngineInfo]:
    """Capability cards for every registered engine.

    ``available`` is decided by a live probe per call — the answer is
    environment truth, not a cached guess.
    """
    cards: list[EngineInfo] = []
    for engine in _ENGINES:
        result = engine.info()
        if asyncio.iscoroutine(result):
            result = await result
        cards.append(result)
    return cards


def get_engine(engine_id: str) -> Any | None:
    """Return the registered engine instance by id, or None."""
    for engine in _ENGINES:
        if engine.id == engine_id:
            return engine
    return None


__all__ = ["list_engines", "get_engine", "EngineInfo", "CAPABILITY_KEYS",
           "NativeEngine", "CodexEngine"]
