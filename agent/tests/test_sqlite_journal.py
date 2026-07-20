"""Tests for sqlite_journal (R197, ``xai-sqlite-journal`` ``lib.rs`` pure core).

Covers the migrated decision core: :data:`BUSY_TIMEOUT`, :class:`JournalMode`
(+ ``as_str`` + ``effective_db_path``), :class:`EnvOverride` +
:func:`mode_from_env`, :func:`for_db_path_inner`, the Linux/macOS/Windows
filesystem classifiers, and :func:`host_discriminator`. The I/O shell
(rusqlite PRAGMA ``open``/``apply``, live ``statfs``/``GetDriveTypeW`` probe,
hostname fetch) is YAGNI -- the pure decision + classifiers land here.

Mirrors grok's own pure-logic tests: ``network_magics_classify_as_network`` /
``local_magics_classify_as_local`` / ``sign_extended_magic_still_matches`` /
``env_override_parses`` / ``mac_classifier_*`` / ``windows_unc_classifies`` /
``effective_db_path_is_per_host_only_in_truncate_mode``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import timedelta
from pathlib import PurePath

import pytest

from minimax_code.sqlite_journal import (
    BUSY_TIMEOUT,
    MNT_LOCAL,
    NETWORK_FS_MAGICS,
    EnvOverride,
    JournalMode,
    for_db_path_inner,
    host_discriminator,
    is_network_fs_mac,
    is_network_fs_magic,
    is_network_fs_name,
    is_windows_unc,
    mode_from_env,
)

# ---------------------------------------------------------------------------
# BUSY_TIMEOUT
# ---------------------------------------------------------------------------


def test_busy_timeout_is_5_seconds() -> None:
    """grok ``BUSY_TIMEOUT`` = 5 s (every consumer set this value)."""
    assert BUSY_TIMEOUT == timedelta(milliseconds=5000)
    assert BUSY_TIMEOUT == timedelta(seconds=5)


# ---------------------------------------------------------------------------
# JournalMode enum + as_str
# ---------------------------------------------------------------------------


def test_journal_mode_has_two_variants() -> None:
    assert {mode.value for mode in JournalMode} == {"WAL", "TRUNCATE"}


@pytest.mark.parametrize(
    "mode, pragma",
    [
        (JournalMode.WAL, "WAL"),
        (JournalMode.TRUNCATE, "TRUNCATE"),
    ],
)
def test_journal_mode_as_str_returns_pragma_value(mode: JournalMode, pragma: str) -> None:
    """``as_str`` is the ``PRAGMA journal_mode`` value; ``str(mode)`` matches."""
    assert mode.as_str() == pragma
    assert str(mode) == pragma


# ---------------------------------------------------------------------------
# mode_from_env + EnvOverride
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, kind, mode",
    [
        (None, "UNSET", None),
        ("", "UNSET", None),
        ("wal", "MODE", JournalMode.WAL),
        ("WAL", "MODE", JournalMode.WAL),
        ("WaL", "MODE", JournalMode.WAL),
        ("truncate", "MODE", JournalMode.TRUNCATE),
        ("TRUNCATE", "MODE", JournalMode.TRUNCATE),
        ("TrunCATE", "MODE", JournalMode.TRUNCATE),
        ("delete", "INVALID", None),
        ("wall", "INVALID", None),
        ("bogus", "INVALID", None),
        ("wal ", "INVALID", None),  # trailing whitespace is a typo, not silently trimmed
    ],
)
def test_mode_from_env_matrix(value: str | None, kind: str, mode: JournalMode | None) -> None:
    """grok ``mode_from_env``: case-insensitive wal/truncate; blank -> UNSET;
    typo -> INVALID (warned but non-fatal, not silently unset)."""
    override = mode_from_env(value)
    assert override.kind.name == kind
    assert override.mode == mode


def test_env_override_factories_shape() -> None:
    assert EnvOverride.unset().kind.name == "UNSET"
    assert EnvOverride.unset().mode is None
    assert EnvOverride.invalid().kind.name == "INVALID"
    assert EnvOverride.invalid().mode is None
    forced = EnvOverride.of_mode(JournalMode.TRUNCATE)
    assert forced.kind.name == "MODE"
    assert forced.mode is JournalMode.TRUNCATE


def test_env_override_is_frozen() -> None:
    """``@dataclass(frozen=True)`` -> mutation raises (mirrors grok's immutable enum)."""
    override = EnvOverride.of_mode(JournalMode.WAL)
    with pytest.raises(FrozenInstanceError):
        override.mode = JournalMode.TRUNCATE  # type: ignore[misc]


# ---------------------------------------------------------------------------
# for_db_path_inner (pure core of grok JournalMode::for_db_path)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_value, is_network, expected",
    [
        # Env override wins regardless of the network flag.
        ("wal", True, JournalMode.WAL),
        ("wal", False, JournalMode.WAL),
        ("truncate", True, JournalMode.TRUNCATE),
        ("truncate", False, JournalMode.TRUNCATE),
        # Unset -> detection.
        (None, True, JournalMode.TRUNCATE),
        (None, False, JournalMode.WAL),
        ("", True, JournalMode.TRUNCATE),
        ("", False, JournalMode.WAL),
        # Invalid -> falls through to detection (warned, not fatal).
        ("bogus", True, JournalMode.TRUNCATE),
        ("bogus", False, JournalMode.WAL),
    ],
)
def test_for_db_path_inner_matrix(
    env_value: str | None, is_network: bool, expected: JournalMode
) -> None:
    assert for_db_path_inner(env_value, is_network) is expected


