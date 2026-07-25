"""Workspace checkpoint package barrel (R310).

Re-exports the value types and runtime landed in R310 from MiniMax's
roadmap R13-R14 — git-stash-backed workspace snapshots plus restore and
diff preview. This opens the ``workspace`` Python package as the
runtime home for checkpoint state, separate from the
:mod:`minimax_code.ipc.handlers_workspace` worktree surface (which lives
in the IPC layer and owns the unrelated ``workspace.*`` worktree RPCs).

Fusion note (concept-for-concept, not line-for-line): this package fuses
the MiniMax roadmap checkpoint concept, not any single grok crate. grok's
``recovery.rs`` is GCS upload-queue recovery — a different concern cited
only as design reference.

Migrated leaves
--------------
- R310 (2026-07-25): ``types.py`` — :class:`Checkpoint`,
  :class:`CheckpointRestoreResult`, :class:`CheckpointDiff` value types.
  ``checkpoint.py`` — :class:`CheckpointManager` runtime
  (``git stash create`` + untracked file copy + restore + diff preview),
  fault-tolerant throughout.
"""

from __future__ import annotations

from minimax_code.workspace.checkpoint import CheckpointManager
from minimax_code.workspace.types import (
    Checkpoint,
    CheckpointDiff,
    CheckpointRestoreResult,
)

__all__ = [
    "Checkpoint",
    "CheckpointDiff",
    "CheckpointManager",
    "CheckpointRestoreResult",
]
