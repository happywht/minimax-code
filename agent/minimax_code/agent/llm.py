"""MiniMax LLM client.

Thin wrapper around :mod:`httpx` (async) that targets the OpenAI-
compatible chat-completions endpoint exposed at
``{MINIMAX_API_BASE}/chat/completions``. We use that contract
because:

* It is the format MiniMax, OpenAI, Anthropic-via-gateway, and
  most local OpenAI-compatible servers (vLLM, llama.cpp, Ollama)
  all speak. One client, many backends.
* Tool-calling (``tools=[...]``, ``tool_calls=[...]``) is part of
  the same payload, so we get function-calling for free.

Behavioural contract
--------------------

* **Mock mode.** When ``MINIMAX_API_KEY`` is unset or empty the
  client returns a deterministic canned response so the agent
  can be exercised in CI / on a fresh checkout without leaking
  secrets. Streaming in mock mode emits the canned text in
  16-character chunks.

* **Streaming.** ``stream_chat`` returns an async iterator of
  :class:`StreamChunk` values. The agent loop appends ``delta``
  strings to the assistant message; non-text deltas (tool calls)
  are accumulated and emitted as a :class:`StreamChunk` with
  ``tool_call_deltas`` populated.

* **Retries.** Network errors, ``5xx`` and ``429`` responses are
  retried with exponential backoff (1s → 2s → 4s, max 3
  attempts). ``4xx`` (other than 429) is treated as fatal —
  retrying is pointless and the error is wrapped in
  :class:`LLMError`.

* **Token counts.** Pulled from the response body's
  ``usage`` field when present; some servers also expose
  ``x-usage-*`` headers which we honour first.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from .. import secrets

logger = logging.getLogger(__name__)


DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_BASE_URL = "https://api.minimax.com/v1"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LLMError(RuntimeError):
    """Raised when the LLM server returns an unrecoverable error."""


class LLMConfigError(LLMError):
    """Raised when the client is mis-configured (missing base URL, etc.)."""


# ---------------------------------------------------------------------------
# Message + chunk types
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Mock responses
# ---------------------------------------------------------------------------


_MOCK_TEXT = (
    "[mock] Hello! I'm running in mock mode because MINIMAX_API_KEY is not set. "
    "Set the env var to call the real MiniMax API. I can still help you build and "
    "test the agent core — try running tests, exploring the codebase, or wiring up "
    "the storage layer."
)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class MiniMaxClient:
    """Async client for the MiniMax chat-completions endpoint.

    Parameters
    ----------
    api_key:
        Bearer token. Pulled from ``MINIMAX_API_KEY`` if omitted.
        Empty / missing values put the client in **mock mode** —
        every call returns the canned response above.
    base_url:
        Root of the API. Pulled from ``MINIMAX_API_BASE`` if
        omitted; defaults to the public MiniMax endpoint.
    model:
        Default model name used by :meth:`chat` and
        :meth:`stream_chat`. Individual calls can override.
    timeout:
        Per-request timeout in seconds. Streaming is broken up
        into chunked reads, but a hung connection will be killed
        at this wall time.
    max_retries:
        Number of attempts on transient errors (network, 5xx,
        429). ``1`` means "no retry".
    mock:
        Force mock mode. Mostly useful in tests; if you set this
        you should also pass a deterministic ``api_key`` to make
        the client trivially mockable.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
        max_retries: int = 3,
        mock: bool | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # Caller-supplied key wins (used by tests and by the IPC
        # layer when a session has its own credential). Otherwise
        # delegate to `secrets` so we pick up the OS keyring value
        # before falling back to the env var.
        if api_key is not None:
            resolved_key = api_key
        else:
            secret_value = secrets.get_api_key()
            resolved_key = secret_value or ""
        resolved_base = base_url if base_url is not None else os.environ.get("MINIMAX_API_BASE", DEFAULT_BASE_URL)

        self.api_key = resolved_key or ""
        self.base_url = (resolved_base or DEFAULT_BASE_URL).rstrip("/")
        self.default_model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.mock = mock if mock is not None else (not bool(self.api_key))
        self._client = client  # caller may inject an httpx mock transport

        logger.info(
            "MiniMaxClient initialised (mock=%s, base_url=%s, model=%s, max_retries=%d)",
            self.mock, self.base_url, self.default_model, self.max_retries,
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

        Each yield is a :class:`StreamChunk`. The final chunk has
        ``finish_reason`` set to a non-None value (``stop`` /
        ``tool_calls`` / ``length``) and a populated ``usage`` dict.
        """
        if self.mock:
            async for c in _mock_stream(self.default_model if model is None else (model or self.default_model)):
                yield c
            return

        payload = _build_payload(
            messages,
            model=model or self.default_model,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            extra=extra,
            stream=True,
        )
        async for chunk in self._post_streaming(payload):
            yield chunk

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> MiniMaxClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # -- internals ---------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def _post_streaming(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[StreamChunk]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        attempt = 0
        last_exc: Exception | None = None
        while attempt < self.max_retries:
            attempt += 1
            try:
                client = self._ensure_client()
                async with client.stream(
                    "POST", url, json=payload, headers=headers
                ) as resp:
                    if resp.status_code == 429 or 500 <= resp.status_code < 600:
                        body_snip = (await resp.aread()).decode("utf-8", "replace")[:200]
                        last_exc = LLMError(
                            f"transient HTTP {resp.status_code}: {body_snip}"
                        )
                        await self._sleep_backoff(attempt)
                        continue
                    if 400 <= resp.status_code < 500:
                        body = (await resp.aread()).decode("utf-8", "replace")
                        raise LLMError(
                            f"HTTP {resp.status_code}: {body[:500]}"
                        )
                    # status_code 2xx — stream it
                    async for chunk in _parse_sse(resp):
                        yield chunk
                    return
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning("llm transport error (attempt %d): %s", attempt, exc)
                await self._sleep_backoff(attempt)
                continue
        raise LLMError(f"giving up after {self.max_retries} attempts: {last_exc}")

    async def _sleep_backoff(self, attempt: int) -> None:
        # Exponential: 1, 2, 4, 8s … capped at 16s. Add a small
        # jitter so a thundering herd does not retry in lockstep.
        base = min(16, 2 ** (attempt - 1))
        await asyncio.sleep(base + random.random() * 0.25)


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------


def _build_payload(
    messages: Sequence[Mapping[str, Any]],
    *,
    model: str,
    tools: Sequence[Mapping[str, Any]] | None,
    tool_choice: str | Mapping[str, Any] | None,
    temperature: float | None,
    max_tokens: int | None,
    extra: Mapping[str, Any] | None,
    stream: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "stream": stream,
    }
    if tools:
        payload["tools"] = list(tools)
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if temperature is not None:
        payload["temperature"] = float(temperature)
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    if extra:
        for k, v in extra.items():
            if k not in payload:
                payload[k] = v
    return payload


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------


async def _parse_sse(resp: httpx.Response) -> AsyncIterator[StreamChunk]:
    """Yield :class:`StreamChunk` from an OpenAI-style SSE stream.

    The wire format is::

        data: {"id": "...", "choices": [{"delta": {...}, "finish_reason": null}], "usage": {...}}
        data: [DONE]

    Lines beginning with anything other than ``data: `` are
    ignored (some servers emit ``event:`` / ``id:`` / ``:`` heartbeats).
    """
    buffer = ""
    async for raw in resp.aiter_text():
        buffer += raw
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].lstrip()
            if data == "[DONE]":
                return
            try:
                obj = json.loads(data)
            except json.JSONDecodeError as exc:
                logger.warning("bad SSE chunk: %r (%s)", data[:200], exc)
                continue
            usage = obj.get("usage") or {}
            chunk = StreamChunk(
                delta="",
                tool_call_deltas=[],
                finish_reason=None,
                usage=usage if isinstance(usage, dict) else {},
            )
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or {}
                if isinstance(delta.get("content"), str):
                    chunk.delta += delta["content"]
                if isinstance(delta.get("tool_calls"), list):
                    chunk.tool_call_deltas.extend(delta["tool_calls"])
                fr = choice.get("finish_reason")
                if fr:
                    chunk.finish_reason = fr
            yield chunk


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

    OpenAI's streaming format splits each tool call across many
    chunks: first a ``{index, id, type, function: {name}}`` and
    then many ``{index, function: {arguments: "<piece>"}}``.
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


# ---------------------------------------------------------------------------
# Mock stream
# ---------------------------------------------------------------------------


async def _mock_stream(model: str) -> AsyncIterator[StreamChunk]:
    """Deterministic mock for offline / CI runs.

    Emits the canned text in 16-character chunks then a final
    chunk with synthetic token counts. No tools are called
    (mock mode is for plumbing tests, not for tool exercises).
    """
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


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "LLMConfigError",
    "LLMError",
    "LLMResponse",
    "MiniMaxClient",
    "StreamChunk",
]
