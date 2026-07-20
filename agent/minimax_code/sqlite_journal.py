"""Filesystem-aware SQLite journal-mode selection -- fusion of grok's
``xai-sqlite-journal`` (R197, ``lib.rs`` pure-logic core, crate single round).

``xai-sqlite-journal`` decides WAL vs rollback-journal TRUNCATE based on where
the DB file lives: WAL's mmap'd ``-shm`` SIGBUSes on network filesystems (NFS,
SMB, FUSE ...), so on such mounts the crate switches to TRUNCATE plus a
per-host DB sibling -- so no peer host (including a pre-fix binary that would
flip a shared DB back to WAL) ever shares the file. The crate ships 779 lines
in one ``lib.rs``; this module migrates the **pure-logic core** (the decision,
the env kill-switch parser, the per-host path derivation, and the platform
filesystem classifiers) and records the rusqlite / statfs / libc I/O shell as
YAGNI.

Migrated (this round, pure logic)
---------------------------------

* :data:`BUSY_TIMEOUT` -- 5 s ``busy_timeout`` (the value every grok consumer set).
* :class:`JournalMode` -- ``WAL`` / ``TRUNCATE`` StrEnum; ``str(mode)`` is the
  ``PRAGMA journal_mode`` value (mirrors grok ``as_str``).
* :class:`EnvOverride` + :func:`mode_from_env` -- the
  ``GROK_SQLITE_JOURNAL_MODE`` kill-switch parser (``wal`` | ``truncate``;
  case-insensitive; typos -> ``INVALID``, observed-warned but non-fatal, not
  silently ``UNSET``).
* :func:`for_db_path_inner` -- pure core of grok ``JournalMode::for_db_path``:
  env override wins, else the caller-supplied network flag decides.
* :meth:`JournalMode.effective_db_path` -- grok
  ``JournalMode::effective_db_path``: per-host DB sibling on ``TRUNCATE``
  (``worktrees.db`` -> ``worktrees.h-<host>.db``), idempotent.
* :func:`host_discriminator` -- hostname sanitizer for the sibling filename
  (lowercased ASCII alphanumeric, other bytes -> ``-``, 24-char cap).
* :data:`NETWORK_FS_MAGICS` + :func:`is_network_fs_magic` -- Linux ``statfs``
  ``f_type`` classifier (15 network magics; low-32-bit compare).
* :data:`MNT_LOCAL` + :func:`is_network_fs_mac` + :func:`is_network_fs_name`
  -- macOS ``statfs`` classifier (``MNT_LOCAL`` absence + fstype allowlist).
* :func:`is_windows_unc` -- Windows UNC path string classifier
  (``\\\\server\\share`` / ``\\\\?\\UNC\\...``; not ``\\.\\`` or ``\\\\?\\C:``).

YAGNI / deferred (this round)
-----------------------------

* **rusqlite PRAGMA layer** -- ``JournalMode::open`` / ``open_readonly`` /
  ``apply`` (``Connection::open`` + ``busy_timeout`` + ``pragma_update`` of
  ``journal_mode`` / ``locking_mode`` / ``query_only``). The platform uses
  ``aiosqlite`` and :mod:`minimax_code.storage.db` owns its connection layer;
  the pure *decision* lands here, the PRAGMA application is YAGNI until a
  storage consumer wires it in.
* **Live filesystem probe** -- ``is_network_fs(path)`` (``imp::is_network_fs``
  per platform: ``libc::statfs`` on Linux/macOS, ``GetDriveTypeW`` /
  ``GetVolumePathNameW`` on Windows). The pure classifiers it calls
  (:func:`is_network_fs_magic` / :func:`is_network_fs_mac` /
  :func:`is_windows_unc`) land here; the actual ``statfs`` / ``GetDriveTypeW``
  probe is I/O -- multi-round when a storage consumer needs it.
* **Hostname fetch** -- ``hostname_raw`` (``libc::gethostname`` on Unix,
  ``COMPUTERNAME`` env on Windows). Python has ``socket.gethostname`` /
  ``platform.node``; the sanitizer (:func:`host_discriminator`) is pure and
  takes the resolved hostname as an argument.
* ``for_db_path`` public wrapper -- calls ``is_network_fs`` (I/O) + ``tracing``
  logs; its pure core (:func:`for_db_path_inner`) lands here.

Product-fusion note
-------------------

The platform's SQLite layer (:mod:`minimax_code.storage.db`, ``aiosqlite``)
currently runs on platformdirs-resolved local paths and never sets
``journal_mode``. When a future round wires WAL-vs-TRUNCATE into storage, it
will consume :func:`for_db_path_inner` + the classifiers from here -- this
module is the decision provider, ``aiosqlite`` owns the connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum, StrEnum
from pathlib import PurePath

#: grok ``BUSY_TIMEOUT`` -- wait for peers' locks instead of failing instantly;
#: 5 s matches what every grok consumer historically set.
BUSY_TIMEOUT: timedelta = timedelta(milliseconds=5000)


class JournalMode(StrEnum):
    """Journal mode chosen for a SQLite database based on where it lives.

    (grok ``JournalMode``.) ``str(mode)`` is the ``PRAGMA journal_mode`` value
    (mirrors grok ``as_str``): ``"WAL"`` for write-ahead logging (local
    filesystems), ``"TRUNCATE"`` for a rollback journal truncated at commit
    (network filesystems -- no per-commit create/unlink, no NFS ``.nfsXXXX``
    silly-rename litter).
    """

    WAL = "WAL"
    TRUNCATE = "TRUNCATE"

    def as_str(self) -> str:
        """The ``PRAGMA journal_mode`` value for this mode (grok ``as_str``)."""
        return self.value

    def effective_db_path(
        self,
        db_path: PurePath,
        hostname: str | None,
    ) -> PurePath:
        """The path actually opened for ``db_path`` under this mode.

        (grok ``JournalMode::effective_db_path``.) ``WAL`` (local): unchanged.
        ``TRUNCATE`` (network): a per-host sibling (``worktrees.db`` ->
        ``worktrees.h-<host>.db``) so a live pre-fix binary on a peer host
        cannot flip a *shared* DB back to WAL. Idempotent (an already-suffixed
        path is returned unchanged) so callers may pre-resolve the path for
        sidecar file operations.

        ``hostname`` is the resolved host (the grok ``hostname_raw`` fetch --
        ``libc::gethostname`` / ``COMPUTERNAME`` -- is I/O and is the caller's
        job; pass ``None`` to fall back to ``db_path`` unchanged, still
        ``TRUNCATE``).
        """
        if self is not JournalMode.TRUNCATE:
            return db_path
        host = host_discriminator(hostname)
        if host is None:
            return db_path
        name = db_path.name
        if not name:
            return db_path
        tag = f".h-{host}"
        # Idempotent: pre-resolved paths pass through unchanged.
        if name.endswith(tag) or f"{tag}." in name:
            return db_path
        # Insert before the final extension ("worktrees.db" -> "worktrees.h-x.db"),
        # else append; rsplit keeps dotfile names like ".hidden" on the append arm.
        parts = name.rsplit(".", 1)
        if len(parts) == 2 and parts[0] != "":
            new_name = f"{parts[0]}{tag}.{parts[1]}"
        else:
            new_name = f"{name}{tag}"
        return db_path.with_name(new_name)


class _EnvOverrideKind(Enum):
    UNSET = "unset"
    INVALID = "invalid"
    MODE = "mode"


@dataclass(frozen=True, slots=True)
class EnvOverride:
    """Parse result of the ``GROK_SQLITE_JOURNAL_MODE`` kill-switch.

    (grok ``EnvOverride``.) ``MODE`` carries the resolved :class:`JournalMode`;
    ``UNSET`` / ``INVALID`` do not -- a typo is observable (warned) but
    non-fatal, not silently treated as unset.
    """

    kind: _EnvOverrideKind
    mode: JournalMode | None = None

    @classmethod
    def unset(cls) -> EnvOverride:
        return cls(kind=_EnvOverrideKind.UNSET)

    @classmethod
    def invalid(cls) -> EnvOverride:
        return cls(kind=_EnvOverrideKind.INVALID)

    @classmethod
    def of_mode(cls, mode: JournalMode) -> EnvOverride:
        return cls(kind=_EnvOverrideKind.MODE, mode=mode)


def mode_from_env(value: str | None) -> EnvOverride:
    """Parse the ``GROK_SQLITE_JOURNAL_MODE`` kill-switch (grok ``mode_from_env``).

    Pure (mirrors grok's own "pure for testability" note). ``None`` and the
    empty string count as unset (a deliberate blank, not a typo); ``wal`` /
    ``truncate`` are case-insensitive; anything else is :meth:`EnvOverride.invalid`.
    """
    if value is None or value == "":
        return EnvOverride.unset()
    lowered = value.lower()
    if lowered == "wal":
        return EnvOverride.of_mode(JournalMode.WAL)
    if lowered == "truncate":
        return EnvOverride.of_mode(JournalMode.TRUNCATE)
    return EnvOverride.invalid()


def for_db_path_inner(env_value: str | None, is_network: bool) -> JournalMode:
    """Pure core of grok ``JournalMode::for_db_path``.

    Env override wins (any explicit ``wal`` / ``truncate``); otherwise the
    caller-supplied ``is_network`` flag decides (``TRUNCATE`` on network,
    ``WAL`` on local). The invalid-env case falls through to detection (grok
    warns via ``tracing``; that log is I/O and omitted from this pure core).

    ``env_value`` is the already-read ``GROK_SQLITE_JOURNAL_MODE`` value
    (``None`` = unset); ``is_network`` is the already-probed filesystem flag.
    The ``std::env::var`` read and the ``statfs`` / ``GetDriveTypeW`` probe are
    I/O -- the caller resolves them and passes the results in.
    """
    override = mode_from_env(env_value)
    if override.kind is _EnvOverrideKind.MODE and override.mode is not None:
        return override.mode
    return JournalMode.TRUNCATE if is_network else JournalMode.WAL


#: Linux ``statfs(2)`` magics that mark a network/remote filesystem (grok
#: ``is_network_fs_magic`` constants, from ``include/uapi/linux/magic.h``;
#: Lustre from its module sources, WekaFS confirmed empirically). Only the low
#: 32 bits are compared (32-bit kernels sign-extend magics with the high bit set).
NETWORK_FS_MAGICS: frozenset[int] = frozenset(
    {
        0x6969,       # NFS
        0x517B,       # SMB
        0xFE534D42,   # SMB2
        0xFF534D42,   # CIFS
        0x01021997,   # 9p (v9fs)
        0x73757245,   # CODA
        0x5346414F,   # AFS
        0x6B414653,   # kAFS (in-kernel client)
        0x00C36400,   # CEPH
        0x0BD00BD0,   # Lustre
        0x01161970,   # GFS2
        0x47504653,   # GPFS (Spectrum Scale)
        0x7461636F,   # OCFS2
        0x18031977,   # WekaFS (parallel FS; not in linux/magic.h)
        0x65735546,   # FUSE (sshfs/s3fs/gluster -- treated as network)
    }
)


def is_network_fs_magic(f_type: int) -> bool:
    """Classify a Linux ``statfs(2)`` ``f_type`` as network/remote (grok).

    Only the low 32 bits are compared: ``f_type`` is a signed word whose width
    varies by architecture, so 32-bit kernels sign-extend magics with the high
    bit set (e.g. CIFS ``0xFF534D42`` -> ``0xFFFFFFFFFF534D42``).
    """
    return (f_type & 0xFFFFFFFF) in NETWORK_FS_MAGICS


#: macOS ``libc::MNT_LOCAL`` flag (mirrors the libc constant; ``statfs.f_flags``
#: is ``u32``). Absence of this bit is the authoritative remote signal.
MNT_LOCAL: int = 0x00001000

#: macOS ``statfs(2)`` ``f_fstypename`` values treated as network/remote (grok
#: ``is_network_fs_name``). macfuse/osxfuse mirror Linux's FUSE-is-network
#: stance (sshfs etc.); fuse-t needs no entry (its mounts surface as ``nfs``).
_MAC_NETWORK_FS_NAMES: frozenset[str] = frozenset(
    {"nfs", "smbfs", "cifs", "afpfs", "webdav", "macfuse", "osxfuse"}
)


def is_network_fs_name(fstype: str) -> bool:
    """Classify a macOS ``statfs(2)`` ``f_fstypename`` as network/remote (grok)."""
    return fstype.lower() in _MAC_NETWORK_FS_NAMES


def is_network_fs_mac(f_flags: int, fstype: str) -> bool:
    """Classify a macOS ``statfs(2)`` result as network/remote (grok).

    Absence of :data:`MNT_LOCAL` in ``f_flags`` is the authoritative remote
    signal (covers unknown/future remote fs types); the ``f_fstypename``
    allowlist (:func:`is_network_fs_name`) is a conservative extra trigger for
    remote-backed mounts that still set ``MNT_LOCAL`` (e.g. FUSE bridges).
    """
    return (f_flags & MNT_LOCAL) == 0 or is_network_fs_name(fstype)


def is_windows_unc(path: str) -> bool:
    """Classify a Windows path string as UNC / network (grok ``is_windows_unc``).

    ``\\\\server\\share`` or ``\\\\?\\UNC\\server\\share`` are network; the
    ``\\\\.\\`` device and ``\\\\?\\C:\\`` verbatim-local forms are not. Mapped
    drives (``Z:`` on SMB) are caught by the ``GetDriveTypeW`` probe instead
    (I/O, YAGNI here). Pure for testability.
    """
    if not path.startswith("\\\\"):
        return False
    rest = path[2:]
    if rest.startswith("?\\"):
        verbatim = rest[2:]
        return verbatim[:4].lower() == "unc\\"
    return not rest.startswith(".\\")


def host_discriminator(hostname: str | None) -> str | None:
    """Sanitize a hostname into a per-host DB-filename discriminator.

    (grok ``host_discriminator``.) Lowercased ASCII alphanumeric; every other
    byte -> ``-``; capped at 24 chars; leading/trailing ``-`` stripped. Returns
    ``None`` when the result is empty (no usable hostname) -- callers then keep
    the shared path (still ``TRUNCATE``). Sanitization collisions across hosts
    only degrade to plain shared-TRUNCATE behavior, never to WAL.

    ``hostname`` is the already-fetched host (grok ``hostname_raw`` --
    ``libc::gethostname`` / ``COMPUTERNAME`` -- is I/O and is the caller's job).
    """
    if hostname is None:
        return None
    sanitized = "".join(
        char.lower() if (char.isascii() and char.isalnum()) else "-"
        for char in hostname.strip()
    )
    sanitized = sanitized[:24].strip("-")
    return sanitized or None


__all__ = [
    "BUSY_TIMEOUT",
    "EnvOverride",
    "JournalMode",
    "MNT_LOCAL",
    "NETWORK_FS_MAGICS",
    "for_db_path_inner",
    "host_discriminator",
    "is_network_fs_mac",
    "is_network_fs_magic",
    "is_network_fs_name",
    "is_windows_unc",
    "mode_from_env",
]
