"""Hello handshake driver -- send HelloMsg, await HelloAckMsg (R134).

Fusion of grok-build's ``xai-computer-hub-sdk/src/handshake.rs``. The
handshake is the first exchange after the WebSocket upgrade: the client
sends :class:`~minimax_code.tool_protocol.handshake.HelloMsg`, the hub
replies with :class:`~minimax_code.tool_protocol.handshake.HelloAckMsg`,
and the SDK surfaces a typed
:class:`~minimax_code.computer_hub_sdk.error.ClientError` on any failure.
Splitting the driver into its own module keeps the connection state
machine (a later leaf) readable: send the frame, parse the ack, surface a
typed error.

Transport abstraction
---------------------

The Rust ``send_hello`` is generic over ``Si: SinkExt<Message>`` /
``St: StreamExt<Item = Result<Message, tungstenite::Error>>`` so the same
driver runs over any framed transport. Python mirrors that with two
:class:`typing.Protocol` surfaces (:class:`HandshakeSink` /
:class:`HandshakeStream`) keyed on a transport-agnostic
:class:`HandshakeFrame` sum type -- the five ``tungstenite::Message`` arms
the driver actually inspects (Text / Binary / Ping / Pong / Close).

MiniMax Code has no WebSocket *client* dependency today (only a FastAPI
server); the concrete WS adapter that bridges a ``websockets`` /
``anyio`` frame onto these protocols lands with ``connection.rs`` (a
later leaf). Landing the driver + protocols now lets that adapter
delegate to :func:`send_hello` verbatim, and lets the driver be exercised
with an in-memory fake sink/stream without any network. The
``tungstenite::Message::Frame`` arm (a low-level raw WebSocket frame) is
dropped: it never reaches the driver's match in practice (the codec
coalesces fragments into the higher-level variants), and Python WS
libraries do not expose a raw-frame variant, so there is nothing to
match on.

Module visibility
-----------------

The crate's ``lib.rs`` declares ``pub mod handshake`` but does **not**
``pub use handshake::*`` (callers reach it as
``xai_computer_hub_sdk::handshake::send_hello``). This landing matches
that: nothing here is re-exported from the package barrel; callers import
``minimax_code.computer_hub_sdk.handshake.send_hello``. The
:data:`PROTOCOL_VERSION` re-export mirrors the Rust module's own ``pub use
xai_tool_protocol::PROTOCOL_VERSION`` so the symbol is reachable through
the SDK namespace too.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from minimax_code.computer_hub_sdk.error import (
    Closed,
    NetworkError,
    ProtocolError,
    SerdeError,
)
from minimax_code.tool_protocol.connection import ConnectionKind
from minimax_code.tool_protocol.handshake import (
    PROTOCOL_VERSION,
    HelloAckMsg,
    HelloMsg,
)
from minimax_code.tool_protocol.ids import ServerId

__all__ = [
    "PROTOCOL_VERSION",
    "BinaryFrame",
    "CloseFrame",
    "HandshakeFrame",
    "HandshakeSink",
    "HandshakeStream",
    "HelloAckMsg",
    "HelloMsg",
    "PingFrame",
    "PongFrame",
    "TextFrame",
    "build_hello",
    "send_hello",
    "validate_hello_ack",
]


# ---------------------------------------------------------------------------
# HandshakeFrame sum type -- the tungstenite::Message arms the driver inspects.
# ---------------------------------------------------------------------------
@dataclass
class TextFrame:
    """A text WebSocket frame carrying the hello_ack JSON document."""

    text: str


@dataclass
class BinaryFrame:
    """A binary WebSocket frame (illegal during handshake)."""

    data: bytes


@dataclass
class PingFrame:
    """A WebSocket Ping; the driver replies with a matching Pong."""

    payload: bytes


@dataclass
class PongFrame:
    """A WebSocket Pong; the driver ignores it."""

    payload: bytes


@dataclass
class CloseFrame:
    """A WebSocket Close; the driver surfaces it as :class:`Closed`."""

    reason: str


#: Union of the five frame arms the handshake driver inspects. Mirrors the
#: ``tungstenite::Message`` match arms in ``send_hello`` (the raw ``Frame``
#: arm is dropped -- see module docstring).
HandshakeFrame = TextFrame | BinaryFrame | PingFrame | PongFrame | CloseFrame


# ---------------------------------------------------------------------------
# Transport-agnostic sink / stream surfaces.
# ---------------------------------------------------------------------------
class HandshakeSink(Protocol):
    """Outbound frame surface for the handshake driver (R134).

    Mirrors the ``Si: SinkExt<Message>`` bound on Rust ``send_hello``: the
    driver sends the hello ``Text`` frame and replies to inbound ``Ping``
    frames with ``Pong``. Any failure raises; the driver wraps the raised
    exception in :class:`NetworkError`.
    """

    async def send_text(self, text: str) -> None:
        """Send a text frame (the serialized :class:`HelloMsg`)."""
        ...

    async def send_pong(self, payload: bytes) -> None:
        """Send a pong frame echoing an inbound ping's payload."""
        ...


