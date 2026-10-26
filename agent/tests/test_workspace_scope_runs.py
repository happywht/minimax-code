"""v1.3.0 regression tests — workspace-root scoping across run entrypoints.

Pins the three wiring surfaces of per-project workspace roots:

1. **System-prompt advisory** — ``_build_system_prompt_extra`` injects the
   workspace-root notice only when the active root differs from the
   process default (zero prompt noise for default-root sessions).
2. **Sub-agent entrypoints** — ``agent.invoke`` / ``agent.spawn_subagent``
   are top-level handlers (no parent task context to inherit), so each
   admits a Native run whose frozen profile and working copy derive from
   the explicit source session, independently of the process default.
3. **Team runs** — ``teams.spawn`` scopes the whole orchestrator run to
   the driving session's root; members spawned via ``create_task`` copy
   the context and inherit it.

Plus the repo-map per-root cache (``_REPO_MAP_INDEXERS``) and the
``agent.send_message`` publish/reset lifecycle.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_agent_execution import agent_env as agent_env
from test_agent_execution import invoke as invoke_agent_root
from test_skill_execution import skill_env as skill_env

from minimax_code.app import set_projects_dao, set_sessions_dao
from minimax_code.config import Config
from minimax_code.ipc.builtins import (
    _build_system_prompt_extra,
    handle_agent_send_message,
)
from minimax_code.ipc.handlers_agents import register_agent_handlers
from minimax_code.ipc.handlers_teams import register_team_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.orchestrator import SubAgentRuntime, set_subagent_runtime
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.workspace_ctx import (
    current_root,
    env_or_cwd_root,
    set_current_root,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeContext:
    """Stand-in for :class:`Context` that captures reply / error."""

    def __init__(self, server: Any = None) -> None:
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None
        self.server = server

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}

    async def emit(self, event: str, data: Any = None, **kw: Any) -> None:
        pass


class _RootRecordingRuntime(SubAgentRuntime):
    """Stub runtime that records the active root at ``invoke`` time."""

    def __init__(self) -> None:
        super().__init__()
        self.observed: list[Path | None] = []

    def build(self, config: Any) -> Any:  # type: ignore[override]
        return config  # opaque handle — invoke never dereferences it

    async def invoke(  # type: ignore[no-untyped-def]
        self, handle, *, session_id: str, request: str
    ):
        self.observed.append(current_root())
        return {"text": "ok", "stub": True, "tool_calls": []}


class _RecordingTracker:
    """Tracker stub — no DB rows, just satisfies the lifecycle calls."""

    async def start_task(
        self, session_id: str, title: str, *, emit: Any = None
    ) -> str:
        return f"task_{uuid.uuid4().hex[:8]}"

    async def update(
        self, task_id: str, progress: Any, message: Any = None, *, emit: Any = None
    ) -> None:  # pragma: no cover — not asserted here
        pass

    async def complete(
        self, task_id: str, *, success: bool = True, error: str | None = None,
        emit: Any = None,
    ) -> None:  # pragma: no cover — not asserted here
        pass


class _RecordingOrchestrator:
    """Fake ``TeamOrchestrator`` — records the root seen inside ``run``."""

    def __init__(self, **kwargs: Any) -> None:
        self.observed: list[Path | None] = []

    async def run(  # type: ignore[no-untyped-def]
        self, team_name, request, *, session_id=None, parent_session_id=None
    ):
        self.observed.append(current_root())
        return SimpleNamespace(
            team_name=team_name,
            orchestration_mode="parallel",
            merged_text="done",
            agents_run=[],
            conflicts=[],
            task_id=f"teamrun_{uuid.uuid4().hex[:8]}",
            success=True,
        )


class _FakeSessionsDAO:
    """Single-row sessions DAO — enough for ``resolve_root_for_session``."""

    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    async def get(self, session_id: str) -> dict[str, Any] | None:
        return self._row if session_id == self._row["id"] else None


def _make_fake_db() -> AsyncMock:
    """Fake AsyncDatabase with awaitable fetch methods (p0-bugfixes pattern)."""
    db = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    db.fetchone = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    db.execute_insert = AsyncMock(return_value=None)
    return db


def _make_fake_runs_dao() -> MagicMock:
    from run_dao_stub import make_run_dao_stub

    return make_run_dao_stub()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
async def agent_dao(async_db: AsyncDatabase) -> AgentDAO:
    return AgentDAO(async_db)


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
    """Keep the workspace_ctx DAO singletons test-local."""
    yield
    set_projects_dao(None)
    set_sessions_dao(None)


@pytest.fixture(autouse=True)
def _no_workspace_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIMAX_CODE_WORKSPACE", raising=False)


@pytest.fixture(autouse=True)
def _clean_repo_map_cache():
    """Snapshot/restore the per-root repo-map cache around each test."""
    from minimax_code import app as app_mod

    prev_buckets = dict(app_mod._REPO_MAP_INDEXERS)
    prev_legacy = app_mod._REPO_MAP_INDEXER
    yield
    app_mod._REPO_MAP_INDEXERS.clear()
    app_mod._REPO_MAP_INDEXERS.update(prev_buckets)
    app_mod._REPO_MAP_INDEXER = prev_legacy


@pytest.fixture
def root_runtime():
    """Install the root-recording runtime for the test duration."""
    from minimax_code.orchestrator import get_subagent_runtime

    try:
        prev = get_subagent_runtime()
    except Exception:  # pragma: no cover — defensive
        prev = None
    runtime = _RootRecordingRuntime()
    set_subagent_runtime(runtime)
    yield runtime
    set_subagent_runtime(prev)


@pytest.fixture
def agent_handlers(agent_dao: AgentDAO):
    """``agent.*`` handlers bound to the test DAO + isolated tracker.

    Without the tracker stub the handler falls back to ``init_runtime``,
    which opens the DEFAULT data-dir database (FK failure here, plus a
    leaked aiosqlite connection that hangs pytest at exit).
    """
    from minimax_code.app import get_progress_tracker, set_progress_tracker

    prev_tracker = get_progress_tracker()
    set_progress_tracker(_RecordingTracker())
    server = IPCServer(Config())
    register_agent_handlers(server, dao=agent_dao)
    try:
        yield server._handlers
    finally:
        set_progress_tracker(prev_tracker)


@pytest.fixture
def team_handlers():
    """``team.*`` handlers with stub DAOs (orchestrator is patched per test)."""
    server = IPCServer(Config())
    register_team_handlers(server, dao=MagicMock(), agent_dao=MagicMock())
    return server._handlers


def _pid() -> str:
    return f"proj_{uuid.uuid4().hex[:10]}"


def _sid() -> str:
    return f"ses_{uuid.uuid4().hex[:10]}"


def _agname() -> str:
    return f"ag_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# 1. System-prompt advisory (conditional injection)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_advisory_injected_for_project_root(tmp_path: Path) -> None:
    proj = tmp_path / "projA"
    proj.mkdir()
    token = set_current_root(proj)
    try:
        with patch(
            "minimax_code.app.ensure_repo_map_indexer",
            new=AsyncMock(return_value=None),
        ):
            extra = await _build_system_prompt_extra(session_id="ses_x")
    finally:
        from minimax_code.workspace_ctx import reset_current_root

        reset_current_root(token)

    assert extra is not None
    assert "Workspace root for this session" in extra
    assert str(proj.resolve()) in extra


@pytest.mark.asyncio
async def test_prompt_advisory_absent_on_default_root() -> None:
    """A root equal to the process default adds no prompt noise."""
    token = set_current_root(env_or_cwd_root())
    try:
        with patch(
            "minimax_code.app.ensure_repo_map_indexer",
            new=AsyncMock(return_value=None),
        ):
            extra = await _build_system_prompt_extra(session_id="ses_x")
    finally:
        from minimax_code.workspace_ctx import reset_current_root

        reset_current_root(token)

    assert extra is not None
    assert "Workspace root for this session" not in extra


@pytest.mark.asyncio
async def test_prompt_advisory_absent_without_scope() -> None:
    assert current_root() is None
    with patch(
        "minimax_code.app.ensure_repo_map_indexer",
        new=AsyncMock(return_value=None),
    ):
        extra = await _build_system_prompt_extra(session_id="ses_x")

    assert extra is not None
    # The stale-note advisory is always present; the root one is not.
    assert "Stale-note advisory" in extra
    assert "Workspace root for this session" not in extra


# ---------------------------------------------------------------------------
# 2. Sub-agent entrypoints (agent.invoke / agent.spawn_subagent)
# ---------------------------------------------------------------------------


async def test_invoke_scopes_to_project_root(agent_env, monkeypatch):
    state = agent_env
    other = state.root / "global-b"
    other.mkdir()
    (other / "hello.txt").write_text("global-b",encoding="utf-8")
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(other))
    ctx = await invoke_agent_root(state)
    ctx.reply_error.assert_not_awaited()
    result = ctx.reply.await_args.args[0]
    assert Path(result["workspace"]["path"]).joinpath("hello.txt").read_text() == "source-a"
    assert result["workspace"]["path"] != str(other)
    assert current_root() is None



async def test_invoke_defaults_without_project(agent_env):
    # An explicit Native source session owns the selected workspace even inbox.
    ctx = await invoke_agent_root(agent_env)
    ctx.reply_error.assert_not_awaited()
    row = await agent_env.dao.get_run(ctx.reply.await_args.args[0]["run_id"])
    assert row["metadata"]["agent_execution"]["source_workspace"] == str(agent_env.source)



async def test_spawn_subagent_scopes_to_parent_session_root(agent_env):
    ctx = await invoke_agent_root(agent_env, "agent.spawn_subagent")
    ctx.reply_error.assert_not_awaited()
    result = ctx.reply.await_args.args[0]
    assert result["parent_session_id"] == "one"
    assert Path(result["workspace"]["path"]).joinpath("hello.txt").read_text() == "source-a"



# ---------------------------------------------------------------------------
# 3. Team runs (teams.spawn)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_team_spawn_scopes_to_session_root(
    team_handlers: dict[str, Any],
    projects_dao: ProjectsDAO,
    sessions_dao: SessionsDAO,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    proj = tmp_path / "teamroot"
    proj.mkdir()
    pid = _pid()
    await projects_dao.create(id=pid, name="T", root_path=str(proj))
    sid = _sid()
    await sessions_dao.create(id=sid, title="team session", project_id=pid)
    set_projects_dao(projects_dao)
    set_sessions_dao(sessions_dao)

    captured: list[_RecordingOrchestrator] = []

    class _SpyOrchestrator(_RecordingOrchestrator):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            captured.append(self)

    monkeypatch.setattr(
        "minimax_code.orchestrator.team_orchestrator.TeamOrchestrator",
        _SpyOrchestrator,
    )

    ctx = _FakeContext()
    await team_handlers["team.spawn"](
        {"team_name": "review", "request": "do it", "session_id": sid}, ctx
    )

    assert ctx.error_value is None, ctx.error_value
    assert ctx.reply_value is not None
    assert ctx.reply_value["success"] is True
    assert len(captured) == 1
    assert captured[0].observed == [proj.resolve()]
    assert current_root() is None


@pytest.mark.asyncio
async def test_team_spawn_defaults_without_project(
    team_handlers: dict[str, Any],
    sessions_dao: SessionsDAO,
    projects_dao: ProjectsDAO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = _sid()
    await sessions_dao.create(id=sid, title="no project")
    set_sessions_dao(sessions_dao)
    set_projects_dao(projects_dao)

    captured: list[_RecordingOrchestrator] = []

    class _SpyOrchestrator(_RecordingOrchestrator):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            captured.append(self)

    monkeypatch.setattr(
        "minimax_code.orchestrator.team_orchestrator.TeamOrchestrator",
        _SpyOrchestrator,
    )

    ctx = _FakeContext()
    await team_handlers["team.spawn"](
        {"team_name": "review", "request": "do it", "session_id": sid}, ctx
    )

    assert ctx.error_value is None, ctx.error_value
    assert len(captured) == 1
    # The default root (env/cwd) is published — never None.
    assert captured[0].observed == [env_or_cwd_root()]


# ---------------------------------------------------------------------------
# 4. Repo-map indexer cache (per-root buckets)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repo_map_indexers_bucketed_by_root(tmp_path: Path) -> None:
    """Root A and root B get independent indexers; A is cached."""
    from minimax_code import app as app_mod

    ra, rb = tmp_path / "ra", tmp_path / "rb"
    ra.mkdir()
    rb.mkdir()

    ix_a1 = await app_mod.ensure_repo_map_indexer(ra)
    ix_a2 = await app_mod.ensure_repo_map_indexer(ra)
    ix_b = await app_mod.ensure_repo_map_indexer(rb)

    assert ix_a1 is not None
    assert ix_a2 is ix_a1, "same root must reuse the cached indexer"
    assert ix_b is not ix_a1, "different root must get its own indexer"

    # Legacy single-slot getter still serves the default-root bucket.
    assert app_mod.get_repo_map_indexer() in (
        None,
        app_mod._REPO_MAP_INDEXERS.get(str(env_or_cwd_root())),
        app_mod._REPO_MAP_INDEXER,
    )


@pytest.mark.asyncio
async def test_repo_map_default_root_writes_legacy_slot(tmp_path: Path) -> None:
    """Ensuring the default root keeps the legacy getter in sync."""
    from minimax_code import app as app_mod

    ix = await app_mod.ensure_repo_map_indexer(env_or_cwd_root())
    assert ix is not None
    assert app_mod.get_repo_map_indexer() is ix
    assert app_mod._REPO_MAP_INDEXER is ix


# ---------------------------------------------------------------------------
# 5. agent.send_message publish/reset lifecycle
# ---------------------------------------------------------------------------


async def test_send_message_publishes_and_resets_root(agent_env, monkeypatch):
    from test_budget_transport import client, response
    from test_run_service import Context

    from minimax_code import app
    state = agent_env
    observed = []
    def wire(request):
        observed.append(current_root())
        return response()
    llm = client(wire, require_budget=True)
    monkeypatch.setattr(app, "get_subagent_llm", lambda:llm)
    monkeypatch.setattr(app, "ensure_hook_manager", lambda:None)
    ctx = Context(state.server)
    try:
        await handle_agent_send_message({"content":"hi","session_id":"one"},ctx)
        ctx.reply_error.assert_not_awaited()
        assert observed == [state.source]
        assert current_root() is None
    finally:
        await llm.close()
