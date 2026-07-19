"""Registry-level errors (R88).

Fusion of grok-build's ``xai-tool-protocol::registry_error`` — the
serializable registry-level error enum. Variants here are for failures that
occur **inside** the registry: mismatched session, server-id collisions,
optimistic-concurrency stale generation. Wire-level transport errors
(``tool_not_found`` etc.) live in :mod:`error_wire`
(:class:`~minimax_code.tool_protocol.error_wire.ToolErrorWire`); this enum
is the registry's analogue for structural / ownership failures.

Mirrors the Rust ``RegistryError`` enum: internally-tagged on ``code`` with
``rename_all = "snake_case"``, but the ``AlreadyRegistered`` variant carries
a per-variant ``#[serde(rename = "tool_already_registered")]`` that overrides
``rename_all`` — the crate's first per-variant rename override. All six
variants carry named fields (no unit variants).

Serde shape
-----------

``#[serde(tag = "code", rename_all = "snake_case")]`` — internally-tagged.
Each variant serialises as ``{"code": "<wire_tag>", ...named fields...}``.
Wire tags:

* :class:`AlreadyRegistered` → ``"tool_already_registered"`` (per-variant
  rename override; note this is NOT the ``rename_all`` default
  ``"already_registered"``).
* :class:`SessionMismatch` → ``"session_mismatch"``.
* :class:`ServerIdCollision` → ``"server_id_collision"``.
* :class:`ServerIdInUse` → ``"server_id_in_use"``.
* :class:`InvalidDescription` → ``"invalid_description"``.
* :class:`StaleGeneration` → ``"stale_generation"``.

``to_wire`` lives on each variant dataclass;
:func:`registry_error_from_wire` is the module-level dispatcher keyed on the
``code`` tag (a union of six dataclasses cannot host a classmethod, mirroring
:func:`~minimax_code.tool_protocol.registration.registration_outcome_from_wire`
and :func:`~minimax_code.tool_protocol.envelope.jsonrpc_id_from_wire`). Unknown
tags raise :class:`ValueError` — there is no ``#[serde(other)]`` arm, matching
the crate's strict-rejection convention (:class:`~envelope.Method`,
:class:`~capabilities.HookKind`, :class:`~capabilities.ToolScope`,
:class:`~registration.TransportKind`).
"""

from __future__ import annotations

from dataclasses import dataclass

from minimax_code.tool_protocol.ids import ServerId, SessionId, ToolId

__all__ = [
    "RegistryError",
    "AlreadyRegistered",
    "SessionMismatch",
    "ServerIdCollision",
    "ServerIdInUse",
    "InvalidDescription",
    "StaleGeneration",
    "registry_error_from_wire",
]


@dataclass
class AlreadyRegistered:
    """A different connection already owns this ``(session, tool)``.

    Wire tag: ``"tool_already_registered"`` — a per-variant ``#[serde(rename)]``
    override of the enum's ``rename_all = "snake_case"`` (so the tag is NOT
    the default ``"already_registered"``). ``tool_id`` is the contested
    identifier; the owning ``tool_call_id`` / connection travels in the
    enclosing frame.
    """

    #: The tool identifier a different connection has already claimed.
    tool_id: ToolId

    def to_wire(self) -> dict[str, object]:
        return {
            "code": "tool_already_registered",
            "tool_id": str(self.tool_id),
        }


@dataclass
class SessionMismatch:
    """The registration's session does not match the connection's bound session.

    ``token_session`` is the session encoded in the caller's auth token;
    ``reg_session`` is the session the registration payload requested.
    """

    #: Session encoded in the caller's auth token.
    token_session: SessionId
    #: Session the registration payload requested.
    reg_session: SessionId

    def to_wire(self) -> dict[str, object]:
        return {
            "code": "session_mismatch",
            "token_session": str(self.token_session),
            "reg_session": str(self.reg_session),
        }


