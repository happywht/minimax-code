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
from typing import TYPE_CHECKING, Any

from ..types import StreamChunk

if TYPE_CHECKING:
    # Annotation-only import (runtime uses ``coerce_effort`` in the concrete
    # transports; the ABC just declares the kwarg shape).
    from ..reasoning import ReasoningEffort

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
        reasoning_effort: ReasoningEffort | str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Yield ``StreamChunk`` values for one chat turn.

        The *final* chunk must carry a non-None ``finish_reason`` and
        a populated ``usage`` dict.

        ``reasoning_effort`` (R54 pipe-through) is normalised via
        :func:`minimax_code.agent.reasoning.coerce_effort` and surfaced on the
        transport as ``last_reasoning_effort``; the concrete transports do
        **not** yet emit it on the wire (the MiniMax/xAI effort contract is
        unsettled — blind injection would break the call), so ``None`` (the
        default) leaves every request byte-identical to the pre-R54 behaviour.
        """

    @property
    @abstractmethod
    def thinking_count(self) -> int:
        """Thinking-block count from the most recent ``stream_chat`` call."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources held by this transport."""
