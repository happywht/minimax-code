"""MiniMax LLM client — Anthropic-compatible transport.

Uses the ``anthropic`` Python SDK to talk to MiniMax's
Anthropic-compatible endpoint (``api.minimaxi.com/anthropic``).
This is the official path for **Token Plan** subscription keys.

The client exposes the same :class:`StreamChunk` /
:class:`LLMResponse` interface that :mod:`core` expects.  All
Anthropic-specific wire formats (message shapes, tool schemas,
streaming events) are translated inside this module so the rest
of the agent sees a stable, OpenAI-inspired contract.

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

* **Retries.** The ``anthropic`` SDK retries 429 / 5xx /
  network errors with exponential backoff automatically
  (controlled by ``max_retries``).

* **Thinking.** MiniMax-M3 returns native ``ThinkingBlock``
  content. The adapter counts thinking blocks and exposes the
  count via ``self.thinking_count`` so the agent core can render
  the "思考 N 次" UI badge.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import anthropic

from .. import secrets

logger = logging.getLogger(__name__)


DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_BASE_URL = "https://api.minimaxi.com/anthropic"

# Anthropic → OpenAI finish_reason mapping
_STOP_REASON_MAP: dict[str, str] = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
}


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
    # Per-turn metadata snapshot the v0.3.0 ``thinking_count`` wire
    # relies on. Populated by ``AgentCore._stream_turn`` after the
    # stream exhausts; ``None`` when the LLM was bypassed (tests that
    # short-circuit via a fake ``stream_chat`` may leave it unset).
    metadata: dict[str, Any] | None = None


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
# Anthropic ↔ OpenAI format converters (private)
# ---------------------------------------------------------------------------


def _extract_system_prompt(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Pull the first ``role: system`` message out of the list.

    Anthropic accepts the system prompt as a top-level ``system``
    parameter, not as a message.  Returns ``(system_text,
    remaining_messages)``.
    """
    system = ""
    remaining: list[dict[str, Any]] = []
    for msg in messages:
        m = dict(msg)
        if m.get("role") == "system" and not system:
            content = m.get("content", "")
            system = str(content) if content else ""
        else:
            remaining.append(m)
    return system, remaining


def _convert_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert OpenAI-style messages to Anthropic format.

    Key differences handled:
    * ``content`` strings become ``[{"type": "text", "text": ...}]``.
    * ``tool_calls`` become ``tool_use`` content blocks.
    * ``role: tool`` messages become ``role: user`` with
      ``tool_result`` content blocks (consecutive ones are merged).
    """
    result: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def _flush_tool_results() -> None:
        nonlocal pending_tool_results
        if pending_tool_results:
            result.append({"role": "user", "content": pending_tool_results})
            pending_tool_results = []

    for msg in messages:
        role = msg.get("role", "")

        # Flush pending tool results before any non-tool message.
        if role != "tool":
            _flush_tool_results()

        if role == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            result.append({"role": "user", "content": content})

        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            text = msg.get("content") or ""
            if text:
                blocks.append({"type": "text", "text": str(text)})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                args = fn.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args.strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                # Anthropic requires non-empty, unique tool_use IDs.
                # If the stored ID is empty (e.g. from a partial stream
                # or mock mode), generate a unique one via uuid4.
                tc_id = tc.get("id") or ""
                if not tc_id.strip():
                    tc_id = f"toolu_{uuid.uuid4().hex[:24]}"
                blocks.append({
                    "type": "tool_use",
                    "id": tc_id,
                    "name": fn.get("name", ""),
                    "input": args,
                })
            if not blocks:
                blocks.append({"type": "text", "text": ""})
            result.append({"role": "assistant", "content": blocks})

        elif role == "tool":
            # tool_use_id must match the corresponding tool_use block.
            # If empty, generate a unique placeholder to avoid API rejection.
            tool_use_id = msg.get("tool_call_id") or ""
            if not tool_use_id.strip():
                tool_use_id = f"toolu_{uuid.uuid4().hex[:24]}"
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": str(msg.get("content", "")),
            })

        elif role == "system":
            # System messages should have been extracted already.
            # If one slips through, treat it as a user message.
            result.append({
                "role": "user",
                "content": [{"type": "text", "text": str(msg.get("content", ""))}],
            })

    _flush_tool_results()
    return result


def _convert_tools(
    tools: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Convert OpenAI tool schemas to Anthropic format.

    ``{type: "function", function: {name, description, parameters}}``
    → ``{name, description, input_schema}``
    """
    if not tools:
        return None
    out: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function", {})
        out.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {
                "type": "object",
                "properties": {},
            }),
        })
    return out


