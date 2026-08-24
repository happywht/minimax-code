"""Tests for per-project workspace root resolution (v1.3.0).

Covers the resolution priority chain, ContextVar concurrency semantics
(parallel scopes + child-task inheritance without write-back), tool-level
containment (strict isolation: a session rooted at project A cannot touch
project B), and the exec default-cwd anchoring.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

from minimax_code.agent.tools.file_ops import (
    PathSecurityError,
    _default_workspace,
    safe_resolve,
)
from minimax_code.app import set_projects_dao, set_sessions_dao
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.workspace_ctx import (
    current_root,
    env_or_cwd_root,
    reset_current_root,
    resolve_root_for_project_id,
    resolve_root_for_session,
    session_root_scope,
    set_current_root,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return make_temp_database_path(tmp_path)


@pytest.fixture
async def async_db(db_path: Path) -> AsyncDatabase:
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def projects_dao(async_db: AsyncDatabase) -> ProjectsDAO:
    dao = ProjectsDAO(async_db)
    await dao.ensure_inbox()
    return dao


@pytest.fixture
async def sessions_dao(async_db: AsyncDatabase) -> SessionsDAO:
    return SessionsDAO(async_db)


@pytest.fixture(autouse=True)
def _clean_daos():
    yield
    set_projects_dao(None)
    set_sessions_dao(None)


@pytest.fixture(autouse=True)
def _no_workspace_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIMAX_CODE_WORKSPACE", raising=False)


def _pid() -> str:
    return f"proj_{uuid.uuid4().hex[:10]}"


def _sid() -> str:
    return f"ses_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Resolution priority chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_root_falls_back_to_cwd_without_project(
    projects_dao: ProjectsDAO, sessions_dao: SessionsDAO, tmp_path: Path
) -> None:
    set_projects_dao(projects_dao)
    set_sessions_dao(sessions_dao)
    sid = _sid()
    await sessions_dao.create(id=sid, title="no project")
    root = await resolve_root_for_session(sid)
    assert root == Path(os.getcwd()).resolve()


@pytest.mark.asyncio
async def test_project_root_wins_over_cwd(
    projects_dao: ProjectsDAO, sessions_dao: SessionsDAO, tmp_path: Path
) -> None:
    proj_root = tmp_path / "projA"
    proj_root.mkdir()
    pid = _pid()
    await projects_dao.create(id=pid, name="A", root_path=str(proj_root))
    sid = _sid()
    await sessions_dao.create(id=sid, title="in A", project_id=pid)

    set_projects_dao(projects_dao)
    set_sessions_dao(sessions_dao)

    assert await resolve_root_for_session(sid) == proj_root.resolve()
    assert await resolve_root_for_project_id(pid) == proj_root.resolve()


@pytest.mark.asyncio
async def test_worktree_workspace_path_wins_over_project_root(
    projects_dao: ProjectsDAO, sessions_dao: SessionsDAO, tmp_path: Path
) -> None:
    proj_root = tmp_path / "projA"
    proj_root.mkdir()
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()
    pid = _pid()
    await projects_dao.create(id=pid, name="A", root_path=str(proj_root))
    sid = _sid()
    await sessions_dao.create(
        id=sid,
        title="worktree",
        project_id=pid,
        workspace_mode="worktree",
        workspace_path=str(wt_path),
    )

    set_projects_dao(projects_dao)
    set_sessions_dao(sessions_dao)

    assert await resolve_root_for_session(sid) == wt_path.resolve()


@pytest.mark.asyncio
async def test_missing_project_root_dir_falls_back(
    projects_dao: ProjectsDAO, sessions_dao: SessionsDAO, tmp_path: Path
) -> None:
    """A deleted root directory degrades to the process root, not a crash."""
    pid = _pid()
    await projects_dao.create(id=pid, name="Gone", root_path=str(tmp_path / "deleted"))
    sid = _sid()
    await sessions_dao.create(id=sid, title="x", project_id=pid)

    set_projects_dao(projects_dao)
    set_sessions_dao(sessions_dao)

    assert await resolve_root_for_session(sid) == env_or_cwd_root()
    # project-id resolution also degrades to the process root
    assert await resolve_root_for_project_id(pid) == env_or_cwd_root()


@pytest.mark.asyncio
async def test_env_root_used_when_project_has_no_root(
    projects_dao: ProjectsDAO, sessions_dao: SessionsDAO, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_root = tmp_path / "envroot"
    env_root.mkdir()
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(env_root))
    pid = _pid()
    await projects_dao.create(id=pid, name="NoRoot")
    sid = _sid()
    await sessions_dao.create(id=sid, title="x", project_id=pid)

    set_projects_dao(projects_dao)
    set_sessions_dao(sessions_dao)

    assert await resolve_root_for_session(sid) == env_root.resolve()
    assert await resolve_root_for_session(None) == env_root.resolve()


# ---------------------------------------------------------------------------
# ContextVar semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_sets_and_resets_root(tmp_path: Path) -> None:
    assert current_root() is None
    token = set_current_root(tmp_path)
    assert current_root() == tmp_path.resolve()
    reset_current_root(token)
    assert current_root() is None


@pytest.mark.asyncio
async def test_parallel_scopes_do_not_interfere(tmp_path: Path) -> None:
    """Two concurrent scopes each see their own root."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()

    async def probe(root: Path) -> Path:
        token = set_current_root(root)
        try:
            await asyncio.sleep(0.01)  # let both tasks interleave
            return current_root()  # type: ignore[return-value]
        finally:
            reset_current_root(token)

    results = await asyncio.gather(probe(a), probe(b))
    assert results[0] == a.resolve()
    assert results[1] == b.resolve()
    assert current_root() is None


