"""M3-A tests — project validate commands and the automatic acceptance loop.

Layers:

- **storage/handler**: ``validate_command`` persists through
  ``project.create`` / ``project.update`` (dispatch level), hydrates
  defensively for pre-032 rows.
- **prompt injection**: a configured command surfaces in the system
  prompt as the acceptance contract.
- **automatic acceptance** (end to end through the native pipeline):
  a run whose stub core publishes an fs-bus write event executes the
  project's command before completing; the evidence lands in
  ``receipt.verification.validation`` and a failing command forces
  ``requires_review``.
"""

from __future__ import annotations

import asyncio
import io
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from minimax_code.config import Config
from minimax_code.ipc import builtins
from minimax_code.ipc.handlers_projects import register_project_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.runs import get_run_service
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase
from minimax_code.verification import (
    coerce_validate_timeout,
    normalize_validate_command,
    run_touched_files,
)

# ---------------------------------------------------------------------------
# Unit behaviour
# ---------------------------------------------------------------------------


def test_normalize_validate_command() -> None:
    assert normalize_validate_command("  pytest -x  ") == "pytest -x"
    assert normalize_validate_command("   ") == ""
    assert normalize_validate_command(None) == ""
    assert normalize_validate_command(42) == ""


def test_coerce_validate_timeout() -> None:
    assert coerce_validate_timeout(None) == 600.0
    assert coerce_validate_timeout(True) == 600.0  # bool is never a knob
    assert coerce_validate_timeout(-5) == 600.0
    assert coerce_validate_timeout(30) == 30.0
    assert coerce_validate_timeout(99999) == 900.0  # ceiling


def test_run_touched_files_matches_run_attribute() -> None:
    class FakeBus:
        def __init__(self, events):
            self._events = events

        def recent(self, limit=100):
            return self._events[:limit]

    mine = SimpleNamespace(attributes=(("run_id", "run_a"),))
    other = SimpleNamespace(attributes=(("run_id", "run_b"),))
    assert run_touched_files(FakeBus([other, mine]), "run_a") is True
    assert run_touched_files(FakeBus([other]), "run_a") is False
    assert run_touched_files(None, "run_a") is False
    assert run_touched_files(FakeBus([mine]), "") is False


# ---------------------------------------------------------------------------
# Storage + handler contract
# ---------------------------------------------------------------------------


@pytest.fixture
async def proj_server(tmp_path):
    db = AsyncDatabase(tmp_path / "proj.db")
    await db.connect()
    await db.migrate()
    server = IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO())
    # Inject the DAO directly — the default factory falls through to the
    # process-wide runtime + real platformdirs database, which a test
    # must never touch (and would contend with any live 8765 agent).
    proj_dao = ProjectsDAO(db)
    register_project_handlers(server, dao=proj_dao)
    state = SimpleNamespace(db=db, server=server,
                            dao=proj_dao, sessions=SessionsDAO(db))
    try:
        yield state
    finally:
        await db.close()


class _Ctx:
    def __init__(self, server):
        self.server = server
        self.reply = AsyncMock()
        self.reply_error = AsyncMock()


async def test_project_validate_command_roundtrip(proj_server) -> None:
    ctx = _Ctx(proj_server.server)
    await proj_server.server._handlers["project.create"](
        {"name": "web", "validate_command": "  npm test  "}, ctx)
    created = ctx.reply.await_args.args[0]["project"]
    assert created["validate_command"] == "npm test"

    ctx2 = _Ctx(proj_server.server)
    await proj_server.server._handlers["project.update"](
        {"project_id": created["id"], "validate_command": "pytest -x -q"}, ctx2)
    updated = await proj_server.dao.get(created["id"])
    assert updated["validate_command"] == "pytest -x -q"

    # Empty string clears the binding (tri-state).
    await proj_server.server._handlers["project.update"](
        {"project_id": created["id"], "validate_command": ""}, _Ctx(proj_server.server))
    cleared = await proj_server.dao.get(created["id"])
    assert cleared["validate_command"] == ""

    # Over-long commands are rejected outright.
    rejected = _Ctx(proj_server.server)
    await proj_server.server._handlers["project.update"](
        {"project_id": created["id"], "validate_command": "x" * 501}, rejected)
    rejected.reply_error.assert_awaited_once()


