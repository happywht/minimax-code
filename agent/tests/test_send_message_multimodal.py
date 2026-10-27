"""Content mapping through real chat admission, storage and budget ownership."""
from __future__ import annotations

from test_run_service import Context
from test_run_service import native as native

from minimax_code import app
from minimax_code.ipc.builtins import handle_agent_send_message
from minimax_code.storage.dao.projects import ProjectsDAO


async def send(state, content, *, session_id="one"):
    state.release.set()
    ctx = Context(state.server)
    await handle_agent_send_message({"content": content, "session_id": session_id}, ctx)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.await_args.args[0]
    assert receipt["text"] == "done"
    run = await state.dao.get_run(receipt["run_id"])
    assert run["status"] == "completed"
    assert (await state.db.fetchone(
        "SELECT id FROM budget_families WHERE id=?",
        (run["metadata"]["budget_family_id"],),
    )) is not None
    assert (await state.dao.request_payload(run["id"]))["content"] == content
    return run


class TestSendMessageMultimodal:
    """Map content without replacing durable session/run/budget storage."""

    async def test_string_content_backward_compat(self, native):
        await send(native, "Hello world")
        assert native.started.get_nowait() == ("one", "Hello world")


    async def test_list_content_accepted(self, native):
        content = [
            {"type": "text", "text": "Describe this image"},
            {"type": "image", "media_type": "image/png", "data": "iVBORw0KGgo="},
        ]
        await send(native, content)
        assert native.started.get_nowait() == ("one", content)


    async def test_empty_list_rejected(self, native):
        ctx = Context(native.server)
        await handle_agent_send_message({"content": [], "session_id": "one"}, ctx)
        ctx.reply.assert_not_awaited()
        assert ctx.reply_error.await_args.args[0] == -32602
        assert "non-empty" in ctx.reply_error.await_args.args[1]
        assert not await native.dao.list_runs()
        assert not native.cores


    async def test_title_from_multimodal(self, native, monkeypatch):
        projects = ProjectsDAO(native.db)
        await projects.ensure_inbox()
        monkeypatch.setattr(app, "get_projects_dao", lambda: projects)
        content = [
            {"type": "text", "text": "Analyze this architecture diagram"},
            {"type": "image", "media_type": "image/png", "data": "abc"},
        ]
        await send(native, content, session_id="new-multimodal")
        session = await app.get_sessions_dao().get("new-multimodal")
        assert session["title"] == "chat:" + content[0]["text"][:32]
        assert native.started.get_nowait() == ("new-multimodal", content)
