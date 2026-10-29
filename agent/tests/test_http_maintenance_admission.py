"""Real ASGI requests participate in the maintenance fence."""
import asyncio
import io
import threading

import httpx
import pytest
from test_rpc_maintenance_admission import fake_producers

from minimax_code.config import Config
from minimax_code.http_server import build_app
from minimax_code.ipc.server import IPCServer
from minimax_code.runtime.maintenance import drain_for_maintenance


@pytest.fixture
async def setup(monkeypatch):
    fake_producers(monkeypatch)
    server = IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO())
    app = build_app(server)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://localhost",
                                 headers={"Authorization": f"Bearer {app.state.auth.token}"}) as client:
        yield server, client
    server.listeners.clear()


@pytest.mark.parametrize("path", ["/attachments?project_id=p&filename=a.txt", "/complete", "/hooks/example"])
async def test_inflight_and_rejection(setup, monkeypatch, path, tmp_path):
    server, client = setup
    entered, release = asyncio.Event(), asyncio.Event()

    if path.startswith("/attachments"):
        from minimax_code import attachment_http
        class Upload:
            async def upload(self, *args):
                entered.set()
                await release.wait()
                return {"id": "a"}
        async def factory():
            return Upload()
        monkeypatch.setattr(attachment_http, "get_attachment_service", factory)
    elif path == "/complete":
        from minimax_code.agent import completion_routes
        async def build(server):
            entered.set()
            await release.wait()
            return None
        async def complete(req, llm):
            from minimax_code.agent.completion import CompletionResponse
            return CompletionResponse(text="ok", model="mock")
        monkeypatch.setattr(completion_routes, "_build_llm_client", build)
        monkeypatch.setattr(completion_routes, "complete", complete)
    else:
        from minimax_code import webhooks as webhook_module
        from minimax_code.storage.dao.webhooks import WebhookDAO
        from minimax_code.storage.db import AsyncDatabase
        db = AsyncDatabase(tmp_path / "data.db")
        await db.connect()
        await db.migrate()
        await WebhookDAO(db).create(name="test", source="custom", url_path=path, secret="secret")
        server._app_state = {"db": db}
        async def dispatch(*args, **kwargs):
            entered.set()
            await release.wait()
        monkeypatch.setattr(webhook_module, "dispatch_webhook_action", dispatch)

    headers = {"X-Attachment-Service-Id": "service"}
    if path.startswith("/hooks/"):
        import hashlib
        import hmac
        body = b'{"file_path":"x"}'
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(
            b"secret", body, hashlib.sha256).hexdigest()
        task = asyncio.create_task(client.post(path, content=body, headers=headers))
    else:
        task = asyncio.create_task(client.post(path, json={"file_path": "x"}, headers=headers))
    await asyncio.wait_for(entered.wait(), 3)
    drain = asyncio.create_task(drain_for_maintenance(server))
    await asyncio.sleep(0)
    assert not drain.done()
    assert (await client.post(path, json={"file_path": "x"}, headers=headers)).status_code == 503
    assert (await client.post(path, headers={"Authorization": ""})).status_code == 401
    assert (await client.get("/health")).status_code == 200
    release.set()
    assert (await task).status_code in (200, 201)
    assert not (await drain).upgrade_ready
    if path.startswith("/hooks/"):
        assert (await db.fetchone("SELECT state FROM webhook_deliveries"))["state"] == "completed"
        await db.close()


async def test_cancelled_upload_retains_thread_until_worker_finishes(setup, monkeypatch):
    from minimax_code import attachment_http
    from minimax_code.attachments import _blocking

    server, client = setup
    started, release = threading.Event(), threading.Event()
    def worker():
        started.set()
        release.wait(3)
    class Upload:
        async def upload(self, *args):
            await _blocking(worker)
            return {"id": "a"}
    async def factory():
        return Upload()
    monkeypatch.setattr(attachment_http, "get_attachment_service", factory)
    task = asyncio.create_task(client.post("/attachments?project_id=p&filename=a.txt",
                                         headers={"X-Attachment-Service-Id": "s"}, content=b"a"))
    assert await asyncio.to_thread(started.wait, 3)
    drain = asyncio.create_task(drain_for_maintenance(server))
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not drain.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (await drain).upgrade_ready


async def test_download_initialization_is_fenced(setup, monkeypatch):
    from minimax_code import attachment_http

    server, client = setup
    entered, release = asyncio.Event(), asyncio.Event()
    async def factory():
        entered.set()
        await release.wait()
        raise ValueError("unavailable")
    monkeypatch.setattr(attachment_http, "get_attachment_service", factory)
    path = "/attachments/id/content?project_id=p"
    request = asyncio.create_task(client.get(path))
    await asyncio.wait_for(entered.wait(), 3)
    drain = asyncio.create_task(drain_for_maintenance(server))
    await asyncio.sleep(0)
    assert not drain.done()
    assert (await client.get(path)).status_code == 503
    assert (await client.get(path, headers={"Authorization": ""})).status_code == 401
    release.set()
    assert (await request).status_code == 409
    assert not (await drain).upgrade_ready


async def test_preview_stream_and_watcher_are_closed(setup):
    server, client = setup
    app = client._transport.app
    started = asyncio.Event()
    never = asyncio.Event()
    async def receive():
        await never.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        if message["type"] == "http.response.start":
            assert message["status"] == 200
            started.set()
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "GET", "scheme": "http", "path": "/preview/events",
             "raw_path": b"/preview/events", "query_string": b"", "root_path": "",
             "headers": [(b"host", b"localhost"), (b"authorization",
                         f"Bearer {app.state.auth.token}".encode())],
             "client": ("127.0.0.1", 1234), "server": ("localhost", 80)}
    request = asyncio.create_task(app(scope, receive, send))
    await asyncio.wait_for(started.wait(), 3)
    result = await asyncio.wait_for(drain_for_maintenance(server), 3)
    await asyncio.wait_for(request, 3)
    assert not result.upgrade_ready
    assert server._preview_state._watcher_task is None
    assert not server._preview_state._subscribers
    with pytest.raises(RuntimeError, match="closed for maintenance"):
        await server._preview_state.start_watcher()


async def test_health_control_drains_before_completion(setup, monkeypatch):
    from minimax_code import app, runs

    server, client = setup
    draining, release_run, entered, release_health = (asyncio.Event() for _ in range(4))
    class Service:
        async def drain(self):
            draining.set()
            await release_run.wait()
    class DB:
        async def execute(self, statement):
            entered.set()
            await release_health.wait()
    monkeypatch.setattr(runs, "get_run_service", lambda server: Service())
    monkeypatch.setattr(app, "get_db", lambda: DB())
    drain = asyncio.create_task(drain_for_maintenance(server))
    await asyncio.wait_for(draining.wait(), 3)
    health = asyncio.create_task(client.get("/health"))
    await asyncio.wait_for(entered.wait(), 3)
    release_run.set()
    await asyncio.sleep(0)
    assert not drain.done()
    release_health.set()
    assert (await health).status_code == 200
    await asyncio.wait_for(drain, 3)
    assert (await client.get("/health")).status_code == 503
