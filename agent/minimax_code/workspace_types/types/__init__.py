"""Workspace leaf wire types — types facade (R67).

Re-exports the public surface of every ``types/`` submodule so callers
can do ``from minimax_code.workspace_types.types import GitStatus``.
Mirrors the Rust crate's ``pub use types::*`` glob re-export.
"""

from __future__ import annotations

from minimax_code.workspace_types.types.config import (
    AgentSessionConfig,
    CapabilityMode,
    IsolationMode,
    PermissionPolicy,
    ProjectConfig,
    ToolServerConfig,
)
from minimax_code.workspace_types.types.files import FileReference, ResolvedFile
from minimax_code.workspace_types.types.git import (
    GitBranchInfo,
    GitDiff,
    GitDiffArgs,
    GitMetadata,
    GitStatus,
    GitStatusOpts,
    VcsKind,
)
from minimax_code.workspace_types.types.hunk import Hunk, HunkAction
from minimax_code.workspace_types.types.interaction import (
    UserAnswer,
    UserQuestion,
    UserQuestionOption,
)
from minimax_code.workspace_types.types.memory import MemoryChunk
from minimax_code.workspace_types.types.permission import PermissionDecision, PermissionRequest
from minimax_code.workspace_types.types.plan_mode import PlanModeDecision, PlanModeTransition
from minimax_code.workspace_types.types.plugins import HookInfo, PluginInfo
from minimax_code.workspace_types.types.search import (
    ContentMatch,
    FuzzyMatch,
    FuzzySearchArgs,
    MatchSpan,
    RipgrepArgs,
    RipgrepStats,
)
from minimax_code.workspace_types.types.session import (
    AgentSessionInfo,
    FsEventKind,
    LspServerStatus,
    McpServerStatus,
    RewindPoint,
    RewindResult,
    ServerStatus,
)
from minimax_code.workspace_types.types.skills import SkillInfo
from minimax_code.workspace_types.types.tools import (
    ToolCallResult,
    ToolDef,
    ToolOutputChunk,
    ToolProgress,
)

__all__ = [
    # config
    "IsolationMode",
    "CapabilityMode",
    "ToolServerConfig",
    "AgentSessionConfig",
    "ProjectConfig",
    "PermissionPolicy",
    # git
    "VcsKind",
    "GitStatusOpts",
    "GitStatus",
    "GitDiffArgs",
    "GitDiff",
    "GitBranchInfo",
    "GitMetadata",
    # search
    "RipgrepArgs",
    "MatchSpan",
    "ContentMatch",
    "RipgrepStats",
    "FuzzySearchArgs",
    "FuzzyMatch",
    # interaction
    "UserQuestionOption",
    "UserQuestion",
    "UserAnswer",
    # tools
    "ToolOutputChunk",
    "ToolProgress",
    "ToolCallResult",
    "ToolDef",
    # session
    "AgentSessionInfo",
    "RewindResult",
    "RewindPoint",
    "FsEventKind",
    "ServerStatus",
    "LspServerStatus",
    "McpServerStatus",
    # plan_mode
    "PlanModeTransition",
    "PlanModeDecision",
    # hunk
    "Hunk",
    "HunkAction",
    # permission
    "PermissionRequest",
    "PermissionDecision",
    # plugins
    "PluginInfo",
    "HookInfo",
    # files
    "FileReference",
    "ResolvedFile",
    # skills
    "SkillInfo",
    # memory
    "MemoryChunk",
]