class HandshakeStream(Protocol):
    """Inbound frame surface for the handshake driver (R134).

    Mirrors the ``St: StreamExt<Item = Result<Message, tungstenite::Error>>``
    bound: an async iterator of already-classified :class:`HandshakeFrame`
    values. Transport-level decode failures are classified by the concrete
    adapter (which lands with ``connection.rs``) before reaching the driver,
    so the driver sees only frame arms or stream end
    (:class:`StopAsyncIteration`).
    """

    def __aiter__(self) -> AsyncIterator[HandshakeFrame]:
        ...


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------
def build_hello(
    kind: ConnectionKind,
    server_id: ServerId | None = None,
    description: str | None = None,
    metadata: Any = None,
) -> HelloMsg:
    """Construct the :class:`HelloMsg` pinned to :data:`PROTOCOL_VERSION` (R134).

    Lifts the ``HelloMsg { protocol_version: PROTOCOL_VERSION.to_owned(), ... }``
    literal so callers cannot drift off the pinned version.
    """
    return HelloMsg(
        protocol_version=PROTOCOL_VERSION,
        kind=kind,
        server_id=server_id,
        description=description,
        metadata=metadata,
    )


def validate_hello_ack(ack: HelloAckMsg) -> HelloAckMsg:
    """Assert the hub speaks :data:`PROTOCOL_VERSION`, else raise (R134).

    Mirrors the ``supported_protocol_versions.iter().any(|v| v == PROTOCOL_VERSION)``
    gate: the hub advertises the versions it understands; if the pinned
    client version is not among them the driver bails with
    :class:`ProtocolError` rather than risking a half-compatible session.
    Returns the ack unchanged so call sites can chain.
    """
    if PROTOCOL_VERSION not in ack.supported_protocol_versions:
        raise ProtocolError(
            f"server does not support {PROTOCOL_VERSION}; "
            f"supported: {ack.supported_protocol_versions!r}"
        )
    return ack


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------
async def send_hello(
    sink: HandshakeSink,
    stream: HandshakeStream,
    kind: ConnectionKind,
    server_id: ServerId | None = None,
    description: str | None = None,
    metadata: Any = None,
) -> HelloAckMsg:
    """Send :class:`HelloMsg`, await :class:`HelloAckMsg`, surface typed errors (R134).

    ``kind`` should be
    :class:`~minimax_code.tool_protocol.connection.ConnectionKind.ToolServer`
    for tool-server builds (the only consumer today). Returns the parsed
    ack so the caller can observe the server-issued ``connection_id`` and
    the server-derived ``user_id``. When ``server_id`` is given it is
    included in the hello frame so the server can identify itself without
    a separate ``register_server`` call.
    """
    hello = build_hello(kind, server_id, description, metadata)
    try:
        text = json.dumps(hello.to_wire())
    except (TypeError, ValueError) as e:
        raise SerdeError(f"hello serialize failed: {e}") from e
    try:
        await sink.send_text(text)
    except Exception as e:
        raise NetworkError(f"hello send failed: {e}") from e

    async for frame in stream:
        if isinstance(frame, TextFrame):
            try:
                ack = HelloAckMsg.from_wire(json.loads(frame.text))
            except (ValueError, KeyError, TypeError) as e:
                raise ProtocolError(f"malformed hello_ack: {e}") from e
            return validate_hello_ack(ack)
        if isinstance(frame, PingFrame):
            try:
                await sink.send_pong(frame.payload)
            except Exception as e:
                raise NetworkError(f"pong send failed: {e}") from e
            continue
        if isinstance(frame, CloseFrame):
            raise Closed(f"server closed during handshake: {frame.reason}")
        if isinstance(frame, PongFrame):
            continue
        if isinstance(frame, BinaryFrame):
            raise ProtocolError("server sent binary frame during handshake")
    raise NetworkError("server closed before hello_ack")
