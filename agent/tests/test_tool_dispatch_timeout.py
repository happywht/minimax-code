"""v1.4.0 — per-tool ``dispatch_timeout`` exemption (layer-1 fix).

The bug: ``spawn_subagent`` embeds a whole sub-agent loop (LLM turns +
tools) inside one tool call, but ``_execute_tool_call`` wrapped every
dispatch in ``wait_for(timeout=AgentConfig.tool_timeout)`` — a hard
120 s ceiling. Anything longer was killed with a misleading
"exceeded 120s timeout" error.

The fix: a tool may declare ``dispatch_timeout`` (class or instance
attribute). The loop reads it before ``wait_for`` and uses it instead
of the generic ceiling; ``<= 0`` disables the timeout entirely
(``wait_for(timeout=None)``).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from typing import Any

import pytest

from minimax_code.agent.core import AgentConfig, AgentCore
from minimax_code.agent.llm import StreamChunk
from minimax_code.agent.tools import Tool, ToolRegistry, ToolResult
from minimax_code.orchestrator.subagent import (
    SubAgentConfig,
    SubAgentRuntime,
    subagent_wall_clock_s,
)

# ---------------------------------------------------------------------------
# Fakes (local copies of the test_agent_core.py patterns)
# ---------------------------------------------------------------------------


class FakeLLM:
    """Canned-chunk LLM; each call advances the response queue."""

    def __init__(self, responses: Iterable[list[StreamChunk]] | None = None) -> None:
        self._queue: list[list[StreamChunk]] = list(responses or [])

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float | None = None,
        reasoning_effort: Any = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        if self._queue:
            chunks = self._queue.pop(0)
        else:
            chunks = [StreamChunk(delta="done", finish_reason="stop")]
        for c in chunks:
            yield c


class SlowTool(Tool):
    """Sleeps past the generic ceiling; exemption set per-test."""

    name = "slow_tool"
    description = "sleeps then answers"
    parameters = {
        "type": "object",
        "properties": {"seconds": {"type": "number", "default": 0.3}},
        "required": [],
        "additionalProperties": False,
    }

    async def run(self, seconds: float = 0.3, **_: Any) -> ToolResult:
        await asyncio.sleep(seconds)
        return ToolResult.ok(output={"slept": seconds})


def _tool_call(name: str, args: Any, call_id: str = "call_1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _text_response(text: str) -> list[StreamChunk]:
    return [
        StreamChunk(delta=text),
        StreamChunk(
            finish_reason="stop",
            usage={"prompt_tokens": 3, "completion_tokens": len(text), "total_tokens": 3 + len(text)},
        ),
    ]


def _tool_response(call: dict[str, Any]) -> list[StreamChunk]:
    return [
        StreamChunk(delta="thinking… "),
        StreamChunk(tool_call_deltas=[call], finish_reason="tool_calls"),
    ]


def _fresh_registry(tool: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool)
    return reg


# ---------------------------------------------------------------------------
# Exemption semantics in the dispatch loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exempt_tool_runs_past_generic_ceiling():
    """A declared ``dispatch_timeout`` outranks the short config ceiling."""
    tool = SlowTool()
    tool.dispatch_timeout = 5.0  # instance-level exemption
    call = _tool_call("slow_tool", {"seconds": 0.3})
    fake = FakeLLM([_tool_response(call), _text_response("after slow tool")])
    core = AgentCore(
        llm=fake,
        registry=_fresh_registry(tool),
        config=AgentConfig(tool_timeout=0.1),  # generic ceiling would kill it
    )
    result = await core.run(session_id="s_timeout", user_message="go")
    # The tool completed: the loop reached the final text response.
    assert result.final_text == "after slow tool"
    assert result.iterations == 2


@pytest.mark.asyncio
async def test_unexempt_tool_hits_generic_ceiling():
    """Without an exemption the generic ``tool_timeout`` still applies."""
    tool = SlowTool()  # dispatch_timeout stays None (class default)
    call = _tool_call("slow_tool", {"seconds": 5})
    fake = FakeLLM([_tool_response(call), _text_response("never reached")])
    core = AgentCore(
        llm=fake,
        registry=_fresh_registry(tool),
        config=AgentConfig(tool_timeout=0.1),
    )
    result = await core.run(session_id="s_timeout2", user_message="go")
    # The tool failed with the timeout error; the LLM saw it and answered.
    assert result.final_text == "never reached"
    tool_result_payload = result.tool_results[0]
    assert tool_result_payload.success is False
    assert "exceeded 0s timeout" in (tool_result_payload.error or "")


@pytest.mark.asyncio
async def test_nonpositive_exemption_disables_timeout():
    """``dispatch_timeout <= 0`` means no dispatch timeout at all."""
    tool = SlowTool()
    tool.dispatch_timeout = 0  # disabled — wait_for(timeout=None)
    call = _tool_call("slow_tool", {"seconds": 0.3})
    fake = FakeLLM([_tool_response(call), _text_response("no ceiling")])
    core = AgentCore(
        llm=fake,
        registry=_fresh_registry(tool),
        config=AgentConfig(tool_timeout=0.1),
    )
    result = await core.run(session_id="s_timeout3", user_message="go")
    assert result.final_text == "no ceiling"


# ---------------------------------------------------------------------------
# Wall-clock knob
# ---------------------------------------------------------------------------


def test_wall_clock_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MINIMAX_CODE_SUBAGENT_TIMEOUT_S", raising=False)
    assert subagent_wall_clock_s() == 600.0


def test_wall_clock_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MINIMAX_CODE_SUBAGENT_TIMEOUT_S", "30")
    assert subagent_wall_clock_s() == 30.0


def test_wall_clock_env_disable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MINIMAX_CODE_SUBAGENT_TIMEOUT_S", "0")
    assert subagent_wall_clock_s() == 0.0


def test_wall_clock_env_garbage(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MINIMAX_CODE_SUBAGENT_TIMEOUT_S", "not-a-number")
    assert subagent_wall_clock_s() == 600.0


def test_tool_base_defaults_to_no_exemption():
    """The class-level default is ``None`` — no behavioural change for
    every tool that doesn't opt in."""
    assert Tool.dispatch_timeout is None
    assert SlowTool.dispatch_timeout is None


# ---------------------------------------------------------------------------
# Envelope bubbling (usage / cancelled / truncated)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stub_envelope_carries_defaults():
    runtime = SubAgentRuntime()  # llm=None → stub path
    handle = runtime.build(SubAgentConfig(name="echoer"))
    envelope = await runtime.invoke(handle, session_id="s_stub", request="hi")
    assert envelope["stub"] is True
    assert envelope["usage"] == {}
    assert envelope["cancelled"] is False
    assert envelope["truncated"] is False


@pytest.mark.asyncio
async def test_real_envelope_bubbles_run_facts():
    runtime = SubAgentRuntime(llm=FakeLLM([_text_response("real answer")]))
    handle = runtime.build(SubAgentConfig(name="echoer"))
    envelope = await runtime.invoke(handle, session_id="s_real", request="hi")
    assert envelope["stub"] is False
    assert envelope["usage"].get("total_tokens") == len("real answer") + 3
    assert envelope["cancelled"] is False
    assert envelope["truncated"] is False
