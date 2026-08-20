"""Tests for the FastAPI HTTP + WebSocket bridge (v0.2.0).

The tests run against the ASGI app in-process — no real socket,
no port collision with concurrent runs. ``httpx.AsyncClient`` is
wired up with ``ASGITransport`` so we exercise the same
serialisation path the browser / curl would, but on a transient
loop owned by the test.

WebSocket coverage uses FastAPI's :class:`TestClient` (which
wraps Starlette's WebSocket test transport). ``httpx`` itself
does not yet ship a WebSocket client, and the ``httpx_ws`` /
``httpx2`` ecosystem is still settling — ``TestClient`` is the
idiomatic FastAPI choice. We suppress the upstream
``starlette.testclient`` deprecation warning because the
``TestClient`` API itself is stable.

Coverage
--------
- ``POST /rpc`` — success envelope, error envelope (unknown method),
  parse error (malformed JSON), invalid request (non-object body).
- ``GET  /health`` — liveness shape: ``{ok, version, uptime_s}``.
- ``GET  /ws`` — receives ``agent.ready`` on connect, and a handler
  that calls ``ctx.emit`` produces a real WS frame in the spec's
  ``{method, params}`` shape.
- ``GET  /ws`` — listener is unregistered when the last client
  disconnects (no leak across tests).

A fixture clears any listeners the previous test may have left
behind on the shared :class:`IPCServer` instance, so a
listener-leak in one test cannot make the next one flaky.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import warnings
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport

from minimax_code.app import register_app_handlers
from minimax_code.config import Config
from minimax_code.http_server import build_app
from minimax_code.ipc.protocol import (
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    Response,
)
from minimax_code.ipc.server import IPCServer

# starlette.testclient warns about the underlying httpx version;
# the TestClient API we use is stable. Silence the upstream noise
# so the test output stays clean.
warnings.filterwarnings(
    "ignore",
    message="Using `httpx`.*deprecated.*",
    category=DeprecationWarning,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ipc_server() -> IPCServer:
    """A fresh :class:`IPCServer` wired up with the full app handlers.

    Uses in-memory stdin/stdout so nothing leaks into the test
    runner's real I/O. We *do not* call ``register_defaults`` a
    second time — the constructor already does that. The
    application handlers come from :func:`register_app_handlers`.
    """
    return IPCServer(
        config=Config.from_env(),
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )


@pytest.fixture
def app_with_handlers(ipc_server: IPCServer) -> FastAPI:
    """A FastAPI app bound to a handler-registered server.

    Registers the app handlers on the shared server, then builds
    the HTTP app. Cleans up any WS listeners the app may have
    registered when the fixture is torn down.
    """
    register_app_handlers(ipc_server)
    app = build_app(ipc_server, version="0.2.0-test")
    yield app
    # Belt-and-braces — make sure no listener leaks between tests.
    ipc_server.listeners.clear()


@pytest.fixture
async def client(app_with_handlers: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An ``httpx.AsyncClient`` wired to the ASGI app in-process."""
    transport = ASGITransport(app=app_with_handlers)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_ok(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["version"] == "0.2.0-test"
    # ``uptime_s`` is an int per the contract; freshly booted
    # agent should report 0 or 1.
    assert isinstance(body["uptime_s"], int)
    assert body["uptime_s"] >= 0


@pytest.mark.asyncio
async def test_health_uptime_advances(
    client: httpx.AsyncClient,
) -> None:
    r1 = await client.get("/health")
    time.sleep(1.05)  # one full second so the int can move
    r2 = await client.get("/health")
    assert r2.json()["uptime_s"] >= r1.json()["uptime_s"] + 1


@pytest.mark.asyncio
async def test_main_app_serves_preview_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ipc_server: IPCServer,
) -> None:
    (tmp_path / "index.html").write_text("<h1>Integrated preview</h1>", encoding="utf-8")
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    app = build_app(ipc_server, version="0.2.0-test")
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        health = await c.get("/preview/health")
        preview = await c.get("/preview/index.html")

    assert health.status_code == 200
    assert health.json()["workspace"] == str(tmp_path)
    assert preview.status_code == 200
    assert "Integrated preview" in preview.text


# ---------------------------------------------------------------------------
# POST /rpc — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rpc_ping_round_trip(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/rpc",
        content=json.dumps(
            {"jsonrpc": "2.0", "id": "1", "method": "ping", "params": {}}
        ),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == "1"
    assert "result" in body
    assert body["result"]["server"] == "minimax-code-agent"
    assert body["result"]["uptime_s"] >= 0


