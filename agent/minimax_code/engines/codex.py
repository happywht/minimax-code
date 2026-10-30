"""CodexEngine — the Codex CLI's App Server as a pluggable engine (M4).

The Codex CLI (``codex app-server``) speaks line-delimited JSON-RPC
over stdio — the same envelope family as MiniMax Code's own IPC. This
module is the adapter:

- **probe** — ``codex --version`` decides availability; failures carry
  a reason so the UI can disable the engine explicitly (never a silent
  fallback).
- **AppServerConnection** — spawn / initialize handshake / thread
  lifecycle (``thread/start``, ``thread/resume``, ``turn/start``,
  ``turn/interrupt``) with server-pushed notifications and
  *server-originated requests* (approvals) surfaced through callbacks.

Protocol version pin: replay tests follow the 0.163.0 schemas generated
with ``codex app-server generate-json-schema``. Other versions need
explicit contract validation before this adapter enables them.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import re
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..execution import (
    _finish_owned,
    _spawn_managed_exec,
    _validated_process_cwd,
    communicate_bounded,
    process_environment,
    terminate_tree,
)
from .base import EngineInfo

logger = logging.getLogger(__name__)

# Bump only together with the protocol replay fixtures.
SUPPORTED_PROTOCOL_PIN = "codex-cli-0.163.0"
_CAPABILITIES = {"streaming": True, "resume": True, "cancel": True,
                 "approvals": True, "usage": True, "steer": True}


class UnsupportedServerRequest:
    """Signal that a server request has no implemented response mapping."""


class ServerRequestError:
    """Return a protocol error rather than inventing a successful response."""

    def __init__(self, code: int, message: str) -> None:
        self.code, self.message = code, message


class ServerRequestResolved:
    """The server has already resolved this request; do not reply again."""


def _codex_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build a child environment from trusted service configuration only.

    Stored CLI login keeps HOME/USERPROFILE and CODEX_HOME. Provider auth,
    proxy and CA variables require explicit server-side name selection. Never
    infer credential names from project config, or accept them from tool args.
    """
    selected: dict[str, str] = {}
    raw = os.environ.get("MINIMAX_CODE_CODEX_ENV_VARS", "[]")
    try:
        names = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid trusted MINIMAX_CODE_CODEX_ENV_VARS configuration") from exc
    if (not isinstance(names, list) or len(names) > 64
            or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,255}", name)
                   for name in names)):
        raise ValueError("Invalid trusted MINIMAX_CODE_CODEX_ENV_VARS configuration")
    for name in ["CODEX_HOME", *names]:
        if name in os.environ:
            selected[name] = os.environ[name]
    selected.update(extra or {})
    return process_environment(selected)


async def _launch_codex(argv: list[str], *, cwd: Path | None = None,
                        extra_env: Mapping[str, str] | None = None) -> asyncio.subprocess.Process:
    """Trusted adapter startup, independent of model-command write permission.

    The per-turn Codex sandbox still governs plan/execute behavior. This
    internal capability only launches the server-selected engine command.
    """
    return await _spawn_managed_exec(
        *argv, cwd=_validated_process_cwd(cwd), env=_codex_environment(extra_env),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )


def _probe_codex(timeout_s: float = 5.0) -> EngineInfo:
    """Compatibility shim for synchronous callers; production uses async probe."""
    return asyncio.run(_probe_codex_async(timeout_s))


async def _probe_codex_async(timeout_s: float = 5.0) -> EngineInfo:
    """Probe the pinned CLI through the same owned process boundary."""
    exe = shutil.which("codex")
    if exe is None:
        return EngineInfo(
            id="codex",
            name="Codex (OpenAI Codex CLI)",
            available=False,
            unavailable_reason="codex CLI not found on PATH — install it (npm i -g @openai/codex) to enable this engine",
            capabilities=dict(_CAPABILITIES),
        )
    failure_reason = None
    try:
        proc = await _launch_codex([exe, "--version"])
        output = await communicate_bounded(proc, timeout=timeout_s, limit=4096)
        version = output.stdout.decode("utf-8", errors="replace").strip() or None
        expected = SUPPORTED_PROTOCOL_PIN.replace("codex-cli-", "codex-cli ", 1)
        if proc.returncode != 0:
            failure_reason = "codex --version exited unsuccessfully"
            raise RuntimeError(f"version probe exited with code {proc.returncode}")
        if version != expected:
            failure_reason = f"Unsupported Codex CLI version; expected {expected}"
            raise RuntimeError(f"unsupported Codex CLI version; expected {expected}")
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        return EngineInfo(
            id="codex",
            name="Codex (OpenAI Codex CLI)",
            available=False,
            # Child output and configuration values may contain credentials.
            unavailable_reason=failure_reason or f"codex --version failed ({type(exc).__name__})",
            capabilities=dict(_CAPABILITIES),
        )
    return EngineInfo(
        id="codex",
        name="Codex (OpenAI Codex CLI)",
        available=True,
        version=version,
        capabilities=dict(_CAPABILITIES),
    )


