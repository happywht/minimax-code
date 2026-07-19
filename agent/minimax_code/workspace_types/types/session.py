"""Session / rewind / filesystem-event / server-status shapes (R67).

Fusion of grok-build's ``xai-grok-workspace-types::types::session``.

Carries:

* :class:`AgentSessionInfo` — the session descriptor emitted as the
  ``SessionInfo`` chunk (and referenced by id from every request via the
  ``x-workspace-session-id`` metadata key).
* :class:`RewindResult` / :class:`RewindPoint` — the rewind RPC's result
  and the history checkpoints it operates on.
* :class:`FsEventKind` — discriminator for filesystem-watch events.
* :class:`ServerStatus` (+ ``LspServerStatus`` / ``McpServerStatus``
  aliases) — lifecycle state of a managed language/tool server.

All structs are ``#[serde(default)]`` and derive ``Default``;
``created_at`` / ``at`` default to the Unix epoch (deterministic
sentinel, not ``Utc::now()``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field

from minimax_code.workspace_types._wire import WireModel
from minimax_code.workspace_types.identity import SessionId
from minimax_code.workspace_types.types.config import IsolationMode

__all__ = [
    "AgentSessionInfo",
    "RewindResult",
    "RewindPoint",
    "FsEventKind",
    "ServerStatus",
    "LspServerStatus",
    "McpServerStatus",
]

#: Unix-epoch sentinel — deterministic default for timestamps.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class AgentSessionInfo(WireModel):
    """Session descriptor emitted as the ``SessionInfo`` chunk.

    ``id`` is the canonical :class:`SessionId`; ``parent`` is the
    forked-from session (``None`` for a root session); ``created_at``
    defaults to the Unix epoch.
    """

    id: SessionId
    parent: SessionId | None = None
    agent_id: str = ""
    isolation: IsolationMode = Field(default=IsolationMode.None_)
    created_at: datetime = Field(default=_EPOCH)

    @classmethod
    def default(cls) -> AgentSessionInfo:
        return cls(id=SessionId(""))


class RewindResult(WireModel):
    """Result of a rewind RPC.

    ``head_prompt_index`` is the new head after the rewind;
    ``prompts_dropped`` is how many were discarded.
    """

    session: SessionId
    head_prompt_index: int = 0
    prompts_dropped: int = 0

    @classmethod
    def default(cls) -> RewindResult:
        return cls(session=SessionId(""))


class RewindPoint(WireModel):
    """One rewindable history checkpoint."""

    prompt_index: int = 0
    at: datetime = Field(default=_EPOCH)
    summary: str = ""


class FsEventKind(StrEnum):
    """Discriminator for filesystem-watch events (``#[serde(default)] Modified``)."""

    Created = "created"
    Modified = "modified"
    Removed = "removed"
    Renamed = "renamed"

    @classmethod
    def default(cls) -> FsEventKind:
        """Rust ``#[default] Modified``."""
        return cls.Modified


class ServerStatus(StrEnum):
    """Lifecycle state of a managed LSP/MCP server (``#[serde(default)] Running``)."""

    Starting = "starting"
    Running = "running"
    Stopped = "stopped"
    Failed = "failed"

    @classmethod
    def default(cls) -> ServerStatus:
        """Rust ``#[default] Running``."""
        return cls.Running


#: Alias — LSP servers share the same status vocabulary (Rust type alias).
LspServerStatus = ServerStatus
#: Alias — MCP servers share the same status vocabulary (Rust type alias).
McpServerStatus = ServerStatus
