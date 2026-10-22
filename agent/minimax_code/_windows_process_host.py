"""Private Windows process launcher; stdlib only, invoked with Python -I -S.

Join a kill-on-close Job BEFORE launching user code. The host's exit closes
the sole job handle, including if it is killed or its command exits while a
grandchild holds inherited pipes. This manages lifetime, not file/network
access. See https://learn.microsoft.com/windows/win32/procthread/job-objects
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
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


def main() -> int:
    try:
        job = join_job()
    except OSError as exc:
        print(f"Managed process Job setup failed; command was not started: {exc}", file=sys.stderr)
        return 126
    # Keep job open until process teardown. Explicitly closing it would also
    # kill this host before it can return the command's actual exit code.
    assert job
    try:
        shell = sys.argv[1] == "--shell"
        command = sys.argv[2] if shell else sys.argv[2:]
        return subprocess.call(command, shell=shell, stdin=sys.stdin, stdout=sys.stdout,
                               stderr=sys.stderr, creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError as exc:
        print(f"Managed command could not start: {exc}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main())