class AppServerConnection:
    """One ``codex app-server`` child process speaking stdio JSON-RPC.

    Requests are matched by id; server-pushed notifications go to
    ``on_notification``; *server-originated requests* (approval
    prompts) go to ``on_server_request`` whose return value is sent
    back as the response. Both callbacks must be fast — dispatch the
    heavy work elsewhere.
    """

    def __init__(self, *, cwd: Path | None = None,
                 codex_cmd: list[str] | None = None,
                 extra_env: Mapping[str, str] | None = None) -> None:
        self._cwd = cwd
        # Full argv so tests can script a fake server (exec semantics:
        # no shell quoting — argv lists only).
        self._codex_cmd = codex_cmd or ["codex", "app-server"]
        # Trusted embedding/server configuration, never JSON-RPC tool input.
        self._extra_env = extra_env
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[Any, asyncio.Future[Any]] = {}
        self._ids = itertools.count(1)
        self._reader_task: asyncio.Task[None] | None = None
        self._exit_task: asyncio.Task[None] | None = None
        self._start_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self.on_notification: Callable[[dict[str, Any]], Any] | None = None
        self.on_server_request: Callable[[dict[str, Any]], Any] | None = None
        self.on_exited: Callable[[], None] | None = None
        # Background tasks for async server-request replies (strong refs).
        self._bg: set[asyncio.Task[None]] = set()
        self.closed = False

    async def start(self) -> None:
        if self.closed:
            raise RuntimeError("Codex connection is closed")
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start_owned())
        try:
            await asyncio.shield(self._start_task)
        except BaseException:
            await _finish_owned(self._begin_close())
            raise

    async def _start_owned(self) -> None:
        self._proc = await _launch_codex(self._codex_cmd, cwd=self._cwd,
                                          extra_env=self._extra_env)
        if self.closed:
            return  # The retained close task will reap this recovered handle.
        self._reader_task = asyncio.create_task(self._read_loop(),
                                               name="codex-app-server-reader")
        self._exit_task = asyncio.create_task(self._watch_exit(),
                                             name="codex-app-server-exit")

    async def _watch_exit(self) -> None:
        assert self._proc is not None
        # Process.wait can await inherited pipe EOF after the leader exits.
        # Observe the transport's exit status independently of those handles.
        while self._proc.returncode is None:
            await asyncio.sleep(0.05)
        self._fail_pending("codex app-server process exited")
        self._unexpected_exit()

    def _fail_pending(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(reason))

    def _unexpected_exit(self) -> None:
        if not self.closed:
            self._begin_close()
            if self.on_exited is not None:
                self.on_exited()

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        reason = "codex app-server process exited"
        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break
                obj = json.loads(raw.decode("utf-8").strip())
                if not isinstance(obj, dict):
                    raise ValueError("Codex JSON-RPC message must be an object")
                self._dispatch(obj)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = f"Codex protocol failure ({type(exc).__name__})"
        finally:
            self._fail_pending(reason)
            self._unexpected_exit()

    def _dispatch(self, obj: dict[str, Any]) -> None:
        if "id" in obj and ("method" not in obj):
            # A response to one of our requests.
            fut = self._pending.pop(obj["id"], None)
            if fut is not None and not fut.done():
                if "error" in obj:
                    fut.set_exception(RuntimeError(str(obj["error"])))
                else:
                    fut.set_result(obj.get("result"))
            return
        if "method" in obj and "id" in obj:
            # A server-originated REQUEST — approval prompts live here.
            # The handler may return the response params directly, or a
            # coroutine/future that resolves to them (approval waits on
            # a human). The reply is written back when it settles.
            handler = self.on_server_request
            if handler is None:
                self._send_error(obj["id"], -32601, "Server request is not supported")
                return
            result = handler(obj)
            if isinstance(result, UnsupportedServerRequest):
                self._send_error(obj["id"], -32601, "Server request is not supported")
                return
            if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                async def _settle(awaitable: Any, req_id: Any, request_method: str) -> None:
                    try:
                        params = await awaitable
                    except Exception:
                        logger.exception("server request handler failed; replying denied")
                        params = (ServerRequestError(-32603, "Input request failed")
                                  if request_method == "item/tool/requestUserInput" else {})
                    if isinstance(params, ServerRequestResolved):
                        return
                    if isinstance(params, ServerRequestError):
                        self._send_error(req_id, params.code, params.message)
                    else:
                        self._send_response(req_id, params or {})

                task = asyncio.create_task(_settle(result, obj["id"], obj["method"]))
                self._bg.add(task)
                task.add_done_callback(self._bg.discard)
            else:
                self._send_response(obj["id"], result or {})
            return
        if "method" in obj:
            handler = self.on_notification
            if handler is not None:
                handler(obj)

    def _send_response(self, req_id: Any, params: dict[str, Any]) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        line = json.dumps({"jsonrpc": "2.0", "id": req_id,
                           "result": params}, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))

    def _send_error(self, req_id: Any, code: int, message: str) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        line = json.dumps({"jsonrpc": "2.0", "id": req_id,
                           "error": {"code": code, "message": message}}, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))

    async def request(self, method: str, params: dict[str, Any] | None = None,
                      timeout: float = 30.0) -> Any:
        assert self._proc is not None and self._proc.stdin is not None
        req_id = next(self._ids)
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        line = json.dumps({"jsonrpc": "2.0", "id": req_id,
                           "method": method, "params": params or {}},
                          ensure_ascii=False) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))
        await self._proc.stdin.drain()
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        line = json.dumps({"jsonrpc": "2.0", "method": method,
                           "params": params or {}}, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))

    async def initialize(self, client_name: str = "minimax-code",
                         client_version: str = "1.0") -> Any:
        result = await self.request("initialize", {
            "clientInfo": {"name": client_name, "version": client_version},
            "capabilities": {"experimentalApi": True},
        }, timeout=30.0)
        self.notify("initialized", {})
        return result

    async def close(self) -> None:
        closing = self._begin_close()
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            await _finish_owned(closing)
            raise

    def _begin_close(self) -> asyncio.Task[None]:
        self.closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_owned(), name="codex-app-server-close")
        return self._close_task

    async def _close_owned(self) -> None:
        if self._start_task is not None:
            # A pending OS spawn owns a handle even before it returns. Retain
            # that task, then reap the handle before releasing the connection.
            await asyncio.gather(self._start_task, return_exceptions=True)
        pending_tasks = list(self._bg)
        for task in pending_tasks:
            task.cancel()
        if self._proc is not None and self._proc.stdin is not None:
            self._proc.stdin.close()
        if self._reader_task is not None:
            self._reader_task.cancel()
            pending_tasks.append(self._reader_task)
        if self._exit_task is not None:
            self._exit_task.cancel()
            pending_tasks.append(self._exit_task)
        if self._proc is not None:
            await terminate_tree(self._proc)
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()


class CodexEngine:
    """Adapter around the Codex CLI App Server."""

    id = "codex"

    async def info(self) -> EngineInfo:
        return await _probe_codex_async()

    @staticmethod
    async def connect(*, cwd: Path | None = None,
                      codex_cmd: list[str] | None = None,
                      extra_env: Mapping[str, str] | None = None) -> AppServerConnection:
        """Spawn an app-server child and complete the initialize handshake."""
        conn = AppServerConnection(cwd=cwd, codex_cmd=codex_cmd, extra_env=extra_env)
        try:
            await conn.start()
            await conn.initialize()
        except BaseException:
            await _finish_owned(conn._begin_close())
            raise
        return conn


__all__ = ["CodexEngine", "AppServerConnection", "SUPPORTED_PROTOCOL_PIN"]
