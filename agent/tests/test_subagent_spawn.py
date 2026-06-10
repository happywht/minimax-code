"""Tests for v0.3.0 §2 — ``agent.spawn_subagent`` handler.

These tests focus on the JSON-RPC handler rather than the runtime
itself (which is already covered by ``test_agents.py``). The
handler is invoked through a small in-process ``IPCServer`` with
a captured context; we assert:

* the handler emits the ``agent.subagent_progress`` events with the
  correct wire shape (``status``, ``progress`` 0..1, ``summary``,
  ``run_id``, ``agent_id``, ``parent_session_id``,
  ``context_message_id``)
* the final ``status`` reaches ``completed`` with ``progress: 1.0``
  and the final text attached
* the reply carries ``agent_run_id`` (the v0.3.0 wire name) and
  echoes the new ``parent_session_id`` / ``context_message_id``
  fields
* missing required params surface as ``INVALID_PARAMS``
* a runtime failure surfaces a final ``failed`` event before the
  error reply

The runtime is forced to its stub path (``llm=None``) so no real
LLM key is needed.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code.config import Config
from minimax_code.ipc.handlers_agents import register_agent_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.orchestrator import SubAgentRuntime, set_subagent_runtime
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeContext:
    """Stand-in for :class:`Context` that captures reply / error / emit."""

    def __init__(self) -> None:
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}

    async def emit(self, event: str, data: Any) -> None:
        self.events.append((event, data))


class _BoomRuntime(SubAgentRuntime):
    """SubAgentRuntime that always raises — exercises the failure path."""

    async def invoke(self, handle, *, session_id: str, request: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom — simulated LLM failure")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return make_temp_database_path(tmp_path)


@pytest.fixture
async def agent_dao(db_path: Path) -> AgentDAO:
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    try:
        yield AgentDAO(db)
    finally:
        await db.close()


@pytest.fixture
def handlers(agent_dao: AgentDAO):
    """Build a server with ``agent.*`` handlers registered.

    Force the process-wide runtime to the deterministic stub so the
    tests don't need an API key. The runtime is restored on teardown
    so other tests aren't affected.
    """
    prev = None
    try:
        from minimax_code.orchestrator import get_subagent_runtime

        prev = get_subagent_runtime()
    except Exception:  # pragma: no cover — defensive
        prev = None
    set_subagent_runtime(SubAgentRuntime())
    server = IPCServer(
        config=Config.from_env(),
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )
    register_agent_handlers(server, dao=agent_dao)
    yield server._handlers
    set_subagent_runtime(prev)


def _unique(prefix: str = "ag") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_subagent_emits_progress_stream(
    handlers: dict[str, Any], agent_dao: AgentDAO
) -> None:
    name = _unique("happy")
    await agent_dao.upsert(name=name, system_prompt="x")
    ctx = _FakeContext()
    parent_sid = f"ses_{uuid.uuid4().hex[:8]}"
    ctx_mid = f"msg_{uuid.uuid4().hex[:8]}"
    await handlers["agent.spawn_subagent"](
        {
            "name": name,
            "request": "hello",
            "parent_session_id": parent_sid,
            "context_message_id": ctx_mid,
            "display_name": "Happy Bot",
        },
        ctx,
    )

    # Reply carries the new v0.3.0 fields.
    assert ctx.error_value is None
    assert ctx.reply_value is not None
    reply = ctx.reply_value
    assert reply["agent_id"] == name
    assert reply["parent_session_id"] == parent_sid
    assert reply["context_message_id"] == ctx_mid
    assert reply["display_name"] == "Happy Bot"
    run_id = reply["agent_run_id"]
    assert run_id.startswith("run_")

    # All events are subagent_progress on the same run.
    assert ctx.events, "expected at least one progress event"
    assert all(ev == "agent.subagent_progress" for ev, _ in ctx.events)
    run_ids = {data["run_id"] for _, data in ctx.events}
    assert run_ids == {run_id}

    # The final event must be ``completed`` with progress=1.0 and text.
    final_event, final_data = ctx.events[-1]
    assert final_event == "agent.subagent_progress"
    assert final_data["status"] == "completed"
    assert final_data["progress"] == 1.0
    assert isinstance(final_data.get("text"), str)
    assert "hello" in final_data["text"]

    # All progress values are in [0, 1].
    for _, data in ctx.events:
        assert 0.0 <= data["progress"] <= 1.0
        # Required fields per the v0.3.0 §2 schema.
        for key in ("run_id", "agent_id", "status", "summary"):
            assert key in data

    # Stages we expect: started → thinking → (tool_call/tool_result
    # only if real LLM produces tool calls — stub path skips them) →
    # completed. Stub path: started, thinking, completed.
    statuses = [data["status"] for _, data in ctx.events]
    assert statuses[0] == "started"
    assert statuses[-1] == "completed"
    assert "thinking" in statuses


@pytest.mark.asyncio
async def test_spawn_subagent_rejects_missing_name(
    handlers: dict[str, Any], agent_dao: AgentDAO
) -> None:
    ctx = _FakeContext()
    await handlers["agent.spawn_subagent"]({"request": "x"}, ctx)
    assert ctx.reply_value is None
    assert ctx.error_value is not None
    assert ctx.error_value["code"] == -32602
    assert "name" in ctx.error_value["message"]


@pytest.mark.asyncio
async def test_spawn_subagent_rejects_unknown_agent(
    handlers: dict[str, Any], agent_dao: AgentDAO
) -> None:
    ctx = _FakeContext()
    await handlers["agent.spawn_subagent"](
        {"name": "no-such-agent", "request": "x"}, ctx
    )
    assert ctx.reply_value is None
    assert ctx.error_value is not None
    assert ctx.error_value["code"] == -32602
    assert "no-such-agent" in ctx.error_value["message"]
    assert ctx.events, "unknown agent should still surface a failed progress event"
    final_event, final_data = ctx.events[-1]
    assert final_event == "agent.subagent_progress"
    assert final_data["status"] == "failed"
    assert "no-such-agent" in (final_data.get("error") or "")


@pytest.mark.asyncio
async def test_spawn_subagent_accepts_internal_id_for_legacy_clients(
    handlers: dict[str, Any], agent_dao: AgentDAO
) -> None:
    name = _unique("legacy")
    row = await agent_dao.upsert(name=name, system_prompt="x")
    ctx = _FakeContext()
    await handlers["agent.spawn_subagent"](
        {"name": row["id"], "request": "hello from id"},
        ctx,
    )

    assert ctx.error_value is None
    assert ctx.reply_value is not None
    assert ctx.reply_value["agent_id"] == name
    final_event, final_data = ctx.events[-1]
    assert final_event == "agent.subagent_progress"
    assert final_data["agent_id"] == name
    assert final_data["status"] == "completed"


@pytest.mark.asyncio
async def test_spawn_subagent_emits_failed_event_on_runtime_error(
    handlers: dict[str, Any], agent_dao: AgentDAO
) -> None:
    """When the runtime raises mid-invoke, the handler must still
    surface a final ``failed`` event so the UI can flip the row."""
    # Replace the stub with a boom runtime.
    set_subagent_runtime(_BoomRuntime())
    name = _unique("boom")
    await agent_dao.upsert(name=name, system_prompt="x")
    ctx = _FakeContext()
    await handlers["agent.spawn_subagent"](
        {"name": name, "request": "x"}, ctx
    )

    # The reply is an error envelope.
    assert ctx.reply_value is None
    assert ctx.error_value is not None
    assert ctx.error_value["code"] == -32603

    # The final emitted event is a ``failed`` with an error string.
    assert ctx.events, "expected progress events before failure"
    final_event, final_data = ctx.events[-1]
    assert final_event == "agent.subagent_progress"
    assert final_data["status"] == "failed"
    assert "boom" in (final_data.get("error") or "")
