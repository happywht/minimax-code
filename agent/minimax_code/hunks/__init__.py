"""Hunk tracking — fusion of grok's ``xai-hunk-tracker`` (R39).

The pure diff-and-attribution layer for tracking file changes at the hunk
(connected diff region) level with source attribution (agent turn vs external
edit). Where MiniMax Code's edit tool overwrites files wholesale, this package
is the vocabulary for a future "review the agent's edit block-by-block" pass:
each edit becomes a :class:`~.types.Hunk` attributed to an
:class:`~.types.AgentEdit` (with the prompt index that made it).

Scope of this package
---------------------

* :mod:`.types` (R39) — :class:`~.types.HunkId`, :class:`~.types.HunkLineInfo`,
  the :data:`~.types.HunkSource` union (``AgentEdit`` /
  ``ExternalEditOnAgentFile`` / ``External``), and :class:`~.types.Hunk` with
  its ``file_created`` factory. Pure data shapes; no diff engine yet.
* :mod:`.diff` (R39) — :func:`~.diff.compute_hunks` (line-based diff → hunks
  via :mod:`difflib`), the size-cap guard, unified-patch generation, line
  patching, and the hunk match/overlap predicates.

What is NOT here (wiring round): the actor (grok ``HunkTrackerActor`` over
tokio mpsc channels) and the git integration (grok ``gix`` status / index)
are Rust platform stacks. A future wiring round picks an asyncio actor or a
synchronous tracker and a Python git binding (or shells out to ``git``); the
pure functions here are called unchanged from either.
"""

from __future__ import annotations

from .diff import (
    CONTEXT_LINES,
    DIFF_TIMEOUT,
    MAX_DIFF_FILE_SIZE,
    calculate_overlap_size,
    compute_hunks,
    find_matching_old_hunk,
    find_overlapping_hunks,
    format_unified_diff,
    generate_hunk_patch,
    generate_unified_patch,
    hunk_moved,
    hunks_match_content,
    hunks_overlap,
    patch_lines,
)
from .types import (
    AgentEdit,
    External,
    ExternalEditOnAgentFile,
    Hunk,
    HunkId,
    HunkLineInfo,
    HunkSource,
)

__all__ = [
    # types (R39)
    "HunkId",
    "HunkLineInfo",
    "AgentEdit",
    "ExternalEditOnAgentFile",
    "External",
    "HunkSource",
    "Hunk",
    # diff (R39)
    "CONTEXT_LINES",
    "MAX_DIFF_FILE_SIZE",
    "DIFF_TIMEOUT",
    "compute_hunks",
    "generate_unified_patch",
    "generate_hunk_patch",
    "format_unified_diff",
    "patch_lines",
    "hunks_match_content",
    "hunk_moved",
    "hunks_overlap",
    "calculate_overlap_size",
    "find_matching_old_hunk",
    "find_overlapping_hunks",
]
