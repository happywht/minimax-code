"""xAI Computer Hub wire-protocol types (R82 + R83 + R84 + R85 + R86 + R87 + R88 + R89 + R90 + R91 + R92 — frames opening slice landed).

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
lands the JSON-RPC 2.0 envelope, R85 lands the method catalog (the
direct consumer of the envelope's ``method`` field), R86 lands the
per-tool capabilities bitset (the type of ``hello_ack.capabilities`` and
a dependency of ``registration``), R87 lands the registration payloads
(the first wire DTO to embed a pydantic model and the home of two new serde
sub-shapes), R88 lands the registry-level error enum (the registry's
analogue of ``ToolErrorWire`` and the crate's first per-variant rename
override), and R89 lands the hook-event enum (the harness→tool payload,
the crate's first PascalCase-tagged internally-tagged enum and the first
to mix unit + struct variants):

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
* **R85** — :mod:`methods` (the closed enumeration of every
  JSON-RPC method on the wire, defined once from a single source of
  truth; lands as :class:`~enum.StrEnum` whose member values are the wire
  strings, plus :meth:`Method.as_wire_str` / :meth:`Method.from_wire_str`
  / :data:`Method.ALL` and the fleet-compat-pinned
  :data:`UNKNOWN_METHOD_MSG_PREFIX`; the direct consumer of
  :mod:`envelope`'s ``method`` field).
* **R86** — :mod:`capabilities` (the per-tool
  wire-traveling capability bitset a tool advertises:
  :class:`ToolCapabilities` (9-field conservative-default struct, the
  type of ``hello_ack.capabilities``), :class:`StreamingSpec`,
  :class:`NotificationSchemas`, and the two snake_case enums
  :class:`HookKind` / :class:`ToolScope`; the two ``bool`` fields always
  serialise even when ``False`` while the ``Option`` / ``Vec`` / ``HashMap``
  arms omit when ``None`` / empty — a fifth serde sub-shape landing here).
* **R87** — :mod:`registration` (the tool-server
  registration payloads: :class:`ToolDescriptionWithSchema`,
  :class:`ToolRegistration`, :class:`ToolServerRegistration`,
  :class:`TransportKind`, and the four :data:`RegistrationOutcome`
  variants; the crate's first wire DTO to embed a pydantic model
  (:class:`~minimax_code.tool_types.ToolDescription`) inside the
  hand-controlled ``to_wire`` layer, and the landing spot for two new
  serde sub-shapes — the three-state ``Option<Vec<SessionId>>`` session
  set and the ``skip_serializing_if = "String::is_empty"`` bare-string
  skip on :attr:`ToolServerRegistration.description`).
* **R88** — :mod:`registry_error`
  (:class:`RegistryError`, the registry's analogue of
  :class:`~error_wire.ToolErrorWire` for structural / ownership failures:
  internally-tagged on ``code`` with ``rename_all = "snake_case"`` across six
  named-field variants, and the crate's first per-variant rename override —
  :class:`AlreadyRegistered` serialises as ``"tool_already_registered"``
  rather than the ``rename_all`` default ``"already_registered"``).
* **R89** — :mod:`hook`
  (:class:`HookEvent`, the harness→tool hook-event payload: internally-tagged
  on ``type`` with **no** ``rename_all`` (the crate's first PascalCase-tagged
  internally-tagged enum — ``"Cancel"`` / ``"SessionEnded"`` / ``"Custom"``),
  four unit variants plus one struct variant (:class:`Custom`, the crate's
  first internally-tagged enum to mix unit + struct arms), and the
  forward-compatible escape hatch for unknown hook kinds via ``Custom.kind``).
* **R90 (this round)** — :mod:`session_event`
  (:class:`SessionEvent`, the session-lifecycle-event union:
  internally-tagged on ``event_type`` with ``rename_all = "snake_case"``,
  five struct variants plus one unit :class:`Unknown` catch-all — the
  crate's first ``#[serde(other)]`` forward-compat arm — and the crate's
  first field-level ``#[serde(default)]`` on :attr:`TurnStarted.yolo_mode`;
  plus :class:`ToolCallOutcome` / :class:`SessionPhase`, the first two
  ``#[serde(other)]``-tolerant string-enums) and :mod:`turn_hook`
  (minimal leaf :class:`TurnHookOutcome` + :data:`TURN_HOOK_KIND` — the
  strict no-catch-all counterpart to the tolerant
  :class:`ToolCallOutcome` / :class:`SessionPhase`, landed as a dependency
  of :class:`TurnEnded`; the rest of the 700-line module was deferred).
* **R91** — :mod:`turn_hook`
  (remainder after R90's minimal leaf: :class:`TurnHookRequest`,
  :class:`BeforeTurnPayload`, :class:`AfterTurnPayload`, :class:`InjectionRole`
  — the harness→tool turn-hook payload trio plus the injection-role enum R90's
  :class:`TurnHookOutcome` leaf depended on; closes the 700-line module).
* **R92 (this round)** — :mod:`frames`
  (opening slice of the crate's largest module — 1549 lines / 86 symbols / 14
  domains: the tool-call params/result/progress family —
  :class:`ToolCallParams`, :class:`ToolCallResult`,
  :class:`ToolCallProgressFrame`, :class:`TracesDonateParams`,
  :class:`LogsDonateParams`, :class:`MetricsDonateParams` — plus the first
  numeric ``usize`` constant family :data:`MAX_SPANS_PER_DONATION` etc.; a
  consolidation round exercising four serde sub-shapes in flat params/result
  context with no crate-first shape; all 10 symbols travel the barrel).
* *deferred* — :mod:`frames`
  (remainder: 12 of 14 domains — tool/system notifications, registration,
  per-tool session binding, server discovery+binding, list & search, session
  lifecycle, simplified lifecycle, subscriptions, hooks, service→harness
  pushes, tool-server status lifecycle, heartbeat; plus the crate's first
  non-derive custom ``impl Serialize`` on PingFrame/PongFrame).

``from_wire`` / ``to_wire`` live on each module (instance method or module
function); the barrel does **not** re-export them, mirroring the Rust
``lib.rs`` ``pub use`` set which exports the type names and the
``check_custom_kind`` / ``known_notification_kinds`` helpers but not the
conversions. Callers reach the wire converters via the submodules
(``tool_protocol.error_wire.from_wire`` etc.).
"""

