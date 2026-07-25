"""Value types for the workspace checkpoint layer (R310).

Pure data — no I/O. Mirrors the value-type-first decomposition pattern
established by :mod:`minimax_code.crash.types`: the snapshot, restore,
and diff results are plain ``dataclass(slots=True)`` containers so the
runtime (:mod:`minimax_code.workspace.checkpoint`) and the IPC layer
(:mod:`minimax_code.ipc.handlers_checkpoint`) share one canonical
shape without coupling to each other's plumbing.

Fusion note: grok's recovery.rs is GCS upload-queue recovery, a
different concern. This layer fuses the *concept* in MiniMax's roadmap
(R13-R14: "git stash + file copy → restore/rollback + diff preview"),
not any specific grok source file — concept-for-concept, not
line-for-line.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Checkpoint:
    """One workspace snapshot for a session.

    Attributes
    ----------
    id:
        Stable handle (``ckpt_<uuid>``). Used as the on-disk snapshot
        directory name and the DAO primary key.
    session_id:
        Logical owner. Not a foreign key — see migration 015.
    label, message:
        Human-facing metadata. ``label`` is the short title shown in a
        rewind list; ``message`` is an optional longer description.
    git_stash_ref:
        SHA returned by ``git stash create`` for the tracked working-tree
        changes, or ``None`` when the tree was clean (no snapshot
        needed) or git was unavailable. This ref is *not* pushed onto
        the stash stack, so it does not pollute ``git stash list``.
    branch:
        Branch name captured at snapshot time (``git rev-parse
        --abbrev-ref HEAD``). ``None`` when git was unavailable.
    tracked_files:
        Repo-relative paths of tracked files whose changes are captured
        in ``git_stash_ref``. Empty when the ref is ``None``.
    untracked_files:
        Repo-relative paths of untracked files physically copied into
        the snapshot directory. Restored on rewind only when the
        destination does not already exist.
    has_untracked_snapshot:
        Whether the on-disk untracked copy exists and is restorable.
    created_at:
        ISO-8601 UTC timestamp (``Z`` suffix), matching the rest of the
        storage layer.
    """

    id: str
    session_id: str
    label: str
    message: str
    git_stash_ref: str | None
    branch: str | None
    tracked_files: list[str]
    untracked_files: list[str]
    has_untracked_snapshot: bool
    created_at: str


@dataclass(slots=True)
class CheckpointRestoreResult:
    """Outcome of rewinding a working tree to a checkpoint.

    ``restored`` is the aggregate boolean (something was applied);
    the granular fields explain *what* so the caller can surface a
    faithful summary instead of a bare ok/not-ok.
    """

    checkpoint_id: str
    restored: bool
    applied_stash: bool
    restored_untracked: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CheckpointDiff:
    """Diff preview for a checkpoint.

    ``available`` is ``False`` when the stash ref can no longer be
    resolved (e.g. it was garbage-collected after a ``git gc``). In
    that case ``patch`` is empty and the UI shows "snapshot expired"
    instead of a stale diff.
    """

    checkpoint_id: str
    available: bool
    patch: str
    files: list[str] = field(default_factory=list)


__all__ = [
    "Checkpoint",
    "CheckpointDiff",
    "CheckpointRestoreResult",
]