@dataclass
class ServerIdCollision:
    """``server_id`` collides with an active server in this session owned by a
    different connection.

    Fails the entire ``register_*`` batch with a top-level JSON-RPC error
    (unlike a per-tool :class:`~registration.Rejected` outcome).
    """

    #: The colliding ``server_id``.
    server_id: ServerId

    def to_wire(self) -> dict[str, object]:
        return {
            "code": "server_id_collision",
            "server_id": str(self.server_id),
        }


@dataclass
class ServerIdInUse:
    """``server_id`` is already in use on this connection by an earlier
    registration with a different tool set."""

    #: The ``server_id`` already owned by an earlier registration.
    server_id: ServerId

    def to_wire(self) -> dict[str, object]:
        return {
            "code": "server_id_in_use",
            "server_id": str(self.server_id),
        }


@dataclass
class InvalidDescription:
    """Description failed structural validation.

    e.g. derived ``tool_id`` invalid, reserved prefix on a client-supplied
    ``server_id``. ``message`` is a human-readable diagnostic.
    """

    #: Human-readable diagnostic.
    message: str

    def to_wire(self) -> dict[str, object]:
        return {
            "code": "invalid_description",
            "message": self.message,
        }


@dataclass
class StaleGeneration:
    """``if_match_generation`` precondition failed.

    The registration carried an ``if_match_generation`` that did not match
    the registry's current generation for that tool — an optimistic-concurrency
    rejection. Callers retry by re-reading the current generation and
    re-submitting.
    """

    #: Generation the caller expected (its ``if_match_generation``).
    expected: int
    #: Generation actually current in the registry.
    actual: int

    def to_wire(self) -> dict[str, object]:
        return {
            "code": "stale_generation",
            "expected": self.expected,
            "actual": self.actual,
        }


#: The registry-level error union — one of the six variants above.
RegistryError = (
    AlreadyRegistered
    | SessionMismatch
    | ServerIdCollision
    | ServerIdInUse
    | InvalidDescription
    | StaleGeneration
)

#: ``code`` wire tag → variant dataclass. Captures the per-variant rename
#: override (``"tool_already_registered"`` for :class:`AlreadyRegistered`).
_WIRE_TAG_TO_VARIANT: dict[str, type[RegistryError]] = {
    "tool_already_registered": AlreadyRegistered,
    "session_mismatch": SessionMismatch,
    "server_id_collision": ServerIdCollision,
    "server_id_in_use": ServerIdInUse,
    "invalid_description": InvalidDescription,
    "stale_generation": StaleGeneration,
}


def registry_error_from_wire(data: dict[str, object]) -> RegistryError:
    """Reconstruct a :data:`RegistryError` from its wire form.

    Dispatches on the ``code`` tag (``#[serde(tag = "code")]``). Unknown codes
    raise :class:`ValueError` — there is no ``#[serde(other)]`` arm, matching
    the crate's strict-rejection convention. Per-variant rename overrides are
    captured in :data:`_WIRE_TAG_TO_VARIANT` (notably
    ``"tool_already_registered"`` for :class:`AlreadyRegistered`).
    """
    tag = str(data["code"])
    variant = _WIRE_TAG_TO_VARIANT.get(tag)
    if variant is None:
        raise ValueError(f"unknown RegistryError code tag: {tag!r}")
    if variant is AlreadyRegistered:
        return AlreadyRegistered(tool_id=ToolId(str(data["tool_id"])))
    if variant is SessionMismatch:
        return SessionMismatch(
            token_session=SessionId(str(data["token_session"])),
            reg_session=SessionId(str(data["reg_session"])),
        )
    if variant is ServerIdCollision:
        return ServerIdCollision(server_id=ServerId(str(data["server_id"])))
    if variant is ServerIdInUse:
        return ServerIdInUse(server_id=ServerId(str(data["server_id"])))
    if variant is InvalidDescription:
        return InvalidDescription(message=str(data["message"]))
    return StaleGeneration(
        expected=int(data["expected"]),
        actual=int(data["actual"]),
    )
