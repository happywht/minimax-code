"""Tests for ``runner.*`` IPC handlers."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable, Coroutine

import pytest

from minimax_code.config import Config
from minimax_code.ipc import handlers_runner
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
async def test_runner_start_external_cli_fails_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        handlers_runner,
        "_runner_catalog",
        lambda: [
            {
                "id": "native",
                "label": "Native shell",
                "kind": "native",
                "available": True,
                "command": None,
                "version": None,
                "reason": None,
                "supports_prompt": False,
                "supports_terminal": True,
            },
            {
                "id": "codex-cli",
                "label": "Codex CLI",
                "kind": "external_cli",
                "available": False,
                "command": "codex",
                "version": None,
                "reason": "not runnable: permission denied",
                "supports_prompt": True,
                "supports_terminal": True,
            },
        ],
    )
    handler, ctx = _make_handler("runner.start")
    await handler({"runner_id": "codex-cli", "command": "hello"}, ctx)

    assert ctx.reply_payload is None
    assert ctx.reply_error_payload is not None
    _code, message, data = ctx.reply_error_payload
    assert "not runnable" in message
    assert data["runner_id"] == "codex-cli"


@pytest.mark.asyncio
async def test_runner_start_external_cli_delegates_to_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_start_terminal_command(**kwargs: Any) -> Any:
        captured.update(kwargs)

        class _Session:
            id = "term_fake"

            def to_wire(self) -> dict[str, Any]:
                return {
                    "id": self.id,
                    "command": kwargs["command"],
                    "cwd": kwargs["cwd"],
                    "session_id": kwargs["chat_session_id"],
                    "run_id": None,
                    "status": "starting",
                    "started_at": 1,
                    "updated_at": 1,
                    "completed_at": None,
                    "exit_code": None,
                    "error": None,
                    "next_seq": 1,
                }

        return _Session()

    monkeypatch.setattr(
        handlers_runner,
        "_runner_catalog",
        lambda: [
            {
                "id": "native",
                "label": "Native shell",
                "kind": "native",
                "available": True,
                "command": None,
                "version": None,
                "reason": None,
                "supports_prompt": False,
                "supports_terminal": True,
            },
            {
                "id": "claude-code-cli",
                "label": "Claude Code CLI",
                "kind": "external_cli",
                "available": True,
                "command": "claude",
                "version": "2.1.168",
                "reason": None,
                "supports_prompt": True,
                "supports_terminal": True,
            },
        ],
    )
    monkeypatch.setattr(handlers_runner, "start_terminal_command", fake_start_terminal_command)

    handler, ctx = _make_handler("runner.start")
    await handler(
        {
            "runner_id": "claude-code-cli",
            "command": "summarize this repo",
            "cwd": str(tmp_path),
            "session_id": "ses_runner",
            "permission_mode": "plan",
        },
        ctx,
    )

    assert ctx.reply_error_payload is None
    assert ctx.reply_payload["runner"]["id"] == "claude-code-cli"
    assert ctx.reply_payload["session"]["id"] == "term_fake"
    assert "claude" in captured["command"]
    assert "--print" in captured["command"]
    assert "--permission-mode plan" in captured["command"]
    assert "summarize this repo" in captured["command"]
    assert captured["cwd"] == str(tmp_path)
    assert captured["chat_session_id"] == "ses_runner"


@pytest.mark.asyncio
async def test_runner_start_codex_cli_uses_safety_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_start_terminal_command(**kwargs: Any) -> Any:
        captured.update(kwargs)

        class _Session:
            id = "term_codex"

            def to_wire(self) -> dict[str, Any]:
                return {
                    "id": self.id,
                    "command": kwargs["command"],
                    "cwd": kwargs["cwd"],
                    "session_id": kwargs["chat_session_id"],
                    "run_id": None,
                    "status": "starting",
                    "started_at": 1,
                    "updated_at": 1,
                    "completed_at": None,
                    "exit_code": None,
                    "error": None,
                    "next_seq": 1,
                }

        return _Session()

    monkeypatch.setattr(
        handlers_runner,
        "_runner_catalog",
        lambda: [
            {
                "id": "native",
                "label": "Native shell",
                "kind": "native",
                "available": True,
                "command": None,
                "version": None,
                "reason": None,
                "supports_prompt": False,
                "supports_terminal": True,
            },
            {
                "id": "codex-cli",
                "label": "Codex CLI",
                "kind": "external_cli",
                "available": True,
                "command": "codex",
                "version": "codex-cli 0.0.0",
                "reason": None,
                "supports_prompt": True,
                "supports_terminal": True,
            },
        ],
    )
    monkeypatch.setattr(handlers_runner, "start_terminal_command", fake_start_terminal_command)

    handler, ctx = _make_handler("runner.start")
    await handler(
        {
            "runner_id": "codex-cli",
            "command": "fix tests",
            "cwd": str(tmp_path),
            "sandbox_mode": "read-only",
            "approval_policy": "on-request",
        },
        ctx,
    )

    assert ctx.reply_error_payload is None
    assert "codex exec" in captured["command"]
    assert "--sandbox read-only" in captured["command"]
    assert "--ask-for-approval on-request" in captured["command"]
    assert "fix tests" in captured["command"]


@pytest.mark.asyncio
async def test_runner_start_rejects_invalid_safety_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        handlers_runner,
        "_runner_catalog",
        lambda: [
            {
                "id": "native",
                "label": "Native shell",
                "kind": "native",
                "available": True,
                "command": None,
                "version": None,
                "reason": None,
                "supports_prompt": False,
                "supports_terminal": True,
            },
            {
                "id": "codex-cli",
                "label": "Codex CLI",
                "kind": "external_cli",
                "available": True,
                "command": "codex",
                "version": "codex-cli 0.0.0",
                "reason": None,
                "supports_prompt": True,
                "supports_terminal": True,
            },
        ],
    )

    handler, ctx = _make_handler("runner.start")
    await handler(
        {
            "runner_id": "codex-cli",
            "command": "hello",
            "sandbox_mode": "root",
        },
        ctx,
    )

    assert ctx.reply_payload is None
    assert ctx.reply_error_payload is not None
    _code, message, _data = ctx.reply_error_payload
    assert "sandbox_mode" in message
