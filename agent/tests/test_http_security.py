"""Authentication failures must happen before any RPC/tool dispatch."""

import io

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from minimax_code.config import Config
from minimax_code.http_server import build_app
from minimax_code.ipc.server import IPCServer


@pytest.fixture
def secured_app(isolated_http_runtime):
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=io.StringIO())
    return build_app(server)


def test_rpc_and_websocket_require_authentication(secured_app):
    with TestClient(secured_app) as client:
        assert client.post("/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
        with pytest.raises(WebSocketDisconnect) as error, client.websocket_connect("/ws"):
            pass
        assert error.value.code == 4401
        assert client.get("/health").json()["auth_required"] is True


@pytest.mark.parametrize("path", ["/rpc", "/complete", "/preview/file.txt", "/preview/events", "/attachments/file"])
def test_file_and_rpc_endpoints_share_auth_boundary(secured_app, path):
    with TestClient(secured_app) as client:
        assert client.get(path).status_code == 401


def test_trusted_browser_bootstrap_sets_http_only_cookie(secured_app):
    with TestClient(secured_app, client=("127.0.0.1", 50000)) as client:
        assert client.post("/auth/session").status_code == 401
        response = client.post("/auth/session", headers={"Origin": "http://testserver", "X-Minimax-Client": "web"})
        assert response.status_code == 200
        assert "httponly" in response.headers["set-cookie"].lower()
        assert "samesite=strict" in response.headers["set-cookie"].lower()
        assert response.headers["cache-control"] == "no-store"
        assert client.post("/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 200


def test_agent_and_preview_ports_keep_independent_cookie_sessions(secured_app, tmp_path):
    from minimax_code.preview.server import build_preview_app

    (tmp_path / "index.html").write_text("preview fixture")
    preview = build_preview_app(tmp_path)
    headers = {"Origin": "http://localhost:5173", "X-Minimax-Client": "web"}
    with (TestClient(secured_app, base_url="http://testserver:8765", client=("127.0.0.1", 1)) as main,
          TestClient(preview, base_url="http://testserver:8766", client=("127.0.0.1", 2)) as files):
        main_auth = main.post("/auth/session", headers=headers)
        preview_auth = files.post("/auth/session", headers=headers)
        assert main_auth.status_code == preview_auth.status_code == 200
        assert "minimax_session_8765=" in main_auth.headers["set-cookie"]
        assert "minimax_session_8766=" in preview_auth.headers["set-cookie"]
        cookies = "; ".join(response.headers["set-cookie"].split(";", 1)[0]
                            for response in (main_auth, preview_auth))
        assert main.post("/rpc", headers={"Cookie": cookies},
                         json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 200
        assert files.get("/preview/index.html", headers={"Cookie": cookies}).status_code == 200
        assert files.get("/health").json()["auth_required"] is True


@pytest.mark.parametrize("host", ["localhost:bad-port", "localhost:99999", "user@localhost"])
def test_invalid_host_syntax_is_rejected_before_cookie_parsing(secured_app, host):
    with TestClient(secured_app) as client:
        assert client.get("/health", headers={"Host": host}).status_code == 400


def test_remote_client_cannot_self_issue_token_even_with_trusted_origin(secured_app):
    with TestClient(secured_app, client=("192.0.2.10", 50000)) as client:
        response = client.post("/auth/session", headers={
            "Origin": "http://testserver", "X-Minimax-Client": "web",
        })
        assert response.status_code == 401


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
def test_untrusted_origin_cannot_bootstrap_or_use_valid_token(secured_app, origin):
    headers = {"Origin": origin, "X-Minimax-Client": "web",
               "Authorization": f"Bearer {secured_app.state.auth.token}"}
    with TestClient(secured_app, headers=headers) as client:
        assert client.post("/auth/session").status_code == 403
        assert client.post("/rpc", json={}).status_code == 403
        with pytest.raises(WebSocketDisconnect) as error, client.websocket_connect("/ws"):
            pass
        assert error.value.code == 4403


def test_host_rebinding_rejected_even_with_valid_token(secured_app):
    with TestClient(secured_app, headers={"Host": "evil.example", "Authorization": f"Bearer {secured_app.state.auth.token}"}) as client:
        assert client.get("/health").status_code == 400
        assert client.post("/rpc", json={}).status_code == 400


def test_websocket_accepts_token_protocol_without_query_secret(secured_app):
    with TestClient(secured_app) as client:
        with client.websocket_connect("/ws", subprotocols=[f"minimax-auth.{secured_app.state.auth.token}"]) as ws:
            assert ws.accepted_subprotocol == f"minimax-auth.{secured_app.state.auth.token}"
            assert ws.receive_json()["method"] == "agent.ready"
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(f"/ws?token={secured_app.state.auth.token}"):
            pass


def test_preview_disallows_sensitive_paths_and_sandboxes_documents(tmp_path):
    from minimax_code.preview.server import build_preview_app

    (tmp_path / "index.html").write_text("<script>fetch('/rpc')</script>", encoding="utf-8")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "key").write_text("private fixture", encoding="utf-8")
    app = build_preview_app(tmp_path)
    with TestClient(app, headers={"Authorization": f"Bearer {app.state.auth.token}"}) as client:
        assert client.get("/preview/.ssh/key").status_code == 403
        result = client.get("/preview/index.html")
        assert result.status_code == 200
        assert "sandbox allow-scripts;" in result.headers["content-security-policy"]
        assert "allow-same-origin" not in result.headers["content-security-policy"]


def test_preview_uses_session_workspace_and_rejects_invalid_binding(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from minimax_code import app as runtime
    from minimax_code.preview.server import build_preview_app

    default, project = tmp_path / "service", tmp_path / "project"
    default.mkdir()
    project.mkdir()
    (default / "index.html").write_text("service file", encoding="utf-8")
    target = project / "index.html"
    target.write_text("project file", encoding="utf-8")

    async def get_session(sid):
        return {"id": sid, "workspace_path": str(project)} if sid == "bound" else None

    monkeypatch.setattr(runtime, "get_sessions_dao", lambda: SimpleNamespace(get=get_session))
    app = build_preview_app(default)
    with TestClient(app, headers={"Authorization": f"Bearer {app.state.auth.token}"}) as client:
        assert client.get("/preview/session/bound/index.html").text == "project file"
        assert client.get("/preview/session/deleted/index.html").status_code == 409
        target.unlink()
        project.rmdir()
        assert client.get("/preview/session/bound/index.html").status_code == 409
        assert client.get("/preview/index.html").text == "service file"
