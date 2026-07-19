"""Workspace RPC request/response foundation (R68).

Fusion of grok's ``xai-grok-workspace-types::rpc`` root module — the
:class:`WorkspaceRpc` marker protocol, the workspace tool-ID constants,
and the module barrel. This is the foundation layer consuming the R67
leaf types: every ``workspace.*`` method is a struct implementing
:class:`WorkspaceRpc` (same struct on client + server), wrapped in an
:class:`RpcEnvelope` response on the wire.

R68 lands this foundation plus the two smallest business RPCs
(:mod:`session`, :mod:`agents_md`). The remaining 10 RPC files
(fs / git / hooks / hunks / search / skills / workspace / worktree /
code_nav / deploy) land in R69+.
"""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

from minimax_code.workspace_types.rpc.agents_md import AgentConfigFile, DiscoverAgentsMdReq
from minimax_code.workspace_types.rpc.envelope import TURN_ACTIVE, RpcEnvelope, RpcError
from minimax_code.workspace_types.rpc.session import (
    BeginPromptReq,
    ConflictType,
    EndPromptReq,
    FileRewindConflict,
    FileRewindResponse,
    RewindToReq,
)

__all__ = [
    # tool IDs (mod.rs)
    "WORKSPACE_RPC_TOOL_ID",
    "WORKSPACE_EVENTS_TOOL_ID",
    "WORKSPACE_TOOL_NOTIFICATIONS_TOOL_ID",
    "WORKSPACE_CLIENT_EXT_NOTIFICATIONS_TOOL_ID",
    # protocol
    "WorkspaceRpc",
    # envelope
    "TURN_ACTIVE",
    "RpcEnvelope",
    "RpcError",
    # session
    "BeginPromptReq",
    "EndPromptReq",
    "RewindToReq",
    "ConflictType",
    "FileRewindConflict",
    "FileRewindResponse",
    # agents_md
    "DiscoverAgentsMdReq",
    "AgentConfigFile",
]

#: Tool ID for the ``WorkspaceRpcHandler`` (workspace method dispatch).
WORKSPACE_RPC_TOOL_ID = "workspace_rpc"

#: Tool ID used for ``WorkspaceEvent`` notification frames.
WORKSPACE_EVENTS_TOOL_ID = "workspace_events"

#: Tool ID used for ``ToolNotification`` forwarding frames.
WORKSPACE_TOOL_NOTIFICATIONS_TOOL_ID = "workspace_tool_notifications"

#: Tool ID used for workspace-originated client ext-notification frames
#: (e.g. ``x.ai/search/fuzzy/status``). Carries ``{method, params}``.
WORKSPACE_CLIENT_EXT_NOTIFICATIONS_TOOL_ID = "workspace_client_ext_notifications"


@runtime_checkable
class WorkspaceRpc(Protocol):
    """Marker protocol for typed workspace RPC requests.

    Mirrors Rust's ``WorkspaceRpc`` trait: client and server share the same
    struct for the same method. ``METHOD`` is the wire method name; the
    associated ``type Response`` is carried as a ``Response`` ClassVar on
    each concrete struct (e.g. ``BeginPromptReq.Response is type(None)``,
    ``RewindToReq.Response is FileRewindResponse``). Rust's
    ``Response: Serialize + DeserializeOwned + Send`` bound is the
    pydantic-serialisable type stored in that ClassVar.
    """

    METHOD: ClassVar[str]