# ---------------------------------------------------------------------------
# is_network_fs_magic (Linux statfs f_type classifier)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "magic",
    [
        0x6969,        # NFS
        0x517B,        # SMB
        0xFE534D42,    # SMB2
        0xFF534D42,    # CIFS
        0x01021997,    # 9p (v9fs)
        0x73757245,    # CODA
        0x5346414F,    # AFS
        0x6B414653,    # kAFS
        0x00C36400,    # CEPH
        0x0BD00BD0,    # Lustre
        0x01161970,    # GFS2
        0x47504653,    # GPFS
        0x7461636F,    # OCFS2
        0x18031977,    # WekaFS
        0x65735546,    # FUSE
    ],
)
def test_network_magics_classify_as_network(magic: int) -> None:
    assert is_network_fs_magic(magic)


@pytest.mark.parametrize(
    "magic",
    [
        0xEF53,        # ext2/3/4
        0x01021994,    # tmpfs
        0x9123683E,    # btrfs
        0x58465342,    # XFS
        0x794C7630,    # overlayfs
        0x2FC12FC1,    # zfs
        0x0,           # anon (zero)
    ],
)
def test_local_magics_classify_as_local(magic: int) -> None:
    assert not is_network_fs_magic(magic)


def test_sign_extended_magic_still_matches() -> None:
    """A 32-bit kernel reports CIFS (``0xFF534D42``) as a negative ``f_type``
    (sign-extended to ``0xFFFFFFFFFF534D42``); the low-32-bit compare must
    still match."""
    assert is_network_fs_magic(0xFFFFFFFFFF534D42)


def test_network_fs_magics_has_fifteen_entries() -> None:
    """NFS/SMB/SMB2/CIFS/9p/CODA/AFS/kAFS/CEPH/Lustre/GFS2/GPFS/OCFS2/WekaFS/FUSE."""
    assert len(NETWORK_FS_MAGICS) == 15


# ---------------------------------------------------------------------------
# MNT_LOCAL + macOS classifiers
# ---------------------------------------------------------------------------


def test_mnt_local_value() -> None:
    """Mirrors ``libc::MNT_LOCAL`` on macOS (statfs.f_flags is u32)."""
    assert MNT_LOCAL == 0x00001000


@pytest.mark.parametrize(
    "fstype",
    ["nfs", "smbfs", "cifs", "afpfs", "webdav", "NFS", "Nfs", "macfuse", "osxfuse"],
)
def test_network_fs_names_classify(fstype: str) -> None:
    assert is_network_fs_name(fstype)


@pytest.mark.parametrize("fstype", ["apfs", "hfs", "tmpfs", "devfs", ""])
def test_local_fs_names_do_not_classify(fstype: str) -> None:
    assert not is_network_fs_name(fstype)


@pytest.mark.parametrize(
    "f_flags, fstype, expected",
    [
        # No MNT_LOCAL -> network regardless of name (covers unknown future remote types).
        (0, "somefutfs", True),
        (0, "macfuse_sshfs", True),
        # Plain local APFS with MNT_LOCAL -> local.
        (MNT_LOCAL, "apfs", False),
        # Allowlisted name wins even when the mount claims MNT_LOCAL.
        (MNT_LOCAL, "smbfs", True),
        (MNT_LOCAL, "macfuse", True),
    ],
)
def test_is_network_fs_mac_matrix(f_flags: int, fstype: str, expected: bool) -> None:
    assert is_network_fs_mac(f_flags, fstype) is expected


