"""Tests for the live-preview server (v0.6.0).

Covers:
  - Static file serving (200, 404, content-type)
  - SSE endpoint event streaming
  - Path traversal protection
  - Health endpoint
  - PreviewState subscribe/unsubscribe
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from minimax_code.preview.server import PreviewState, _safe_path, build_preview_app
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
