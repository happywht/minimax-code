"""Tests for ``minimax_code.computer_hub_sdk.handshake`` (R134).

Mirrors grok-build's ``xai-computer-hub-sdk/src/handshake.rs``. The Rust
module has no ``#[test]`` of its own (its driver is exercised through
``connection.rs`` integration tests); this suite lands a Python-side
exhaustive branch cover so the transport-agnostic driver is correct
before the concrete WS adapter (``connection.rs`` leaf) is wired.

The driver is generic over a sink + stream; the suite drives it with two
in-memory fakes (:class:`_FakeSink` / :class:`_FakeStream`) so every
``tungstenite::Message`` match arm is reached without any network:

* :class:`TextFrame` -- happy path ack, malformed JSON, malformed shape,
  version mismatch;
* :class:`PingFrame` -- driver replies Pong (and surfaces a pong-send
  failure as :class:`NetworkError`);
* :class:`CloseFrame` -- surfaces :class:`Closed` with the reason;
* :class:`PongFrame` -- skipped, falls through to a later ack;
* :class:`BinaryFrame` -- illegal during handshake -> :class:`ProtocolError`;
* stream end -- :class:`NetworkError` "server closed before hello_ack";
* the hello ``Text`` send itself failing -> :class:`NetworkError`.

The pure helpers (:func:`build_hello` / :func:`validate_hello_ack`) are
asserted directly: :func:`build_hello` pins :data:`PROTOCOL_VERSION`;
:func:`validate_hello_ack` raises :class:`ProtocolError` off-version and
chains the ack through on-version.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from minimax_code.computer_hub_sdk.error import (
    Closed,
    NetworkError,
    ProtocolError,
)
from minimax_code.computer_hub_sdk.handshake import (
    PROTOCOL_VERSION,
    BinaryFrame,
    CloseFrame,
    HandshakeStream,
    PingFrame,
    PongFrame,
    TextFrame,
    build_hello,
    send_hello,
    validate_hello_ack,
)
from minimax_code.tool_protocol.connection import ConnectionKind
from minimax_code.tool_protocol.handshake import HelloAckMsg


# ---------------------------------------------------------------------------
# In-memory fakes (satisfy HandshakeSink / HandshakeStream structurally).
# ---------------------------------------------------------------------------
class _FakeSink:
    """In-memory :class:`HandshakeSink` capturing outbound frames (R134).

    Records every ``send_text`` / ``send_pong`` call so the suite can
    assert the hello frame was emitted and that pings were answered. The
    ``fail_text`` / ``fail_pong`` flags raise to exercise the driver's
    ``map_err(NetworkError)`` path.
    """

    def __init__(self, *, fail_text: bool = False, fail_pong: bool = False) -> None:
        self.sent_text: list[str] = []
        self.sent_pong: list[bytes] = []
        self._fail_text = fail_text
        self._fail_pong = fail_pong

    async def send_text(self, text: str) -> None:
        if self._fail_text:
            raise OSError("text send boom")
        self.sent_text.append(text)

    async def send_pong(self, payload: bytes) -> None:
        if self._fail_pong:
            raise OSError("pong send boom")
        self.sent_pong.append(payload)


class _FakeStream:
    """In-memory :class:`HandshakeStream` yielding a fixed frame list (R134).

    ``StopAsyncIteration`` once exhausted, mirroring the stream end the
    driver turns into ``NetworkError("server closed before hello_ack")``.
    """

    def __init__(self, frames: list[Any]) -> None:
        self._frames = list(frames)

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> Any:
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


def _ack_wire(supported: tuple[str, ...] = ("1.0.0",)) -> dict[str, Any]:
    """A well-formed hello_ack wire dict (version-negotiable by ``supported``)."""
    return {
        "connection_id": "conn-1",
        "user_id": "user-7",
        "computer_hub_version": "hub-0.1.0",
        "supported_protocol_versions": list(supported),
    }


# ---------------------------------------------------------------------------
# build_hello / validate_hello_ack -- pure helpers.
# ---------------------------------------------------------------------------
def test_build_hello_pins_protocol_version() -> None:
    hello = build_hello(ConnectionKind.ToolServer)
    assert hello.protocol_version == PROTOCOL_VERSION
    assert hello.kind is ConnectionKind.ToolServer


def test_build_hello_threads_optional_fields() -> None:
    hello = build_hello(
        ConnectionKind.ToolServer,
        server_id="srv-9",  # type: ignore[arg-type]
        description="ci",
        metadata={"runner": "gha"},
    )
    assert str(hello.server_id) == "srv-9"
    assert hello.description == "ci"
    assert hello.metadata == {"runner": "gha"}
    # wire form omits None fields and emits the pinned version.
    wire = hello.to_wire()
    assert wire["protocol_version"] == PROTOCOL_VERSION
    assert wire["kind"] == "tool_server"
    assert wire["server_id"] == "srv-9"


def test_validate_hello_ack_passes_when_version_supported() -> None:
    ack = HelloAckMsg.from_wire(_ack_wire(("0.9.0", "1.0.0")))
    assert validate_hello_ack(ack) is ack


def test_validate_hello_ack_raises_when_version_unsupported() -> None:
    ack = HelloAckMsg.from_wire(_ack_wire(("2.0.0",)))
    with pytest.raises(ProtocolError, match="server does not support"):
        validate_hello_ack(ack)


# ---------------------------------------------------------------------------
# send_hello -- happy path + Text-frame outcomes.
# ---------------------------------------------------------------------------
async def test_send_hello_happy_path_returns_ack() -> None:
    sink = _FakeSink()
    stream = _FakeStream([TextFrame(json.dumps(_ack_wire()))])
    ack = await send_hello(sink, stream, ConnectionKind.ToolServer)
    assert ack.connection_id == "conn-1"
    assert str(ack.user_id) == "user-7"
    # the hello Text frame was emitted exactly once, carrying the pinned version.
    assert len(sink.sent_text) == 1
    hello_wire = json.loads(sink.sent_text[0])
    assert hello_wire["protocol_version"] == PROTOCOL_VERSION
    assert hello_wire["kind"] == "tool_server"


async def test_send_hello_version_mismatch_raises_protocol_error() -> None:
    sink = _FakeSink()
    stream = _FakeStream([TextFrame(json.dumps(_ack_wire(("9.9.9",))))])
    with pytest.raises(ProtocolError, match="server does not support"):
        await send_hello(sink, stream, ConnectionKind.ToolServer)


async def test_send_hello_malformed_json_raises_protocol_error() -> None:
    sink = _FakeSink()
    stream = _FakeStream([TextFrame("{not json")])
    with pytest.raises(ProtocolError, match="malformed hello_ack"):
        await send_hello(sink, stream, ConnectionKind.ToolServer)


async def test_send_hello_malformed_shape_raises_protocol_error() -> None:
    sink = _FakeSink()
    # valid JSON, wrong shape (missing required fields) -> from_wire KeyError.
    stream = _FakeStream([TextFrame(json.dumps({"unrelated": True}))])
    with pytest.raises(ProtocolError, match="malformed hello_ack"):
        await send_hello(sink, stream, ConnectionKind.ToolServer)


# ---------------------------------------------------------------------------
# send_hello -- non-Text frame arms.
# ---------------------------------------------------------------------------
async def test_send_hello_answers_ping_then_returns_ack() -> None:
    sink = _FakeSink()
    stream = _FakeStream(
        [PingFrame(b"ping-payload"), TextFrame(json.dumps(_ack_wire()))]
    )
    ack = await send_hello(sink, stream, ConnectionKind.ToolServer)
    assert ack.connection_id == "conn-1"
    # exactly one Pong, echoing the Ping payload verbatim.
    assert sink.sent_pong == [b"ping-payload"]


async def test_send_hello_pong_send_failure_raises_network_error() -> None:
    sink = _FakeSink(fail_pong=True)
    stream = _FakeStream(
        [PingFrame(b"ping-payload"), TextFrame(json.dumps(_ack_wire()))]
    )
    with pytest.raises(NetworkError, match="pong send failed"):
        await send_hello(sink, stream, ConnectionKind.ToolServer)


async def test_send_hello_skips_pong_frame_then_returns_ack() -> None:
    sink = _FakeSink()
    stream = _FakeStream(
        [PongFrame(b"unsolicited"), TextFrame(json.dumps(_ack_wire()))]
    )
    ack = await send_hello(sink, stream, ConnectionKind.ToolServer)
    assert ack.connection_id == "conn-1"
    # the driver never replies to a Pong; only the hello Text was sent.
    assert sink.sent_pong == []


async def test_send_hello_close_frame_raises_closed_with_reason() -> None:
    sink = _FakeSink()
    stream = _FakeStream([CloseFrame("upgrade rejected")])
    with pytest.raises(Closed, match="server closed during handshake: upgrade rejected"):
        await send_hello(sink, stream, ConnectionKind.ToolServer)


async def test_send_hello_binary_frame_raises_protocol_error() -> None:
    sink = _FakeSink()
    stream = _FakeStream([BinaryFrame(b"\x00\x01")])
    with pytest.raises(ProtocolError, match="binary frame during handshake"):
        await send_hello(sink, stream, ConnectionKind.ToolServer)


# ---------------------------------------------------------------------------
# send_hello -- stream-end + hello-send failure.
# ---------------------------------------------------------------------------
async def test_send_hello_empty_stream_raises_network_error() -> None:
    sink = _FakeSink()
    stream: HandshakeStream = _FakeStream([])
    with pytest.raises(NetworkError, match="server closed before hello_ack"):
        await send_hello(sink, stream, ConnectionKind.ToolServer)


async def test_send_hello_text_send_failure_raises_network_error() -> None:
    sink = _FakeSink(fail_text=True)
    stream = _FakeStream([TextFrame(json.dumps(_ack_wire()))])
    with pytest.raises(NetworkError, match="hello send failed"):
        await send_hello(sink, stream, ConnectionKind.ToolServer)
