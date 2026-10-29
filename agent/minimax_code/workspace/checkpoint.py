"""Workspace checkpoint runtime — git stash + file copy (R310).

The runtime half of the R13-R14 checkpoint capability. A checkpoint
captures two independent slices of a working tree:

* **Tracked changes** (modified + staged files git already knows about)
  are delegated to ``git stash create``. That command returns a commit
  SHA *without* pushing onto the stash stack, so the user's
  ``git stash list`` stays pristine and the ref is simply a dangling
  commit we can ``apply`` later.
* **Untracked files** (``git ls-files --others --exclude-standard``) are
  physically copied under ``<snapshot_root>/<checkpoint_id>/``, because
  ``git stash create`` ignores them by default.

Both halves are fault-tolerant. A non-repo cwd, a missing ``git``
binary, or a wedged call yields a checkpoint with empty git fields and a
warning rather than an exception — the same "partial trajectory still
lands" contract the self-evolution collector (R309) honours. The caller
gets a structured :class:`Checkpoint` either way and can decide how to
surface the warnings.

Fusion note (concept-for-concept, not line-for-line): this is MiniMax
roadmap R13-R14. grok's ``recovery.rs`` is GCS upload-queue recovery —
a different concern cited only as design reference.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ..execution import capture_output, require_write_access, spawn_exec
from ..file_mutations import atomic_copy_file
from ..managed_paths import managed_child
from ..workspace_ctx import require_directory
from .types import Checkpoint, CheckpointDiff, CheckpointRestoreResult

logger = logging.getLogger(__name__)

#: Per-git-call cap. Generous (a cold ``git stash create`` is sub-second
#: even on a large repo) but bounded so a wedged git never stalls the
#: IPC handler thread.
_DEFAULT_TIMEOUT_S = 30.0


async def _run_git(
    cwd: str, *args: str, timeout_s: float
) -> tuple[int | None, str, str, str | None]:
    """Run ``git`` in ``cwd``; return ``(rc, stdout, stderr, error)``.

    Fault-tolerant: a missing binary or a timeout yields ``rc=None`` and
    a populated ``error`` so the caller can record it as a warning
    instead of crashing. Mirrors :func:`minimax_code.agent.self_evolution.runner._run_cmd`.
    """
    try:
        proc = await spawn_exec(
            "git", "--no-pager", "-c", "core.fsmonitor=false",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None, "", "", "git not found"
    except OSError as exc:
        return None, "", "", f"could not spawn git: {exc}"

    try:
        out_b, err_b = await capture_output(proc, timeout=timeout_s)
    except TimeoutError:
        return None, "", "", f"git timed out after {timeout_s}s"
    except RuntimeError as exc:
        return None, "", "", str(exc)

    rc = proc.returncode
    out = out_b.decode("utf-8", errors="replace") if out_b else ""
    err = err_b.decode("utf-8", errors="replace") if err_b else ""
    return rc, out, err, None


def _now_iso() -> str:
    """UTC ISO-8601 timestamp with ``Z`` suffix, matching the storage layer."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_iso(dt: datetime) -> str:
    """Normalise an injectable clock value to the storage-layer timestamp shape."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CheckpointManager:
    """Snapshot and rewind a working tree.

    The manager owns the on-disk untracked-file copy area
    (``snapshot_root``). Tracked changes are delegated to git's dangling
    stash commits, so they need no disk layout here.

    All git interaction is fault-tolerant (see module docstring); the
    methods below never raise on git failures — they record warnings on
    the returned objects.
    """

    def __init__(
        self, snapshot_root: Path, *, timeout_s: float = _DEFAULT_TIMEOUT_S
    ) -> None:
        self._snapshot_root = Path(snapshot_root)
        self._timeout_s = timeout_s

    def snapshot_dir(self, checkpoint_id: str) -> Path:
        """Directory holding the untracked-file copy for one checkpoint."""
        return managed_child(self._snapshot_root, checkpoint_id)

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    async def create(
        self,
        cwd: str | Path,
        *,
        checkpoint_id: str,
        session_id: str,
        label: str,
        message: str = "",
        now: datetime | None = None,
    ) -> Checkpoint:
        """Capture a snapshot of ``cwd`` and return it.

        Tracked working-tree changes become a ``git stash create`` ref;
        untracked files are physically copied under
        :meth:`snapshot_dir`. Git failures downgrade those fields to
        empty/``None`` and append a warning — the snapshot itself still
        succeeds with whatever slices were collectable.
        """
        require_write_access()
        self.snapshot_dir(checkpoint_id)
        cwd_str = str(require_directory(cwd))
        warnings: list[str] = []

        branch = await self._branch(cwd_str, warnings)
        stash_ref = await self._stash_create(cwd_str, warnings)
        tracked_files = (
            await self._stash_files(cwd_str, stash_ref, warnings) if stash_ref else []
        )
        untracked_files = await self._untracked_files(cwd_str, warnings)

        has_snapshot = False
        if untracked_files:
            has_snapshot = self._copy_untracked(
                cwd_str, checkpoint_id, untracked_files, warnings
            )

        timestamp = _now_iso() if now is None else _to_iso(now)

        # Warnings are not stored on the row (they are transient
        # collection-time signals); they are logged so a degraded
        # snapshot is observable without failing the call.
        for warning in warnings:
            logger.warning("checkpoint %s: %s", checkpoint_id, warning)

        return Checkpoint(
            id=checkpoint_id,
            session_id=session_id,
            label=label,
            message=message,
            git_stash_ref=stash_ref,
            branch=branch,
            tracked_files=tracked_files,
            untracked_files=untracked_files,
            has_untracked_snapshot=has_snapshot,
            created_at=timestamp,
        )

    # ------------------------------------------------------------------
    # restore
    # ------------------------------------------------------------------

    async def restore(
        self,
        cwd: str | Path,
        checkpoint: Checkpoint,
    ) -> CheckpointRestoreResult:
        """Rewind ``cwd`` to ``checkpoint``.

        Applies the stash ref (if any) and copies untracked files back,
        skipping any destination that already exists — a rewind must
        never clobber work the user did after the snapshot.
        """
        require_write_access()
        self.snapshot_dir(checkpoint.id)
        cwd_str = str(require_directory(cwd))
        warnings: list[str] = []
        applied_stash = False
        restored_untracked: list[str] = []
        skipped_existing: list[str] = []

        if checkpoint.git_stash_ref:
            if await self._can_apply_stash(cwd_str, checkpoint.git_stash_ref, warnings):
                applied_stash = await self._stash_apply(
                    cwd_str, checkpoint.git_stash_ref, warnings
                )

        if checkpoint.has_untracked_snapshot and checkpoint.untracked_files:
            restored_untracked, skipped_existing = self._restore_untracked(
                cwd_str, checkpoint.id, checkpoint.untracked_files, warnings
            )
        elif checkpoint.untracked_files:
            warnings.append("untracked snapshot unavailable; listed files could not be restored")

        restored = applied_stash or bool(restored_untracked)
        return CheckpointRestoreResult(
            checkpoint_id=checkpoint.id,
            restored=restored,
            applied_stash=applied_stash,
            restored_untracked=restored_untracked,
            skipped_existing=skipped_existing,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # diff
    # ------------------------------------------------------------------

    async def diff(
        self, cwd: str | Path, checkpoint: Checkpoint
    ) -> CheckpointDiff:
        """Preview the tracked-change patch captured by a checkpoint.

        Returns ``available=False`` when there is no stash ref or the ref
        can no longer be resolved (e.g. garbage-collected), so the UI can
        show "snapshot expired" instead of a stale diff.
        """
        if not checkpoint.git_stash_ref or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", checkpoint.git_stash_ref):
            return CheckpointDiff(
                checkpoint_id=checkpoint.id, available=False, patch="", files=[]
            )
        cwd_str = str(require_directory(cwd))
        rc, patch, _, err = await _run_git(
            cwd_str,
            "stash",
            "show",
            "-p",
            "--no-color", "--no-ext-diff", "--no-textconv",
            checkpoint.git_stash_ref,
            timeout_s=self._timeout_s,
        )
        if err is not None or rc != 0:
            return CheckpointDiff(
                checkpoint_id=checkpoint.id, available=False, patch="", files=[]
            )
        files = await self._stash_files(cwd_str, checkpoint.git_stash_ref, None)
        return CheckpointDiff(
            checkpoint_id=checkpoint.id, available=True, patch=patch, files=files
        )

    # ------------------------------------------------------------------
    # delete snapshot
    # ------------------------------------------------------------------

    def delete_snapshot(self, checkpoint_id: str) -> bool:
        """Remove the on-disk untracked copy.

        Returns whether anything was removed. Idempotent — safe to call
        for checkpoints that never had an untracked slice.
        """
        require_write_access()
        directory = self.snapshot_dir(checkpoint_id)
        if directory.exists():
            shutil.rmtree(directory)
            return True
        return False

    # ------------------------------------------------------------------
    # git helpers
    # ------------------------------------------------------------------

    async def _branch(self, cwd: str, warnings: list[str]) -> str | None:
        rc, out, _, err = await _run_git(
            cwd, "rev-parse", "--abbrev-ref", "HEAD", timeout_s=self._timeout_s
        )
        if err is not None or rc != 0:
            warnings.append(f"branch: {err or out.strip() or 'git failed'}")
            return None
        return out.strip() or None

    async def _stash_create(self, cwd: str, warnings: list[str]) -> str | None:
        # ``git stash create`` prints a commit SHA when there are changes
        # to stash, or empty stdout when the tree is clean. It never
        # pushes onto the stash stack.
        rc, out, _, err = await _run_git(
            cwd, "stash", "create", timeout_s=self._timeout_s
        )
        if err is not None:
            warnings.append(f"stash create: {err}")
            return None
        if rc != 0:
            warnings.append(f"stash create: {out.strip() or 'git failed'}")
            return None
        ref = out.strip()
        return ref or None

    async def _stash_files(
        self, cwd: str, stash_ref: str | None, warnings: list[str] | None
    ) -> list[str]:
        if not stash_ref or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", stash_ref):
            return []
        rc, out, _, err = await _run_git(
            cwd,
            "stash",
            "show",
            "--name-only",
            "-z",
            "--no-color",
            stash_ref,
            timeout_s=self._timeout_s,
        )
        if err is not None or rc != 0:
            if warnings is not None:
                warnings.append(f"stash files: {err or 'git failed'}")
            return []
        return [name for name in out.split("\0") if name]

    async def _untracked_files(self, cwd: str, warnings: list[str]) -> list[str]:
        rc, out, _, err = await _run_git(
            cwd,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            timeout_s=self._timeout_s,
        )
        if err is not None or rc != 0:
            warnings.append(f"untracked: {err or 'git failed'}")
            return []
        return [name for name in out.split("\0") if name]

    async def _can_apply_stash(
        self, cwd: str, stash_ref: str, warnings: list[str]
    ) -> bool:
        """Fail closed if tracked work or the snapshot's base has changed.

        This is a preflight, not a lock: another process can modify the tree
        after it completes. Untracked destinations are handled separately.
        """
        if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", stash_ref):
            warnings.append("已跟踪快照未应用：快照引用无效。")
            return False
        rc, out, _, err = await _run_git(
            cwd, "--no-optional-locks", "status", "--porcelain=v1", "-z",
            "--untracked-files=no", timeout_s=self._timeout_s,
        )
        if err is not None or rc != 0:
            warnings.append("已跟踪快照未应用：无法检查工作树和暂存区状态。")
            return False
        if out:
            warnings.append("已跟踪快照未应用：工作树或暂存区存在改动或冲突，请先保护并处理这些改动。")
            return False
        rc, head, _, err = await _run_git(
            cwd, "rev-parse", "--verify", "HEAD^{commit}", timeout_s=self._timeout_s,
        )
        if err is not None or rc != 0:
            warnings.append("已跟踪快照未应用：无法检查当前提交。")
            return False
        rc, base, _, err = await _run_git(
            cwd, "rev-parse", "--verify", f"{stash_ref}^1", timeout_s=self._timeout_s,
        )
        if err is not None or rc != 0:
            warnings.append("已跟踪快照未应用：无法检查快照基底。")
            return False
        if head.strip() != base.strip():
            warnings.append("已跟踪快照未应用：当前提交与快照基底不同，请先审阅差异。")
            return False
        return True

    async def _stash_apply(
        self, cwd: str, stash_ref: str, warnings: list[str]
    ) -> bool:
        if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", stash_ref):
            warnings.append("stash apply: invalid snapshot ref")
            return False
        rc, out, err_out, err = await _run_git(
            cwd, "stash", "apply", stash_ref, timeout_s=self._timeout_s
        )
        if err is not None:
            warnings.append(f"stash apply: {err}")
            return False
        if rc != 0:
            # Typically a merge conflict with existing working-tree
            # content — surface the git message so the user can decide.
            warnings.append(f"stash apply: {(err_out or out).strip() or 'conflict'}")
            return False
        return True

    # ------------------------------------------------------------------
    # file copy helpers
    # ------------------------------------------------------------------

    def _copy_untracked(
        self,
        cwd: str,
        checkpoint_id: str,
        files: list[str],
        warnings: list[str],
    ) -> bool:
        dest_root = self.snapshot_dir(checkpoint_id)
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warnings.append(f"snapshot mkdir: {exc}")
            return False
        ok = False
        for rel in files:
            try:
                src = _checkpoint_file(Path(cwd), rel)
                dst = _checkpoint_file(dest_root, rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy_file(src, dst, baseline_sha256=None)
                ok = True
            except (OSError, ValueError) as exc:
                warnings.append(f"snapshot copy {rel}: {exc}")
        return ok

    def _restore_untracked(
        self,
        cwd: str,
        checkpoint_id: str,
        files: list[str],
        warnings: list[str],
    ) -> tuple[list[str], list[str]]:
        src_root = self.snapshot_dir(checkpoint_id)
        restored: list[str] = []
        skipped: list[str] = []
        for rel in files:
            try:
                src = _checkpoint_file(src_root, rel)
                dst = _checkpoint_file(Path(cwd), rel)
                if not src.exists():
                    warnings.append(f"restore {rel}: snapshot missing")
                    continue
                if dst.exists():
                    skipped.append(rel)
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy_file(src, dst, baseline_sha256=None)
                restored.append(rel)
            except (OSError, ValueError) as exc:
                warnings.append(f"restore {rel}: {exc}")
        return restored, skipped


def _checkpoint_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Snapshot file must be workspace-relative")
    base = root.absolute()
    target = base / path
    if target.resolve() != target or not target.is_relative_to(base):
        raise ValueError("Snapshot file escapes its directory or contains a symbolic link")
    return target


__all__ = ["CheckpointManager"]
