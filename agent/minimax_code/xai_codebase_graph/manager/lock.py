r"""Workspace-level locking for index operations (R305h).

Ported **by function** (not line-by-line) from grok
``xai-codebase-graph/src/manager/lock.rs`` (539 lines). The module owns a
single concern: coordinating index operations on the same workspace so that
redundant builds / saves / refreshes do not stomp each other, while allowing
concurrent readers.

Two coordination layers (mirrors grok's two-layer design):

* **In-memory locks** -- the fast path for same-process deduplication. A
  module-global registry keyed by canonical workspace path tracks how many
  shared readers are active and whether an exclusive lock is held. grok uses
  ``DashMap<PathBuf, InMemoryLockState>`` (lock-free concurrent hashmap); the
  Python port uses a plain :class:`dict` guarded by a single
  :class:`threading.Lock`. The entry-API atomic check-and-modify that grok
  performs inside ``DashMap::entry`` becomes a ``with _REGISTRY_GUARD:`` block
  -- same atomicity guarantee (the whole acquire is one critical section), no
  concurrent readers needed because index operations are coarse-grained.
* **File locks** -- cross-process coordination. An exclusive operation writes a
  ``.goto_index.lock`` file beside the cache containing ``operation`` /
  ``pid`` / ``started`` / ``workspace`` lines; a later contender parses it,
  considers it stale when the holder PID is dead **or** the per-operation
  timeout elapsed, and otherwise backs off. The lock-file format is kept
  byte-compatible with grok so a mixed Rust/Python fleet agrees on liveness.

Lock modes (mirrors grok ``IndexOperation``):

* **Shared (Load)** -- many readers allowed; blocked while an exclusive lock
  is held.
* **Exclusive (Save / Build / BackgroundRefresh)** -- single writer; blocks
  every other operation (shared and exclusive).

Functional-clone decisions (the load-bearing deltas vs. a structural port):

1. ``enum IndexOperation`` -> :class:`IndexOperation` (:mod:`enum`). The
   ``as_str`` / ``Display`` impl collapses to ``__str__`` returning the enum
   ``value`` (the value already *is* the lock-file string), and the four
   stale-timeout constants stay module-private integers compared in seconds.
2. ``static IN_MEMORY_LOCKS: Lazy<DashMap<..>>`` -> module-global ``dict`` +
   :class:`threading.Lock`. ``once_cell::Lazy`` is unnecessary in Python: the
   module-level binding is initialised at import time.
3. RAII ``WorkspaceLockGuard`` + ``Drop`` impl -> :meth:`WorkspaceLockGuard.release`
   plus ``__del__`` (best-effort, mirrors ``Drop`` firing when the binding goes
   out of scope) and ``__enter__`` / ``__exit__`` (the Pythonic structured
   form). ``release`` is idempotent so all three paths are safe to fire.
4. ``enum LockResult { Acquired(guard), Busy { operation, holder_pid } }`` ->
   a single :class:`LockResult` carrying an optional guard plus busy
   metadata. Python has no native sum type; one class with an
   ``is_acquired`` discriminator reads cleaner than two subclasses and keeps
   ``unwrap`` / ``release`` in one place. ``LockResult`` also owns the guard's
   lifetime -- dropping a ``LockResult`` releases the inner guard, exactly as
   grok's ``LockResult::Acquired(WorkspaceLockGuard)`` drops the guard when
   the enum value drops.
5. ``SystemTime`` / ``UNIX_EPOCH`` / ``Duration`` -> epoch seconds as
   :class:`int` (``int(time.time())``). grok computes ``age =
   now.duration_since(started)``; the Python port computes ``age =
   _now_epoch() - started``. Same semantics, one integer subtract.
6. ``is_process_alive`` keeps grok's platform split: POSIX probes with
   ``os.kill(pid, 0)`` (``ProcessLookupError`` -> dead, ``PermissionError``
   -> alive-but-foreign); non-POSIX returns ``True`` and relies on the
   timeout-based stale path. This is grok's deliberate design, preserved
   faithfully.
7. ``dunce::canonicalize`` -> :meth:`pathlib.Path.resolve` (``strict=False``
   by default, so a not-yet-existing workspace still canonicalises). ``dunce``
   exists to avoid the UNC ``\\?\`` prefix on Windows; Python's ``resolve``
   already returns a usable path, so no shim is needed.
8. ``tracing::{debug,warn}`` -> :mod:`logging` at the module logger. Same
   call sites, same severity, same context fields.

The cross-process file lock is intentionally **not** an atomic
``O_EXCL`` create -- grok reads-then-writes with a TOCTOU window, and the
Python port preserves that. The window is benign for this crate's use case
(a single agent process per workspace plus rare background refreshes); making
it atomic would diverge from grok's observable behaviour.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from enum import Enum
from pathlib import Path

from minimax_code.xai_codebase_graph.manager.cache import get_cache_path

__all__ = [
    "BG_REFRESH_STALE_DURATION_SEC",
    "BUILD_STALE_DURATION_SEC",
    "LOAD_STALE_DURATION_SEC",
    "SAVE_STALE_DURATION_SEC",
    "IndexOperation",
    "LockResult",
    "WorkspaceLockGuard",
    "is_operation_in_progress",
    "try_lock",
]

_LOG = logging.getLogger(__name__)

# Stale durations in seconds. A lock older than its operation's threshold is
# considered reclaimable regardless of the holder PID's liveness. Mirrors grok
# ``const`` block (LOAD/SAVE 120s, BUILD 600s, BG_REFRESH 300s).
LOAD_STALE_DURATION_SEC: int = 120
SAVE_STALE_DURATION_SEC: int = 120
BUILD_STALE_DURATION_SEC: int = 600
BG_REFRESH_STALE_DURATION_SEC: int = 300


class IndexOperation(Enum):
    """The four index operations that contend for a workspace lock.

    ``Load`` is shared (read); the other three are exclusive (write). Mirrors
    grok ``enum IndexOperation`` -- the enum ``value`` is the exact string
    written to the ``operation=`` line of the lock file, so a mixed
    Rust/Python fleet reads identical tokens.
    """

    Load = "load"
    Save = "save"
    Build = "build"
    BackgroundRefresh = "background_refresh"

    def is_exclusive(self) -> bool:
        """Whether this operation requires exclusive (single-writer) access.

        grok ``fn is_exclusive``: ``Load`` is shared, the other three are
        exclusive.
        """
        return self is not IndexOperation.Load

    def stale_timeout(self) -> int:
        """Seconds after which a lock of this kind is considered reclaimable.

        Mirrors grok ``fn stale_timeout`` (returns ``Duration``, here seconds).
        """
        if self is IndexOperation.Load:
            return LOAD_STALE_DURATION_SEC
        if self is IndexOperation.Save:
            return SAVE_STALE_DURATION_SEC
        if self is IndexOperation.Build:
            return BUILD_STALE_DURATION_SEC
        return BG_REFRESH_STALE_DURATION_SEC

    def __str__(self) -> str:  # grok ``impl Display`` -> ``as_str``
        return self.value


class _InMemoryLockState:
    """Per-workspace in-memory lock counters (same-process dedup).

    ``readers`` counts active shared (Load) holders; ``exclusive`` is set
    while a writer holds the workspace. Mirrors grok ``struct
    InMemoryLockState``; kept module-private because only the acquire/release
    helpers below ever touch it.
    """

    __slots__ = ("operation", "readers", "exclusive")

    def __init__(
        self,
        operation: IndexOperation,
        *,
        readers: int = 0,
        exclusive: bool = False,
    ) -> None:
        self.operation = operation
        self.readers = readers
        self.exclusive = exclusive


# Global registry of in-memory locks keyed by canonical workspace path.
# grok uses ``Lazy<DashMap<PathBuf, InMemoryLockState>>`` (lock-free); the
# Python port uses a plain dict behind one threading.Lock -- the acquire /
# release critical sections are tiny and coarse-grained, so a single mutex is
# both correct and simpler than a concurrent map.
_IN_MEMORY_LOCKS: dict[Path, _InMemoryLockState] = {}
_IN_MEMORY_LOCKS_GUARD = threading.Lock()


class WorkspaceLockGuard:
    """RAII guard that releases the lock when released/dropped.

    Mirrors grok ``struct WorkspaceLockGuard`` + its ``Drop`` impl: on
    release it (1) decrements / clears the in-memory state, and (2) for
    exclusive operations, removes the lock file ( tolerating an already-absent
    file). ``release`` is idempotent -- the ``_released`` flag makes the
    explicit, ``__del__``, and ``__exit__`` paths mutually safe.
    """

    __slots__ = ("_workspace", "_lock_file_path", "_operation", "_released")

    def __init__(
        self,
        workspace: Path,
        lock_file_path: Path,
        operation: IndexOperation,
    ) -> None:
        self._workspace = workspace
        self._lock_file_path = lock_file_path
        self._operation = operation
        self._released = False

    @property
    def workspace(self) -> Path:
        """The canonical workspace path this guard locks."""
        return self._workspace

    @property
    def operation(self) -> IndexOperation:
        """The operation this guard was acquired for."""
        return self._operation

    def release(self) -> None:
        """Release both the in-memory and (for exclusive ops) file lock.

        Idempotent; safe to call from ``__del__`` after explicit release.
        Mirrors grok ``Drop::drop``.
        """
        if self._released:
            return
        self._released = True

        # Step 1: release the in-memory lock (shared or exclusive).
        _release_in_memory_lock(self._workspace, self._operation)

        # Step 2: for exclusive operations only, remove the lock file. A
        # missing file is fine (already cleaned, or never created); any other
        # OSError is logged but never re-raised -- grok uses ``tracing::warn``
        # for the same case so the guard's release never propagates failure.
        if self._operation.is_exclusive():
            try:
                self._lock_file_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:  # pragma: no cover - filesystem-dependent
                _LOG.warning(
                    "Failed to remove lock file %s: %s", self._lock_file_path, exc
                )

    def __del__(self) -> None:
        # Best-effort release mirroring RAII drop. Swallow any exception so
        # interpreter-shutdown quirks never leak into the caller.
        try:
            self.release()
        except Exception:  # noqa: BLE001 - __del__ must never raise
            pass

    def __enter__(self) -> WorkspaceLockGuard:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class LockResult:
    """Outcome of :func:`try_lock`: either acquired (holds a guard) or busy.

    Mirrors grok ``enum LockResult { Acquired(WorkspaceLockGuard), Busy {
    operation, holder_pid } }``. Python has no sum type, so this is one class
    with an ``is_acquired`` discriminator: when acquired it owns the guard
    (and releasing the result releases the guard, exactly as dropping grok's
    ``Acquired`` variant drops the guard); when busy it carries the blocking
    operation description and the holder PID if known.
    """

    __slots__ = ("_guard", "_busy_operation", "_holder_pid", "_released")

    def __init__(
        self,
        *,
        guard: WorkspaceLockGuard | None = None,
        busy_operation: str | None = None,
        holder_pid: int | None = None,
    ) -> None:
        self._guard = guard
        self._busy_operation = busy_operation
        self._holder_pid = holder_pid
        self._released = False

    # -- construction helpers (mirror grok enum variant construction) -------

    @classmethod
    def acquired(cls, guard: WorkspaceLockGuard) -> LockResult:
        """``LockResult::Acquired(guard)``."""
        return cls(guard=guard)

    @classmethod
    def busy(
        cls,
        operation: str,
        holder_pid: int | None = None,
    ) -> LockResult:
        """``LockResult::Busy { operation, holder_pid }``."""
        return cls(busy_operation=operation, holder_pid=holder_pid)

    # -- query -------------------------------------------------------------

    def is_acquired(self) -> bool:
        """True when the lock was acquired (mirrors grok ``is_acquired``)."""
        return self._guard is not None

    @property
    def busy_operation(self) -> str | None:
        """Description of the blocking operation (None when acquired)."""
        return self._busy_operation

    @property
    def holder_pid(self) -> int | None:
        """PID of the process holding the lock, if known (None otherwise)."""
        return self._holder_pid

    # -- unwrap / release --------------------------------------------------

    def unwrap(self) -> WorkspaceLockGuard:
        """Return the guard, raising if the lock was busy.

        Mirrors grok ``LockResult::unwrap`` which panics on ``Busy``. The
        Python port raises :class:`RuntimeError` (panic has no Python
        equivalent) carrying the busy description.
        """
        if self._guard is None:
            raise RuntimeError(f"Lock was busy: {self._busy_operation}")
        return self._guard

    def release(self) -> None:
        """Release the held guard, if any (idempotent).

        Dropping a grok ``LockResult::Acquired`` drops the inner guard; this
        method makes that explicit and safe to call from ``__del__``.
        """
        if self._released:
            return
        self._released = True
        if self._guard is not None:
            self._guard.release()
            self._guard = None

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:  # noqa: BLE001 - __del__ must never raise
            pass

    def __enter__(self) -> WorkspaceLockGuard:
        return self.unwrap()

    def __exit__(self, *exc: object) -> None:
        self.release()


# === public entry points ===================================================


def try_lock(workspace: os.PathLike[str] | str, operation: IndexOperation) -> LockResult:
    """Try to acquire a lock for an index operation on ``workspace``.

    Returns :class:`LockResult`; inspect with :meth:`LockResult.is_acquired`.
    Mirrors grok ``fn try_lock``: in-memory acquire first (same-process fast
    path), then -- for exclusive operations only -- a file-lock acquire
    (cross-process). A file-lock failure releases the in-memory lock so the
    workspace is not left half-locked.

    Parameters
    ----------
    workspace:
        The workspace root path. Canonicalised before use as the lock key.
    operation:
        The operation to perform (determines shared vs exclusive + timeout).
    """
    workspace_path = _canonicalize_workspace(workspace)
    lock_file_path = _get_lock_file_path(workspace_path)

    # Step 1: in-memory lock (same-process dedup, fast path).
    if not _try_acquire_in_memory_lock(workspace_path, operation):
        _LOG.debug(
            "In-memory lock busy: workspace=%s operation=%s",
            workspace_path,
            operation,
        )
        return LockResult.busy(
            f"{operation} (same process)",
            holder_pid=os.getpid(),
        )

    # Step 2: exclusive operations also take a file lock (cross-process).
    if operation.is_exclusive():
        busy = _try_acquire_file_lock(lock_file_path, operation)
        if busy is not None:
            # File lock contested -- roll back the in-memory acquire so a later
            # caller is not blocked by a state we failed to fully realise.
            blocking_op, blocking_pid = busy
            _release_in_memory_lock(workspace_path, operation)
            _LOG.debug(
                "File lock busy: workspace=%s operation=%s blocking_op=%s "
                "blocking_pid=%s",
                workspace_path,
                operation,
                blocking_op,
                blocking_pid,
            )
            return LockResult.busy(blocking_op, holder_pid=blocking_pid)

    _LOG.debug(
        "Lock acquired: workspace=%s operation=%s lock_file=%s",
        workspace_path,
        operation,
        lock_file_path,
    )
    return LockResult.acquired(
        WorkspaceLockGuard(workspace_path, lock_file_path, operation)
    )


def is_operation_in_progress(
    workspace: os.PathLike[str] | str,
    operation: IndexOperation,
) -> bool:
    """Non-blocking probe: is ``operation`` (or a conflicting one) running?

    Acquires nothing -- pure read. Mirrors grok ``fn
    is_operation_in_progress``: an exclusive operation is considered in
    progress if any reader or writer holds the workspace; a shared operation
    only if an exclusive holder exists. Exclusive operations additionally
    consult the cross-process lock file (parsing + liveness + timeout).
    """
    workspace_path = _canonicalize_workspace(workspace)

    # In-memory check first (same process).
    with _IN_MEMORY_LOCKS_GUARD:
        state = _IN_MEMORY_LOCKS.get(workspace_path)
        if state is not None:
            if operation.is_exclusive():
                if state.readers > 0 or state.exclusive:
                    return True
            elif state.exclusive:
                return True

    # Cross-process check for exclusive operations only.
    if operation.is_exclusive():
        lock_file_path = _get_lock_file_path(workspace_path)
        try:
            contents = lock_file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return False
        except OSError:
            return False
        parsed = _parse_lock_file(contents)
        if parsed is not None:
            _op_str, pid, started = parsed
            age = _now_epoch() - started
            if age < operation.stale_timeout() and _is_process_alive(pid):
                return True

    return False


# === private helpers =======================================================


def _canonicalize_workspace(workspace: os.PathLike[str] | str) -> Path:
    """Canonicalise the workspace path for consistent lock keys.

    Mirrors grok ``fn canonicalize_workspace`` (``dunce::canonicalize`` with a
    fallback to the raw path). Python ``Path.resolve(strict=False)`` never
    raises on a missing path, so the fallback branch is belt-and-braces.
    """
    try:
        return Path(workspace).resolve()
    except OSError:
        return Path(workspace)


def _get_lock_file_path(workspace: Path) -> Path:
    """Lock file lives beside the cache (``.goto_index.bin`` -> ``.goto_index.lock``).

    Mirrors grok ``fn get_lock_file_path`` which calls
    ``super::get_cache_path(workspace).with_extension("lock")``. Python's
    ``with_suffix`` swaps the trailing ``.bin`` for ``.lock`` identically.
    """
    cache_path = get_cache_path(workspace)
    return cache_path.with_suffix(".lock")


def _try_acquire_in_memory_lock(workspace: Path, operation: IndexOperation) -> bool:
    """Atomically acquire / refuse the in-memory lock.

    Mirrors grok ``fn try_acquire_in_memory_lock`` using the ``DashMap::entry``
    API: absent -> insert a fresh state; occupied exclusive-seeker -> require
    zero readers and no exclusive; occupied shared-seeker -> require no
    exclusive. The whole check-and-mutate runs under one critical section.
    """
    with _IN_MEMORY_LOCKS_GUARD:
        state = _IN_MEMORY_LOCKS.get(workspace)
        if state is None:
            _IN_MEMORY_LOCKS[workspace] = _InMemoryLockState(
                operation,
                readers=0 if operation.is_exclusive() else 1,
                exclusive=operation.is_exclusive(),
            )
            return True

        if operation.is_exclusive():
            if state.readers > 0 or state.exclusive:
                return False
            state.exclusive = True
            state.operation = operation
        else:
            if state.exclusive:
                return False
            state.readers += 1
        return True


def _release_in_memory_lock(workspace: Path, operation: IndexOperation) -> None:
    """Release the in-memory lock, removing the entry once drained.

    Mirrors grok ``fn release_in_memory_lock``: exclusive -> clear the flag;
    shared -> ``saturating_sub`` the reader count (``max(0, n-1)``); drop the
    entry entirely once no readers and no exclusive holder remain.
    """
    with _IN_MEMORY_LOCKS_GUARD:
        state = _IN_MEMORY_LOCKS.get(workspace)
        if state is None:
            return
        if operation.is_exclusive():
            state.exclusive = False
        else:
            state.readers = max(0, state.readers - 1)
        if not state.exclusive and state.readers == 0:
            del _IN_MEMORY_LOCKS[workspace]


def _try_acquire_file_lock(
    lock_path: Path,
    operation: IndexOperation,
) -> tuple[str, int | None] | None:
    """Try to claim the cross-process lock file.

    Returns ``None`` on success, or ``(operation_str, holder_pid)`` describing
    the live blocker when contested. Mirrors grok ``fn try_acquire_file_lock``:
    parse any existing file, treat it as stale when the holder PID is dead or
    the per-operation timeout elapsed (else return the blocker), ensure the
    parent directory exists, then overwrite the file with our claim.

    The ``Result<(), (String, Option<u32>)>`` return shape maps directly to
    ``Optional[tuple[str, Optional[int]]]`` -- ``None`` is the ``Ok(())`` arm.
    """
    # Inspect any existing lock file for a live contender.
    try:
        existing = lock_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = None
    except OSError:
        existing = None

    if existing is not None:
        parsed = _parse_lock_file(existing)
        if parsed is not None:
            op_str, pid, started = parsed
            age = _now_epoch() - started
            if age < operation.stale_timeout() and _is_process_alive(pid):
                return (op_str, pid)
            _LOG.debug(
                "Taking over stale lock: lock_path=%s stale_op=%s stale_pid=%s "
                "age_secs=%d",
                lock_path,
                op_str,
                pid,
                age,
            )

    # Ensure the parent directory exists (best-effort, mirrors grok).
    parent = lock_path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - filesystem-dependent
        _LOG.warning("Failed to create lock directory %s: %s", parent, exc)

    # Overwrite with our claim. ``workspace`` is the cache directory's name
    # (mirrors grok ``lock_path.parent().file_name()``), purely diagnostic.
    workspace_name = parent.name or "unknown"
    body = (
        f"operation={operation.value}\n"
        f"pid={os.getpid()}\n"
        f"started={_now_epoch()}\n"
        f"workspace={workspace_name}\n"
    )
    try:
        lock_path.write_text(body, encoding="utf-8")
    except OSError as exc:
        # grok maps the io::Error to ``Err((format!("io_error: {}", e), None))``.
        return (f"io_error: {exc}", None)
    return None


def _parse_lock_file(contents: str) -> tuple[str, int, int] | None:
    """Parse ``operation`` / ``pid`` / ``started`` lines from a lock file.

    Returns ``None`` if any of the three fields is missing or malformed
    (mirrors grok ``fn parse_lock_file``'s ``Option<(_,_ ,_)>`` final match).
    """
    operation: str | None = None
    pid: int | None = None
    started: int | None = None

    for line in contents.splitlines():
        if line.startswith("operation="):
            operation = line[len("operation="):]
        elif line.startswith("pid="):
            try:
                pid = int(line[len("pid="):])
            except ValueError:
                pid = None
        elif line.startswith("started="):
            try:
                started = int(line[len("started="):])
            except ValueError:
                started = None

    if operation is not None and pid is not None and started is not None:
        return (operation, pid, started)
    return None


def _is_process_alive(pid: int) -> bool:
    """Liveness probe, mirroring grok's platform-split ``is_process_alive``.

    POSIX: ``os.kill(pid, 0)`` -- ``ProcessLookupError`` means the PID is
    gone, ``PermissionError`` means it exists but is foreign to us (alive).
    Non-POSIX: always ``True``; stale detection falls back to the timeout
    check alone (grok's deliberate design for non-Unix platforms).
    """
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Exists but we may not signal it -- treat as alive (grok parity).
            return True
        except OSError:
            return False
        return True
    # Non-POSIX: rely on timeout-based stale detection (grok parity).
    return True


def _now_epoch() -> int:
    """Current UNIX epoch in whole seconds (grok ``as_secs`` granularity)."""
    return int(time.time())
