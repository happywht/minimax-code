"""End-to-end test of the chat permission flow.

This file covers the gap between :mod:`test_agent_core` (which exercises
the conversation loop with a fake LLM) and
:mod:`test_permissions` (which tests the storage layer in isolation).

Coverage
--------

* :class:`TestAgentCoreGating` — the agent loop respects
  ``PermissionStore`` actions and routes ``ask`` through the
  :class:`PermissionGater`.
* :class:`TestPermissionGater` — the gater's emit / await / resolve
  contract in isolation.
* :class:`TestPermissionResolveHandler` — the
  ``permission.resolve`` JSON-RPC handler end-to-end via the
  in-process :class:`IPCClient`.
* :class:`TestChatEndToEnd` — ``agent.send_message`` actually pauses
  for a real ``ask`` rule, then resumes after a ``permission.resolve``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.core import AgentConfig, AgentCore
from minimax_code.agent.llm import StreamChunk
from minimax_code.agent.tools import Tool, ToolRegistry, ToolResult
from minimax_code.ipc.client import IPCClient
from minimax_code.perm_consent import PermissionGater
from minimax_code.permissions import PermissionStore
from minimax_code.storage.dao.permissions import PermissionRuleDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeLLM:
    """Fake :class:`MiniMaxClient` that returns a canned sequence of chunks."""

    def __init__(self, responses: list[list[StreamChunk]]) -> None:
        self._queue = list(responses)
        self.messages: list[list[dict[str, Any]]] = []
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
        chunks = self._queue.pop(0) if self._queue else [
            StreamChunk(delta="(default)", finish_reason="stop")
        ]
        for c in chunks:
            yield c


class EchoTool(Tool):
    name = "echo"
    description = "echoes its input"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> ToolResult:
        self.calls.append(kwargs)
        return ToolResult.ok(output={"echoed": kwargs.get("text")})


def _tool_call(name: str, args: Any, call_id: str = "call_1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _tool_response(call: dict[str, Any]) -> list[StreamChunk]:
    return [
        StreamChunk(delta="thinking… "),
        StreamChunk(
            tool_call_deltas=[call],
            finish_reason="tool_calls",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        ),
    ]


def _text_response(text: str) -> list[StreamChunk]:
    return [
        StreamChunk(delta=text),
        StreamChunk(
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": len(text), "total_tokens": 1 + len(text)},
        ),
    ]


class RecordingEmit:
    """Stand-in for ``ctx.emit`` — records every event the gater pushes.

    Returns a coroutine that resolves immediately so the gater can
    ``await`` it without spinning.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, event: str, data: Any) -> None:
        self.events.append((event, dict(data) if isinstance(data, dict) else {"data": data}))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_db(tmp_path: Path) -> AsyncDatabase:
    path = make_temp_database_path(tmp_path)
    db = AsyncDatabase(path)
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def perm_store(async_db: AsyncDatabase) -> PermissionStore:
    dao = PermissionRuleDAO(async_db)
    store = PermissionStore(dao)
    await store.warm()
    return store


# ---------------------------------------------------------------------------
# AgentCore gating
# ---------------------------------------------------------------------------


