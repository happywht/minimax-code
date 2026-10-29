from __future__ import annotations

import subprocess
from unittest.mock import AsyncMock

import pytest
from test_git_handlers import _CapturingContext, _make_handler, repo  # noqa: F401
from test_run_service import native  # noqa: F401

from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO


@pytest.fixture
async def scoped(native, repo, monkeypatch):  # noqa: F811
    from minimax_code import app
    root = (repo / "src").resolve()
    await ProjectsDAO(native.db).create(id="proj", name="Project", root_path=str(root))
    await SessionsDAO(native.db).update("one", project_id="proj", workspace_path=str(root))
    monkeypatch.setattr(app, "ensure_db", AsyncMock(return_value=native.db))
    run = await native.dao.create_run(session_id="one", status="completed", metadata={
        "workspace_snapshot": str(root), "project_id": "proj", "model_snapshot": {"engine": "native"}})
    return native, repo, root, run


async def test_run_diff_scopes_real_worktree_and_keeps_index(scoped):
    _, repository, root, run = scoped
    (repository / "README.md").write_text("outside change\n")
    (root / "staged.py").write_text("staged\n")
    subprocess.run(["git", "add", "src/staged.py"], cwd=repository, check=True)
    (root / "new.txt").write_text("private untracked content")
    index = (repository / ".git" / "index").read_bytes()
    handler, ctx = _make_handler(repository, "git.diff")
    await handler({"run_id": run["id"], "session_id": "one"}, ctx)
    assert ctx.reply_error_payload is None
    result = ctx.reply_payload
    assert result["workspace"] == str(root) and result["scope"] == "current_workspace"
    assert "helper" in result["diff"] and "outside change" not in result["diff"]
    assert "staged.py" in result["staged_diff"] and result["untracked"] == ["new.txt"]
    assert "private untracked content" not in str(result)
    assert (repository / ".git" / "index").read_bytes() == index


async def test_run_diff_rejects_wrong_session_or_rebound_project(scoped):
    state, repository, _, run = scoped
    handler, ctx = _make_handler(repository, "git.diff")
    await handler({"run_id": run["id"], "session_id": "two"}, ctx)
    assert ctx.reply_payload is None and ctx.reply_error_payload is not None
    await SessionsDAO(state.db).update("one", project_id="inbox")
    ctx = _CapturingContext()
    await handler({"run_id": run["id"], "session_id": "one"}, ctx)
    assert ctx.reply_payload is None and "binding" in ctx.reply_error_payload[1]


async def test_run_diff_rejects_legacy_or_restricted_binding(scoped, monkeypatch):
    state, repository, root, _ = scoped
    legacy = await state.dao.create_run(session_id="one", status="completed", metadata={"workspace_snapshot": str(root)})
    handler, ctx = _make_handler(repository, "git.diff")
    await handler({"run_id": legacy["id"], "session_id": "one"}, ctx)
    assert ctx.reply_payload is None and ctx.reply_error_payload is not None
    from minimax_code.ipc.handler_utils import HandlerError
    monkeypatch.setattr("minimax_code.ipc.host_session_gate.require_host_session", AsyncMock(side_effect=HandlerError(-32000, "restricted")))
    current = await state.dao.create_run(session_id="one", status="completed", metadata={"workspace_snapshot": str(root), "project_id": "proj", "model_snapshot": {"engine": "native"}})
    ctx = _CapturingContext()
    await handler({"run_id": current["id"], "session_id": "one"}, ctx)
    assert ctx.reply_payload is None and ctx.reply_error_payload[1] == "restricted"


async def test_codex_run_is_bound_to_its_saved_workspace(scoped):
    state, repository, root, _ = scoped
    await state.db.execute("UPDATE sessions SET engine='codex' WHERE id='one'")
    run = await state.dao.create_run(session_id="one", status="completed", metadata={
        "workspace_snapshot": str(root), "project_id": "proj", "engine": "codex"})
    handler, ctx = _make_handler(repository, "git.diff")
    await handler({"run_id": run["id"], "session_id": "one"}, ctx)
    assert ctx.reply_error_payload is None and "helper" in ctx.reply_payload["diff"]
    ctx = _CapturingContext()
    await handler({"run_id": run["id"], "session_id": "one", "cwd": str(repository)}, ctx)
    assert ctx.reply_payload is None and ctx.reply_error_payload is not None


async def test_run_diff_discards_if_session_moves_during_read(scoped, monkeypatch):
    from minimax_code.ipc import handlers_git
    state, repository, _, run = scoped
    original = handlers_git._run_git
    async def rebind(args, **kwargs):
        output = await original(args, **kwargs)
        if "status" in args:
            await SessionsDAO(state.db).update("one", project_id="inbox")
        return output
    monkeypatch.setattr(handlers_git, "_run_git", rebind)
    handler, ctx = _make_handler(repository, "git.diff")
    await handler({"run_id": run["id"], "session_id": "one"}, ctx)
    assert ctx.reply_payload is None and ctx.reply_error_payload is not None
