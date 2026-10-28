"""M3-C tests — spawn concurrency cap, run baseline checkpoints, worktree merge.

- **concurrency cap**: ``MINIMAX_CODE_MAX_SUBAGENTS`` builds the driving
  semaphore (default 2, ``<=0`` disables, garbage falls back).
- **baseline checkpoint**: every file-capable run snapshots its workspace
  (``git stash create`` — a dangling ref, the tree is untouched) before
  the first tool; a non-git workspace degrades to no checkpoint and the
  run proceeds.
- **worktree merge**: commit → validate inside the worktree → --no-ff
  merge into the main checkout; conflicts abort and report; a failing
  validation stops the merge before it starts.
"""

from __future__ import annotations

import asyncio
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from minimax_code.config import Config
from minimax_code.ipc import builtins
from minimax_code.ipc.handlers_workspace import register_workspace_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.runs import get_run_service
from minimax_code.storage.dao.checkpoints import CheckpointDAO
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase

# ---------------------------------------------------------------------------
# Concurrency cap
# ---------------------------------------------------------------------------


def test_spawn_semaphore_env_resolution(monkeypatch) -> None:
    import minimax_code.agent.tools.subagents as mod

    monkeypatch.delenv("MINIMAX_CODE_MAX_SUBAGENTS", raising=False)
    monkeypatch.setattr(mod, "_MAX_CONCURRENT_SUBAGENTS", None)
    assert mod._spawn_semaphore() is not None  # default cap of 2

    monkeypatch.setattr(mod, "_MAX_CONCURRENT_SUBAGENTS", None)
    monkeypatch.setenv("MINIMAX_CODE_MAX_SUBAGENTS", "0")
    assert mod._spawn_semaphore() is None  # cap disabled

    monkeypatch.setattr(mod, "_MAX_CONCURRENT_SUBAGENTS", None)
    monkeypatch.setenv("MINIMAX_CODE_MAX_SUBAGENTS", "5")
    sem = mod._spawn_semaphore()
    assert sem is not None and sem._value == 5

    monkeypatch.setattr(mod, "_MAX_CONCURRENT_SUBAGENTS", None)
    monkeypatch.setenv("MINIMAX_CODE_MAX_SUBAGENTS", "bogus")
    sem = mod._spawn_semaphore()
    assert sem is not None and sem._value == 2  # garbage falls back
    monkeypatch.setattr(mod, "_MAX_CONCURRENT_SUBAGENTS", None)


# ---------------------------------------------------------------------------
# Run baseline checkpoint — end to end through the native pipeline
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=repo, check=True)
    return ""


class Context:
    def __init__(self, server):
        self.server = server
        self.reply = AsyncMock()
        self.reply_error = AsyncMock()
        self.emit = AsyncMock()


@pytest.fixture
async def native(monkeypatch, tmp_path):
    from minimax_code import agent, app

    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=workspace, check=True)
    (workspace / "seed.txt").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=workspace, check=True)

    data_dir = tmp_path / "data"
    db = AsyncDatabase(tmp_path / "base.db")
    await db.connect()
    await db.migrate()
    sessions = SessionsDAO(db)
    await sessions.create(id="one", title="one", workspace_path=str(workspace))
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(workspace))
    monkeypatch.setattr(app, "init_runtime", AsyncMock())
    monkeypatch.setattr(app, "get_db", lambda: db)
    monkeypatch.setattr(app, "get_sessions_dao", lambda: sessions)
    monkeypatch.setattr(app, "get_projects_dao", lambda: None)
    import minimax_code.storage.db as storage_db

    monkeypatch.setattr(storage_db, "ensure_data_dir", lambda: data_dir)
    monkeypatch.setattr(app, "ensure_codebase_indexer", lambda *a: None)
    monkeypatch.setattr(app, "get_codebase_indexer", lambda: None)
    monkeypatch.setattr(app, "ensure_hook_manager", lambda: None)
    monkeypatch.setattr(app, "ensure_telemetry_engine", lambda: None)
    monkeypatch.setattr(app, "get_subagent_llm", lambda: SimpleNamespace(default_model="t", mock=True))
    monkeypatch.setattr(builtins, "_build_system_prompt_extra", AsyncMock(return_value=None))
    monkeypatch.setattr("minimax_code.ipc.handlers_permissions._ensure_permission_store", AsyncMock(return_value=None))
    server = IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO())
    release = asyncio.Event()

    class Core:
        def __init__(self, **kwargs):
            self.config = kwargs["config"]
            self.llm = kwargs["llm"]
            self.budget = None
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

        async def run(self, *, session_id, user_message):
            await release.wait()
            from types import SimpleNamespace as NS

            return NS(final_text="done", iterations=1, usage={}, truncated=False,
                      cancelled=False, compactions=0, budget_exhausted=False,
                      tool_calls=[], tool_results=[])

    monkeypatch.setattr(agent, "AgentCore", Core)
    native = SimpleNamespace(db=db, server=server, dao=AgentRunsDAO(db),
                             sessions=sessions, release=release,
                             workspace=workspace, data_dir=data_dir)
    try:
        yield native
    finally:
        await get_run_service(server).shutdown()
        await db.close()


