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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .watcher import watch_workspace, WatcherEvent

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
        self._subscribers: list[asyncio.Queue[WatcherEvent]] = []

    async def start_watcher(self) -> None:
        """Start the background file-system watcher."""
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
            dead: list[asyncio.Queue[WatcherEvent]] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)

    def subscribe(self) -> asyncio.Queue[WatcherEvent]:
        """Create a new subscriber queue."""
        q: asyncio.Queue[WatcherEvent] = asyncio.Queue(maxsize=64)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[WatcherEvent]) -> None:
        """Remove a subscriber queue."""
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def stop(self) -> None:
        """Cancel watcher and fan-out tasks."""
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


def _safe_path(workspace: Path, file_path: str) -> Path | None:
    """Resolve *file_path* under *workspace*, returning ``None`` if it escapes."""
    # Normalise and resolve to an absolute path.
    resolved = (workspace / file_path).resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError:
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

    # Add CORS middleware so the Vite SPA can connect.
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET"],
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

    # ---- GET /preview/health -------------------------------------------

    @app.get("/preview/health")
    async def preview_health() -> dict[str, Any]:
        return {"ok": True, "workspace": str(workspace)}

    # ---- GET /preview/events -------------------------------------------

    @app.get("/preview/events")
    async def preview_events(request: Request) -> StreamingResponse:
        """SSE endpoint — streams file-change events to the client."""

        await state.start_watcher()

        async def event_stream() -> AsyncIterator[str]:
            q = state.subscribe()
            try:
                while True:
                    # Use a timeout so we can check for disconnects.
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        # Send a keepalive comment.
                        yield ": keepalive\n\n"
                        continue
                    data = json.dumps(event.to_dict())
                    yield f"data: {data}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                state.unsubscribe(q)

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

    @app.get("/preview/{file_path:path}", response_model=None)
    async def preview_file(file_path: str) -> FileResponse | JSONResponse:
        """Serve a file from the workspace."""
        safe = _safe_path(workspace, file_path)
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
        )

    return state