@pytest.mark.asyncio
async def test_rpc_status(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/rpc",
        content=json.dumps(
            {"jsonrpc": "2.0", "id": "s1", "method": "status", "params": {}}
        ),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "s1"
    assert body["result"]["agent"] == "minimax-code-agent"
    assert body["result"]["version"]


# ---------------------------------------------------------------------------
# POST /rpc — error envelopes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rpc_unknown_method_returns_error(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/rpc",
        content=json.dumps(
            {"jsonrpc": "2.0", "id": "u1", "method": "does.not.exist", "params": {}}
        ),
        headers={"Content-Type": "application/json"},
    )
    # HTTP is always 200; the failure mode is in the JSON-RPC
    # ``error`` field. This matches the architecture doc.
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "u1"
    assert "error" in body
    assert body["error"]["code"] == METHOD_NOT_FOUND
    assert "does.not.exist" in body["error"]["message"]


@pytest.mark.asyncio
async def test_rpc_malformed_json_returns_parse_error(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post(
        "/rpc",
        content="{not json}",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 0
    assert body["error"]["code"] == PARSE_ERROR


@pytest.mark.asyncio
async def test_rpc_non_object_body_returns_invalid_request(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post(
        "/rpc",
        content=json.dumps([1, 2, 3]),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"]["code"] == -32600  # INVALID_REQUEST


@pytest.mark.asyncio
async def test_rpc_empty_body_returns_parse_error(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post(
        "/rpc", content="", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"]["code"] == PARSE_ERROR


# ---------------------------------------------------------------------------
# GET /ws — readiness + event fan-out
# ---------------------------------------------------------------------------


def test_ws_receives_agent_ready(app_with_handlers: FastAPI) -> None:
    """A fresh WS connection must receive ``agent.ready`` immediately.

    The lifecycle event is per-client, sent directly by the WS
    handler — it is *not* routed through the IPCServer listener.
    """
    with TestClient(app_with_handlers) as c:
        with c.websocket_connect("/ws") as ws:
            # Send a no-op text frame so the server's receive
            # loop wakes up; the agent.ready push was already
            # buffered before our first receive call.
            ws.send_text("client-hello")
            first = ws.receive_json()
        assert first["jsonrpc"] == "2.0"
        assert first["method"] == "agent.ready"
        assert first["params"]["server"] == "minimax-code-agent"
        assert first["params"]["version"] == "0.2.0-test"


def test_ws_fans_out_handler_emits(
    app_with_handlers: FastAPI,
    ipc_server: IPCServer,
) -> None:
    """A handler that calls ``ctx.emit`` must produce a WS frame.

    The bridge translates the internal Event shape
    ``{event, data}`` to the wire shape ``{method, params}`` so
    the web client sees a single consistent envelope.
    """

    # Register a one-shot handler that emits an event then replies.
    async def emit_handler(params: Any, ctx: Any) -> None:
        await ctx.emit("agent.test_event", {"hello": "world", "n": 42})
        await ctx.reply({"ok": True})

    ipc_server.register("test.emit_event", emit_handler)

    with TestClient(app_with_handlers) as c:
        with c.websocket_connect("/ws") as ws:
            # Drain the lifecycle frame first.
            ws.send_text("ping")
            ready = ws.receive_json()
            assert ready["method"] == "agent.ready"

            # Drive the handler via HTTP while the WS is open.
            # TestClient's WebSocket support uses a synchronous
            # style; we hand-roll a tiny HTTP request via the
            # same client.
            rpc = c.post(
                "/rpc",
                content=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "t1",
                        "method": "test.emit_event",
                        "params": {},
                    }
                ),
                headers={"Content-Type": "application/json"},
            )
            assert rpc.status_code == 200
            assert rpc.json()["result"] == {"ok": True}

            # The WS should now have the emitted event in the
            # canonical wire shape. ``receive_json`` is a
            # blocking sync call; under the hood it polls the
            # ASGI transport's event queue.
            evt = ws.receive_json()
            assert evt["jsonrpc"] == "2.0"
            assert evt["method"] == "agent.test_event"
            assert evt["params"] == {"hello": "world", "n": 42}
            # No ``event`` / ``data`` keys leak into the wire format.
            assert "event" not in evt
            assert "data" not in evt


@pytest.mark.asyncio
async def test_ws_listener_unregistered_after_last_disconnect(
    app_with_handlers: FastAPI,
    ipc_server: IPCServer,
) -> None:
    """The server must not leak listeners when WS clients drop.

    We open and close one socket, then assert the server's
    listener list is empty. The next test that opens a socket
    would otherwise see double deliveries.
    """
    assert ipc_server.listeners == []

    # ``TestClient.websocket_connect`` is a sync context
    # manager. We run it on the running event loop via
    # ``asyncio.to_thread`` so the rest of the test can stay
    # async and we can poll the listener list afterward.
    def open_and_close() -> None:
        with TestClient(app_with_handlers) as c:
            with c.websocket_connect("/ws") as ws:
                ws.send_text("ping")
                ready = ws.receive_json()
                assert ready["method"] == "agent.ready"
                # While the socket is open a listener is registered.
                assert len(ipc_server.listeners) == 1

    await asyncio.to_thread(open_and_close)

    # Give the server a tick to run its ``finally`` block.
    for _ in range(40):
        if not ipc_server.listeners:
            break
        await asyncio.sleep(0.05)
    assert ipc_server.listeners == []


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cors_allows_vite_origin(client: httpx.AsyncClient) -> None:
    r = await client.get(
        "/health",
        headers={"Origin": "http://localhost:5173"},
    )
    assert r.status_code == 200
    # FastAPI's CORSMiddleware echoes the allowed origin back in
    # the ``Access-Control-Allow-Origin`` header.
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


@pytest.mark.asyncio
async def test_cors_allows_127_origin(client: httpx.AsyncClient) -> None:
    r = await client.get(
        "/health",
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"


@pytest.mark.asyncio
async def test_cors_allows_explicit_additional_origin(
    monkeypatch: pytest.MonkeyPatch,
    ipc_server: IPCServer,
) -> None:
    monkeypatch.setenv("MINIMAX_CODE_CORS_ORIGINS", "http://127.0.0.1:15173")
    app = build_app(ipc_server, version="cors-test")
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as custom:
        response = await custom.get(
            "/health",
            headers={"Origin": "http://127.0.0.1:15173"},
        )
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:15173"


# ---------------------------------------------------------------------------
# Sanity: response shape (canonical JSON-RPC 2.0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rpc_response_is_canonical_envelope(
    client: httpx.AsyncClient,
) -> None:
    """The /rpc response must round-trip through ``Response`` cleanly.

    This guards against drift: if someone re-shapes the HTTP
    response, the parsing in the web client breaks. The contract
    is the Pydantic model in ``ipc.protocol``.
    """
    r = await client.post(
        "/rpc",
        content=json.dumps(
            {"jsonrpc": "2.0", "id": 7, "method": "ping", "params": {}}
        ),
        headers={"Content-Type": "application/json"},
    )
    body = r.json()
    # Validate the envelope shape with the existing Pydantic model.
    parsed = Response.model_validate(body)
    assert parsed.id == 7
    assert parsed.result is not None
    assert "pong" in parsed.result


# ---------------------------------------------------------------------------
# Production single-process mode (StaticFiles mount of web/dist)
# ---------------------------------------------------------------------------


def test_serves_web_dist_from_env_override(
    ipc_server: IPCServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``MINIMAX_CODE_WEB_DIST`` pins which directory the SPA mounts from.

    The env override takes precedence over the repo-default
    ``web/dist`` probe, so the mounted shell is exactly the one the
    operator pointed at — deterministic regardless of whether the
    local checkout happens to carry a built dist.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><title>prod-shell</title><div id='root'></div>",
        encoding="utf-8",
    )
    monkeypatch.setenv("MINIMAX_CODE_WEB_DIST", str(dist))

    app = build_app(ipc_server, version="web-dist-test")
    with TestClient(app) as c:
        resp = c.get("/")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "prod-shell" in resp.text

        # API routes keep precedence over the catch-all mount.
        assert c.get("/health").json()["ok"] is True


def test_web_dist_env_override_without_index_html_is_ignored(
    ipc_server: IPCServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured directory lacking index.html never 500s the mount.

    ``_web_dist_dir`` validates the candidate before mounting; a bad
    override silently falls back to the repo default (or no mount).
    Either way ``build_app`` must not raise.
    """
    empty = tmp_path / "not-a-dist"
    empty.mkdir()
    monkeypatch.setenv("MINIMAX_CODE_WEB_DIST", str(empty))

    app = build_app(ipc_server, version="web-dist-bad-test")
    with TestClient(app) as c:
        # The agent API still answers regardless of the mount decision.
        assert c.get("/health").json()["ok"] is True
