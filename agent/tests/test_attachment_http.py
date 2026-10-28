"""Real authenticated HTTP streaming and strict RPC metadata contract."""
from __future__ import annotations

import io

import httpx
import pytest
from starlette.requests import ClientDisconnect

from minimax_code import attachment_http
from minimax_code.attachments import MAX_BYTES, AttachmentService
from minimax_code.config import Config
from minimax_code.http_server import build_app
from minimax_code.ipc import handlers_attachments
from minimax_code.ipc.handlers_attachments import register_attachment_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.db import AsyncDatabase


@pytest.fixture
async def upload_client(tmp_path, monkeypatch):
    db = AsyncDatabase(tmp_path / "data.db")
    await db.connect()
    await db.migrate()
    await ProjectsDAO(db).create(id="p", name="Project")
    await ProjectsDAO(db).create(id="other", name="Other")
    service = AttachmentService(db, tmp_path)
    async def factory():
        return service
    monkeypatch.setattr(attachment_http, "get_attachment_service", factory)
    monkeypatch.setattr(handlers_attachments, "get_attachment_service", factory)
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=io.StringIO())
    register_attachment_handlers(server)
    app = build_app(server, version="attachment-test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        client.headers["Authorization"] = f"Bearer {app.state.auth.token}"
        client.headers["X-Attachment-Service-Id"] = (await service.list("p"))["service_id"]
        yield client, service, db, app
    server.listeners.clear()
    await db.close()


async def rpc(client, method, params):
    return (await client.post("/rpc", json={"jsonrpc": "2.0", "id": "attachment", "method": method,
                                           "params": params})).json()


async def upload(client, data, filename="original.txt", headers=None):
    return await client.post("/attachments", params={"project_id": "p", "filename": filename},
                             content=data, headers=headers)


@pytest.mark.parametrize("case", ["formula", "empty", "rejected"])
async def test_xlsx_actual_upload_persisted_preview_and_identical_download(upload_client, case):
    pytest.importorskip("openpyxl")
    from xlsx_fixture import OFFICE, REL, cell, worksheet, xlsx_bytes

    client, _, db, _ = upload_client
    changes = {"xl/worksheets/_rels/sheet1.xml.rels": f'<Relationships xmlns="{REL}"><Relationship Id="bad" Type="{OFFICE}/hyperlink" Target="https://invalid.example/" TargetMode="External"/></Relationships>'} if case == "rejected" else {}
    original = xlsx_bytes([("Cells", worksheet([(1, cell("A1", "999", formula="1+1") + cell("B1", formula="NOW()"))] if case == "formula" else []), "visible")], changes=changes)
    response = await upload(client, original, "cells.xlsx")
    assert response.status_code == 201
    metadata = response.json()["attachment"]
    assert metadata["parser_status"] == {"formula": "parsed", "empty": "empty", "rejected": "parse_failed"}[case]
    assert metadata["parser_reason"] == ("unsupported_feature" if case == "rejected" else "ok")
    result = (await rpc(client, "attachment.get", {"project_id": "p", "attachment_id": metadata["id"]}))["result"]
    if case == "formula":
        assert '"cached_raw":"999"' in result["preview_text"] and '"recalculated":false' in result["preview_text"]
        assert '"cached_present":false' in result["preview_text"]
    else:
        assert result["preview_text"] == ""
    stored = await db.fetchone("SELECT preview_text,quality_status FROM attachments WHERE id=?", (metadata["id"],))
    assert stored["preview_text"] == result["preview_text"] and stored["quality_status"] == "unverified"
    downloaded = await client.get(f"/attachments/{metadata['id']}/content?project_id=p")
    assert downloaded.content == original and downloaded.headers["x-attachment-sha256"] == metadata["sha256"]


async def test_authenticated_upload_list_preview_download_inert_bytes_and_headers(upload_client):
    client, _, _, _ = upload_client
    original = b"\xef\xbb\xbf<script>not executed</script>\r\n"
    response = await upload(client, original, "../file/report.txt")
    assert response.status_code == 201
    row = response.json()["attachment"]
    assert "preview_text" not in row and row["quality_status"] == "unverified"
    listed = (await rpc(client, "attachment.list", {"project_id": "p"}))["result"]
    assert listed["attachments"] == [row] and listed["service_id"] == row["service_id"]
    selected = (await rpc(client, "attachment.get", {"project_id": "p", "attachment_id": row["id"]}))["result"]
    assert selected == {"attachment": row, "preview_text": original.decode()}
    response = await client.get(f"/attachments/{row['id']}/content?project_id=p",
                                headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 200 and response.content == original
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-attachment-sha256"] == row["sha256"]
    assert response.headers["x-attachment-service-id"] == row["service_id"]
    assert "X-Attachment-Service-Id" in response.headers["access-control-expose-headers"]
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;") and "../" not in disposition


async def test_boundaries_block_unauthenticated_wrong_origin_host_and_service_before_body(upload_client):
    client, service, db, _ = upload_client
    observed = []
    async def body():
        observed.append("read")
        yield b"must not read"
    token = client.headers.pop("Authorization")
    response = await upload(client, body())
    assert response.status_code == 401
    client.headers["Authorization"] = token
    assert (await upload(client, body(), headers={"Origin": "https://evil.invalid"})).status_code == 403
    assert (await upload(client, body(), headers={"Host": "evil.invalid"})).status_code == 400
    await db.execute("UPDATE artifact_service SET service_id=?", ("svc_" + "2" * 32,))
    assert (await upload(client, body())).status_code == 409
    assert observed == [] and not service.vault.exists()


async def test_raw_stream_no_length_exact_limit_and_claimed_length_independent(upload_client):
    client, service, db, _ = upload_client
    async def exact():
        for _ in range(MAX_BYTES // 65536):
            yield b"x" * 65536
    response = await upload(client, exact(), "max.txt")
    assert response.status_code == 201 and response.json()["attachment"]["size_bytes"] == MAX_BYTES
    assert response.json()["attachment"]["parser_status"] == "truncated"
    async def over():
        async for chunk in exact():
            yield chunk
        yield b"x"
    assert (await upload(client, over(), headers={"Content-Length": "1"})).status_code == 413
    assert len(await db.fetchall("SELECT * FROM attachments")) == 1
    assert len(list(service.vault.iterdir())) == 1
    assert not list(service.vault.glob(".pending-*"))


async def test_oversize_header_rejects_before_stream_and_disconnect_cleans(upload_client):
    client, service, db, _ = upload_client
    seen = []
    async def body():
        seen.append(True)
        yield b"first"
        raise ClientDisconnect()
    assert (await upload(client, body(), headers={"Content-Length": str(MAX_BYTES + 1)})).status_code == 413
    assert (await upload(client, body(), headers={"Content-Length": "9" * 5000})).status_code == 400
    assert seen == []
    assert (await upload(client, body())).status_code == 400
    assert seen == [True] and not list(service.vault.iterdir())
    assert not await db.fetchall("SELECT * FROM attachments")


async def test_cross_project_corruption_auth_and_untrusted_query_download(upload_client):
    client, service, _, _ = upload_client
    row = (await upload(client, b"original")).json()["attachment"]
    url = f"/attachments/{row['id']}/content?project_id=p"
    assert (await client.get(url.replace("project_id=p", "project_id=other"))).status_code == 409
    assert (await client.get(url + "&host_id=other")).status_code == 400
    assert (await client.get(url, headers={"Origin": "https://evil.invalid"})).status_code == 403
    token = client.headers.pop("Authorization")
    assert (await client.get(url)).status_code == 401
    client.headers["Authorization"] = token
    (service.vault / row["snapshot_id"]).write_bytes(b"tampered")
    assert (await client.get(url)).status_code == 409
    preview = await rpc(client, "attachment.get", {"project_id": "p", "attachment_id": row["id"]})
    assert preview["error"]["code"] == -32000 and "corrupt" in preview["error"]["message"]


@pytest.mark.parametrize("method,params", [
    ("attachment.list", {}), ("attachment.list", None), ("attachment.list", []),
    ("attachment.list", {"project_id": "p", "host_id": "other"}),
    ("attachment.get", {"project_id": "p", "attachment_id": "../escape"}),
    ("attachment.get", {"project_id": "p", "attachment_id": "att_" + "0" * 32, "path": "/tmp/a"}),
])
async def test_strict_rpc_contract_before_vault_reads(upload_client, method, params):
    client, service, _, _ = upload_client
    assert (await rpc(client, method, params))["error"]["code"] == -32602
    assert not service.vault.exists()


@pytest.mark.parametrize("query", ["project_id=p", "project_id=p&filename=a.txt&filename=b.txt",
                                   "project_id=p&filename=a.txt&host_id=other", "project_id=p&filename=a%0d%0a.txt"])
async def test_strict_upload_display_query_and_no_filename_header_injection(upload_client, query):
    client, service, _, _ = upload_client
    response = await client.post("/attachments?" + query, content=b"original")
    assert response.status_code in {400, 409} and not service.vault.exists()


async def test_required_service_header_and_cors_preflight(upload_client):
    client, service, _, _ = upload_client
    del client.headers["X-Attachment-Service-Id"]
    assert (await upload(client, b"no authority")).status_code == 400
    response = await client.options("/attachments", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type,x-attachment-service-id"})
    assert response.status_code == 200
    assert "x-attachment-service-id" in response.headers["access-control-allow-headers"].lower()
    assert not service.vault.exists()


async def test_pdf_actual_worker_upload_rpc_preview_original_and_mismatch(upload_client):
    pytest.importorskip("pypdf")
    from pdf_fixture import pdf_bytes

    client, service, db, _ = upload_client
    original = pdf_bytes(("HTTP ASCII", "", "中文第三页"))
    response = await upload(client, original, "../source.pdf")
    assert response.status_code == 201
    row = response.json()["attachment"]
    assert row["parser_status"] == "parsed" and row["parser_scope"] == "inert_text"
    selected = (await rpc(client, "attachment.get", {"project_id": "p", "attachment_id": row["id"]}))["result"]
    assert "[page 3]" in selected["preview_text"] and "中文第三页" in selected["preview_text"]
    assert (await client.get(f"/attachments/{row['id']}/content?project_id=p")).content == original
    before = dict(await db.fetchone("SELECT * FROM attachments WHERE id=?", (row["id"],)))
    await db.close()
    await db.connect()
    assert (await service.get("p", row["id"]))["attachment"] == row
    assert dict(await db.fetchone("SELECT * FROM attachments WHERE id=?", (row["id"],))) == before
    mismatch = (await upload(client, original, "pretend.txt")).json()["attachment"]
    assert mismatch["parser_status"] == "type_mismatch"
    bad = (await upload(client, b"%PDF-1.7\nonly signature", "broken.pdf")).json()["attachment"]
    assert bad["parser_status"] == "parse_failed" and bad["parser_reason"] == "invalid_package"
    assert (await service.content("p", bad["id"]))[1] == b"%PDF-1.7\nonly signature"
