"""Native checkpoint admission, with real Git and managed local processes."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_m3_isolation import Context, native  # noqa: F401

from minimax_code.execution import ExecutionPolicy, execution_scope, spawn_exec, terminate_tree
from minimax_code.ipc import builtins
from minimax_code.storage.dao.checkpoints import CheckpointDAO
from minimax_code.workspace.checkpoint import CheckpointManager
from minimax_code.workspace_ctx import WorkspaceUnavailableError

PYTHON = getattr(sys, "_base_executable", None) or sys.executable


async def send(native, *, mode="execute", session_id="one"):  # noqa: F811
    native.release.set()
    ctx = Context(native.server)
    await builtins.handle_agent_send_message(
        {"session_id": session_id, "content": "work", "mode": mode}, ctx)
    ctx.reply_error.assert_not_awaited()
    return (await native.dao.list_runs(session_id=session_id))[0]


async def test_native_plan_skips_process_and_snapshot_entirely(native, monkeypatch):  # noqa: F811
    process = AsyncMock(side_effect=AssertionError("Planning must not start a process"))
    snapshot = AsyncMock(side_effect=AssertionError("Planning must not create a snapshot"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", process)
    monkeypatch.setattr(CheckpointManager, "create", snapshot)
    run = await send(native, mode="plan")
    assert run["status"] == "completed"
    process.assert_not_awaited()
    snapshot.assert_not_awaited()
    assert not await CheckpointDAO(native.db).list_by_session(session_id="one")
    assert not (native.data_dir / "checkpoints").exists()
    assert "baseline_checkpoint" not in run["metadata"]


@pytest.mark.parametrize("mode", ["chat", "execute"])
async def test_native_writable_git_run_preserves_baseline_and_working_tree(native, mode):  # noqa: F811
    (native.workspace / "seed.txt").write_text("edited", encoding="utf-8")
    (native.workspace / "new.txt").write_text("untracked", encoding="utf-8")
    before = subprocess.run(["git", "status", "--porcelain"], cwd=native.workspace,
                            capture_output=True, check=True).stdout
    run = await send(native, mode=mode)
    assert run["status"] == "completed"
    rows = await CheckpointDAO(native.db).list_by_session(session_id="one")
    assert len(rows) == 1 and rows[0]["id"] == run["metadata"]["baseline_checkpoint"]
    assert rows[0]["git_stash_ref"]
    assert "new.txt" in rows[0]["untracked_files"]
    after = subprocess.run(["git", "status", "--porcelain"], cwd=native.workspace,
                           capture_output=True, check=True).stdout
    assert before == after


async def test_native_non_git_directory_is_advisory_without_snapshot(native):  # noqa: F811
    plain = native.workspace.parent / "plain"
    plain.mkdir()
    (plain / "large-folder-sentinel").write_text("do not copy an arbitrary directory")
    await native.sessions.create(id="plain", title="plain", workspace_path=str(plain))
    run = await send(native, session_id="plain")
    assert run["status"] == "completed" and "baseline_checkpoint" not in run["metadata"]
    assert not await CheckpointDAO(native.db).list_by_session(session_id="plain")
    assert not (native.data_dir / "checkpoints").exists()


async def test_missing_git_is_advisory_for_native_execution(native, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setattr(builtins, "spawn_exec", AsyncMock(side_effect=FileNotFoundError("git missing")))
    run = await send(native)
    assert run["status"] == "completed" and "baseline_checkpoint" not in run["metadata"]
    assert not await CheckpointDAO(native.db).list_by_session(session_id="one")
    assert "continuing without it" in caplog.text


async def test_probe_uses_filtered_environment_and_explicit_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("UNRELATED_SERVICE_SECRET", "synthetic-do-not-inherit")
    observed = tmp_path / "child.json"
    argv_seen = []

    async def launch(*argv, **kwargs):
        argv_seen.append(argv)
        source = ("import os,json; from pathlib import Path; "
                  f"Path({str(observed)!r}).write_text(json.dumps({{"
                  "'secret_present':'UNRELATED_SERVICE_SECRET' in os.environ,'cwd':os.getcwd()}))")
        return await spawn_exec(PYTHON, "-c", source, **kwargs)

    monkeypatch.setattr(builtins, "spawn_exec", launch)
    with execution_scope(ExecutionPolicy(tmp_path)):
        assert await builtins._baseline_git_workspace(str(tmp_path))
    facts = json.loads(observed.read_text())
    assert not facts["secret_present"] and Path(facts["cwd"]) == tmp_path
    assert argv_seen == [("git", "--no-pager", "-c", "core.fsmonitor=false", "rev-parse", "--git-dir")]


async def test_probe_missing_bound_workspace_never_falls_back(tmp_path):
    with execution_scope(ExecutionPolicy(tmp_path)):
        with pytest.raises(WorkspaceUnavailableError):
            await builtins._baseline_git_workspace(str(tmp_path / "deleted"))


def long_process_source(heartbeat: Path) -> str:
    worker = ("import time; from pathlib import Path; "
              f"p=Path({str(heartbeat)!r}); "
              "exec(\"while True:\\n p.open('a').write('tick\\\\n')\\n time.sleep(.02)\")")
    return ("import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{worker!r}],stdin=subprocess.DEVNULL); "
            "time.sleep(60)")


async def wait_heartbeat(heartbeat: Path):
    for _ in range(200):
        if heartbeat.exists() and heartbeat.read_text():
            return
        await asyncio.sleep(.025)
    raise AssertionError("Synthetic probe descendant did not start")


async def stopped(heartbeat: Path):
    value = heartbeat.read_text()
    await asyncio.sleep(.2)
    assert heartbeat.read_text() == value


async def test_probe_timeout_is_advisory_and_reaps_descendants(native, monkeypatch):  # noqa: F811
    heartbeat = native.workspace / "probe-heartbeat"
    processes = []

    async def launch(*argv, **kwargs):
        proc = await spawn_exec(PYTHON, "-c", long_process_source(heartbeat), **kwargs)
        processes.append(proc)
        await wait_heartbeat(heartbeat)
        return proc

    monkeypatch.setattr(builtins, "spawn_exec", launch)
    monkeypatch.setattr(builtins, "_BASELINE_PROBE_TIMEOUT_S", .1)
    try:
        run = await send(native)
        assert run["status"] == "completed" and "baseline_checkpoint" not in run["metadata"]
        assert not await CheckpointDAO(native.db).list_by_session(session_id="one")
        assert processes[0].returncode is not None
        await stopped(heartbeat)
    finally:
        for proc in processes:
            await terminate_tree(proc)


async def test_probe_cancellation_propagates_after_descendants_stop(tmp_path, monkeypatch):
    heartbeat = tmp_path / "probe-heartbeat"
    processes = []

    async def launch(*argv, **kwargs):
        proc = await spawn_exec(PYTHON, "-c", long_process_source(heartbeat), **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(builtins, "spawn_exec", launch)
    task = asyncio.create_task(builtins._baseline_git_workspace(str(tmp_path)))
    try:
        await wait_heartbeat(heartbeat)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert processes[0].returncode is not None
        await stopped(heartbeat)
    finally:
        for proc in processes:
            await terminate_tree(proc)
