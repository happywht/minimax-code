"""Agent core tests — LLM client, conversation loop, cancellation.

The LLM client is exercised with ``respx``-style httpx mocks built
from a small in-process httpx ``MockTransport``. The loop is
exercised with a fake LLM that yields canned stream chunks so we
do not need any network IO.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from typing import Any

import anthropic
import httpx
import pytest

from minimax_code.agent.core import (
    AgentConfig,
    AgentCore,
)
from minimax_code.agent.llm import (
    LLMError,
    LLMResponse,
    MiniMaxClient,
    StreamChunk,
)
from minimax_code.agent.tools import (
    Tool,
    ToolRegistry,
    ToolResult,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeLLM:
    """Drop-in replacement for :class:`MiniMaxClient` that returns canned chunks.

    Each call advances the ``responses`` queue. When the queue is
    empty, we return a default "done" chunk. The recorded
    ``messages`` list captures every payload the agent sent.
    """

    def __init__(self, responses: Iterable[list[StreamChunk]] | None = None) -> None:
        self._queue: list[list[StreamChunk]] = list(responses or [])
        self.messages: list[list[dict[str, Any]]] = []
        self.tools_payloads: list[list[dict[str, Any]] | None] = []
        self.call_count = 0

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self.call_count += 1
        self.messages.append(list(messages))
        self.tools_payloads.append(tools)
        if self._queue:
            chunks = self._queue.pop(0)
        else:
            chunks = [StreamChunk(delta="(default answer)", finish_reason="stop")]
        for c in chunks:
            yield c


class StallingLLM:
    """Fake LLM that emits one chunk and then stalls mid-stream."""

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta="partial answer")
        await asyncio.sleep(1)


class CountingTool(Tool):
    name = "echo"
    description = "echoes its input back"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_mode: str | None = None

    async def run(self, **kwargs: Any) -> ToolResult:
        self.calls.append(kwargs)
        if self.fail_mode == "raise":
            raise RuntimeError("boom")
        if self.fail_mode == "timeout":
            await asyncio.sleep(10)
        return ToolResult.ok(output={"echoed": kwargs.get("text")})


def _tool_call(name: str, args: Any, call_id: str = "call_1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _text_response(text: str) -> list[StreamChunk]:
    return [
        StreamChunk(delta=text),
        StreamChunk(finish_reason="stop", usage={"prompt_tokens": 3, "completion_tokens": len(text), "total_tokens": 3 + len(text)}),
    ]


def _tool_response(call: dict[str, Any], text_tail: str = "All done.") -> list[StreamChunk]:
    return [
        StreamChunk(delta="thinking… "),
        StreamChunk(tool_call_deltas=[call], finish_reason="tool_calls", usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}),
    ]


# ---------------------------------------------------------------------------
# LLM client: mock httpx + retry
# ---------------------------------------------------------------------------


def _make_transport(responder):
    """Build an httpx MockTransport that calls ``responder(request)``."""
    return httpx.MockTransport(responder)


def _make_anthropic_client(
    handler, *, api_key: str = "test", base_url: str = "https://example.invalid", max_retries: int = 3,
) -> anthropic.AsyncAnthropic:
    """Create an ``anthropic.AsyncAnthropic`` backed by a mock httpx transport."""
    return anthropic.AsyncAnthropic(
        api_key=api_key,
        base_url=base_url,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_retries=max_retries,
    )


def test_mock_mode_skips_network() -> None:
    client = MiniMaxClient(api_key="", mock=True)
    assert client.mock is True


def test_chat_assembles_text_chunks() -> None:
    async def run() -> None:
        client = MiniMaxClient(api_key="", mock=True)
        resp = await client.chat([{"role": "user", "content": "hi"}])
        assert "mock" in resp.message["content"].lower()
        assert resp.finish_reason == "stop"
        assert resp.usage["total_tokens"] >= 1

    asyncio.run(run())


def test_streaming_yields_text_in_order() -> None:
    async def run() -> list[str]:
        client = MiniMaxClient(api_key="", mock=True)
        out: list[str] = []
        async for c in client.stream_chat([{"role": "user", "content": "hi"}]):
            if c.delta:
                out.append(c.delta)
        return out

    chunks = asyncio.run(run())
    assert "".join(chunks)


def test_retries_on_5xx_then_succeeds() -> None:
    """The Anthropic SDK retries 5xx errors internally."""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(
                503,
                json={"type": "error", "error": {"type": "api_error", "message": "unavailable"}},
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_anthropic_sse_text("hi from retry"),
        )

    async def run() -> None:
        mock_anthropic = _make_anthropic_client(handler, max_retries=3)
        client = MiniMaxClient(
            api_key="test",
            base_url="https://example.invalid",
            max_retries=3,
            client=mock_anthropic,
        )
        resp = await client.chat([{"role": "user", "content": "hi"}])
        assert resp.message["content"] == "hi from retry"
        await mock_anthropic.close()

    asyncio.run(run())
    assert len(attempts) == 3


def test_4xx_is_fatal_no_retry() -> None:
    """The Anthropic SDK does not retry 4xx errors."""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(
            401,
            json={"type": "error", "error": {"type": "authentication_error", "message": "unauthorized"}},
        )

    async def run() -> None:
        mock_anthropic = _make_anthropic_client(handler, api_key="bad", max_retries=5)
        client = MiniMaxClient(
            api_key="bad",
            base_url="https://example.invalid",
            max_retries=5,
            client=mock_anthropic,
        )
        with pytest.raises(LLMError) as excinfo:
            await client.chat([{"role": "user", "content": "hi"}])
        assert "401" in str(excinfo.value)
        await mock_anthropic.close()

    asyncio.run(run())
    assert len(attempts) == 1


def test_tool_call_streaming_assembly() -> None:
    """Chunks of a single tool_call should be merged into one entry."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_anthropic_sse_tool_use(
                "c1", "echo",
                ['{"text":', '"hi"}'],
            ),
        )

    async def run() -> LLMResponse:
        mock_anthropic = _make_anthropic_client(handler, api_key="x")
        client = MiniMaxClient(
            api_key="x",
            base_url="https://example.invalid",
            client=mock_anthropic,
        )
        result = await client.chat([{"role": "user", "content": "go"}])
        await mock_anthropic.close()
        return result

    resp = asyncio.run(run())
    assert resp.finish_reason == "tool_calls"
    assert resp.message.get("tool_calls"), resp.message
    tc = resp.message["tool_calls"][0]
    assert tc["function"]["name"] == "echo"
    assert json.loads(tc["function"]["arguments"]) == {"text": "hi"}


