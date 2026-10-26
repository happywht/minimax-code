"""Crash detection — independent module (R12).

Fuses grok-build's ``xai-crash-handler`` design (separate crate,
``install()`` at ``main()`` entry, ``check_previous_crash()`` on next
boot) into Python via :mod:`faulthandler`. The agent's business logic
stays decoupled — this module owns only the marker-file lifecycle plus
the fault traceback sink.

Marker protocol (the whole point)
---------------------------------
1. CLI acquires a service lease, then :func:`prepare_startup_marker`
   captures the old report before atomically writing its own owner token.
2. Recovery reads the captured report once; the current marker stays present.
3. Only confirmed runtime and event-loop shutdown calls the owner's
   ``mark_clean`` before releasing the lease. No atexit cleanup infers success.

The legacy standalone mark/check helpers remain available for embedders/tests;
they do not themselves establish exclusive service ownership.

This is deliberately file-based and process-independent, so it survives
whatever killed the agent (OOM, segfault, ``kill -9``, power loss) as
long as the data dir is on local disk. The data dir must NOT live on a
network filesystem — see the SQLite-over-NFS caveat documented in
``docs/evolution/ITERATION_LOG.md`` (R12).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ..storage.dao._base import now_iso

logger = logging.getLogger(__name__)

# Marker file name (one per data dir ⇒ one per agent install).
MARKER_NAME = ".minimax_code_running"
# Fault traceback sink — appended on segfault / fault signals.
TRACE_NAME = "last_fault.trace"

# Keep a handle on the faulthandler sink so the OS doesn't GC-close it
# before a crash can write to it. Mirrors grok's pre-opened
# ``last-crash.bin``.
_FAULT_SINK: object | None = None


@dataclass
class CrashReport:
    """A previously-crashed run detected on boot.

    Attributes
    ----------
    crashed_pid:
        OS pid recorded by :func:`mark_dirty_start`.
    started_at:
        ISO timestamp of the crashed run's start.
    detected_at:
        ISO timestamp of this boot's detection.
    """

    crashed_pid: int
    started_at: str
    detected_at: str


def marker_path(data_dir: Path) -> Path:
    """Resolve the marker path under ``data_dir``."""
    return data_dir / MARKER_NAME


def trace_path(data_dir: Path) -> Path:
    """Resolve the fault-traceback sink path under ``data_dir``."""
    return data_dir / TRACE_NAME


def mark_dirty_start(data_dir: Path) -> None:
    """Record that a run is in flight.

    Writes ``{pid, started_at}`` atomically (temp + rename). Called at
    process entry so a crash before clean exit leaves the marker behind.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "started_at": now_iso()}
    target = marker_path(data_dir)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, target)


def mark_clean_exit(data_dir: Path) -> None:
    """Remove the marker on graceful shutdown.

    Legacy standalone helper. The CLI uses StartupMarkerOwner.mark_clean,
    which verifies the owner token after confirmed runtime shutdown.
    """
    marker = marker_path(data_dir)
    try:
        marker.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("could not remove crash marker %s: %s", marker, exc)


def check_previous_crash(data_dir: Path) -> CrashReport | None:
    """Return a :class:`CrashReport` if the previous run crashed.

    On detection the marker is consumed (deleted) so a single crash is
    reported exactly once; a subsequent clean boot returns ``None``.
    """
    marker = marker_path(data_dir)
    if not marker.exists():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Corrupt marker shouldn't block boot — treat as a crash with
        # whatever we can recover, then consume it.
        logger.warning("crash marker corrupt (%s); consuming", exc)
        payload = {"pid": -1, "started_at": ""}
    if not isinstance(payload, dict):
        payload = {}
    try:
        previous_pid = int(payload.get("pid", -1))
    except (TypeError, ValueError, OverflowError):
        previous_pid = -1
    report = CrashReport(
        crashed_pid=previous_pid,
        started_at=str(payload.get("started_at", "")),
        detected_at=now_iso(),
    )
    # Construct a valid report before consuming unknown/corrupt old evidence.
    mark_clean_exit(data_dir)
    return report


def install_faulthandler(data_dir: Path) -> bool:
    """Enable :mod:`faulthandler` to capture segfault tracebacks.

    Opens a pre-existing append sink (``last_fault.trace``) and keeps a
    module-level reference so the file descriptor stays open until a
    fault actually writes to it. Returns ``True`` if installed, ``False``
    on any failure (fail-open — never blocks boot).
    """
    global _FAULT_SINK
    try:
        import faulthandler

        data_dir.mkdir(parents=True, exist_ok=True)
        sink = trace_path(data_dir).open("ab", buffering=0)
        faulthandler.enable(sink, all_threads=True)
        _FAULT_SINK = sink
        return True
    except Exception as exc:  # pragma: no cover — environment-dependent
        logger.debug("faulthandler install failed (fail-open): %s", exc)
        return False


def crash_report_to_dict(report: CrashReport | None) -> dict | None:
    """Serialise a :class:`CrashReport` for IPC / logging (``None``-safe)."""
    return asdict(report) if report is not None else None


class StartupMarkerOwner:
    """A CLI startup's marker, protected by its already acquired service lease.

    Previous recovery is captured *before* writing the current marker. There is
    intentionally no atexit/destructor cleanup: only confirmed shutdown is clean.
    """

    def __init__(self, lease):
        lease.assert_owned(lease.data_dir)
        self.lease = lease
        self.pid = os.getpid()
        self.token = uuid.uuid4().hex
        self.previous = check_previous_crash(lease.data_dir)
        self._consumed = False
        self._closed = False
        target = marker_path(lease.data_dir)
        temporary = target.with_name(f"{target.name}.{self.token}.tmp")
        try:
            temporary.write_text(json.dumps({"pid": self.pid, "started_at": now_iso(),
                                             "owner": self.token}), encoding="utf-8")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def previous_once(self) -> CrashReport | None:
        self.lease.assert_owned(self.lease.data_dir)
        if self._consumed:
            return None
        self._consumed = True
        return self.previous

    def mark_clean(self) -> None:
        if self._closed or self.pid != os.getpid():
            return
        self.lease.assert_owned(self.lease.data_dir)
        target = marker_path(self.lease.data_dir)
        # Never remove a marker written by a later owner or a foreign writer.
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("owner") != self.token:
            raise RuntimeError("Startup marker ownership changed before clean shutdown")
        target.unlink()
        self._closed = True


_STARTUP_OWNERS: dict[Path, StartupMarkerOwner] = {}


def prepare_startup_marker(lease) -> StartupMarkerOwner:
    """Capture the old marker and install the current marker under the lease."""
    owner = StartupMarkerOwner(lease)
    _STARTUP_OWNERS[lease.data_dir] = owner
    return owner


def startup_previous_crash(data_dir: Path) -> CrashReport | None:
    """CLI recovery consumes its captured old report, never its current marker.

    Bare embedders retain the existing standalone marker API. They must arrange
    their own lifetime/ownership; this helper never implicitly acquires a lock.
    """
    owner = _STARTUP_OWNERS.get(data_dir.expanduser().resolve())
    if owner is not None and owner.pid == os.getpid() and not owner._closed:
        return owner.previous_once()
    return check_previous_crash(data_dir)


__all__ = [
    "CrashReport",
    "MARKER_NAME",
    "TRACE_NAME",
    "check_previous_crash",
    "crash_report_to_dict",
    "install_faulthandler",
    "mark_clean_exit",
    "mark_dirty_start",
    "marker_path",
    "trace_path",
]
