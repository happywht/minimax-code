"""Tests for ``agent.cancel_subagent`` IPC handler.

Validates the cooperative cancellation mechanism for sub-agent runs:
- Graceful response when no active run matches the ``run_id``.
- ``AgentCore.cancel()`` is called when a run is found in ``_ACTIVE_RUNS``.
- A ``failed`` subagent_progress event is emitted on cancellation.
- Missing ``run_id`` parameter triggers a -32602 error.
- Non-dict ``params`` triggers a -32602 error.
- ``_ACTIVE_RUNS`` is cleaned up after cancellation.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

import pytest

from minimax_code.config import Config
from minimax_code.ipc.builtins import _ACTIVE_RUNS
from minimax_code.ipc.handlers_agents import register_agent_handlers
from minimax_code.ipc.server import Context, IPCServer
from minimax_code.orchestrator import SubAgentRuntime, set_subagent_runtime

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx() -> Context:
    """Build a minimal ``Context`` with a mock ``reply``."""
    server = MagicMock(spec=IPCServer)
    ctx = Context(server=server, request_id=1, method="agent.cancel_subagent")
    ctx.reply = AsyncMock()
    ctx.reply_error = AsyncMock()
    ctx.emit = AsyncMock()
    return ctx


@pytest.fixture(autouse=True)
def _clean_active_runs():
    """Ensure ``_ACTIVE_RUNS`` is empty before and after each test."""
    _ACTIVE_RUNS.clear()
    yield
    _ACTIVE_RUNS.clear()


@pytest.fixture
def handlers():
    """Register agent handlers onto a fresh IPCServer and return the
    handler dict. Uses the same pattern as ``test_subagent_spawn.py``.
    """
    prev = None
    try:
        from minimax_code.orchestrator import get_subagent_runtime
        prev = get_subagent_runtime()
    except Exception:
        prev = None
    set_subagent_runtime(SubAgentRuntime())
    server = IPCServer(
        config=Config.from_env(),
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )
    register_agent_handlers(server)
    yield server._handlers
    set_subagent_runtime(prev)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_unknown_run_returns_graceful(handlers):
    """When no active run matches the run_id, reply with a graceful note."""
    handler = handlers["agent.cancel_subagent"]
    ctx = _make_ctx()
    await handler({"run_id": "run_nonexistent"}, ctx)

    ctx.reply.assert_awaited_once()
    result = ctx.reply.call_args[0][0]
    assert result["ok"] is True
    assert result["cancelled"] is False
    assert "note" in result
    ctx.reply_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_active_run_calls_core_cancel(handlers):
    """When a sub-agent core is registered, ``cancel()`` is called."""
    core = MagicMock()
    core.cancel = MagicMock()
    _ACTIVE_RUNS["run_abc123"] = {"core": core, "type": "subagent"}

    handler = handlers["agent.cancel_subagent"]
    ctx = _make_ctx()
    await handler({"run_id": "run_abc123"}, ctx)

    core.cancel.assert_called_once()
    ctx.reply.assert_awaited_once()
    result = ctx.reply.call_args[0][0]
    assert result["ok"] is True
    assert result["cancelled"] is True


@pytest.mark.asyncio
async def test_cancel_emits_failed_progress_event(handlers):
    """Cancelling a running sub-agent emits a ``failed`` progress event
    so the frontend row transitions to a terminal state."""
    core = MagicMock()
    core.cancel = MagicMock()
    _ACTIVE_RUNS["run_emit"] = {"core": core, "type": "subagent"}

    handler = handlers["agent.cancel_subagent"]
    ctx = _make_ctx()
    await handler({"run_id": "run_emit"}, ctx)

    # The handler should have emitted a subagent_progress event.
    ctx.emit.assert_awaited()
    event_name = ctx.emit.call_args[0][0]
    event_data = ctx.emit.call_args[0][1]
    assert event_name == "agent.subagent_progress"
    assert event_data["status"] == "failed"
    assert event_data["summary"] == "cancelled"
    assert event_data["error"] == "cancelled by user"


@pytest.mark.asyncio
async def test_cancel_missing_run_id_returns_error(handlers):
    """Missing ``run_id`` parameter triggers a -32602 error."""
    handler = handlers["agent.cancel_subagent"]
    ctx = _make_ctx()
    await handler({}, ctx)

    ctx.reply_error.assert_awaited_once()
    code = ctx.reply_error.call_args[0][0]
    assert code == -32602


@pytest.mark.asyncio
async def test_cancel_non_dict_params_returns_error(handlers):
    """Non-dict ``params`` triggers a -32602 error."""
    handler = handlers["agent.cancel_subagent"]
    ctx = _make_ctx()
    await handler("bad", ctx)

    ctx.reply_error.assert_awaited_once()
    code = ctx.reply_error.call_args[0][0]
    assert code == -32602


@pytest.mark.asyncio
async def test_cancel_removes_entry_from_active_runs(handlers):
    """After cancellation, the run_id is removed from ``_ACTIVE_RUNS``."""
    core = MagicMock()
    core.cancel = MagicMock()
    _ACTIVE_RUNS["run_cleanup"] = {"core": core, "type": "subagent"}
    assert "run_cleanup" in _ACTIVE_RUNS

    handler = handlers["agent.cancel_subagent"]
    ctx = _make_ctx()
    await handler({"run_id": "run_cleanup"}, ctx)

    assert "run_cleanup" not in _ACTIVE_RUNS


@pytest.mark.asyncio
async def test_cancel_does_not_remove_if_no_core(handlers):
    """An entry without a ``core`` key should still reply gracefully
    and not raise an exception."""
    _ACTIVE_RUNS["run_nocore"] = {"type": "subagent"}  # no "core" key

    handler = handlers["agent.cancel_subagent"]
    ctx = _make_ctx()
    await handler({"run_id": "run_nocore"}, ctx)

    # Should reply with cancelled=False (entry was popped but no core).
    ctx.reply.assert_awaited_once()
    result = ctx.reply.call_args[0][0]
    assert result["ok"] is True
    assert result["cancelled"] is False