# ---------------------------------------------------------------------------
# Anthropic SSE helpers for transport testing
# ---------------------------------------------------------------------------


def _anthropic_sse_text(
    text: str, input_tokens: int = 1, output_tokens: int = 1,
) -> str:
    """Build Anthropic-format SSE for a text response."""
    msg_start = json.dumps({
        "type": "message_start",
        "message": {
            "id": "msg_test", "type": "message", "role": "assistant",
            "content": [], "model": "test",
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 0},
        },
    })
    block_start = json.dumps({
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""},
    })
    block_delta = json.dumps({
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": text},
    })
    block_stop = json.dumps({"type": "content_block_stop", "index": 0})
    msg_delta = json.dumps({
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    })
    msg_stop = json.dumps({"type": "message_stop"})
    return (
        f"event: message_start\ndata: {msg_start}\n\n"
        f"event: content_block_start\ndata: {block_start}\n\n"
        f"event: content_block_delta\ndata: {block_delta}\n\n"
        f"event: content_block_stop\ndata: {block_stop}\n\n"
        f"event: message_delta\ndata: {msg_delta}\n\n"
        f"event: message_stop\ndata: {msg_stop}\n\n"
    )


def _anthropic_sse_tool_use(
    tool_id: str,
    tool_name: str,
    partial_json_fragments: list[str],
    input_tokens: int = 1,
    output_tokens: int = 4,
) -> str:
    """Build Anthropic-format SSE for a tool_use response."""
    msg_start = json.dumps({
        "type": "message_start",
        "message": {
            "id": "msg_test", "type": "message", "role": "assistant",
            "content": [], "model": "test",
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 0},
        },
    })
    block_start = json.dumps({
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": tool_id, "name": tool_name},
    })
    delta_events = ""
    for fragment in partial_json_fragments:
        delta_events += (
            f"event: content_block_delta\ndata: "
            f'{json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": fragment}})}'
            "\n\n"
        )
    block_stop = json.dumps({"type": "content_block_stop", "index": 0})
    msg_delta = json.dumps({
        "type": "message_delta",
        "delta": {"stop_reason": "tool_use", "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    })
    msg_stop = json.dumps({"type": "message_stop"})
    return (
        f"event: message_start\ndata: {msg_start}\n\n"
        f"event: content_block_start\ndata: {block_start}\n\n"
        + delta_events
        + f"event: content_block_stop\ndata: {block_stop}\n\n"
        f"event: message_delta\ndata: {msg_delta}\n\n"
        f"event: message_stop\ndata: {msg_stop}\n\n"
    )


# ---------------------------------------------------------------------------
# Conversation loop
# ---------------------------------------------------------------------------


def _fresh_registry(tool: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool)
    return reg


@pytest.mark.asyncio
async def test_loop_direct_answer_no_tools() -> None:
    fake = FakeLLM([_text_response("Plain answer.")])
    core = AgentCore(llm=fake, registry=_fresh_registry(CountingTool()))
    result = await core.run(session_id="s1", user_message="hi")
    assert result.final_text == "Plain answer."
    assert result.iterations == 1
    assert result.cancelled is False
    assert result.truncated is False


@pytest.mark.asyncio
async def test_loop_dispatches_tool_then_finalizes() -> None:
    tool = CountingTool()
    call = _tool_call("echo", {"text": "ping"}, call_id="call_echo_1")
    fake = FakeLLM(
        [
            _tool_response(call),
            _text_response("Tool returned: ping"),
        ]
    )
    chunks: list[str] = []
    tool_calls_seen: list[dict[str, Any]] = []
    tool_results_seen: list[ToolResult] = []
    statuses: list[str] = []

    core = AgentCore(llm=fake, registry=_fresh_registry(tool))
    core.on_chunk = lambda d, done, _metadata=None: _maybe_coro(chunks.append(d))
    core.on_tool_call = lambda c: _maybe_coro(tool_calls_seen.append(c))
    core.on_tool_result = lambda c, r: _maybe_coro(tool_results_seen.append(r))
    core.on_status = lambda s, _d: _maybe_coro(statuses.append(s))

    result = await core.run(session_id="s1", user_message="echo please")

    assert result.final_text == "Tool returned: ping"
    assert result.iterations == 2
    assert len(tool.calls) == 1
    assert tool.calls[0]["text"] == "ping"
    assert "Tool returned: ping" in "".join(chunks)
    assert any(c["name"] == "echo" for c in tool_calls_seen)
    assert tool_results_seen[0].success is True
    assert statuses[:3] == ["thinking", "calling_tool", "tool_running"]


@pytest.mark.asyncio
async def test_loop_persists_messages() -> None:
    persisted: list[dict[str, Any]] = []

    async def persist(session_id: str, message: dict[str, Any]) -> None:
        persisted.append({"session_id": session_id, **message})

    tool = CountingTool()
    call = _tool_call("echo", {"text": "x"}, call_id="c1")
    fake = FakeLLM([_tool_response(call), _text_response("ok")])
    core = AgentCore(llm=fake, registry=_fresh_registry(tool), persist_message=persist)
    await core.run(session_id="s1", user_message="go")
    roles = [m["role"] for m in persisted]
    # user → assistant (with tool_calls) → tool → assistant (final)
    assert roles[0] == "user"
    assert roles.count("assistant") == 2
    assert roles.count("tool") == 1
    # The tool message references the call id we emitted.
    assert persisted[2]["tool_call_id"] == "c1"
    assert persisted[2]["name"] == "echo"


@pytest.mark.asyncio
async def test_loop_continues_after_tool_failure() -> None:
    """A single tool failure must not abort the loop — the error
    is fed back to the LLM so it can decide what to do next."""
    tool = CountingTool()
    tool.fail_mode = "raise"
    call = _tool_call("echo", {"text": "x"}, call_id="c1")
    fake = FakeLLM(
        [
            _tool_response(call),
            # Second LLM call: LLM acknowledges the failure and
            # produces a final answer.
            _text_response("Tool failed; moving on."),
        ]
    )
    core = AgentCore(llm=fake, registry=_fresh_registry(tool))
    result = await core.run(session_id="s1", user_message="go")
    assert result.final_text == "Tool failed; moving on."
    assert result.iterations == 2


@pytest.mark.asyncio
async def test_loop_cancellation_stops_iteration() -> None:
    """Setting the cancel flag mid-run aborts the loop after the
    current step. The LLM's first call runs to completion; we
    cancel right after the first response comes back, so the
    loop exits without calling the tool a second time."""
    cancelled = asyncio.Event()

    async def hook_on_status(status: str, detail: dict[str, Any]) -> None:
        if status == "calling_tool":
            core.cancel()
            cancelled.set()

    tool = CountingTool()
    call = _tool_call("echo", {"text": "x"}, call_id="c1")
    fake = FakeLLM([_tool_response(call), _text_response("never seen")])
    core = AgentCore(llm=fake, registry=_fresh_registry(tool))
    core.on_status = hook_on_status
    result = await core.run(session_id="s1", user_message="go")
    assert result.cancelled is True
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_loop_history_provider_loads_existing_messages() -> None:
    history = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "earlier reply"},
    ]

    async def load_history(session_id: str) -> list[dict[str, Any]]:
        assert session_id == "ses_xyz"
        return list(history)

    fake = FakeLLM([_text_response("I see your history.")])
    core = AgentCore(llm=fake, registry=_fresh_registry(CountingTool()), history_provider=load_history)
    await core.run(session_id="ses_xyz", user_message="now")
    # The LLM saw the system prompt + history + new user msg.
    sent = fake.messages[0]
    assert sent[0]["role"] == "system"
    assert any(m["content"] == "earlier" for m in sent)
    assert sent[-1] == {"role": "user", "content": "now"}


@pytest.mark.asyncio
async def test_loop_truncates_at_max_iterations() -> None:
    """A misbehaving model that keeps calling tools should be
    stopped at the max_iterations ceiling."""
    tool = CountingTool()
    # Always call the tool — never produce a final answer.
    infinite = [_tool_response(_tool_call("echo", {"text": f"i{i}"}, call_id=f"c{i}")) for i in range(20)]
    fake = FakeLLM(infinite)
    chunks: list[tuple[str, bool, dict[str, Any] | None]] = []
    persisted: list[dict[str, Any]] = []

    async def persist(_session_id: str, message: dict[str, Any]) -> None:
        persisted.append(message)

    core = AgentCore(
        llm=fake,
        registry=_fresh_registry(tool),
        config=AgentConfig(max_iterations=3),
        persist_message=persist,
    )
    core.on_chunk = lambda d, done, metadata=None: _maybe_coro(chunks.append((d, done, metadata)))
    result = await core.run(session_id="s1", user_message="loop forever")
    assert result.truncated is True
    assert result.iterations == 3
    assert result.final_text == "I stopped after reaching the 3-iteration limit before producing a final answer."
    final_delta, final_done, final_metadata = chunks[-1]
    assert final_delta == result.final_text
    assert final_done is True
    assert final_metadata is not None
    assert final_metadata["truncated"] is True
    assert persisted[-1]["role"] == "assistant"
    assert persisted[-1]["content"] == result.final_text


@pytest.mark.asyncio
async def test_stream_stall_is_retryable_failure_not_completed_message() -> None:
    """A stalled LLM stream must fail the run instead of looking complete."""
    chunks: list[tuple[str, bool, dict[str, Any] | None]] = []
    persisted: list[dict[str, Any]] = []
    statuses: list[tuple[str, dict[str, Any]]] = []

    async def persist(_session_id: str, message: dict[str, Any]) -> None:
        persisted.append(message)

    core = AgentCore(
        llm=StallingLLM(),
        registry=_fresh_registry(CountingTool()),
        config=AgentConfig(stall_timeout=0.01),
        persist_message=persist,
    )
    core.on_chunk = lambda d, done, metadata=None: _maybe_coro(chunks.append((d, done, metadata)))
    core.on_status = lambda status, detail: _maybe_coro(statuses.append((status, detail)))

    with pytest.raises(LLMError, match="can be retried"):
        await core.run(session_id="s1", user_message="please continue")

    assert chunks[0][0] == "partial answer"
    assert chunks[-1][0].startswith("\n\nLLM stream stopped responding")
    assert chunks[-1][1] is False
    assert not any(done for _, done, _ in chunks)
    assert statuses[-1][0] == "error"
    assert "timed out" in statuses[-1][1]["detail"]
    assert [message["role"] for message in persisted] == ["user"]


@pytest.mark.asyncio
async def test_tool_result_is_emitted_before_followup_llm_stall_fails_run() -> None:
    """A post-tool LLM stall must not hide or roll back the tool result."""

    class ToolThenStallLLM:
        def __init__(self) -> None:
            self.calls = 0
            self.thinking_count = 0

        async def stream_chat(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamChunk]:
            self.calls += 1
            if self.calls == 1:
                for chunk in _tool_response(
                    _tool_call("echo", {"text": "finished"}, call_id="call-before-stall")
                ):
                    yield chunk
                return
            await asyncio.sleep(1)
            if False:  # pragma: no cover - keeps this an async generator
                yield StreamChunk()

    tool = CountingTool()
    tool_results: list[tuple[dict[str, Any], ToolResult]] = []
    core = AgentCore(
        llm=ToolThenStallLLM(),  # type: ignore[arg-type]
        registry=_fresh_registry(tool),
        config=AgentConfig(stall_timeout=0.01),
    )
    core.on_tool_result = lambda call, result: _maybe_coro(tool_results.append((call, result)))

    with pytest.raises(LLMError, match="can be retried"):
        await core.run(session_id="s1", user_message="use the tool")

    assert tool.calls == [{"text": "finished"}]
    assert len(tool_results) == 1
    assert tool_results[0][0]["id"] == "call-before-stall"
    assert tool_results[0][1].success is True


@pytest.mark.asyncio
async def test_loop_passes_tools_to_llm() -> None:
    fake = FakeLLM([_text_response("ok")])
    reg = _fresh_registry(CountingTool())
    core = AgentCore(llm=fake, registry=reg)
    await core.run(session_id="s1", user_message="hi")
    assert fake.tools_payloads[0] is not None
    names = [t["function"]["name"] for t in fake.tools_payloads[0]]
    assert "echo" in names


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _maybe_coro(coro):
    """Helper that schedules a coroutine on the running loop and
    returns the awaitable. Lets us pass simple sync-looking
    callbacks to ``on_*`` without the tests having to know
    whether the API is async."""
    if asyncio.iscoroutine(coro):
        return coro
    async def _wrap():
        return coro
    return _wrap()
