"""Workspace metadata / config / session-admin RPCs (R72).

Fusion of grok's ``xai-grok-workspace-types::rpc::workspace`` — 14
``workspace.*`` methods covering environment info, config loading, toolset
admin, plugin management, and background-task / TODO listing. This file
lands three serde patterns new to the RPC layer:

* **``skip_serializing_if = "String::is_empty"``** — the deprecated
  ``caller_session_id`` field on :class:`UpdateToolConfigReq` /
  :class:`DropSessionReq` is omitted from the wire when empty, so a typed
  client that leaves the default never sends a self-attested ``""`` (the
  server derives the caller from the hub-bound envelope session and only
  falls back to this field on old call paths). Distinct from R71's
  ``#[serde(skip)]`` (the field does not exist on the wire at all) and R70's
  ``Option::is_none`` (``None`` elision): here a *non-optional* ``str`` is
  elided specifically when it equals the empty string. Implemented via a
  shared :class:`_OmitsEmptyCallerSessionId` mixin with a ``mode="wrap"``
  :func:`model_serializer` that strips the key from the default dump.
* **bulk ``Response = serde_json::Value``** — 11 of the 14 methods carry
  arbitrary-JSON responses (server-defined shapes this crate deliberately
  does not type), all surfaced as ``Response: ClassVar = Any``.
* **typed shape alongside a raw ``Value`` response** — :class:`WorkspaceInfo`
  is the typed shape of the ``workspace.info`` raw ``Value``, but
  ``WorkspaceInfoReq.Response`` stays ``Any`` (the wire preserves the raw
  contract); clients ``model_validate`` into :class:`WorkspaceInfo` when
  they want the ``os`` / ``shell`` / ``cwd`` fields.

The two list responses (:class:`ListBackgroundTasksResponse`,
:class:`ListTodosResponse`) are the only non-``Value`` responses and carry
their own typed wire structs (:class:`BackgroundTaskSummaryWire`,
:class:`TodoSummaryWire`).
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field, model_serializer

from minimax_code.workspace_types._wire import WireModel

__all__ = [
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


class WorkspaceInfo(WireModel):
    """Typed shape of the ``workspace.info`` raw ``Value`` response.

    ``os`` is ``std::env::consts::OS`` on the server (e.g. ``"linux"``);
    ``shell`` is the shell basename (``"sh"`` when ``$SHELL`` is unset);
    ``cwd`` is the workspace root. The request's ``Response`` stays raw
    ``Any`` (the wire contract preserves the untyped value); this struct
    exists for clients that want the fields via ``model_validate``. SYNC:
    matches the object built by the ``workspace.info`` dispatch arm in
    ``xai-grok-workspace/src/hub_server.rs``. Does **not** derive ``Default``
    in the source (all three fields required).
    """

    os: str
    shell: str
    cwd: str


class BackgroundTaskSummaryWire(WireModel):
    """One outstanding background terminal command. snake_case on wire.

    A slim, dependency-free DTO over ``xai_grok_tools``'s ``TaskSnapshot``.
    ``command`` prefers the task's ``display_command``. ``tool_name``, when
    set, is the model-facing name of the tool that created the task; it
    carries ``#[serde(default, skip_serializing_if = "Option::is_none")]`` —
    omitted when ``None`` at any nesting depth (a plain ``model_serializer``
    hand-builds the dict, mirroring R70's :class:`ContentMatch`).
    """

    task_id: str
    command: str
    tool_name: str | None = None

    @model_serializer
    def _to_wire_dict(self) -> dict[str, Any]:
        # Hand-build snake_case keys, dropping tool_name when None —
        # reproducing #[serde(skip_serializing_if = "Option::is_none")] at
        # every nesting depth (sort_mappings re-sorts keys in to_wire).
        out: dict[str, Any] = {"task_id": self.task_id, "command": self.command}
        if self.tool_name is not None:
            out["tool_name"] = self.tool_name
        return out

    @classmethod
    def default(cls) -> BackgroundTaskSummaryWire:
        # `#[derive(Default)]`: task_id → "", command → "", tool_name → None.
        return cls(task_id="", command="")


class ListBackgroundTasksResponse(WireModel):
    """Response of ``workspace.list_background_tasks``. Derives Default."""

    tasks: list[BackgroundTaskSummaryWire] = Field(default_factory=list)


class TodoSummaryWire(WireModel):
    """One TODO list item. snake_case on wire.

    A slim DTO over ``xai_grok_tools``'s ``TodoState``. ``status`` is the
    snake_case tag: ``pending`` | ``in_progress`` | ``completed`` |
    ``cancelled``.
    """

    id: str
    content: str
    status: str

    @classmethod
    def default(cls) -> TodoSummaryWire:
        # `#[derive(Default)]`: all three String fields → "".
        return cls(id="", content="", status="")


class ListTodosResponse(WireModel):
    """Response of ``workspace.list_todos``. Derives Default."""

    todos: list[TodoSummaryWire] = Field(default_factory=list)


class _OmitsEmptyCallerSessionId(WireModel):
    """``caller_session_id`` elision mixin (R72).

    Both :class:`UpdateToolConfigReq` and :class:`DropSessionReq` carry a
    deprecated ``caller_session_id: String`` with ``#[serde(default,
    skip_serializing_if = "String::is_empty")]``: empty means absent, so a
    typed client that leaves the default never sends a self-attested ``""``
    (the server derives the caller from the hub-bound envelope session and
    only falls back to this field on old call paths; it also filters empty
    to absent for old serializers). This wrap :func:`model_serializer`
    strips the key from the default wire dump when its value is the empty
    string — distinct from R71's ``#[serde(skip)]`` (field absent entirely)
    and R70's ``Option::is_none`` (``None`` elision): here a *non-optional*
    ``str`` is elided specifically when empty.
    """

    caller_session_id: str = ""

    @model_serializer(mode="wrap")
    def _omit_empty_caller(self, handler):  # noqa: ANN001, ANN202
        # handler(self) returns the default JSON-safe dump (all fields,
        # including subclass fields); drop caller_session_id when empty.
        raw = handler(self)
        if raw.get("caller_session_id") == "":
            raw.pop("caller_session_id", None)
        return raw


# -- requests (empty-parameter; Response = Value / Any) -------------------


class WorkspaceInfoReq(WireModel):
    """``workspace.info`` — workspace environment info.

    No parameters (empty struct). ``Response = serde_json::Value`` (the raw
    server object; typed shape is :class:`WorkspaceInfo`).
    """

    METHOD: ClassVar[str] = "workspace.info"
    Response: ClassVar = Any  # serde_json::Value; typed shape is WorkspaceInfo


class LoadProjectConfigReq(WireModel):
    """``workspace.load_project_config`` — project config at the root.

    No parameters. ``Response = serde_json::Value``.
    """

    METHOD: ClassVar[str] = "workspace.load_project_config"
    Response: ClassVar = Any


class LoadPermissionsReq(WireModel):
    """``workspace.load_permissions`` — permission settings at the root.

    No parameters. ``Response = serde_json::Value``.
    """

    METHOD: ClassVar[str] = "workspace.load_permissions"
    Response: ClassVar = Any


class LoadEnvrcReq(WireModel):
    """``workspace.load_envrc`` — ``.envrc`` at the root (empty when absent).

    No parameters. ``Response = serde_json::Value``.
    """

    METHOD: ClassVar[str] = "workspace.load_envrc"
    Response: ClassVar = Any


class ToolDefinitionsReq(WireModel):
    """``workspace.tool_definitions`` — a session's finalized toolset.

    ``Response = serde_json::Value``. Derives ``Default`` → ``session_id`` "".
    """

    METHOD: ClassVar[str] = "workspace.tool_definitions"
    Response: ClassVar = Any

    session_id: str

    @classmethod
    def default(cls) -> ToolDefinitionsReq:
        return cls(session_id="")


class ResolveFileReferencesReq(WireModel):
    """``workspace.resolve_file_references`` — resolve ``@file`` refs.

    ``Response = serde_json::Value``. Derives ``Default`` → ``refs`` ``[]``.
    """

    METHOD: ClassVar[str] = "workspace.resolve_file_references"
    Response: ClassVar = Any

    refs: list[str]

    @classmethod
    def default(cls) -> ResolveFileReferencesReq:
        return cls(refs=[])


class UpdateToolConfigReq(_OmitsEmptyCallerSessionId):
    """``workspace.update_tool_config`` — replace a session's tool config.

    Rejected with the retryable :data:`~minimax_code.workspace_types.rpc.TURN_ACTIVE`
    wire code while the target session has an active turn and the new config
    differs; retry at the turn boundary. ``caller_session_id`` is deprecated
    (self-attested) and elided when empty (see the mixin). ``new_config`` is
    raw JSON (the tool-config shape). ``Response = serde_json::Value``.
    """

    METHOD: ClassVar[str] = "workspace.update_tool_config"
    Response: ClassVar = Any

    session_id: str
    new_config: Any

    @classmethod
    def default(cls) -> UpdateToolConfigReq:
        # `#[derive(Default)]`: caller_session_id → "", session_id → "",
        # new_config → Value::Null (None).
        return cls(caller_session_id="", session_id="", new_config=None)


class DropSessionReq(_OmitsEmptyCallerSessionId):
    """``workspace.drop_session`` — drop a workspace session.

    ``caller_session_id`` is deprecated (self-attested) and elided when empty
    (see the mixin). ``Response = serde_json::Value``.
    """

    METHOD: ClassVar[str] = "workspace.drop_session"
    Response: ClassVar = Any

    session_id: str

    @classmethod
    def default(cls) -> DropSessionReq:
        return cls(caller_session_id="", session_id="")


class ConfigureMcpReq(WireModel):
    """``workspace.configure_mcp`` — start MCP servers for the caller's session.

    ``mcp_servers`` stays raw JSON (the shape is the ACP ``McpServer`` list)
    so this crate carries no ``agent-client-protocol`` dependency.
    ``Response = serde_json::Value``.
    """

    METHOD: ClassVar[str] = "workspace.configure_mcp"
    Response: ClassVar = Any

    mcp_servers: Any

    @classmethod
    def default(cls) -> ConfigureMcpReq:
        # `#[derive(Default)]`: mcp_servers → Value::Null (None).
        return cls(mcp_servers=None)


class InstallPluginReq(WireModel):
    """``workspace.install_plugin`` — no-op on the server (returns ``null``).

    Installation needs shell-side auth + registry. No parameters.
    ``Response = serde_json::Value``.
    """

    METHOD: ClassVar[str] = "workspace.install_plugin"
    Response: ClassVar = Any


class RefreshPluginsReq(WireModel):
    """``workspace.refresh_plugins`` — re-discover plugins at the root.

    No parameters. ``Response = serde_json::Value``.
    """

    METHOD: ClassVar[str] = "workspace.refresh_plugins"
    Response: ClassVar = Any


class ListBackgroundTasksReq(WireModel):
    """``workspace.list_background_tasks`` — outstanding background commands.

    Lists the not-completed background terminal tasks for ``session_id`` for
    post-compaction ``<system-reminder>`` state. ``WorkspaceClient`` is
    session-agnostic, so the caller supplies the hub-bound session id.
    ``Response = ListBackgroundTasksResponse``. Derives ``Default`` →
    ``session_id`` "".
    """

    METHOD: ClassVar[str] = "workspace.list_background_tasks"
    Response: ClassVar[type] = ListBackgroundTasksResponse

    session_id: str

    @classmethod
    def default(cls) -> ListBackgroundTasksReq:
        return cls(session_id="")


class ListTodosReq(WireModel):
    """``workspace.list_todos`` — list the session's TODO items.

    For post-compaction ``<system-reminder>`` state; the caller supplies the
    hub-bound session id. ``Response = ListTodosResponse``. Derives
    ``Default`` → ``session_id`` "".
    """

    METHOD: ClassVar[str] = "workspace.list_todos"
    Response: ClassVar[type] = ListTodosResponse

    session_id: str

    @classmethod
    def default(cls) -> ListTodosReq:
        return cls(session_id="")
