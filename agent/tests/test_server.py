"""Tests for ``computer_hub_sdk.server`` preamble (R175, server.rs leaf 1).

Covers the pure-logic / type-contract surface of the ``server.rs`` port
that sits ahead of the live ``ToolServer`` actor:

* :class:`SystemNotifyAck` enum shape (two variants, identity equality).
* :data:`ReconnectSettledCallback` alias (the bare ``() -> None`` call shape).
* :func:`json_serialized_len` — compact UTF-8 byte count with serde-faithful
  separators / ``ensure_ascii`` / ``allow_nan`` knobs, plus the
  :class:`SerdeError` failure path for non-finite floats and unserialisable
  values.
* :func:`system_notify_ack_from_outcome` — the three Rust match arms: the
  success arm, the bare ``-32601 method_not_found`` (no ``data``) lift to
  ``ForwardingUnsupported``, the load-bearing ``data`` guard, and the
  fall-through to :meth:`ClientError.from_jsonrpc_error`.

The actor body (``ToolServer`` / ``ToolServerBuilder`` / inbox dispatcher)
is bound to the live xAI socket protocol and is out of scope here.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from minimax_code.computer_hub_sdk.error import AuthError, ClientError, SerdeError
from minimax_code.computer_hub_sdk.server import (
    ReconnectSettledCallback,
    SystemNotifyAck,
    json_serialized_len,
    system_notify_ack_from_outcome,
)
from minimax_code.tool_protocol.envelope import JsonRpcError, ResponseError, ResponseResult


# ===========================================================================
# SystemNotifyAck enum (Rust #[derive(Debug, Clone, Copy, PartialEq, Eq)]).
# ===========================================================================
def test_system_notify_ack_has_exactly_two_variants():
    assert len(list(SystemNotifyAck)) == 2
    assert SystemNotifyAck.Accepted in list(SystemNotifyAck)
    assert SystemNotifyAck.ForwardingUnsupported in list(SystemNotifyAck)


def test_system_notify_ack_variants_are_unequal():
    assert SystemNotifyAck.Accepted != SystemNotifyAck.ForwardingUnsupported
    assert SystemNotifyAck.ForwardingUnsupported != SystemNotifyAck.Accepted


def test_system_notify_ack_variant_equals_itself_and_is_hashable():
    assert SystemNotifyAck.Accepted == SystemNotifyAck.Accepted
    assert SystemNotifyAck.ForwardingUnsupported == SystemNotifyAck.ForwardingUnsupported
    # Hashable so the variant can key a dict / sit in a set (Rust Eq + Hash parity).
    assert {SystemNotifyAck.Accepted, SystemNotifyAck.ForwardingUnsupported, SystemNotifyAck.Accepted} == {
        SystemNotifyAck.Accepted,
        SystemNotifyAck.ForwardingUnsupported,
    }


def test_system_notify_ack_variant_value_is_its_name():
    """C-like enum: the serialised value is the variant name verbatim."""
    assert SystemNotifyAck.Accepted.value == "Accepted"
    assert SystemNotifyAck.ForwardingUnsupported.value == "ForwardingUnsupported"


# ===========================================================================
# ReconnectSettledCallback alias (Rust Box<dyn Fn() + Send + Sync + 'static>).
# ===========================================================================
def test_reconnect_settled_callback_alias_is_zero_arg_callable_shape():
    """The alias names the ``() -> None`` call shape; Python has no trait
    object / lifetime vocabulary, so the contract is just the signature."""
    assert ReconnectSettledCallback == Callable[[], None]


def test_reconnect_settled_callback_alias_accepts_conforming_function():
    """Any zero-arg callable satisfies the alias at runtime."""

    def _cb() -> None:
        return None

    _cb()  # a conforming callable is an ordinary function; the alias documents shape


# ===========================================================================
# json_serialized_len — compact UTF-8 byte count (serde_json::to_writer parity).
# ===========================================================================
def test_json_serialized_len_empty_object():
    assert json_serialized_len({}) == 2  # {}


def test_json_serialized_len_empty_array():
    assert json_serialized_len([]) == 2  # []


def test_json_serialized_len_null_and_scalars():
    assert json_serialized_len(None) == 4  # null
    assert json_serialized_len(True) == 4  # true
    assert json_serialized_len(False) == 5  # false
    assert json_serialized_len(42) == 2  # 42
    assert json_serialized_len("hi") == 4  # "hi"


def test_json_serialized_len_uses_compact_separators():
    """serde_json emits no whitespace; Python's default dumps adds spaces."""
    # Default Python form: {"a": 1} (note the space) = 8 bytes.
    assert len(json.dumps({"a": 1}).encode("utf-8")) == 8
    # Compact serde form: {"a":1} = 7 bytes.
    assert json_serialized_len({"a": 1}) == 7


def test_json_serialized_len_counts_utf8_bytes_not_chars():
    """Non-ASCII kept verbatim as UTF-8 (serde_json), not escaped to \\uXXXX."""
    # "é" is 2 UTF-8 bytes; ensure_ascii=False keeps it verbatim.
    # {"k":"é"} -> 9 chars but 10 UTF-8 bytes.
    assert json_serialized_len({"k": "é"}) == 10
    # Python's default ensure_ascii=True escapes the non-ASCII char and the
    # default separators add a space after ':' — 15 bytes total, strictly
    # more than the compact verbatim form.
    assert len(json.dumps({"k": "é"}).encode("utf-8")) == 15