def _convert_tool_choice(
    tool_choice: str | Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Map OpenAI ``tool_choice`` values to Anthropic equivalents."""
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {"type": "auto"}
        if tool_choice == "none":
            return None
        if tool_choice == "required":
            return {"type": "any"}
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function", {})
        if fn.get("name"):
            return {"type": "tool", "name": fn["name"]}
    return {"type": "auto"}


async def _anthropic_stream_to_chunks(
    stream: anthropic.AsyncMessageStream,
) -> AsyncIterator[StreamChunk]:
    """Convert Anthropic stream events into :class:`StreamChunk`.

    The adapter handles raw events from the SDK's stream context:
    * ``content_block_delta`` with ``text_delta`` → text delta
    * ``content_block_start`` with ``tool_use`` → tool call header
    * ``content_block_delta`` with ``input_json_delta`` → tool arg delta
    * ``message_delta`` → finish_reason + usage

    Thinking blocks are counted (but their content is not forwarded
    to the caller — the agent core only tracks the count).
    """
    input_tokens = 0
    tool_blocks: dict[int, dict[str, str]] = {}  # index → {id, name}
    thinking_count = 0

    async for event in stream:
        etype = event.type

        if etype == "message_start":
            # Capture input token count from the initial message.
            usage = getattr(event.message, "usage", None)
            if usage:
                input_tokens = usage.input_tokens or 0

        elif etype == "content_block_start":
            block = event.content_block
            idx = event.index
            btype = getattr(block, "type", "")

            if btype == "tool_use":
                tool_blocks[idx] = {
                    "id": block.id,
                    "name": block.name,
                }
                # Emit the tool-call "header" delta (id + name).
                yield StreamChunk(
                    tool_call_deltas=[{
                        "index": idx,
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": "",
                        },
                    }],
                )

        elif etype == "content_block_delta":
            delta = event.delta
            idx = event.index
            dtype = getattr(delta, "type", "")

            if dtype == "text_delta":
                yield StreamChunk(delta=delta.text)

            elif dtype == "input_json_delta":
                info = tool_blocks.get(idx, {"id": "", "name": ""})
                yield StreamChunk(
                    tool_call_deltas=[{
                        "index": idx,
                        "id": info.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": "",
                            "arguments": delta.partial_json,
                        },
                    }],
                )

            elif dtype == "thinking_delta":
                # Count thinking blocks but don't forward content.
                # The thinking_count is emitted in usage on the final chunk.
                thinking_count += 1

        elif etype == "message_delta":
            # Final event: stop_reason + output token usage.
            output_tokens = 0
            if hasattr(event, "usage") and event.usage:
                output_tokens = event.usage.output_tokens or 0

            stop_reason = "stop"
            if hasattr(event, "delta") and event.delta:
                sr = getattr(event.delta, "stop_reason", None)
                if sr:
                    stop_reason = _STOP_REASON_MAP.get(sr, "stop")

            usage: dict[str, int] = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }
            if thinking_count > 0:
                usage["thinking_tokens"] = thinking_count

            yield StreamChunk(
                finish_reason=stop_reason,
                usage=usage,
            )

        # content_block_stop, message_stop: informational, skip.


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class MiniMaxClient:
    """Async client for the MiniMax Anthropic-compatible endpoint.

    Parameters
    ----------
    api_key:
        Bearer token. Pulled from ``MINIMAX_API_KEY`` / OS keyring
        if omitted.  Empty / missing values put the client in
        **mock mode** — every call returns the canned response.
    base_url:
        Root of the API. Pulled from ``MINIMAX_API_BASE`` if
        omitted; defaults to MiniMax's Anthropic-compatible proxy.
    model:
        Default model name used by :meth:`chat` and
        :meth:`stream_chat`. Individual calls can override.
    timeout:
        Per-request timeout in seconds.
    max_retries:
        Number of attempts on transient errors (network, 5xx,
        429). The ``anthropic`` SDK handles backoff automatically.
    mock:
        Force mock mode. Mostly useful in tests; if you set this
        you should also pass a deterministic ``api_key`` to make
        the client trivially mockable.
    client:
        Optional pre-built ``anthropic.AsyncAnthropic`` instance
        (useful for injecting test doubles).
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
        client: anthropic.AsyncAnthropic | None = None,
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
        self.mock = mock if mock is not None else (not bool(self.api_key))
        self._client = client  # caller may inject an anthropic mock

        # ``thinking_count`` is the value the agent should attach to
        # the next ``agent.message_chunk`` event's ``metadata``. The
        # mock path increments it on every ``stream_chat`` call (one
        # "think" per call), and the real path reads it from the
        # adapter's ``usage.thinking_tokens`` field. Either way it
        # is a per-call snapshot, not a running total — the
        # ``AgentCore`` reads the value *after* the call returns
        # and emits it on the final chunk.
        self.thinking_count: int = 0

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

        Side effect
        -----------

        Updates :attr:`thinking_count` after the stream completes so
        the caller can attach it to the next ``agent.message_chunk``
        event's ``metadata`` envelope. Mock mode increments per call
        (each canned call is one "think"); the real path reads the
        adapter's ``usage.thinking_tokens`` field (else 0).
        The value is reset to 0 at the start of every call so a
        partial / failed call cannot leak state from the previous
        one.
        """
        self.thinking_count = 0

        # ---- mock path (unchanged) --------------------------------------
        if self.mock:
            mock_thinking = 1  # one synthetic "think" per call
            async for c in _mock_stream(
                self.default_model if model is None else (model or self.default_model)
            ):
                if c.usage:
                    self.thinking_count = mock_thinking
                yield c
            return

        # ---- Anthropic path ----------------------------------------------
        system_prompt, remaining = _extract_system_prompt(messages)
        a_messages = _convert_messages(remaining)
        a_tools = _convert_tools(tools)

        client = self._ensure_client()

        try:
            async with client.messages.stream(
                model=model or self.default_model,
                system=system_prompt or anthropic.NOT_GIVEN,
                messages=a_messages,
                tools=a_tools or anthropic.NOT_GIVEN,
                tool_choice=(
                    _convert_tool_choice(tool_choice)
                    if a_tools
                    else anthropic.NOT_GIVEN
                ),
                temperature=temperature if temperature is not None else anthropic.NOT_GIVEN,
                max_tokens=max_tokens or 4096,
            ) as stream:
                async for chunk in _anthropic_stream_to_chunks(stream):
                    if chunk.usage and isinstance(
                        chunk.usage.get("thinking_tokens"), int
                    ):
                        self.thinking_count = chunk.usage["thinking_tokens"]
                    yield chunk
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic API error: {exc}") from exc

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def __aenter__(self) -> MiniMaxClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # -- internals ---------------------------------------------------------

    def _ensure_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=self.max_retries,
            )
        return self._client


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
