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
import logging
from collections.abc import Callable

import pytest

from minimax_code.computer_hub_sdk.error import AuthError, ClientError, SerdeError
from minimax_code.computer_hub_sdk.server import (
    ReconnectSettledCallback,
    SystemNotifyAck,
    json_serialized_len,
    parse_tool_call_id,
    progress_to_frame,
    system_notify_ack_from_outcome,
)
from minimax_code.tool_protocol.envelope import JsonRpcError, ResponseError, ResponseResult
from minimax_code.tool_protocol.frames import ToolCallProgressFrame
from minimax_code.tool_protocol.ids import IdError, ToolCallId
from minimax_code.tool_runtime.tool import ContentBlock, ToolProgress


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


# ===========================================================================
# parse_tool_call_id — JSON-pointer walk + serde from_value .ok() (R176).
# ===========================================================================
def test_parse_tool_call_id_normal_path():
    """params.tool_call_id present as a non-empty string -> ToolCallId."""
    result = parse_tool_call_id({"params": {"tool_call_id": "call_abc"}})
    assert result == "call_abc"
    assert isinstance(result, ToolCallId)


def test_parse_tool_call_id_missing_params_returns_none():
    """No params node -> pointer walk bails -> None."""
    assert parse_tool_call_id({}) is None
    assert parse_tool_call_id({"method": "tools/call"}) is None


def test_parse_tool_call_id_missing_tool_call_id_returns_none():
    """params present but tool_call_id absent -> None."""
    assert parse_tool_call_id({"params": {}}) is None
    assert parse_tool_call_id({"params": {"other": 1}}) is None


def test_parse_tool_call_id_non_string_value_returns_none():
    """A non-string tool_call_id (type mismatch) -> serde from_value fails -> None."""
    assert parse_tool_call_id({"params": {"tool_call_id": 42}}) is None
    assert parse_tool_call_id({"params": {"tool_call_id": True}}) is None
    assert parse_tool_call_id({"params": {"tool_call_id": None}}) is None
    assert parse_tool_call_id({"params": {"tool_call_id": [1]}}) is None


def test_parse_tool_call_id_empty_string_returns_none():
    """An empty string is rejected by ToolCallId::new; .ok() flattens to None."""
    result = parse_tool_call_id({"params": {"tool_call_id": ""}})
    assert result is None
    # And the validating constructor itself raises IdError for the empty case,
    # confirming the .ok() flattening reproduces serde's failure mode.
    with pytest.raises(IdError):
        ToolCallId("")


def test_parse_tool_call_id_value_not_dict_returns_none():
    """The top-level value not being an object -> pointer walk fails -> None."""
    assert parse_tool_call_id("not a dict") is None
    assert parse_tool_call_id(42) is None
    assert parse_tool_call_id(None) is None
    assert parse_tool_call_id([1, 2, 3]) is None


def test_parse_tool_call_id_params_not_dict_returns_none():
    """params present but not an object (e.g. a list) -> walk can't descend -> None."""
    assert parse_tool_call_id({"params": [1, 2]}) is None
    assert parse_tool_call_id({"params": "str"}) is None


def test_parse_tool_call_id_does_not_mutate_input():
    """The pointer walk reads only; the caller's frame is untouched."""
    value = {"params": {"tool_call_id": "call_xyz"}}
    parse_tool_call_id(value)
    assert value == {"params": {"tool_call_id": "call_xyz"}}


def test_parse_tool_call_id_roundtrips_through_str_equality():
    """The extracted id is a str subclass; str(id) reconstructs the lookup."""
    original = {"params": {"tool_call_id": "call_12345"}}
    extracted = parse_tool_call_id(original)
    assert extracted is not None
    assert {"params": {"tool_call_id": str(extracted)}} == original


# ===========================================================================
# progress_to_frame — ToolProgress -> ToolCallProgressFrame (R176, 3 arms).
# ===========================================================================
def test_progress_to_frame_text_variant():
    """Text arm: kind="text", body={"text": text}, dropped_count=None."""
    frame = progress_to_frame(ToolProgress.Text("hello"), ToolCallId("c1"))
    assert isinstance(frame, ToolCallProgressFrame)
    assert frame.kind == "text"
    assert frame.body == {"text": "hello"}
    assert frame.dropped_count is None


