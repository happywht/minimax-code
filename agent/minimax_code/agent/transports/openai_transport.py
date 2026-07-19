"""OpenAI-compatible wire-protocol transport.

Uses the ``openai`` Python SDK to talk to any OpenAI-compatible endpoint
(OpenAI, 智谱 GLM, DeepSeek, Moonshot, Ollama, etc.).  Because the agent
core (``core.py``) already works with OpenAI-style messages and tool
schemas, this transport is nearly a passthrough — minimal conversion needed.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import openai

from ..reasoning import ReasoningEffort, coerce_effort
from ..types import LLMError, StreamChunk
from . import LLMTransport
from ._breaker import check_or_raise, record_outcome, resolve_breaker

logger = logging.getLogger(__name__)


class OpenAITransport(LLMTransport):
    """Transport for OpenAI-compatible APIs via ``openai`` SDK.

    Parameters
    ----------
    api_key:
        Bearer token for the API endpoint.
    base_url:
        Root URL (e.g. ``https://api.openai.com/v1``).
    timeout:
        Per-request timeout in seconds.
    max_retries:
        Attempts on transient errors (429/5xx/network).
    client:
        Optional pre-built ``openai.AsyncOpenAI`` instance.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float = 180.0,
        max_retries: int = 3,
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = client
        self._thinking_count = 0
        self._last_reasoning_effort: ReasoningEffort | None = None

    @property
    def thinking_count(self) -> int:
        return self._thinking_count

    @property
    def last_reasoning_effort(self) -> ReasoningEffort | None:
        """Reasoning effort normalised from the most recent ``stream_chat`` call.

        R54 records the coerced value; R56 emits it on the wire via the OpenAI
        ``reasoning_effort`` field (the emit seam
        :meth:`ReasoningEffort.to_openai_effort_token` degrades ``xhigh`` →
        ``high`` and drops ``none``; the official field accepts only ``minimal``
        / ``low`` / ``medium`` / ``high``, and many compat endpoints reject it
        outright). A ``None`` effort (the AgentConfig default) emits nothing, so
        the request kwargs stay byte-identical to the pre-R54 path.
        """
        return self._last_reasoning_effort

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
        """Stream via the OpenAI chat completions API.

        Messages and tools are already in OpenAI format, so we pass them
        through almost verbatim. The SDK's ``stream=True`` returns an
        async iterator of ``ChatCompletionChunk`` objects whose
        ``choices[0].delta`` we map to :class:`StreamChunk`.
        """
        self._thinking_count = 0
        # R54 coerce + R56 emit: normalise the runtime effort once, up-front,
        # then read it back below to emit the OpenAI ``reasoning_effort`` token.
        # The emit seam (``to_openai_effort_token``) drops ``none`` and degrades
        # ``xhigh`` → ``high`` (the official field accepts only minimal / low /
        # medium / high), so a ``None`` effort (the AgentConfig default) leaves
        # ``kwargs`` untouched — zero regression vs the pre-R54 path.
        self._last_reasoning_effort = coerce_effort(reasoning_effort)

        # R18: circuit-breaker pre-check. Fail-open — a missing / disabled /
        # faulty breaker resolves to None and check_or_raise is a no-op, so
        # the stream proceeds unprotected rather than not at all.
        breaker = resolve_breaker("llm:openai")
        check_or_raise(breaker)

        client = self._ensure_client()

        # Build kwargs — omit None values so the SDK uses its defaults.
        # Convert image blocks in user messages to OpenAI vision format.
        converted_messages = _convert_openai_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = _sanitize_tools(tools)
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        # R56: emit the reasoning-effort token on the wire. The emit seam drops
        # ``none`` and degrades ``xhigh`` → ``high``; a ``None`` effort (the
        # AgentConfig default) yields ``None`` here, so ``kwargs`` stays
        # unchanged — byte-identical to the pre-R56 request.
        _effort_token = (
            self._last_reasoning_effort.to_openai_effort_token()
            if self._last_reasoning_effort is not None
            else None
        )
        if _effort_token is not None:
            kwargs["reasoning_effort"] = _effort_token

        try:
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in _openai_stream_to_chunks(stream):
                if chunk.usage and isinstance(
                    chunk.usage.get("thinking_tokens"), int
                ):
                    self._thinking_count = chunk.usage["thinking_tokens"]
                yield chunk
        except openai.APIError as exc:
            record_outcome(
                breaker,
                success=False,
                status_code=getattr(exc, "status_code", None),
            )
            raise LLMError(
                f"OpenAI API error: {exc}",
                status_code=getattr(exc, "status_code", None),
            ) from exc
        else:
            record_outcome(breaker, success=True)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _ensure_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            self._client = openai.AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout,
                max_retries=self._max_retries,
            )
        return self._client


