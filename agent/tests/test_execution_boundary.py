"""Real subprocess regressions for the shared execution boundary."""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from minimax_code.execution import (
    ExecutionDeniedError,
    ExecutionPolicy,
    communicate_bounded,
    execution_scope,
    require_tool_access,
    require_write_access,
    spawn_exec,
    spawn_shell,
)
from minimax_code.workspace_ctx import (
    WorkspaceUnavailableError,
    reset_current_root,
    set_current_root,
)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object lifecycle")
@pytest.mark.parametrize("kill_host", [False, True])
async def test_windows_job_reaps_descendants_when_host_exits(tmp_path, kill_host):
    import ctypes
    from ctypes import wintypes

    from minimax_code.execution import terminate_tree

    source = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "print(child.pid,flush=True); "
        + ("time.sleep(30)" if kill_host else "sys.exit(7)")
    )
    proc = await spawn_exec(sys.executable, "-c", source, cwd=tmp_path,
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        child_pid = int(await asyncio.wait_for(proc.stdout.readline(), 5))
        if kill_host:
            # Kill only the launcher, not taskkill /T: its job must do cleanup.
            proc.kill()
        await communicate_bounded(proc, timeout=5)
        if not kill_host:
            assert proc.returncode == 7
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, child_pid)  # SYNCHRONIZE
        if handle:
            try:
                assert kernel.WaitForSingleObject(handle, 1000) == 0
            finally:
                kernel.CloseHandle(handle)
        else:
            assert ctypes.get_last_error() == 87  # PID already gone
    finally:
        await terminate_tree(proc)


async def test_child_sees_only_explicit_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-do-not-inherit")
    monkeypatch.setenv("CUSTOM_SECRET", "another-ambient-secret")
    proc = await spawn_exec(sys.executable, "-c", "import os,json; print(json.dumps(dict(os.environ)))",
                            cwd=tmp_path, env={"MCP_TOKEN": "explicit-config-value"},
                            stdout=asyncio.subprocess.PIPE)
    result = await communicate_bounded(proc, timeout=5)
    env = json.loads(result.stdout)
    assert "OPENAI_API_KEY" not in env and "CUSTOM_SECRET" not in env
    assert env["MCP_TOKEN"] == "explicit-config-value"


async def test_invalid_directory_and_escape_cannot_start(tmp_path):
    with execution_scope(ExecutionPolicy(tmp_path)):
        with pytest.raises(WorkspaceUnavailableError):
            await spawn_shell("echo should-not-run", cwd=tmp_path / "deleted")
        with pytest.raises(ExecutionDeniedError):
            await spawn_shell("echo should-not-run", cwd=tmp_path.parent)


async def test_read_only_scope_is_inherited_by_child_tasks(tmp_path):
    with execution_scope(ExecutionPolicy(tmp_path, read_only=True)):
        task = asyncio.create_task(spawn_exec(sys.executable, "-c", "print('not allowed')"))
        with pytest.raises(ExecutionDeniedError, match="read-only"):
            await task
    proc = await spawn_exec(sys.executable, "-c", "print('allowed')", cwd=tmp_path,
                            stdout=asyncio.subprocess.PIPE)
    assert (await communicate_bounded(proc, timeout=5)).stdout.strip() == b"allowed"


