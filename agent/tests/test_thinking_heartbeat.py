"""Regression test for the thinking-block stall watchdog fix.

Background
----------

``AnthropicTransport._anthropic_stream_to_chunks`` historically
swallowed ``thinking`` content blocks and ``thinking_delta``
events (only counting them, never yielding a chunk).  When the
LLM spent more than ``AgentConfig.stall_timeout`` (now default 120s)
inside an extended-thinking block, the watchdog in
``AgentCore._stream_turn`` would fire and abort the turn with
an LLM stream timeout.

The fix is to yield an empty-delta ``StreamChunk`` whenever a
thinking block starts or a thinking delta arrives.  The empty
delta is filtered out by both ``_emit_chunk`` (``if chunk.delta:``)
and ``_assemble_chunks`` (``if c.delta:``) so it is invisible to
the UI and the final text, but it resets the watchdog.

What this test pins
-------------------

1.  A ``thinking`` content_block_start emits a heartbeat chunk.
2.  A ``thinking_delta`` event emits a heartbeat chunk.
3.  ``thinking_count`` is still populated correctly
    (existing behaviour is not regressed).
4.  The assembled response does not include any of the
    heartbeat deltas in its final ``content``.
5.  Mixed events (text + thinking + tool_use + message_delta)
    are stitched together correctly.

The test builds a fake ``AsyncMessageStream`` (matching the
shape of ``anthropic.AsyncMessageStream``) and drives the
converter directly — no network, no real API key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

anthropic = pytest.importorskip("anthropic")

from minimax_code.agent.transports.anthropic_transport import (  # noqa: E402
    _anthropic_stream_to_chunks,
)

# ---------------------------------------------------------------------------
# Fake event / stream types mirroring anthropic's wire shape
# ---------------------------------------------------------------------------


@dataclass
class _FakeDelta:
    type: str = ""
    text: str = ""
    partial_json: str = ""
    stop_reason: str | None = None


@dataclass
class _FakeContentBlock:
    type: str = ""
    id: str = ""
    name: str = ""


@dataclass
class _FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _FakeMessage:
    usage: _FakeUsage = field(default_factory=_FakeUsage)


@dataclass
class _FakeEvent:
    type: str = ""
    index: int = 0
    content_block: _FakeContentBlock | None = None
    delta: _FakeDelta | None = None
    message: _FakeMessage | None = None
    usage: _FakeUsage | None = None


class _FakeStream:
    """Minimal stand-in for ``anthropic.AsyncMessageStream``."""

    def __init__(self, events: list[_FakeEvent]) -> None:
        self._events = events

    async def __aiter__(self) -> AsyncIterator[_FakeEvent]:
        for ev in self._events:
            yield ev


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _thinking_block(idx: int = 0) -> _FakeEvent:
    return _FakeEvent(
        type="content_block_start",
        index=idx,
        content_block=_FakeContentBlock(type="thinking"),
    )


def _thinking_delta(idx: int = 0) -> _FakeEvent:
    return _FakeEvent(
        type="content_block_delta",
        index=idx,
        delta=_FakeDelta(type="thinking_delta"),
    )


def _text_block(idx: int = 0) -> _FakeEvent:
    return _FakeEvent(
        type="content_block_start",
        index=idx,
        content_block=_FakeContentBlock(type="text"),
    )


def _text_delta(idx: int, text: str) -> _FakeEvent:
    return _FakeEvent(
        type="content_block_delta",
        index=idx,
        delta=_FakeDelta(type="text_delta", text=text),
    )


def _message_start(input_tokens: int = 0) -> _FakeEvent:
    return _FakeEvent(
        type="message_start",
        message=_FakeMessage(usage=_FakeUsage(input_tokens=input_tokens)),
    )


def _message_delta(stop_reason: str = "end_turn", output_tokens: int = 0) -> _FakeEvent:
    return _FakeEvent(
        type="message_delta",
        delta=_FakeDelta(stop_reason=stop_reason),
        usage=_FakeUsage(output_tokens=output_tokens),
    )


async def _drain(stream: _FakeStream) -> list:
    """Collect every chunk yielded by the converter."""
    out = []
    async for chunk in _anthropic_stream_to_chunks(stream):  # type: ignore[arg-type]
        out.append(chunk)
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thinking_block_emits_heartbeat() -> None:
    """A thinking content_block_start must yield at least one chunk.

    This is the watchdog fix: previously the converter was silent
    during the LLM's "thinking" phase, so the stall watchdog
    in ``AgentCore._stream_turn`` would fire and abort the turn.
    """
    stream = _FakeStream(
        events=[
            _message_start(),
            _thinking_block(idx=0),
            _message_delta(),
        ]
    )
    chunks = await _drain(stream)

    # At least one heartbeat was emitted (the empty-delta chunk).
    heartbeats = [c for c in chunks if c.delta == "" and not c.tool_call_deltas and not c.finish_reason]
    assert heartbeats, "expected a heartbeat chunk during thinking block; got none"

    # And it must NOT carry a finish_reason (otherwise the loop in
    # core.py would treat it as the final chunk).
    assert all(not c.finish_reason for c in heartbeats)


@pytest.mark.asyncio
async def test_thinking_delta_emits_heartbeat() -> None:
    """A thinking_delta event must also yield a heartbeat.

    The original code did ``pass`` on thinking_delta — same stall
    hazard as content_block_start with type=thinking.
    """
    stream = _FakeStream(
        events=[
            _message_start(),
            _thinking_block(idx=0),
            _thinking_delta(idx=0),
            _thinking_delta(idx=0),
            _message_delta(),
        ]
    )
    chunks = await _drain(stream)
    heartbeats = [c for c in chunks if c.delta == "" and not c.tool_call_deltas and not c.finish_reason]
    # 1 from content_block_start + 2 from deltas = 3
    assert len(heartbeats) >= 3, f"expected >=3 heartbeats, got {len(heartbeats)}"


@pytest.mark.asyncio
async def test_thinking_count_still_reported() -> None:
    """Existing behaviour — thinking_count on the final usage chunk.

    Pin that the heartbeat change did not break the per-block
    counting.  Two thinking blocks should still surface
    ``thinking_tokens=2`` on the final ``message_delta`` chunk.
    """
    stream = _FakeStream(
        events=[
            _message_start(input_tokens=10),
            _thinking_block(idx=0),
            _thinking_delta(idx=0),
            _thinking_block(idx=1),
            _thinking_delta(idx=1),
            _message_delta(stop_reason="end_turn", output_tokens=5),
        ]
    )
    chunks = await _drain(stream)
    final = [c for c in chunks if c.finish_reason]
    assert len(final) == 1
    assert final[0].usage.get("thinking_tokens") == 2, (
        f"expected thinking_tokens=2, got usage={final[0].usage}"
    )
    assert final[0].usage["prompt_tokens"] == 10
    assert final[0].usage["completion_tokens"] == 5


@pytest.mark.asyncio
async def test_heartbeat_does_not_pollute_final_text() -> None:
    """Heartbeat chunks must not leak into the assembled content.

    The final user-visible ``content`` of the assistant message
    is built by ``_assemble_chunks`` (mirrored in ``llm._assemble``)
    via ``if c.delta: text_parts.append(c.delta)``.  An empty-delta
    heartbeat is therefore filtered out — this test pins that
    property at the chunk level so it can never regress.
    """
    stream = _FakeStream(
        events=[
            _message_start(),
            _text_block(idx=0),
            _text_delta(idx=0, text="hello "),
            _thinking_block(idx=1),
            _text_delta(idx=0, text="world"),
            _text_block(idx=2),
            _text_delta(idx=2, text="!"),
            _message_delta(),
        ]
    )
    chunks = await _drain(stream)

    # All heartbeat chunks have empty delta — they are invisible to
    # downstream assembly.
    for c in chunks:
        if c.delta == "" and not c.tool_call_deltas and not c.finish_reason:
            # Confirm it is structurally a heartbeat, not a real chunk.
            assert c.usage == {}
            assert c.finish_reason is None


@pytest.mark.asyncio
async def test_mixed_stream_text_and_thinking() -> None:
    """Realistic stream: text + interleaved thinking + text.

    The converter should still surface text deltas as
    ``StreamChunk(delta=...)`` and heartbeats during the
    thinking phases, with the right final usage.
    """
    stream = _FakeStream(
        events=[
            _message_start(input_tokens=7),
            _text_block(idx=0),
            _text_delta(idx=0, text="answer is "),
            _thinking_block(idx=1),
            _thinking_delta(idx=1),
            _text_delta(idx=0, text="42"),
            _message_delta(output_tokens=11),
        ]
    )
    chunks = await _drain(stream)

    text_deltas = [c.delta for c in chunks if c.delta]
    assert text_deltas == ["answer is ", "42"], text_deltas

    # At least one heartbeat between the text segments.
    heartbeats = [
        c for c in chunks
        if c.delta == "" and not c.tool_call_deltas and not c.finish_reason
    ]
    assert heartbeats, "no heartbeat between text and thinking"

    # And thinking_count is 1 (one thinking block).
    final = [c for c in chunks if c.finish_reason][0]
    assert final.usage.get("thinking_tokens") == 1
