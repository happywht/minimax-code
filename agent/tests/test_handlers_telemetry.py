"""Tests for the ``telemetry.*`` IPC handlers (R11).

A real :class:`TelemetryEngine` is injected via ``set_telemetry_engine`` so
the handlers exercise the live emit → redact → buffer path, not a stub.
We assert:

* ``telemetry.recent`` returns buffered events (filtered by type/session)
  and reports ``enabled: True`` + ``buffered`` count.
* ``telemetry.metrics`` returns the per-session / all-sessions snapshot.
* ``telemetry.clear`` drops the buffer and reports how many were cleared.
* When the engine is disabled (``None``), every endpoint degrades to an
  ``enabled: False`` empty shape instead of erroring.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from minimax_code import app
from minimax_code.config import Config
from minimax_code.ipc.handlers_telemetry import register_telemetry_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.telemetry import EventType, TelemetryEngine, TelemetryEvent


class _CapturedReply:
    def __init__(self) -> None:
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}

    async def emit(self, event: str, data: Any) -> None:  # pragma: no cover
        return None


@pytest.fixture
def handlers():  # type: ignore[no-untyped-def]
    """Register telemetry handlers with a fresh injected engine."""
    app.set_telemetry_engine(TelemetryEngine(buffer_capacity=20))
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=io.StringIO())
    register_telemetry_handlers(server)
    captured: dict[str, Any] = {
        "recent": server._handlers.get("telemetry.recent"),
        "metrics": server._handlers.get("telemetry.metrics"),
        "clear": server._handlers.get("telemetry.clear"),
    }
    for name in ("recent", "metrics", "clear"):
        assert captured[name] is not None, f"telemetry.{name} not registered"
    return captured


@pytest.fixture(autouse=True)
def _reset_engine():
    yield
    app.set_telemetry_engine(None)


@pytest.mark.asyncio
async def test_recent_returns_buffered_events(handlers: dict[str, Any]) -> None:
    engine: TelemetryEngine = app.get_telemetry_engine()
    engine.emit(TelemetryEvent(type=EventType.SESSION_START, session_id="s1"))
    engine.emit(
        TelemetryEvent(type=EventType.TOOL_CALL, session_id="s1", name="read_file")
    )
    engine.emit(TelemetryEvent(type=EventType.TOOL_CALL, session_id="s2", name="edit"))

    ctx = _CapturedReply()
    await handlers["recent"]({}, ctx)
    result = ctx.reply_value
    assert result["enabled"] is True
    assert result["total"] == 3
    assert result["buffered"] == 3

    ctx2 = _CapturedReply()
    await handlers["recent"]({"event_type": "tool_call", "session_id": "s2"}, ctx2)
    assert ctx2.reply_value["total"] == 1


@pytest.mark.asyncio
async def test_metrics_returns_snapshot(handlers: dict[str, Any]) -> None:
    engine: TelemetryEngine = app.get_telemetry_engine()
    engine.emit(
        TelemetryEvent(
            type=EventType.TOOL_CALL,
            session_id="s1",
            name="read_file",
            payload={"duration_ms": 42},
        )
    )
    ctx = _CapturedReply()
    await handlers["metrics"]({"session_id": "s1"}, ctx)
    snap = ctx.reply_value
    assert snap["enabled"] is True
    assert snap["tool_calls"] == 1
    assert snap["latency_ms"]["count"] == 1
    assert snap["session_id"] == "s1"


@pytest.mark.asyncio
async def test_clear_drops_buffer(handlers: dict[str, Any]) -> None:
    engine: TelemetryEngine = app.get_telemetry_engine()
    engine.emit(TelemetryEvent(type=EventType.SESSION_START, session_id="s1"))
    engine.emit(TelemetryEvent(type=EventType.TURN, session_id="s1"))
    assert engine.buffered_count == 2

    ctx = _CapturedReply()
    await handlers["clear"]({}, ctx)
    assert ctx.reply_value == {"ok": True, "cleared": 2, "enabled": True}
    assert engine.buffered_count == 0


@pytest.mark.asyncio
async def test_endpoints_degrade_when_engine_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telemetry disabled via env → every endpoint returns enabled=False, no error."""
    monkeypatch.setenv("MINIMAX_CODE_TELEMETRY", "0")
    app.set_telemetry_engine(None)
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=io.StringIO())
    register_telemetry_handlers(server)

    for method in ("telemetry.recent", "telemetry.metrics", "telemetry.clear"):
        ctx = _CapturedReply()
        await server._handlers[method]({}, ctx)
        assert ctx.error_value is None
        assert ctx.reply_value["enabled"] is False
