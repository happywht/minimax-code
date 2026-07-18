"""LLM data types shared across transport and client layers.

Extracted from ``llm.py`` to break the circular import:
``llm`` → ``transports`` → ``llm``.  Both ``llm.py`` and the transport
modules import from this file instead of referencing each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StreamChunk:
    """One chunk of an in-flight LLM response.

    Either ``delta`` (text) or ``tool_call_deltas`` (partial JSON
    for an in-progress function call) is populated; the
    ``finish_reason`` is ``None`` except on the final chunk.
    """

    delta: str = ""
    tool_call_deltas: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    # Set on the *final* chunk (and on the non-streaming reply).
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """The final, fully-assembled response from a chat call."""

    message: dict[str, Any]
    usage: dict[str, int]
    finish_reason: str
    model: str
    # Per-turn metadata snapshot the v0.3.0 ``thinking_count`` wire
    # relies on. Populated by ``AgentCore._stream_turn`` after the
    # stream exhausts; ``None`` when the LLM was bypassed (tests that
    # short-circuit via a fake ``stream_chat`` may leave it unset).
    metadata: dict[str, Any] | None = None


class LLMError(RuntimeError):
    """Raised when the LLM server returns an unrecoverable error.

    ``status_code`` carries the upstream HTTP status when known (e.g.
    429 / 503), or ``None`` when the transport flattened a connection or
    timeout error into this exception. Retry classification in
    :mod:`minimax_code.agent.reliability.retry` reads it to distinguish
    retryable from terminal failures.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class LLMStreamTimeout(LLMError):
    """Raised when an open LLM stream stops producing events."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"LLM stream timed out after {timeout_seconds:.0f}s without data. "
            "The run was stopped and can be retried."
        )


class LLMConfigError(LLMError):
    """Raised when the client is mis-configured (missing base URL, etc.)."""


__all__ = [
    "LLMConfigError",
    "LLMError",
    "LLMStreamTimeout",
    "LLMResponse",
    "StreamChunk",
]