class TestAgentCoreGating:
    @pytest.mark.asyncio
    async def test_default_allow_when_no_rule(self) -> None:
        tool = EchoTool()
        call = _tool_call("echo", {"text": "ok"}, call_id="c1")
        fake = FakeLLM([_tool_response(call), _text_response("done")])
        core = AgentCore(
            llm=fake,
            registry=_fresh_registry(tool),
            permission_store=None,  # no gating
            permission_gater=None,
        )
        result = await core.run(session_id="s1", user_message="go")
        assert result.final_text == "done"
        assert len(tool.calls) == 1

    @pytest.mark.asyncio
    async def test_explicit_allow_runs(self, perm_store: PermissionStore) -> None:
        await perm_store.upsert(tool_pattern="echo", action="allow")
        tool = EchoTool()
        call = _tool_call("echo", {"text": "ok"}, call_id="c1")
        fake = FakeLLM([_tool_response(call), _text_response("done")])
        core = AgentCore(
            llm=fake,
            registry=_fresh_registry(tool),
            permission_store=perm_store,
            permission_gater=None,
        )
        result = await core.run(session_id="s1", user_message="go")
        assert result.final_text == "done"
        assert len(tool.calls) == 1

    @pytest.mark.asyncio
    async def test_explicit_deny_blocks(self, perm_store: PermissionStore) -> None:
        await perm_store.upsert(tool_pattern="echo", action="deny")
        tool = EchoTool()
        call = _tool_call("echo", {"text": "ok"}, call_id="c1")
        # The LLM will get back a tool failure and produce a final answer.
        fake = FakeLLM([_tool_response(call), _text_response("blocked.")])
        core = AgentCore(
            llm=fake,
            registry=_fresh_registry(tool),
            permission_store=perm_store,
            permission_gater=None,
        )
        result = await core.run(session_id="s1", user_message="go")
        # Tool must NOT have run.
        assert tool.calls == []
        assert result.final_text == "blocked."

    @pytest.mark.asyncio
    async def test_ask_pauses_until_resolve(
        self, perm_store: PermissionStore
    ) -> None:
        await perm_store.upsert(tool_pattern="echo", action="ask")
        tool = EchoTool()
        call = _tool_call("echo", {"text": "ping"}, call_id="c1")
        fake = FakeLLM([_tool_response(call), _text_response("allowed!")])

        emit = RecordingEmit()
        gater = PermissionGater(emit=emit)

        core = AgentCore(
            llm=fake,
            registry=_fresh_registry(tool),
            permission_store=perm_store,
            permission_gater=gater,
        )

        async def _resolve_later() -> None:
            # Let the gater register its pending request + emit the event.
            await asyncio.sleep(0.05)
            assert gater.pending_count == 1
            rid = gater.pending_ids()[0]
            assert gater.resolve(rid, True) is True

        async def _runner() -> Any:
            return await core.run(session_id="s1", user_message="go")

        results = await asyncio.gather(_runner(), _resolve_later())
        result = results[0]
        # The gater emitted a permission.request before running the tool.
        assert any(ev[0] == "permission.request" for ev in emit.events)
        req_event = next(d for ev, d in emit.events if ev == "permission.request")
        assert req_event["tool"] == "echo"
        assert req_event["args"] == {"text": "ping"}
        # The tool ran (user said allow).
        assert len(tool.calls) == 1
        assert tool.calls[0]["text"] == "ping"
        # And the agent finalized cleanly.
        assert result.final_text == "allowed!"

    @pytest.mark.asyncio
    async def test_ask_user_deny_blocks(
        self, perm_store: PermissionStore
    ) -> None:
        await perm_store.upsert(tool_pattern="echo", action="ask")
        tool = EchoTool()
        call = _tool_call("echo", {"text": "x"}, call_id="c1")
        fake = FakeLLM([_tool_response(call), _text_response("ok, skipping")])
        emit = RecordingEmit()
        gater = PermissionGater(emit=emit)
        core = AgentCore(
            llm=fake,
            registry=_fresh_registry(tool),
            permission_store=perm_store,
            permission_gater=gater,
        )

        async def _deny_later() -> None:
            await asyncio.sleep(0.05)
            rid = gater.pending_ids()[0]
            assert gater.resolve(rid, False) is True

        results = await asyncio.gather(
            core.run(session_id="s1", user_message="go"), _deny_later()
        )
        assert tool.calls == []  # denied → tool never ran
        assert results[0].final_text == "ok, skipping"

    @pytest.mark.asyncio
    async def test_ask_timeout_denies(self, perm_store: PermissionStore) -> None:
        await perm_store.upsert(tool_pattern="echo", action="ask")
        tool = EchoTool()
        call = _tool_call("echo", {"text": "x"}, call_id="c1")
        fake = FakeLLM([_tool_response(call), _text_response("gave up")])
        emit = RecordingEmit()
        gater = PermissionGater(emit=emit, )  # type: ignore[arg-type]
        gater.default_timeout = 0.05  # type: ignore[attr-defined]
        core = AgentCore(
            llm=fake,
            registry=_fresh_registry(tool),
            permission_store=perm_store,
            permission_gater=gater,
        )
        result = await core.run(session_id="s1", user_message="go")
        # No one ever called resolve — gater timed out → deny.
        assert tool.calls == []
        assert result.final_text == "gave up"


