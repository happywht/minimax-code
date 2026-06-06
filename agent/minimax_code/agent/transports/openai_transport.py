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

from ..types import LLMError, StreamChunk
from . import LLMTransport

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
        timeout: float = 60.0,
        max_retries: int = 3,
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = client
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
        """Stream via the OpenAI chat completions API.

        Messages and tools are already in OpenAI format, so we pass them
        through almost verbatim. The SDK's ``stream=True`` returns an
        async iterator of ``ChatCompletionChunk`` objects whose
        ``choices[0].delta`` we map to :class:`StreamChunk`.
        """
        self._thinking_count = 0

        client = self._ensure_client()

        # Build kwargs — omit None values so the SDK uses its defaults.
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [dict(m) for m in messages],
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

        try:
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in _openai_stream_to_chunks(stream):
                if chunk.usage and isinstance(
                    chunk.usage.get("thinking_tokens"), int
                ):
                    self._thinking_count = chunk.usage["thinking_tokens"]
                yield chunk
        except openai.APIError as exc:
            raise LLMError(f"OpenAI API error: {exc}") from exc

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
