"""Remote workspace API wire types (R67).

Fusion of grok-build's ``xai-grok-workspace-types`` **leaf layer** —
the fourth type-contract surface in the platform (after R64 extensions,
R65 tool-types, R66 config-types): the wire DTOs for the *remote*
workspace API (session lifecycle, chunks, RPC requests, events, errors).

This package hosts the **pure leaf types** — no I/O, no env reads, no
dependency on the rest of the agent. The runtime transport (rpc/ +
events/ + chunks/ struct bodies, 4186+ lines across 13 namespaces) is
deferred to later rounds; R67 lands the dependency-free foundation:

* :class:`AdjacentTagged` / :class:`WireModel` — wire-encoding primitives
  (adjacent-tagged enums + BTreeMap-sorted struct serialisation).
* :class:`SessionId` / :class:`ToolCallId` / :class:`HunkId` —
  transparent String newtypes.
* :class:`Metadata` + ``META_*`` header constants — sorted metadata map.
* :class:`ChunkKind` — the 29-variant chunk discriminator (snake_case
  wire value, PascalCase Display).
* :class:`IoKind` / :class:`WorkspaceError` — the error layer
  (39-variant ``ErrorKind`` mirror + 13-variant adjacent-tagged error).
* ``types`` — the full leaf struct/enum surface (config, git, search,
  interaction, tools, session, plan_mode, hunk, permission, plugins,
  files, skills, memory).

R78 adds the crate's top-level dispatch envelope
(:class:`RequestMessage`) — the generic ``RequestMessage<T>`` wire
surface that every workspace RPC is wrapped in. This is the first of
the four top-level dispatch modules (``request`` → ``requests`` →
``events`` → ``chunks``) to land; ``rpc/`` (R68-R77) and ``types/``
(R67) are the leaf payloads this envelope carries.

Mirrors the Rust ``lib.rs`` ``pub use`` re-exports.
"""

from __future__ import annotations

from minimax_code.workspace_types._tagged import AdjacentTagged
from minimax_code.workspace_types._wire import WireModel, sort_mappings
from minimax_code.workspace_types.chunk_kind import ChunkKind
from minimax_code.workspace_types.errors import IoKind, WorkspaceError
from minimax_code.workspace_types.identity import HunkId, SessionId, ToolCallId
from minimax_code.workspace_types.metadata import (
    META_CLIENT_ID,
    META_GRPC_TIMEOUT,
    META_PROMPT_INDEX,
    META_SESSION_ID,
    META_TRACEPARENT,
    META_TRACESTATE,
    STANDARD_META_KEYS,
    Metadata,
)
from minimax_code.workspace_types.request import RequestMessage
from minimax_code.workspace_types.types import (
    AgentSessionConfig,
    AgentSessionInfo,
    CapabilityMode,
    ContentMatch,
    FileReference,
    FsEventKind,
    FuzzyMatch,
    FuzzySearchArgs,
    GitBranchInfo,
    GitDiff,
    GitDiffArgs,
    GitMetadata,
    GitStatus,
    GitStatusOpts,
    HookInfo,
    Hunk,
    HunkAction,
    IsolationMode,
    LspServerStatus,
    MatchSpan,
    McpServerStatus,
    MemoryChunk,
    PermissionDecision,
    PermissionPolicy,
    PermissionRequest,
    PlanModeDecision,
    PlanModeTransition,
    PluginInfo,
    ProjectConfig,
    ResolvedFile,
    RewindPoint,
    RewindResult,
    RipgrepArgs,
    RipgrepStats,
    ServerStatus,
    SkillInfo,
    ToolCallResult,
    ToolDef,
    ToolOutputChunk,
    ToolProgress,
    ToolServerConfig,
    UserAnswer,
    UserQuestion,
    UserQuestionOption,
    VcsKind,
)

__all__ = [
    # wire primitives
    "AdjacentTagged",
    "WireModel",
    "sort_mappings",
    # identity newtypes
    "SessionId",
    "ToolCallId",
    "HunkId",
    # metadata
    "Metadata",
    "META_SESSION_ID",
    "META_TRACEPARENT",
    "META_TRACESTATE",
    "META_CLIENT_ID",
    "META_PROMPT_INDEX",
    "META_GRPC_TIMEOUT",
    "STANDARD_META_KEYS",
    # request envelope (R78 — crate top-level dispatch surface)
    "RequestMessage",
    # chunk discriminator
    "ChunkKind",
    # error layer
    "IoKind",
    "WorkspaceError",
    # types.* (leaf struct/enum surface)
    "IsolationMode",
    "CapabilityMode",
    "ToolServerConfig",
    "AgentSessionConfig",
    "ProjectConfig",
    "PermissionPolicy",
    "VcsKind",
    "GitStatusOpts",
    "GitStatus",
    "GitDiffArgs",
    "GitDiff",
    "GitBranchInfo",
    "GitMetadata",
    "RipgrepArgs",
    "MatchSpan",
    "ContentMatch",
    "RipgrepStats",
    "FuzzySearchArgs",
    "FuzzyMatch",
    "UserQuestionOption",
    "UserQuestion",
    "UserAnswer",
    "ToolOutputChunk",
    "ToolProgress",
    "ToolCallResult",
    "ToolDef",
    "AgentSessionInfo",
    "RewindResult",
    "RewindPoint",
    "FsEventKind",
    "ServerStatus",
    "LspServerStatus",
    "McpServerStatus",
    "PlanModeTransition",
    "PlanModeDecision",
    "Hunk",
    "HunkAction",
    "PermissionRequest",
    "PermissionDecision",
    "PluginInfo",
    "HookInfo",
    "FileReference",
    "ResolvedFile",
    "SkillInfo",
    "MemoryChunk",
]
