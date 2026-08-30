"""FastAPI application for the live-preview server.

Serves workspace files over HTTP and pushes file-change events via
SSE (Server-Sent Events).  Runs on a separate port from the main
agent (default 8766) so it can be independently started / stopped.

Routes
------
- ``GET /preview/health``       — liveness probe
- ``GET /preview/events``       — SSE stream of file-change events
- ``GET /preview/{file_path}``  — serve a workspace file (with path-traversal guard)

Since v1.7.1 the served root is mutable: :meth:`PreviewState.set_root`
(reached from the ``preview.set_root`` IPC handler) re-points the whole
surface at another project root without restarting the process.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

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

    async def set_root(self, new_root: Path) -> None:
        """Re-point the preview surface at *new_root* (v1.7.1).

        Stops the current watcher/fan-out tasks, swaps the root, and
        restarts them only if they were running (a state that was never
        started stays quiet). Any events queued against the old root are
        dropped from the main queue so subscribers do not receive stale
        paths; events already fanned out into subscriber queues may
        trigger at most one spurious iframe reload, which the frontend's
        own re-root reload supersedes. No-op when the root is unchanged.
        """
        root = Path(new_root).expanduser().resolve()
        if root == self.workspace:
            return
        was_running = (
            self._watcher_task is not None and not self._watcher_task.done()
        )
        await self.stop()
        self.workspace = root
        while not self.event_queue.empty():
            self.event_queue.get_nowait()
        if was_running:
            await self.start_watcher()
        logger.info("preview root switched: workspace=%s", root)


# ---------------------------------------------------------------------------
# Process-wide state singleton (v1.7.1)
# ---------------------------------------------------------------------------
#
# ``http_server.build_app`` creates the PreviewState bound to the HTTP app
# and publishes it here so the ``preview.set_root`` IPC handler — which is
# registered on the IPCServer and has no FastAPI handle — can reach the
# *same* instance the routes serve from. In stdio mode (no HTTP app) the
# singleton is built lazily at the process-default root so the IPC method
# still round-trips; there is simply no route serving from it.

_PREVIEW_STATE: PreviewState | None = None


def get_preview_state() -> PreviewState | None:
    """Return the process-wide preview state, or ``None`` if not yet built."""
    return _PREVIEW_STATE


def set_preview_state(state: PreviewState | None) -> None:
    """Publish *state* as the process-wide preview state (``build_app`` seam)."""
    global _PREVIEW_STATE
    _PREVIEW_STATE = state


def ensure_preview_state() -> PreviewState:
    """Return the process-wide preview state, building it once on demand.

    The lazy instance is rooted at the process-default workspace
    (``MINIMAX_CODE_WORKSPACE`` or CWD — the same authority
    :func:`minimax_code.workspace_ctx.env_or_cwd_root` defines). No watcher
    is started here; that only happens from the HTTP lifespan or the SSE
    endpoint, so a lazily built state in stdio mode costs one object.
    """
    global _PREVIEW_STATE
    if _PREVIEW_STATE is None:
        from ..workspace_ctx import env_or_cwd_root

        _PREVIEW_STATE = PreviewState(env_or_cwd_root())
    return _PREVIEW_STATE


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
    """Register preview routes on an existing FastAPI application.

    *workspace* is only the *initial* root: the routes read the live root
    from ``state.workspace`` on every request (v1.7.1), so
    :meth:`PreviewState.set_root` re-points the whole surface at runtime.
    """
    state = state or PreviewState(workspace)
    app.state.preview = state

    # ---- GET /preview/health -------------------------------------------

    @app.get("/preview/health")
    async def preview_health() -> dict[str, Any]:
        return {"ok": True, "workspace": str(state.workspace)}

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
                    except TimeoutError:
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
        safe = _safe_path(state.workspace, file_path)
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
            # Live preview: never let the browser serve a stale document from
            # heuristic caching. Without this, an iframe navigation to the
            # same URL (e.g. after a project re-root on the same file path)
            # can be satisfied from cache and keep rendering the previous
            # project's content indefinitely.
            headers={"Cache-Control": "no-store"},
        )

    return state
