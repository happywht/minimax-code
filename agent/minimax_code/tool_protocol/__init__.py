"""xAI Computer Hub wire-protocol types (R82 — foundation layer).

Fusion of grok-build's ``xai-tool-protocol`` crate — the wire DTOs for
the computer-hub tool-server protocol: identifier newtypes, registration
payloads, capabilities, hook events, handshake messages, the JSON-RPC 2.0
envelope and method catalog, the ``ToolErrorWire`` / ``ToolOutputWire`` /
``WireToolNotification`` wire enums, every method's ``params`` / ``result``
payload struct, and the numeric ↔ string error-code mapping.

This package hosts the **pure wire types** — no I/O, no env reads, no
dependency on the rest of the agent. The crate is large (6613 lines, 16
modules); R82 lands the dependency-free **foundation layer** (4 modules),
and later rounds expand outward:

* **R82 (this round)** — :mod:`ids` (8 identifier newtypes +
  :class:`IdError`), :mod:`connection` (``ConnectionKind`` +
  ``ToolDefinitionMode``), :mod:`handshake` (``PROTOCOL_VERSION`` +
  ``HelloMsg`` / ``HelloAckMsg``), :mod:`error_codes` (the
  ``ERROR_CODES`` table + lookup helpers + workspace-unavailable
  contract + two ``#[serde(other)]``-tolerant enums).
* *deferred* — :mod:`envelope` (JSON-RPC 2.0 request/response/notification),
  :mod:`methods` (method catalog), :mod:`capabilities`,
  :mod:`registration`, :mod:`frames` (tool-server frame protocol, 1549
  lines), :mod:`session_event`, :mod:`turn_hook`, :mod:`error_wire` /
  :mod:`output_wire` / :mod:`notification_wire` (the three wire enums),
  :mod:`hook`, :mod:`registry_error`.

Mirrors the Rust ``lib.rs`` ``pub use`` re-exports (R82 subset).
"""

from __future__ import annotations

from minimax_code.tool_protocol.connection import (
    ConnectionKind,
    ToolDefinitionMode,
)
from minimax_code.tool_protocol.error_codes import (
    ERROR_CODES,
    WORKSPACE_UNAVAILABLE_JSONRPC_CODE,
    WORKSPACE_UNAVAILABLE_MESSAGE,
    WORKSPACE_UNAVAILABLE_SUBCODE,
    WorkspaceGonePhase,
    WorkspaceGoneReason,
    WorkspaceUnavailableDetails,
    numeric_for,
    string_for,
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
    # handshake (R82)
    "PROTOCOL_VERSION",
    "HelloMsg",
    "HelloAckMsg",
    # error_codes (R82)
    "ERROR_CODES",
    "numeric_for",
    "string_for",
    "WORKSPACE_UNAVAILABLE_SUBCODE",
    "WORKSPACE_UNAVAILABLE_MESSAGE",
    "WORKSPACE_UNAVAILABLE_JSONRPC_CODE",
    "WorkspaceGoneReason",
    "WorkspaceGonePhase",
    "WorkspaceUnavailableDetails",
]
