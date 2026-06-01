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
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, json={"error": "unavailable"})
        # On the third attempt, return a valid streaming body.
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                _text_response_body("hi from retry"),
            ),
        )

    async def run() -> None:
        client = MiniMaxClient(
            api_key="test",
            base_url="https://example.invalid",
            max_retries=3,
            client=httpx.AsyncClient(transport=_make_transport(handler)),
        )
        resp = await client.chat([{"role": "user", "content": "hi"}])
        assert resp.message["content"] == "hi from retry"

    asyncio.run(run())
    assert len(attempts) == 3


def test_4xx_is_fatal_no_retry() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(401, json={"error": "unauthorized"})

    async def run() -> None:
        client = MiniMaxClient(
            api_key="bad",
            base_url="https://example.invalid",
            max_retries=5,
            client=httpx.AsyncClient(transport=_make_transport(handler)),
        )
        with pytest.raises(LLMError) as excinfo:
            await client.chat([{"role": "user", "content": "hi"}])
        assert "401" in str(excinfo.value)

    asyncio.run(run())
    assert len(attempts) == 1


def test_tool_call_streaming_assembly() -> None:
    """Chunks of a single tool_call should be merged into one entry."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","type":"function","function":{"name":"","arguments":""}}]}}]}\n\n'
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"echo","arguments":"{\\"text\\":"}}]}}]}\n\n'
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"hi\\"}"}}]}}]}\n\n'
            'data: {"choices":[{"finish_reason":"tool_calls","delta":{}}]}\n\n'
            'data: [DONE]\n\n'
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async def run() -> LLMResponse:
        client = MiniMaxClient(
            api_key="x",
            base_url="https://example.invalid",
            client=httpx.AsyncClient(transport=_make_transport(handler)),
        )
        return await client.chat([{"role": "user", "content": "go"}])

    resp = asyncio.run(run())
    assert resp.finish_reason == "tool_calls"
    assert resp.message.get("tool_calls"), resp.message
    tc = resp.message["tool_calls"][0]
    assert tc["function"]["name"] == "echo"
    assert json.loads(tc["function"]["arguments"]) == {"text": "hi"}


# ---------------------------------------------------------------------------
# SSE helpers for transport testing
# ---------------------------------------------------------------------------


def _sse(*bodies: str) -> str:
    """Build a minimal SSE response body."""
    out: list[str] = []
    for b in bodies:
        out.append(b)
        if not b.endswith("\n\n"):
            out[-1] = b.rstrip() + "\n\n"
    out.append("data: [DONE]\n\n")
    return "".join(out)


def _text_response_body(text: str) -> str:
    return (
        f'data: {{"choices":[{{"delta":{{"content":"{text}"}}}}]}}\n\n'
        'data: {"choices":[{"finish_reason":"stop","delta":{}}],'
        '"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n'
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
    core.on_chunk = lambda d, done: _maybe_coro(chunks.append(d))
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
    core = AgentCore(
        llm=fake,
        registry=_fresh_registry(tool),
        config=AgentConfig(max_iterations=3),
    )
    result = await core.run(session_id="s1", user_message="loop forever")
    assert result.truncated is True
    assert result.iterations == 3


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
