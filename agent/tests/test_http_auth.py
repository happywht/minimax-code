"""Token-auth regression suite for the HTTP/WS bridge (v1.8.0).

``MINIMAX_CODE_HTTP_TOKEN`` set → every capability surface rejects
unauthenticated requests; unset → behaviour is byte-for-byte the
pre-v1.8.0 localhost deployment.

Coverage
--------
- auth off: /rpc, /ws, /preview untouched; ``/health`` reports ``auth=false``.
- auth on: /rpc, /complete, /preview/* require the token (Bearer header or
  ``?token=`` query); wrong token 401; ``/health``, ``/hooks/*`` and the
  SPA mount stay anonymous.
- preview cookie channel: a successful query-token response seeds the
  ``minimax_token`` cookie so the iframe's relative asset requests pass.
- /ws: upgrade rejected without a token (header form accepted too).
"""

from __future__ import annotations

import io
import warnings
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport

from minimax_code.config import Config
from minimax_code.http_server import (
    AUTH_COOKIE_NAME,
    AUTH_ENV_VAR,
    build_app,
)
from minimax_code.ipc.protocol import Response
from minimax_code.ipc.server import IPCServer

warnings.filterwarnings(
    "ignore",
    message="Using `httpx`.*deprecated.*",
    category=DeprecationWarning,
)