# ---------------------------------------------------------------------------
# is_windows_unc (Windows UNC path classifier)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, expected",
    [
        (r"\\server\share\grok", True),
        (r"\\?\UNC\server\share\grok", True),
        (r"\\?\unc\server\share", True),
        (r"\\?\C:\Users\x", False),
        (r"\\.\pipe\grok", False),
        (r"C:\Users\x", False),
        ("/home/x", False),
    ],
)
def test_is_windows_unc_matrix(path: str, expected: bool) -> None:
    assert is_windows_unc(path) is expected


# ---------------------------------------------------------------------------
# host_discriminator (hostname sanitizer)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostname, expected",
    [
        ("ci-host-01", "ci-host-01"),
        ("  ci-host-01  ", "ci-host-01"),          # outer whitespace trimmed
        ("MY-Host", "my-host"),                      # lowercased
        ("host.example.com", "host-example-com"),    # dots -> '-'
        ("host_01", "host-01"),                       # underscore -> '-'
        ("a" * 30, "a" * 24),                         # capped at 24 chars
        ("---", None),                                # all-dashes -> empty -> None
        ("", None),                                   # empty -> None
        ("...", None),                                # dots collapse to dashes -> None
    ],
)
def test_host_discriminator_matrix(hostname: str, expected: str | None) -> None:
    assert host_discriminator(hostname) == expected


def test_host_discriminator_none_returns_none() -> None:
    assert host_discriminator(None) is None


def test_host_discriminator_strips_leading_trailing_dashes() -> None:
    """A leading/trailing non-alnum byte becomes '-' and is then stripped."""
    assert host_discriminator("!host!") == "host"
    assert host_discriminator("-host-") == "host"


# ---------------------------------------------------------------------------
# effective_db_path (JournalMode method)
# ---------------------------------------------------------------------------


def test_effective_db_path_wal_is_unchanged() -> None:
    """Local mode never rewrites the path."""
    p = PurePath("/tmp/dir/worktrees.db")
    assert JournalMode.WAL.effective_db_path(p, "ci-host") == p


def test_effective_db_path_truncate_inserts_per_host_before_extension() -> None:
    p = PurePath("/tmp/dir/worktrees.db")
    out = JournalMode.TRUNCATE.effective_db_path(p, "ci-host-01")
    assert out == PurePath("/tmp/dir/worktrees.h-ci-host-01.db")
    assert out.parent == p.parent


def test_effective_db_path_truncate_is_idempotent() -> None:
    """Pre-resolved paths pass through unchanged (for sidecar file operations)."""
    p = PurePath("/tmp/dir/worktrees.h-x.db")
    assert JournalMode.TRUNCATE.effective_db_path(p, "x") == p


def test_effective_db_path_truncate_bare_name_appends() -> None:
    """Extension-less names get the suffix appended, not inserted mid-name."""
    out = JournalMode.TRUNCATE.effective_db_path(PurePath("/tmp/dir/state"), "x")
    assert out == PurePath("/tmp/dir/state.h-x")


def test_effective_db_path_truncate_dotfile_appends() -> None:
    """Dotfile names (empty stem) stay on the append arm (``rsplit_once`` guard)."""
    out = JournalMode.TRUNCATE.effective_db_path(PurePath("/tmp/dir/.hidden"), "x")
    assert out == PurePath("/tmp/dir/.hidden.h-x")


def test_effective_db_path_truncate_multi_dot_preserves_inner() -> None:
    """Only the final extension splits; inner dots stay in the stem."""
    out = JournalMode.TRUNCATE.effective_db_path(PurePath("/d/a.b.c"), "x")
    assert out == PurePath("/d/a.b.h-x.c")


def test_effective_db_path_truncate_no_hostname_unchanged() -> None:
    """``host_discriminator`` returns None -> fall back to the shared path (still TRUNCATE)."""
    p = PurePath("/tmp/dir/worktrees.db")
    assert JournalMode.TRUNCATE.effective_db_path(p, None) == p
    assert JournalMode.TRUNCATE.effective_db_path(p, "---") == p


def test_effective_db_path_truncate_relative_path() -> None:
    """Relative paths are rewritten in place (parent preserved)."""
    out = JournalMode.TRUNCATE.effective_db_path(PurePath("worktrees.db"), "x")
    assert out == PurePath("worktrees.h-x.db")
