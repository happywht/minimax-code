"""xAI Computer Hub wire-protocol types (R82 + R83 + R84 + R85 — methods landed).

Fusion of grok-build's ``xai-tool-protocol`` crate — the wire DTOs for
the computer-hub tool-server protocol: identifier newtypes, registration
payloads, capabilities, hook events, handshake messages, the JSON-RPC 2.0
envelope and method catalog, the ``ToolErrorWire`` / ``ToolOutputWire`` /
``WireToolNotification`` wire enums, every method's ``params`` / ``result``
payload struct, and the numeric ↔ string error-code mapping.

This package hosts the **pure wire types** — no I/O, no env reads, no
dependency on the rest of the agent. The crate is large (6613 lines, 16
modules); R82 lands the dependency-free **foundation layer**, R83 lands
the three wire enums + the two ``error_codes`` helpers they unblock, R84
lands the JSON-RPC 2.0 envelope, and R85 lands the method catalog (the
direct consumer of the envelope's ``method`` field):

* **R82** — :mod:`ids` (8 identifier newtypes + :class:`IdError`),
  :mod:`connection` (``ConnectionKind`` + ``ToolDefinitionMode``),
  :mod:`handshake` (``PROTOCOL_VERSION`` + ``HelloMsg`` /
  ``HelloAckMsg``), :mod:`error_codes` (the ``ERROR_CODES`` table +
  lookup helpers + workspace-unavailable contract + two
  ``#[serde(other)]``-tolerant enums).
* **R83** — :mod:`error_wire` (``ToolErrorWire``, the
  15-variant internally-tagged enum on ``code``), :mod:`output_wire`
  (``ToolOutputWire`` adjacent-tagged on ``kind``/``value`` + ``McpBlock``
  internally-tagged on ``type``), :mod:`notification_wire`
  (``WireToolNotification`` adjacent-tagged on ``shape``/``value`` + the
  forward-compat ``Custom`` with spoof-resistant
  :func:`~error_codes.check_custom_kind`); plus the R82-deferred
  ``from_tool_error_wire`` / ``workspace_unavailable_wire`` helpers in
  :mod:`error_codes` (they depend on ``ToolErrorWire`` and so return
  here to close the gap).
* **R84** — :mod:`envelope` (JSON-RPC 2.0
  request/response/notification/error wrappers + the strict ``"2.0"``
  protocol-version marker + :data:`JsonRpcId`, the crate's first
  ``#[serde(untagged)]`` enum — closing the four-shape serde coverage:
  internal-tag / adjacent-tag / untagged / transparent-newtype; plus the
  ``result`` XOR ``error`` response invariant enforced via custom serde).
* **R85 (this round)** — :mod:`methods` (the closed enumeration of every
  JSON-RPC method on the wire, defined once from a single source of
  truth; lands as :class:`~enum.StrEnum` whose member values are the wire
  strings, plus :meth:`Method.as_wire_str` / :meth:`Method.from_wire_str`
  / :data:`Method.ALL` and the fleet-compat-pinned
  :data:`UNKNOWN_METHOD_MSG_PREFIX`; the direct consumer of
  :mod:`envelope`'s ``method`` field).
* *deferred* — :mod:`capabilities`, :mod:`registration`, :mod:`frames`
  (tool-server frame protocol, 1549 lines), :mod:`session_event`,
  :mod:`turn_hook`, :mod:`hook`, :mod:`registry_error`.

``from_wire`` / ``to_wire`` live on each module (instance method or module
function); the barrel does **not** re-export them, mirroring the Rust
``lib.rs`` ``pub use`` set which exports the type names and the
``check_custom_kind`` / ``known_notification_kinds`` helpers but not the
conversions. Callers reach the wire converters via the submodules
(``tool_protocol.error_wire.from_wire`` etc.).
"""

from __future__ import annotations

