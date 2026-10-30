"""Owned child working copies and explicit, conflict-checked collection.

These directories isolate normal relative file/process writes, not arbitrary
host processes. A worktree is not an operating-system sandbox. Failed runs and
collected copies are retained for review; removal belongs to workspace cleanup.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..execution import (
    ExecutionPolicy,
    capture_output,
    execution_scope,
    require_write_access,
    spawn_exec,
)
from ..file_mutations import atomic_copy_file, atomic_write_text
from ..managed_paths import managed_child
from ..process_types import ProcessBoundaryError
from ..workspace_ctx import (
    require_directory,
    reset_current_root,
    reset_current_run_id,
    set_current_root,
    set_current_run_id,
)

_artifact_root: ContextVar[Path | None] = ContextVar("child_artifact_root", default=None)
_active_child: ContextVar[str | None] = ContextVar("active_child_workspace", default=None)
_EXCLUDED = {".git", ".minimax", "node_modules", ".venv", "venv", "__pycache__",
             ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build", ".ssh"}
MAX_FILES = 10000
MAX_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024


def artifact_root(run_id: str) -> Path | None:
    # Only the owning run reports into its parent. Reports from nested
    # delegates remain in the current workspace where their coordinator reads.
    return _artifact_root.get() if _active_child.get() == run_id else None


def active_child() -> str | None:
    return _active_child.get()


def _path(root: Path, relative: str) -> Path:
    raw = root / relative
    if (Path(relative).is_absolute() or ".." in Path(relative).parts
            or raw.resolve() != raw or not raw.is_relative_to(root)):
        raise ValueError(f"Unsafe child workspace path: {relative!r}")
    return raw


def _included(relative: str) -> bool:
    parts = Path(relative).parts
    return (not any(p.casefold() in _EXCLUDED for p in parts)
            and not any(p.casefold().startswith(".env") for p in parts)
            and Path(relative).suffix.casefold() not in {".pem", ".key"})


def _digest(path: Path, *, max_bytes: int | None = None) -> str | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Expected a regular file: {path}")
    limit = MAX_FILE_BYTES if max_bytes is None else max_bytes
    if path.stat().st_size > limit:
        raise ValueError(f"Child workspace file exceeds {limit} bytes: {path}")
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(min(65536, limit - total + 1)):
            total += len(chunk)
            if total > limit:
                raise ValueError(f"Child workspace file exceeds {limit} bytes: {path}")
            digest.update(chunk)
    return digest.hexdigest()


def _raise_walk_error(error: OSError) -> None:
    raise error


def _scan(root: Path, *, restricted=False) -> dict[str, str]:
    root = require_directory(root)
    if restricted:
        _validate_restricted_mounts(root)
    result: dict[str, str] = {}
    total = 0
    device = root.stat().st_dev
    for directory, dirs, files in os.walk(root, onerror=_raise_walk_error):
        base = Path(directory)
        dirs[:] = sorted(d for d in dirs if _included((base / d).relative_to(root).as_posix()))
        for name in dirs:
            target = _path(root, (base / name).relative_to(root).as_posix())
            if restricted:
                info = target.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_dev != device:
                    raise ValueError("Restricted copy cannot traverse aliases or mounted directories")
        for name in sorted(files):
            relative = (base / name).relative_to(root).as_posix()
            if not _included(relative):
                continue
            target = _path(root, relative)
            if restricted:
                info = target.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_dev != device:
                    raise ValueError("Restricted copy requires ordinary files without hardlinks")
            total += target.stat().st_size
            if len(result) >= MAX_FILES or total > MAX_BYTES:
                raise ValueError("Child workspace snapshot exceeds its file/byte limit")
            digest = _digest(target)
            if digest is not None:
                result[relative] = digest
    return result


def _validate_restricted_mounts(root: Path) -> None:
    from ..app import get_execution_profiles
    from ..process_workspace import validate_process_workspace

    catalog = get_execution_profiles()
    if catalog is None or catalog.supervisor is None:
        raise ValueError("Restricted workspace service is unavailable")
    catalog.assert_extension_boundary()
    validate_process_workspace(root, data_dir=catalog.data_dir,
        protected_roots=catalog.supervisor._protected_roots, included=_included)


async def _git(root: Path, *args: str) -> str:
    require_write_access()
    with execution_scope(ExecutionPolicy(workspace=root)):
        process = await spawn_exec(
            "git", "--no-pager", "-c", "core.hooksPath=", "-c", "core.fsmonitor=false",
            *args, cwd=root, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await capture_output(process, timeout=60)
    if process.returncode:
        raise ValueError(f"Child workspace Git operation failed: {stderr.decode(errors='replace')[:1000]}")
    return stdout.decode("utf-8", errors="strict")


@dataclass
class ChildWorkspace:
    parent: Path
    run_id: str
    base: Path
    record: dict[str, Any]

    @property
    def root(self) -> Path:
        return _path(self.base, "workspace")

    @property
    def manifest(self) -> Path:
        return _path(self.base, "manifest.json")

    def save(self) -> None:
        atomic_write_text(self.manifest, json.dumps(self.record, ensure_ascii=False, indent=2),
                          baseline_path=self.manifest, baseline_sha256=_digest(self.manifest, max_bytes=8 * 1024 * 1024))

    def summary(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "path": str(self.root), "kind": self.record["kind"],
                "state": self.record["state"], "files_written": self.record.get("changes", []),
                "omitted": self.record.get("omitted", []), "collection_required": True}

    @contextmanager
    def scope(self):
        require_write_access()
        # Check the inherited execution boundary before changing current_root.
        with execution_scope(ExecutionPolicy(workspace=self.root)):
            root_token = set_current_root(self.root)
            run_token = set_current_run_id(self.run_id)
            artifact_token = _artifact_root.set(self.parent)
            child_token = _active_child.set(self.run_id)
            try:
                yield
            finally:
                _active_child.reset(child_token)
                _artifact_root.reset(artifact_token)
                reset_current_run_id(run_token)
                reset_current_root(root_token)

    def finish(self, state: str) -> None:
        self.record["state"] = state
        try:
            final = _scan(self.root, restricted=self.record.get("restricted", False))
            self.record["final"] = final
            baseline = self.record["baseline"]
            self.record["changes"] = sorted(p for p in baseline.keys() | final.keys()
                                             if baseline.get(p) != final.get(p))
        except (OSError, ValueError, ProcessBoundaryError) as exc:
            self.record["state"] = "needs_review"
            self.record["error"] = str(exc)
            raise
        finally:
            self.save()


def _base(parent: Path, run_id: str) -> Path:
    return managed_child(require_directory(parent) / ".minimax" / "child-workspaces", run_id)


def load_child(parent: Path, run_id: str) -> ChildWorkspace | None:
    parent = require_directory(parent)
    base = _base(parent, run_id)
    manifest = _path(base, "manifest.json")
    if not manifest.exists():
        return None
    if manifest.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Child workspace manifest is too large")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if (not isinstance(record, dict) or record.get("parent") != str(parent)
            or record.get("run_id") != run_id or record.get("version") != 1):
        raise ValueError("Child workspace ownership record is invalid")
    states = {"preparing", "running", "completed", "failed", "cancelled", "needs_review", "collected"}
    if (not isinstance(record.get("state"), str) or record["state"] not in states
            or not isinstance(record.get("kind"), str)
            or record["kind"] not in {"directory", "git_worktree"}):
        raise ValueError("Child workspace state or kind is invalid")
    for field in ("baseline", "final"):
        mapping = record.get(field, {} if field == "final" else None)
        if not isinstance(mapping, dict) or len(mapping) > MAX_FILES:
            raise ValueError("Child workspace file manifest is invalid")
        for relative, digest in mapping.items():
            if (not isinstance(relative, str) or not relative or relative == "."
                    or not _included(relative) or not isinstance(digest, str)
                    or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
                raise ValueError("Child workspace file entry is invalid")
            _path(base / "workspace", relative)
    if record["state"] in {"completed", "failed", "cancelled", "collected"} and "final" not in record:
        raise ValueError("Finished child workspace has no final snapshot")
    if record["state"] == "collected":
        receipt = record.get("receipt")
        if (not isinstance(receipt, dict) or receipt.get("run_id") != run_id
                or receipt.get("complete") is not True or receipt.get("conflicts") != []
                or not isinstance(receipt.get("merged"), list)
                or not isinstance(receipt.get("noop"), list)):
            raise ValueError("Child workspace collection receipt is invalid")
    return ChildWorkspace(parent, run_id, base, record)


async def prepare_child(parent: Path, run_id: str, *, restricted=False) -> ChildWorkspace:
    require_write_access()
    parent = require_directory(parent)
    if restricted:
        source_snapshot = _scan(parent, restricted=True)
        source_identity = parent.stat().st_dev, parent.stat().st_ino
    base = _base(parent, run_id)
    base.mkdir(parents=True, exist_ok=False)
    child = ChildWorkspace(parent, run_id, base, {
        "version": 1, "parent": str(parent), "run_id": run_id,
        "state": "preparing", "kind": "directory", "baseline": {}, "omitted": [],
        "restricted": restricted,
    })
    child.save()
    try:
        # Detect Git from the selected root, never silently substitute an
        # ancestor repository or a process-wide working directory.
        if not restricted and (parent / ".git").exists():
            revision = (await _git(parent, "rev-parse", "--verify", "HEAD^{commit}")).strip()
            names = (await _git(parent, "ls-files", "-z", "--cached", "--others",
                                "--exclude-standard")).split("\0")
            relative_files = sorted(set(p for p in names if p))
            await _git(parent, "worktree", "add", "--detach", "--no-checkout", str(child.root), revision)
            await _git(child.root, "read-tree", revision)
            child.record.update(kind="git_worktree", revision=revision)
        else:
            child.root.mkdir()
            relative_files = sorted(source_snapshot if restricted else _scan(parent))
        if restricted:
            _validate_restricted_mounts(parent)
            _validate_restricted_mounts(child.root)
        total = 0
        baseline = child.record["baseline"]
        for relative in relative_files:
            if not _included(relative):
                child.record["omitted"].append(relative)
                continue
            source = _path(parent, relative)
            if restricted:
                info = source.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_dev != source_identity[0]:
                    raise ValueError("Restricted input identity changed during copy")
            if not source.exists():  # An existing uncommitted deletion.
                continue
            digest = _digest(source)
            total += source.stat().st_size
            if len(baseline) >= MAX_FILES or total > MAX_BYTES:
                raise ValueError("Child workspace snapshot exceeds its file/byte limit")
            target = _path(child.root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_copy_file(source, target, baseline_sha256=None)
            if _digest(target) != digest:
                raise ValueError(f"Input changed during snapshot: {relative}")
            os.chmod(target, stat.S_IMODE(source.stat().st_mode))
            baseline[relative] = digest
            # Yield between copies so cancellation can stop a large snapshot.
            await asyncio.sleep(0)
        if restricted:
            if (source_identity != (parent.stat().st_dev, parent.stat().st_ino)
                    or _scan(parent, restricted=True) != source_snapshot
                    or _scan(child.root, restricted=True) != source_snapshot):
                raise ValueError("Restricted source changed during preparation")
        child.record["state"] = "running"
        child.save()
        return child
    except BaseException as exc:
        child.record.update(state="needs_review", error=str(exc) or type(exc).__name__)
        child.save()
        raise


def collection_history(child: ChildWorkspace) -> list[dict[str, Any]]:
    """Read recent durable attempts without modifying or deleting older records."""
    directory = _path(child.base, "collection-attempts")
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError("Child collection history is invalid")
    entries = sorted(directory.glob("*.json"), reverse=True)[:50]
    result = []
    for path in entries:
        if path.resolve() != path or path.stat().st_size > 1024 * 1024:
            raise ValueError("Child collection history is invalid")
        record = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(record, dict) or record.get("run_id") != child.run_id
                or record.get("version") != 1 or record.get("status") not in
                {"in_progress", "interrupted", "partial", "complete"}
                or not isinstance(record.get("report"), dict)):
            raise ValueError("Child collection history is invalid")
        result.append(record)
    return result


def read_resolution_backup(child: ChildWorkspace, attempt_id: str) -> dict[str, str]:
    """Read only a journal-bound, digest-verified pre-resolution text backup."""
    records = [record for record in collection_history(child) if record.get("id") == attempt_id]
    if len(records) != 1:
        raise ValueError("Backup attempt is unavailable")
    record = records[0]
    relative, expected = record.get("path"), record.get("parent_sha256")
    if (record.get("operation") != "manual_resolution" or not isinstance(relative, str)
            or not _included(relative) or not isinstance(expected, str)
            or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected)
            or not isinstance(attempt_id, str) or not attempt_id or Path(attempt_id).name != attempt_id):
        raise ValueError("Backup lacks a verifiable original digest")
    backup = _path(child.base, "resolution-backups/" + attempt_id + "/" + relative)
    if record.get("backup") != str(backup) or not backup.is_file() or backup.is_symlink():
        raise ValueError("Backup is unavailable or its path is invalid")
    with backup.open("rb") as stream:
        data = stream.read(16385)
    if len(data) > 16384 or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Backup content does not match the recorded digest")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Backup is not UTF-8") from exc
    return {"attempt_id": attempt_id, "path": relative, "sha256": expected, "content": content}


def collect_child(child: ChildWorkspace, *, on_conflict: str = "fail") -> dict[str, Any]:
    require_write_access()
    if active_child():
        raise ValueError("Only the parent workspace may collect child changes")
    if on_conflict not in {"fail", "skip"}:
        raise ValueError("Unsupported conflict policy")
    if child.record["state"] == "collected":
        return {**child.record["receipt"], "already_collected": True}
    if child.record["state"] not in {"completed", "failed", "cancelled"}:
        raise ValueError("Child workspace is active or needs manual review")
    final = _scan(child.root)
    if final != child.record.get("final"):
        raise ValueError("Child files changed after execution; preserve them for manual review")
    baseline = child.record["baseline"]
    changes = sorted(p for p in baseline.keys() | final.keys() if baseline.get(p) != final.get(p))
    conflicts, pending, noop = [], [], []
    for relative in changes:
        target = _path(child.parent, relative)
        current = _digest(target)
        resolution = child.record.get("resolutions", {}).get(relative, {})
        if (current == final.get(relative) or
                (resolution.get("parent_sha256") == current and current is not None
                 and resolution.get("child_sha256") == final.get(relative))):
            noop.append(relative)
        elif current != baseline.get(relative):
            conflicts.append({"path": relative, "baseline_sha256": baseline.get(relative),
                              "workspace_sha256": current, "child_sha256": final.get(relative)})
        elif relative not in final:
            # Deletion requires the forthcoming reversible deletion/undo flow.
            # Preserve both copies instead of silently removing user data.
            conflicts.append({"path": relative, "reason": "deletion_requires_review"})
        else:
            pending.append((relative, current))
    report: dict[str, Any] = {"run_id": child.run_id, "merged": [], "noop": noop,
                              "conflicts": conflicts, "complete": False, "pruned": False}
    if conflicts and on_conflict == "fail":
        return report
    directory = _path(child.base, "collection-attempts")
    directory.mkdir(parents=True, exist_ok=True)
    attempt_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex
    journal = _path(child.base, "collection-attempts/" + attempt_id + ".json")
    attempt: dict[str, Any] = {"version": 1, "id": attempt_id, "run_id": child.run_id,
                               "status": "in_progress", "pending_path": None, "report": report}

    def save_attempt() -> None:
        atomic_write_text(journal, json.dumps(attempt, ensure_ascii=False, indent=2),
                          baseline_path=journal,
                          baseline_sha256=_digest(journal, max_bytes=1024 * 1024))

    save_attempt()  # No parent files are touched until the attempt is durable.
    try:
        for relative, current in pending:
            # A crash anywhere after this save leaves this path explicitly uncertain.
            attempt["pending_path"] = relative
            save_attempt()
            target = _path(child.parent, relative)
            if current is not None:
                backup = _path(child.base, "backups/" + relative)
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.exists():
                    atomic_copy_file(target, backup, baseline_sha256=None,
                                     expected_source_sha256=current)
                elif _digest(backup) != current:
                    raise ValueError("Existing collection backup differs from the current file; review it before collecting")
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_copy_file(_path(child.root, relative), target, baseline_sha256=current,
                             expected_source_sha256=final[relative])
            report["merged"].append({"path": relative, "sha256": final[relative]})
            attempt["pending_path"] = None
            save_attempt()
        report["complete"] = not conflicts
        if report["complete"]:
            child.record.update(state="collected", receipt=report)
            child.save()
        attempt["status"] = "complete" if report["complete"] else "partial"
        save_attempt()
    except BaseException:
        # Do not assert whether the pending copy happened if a write or save failed.
        attempt["status"] = "interrupted"
        try:
            save_attempt()
        except Exception:
            pass  # The last durable record remains conservative.
        raise
    return report


def resolve_child_text(child: ChildWorkspace, relative: str, content: str,
                       parent_sha: str, child_sha: str) -> dict[str, Any]:
    """Record a user-authored merge with a durable backup and uncertain-write journal."""
    require_write_access()
    if active_child() or child.record["state"] not in {"completed", "failed", "cancelled"}:
        raise ValueError("Child workspace cannot be resolved in its current state")
    if len(content.encode("utf-8")) > 16384:
        raise ValueError("Merged text exceeds the 16 KiB limit")
    parent, source = _path(child.parent, relative), _path(child.root, relative)
    if (not _included(relative) or not parent_sha or not child_sha
            or child.record.get("final", {}).get(relative) != child_sha
            or _digest(parent) != parent_sha or _digest(source) != child_sha):
        raise ValueError("Files changed since review; review them again")
    # Both versions must be small, regular UTF-8 files; never create or delete here.
    for path in (parent, source):
        if path.stat().st_size > 16384:
            raise ValueError("File exceeds the manual merge limit")
        path.read_bytes().decode("utf-8")
    directory = _path(child.base, "collection-attempts")
    directory.mkdir(parents=True, exist_ok=True)
    attempt_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex
    journal = _path(child.base, "collection-attempts/" + attempt_id + ".json")
    backup = _path(child.base, "resolution-backups/" + attempt_id + "/" + relative)
    report = {"run_id": child.run_id, "merged": [], "noop": [], "conflicts": [],
              "complete": False, "pruned": False}
    attempt = {"version": 1, "id": attempt_id, "run_id": child.run_id,
               "status": "in_progress", "pending_path": relative, "report": report,
               "operation": "manual_resolution", "backup": str(backup),
               "path": relative, "parent_sha256": parent_sha}

    def save_attempt() -> None:
        atomic_write_text(journal, json.dumps(attempt, ensure_ascii=False, indent=2),
                          baseline_path=journal, baseline_sha256=_digest(journal, max_bytes=1024 * 1024))

    save_attempt()
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        atomic_copy_file(parent, backup, baseline_sha256=None, expected_source_sha256=parent_sha)
        if _digest(source) != child_sha:
            raise ValueError("Child file changed since review")
        atomic_write_text(parent, content, baseline_path=parent, baseline_sha256=parent_sha)
        merged_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        child.record.setdefault("resolutions", {})[relative] = {
            "parent_sha256": merged_sha, "child_sha256": child_sha, "attempt_id": attempt_id}
        child.save()
        report["merged"].append({"path": relative, "sha256": merged_sha})
        attempt.update(status="complete", pending_path=None)
        save_attempt()
    except BaseException:
        attempt["status"] = "interrupted"
        try:
            save_attempt()
        except Exception:
            pass
        raise
    return {"path": relative, "sha256": merged_sha, "backup": str(backup)}
