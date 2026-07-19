"""Workspace config leaf types (R67).

Fusion of grok-build's ``xai-grok-workspace-types::types::config`` —
session-isolation / capability enums and the project-level config
structs. Pure types: no I/O, no env reads, no dependency on the rest of
the agent. The crate's reason for existing — dependency inversion — is
preserved as a clean layering boundary (this module imports only
``_wire``).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from minimax_code.workspace_types._wire import WireModel

__all__ = [
    "IsolationMode",
    "CapabilityMode",
    "ToolServerConfig",
    "AgentSessionConfig",
    "ProjectConfig",
    "PermissionPolicy",
]


class IsolationMode(StrEnum):
    """Filesystem isolation mode for an agent session.

    Rust variant ``None`` is renamed to ``None_`` here (``None`` is a
    Python keyword). Wire value is ``"none"`` (snake_case rename).
    """

    None_ = "none"
    Worktree = "worktree"
    Sandbox = "sandbox"

    @classmethod
    def default(cls) -> IsolationMode:
        """Rust ``#[default] None``."""
        return cls.None_


class CapabilityMode(StrEnum):
    """Read/write capability granted to an agent session."""

    ReadWrite = "read_write"
    ReadOnly = "read_only"
    None_ = "none"

    @classmethod
    def default(cls) -> CapabilityMode:
        """Rust ``#[default] ReadWrite``."""
        return cls.ReadWrite


class ToolServerConfig(WireModel):
    """One tool-server (MCP/stdio) entry in an agent session config.

    Wire: ``id`` required; ``enabled`` defaults ``false``; ``command`` is
    ``Option<String>`` (null when absent); ``args`` is a ``BTreeMap``
    (keys sorted on the wire).
    """

    id: str
    enabled: bool = False
    command: str | None = None
    args: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def default(cls) -> ToolServerConfig:
        return cls(id="")


class AgentSessionConfig(WireModel):
    """Per-agent session configuration.

    Wire: ``agent_id`` required; everything else defaults
    (``isolation``=none, ``capability_mode``=read_write, ``tool_config``
    =[], ``max_depth``=0, ``cwd_override``=null, ``extra_env``={}).
    """

    agent_id: str
    isolation: IsolationMode = Field(default=IsolationMode.None_)
    capability_mode: CapabilityMode = Field(default=CapabilityMode.ReadWrite)
    tool_config: list[ToolServerConfig] = Field(default_factory=list)
    max_depth: int = 0
    cwd_override: str | None = None
    extra_env: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def default(cls) -> AgentSessionConfig:
        return cls(agent_id="")


class ProjectConfig(WireModel):
    """Project-level config block.

    ``values`` is a ``BTreeMap<String, String>`` (sorted keys on wire);
    ``trusted`` defaults ``false``.
    """

    values: dict[str, str] = Field(default_factory=dict)
    trusted: bool = False

    @classmethod
    def default(cls) -> ProjectConfig:
        return cls()


class PermissionPolicy(WireModel):
    """Permission allow/deny/ask rule lists (``Vec<String>`` each)."""

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)

    @classmethod
    def default(cls) -> PermissionPolicy:
        return cls()
