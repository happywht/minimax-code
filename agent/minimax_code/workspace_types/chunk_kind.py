"""Chunk discriminator enum — R67 error-dependency closure (R67).

Fusion of grok-build's ``xai-grok-workspace-types::chunks::ChunkKind``.

Migrated as the transitive dependency of :class:`WorkspaceError`'s
``ProtocolMismatch`` variant (which embeds a ``ChunkKind``). The full
``chunks/`` module (OpsChunk / SessionChunk / ToolChunk / ToolResponse,
844 lines) is deferred to a later round — only the 29-variant
discriminator enum is needed for R67's error layer to round-trip.

Wire subtlety
-------------

``ChunkKind`` has **two distinct representations**:

* **wire value** — ``snake_case`` (``"tool_output"``, ``"git_status"``);
  this is what ``#[serde(rename_all = "snake_case")]`` emits.
* **Display / ``as_str()``** — the ``PascalCase`` Rust variant name
  (``"ToolOutput"``, ``"GitStatus"``); this is what ``Display`` and the
  ``as_str()`` method return.

Python mirrors this with a ``StrEnum`` whose *value* is the snake_case
wire string (so JSON / pydantic serialisation is wire-correct) and an
:meth:`as_str` method returning the PascalCase name (mirrors Rust
``Display`` / ``as_str``).
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ChunkKind"]


class ChunkKind(StrEnum):
    """Discriminator for chunk payloads (29 variants).

    Enum member *name* is PascalCase (Python convention); *value* is the
    snake_case wire string. :meth:`as_str` returns the PascalCase name
    (mirrors Rust ``Display`` / ``as_str``).
    """

    ToolOutput = "tool_output"
    ToolProgress = "tool_progress"
    ToolFinal = "tool_final"
    ToolDefinitions = "tool_definitions"
    NeedPermission = "need_permission"
    NeedUserAnswer = "need_user_answer"
    NeedPlanModeChange = "need_plan_mode_change"
    GitStatus = "git_status"
    GitDiff = "git_diff"
    GitBranchInfo = "git_branch_info"
    GitMetadata = "git_metadata"
    Hunks = "hunks"
    Skills = "skills"
    Plugins = "plugins"
    ProjectConfig = "project_config"
    Permissions = "permissions"
    Envrc = "envrc"
    ResolvedFiles = "resolved_files"
    MemoryChunks = "memory_chunks"
    Plugin = "plugin"
    Ack = "ack"
    FuzzyMatch = "fuzzy_match"
    RipgrepHit = "ripgrep_hit"
    RipgrepDone = "ripgrep_done"
    SessionId = "session_id"
    SessionInfo = "session_info"
    RewindResult = "rewind_result"
    RewindPoints = "rewind_points"
    SessionAck = "session_ack"

    def as_str(self) -> str:
        """PascalCase variant name — mirrors Rust ``Display`` / ``as_str``."""
        return self.name

    @classmethod
    def all(cls) -> list[ChunkKind]:
        """All variants in declaration order — mirrors ``ChunkKind::all``."""
        return list(cls)