from __future__ import annotations

from minimax_code.tool_protocol.capabilities import (
    HookKind,
    NotificationSchemas,
    StreamingSpec,
    ToolCapabilities,
    ToolScope,
)
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
from minimax_code.tool_protocol.frames import (
    MAX_DONATION_BYTES,
    MAX_LOG_RECORDS_PER_DONATION,
    MAX_METRICS_PER_DONATION,
    MAX_SPANS_PER_DONATION,
    LogsDonateParams,
    MetricsDonateParams,
    ToolCallParams,
    ToolCallProgressFrame,
    ToolCallResult,
    TracesDonateParams,
)
from minimax_code.tool_protocol.handshake import (
    PROTOCOL_VERSION,
    HelloAckMsg,
    HelloMsg,
)
from minimax_code.tool_protocol.hook import HookEvent
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
from minimax_code.tool_protocol.registration import (
    Registered,
    RegistrationOutcome,
    Rejected,
    Shadowed,
    ToolDescriptionWithSchema,
    ToolRegistration,
    ToolServerRegistration,
    TransportKind,
    Updated,
)
from minimax_code.tool_protocol.registry_error import (
    RegistryError,
)
from minimax_code.tool_protocol.session_event import (
    SessionEvent,
    SessionPhase,
    ToolCallOutcome,
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
    # capabilities (R86)
    "ToolCapabilities",
    "StreamingSpec",
    "HookKind",
    "ToolScope",
    "NotificationSchemas",
    # handshake (R82)
    "PROTOCOL_VERSION",
    "HelloMsg",
    "HelloAckMsg",
    # hook (R89) — only the union; variants stay in-submodule to mirror
    # Rust lib.rs `pub use hook::HookEvent` (Custom also clashes with
    # error_wire.Custom at the barrel level — same pattern as R88).
    "HookEvent",
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
    # registration (R87)
    "TransportKind",
    "ToolDescriptionWithSchema",
    "ToolRegistration",
    "ToolServerRegistration",
    "RegistrationOutcome",
    "Registered",
    "Updated",
    "Shadowed",
    "Rejected",
    # registry_error (R88) — only the union; variants stay in-submodule to
    # avoid clobbering error_wire.SessionMismatch (R83) at the barrel level,
    # mirroring Rust lib.rs `pub use registry_error::RegistryError`.
    "RegistryError",
    # session_event (R90) — union + nested enums; variants stay in-submodule
    # to mirror Rust lib.rs `pub use session_event::{SessionEvent, SessionPhase, ToolCallOutcome}`.
    # turn_hook is `pub mod` with no `pub use`, so TurnHookOutcome / TURN_HOOK_KIND
    # stay out of the barrel (module-qualified access only).
    "SessionEvent",
    "SessionPhase",
    "ToolCallOutcome",
    # frames (R92) — tool call params/result/progress + telemetry donation.
    # All 10 symbols travel the barrel (Rust lib.rs `pub use frames::{...}` is
    # the crate's largest re-export); from_wire converters stay submodule-
    # qualified, mirroring the rest of the crate.
    "MAX_DONATION_BYTES",
    "MAX_LOG_RECORDS_PER_DONATION",
    "MAX_METRICS_PER_DONATION",
    "MAX_SPANS_PER_DONATION",
    "ToolCallParams",
    "ToolCallResult",
    "ToolCallProgressFrame",
    "TracesDonateParams",
    "LogsDonateParams",
    "MetricsDonateParams",
]
