"""Fixed, hashed PID-namespace init program for the supervised Linux profile.

Importing this module does not run the guard. The source is passed unchanged to
the pinned image's isolated interpreter, never interpolated with payload input.
The final policy and product fault tests are separate acceptance requirements.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence

GUARD_KIND = "pid1-boottime-v1"
PYTHON_PATH = "/usr/local/bin/python3"
MAX_ENVELOPE_BYTES = 65536
MAX_ARGV = 100
MAX_ARGUMENT_BYTES = 4096
MAX_DEADLINE_NS = (1 << 63) - 1

# Keep this exact UTF-8 source stable. Changes revoke the frozen target hash.
GUARD_SOURCE = r'''import ctypes
import json
import os
import re
import select
import signal
import sys
import time


def _bad_constant(value):
    raise ValueError("Non-finite JSON value")


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate envelope field")
        value[key] = item
    return value


def _decode(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
        raise ValueError("Envelope is too large")
    value = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_bad_constant)
    if not isinstance(value, dict) or set(value) != {
        "exec_id", "boot_id", "deadline_boottime_ns", "argv"
    }:
        raise ValueError("Envelope fields are incompatible")
    if type(value["exec_id"]) is not str or re.fullmatch(r"[0-9a-f]{32}", value["exec_id"]) is None:
        raise ValueError("Execution identity is incompatible")
    if type(value["boot_id"]) is not str or re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value["boot_id"]
    ) is None:
        raise ValueError("Boot identity is incompatible")
    deadline = value["deadline_boottime_ns"]
    if type(deadline) is not int or not 0 < deadline <= 9223372036854775807:
        raise ValueError("Deadline must be a positive signed int64")
    argv = value["argv"]
    if type(argv) is not list or not 0 < len(argv) <= 100 or not argv[0]:
        raise ValueError("Payload argv is incompatible")
    if any(type(arg) is not str or "\0" in arg or len(arg.encode("utf-8")) > 4096 for arg in argv):
        raise ValueError("Payload argument is incompatible")
    return value


class _Timespec(ctypes.Structure):
    _fields_ = [("seconds", ctypes.c_long), ("nanoseconds", ctypes.c_long)]


class _TimerSpec(ctypes.Structure):
    _fields_ = [("interval", _Timespec), ("value", _Timespec)]


def _run(value):
    if os.getpid() != 1 or os.uname().machine != "x86_64" or ctypes.sizeof(ctypes.c_long) != 8:
        raise RuntimeError("A supported private PID namespace is required")
    with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as stream:
        actual_boot_id = stream.read(65).strip()
    if actual_boot_id != value["boot_id"]:
        raise RuntimeError("Boot identity changed")
    clock = time.CLOCK_BOOTTIME
    deadline = value["deadline_boottime_ns"]
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(4, 0, 0, 0, 0) != 0 or libc.prctl(3, 0, 0, 0, 0) != 0:
        raise RuntimeError("PID1 dumpability protection is unavailable")
    # CPython installs a Python SIGINT handler. Re-entrant KeyboardInterrupt
    # during exception handling could reach blocking interpreter shutdown IO.
    # True namespace init ignores default-action signals from its descendants.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    libc.timerfd_create.argtypes = [ctypes.c_int, ctypes.c_int]
    libc.timerfd_create.restype = ctypes.c_int
    timer = libc.timerfd_create(clock, os.O_CLOEXEC | os.O_NONBLOCK)
    if timer < 0:
        raise RuntimeError("BOOTTIME timerfd is unavailable")
    libc.timerfd_settime.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(_TimerSpec), ctypes.c_void_p]
    libc.timerfd_settime.restype = ctypes.c_int
    spec = _TimerSpec()
    spec.value.seconds, spec.value.nanoseconds = divmod(deadline, 1000000000)
    if libc.timerfd_settime(timer, 1, ctypes.byref(spec), None) != 0:
        raise RuntimeError("Absolute BOOTTIME timer could not be armed")
    # Linux x86_64 syscall 436 (v6.8 arch/x86/entry/syscalls/syscall_64.tbl).
    # The pinned musl image has no close_range symbol. Probe an empty range
    # before fork; do not fall back to an unbounded per-descriptor close loop.
    close_range = libc.syscall
    close_range.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
    close_range.restype = ctypes.c_long
    if close_range(436, 4294967295, 4294967295, 0) != 0:
        raise RuntimeError("Kernel descriptor-range close is unavailable")
    poller = select.poll()
    poller.register(timer, select.POLLIN | select.POLLERR | select.POLLHUP)
    if time.clock_gettime_ns(clock) >= deadline:
        os._exit(124)
    leader = os.fork()
    if leader == 0:
        try:
            for name in ("SIGINT", "SIGPIPE", "SIGXFZ", "SIGXFSZ"):
                signo = getattr(signal, name, None)
                if signo is not None:
                    signal.signal(signo, signal.SIG_DFL)
            if close_range(436, 3, 4294967295, 0) != 0:
                os._exit(125)
            if time.clock_gettime_ns(clock) >= deadline:
                os._exit(124)
            os.execvpe(value["argv"][0], value["argv"], os.environ)
        except BaseException:
            os._exit(125)
    # The init never execs. It does not read/write payload streams, touch the
    # workspace, block in waitpid, or depend on a payload-controlled status file.
    while True:
        if time.clock_gettime_ns(clock) >= deadline:
            os._exit(124)
        for _ in range(32):
            if time.clock_gettime_ns(clock) >= deadline:
                os._exit(124)
            child, status = os.waitpid(-1, os.WNOHANG)
            if child == leader:
                if time.clock_gettime_ns(clock) >= deadline:
                    os._exit(124)
                if os.WIFEXITED(status):
                    os._exit(os.WEXITSTATUS(status))
                if os.WIFSIGNALED(status):
                    os._exit(128 + os.WTERMSIG(status))
                os._exit(125)
            if child == 0:
                break
        remaining = deadline - time.clock_gettime_ns(clock)
        if remaining <= 0:
            os._exit(124)
        # Poll timeout is finite even across suspend; timerfd is BOOTTIME based.
        events = poller.poll(min(50, (remaining + 999999) // 1000000))
        if events:
            if any(mask & (select.POLLERR | select.POLLHUP | select.POLLNVAL) for _, mask in events):
                os._exit(125)
            os._exit(124)


def _entry():
    try:
        if len(sys.argv) != 2:
            os._exit(125)
        _run(_decode(sys.argv[1]))
    except BaseException:
        # The parent has no Python SIGINT handler once a payload can exist.
        # Logging here could block forever on a reader-controlled pipe.
        os._exit(125)


if __name__ == "__main__":
    _entry()
'''
GUARD_SHA256 = hashlib.sha256(GUARD_SOURCE.encode("utf-8")).hexdigest()


def build_guard_argv(*, exec_id: str, boot_id: str, deadline_boottime_ns: int,
                     argv: Sequence[str]) -> tuple[str, ...]:
    """Return the sole fixed entrypoint and one bounded canonical JSON argument."""
    if type(exec_id) is not str or re.fullmatch(r"[0-9a-f]{32}", exec_id) is None:
        raise ValueError("Execution identity must be 32 lowercase hexadecimal characters")
    if type(boot_id) is not str or re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", boot_id
    ) is None:
        raise ValueError("Boot identity must be a canonical lowercase UUID")
    if type(deadline_boottime_ns) is not int or not 0 < deadline_boottime_ns <= MAX_DEADLINE_NS:
        raise ValueError("Deadline must be a positive signed int64")
    if not isinstance(argv, Sequence) or isinstance(argv, (str, bytes, bytearray)):
        raise ValueError("Payload argv must be a sequence of strings")
    values = list(argv)
    if not 0 < len(values) <= MAX_ARGV or not values[0]:
        raise ValueError("Payload argv is empty or too large")
    try:
        if any(type(arg) is not str or "\0" in arg or len(arg.encode("utf-8")) > MAX_ARGUMENT_BYTES
               for arg in values):
            raise ValueError("Payload argument is incompatible")
        envelope = json.dumps({"exec_id": exec_id, "boot_id": boot_id,
            "deadline_boottime_ns": deadline_boottime_ns, "argv": values},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        if len(envelope.encode("utf-8")) > MAX_ENVELOPE_BYTES:
            raise ValueError("Payload envelope exceeds the supported byte limit")
    except UnicodeError as exc:
        raise ValueError("Payload arguments must be valid UTF-8") from exc
    return (PYTHON_PATH, "-I", "-S", "-c", GUARD_SOURCE, envelope)