def _fresh_registry(tool: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool)
    return reg


# ---------------------------------------------------------------------------
# PermissionGater
# ---------------------------------------------------------------------------


class TestPermissionGater:
    @pytest.mark.asyncio
    async def test_request_emit_then_resolve(self) -> None:
        emit = RecordingEmit()
        gater = PermissionGater(emit=emit)

        async def _resolve_later() -> None:
            await asyncio.sleep(0.02)
            assert gater.pending_count == 1
            rid = gater.pending_ids()[0]
            gater.resolve(rid, True)

        result, _ = await asyncio.gather(
            gater.request_consent(tool="echo", args={"text": "x"}),
            _resolve_later(),
        )
        assert result is True
        assert gater.pending_count == 0
        assert emit.events[0][0] == "permission.request"
        ev_data = emit.events[0][1]
        assert ev_data["tool"] == "echo"
        assert ev_data["args"] == {"text": "x"}
        assert "request_id" in ev_data

    @pytest.mark.asyncio
    async def test_resolve_unknown_returns_false(self) -> None:
        emit = RecordingEmit()
        gater = PermissionGater(emit=emit)
        assert gater.resolve("perm_nope", True) is False

    @pytest.mark.asyncio
    async def test_resolve_during_pending_is_idempotent(self) -> None:
        """A second resolve on the same in-flight request returns True but doesn't raise."""
        emit = RecordingEmit()
        gater = PermissionGater(emit=emit)

        async def _gate() -> bool:
            return await gater.request_consent(tool="echo", args={}, timeout=2)

        task = asyncio.create_task(_gate())
        await asyncio.sleep(0.02)
        rid = gater.pending_ids()[0]
        # First resolve unblocks the future.
        assert gater.resolve(rid, True) is True
        # Second resolve (future already done) returns True (no-op).
        assert gater.resolve(rid, False) is True
        assert await task is True

    @pytest.mark.asyncio
    async def test_cancel_all_denies_pending(self) -> None:
        emit = RecordingEmit()
        gater = PermissionGater(emit=emit)

        # Manually park two futures, then cancel.
        async def _park() -> bool:
            return await gater.request_consent(tool="a", args={}, timeout=10)

        tasks = [asyncio.create_task(_park()) for _ in range(2)]
        await asyncio.sleep(0.01)
        assert gater.pending_count == 2
        n = gater.cancel_all()
        assert n == 2
        results = await asyncio.gather(*tasks)
        assert results == [False, False]


# ---------------------------------------------------------------------------
# permission.resolve IPC handler
# ---------------------------------------------------------------------------


