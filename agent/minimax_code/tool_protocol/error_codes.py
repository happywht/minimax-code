"""Numeric ↔ string error-code mapping for the wire protocol (R82 + R83 backfill).

Fusion of grok-build's ``xai-tool-protocol::error_codes`` — the fixed
table pairing JSON-RPC numeric codes with Grok stable string identifiers,
plus the lookup helpers, the workspace-unavailable error contract, and
the two ``#[serde(other)]``-tolerant enums describing why a workspace
(tool) server went away.

Receivers SHOULD switch on ``data.code`` (the snake_case string) rather
than the numeric JSON-RPC ``error.code``: the numeric is the JSON-RPC
envelope code; the string is the Grok stable identifier.

R83 backfill
------------

``from_tool_error_wire`` and ``workspace_unavailable_wire`` (the two
helpers that build / classify a :class:`ToolErrorWire`) were deferred in
R82 pending ``error_wire::ToolErrorWire``; R83 lands that enum and the
two helpers return here to close the gap. ``from_tool_error_wire`` maps
each variant to its most-appropriate numeric JSON-RPC code (``Custom``
always → ``-32603`` since its code string is not in the table by
definition); ``workspace_unavailable_wire`` builds the recognisable
"workspace gone" error as a :class:`~error_wire.Custom` variant.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from minimax_code.tool_protocol.error_wire import Custom, ToolErrorWire

__all__ = [
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
# R83 backfill — from_tool_error_wire (classify a ToolErrorWire → numeric).
# -----------------------------------------------------------------------


#: Wire ``code`` tag → numeric JSON-RPC code (``from_tool_error_wire``).
#:
#: Dispatches on the variant's ``code`` ClassVar rather than the variant
#: type, so it rides on :mod:`error_wire`'s rename-aware tags (``forbidden``
#: etc.) and needs no per-type imports. Multiple variants fold onto
#: ``-32603`` (``internal_error``): ``Cancelled`` / ``Execution`` /
#: ``Internal`` / ``Custom`` all share the generic envelope code — the
#: string ``code`` discriminator is the stable identifier, not the numeric.
_VARIANT_CODE_TO_NUMERIC: dict[str, int] = {
    "tool_not_found": -32011,
    "session_mismatch": -32600,
    "forbidden": -32003,
    "connection_lost": -32004,
    "timeout": -32001,
    "cancelled": -32603,
    "invalid_params": -32602,
    "execution": -32603,
    "unsupported_protocol_version": -32605,
    "frame_too_large": -32018,
    "behavior_version_unsupported": -32020,
    "internal_error": -32603,
    "render_limited": -32023,
    "terminal_error": -32024,
    "custom": -32603,
}


def from_tool_error_wire(err: ToolErrorWire) -> int:
    """Numeric JSON-RPC code most appropriate for a :class:`ToolErrorWire`.

    Mirrors ``error_codes::from_tool_error_wire``: an exhaustive match from
    each variant to its envelope code. ``Custom`` always maps to ``-32603``
    (``internal_error``) since its ``code`` string is not in the table by
    definition. Dispatch is on the variant's wire ``code`` tag, so the
    rename overrides (``forbidden`` etc.) fall out automatically without
    importing the variant types.
    """
    return _VARIANT_CODE_TO_NUMERIC[err.code]


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


# -----------------------------------------------------------------------
# R83 backfill — workspace_unavailable_wire (build the Custom variant).
# -----------------------------------------------------------------------


def workspace_unavailable_wire(
    reason: WorkspaceGoneReason,
    phase: WorkspaceGonePhase,
) -> Custom:
    """Build the recognisable "workspace gone" error (``workspace_unavailable_wire``).

    Mirrors ``error_codes::workspace_unavailable_wire``: returns a
    :class:`~minimax_code.tool_protocol.error_wire.Custom` variant whose
    :attr:`~.error_wire.Custom.subcode` and ``details["code"]`` are both
    :data:`WORKSPACE_UNAVAILABLE_SUBCODE`, so a recogniser keying on either
    matches. ``details`` carries the :class:`WorkspaceUnavailableDetails`
    payload with ``retryable=True``. Reusing ``Custom`` (not a new variant)
    keeps the frame deserialisable on older peers.
    """
    details = WorkspaceUnavailableDetails(
        code=WORKSPACE_UNAVAILABLE_SUBCODE,
        reason=reason,
        phase=phase,
        retryable=True,
    )
    return Custom(
        subcode=WORKSPACE_UNAVAILABLE_SUBCODE,
        message=WORKSPACE_UNAVAILABLE_MESSAGE,
        details=details.to_wire(),
    )
