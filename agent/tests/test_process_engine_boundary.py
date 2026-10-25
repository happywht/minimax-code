"""Real local processes, with synthetic credentials; no model/network calls."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from minimax_code.engines import codex
from minimax_code.execution import (
    ExecutionDeniedError,
    ExecutionPolicy,
    communicate_bounded,
    execution_scope,
    spawn_exec,
    terminate_tree,
)
from minimax_code.workspace_ctx import WorkspaceUnavailableError

ENGINE_PYTHON = getattr(sys, "_base_executable", sys.executable)


async def wait_file(path: Path) -> str:
    for _ in range(200):
        if path.exists() and (value := path.read_text()):
            return value
        await asyncio.sleep(0.025)
    raise AssertionError(f"Synthetic child did not create {path.name}")


def tree_script(tmp_path: Path, *, leader_exit: bool = False, close_stdout: bool = False) -> tuple[Path, Path]:
    heartbeat = tmp_path / "heartbeat"
    worker = tmp_path / "heartbeat_worker.py"
    worker.write_text(
        "import time,sys,os\nfrom pathlib import Path\n"
        f"p=Path({str(heartbeat)!r})\n"
        + ("os.close(1)\n" if close_stdout else "")
        + "while True:\n p.open('a').write(str(time.monotonic_ns())+'\\n')\n time.sleep(.025)\n",
        encoding="utf-8",
    )
    server = tmp_path / "tree_server.py"
    server.write_text(
        "import subprocess,sys,json,time,os\n"
        f"child=subprocess.Popen([sys.executable,{str(worker)!r}],stdin=subprocess.DEVNULL)\n"
        f"marker={str(tmp_path / 'child.pid')!r}\n"
        "open(marker,'w').write(str(child.pid))\n"
        + ("time.sleep(.2)\nsys.exit(0)\n" if leader_exit else
           "for raw in sys.stdin:\n"
           " req=json.loads(raw)\n"
           " if 'id' in req: print(json.dumps({'id':req['id'],'result':{}}),flush=True)\n"),
        encoding="utf-8",
    )
    return server, heartbeat


async def assert_stopped(heartbeat: Path) -> None:
    # Process handles/PIDs are separately tested on Windows; this proves that
    # the actual descendant ceased executing, including after owner death.
    first = await wait_file(heartbeat)
    await asyncio.sleep(0.2)
    assert heartbeat.read_text() == first


async def test_engine_environment_is_filtered_and_trusted_config_is_explicit(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("UNRELATED_SERVICE_KEY", "synthetic-unrelated-service-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-selected-provider-secret")
    monkeypatch.setenv("HTTPS_PROXY", "https://synthetic-proxy.invalid")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "cli-home"))
    monkeypatch.delenv("MINIMAX_CODE_CODEX_ENV_VARS", raising=False)
    source = "import os,json; print(json.dumps(dict(os.environ)),flush=True)"
    proc = await codex._launch_codex([ENGINE_PYTHON, "-c", source], cwd=tmp_path)
    output = await communicate_bounded(proc, timeout=5)
    child_env = json.loads(output.stdout)
    assert "UNRELATED_SERVICE_KEY" not in child_env
    assert "OPENAI_API_KEY" not in child_env and "HTTPS_PROXY" not in child_env
    assert child_env["CODEX_HOME"] == str(tmp_path / "cli-home")
    monkeypatch.setenv("MINIMAX_CODE_CODEX_ENV_VARS", '["OPENAI_API_KEY", "HTTPS_PROXY"]')
    proc = await codex._launch_codex([ENGINE_PYTHON, "-c", source], cwd=tmp_path,
                                    extra_env={"EXPLICIT_ENGINE_TOKEN": "synthetic-extra-secret"})
    output = await communicate_bounded(proc, timeout=5)
    child_env = json.loads(output.stdout)
    assert child_env["OPENAI_API_KEY"] == "synthetic-selected-provider-secret"
    assert child_env["HTTPS_PROXY"] == "https://synthetic-proxy.invalid"
    assert child_env["EXPLICIT_ENGINE_TOKEN"] == "synthetic-extra-secret"
    assert "UNRELATED_SERVICE_KEY" not in child_env
    assert "synthetic-" not in caplog.text


@pytest.mark.parametrize("config", ['{"BAD":"synthetic-secret"}', '["BAD=synthetic-secret"]', 'synthetic-secret'])
async def test_invalid_environment_config_cannot_launch_or_disclose(tmp_path, monkeypatch, config):
    monkeypatch.setenv("MINIMAX_CODE_CODEX_ENV_VARS", config)
    with pytest.raises(ValueError) as exc:
        await codex._launch_codex([ENGINE_PYTHON, "-c", "raise AssertionError"], cwd=tmp_path)
    assert "synthetic-secret" not in str(exc.value)


@pytest.mark.parametrize("extra", [{"": "x"}, {"BAD=KEY": "synthetic-secret"}, {"OK": "bad\0value"}])
async def test_invalid_explicit_environment_is_rejected(tmp_path, extra):
    with pytest.raises(ValueError, match="Invalid child environment entry"):
        await codex._launch_codex([ENGINE_PYTHON, "-c", "raise AssertionError"], cwd=tmp_path,
                                  extra_env=extra)


async def test_engine_start_permitted_in_plan_without_authorizing_arbitrary_commands(tmp_path):
    server, heartbeat = tree_script(tmp_path)
    conn = codex.AppServerConnection(cwd=tmp_path, codex_cmd=[ENGINE_PYTHON, str(server)])
    try:
        with execution_scope(ExecutionPolicy(tmp_path, read_only=True)):
            await conn.start()
            await conn.initialize()
            with pytest.raises(ExecutionDeniedError, match="read-only"):
                await spawn_exec(ENGINE_PYTHON, "-c", "raise AssertionError", cwd=tmp_path)
        await wait_file(heartbeat)
    finally:
        await conn.close()
    await assert_stopped(heartbeat)


async def test_engine_missing_or_escaped_cwd_never_falls_back(tmp_path):
    with execution_scope(ExecutionPolicy(tmp_path, read_only=True)):
        with pytest.raises(WorkspaceUnavailableError):
            await codex.AppServerConnection(cwd=tmp_path / "missing").start()
        with pytest.raises(ExecutionDeniedError, match="outside"):
            await codex.AppServerConnection(cwd=tmp_path.parent).start()


@pytest.mark.parametrize("leader_exit,close_stdout", [(False, False), (True, False), (True, True)])
async def test_engine_close_and_leader_exit_reap_descendants(tmp_path, leader_exit, close_stdout):
    server, heartbeat = tree_script(tmp_path, leader_exit=leader_exit, close_stdout=close_stdout)
    conn = codex.AppServerConnection(cwd=tmp_path, codex_cmd=[ENGINE_PYTHON, str(server)])
    await conn.start()
    try:
        await wait_file(heartbeat)
        if leader_exit:
            for _ in range(100):
                if conn.closed:
                    break
                await asyncio.sleep(.025)
            assert conn.closed, "Leader watcher must not wait for descendant pipe EOF"
        await asyncio.wait_for(conn.close(), 5)
        assert conn._proc.returncode is not None
        await assert_stopped(heartbeat)
    finally:
        await conn.close()


async def test_unexpected_stdout_eof_reaps_live_server_tree(tmp_path):
    server, heartbeat = tree_script(tmp_path, close_stdout=True)
    server.write_text(server.read_text().replace("for raw in sys.stdin:",
                                                "time.sleep(.2)\nos.close(1)\ntime.sleep(60)\nfor raw in sys.stdin:"))
    conn = codex.AppServerConnection(cwd=tmp_path, codex_cmd=[ENGINE_PYTHON, str(server)])
    await conn.start()
    try:
        await wait_file(heartbeat)
        for _ in range(100):
            if conn.closed:
                break
            await asyncio.sleep(.025)
        assert conn.closed, "Unexpected stdout EOF must close the still-live engine"
        await conn.close()
        await assert_stopped(heartbeat)
    finally:
        await conn.close()


async def test_initialize_failure_closes_the_started_tree(tmp_path, monkeypatch):
    server, heartbeat = tree_script(tmp_path)
    started = []
    original = codex.AppServerConnection.initialize

    async def fail(conn, *args, **kwargs):
        started.append(conn)
        await wait_file(heartbeat)
        raise RuntimeError("synthetic initialize failure")

    monkeypatch.setattr(codex.AppServerConnection, "initialize", fail)
    with pytest.raises(RuntimeError, match="initialize failure"):
        await codex.CodexEngine.connect(cwd=tmp_path, codex_cmd=[ENGINE_PYTHON, str(server)])
    assert started[0].closed and started[0]._proc.returncode is not None
    await assert_stopped(heartbeat)
    monkeypatch.setattr(codex.AppServerConnection, "initialize", original)


@pytest.mark.parametrize("engine", [False, True])
async def test_repeated_spawn_cancellation_retains_handle_until_reaped(tmp_path, monkeypatch, engine):
    original = asyncio.create_subprocess_exec
    created, release = asyncio.Event(), asyncio.Event()
    processes = []

    async def delayed(*args, **kwargs):
        proc = await original(*args, **kwargs)
        processes.append(proc)
        created.set()
        await release.wait()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    server, heartbeat = tree_script(tmp_path)
    conn = codex.AppServerConnection(cwd=tmp_path, codex_cmd=[ENGINE_PYTHON, str(server)])
    operation = conn.start() if engine else spawn_exec(ENGINE_PYTHON, str(server), cwd=tmp_path,
                                                       stdin=asyncio.subprocess.PIPE)
    task = asyncio.create_task(operation)
    try:
        await asyncio.wait_for(created.wait(), 5)
        await wait_file(heartbeat)
        task.cancel()
        await asyncio.sleep(.02)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done(), "Cancellation must retain ownership of the pending spawn"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert processes[0].returncode is not None
        await assert_stopped(heartbeat)
    finally:
        release.set()
        if engine:
            await conn.close()
        for proc in processes:
            await terminate_tree(proc)


async def test_repeated_close_cancellation_waits_for_same_cleanup(tmp_path, monkeypatch):
    server, heartbeat = tree_script(tmp_path)
    conn = codex.AppServerConnection(cwd=tmp_path, codex_cmd=[ENGINE_PYTHON, str(server)])
    await conn.start()
    await wait_file(heartbeat)
    original = codex.terminate_tree
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def delayed(proc):
        calls.append(proc)
        entered.set()
        await release.wait()
        await original(proc)

    monkeypatch.setattr(codex, "terminate_tree", delayed)
    closing = asyncio.create_task(conn.close())
    try:
        await entered.wait()
        closing.cancel()
        await asyncio.sleep(.02)
        closing.cancel()
        await asyncio.sleep(.02)
        assert not closing.done()
        second = asyncio.create_task(conn.close())
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, 5)
        await asyncio.wait_for(second, 5)
        await conn.close()
        assert len(calls) == 1 and conn._proc.returncode is not None
        await assert_stopped(heartbeat)
    finally:
        release.set()
        await conn.close()


async def test_version_probe_uses_owned_timeout_and_filtered_env(tmp_path, monkeypatch):
    server, heartbeat = tree_script(tmp_path)
    server.write_text(server.read_text().replace("for raw in sys.stdin:",
                                                "time.sleep(60)\nfor raw in sys.stdin:"))
    original = codex._launch_codex
    seen = []

    async def launch(argv, **kwargs):
        proc = await original([ENGINE_PYTHON, str(server)], cwd=tmp_path)
        seen.append(proc)
        await wait_file(heartbeat)
        return proc

    monkeypatch.setattr(codex.shutil, "which", lambda *a, **k: ENGINE_PYTHON)
    monkeypatch.setattr(codex, "_launch_codex", launch)
    info = await codex._probe_codex_async(.1)
    assert not info.available and "TimeoutError" in info.unavailable_reason
    assert seen[0].returncode is not None
    await assert_stopped(heartbeat)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch launcher")
async def test_windows_cmd_engine_command_and_version_probe(tmp_path, monkeypatch):
    command = tmp_path / "synthetic codex.cmd"
    command.write_text('@echo off\r\necho codex-cli 0.163.0\r\n', encoding="utf-8")
    which = codex.shutil.which
    monkeypatch.setattr(codex.shutil, "which", lambda name, *a, **k:
                        str(command) if name == "codex" else which(name, *a, **k))
    info = await codex._probe_codex_async()
    assert info.available and info.version == "codex-cli 0.163.0"


def test_probe_error_does_not_echo_child_output_or_config(monkeypatch):
    async def launch(*args, **kwargs):
        raise ValueError("synthetic-secret-value")

    monkeypatch.setattr(codex.shutil, "which", lambda *a, **k: "codex")
    monkeypatch.setattr(codex, "_launch_codex", launch)
    info = codex._probe_codex()
    assert "synthetic-secret-value" not in info.unavailable_reason


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process host")
async def test_windows_host_preserves_streams_and_nonzero_command_exit(tmp_path):
    proc = await spawn_exec(ENGINE_PYTHON, "-c",
                            "import sys; print('out'); print('err',file=sys.stderr); sys.exit(7)",
                            cwd=tmp_path, stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE)
    output = await communicate_bounded(proc, timeout=5)
    assert output.stdout.strip() == b"out" and output.stderr.strip() == b"err"
    assert proc.returncode == 7


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process host")
async def test_windows_host_reports_command_start_failure(tmp_path):
    broken = tmp_path / "broken.exe"
    broken.write_text("This synthetic fixture is not an executable.")
    proc = await spawn_exec(str(broken), cwd=tmp_path, stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE)
    output = await communicate_bounded(proc, timeout=5)
    assert proc.returncode == 127
    assert b"Managed command could not start" in output.stderr
