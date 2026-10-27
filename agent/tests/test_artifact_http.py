"""Authenticated HTTP/RPC flow against real migrated SQLite and snapshot files."""
from __future__ import annotations

import io

import httpx
import pytest

from minimax_code import artifact_http
from minimax_code.config import Config
from minimax_code.http_server import build_app
from minimax_code.ipc import handlers_artifacts
from minimax_code.ipc.handlers_artifacts import register_artifact_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.db import AsyncDatabase
from minimax_code.text_artifacts import ArtifactService


@pytest.fixture
async def artifact_client(tmp_path, monkeypatch):
    db = AsyncDatabase(tmp_path / "data.db")
    await db.connect()
    await db.migrate()
    root = tmp_path / "project"
    root.mkdir()
    await ProjectsDAO(db).create(id="p", name="project", root_path=str(root))
    service = ArtifactService(db, tmp_path)

    async def factory():
        return service

    monkeypatch.setattr(handlers_artifacts, "get_artifact_service", factory)
    monkeypatch.setattr(artifact_http, "get_artifact_service", factory)
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=io.StringIO())
    register_artifact_handlers(server)
    app = build_app(server, version="artifact-test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://testserver") as client:
        client.headers["Authorization"] = f"Bearer {app.state.auth.token}"
        yield client, root, service, app
    server.listeners.clear()
    await db.close()


async def rpc(client, method, params):
    return (await client.post("/rpc", json={"jsonrpc": "2.0", "id": 1,
                                           "method": method, "params": params})).json()


async def test_authenticated_register_list_preview_download_and_corruption(artifact_client):
    client, root, service, _ = artifact_client
    original = b"# original\r\n<script>alert('x')</script>\r\n"
    (root / "report.md").write_bytes(original)
    registered = await rpc(client, "artifact.register", {"project_id": "p", "source_path": "report.md"})
    artifact = registered["result"]["artifact"]
    listed = (await rpc(client, "artifact.list", {"project_id": "p"}))["result"]
    assert listed["artifacts"] == [artifact]
    assert listed["service_id"] == artifact["service_id"]
    selected = (await rpc(client, "artifact.get", {"project_id": "p", "artifact_id": artifact["id"]}))["result"]
    assert selected["preview_text"] == original.decode()
    url = f"/artifacts/{artifact['id']}/content?project_id=p"
    (root / "report.md").write_bytes(b"new source")
    response = await client.get(url, headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 200 and response.content == original
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-artifact-service-id"] == artifact["service_id"]
    assert "X-Artifact-Service-Id" in response.headers["access-control-expose-headers"]
    (service.vault / artifact["snapshot_id"]).write_bytes(b"bad")
    assert (await client.get(url)).status_code == 409
    selected = await rpc(client, "artifact.get", {"project_id": "p", "artifact_id": artifact["id"]})
    assert selected["error"]["code"] == -32000
    assert "corrupt" in selected["error"]["message"]


@pytest.mark.parametrize("method,params", [
    ("artifact.register", {"project_id": "p", "source_path": "a.txt", "host_id": "other"}),
    ("artifact.register", {"project_id": "p", "source_path": "a.txt", "absolute_path": "/a"}),
    ("artifact.register", {"project_id": "p", "source_path": "a.txt", "run_id": 2}),
    ("artifact.list", {}), ("artifact.list", {"project_id": "p", "host": "local"}),
    ("artifact.get", {"project_id": "p", "artifact_id": "../x"}),
    ("artifact.list", None), ("artifact.list", []),
])
async def test_strict_parameters_before_snapshot_work(artifact_client, method, params):
    client, _, service, _ = artifact_client
    assert (await rpc(client, method, params))["error"]["code"] == -32602
    assert not service.vault.exists()


async def test_content_main_host_origin_and_auth_boundaries(artifact_client):
    client, root, _, _ = artifact_client
    (root / "a.txt").write_bytes(b"a")
    row = (await rpc(client, "artifact.register", {"project_id": "p", "source_path": "a.txt"}))["result"]["artifact"]
    url = f"/artifacts/{row['id']}/content?project_id=p"
    token = client.headers.pop("Authorization")
    assert (await client.get(url)).status_code == 401
    assert (await client.get(url + "&token=" + token)).status_code == 401
    client.headers["Authorization"] = token
    assert (await client.get(url, headers={"Origin": "https://evil.invalid"})).status_code == 403
    assert (await client.get(url, headers={"Host": "evil.invalid"})).status_code == 400
    assert (await client.get(url + "&host_id=other")).status_code == 400
    assert (await client.get(url.replace("project_id=p", "project_id=missing"))).status_code == 409


async def test_storage_unreadable_is_explicit_not_fallback(artifact_client, monkeypatch):
    client, root, _, _ = artifact_client
    (root / "a.txt").write_bytes(b"a")

    async def fail():
        raise RuntimeError("database inaccessible")

    monkeypatch.setattr(handlers_artifacts, "get_artifact_service", fail)
    monkeypatch.setattr(artifact_http, "get_artifact_service", fail)
    assert (await rpc(client, "artifact.register", {"project_id": "p", "source_path": "a.txt"}))["error"]["code"] == -32603
    assert (await client.get("/artifacts/art_" + "0" * 32 + "/content?project_id=p")).status_code == 503
