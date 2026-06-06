"""Transport abstraction for LLM API calls.

Each transport wraps a specific wire protocol (Anthropic, OpenAI-compatible,
or mock) and exposes a uniform ``stream_chat`` async iterator interface that
yields :class:`~minimax_code.agent.llm.StreamChunk` values.

The rest of the agent (``core.py``, ``builtins.py``, etc.) never imports a
concrete transport directly — it goes through :class:`MiniMaxClient` which
picks the right implementation based on ``protocol``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from ..types import StreamChunk

__all__ = ["LLMTransport"]


class LLMTransport(ABC):
    """Abstract base for LLM wire-protocol transports.

    Concrete implementations must provide:
    * :meth:`stream_chat` — async iterator of ``StreamChunk``
    * :attr:`thinking_count` — number of thinking blocks in the last call
    * :meth:`close` — release resources (HTTP clients, etc.)
    """

    @abstractmethod
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
        """Yield ``StreamChunk`` values for one chat turn.

        The *final* chunk must carry a non-None ``finish_reason`` and
        a populated ``usage`` dict.
        """

    @property
    @abstractmethod
    def thinking_count(self) -> int:
        """Thinking-block count from the most recent ``stream_chat`` call."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources held by this transport."""
