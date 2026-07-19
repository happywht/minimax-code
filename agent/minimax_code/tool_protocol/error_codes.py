"""Numeric ↔ string error-code mapping for the wire protocol (R82).

Fusion of grok-build's ``xai-tool-protocol::error_codes`` — the fixed
table pairing JSON-RPC numeric codes with Grok stable string identifiers,
plus the lookup helpers, the workspace-unavailable error contract, and
the two ``#[serde(other)]``-tolerant enums describing why a workspace
(tool) server went away.

Receivers SHOULD switch on ``data.code`` (the snake_case string) rather
than the numeric JSON-RPC ``error.code``: the numeric is the JSON-RPC
envelope code; the string is the Grok stable identifier.

YAGNI boundary
--------------

``from_tool_error_wire`` and ``workspace_unavailable_wire`` (the two
helpers that build / classify a :class:`ToolErrorWire`) are deferred:
they depend on ``error_wire::ToolErrorWire`` (a later round in this
crate). The pieces they compose from — the table, the lookup helpers,
the workspace-unavailable constants and the two tolerant enums plus
:class:`WorkspaceUnavailableDetails` — land here so the later round
wires them up without re-touching the contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
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


#: ``(numeric_code, string_code)`` pairs (``error_codes::ERROR_CODES``). Both
#: columns are unique. Implemented as a small fixed tuple of pairs; the set
#: is small enough that a linear scan is faster than any
#: ``HashMap``-shaped alternative (the Rust rationale).
ERROR_CODES: Sequence[tuple[int, str]] = (
    (-32700, "parse_error"),
    (-32600, "invalid_request"),
    (-32601, "method_not_found"),
    (-32602, "invalid_params"),
    (-32603, "internal_error"),
    (-32605, "unsupported_protocol_version"),
    (-32001, "timeout"),
    (-32002, "unauthorized"),
    (-32003, "forbidden"),
    (-32004, "connection_lost"),
    (-32005, "tool_server_gone"),
    (-32006, "session_not_found"),
    (-32008, "session_draining"),
    (-32011, "tool_not_found"),
    (-32012, "tool_already_registered"),
    (-32013, "tool_unavailable"),
    (-32014, "stale_generation"),
    (-32015, "duplicate_client_name"),
    (-32016, "tool_busy"),
    (-32017, "notification_schema_violation"),
    (-32018, "frame_too_large"),
    (-32019, "schema_unknown_kind"),
    (-32020, "behavior_version_unsupported"),
    (-32021, "server_id_in_use"),
    (-32022, "invalid_description"),
    (-32023, "render_limited"),
    (-32024, "terminal_error"),
    (-32099, "rate_limited"),
)


def numeric_for(code_str: str) -> int | None:
    """Numeric JSON-RPC code for a Grok string code (``numeric_for``).

    Returns ``None`` for strings not in the table; receivers should fall
    back to ``-32603 internal_error`` for unknown strings.
    """
    for numeric, s in ERROR_CODES:
        if s == code_str:
            return numeric
    return None


def string_for(code: int) -> str | None:
    """Grok string code for a numeric JSON-RPC code (``string_for``).

    Returns ``None`` for codes not in the table.
    """
    for numeric, s in ERROR_CODES:
        if numeric == code:
            return s
    return None


# -----------------------------------------------------------------------
# Workspace-unavailable contract (``WORKSPACE_UNAVAILABLE_*`` constants).
# -----------------------------------------------------------------------


#: Stable identifier for "this session's workspace (tool) server is gone;
#: re-provision and retry" — used as both the ``Custom`` subcode and the
#: ``details["code"]`` value. Reusing ``Custom`` (not a new variant) keeps
#: the frame deserialisable on older peers.
WORKSPACE_UNAVAILABLE_SUBCODE: str = "workspace_unavailable"

#: Generic, tenant-data-free message paired with the workspace-gone error.
WORKSPACE_UNAVAILABLE_MESSAGE: str = "workspace server gone; re-provision and retry"

#: JSON-RPC envelope code paired with the workspace-unavailable error.
#: Shares the canonical ``tool_server_gone`` numeric; recognizers key on
#: ``data.subcode``, not this companion.
WORKSPACE_UNAVAILABLE_JSONRPC_CODE: int = -32005


class WorkspaceGoneReason(StrEnum):
    """Why the workspace (tool) server went away (``error_codes::WorkspaceGoneReason``).

    ``#[serde(rename_all = "snake_case")]`` with ``#[serde(other)]`` on
    :attr:`Unknown`: a value a newer peer emits that this build does not
    know is absorbed into :attr:`Unknown` rather than failing the typed
    parse. :meth:`from_wire` reproduces that catch-all.
    """

    IdleTimeout = "idle_timeout"
    Disconnect = "disconnect"
    Shutdown = "shutdown"
    #: No owner has bound a tool-server for the session yet (an attach-time
    #: miss), as opposed to a workspace that was bound and then lost.
    NotBound = "not_bound"
    #: Target hub liveness key absent (origin reaper or forward-time check).
    InstanceGone = "instance_gone"
    #: Absorbs values a newer peer may add (the ``#[serde(other)]`` arm).
    Unknown = "unknown"

    @classmethod
    def from_wire(cls, value: str) -> WorkspaceGoneReason:
        """Parse with the ``#[serde(other)]`` catch-all into :attr:`Unknown`.

        ``"unknown"`` itself is a known value, so it round-trips through
        the normal lookup; any other unknown string falls back to
        :attr:`Unknown`.
        """
        try:
            return cls(value)
        except ValueError:
            return cls.Unknown


class WorkspaceGonePhase(StrEnum):
    """When, relative to the failing call, the loss was observed (``WorkspaceGonePhase``).

    Same ``#[serde(other)]`` tolerance as :class:`WorkspaceGoneReason`.
    """

    InFlightCancelled = "in_flight_cancelled"
    RouteMissing = "route_missing"
    #: Observed while resolving a ``session_attach_server`` request.
    Attach = "attach"
    #: Absorbs values a newer peer may add (the ``#[serde(other)]`` arm).
    Unknown = "unknown"

    @classmethod
    def from_wire(cls, value: str) -> WorkspaceGonePhase:
        """Parse with the ``#[serde(other)]`` catch-all into :attr:`Unknown`."""
        try:
            return cls(value)
        except ValueError:
            return cls.Unknown


@dataclass
class WorkspaceUnavailableDetails:
    """Structured payload in the wire ``details`` object (``WorkspaceUnavailableDetails``).

    ``code`` mirrors the ``Custom`` subcode (the ``ToolError::custom``
    convention), so it survives a ``Wire → ToolError → Wire`` round-trip
    and is the field recognizers read.
    """

    code: str
    reason: WorkspaceGoneReason
    phase: WorkspaceGonePhase
    retryable: bool

    def to_wire(self) -> dict[str, object]:
        """Serialise the four fields (reason/phase as their snake_case wire strings)."""
        return {
            "code": self.code,
            "reason": self.reason.value,
            "phase": self.phase.value,
            "retryable": bool(self.retryable),
        }

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> WorkspaceUnavailableDetails:
        """Reconstruct; reason/phase use the tolerant ``from_wire`` catch-all."""
        return cls(
            code=str(data["code"]),
            reason=WorkspaceGoneReason.from_wire(str(data["reason"])),
            phase=WorkspaceGonePhase.from_wire(str(data["phase"])),
            retryable=bool(data["retryable"]),
        )