async def test_validate_command_absent_in_legacy_rows(proj_server) -> None:
    # Simulate a row written before migration 032.
    async with proj_server.db.transaction() as conn:
        await conn.execute(
            "INSERT INTO projects (id, name, description, archived, root_path, created_at, updated_at) "
            "VALUES ('p_old', 'old', '', 0, '', '2026-01-01', '2026-01-01')")
    row = await proj_server.dao.get("p_old")
    assert row["validate_command"] == ""


# ---------------------------------------------------------------------------
# Prompt injection — the acceptance contract reaches the model
# ---------------------------------------------------------------------------


async def test_system_prompt_carries_acceptance_contract(proj_server, monkeypatch) -> None:

    await proj_server.dao.create(id="proj_v", name="validated", validate_command="pytest -x")
    session = await proj_server.sessions.create(id="ses_v", title="s")
    assert session is not None
    await proj_server.db.execute("UPDATE sessions SET project_id = 'proj_v' WHERE id = 'ses_v'")
    monkeypatch.setattr("minimax_code.app.get_db", lambda: proj_server.db)

    extra = await builtins._build_system_prompt_extra(
        session_id="ses_v", project_id="proj_v")
    assert extra is not None and "pytest -x" in extra
    assert "acceptance contract" in extra

    plain = await builtins._build_system_prompt_extra(
        session_id="ses_v", project_id="inbox")
    assert plain is None or "acceptance contract" not in plain


# ---------------------------------------------------------------------------
# Automatic acceptance — end to end through the native pipeline
# ---------------------------------------------------------------------------