async def test_nested_scope_cannot_relax_read_only_and_restores_parent(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    with execution_scope(ExecutionPolicy(tmp_path, read_only=True)):
        with pytest.raises(RuntimeError, match="exit inner"):
            with execution_scope(ExecutionPolicy(child)):
                with pytest.raises(ExecutionDeniedError, match="read-only"):
                    require_write_access()
                with pytest.raises(ExecutionDeniedError, match="read-only"):
                    require_tool_access("write_file")
                task = asyncio.create_task(spawn_exec(sys.executable, "-c", "print('blocked')"))
                with pytest.raises(ExecutionDeniedError, match="read-only"):
                    await task
                raise RuntimeError("exit inner")
        with pytest.raises(ExecutionDeniedError, match="read-only"):
            require_write_access()
    require_write_access()


async def test_nested_read_only_scope_does_not_restrict_parent_after_exit(tmp_path):
    with execution_scope(ExecutionPolicy(tmp_path)):
        with execution_scope(ExecutionPolicy(tmp_path, read_only=True)):
            with pytest.raises(ExecutionDeniedError, match="read-only"):
                require_write_access()
        require_write_access()


async def test_nested_workspace_cannot_widen_and_omission_inherits(tmp_path):
    root = tmp_path / "parent"
    child = root / "child"
    outside = tmp_path / "outside"
    child.mkdir(parents=True)
    outside.mkdir()
    with execution_scope(ExecutionPolicy(root)):
        with pytest.raises(ExecutionDeniedError, match="outside the parent"):
            with execution_scope(ExecutionPolicy(outside)):
                pass
        with execution_scope(ExecutionPolicy(child)):
            with execution_scope(ExecutionPolicy()):
                with pytest.raises(ExecutionDeniedError, match="outside the active"):
                    await spawn_exec(sys.executable, "-c", "pass", cwd=root)
            with pytest.raises(ExecutionDeniedError, match="outside the parent"):
                with execution_scope(ExecutionPolicy(root)):
                    pass
        proc = await spawn_exec(sys.executable, "-c", "pass", cwd=root)
        await communicate_bounded(proc, timeout=5)
        assert proc.returncode == 0


@pytest.mark.parametrize("implicit_parent", [False, True])
async def test_nested_scope_rejects_replaced_parent_with_original_child(tmp_path, implicit_parent):
    root = tmp_path / "parent"
    child = root / "child"
    child.mkdir(parents=True)
    token = set_current_root(root) if implicit_parent else None
    try:
        with execution_scope(ExecutionPolicy(None if implicit_parent else root)):
            with execution_scope(ExecutionPolicy(child)):
                original = tmp_path / "original"
                root.rename(original)
                root.mkdir()
                (original / "child").rename(child)
                with pytest.raises(ExecutionDeniedError, match="workspace has changed"):
                    await spawn_exec(sys.executable, "-c", "pass", cwd=child)
    finally:
        if token is not None:
            reset_current_root(token)


def test_implicit_parent_uses_current_session_root(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    token = set_current_root(root)
    try:
        with execution_scope(ExecutionPolicy()):
            with pytest.raises(ExecutionDeniedError, match="outside the parent"):
                with execution_scope(ExecutionPolicy(tmp_path)):
                    pass
    finally:
        reset_current_root(token)


async def test_output_is_capped_while_draining_both_pipes(tmp_path):
    proc = await spawn_exec(sys.executable, "-c",
                            "import sys; sys.stdout.write('a'*200000); sys.stderr.write('b'*200000)",
                            cwd=tmp_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    result = await communicate_bounded(proc, timeout=5, limit=1024)
    assert result.stdout == b"a" * 1024 and result.stderr == b"b" * 1024
    assert result.stdout_truncated and result.stderr_truncated
    assert proc.returncode == 0


async def test_cancellation_reaps_child(tmp_path):
    proc = await spawn_exec(sys.executable, "-c", "import time; time.sleep(30)", cwd=tmp_path,
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    task = asyncio.create_task(communicate_bounded(proc, timeout=60))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert proc.returncode is not None


async def test_timeout_reaps_child(tmp_path):
    proc = await spawn_exec(sys.executable, "-c", "import time; time.sleep(30)", cwd=tmp_path,
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    with pytest.raises(TimeoutError):
        await communicate_bounded(proc, timeout=0.1)
    assert proc.returncode is not None


async def test_timeout_preserves_bounded_output_and_reaps_child(tmp_path):
    from minimax_code.execution import ProcessTimeoutError

    proc = await spawn_exec(
        sys.executable, "-c",
        "import sys,time; sys.stdout.write('a'*2048); sys.stdout.flush(); "
        "sys.stderr.write('failure'); sys.stderr.flush(); time.sleep(30)",
        cwd=tmp_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    with pytest.raises(ProcessTimeoutError) as caught:
        await communicate_bounded(proc, timeout=3, limit=1024)
    assert caught.value.output.stdout == b"a" * 1024
    assert caught.value.output.stdout_truncated
    assert caught.value.output.stderr == b"failure"
    assert not caught.value.output.stderr_truncated
    assert proc.returncode is not None


async def test_cancellation_during_spawn_recovers_and_reaps_handle(tmp_path, monkeypatch):
    original = asyncio.create_subprocess_exec
    created = asyncio.Event()
    release = asyncio.Event()
    processes = []

    async def delayed(*args, **kwargs):
        proc = await original(*args, **kwargs)
        processes.append(proc)
        created.set()
        await release.wait()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    task = asyncio.create_task(spawn_exec(sys.executable, "-c", "import time; time.sleep(30)",
                                          cwd=tmp_path, stdout=asyncio.subprocess.PIPE,
                                          stderr=asyncio.subprocess.PIPE))
    await asyncio.wait_for(created.wait(), 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert processes[0].returncode is not None


async def test_symlink_cwd_escape_is_blocked(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation requires OS permission")
    with execution_scope(ExecutionPolicy(tmp_path)):
        with pytest.raises(ExecutionDeniedError):
            await spawn_shell("echo should-not-run", cwd=link)


async def test_hook_does_not_inherit_ambient_secret(tmp_path, monkeypatch):
    from minimax_code.hooks.executor import HookExecutor
    from minimax_code.hooks.types import HookConfig, HookContext, HookEvent

    monkeypatch.setenv("CUSTOM_SECRET", "hidden")
    hook = HookConfig(event=HookEvent.PRE_TOOL_USE,
                      command=[sys.executable, "-c", "import os; print(os.getenv('CUSTOM_SECRET', 'absent'))"])
    result = await HookExecutor().run(hook, HookContext(event=HookEvent.PRE_TOOL_USE, cwd=str(tmp_path)))
    assert result.ok and result.stdout.strip() == "absent"


async def test_planning_blocks_file_tools_even_without_registry(tmp_path):
    from minimax_code.agent.tools.edit import EditFileTool
    from minimax_code.agent.tools.file_ops import AppendFileTool, WriteFileTool
    from minimax_code.workspace_ctx import reset_current_root, set_current_root

    target = tmp_path / "file.txt"
    target.write_text("original", encoding="utf-8")
    token = set_current_root(tmp_path)
    try:
        with execution_scope(ExecutionPolicy(tmp_path, read_only=True)):
            assert not (await WriteFileTool().run(path="file.txt", content="changed")).success
            assert not (await EditFileTool().run(path="file.txt", old_string="original", new_string="changed")).success
            assert not (await AppendFileTool().run(path="file.txt", content="tail")).success
        assert target.read_text(encoding="utf-8") == "original"
    finally:
        reset_current_root(token)


def test_atomic_write_failure_keeps_original_and_removes_temp(tmp_path, monkeypatch):
    import hashlib

    from minimax_code import file_mutations

    target = tmp_path / "file.txt"
    target.write_bytes(b"original")
    def fail_replace(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(file_mutations.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        file_mutations.atomic_write_text(target, "changed", baseline_path=target,
                                          baseline_sha256=hashlib.sha256(b"original").hexdigest())
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob(".minimax-write-*"))


@pytest.mark.parametrize("append", [False, True])
def test_atomic_write_existing_file_after_metadata_change(tmp_path, append):
    import hashlib

    from minimax_code.file_mutations import atomic_append_text, atomic_write_text

    target = tmp_path / "file.txt"
    target.write_bytes(b"first")
    target.write_bytes(b"original")
    digest = hashlib.sha256(b"original").hexdigest()
    operation = atomic_append_text if append else atomic_write_text
    operation(target, "changed", baseline_path=target, baseline_sha256=digest)
    assert target.read_bytes() == (b"originalchanged" if append else b"changed")


@pytest.mark.parametrize("append", [False, True])
def test_atomic_write_rejects_same_content_file_replaced_before_open(tmp_path, monkeypatch, append):
    import hashlib
    import os

    from minimax_code.file_mutations import atomic_append_text, atomic_write_text

    target = tmp_path / "file.txt"
    replacement = tmp_path / "replacement.txt"
    target.write_bytes(b"original")
    replacement.write_bytes(b"original")
    original_open = os.open

    def replace_before_open(path, flags, *args, **kwargs):
        if path == target and replacement.exists():
            os.replace(replacement, target)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_before_open)
    operation = atomic_append_text if append else atomic_write_text
    with pytest.raises(OSError, match="replaced"):
        operation(target, "changed", baseline_path=target,
                  baseline_sha256=hashlib.sha256(b"original").hexdigest())
    assert target.read_bytes() == b"original"


def test_atomic_write_rejects_metadata_change_while_hashing(tmp_path, monkeypatch):
    import hashlib
    import os

    from minimax_code.file_mutations import atomic_write_text

    target = tmp_path / "file.txt"
    target.write_bytes(b"original")
    original_digest = hashlib.file_digest

    def mutate_during_hash(*args, **kwargs):
        result = original_digest(*args, **kwargs)
        info = target.stat()
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns - 2_000_000_000))
        return result

    monkeypatch.setattr(hashlib, "file_digest", mutate_during_hash)
    with pytest.raises(OSError, match="changed while reading"):
        atomic_write_text(target, "changed", baseline_path=target,
                          baseline_sha256=hashlib.sha256(b"original").hexdigest())
    assert target.read_bytes() == b"original"


def test_atomic_write_rejects_changed_baseline(tmp_path):
    import hashlib

    from minimax_code.file_mutations import atomic_write_text

    target = tmp_path / "file.txt"
    target.write_bytes(b"user changed it")
    with pytest.raises(OSError, match="changed"):
        atomic_write_text(target, "agent changes", baseline_path=target,
                          baseline_sha256=hashlib.sha256(b"old baseline").hexdigest())
    assert target.read_bytes() == b"user changed it"


def test_atomic_append_failure_preserves_binary_prefix(tmp_path, monkeypatch):
    import hashlib

    from minimax_code import file_mutations

    target = tmp_path / "file.txt"
    original = b"\xff\x00original\r\n"
    target.write_bytes(original)

    def fail_replace(*args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(file_mutations.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        file_mutations.atomic_append_text(target, "tail", baseline_path=target,
                                           baseline_sha256=hashlib.sha256(original).hexdigest())
    assert target.read_bytes() == original
    assert not list(tmp_path.glob(".minimax-write-*"))
