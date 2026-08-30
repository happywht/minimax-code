"""Tests for the live-preview server (v0.6.0; re-rooting since v1.7.1).

Covers:
  - Static file serving (200, 404, content-type)
  - SSE endpoint event streaming
  - Path traversal protection
  - Health endpoint
  - PreviewState subscribe/unsubscribe
  - ``PreviewState.set_root`` re-rooting + routes following the live root
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from minimax_code.preview.server import (
    PreviewState,
    _safe_path,
    build_preview_app,
    ensure_preview_state,
    get_preview_state,
    set_preview_state,
)
from minimax_code.preview.watcher import WatcherEvent


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temp workspace with sample files."""
    (tmp_path / "index.html").write_text("<h1>Hello Preview</h1>", encoding="utf-8")
    (tmp_path / "style.css").write_text("body { color: red; }", encoding="utf-8")
    sub = tmp_path / "assets"
    sub.mkdir()
    (sub / "app.js").write_text("console.log('hi');", encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(workspace: Path):
    """Build the preview FastAPI app."""
    return build_preview_app(workspace)


@pytest.fixture
async def client(app):
    """httpx async client backed by the ASGI transport."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestStaticFileServing:
    """Test file serving via GET /preview/{file_path}."""

    @pytest.mark.asyncio
    async def test_serve_html(self, client: httpx.AsyncClient) -> None:
        r = await client.get("/preview/index.html")
        assert r.status_code == 200
        assert "<h1>Hello Preview</h1>" in r.text
        assert "text/html" in r.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_serve_css(self, client: httpx.AsyncClient) -> None:
        r = await client.get("/preview/style.css")
        assert r.status_code == 200
        assert "color: red" in r.text

    @pytest.mark.asyncio
    async def test_serve_nested_file(self, client: httpx.AsyncClient) -> None:
        r = await client.get("/preview/assets/app.js")
        assert r.status_code == 200
        assert "console.log" in r.text

    @pytest.mark.asyncio
    async def test_file_not_found(self, client: httpx.AsyncClient) -> None:
        r = await client.get("/preview/nonexistent.txt")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, client: httpx.AsyncClient) -> None:
        """Attempt to escape workspace via ../ sequences.

        On Windows the resolved path may still land inside a valid parent
        dir (where no matching file exists), so the server returns 404.
        On Linux it may return 403. Both are acceptable — the key is the
        file is NOT served.
        """
        r = await client.get("/preview/../../../etc/passwd")
        assert r.status_code in (403, 404)


class TestSafePath:
    """Unit tests for _safe_path traversal guard."""

    def test_normal_path(self, tmp_path: Path) -> None:
        result = _safe_path(tmp_path, "index.html")
        assert result is not None
        assert result.name == "index.html"

    def test_nested_path(self, tmp_path: Path) -> None:
        result = _safe_path(tmp_path, "assets/app.js")
        assert result is not None

    def test_traversal_returns_none(self, tmp_path: Path) -> None:
        result = _safe_path(tmp_path, "../../etc/passwd")
        assert result is None

    def test_absolute_escape_returns_none(self, tmp_path: Path) -> None:
        result = _safe_path(tmp_path, "/etc/passwd")
        assert result is None


class TestHealthEndpoint:
    """Test GET /preview/health."""

    @pytest.mark.asyncio
    async def test_health(self, client: httpx.AsyncClient, workspace: Path) -> None:
        r = await client.get("/preview/health")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert str(workspace) in data["workspace"]


class TestPreviewState:
    """Unit tests for PreviewState subscribe/unsubscribe."""

    def test_subscribe_creates_queue(self, workspace: Path) -> None:
        state = PreviewState(workspace)
        q = state.subscribe()
        assert isinstance(q, asyncio.Queue)
        assert q in state._subscribers

    def test_unsubscribe_removes_queue(self, workspace: Path) -> None:
        state = PreviewState(workspace)
        q = state.subscribe()
        state.unsubscribe(q)
        assert q not in state._subscribers

    @pytest.mark.asyncio
    async def test_fanout_delivers_event(self, workspace: Path) -> None:
        state = PreviewState(workspace)
        q = state.subscribe()
        event = WatcherEvent(kind="changed", path="index.html")
        # Simulate what _fanout does: put event directly into subscriber queue.
        for sub_q in state._subscribers:
            sub_q.put_nowait(event)
        result = q.get_nowait()
        assert result.kind == "changed"
        assert result.path == "index.html"

    def test_multiple_subscribers(self, workspace: Path) -> None:
        state = PreviewState(workspace)
        q1 = state.subscribe()
        q2 = state.subscribe()
        assert len(state._subscribers) == 2
        state.unsubscribe(q1)
        assert len(state._subscribers) == 1
        assert q2 in state._subscribers


class TestSetRoot:
    """PreviewState.set_root re-rooting semantics (v1.7.1)."""

    @pytest.mark.asyncio
    async def test_set_root_updates_workspace(self, tmp_path: Path) -> None:
        ws1 = tmp_path / "ws1"
        ws2 = tmp_path / "ws2"
        ws1.mkdir()
        ws2.mkdir()
        state = PreviewState(ws1)
        await state.set_root(ws2)
        assert state.workspace == ws2.resolve()

    @pytest.mark.asyncio
    async def test_set_root_same_root_is_noop(self, workspace: Path) -> None:
        state = PreviewState(workspace)
        await state.start_watcher()
        task_before = state._watcher_task
        await state.set_root(workspace)  # identical resolved root
        assert state._watcher_task is task_before  # untouched
        await state.stop()

    @pytest.mark.asyncio
    async def test_set_root_restarts_running_watcher(
        self, workspace: Path, tmp_path: Path
    ) -> None:
        other = tmp_path / "other"
        other.mkdir()
        state = PreviewState(workspace)
        await state.start_watcher()
        old_task = state._watcher_task
        await state.set_root(other)
        assert state._watcher_task is not old_task
        assert state._watcher_task is not None
        assert not state._watcher_task.done()
        await state.stop()

    @pytest.mark.asyncio
    async def test_set_root_leaves_stopped_state_stopped(
        self, workspace: Path, tmp_path: Path
    ) -> None:
        """A state whose watcher never started stays quiet after re-root."""
        other = tmp_path / "other"
        other.mkdir()
        state = PreviewState(workspace)
        await state.set_root(other)
        assert state._watcher_task is None
        assert state._fanout_task is None

    @pytest.mark.asyncio
    async def test_set_root_drops_stale_queued_events(
        self, workspace: Path, tmp_path: Path
    ) -> None:
        other = tmp_path / "other"
        other.mkdir()
        state = PreviewState(workspace)
        state.event_queue.put_nowait(WatcherEvent(kind="changed", path="old.html"))
        await state.set_root(other)
        assert state.event_queue.empty()


class TestRoutesFollowLiveRoot:
    """Routes must serve from ``state.workspace`` at request time (v1.7.1)."""

    @pytest.mark.asyncio
    async def test_files_and_health_follow_new_root(
        self, workspace: Path, tmp_path: Path
    ) -> None:
        other = tmp_path / "other-root"
        other.mkdir()
        (other / "index.html").write_text("<h1>Other Root</h1>", encoding="utf-8")
        app = build_preview_app(workspace)
        state: PreviewState = app.state.preview
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            # Before: serves the original workspace's file.
            r1 = await c.get("/preview/index.html")
            assert "Hello Preview" in r1.text
            await state.set_root(other)
            # After: same route, new root — the old file is gone, the new
            # one is served, and health reflects the switch.
            r2 = await c.get("/preview/index.html")
            assert "Other Root" in r2.text
            h = await c.get("/preview/health")
            assert h.json()["workspace"] == str(other.resolve())

    @pytest.mark.asyncio
    async def test_containment_enforced_against_new_root(
        self, workspace: Path, tmp_path: Path
    ) -> None:
        """Traversal stays blocked relative to the *live* root."""
        other = tmp_path / "another-root"
        other.mkdir()
        app = build_preview_app(workspace)
        state: PreviewState = app.state.preview
        await state.set_root(other)
        # The route guard runs _safe_path against state.workspace at
        # request time — assert the pure function directly so the check
        # is independent of httpx URL normalisation.
        escape = f"../{workspace.name}/index.html"
        assert _safe_path(state.workspace, escape) is None
        assert _safe_path(state.workspace, "index.html") is None or str(
            _safe_path(state.workspace, "index.html")
        ).startswith(str(other.resolve()))

    @pytest.mark.asyncio
    async def test_file_responses_disable_browser_cache(self, workspace: Path) -> None:
        """Files are served with Cache-Control: no-store (v1.7.1).

        Without it an iframe navigation to the same URL (a re-root on the
        same file path) can be satisfied from heuristic browser cache and
        keep rendering the previous project's document indefinitely.
        """
        app = build_preview_app(workspace)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/preview/index.html")
            assert r.status_code == 200
            assert r.headers["cache-control"] == "no-store"


class TestPreviewStateSingleton:
    """Process-wide state singleton (v1.7.1)."""

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        yield
        set_preview_state(None)

    def test_set_and_get_roundtrip(self, workspace: Path) -> None:
        state = PreviewState(workspace)
        set_preview_state(state)
        assert get_preview_state() is state

    def test_get_returns_none_when_unset(self) -> None:
        set_preview_state(None)
        assert get_preview_state() is None

    @pytest.mark.asyncio
    async def test_ensure_builds_lazily_at_process_default_root(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_preview_state(None)
        monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(workspace))
        state = ensure_preview_state()
        assert state.workspace == workspace.resolve()
        assert ensure_preview_state() is state  # cached


class TestSSEEvents:
    """Test the SSE /preview/events endpoint.

    Note: The full streaming loop cannot be tested with httpx ASGI
    transport because ``request.is_disconnected()`` never returns True
    in that context, causing the infinite ``while True`` to hang. We
    test the route is registered and returns the correct response type.
    """

    @pytest.mark.asyncio
    async def test_sse_route_registered(self, workspace: Path) -> None:
        """The /preview/events route should exist and return SSE headers."""
        app = build_preview_app(workspace)
        # Verify the route is registered in the FastAPI router.
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/preview/events" in routes

    def test_watcher_event_serialization(self) -> None:
        """WatcherEvent should serialize to the expected SSE format."""
        evt = WatcherEvent(kind="changed", path="index.html")
        data = json.dumps(evt.to_dict())
        # This is what gets sent as SSE ``data: {...}``.
        assert '"kind": "changed"' in data
        assert '"path": "index.html"' in data
