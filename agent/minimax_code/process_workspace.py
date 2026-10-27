"""L1 source/copy/mount admission, independent of permissive inner policies."""
from __future__ import annotations

from pathlib import Path

from .process_types import ProcessBoundaryError


def _ancestors(path):
    return (path, *path.parents)


def _identity(path):
    try:
        info = path.stat()
        return info.st_dev, info.st_ino
    except FileNotFoundError:
        return None


def validate_process_workspace(root: Path, *, data_dir: Path, protected_roots=(), included=None):
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ProcessBoundaryError("Restricted workspace must be a directory")
    home = Path.home().resolve(strict=True)
    if root == home or home.is_relative_to(root) or _identity(root) in {
            _identity(path) for path in _ancestors(home)}:
        raise ProcessBoundaryError("Restricted workspace contains HOME")
    excluded = {Path(data_dir), *(Path(p) for p in protected_roots),
        *(home / name for name in (".ssh", ".aws", ".azure", ".kube", ".config", ".docker",
            ".gnupg", ".local/share/containers", ".git-credentials", ".netrc", ".npmrc", ".pypirc"))}
    root_identity = _identity(root)
    ancestors = {_identity(path) for path in _ancestors(root)}
    for path in excluded:
        path = path.resolve()
        identity = _identity(path)
        if (root == path or root.is_relative_to(path) or path.is_relative_to(root)
                or (identity is not None and identity in ancestors)
                or root_identity in {_identity(p) for p in _ancestors(path)}):
            raise ProcessBoundaryError("Restricted workspace overlaps a protected service or credentials tree")
    from .process_mounts import read_mount_table

    mounts = read_mount_table()
    if mounts is not None:
        mounts.reject_sensitive_aliases(root, home=home,
                                       protected_roots=tuple(path.resolve() for path in excluded))
        if included is not None:
            mounts.reject_included_mounts(root, included)
    if any(char in str(root) for char in (",", "\n", "\r")):
        raise ProcessBoundaryError("Restricted workspace cannot use fixed mount syntax")
    git = root / ".git"
    if git.is_symlink() or (git.exists() and not git.is_dir()):
        raise ProcessBoundaryError("Restricted workspace cannot use an external Git worktree")
    return root
