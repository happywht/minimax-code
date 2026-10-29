"""FastAPI application for the live-preview server.

Serves workspace files over HTTP and pushes file-change events via
SSE (Server-Sent Events).  Runs on a separate port from the main
agent (default 8766) so it can be independently started / stopped.

Routes
------
- ``GET /preview/health``       — liveness probe
- ``GET /preview/events``       — SSE stream of file-change events
- ``GET /preview/{file_path}``  — serve a workspace file (with path-traversal guard)
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ..ipc.protocol import PreviewAccessResult
from .watcher import WatcherEvent, watch_workspace

logger = logging.getLogger(__name__)

# CORS — same origin as the main agent (Vite dev server).
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


class PreviewState:
    """Shared state for the preview server."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.event_queue: asyncio.Queue[WatcherEvent] = asyncio.Queue(maxsize=256)
        self._watcher_task: asyncio.Task[None] | None = None
        self._fanout_task: asyncio.Task[None] | None = None
        self._subscribers: list[asyncio.Queue[WatcherEvent | None]] = []
        self.children: dict[Path, PreviewState] = {}
        self._maintenance_closed = False

    async def start_watcher(self) -> None:
        """Start the background file-system watcher."""
        if self._maintenance_closed:
            raise RuntimeError("Preview is closed for maintenance")
        if self._watcher_task is not None and not self._watcher_task.done():
            return
        self._watcher_task = asyncio.create_task(
            watch_workspace(self.workspace, self.event_queue),
            name="preview-watcher",
        )
        # Fan-out task: reads from the main queue and pushes to subscribers.
        self._fanout_task = asyncio.create_task(self._fanout(), name="preview-fanout")

    async def _fanout(self) -> None:
        """Read events from the main queue and fan out to subscribers."""
        while True:
            try:
                event = await self.event_queue.get()
            except asyncio.CancelledError:
                return
            dead: list[asyncio.Queue[WatcherEvent | None]] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)

    def subscribe(self) -> asyncio.Queue[WatcherEvent | None]:
        """Create a new subscriber queue."""
        q: asyncio.Queue[WatcherEvent | None] = asyncio.Queue(maxsize=64)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[WatcherEvent | None]) -> None:
        """Remove a subscriber queue."""
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def stop(self) -> None:
        """Cancel watcher and fan-out tasks."""
        for child in list(self.children.values()):
            await child.stop()
        self.children.clear()
        tasks = [task for task in (self._watcher_task, self._fanout_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._watcher_task = None
        self._fanout_task = None

    async def close_for_maintenance(self) -> None:
        """Wake streams and permanently prevent new watchers in this process."""
        self._maintenance_closed = True
        for child in list(self.children.values()):
            child._maintenance_closed = True
        for active in [self, *self.children.values()]:
            for queue in list(active._subscribers):
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
        await self.stop()


def _safe_path(workspace: Path, file_path: str) -> Path | None:
    """Resolve *file_path* under *workspace*, returning ``None`` if it escapes."""
    # Normalise and resolve to an absolute path.
    if not workspace.is_dir():
        return None
    from ..agent.tools.file_ops import PathSecurityError, safe_resolve

    try:
        resolved = safe_resolve(file_path, workspace=workspace)
    except (ValueError, PathSecurityError):
        # Path traversal attempt — reject.
        return None
    return resolved


def _guess_content_type(path: Path) -> str:
    """Return a MIME type for *path*."""
    ct, _ = mimetypes.guess_type(str(path))
    return ct or "application/octet-stream"


def build_preview_app(workspace: Path) -> FastAPI:
    """Build the preview FastAPI application.

    Parameters
    ----------
    workspace:
        Root directory to serve files from and watch for changes.
    """
    state = PreviewState(workspace)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await state.start_watcher()
        logger.info("preview server started: workspace=%s", workspace)
        try:
            yield
        finally:
            await state.stop()

    app = FastAPI(
        title="minimax-code-preview",
        version="0.6.0",
        lifespan=lifespan,
    )

    from ..http_security import install_application_auth

    install_application_auth(app, CORS_ORIGINS)

    @app.get("/health")
    async def health():
        return {"ok": True, "auth_required": True}

    # Add CORS middleware so the Vite SPA can connect.
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    register_preview_routes(app, workspace, state=state)

    return app


def register_preview_routes(
    app: FastAPI,
    workspace: Path,
    *,
    state: PreviewState | None = None,
) -> PreviewState:
    """Register preview routes on an existing FastAPI application."""
    state = state or PreviewState(workspace)
    app.state.preview = state

    async def session_workspace(session_id: str | None) -> Path:
        from ..workspace_ctx import (
            WorkspaceUnavailableError,
            require_directory,
            resolve_root_for_session,
        )

        try:
            return await resolve_root_for_session(session_id) if session_id else require_directory(workspace)
        except WorkspaceUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # ---- GET /preview/health -------------------------------------------

    @app.get("/preview/health")
    @app.get("/preview/session/{session_id}/health")
    async def preview_health(session_id: str | None = None) -> dict[str, Any]:
        return {"ok": True, "workspace": str(await session_workspace(session_id))}

    # ---- GET /preview/events -------------------------------------------

    @app.get("/preview/events")
    @app.get("/preview/session/{session_id}/events")
    async def preview_events(request: Request, session_id: str | None = None) -> StreamingResponse:
        """SSE endpoint — streams file-change events to the client."""

        root = await session_workspace(session_id)
        if state._maintenance_closed:
            raise HTTPException(status_code=503, detail="Service is in maintenance")
        active = state
        if root != state.workspace:
            if root not in state.children:
                if len(state.children) >= 32:
                    idle_root = next((path for path, item in state.children.items()
                                      if not item._subscribers), None)
                    if idle_root is None:
                        raise HTTPException(status_code=429, detail="Too many active previews")
                    idle = state.children.pop(idle_root)
                    await idle.stop()
                    if state._maintenance_closed:
                        raise HTTPException(status_code=503, detail="Service is in maintenance")
                state.children[root] = PreviewState(root)
            active = state.children[root]
        await active.start_watcher()

        async def event_stream() -> AsyncIterator[str]:
            q = active.subscribe()
            try:
                while True:
                    if state._maintenance_closed:
                        break
                    # Use a timeout so we can check for disconnects.
                    if await request.is_disconnected():
                        break
                    if request.url.path.startswith("/preview/view/"):
                        record = request.app.state.auth.preview_grant(request.url.path)
                        if record is None:
                            break
                        try:
                            if await session_workspace(record["session_id"]) != root:
                                break
                        except HTTPException:
                            break
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=15.0)
                    except TimeoutError:
                        # Send a keepalive comment.
                        yield ": keepalive\n\n"
                        continue
                    if event is None:
                        break
                    data = json.dumps(event.to_dict())
                    yield f"data: {data}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                active.unsubscribe(q)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ---- GET /preview/{file_path} --------------------------------------

    async def grant_workspace(request: Request, grant: str) -> Path:
        record = request.app.state.auth.preview_grant(f"/preview/view/{grant}/file")
        if record is None:
            raise HTTPException(401, "Preview access expired; reload the preview")
        root = await session_workspace(record["session_id"])
        if str(root) != record["workspace"]:
            raise HTTPException(409, "Preview workspace binding changed; reload the preview")
        return root

    @app.post("/preview/access")
    async def preview_access(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "Expected a JSON object") from exc
        if not isinstance(body, dict) or set(body) - {"session_id"}:
            raise HTTPException(400, "Expected an optional session_id")
        session_id = body.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise HTTPException(400, "session_id must be a string")
        root = await session_workspace(session_id)
        try:
            grant = request.app.state.auth.issue_preview_grant(str(root), session_id)
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc
        remaining = max(0, int(request.app.state.auth.preview_grants[grant]["expires"] - time.monotonic()))
        result = PreviewAccessResult(path=f"/preview/view/{grant}", expires_in=remaining)
        return JSONResponse(result.model_dump(),
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @app.get("/preview/view/{grant}/events")
    async def granted_events(request: Request, grant: str):
        # Resolve the binding before opening a read-only event stream.
        await grant_workspace(request, grant)
        record = request.app.state.auth.preview_grant(request.url.path)
        assert record is not None
        return await preview_events(request, record["session_id"])

    @app.get("/preview/{file_path:path}", response_model=None)
    @app.get("/preview/session/{session_id}/{file_path:path}", response_model=None)
    @app.get("/preview/view/{grant}/{file_path:path}", response_model=None)
    async def preview_file(request: Request, file_path: str, session_id: str | None = None,
                           grant: str | None = None) -> FileResponse | JSONResponse:
        """Serve a file from the workspace."""
        root = await grant_workspace(request, grant) if grant else await session_workspace(session_id)
        safe = _safe_path(root, file_path)
        if safe is None:
            return JSONResponse(
                content={"error": "path traversal denied"},
                status_code=403,
            )
        if not safe.exists() or not safe.is_file():
            return JSONResponse(
                content={"error": "not found", "path": file_path},
                status_code=404,
            )
        return FileResponse(
            safe,
            media_type=_guess_content_type(safe),
            headers={
                "Content-Security-Policy": "sandbox allow-scripts; default-src 'self' data: blob:; connect-src 'none'; form-action 'none'",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                **({"Access-Control-Allow-Origin": "null"} if grant else {}),
            },
        )

    return state
