"""Workspace RPC request/response foundation (R68).

Fusion of grok's ``xai-grok-workspace-types::rpc`` root module — the
:class:`WorkspaceRpc` marker protocol, the workspace tool-ID constants,
and the module barrel. This is the foundation layer consuming the R67
leaf types: every ``workspace.*`` method is a struct implementing
:class:`WorkspaceRpc` (same struct on client + server), wrapped in an
:class:`RpcEnvelope` response on the wire.

R68 lands the foundation (envelope + protocol + 4 tool IDs) plus the two
smallest business RPCs (:mod:`session`, :mod:`agents_md`). R69 adds the
envelope's two consumer sides: :mod:`code_nav` (Ok side — five navigation
RPCs with nested responses) and :mod:`deploy` (Err side — the 15-code
``DeployError`` vocabulary carried in ``RpcError.code``). R70 adds
:mod:`search` — a mixed camelCase / snake_case namespace (``workspace.ripgrep``
content search + four ``workspace.fuzzy_*`` file searches) that lands four
serde patterns new to the layer (camelCase ``rename_all``, untagged
``TargetClientId`` enum, custom-default ``respect_gitignore``, primitive /
``Value`` responses). R71 adds :mod:`hooks` — the ``workspace.hook_registry``
method landing four more serde patterns new to the layer
(``#[serde(skip)]`` field elision, a forward-tolerant ``str``-subclass enum
that serves as a JSON map key, and an empty-parameter request). R72 adds
:mod:`workspace` — 14 environment / config / session-admin RPCs landing
three more serde patterns new to the layer (``skip_serializing_if =
"String::is_empty"`` non-optional elision via a wrap ``model_serializer``
mixin, bulk ``Response = serde_json::Value`` surfaced as ``Any``, and a
typed shape (:class:`WorkspaceInfo`) carried alongside a raw ``Value``
response). R73 adds :mod:`skills` — the two ``workspace.discover_*`` methods
plus the :class:`SkillScope` forward-tolerant enum and the 26-field
:class:`SkillInfo` discovery payload, landing three more serde patterns new
to the layer (a ``default = "default_true"`` bool field, bulk
``Option::is_none`` elision via a wrap ``model_serializer`` that drops every
``None``-valued key, and bare-list ``Response = Vec<Value>`` / ``Vec<SkillInfo>``
surfaced as ``list[Any]`` / ``list[SkillInfo]``). The remaining 4 RPC files
(fs / git / hunks / worktree) land in R74+.
"""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

from minimax_code.workspace_types.rpc.agents_md import AgentConfigFile, DiscoverAgentsMdReq
from minimax_code.workspace_types.rpc.code_nav import (
    CodeFindDefinitionsReq,
    CodeFindReferencesReq,
    CodeGotoDefinitionReq,
    CodeGotoReferencesReq,
    CodeIndexStats,
    CodeIndexStatusReq,
    CodeIndexStatusResponse,
    CodeNavLocation,
    CodeNavResponse,
)
from minimax_code.workspace_types.rpc.deploy import DeployError
from minimax_code.workspace_types.rpc.envelope import TURN_ACTIVE, RpcEnvelope, RpcError
from minimax_code.workspace_types.rpc.hooks import (
    HookEventNameWire,
    HookRegistryReq,
    HookRegistryWire,
    HookSpecWire,
)
from minimax_code.workspace_types.rpc.search import (
    ClientId,
    ContentMatch,
    ContentMatchFile,
    ContentSearchData,
    ContentSearchRequest,
    FuzzyChangeReq,
    FuzzyCloseReq,
    FuzzyOpenReq,
    FuzzyStatusReq,
    TargetClientId,
)
from minimax_code.workspace_types.rpc.session import (
    BeginPromptReq,
    ConflictType,
    EndPromptReq,
    FileRewindConflict,
    FileRewindResponse,
    RewindToReq,
)
from minimax_code.workspace_types.rpc.skills import (
    DiscoverPluginsReq,
    DiscoverSkillsReq,
    SkillInfo,
    SkillScope,
)
from minimax_code.workspace_types.rpc.workspace import (
    BackgroundTaskSummaryWire,
    ConfigureMcpReq,
    DropSessionReq,
    InstallPluginReq,
    ListBackgroundTasksReq,
    ListBackgroundTasksResponse,
    ListTodosReq,
    ListTodosResponse,
    LoadEnvrcReq,
    LoadPermissionsReq,
    LoadProjectConfigReq,
    RefreshPluginsReq,
    ResolveFileReferencesReq,
    TodoSummaryWire,
    ToolDefinitionsReq,
    UpdateToolConfigReq,
    WorkspaceInfo,
    WorkspaceInfoReq,
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
    # code_nav (R69, Ok side)
    "CodeGotoDefinitionReq",
    "CodeGotoReferencesReq",
    "CodeFindDefinitionsReq",
    "CodeFindReferencesReq",
    "CodeIndexStatusReq",
    "CodeNavLocation",
    "CodeNavResponse",
    "CodeIndexStats",
    "CodeIndexStatusResponse",
    # deploy (R69, Err side)
    "DeployError",
    # hooks (R71, forward-tolerant str-subclass enum + serde(skip) + map key)
    "HookEventNameWire",
    "HookRegistryReq",
    "HookRegistryWire",
    "HookSpecWire",
    # search (R70, mixed camelCase/snake_case + untagged TargetClientId)
    "ClientId",
    "TargetClientId",
    "ContentMatch",
    "ContentMatchFile",
    "ContentSearchData",
    "ContentSearchRequest",
    "FuzzyOpenReq",
    "FuzzyChangeReq",
    "FuzzyCloseReq",
    "FuzzyStatusReq",
    # skills (R73, default_true bool + bulk Option::is_none wrap elision + bare-list Response)
    "SkillScope",
    "SkillInfo",
    "DiscoverSkillsReq",
    "DiscoverPluginsReq",
    # workspace (R72, skip_serializing_if=String::is_empty + bulk Response=Value + typed shape)
    "WorkspaceInfo",
    "BackgroundTaskSummaryWire",
    "ListBackgroundTasksResponse",
    "TodoSummaryWire",
    "ListTodosResponse",
    "WorkspaceInfoReq",
    "LoadProjectConfigReq",
    "LoadPermissionsReq",
    "LoadEnvrcReq",
    "ToolDefinitionsReq",
    "ResolveFileReferencesReq",
    "UpdateToolConfigReq",
    "DropSessionReq",
    "ConfigureMcpReq",
    "InstallPluginReq",
    "RefreshPluginsReq",
    "ListBackgroundTasksReq",
    "ListTodosReq",
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