from minimax_code.tool_protocol.connection import (
    ConnectionKind,
    ToolDefinitionMode,
)
from minimax_code.tool_protocol.envelope import (
    JsonRpcError,
    JsonRpcId,
    JsonRpcIdNumber,
    JsonRpcIdString,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcVersion,
    JsonRpcVersionError,
    ResponseError,
    ResponseOutcome,
    ResponseResult,
)
from minimax_code.tool_protocol.error_codes import (
    ERROR_CODES,
    WORKSPACE_UNAVAILABLE_JSONRPC_CODE,
    WORKSPACE_UNAVAILABLE_MESSAGE,
    WORKSPACE_UNAVAILABLE_SUBCODE,
    WorkspaceGonePhase,
    WorkspaceGoneReason,
    WorkspaceUnavailableDetails,
    from_tool_error_wire,
    numeric_for,
    string_for,
    workspace_unavailable_wire,
)
from minimax_code.tool_protocol.error_wire import (
    BehaviorVersionUnsupported,
    Cancelled,
    Custom,
    Execution,
    Internal,
    InvalidArguments,
    PayloadTooLarge,
    PermissionDenied,
    RenderLimited,
    SessionMismatch,
    TerminalError,
    Timeout,
    ToolErrorWire,
    ToolNotFound,
    TransportClosed,
    UnsupportedProtocolVersion,
)
from minimax_code.tool_protocol.handshake import (
    PROTOCOL_VERSION,
    HelloAckMsg,
    HelloMsg,
)
from minimax_code.tool_protocol.ids import (
    ConnectionId,
    EmptyIdError,
    FrameSeq,
    IdError,
    InvalidFormatIdError,
    RequestId,
    ReservedPrefixIdError,
    ServerId,
    SessionId,
    ToolCallId,
    ToolId,
    UserId,
)
from minimax_code.tool_protocol.methods import (
    UNKNOWN_METHOD_MSG_PREFIX,
    Method,
)
from minimax_code.tool_protocol.notification_wire import (
    KNOWN_NOTIFICATION_KINDS,
    KnownVariantCollision,
    WireCustomNotification,
    WireToolNotification,
    check_custom_kind,
    known_notification_kinds,
)
from minimax_code.tool_protocol.output_wire import (
    ImageBlock,
    Json,
    Mcp,
    McpBlock,
    ResourceBlock,
    Text,
    TextBlock,
    ToolOutputWire,
)

__all__ = [
    # ids (R82)
    "IdError",
    "EmptyIdError",
    "InvalidFormatIdError",
    "ReservedPrefixIdError",
    "SessionId",
    "UserId",
    "ConnectionId",
    "RequestId",
    "ToolCallId",
    "ServerId",
    "ToolId",
    "FrameSeq",
    # connection (R82)
    "ConnectionKind",
    "ToolDefinitionMode",
    # envelope (R84)
    "JsonRpcVersion",
    "JsonRpcVersionError",
    "JsonRpcId",
    "JsonRpcIdString",
    "JsonRpcIdNumber",
    "JsonRpcRequest",
    "JsonRpcNotification",
    "JsonRpcError",
    "JsonRpcResponse",
    "ResponseOutcome",
    "ResponseResult",
    "ResponseError",
    # methods (R85)
    "Method",
    "UNKNOWN_METHOD_MSG_PREFIX",
    # handshake (R82)
    "PROTOCOL_VERSION",
    "HelloMsg",
    "HelloAckMsg",
    # error_codes (R82 + R83 backfill)
    "ERROR_CODES",
    "numeric_for",
    "string_for",
    "from_tool_error_wire",
    "WORKSPACE_UNAVAILABLE_SUBCODE",
    "WORKSPACE_UNAVAILABLE_MESSAGE",
    "WORKSPACE_UNAVAILABLE_JSONRPC_CODE",
    "WorkspaceGoneReason",
    "WorkspaceGonePhase",
    "WorkspaceUnavailableDetails",
    "workspace_unavailable_wire",
    # error_wire (R83)
    "ToolErrorWire",
    "ToolNotFound",
    "SessionMismatch",
    "PermissionDenied",
    "TransportClosed",
    "Timeout",
    "Cancelled",
    "InvalidArguments",
    "Execution",
    "UnsupportedProtocolVersion",
    "PayloadTooLarge",
    "BehaviorVersionUnsupported",
    "RenderLimited",
    "TerminalError",
    "Internal",
    "Custom",
    # output_wire (R83)
    "ToolOutputWire",
    "Text",
    "Json",
    "Mcp",
    "McpBlock",
    "TextBlock",
    "ImageBlock",
    "ResourceBlock",
    # notification_wire (R83)
    "WireToolNotification",
    "WireCustomNotification",
    "KnownVariantCollision",
    "KNOWN_NOTIFICATION_KINDS",
    "known_notification_kinds",
    "check_custom_kind",
]