@pytest.fixture
async def native(monkeypatch, tmp_path):
    from minimax_code import agent, app

    db = AsyncDatabase(tmp_path / "val.db")
    await db.connect()
    await db.migrate()
    projects = ProjectsDAO(db)
    sessions = SessionsDAO(db)
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    # Fuse: a wedged validate command must fail the test in seconds,
    # not hang it until the 600s production default.
    monkeypatch.setenv("MINIMAX_CODE_VALIDATE_TIMEOUT_S", "15")
    monkeypatch.setattr(app, "init_runtime", AsyncMock())
    monkeypatch.setattr(app, "get_db", lambda: db)
    monkeypatch.setattr(app, "get_sessions_dao", lambda: sessions)
    # Sessions bound to a project resolve their root through this DAO.
    monkeypatch.setattr(app, "get_projects_dao", lambda: projects)
    monkeypatch.setattr(app, "ensure_codebase_indexer", lambda *a: None)
    monkeypatch.setattr(app, "get_codebase_indexer", lambda: None)
    monkeypatch.setattr(app, "ensure_hook_manager", lambda: None)
    monkeypatch.setattr(app, "ensure_telemetry_engine", lambda: None)
    monkeypatch.setattr(app, "get_subagent_llm", lambda: SimpleNamespace(default_model="test", mock=True))
    monkeypatch.setattr(builtins, "_build_system_prompt_extra", AsyncMock(return_value=None))
    monkeypatch.setattr("minimax_code.ipc.handlers_permissions._ensure_permission_store", AsyncMock(return_value=None))
    server = IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO())
    release = asyncio.Event()

    class CoreReal:
        def __init__(self, **kwargs):
            self.config = kwargs["config"]
            self.llm = kwargs["llm"]
            self.budget = None
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

        async def run(self, *, session_id, user_message):
            from minimax_code.app import ensure_fs_bus

            # The main-agent path does not publish current_run_id(); the
            # run id comes from the durable row this run was admitted as.
            rows = await native.dao.list_runs(session_id=session_id,
                                              order_by="created_at DESC", limit=1)
            rid = rows[0]["id"] if rows else None
            native.fired_run_id = rid
            if native.simulate_write and rid:
                ensure_fs_bus().emit("modified", [str(tmp_path / "src.py")],
                                     cause="write_file", run_id=rid)
            await release.wait()
            if native.shell_write:
                from minimax_code.execution import communicate_bounded, spawn_exec
                proc = await spawn_exec(sys.executable, "-c", "from pathlib import Path; Path('shell.txt').write_text('actual shell write')",
                                        cwd=str(tmp_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await communicate_bounded(proc, timeout=5)
            from minimax_code.storage.dao.messages import MessagesDAO
            await MessagesDAO(db).create(id=rows[0]["assistant_message_id"], session_id=session_id,
                role="assistant", content="done", run_id=rid, metadata={"run_id": rid})
            return SimpleNamespace(**{"final_text": "done", "iterations": 1, "usage": {},
                "truncated": False, "cancelled": False, "compactions": 0,
                "budget_exhausted": False, "tool_calls": [], "tool_results": [], **native.result_flags})

    monkeypatch.setattr(agent, "AgentCore", CoreReal)
    native = SimpleNamespace(db=db, server=server, dao=AgentRunsDAO(db),
                             projects=projects, sessions=sessions,
                             release=release, simulate_write=False,
                             shell_write=False, result_flags={}, fired_run_id=None, root=tmp_path)
    try:
        yield native
    finally:
        await get_run_service(server).shutdown()
        await db.close()


def _quoted_ok_command() -> str:
    """A cross-platform command that exits 0.

    The interpreter path MUST be double-quoted: this repo's venv lives
    under a path containing spaces (`D:\\...\\mm code\\agent\\.venv`),
    and cmd.exe truncates the command at the first space otherwise.
    Double quotes are valid on both cmd.exe and POSIX shells.
    """
    return f"\"{sys.executable}\" -c \"print('validation ok')\""


def _quoted_fail_command() -> str:
    """A cross-platform command that exits 3 (same quoting rule)."""
    return f"\"{sys.executable}\" -c \"raise SystemExit(3)\""


class _ValCtx:
    def __init__(self, server):
        self.server = server
        self.reply = AsyncMock()
        self.reply_error = AsyncMock()
        self.emit = AsyncMock()


async def _admit_validated_run(native, validate_command: str, mode="chat"):
    project = await native.projects.create(
        id="proj_val", name="validated", validate_command=validate_command,
        root_path=str(native.root))
    await native.sessions.create(id="ses_val", title="s", project_id=project["id"])
    ctx = _ValCtx(native.server)
    task = asyncio.create_task(builtins.handle_agent_send_message(
        {"session_id": "ses_val", "content": "change some code", "mode": mode, "idempotency_key": "validated"}, ctx))
    # The stub signals via fired_run_id once inside run(); wait for it.
    for _ in range(1000):
        if native.fired_run_id:
            break
        await asyncio.sleep(0.01)
    if not native.fired_run_id:
        errs = [c.args for c in ctx.reply_error.await_args_list] if ctx.reply_error.await_args_list else []
        pytest.fail(f"stub core never entered run(); done={task.done()} reply_error={errs}")
    native.release.set()
    await asyncio.wait_for(task, 20)
    native.release.clear()
    return ctx


async def test_automatic_validation_runs_on_file_modifying_run(native) -> None:
    native.simulate_write = True
    await _admit_validated_run(native, _quoted_ok_command())
    run = (await native.dao.list_runs(session_id="ses_val"))[0]
    receipt = await native.dao.result_payload(run["id"])
    validation = receipt["verification"].get("validation")
    assert validation is not None, "file-modifying run must execute the validate command"
    assert validation["ok"] is True
    assert "validation ok" in validation["stdout"]
    assert receipt["verification"]["requires_review"] is False


async def test_failing_validation_forces_review(native) -> None:
    native.simulate_write = True
    await _admit_validated_run(native, _quoted_fail_command())
    run = (await native.dao.list_runs(session_id="ses_val"))[0]
    receipt = await native.dao.result_payload(run["id"])
    validation = receipt["verification"]["validation"]
    assert validation["ok"] is False
    assert validation["exit_code"] == 3
    assert receipt["verification"]["requires_review"] is True
    # The run still completes — evidence, not pipeline failure.
    assert run["status"] == "completed"


async def test_missing_fs_telemetry_does_not_suppress_validation(native) -> None:
    native.simulate_write = False
    await _admit_validated_run(native, _quoted_ok_command())
    run = (await native.dao.list_runs(session_id="ses_val"))[0]
    receipt = await native.dao.result_payload(run["id"])
    assert receipt["verification"]["verdict"] == "passed"
