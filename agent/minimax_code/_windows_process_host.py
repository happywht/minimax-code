"""Private Windows process launcher; stdlib only, invoked with Python -I -S.

Join a kill-on-close Job BEFORE launching user code. The host's exit closes
the sole job handle, including if it is killed or its command exits while a
grandchild holds inherited pipes. This manages lifetime, not file/network
access. See https://learn.microsoft.com/windows/win32/procthread/job-objects
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from ctypes import wintypes


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("ProcessTime", ctypes.c_longlong), ("JobTime", ctypes.c_longlong),
        ("Flags", wintypes.DWORD), ("MinWorkingSet", ctypes.c_size_t),
        ("MaxWorkingSet", ctypes.c_size_t), ("ActiveProcesses", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t), ("Priority", wintypes.DWORD),
        ("Scheduling", wintypes.DWORD),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("Basic", BasicLimits), ("IoCounters", ctypes.c_ulonglong * 6),
        ("ProcessMemory", ctypes.c_size_t), ("JobMemory", ctypes.c_size_t),
        ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t),
    ]


def join_job() -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)  # non-inheritable, unnamed handle
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    return job


def _process_kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _creation_time(kernel, process: int) -> int:
    times = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(process, *(ctypes.byref(value) for value in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def parent_arguments() -> tuple[str, str, str]:
    """Identify the actual service, including when Python uses a venv redirector."""
    kernel = _process_kernel()
    return "--parent", str(os.getpid()), str(_creation_time(kernel, kernel.GetCurrentProcess()))


def watch_parent(pid: int, created: int) -> None:
    """Hold a process handle, not a periodically re-opened/reusable PID.

    The daemon owns the handle until process teardown. Closing a handle while
    WaitForSingleObject is pending is undefined, so the main thread never
    closes it. os._exit closes this launcher's Job and kills its whole tree.
    """
    if pid <= 0 or created <= 0:
        raise ValueError("Invalid service process identity")
    kernel = _process_kernel()
    # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION; no terminate rights.
    parent = kernel.OpenProcess(0x00101000, False, pid)
    if not parent:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if _creation_time(kernel, parent) != created:
            raise ValueError("Service process identity changed")
        if kernel.WaitForSingleObject(parent, 0) != 0x102:  # WAIT_TIMEOUT = alive
            raise ValueError("Service process already exited")
    except BaseException:
        kernel.CloseHandle(parent)
        raise

    def wait() -> None:
        # A wait failure is also fail-closed: do not leave an unmanaged tree.
        kernel.WaitForSingleObject(parent, 0xFFFFFFFF)
        os._exit(125)

    try:
        threading.Thread(target=wait, daemon=True, name="service-lifetime").start()
    except BaseException:
        kernel.CloseHandle(parent)
        raise


def main() -> int:
    try:
        job = join_job()
        if len(sys.argv) < 6 or sys.argv[1] != "--parent" or sys.argv[4] not in {"--exec", "--shell"}:
            raise ValueError("Managed process requires a service identity and command")
        watch_parent(int(sys.argv[2]), int(sys.argv[3]))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Managed process lifetime setup failed; command was not started: {exc}", file=sys.stderr)
        return 126
    # Keep job open until process teardown. Explicitly closing it would also
    # kill this host before it can return the command's actual exit code.
    assert job
    try:
        shell = sys.argv[4] == "--shell"
        command = sys.argv[5] if shell else sys.argv[5:]
        return subprocess.call(command, shell=shell, stdin=sys.stdin, stdout=sys.stdout,
                               stderr=sys.stderr, creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError as exc:
        print(f"Managed command could not start: {exc}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main())
