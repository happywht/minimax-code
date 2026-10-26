"""Process ownership for one trusted, local service data directory.

The persistent lock file is never replaced or removed. PID records are not a
lock, and this module never steals ownership based on a stale-looking PID.
Raw non-inheritable descriptors intentionally have no destructor/atexit unlock:
an unconfirmed shutdown retains ownership until the OS terminates the process.
"""
from __future__ import annotations

import errno
import os
import stat
import threading
from pathlib import Path
from typing import Any

LOCK_NAME = ".minimax_code_service.lock"


class ServiceLeaseError(RuntimeError):
    """Service ownership could not be established or verified."""


class ServiceAlreadyRunning(ServiceLeaseError):
    """Another owner still holds this directory's operating-system lock."""


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _canonical(directory: str | Path) -> Path:
    return Path(directory).expanduser().resolve()


def _windows_api() -> tuple[Any, Any]:
    import ctypes
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.LockFileEx.restype = wintypes.BOOL
    kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.UnlockFileEx.restype = wintypes.BOOL
    return kernel, Overlapped()


_guard = threading.RLock()
_leases: dict[Path, ServiceLease] = {}


class ServiceLease:
    """Held raw FD; close explicitly only after the owner's work is quiescent."""

    def __init__(self, directory: Path) -> None:
        self.data_dir = directory
        self.path = directory / LOCK_NAME
        self.owner_pid = os.getpid()
        self.directory_identity = _identity(directory.stat())
        self._fd: int | None = None
        self._windows: tuple[Any, Any] | None = None
        self._lock_identity: tuple[int, int] | None = None
        descriptor = None
        try:
            if self.path.is_symlink():
                raise ServiceLeaseError("The service lock file must not be a symbolic link")
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            descriptor = os.open(self.path, flags, 0o600)
            os.set_inheritable(descriptor, False)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ServiceLeaseError("The service lock must be a regular file")
            self._lock_identity = _identity(info)
            if _identity(self.path.lstat()) != self._lock_identity:
                raise ServiceLeaseError("Service lock identity changed during acquisition")
            if os.name == "nt":
                import ctypes
                import msvcrt

                kernel, overlap = _windows_api()
                if not kernel.LockFileEx(msvcrt.get_osfhandle(descriptor), 3, 0, 1, 0,
                                         ctypes.byref(overlap)):
                    code = ctypes.get_last_error()
                    if code == 33:  # ERROR_LOCK_VIOLATION
                        raise ServiceAlreadyRunning("This service data directory is already in use")
                    raise ctypes.WinError(code)
                self._windows = kernel, overlap
            elif os.name == "posix":
                import fcntl

                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno in {errno.EACCES, errno.EAGAIN}:
                        raise ServiceAlreadyRunning(
                            "This service data directory is already in use"
                        ) from exc
                    raise
            else:
                raise ServiceLeaseError("Service directory locking is unsupported on this platform")
            self._fd = descriptor
            descriptor = None
            self.assert_owned(directory)
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            raise

    def assert_owned(self, expected_data_dir: str | Path, db_path: str | Path | None = None) -> None:
        if self.owner_pid != os.getpid() or self._fd is None:
            raise ServiceLeaseError("The current process does not own an active service lease")
        try:
            expected = _canonical(expected_data_dir)
            if expected != self.data_dir or _identity(expected.stat()) != self.directory_identity:
                raise ServiceLeaseError("Service data directory identity changed")
            if (_identity(os.fstat(self._fd)) != self._lock_identity
                    or _identity(self.path.lstat()) != self._lock_identity
                    or self.path.is_symlink()):
                raise ServiceLeaseError("Service lock file identity changed")
            if db_path is not None and _canonical(db_path).parent != self.data_dir:
                raise ServiceLeaseError("Database is outside the owned service data directory")
        except OSError as exc:
            raise ServiceLeaseError("Service ownership cannot be verified") from exc

    def close(self) -> None:
        with _guard:
            descriptor, self._fd = self._fd, None
            if descriptor is None:
                return
            try:
                if self.owner_pid == os.getpid():
                    if os.name == "nt":
                        import ctypes
                        import msvcrt

                        assert self._windows is not None
                        kernel, overlap = self._windows
                        if not kernel.UnlockFileEx(msvcrt.get_osfhandle(descriptor), 0, 1, 0,
                                                   ctypes.byref(overlap)):
                            raise ctypes.WinError(ctypes.get_last_error())
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                # A fork child must only close its duplicate, never LOCK_UN.
            finally:
                os.close(descriptor)
                if _leases.get(self.data_dir) is self:
                    _leases.pop(self.data_dir)

    def __enter__(self) -> ServiceLease:
        self.assert_owned(self.data_dir)
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def acquire_service_lease(data_dir: str | Path) -> ServiceLease:
    """Fail immediately on contention, before any logging/recovery mutation."""
    try:
        directory = _canonical(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        with _guard:
            if directory in _leases:
                raise ServiceAlreadyRunning("This process already owns the service data directory")
            lease = ServiceLease(directory)
            _leases[directory] = lease
            return lease
    except ServiceLeaseError:
        raise
    except OSError as exc:
        raise ServiceLeaseError("Cannot lock the service data directory") from exc


def assert_owned(data_dir: str | Path, db_path: str | Path | None = None) -> ServiceLease:
    """Assert existing ownership; never acquire a lease implicitly."""
    with _guard:
        lease = _leases.get(_canonical(data_dir))
        if lease is None:
            raise ServiceLeaseError("Acquire a service lease before initializing this runtime")
        lease.assert_owned(data_dir, db_path)
        return lease


def _after_fork_child() -> None:
    global _guard, _leases
    for lease in _leases.values():
        if lease._fd is not None:
            os.close(lease._fd)
            lease._fd = None
    _leases = {}
    _guard = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_child)
