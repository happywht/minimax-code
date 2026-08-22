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
        reasoning_effort: Any = None,  # R55: mirror MiniMaxClient.stream_chat kwarg
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
        reasoning_effort: Any = None,  # R55: mirror MiniMaxClient.stream_chat kwarg
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


class LongResultTool(Tool):
    """Echo tool whose result payload is long enough (~400 estimated
    tokens) that a few tool turns push the in-flight message list over
    the compaction budget — the intra-loop tests need real bulk."""

    name = "echo"
    description = "echoes a long payload back"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult.ok(output={"echoed": "x" * 1600})


def _tool_response_with_usage(
    call: dict[str, Any], prompt_tokens: int
) -> list[StreamChunk]:
    """Tool-call chunk list whose usage reports a chosen prompt size."""
    return [
        StreamChunk(delta="working…"),
        StreamChunk(
            tool_call_deltas=[call],
            finish_reason="tool_calls",
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 4,
                "total_tokens": prompt_tokens + 4,
            },
        ),
    ]


def _compaction_history() -> list[dict[str, Any]]:
    """8 old user/assistant turns (~320 estimated tokens — under the
    500-token pre-turn threshold, so only the intra-loop path can act)."""
    hist: list[dict[str, Any]] = []
    for i in range(8):
        hist.append({"role": "user", "content": f"history question {i} " + "h" * 60})
        hist.append({"role": "assistant", "content": f"history answer {i} " + "a" * 60})
    return hist


@pytest.mark.asyncio
async def test_loop_compacts_history_intra_loop() -> None:
    """v1.1.0 intra-loop compaction: when the previous iteration's real
    prompt usage crosses the threshold, the in-flight message list is
    compacted before the next LLM call — and the loop itself is not
    interrupted (the turn still reaches a final answer).

    Setup: window=1000 / threshold=0.5 ⇒ decision threshold 500. The
    seeded history estimates ~320 tokens (pre-turn gate stays closed),
    each tool result adds ~400, and the LLM reports prompt_tokens=800 —
    over the threshold from iteration 3 on (min_steps_before_compact=3
    holds iterations 0-2 back).
    """
    async def load_history(_sid: str) -> list[dict[str, Any]]:
        return _compaction_history()

    responses = [
        _tool_response_with_usage(_tool_call("echo", {"text": f"i{i}"}, call_id=f"c{i}"), 800)
        for i in range(4)
    ] + [_text_response("Done after compaction.")]
    fake = FakeLLM(responses)

    core = AgentCore(
        llm=fake,
        registry=_fresh_registry(LongResultTool()),
        config=AgentConfig(
            max_iterations=8,
            context_window=1000,
            compaction_threshold=0.5,
        ),
        history_provider=load_history,
    )
    result = await core.run(session_id="s_compact", user_message="loop with bulk")
    assert result.compactions >= 1
    assert result.truncated is False
    assert result.final_text == "Done after compaction."
    # The 4th LLM call (iteration 3) saw fewer messages than the 3rd —
    # without compaction each tool turn grows the list by 2.
    assert len(fake.messages[3]) < len(fake.messages[2])
    # The injected summary line sits right after the system prompt.
    assert fake.messages[3][1]["role"] == "user"
    assert fake.messages[3][1]["content"].startswith("[Conversation summary (compacted)]")


@pytest.mark.asyncio
async def test_loop_no_compaction_below_threshold() -> None:
    """Real usage under the threshold ⇒ the list grows normally and
    compactions stays 0."""
    async def load_history(_sid: str) -> list[dict[str, Any]]:
        return _compaction_history()

    responses = [
        _tool_response_with_usage(_tool_call("echo", {"text": f"i{i}"}, call_id=f"c{i}"), 100)
        for i in range(4)
    ] + [_text_response("Done without compaction.")]
    fake = FakeLLM(responses)

    core = AgentCore(
        llm=fake,
        registry=_fresh_registry(LongResultTool()),
        config=AgentConfig(
            # 8 iterations: iteration 4 (the final answer) still has 3
            # remaining, so the convergence nudge never fires here and
            # this test stays purely about the compaction gate.
            max_iterations=8,
            context_window=1000,
            compaction_threshold=0.5,
        ),
        history_provider=load_history,
    )
    result = await core.run(session_id="s_nocompact", user_message="loop small usage")
    assert result.compactions == 0
    # Normal growth: one assistant + one tool message per iteration.
    assert len(fake.messages[3]) == len(fake.messages[2]) + 2


@pytest.mark.asyncio
async def test_loop_no_compaction_without_context_window() -> None:
    """No context window configured (the pre-v1.1.0 default) ⇒ the
    gate stays closed no matter how large the reported usage is."""
    async def load_history(_sid: str) -> list[dict[str, Any]]:
        return _compaction_history()

    responses = [
        _tool_response_with_usage(_tool_call("echo", {"text": f"i{i}"}, call_id=f"c{i}"), 999_999)
        for i in range(4)
    ] + [_text_response("Done, gate closed.")]
    fake = FakeLLM(responses)

    core = AgentCore(
        llm=fake,
        registry=_fresh_registry(LongResultTool()),
        config=AgentConfig(
            max_iterations=8,
            compaction_threshold=0.8,  # window is None → gate closed
        ),
        history_provider=load_history,
    )
    result = await core.run(session_id="s_nowindow", user_message="loop no window")
    assert result.compactions == 0
    assert len(fake.messages[3]) == len(fake.messages[2]) + 2