def test_json_serialized_len_nested_compact():
    """Nested structure serialises compact (no whitespace at any depth)."""
    value = [1, {"b": [2, 3]}]
    # [1,{"b":[2,3]}] = 15 bytes.
    assert json_serialized_len(value) == 15


def test_json_serialized_len_matches_manual_compact_dumps():
    """The function agrees with the explicit compact-dumps reference for a
    representative payload (the cross-check the fidelity note promises)."""
    value = {"name": "tools/call", "args": {"x": 1, "y": [True, None, "z"]}, "n": 3.14}
    expected = len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    assert json_serialized_len(value) == expected


def test_json_serialized_len_nan_raises_serde_error():
    """serde_json rejects NaN (not valid JSON); allow_nan=False reproduces it."""
    with pytest.raises(SerdeError):
        json_serialized_len(float("nan"))


def test_json_serialized_len_infinity_raises_serde_error():
    """serde_json rejects Infinity; allow_nan=False reproduces it."""
    with pytest.raises(SerdeError):
        json_serialized_len(float("inf"))


def test_json_serialized_len_negative_infinity_raises_serde_error():
    with pytest.raises(SerdeError):
        json_serialized_len(float("-inf"))


def test_json_serialized_len_unserializable_value_raises_serde_error():
    """A value json cannot encode (e.g. a set) maps to SerdeError, not TypeError."""
    with pytest.raises(SerdeError):
        json_serialized_len({1, 2, 3})


def test_json_serialized_len_serde_error_is_client_error_subclass():
    """SerdeError is part of the ClientError taxonomy (Rust ClientError::Serde)."""
    with pytest.raises(ClientError):
        json_serialized_len(float("nan"))


# ===========================================================================
# system_notify_ack_from_outcome — the three Rust match arms.
# ===========================================================================
def test_ack_from_outcome_result_is_accepted():
    """ResponseOutcome::Result(_) -> Accepted (success arm; payload ignored)."""
    outcome = ResponseResult(value={"ok": True})
    assert system_notify_ack_from_outcome(outcome) == SystemNotifyAck.Accepted


def test_ack_from_outcome_result_payload_value_is_irrelevant():
    """The success arm does not inspect the payload value."""
    assert system_notify_ack_from_outcome(ResponseResult(value=None)) == SystemNotifyAck.Accepted
    assert system_notify_ack_from_outcome(ResponseResult(value=42)) == SystemNotifyAck.Accepted
    assert system_notify_ack_from_outcome(ResponseResult(value=[1, 2, 3])) == SystemNotifyAck.Accepted


def test_ack_from_outcome_bare_method_not_found_is_forwarding_unsupported():
    """Plain -32601 with no data discriminator -> ForwardingUnsupported."""
    err = JsonRpcError(code=-32601, message="method not found")
    assert err.data is None  # guard precondition
    outcome = ResponseError(err)
    assert system_notify_ack_from_outcome(outcome) == SystemNotifyAck.ForwardingUnsupported


def test_ack_from_outcome_method_not_found_with_data_is_client_error():
    """The data.is_none() guard is load-bearing: a richer -32601 that carries
    a data discriminator must NOT be swallowed as ForwardingUnsupported; it
    flows through the normal taxonomy."""
    err = JsonRpcError(code=-32601, message="method not found", data={"code": "tool_unavailable"})
    assert err.data is not None
    with pytest.raises(ClientError):
        system_notify_ack_from_outcome(ResponseError(err))


def test_ack_from_outcome_other_protocol_error_is_client_error():
    """A non-method_not_found protocol error falls through to from_jsonrpc_error."""
    err = JsonRpcError(code=-32603, message="internal error")
    with pytest.raises(ClientError):
        system_notify_ack_from_outcome(ResponseError(err))


def test_ack_from_outcome_auth_error_is_classified_via_from_jsonrpc_error():
    """The fall-through arm delegates to from_jsonrpc_error, which narrows
    -32002 to AuthError; the lift keeps the taxonomy intact."""
    err = JsonRpcError(code=-32002, message="unauthorized")
    with pytest.raises(AuthError):
        system_notify_ack_from_outcome(ResponseError(err))


def test_ack_from_outcome_error_message_is_preserved():
    """The envelope message survives the lift into the ClientError taxonomy."""
    err = JsonRpcError(code=-32603, message="boom-from-server")
    with pytest.raises(ClientError) as exc_info:
        system_notify_ack_from_outcome(ResponseError(err))
    assert "boom-from-server" in str(exc_info.value)


def test_ack_from_outcome_network_error_code_is_classified():
    """-32004 (connection_lost) narrows to NetworkError via from_jsonrpc_error."""
    from minimax_code.computer_hub_sdk.error import NetworkError

    err = JsonRpcError(code=-32004, message="connection lost")
    with pytest.raises(NetworkError):
        system_notify_ack_from_outcome(ResponseError(err))
