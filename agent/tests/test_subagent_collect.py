"""v1.5.0 — collect_subagent: three-way sandbox merge (layer 3).

The merge algorithm compares, per sandboxed file, the ``_base/`` COW
snapshot (the workspace bytes at first touch), the workspace's current
bytes, and the sandbox mirror's bytes — all hashed at the byte level.
These tests pin:

* clean merges (workspace untouched since the snapshot, or file absent)
  plus the fs_bus mirror with cause ``collect_subagent``
* conflict detection with all three sha256 fingerprints surfaced, and
  the three policies (fail / skip / overwrite)
* ``.merged`` marker idempotence — a second collect is a no-op
* in-flight / unknown-run / malformed-argument guards
* the ``files_written`` manifest riding the spawn envelope and the
  persisted run row (survives agent restarts)

All collect exercises go through ``registry.dispatch`` (project
convention since v1.4.1).
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.tools import get_default_registry
from minimax_code.agent.tools.sandbox import BASE_DIR, MERGED_MARKER, SANDBOX_RELPATH
from minimax_code.agent.tools.subagents import (
    SpawnSubagentTool,
    _lookup_finished_run,
)
from minimax_code.orchestrator.subagent import (
    SubAgentRuntime,
    get_background_run,
    set_subagent_runtime,
)
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.workspace_ctx import reset_current_root, set_current_root

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


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


@pytest.fixture
def workspace_root(tmp_path: Path):
    root = tmp_path / "proj_root"
    root.mkdir()
    token = set_current_root(root)
    try:
        yield root
    finally:
        reset_current_root(token)


async def _dispatch(name: str, args: dict) -> Any:
    return await get_default_registry().dispatch(name, args)


def _sandbox(root: Path, run_id: str) -> Path:
    sb = root / SANDBOX_RELPATH / run_id
    sb.mkdir(parents=True, exist_ok=True)
    return sb


def _stage(
    root: Path,
    run_id: str,
    rel: str,
    *,
    ws: bytes | None,
    base: bytes | None,
    sb: bytes,
) -> Path:
    """Lay out one file's three-way state: workspace / baseline / mirror."""
    box = _sandbox(root, run_id)
    if ws is not None:
        ws_file = root.joinpath(*rel.split("/"))
        ws_file.parent.mkdir(parents=True, exist_ok=True)
        ws_file.write_bytes(ws)
    if base is not None:
        base_file = (box / BASE_DIR).joinpath(*rel.split("/"))
        base_file.parent.mkdir(parents=True, exist_ok=True)
        base_file.write_bytes(base)
    mirror = box.joinpath(*rel.split("/"))
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_bytes(sb)
    return box


async def _seed_agent(db: AsyncDatabase) -> str:
    dao = AgentDAO(db)
    name = f"col_{uuid.uuid4().hex[:8]}"
    await dao.upsert(name=name, system_prompt="you are a test agent")
    return name


# ---------------------------------------------------------------------------
# Clean merges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_merges_new_and_clean_files(workspace_root: Path) -> None:
    run_id = "run_c1"
    _stage(workspace_root, run_id, "brand_new.py", ws=None, base=None, sb=b"# new")
    _stage(
        workspace_root, run_id, "modified.py",
        ws=b"v1\n", base=b"v1\n", sb=b"v2\n",
    )

    res = await _dispatch("collect_subagent", {"run_id": run_id})
    assert res.success, res.error
    out = res.output
    assert [m["path"] for m in out["merged"]] == ["brand_new.py", "modified.py"]
    assert out["noop"] == [] and out["conflicts"] == []
    assert (workspace_root / "brand_new.py").read_bytes() == b"# new"
    assert (workspace_root / "modified.py").read_bytes() == b"v2\n"
    # Marker written → the next collect is a no-op.
    assert (workspace_root / SANDBOX_RELPATH / run_id / MERGED_MARKER).is_file()


@pytest.mark.asyncio
async def test_collect_binary_files_merge_at_byte_level(workspace_root: Path) -> None:
    run_id = "run_bin"
    payload = bytes(range(256)) * 64 + b"\x00\xff"
    _stage(workspace_root, run_id, "asset.bin", ws=None, base=None, sb=payload)

    res = await _dispatch("collect_subagent", {"run_id": run_id})
    assert res.success, res.error
    merged_file = workspace_root / "asset.bin"
    assert merged_file.read_bytes() == payload  # byte-identical, NULs intact


