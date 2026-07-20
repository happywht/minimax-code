"""Client-side error taxonomy (R133).

Fusion of grok-build's ``xai-computer-hub-sdk/src/error.rs``. Wire-level
:class:`~minimax_code.tool_protocol.error_wire.ToolErrorWire` variants and
JSON-RPC error envelopes are mapped into the smaller :class:`ClientError`
vocabulary at the SDK boundary so consumers match on a single exception
hierarchy without re-deriving the numeric/string code mapping.

Model choice
------------

The Rust ``ClientError`` is a ``#[derive(thiserror::Error)]`` enum propagated
via ``?`` with six ``From`` impls. Python's idiomatic equivalent is an
exception hierarchy: :class:`ClientError` is the catch-all base, each enum
variant is a subclass, and the ``From`` impls collapse to
``raise Subclass(str(src)) from src`` at the call site (the SDK's WebSocket /
serde / url consumers land with ``connection.rs`` / ``harness.rs`` later
leaves; this module is the type contract, not the glue).

Variant naming stays one-to-one with the Rust enum arms
(``NetworkError``, ``HandshakeAuthFailed``, ``InsecureScheme`` ...) so consumer
code ports verbatim; the lone rename is ``Serde`` -> ``SerdeError`` because a
bare ``Serde`` class name reads as the serialisation library, not an
exception.

Display strings
---------------

Each subclass ``__init__`` reproduces the ``#[error("...")]`` format string
verbatim so ``str(ClientError(...))`` matches the Rust ``Display`` output.
The ``#[error(transparent)]`` :class:`Wire` arm delegates to the inner
``ToolErrorWire`` Display (``str(wire)``).
"""

from __future__ import annotations

from typing import Any

from minimax_code.tool_protocol.envelope import JsonRpcError
from minimax_code.tool_protocol.error_wire import (
    Custom,
    PermissionDenied,
    ToolErrorWire,
    TransportClosed,
    UnsupportedProtocolVersion,
)
from minimax_code.tool_protocol.error_wire import (
    from_wire as tool_error_wire_from_wire,
)

__all__ = [
    "AuthError",
    "BackpressureError",
    "CallIdInUse",
    "ClientError",
    "Closed",
    "HandshakeAuthFailed",
    "InsecureScheme",
    "InvalidConfig",
    "NetworkError",
    "ProtocolError",
    "RegistrationConflict",
    "SerdeError",
    "Wire",
]

#: JSON-RPC envelope codes that collapse to :class:`AuthError` when the
#: envelope carries no decodable ``data`` payload (Rust ``-32002 | -32003``).
_AUTH_CODES = frozenset({-32002, -32003})
#: JSON-RPC envelope code collapsing to :class:`NetworkError` (Rust ``-32004``).
_NETWORK_CODE = -32004
#: Inclusive bounds of the JSON-RPC reserved error band (Rust ``-32600..=-32500``).
_PROTOCOL_RANGE = (-32600, -32500)


def _in_protocol_range(code: int) -> bool:
    """``True`` when ``code`` falls in the ``-32600..=-32500`` reserved band."""
    return _PROTOCOL_RANGE[0] <= code <= _PROTOCOL_RANGE[1]


def _try_decode_wire(data: Any) -> ToolErrorWire | None:
    """Decode ``data`` as a :class:`ToolErrorWire` or return ``None`` (R133).

    Mirrors ``serde_json::from_value::<ToolErrorWire>(data)``: any decode
    failure (wrong shape, unknown discriminator, missing ``code`` field)
    collapses to ``None`` and the caller falls through to the numeric-code
    branches. :func:`~minimax_code.tool_protocol.error_wire.from_wire` raises
    :class:`ValueError` on an unknown code and :class:`KeyError` on a missing
    one; both are caught here.
    """
    if not isinstance(data, dict):
        return None
    try:
        return tool_error_wire_from_wire(data)
    except Exception:
        return None