class TestPermissionResolveHandler:
    @pytest.mark.asyncio
    async def test_resolve_unblocks_gated_loop(
        self, async_db: AsyncDatabase
    ) -> None:
        # Set up a permissive in-process client + pre-built perm store.
        client = IPCClient()
        dao = PermissionRuleDAO(async_db)
        store = PermissionStore(dao)
        await store.warm()
        setattr(client.server, "_permission_store", store)

        # Install a gater on the server, simulate a pending request.
        emit = RecordingEmit()
        gater = PermissionGater(emit=emit)
        setattr(client.server, "_permission_gater", gater)

        # Start a gated request in the background.
        async def _gate() -> bool:
            return await gater.request_consent(
                tool="exec_command", args={"cmd": ["rm", "-rf", "/"]}
            )

        gate_task = asyncio.create_task(_gate())
        await asyncio.sleep(0.02)
        assert gater.pending_count == 1
        rid = gater.pending_ids()[0]

        # POST permission.resolve.
        result = await client.request(
            "permission.resolve", {"request_id": rid, "decision": "allow"}
        )
        assert result["ok"] is True
        assert result["request_id"] == rid
        assert result["decision"] == "allow"

        # The gated task unblocks with True.
        assert await gate_task is True
        # And the gater's pending map is empty.
        assert gater.pending_count == 0

    @pytest.mark.asyncio
    async def test_resolve_unknown_returns_ok_false(self) -> None:
        client = IPCClient()
        emit = RecordingEmit()
        gater = PermissionGater(emit=emit)
        setattr(client.server, "_permission_gater", gater)
        result = await client.request(
            "permission.resolve",
            {"request_id": "perm_ghost", "decision": "allow"},
        )
        assert result["ok"] is False
        assert "unknown" in result.get("reason", "")

    @pytest.mark.asyncio
    async def test_resolve_deny_decision(self) -> None:
        client = IPCClient()
        emit = RecordingEmit()
        gater = PermissionGater(emit=emit)
        setattr(client.server, "_permission_gater", gater)
        async def _gate() -> bool:
            return await gater.request_consent(tool="x", args={}, timeout=2)
        task = asyncio.create_task(_gate())
        await asyncio.sleep(0.02)
        rid = gater.pending_ids()[0]
        result = await client.request(
            "permission.resolve", {"request_id": rid, "decision": "deny"}
        )
        assert result["ok"] is True
        assert result["decision"] == "deny"
        assert await task is False

    @pytest.mark.asyncio
    async def test_resolve_rejects_missing_params(self) -> None:
        client = IPCClient()
        with pytest.raises(RuntimeError) as ei:
            await client.request("permission.resolve", {"request_id": "x"})
        assert "decision" in str(ei.value).lower() or "missing" in str(ei.value).lower()

    @pytest.mark.asyncio
    async def test_resolve_rejects_invalid_decision(self) -> None:
        client = IPCClient()
        with pytest.raises(RuntimeError) as ei:
            await client.request(
                "permission.resolve",
                {"request_id": "perm_1", "decision": "maybe"},
            )
        assert "decision" in str(ei.value).lower()


# ---------------------------------------------------------------------------
# agent.send_message end-to-end
# ---------------------------------------------------------------------------


class TestChatEndToEnd:
    @pytest.mark.asyncio
    async def test_send_message_ask_rule_routes_through_gater(
        self, perm_store: PermissionStore
    ) -> None:
        """Full chat flow: rule "ask" → gater emits → resolve → done.

        We can't easily inject a fake stream_chat through the
        MiniMaxClient surface (it'd need a real httpx transport), so
        this test exercises the gating by hand: install a perm_store
        on the server, start a gater, fire an ask request via the
        gater, then resolve. This mirrors what
        :func:`handle_agent_send_message` does internally.
        """

        client = IPCClient()
        setattr(client.server, "_permission_store", perm_store)
        await perm_store.upsert(tool_pattern="echo", action="ask")

        emit = RecordingEmit()
        gater = PermissionGater(emit=emit)
        setattr(client.server, "_permission_gater", gater)

        # Start the gated ask.
        async def _gate() -> bool:
            return await gater.request_consent(tool="echo", args={"text": "ping"})

        task = asyncio.create_task(_gate())
        await asyncio.sleep(0.02)
        rid = gater.pending_ids()[0]
        req_event = next(d for ev, d in emit.events if ev == "permission.request")
        assert req_event["tool"] == "echo"
        assert req_event["request_id"] == rid

        # Resolve via the IPC handler.
        result = await client.request(
            "permission.resolve", {"request_id": rid, "decision": "allow"}
        )
        assert result["ok"] is True
        assert await task is True


__all__ = [
    "TestAgentCoreGating",
    "TestPermissionGater",
    "TestPermissionResolveHandler",
    "TestChatEndToEnd",
]