async def _send(native, session_id="one"):
    ctx = Context(native.server)
    task = asyncio.create_task(builtins.handle_agent_send_message(
        {"session_id": session_id, "content": "work"}, ctx))
    await asyncio.sleep(1.5)  # baseline checkpoint runs before the loop
    native.release.set()
    await asyncio.wait_for(task, 5)
    native.release.clear()
    return ctx


async def test_run_creates_baseline_checkpoint(native) -> None:
    await _send(native)
    runs = await native.dao.list_runs(session_id="one")
    ckpt_id = runs[0]["metadata"].get("baseline_checkpoint")
    assert ckpt_id, "run metadata must record its baseline checkpoint"
    rows = await CheckpointDAO(native.db).list_by_session(session_id="one")
    labels = [row["label"] for row in rows]
    assert "run-baseline" in labels
    row = next(r for r in rows if r["label"] == "run-baseline")
    assert row["id"] == ckpt_id  # the checkpoint_id doubles as the row key
    # git stash create is a dangling ref — the working tree is untouched.
    assert native.workspace.is_dir()


async def test_non_git_workspace_still_runs(native) -> None:
    # Point the session at a plain directory (no .git) — the baseline
    # degrades to a warning and the run completes normally.
    plain = native.workspace.parent / "plain"
    plain.mkdir(exist_ok=True)
    await native.sessions.create(id="two", title="two", workspace_path=str(plain))
    await _send(native, session_id="two")
    runs = await native.dao.list_runs(session_id="two")
    assert runs[0]["status"] == "completed"
    assert "baseline_checkpoint" not in runs[0]["metadata"]


# ---------------------------------------------------------------------------
# workspace.merge_worktree — real git integration
# ---------------------------------------------------------------------------


@pytest.fixture
async def merge_env(tmp_path, monkeypatch):
    from minimax_code import app

    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=main, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=main, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=main, check=True)
    (main / "code.txt").write_text("v1", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=main, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=main, check=True)

    wt_root = tmp_path / "wts"
    wt_root.mkdir()
    wt = wt_root / "ses_wt"
    subprocess.run(["git", "worktree", "add", "--detach", str(wt), "HEAD"],
                   cwd=main, check=True)

    db = AsyncDatabase(tmp_path / "m.db")
    await db.connect()
    await db.migrate()
    sessions = SessionsDAO(db)
    projects = ProjectsDAO(db)
    await sessions.create(id="ses_wt", title="wt", workspace_mode="worktree",
                          workspace_path=str(wt), base_branch="main",
                          project_id="inbox")
    server = IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO())
    register_workspace_handlers(server, dao=sessions, repo_root=main,
                                worktree_root=wt_root)
    env = SimpleNamespace(db=db, server=server, sessions=sessions, projects=projects,
                          main=main, wt=wt, wt_root=wt_root)
    try:
        # Restore the caller's cache before closing this fixture's database.
        with monkeypatch.context() as fixture_patch:
            fixture_patch.setattr(app, "_PROJECTS_DAO", projects)
            yield env
    finally:
        await db.close()


async def _merge(env, extra: dict[str, Any] | None = None):
    ctx = Context(env.server)
    await env.server._handlers["workspace.merge_worktree"](
        {"session_id": "ses_wt", **(extra or {})}, ctx)
    return ctx