class ClientError(Exception):
    """Base for all errors surfaced by the client SDK (R133).

    Mirrors ``xai_computer_hub_sdk::error::ClientError``. Consumers catch
    :class:`ClientError` to handle any SDK failure, or a specific subclass
    for variant-specific handling. The two bind recognizers
    (:meth:`is_server_not_found` / :meth:`is_tool_unavailable`) distinguish
    recoverable re-provision cases from fatal ServerNotFound at the harness
    dispatch boundary.
    """

    def _has_collapsed_jsonrpc_subcode(self, subcode: str) -> bool:
        """``True`` when a data-less envelope collapsed to ``jsonrpc_<code>``.

        Shared by the bind recognizers (Rust private method of the same name).
        Only a :class:`Wire` wrapping a :class:`~.error_wire.Custom` variant
        whose ``subcode`` matches qualifies.
        """
        return (
            isinstance(self, Wire)
            and isinstance(self.wire, Custom)
            and self.wire.subcode == subcode
        )

    def is_server_not_found(self) -> bool:
        """``True`` for the server's ``-32601`` "server not found" rejection.

        No workspace-server is registered for this user; the harness treats
        this as a fatal bind outcome, distinct from
        :meth:`is_tool_unavailable`.
        """
        return self._has_collapsed_jsonrpc_subcode("jsonrpc_-32601")

    def is_tool_unavailable(self) -> bool:
        """``True`` for the server's ``-32013`` "bind did not complete" error.

        The ``ServerBindOutcome::Unavailable`` cases; the harness re-provisions
        this recoverable case, distinct from :meth:`is_server_not_found`.
        """
        return self._has_collapsed_jsonrpc_subcode("jsonrpc_-32013")

    @classmethod
    def from_jsonrpc_error(cls, err: JsonRpcError) -> ClientError:
        """Map a JSON-RPC envelope error into a :class:`ClientError` (R133).

        The envelope's ``data`` payload (when present) carries the stable
        :class:`ToolErrorWire` discriminator and is decoded first; the numeric
        ``code`` is the coarse fallback when ``data`` is absent or undecodable.
        """
        data = err.data
        if data is not None:
            wire = _try_decode_wire(data)
            if wire is not None:
                return cls.from_wire(wire)
        code = err.code
        if code in _AUTH_CODES:
            return AuthError(err.message)
        if code == _NETWORK_CODE:
            return NetworkError(err.message)
        if _in_protocol_range(code):
            return ProtocolError(err.message)
        return Wire(
            Custom(
                subcode=f"jsonrpc_{code}",
                message=err.message,
                details=None,
            )
        )

    @classmethod
    def from_wire(cls, wire: ToolErrorWire) -> ClientError:
        """Map a :class:`ToolErrorWire` variant into the SDK taxonomy (R133).

        Three variants carry enough structure to promote to a narrower
        :class:`ClientError` (auth / network / protocol); everything else
        wraps verbatim under :class:`Wire` for callers that switch on the
        stable string code.
        """
        if isinstance(wire, PermissionDenied):
            return AuthError(wire.reason)
        if isinstance(wire, TransportClosed):
            return NetworkError(f"transport closed for {wire.tool_id}")
        if isinstance(wire, UnsupportedProtocolVersion):
            return ProtocolError(f"unsupported protocol; supported: {wire.supported!r}")
        return Wire(wire)

    @classmethod
    def from_handshake_error(cls, err: Exception) -> ClientError:
        """Classify a failed WebSocket upgrade (R133).

        A ``401``/``403`` on the HTTP upgrade is a non-retryable auth rejection
        (:class:`HandshakeAuthFailed`); every other failure stays a transport
        :class:`NetworkError`. The Rust original extracts the status from a
        typed ``tungstenite::Error::Http`` variant; Python's WS transport layer
        lands with ``connection.rs`` (a later leaf), so this version probes a
        ``status`` attribute on the exception (the contract the future WS
        adapter will set) and falls back to the blanket :class:`NetworkError`.
        """
        status = getattr(err, "status", None)
        if status in (401, 403):
            return HandshakeAuthFailed(int(status))
        return NetworkError(str(err))


# ---------------------------------------------------------------------------
# Variant subclasses. __init__ reproduces the #[error("...")] format string
# verbatim so str(error) matches the Rust Display output.
# ---------------------------------------------------------------------------
class NetworkError(ClientError):
    """WebSocket transport failure: failed connect / dropped socket / reconnect interrupt."""

    def __init__(self, message: str) -> None:
        super().__init__(f"network error: {message}")


class ProtocolError(ClientError):
    """Wire-protocol violation: malformed JSON, unexpected method, hello mismatch."""

    def __init__(self, message: str) -> None:
        super().__init__(f"protocol error: {message}")


class AuthError(ClientError):
    """Authentication or authorisation rejected by the server."""

    def __init__(self, message: str) -> None:
        super().__init__(f"auth error: {message}")


class HandshakeAuthFailed(ClientError):
    """Server rejected the WS upgrade with HTTP 401/403 (non-retryable).

    Replaying the same credential is rejected identically, so the reconnect
    loop classifies this as fatal instead of retrying forever.
    """

    status: int

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"handshake auth failed: HTTP {status}")


class RegistrationConflict(ClientError):
    """``register_tool`` / ``register_session`` ack reported a conflict."""

    def __init__(self, message: str) -> None:
        super().__init__(f"registration conflict: {message}")


class BackpressureError(ClientError):
    """Outbound mpsc full or call-site bounded wait elapsed (socket may be healthy)."""

    def __init__(self, message: str) -> None:
        super().__init__(f"backpressure: {message}")


class SerdeError(ClientError):
    """JSON serialise / deserialise failure inside the SDK (Rust arm ``Serde``)."""

    def __init__(self, message: str) -> None:
        super().__init__(f"serde error: {message}")


class InvalidConfig(ClientError):
    """Builder consistency error: missing URL, missing auth, etc."""

    def __init__(self, message: str) -> None:
        super().__init__(f"invalid configuration: {message}")


class Wire(ClientError):
    """Wrapped wire-format tool error; surfaces the upstream ``ToolErrorWire`` verbatim.

    ``#[error(transparent)]`` -> Display delegates to the inner variant, so
    ``str(Wire(wire)) == str(wire)`` (e.g. a :class:`~.error_wire.Custom`
    renders as ``"custom: <subcode> — <message>"``).
    """

    wire: ToolErrorWire

    def __init__(self, wire: ToolErrorWire) -> None:
        self.wire = wire
        super().__init__(str(wire))


class Closed(ClientError):
    """Server-side close / shutdown signal received during steady state."""

    def __init__(self, message: str) -> None:
        super().__init__(f"server closed connection: {message}")


class InsecureScheme(ClientError):
    """Refused to send credentials over plaintext ``ws://`` to non-loopback host.

    Local-loopback (``127.0.0.1``, ``::1``, ``localhost``) is the only
    exception; every other host MUST be reached over ``wss://`` so the bearer
    token never crosses the network in plaintext.
    """

    url: str

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(
            "insecure scheme: refusing to send credentials over plaintext "
            f"ws:// to non-loopback host {url}"
        )


class CallIdInUse(ClientError):
    """Caller passed a ``ToolCallId`` that already keys an in-flight dispatch.

    The prior call's progress waiter and response correlation are left intact;
    this error surfaces synchronously so the second caller retries with a fresh
    id. This is client misuse, not a transport or server failure.
    """

    call_id: str

    def __init__(self, call_id: str) -> None:
        self.call_id = call_id
        super().__init__(f"call_id {call_id} already in flight on this connection")
