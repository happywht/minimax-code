"""Tests for lightweight ``terminal.*`` IPC command sessions."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable, Coroutine

import pytest

from minimax_code.config import Config
from minimax_code.ipc import handlers_terminal
from minimax_code.ipc.handlers_terminal import register_terminal_handlers
from minimax_code.ipc.server import IPCServer


class _CapturingContext:
    def __init__(self) -> None:
        self.reply_payload: Any = None
        self.reply_error_payload: tuple[int, str, Any] | None = None

    async def reply(self, result: Any) -> None:
        self.reply_payload = result

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.reply_error_payload = (code, message, data)

    async def emit(self, event: str, data: Any = None) -> None:  # pragma: no cover
        return None


@pytest.fixture(autouse=True)
def _clear_sessions() -> None:
    handlers_terminal._SESSIONS.clear()  # type: ignore[attr-defined]


def _make_handler(method: str) -> tuple[Callable[..., Coroutine[Any, Any, None]], _CapturingContext]:
    server = IPCServer(config=Config.from_env(), stdin=sys.stdin, stdout=sys.stdout)
    register_terminal_handlers(server)
    return server._handlers[method], _CapturingContext()  # type: ignore[return-value]


def _python_command(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


async def _read_until_done(session_id: str, *, timeout_s: float = 5.0) -> dict[str, Any]:
    read, ctx = _make_handler("terminal.read")
    deadline = asyncio.get_event_loop().time() + timeout_s
    payload: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        await read({"session_id": session_id, "after_seq": 0}, ctx)
        assert ctx.reply_error_payload is None
        payload = ctx.reply_payload
        if payload["session"]["status"] in {"completed", "failed", "cancelled"}:
            return payload
        await asyncio.sleep(0.05)
    raise AssertionError("terminal session did not finish")


@pytest.mark.asyncio
async def test_terminal_start_and_read_collects_stdout(tmp_path: Path) -> None:
    start, ctx = _make_handler("terminal.start")
    await start(
        {
            "command": _python_command("print('hello terminal')"),
            "cwd": str(tmp_path),
            "timeout_s": 5,
        },
        ctx,
    )

    assert ctx.reply_error_payload is None
    session = ctx.reply_payload["session"]
    payload = await _read_until_done(session["id"])

    assert payload["session"]["status"] == "completed"
    assert payload["session"]["exit_code"] == 0
    output = "".join(chunk["text"] for chunk in payload["chunks"])
    assert "hello terminal" in output


@pytest.mark.asyncio
async def test_terminal_read_after_seq_returns_incremental_chunks(tmp_path: Path) -> None:
    start, ctx = _make_handler("terminal.start")
    await start(
        {
            "command": _python_command("print('first'); print('second')"),
            "cwd": str(tmp_path),
            "timeout_s": 5,
        },
        ctx,
    )
    session_id = ctx.reply_payload["session"]["id"]
    payload = await _read_until_done(session_id)
    first_seq = payload["chunks"][0]["seq"]

    read, read_ctx = _make_handler("terminal.read")
    await read({"session_id": session_id, "after_seq": first_seq}, read_ctx)

    assert read_ctx.reply_error_payload is None
    assert all(chunk["seq"] > first_seq for chunk in read_ctx.reply_payload["chunks"])


@pytest.mark.asyncio
async def test_terminal_stop_cancels_running_process(tmp_path: Path) -> None:
    start, ctx = _make_handler("terminal.start")
    await start(
        {
            "command": _python_command("import time; print('ready', flush=True); time.sleep(30)"),
            "cwd": str(tmp_path),
            "timeout_s": 60,
        },
        ctx,
    )
    session_id = ctx.reply_payload["session"]["id"]
    await asyncio.sleep(0.2)

    stop, stop_ctx = _make_handler("terminal.stop")
    await stop({"session_id": session_id}, stop_ctx)

    assert stop_ctx.reply_error_payload is None
    assert stop_ctx.reply_payload["session"]["status"] == "cancelled"