async def test_merge_commits_and_merges_into_main(merge_env) -> None:
    (merge_env.wt / "code.txt").write_text("v2 from worktree", encoding="utf-8")
    (merge_env.wt / "new.txt").write_text("added", encoding="utf-8")
    ctx = await _merge(merge_env)
    result = ctx.reply.await_args.args[0]
    assert result["merged"] is True
    assert result["committed_pending"] is True
    assert result["main_branch"] == "main"
    # Main checkout now carries the worktree's content.
    assert (merge_env.main / "code.txt").read_text(encoding="utf-8") == "v2 from worktree"
    assert (merge_env.main / "new.txt").read_text(encoding="utf-8") == "added"
    # Merging again with no new work is a no-op.
    ctx2 = await _merge(merge_env)
    assert ctx2.reply.await_args.args[0]["reason"] == "already_merged"


async def test_merge_conflicts_abort_and_report(merge_env) -> None:
    # Diverge: the worktree edits code.txt, and main edits it too.
    (merge_env.wt / "code.txt").write_text("worktree version", encoding="utf-8")
    (merge_env.main / "code.txt").write_text("main version", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=merge_env.main, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "main diverges"], cwd=merge_env.main, check=True)

    ctx = await _merge(merge_env)
    result = ctx.reply.await_args.args[0]
    assert result["merged"] is False
    assert result["reason"] == "conflicts"
    assert "code.txt" in result["conflicts"]
    # The merge was aborted — main's working tree is clean of conflict markers.
    status = subprocess.run(["git", "status", "--porcelain"], cwd=merge_env.main,
                            capture_output=True, text=True)
    assert status.stdout.strip() == ""


async def test_merge_stops_on_failed_validation(merge_env) -> None:
    """A failing project validate command stops the merge before it starts."""
    projects = merge_env.projects
    await projects.create(id="proj_v", name="v",
                          validate_command="python -c \"raise SystemExit(9)\"")
    await merge_env.sessions.update("ses_wt", project_id="proj_v")

    (merge_env.wt / "code.txt").write_text("broken change", encoding="utf-8")
    ctx = await _merge(merge_env, {"run_validate": True})
    result = ctx.reply.await_args.args[0]
    assert result["merged"] is False
    assert result["reason"] == "validation_failed"
    assert result["validation"]["exit_code"] == 9
    # The main checkout never saw the change.
    assert (merge_env.main / "code.txt").read_text(encoding="utf-8") == "v1"


async def test_merge_skips_validation_when_disabled(merge_env) -> None:
    projects = merge_env.projects
    await projects.create(id="proj_v", name="v",
                          validate_command="python -c \"raise SystemExit(9)\"")
    await merge_env.sessions.update("ses_wt", project_id="proj_v")

    (merge_env.wt / "code.txt").write_text("v2 anyway", encoding="utf-8")
    ctx = await _merge(merge_env, {"run_validate": False})
    result = ctx.reply.await_args.args[0]
    assert result["merged"] is True  # explicit opt-out merges without validation
    assert result["validation"] is None


async def test_merge_fixture_never_reopens_previous_runtime_database(tmp_path, monkeypatch):
    from minimax_code import app

    old_db = AsyncDatabase(tmp_path / "previous.sqlite3")
    scope = None
    try:
        await old_db.connect()
        await old_db.migrate()
        await old_db.close()
        old_projects = ProjectsDAO(old_db)
        monkeypatch.setattr(app, "_PROJECTS_DAO", old_projects)
        runtime_path = tmp_path / "runtime"
        runtime_path.mkdir()
        scope = merge_env.__wrapped__(runtime_path, monkeypatch)
        env = await anext(scope)
        assert app.get_projects_dao() is env.projects
        (env.wt / "code.txt").write_text("owned merge", encoding="utf-8")
        ctx = await _merge(env)
        assert ctx.reply.await_args.args[0]["merged"] is True
        assert old_db._conn is None
        await scope.aclose()
        assert env.db._conn is None
        assert app.get_projects_dao() is old_projects
        assert old_db._conn is None
    finally:
        if scope is not None:
            await scope.aclose()
        await old_db.close()
