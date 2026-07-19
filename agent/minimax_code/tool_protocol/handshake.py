"""Handshake messages exchanged after the WebSocket upgrade (R82).

Fusion of grok-build's ``xai-tool-protocol::handshake`` — the first
frame the client sends after the upgrade succeeds (``HelloMsg``) and the
hub's reply (``HelloAckMsg``), plus the pinned wire
:data:`PROTOCOL_VERSION`.

No session ids are carried at handshake time: the connection starts with
an empty bound-session set and binds sessions dynamically over its
lifetime via ``register_session`` / ``unregister_session`` JSON-RPC calls.
Tool-server connections carry ``server_id`` so the hub can identify the
server without a separate ``register_server`` call.

Both structs are plain ``#[derive(Serialize, Deserialize)]`` (no tagged
enum, no BTreeMap sorting) — reproduced as :func:`dataclasses.dataclass`
with hand-written ``to_wire`` / ``from_wire`` so the serde
``skip_serializing_if`` rules map exactly:

* ``Option::<T>::None`` fields are omitted (``skip_serializing_if =
  "Option::is_none"``);
* :attr:`HelloAckMsg.capabilities` (a ``Vec<String>``) is omitted when
  empty (``skip_serializing_if = "Vec::is_empty"``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minimax_code.tool_protocol.connection import ConnectionKind
from minimax_code.tool_protocol.ids import ConnectionId, ServerId, UserId

__all__ = ["PROTOCOL_VERSION", "HelloMsg", "HelloAckMsg"]


#: Wire-protocol version both ends speak (``handshake::PROTOCOL_VERSION``).
#:
#: Bumped when an incompatible schema change lands; minor additions go
#: through capability negotiation rather than a version bump.
PROTOCOL_VERSION: str = "1.0.0"


@dataclass
class HelloMsg:
    """First frame sent by the client after the WebSocket upgrade (``handshake::HelloMsg``).

    Tool-server connections set :attr:`server_id` so the hub can identify
    the server without a separate ``register_server`` call; harness
    connections leave it unset. All optional fields are omitted from the
    wire form when ``None``.
    """

    protocol_version: str
    kind: ConnectionKind
    server_id: ServerId | None = None
    description: str | None = None
    #: Opaque metadata surfaced in ``ServerInfo.metadata`` — arbitrary JSON
    #: (``serde_json::Value``), passed through untouched.
    metadata: Any = None

    def to_wire(self) -> dict[str, Any]:
        """Serialise with ``Option::None`` fields omitted."""
        d: dict[str, Any] = {
            "protocol_version": self.protocol_version,
            "kind": self.kind.value,
        }
        if self.server_id is not None:
            d["server_id"] = str(self.server_id)
        if self.description is not None:
            d["description"] = self.description
        if self.metadata is not None:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> HelloMsg:
        """Reconstruct from a wire dict (absent optional fields default to ``None``)."""
        raw_server = data.get("server_id")
        return cls(
            protocol_version=data["protocol_version"],
            kind=ConnectionKind(data["kind"]),
            server_id=ServerId(raw_server) if raw_server is not None else None,
            description=data.get("description"),
            metadata=data.get("metadata"),
        )


@dataclass
class HelloAckMsg:
    """Computer hub's reply to :class:`HelloMsg` (``handshake::HelloAckMsg``).

    :attr:`user_id` is hub-derived (resolved from the upgrade credential —
    JWT ``sub``, local-dev hash, etc.) so the client never announces it.
    :attr:`capabilities` is absent on hubs predating the field (additive);
    clients gate per-call fallbacks on membership rather than probing.
    """

    connection_id: ConnectionId
    user_id: UserId
    computer_hub_version: str
    supported_protocol_versions: list[str]
    capabilities: list[str] | None = None

    def to_wire(self) -> dict[str, Any]:
        """Serialise, omitting ``capabilities`` when empty (``Vec::is_empty``)."""
        d: dict[str, Any] = {
            "connection_id": str(self.connection_id),
            "user_id": str(self.user_id),
            "computer_hub_version": self.computer_hub_version,
            "supported_protocol_versions": list(self.supported_protocol_versions),
        }
        # ``skip_serializing_if = "Vec::is_empty"``: omit when absent or empty.
        if self.capabilities:
            d["capabilities"] = list(self.capabilities)
        return d

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> HelloAckMsg:
        """Reconstruct; ``capabilities`` defaults to ``None`` when absent."""
        return cls(
            connection_id=ConnectionId(data["connection_id"]),
            user_id=UserId(data["user_id"]),
            computer_hub_version=data["computer_hub_version"],
            supported_protocol_versions=list(data["supported_protocol_versions"]),
            capabilities=(
                list(data["capabilities"]) if "capabilities" in data else None
            ),
        )
