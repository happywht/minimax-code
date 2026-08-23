"""v1.2.2 regression tests — SubAgentConfig passthrough + failed-task close-out.

Two handler-level bugs pinned here:

1. **Config passthrough break** — ``agent.invoke`` / ``agent.spawn_subagent``
   used to build :class:`SubAgentConfig` from the DAO row with only five
   fields (name / system_prompt / tool_allowlist / model / id), silently
   dropping the persisted ``max_iterations`` / ``temperature`` / ``skills``.
   A sub-agent saved with a 200-iteration budget actually ran at the
   default 50.

2. **Zombie task rows** — when ``agent.invoke`` blew up after
   ``tracker.start_task`` (LLM failure, emit error, cancellation), the
   except branch only replied with an error envelope; the ``tasks`` row
   stayed ``running`` forever. The fix wraps the run body in an inner
   try/except that calls ``tracker.complete(success=False)`` before
   re-raising.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code.config import Config
from minimax_code.ipc.handlers_agents import _config_from_row, register_agent_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.orchestrator import SubAgentRuntime, set_subagent_runtime
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeContext:
    """Stand-in for :class:`Context` that captures reply / error / emit."""

    def __init__(self) -> None:
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}

    async def emit(self, event: str, data: Any) -> None:
        self.events.append((event, data))


class _CaptureRuntime(SubAgentRuntime):
    """Stub runtime that records every config handed to ``build``."""

    def __init__(self) -> None:
        super().__init__()
        self.built: list[Any] = []

    def build(self, config: Any) -> Any:  # type: ignore[override]
        self.built.append(config)
        return super().build(config)


class _BoomRuntime(SubAgentRuntime):
    """SubAgentRuntime that always raises — exercises the failure path."""

    async def invoke(self, handle, *, session_id: str, request: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom — simulated LLM failure")


class _RecordingTracker:
    """Tracker stub — no DB rows, just records the lifecycle calls."""

    def __init__(self) -> None:
        self.started: list[tuple[str, str, str]] = []
        self.completed: list[tuple[str, bool, str | None]] = []

    async def start_task(
        self, session_id: str, title: str, *, emit: Any = None
    ) -> str:
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        self.started.append((task_id, session_id, title))
        return task_id

    async def update(
        self,
        task_id: str,
        progress: Any,
        message: Any = None,
        *,
        emit: Any = None,
    ) -> None:  # pragma: no cover — not exercised by these tests
        pass

    async def complete(
        self,
        task_id: str,
        *,
        success: bool = True,
        error: str | None = None,
        emit: Any = None,
    ) -> None:
        self.completed.append((task_id, success, error))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return make_temp_database_path(tmp_path)


@pytest.fixture
async def agent_dao(db_path: Path) -> AgentDAO:
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    try:
        yield AgentDAO(db)
    finally:
        await db.close()


@pytest.fixture
def capture_runtime() -> _CaptureRuntime:
    """Install a config-capturing stub runtime for the test duration."""
    from minimax_code.orchestrator import get_subagent_runtime

    prev = None
    try:
        prev = get_subagent_runtime()
    except Exception:  # pragma: no cover — defensive
        prev = None
    runtime = _CaptureRuntime()
    set_subagent_runtime(runtime)
    yield runtime
    set_subagent_runtime(prev)


@pytest.fixture
def tracker() -> _RecordingTracker:
    """Install a recording progress tracker for the test duration."""
    from minimax_code.app import get_progress_tracker, set_progress_tracker

    prev = get_progress_tracker()
    rec = _RecordingTracker()
    set_progress_tracker(rec)
    yield rec
    set_progress_tracker(prev)


@pytest.fixture
def handlers(agent_dao: AgentDAO, capture_runtime: _CaptureRuntime):
    # Isolate the progress tracker too: without this the handler falls
    # back to the global runtime, which opens the DEFAULT data-dir
    # database (a FOREIGN KEY failure here, plus a leaked aiosqlite
    # connection that hangs pytest at exit).
    from minimax_code.app import get_progress_tracker, set_progress_tracker

    prev_tracker = get_progress_tracker()
    set_progress_tracker(_RecordingTracker())
    server = IPCServer(
        config=Config.from_env(),
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )
    register_agent_handlers(server, dao=agent_dao)
    try:
        yield server._handlers
    finally:
        set_progress_tracker(prev_tracker)


def _unique(prefix: str = "ag") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Tests: config passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_passes_extended_config_fields(
    handlers: dict[str, Any],
    agent_dao: AgentDAO,
    capture_runtime: _CaptureRuntime,
) -> None:
    name = _unique("full")
    await agent_dao.upsert(
        name=name,
        system_prompt="You are precise.",
        tool_allowlist=["read_file"],
        skills=["commit-helper"],
        max_iterations=200,
        temperature=0.7,
        description="budget-heavy variant",
    )
    ctx = _FakeContext()
    await handlers["agent.invoke"]({"name": name, "request": "hi"}, ctx)

    assert ctx.error_value is None, ctx.error_value
    assert len(capture_runtime.built) == 1
    cfg = capture_runtime.built[0]
    assert cfg.max_iterations == 200, "persisted max_iterations was dropped"
    assert cfg.temperature == pytest.approx(0.7), "persisted temperature was dropped"
    assert cfg.skills == ["commit-helper"], "persisted skills were dropped"
    assert cfg.description == "budget-heavy variant"
    assert cfg.system_prompt == "You are precise."
    assert cfg.tool_allowlist == ["read_file"]


@pytest.mark.asyncio
async def test_spawn_passes_extended_config_fields(
    handlers: dict[str, Any],
    agent_dao: AgentDAO,
    capture_runtime: _CaptureRuntime,
) -> None:
    name = _unique("spawn")
    await agent_dao.upsert(
        name=name,
        system_prompt="Write code.",
        skills=["code-review", "test-generator"],
        max_iterations=120,
        temperature=0.2,
    )
    ctx = _FakeContext()
    await handlers["agent.spawn_subagent"]({"name": name, "request": "hi"}, ctx)

    assert ctx.error_value is None, ctx.error_value
    assert len(capture_runtime.built) == 1
    cfg = capture_runtime.built[0]
    assert cfg.max_iterations == 120, "persisted max_iterations was dropped"
    assert cfg.temperature == pytest.approx(0.2)
    assert cfg.skills == ["code-review", "test-generator"]


def test_config_from_row_keeps_defaults_for_sparse_rows() -> None:
    """Rows without extended columns must keep the dataclass defaults."""
    cfg = _config_from_row(
        {
            "name": "legacy",
            "system_prompt": "old",
            "tool_allowlist": ["read_file"],
            "model": None,
            "id": "agent_abc",
        }
    )
    assert cfg.max_iterations == 50  # dataclass default, not None
    assert cfg.temperature is None
    assert cfg.skills is None
    assert cfg.tags is None
    assert cfg.enabled is True
    assert cfg.name == "legacy"


def test_config_from_row_coerces_types() -> None:
    """SQLite integers/floats come back as int/float — the helper coerces."""
    cfg = _config_from_row(
        {
            "name": "typed",
            "max_iterations": 3,
            "temperature": 1,
            "skills": ["a"],
            "tags": ["b"],
            "enabled": 0,
        }
    )
    assert cfg.max_iterations == 3
    assert cfg.temperature == pytest.approx(1.0)
    assert cfg.skills == ["a"]
    assert cfg.tags == ["b"]
    assert cfg.enabled is False


# ---------------------------------------------------------------------------
# Tests: failed-task close-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_failure_closes_task_row(
    handlers: dict[str, Any],
    agent_dao: AgentDAO,
    tracker: _RecordingTracker,
) -> None:
    """Runtime failure after start_task must mark the row failed, not
    leave it ``running`` forever."""
    set_subagent_runtime(_BoomRuntime())
    try:
        name = _unique("boom")
        await agent_dao.upsert(name=name, system_prompt="x")
        ctx = _FakeContext()
        await handlers["agent.invoke"]({"name": name, "request": "hi"}, ctx)

        assert ctx.reply_value is None
        assert ctx.error_value is not None
        assert ctx.error_value["code"] == -32603

        # The zombie-row fix: complete(success=False, error=...) fired.
        assert tracker.started, "start_task should have been called"
        assert len(tracker.completed) == 1
        task_id, success, error = tracker.completed[0]
        assert task_id == tracker.started[0][0]
        assert success is False
        assert "boom" in (error or "")
    finally:
        set_subagent_runtime(None)


@pytest.mark.asyncio
async def test_invoke_success_keeps_normal_complete(
    handlers: dict[str, Any],
    agent_dao: AgentDAO,
    tracker: _RecordingTracker,
    capture_runtime: _CaptureRuntime,
) -> None:
    """Happy path unchanged: exactly one complete(success=True)."""
    name = _unique("happy")
    await agent_dao.upsert(name=name, system_prompt="x")
    ctx = _FakeContext()
    await handlers["agent.invoke"]({"name": name, "request": "hi"}, ctx)

    assert ctx.error_value is None, ctx.error_value
    assert ctx.reply_value is not None
    assert ctx.reply_value["task_id"] == tracker.started[0][0]
    assert len(tracker.completed) == 1
    assert tracker.completed[0][1] is True
