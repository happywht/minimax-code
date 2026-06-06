"""MiniMax LLM client — transport-based architecture.

The client delegates to a :class:`~minimax_code.agent.transports.LLMTransport`
implementation based on the ``protocol`` parameter:

* ``"anthropic"`` (default) — talks Anthropic's native protocol
* ``"openai"`` — talks any OpenAI-compatible API
* mock mode — deterministic canned responses (no API key)

The public interface (:class:`StreamChunk`, :class:`LLMResponse`,
:meth:`MiniMaxClient.stream_chat`) is unchanged from the pre-refactor
design, so existing consumers (``core.py``, ``builtins.py``, tests)
continue to work without modification.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from typing import Any

from .transports import LLMTransport
from .transports.anthropic_transport import AnthropicTransport
from .transports.mock_transport import MockTransport
from .types import LLMConfigError, LLMError, LLMResponse, StreamChunk

from .. import secrets

logger = logging.getLogger(__name__)


DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_BASE_URL = "https://api.minimaxi.com/anthropic"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class MiniMaxClient:
    """Async LLM client with pluggable transport.

    Parameters
    ----------
    protocol:
        Wire protocol — ``"anthropic"`` (default), ``"openai"``, or
        ``"mock"``.  When omitted and ``api_key`` is empty, falls back
        to mock mode automatically.
    api_key:
        Bearer token.  Pulled from ``MINIMAX_API_KEY`` / OS keyring
        if omitted.  Empty / missing values put the client in
        **mock mode** (unless ``protocol`` is explicitly set).
    base_url:
        Root of the API.  Pulled from ``MINIMAX_API_BASE`` if
        omitted; defaults to MiniMax's Anthropic-compatible proxy.
    model:
        Default model name used by :meth:`chat` and
        :meth:`stream_chat`. Individual calls can override.
    timeout:
        Per-request timeout in seconds.
    max_retries:
        Number of attempts on transient errors (network, 5xx, 429).
    mock:
        Force mock mode.  If ``None`` (default), mock mode is
        auto-detected from the presence of ``api_key``.
    client:
        Optional pre-built SDK client instance (useful for injecting
        test doubles).  Type depends on the transport:
        ``anthropic.AsyncAnthropic`` or ``openai.AsyncOpenAI``.
    """

    def __init__(
        self,
        *,
        protocol: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
        max_retries: int = 3,
        mock: bool | None = None,
        client: Any = None,
    ) -> None:
        # Resolve API key.
        if api_key is not None:
            resolved_key = api_key
        else:
            secret_value = secrets.get_api_key()
            resolved_key = secret_value or ""

        resolved_base = (
            base_url
            if base_url is not None
            else os.environ.get("MINIMAX_API_BASE", DEFAULT_BASE_URL)
        )

        self.api_key = resolved_key or ""
        self.base_url = (resolved_base or DEFAULT_BASE_URL).rstrip("/")
        self.default_model = model
        self.timeout = timeout
        self.max_retries = max_retries

        # Determine effective protocol.
        force_mock = mock if mock is not None else (not bool(self.api_key))
        if protocol == "mock" or force_mock:
            effective_protocol = "mock"
        else:
            effective_protocol = (protocol or "anthropic").lower()

        self.protocol = effective_protocol
        self.mock = effective_protocol == "mock"

        # Build transport.
        self._transport: LLMTransport = self._build_transport(
            effective_protocol, client
        )
        self._client = client  # retain for legacy close() compat
        self._thinking_count = 0

        logger.info(
            "MiniMaxClient initialised (protocol=%s, mock=%s, "
            "base_url=%s, model=%s, max_retries=%d)",
            effective_protocol, self.mock, self.base_url,
            self.default_model, self.max_retries,
        )

    # -- public surface ----------------------------------------------------

    async def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        model: str | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | Mapping[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> LLMResponse:
        """One-shot, non-streaming chat call.

        Internally calls :meth:`stream_chat` and assembles the
        chunks. We use streaming even for non-streaming callers
        because it keeps a single code path.
        """
        chunks: list[StreamChunk] = []
        async for c in self.stream_chat(
            messages,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            extra=extra,
        ):
            chunks.append(c)

        return _assemble(chunks, model=model or self.default_model)

    async def stream_chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        model: str | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | Mapping[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream the assistant response chunk-by-chunk.

        Delegates to the active transport.  The final chunk has
        ``finish_reason`` set to a non-None value (``stop`` /
        ``tool_calls`` / ``length``) and a populated ``usage`` dict.

        Side effect
        -----------

        Updates :attr:`thinking_count` after the stream completes.
        """
        async for chunk in self._transport.stream_chat(
            messages,
            model=model or self.default_model,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk
        # Sync thinking_count from the transport after stream completes.
        self._thinking_count = self._transport.thinking_count

    async def close(self) -> None:
        await self._transport.close()

    async def __aenter__(self) -> MiniMaxClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # -- thinking count (public attribute) ---------------------------------

    @property
    def thinking_count(self) -> int:
        """Thinking-block count from the most recent call."""
        return self._thinking_count

    @thinking_count.setter
    def thinking_count(self, value: int) -> None:
        self._thinking_count = value

    # -- internals ---------------------------------------------------------

    def _build_transport(self, protocol: str, client: Any) -> LLMTransport:
        """Instantiate the correct transport for *protocol*."""
        if protocol == "mock":
            return MockTransport()

        if protocol == "openai":
            from .transports.openai_transport import OpenAITransport
            return OpenAITransport(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=self.max_retries,
                client=client,
            )

        # Default: anthropic
        return AnthropicTransport(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
            client=client,
        )


# ---------------------------------------------------------------------------
# Assembly helpers
# ---------------------------------------------------------------------------


def _assemble(chunks: Iterable[StreamChunk], *, model: str) -> LLMResponse:
    """Combine streamed chunks into a final :class:`LLMResponse`."""
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    finish_reason = "stop"
    usage: dict[str, int] = {}

    for c in chunks:
        if c.delta:
            text_parts.append(c.delta)
        if c.usage:
            usage = c.usage
        if c.finish_reason:
            finish_reason = c.finish_reason
        for delta in c.tool_call_deltas:
            _merge_tool_call_delta(tool_calls, delta)

    # Ghost-slot filter — phantom entries created by _merge_tool_call_delta
    # padding must not leak into the assembled response.
    tool_calls = [
        tc for tc in tool_calls
        if (tc.get("id") or "").strip()
        or ((tc.get("function") or {}).get("name") or "").strip()
    ]

    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
    if tool_calls:
        message["tool_calls"] = tool_calls

    return LLMResponse(
        message=message,
        usage=usage,
        finish_reason=finish_reason,
        model=model,
    )


def _merge_tool_call_delta(acc: list[dict[str, Any]], delta: dict[str, Any]) -> None:
    """Merge one streamed ``tool_calls`` delta into the accumulator.

    The adapter emits deltas in OpenAI streaming format:
    first ``{index, id, type, function: {name}}`` and then many
    ``{index, function: {arguments: "<piece>"}}``.
    """
    try:
        index = int(delta.get("index", 0))
    except (TypeError, ValueError):
        index = 0
    # Grow the accumulator to fit.
    while len(acc) <= index:
        acc.append(
            {
                "id": "",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            }
        )
    target = acc[index]
    if "id" in delta and delta["id"]:
        target["id"] = delta["id"]
    if delta.get("type"):
        target["type"] = delta["type"]
    fn_delta = delta.get("function") or {}
    if isinstance(fn_delta.get("name"), str) and fn_delta["name"]:
        target["function"]["name"] = (
            target["function"].get("name", "") + fn_delta["name"]
        )
    if isinstance(fn_delta.get("arguments"), str):
        target["function"]["arguments"] = (
            target["function"].get("arguments", "") + fn_delta["arguments"]
        )


# Re-export types for backward compat — existing ``from .llm import StreamChunk``
# continues to work.
__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "LLMConfigError",
    "LLMError",
    "LLMResponse",
    "MiniMaxClient",
    "StreamChunk",
]