# ---------------------------------------------------------------------------
# Conflicts & the three policies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conflict_fail_reports_three_hashes_no_clobber(
    workspace_root: Path,
) -> None:
    run_id = "run_conf"
    _stage(
        workspace_root, run_id, "shared.txt",
        ws=b"external edit", base=b"original", sb=b"sandbox edit",
    )

    res = await _dispatch("collect_subagent", {"run_id": run_id})
    assert not res.success
    (conflict,) = res.output["conflicts"]
    assert conflict["path"] == "shared.txt"
    assert conflict["base_sha256"] is not None
    assert conflict["current_sha256"] is not None
    assert conflict["sandbox_sha256"] is not None
    assert "changed since" in conflict["reason"]
    # Fail policy: workspace untouched, no marker (re-run stays possible).
    assert (workspace_root / "shared.txt").read_bytes() == b"external edit"
    assert not (workspace_root / SANDBOX_RELPATH / run_id / MERGED_MARKER).exists()


@pytest.mark.asyncio
async def test_conflict_skip_keeps_workspace(workspace_root: Path) -> None:
    run_id = "run_skip"
    _stage(
        workspace_root, run_id, "shared.txt",
        ws=b"external edit", base=b"original", sb=b"sandbox edit",
    )

    res = await _dispatch(
        "collect_subagent", {"run_id": run_id, "on_conflict": "skip"}
    )
    assert res.success, res.error
    assert res.output["skipped"] == ["shared.txt"]
    assert res.output["merged"] == []
    assert (workspace_root / "shared.txt").read_bytes() == b"external edit"
    assert (workspace_root / SANDBOX_RELPATH / run_id / MERGED_MARKER).is_file()


@pytest.mark.asyncio
async def test_conflict_overwrite_applies_sandbox(workspace_root: Path) -> None:
    run_id = "run_ow"
    _stage(
        workspace_root, run_id, "shared.txt",
        ws=b"external edit", base=b"original", sb=b"sandbox edit",
    )

    res = await _dispatch(
        "collect_subagent", {"run_id": run_id, "on_conflict": "overwrite"}
    )
    assert res.success, res.error
    assert res.output["merged"] == [
        {"path": "shared.txt", "conflict_resolved": "overwrite"}
    ]
    assert (workspace_root / "shared.txt").read_bytes() == b"sandbox edit"


@pytest.mark.asyncio
async def test_workspace_deletion_after_snapshot_is_conflict(
    workspace_root: Path,
) -> None:
    run_id = "run_del"
    _stage(
        workspace_root, run_id, "gone.txt",
        ws=None, base=b"original", sb=b"sandbox edit",
    )

    res = await _dispatch("collect_subagent", {"run_id": run_id})
    assert not res.success
    (conflict,) = res.output["conflicts"]
    assert "deleted" in conflict["reason"]
    assert conflict["base_sha256"] is not None
    assert "current_sha256" not in conflict  # no workspace copy to hash


# ---------------------------------------------------------------------------
# Idempotence & guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_collect_is_idempotent(workspace_root: Path) -> None:
    run_id = "run_idem"
    _stage(
        workspace_root, run_id, "f.txt", ws=b"v1", base=b"v1", sb=b"v2"
    )
    first = await _dispatch("collect_subagent", {"run_id": run_id})
    assert first.success, first.error
    merged_file = workspace_root / "f.txt"
    stat_after_first = merged_file.stat()

    second = await _dispatch("collect_subagent", {"run_id": run_id})
    assert second.success, second.error
    assert second.output["already_collected"] is True
    assert second.output["merged"] == first.output["merged"]
    # No re-copy: mtime unchanged.
    stat_after_second = merged_file.stat()
    assert stat_after_first.st_mtime_ns == stat_after_second.st_mtime_ns
    assert merged_file.read_bytes() == b"v2"


