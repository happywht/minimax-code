"""JSON-RPC handlers for lightweight terminal command sessions.

This is deliberately not a full PTY yet.  P1.1 gives the UI a
Codex-like command runner loop: start a command, poll stdout/stderr
chunks, and stop a long-running process.  The session registry is
in-memory because terminal processes are process-local by nature.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .handler_utils import HandlerError
from .protocol import INVALID_PARAMS
from .server import Context
from ..agent.tools.terminal import (
    _SIGKILL,
    _child_spawn_kwargs,
    _signal_process_tree,
)

logger = logging.getLogger(__name__)

_TERMINAL_ERROR = -32000
_MAX_COMMAND_LEN = 4_000
_MAX_CHUNKS_PER_SESSION = 2_000
_DEFAULT_TIMEOUT_S = 10 * 60
_MAX_TIMEOUT_S = 60 * 60
_OUTPUT_TAIL_LIMIT = 1_200
_WORKSPACE_ENV_KEYS = ("MINIMAX_CODE_WORKSPACE", "MINIMAX_CODE_WORKSPACE_ROOT")
_WORKSPACE_MARKERS = ("pnpm-workspace.yaml", "AGENTS.md", ".git")


@dataclass
class TerminalChunk:
    seq: int
    stream: str
    text: str
    received_at: float


@dataclass
class TerminalSession:
    id: str
    command: str
    cwd: str
    chat_session_id: str | None = None
    run_id: str | None = None
    run_step_id: str | None = None
    status: str = "starting"
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    exit_code: int | None = None
    error: str | None = None
    process: asyncio.subprocess.Process | None = None
    task: asyncio.Task[None] | None = None
    chunks: list[TerminalChunk] = field(default_factory=list)
    next_seq: int = 1

    def append(self, stream: str, text: str) -> None:
        if not text:
            return
        self.chunks.append(
            TerminalChunk(
                seq=self.next_seq,
                stream=stream,
                text=text,
                received_at=time.time(),
            )
        )
        self.next_seq += 1
        self.updated_at = time.time()
        if len(self.chunks) > _MAX_CHUNKS_PER_SESSION:
            del self.chunks[: len(self.chunks) - _MAX_CHUNKS_PER_SESSION]

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "cwd": self.cwd,
            "session_id": self.chat_session_id,
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "exit_code": self.exit_code,
            "error": self.error,
            "next_seq": self.next_seq,
        }


_SESSIONS: dict[str, TerminalSession] = {}


def _preview_text(value: Any, *, limit: int = 600) -> str:
    try:
        import json

        text = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, default=str)
        )
    except Exception:
        text = str(value)
    text = text.replace("\r\n", "\n")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...(truncated, {len(text) - limit} chars)"


def _terminal_output_tail(session: TerminalSession, *, limit: int = _OUTPUT_TAIL_LIMIT) -> str:
    text = "".join(chunk.text for chunk in session.chunks)
    if len(text) <= limit:
        return text
    return text[-limit:]


def _require_params(params: Any) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise HandlerError(INVALID_PARAMS, "params must be a JSON object")
    return params


def _command_from_params(params: dict[str, Any]) -> str:
    command = params.get("command")
    if not isinstance(command, str) or not command.strip():
        raise HandlerError(INVALID_PARAMS, "'command' must be a non-empty string")
    command = command.strip()
    if len(command) > _MAX_COMMAND_LEN:
        raise HandlerError(INVALID_PARAMS, f"'command' must be <= {_MAX_COMMAND_LEN} chars")
    return command


def default_working_directory() -> str:
    for key in _WORKSPACE_ENV_KEYS:
        raw = os.environ.get(key)
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if path.exists() and path.is_dir():
            return str(path)

    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in _WORKSPACE_MARKERS):
            return str(candidate)
    return str(current)


def _cwd_from_params(params: dict[str, Any]) -> str:
    raw = params.get("cwd")
    if raw is None or raw == "":
        return default_working_directory()
    if not isinstance(raw, str):
        raise HandlerError(INVALID_PARAMS, "'cwd' must be a string when provided")
    path = Path(raw).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise HandlerError(INVALID_PARAMS, "'cwd' must point to an existing directory")
    return str(path)


def _timeout_from_params(params: dict[str, Any]) -> float:
    raw = params.get("timeout_s", _DEFAULT_TIMEOUT_S)
    if not isinstance(raw, (int, float)) or raw <= 0:
        raise HandlerError(INVALID_PARAMS, "'timeout_s' must be a positive number")
    return float(min(raw, _MAX_TIMEOUT_S))


def _chat_session_id_from_params(params: dict[str, Any]) -> str | None:
    raw = params.get("session_id")
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise HandlerError(INVALID_PARAMS, "'session_id' must be a string when provided")
    return raw


def _session_from_params(params: dict[str, Any]) -> TerminalSession:
    session_id = params.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise HandlerError(INVALID_PARAMS, "'session_id' must be a non-empty string")
    session = _SESSIONS.get(session_id)
    if session is None:
        raise HandlerError(INVALID_PARAMS, f"unknown terminal session: {session_id!r}")
    return session


async def _read_stream(session: TerminalSession, stream: asyncio.StreamReader, name: str) -> None:
    while True:
        data = await stream.read(4096)
        if not data:
            return
        session.append(name, data.decode("utf-8", errors="replace"))


async def _get_runs_dao() -> Any | None:
    try:
        from ..app import get_db, init_runtime
        from ..storage.dao.runs import AgentRunsDAO

        db = get_db()
        if db is None:
            try:
                await init_runtime()
            except Exception:
                logger.debug(
                    "init_runtime failed while enabling terminal run tracking",
                    exc_info=True,
                )
            db = get_db()
        return AgentRunsDAO(db) if db is not None else None
    except Exception:
        logger.debug("terminal run tracking unavailable", exc_info=True)
        return None


async def _create_run_tracking(
    session: TerminalSession,
    emit: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> None:
    if not session.chat_session_id:
        return
    dao = await _get_runs_dao()
    if dao is None:
        return
    command_preview = _preview_text(session.command, limit=80).replace("\n", " ")
    try:
        run = await dao.create_run(
            session_id=session.chat_session_id,
            mode="execute",
            status="running",
            title=f"Terminal: {command_preview}",
            metadata={
                "source": "terminal",
                "terminal_session_id": session.id,
                "command": session.command,
                "cwd": session.cwd,
            },
        )
        session.run_id = run["id"]
        await emit("run.created", {"run": run})
        step = await dao.create_step(
            run_id=session.run_id,
            session_id=session.chat_session_id,
            kind="tool_call",
            title="terminal",
            summary=_preview_text({"command": session.command, "cwd": session.cwd}, limit=300),
            tool_call_id=session.id,
            tool_name="terminal",
            payload={
                "source": "terminal",
                "terminal_session_id": session.id,
                "command": session.command,
                "cwd": session.cwd,
            },
        )
        session.run_step_id = step["id"]
        await emit("run.step.started", {"run_id": session.run_id, "step": step})
    except Exception:
        logger.exception("failed to create terminal run tracking")
        session.run_id = None
        session.run_step_id = None


async def _complete_run_tracking(
    session: TerminalSession,
    emit: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> None:
    if not session.chat_session_id or not session.run_id:
        return
    dao = await _get_runs_dao()
    if dao is None:
        return
    step_status = "completed"
    run_status = "completed"
    if session.status == "failed":
        step_status = run_status = "failed"
    elif session.status == "cancelled":
        step_status = run_status = "cancelled"
    output_tail = _terminal_output_tail(session)
    payload = {
        "source": "terminal",
        "terminal_session_id": session.id,
        "command": session.command,
        "cwd": session.cwd,
        "exit_code": session.exit_code,
        "status": session.status,
        "output_tail": output_tail,
    }
    summary = _preview_text(
        {
            "status": session.status,
            "exit_code": session.exit_code,
            "output_tail": output_tail,
        },
        limit=500,
    )
    try:
        if session.run_step_id:
            step = await dao.complete_step(
                session.run_step_id,
                status=step_status,
                summary=summary,
                payload=payload,
                error=session.error if session.status == "failed" else None,
            )
            if step is not None:
                await emit("run.step.completed", {"run_id": session.run_id, "step": step})
        run = await dao.update_run_status(
            session.run_id,
            status=run_status,
            error=session.error if session.status in {"failed", "cancelled"} else None,
            metadata={
                "source": "terminal",
                "terminal_session_id": session.id,
                "command": session.command,
                "cwd": session.cwd,
                "exit_code": session.exit_code,
                "status": session.status,
            },
        )
        if run is not None:
            await emit("run.completed", {"run": run})
    except Exception:
        logger.exception("failed to complete terminal run tracking")


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    """Stop *proc*, escalating through the whole process tree.

    v1.2.2: every signal now targets the tree (grandchildren included) —
    the old terminate→kill ladder only reached the direct child, so a
    command that spawned its own workers left them orphaned after a
    timeout or user stop. On Windows the first signal is already a
    hard tree kill (taskkill /F /T), so the escalation is a no-op.
    """
    if proc.returncode is not None:
        return
    await _signal_process_tree(proc, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
        return
    except TimeoutError:
        pass
    await _signal_process_tree(proc, _SIGKILL)
    try:
        await proc.wait()
    except Exception:  # pragma: no cover — defensive
        logger.debug("proc.wait after tree kill failed", exc_info=True)


async def _run_session(
    session: TerminalSession,
    timeout_s: float,
    emit: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> None:
    try:
        await _create_run_tracking(session, emit)
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        process = await asyncio.create_subprocess_shell(
            session.command,
            cwd=session.cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_child_spawn_kwargs(),
        )
        session.process = process
        session.status = "running"
        session.updated_at = time.time()
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task = asyncio.create_task(_read_stream(session, process.stdout, "stdout"))
        stderr_task = asyncio.create_task(_read_stream(session, process.stderr, "stderr"))
        try:
            session.exit_code = await asyncio.wait_for(process.wait(), timeout=timeout_s)
            await asyncio.gather(stdout_task, stderr_task)
            if session.status != "cancelled":
                session.status = "completed" if session.exit_code == 0 else "failed"
        except TimeoutError:
            session.status = "cancelled"
            session.error = f"command timed out after {timeout_s:g}s"
            await _terminate_process(process)
            session.exit_code = process.returncode
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        finally:
            session.completed_at = time.time()
            session.updated_at = session.completed_at
    except Exception as exc:
        session.status = "failed"
        session.error = str(exc)
        session.completed_at = time.time()
        session.updated_at = session.completed_at
    finally:
        await _complete_run_tracking(session, emit)


async def _stop_session(session: TerminalSession) -> None:
    if session.status in {"completed", "failed", "cancelled"}:
        return
    session.status = "cancelled"
    session.error = "stopped by user"
    session.updated_at = time.time()
    if session.process is not None:
        await _terminate_process(session.process)


async def start_terminal_command(
    *,
    command: str,
    cwd: str,
    timeout_s: float,
    chat_session_id: str | None,
    emit: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> TerminalSession:
    """Start a lightweight command session and return its in-memory row."""
    session = TerminalSession(
        id=f"term_{uuid.uuid4().hex[:10]}",
        command=command,
        cwd=cwd,
        chat_session_id=chat_session_id,
    )
    _SESSIONS[session.id] = session
    session.task = asyncio.create_task(_run_session(session, timeout_s, emit))
    return session


def _chunks_after(session: TerminalSession, after_seq: int) -> list[dict[str, Any]]:
    return [
        {
            "seq": chunk.seq,
            "stream": chunk.stream,
            "text": chunk.text,
            "received_at": chunk.received_at,
        }
        for chunk in session.chunks
        if chunk.seq > after_seq
    ]


def register_terminal_handlers(server: Any) -> None:
    """Register ``terminal.*`` handlers."""

    async def handle_start(params: Any, ctx: Context) -> None:
        try:
            p = _require_params(params)
            command = _command_from_params(p)
            cwd = _cwd_from_params(p)
            timeout_s = _timeout_from_params(p)
            chat_session_id = _chat_session_id_from_params(p)
            session = await start_terminal_command(
                command=command,
                cwd=cwd,
                timeout_s=timeout_s,
                chat_session_id=chat_session_id,
                emit=ctx.emit,
            )
            await ctx.reply({"session": session.to_wire()})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            await ctx.reply_error(_TERMINAL_ERROR, "terminal.start failed", {"error": str(exc)})

    async def handle_read(params: Any, ctx: Context) -> None:
        try:
            p = _require_params(params)
            session = _session_from_params(p)
            after_seq = p.get("after_seq", 0)
            if not isinstance(after_seq, int) or after_seq < 0:
                raise HandlerError(INVALID_PARAMS, "'after_seq' must be a non-negative integer")
            await ctx.reply(
                {"session": session.to_wire(), "chunks": _chunks_after(session, after_seq)}
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            await ctx.reply_error(_TERMINAL_ERROR, "terminal.read failed", {"error": str(exc)})

    async def handle_stop(params: Any, ctx: Context) -> None:
        try:
            p = _require_params(params)
            session = _session_from_params(p)
            await _stop_session(session)
            await ctx.reply({"session": session.to_wire()})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            await ctx.reply_error(_TERMINAL_ERROR, "terminal.stop failed", {"error": str(exc)})

    async def handle_list(params: Any, ctx: Context) -> None:
        try:
            sessions = sorted(_SESSIONS.values(), key=lambda item: item.started_at, reverse=True)
            await ctx.reply({"sessions": [session.to_wire() for session in sessions[:20]]})
        except Exception as exc:
            await ctx.reply_error(_TERMINAL_ERROR, "terminal.list failed", {"error": str(exc)})

    server.register("terminal.start", handle_start)
    server.register("terminal.read", handle_read)
    server.register("terminal.stop", handle_stop)
    server.register("terminal.list", handle_list)


__all__ = ["register_terminal_handlers", "start_terminal_command"]
