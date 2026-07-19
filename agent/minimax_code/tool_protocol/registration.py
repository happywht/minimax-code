"""Registration payloads, descriptions, transports, and outcomes (R87).

Fusion of grok-build's ``xai-tool-protocol::registration`` — the wire
payloads a tool server sends to register itself (or a single tool) with
the computer hub, plus the per-tool outcome the hub reports back.

This module is the **consumer** of three earlier rounds:

* :mod:`ids` (R82) — :class:`ToolId` / :class:`SessionId` / :class:`UserId`
  / :class:`ServerId` identifier newtypes, plus the
  :class:`~minimax_code.tool_protocol.ids.IdError` the
  :meth:`derive_tool_id` helpers surface.
* :mod:`capabilities` (R86) — :class:`ToolCapabilities` /
  :class:`NotificationSchemas` / :class:`HookKind` referenced by the
  per-tool schema wrapper and the server-batch registration.
* :mod:`tool_types` (R65, the migrated ``xai_tool_types`` crate) — the
  strong :class:`~minimax_code.tool_types.ToolDescription` carried inside
  every registration payload. This is the crate's first wire DTO that
  embeds a **pydantic** model where the rest of the protocol layer uses
  hand-controlled ``to_wire`` dicts; the bridge is
  :meth:`ToolDescription.model_dump` (``exclude_none=True``) on the way out
  and :meth:`ToolDescription.model_validate` on the way back, matching
  Rust's ``skip_serializing_if = "Option::is_none"`` on every ``Option``
  field of ``xai_tool_types::ToolDescription``.

Serde shapes
------------

Five shapes land here, three new to the crate:

* :class:`TransportKind` — plain ``#[serde(rename_all = "snake_case")]``
  enum (Local / Remote), reproduced as :class:`enum.StrEnum`; no
  ``#[serde(other)]`` arm, so an unknown wire string fails
  :meth:`~TransportKind.from_wire` rather than being swallowed.
* :class:`ToolDescriptionWithSchema` / :class:`ToolRegistration` /
  :class:`ToolServerRegistration` — plain structs with per-field
  ``#[serde(default, skip_serializing_if)]``. Two sub-shapes land here for
  the first time:

  + **3-state ``Option<Vec<SessionId>>``** (``sessions``) — ``None`` (field
    omitted) means "no change" (preserve existing bindings on a
    re-register), ``Some(vec![])`` (explicit empty array) means "unbind
    every session", ``Some(vec![...])`` means "replace with exactly these
    ids". Reproduced as ``list[SessionId] | None`` with ``is not None`` on
    the wire (so an explicit ``[]`` serialises, ``None`` omits).
  + **``skip_serializing_if = "String::is_empty"``** on
    :attr:`ToolServerRegistration.description` — a *bare* ``String`` (not
    ``Option<String>``) that omits from the wire when empty. This is the
    crate's **sixth** serde sub-shape (after bool-always / Option-skip /
    Vec-empty / HashMap-empty / transparent-newtype): a non-optional string
    that still has a presence-dependent skip. Reproduced as ``str = ""``
    with ``if self.description:`` on the wire.

* :class:`RegistrationOutcome` — ``#[serde(tag = "outcome", rename_all =
  "snake_case")]`` internally-tagged enum of four struct variants
  (``Registered`` / ``Updated`` / ``Shadowed`` / ``Rejected``). The
  discriminator rides *inside* the content object (the same internal-tag
  shape as R83's :class:`~minimax_code.tool_protocol.error_wire.ToolErrorWire`
  and R82's :class:`~minimax_code.tool_protocol.connection.ToolDefinitionMode`).
  Lands as four :func:`dataclasses.dataclass` variants plus a
  :data:`RegistrationOutcome` union alias and a module-level
  :func:`registration_outcome_from_wire` (the union cannot host a
  ``from_wire`` classmethod the way a concrete dataclass can; mirrors
  :func:`~minimax_code.tool_protocol.envelope.jsonrpc_id_from_wire`).

Naming
------

Rust's enum variants ``Registered`` / ``Updated`` / ``Shadowed`` /
``Rejected`` are members of the ``RegistrationOutcome`` enum (not
standalone items), so ``lib.rs`` re-exports only ``RegistrationOutcome``.
Python needs the variant classes constructable, so the barrel also exports
the four variant names — the same Python adaptation R83 made for
``ToolErrorWire``'s 15 variants. ``derive_tool_id`` returns ``Result<ToolId,
IdError>`` in Rust; the Python twin returns :class:`ToolId` directly and
lets :class:`~minimax_code.tool_protocol.ids.IdError` propagate from the
:class:`ToolId` constructor (the established R82 newtype pattern).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from minimax_code.tool_protocol.capabilities import (
    HookKind,
    NotificationSchemas,
    ToolCapabilities,
)
from minimax_code.tool_protocol.ids import ServerId, SessionId, ToolId, UserId
from minimax_code.tool_types import ToolDescription

__all__ = [
    "TransportKind",
    "ToolDescriptionWithSchema",
    "ToolRegistration",
    "ToolServerRegistration",
    "RegistrationOutcome",
    "Registered",
    "Updated",
    "Shadowed",
    "Rejected",
    "registration_outcome_from_wire",
]


def _description_to_wire(desc: ToolDescription) -> dict[str, object]:
    """Serialise a :class:`ToolDescription` matching Rust's serde.

    ``xai_tool_types::ToolDescription`` gives every ``Option`` field
    ``skip_serializing_if = "Option::is_none"`` (and ``#[serde(skip)]`` on
    the dropped ``extra``); ``model_dump(exclude_none=True)`` reproduces
    that — ``name`` / ``description`` (non-optional) always survive, the
    four optional fields omit when ``None``.
    """
    return desc.model_dump(exclude_none=True)


class TransportKind(StrEnum):
    """Whether a registered tool runs in-process or behind a remote connection.

    ``#[serde(rename_all = "snake_case")]`` with no ``#[serde(other)]``:
    member values are the snake_case wire strings; an unknown wire string
    fails :meth:`from_wire` (serde rejects rather than swallowing).
    """

    Local = "local"
    Remote = "remote"

    def to_wire(self) -> str:
        """The snake_case wire string (``#[serde(rename_all)]``)."""
        return self.value

    @classmethod
    def from_wire(cls, data: str) -> TransportKind:
        """Reconstruct from a wire string; reject unknown values.

        Mirrors serde with no ``#[serde(other)]`` arm: an unknown string
        raises :class:`ValueError` rather than being silently swallowed.
        """
        member = cls._value2member_map_.get(data)
        if member is None:
            raise ValueError(f"unknown TransportKind wire value: {data!r}")
        return member  # type: ignore[return-value]


@dataclass
class ToolDescriptionWithSchema:
    """A single tool's wire description plus optional schema/capability metadata.

    The ``tool_id`` is **not** stored explicitly — it is derived from
    ``description.{namespace, name}`` via :meth:`derive_tool_id`. The
    embedded :class:`ToolDescription` is serialised with
    ``model_dump(exclude_none=True)`` to match Rust's per-``Option`` skip
    rule (see :func:`_description_to_wire`); the three optional wrappers
    omit when ``None``.
    """

    #: The tool's declarative description (name / namespace / schema).
    #: Serialised via ``model_dump(exclude_none=True)``.
    description: ToolDescription
    #: Raw JSON Schema for the tool's arguments. ``serde_json::Value`` on
    #: the Rust side; omitted when ``None``.
    input_schema: Any | None = None
    #: Per-tool wire-traveling capabilities (R86). Omitted when ``None``;
    #: when present, the whole :class:`ToolCapabilities` object serialises
    #: (even its all-off default — the ``bool`` fields always ride).
    capabilities: ToolCapabilities | None = None
    #: Per-tool notification schemas (R86). Omitted when ``None``; when
    #: present the whole object serialises (even ``{}`` if both maps empty).
    notification_schemas: NotificationSchemas | None = None

    def derive_tool_id(self) -> ToolId:
        """Derive the canonical ``tool_id`` (``ToolDescriptionWithSchema::derive_tool_id``).

        Namespaced descriptions render as ``"{namespace}:{name}"``; the
        bare ``name`` otherwise. The result is run through the
        :class:`ToolId` constructor, so an invalid name or namespace
        surfaces as :class:`~minimax_code.tool_protocol.ids.IdError` (Rust
        returns ``Result<ToolId, IdError>``; Python lets it propagate).
        """
        if self.description.namespace is not None:
            return ToolId(f"{self.description.namespace}:{self.description.name}")
        return ToolId(self.description.name)

    def to_wire(self) -> dict[str, object]:
        d: dict[str, object] = {
            "description": _description_to_wire(self.description),
        }
        if self.input_schema is not None:
            d["input_schema"] = self.input_schema
        if self.capabilities is not None:
            d["capabilities"] = self.capabilities.to_wire()
        if self.notification_schemas is not None:
            d["notification_schemas"] = self.notification_schemas.to_wire()
        return d

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> ToolDescriptionWithSchema:
        kwargs: dict[str, Any] = {
            "description": ToolDescription.model_validate(data["description"]),
        }
        if "input_schema" in data:
            kwargs["input_schema"] = data["input_schema"]
        if "capabilities" in data:
            kwargs["capabilities"] = ToolCapabilities.from_wire(
                data["capabilities"]  # type: ignore[arg-type]
            )
        if "notification_schemas" in data:
            kwargs["notification_schemas"] = NotificationSchemas.from_wire(
                data["notification_schemas"]  # type: ignore[arg-type]
            )
        return cls(**kwargs)


@dataclass
class ToolRegistration:
    """Single-tool registration — wire sugar for a one-tool ``register_server``.

    ``tool_id`` MUST equal ``derive_tool_id()`` (the hub enforces this at
    register-tool time); carried explicitly so receivers route without
    re-deriving. ``sessions`` carries the per-tool session set with
    **three-state semantics** (see the attribute doc).

    Field order note: Rust declares ``tool_id, sessions, user_id,
    server_id, description, input_schema, capabilities,
    notification_schemas, transport_kind, if_match_generation, metadata``;
    Python's dataclass rule (non-default before default) reorders so the
    four required fields lead. Wire key order is hand-controlled in
    :meth:`to_wire` to match Rust; JSON object key order is not
    semantically significant.
    """

    #: MUST equal :meth:`derive_tool_id`. Always serialised.
    tool_id: ToolId
    #: The registering user. Always serialised.
    user_id: UserId
    #: The tool's declarative description. Serialised via
    #: ``model_dump(exclude_none=True)``.
    description: ToolDescription
    #: Whether the tool runs in-process (``Local``) or remote (``Remote``).
    #: Always serialised.
    transport_kind: TransportKind
    #: Per-tool session set — **three-state**: ``None`` (field omitted) =
    #: "no change" (preserve existing bindings on a re-register; the hub
    #: treats first-time as the empty set); ``[]`` (explicit empty) =
    #: "unbind every session"; ``[...]`` = "replace with exactly these
    #: ids". Serialise with ``is not None`` so an explicit ``[]`` rides.
    sessions: list[SessionId] | None = None
    #: ``None`` → the hub synthesises ``auto:tool:{tool_id}``.
    server_id: ServerId | None = None
    #: Raw JSON Schema for arguments. Omitted when ``None``.
    input_schema: Any | None = None
    #: Per-tool capabilities (R86). Omitted when ``None``.
    capabilities: ToolCapabilities | None = None
    #: Per-tool notification schemas (R86). Omitted when ``None``.
    notification_schemas: NotificationSchemas | None = None
    #: Optimistic-concurrency precondition. ``None`` → last-writer-wins.
    if_match_generation: int | None = None
    #: Opaque metadata; propagated to ``ServerInfo.metadata`` in
    #: ``servers.list`` responses. Omitted when ``None``.
    metadata: Any | None = None

    def derive_tool_id(self) -> ToolId:
        """Derive the canonical ``tool_id`` from ``description.{namespace, name}``.

        The ``tool_id`` payload field MUST equal this value; the hub
        enforces the invariant at register-tool time. Mirrors
        :meth:`ToolDescriptionWithSchema.derive_tool_id`.
        """
        if self.description.namespace is not None:
            return ToolId(f"{self.description.namespace}:{self.description.name}")
        return ToolId(self.description.name)

    def to_wire(self) -> dict[str, object]:
        d: dict[str, object] = {"tool_id": str(self.tool_id)}
        if self.sessions is not None:
            d["sessions"] = [str(s) for s in self.sessions]
        d["user_id"] = str(self.user_id)
        if self.server_id is not None:
            d["server_id"] = str(self.server_id)
        d["description"] = _description_to_wire(self.description)
        if self.input_schema is not None:
            d["input_schema"] = self.input_schema
        if self.capabilities is not None:
            d["capabilities"] = self.capabilities.to_wire()
        if self.notification_schemas is not None:
            d["notification_schemas"] = self.notification_schemas.to_wire()
        d["transport_kind"] = self.transport_kind.to_wire()
        if self.if_match_generation is not None:
            d["if_match_generation"] = self.if_match_generation
        if self.metadata is not None:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> ToolRegistration:
        kwargs: dict[str, Any] = {
            "tool_id": ToolId(str(data["tool_id"])),
            "user_id": UserId(str(data["user_id"])),
            "description": ToolDescription.model_validate(data["description"]),
            "transport_kind": TransportKind.from_wire(str(data["transport_kind"])),
        }
        if "sessions" in data:
            kwargs["sessions"] = [SessionId(str(s)) for s in data["sessions"]]  # type: ignore[union-attr]
        if "server_id" in data:
            kwargs["server_id"] = ServerId(str(data["server_id"]))
        if "input_schema" in data:
            kwargs["input_schema"] = data["input_schema"]
        if "capabilities" in data:
            kwargs["capabilities"] = ToolCapabilities.from_wire(
                data["capabilities"]  # type: ignore[arg-type]
            )
        if "notification_schemas" in data:
            kwargs["notification_schemas"] = NotificationSchemas.from_wire(
                data["notification_schemas"]  # type: ignore[arg-type]
            )
        if "if_match_generation" in data:
            kwargs["if_match_generation"] = int(data["if_match_generation"])  # type: ignore[arg-type]
        if "metadata" in data:
            kwargs["metadata"] = data["metadata"]
        return cls(**kwargs)


@dataclass
class ToolServerRegistration:
    """Multi-tool registration — one batch shares ``server_id`` / ``sessions``.

    Per-tool outcomes are reported individually via
    :class:`RegistrationOutcome`. ``sessions`` follows the same three-state
    semantics as :attr:`ToolRegistration.sessions` (applied to every tool in
    the batch). :attr:`description` is the crate's first bare-``String``
    field with ``skip_serializing_if = "String::is_empty"`` — a non-optional
    string that still omits from the wire when empty.

    Field order note: Rust declares ``server_id, sessions, user_id, title,
    description, tools, hooks, if_match_generation, metadata``; Python's
    dataclass rule reorders so the three required fields lead. Wire key
    order is hand-controlled in :meth:`to_wire`.
    """

    #: The batch's server identity. Always serialised.
    server_id: ServerId
    #: The registering user. Always serialised.
    user_id: UserId
    #: The batch's tool list. Always serialised (even when empty — ``Vec``
    #: carries no skip). Each element round-trips through
    #: :class:`ToolDescriptionWithSchema`.
    tools: list[ToolDescriptionWithSchema]
    #: Per-batch session set — three-state (see :attr:`ToolRegistration.sessions`).
    sessions: list[SessionId] | None = None
    #: Optional human-readable title. Omitted when ``None``.
    title: str | None = None
    #: Human-readable description of the server. ``#[serde(default,
    #: skip_serializing_if = "String::is_empty")]``: defaults to ``""`` and
    #: omits from the wire when empty (the sixth serde sub-shape).
    description: str = ""
    #: Lifecycle hooks the whole batch opts in to (R86). Defaults empty;
    #: omitted when empty.
    hooks: list[HookKind] = field(default_factory=list)
    #: Optimistic-concurrency precondition. ``None`` → last-writer-wins.
    if_match_generation: int | None = None
    #: Opaque metadata applied to every tool; propagated to
    #: ``ServerInfo.metadata``. Omitted when ``None``.
    metadata: Any | None = None

    def to_wire(self) -> dict[str, object]:
        d: dict[str, object] = {"server_id": str(self.server_id)}
        if self.sessions is not None:
            d["sessions"] = [str(s) for s in self.sessions]
        d["user_id"] = str(self.user_id)
        if self.title is not None:
            d["title"] = self.title
        if self.description:  # skip_serializing_if = "String::is_empty"
            d["description"] = self.description
        d["tools"] = [t.to_wire() for t in self.tools]
        if self.hooks:  # skip_serializing_if = "Vec::is_empty"
            d["hooks"] = [h.to_wire() for h in self.hooks]
        if self.if_match_generation is not None:
            d["if_match_generation"] = self.if_match_generation
        if self.metadata is not None:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> ToolServerRegistration:
        kwargs: dict[str, Any] = {
            "server_id": ServerId(str(data["server_id"])),
            "user_id": UserId(str(data["user_id"])),
            "tools": [
                ToolDescriptionWithSchema.from_wire(t)  # type: ignore[arg-type]
                for t in data["tools"]  # type: ignore[union-attr]
            ],
        }
        if "sessions" in data:
            kwargs["sessions"] = [SessionId(str(s)) for s in data["sessions"]]  # type: ignore[union-attr]
        if "title" in data:
            kwargs["title"] = str(data["title"])
        if "description" in data:
            kwargs["description"] = str(data["description"])
        if "hooks" in data:
            kwargs["hooks"] = [
                HookKind.from_wire(h) for h in data["hooks"]  # type: ignore[union-attr]
            ]
        if "if_match_generation" in data:
            kwargs["if_match_generation"] = int(data["if_match_generation"])  # type: ignore[arg-type]
        if "metadata" in data:
            kwargs["metadata"] = data["metadata"]
        return cls(**kwargs)


# -----------------------------------------------------------------------
# RegistrationOutcome — internally-tagged enum (tag = "outcome") of four
# struct variants. Each variant is a dataclass with its own to_wire (which
# stamps the "outcome" discriminator); the union alias is
# RegistrationOutcome, and registration_outcome_from_wire dispatches on the
# tag the way serde's internally-tagged representation does.
# -----------------------------------------------------------------------


@dataclass
class Registered:
    """The tool was newly registered (``RegistrationOutcome::Registered``).

    Internally-tagged on ``outcome = "registered"``; carries the assigned
    :class:`ToolId` and the hub's monotonically-increasing ``generation``.
    """

    tool_id: ToolId
    generation: int

    def to_wire(self) -> dict[str, object]:
        return {
            "outcome": "registered",
            "tool_id": str(self.tool_id),
            "generation": self.generation,
        }


@dataclass
class Updated:
    """An existing tool's registration was updated (``RegistrationOutcome::Updated``).

    Same fields as :class:`Registered`; the hub picks this variant when the
    tool already existed and ``if_match_generation`` satisfied.
    """

    tool_id: ToolId
    generation: int

    def to_wire(self) -> dict[str, object]:
        return {
            "outcome": "updated",
            "tool_id": str(self.tool_id),
            "generation": self.generation,
        }


@dataclass
class Shadowed:
    """The tool was registered but is shadowed by a higher-priority server.

    ``Shadowed`` carries a human-readable ``reason`` (e.g. another server
    won leader election); the tool is registered but unreachable from
    sessions until the shadow lifts.
    """

    tool_id: ToolId
    reason: str

    def to_wire(self) -> dict[str, object]:
        return {
            "outcome": "shadowed",
            "tool_id": str(self.tool_id),
            "reason": self.reason,
        }


@dataclass
class Rejected:
    """The registration was rejected (``RegistrationOutcome::Rejected``).

    ``code`` is a stable snake_case machine code (mirrors
    :class:`~minimax_code.tool_protocol.error_wire.ToolErrorWire`'s ``code``
    shape); ``message`` is the human-readable detail.
    """

    tool_id: ToolId
    code: str
    message: str

    def to_wire(self) -> dict[str, object]:
        return {
            "outcome": "rejected",
            "tool_id": str(self.tool_id),
            "code": self.code,
            "message": self.message,
        }


#: Discriminated union of the four outcome variants (the
#: ``RegistrationOutcome`` enum). Variant dispatch is via the ``outcome``
#: tag — see :func:`registration_outcome_from_wire`.
RegistrationOutcome = Registered | Updated | Shadowed | Rejected


#: Wire tag → variant class, in :meth:`registration_outcome_from_wire` order.
_OUTCOME_VARIANTS: dict[str, type[RegistrationOutcome]] = {
    "registered": Registered,
    "updated": Updated,
    "shadowed": Shadowed,
    "rejected": Rejected,
}


def registration_outcome_from_wire(data: dict[str, object]) -> RegistrationOutcome:
    """Reconstruct a :class:`RegistrationOutcome` from an internally-tagged dict.

    Dispatches on the ``outcome`` tag the way serde's internally-tagged
    representation (``#[serde(tag = "outcome")]``) does; an unknown tag
    raises :class:`ValueError`. Each variant reconstructs its named fields
    (``tool_id`` always; ``generation`` for Registered/Updated, ``reason``
    for Shadowed, ``code`` + ``message`` for Rejected).
    """
    tag = str(data["outcome"])
    variant = _OUTCOME_VARIANTS.get(tag)
    if variant is None:
        raise ValueError(f"unknown RegistrationOutcome outcome tag: {tag!r}")
    tool_id = ToolId(str(data["tool_id"]))
    if variant is Registered:
        return Registered(tool_id=tool_id, generation=int(data["generation"]))  # type: ignore[arg-type]
    if variant is Updated:
        return Updated(tool_id=tool_id, generation=int(data["generation"]))  # type: ignore[arg-type]
    if variant is Shadowed:
        return Shadowed(tool_id=tool_id, reason=str(data["reason"]))
    return Rejected(
        tool_id=tool_id, code=str(data["code"]), message=str(data["message"])
    )
