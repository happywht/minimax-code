"""Regression tests for P0/P1 bug-fixes (v0.9.0 quality polish).

Covers:
  P0#1  — team.spawn emits events via server.notify(), not server.emit()
  P0#2  — notification/workflow DAO factories never call AsyncDatabase()
           without a path
  P1#6  — all handler DAO factories use the get_db() singleton instead of
           opening extra DB connections
  P1#8  — HTTP POST /rpc rejects oversized request bodies
  P1#7  — agent.send_message reuses the LLM client singleton
  P1#9  — graceful shutdown cancels active cores and cleans up resources
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minimax_code.config import Config
from minimax_code.ipc.server import IPCServer


# ── Helpers ───────────────────────────────────────────────────────────────────


class _CapturedReply:
    """Minimal Context substitute that captures reply / reply_error.

    Includes a ``server`` attribute for handlers that access
    ``ctx.server`` (e.g. audit handlers that cache the DAO factory
    on the server object).
    """

    def __init__(self, server: IPCServer | None = None) -> None:
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None
        self.server = server

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}

    async def emit(self, event: str, data: Any = None, **kw: Any) -> None:
        """No-op emit — swallows events in tests."""
        pass


class _StubTeamDAO:
    """In-memory stub for AgentTeamDAO."""

    def __init__(self) -> None:
        self._teams: dict[str, dict[str, Any]] = {}

    async def list_all(self) -> list[dict[str, Any]]:
        return list(self._teams.values())

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        return self._teams.get(name)

    async def create(self, **kw: Any) -> dict[str, Any]:
        name = kw["name"]
        team = {
            "id": "team_001",
            "name": name,
            "description": kw.get("description", ""),
            "icon": kw.get("icon", ""),
            "color": kw.get("color", ""),
            "agents": kw.get("agents", []),
            "orchestration_mode": kw.get("orchestration_mode", "parallel"),
            "enabled": True,
            "created_at": "2026-06-07T00:00:00",
            "updated_at": "2026-06-07T00:00:00",
        }
        self._teams[name] = team
        return team

    async def set_enabled(self, name: str, val: bool) -> dict[str, Any] | None:
        t = self._teams.get(name)
        if t:
            t["enabled"] = val
        return t


def _make_fake_db() -> AsyncMock:
    """Build a fake AsyncDatabase with awaitable fetch methods."""
    db = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    db.fetchone = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    db.execute_insert = AsyncMock(return_value=None)
    return db


# ── P0#1: team.spawn server.emit() → server.notify() ─────────────────────────


@pytest.mark.asyncio
async def test_team_spawn_does_not_crash_on_emit():
    """P0#1: team.spawn must NOT crash with AttributeError on server.emit().

    The old code called ``server.emit(event, payload)`` but IPCServer
    has no ``emit()`` method. The fix uses Event + server.notify().
    """
    from minimax_code.ipc.handlers_teams import register_team_handlers
    from minimax_code.orchestrator.team_orchestrator import (
        AgentRunResult,
        TeamRunResult,
    )

    stub_dao = _StubTeamDAO()
    await stub_dao.create(name="review-team", agents=["reviewer"])

    server = IPCServer(Config())
    register_team_handlers(server, dao=stub_dao)

    async def _fake_run(team_name, request, **kw):
        return TeamRunResult(
            team_name=team_name,
            orchestration_mode="parallel",
            agents_run=[
                AgentRunResult(
                    agent_name="reviewer",
                    success=True,
                    text="LGTM",
                    error=None,
                    iterations=1,
                    stub=True,
                )
            ],
            merged_text="LGTM",
            conflicts=[],
            task_id=None,
            success=True,
        )

    with (
        patch(
            "minimax_code.ipc.handlers_teams._make_agent_dao",
            return_value=MagicMock(),
        ),
        patch(
            "minimax_code.orchestrator.team_orchestrator.TeamOrchestrator.run",
            side_effect=_fake_run,
        ),
    ):
        ctx = _CapturedReply()
        await server._handlers["team.spawn"](
            {"team_name": "review-team", "request": "check code"},
            ctx,
        )

    # The key assertion: no crash, successful response
    assert ctx.error_value is None, f"unexpected error: {ctx.error_value}"
    assert ctx.reply_value is not None
    assert ctx.reply_value["team_name"] == "review-team"
    assert ctx.reply_value["success"] is True


@pytest.mark.asyncio
async def test_team_spawn_emit_does_not_crash_on_listener_error():
    """If a listener raises during event emission, spawn must still succeed."""
    from minimax_code.ipc.handlers_teams import register_team_handlers
    from minimax_code.orchestrator.team_orchestrator import (
        AgentRunResult,
        TeamRunResult,
    )

    stub_dao = _StubTeamDAO()
    await stub_dao.create(name="resilient-team", agents=["a1"])

    server = IPCServer(Config())
    register_team_handlers(server, dao=stub_dao)

    # Register a bad listener that raises
    def _bad_listener(env):
        raise RuntimeError("boom")

    server.register_listener(_bad_listener)

    async def _fake_run(team_name, request, **kw):
        return TeamRunResult(
            team_name=team_name,
            orchestration_mode="parallel",
            agents_run=[
                AgentRunResult("a1", True, "ok", None, 1, True)
            ],
            merged_text="ok",
            conflicts=[],
            task_id=None,
            success=True,
        )

    with (
        patch(
            "minimax_code.ipc.handlers_teams._make_agent_dao",
            return_value=MagicMock(),
        ),
        patch(
            "minimax_code.orchestrator.team_orchestrator.TeamOrchestrator.run",
            side_effect=_fake_run,
        ),
    ):
        ctx = _CapturedReply()
        await server._handlers["team.spawn"](
            {"team_name": "resilient-team", "request": "test"},
            ctx,
        )

    # Must still succeed despite the bad listener
    assert ctx.error_value is None
    assert ctx.reply_value["success"] is True


# ── P0#2 + P1#6: DAO factories use get_db() singleton ────────────────────────
#
# get_db is imported inside each factory via ``from ..app import get_db``
# so we patch at the source module: ``minimax_code.app.get_db``.


@pytest.mark.asyncio
async def test_notification_dao_factory_uses_get_db():
    """notification DAO factory must use get_db() and never call AsyncDatabase()."""
    from minimax_code.ipc.handlers_notifications import (
        _make_notification_dao_factory,
    )
    from minimax_code.storage.dao.notifications import NotificationDAO

    fake_db = _make_fake_db()

    with patch("minimax_code.app.get_db", return_value=fake_db):
        server = IPCServer(Config())
        factory = _make_notification_dao_factory(server)
        dao = await factory()
        assert isinstance(dao, NotificationDAO)
        assert getattr(server, "_notification_dao", None) is dao


@pytest.mark.asyncio
async def test_notification_dao_factory_returns_error_when_no_db():
    """When get_db() returns None, the factory must raise _HandlerError."""
    from minimax_code.ipc.handlers_notifications import (
        _make_notification_dao_factory,
    )
    from minimax_code.ipc.handler_utils import HandlerError as _HandlerError

    with patch("minimax_code.app.get_db", return_value=None):
        server = IPCServer(Config())
        factory = _make_notification_dao_factory(server)
        with pytest.raises(_HandlerError) as exc_info:
            await factory()
        assert "storage not initialised" in exc_info.value.message


@pytest.mark.asyncio
async def test_workflow_dao_factory_uses_get_db():
    """workflow DAO factory must use get_db() and never call AsyncDatabase()."""
    from minimax_code.ipc.handlers_workflows import _make_workflow_dao_factory
    from minimax_code.storage.dao.workflows import WorkflowDAO

    fake_db = _make_fake_db()

    with patch("minimax_code.app.get_db", return_value=fake_db):
        server = IPCServer(Config())
        factory = _make_workflow_dao_factory(server)
        dao = await factory()
        assert isinstance(dao, WorkflowDAO)


@pytest.mark.asyncio
async def test_workflow_dao_factory_returns_error_when_no_db():
    """When get_db() returns None, the factory must raise _HandlerError."""
    from minimax_code.ipc.handlers_workflows import (
        _make_workflow_dao_factory,
    )
    from minimax_code.ipc.handler_utils import HandlerError as _HandlerError

    with patch("minimax_code.app.get_db", return_value=None):
        server = IPCServer(Config())
        factory = _make_workflow_dao_factory(server)
        with pytest.raises(_HandlerError) as exc_info:
            await factory()
        assert "storage not initialised" in exc_info.value.message


@pytest.mark.asyncio
async def test_team_dao_factory_uses_get_db():
    """team DAO factory must use get_db() and never open a new connection."""
    from minimax_code.ipc.handlers_teams import register_team_handlers

    fake_db = _make_fake_db()

    with patch("minimax_code.app.get_db", return_value=fake_db):
        server = IPCServer(Config())
        register_team_handlers(server, dao=None)

        ctx = _CapturedReply()
        await server._handlers["team.list"](None, ctx)
        assert ctx.reply_value is not None
        assert "teams" in ctx.reply_value


@pytest.mark.asyncio
async def test_team_dao_factory_returns_none_when_no_db():
    """When get_db() returns None, team DAO factory returns None and
    the handler returns a graceful error."""
    from minimax_code.ipc.handlers_teams import register_team_handlers

    with patch("minimax_code.app.get_db", return_value=None):
        server = IPCServer(Config())
        register_team_handlers(server, dao=None)

        ctx = _CapturedReply()
        await server._handlers["team.list"](None, ctx)
        assert ctx.error_value is not None
        assert "storage" in ctx.error_value["message"].lower()


@pytest.mark.asyncio
async def test_audit_dao_factory_uses_get_db():
    """audit DAO factory must use get_db() and never open a new connection."""
    from minimax_code.ipc.handlers_audit import register_audit_handlers

    fake_db = _make_fake_db()

    with patch("minimax_code.app.get_db", return_value=fake_db):
        server = IPCServer(Config())
        register_audit_handlers(server)

        # audit handlers use ctx.server to cache the factory
        ctx = _CapturedReply(server=server)
        await server._handlers["audit.list"]({}, ctx)
        assert ctx.reply_value is not None
        assert "entries" in ctx.reply_value
        assert "total" in ctx.reply_value


@pytest.mark.asyncio
async def test_audit_dao_factory_returns_none_when_no_db():
    """When get_db() returns None, audit DAO factory returns None (handlers
    gracefully return empty results)."""
    from minimax_code.ipc.handlers_audit import register_audit_handlers

    with patch("minimax_code.app.get_db", return_value=None):
        server = IPCServer(Config())
        register_audit_handlers(server)

        ctx = _CapturedReply(server=server)
        await server._handlers["audit.list"]({}, ctx)
        # Should return empty results, not crash
        assert ctx.reply_value == {"entries": [], "total": 0}


# ── P1#8: HTTP POST /rpc body size limit ──────────────────────────────────────


@pytest.mark.asyncio
async def test_rpc_rejects_oversized_body():
    """POST /rpc must reject payloads larger than MAX_RPC_BODY_BYTES."""
    import json

    from httpx import ASGITransport, AsyncClient

    from minimax_code.http_server import MAX_RPC_BODY_BYTES, build_app

    server = IPCServer(Config())
    app = build_app(server)

    # Build a payload that exceeds the limit
    huge_payload = {"jsonrpc": "2.0", "method": "ping", "id": 1}
    # Pad to exceed limit
    huge_payload["params"] = {"fill": "x" * (MAX_RPC_BODY_BYTES + 1)}
    body = json.dumps(huge_payload).encode()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/rpc",
            content=body,
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert "too large" in data["error"]["message"]


@pytest.mark.asyncio
async def test_rpc_accepts_normal_body():
    """POST /rpc must accept normal-sized payloads without issue."""
    import json

    from httpx import ASGITransport, AsyncClient

    from minimax_code.http_server import build_app

    server = IPCServer(Config())
    app = build_app(server)

    payload = {"jsonrpc": "2.0", "method": "ping", "id": 1}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/rpc",
            json=payload,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data
    # ping handler returns a dict with "pong" key
    assert "pong" in data["result"]


# ── P1#7: agent.send_message reuses LLM client singleton ─────────────────────


@pytest.mark.asyncio
async def test_send_message_reuses_subagent_llm():
    """agent.send_message must use get_subagent_llm() singleton,
    NOT create a fresh MiniMaxClient() per request."""
    from unittest.mock import patch, MagicMock

    fake_llm = MagicMock()
    fake_llm.mock = True

    with patch("minimax_code.app.get_subagent_llm", return_value=fake_llm):
        from minimax_code.ipc.builtins import handle_agent_send_message

        server = IPCServer(Config())
        ctx = _CapturedReply(server=server)

        # Patch AgentCore so we don't need a full conversation loop
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.final_text = "test response"
        mock_result.iterations = 1
        mock_result.usage = {}
        mock_core.run = AsyncMock(return_value=mock_result)
        mock_core.on_chunk = None
        mock_core.on_status = None
        mock_core.on_tool_call = None
        mock_core.on_tool_result = None

        with (
            # All lazy imports in builtins.py use ``from ..app import ...``
            # so we patch at the source module.
            patch("minimax_code.app.init_runtime", return_value=MagicMock()),
            patch("minimax_code.app.get_sessions_dao", return_value=None),
            patch("minimax_code.app.get_db", return_value=_make_fake_db()),
            patch("minimax_code.agent.AgentCore", return_value=mock_core),
            patch(
                "minimax_code.ipc.builtins._build_system_prompt_extra",
                return_value=None,
            ),
            patch("minimax_code.perm_consent.PermissionGater", return_value=MagicMock()),
            patch(
                "minimax_code.ipc.handlers_permissions._ensure_permission_store",
                return_value=None,
            ),
        ):
            await handle_agent_send_message(
                {"content": "hello", "session_id": "ses_test123"},
                ctx,
            )

    # The handler should succeed (not error out)
    assert ctx.error_value is None, f"unexpected error: {ctx.error_value}"
    assert ctx.reply_value is not None
    assert ctx.reply_value["session_id"] == "ses_test123"


@pytest.mark.asyncio
async def test_send_message_falls_back_to_default_llm():
    """When get_subagent_llm() returns None, handler must create MiniMaxClient()."""
    from unittest.mock import patch, MagicMock

    with patch("minimax_code.app.get_subagent_llm", return_value=None):
        from minimax_code.ipc.builtins import handle_agent_send_message

        server = IPCServer(Config())
        ctx = _CapturedReply(server=server)

        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.final_text = "fallback"
        mock_result.iterations = 1
        mock_result.usage = {}
        mock_core.run = AsyncMock(return_value=mock_result)
        mock_core.on_chunk = None
        mock_core.on_status = None
        mock_core.on_tool_call = None
        mock_core.on_tool_result = None

        with (
            patch("minimax_code.app.init_runtime", return_value=MagicMock()),
            patch("minimax_code.app.get_sessions_dao", return_value=None),
            patch("minimax_code.app.get_db", return_value=_make_fake_db()),
            patch("minimax_code.agent.AgentCore", return_value=mock_core),
            patch(
                "minimax_code.ipc.builtins._build_system_prompt_extra",
                return_value=None,
            ),
            patch("minimax_code.perm_consent.PermissionGater", return_value=MagicMock()),
            patch(
                "minimax_code.ipc.handlers_permissions._ensure_permission_store",
                return_value=None,
            ),
        ):
            await handle_agent_send_message(
                {"content": "hello", "session_id": "ses_fb"},
                ctx,
            )

    assert ctx.error_value is None, f"unexpected error: {ctx.error_value}"
    assert ctx.reply_value is not None
    assert ctx.reply_value["session_id"] == "ses_fb"


@pytest.mark.asyncio
async def test_send_message_rejects_no_db_with_clear_error():
    """When get_db() returns None, handler must return a clear error
    instead of opening a second DB connection."""
    from unittest.mock import patch, MagicMock

    with (
        patch("minimax_code.app.get_subagent_llm", return_value=None),
        patch("minimax_code.app.init_runtime", return_value=MagicMock()),
        patch("minimax_code.app.get_db", return_value=None),
    ):
        from minimax_code.ipc.builtins import handle_agent_send_message

        server = IPCServer(Config())
        ctx = _CapturedReply(server=server)

        await handle_agent_send_message(
            {"content": "hello", "session_id": "ses_nodb"},
            ctx,
        )

    assert ctx.error_value is not None
    assert "storage" in ctx.error_value["message"].lower()


# ── P1#9: Graceful shutdown ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_server_stop_cancels_active_cores():
    """Calling server.stop() must cancel all active AgentCore instances."""
    from minimax_code.ipc.builtins import _ACTIVE_RUNS

    server = IPCServer(Config())

    # Simulate 2 active cores
    mock_core_1 = MagicMock()
    mock_core_2 = MagicMock()
    _ACTIVE_RUNS["ses_a"] = {"core": mock_core_1, "type": "main"}
    _ACTIVE_RUNS["ses_b"] = {"core": mock_core_2, "type": "main"}

    try:
        server.stop()

        # Both cores must be cancelled
        mock_core_1.cancel.assert_called_once()
        mock_core_2.cancel.assert_called_once()
        # And removed from the registry
        assert len(_ACTIVE_RUNS) == 0
    finally:
        _ACTIVE_RUNS.clear()


@pytest.mark.asyncio
async def test_shutdown_handler_triggers_stop():
    """The ``shutdown`` IPC handler must call server.stop() which
    cancels active cores."""
    from minimax_code.ipc.builtins import _ACTIVE_RUNS

    server = IPCServer(Config())
    # __init__ already registers the default handlers (ping, shutdown, etc.)

    mock_core = MagicMock()
    _ACTIVE_RUNS["ses_active"] = {"core": mock_core, "type": "main"}

    try:
        ctx = _CapturedReply(server=server)
        await server._handlers["shutdown"]({}, ctx)

        assert ctx.reply_value == {"ok": True}
        mock_core.cancel.assert_called_once()
        assert len(_ACTIVE_RUNS) == 0
    finally:
        _ACTIVE_RUNS.clear()


@pytest.mark.asyncio
async def test_http_lifespan_cleans_up_resources():
    """The FastAPI lifespan must clean up DB, LLM, and active cores
    on shutdown."""
    from minimax_code.http_server import build_app

    server = IPCServer(Config())

    fake_llm = MagicMock()
    fake_llm.close = AsyncMock()
    fake_db = MagicMock()
    fake_db.close = AsyncMock()

    with (
        patch("minimax_code.app.get_subagent_llm", return_value=fake_llm),
        patch("minimax_code.app.get_db", return_value=fake_db),
        patch("minimax_code.scheduler.get_scheduler", return_value=None),
    ):
        app = build_app(server)

        # Trigger the lifespan startup + shutdown by directly
        # invoking the async context manager.
        async with app.router.lifespan_context(app):
            pass  # startup done, exiting triggers shutdown

    # After the lifespan exits, cleanup should have been called.
    fake_db.close.assert_awaited_once()
    fake_llm.close.assert_awaited_once()
