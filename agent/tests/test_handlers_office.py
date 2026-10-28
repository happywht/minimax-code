"""Authenticated real RPC/HTTP product boundary, with a temporary DB and vault."""
from __future__ import annotations

import io
from unittest.mock import AsyncMock

import httpx
import pytest

from minimax_code import attachment_http
from minimax_code.config import Config
from minimax_code.http_server import build_app
from minimax_code.ipc import handlers_office
from minimax_code.ipc.handlers_office import register_office_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.office_deliveries import OfficeService
from minimax_code.office_xlsx import XlsxSpec, verify
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.db import AsyncDatabase


@pytest.fixture
async def office_client(tmp_path, monkeypatch):
    db = AsyncDatabase(tmp_path / "data" / "test.db")
    await db.connect()
    await db.migrate()
    root = tmp_path / "workspace"
    root.mkdir()
    await ProjectsDAO(db).create(id="p", name="Project", root_path=str(root))
    service = OfficeService(db, db.path.parent)
    identity = (await service.list("p"))["service_id"]

    async def factory():
        return service

    async def originals():
        return service.originals

    monkeypatch.setattr(handlers_office, "get_office_service", factory)
    monkeypatch.setattr(attachment_http, "get_attachment_service", originals)
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=io.StringIO())
    register_office_handlers(server)
    app = build_app(server, version="office-test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        client.headers["Authorization"] = f"Bearer {app.state.auth.token}"
        yield client, service, identity
    server.listeners.clear()
    await db.close()


def payload(identity):
    return {"project_id": "p", "expected_service_id": identity, "request_key": "rpc_1",
            "filename": "result.xlsx", "spec": {"sheet_name": "data", "columns": [
                {"name": "value", "kind": "text"}], "rows": [["=literal"], ["<script>text</script>"]]}}


async def rpc(client, method, params):
    return (await client.post("/rpc", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})).json()


async def test_full_rpc_download_refresh_and_integrity(office_client):
    client, service, identity = office_client
    registered = await rpc(client, "office.generate_xlsx", payload(identity))
    assert "error" not in registered
    result = registered["result"]
    assert result["delivery"]["validation_report"]["business_quality"] == "unverified"
    listed = (await rpc(client, "office.list", {"project_id": "p"}))["result"]
    assert len(listed["deliveries"]) == 1 and listed["service_id"] == identity
    selected = (await rpc(client, "office.get", {"project_id": "p", "delivery_id": result["delivery"]["id"]}))["result"]
    assert selected == result and "=literal" in selected["preview_text"]
    response = await client.get(f"/attachments/{result['attachment']['id']}/content?project_id=p")
    assert response.status_code == 200
    assert response.headers["x-attachment-sha256"] == result["attachment"]["sha256"]
    assert response.headers["x-attachment-service-id"] == identity
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert verify(response.content, XlsxSpec.model_validate(payload(identity)["spec"])) == result["delivery"]["validation_report"]
    path = service.originals.vault / result["attachment"]["snapshot_id"]
    path.write_bytes(b"corruption")
    assert (await rpc(client, "office.get", {"project_id": "p", "delivery_id": result["delivery"]["id"]}))["error"]["code"] == -32000
    assert (await client.get(f"/attachments/{result['attachment']['id']}/content?project_id=p")).status_code == 409


@pytest.mark.parametrize("change", [{"host_id": "remote"}, {"path": "/outside.xlsx"},
    {"command": "whoami"}, {"execution_profile": "host"}, {"confirmed": True},
    {"run_id": 7}, {"filename": "macro.xlsm"}, {"expected_service_id": "wrong"},
    {"spec": {"sheet_name": "bad", "columns": [], "rows": []}}])
async def test_strict_parameters_refuse_before_storage(office_client, monkeypatch, change):
    client, service, identity = office_client
    factory = AsyncMock(side_effect=AssertionError("invalid request must not access storage"))
    monkeypatch.setattr(handlers_office, "get_office_service", factory)
    values = payload(identity)
    values.update(change)
    result = await rpc(client, "office.generate_xlsx", values)
    assert result["error"]["code"] == -32602
    factory.assert_not_awaited()
    assert not service.originals.vault.exists()


@pytest.mark.parametrize("kind", ["authentication", "host", "origin"])
async def test_boundary_denies_before_body_or_worker(office_client, monkeypatch, kind):
    client, service, identity = office_client
    generate = AsyncMock(side_effect=AssertionError("must not enter Office service"))
    monkeypatch.setattr(service, "generate", generate)
    headers = {}
    if kind == "authentication":
        client.headers.pop("Authorization")
    elif kind == "host":
        headers["Host"] = "evil.example"
    else:
        headers["Origin"] = "https://evil.example"
    response = await client.post("/rpc", content=b"not even JSON", headers=headers)
    assert response.status_code == (400 if kind == "host" else 401 if kind == "authentication" else 403)
    generate.assert_not_awaited()
    assert not service.originals.vault.exists()


async def test_idempotency_conflict_is_explicit_not_new_delivery(office_client):
    client, service, identity = office_client
    original = payload(identity)
    first = await rpc(client, "office.generate_xlsx", original)
    assert await rpc(client, "office.generate_xlsx", original) == first
    original["spec"]["rows"].append(["different"])
    assert (await rpc(client, "office.generate_xlsx", original))["error"]["code"] == -32000
    assert len((await service.list("p"))["deliveries"]) == 1