@pytest.mark.asyncio
async def test_loop_injects_handoff_nudge_near_budget() -> None:
    """v1.1.1 handoff nudge (iteration safety valve): with ≤2 iterations
    remaining after the current one, an ephemeral user note is appended
    to the LLM payload. The note must (a) appear in time, (b) never
    leak into the in-flight ``messages`` growth, (c) never be persisted,
    and (d) never instruct the model to fake a final answer — the old
    "produce a final answer now" wording ended runs with truncated=False
    and silently killed auto-continue."""
    responses = [
        _tool_response(_tool_call("echo", {"text": f"i{i}"}, call_id=f"c{i}"))
        for i in range(3)
    ] + [_text_response("Honest status report.")]
    fake = FakeLLM(responses)
    persisted: list[dict[str, Any]] = []

    async def persist(_session_id: str, message: dict[str, Any]) -> None:
        persisted.append(message)

    core = AgentCore(
        llm=fake,
        registry=_fresh_registry(CountingTool()),
        config=AgentConfig(max_iterations=4),
        persist_message=persist,
    )
    result = await core.run(session_id="s_nudge", user_message="run to the budget")
    assert result.final_text == "Honest status report."
    assert result.truncated is False

    # (a) Iteration 0 had 3 iterations remaining → no nudge; iteration 1
    # had 2 → nudge appended as the last message of the payload.
    assert "iteration budget" not in str(fake.messages[0][-1])
    last = fake.messages[1][-1]
    assert last["role"] == "user"
    assert "iteration budget is almost exhausted" in last["content"]
    assert "2 iteration(s)" in last["content"]
    # (d) Manual-continuation mode: honest handoff directive, never a
    # lure to fabricate completion.
    assert "NO automatic continuation" in last["content"]
    assert "Never present unfinished work as complete" in last["content"]
    assert "final answer now" not in last["content"]
    # The nudge rides on top of the real history — the message right
    # before it is the tool result from iteration 0.
    assert fake.messages[1][-2]["role"] == "tool"

    # (b) The nudge is ephemeral: iteration 2's payload still starts with
    # the real history (no nudge accumulated from the previous call).
    assert "iteration budget" not in str(fake.messages[2][0])
    assert sum(
        "iteration budget" in str(m) for m in fake.messages[2]
    ) == 1  # only its own nudge

    # (c) Nothing nudge-flavoured was persisted.
    assert not any("[system note]" in str(m) for m in persisted)


@pytest.mark.asyncio
async def test_loop_nudge_auto_continue_directive_keeps_model_working() -> None:
    """v1.1.1: with auto_continue on, the nudge must tell the model to
    KEEP WORKING (the runtime splits blocks automatically) instead of
    wrapping up — this is the exact regression that made auto-continue
    never fire in v1.1.0."""
    responses = [
        _tool_response(_tool_call("echo", {"text": "i0"}, call_id="c0")),
        _tool_response(_tool_call("echo", {"text": "i1"}, call_id="c1")),
        _text_response("Real final after the budget."),
    ]
    fake = FakeLLM(responses)

    core = AgentCore(
        llm=fake,
        registry=_fresh_registry(CountingTool()),
        config=AgentConfig(max_iterations=4, auto_continue=True),
    )
    await core.run(session_id="s_nudge_ac", user_message="run to the budget")

    nudge = str(fake.messages[1][-1])
    assert "Keep working until the task is truly done" in nudge
    assert "automatically continues with a fresh block" in nudge
    assert "do NOT wrap up early" in nudge
    assert "NO automatic continuation" not in nudge  # that's the other mode


@pytest.mark.asyncio
async def test_loop_nudge_fires_on_context_pressure() -> None:
    """v1.1.1: the nudge also fires on context pressure — reported prompt
    usage ≥90% of the configured window — even when plenty of iterations
    remain."""
    # Window 1000 → 90% threshold = 900. Iteration 0 reports 950 usage →
    # nudge rides iteration 1's payload even though 4 iterations remain.
    responses = [
        _tool_response_with_usage(_tool_call("echo", {"text": "i0"}, call_id="c0"), 950),
        _tool_response(_tool_call("echo", {"text": "i1"}, call_id="c1")),
        _text_response("Done despite pressure."),
    ]
    fake = FakeLLM(responses)

    core = AgentCore(
        llm=fake,
        registry=_fresh_registry(CountingTool()),
        config=AgentConfig(max_iterations=8, context_window=1000),
    )
    await core.run(session_id="s_nudge_ctx", user_message="fill the window")

    assert "context window" not in str(fake.messages[0][-1])  # 950 not yet reported
    nudge = str(fake.messages[1][-1])
    assert "context window is nearly full" in nudge


def test_default_max_iterations_env_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    """v1.1.1: the iteration safety valve defaults to 200 and honours the
    MINIMAX_MAX_ITERATIONS env var (clamped to [1, 10_000]; garbage
    falls back to the default)."""
    from minimax_code.agent.core import (
        _DEFAULT_MAX_ITERATIONS,
        _default_max_iterations,
    )

    assert _DEFAULT_MAX_ITERATIONS == 200

    monkeypatch.delenv("MINIMAX_MAX_ITERATIONS", raising=False)
    assert _default_max_iterations() == 200
    assert AgentConfig().max_iterations == 200

    monkeypatch.setenv("MINIMAX_MAX_ITERATIONS", "64")
    assert _default_max_iterations() == 64

    monkeypatch.setenv("MINIMAX_MAX_ITERATIONS", "0")  # clamped up
    assert _default_max_iterations() == 1

    monkeypatch.setenv("MINIMAX_MAX_ITERATIONS", "999999")  # clamped down
    assert _default_max_iterations() == 10_000

    monkeypatch.setenv("MINIMAX_MAX_ITERATIONS", "twelve")  # garbage → default
    assert _default_max_iterations() == 200


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
