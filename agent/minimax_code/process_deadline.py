"""Host boot identity and suspend-aware, immutable process deadlines."""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from .process_types import ProcessBoundaryError

_BOOT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MAX_INT64 = (1 << 63) - 1


def read_clock() -> tuple[str, int]:
    if sys.platform != "linux" or not hasattr(time, "CLOCK_BOOTTIME"):
        raise ProcessBoundaryError("Linux CLOCK_BOOTTIME is required for restricted execution")
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    now = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    if not _BOOT_ID.fullmatch(boot_id) or type(now) is not int or not 0 < now <= MAX_INT64:
        raise ProcessBoundaryError("Host boot identity or BOOTTIME is incompatible")
    return boot_id, now


def new_deadline(timeout_seconds: int) -> tuple[str, int]:
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ProcessBoundaryError("Process timeout must be a positive integer")
    boot_id, now = read_clock()
    deadline = now + timeout_seconds * 1_000_000_000
    if not 0 < deadline <= MAX_INT64:
        raise ProcessBoundaryError("Process BOOTTIME deadline exceeds signed int64")
    return boot_id, deadline


def remaining_seconds(row: dict) -> float:
    boot_id, now = read_clock()
    deadline = row.get("deadline_boottime_ns")
    if (row.get("deadline_boot_id") != boot_id or type(deadline) is not int
            or not 0 < deadline <= MAX_INT64):
        raise ProcessBoundaryError("Process boot changed or immutable deadline is missing; cleanup only")
    return max(0, deadline - now) / 1_000_000_000
