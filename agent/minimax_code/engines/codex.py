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
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import EngineInfo

logger = logging.getLogger(__name__)

# Bump only together with the protocol replay fixtures.
SUPPORTED_PROTOCOL_PIN = "codex-cli-0.163.0"
_CAPABILITIES = {"streaming": True, "resume": True, "cancel": True,
                 "approvals": True, "usage": True, "steer": False}


def _probe_codex(timeout_s: float = 5.0) -> EngineInfo:
    """Return the Codex engine's availability card (blocking probe)."""
    exe = shutil.which("codex")
    if exe is None:
        return EngineInfo(
            id="codex",
            name="Codex (OpenAI Codex CLI)",
            available=False,
            unavailable_reason="codex CLI not found on PATH — install it (npm i -g @openai/codex) to enable this engine",
            capabilities=dict(_CAPABILITIES),
        )
    try:
        proc = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        version = proc.stdout.decode("utf-8", errors="replace").strip() or None
        expected = SUPPORTED_PROTOCOL_PIN.replace("codex-cli-", "codex-cli ", 1)
        if proc.returncode != 0:
            raise RuntimeError(f"version probe exited with code {proc.returncode}")
        if version != expected:
            raise RuntimeError(f"unsupported version {version!r}; expected {expected}")
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        return EngineInfo(
            id="codex",
            name="Codex (OpenAI Codex CLI)",
            available=False,
            unavailable_reason=f"codex --version failed: {exc}",
            capabilities=dict(_CAPABILITIES),
        )
    return EngineInfo(
        id="codex",
        name="Codex (OpenAI Codex CLI)",
        available=True,
        version=version,
        capabilities=dict(_CAPABILITIES),
    )


async def _probe_codex_async(timeout_s: float = 5.0) -> EngineInfo:
    """Event-loop-safe probe: the blocking version runs in a thread."""
    import asyncio

    return await asyncio.to_thread(_probe_codex, timeout_s)


class AppServerConnection:
    """One ``codex app-server`` child process speaking stdio JSON-RPC.

    Requests are matched by id; server-pushed notifications go to
    ``on_notification``; *server-originated requests* (approval
    prompts) go to ``on_server_request`` whose return value is sent
    back as the response. Both callbacks must be fast — dispatch the
    heavy work elsewhere.
    """

    def __init__(self, *, cwd: Path | None = None,
                 codex_cmd: list[str] | None = None) -> None:
        self._cwd = cwd
        # Full argv so tests can script a fake server (exec semantics:
        # no shell quoting — argv lists only).
        self._codex_cmd = codex_cmd or ["codex", "app-server"]
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[Any, asyncio.Future[Any]] = {}
        self._ids = itertools.count(1)
        self._reader_task: asyncio.Task[None] | None = None
        self.on_notification: Callable[[dict[str, Any]], Any] | None = None
        self.on_server_request: Callable[[dict[str, Any]], Any] | None = None
        self.on_exited: Callable[[], None] | None = None
        # Background tasks for async server-request replies (strong refs).
        self._bg: set[asyncio.Task[None]] = set()
        self.closed = False

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._codex_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(self._cwd) if self._cwd else None,
        )
        self._reader_task = asyncio.create_task(self._read_loop(),
                                               name="codex-app-server-reader")

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
            reason = f"Codex protocol failure: {exc}"
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError(reason))
            if not self.closed and self.on_exited is not None:
                self.on_exited()

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
                self._send_response(obj["id"], {})
                return
            result = handler(obj)
            if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                async def _settle(awaitable: Any, req_id: Any) -> None:
                    try:
                        params = await awaitable
                    except Exception:
                        logger.exception("server request handler failed; replying denied")
                        params = {}
                    self._send_response(req_id, params or {})

                task = asyncio.create_task(_settle(result, obj["id"]))
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
        }, timeout=30.0)
        self.notify("initialized", {})
        return result

    async def close(self) -> None:
        self.closed = True
        pending_tasks = list(self._bg)
        for task in pending_tasks:
            task.cancel()
        if self._proc is not None and self._proc.stdin is not None:
            self._proc.stdin.close()
        if self._reader_task is not None:
            self._reader_task.cancel()
            pending_tasks.append(self._reader_task)
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        if self._proc is not None:
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()


class CodexEngine:
    """Adapter around the Codex CLI App Server."""

    id = "codex"

    async def info(self) -> EngineInfo:
        return await _probe_codex_async()

    @staticmethod
    async def connect(*, cwd: Path | None = None,
                      codex_cmd: list[str] | None = None) -> AppServerConnection:
        """Spawn an app-server child and complete the initialize handshake."""
        conn = AppServerConnection(cwd=cwd, codex_cmd=codex_cmd)
        await conn.start()
        await conn.initialize()
        return conn


__all__ = ["CodexEngine", "AppServerConnection", "SUPPORTED_PROTOCOL_PIN"]
