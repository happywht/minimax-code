"""Mock transport for offline / CI development.

Returns a deterministic canned response without hitting any real API.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from ..types import StreamChunk
from . import LLMTransport

_MOCK_TEXT = (
    "[mock] Hello! I'm running in mock mode because no API key is configured. "
    "Add a provider and set an API key in Settings to call a real LLM. "
    "I can still help you build and test the agent core — try running tests, "
    "exploring the codebase, or wiring up the storage layer."
)


class MockTransport(LLMTransport):
    """Deterministic mock for offline / CI runs.

    Emits the canned text in 16-character chunks then a final chunk
    with synthetic token counts. No tools are called (mock mode is
    for plumbing tests, not tool exercises).
    """

    def __init__(self) -> None:
        self._thinking_count = 0

    @property
    def thinking_count(self) -> int:
        return self._thinking_count

    async def stream_chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        model: str,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | Mapping[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self._thinking_count = 1  # one synthetic "think" per call
        # Extract text from messages (handles multimodal list content)
        _extract_text(messages)
        step = 16
        for i in range(0, len(_MOCK_TEXT), step):
            await asyncio.sleep(0.005)
            yield StreamChunk(delta=_MOCK_TEXT[i : i + step])
        yield StreamChunk(
            finish_reason="stop",
            usage={
                "prompt_tokens": 1,
                "completion_tokens": max(1, len(_MOCK_TEXT) // 4),
                "total_tokens": 1 + max(1, len(_MOCK_TEXT) // 4),
            },
        )

    async def close(self) -> None:
        pass  # no resources to release


def _extract_text(messages: Sequence[Mapping[str, Any]]) -> str:
    """Extract concatenated text from messages (handles multimodal list content).

    This is used by MockTransport to validate that multimodal messages
    are parseable. The actual mock response is canned regardless.
    """
    parts: list[str] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return " ".join(parts)
