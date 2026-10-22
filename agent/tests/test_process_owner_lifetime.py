"""Crash the service process itself, leaving cleanup to the managed launcher."""

from __future__ import annotations

import asyncio
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path

import pytest

from minimax_code.execution import process_environment, terminate_tree

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows process handles")


def kernel_api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel


@pytest.mark.parametrize("use_shell", [False, True])
async def test_service_crash_reaps_launcher_command_and_grandchild(tmp_path, use_shell):
    package_root = Path(__file__).resolve().parents[1]
    marker = tmp_path / "children.json"
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import subprocess,sys,time,os,json\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        f"marker=Path({str(marker)!r})\n"
        "pending=marker.with_suffix('.tmp')\n"
        "pending.write_text(json.dumps([os.getpid(),child.pid]))\n"
        "pending.replace(marker)\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    service = tmp_path / "service.py"
    service.write_text(
        "import asyncio,os,sys,json,subprocess\n"
        f"sys.path.insert(0,{str(package_root)!r})\n"
        "from minimax_code.execution import spawn_exec,spawn_shell\n"
        "async def main():\n"
        f"    args=[sys.executable,{str(worker)!r}]\n"
        + ("    child=await spawn_shell(subprocess.list2cmdline(args), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)\n"
           if use_shell else
           "    child=await spawn_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)\n")
        + "    print(json.dumps([os.getpid(),child.pid]),flush=True)\n"
        "    await asyncio.Event().wait()\n"
        "asyncio.run(main())\n", encoding="utf-8",
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(service), cwd=tmp_path, env=process_environment(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    kernel = kernel_api()
    handles = []
    try:
        service_pid, launcher_pid = json.loads(await asyncio.wait_for(proc.stdout.readline(), 10))
        for _ in range(200):
            if marker.exists():
                break
            await asyncio.sleep(0.025)
        assert marker.exists(), (await proc.stderr.read()).decode() if proc.returncode else "worker did not start"
        child_pids = json.loads(marker.read_text())
        for pid in [service_pid, launcher_pid, *child_pids]:
            handle = kernel.OpenProcess(0x00100001, False, pid)  # SYNCHRONIZE | TERMINATE
            assert handle, f"Expected live managed process {pid}"
            handles.append(handle)
        # Kill ONLY the actual service (not the venv Python redirector, and
        # not taskkill /T). The child's parent handle must trigger Job cleanup.
        assert kernel.TerminateProcess(handles[0], 37)
        for handle in handles[1:]:
            assert await asyncio.to_thread(kernel.WaitForSingleObject, handle, 5000) == 0
        await asyncio.wait_for(proc.wait(), 5)
    finally:
        # Handles identify only this test's processes even if PIDs get reused.
        for handle in handles:
            if kernel.WaitForSingleObject(handle, 0) == 0x102:
                kernel.TerminateProcess(handle, 99)
            kernel.CloseHandle(handle)
        await terminate_tree(proc)


async def test_reused_pid_identity_is_rejected_before_command(tmp_path):
    from minimax_code._windows_process_host import parent_arguments

    parent = list(parent_arguments())
    parent[2] = str(int(parent[2]) + 1)
    launcher = Path(__file__).resolve().parents[1] / "minimax_code/_windows_process_host.py"
    marker = tmp_path / "must-not-exist"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-S", str(launcher), *parent, "--exec", sys.executable,
        "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()",
        cwd=tmp_path, env=process_environment(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), 10)
        assert proc.returncode == 126
        assert b"identity changed" in stderr
        assert not marker.exists()
    finally:
        await terminate_tree(proc)
