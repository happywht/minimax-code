from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock

from test_run_service import Context, native  # noqa: F401

from minimax_code.ipc.handlers_runtime import _handle_workspace_check


async def test_workspace_diagnostic_uses_session_project_and_default_precedence(native, monkeypatch, tmp_path):  # noqa: F811
    from minimax_code import app

    monkeypatch.setattr(app, "ensure_db", AsyncMock(return_value=native.db))
    project = tmp_path / "project"
    project.mkdir()
    await native.db.execute("UPDATE projects SET root_path=? WHERE id='inbox'", (str(project),))
    request = {"session_id": "one", "project_id": "inbox"}

    async def check():
        ctx = Context(native.server)
        await _handle_workspace_check(request, ctx)
        assert ctx.reply.await_args is not None, ctx.reply_error.await_args
        return ctx.reply.await_args.args[0]

    result = await check()
    assert result["status"] == "ok" and result["source"] == "session" and result["path"] == str(tmp_path)
    await native.db.execute("UPDATE sessions SET workspace_path=? WHERE id='one'", (str(tmp_path / "missing"),))
    result = await check()
    assert result["status"] == "error" and result["source"] == "session" and result["path"] is None
    await native.db.execute("UPDATE sessions SET workspace_path=NULL WHERE id='one'")
    result = await check()
    assert result["status"] == "ok" and result["source"] == "project" and result["path"] == str(project)
    await native.db.execute("UPDATE projects SET root_path='' WHERE id='inbox'")
    result = await check()
    assert result["status"] == "ok" and result["source"] == "default" and result["path"] == str(tmp_path)
    rejected = Context(native.server)
    await _handle_workspace_check({**request, "project_id": "other"}, rejected)
    assert rejected.reply_error.await_count == 1


async def test_workspace_diagnostic_does_not_probe_unc_or_restricted_session(native, monkeypatch):  # noqa: F811
    from minimax_code import app, workspace_ctx
    from minimax_code.ipc import host_session_gate
    from minimax_code.ipc.handler_utils import HandlerError

    monkeypatch.setattr(app, "ensure_db", AsyncMock(return_value=native.db))
    await native.db.execute("UPDATE sessions SET workspace_path=? WHERE id='one'", (r"\\server\share",))
    monkeypatch.setattr(workspace_ctx, "require_directory", lambda _: (_ for _ in ()).throw(AssertionError("must not probe")))
    request = {"session_id": "one", "project_id": "inbox"}
    ctx = Context(native.server)
    await _handle_workspace_check(request, ctx)
    assert ctx.reply.await_args.args[0]["reason"] == "network_path_not_checked"
    monkeypatch.setattr(host_session_gate, "require_host_session", AsyncMock(side_effect=HandlerError(-32000, "restricted")))
    ctx = Context(native.server)
    await _handle_workspace_check(request, ctx)
    assert ctx.reply.await_args.args[0]["status"] == "unknown"
    assert ctx.reply.await_args.args[0]["path"] is None


async def test_workspace_diagnostic_limits_concurrent_blocked_checks(native, monkeypatch, tmp_path):  # noqa: F811
    from minimax_code import app, workspace_ctx

    monkeypatch.setattr(app, "ensure_db", AsyncMock(return_value=native.db))
    release = threading.Event()
    monkeypatch.setattr(workspace_ctx, "require_directory", lambda _: (release.wait(3), tmp_path)[1])
    request = {"session_id": "one", "project_id": "inbox"}
    contexts = [Context(native.server) for _ in range(3)]
    tasks = [asyncio.create_task(_handle_workspace_check(request, ctx)) for ctx in contexts[:2]]
    try:
        for _ in range(100):
            slots = getattr(native.server, "_workspace_diagnostic_slots", None)
            if slots is not None and slots.locked():
                break
            await asyncio.sleep(.01)
        await _handle_workspace_check(request, contexts[2])
        assert contexts[2].reply.await_args.args[0]["status"] == "unknown"
    finally:
        release.set()
        await asyncio.gather(*tasks)
