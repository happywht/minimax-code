"""Tests for ``runner.*`` IPC handlers."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable, Coroutine

import pytest

from minimax_code.config import Config
from minimax_code.ipc import handlers_terminal
from minimax_code.ipc.handlers_runner import register_runner_handlers
from minimax_code.ipc.handlers_terminal import register_terminal_handlers
from minimax_code.ipc.server import IPCServer


class _CapturingContext:
    def __init__(self) -> None:
        self.reply_payload: Any = None
        self.reply_error_payload: tuple[int, str, Any] | None = None
        self.events: list[tuple[str, Any]] = []

    async def reply(self, result: Any) -> None:
        self.reply_payload = result

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.reply_error_payload = (code, message, data)

    async def emit(self, event: str, data: Any = None) -> None:
        self.events.append((event, data))


@pytest.fixture(autouse=True)
def _clear_sessions() -> None:
    handlers_terminal._SESSIONS.clear()  # type: ignore[attr-defined]


def _make_handler(
    method: str,
) -> tuple[Callable[..., Coroutine[Any, Any, None]], _CapturingContext]:
    server = IPCServer(config=Config.from_env(), stdin=sys.stdin, stdout=sys.stdout)
    register_terminal_handlers(server)
    register_runner_handlers(server)
    return server._handlers[method], _CapturingContext()  # type: ignore[return-value]


def _python_command(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


async def _await_session(session_id: str, *, timeout_s: float = 5.0) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        session = handlers_terminal._SESSIONS[session_id]  # type: ignore[attr-defined]
        if session.status in {"completed", "failed", "cancelled"}:
            if session.task is not None:
                await session.task
            return session.to_wire()
        await asyncio.sleep(0.05)
    raise AssertionError("runner terminal session did not finish")


@pytest.mark.asyncio
async def test_runner_list_exposes_native_and_external_adapters() -> None:
    handler, ctx = _make_handler("runner.list")
    await handler({}, ctx)

    assert ctx.reply_error_payload is None
    runners = {runner["id"]: runner for runner in ctx.reply_payload["runners"]}
    assert runners["native"]["available"] is True
    assert runners["native"]["supports_terminal"] is True
    assert "codex-cli" in runners
    assert "claude-code-cli" in runners


@pytest.mark.asyncio
async def test_runner_start_native_creates_terminal_session(tmp_path: Path) -> None:
    handler, ctx = _make_handler("runner.start")
    await handler(
        {
            "runner_id": "native",
            "command": _python_command("print('runner ok')"),
            "cwd": str(tmp_path),
            "timeout_s": 5,
        },
        ctx,
    )

    assert ctx.reply_error_payload is None
    assert ctx.reply_payload["runner"]["id"] == "native"
    session = ctx.reply_payload["session"]
    done = await _await_session(session["id"])
    assert done["status"] == "completed"
    assert done["exit_code"] == 0


@pytest.mark.asyncio
async def test_runner_start_external_cli_fails_explicitly() -> None:
    handler, ctx = _make_handler("runner.start")
    await handler({"runner_id": "codex-cli", "command": "hello"}, ctx)

    assert ctx.reply_payload is None
    assert ctx.reply_error_payload is not None
    _code, message, data = ctx.reply_error_payload
    assert "not executable yet" in message
    assert data["runner_id"] == "codex-cli"
