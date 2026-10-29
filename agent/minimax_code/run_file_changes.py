"""Opt-in, bounded file-name comparison for one service-owned task.

This is observational evidence, not authorship or a restorable checkpoint.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path

MAX_FILES = 1000
MAX_BYTES = 16 * 1024 * 1024
TIMEOUT = 2.0
EXCLUDED = {".git", ".minimax"}


def _hash_files(root: Path, names: list[str], deadline: float) -> dict[str, str]:
    from .text_artifacts import _identity, _regular, _safe_open

    hashes: dict[str, str] = {}
    total = 0
    for name in names:
        if time.monotonic() >= deadline:
            raise ValueError("time limit exceeded")
        relative = Path(name)
        if (relative.is_absolute() or ".." in relative.parts or not relative.parts
                or any(part.lower() in EXCLUDED for part in relative.parts)):
            if any(part.lower() in EXCLUDED for part in relative.parts):
                continue
            raise ValueError("invalid Git path")
        path = root / relative
        try:
            with _safe_open(path) as fd:
                info = os.fstat(fd)
                _regular(info)
                if total + info.st_size > MAX_BYTES:
                    raise ValueError("byte limit exceeded")
                digest = hashlib.sha256()
                while chunk := os.read(fd, 65536):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("byte limit exceeded")
                    if time.monotonic() >= deadline:
                        raise ValueError("time limit exceeded")
                    digest.update(chunk)
                if _identity(info) != _identity(os.fstat(fd)):
                    raise ValueError("file changed while reading")
                hashes[relative.as_posix()] = digest.hexdigest()
        except FileNotFoundError:
            # Tracked deletions are absent from this sampling point.
            continue
    return hashes


async def capture(job):
    """Return a complete bounded sample, or an explicit failure reason."""
    from .execution import ExecutionPolicy, execution_scope
    from .ipc.handlers_git import _run_git
    from .ipc.host_session_gate import require_host_session
    from .workspace_ctx import require_directory, resolve_root_for_session

    try:
        await require_host_session(job.session_id)
        session = await job.dao._db.fetchone(
            "SELECT project_id FROM sessions WHERE id=?", (job.session_id,))
        root = require_directory(await resolve_root_for_session(job.session_id))
        if not session or session["project_id"] != job.project_id or str(root) != job.workspace:
            raise ValueError("workspace binding changed")
        identity = root.stat()
        with execution_scope(ExecutionPolicy(workspace=root)):
            output = await asyncio.wait_for(_run_git(
                ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "."],
                cwd=root), timeout=TIMEOUT)
        names = sorted(set(filter(None, output.split("\0"))))
        if len(names) > MAX_FILES or any("\ufffd" in name for name in names):
            raise ValueError("file count or filename limit exceeded")
        deadline = time.monotonic() + TIMEOUT
        hashes = await asyncio.wait_for(asyncio.to_thread(_hash_files, root, names, deadline), TIMEOUT)
        current = require_directory(await resolve_root_for_session(job.session_id))
        current_session = await job.dao._db.fetchone(
            "SELECT project_id FROM sessions WHERE id=?", (job.session_id,))
        info = current.stat()
        if (current != root or not current_session or current_session["project_id"] != job.project_id
                or (info.st_dev, info.st_ino) != (identity.st_dev, identity.st_ino)):
            raise ValueError("workspace binding changed")
        return {"root": str(root), "identity": (info.st_dev, info.st_ino), "files": hashes}
    except Exception:
        return None


async def save_comparison(job, before):
    """Best effort: never turn a successful task into a failure."""
    try:
        after = await capture(job) if before is not None and not job.cancel_requested else None
        if before is None or after is None or (before["root"], before["identity"]) != (after["root"], after["identity"]):
            payload = {"source": "run_file_changes", "available": False}
        else:
            old, new = before["files"], after["files"]
            payload = {"source": "run_file_changes", "available": True,
                       "added": sorted(new.keys() - old.keys()),
                       "deleted": sorted(old.keys() - new.keys()),
                       "modified": sorted(name for name in old.keys() & new.keys() if old[name] != new[name])}
        await job.dao.create_step(run_id=job.run_id, session_id=job.session_id, kind="observation",
                                  status="completed", title="File changes during task",
                                  summary="Two bounded workspace samples", payload=payload)
    except Exception:
        # The task outcome is independent of optional observation.
        return
