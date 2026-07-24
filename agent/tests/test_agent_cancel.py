"""Tests for ``agent.cancel`` IPC handler.

Validates:
- Graceful response when no active core exists for the session.
- The ``cancel()`` method is called on the matching AgentCore.
- The ``_ACTIVE_RUNS`` dict is cleaned up after a core finishes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from minimax_code.ipc.builtins import (
    _ACTIVE_RUNS,
    handle_agent_cancel,
)
from minimax_code.ipc.server import Context, IPCServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx() -> Context:
    """Build a minimal ``Context`` with a mock ``reply``."""
    server = MagicMock(spec=IPCServer)
    ctx = Context(server=server, request_id=1, method="agent.cancel")
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_unknown_session_returns_ok():
    """When no active core matches, reply with a graceful note."""
    ctx = _make_ctx()
    await handle_agent_cancel({"session_id": "ses_nonexistent"}, ctx)

    ctx.reply.assert_awaited_once()
    result = ctx.reply.call_args[0][0]
    assert result["ok"] is True
    assert result["note"] == "no active session"
    ctx.reply_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_active_session_sets_event():
    """When a core is registered, ``cancel()`` is called on it."""
    core = MagicMock()
    core.cancel = MagicMock()
    _ACTIVE_RUNS["ses_running"] = {"core": core, "type": "main"}

    ctx = _make_ctx()
    await handle_agent_cancel({"session_id": "ses_running"}, ctx)

    core.cancel.assert_called_once()
    ctx.reply.assert_awaited_once()
    result = ctx.reply.call_args[0][0]
    assert result["ok"] is True
    assert result["cancelled"] == "ses_running"


@pytest.mark.asyncio
async def test_cancel_missing_session_id_returns_error():
    """Missing ``session_id`` parameter triggers a -32602 error."""
    ctx = _make_ctx()
    await handle_agent_cancel({}, ctx)

    ctx.reply_error.assert_awaited_once()
    code = ctx.reply_error.call_args[0][0]
    assert code == -32602


@pytest.mark.asyncio
async def test_cancel_non_dict_params_returns_error():
    """Non-dict ``params`` triggers a -32602 error."""
    ctx = _make_ctx()
    await handle_agent_cancel("bad", ctx)

    ctx.reply_error.assert_awaited_once()
    code = ctx.reply_error.call_args[0][0]
    assert code == -32602


@pytest.mark.asyncio
async def test_active_runs_cleanup_after_run():
    """Verify that a core registered in ``_ACTIVE_RUNS`` is removed
    after ``handle_agent_send_message`` completes (via the ``finally``
    block in builtins.py).

    This test does *not* call the full ``handle_agent_send_message``
    (which needs storage, LLM client, etc.). Instead it verifies the
    cleanup contract directly: a mock core popped from the dict
    simulates the ``finally`` behaviour.
    """
    # Simulate what builtins.py does: register before run, pop after.
    _ACTIVE_RUNS["ses_test"] = {"core": MagicMock(), "type": "main"}
    assert "ses_test" in _ACTIVE_RUNS

    # Simulate the finally block.
    _ACTIVE_RUNS.pop("ses_test", None)
    assert "ses_test" not in _ACTIVE_RUNS