def test_progress_to_frame_content_variant_drives_to_dict():
    """Content arm serialises blocks via ContentBlock.to_dict (serde to_value)."""
    block = ContentBlock.Text("a")
    frame = progress_to_frame(ToolProgress.Content([block]), ToolCallId("c1"))
    assert frame.kind == "content"
    assert frame.body == [block.to_dict()]


def test_progress_to_frame_content_multiple_blocks_preserve_order():
    """Block order is preserved across the to_value serialisation."""
    b1 = ContentBlock.Text("first")
    b2 = ContentBlock.Text("second")
    frame = progress_to_frame(ToolProgress.Content([b1, b2]), ToolCallId("c1"))
    assert frame.body == [b1.to_dict(), b2.to_dict()]


def test_progress_to_frame_content_empty_blocks_is_empty_array():
    """An empty block list serialises to an empty JSON array."""
    frame = progress_to_frame(ToolProgress.Content([]), ToolCallId("c1"))
    assert frame.body == []


def test_progress_to_frame_content_none_blocks_treated_as_empty():
    """blocks is Optional; a None payload coerces to an empty array (not Null)."""
    progress = ToolProgress(kind="content")  # blocks defaults to None
    frame = progress_to_frame(progress, ToolCallId("c1"))
    assert frame.body == []


def test_progress_to_frame_custom_variant_carries_subkind_and_payload():
    """Custom arm: kind=subkind, body=payload."""
    frame = progress_to_frame(
        ToolProgress.Custom("bash_output_chunk", {"stdout": "ok"}),
        ToolCallId("c1"),
    )
    assert frame.kind == "bash_output_chunk"
    assert frame.body == {"stdout": "ok"}


def test_progress_to_frame_custom_payload_carried_verbatim():
    """Custom.payload is an opaque serde_json::Value; carried by reference."""
    payload = {"nested": [1, 2, {"k": None}]}
    frame = progress_to_frame(ToolProgress.Custom("sub", payload), ToolCallId("c1"))
    assert frame.body is payload


def test_progress_to_frame_preserves_tool_call_id():
    """The id passes through unchanged (str equality + instance type)."""
    cid = ToolCallId("call_xyz")
    for progress in (
        ToolProgress.Text("x"),
        ToolProgress.Content([ContentBlock.Text("x")]),
        ToolProgress.Custom("sub", {}),
    ):
        frame = progress_to_frame(progress, cid)
        assert frame.tool_call_id == cid
        assert isinstance(frame.tool_call_id, ToolCallId)


def test_progress_to_frame_dropped_count_always_none():
    """The demux inbox sets dropped_count later; here it is always None."""
    for progress in (
        ToolProgress.Text("x"),
        ToolProgress.Content([]),
        ToolProgress.Custom("sub", None),
    ):
        assert progress_to_frame(progress, ToolCallId("c1")).dropped_count is None


def test_progress_to_frame_unknown_kind_raises_value_error():
    """Python kind is a free str; an unknown discriminator is a programmer error."""
    progress = ToolProgress(kind="bogus")
    with pytest.raises(ValueError, match="unknown ToolProgress kind"):
        progress_to_frame(progress, ToolCallId("c1"))


def test_progress_to_frame_content_serialization_failure_falls_back_to_null(monkeypatch, caplog):
    """A to_dict failure (serde to_value err) -> Value::default() (Null) + warn."""
    block = ContentBlock.Text("a")

    def boom(self):
        raise RuntimeError("serialize boom")

    monkeypatch.setattr(ContentBlock, "to_dict", boom)
    with caplog.at_level(logging.WARNING):
        frame = progress_to_frame(ToolProgress.Content([block]), ToolCallId("c1"))
    assert frame.kind == "content"
    assert frame.body is None  # Value::default() == Value::Null
    assert any("failed to serialize" in rec.message for rec in caplog.records)
