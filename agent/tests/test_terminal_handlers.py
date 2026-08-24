"""Tests for lightweight ``terminal.*`` IPC command sessions."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from minimax_code.app import set_projects_dao
from minimax_code.config import Config
from minimax_code.ipc import handlers_terminal
from minimax_code.ipc.handlers_terminal import default_working_directory, register_terminal_handlers
from minimax_code.ipc.protocol import INVALID_PARAMS
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path


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


def _make_handler(method: str) -> tuple[Callable[..., Coroutine[Any, Any, None]], _CapturingContext]:
    server = IPCServer(config=Config.from_env(), stdin=sys.stdin, stdout=sys.stdout)
    register_terminal_handlers(server)
    return server._handlers[method], _CapturingContext()  # type: ignore[return-value]


def _python_command(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


def test_default_working_directory_prefers_workspace_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))

    assert default_working_directory() == str(tmp_path.resolve())


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


async def _await_session_task(session_id: str) -> None:
    session = handlers_terminal._SESSIONS[session_id]  # type: ignore[attr-defined]
    if session.task is not None:
        await session.task


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
    await _await_session_task(session["id"])

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
    await _await_session_task(session_id)
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
    await _await_session_task(session_id)

    assert stop_ctx.reply_error_payload is None
    assert stop_ctx.reply_payload["session"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_terminal_session_tracks_run_timeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from minimax_code import app as app_module

    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    monkeypatch.setattr(app_module, "_DB_SINGLETON", db)
    try:
        chat_session_id = f"ses_{uuid4_hex()}"
        await SessionsDAO(db).create(id=chat_session_id, title="terminal timeline")

        start, ctx = _make_handler("terminal.start")
        await start(
            {
                "command": _python_command("print('timeline terminal')"),
                "cwd": str(tmp_path),
                "timeout_s": 5,
                "session_id": chat_session_id,
            },
            ctx,
        )

        assert ctx.reply_error_payload is None
        session_id = ctx.reply_payload["session"]["id"]
        payload = await _read_until_done(session_id)
        await _await_session_task(session_id)

        assert payload["session"]["session_id"] == chat_session_id
        assert payload["session"]["run_id"]
        event_names = [event for event, _data in ctx.events]
        assert event_names == [
            "run.created",
            "run.step.started",
            "run.step.completed",
            "run.completed",
        ]

        dao = AgentRunsDAO(db)
        run = await dao.get_run(payload["session"]["run_id"])
        assert run is not None
        assert run["mode"] == "execute"
        assert run["status"] == "completed"
        assert run["metadata"]["source"] == "terminal"
        steps = await dao.list_steps(run["id"])
        assert steps[0]["kind"] == "tool_call"
        assert steps[0]["status"] == "completed"
        assert "timeline terminal" in steps[0]["payload"]["output_tail"]
    finally:
        monkeypatch.setattr(app_module, "_DB_SINGLETON", None)
        await db.close()


def uuid4_hex() -> str:
    import uuid

    return uuid.uuid4().hex[:10]


# ---------------------------------------------------------------------------
# v1.3.0 — project-scoped cwd (strict isolation)
# ---------------------------------------------------------------------------


@pytest.fixture
async def rooted_dirs(tmp_path: Path):
    """A rooted project (``proj_rooted`` → ``root/``) plus a rootless sibling."""
    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    dao = ProjectsDAO(db)
    await dao.ensure_inbox()

    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    await dao.create(id="proj_rooted", name="Rooted", root_path=str(root))
    await dao.create(id="proj_rootless", name="Rootless")
    set_projects_dao(dao)
    try:
        yield root, outside
    finally:
        set_projects_dao(None)
        await db.close()


@pytest.mark.asyncio
async def test_cwd_default_anchors_at_project_root(rooted_dirs) -> None:
    root, _ = rooted_dirs
    cwd = await handlers_terminal._cwd_from_params({"project_id": "proj_rooted"})
    assert cwd == str(root.resolve())


@pytest.mark.asyncio
async def test_cwd_inside_root_allowed(rooted_dirs) -> None:
    root, _ = rooted_dirs
    cwd = await handlers_terminal._cwd_from_params(
        {"project_id": "proj_rooted", "cwd": str(root / "sub")}
    )
    assert cwd == str((root / "sub").resolve())


@pytest.mark.asyncio
async def test_relative_cwd_resolves_against_root(rooted_dirs) -> None:
    root, _ = rooted_dirs
    cwd = await handlers_terminal._cwd_from_params(
        {"project_id": "proj_rooted", "cwd": "sub"}
    )
    assert cwd == str((root / "sub").resolve())


@pytest.mark.asyncio
async def test_cwd_outside_root_rejected(rooted_dirs) -> None:
    _, outside = rooted_dirs
    with pytest.raises(handlers_terminal.HandlerError) as excinfo:
        await handlers_terminal._cwd_from_params(
            {"project_id": "proj_rooted", "cwd": str(outside)}
        )
    assert excinfo.value.code == INVALID_PARAMS
    assert "outside the project root" in excinfo.value.message


@pytest.mark.asyncio
async def test_rootless_project_keeps_legacy_default(
    rooted_dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MINIMAX_CODE_WORKSPACE", raising=False)
    monkeypatch.delenv("MINIMAX_CODE_WORKSPACE_ROOT", raising=False)
    cwd = await handlers_terminal._cwd_from_params({"project_id": "proj_rootless"})
    assert cwd == default_working_directory()


@pytest.mark.asyncio
async def test_nonexistent_cwd_rejected(tmp_path: Path) -> None:
    with pytest.raises(handlers_terminal.HandlerError) as excinfo:
        await handlers_terminal._cwd_from_params({"cwd": str(tmp_path / "nope")})
    assert excinfo.value.code == INVALID_PARAMS
    assert "existing directory" in excinfo.value.message


@pytest.mark.asyncio
async def test_terminal_start_uses_project_root(rooted_dirs) -> None:
    root, _ = rooted_dirs
    start, ctx = _make_handler("terminal.start")
    await start(
        {
            "command": _python_command("print('rooted terminal')"),
            "project_id": "proj_rooted",
            "timeout_s": 5,
        },
        ctx,
    )
    assert ctx.reply_error_payload is None
    session = ctx.reply_payload["session"]
    assert session["cwd"] == str(root.resolve())
    payload = await _read_until_done(session["id"])
    await _await_session_task(session["id"])
    assert payload["session"]["status"] == "completed"