@pytest.mark.asyncio
async def test_child_task_inherits_parent_root(tmp_path: Path) -> None:
    """create_task copies context — children see the parent run's root."""
    token = set_current_root(tmp_path)

    async def child() -> Path | None:
        return current_root()

    inherited = await asyncio.create_task(child())
    reset_current_root(token)
    assert inherited == tmp_path.resolve()


@pytest.mark.asyncio
async def test_child_task_set_does_not_leak_to_parent(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()

    async def child() -> None:
        token = set_current_root(other)
        await asyncio.sleep(0)
        reset_current_root(token)

    await asyncio.create_task(child())
    assert current_root() is None  # parent unaffected


# ---------------------------------------------------------------------------
# Tool-level containment — strict isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_resolve_anchors_relative_to_session_root(tmp_path: Path) -> None:
    proj = tmp_path / "projA"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "main.py").write_text("print('hi')")

    token = set_current_root(proj)
    try:
        resolved = safe_resolve("src/main.py")
        assert resolved == (proj / "src" / "main.py").resolve()
        assert _default_workspace() == proj.resolve()
    finally:
        reset_current_root(token)


@pytest.mark.asyncio
async def test_safe_resolve_rejects_cross_root_access(tmp_path: Path) -> None:
    """Strict isolation: session rooted at A cannot read B, even by abs path."""
    proj_a = tmp_path / "projA"
    proj_b = tmp_path / "projB"
    proj_a.mkdir()
    proj_b.mkdir()
    (proj_b / "secret.txt").write_text("B-only")

    token = set_current_root(proj_a)
    try:
        with pytest.raises(PathSecurityError):
            safe_resolve("../projB/secret.txt")
        with pytest.raises(PathSecurityError):
            safe_resolve(str(proj_b / "secret.txt"))
    finally:
        reset_current_root(token)


@pytest.mark.asyncio
async def test_scope_resets_after_block(tmp_path: Path) -> None:
    async with session_root_scope(None) as root:
        assert current_root() == root
    assert current_root() is None


# ---------------------------------------------------------------------------
# Exec default cwd
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_default_cwd_is_session_root(tmp_path: Path) -> None:
    """exec with no explicit cwd anchors at the session root."""
    from minimax_code.agent.tools.terminal import ExecCommandTool

    proj = tmp_path / "projA"
    proj.mkdir()
    # A cross-platform cwd probe: `pwd` doesn't exist on Windows, but a
    # tiny python script printing os.getcwd() works everywhere.
    (proj / "probe.py").write_text("import os; print(os.getcwd())", encoding="utf-8")

    token = set_current_root(proj)
    try:
        result = await ExecCommandTool().run(cmd=["python", "probe.py"])
        assert result.success, str(result.error)
        stdout = str(result.output["stdout"]).strip()
        assert Path(stdout).resolve() == proj.resolve()
    finally:
        reset_current_root(token)
