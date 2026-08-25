"""v1.5.1 — in-flight write registry: cross-run concurrent-writer warnings.

The registry (``file_ops._INFLIGHT_WRITES``) maps workspace paths to the
sub-agent run currently claiming them. It is advisory only — writes are
never blocked; instead the tool output carries a ``concurrent_writer``
warning so the LLM can coordinate or arm ``expected_sha256``. These
tests pin:

* claim-on-write, rival warning, self-exclusion, main-agent visibility
* claim release on run completion (via ``_drive_run``'s finally)
* case folding, unrelated-file isolation, no claim from the main agent
* fs-bus emit attribution (``run_id`` attribute) for the change notes

All tool exercises go through ``registry.dispatch`` (project convention
since v1.4.1).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.tools import get_default_registry
from minimax_code.agent.tools.file_ops import (
    _INFLIGHT_WRITES,
    claim_write,
    in_flight_writer,
    release_run_writes,
)
from minimax_code.orchestrator.subagent import SubAgentRuntime, set_subagent_runtime
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.workspace_ctx import (
    current_run_id,
    reset_current_root,
    reset_current_run_id,
    set_current_root,
    set_current_run_id,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace_root(tmp_path: Path):
    root = tmp_path / "proj_root"
    root.mkdir()
    token = set_current_root(root)
    try:
        yield root
    finally:
        reset_current_root(token)


@pytest.fixture(autouse=True)
def clean_registry():
    _INFLIGHT_WRITES.clear()
    yield
    _INFLIGHT_WRITES.clear()


@pytest.fixture
async def app_db(tmp_path: Path):
    from minimax_code import app
    from minimax_code.storage.dao.sessions import SessionsDAO

    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(app, "_DB_SINGLETON", db)
    app.set_sessions_dao(SessionsDAO(db))
    try:
        yield db
    finally:
        monkey.undo()
        app.set_sessions_dao(None)
        await db.close()


@contextmanager
def run_as(run_id: str | None) -> Iterator[None]:
    """Publish a run id (None = main agent) for the block; reset after."""
    token = set_current_run_id(run_id)
    try:
        yield
    finally:
        reset_current_run_id(token)


async def _dispatch(name: str, args: dict) -> Any:
    return await get_default_registry().dispatch(name, args)


async def _seed_agent(db: AsyncDatabase) -> str:
    dao = AgentDAO(db)
    name = f"wreg_{uuid.uuid4().hex[:8]}"
    await dao.upsert(name=name, system_prompt="you are a test agent")
    return name


# ---------------------------------------------------------------------------
# Claim / warn / release semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rival_run_write_warns_but_never_blocks(workspace_root: Path) -> None:
    with run_as("run_a"):
        first = await _dispatch("write_file", {"path": "shared.txt", "content": "a"})
        assert first.success, first.error
        assert "concurrent_writer" not in first.output

    with run_as("run_b"):
        second = await _dispatch("write_file", {"path": "shared.txt", "content": "b"})
    # Advisory by design: the write succeeds AND the warning is visible.
    assert second.success, second.error
    assert second.output["concurrent_writer"] == "run_a"
    assert "run_a" in second.output["warning"]
    assert (workspace_root / "shared.txt").read_text(encoding="utf-8") == "b"


@pytest.mark.asyncio
async def test_same_run_rewrite_has_no_warning(workspace_root: Path) -> None:
    with run_as("run_a"):
        for content in ("v1", "v2"):
            res = await _dispatch("write_file", {"path": "own.txt", "content": content})
            assert res.success, res.error
            assert "concurrent_writer" not in res.output


@pytest.mark.asyncio
async def test_main_agent_sees_subagent_claim(workspace_root: Path) -> None:
    with run_as("run_a"):
        res = await _dispatch("write_file", {"path": "claimed.txt", "content": "sub"})
        assert res.success, res.error

    # Main agent (no run id) writes the claimed file → warned.
    with run_as(None):
        res = await _dispatch(
            "write_file", {"path": "claimed.txt", "content": "main"}
        )
    assert res.success, res.error
    assert res.output["concurrent_writer"] == "run_a"


@pytest.mark.asyncio
async def test_release_clears_claims(workspace_root: Path) -> None:
    claim_write(workspace_root / "f.txt", "run_x")
    claim_write(workspace_root / "other.txt", "run_y")  # unrelated run
    release_run_writes("run_x")
    assert in_flight_writer(workspace_root / "f.txt") is None
    assert in_flight_writer(workspace_root / "other.txt") == "run_y"


@pytest.mark.asyncio
async def test_registry_folds_path_case(workspace_root: Path) -> None:
    with run_as("run_a"):
        res = await _dispatch("write_file", {"path": "Case.txt", "content": "x"})
        assert res.success, res.error
    assert in_flight_writer(workspace_root / "case.TXT") == "run_a"


@pytest.mark.asyncio
async def test_edit_file_warns_on_rival_claim(workspace_root: Path) -> None:
    (workspace_root / "edit.txt").write_text("hello world\n", encoding="utf-8")
    claim_write(workspace_root / "edit.txt", "run_a")

    with run_as("run_b"):
        res = await _dispatch(
            "edit_file",
            {"path": "edit.txt", "old_string": "hello", "new_string": "hi"},
        )
    assert res.success, res.error
    assert res.output["concurrent_writer"] == "run_a"
    assert "run_a" in res.output["warning"]


@pytest.mark.asyncio
async def test_unrelated_files_do_not_interfere(workspace_root: Path) -> None:
    with run_as("run_a"):
        res = await _dispatch("write_file", {"path": "a.txt", "content": "a"})
        assert res.success, res.error
    with run_as("run_b"):
        res = await _dispatch("write_file", {"path": "b.txt", "content": "b"})
    assert res.success, res.error
    assert "concurrent_writer" not in res.output


@pytest.mark.asyncio
async def test_main_agent_write_leaves_no_claim(workspace_root: Path) -> None:
    with run_as(None):
        res = await _dispatch("write_file", {"path": "main.txt", "content": "m"})
        assert res.success, res.error
    assert _INFLIGHT_WRITES == {}


# ---------------------------------------------------------------------------
# fs-bus attribution (feeds the v1.5.1 change notes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_emit_carries_run_id_attribute(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, Any]] = []

    class FakeBus:
        def emit(self, kind, paths, cause, **kw):
            captured.append({"kind": kind, "cause": cause, **kw})

    monkeypatch.setattr("minimax_code.app.ensure_fs_bus", lambda: FakeBus())

    with run_as("run_a"):
        res = await _dispatch("write_file", {"path": "emit.txt", "content": "x"})
        assert res.success, res.error
    (event,) = captured
    assert event["run_id"] == "run_a"

    # Main-agent writes carry no run_id attribute at all.
    with run_as(None):
        res = await _dispatch("write_file", {"path": "emit2.txt", "content": "x"})
        assert res.success, res.error
    (_, main_event) = captured
    assert "run_id" not in main_event


# ---------------------------------------------------------------------------
# Drive lifecycle: claims live inside the run, die with it
# ---------------------------------------------------------------------------


class ClaimingRuntime(SubAgentRuntime):
    """Writes one file mid-run — exercises the run-id ContextVar chain."""

    seen_run_id: str | None = None
    seen_claims: dict[str, str] = {}

    async def invoke(self, handle, *, session_id: str, request: str) -> dict:
        type(self).seen_run_id = current_run_id()
        res = await get_default_registry().dispatch(
            "write_file", {"path": "sub_out.py", "content": "# sub"}
        )
        assert res.success, res.error
        # Snapshot the registry while the drive is still in flight.
        type(self).seen_claims = dict(_INFLIGHT_WRITES)
        return {
            "agent": handle.config.name,
            "request": request,
            "session_id": session_id,
            "text": "written",
            "cancelled": False,
            "iterations": 1,
            "tool_calls": [{"name": "write_file"}],
            "stub": False,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "truncated": False,
        }


@pytest.mark.asyncio
async def test_drive_run_publishes_then_releases_claims(
    app_db: AsyncDatabase, workspace_root: Path
) -> None:
    prev = set_subagent_runtime(ClaimingRuntime())
    try:
        name = await _seed_agent(app_db)
        from minimax_code.agent.tools.subagents import SpawnSubagentTool

        out = await SpawnSubagentTool().run(agent_name=name, prompt="write it")
    finally:
        set_subagent_runtime(prev)
    assert out.success, out.error
    run_id = out.output["run_id"]

    # Inside the drive the run id was published and the write claimed
    # under exactly that id…
    assert ClaimingRuntime.seen_run_id == run_id
    assert set(ClaimingRuntime.seen_claims.values()) == {run_id}
    # …but the finally released every claim once the run left flight.
    assert in_flight_writer(workspace_root / "sub_out.py") is None
    assert _INFLIGHT_WRITES == {}
