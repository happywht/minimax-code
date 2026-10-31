from __future__ import annotations

import pytest

from minimax_code.ipc.protocol import RunStartParams
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.dao.text_artifacts import ArtifactDAO
from minimax_code.storage.db import AsyncDatabase


@pytest.mark.parametrize("engine", ["native", "codex"])
async def test_admission_checks_binding_in_insert_transaction(tmp_path, engine):
    db = AsyncDatabase(tmp_path / "binding.db")
    await db.connect()
    await db.migrate()
    try:
        await ProjectsDAO(db).create(id="p", name="P", root_path=str(tmp_path))
        await ProjectsDAO(db).create(id="other", name="Other", root_path=str(tmp_path))
        await SessionsDAO(db).create(id="s", title="s", engine=engine, project_id="p")
        identity = await ArtifactDAO(db).service_id()
        dao = AgentRunsDAO(db)
        binding = {"service_id": identity, "project_id": "p", "engine": engine}
        request = {"session_id": "s", "mode": "execute", "content": "work"}
        if engine == "codex":
            request["engine"] = "codex"
        args = dict(session_id="s", request=request, title="task", metadata={"project_id": "p"},
                    request_key="key", admission_binding=binding)
        await db.execute("UPDATE sessions SET project_id='other' WHERE id='s'")
        with pytest.raises(ValueError, match="project changed"):
            await dao.admit_run(**args)
        assert not await dao.list_runs(session_id="s")
        await db.execute("UPDATE sessions SET project_id='p' WHERE id='s'")
        with pytest.raises(ValueError, match="project changed"):
            await dao.admit_run(**{**args, "admission_binding": {**binding, "service_id": "wrong"}})
        run, created = await dao.admit_run(**args)
        assert created and run["metadata"]["project_id"] == "p"
        await db.execute("UPDATE sessions SET project_id='other' WHERE id='s'")
        with pytest.raises(ValueError, match="project changed"):
            await dao.admit_run(**args)
        with pytest.raises(ValueError, match="project changed"):
            await dao.check_admission_binding("s", binding, run["metadata"])
        assert len(await dao.list_runs(session_id="s")) == 1
    finally:
        await db.close()


def test_explicit_null_project_is_distinct_from_omitted():
    base = {"session_id": "s", "idempotency_key": "k", "content": "work"}
    assert "expected_project_id" not in RunStartParams.model_validate(base).model_fields_set
    assert "expected_project_id" in RunStartParams.model_validate({**base, "expected_project_id": None}).model_fields_set


@pytest.mark.parametrize("engine", ["native", "codex"])
async def test_run_start_passes_binding_to_each_engine(tmp_path, monkeypatch, engine):
    import io
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from minimax_code import app
    from minimax_code.config import Config
    from minimax_code.ipc import builtins, handlers_runs
    from minimax_code.ipc.server import IPCServer

    db = AsyncDatabase(tmp_path / "rpc-binding.db")
    await db.connect()
    await db.migrate()
    try:
        await ProjectsDAO(db).create(id="p", name="P", root_path=str(tmp_path))
        await SessionsDAO(db).create(id="s", title="s", engine=engine, project_id="p")
        identity = await ArtifactDAO(db).service_id()
        monkeypatch.setattr(app, "init_runtime", AsyncMock())
        monkeypatch.setattr(app, "get_db", lambda: db)
        monkeypatch.setattr("minimax_code.ipc.host_session_gate.require_chat_session", AsyncMock())
        native = AsyncMock()
        codex = AsyncMock()
        monkeypatch.setattr(builtins, "handle_agent_send_message", native)
        monkeypatch.setattr(handlers_runs, "_start_codex_run", codex)
        server = IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO())
        handlers_runs.register_run_handlers(server)
        ctx = SimpleNamespace(server=server, reply=AsyncMock(), reply_error=AsyncMock())
        await server._handlers["run.start"]({"session_id": "s", "idempotency_key": "k",
            "content": "work", "engine": engine, "expected_service_id": identity,
            "expected_project_id": "p"}, ctx)
        called = codex if engine == "codex" else native
        called.assert_awaited_once()
        assert called.await_args.kwargs["admission_binding"] == {
            "service_id": identity, "project_id": "p", "engine": engine}
        ctx.reply_error.assert_not_awaited()
        called.reset_mock()
        await server._handlers["run.start"]({"session_id": "s", "idempotency_key": "k2",
            "content": "work", "engine": engine, "expected_service_id": identity}, ctx)
        called.assert_awaited_once()
        assert called.await_args.kwargs["admission_binding"] is None
    finally:
        await db.close()
