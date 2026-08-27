"""Regression: OpenAI transport usage handling on compat providers.

v1.6.5 (#138) — Zhipu GLM (and other OpenAI-compatible providers like
DeepSeek) attach ``usage`` to the chunk that carries ``finish_reason``
instead of a trailing empty-choices chunk, and stream their thinking via
``delta.reasoning_content``. The transport dropped that usage entirely,
so every GLM reply landed with tokens_in=tokens_out=0 — which made the
model switcher *look* like it never switched (no token accounting, no
thinking_count).

These tests feed real SSE bytes through the real ``openai`` SDK (mock
httpx transport, same trick as the anthropic tests in
``test_agent_core.py``) so SDK parsing can't drift from what providers
actually send.
"""

from __future__ import annotations

import httpx
import openai
import pytest

from minimax_code.agent.transports.openai_transport import (
    OpenAITransport,
    _usage_to_dict,
)

# --- Wire fixtures: byte-for-byte shapes captured from live providers. ------

ZHIPU_REASONING = (
    '{"id":"t1","created":1,"object":"chat.completion.chunk","model":"glm-5.3",'
    '"choices":[{"index":0,"delta":{"role":"assistant","reasoning_content":"..."}}]}'
)
ZHIPU_CONTENT = (
    '{"id":"t1","created":1,"object":"chat.completion.chunk","model":"glm-5.3",'
    '"choices":[{"index":0,"delta":{"role":"assistant","content":"received"}}]}'
)
# Zhipu shape: usage rides on the SAME chunk as finish_reason, with the
# reasoning budget under completion_tokens_details.reasoning_tokens.
ZHIPU_FINISH = (
    '{"id":"t1","created":1,"object":"chat.completion.chunk","model":"glm-5.3",'
    '"choices":[{"index":0,"finish_reason":"stop","delta":{"role":"assistant","content":""}}],'
    '"usage":{"prompt_tokens":16,"completion_tokens":66,"total_tokens":82,'
    '"prompt_tokens_details":{"cached_tokens":0},'
    '"completion_tokens_details":{"reasoning_tokens":61}}}'
)

# Official OpenAI shape: finish_reason chunk WITHOUT usage, then a
# trailing empty-choices chunk carrying only usage.
OPENAI_FINISH = (
    '{"id":"t2","created":1,"object":"chat.completion.chunk","model":"gpt-x",'
    '"choices":[{"index":0,"finish_reason":"stop","delta":{"role":"assistant"}}]}'
)
OPENAI_USAGE = (
    '{"id":"t2","created":1,"object":"chat.completion.chunk","model":"gpt-x",'
    '"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}'
)


def _sse(*payloads: str) -> bytes:
    lines = [f"data: {p}" for p in payloads]
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


def _make_transport(body: bytes) -> OpenAITransport:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    return OpenAITransport(
        api_key="test",
        base_url="https://api.test/v1",
        client=openai.AsyncOpenAI(
            api_key="test",
            base_url="https://api.test/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(responder)),
        ),
    )


async def _collect(transport: OpenAITransport) -> list:
    return [
        chunk
        async for chunk in transport.stream_chat(
            [{"role": "user", "content": "hi"}], model="glm-5.3"
        )
    ]


# --- Zhipu / compat shape ----------------------------------------------------


@pytest.mark.asyncio
async def test_zhipu_usage_rides_on_finish_chunk() -> None:
    """Usage attached to the finish_reason chunk must survive the mapping."""
    transport = _make_transport(
        _sse(ZHIPU_REASONING, ZHIPU_CONTENT, ZHIPU_FINISH)
    )
    chunks = await _collect(transport)

    finish = [c for c in chunks if c.finish_reason]
    assert len(finish) == 1
    assert finish[0].usage == {
        "prompt_tokens": 16,
        "completion_tokens": 66,
        "total_tokens": 82,
        "thinking_tokens": 61,
    }


@pytest.mark.asyncio
async def test_zhipu_reasoning_chunks_do_not_pollute_text() -> None:
    """``delta.reasoning_content`` frames must not become text deltas."""
    transport = _make_transport(
        _sse(ZHIPU_REASONING, ZHIPU_CONTENT, ZHIPU_FINISH)
    )
    chunks = await _collect(transport)

    text = "".join(c.delta for c in chunks if not c.finish_reason)
    assert text == "received"


@pytest.mark.asyncio
async def test_zhipu_thinking_count_from_reasoning_tokens() -> None:
    """reasoning_tokens feeds the thinking_count channel (GLM path)."""
    transport = _make_transport(
        _sse(ZHIPU_REASONING, ZHIPU_CONTENT, ZHIPU_FINISH)
    )
    await _collect(transport)
    assert transport.thinking_count == 61


# --- Official OpenAI shape ----------------------------------------------------


@pytest.mark.asyncio
async def test_openai_trailing_usage_chunk_still_works() -> None:
    """The pre-existing empty-choices usage chunk keeps working."""
    transport = _make_transport(_sse(OPENAI_FINISH, OPENAI_USAGE))
    chunks = await _collect(transport)

    finish = [c for c in chunks if c.finish_reason]
    assert len(finish) == 1
    # No usage on the finish chunk itself for this provider shape.
    assert finish[0].usage == {}
    # The trailing empty-choices chunk carries the usage.
    usage_chunks = [c for c in chunks if not c.finish_reason and c.usage]
    assert len(usage_chunks) == 1
    assert usage_chunks[0].usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert transport.thinking_count == 0


@pytest.mark.asyncio
async def test_stream_without_usage_yields_empty_dict() -> None:
    """No usage anywhere → finish chunk keeps an empty usage dict."""
    transport = _make_transport(_sse(OPENAI_FINISH))
    chunks = await _collect(transport)

    finish = [c for c in chunks if c.finish_reason]
    assert len(finish) == 1
    assert finish[0].usage == {}


# --- Helper unit level --------------------------------------------------------


def test_usage_to_dict_none_and_empty() -> None:
    assert _usage_to_dict(None) == {}
    assert _usage_to_dict({}) == {}


def test_usage_to_dict_raw_dict_with_reasoning() -> None:
    data = _usage_to_dict(
        {
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "total_tokens": 3,
            "completion_tokens_details": {"reasoning_tokens": 2},
        }
    )
    assert data == {
        "prompt_tokens": 1,
        "completion_tokens": 2,
        "total_tokens": 3,
        "thinking_tokens": 2,
    }


def test_usage_to_dict_zero_reasoning_not_recorded() -> None:
    data = _usage_to_dict(
        {
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "total_tokens": 3,
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
    )
    assert "thinking_tokens" not in data