# ---------------------------------------------------------------------------
# Helpers (private)
# ---------------------------------------------------------------------------


def _sanitize_tools(
    tools: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Ensure tool definitions are valid OpenAI format dicts.

    Handles edge cases like missing ``parameters`` or non-dict ``function``.
    """
    out: list[dict[str, Any]] = []
    for tool in tools:
        t = dict(tool)
        fn = t.get("function")
        if not isinstance(fn, dict):
            fn = {"name": str(fn) if fn else "unknown"}
            t["function"] = fn
        # Ensure ``parameters`` exists and is a dict.
        if "parameters" not in fn or not isinstance(fn["parameters"], dict):
            fn["parameters"] = {"type": "object", "properties": {}}
        out.append(t)
    return out


def _convert_openai_messages(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Convert messages, handling image blocks in user messages.

    Transforms internal image blocks ``{type: "image", media_type, data}``
    into OpenAI vision format ``{type: "image_url", image_url: {url}}``.
    String content and other roles are passed through unchanged.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        m = dict(msg)
        role = m.get("role", "")
        content = m.get("content")

        if role == "user" and isinstance(content, list):
            converted: list[dict[str, Any]] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    media = block.get("media_type", "image/png")
                    data = block.get("data", "")
                    converted.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{media};base64,{data}"},
                    })
                else:
                    converted.append(block)
            m["content"] = converted

        result.append(m)
    return result


async def _openai_stream_to_chunks(
    stream: openai.AsyncStream[openai.types.chat.ChatCompletionChunk],
) -> AsyncIterator[StreamChunk]:
    """Map OpenAI streaming chunks to :class:`StreamChunk`.

    Key mappings:
    * ``delta.content`` → ``StreamChunk.delta`` (text)
    * ``delta.tool_calls[i]`` → ``StreamChunk.tool_call_deltas``
    * ``finish_reason`` on final chunk
    * ``usage`` on the final chunk (when ``stream_options.include_usage``)
    """
    async for chunk in stream:
        if not chunk.choices:
            # Some providers send an empty-choices chunk at the end
            # carrying only ``usage``.
            if hasattr(chunk, "usage") and chunk.usage:
                yield StreamChunk(
                    finish_reason=None,
                    usage={
                        "prompt_tokens": chunk.usage.prompt_tokens or 0,
                        "completion_tokens": chunk.usage.completion_tokens or 0,
                        "total_tokens": chunk.usage.total_tokens or 0,
                    },
                )
            continue

        choice = chunk.choices[0]
        delta = choice.delta

        # Text content
        text = delta.content
        tool_call_deltas: list[dict[str, Any]] = []

        # Tool calls — convert to the flat format core.py expects.
        if delta.tool_calls:
            for tc in delta.tool_calls:
                entry: dict[str, Any] = {
                    "index": tc.index,
                    "id": tc.id or "",
                    "type": "function",
                    "function": {
                        "name": tc.function.name if tc.function and tc.function.name else "",
                        "arguments": (
                            tc.function.arguments
                            if tc.function and tc.function.arguments
                            else ""
                        ),
                    },
                }
                tool_call_deltas.append(entry)

        # Build the StreamChunk — yield only if there's content.
        if text or tool_call_deltas:
            yield StreamChunk(
                delta=text or "",
                tool_call_deltas=tool_call_deltas,
            )

        # Finish reason
        if choice.finish_reason:
            # OpenAI uses "tool_calls" (plural) for tool-use stops.
            # Keep as-is — core.py already handles this value.
            yield StreamChunk(
                finish_reason=choice.finish_reason,
                usage={},
            )

    # If usage wasn't sent in-stream (some providers don't support
    # ``stream_options``), the final ``finish_reason`` chunk above
    # already carries empty usage — downstream code handles that.
