"""Anthropic wire-protocol transport.

Wraps the ``anthropic`` Python SDK and translates between OpenAI-style
messages/tools (used throughout the agent core) and Anthropic's native
format.  Extracted from ``llm.py`` during the transport refactoring so
that the Anthropic-specific code lives in one place.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import anthropic

from ..types import LLMError, StreamChunk
from . import LLMTransport

logger = logging.getLogger(__name__)

# Anthropic → OpenAI finish_reason mapping
_STOP_REASON_MAP: dict[str, str] = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
}


# ---------------------------------------------------------------------------
# Format converters (private)
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
    # Track tool_use IDs that we've emitted so we can drop orphaned
    # tool_result entries (e.g. from ghost tool calls filtered above).
    emitted_tool_ids: set[str] = set()

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
            # Convert image blocks to Anthropic format
            converted: list[dict[str, Any]] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    converted.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": block.get("media_type", "image/png"),
                            "data": block["data"],
                        },
                    })
                else:
                    converted.append(block)
            result.append({"role": "user", "content": converted})

        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            text = msg.get("content") or ""
            if text:
                blocks.append({"type": "text", "text": str(text)})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                tc_name = tc.get("name") or fn.get("name", "")
                # --- Skip ghost tool calls (empty name) -------------------
                if not tc_name.strip():
                    logger.warning(
                        "convert_messages: skipping ghost tool_use (id=%r, name=%r)",
                        tc.get("id", ""), tc_name,
                    )
                    continue
                args = (
                    fn.get("arguments", "{}")
                    if fn.get("arguments") is not None
                    else tc.get("arguments", "{}")
                )
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args.strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                # Anthropic requires non-empty, unique tool_use IDs.
                tc_id = tc.get("id") or ""
                if not tc_id.strip():
                    tc_id = f"toolu_{uuid.uuid4().hex[:24]}"
                    logger.warning(
                        "convert_messages: empty tool_use id, generated %s "
                        "(fn.name=%r)", tc_id, tc_name,
                    )
                emitted_tool_ids.add(tc_id)
                blocks.append({
                    "type": "tool_use",
                    "id": tc_id,
                    "name": tc_name,
                    "input": args,
                })
            if not blocks:
                blocks.append({"type": "text", "text": ""})
            result.append({"role": "assistant", "content": blocks})

        elif role == "tool":
            tool_use_id = msg.get("tool_call_id") or ""
            if not tool_use_id.strip():
                tool_use_id = f"toolu_{uuid.uuid4().hex[:24]}"
                logger.warning(
                    "convert_messages: empty tool_result id, generated %s",
                    tool_use_id,
                )
            # Drop orphaned tool results whose tool_use was filtered as
            # a ghost (empty name).  Anthropic rejects unmatched results.
            if tool_use_id not in emitted_tool_ids:
                logger.warning(
                    "convert_messages: skipping orphaned tool_result "
                    "tool_use_id=%s (no matching tool_use block)",
                    tool_use_id,
                )
                continue
            logger.debug(
                "convert_messages: tool_result tool_use_id=%s content_len=%d",
                tool_use_id, len(str(msg.get("content", ""))),
            )
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": str(msg.get("content", "")),
            })

        elif role == "system":
            # System messages should have been extracted already.
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
            usage = getattr(event.message, "usage", None)
            if usage:
                input_tokens = usage.input_tokens or 0

        elif etype == "content_block_start":
            block = event.content_block
            idx = event.index
            btype = getattr(block, "type", "")

            if btype == "tool_use":
                logger.debug(
                    "stream: content_block_start tool_use idx=%s id=%r name=%r",
                    idx, block.id, block.name,
                )
                tool_blocks[idx] = {
                    "id": block.id,
                    "name": block.name,
                }
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

            elif btype == "thinking":
                # Count distinct thinking blocks, not delta fragments.
                thinking_count += 1
                # Heartbeat: yield an empty-delta chunk so the
                # stall-watchdog in ``AgentCore._stream_turn`` does
                # not fire while the LLM is mid-thought.  The chunk
                # is filtered out by both ``_emit_chunk`` (``if
                # chunk.delta:``) and ``_assemble_chunks`` (``if
                # c.delta:``) so it is invisible to the UI and to
                # the final text.
                yield StreamChunk()

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
                # Counted at ``content_block_start`` (one tick per
                # block, not per delta).  Yield an empty-delta
                # heartbeat so the stall watchdog does not fire on
                # long thinking-block deltas — same rationale as
                # the heartbeat in ``content_block_start``.
                yield StreamChunk()

        elif etype == "message_delta":
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


# ---------------------------------------------------------------------------
# Transport implementation
# ---------------------------------------------------------------------------


class AnthropicTransport(LLMTransport):
    """Transport that talks Anthropic's native protocol via ``anthropic`` SDK.

    Parameters
    ----------
    api_key:
        Bearer token for the Anthropic-compatible endpoint.
    base_url:
        Root URL of the API.
    timeout:
        Per-request timeout in seconds.
    max_retries:
        Attempts on transient errors (429/5xx/network).
    client:
        Optional pre-built ``anthropic.AsyncAnthropic`` instance
        (useful for injecting test doubles).
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float = 60.0,
        max_retries: int = 3,
        client: anthropic.AsyncAnthropic | None = None,
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
        self._thinking_count = 0

        system_prompt, remaining = _extract_system_prompt(messages)
        a_messages = _convert_messages(remaining)
        a_tools = _convert_tools(tools)

        client = self._ensure_client()

        try:
            async with client.messages.stream(
                model=model,
                system=system_prompt or anthropic.NOT_GIVEN,
                messages=a_messages,
                tools=a_tools or anthropic.NOT_GIVEN,
                tool_choice=(
                    _convert_tool_choice(tool_choice)
                    if a_tools
                    else anthropic.NOT_GIVEN
                ),
                temperature=(
                    temperature if temperature is not None
                    else anthropic.NOT_GIVEN
                ),
                max_tokens=max_tokens or 4096,
            ) as stream:
                async for chunk in _anthropic_stream_to_chunks(stream):
                    if chunk.usage and isinstance(
                        chunk.usage.get("thinking_tokens"), int
                    ):
                        self._thinking_count = chunk.usage["thinking_tokens"]
                    yield chunk
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic API error: {exc}") from exc

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _ensure_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout,
                max_retries=self._max_retries,
            )
        return self._client
