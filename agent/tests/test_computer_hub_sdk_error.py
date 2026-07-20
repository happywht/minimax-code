"""Tests for ``minimax_code.computer_hub_sdk.error`` (R133).

Mirrors grok-build's ``xai-computer-hub-sdk/src/error.rs`` test suite. The
nine cases pin the three mapping surfaces:

* :func:`ClientError.from_handshake_error` -- HTTP upgrade status ->
  :class:`HandshakeAuthFailed` (401/403) vs :class:`NetworkError` (other).
* :func:`ClientError.from_jsonrpc_error` -- the ``data``-payload-first decode
  path (stable subcode + structured details survive) plus the numeric-code
  fallback (``-32002|-32003`` -> Auth, ``-32004`` -> Network,
  ``-32600..=-32500`` -> Protocol, else collapsed ``jsonrpc_<code>`` Custom).
* the bind recognizers (:meth:`ClientError.is_server_not_found` /
  :meth:`ClientError.is_tool_unavailable`) -- mutually exclusive subcode
  matches, plus the SDK re-export of :func:`is_workspace_unavailable`.

``http_upgrade_error`` (a ``tungstenite::Error::Http`` builder in Rust) is
replaced by a tiny fake exception exposing ``.status``; Python's WS transport
layer lands with ``connection.rs`` (a later leaf), so the SDK reads the status
off whatever exception the adapter raises rather than a typed enum.
"""

from __future__ import annotations

import pytest

from minimax_code.computer_hub_sdk.error import (
    AuthError,
    ClientError,
    HandshakeAuthFailed,
    NetworkError,
    Wire,
)
from minimax_code.tool_protocol.envelope import JsonRpcError
from minimax_code.tool_protocol.error_codes import (
    WORKSPACE_UNAVAILABLE_SUBCODE,
    WorkspaceGonePhase,
    WorkspaceGoneReason,
    workspace_unavailable_wire,
)
from minimax_code.tool_protocol.error_wire import Custom


class _FakeHttpUpgradeError(Exception):
    """Stand-in for ``tungstenite::Error::Http`` (R133).

    The Rust suite builds a typed HTTP-response-carrying tungstenite error to
    exercise ``from_handshake_error``'s status extraction. Python has no
    tungstenite; the SDK probes ``.status`` on the exception (the contract the
    future WS adapter will set), so this fake satisfies that probe.
    """

    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.status = status


def _workspace_gone_envelope() -> JsonRpcError:
    """The recognisable "workspace gone" envelope (Rust ``workspace_gone_envelope``).

    ``code`` -32005 falls outside the auth/network/protocol bands, so without
    the decodable ``data`` payload it would collapse to a ``jsonrpc_-32005``
    Custom subcode; the payload carries the stable ``workspace_unavailable``
    discriminator and structured details that
    :func:`ClientError.from_jsonrpc_error` must surface intact.
    """
    wire = workspace_unavailable_wire(
        WorkspaceGoneReason.Disconnect,
        WorkspaceGonePhase.RouteMissing,
    )
    return JsonRpcError(
        code=-32005,
        message="workspace server gone",
        data=wire.to_wire(),
    )


# ---------------------------------------------------------------------------
# from_handshake_error
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", [401, 403])
def test_handshake_401_and_403_map_to_handshake_auth_failed(status: int) -> None:
    err = ClientError.from_handshake_error(_FakeHttpUpgradeError(status))
    assert isinstance(err, HandshakeAuthFailed)
    assert err.status == status


@pytest.mark.parametrize("status", [500, 502, 429])
def test_handshake_non_auth_status_stays_network_error(status: int) -> None:
    err = ClientError.from_handshake_error(_FakeHttpUpgradeError(status))
    assert isinstance(err, NetworkError)
    # HandshakeAuthFailed is a distinct subclass, not a NetworkError specialization.
    assert not isinstance(err, HandshakeAuthFailed)


# ---------------------------------------------------------------------------
# from_jsonrpc_error
# ---------------------------------------------------------------------------
def test_from_jsonrpc_error_preserves_workspace_subcode_and_details() -> None:
    # The data payload decodes as ToolErrorWire first, so the stable subcode
    # and structured details reach the SDK consumer intact rather than
    # collapsing to the numeric code.
    err = ClientError.from_jsonrpc_error(_workspace_gone_envelope())
    assert isinstance(err, Wire)
    assert isinstance(err.wire, Custom)
    assert err.wire.subcode == WORKSPACE_UNAVAILABLE_SUBCODE
    details = err.wire.details
    assert details is not None
    assert details["code"] == WORKSPACE_UNAVAILABLE_SUBCODE
    assert details["reason"] == "disconnect"
    assert details["phase"] == "route_missing"
    assert details["retryable"] is True


def test_is_server_not_found_recognizes_bare_minus_32601() -> None:
    # data-less -32601 -> custom subcode jsonrpc_-32601.
    err = ClientError.from_jsonrpc_error(
        JsonRpcError(
            code=-32601,
            message="server abc not found for user",
            data=None,
        )
    )
    assert err.is_server_not_found()


def test_is_tool_unavailable_recognizes_bare_minus_32013() -> None:
    err = ClientError.from_jsonrpc_error(
        JsonRpcError(
            code=-32013,
            message="server abc did not complete the bind",
            data=None,
        )
    )
    assert err.is_tool_unavailable()


def test_is_server_not_found_rejects_other_errors() -> None:
    auth = ClientError.from_jsonrpc_error(
        JsonRpcError(code=-32002, message="nope", data=None)
    )
    assert isinstance(auth, AuthError)
    assert not auth.is_server_not_found()
    # workspace-gone is the tool-call re-provision path, not bind ServerNotFound.
    assert not ClientError.from_jsonrpc_error(
        _workspace_gone_envelope()
    ).is_server_not_found()


def test_bind_recognizers_are_mutually_exclusive() -> None:
    not_found = ClientError.from_jsonrpc_error(
        JsonRpcError(code=-32601, message="not found", data=None)
    )
    unavailable = ClientError.from_jsonrpc_error(
        JsonRpcError(code=-32013, message="unavailable", data=None)
    )
    assert not_found.is_server_not_found()
    assert not not_found.is_tool_unavailable()  # -32601 != tool_unavailable
    assert unavailable.is_tool_unavailable()
    assert not unavailable.is_server_not_found()  # -32013 != server_not_found


# ---------------------------------------------------------------------------
# SDK re-exported recognizer (is_workspace_unavailable, lib.rs line 71 mirror)
# ---------------------------------------------------------------------------
def test_sdk_reexported_recognizer_matches_decoded_error() -> None:
    # SDK-only consumers reach the recognizer through the SDK re-export and
    # the core decode path (error_from_envelope -> ToolError).
    from minimax_code.computer_hub_core import error_from_envelope
    from minimax_code.computer_hub_sdk import is_workspace_unavailable

    err = error_from_envelope(_workspace_gone_envelope())
    assert is_workspace_unavailable(err)


def test_sdk_reexported_recognizer_rejects_unrelated_custom_error() -> None:
    from minimax_code.computer_hub_core import error_from_envelope
    from minimax_code.computer_hub_sdk import is_workspace_unavailable

    wire = Custom(
        subcode="unrelated",
        message="nope",
        details={"code": "unrelated"},
    )
    env = JsonRpcError(code=-32000, message="nope", data=wire.to_wire())
    err = error_from_envelope(env)
    assert not is_workspace_unavailable(err)