TOKEN = "sekret-token-1"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ipc_server() -> IPCServer:
    return IPCServer(
        config=Config.from_env(),
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A preview workspace with one servable file, wired via env."""
    (tmp_path / "index.html").write_text(
        "<html><body>preview</body></html>", encoding="utf-8"
    )
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    return tmp_path


@pytest.fixture
def app(ipc_server: IPCServer, workspace: Path) -> FastAPI:
    from minimax_code.app import register_app_handlers

    register_app_handlers(ipc_server)
    return build_app(ipc_server, version="1.8.0-test")


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # TestClient (not bare httpx) so the WS endpoints are exercisable.
    return TestClient(app)


def _asgi(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Auth off — zero-behaviour-change guarantee
# ---------------------------------------------------------------------------


async def test_auth_off_rpc_open(app: FastAPI) -> None:
    async with _asgi(app) as http:
        resp = await http.post("/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 200
    assert resp.json()["result"]["server"] == "minimax-code-agent"


def test_auth_off_health_reports_disabled(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["auth"] is False


def test_auth_off_ws_connects(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        frame = ws.receive_json()
    assert frame["method"] == "agent.ready"


def test_auth_off_preview_open(client: TestClient) -> None:
    resp = client.get("/preview/index.html")
    assert resp.status_code == 200
    assert "preview" in resp.text


# ---------------------------------------------------------------------------
# Auth on — protected surfaces
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_ENV_VAR, TOKEN)


def test_auth_on_health_reports_enabled(auth_on, client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["auth"] is True


def test_auth_on_rpc_without_token_401(auth_on, client: TestClient) -> None:
    resp = client.post("/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"
    assert resp.json()["error"] == "unauthorized"


def test_auth_on_rpc_wrong_token_401(auth_on, client: TestClient) -> None:
    resp = client.post(
        "/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


def test_auth_on_rpc_bearer_ok(auth_on, client: TestClient) -> None:
    resp = client.post(
        "/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["server"] == "minimax-code-agent"


def test_auth_on_rpc_query_token_ok(auth_on, client: TestClient) -> None:
    resp = client.post("/rpc?token=" + TOKEN, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 200
    assert resp.json()["result"]["server"] == "minimax-code-agent"


async def test_auth_on_rpc_401_carries_cors_headers(
    auth_on, app: FastAPI
) -> None:
    # The browser must be able to *see* the 401 cross-origin (dev mode:
    # Vite :5173 → agent :8765). CORS sits outside the auth middleware, so
    # the rejection still carries the allow-origin header.
    async with _asgi(app) as http:
        resp = await http.post(
            "/rpc",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Origin": "http://localhost:5173"},
        )
    assert resp.status_code == 401
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


async def test_auth_on_cors_preflight_passes(auth_on, app: FastAPI) -> None:
    # Preflight OPTIONS carries no Authorization header by design — it must
    # never be rejected by the token gate.
    async with _asgi(app) as http:
        resp = await http.options(
            "/rpc",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization, content-type",
            },
        )
    assert resp.status_code == 200


def test_auth_on_complete_protected(auth_on, client: TestClient) -> None:
    resp = client.post("/complete", json={})
    assert resp.status_code == 401


def test_auth_on_preview_requires_token(auth_on, client: TestClient) -> None:
    resp = client.get("/preview/index.html")
    assert resp.status_code == 401
    resp = client.get("/preview/health")
    assert resp.status_code == 401


def test_auth_on_preview_query_token_seeds_cookie(
    auth_on, client: TestClient
) -> None:
    resp = client.get("/preview/index.html?token=" + TOKEN)
    assert resp.status_code == 200
    set_cookie = resp.headers.get("set-cookie", "")
    assert AUTH_COOKIE_NAME in set_cookie
    assert "Path=/preview" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Strict" in set_cookie


def test_auth_on_preview_cookie_channel(
    auth_on, client: TestClient
) -> None:
    # Simulate the iframe flow: first hit carries ?token= and seeds the
    # cookie; the follow-up relative asset request carries *only* the
    # cookie — no token in the URL.
    first = client.get("/preview/index.html?token=" + TOKEN)
    assert first.status_code == 200
    second = client.get("/preview/index.html")  # cookies persist in TestClient
    assert second.status_code == 200


def test_auth_on_hooks_stay_anonymous(auth_on, client: TestClient) -> None:
    # Inbound webhooks authenticate by their own HMAC secret; the token
    # gate must not shadow that (senders cannot carry the app token).
    # (The exact body depends on runtime state — "no db" in this bare-app
    # fixture — what matters is that the request reached the webhook
    # handler rather than the 401 gate.)
    resp = client.post("/hooks/does-not-exist", json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_auth_on_spa_static_anonymous(auth_on, client: TestClient) -> None:
    # The browser must be able to load the SPA before any token exists.
    resp = client.get("/")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth on — WebSocket gate
# ---------------------------------------------------------------------------


def test_auth_on_ws_without_token_rejected(auth_on, client: TestClient) -> None:
    with pytest.raises(Exception):
        with client.websocket_connect("/ws"):
            pass  # pragma: no cover — the connect itself must fail


def test_auth_on_ws_query_token_ok(auth_on, client: TestClient) -> None:
    with client.websocket_connect("/ws?token=" + TOKEN) as ws:
        frame = ws.receive_json()
    assert frame["method"] == "agent.ready"


def test_auth_on_ws_header_token_ok(auth_on, client: TestClient) -> None:
    with client.websocket_connect(
        "/ws", headers={"Authorization": f"Bearer {TOKEN}"}
    ) as ws:
        frame = ws.receive_json()
    assert frame["method"] == "agent.ready"


def test_auth_on_ws_wrong_token_rejected(auth_on, client: TestClient) -> None:
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=wrong"):
            pass  # pragma: no cover


# ---------------------------------------------------------------------------
# Response envelope sanity
# ---------------------------------------------------------------------------


def test_401_body_is_plain_error_not_rpc_envelope(auth_on, client: TestClient) -> None:
    """The 401 is a transport-level rejection, not a JSON-RPC error frame.

    ``Response`` envelopes are reserved for *handler* failures; an
    unauthenticated caller never reaches the registry. Assert the body is
    the plain ``{"error", "hint"}`` shape so clients can branch on it.
    """
    resp = client.post("/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401
    body = resp.json()
    assert set(body) == {"error", "hint"}
    # And it is NOT a valid RPC Response envelope (no jsonrpc/result/error trio).
    with pytest.raises(Exception):
        Response.model_validate(body)
