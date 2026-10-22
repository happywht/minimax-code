"""Opaque iframe resources get bounded read access, never an application token."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from minimax_code.preview.server import build_preview_app


def test_preview_grant_reads_assets_but_cannot_issue_grants_or_call_rpc(tmp_path):
    (tmp_path / "style.css").write_text("body {color: green}")
    app = build_preview_app(tmp_path)
    with TestClient(app) as client:
        assert client.post("/preview/access", json={}).status_code == 401
        access = client.post("/preview/access", json={}, headers={
            "Authorization": f"Bearer {app.state.auth.token}",
        })
        assert access.status_code == 200
        assert access.headers["cache-control"] == "no-store"
        base = access.json()["path"]
        grant = base.rsplit("/", 1)[1]
        assert grant != app.state.auth.token
        assert 0 < access.json()["expires_in"] <= 900
        asset = client.get(base + "/style.css", headers={"Origin": "null"})
        assert asset.status_code == 200
        assert asset.headers["access-control-allow-origin"] == "null"
        assert "sandbox allow-scripts" in asset.headers["content-security-policy"]
        assert "allow-same-origin" not in asset.headers["content-security-policy"]
        assert client.get("/preview/style.css").status_code == 401
        assert client.post("/rpc", headers={"Authorization": f"Bearer {grant}"}, json={}).status_code == 401
        assert client.post("/preview/access", headers={"Authorization": f"Bearer {grant}"}, json={}).status_code == 401
        assert client.post(base + "/style.css").status_code == 401
        assert client.get(base + "/%2e%2e/%2e%2e/escape").status_code == 403
        app.state.auth.preview_grants[grant]["expires"] = 0
        assert client.get(base + "/style.css").status_code == 401


def test_grant_is_invalid_after_project_rebinding(tmp_path, monkeypatch):
    from minimax_code import app as runtime

    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "index.html").write_text("first")
    (second / "index.html").write_text("second")
    row = {"id": "s", "workspace_path": str(first)}

    async def get_session(session_id):
        return row if session_id == "s" else None

    monkeypatch.setattr(runtime, "get_sessions_dao", lambda: SimpleNamespace(get=get_session))
    app = build_preview_app(tmp_path)
    with TestClient(app) as client:
        base = client.post("/preview/access", json={"session_id": "s"}, headers={
            "Authorization": f"Bearer {app.state.auth.token}",
        }).json()["path"]
        assert client.get(base + "/index.html").text == "first"
        row["workspace_path"] = str(second)
        assert client.get(base + "/index.html").status_code == 409


def test_grants_reuse_same_binding_and_validate_input(tmp_path):
    app = build_preview_app(tmp_path)
    with TestClient(app, headers={"Authorization": f"Bearer {app.state.auth.token}"}) as client:
        one = client.post("/preview/access", json={}).json()
        two = client.post("/preview/access", json={}).json()
        assert one["path"] == two["path"]
        assert len(app.state.auth.preview_grants) == 1
        assert client.post("/preview/access", content="not json").status_code == 400
        assert client.post("/preview/access", json={"session_id": []}).status_code == 400
        assert client.post("/preview/access", json={"root": "/unbound"}).status_code == 400