@pytest.mark.asyncio
async def test_collect_rejects_in_flight_run(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stage(workspace_root, "run_live", "f.txt", ws=None, base=None, sb=b"x")
    monkeypatch.setattr(
        "minimax_code.orchestrator.subagent.get_background_run",
        lambda rid: object() if rid == "run_live" else None,
    )
    res = await _dispatch("collect_subagent", {"run_id": "run_live"})
    assert not res.success
    assert "in flight" in res.error


@pytest.mark.asyncio
async def test_collect_unknown_run_fails(workspace_root: Path) -> None:
    res = await _dispatch("collect_subagent", {"run_id": "run_nope"})
    assert not res.success
    assert "no sandbox found" in res.error


@pytest.mark.asyncio
async def test_collect_validates_arguments(workspace_root: Path) -> None:
    bad = await _dispatch(
        "collect_subagent", {"run_id": "run_x", "on_conflict": "bogus"}
    )
    assert not bad.success
    assert "on_conflict" in bad.error
    empty = await _dispatch("collect_subagent", {"run_id": "  "})
    assert not empty.success


@pytest.mark.asyncio
async def test_collect_empty_sandbox_succeeds_with_empty_report(
    workspace_root: Path,
) -> None:
    _sandbox(workspace_root, "run_empty")
    res = await _dispatch("collect_subagent", {"run_id": "run_empty"})
    assert res.success, res.error
    assert res.output["merged"] == [] and res.output["noop"] == []
    assert (workspace_root / SANDBOX_RELPATH / "run_empty" / MERGED_MARKER).is_file()


# ---------------------------------------------------------------------------
# fs_bus mirror
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_emits_fs_bus_events(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, list[str], str]] = []

    class FakeBus:
        def emit(self, kind: str, paths: list[str], cause: str, **kw: Any) -> None:
            calls.append((kind, list(paths), cause))

    monkeypatch.setattr("minimax_code.app.ensure_fs_bus", lambda: FakeBus())
    run_id = "run_bus"
    _stage(workspace_root, run_id, "n.txt", ws=None, base=None, sb=b"n")
    _stage(workspace_root, run_id, "m.txt", ws=b"v1", base=b"v1", sb=b"v2")

    res = await _dispatch("collect_subagent", {"run_id": run_id})
    assert res.success, res.error
    kinds = {path: kind for kind, paths, _ in calls for path in paths}
    assert kinds[str(workspace_root / "n.txt")] == "created"
    assert kinds[str(workspace_root / "m.txt")] == "modified"
    assert all(cause == "collect_subagent" for _, _, cause in calls)


# ---------------------------------------------------------------------------
# files_written propagation (spawn envelope + persisted run row)
# ---------------------------------------------------------------------------


class WritingRuntime(SubAgentRuntime):
    """Writes one file mid-run — exercises the sandbox ContextVar chain."""

    async def invoke(self, handle, *, session_id: str, request: str) -> dict:
        res = await get_default_registry().dispatch(
            "write_file", {"path": "src/out.py", "content": "# sb"}
        )
        assert res.success, res.error
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
async def test_spawn_envelope_and_run_row_carry_files_written(
    app_db: AsyncDatabase, workspace_root: Path
) -> None:
    prev = set_subagent_runtime(WritingRuntime())
    try:
        name = await _seed_agent(app_db)
        out = await SpawnSubagentTool().run(
            agent_name=name, prompt="write it", sandbox=True
        )
    finally:
        set_subagent_runtime(prev)
    assert out.success, out.error
    run_id = out.output["run_id"]
    assert out.output["files_written"] == ["src/out.py"]

    # The manifest survives on the run row (agent-restart channel).
    row = await AgentRunsDAO(app_db).get_run(run_id)
    assert row is not None
    meta = row.get("metadata") or {}
    assert meta["files_written"] == ["src/out.py"]

    # And the finished-run lookup projects it back out.
    looked = await _lookup_finished_run(run_id)
    assert looked.success, looked.error
    assert looked.output["files_written"] == ["src/out.py"]


@pytest.mark.asyncio
async def test_background_files_written_via_check_after_completion(
    app_db: AsyncDatabase, workspace_root: Path
) -> None:
    prev = set_subagent_runtime(WritingRuntime())
    try:
        name = await _seed_agent(app_db)
        out = await SpawnSubagentTool().run(
            agent_name=name, prompt="bg write", wait=False, sandbox=True
        )
        assert out.success, out.error
        run_id = out.output["run_id"]
        task = get_background_run(run_id)
        assert task is not None
        await asyncio.wait_for(asyncio.shield(task), timeout=10)
        await asyncio.sleep(0)  # let the done-callback run its pop
    finally:
        set_subagent_runtime(prev)

    # Task gone → check_subagent falls through to the run row.
    assert get_background_run(run_id) is None
    looked = await _lookup_finished_run(run_id)
    assert looked.success, looked.error
    assert looked.output["files_written"] == ["src/out.py"]
    # The sandbox file itself is still pending collection.
    sb_file = workspace_root / SANDBOX_RELPATH / run_id / "src" / "out.py"
    assert sb_file.read_bytes() == b"# sb"
    assert not (workspace_root / "src").exists()
