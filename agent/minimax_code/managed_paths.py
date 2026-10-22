"""Paths for per-run data; IDs must never become arbitrary path fragments."""

from __future__ import annotations

import re
from pathlib import Path

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_DEVICES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


def managed_child(root: Path, identifier: str) -> Path:
    """One validated child of an owned directory, never the directory itself."""
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or identifier.upper() in _DEVICES:
        raise ValueError("Invalid managed directory identifier")
    base = root.absolute()
    target = base / identifier
    if target.resolve() != target:
        raise ValueError("Managed path contains a symbolic link or junction")
    return target


def run_data_path(root: Path, namespace: str, run_id: str, *, receipt: bool = False) -> Path | None:
    if not isinstance(run_id, str) or not _ID.fullmatch(run_id) or run_id.upper() in _DEVICES:
        return None
    if namespace not in {"artifacts", "sandboxes"}:
        raise ValueError("Unknown managed namespace")
    root = root.resolve()
    base = root / ".minimax" / namespace
    target = base / ".collected" / f"{run_id}.json" if receipt else base / run_id
    # Also reject aliases into another run, and junctions/symlinks in parents.
    if target.resolve() != target or not target.is_relative_to(root):
        return None
    return target
