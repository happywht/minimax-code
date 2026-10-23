"""M2 state-machine tests — ``awaiting_approval`` / ``waiting_input`` on the run row.

The durable run row must reflect *why* a run is idle: a consent modal
pauses the run as ``awaiting_approval``, an inline ask_user card as
``waiting_input``, and both return to ``running`` when the human
answers. Uses the real ``AgentCore`` loop (scripted LLM + real
``PermissionGater`` / ask_user wait) with a recording state hook —
the same hook ``builtins`` wires to the runs DAO in production.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from minimax_code.agent.core import AgentConfig, AgentCore
from minimax_code.agent.tools import ToolRegistry
from minimax_code.agent.tools.ask_user import AskUserTool
from minimax_code.agent.tools.base import Tool, ToolResult
from minimax_code.agent.types import StreamChunk
from minimax_code.perm_consent import PermissionGater


class _EchoTool(Tool):
    name = "echo"
    description = "echoes input"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult.ok(output={"echoed": kwargs.get("text")})


def _tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


class ScriptedLLM:
    """Queued StreamChunk responses (no usage — budget tests cover that)."""

    def __init__(self, responses: list[list[StreamChunk]]) -> None:
        self._queue = list(responses)
        self.call_count = 0
        self.mock = True

    async def stream_chat(self, messages: Any, **_: Any):  # noqa: ANN401
        self.call_count += 1
        chunks = self._queue.pop(0) if self._queue else [
            StreamChunk(delta="done", finish_reason="stop")]
        for chunk in chunks:
            yield chunk


class StateRecorder:
    """Collects run-state transitions; optionally blocks mid-transition."""

    def __init__(self) -> None:
        self.states: list[str] = []
        self.saw_awaiting = asyncio.Event()

    async def __call__(self, state: str) -> None:
        self.states.append(state)
        if state == "awaiting_approval":
            self.saw_awaiting.set()


def _core(llm: ScriptedLLM, *, store: Any = None, gater: Any = None,
          registry: ToolRegistry | None = None) -> AgentCore:
    core = AgentCore(
        llm=llm,
        registry=registry or ToolRegistry(),
        config=AgentConfig(max_output_tokens=100),
        permission_store=store,
        permission_gater=gater,
    )
    return core


async def _noop_emit(event: str, data: Any) -> None:
    return None


async def _resolve_once_pending(gater: PermissionGater, allowed: bool) -> None:
    """Resolve the gater's first pending consent request when it appears."""
    for _ in range(400):
        pending = gater.pending_ids()
        if pending:
            gater.resolve(pending[0], allowed)
            return
        await asyncio.sleep(0.005)
    raise AssertionError("consent request never became pending")


async def test_consent_wait_marks_run_awaiting_approval_then_running() -> None:
    llm = ScriptedLLM([
        [StreamChunk(delta="", tool_call_deltas=[_tool_call("echo", {"text": "hi"})],
                     finish_reason="tool_calls")],
        [StreamChunk(delta="finished", finish_reason="stop")],
    ])
    gater = PermissionGater(emit=_noop_emit)
    store = type("S", (), {})()
    store.lookup = lambda name: {"action": "ask"}  # type: ignore[attr-defined]
    registry = ToolRegistry()
    registry.register(_EchoTool())
    core = _core(llm, store=store, gater=gater, registry=registry)
    recorder = StateRecorder()
    core.run_state_hook = recorder

    run_task = asyncio.create_task(core.run(session_id="s", user_message="go"))
    await asyncio.wait_for(recorder.saw_awaiting.wait(), 3)
    assert recorder.states == ["awaiting_approval"]
    await _resolve_once_pending(gater, allowed=True)
    result = await asyncio.wait_for(run_task, 3)
    assert result.budget_exhausted is False
    assert recorder.states == ["awaiting_approval", "running"]


async def test_consent_denial_still_returns_to_running() -> None:
    llm = ScriptedLLM([
        [StreamChunk(delta="", tool_call_deltas=[_tool_call("echo", {"text": "hi"})],
                     finish_reason="tool_calls")],
        [StreamChunk(delta="skipped the tool", finish_reason="stop")],
    ])
    gater = PermissionGater(emit=_noop_emit)
    store = type("S", (), {})()
    store.lookup = lambda name: {"action": "ask"}  # type: ignore[attr-defined]
    registry = ToolRegistry()
    registry.register(_EchoTool())
    core = _core(llm, store=store, gater=gater, registry=registry)
    recorder = StateRecorder()
    core.run_state_hook = recorder

    run_task = asyncio.create_task(core.run(session_id="s", user_message="go"))
    await asyncio.wait_for(recorder.saw_awaiting.wait(), 3)
    await _resolve_once_pending(gater, allowed=False)
    await asyncio.wait_for(run_task, 3)
    assert recorder.states == ["awaiting_approval", "running"]


async def test_ask_user_wait_marks_run_waiting_input_then_running() -> None:
    llm = ScriptedLLM([
        [StreamChunk(delta="", tool_call_deltas=[_tool_call(
            "ask_user", {"questions": [{"question": "Which one?",
                                         "header": "Pick",
                                         "options": [{"label": "A"}, {"label": "B"}],
                                         "multiSelect": False}]})],
                     finish_reason="tool_calls")],
        [StreamChunk(delta="answered", finish_reason="stop")],
    ])
    registry = ToolRegistry()
    registry.register(AskUserTool())
    core = _core(llm, registry=registry)
    recorder = StateRecorder()
    core.run_state_hook = recorder
    answered = asyncio.Event()

    async def on_ask_user(payload: dict[str, Any]) -> None:
        async def resolve() -> None:
            await asyncio.sleep(0.02)
            assert core.resolve_ask_user(payload["request_id"], [{"label": "A"}])
            answered.set()
        asyncio.create_task(resolve())

    core.on_ask_user = on_ask_user
    run_task = asyncio.create_task(core.run(session_id="s", user_message="go"))
    await asyncio.wait_for(answered.wait(), 3)
    await asyncio.wait_for(run_task, 3)
    assert recorder.states == ["waiting_input", "running"]
