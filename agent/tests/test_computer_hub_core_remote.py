"""Tests for the R120/R121 computer_hub_core remote module (layer 4).

Covers the migration of ``xai-computer-hub-core/src/remote.rs`` layer 4 —
the wire decode/encode pure functions that turn already-deserialised wire
types into runtime types:

- :func:`progress_from_frame` — :class:`ToolCallProgressFrame` ->
  :class:`ToolProgress` (Custom lift).
- :func:`output_to_value` — :class:`ToolOutputWire` -> JSON value
  (Text -> bare string, Json -> verbatim, Mcp -> ``{"blocks": [...]}``).
- :func:`decode_call_result` — a ``tool_call_result`` body ->
  :class:`TypedToolOutput` (strict decode, cco degrade-to-None, bare-body
  passthrough, ``response_decoding`` failure arm).

Layers 1-3 (``ConnectionClient`` trait / ``RemoteToolProxy`` +
``RemoteTransport`` / ``dispatch_via_connection`` + ``RequestStream``)
land in later rounds; R121 completed the layer-4 error-decode group
(``tool_error_from_wire`` / ``error_from_envelope`` /
``is_workspace_unavailable`` + the private ``_terminal_from_response``),
so these tests now exercise all eight layer-4 seams (R120's three
success-path + R121's four error-decode) in isolation.

The tests mirror Rust's behaviour plus the mapping decisions that
distinguish the Python landing:

- All four functions are **sync** (no ``await``).
- :func:`output_to_value` / :func:`_map_block` dispatch on ``isinstance``
  over the closed union members; the trailing ``TypeError`` arms are
  unreachable for valid wire values (covered by a negative test that
  feeds an alien type).
- :func:`decode_call_result` returns ``TypedToolOutput | ToolError`` and
  never raises — a malformed strict-decode body surfaces as
  ``ToolError.custom("response_decoding", ...)``.
- The cco degrade swallows any shape mismatch (a malformed cco dict and a
  non-object cco both become ``None``).
"""

from __future__ import annotations

import abc
import asyncio
import inspect
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from minimax_code.computer_hub_core.remote import (
    ConnectionClient,
    _request_stream,
    _terminal_from_response,
    _terminal_item,
    decode_call_result,
    dispatch_via_connection,
    error_from_envelope,
    is_workspace_unavailable,
    output_to_value,
    progress_from_frame,
    tool_error_from_wire,
)
from minimax_code.tool_protocol import (
    WORKSPACE_UNAVAILABLE_SUBCODE,
    BehaviorVersionUnsupported,
    Cancelled,
    Custom,
    Execution,
    ImageBlock,
    Internal,
    InvalidArguments,
    Json,
    JsonRpcError,
    JsonRpcIdString,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcVersion,
    Mcp,
    Method,
    PayloadTooLarge,
    PermissionDenied,
    RenderLimited,
    RequestId,
    ResourceBlock,
    ResponseError,
    ResponseResult,
    SessionId,
    SessionMismatch,
    TerminalError,
    Text,
    TextBlock,
    Timeout,
    ToolCallId,
    ToolCallParams,
    ToolCallProgressFrame,
    ToolId,
    ToolNotFound,
    TransportClosed,
    UnsupportedProtocolVersion,
)
from minimax_code.tool_runtime import (
    BehaviorVersion,
    Cwd,
    ToolCallContext,
    ToolChatCompletionResponse,
    ToolError,
    ToolErrorKind,
    ToolStreamItem,
    TypedToolOutput,
)

# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _tid() -> ToolId:
    return ToolId("search__web")


# ---------------------------------------------------------------------------
# progress_from_frame — frame.kind -> Custom subkind, frame.body -> payload.
# ---------------------------------------------------------------------------


def test_progress_from_frame_is_sync():
    assert not inspect.iscoroutinefunction(progress_from_frame)


def test_progress_from_frame_lifts_kind_to_custom_subkind_and_body_to_payload():
    frame = ToolCallProgressFrame(
        tool_call_id="call-1", kind="bash_output_chunk", body={"line": "hello"}
    )
    progress = progress_from_frame(frame)
    assert progress.kind == "custom"
    assert progress.subkind == "bash_output_chunk"
    assert progress.payload == {"line": "hello"}


def test_progress_from_frame_body_may_be_any_json_shape():
    # The body is opaque (serde_json::Value) — strings, lists, scalars all pass.
    for body in ("a string", 42, 3.14, True, None, [1, 2], {"k": "v"}):
        frame = ToolCallProgressFrame(tool_call_id="c", kind="k", body=body)
        assert progress_from_frame(frame).payload == body


def test_progress_from_frame_does_not_touch_tool_call_id_or_dropped_count():
    # Only kind/body are lifted; tool_call_id/dropped_count are not carried
    # onto the ToolProgress (they bookkeep at the frame layer).
    frame = ToolCallProgressFrame(
        tool_call_id="call-9",
        kind="chunk",
        body="x",
        dropped_count=7,
    )
    progress = progress_from_frame(frame)
    assert progress.subkind == "chunk"
    assert progress.payload == "x"


# ---------------------------------------------------------------------------
# output_to_value — Text/Json/Mcp three-arm projection.
# ---------------------------------------------------------------------------


def test_output_to_value_is_sync():
    assert not inspect.iscoroutinefunction(output_to_value)


def test_output_to_value_text_becomes_bare_string():
    assert output_to_value(Text("hello world")) == "hello world"
    assert isinstance(output_to_value(Text("x")), str)


def test_output_to_value_json_forwarded_verbatim():
    payload = {"any": ["arbitrary", 1, True], "json": None}
    assert output_to_value(Json(payload)) is payload
    # Non-dict JSON payloads also pass through untouched.
    assert output_to_value(Json([1, 2, 3])) == [1, 2, 3]
    assert output_to_value(Json(42)) == 42


def test_output_to_value_mcp_reserialised_as_blocks_list():
    value = output_to_value(
        Mcp(blocks=[TextBlock("hi"), ImageBlock(mime_type="image/png", data="b64")])
    )
    assert isinstance(value, dict)
    assert set(value.keys()) == {"blocks"}
    blocks = value["blocks"]
    assert blocks[0] == {"type": "text", "text": "hi"}
    assert blocks[1] == {
        "type": "image",
        "mime_type": "image/png",
        "data": "b64",
    }


def test_output_to_value_mcp_empty_blocks():
    assert output_to_value(Mcp(blocks=[])) == {"blocks": []}


def test_output_to_value_mcp_maps_all_three_block_variants():
    value = output_to_value(
        Mcp(
            blocks=[
                TextBlock("t"),
                ImageBlock(mime_type="image/jpeg", data="d"),
                ResourceBlock(uri="file:///x", mime_type="text/plain", text="hi"),
            ]
        )
    )
    blocks = value["blocks"]
    assert blocks[0] == {"type": "text", "text": "t"}
    assert blocks[1] == {"type": "image", "mime_type": "image/jpeg", "data": "d"}
    assert blocks[2] == {
        "type": "resource",
        "uri": "file:///x",
        "mime_type": "text/plain",
        "text": "hi",
    }


def test_output_to_value_alien_type_raises_typeerror():
    # The union is closed; an alien type hits the unreachable TypeError arm.
    with pytest.raises(TypeError):
        output_to_value("not a ToolOutputWire")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# decode_call_result — bare-body passthrough vs strict tool_call_result decode.
# ---------------------------------------------------------------------------


def test_decode_call_result_is_sync():
    assert not inspect.iscoroutinefunction(decode_call_result)


def test_decode_call_result_bare_body_without_tool_call_id_passes_through():
    # A body with no tool_call_id is a hub-local raw output: from_value as-is.
    value = {"text": "bare output"}
    result = decode_call_result(_tid(), value)
    assert isinstance(result, TypedToolOutput)
    assert result.tool_id == _tid()


def test_decode_call_result_non_dict_body_passes_through():
    # A non-object JSON value (Rust non-Value::Object) -> passthrough.
    result = decode_call_result(_tid(), "just a string")
    assert isinstance(result, TypedToolOutput)
    assert result.tool_id == _tid()


def test_decode_call_result_text_output_round_trips():
    body = {
        "tool_call_id": "call-1",
        "output": {"kind": "text", "value": "hello"},
    }
    result = decode_call_result(_tid(), body)
    assert isinstance(result, TypedToolOutput)
    assert result.tool_id == _tid()
    assert result.value == "hello"  # Text -> bare string


def test_decode_call_result_json_output_round_trips():
    payload = {"n": 1, "list": [2, 3]}
    body = {
        "tool_call_id": "call-2",
        "output": {"kind": "json", "value": payload},
    }
    result = decode_call_result(_tid(), body)
    assert isinstance(result, TypedToolOutput)
    assert result.value == payload  # Json forwarded verbatim


def test_decode_call_result_mcp_output_round_trips_as_blocks():
    body = {
        "tool_call_id": "call-3",
        "output": {
            "kind": "mcp",
            "value": {"blocks": [{"type": "text", "text": "hi"}]},
        },
    }
    result = decode_call_result(_tid(), body)
    assert isinstance(result, TypedToolOutput)
    assert result.value == {"blocks": [{"type": "text", "text": "hi"}]}


# ---------------------------------------------------------------------------
# decode_call_result — chat_completion_output degrade-to-None.
# ---------------------------------------------------------------------------


def test_decode_call_result_parses_well_formed_cco():
    body = {
        "tool_call_id": "call-cco",
        "output": {"kind": "text", "value": "x"},
        "chat_completion_output": {
            "result": {"sender": "assistant", "message": "done"}
        },
    }
    result = decode_call_result(_tid(), body)
    assert isinstance(result, TypedToolOutput)
    assert isinstance(result.chat_completion_output, ToolChatCompletionResponse)
    assert result.chat_completion_output.result is not None
    assert result.chat_completion_output.result.message == "done"


def test_decode_call_result_cco_absent_yields_none():
    body = {
        "tool_call_id": "call-no-cco",
        "output": {"kind": "text", "value": "x"},
    }
    result = decode_call_result(_tid(), body)
    assert isinstance(result, TypedToolOutput)
    assert result.chat_completion_output is None


def test_decode_call_result_cco_non_object_degrades_to_none():
    # A non-object cco (serde cannot deserialise a non-map into a struct).
    body = {
        "tool_call_id": "call-cco-str",
        "output": {"kind": "text", "value": "x"},
        "chat_completion_output": "not a dict",
    }
    result = decode_call_result(_tid(), body)
    assert isinstance(result, TypedToolOutput)
    assert result.chat_completion_output is None


def test_decode_call_result_cco_malformed_dict_degrades_to_none():
    # ToolChatCompletionResponse.from_dict tolerates missing fields, so feed a
    # payload whose inner result/stream_error is a non-dict that the nested
    # from_dict still accepts as None — and confirm the cco survives as a
    # (possibly empty) response rather than raising. The degrade arm is then
    # exercised separately via a patched from_dict below.
    body = {
        "tool_call_id": "call-cco-empty",
        "output": {"kind": "text", "value": "x"},
        "chat_completion_output": {},  # empty -> both fields None
    }
    result = decode_call_result(_tid(), body)
    assert isinstance(result, TypedToolOutput)
    assert isinstance(result.chat_completion_output, ToolChatCompletionResponse)
    assert result.chat_completion_output.result is None
    assert result.chat_completion_output.stream_error is None


def test_decode_call_result_cco_unparseable_degrades_to_none(monkeypatch):
    # Force ToolChatCompletionResponse.from_dict to raise to exercise the
    # `except Exception: return None` degrade arm.
    body = {
        "tool_call_id": "call-cco-bad",
        "output": {"kind": "text", "value": "x"},
        "chat_completion_output": {"will": "explode"},
    }

    def _boom(_data):
        raise ValueError("simulated parse failure")

    monkeypatch.setattr(ToolChatCompletionResponse, "from_dict", _boom)
    result = decode_call_result(_tid(), body)
    assert isinstance(result, TypedToolOutput)
    assert result.chat_completion_output is None


# ---------------------------------------------------------------------------
# decode_call_result — response_decoding failure arm (never raises).
# ---------------------------------------------------------------------------


def test_decode_call_result_missing_output_yields_response_decoding_error():
    body = {"tool_call_id": "call-missing-output"}
    result = decode_call_result(_tid(), body)
    assert isinstance(result, ToolError)
    assert result.details == {"code": "response_decoding"}
    # The detail string carries the underlying error message.
    assert "output" in result.detail


def test_decode_call_result_unknown_output_kind_yields_response_decoding_error():
    body = {
        "tool_call_id": "call-bad-kind",
        "output": {"kind": "not_a_real_kind", "value": "x"},
    }
    result = decode_call_result(_tid(), body)
    assert isinstance(result, ToolError)
    assert result.details == {"code": "response_decoding"}


def test_decode_call_result_output_not_a_dict_yields_response_decoding_error():
    body = {
        "tool_call_id": "call-output-not-dict",
        "output": "not an adjacent-tagged dict",
    }
    result = decode_call_result(_tid(), body)
    assert isinstance(result, ToolError)
    assert result.details == {"code": "response_decoding"}


def test_decode_call_result_missing_tool_call_id_is_passthrough_not_error():
    # A dict without tool_call_id is a bare body -> passthrough, NOT a
    # response_decoding error (the strict arm only fires when tool_call_id
    # IS present but the rest of the body is malformed).
    body = {"output": {"kind": "text", "value": "x"}}
    result = decode_call_result(_tid(), body)
    assert isinstance(result, TypedToolOutput)
    assert result.tool_id == _tid()
    # Passthrough feeds the *whole* body to from_value as the raw value (it
    # is NOT decoded as a tool_call_result — no tool_call_id), so the value
    # is the body verbatim and no response_decoding error is surfaced.


def test_decode_call_result_failure_arm_never_raises_for_alien_value():
    # Even a deeply malformed body must surface as ToolError, not raise.
    body = {"tool_call_id": "x", "output": object()}  # type: ignore[dict-item]
    result = decode_call_result(_tid(), body)
    assert isinstance(result, ToolError)
    assert result.details == {"code": "response_decoding"}


# ---------------------------------------------------------------------------
# Cross-check: ToolError.custom(code, detail) -> details == {"code": code}.
# ---------------------------------------------------------------------------


def test_tool_error_custom_details_shape_used_by_response_decoding():
    # The failure arm relies on ToolError.custom(code, detail) populating
    # details == {"code": code}; pin that contract here.
    err = ToolError.custom("response_decoding", "boom")
    assert err.details == {"code": "response_decoding"}
    assert err.detail == "boom"


# ===========================================================================
# R121 — tool_error_from_wire (14-variant ToolErrorWire -> ToolError).
# ===========================================================================


def test_tool_error_from_wire_is_sync():
    assert not inspect.iscoroutinefunction(tool_error_from_wire)


def test_tool_error_from_wire_invalid_arguments_with_details():
    wire = InvalidArguments(message="missing q", details={"field": "q"})
    err = tool_error_from_wire(wire)
    assert isinstance(err, ToolError)
    assert err.kind == ToolErrorKind.INVALID_ARGUMENTS
    assert err.detail == "missing q"
    # with_details replaces; invalid_arguments installs no details, so the
    # wire's details land verbatim.
    assert err.details == {"field": "q"}


def test_tool_error_from_wire_invalid_arguments_without_details():
    err = tool_error_from_wire(InvalidArguments(message="bad"))
    assert err.kind == ToolErrorKind.INVALID_ARGUMENTS
    # No wire details -> the error keeps whatever invalid_arguments installed.
    assert err.details is None


def test_tool_error_from_wire_tool_not_found():
    err = tool_error_from_wire(ToolNotFound(tool_id=_tid()))
    assert err.kind == ToolErrorKind.NOT_FOUND
    assert err.detail == f"tool not found: {_tid()}"
    assert err.details == {"tool_id": str(_tid())}


def test_tool_error_from_wire_permission_denied():
    err = tool_error_from_wire(PermissionDenied(reason="not allowed"))
    assert err.kind == ToolErrorKind.PERMISSION_DENIED
    assert err.detail == "not allowed"


def test_tool_error_from_wire_timeout_carries_elapsed_ms_in_details():
    err = tool_error_from_wire(Timeout(tool_id=_tid(), elapsed_ms=1500))
    assert err.kind == ToolErrorKind.TIMEOUT
    assert err.detail == "timed out after 1500ms"
    assert err.details == {"tool_id": str(_tid()), "elapsed_ms": 1500}


def test_tool_error_from_wire_cancelled():
    err = tool_error_from_wire(Cancelled(tool_id=_tid()))
    assert err.kind == ToolErrorKind.CANCELLED
    assert err.detail == "cancelled"
    assert err.details == {"tool_id": str(_tid())}


def test_tool_error_from_wire_execution():
    err = tool_error_from_wire(Execution(tool_id=_tid(), message="boom"))
    assert err.kind == ToolErrorKind.EXECUTION
    assert err.detail == "boom"
    assert err.details == {"tool_id": str(_tid())}


def test_tool_error_from_wire_behavior_version_unsupported():
    err = tool_error_from_wire(
        BehaviorVersionUnsupported(tool_id=_tid(), requested="2.0")
    )
    assert err.kind == ToolErrorKind.BEHAVIOR_VERSION_UNSUPPORTED
    assert err.detail == "behavior version 2.0 not supported"
    assert err.details == {"tool_id": str(_tid()), "requested": "2.0"}


def test_tool_error_from_wire_render_limited_with_card_id():
    err = tool_error_from_wire(
        RenderLimited(tool_id=_tid(), reason="too big", card_id="card-7")
    )
    assert err.kind == ToolErrorKind.RENDER_LIMITED
    assert err.detail == "too big"
    assert err.details == {"tool_id": str(_tid()), "card_id": "card-7"}


def test_tool_error_from_wire_render_limited_null_card_id():
    # card_id defaults to None -> null in details (Rust json!(...) emits
    # Value::Null for Option::None).
    err = tool_error_from_wire(RenderLimited(tool_id=_tid(), reason="too big"))
    assert err.details == {"tool_id": str(_tid()), "card_id": None}


def test_tool_error_from_wire_terminal_error():
    err = tool_error_from_wire(TerminalError(tool_id=_tid(), message="fatal"))
    assert err.kind == ToolErrorKind.TERMINAL_ERROR
    assert err.detail == "fatal"
    assert err.details == {"tool_id": str(_tid())}


def test_tool_error_from_wire_custom_with_details():
    err = tool_error_from_wire(
        Custom(subcode="my_code", message="oops", details={"x": 1})
    )
    assert err.kind == ToolErrorKind.CUSTOM
    assert err.detail == "oops"
    # with_details replaces custom's {"code": "my_code"} with the wire details.
    assert err.details == {"x": 1}


def test_tool_error_from_wire_custom_without_details_keeps_code():
    err = tool_error_from_wire(Custom(subcode="my_code", message="oops"))
    assert err.kind == ToolErrorKind.CUSTOM
    # No wire details -> custom's {"code": "my_code"} survives.
    assert err.details == {"code": "my_code"}


def test_tool_error_from_wire_session_mismatch():
    err = tool_error_from_wire(SessionMismatch())
    assert err.kind == ToolErrorKind.CUSTOM
    assert err.detail == "session mismatch"
    assert err.details == {"code": "session_mismatch"}


def test_tool_error_from_wire_transport_closed():
    err = tool_error_from_wire(TransportClosed(tool_id=_tid()))
    assert err.kind == ToolErrorKind.NETWORK_ERROR
    assert err.detail == f"transport closed for {_tid()}"


def test_tool_error_from_wire_unsupported_protocol_version():
    err = tool_error_from_wire(UnsupportedProtocolVersion(supported=["1.0", "2.0"]))
    assert err.kind == ToolErrorKind.CUSTOM
    # Rust Debug {supported:?} -> Python repr {supported!r}.
    assert err.detail == "supported versions: " + repr(["1.0", "2.0"])
    assert err.details == {"code": "unsupported_protocol_version"}


def test_tool_error_from_wire_payload_too_large():
    err = tool_error_from_wire(PayloadTooLarge(bytes=9999, limit=4096))
    assert err.kind == ToolErrorKind.CUSTOM
    assert err.detail == "payload 9999 bytes exceeds limit 4096"
    assert err.details == {"code": "payload_too_large"}


def test_tool_error_from_wire_internal_with_request_id_reinstalls_code():
    err = tool_error_from_wire(
        Internal(request_id=RequestId("req-9"), detail="kaboom")
    )
    assert err.kind == ToolErrorKind.CUSTOM
    assert err.detail == "kaboom"
    # with_details replaces custom's {"code": "internal_error"} so the new
    # details must re-carry "code" alongside request_id (Rust's explicit
    # re-install).
    assert err.details == {"code": "internal_error", "request_id": "req-9"}


def test_tool_error_from_wire_internal_without_request_id_keeps_code():
    err = tool_error_from_wire(Internal(detail="kaboom"))
    assert err.detail == "kaboom"
    # No request_id -> custom's {"code": "internal_error"} survives.
    assert err.details == {"code": "internal_error"}


def test_tool_error_from_wire_internal_fallback_detail_when_none():
    # detail=None -> Rust Option::unwrap_or_else -> "internal router error".
    err = tool_error_from_wire(Internal())
    assert err.detail == "internal router error"
    assert err.details == {"code": "internal_error"}


# ===========================================================================
# R121 — error_from_envelope (JsonRpcError -> ToolError).
# ===========================================================================


def test_error_from_envelope_is_sync():
    assert not inspect.iscoroutinefunction(error_from_envelope)


def test_error_from_envelope_wire_arm_decodes_known_tool_error():
    # data carries a serialised ToolErrorWire -> wire arm -> tool_error_from_wire.
    wire_dict = InvalidArguments(message="bad args").to_wire()
    err = error_from_envelope(JsonRpcError(code=-32000, message="x", data=wire_dict))
    assert err.kind == ToolErrorKind.INVALID_ARGUMENTS
    assert err.detail == "bad args"


def test_error_from_envelope_non_dict_data_falls_back():
    # data is a string (serde cannot deserialise a non-map into the wire
    # struct) -> fallback custom("jsonrpc_{code}") + with_details(data).
    err = error_from_envelope(JsonRpcError(code=-32001, message="nope", data="oops"))
    assert err.kind == ToolErrorKind.CUSTOM
    assert err.detail == "nope"
    assert err.details == "oops"  # with_details replaces {"code": ...} with data


def test_error_from_envelope_none_data_falls_back_without_replace():
    # data=None -> fallback, no with_details -> custom's {"code"} survives.
    err = error_from_envelope(JsonRpcError(code=-32002, message="absent"))
    assert err.kind == ToolErrorKind.CUSTOM
    assert err.detail == "absent"
    assert err.details == {"code": "jsonrpc_-32002"}


def test_error_from_envelope_dict_unknown_code_falls_back():
    # data is a dict but the code tag is unknown -> wire decode raises ->
    # fallback, with_details(data) replaces {"code": ...}.
    err = error_from_envelope(
        JsonRpcError(code=-32003, message="m", data={"code": "not_a_real_code"})
    )
    assert err.kind == ToolErrorKind.CUSTOM
    assert err.detail == "m"
    assert err.details == {"code": "not_a_real_code"}


def test_error_from_envelope_fallback_embeds_numeric_code_in_subcode():
    err = error_from_envelope(JsonRpcError(code=42, message="m"))
    assert err.detail == "m"
    assert err.details == {"code": "jsonrpc_42"}


# ===========================================================================
# R121 — is_workspace_unavailable.
# ===========================================================================


def test_is_workspace_unavailable_is_sync():
    assert not inspect.iscoroutinefunction(is_workspace_unavailable)


def test_is_workspace_unavailable_true_for_custom_with_subcode():
    err = ToolError.custom(WORKSPACE_UNAVAILABLE_SUBCODE, "workspace gone")
    assert is_workspace_unavailable(err) is True


def test_is_workspace_unavailable_false_for_non_custom_kind():
    err = ToolError.invalid_arguments("x")
    assert is_workspace_unavailable(err) is False


def test_is_workspace_unavailable_false_for_details_not_dict():
    err = ToolError.custom("x", "y")
    err.details = "not a dict"
    assert is_workspace_unavailable(err) is False


def test_is_workspace_unavailable_false_for_code_mismatch():
    err = ToolError.custom("some_other_code", "y")
    assert is_workspace_unavailable(err) is False


def test_is_workspace_unavailable_false_for_non_string_code():
    err = ToolError.custom("x", "y")
    err.details = {"code": 123}
    assert is_workspace_unavailable(err) is False


# ===========================================================================
# R121 — _terminal_from_response (JsonRpcResponse -> TypedToolOutput | ToolError).
# ===========================================================================


def test_terminal_from_response_is_sync():
    assert not inspect.iscoroutinefunction(_terminal_from_response)


def test_terminal_from_response_result_arm_delegates_to_decode_call_result():
    body = {"tool_call_id": "call-1", "output": {"kind": "text", "value": "hi"}}
    resp = JsonRpcResponse(
        jsonrpc=JsonRpcVersion(),
        id="resp-1",
        outcome=ResponseResult(value=body),
    )
    result = _terminal_from_response(_tid(), resp)
    assert isinstance(result, TypedToolOutput)
    assert result.tool_id == _tid()
    assert result.value == "hi"


def test_terminal_from_response_error_arm_delegates_to_error_from_envelope():
    wire_dict = InvalidArguments(message="bad").to_wire()
    resp = JsonRpcResponse(
        jsonrpc=JsonRpcVersion(),
        id="resp-2",
        outcome=ResponseError(
            error=JsonRpcError(code=-32000, message="x", data=wire_dict)
        ),
    )
    result = _terminal_from_response(_tid(), resp)
    assert isinstance(result, ToolError)
    assert result.kind == ToolErrorKind.INVALID_ARGUMENTS


def test_terminal_from_response_result_arm_malformed_surfaces_tool_error():
    # A malformed body in the Result arm surfaces as the ToolError that
    # decode_call_result returns (response_decoding); _terminal_from_response
    # does NOT swallow it.
    body = {"tool_call_id": "x"}  # missing output
    resp = JsonRpcResponse(
        jsonrpc=JsonRpcVersion(),
        id="resp-3",
        outcome=ResponseResult(value=body),
    )
    result = _terminal_from_response(_tid(), resp)
    assert isinstance(result, ToolError)
    assert result.details == {"code": "response_decoding"}


# ===========================================================================
# R122 — ConnectionClient ABC (layer 1).
#
# The object-safe connection contract: three async methods (request /
# subscribe_progress / notify). These tests pin the trait-shape invariants
# the layers below (RemoteToolProxy / dispatch_via_connection, later
# rounds) will rely on:
#
# - subclass of :class:`abc.ABC` with exactly three abstract methods;
# - direct / partial instantiation is refused;
# - all three methods are coroutine functions (the ``#[async_trait]`` ->
#   ``async def`` mapping);
# - the ``Result<T, ToolError>`` arms annotate as ``T | ToolError`` and
#   ``BoxStream`` annotates as :class:`AsyncIterator`;
# - a concrete recording implementation drives each method end-to-end.
# ===========================================================================


def _req(method: str = "tool_call_request", rid: str = "req-1") -> JsonRpcRequest:
    """Build a minimal :class:`JsonRpcRequest` for behaviour tests."""
    return JsonRpcRequest(
        jsonrpc=JsonRpcVersion(),
        id=rid,
        method=method,
        params={"tool_call_id": "c", "args": {}},
    )


def _notif(method: str = "cancel") -> JsonRpcNotification:
    """Build a minimal :class:`JsonRpcNotification` for behaviour tests."""
    return JsonRpcNotification(
        jsonrpc=JsonRpcVersion(),
        method=method,
        params={"tool_call_id": "c"},
    )


def _tcid(s: str = "call-1") -> ToolCallId:
    return ToolCallId(s)


def _frame(
    tcid: ToolCallId | None = None,
    kind: str = "chunk",
    body: object | None = None,
) -> ToolCallProgressFrame:
    return ToolCallProgressFrame(
        tool_call_id=tcid or _tcid(),
        kind=kind,
        body=body if body is not None else {"n": 1},
    )


class _RecordingConnection(ConnectionClient):
    """Minimal concrete :class:`ConnectionClient` for behaviour tests.

    Records every call and returns programmer-fed outcomes.
    :meth:`subscribe_progress` serves a registered list of frames as an
    async iterator (the Rust ``BoxStream`` -> Python ``AsyncIterator``
    mapping): awaiting the coroutine resolves to the iterator, exactly as
    Rust's ``async fn -> BoxStream`` future resolves to the stream.
    """

    def __init__(self) -> None:
        self.requests: list[JsonRpcRequest] = []
        self.notifies: list[JsonRpcNotification] = []
        self.subscribed: list[ToolCallId] = []
        self.next_response: JsonRpcResponse | ToolError = JsonRpcResponse(
            jsonrpc=JsonRpcVersion(),
            id="resp-1",
            outcome=ResponseResult(
                value={"tool_call_id": "c", "output": {"kind": "text", "value": "hi"}}
            ),
        )
        self.next_notify_outcome: None | ToolError = None
        self.frames: dict[ToolCallId, list[ToolCallProgressFrame]] = {}

    async def request(self, request: JsonRpcRequest) -> JsonRpcResponse | ToolError:
        self.requests.append(request)
        return self.next_response

    async def subscribe_progress(
        self, tool_call_id: ToolCallId
    ) -> AsyncIterator[ToolCallProgressFrame]:
        self.subscribed.append(tool_call_id)
        frames = list(self.frames.get(tool_call_id, []))

        async def _gen():  # noqa: ANN202 - test helper inner generator
            for f in frames:
                yield f

        return _gen()

    async def notify(self, notification: JsonRpcNotification) -> None | ToolError:
        self.notifies.append(notification)
        return self.next_notify_outcome


# ---------------------------------------------------------------------------
# ABC shape / abstractness.
# ---------------------------------------------------------------------------


def test_connection_client_is_abc_subclass():
    assert issubclass(ConnectionClient, abc.ABC)


def test_three_abstract_methods_named():
    assert ConnectionClient.__abstractmethods__ == frozenset(
        {"request", "subscribe_progress", "notify"}
    )


def test_cannot_instantiate_abc_directly():
    with pytest.raises(TypeError):
        ConnectionClient()


def test_partial_impl_missing_one_still_abstract():
    class _Two(ConnectionClient):
        async def request(self, request):  # noqa: ANN001
            ...

        async def subscribe_progress(self, tool_call_id):  # noqa: ANN001
            ...

    assert _Two.__abstractmethods__ == frozenset({"notify"})
    with pytest.raises(TypeError):
        _Two()


def test_partial_impl_missing_two_still_abstract():
    class _One(ConnectionClient):
        async def notify(self, notification):  # noqa: ANN001
            ...

    assert _One.__abstractmethods__ == frozenset({"request", "subscribe_progress"})
    with pytest.raises(TypeError):
        _One()


def test_concrete_impl_instantiable_no_abstractmethods():
    assert _RecordingConnection.__abstractmethods__ == set()
    conn = _RecordingConnection()
    assert isinstance(conn, ConnectionClient)


# ---------------------------------------------------------------------------
# #[async_trait] -> async def: all three are coroutine functions.
# ---------------------------------------------------------------------------


def test_all_three_methods_are_coroutine_functions():
    assert inspect.iscoroutinefunction(ConnectionClient.request)
    assert inspect.iscoroutinefunction(ConnectionClient.subscribe_progress)
    assert inspect.iscoroutinefunction(ConnectionClient.notify)


# ---------------------------------------------------------------------------
# Return annotations — Result<T, ToolError> -> T | ToolError; BoxStream ->
# AsyncIterator[ToolCallProgressFrame]. (Source-string form under
# ``from __future__ import annotations``.)
# ---------------------------------------------------------------------------


def test_request_return_annotation_union_response_or_error():
    ann = ConnectionClient.request.__annotations__["return"]
    assert "JsonRpcResponse" in ann
    assert "ToolError" in ann


def test_notify_return_annotation_union_none_or_error():
    ann = ConnectionClient.notify.__annotations__["return"]
    assert "None" in ann
    assert "ToolError" in ann


def test_subscribe_progress_return_annotation_async_iterator_of_frame():
    ann = ConnectionClient.subscribe_progress.__annotations__["return"]
    assert "AsyncIterator" in ann
    assert "ToolCallProgressFrame" in ann


# ---------------------------------------------------------------------------
# request — JsonRpcRequest -> JsonRpcResponse | ToolError.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_success_returns_response():
    conn = _RecordingConnection()
    result = await conn.request(_req())
    assert isinstance(result, JsonRpcResponse)


@pytest.mark.asyncio
async def test_request_failure_returns_tool_error():
    conn = _RecordingConnection()
    conn.next_response = ToolError.custom("network_error", "connection reset")
    result = await conn.request(_req())
    assert isinstance(result, ToolError)


@pytest.mark.asyncio
async def test_request_records_arg_verbatim():
    conn = _RecordingConnection()
    req = _req(method="tool_call_request", rid="abc-7")
    await conn.request(req)
    assert conn.requests == [req]


# ---------------------------------------------------------------------------
# subscribe_progress — ToolCallId -> AsyncIterator[ToolCallProgressFrame].
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_progress_coroutine_resolves_to_async_iterator():
    # Rust contract: ``async fn -> BoxStream``. The call returns a coroutine;
    # awaiting it resolves to the stream (an async-iterable object).
    conn = _RecordingConnection()
    coro = conn.subscribe_progress(_tcid())
    assert inspect.iscoroutine(coro)
    stream = await coro
    assert hasattr(stream, "__aiter__")


@pytest.mark.asyncio
async def test_subscribe_progress_yields_registered_frames_in_order():
    conn = _RecordingConnection()
    tcid = _tcid("call-9")
    conn.frames[tcid] = [
        _frame(tcid, "chunk", {"n": 1}),
        _frame(tcid, "log", {"line": "x"}),
    ]
    stream = await conn.subscribe_progress(tcid)
    collected = [f async for f in stream]
    assert [f.kind for f in collected] == ["chunk", "log"]
    assert all(f.tool_call_id == tcid for f in collected)


@pytest.mark.asyncio
async def test_subscribe_progress_empty_stream_terminates():
    conn = _RecordingConnection()
    stream = await conn.subscribe_progress(_tcid("no-frames"))
    assert [f async for f in stream] == []


@pytest.mark.asyncio
async def test_subscribe_progress_records_arg():
    conn = _RecordingConnection()
    tcid = _tcid("call-rec")
    await conn.subscribe_progress(tcid)
    assert conn.subscribed == [tcid]


# ---------------------------------------------------------------------------
# notify — JsonRpcNotification -> None | ToolError.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_success_returns_none():
    conn = _RecordingConnection()
    result = await conn.notify(_notif())
    assert result is None


@pytest.mark.asyncio
async def test_notify_failure_returns_tool_error():
    conn = _RecordingConnection()
    conn.next_notify_outcome = ToolError.custom("network_error", "write failed")
    result = await conn.notify(_notif())
    assert isinstance(result, ToolError)


@pytest.mark.asyncio
async def test_notify_records_arg_verbatim():
    conn = _RecordingConnection()
    n = _notif(method="cancel")
    await conn.notify(n)
    assert conn.notifies == [n]


# ===========================================================================
# R123 — layer 3: _request_stream (impl Stream for RequestStream) +
# _terminal_item (the terminal-construction poll arm). The private async
# generator interleaves wire progress frames with the terminal JSON-RPC
# response, preserving the four poll-time invariants of Rust's hand-written
# poll_next: request priority, progress backfill, exactly one terminal, and
# progress-closed-is-not-stream-closed.
# ===========================================================================


# ---------------------------------------------------------------------------
# R123 fixtures — scripted progress + future-backed request.
# ---------------------------------------------------------------------------


class _ScriptedProgress:
    """Async iterator yielding a scripted frame list, then closing.

    Each frame-yielding ``__anext__`` awaits one ``asyncio.sleep(0)`` so a
    concurrently-resolving request future can win the ``asyncio.wait`` race
    inside :func:`_request_stream`. The closing ``__anext__`` (past the
    scripted list) raises ``StopAsyncIteration`` WITHOUT sleeping — the
    Rust ``BoxStream`` close maps to an immediate async-iterator close.
    """

    def __init__(self, frames: list[ToolCallProgressFrame] | None = None) -> None:
        self._frames = list(frames or [])
        self._i = 0

    def __aiter__(self) -> _ScriptedProgress:
        return self

    async def __anext__(self) -> ToolCallProgressFrame:
        if self._i >= len(self._frames):
            raise StopAsyncIteration
        await asyncio.sleep(0)
        frame = self._frames[self._i]
        self._i += 1
        return frame


def _ok_resp(rid: str = "resp-1") -> JsonRpcResponse:
    """A success :class:`JsonRpcResponse` carrying a text-output body."""
    return JsonRpcResponse(
        jsonrpc=JsonRpcVersion(),
        id=rid,
        outcome=ResponseResult(
            value={"tool_call_id": "c", "output": {"kind": "text", "value": "hi"}}
        ),
    )


async def _resolve_after(fut: asyncio.Future, value: object, cycles: int) -> None:
    """Resolve ``fut`` with ``value`` after ``cycles`` event-loop ticks.

    Background-scheduled via :func:`asyncio.ensure_future` so the request
    future resolves mid-stream (after scripted progress frames have landed),
    letting the tests exercise the progress-backfill -> terminal ordering
    without hand-driving each ``__anext__``.
    """
    for _ in range(cycles):
        await asyncio.sleep(0)
    if not fut.done():
        fut.set_result(value)


async def _collect(gen: AsyncIterator[ToolStreamItem]) -> list[ToolStreamItem]:
    """Drain an async generator into a list (test readability helper)."""
    return [item async for item in gen]


# ---------------------------------------------------------------------------
# _terminal_item — Result<JsonRpcResponse, ToolError> dispatch (poll arm).
# ---------------------------------------------------------------------------


def test_terminal_item_is_sync():
    assert not inspect.iscoroutinefunction(_terminal_item)


def test_terminal_item_decodes_jsonrpc_response_via_terminal_from_response():
    # The Ok arm: isinstance(JsonRpcResponse) -> _terminal_from_response.
    tid = _tid()
    result = _terminal_item(tid, _ok_resp())
    assert isinstance(result, TypedToolOutput)
    assert not isinstance(result, ToolError)


def test_terminal_item_passes_tool_error_through_unchanged():
    # The Err arm: ``Err(err) => Err(err)`` identity passthrough.
    tid = _tid()
    err = ToolError.custom("boom", "oops")
    result = _terminal_item(tid, err)
    assert result is err


# ---------------------------------------------------------------------------
# _request_stream — async-generator shape.
# ---------------------------------------------------------------------------


def test_request_stream_is_async_generator_function():
    # Rust ``impl Stream`` -> Python ``async def`` generator function.
    assert inspect.isasyncgenfunction(_request_stream)


@pytest.mark.asyncio
async def test_request_stream_call_returns_async_generator_object():
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(_ok_resp())
    gen = _request_stream(_tid(), _ScriptedProgress([]), fut)
    assert inspect.isasyncgen(gen)
    await gen.aclose()


# ---------------------------------------------------------------------------
# Invariant 1 — request priority: a ready response short-circuits and drops
# any concurrent/late progress (Rust polls request before progress).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_immediate_terminal_drops_all_progress():
    # Response preset to resolved; two scripted frames must never surface.
    tid = _tid()
    progress = _ScriptedProgress(
        [_frame(kind="chunk", body={"n": 1}), _frame(kind="chunk", body={"n": 2})]
    )
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(_ok_resp())
    items = await _collect(_request_stream(tid, progress, fut))
    assert [item.kind for item in items] == ["terminal"]
    assert isinstance(items[0].terminal, TypedToolOutput)


# ---------------------------------------------------------------------------
# Invariant 2 — progress backfill: while the response is pending, frames are
# lifted (one per cycle) to Progress items via progress_from_frame.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_frames_backfill_while_request_pending():
    # Hand-stepped __anext__ so the request future STAYS pending across both
    # frame cycles — no event-loop scheduling jitter can let the request win
    # early. Decouples the progress-backfill ordering from timing.
    tid = _tid()
    progress = _ScriptedProgress(
        [_frame(kind="chunk", body={"n": 1}), _frame(kind="log", body={"line": "x"})]
    )
    fut = asyncio.get_running_loop().create_future()  # stays pending
    gen = _request_stream(tid, progress, fut)
    # Cycle 1 + 2: progress frames win (request still pending).
    item1 = await gen.__anext__()
    assert item1.kind == "progress"
    assert item1.progress is not None
    item2 = await gen.__anext__()
    assert item2.kind == "progress"
    assert item2.progress is not None
    # Now resolve the request -> terminal (request priority).
    fut.set_result(_ok_resp())
    item3 = await gen.__anext__()
    assert item3.kind == "terminal"
    assert isinstance(item3.terminal, TypedToolOutput)
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


# ---------------------------------------------------------------------------
# Invariant 4 — progress-closed is NOT stream-closed: a closed progress
# stream keeps the generator awaiting the still-pending response (Rust's
# Ready(None)|Pending => Pending arm).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_progress_keeps_awaiting_then_emits_terminal():
    tid = _tid()
    progress = _ScriptedProgress([])  # closes on first __anext__
    fut = asyncio.get_running_loop().create_future()
    asyncio.ensure_future(_resolve_after(fut, _ok_resp(), cycles=3))
    items = await _collect(_request_stream(tid, progress, fut))
    assert [item.kind for item in items] == ["terminal"]
    assert isinstance(items[0].terminal, TypedToolOutput)


@pytest.mark.asyncio
async def test_frame_then_close_then_late_response():
    # Hand-stepped: frame 1 via __anext__ (request pending), then anext 2
    # closes progress -> progress_done -> await fut (BLOCKS until the
    # background resolver releases it).
    tid = _tid()
    progress = _ScriptedProgress([_frame(kind="chunk", body={"n": 1})])
    fut = asyncio.get_running_loop().create_future()
    gen = _request_stream(tid, progress, fut)
    item1 = await gen.__anext__()
    assert item1.kind == "progress"
    asyncio.ensure_future(_resolve_after(fut, _ok_resp(), cycles=2))
    item2 = await gen.__anext__()
    assert item2.kind == "terminal"
    assert isinstance(item2.terminal, TypedToolOutput)


# ---------------------------------------------------------------------------
# Request error arm — a ToolError response surfaces as an error Terminal
# (_terminal_item's Err passthrough).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_error_surfaces_as_error_terminal():
    # Hand-stepped: progress frame first, then resolve the request to a
    # ToolError -> error terminal via _terminal_item's Err passthrough.
    tid = _tid()
    progress = _ScriptedProgress([_frame()])
    err = ToolError.custom("network_error", "connection reset")
    fut = asyncio.get_running_loop().create_future()
    gen = _request_stream(tid, progress, fut)
    item1 = await gen.__anext__()
    assert item1.kind == "progress"
    fut.set_result(err)
    item2 = await gen.__anext__()
    assert item2.kind == "terminal"
    assert item2.is_error()
    assert item2.terminal is err


# ---------------------------------------------------------------------------
# Invariant 3 — exactly one terminal: after the Terminal the generator
# exhausts (StopAsyncIteration), never emitting a second terminal.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exactly_one_terminal_then_stopasynciteration():
    # Hand-stepped: progress, then resolve -> terminal, then the generator
    # must exhaust (done flag short-circuit) rather than emit a 2nd terminal.
    tid = _tid()
    progress = _ScriptedProgress([_frame()])
    fut = asyncio.get_running_loop().create_future()
    gen = _request_stream(tid, progress, fut)
    assert (await gen.__anext__()).kind == "progress"
    fut.set_result(_ok_resp())
    assert (await gen.__anext__()).kind == "terminal"
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


# ---------------------------------------------------------------------------
# Cancellation — aclose mid-stream cancels the still-pending request future
# (the finally block), so an abandoned consumer does not leak the request.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_mid_stream_cancels_pending_request():
    tid = _tid()
    progress = _ScriptedProgress([_frame(), _frame()])
    fut = asyncio.get_running_loop().create_future()  # never resolved
    gen = _request_stream(tid, progress, fut)
    assert (await gen.__anext__()).kind == "progress"
    await gen.aclose()
    assert fut.cancelled()


# ===========================================================================
# R124 — layer 3: dispatch_via_connection assembler. The pub(crate) fn lifts
# a runtime (tool_id, arguments, ctx) triple into a tool_call_request
# JSON-RPC envelope, forwards it over a ConnectionClient, and hands back the
# interleaved progress/response stream _request_stream drives. The six-step
# assembly (ctx extraction -> subscribe-before-send -> ToolCallParams ->
# JsonRpcRequest -> request-not-awaited -> _request_stream assemble) mirrors
# Rust's six statements; the Python landing is an async def (not a sync fn)
# because _request_stream takes a resolved progress iterator, so the
# subscribe future must materialise at assembly time.
# ===========================================================================


# ---------------------------------------------------------------------------
# R124 fixtures — call id / session / ctx / scripted-connection builders.
# ---------------------------------------------------------------------------


def _call_id() -> ToolCallId:
    return ToolCallId("call-9")


def _session_id() -> SessionId:
    return SessionId("session-7")


def _ctx(
    call_id: ToolCallId | None = None,
    *,
    cwd: Path | None = None,
    behavior: str | None = None,
) -> ToolCallContext:
    """Build a ToolCallContext, optionally installing Cwd/BehaviorVersion.

    Mirrors how a layer-2 caller threads the per-call context: the call_id
    becomes both the params' tool_call_id and the subscribe key, and the
    typed extensions project into the params wire fields.
    """
    ctx = ToolCallContext.new(call_id or _call_id())
    if cwd is not None:
        ctx.insert(Cwd(cwd))
    if behavior is not None:
        ctx.insert(BehaviorVersion(behavior))
    return ctx


class _ScriptedConn(_RecordingConnection):
    """Connection whose progress is scripted and whose response is future-driven.

    subscribe_progress returns a :class:`_ScriptedProgress` (one sleep per
    frame) and request awaits a caller-driven future, so an end-to-end
    dispatch_via_connection reproduces R123's progress-backfill-then-terminal
    ordering: the scripted progress drains frame by frame while the response
    future is resolved in the background a few cycles later.
    """

    def __init__(self, frames, resp_future):
        super().__init__()
        self._frames = list(frames)
        self._resp_future = resp_future

    async def subscribe_progress(self, tool_call_id):
        self.subscribed.append(tool_call_id)
        return _ScriptedProgress(self._frames)

    async def request(self, request):
        self.requests.append(request)
        return await self._resp_future


# ---------------------------------------------------------------------------
# Shape — async def returning an async iterator (Rust sync fn -> BoxStream).
# ---------------------------------------------------------------------------


def test_dispatch_via_connection_is_coroutine_function():
    # Python landing is `async def` (not a sync fn like Rust) because the
    # subscribe future must resolve at assembly time before _request_stream
    # takes the resolved progress iterator.
    assert inspect.iscoroutinefunction(dispatch_via_connection)


@pytest.mark.asyncio
async def test_dispatch_via_connection_returns_async_iterator():
    conn = _RecordingConnection()
    gen = await dispatch_via_connection(
        conn, _tid(), _session_id(), {"q": 1}, _ctx()
    )
    assert hasattr(gen, "__aiter__")
    # Drive the stream to its terminal: dispatch_via_connection creates the
    # request coroutine at assembly time (Rust lazy-future semantics — the
    # body only runs when _request_stream is polled), so consuming the gen
    # both proves the iterator is real AND avoids a never-awaited coroutine.
    await _collect(gen)


# ---------------------------------------------------------------------------
# Step 1 — ctx extension extraction (Cwd/BehaviorVersion -> params wire
# fields; wire-omitted when the extension is absent).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step1_cwd_and_behavior_version_projected_into_params():
    conn = _RecordingConnection()
    cwd = Path("/tmp/work")
    gen = await dispatch_via_connection(
        conn,
        _tid(),
        _session_id(),
        {"q": 1},
        _ctx(cwd=cwd, behavior="2026-07"),
    )
    await _collect(gen)  # drive to terminal -> request body executes
    assert len(conn.requests) == 1
    params = conn.requests[0].params
    # Cwd projects as str(path); BehaviorVersion as the version string.
    assert params["cwd"] == str(cwd)
    assert params["behavior_version"] == "2026-07"
    # deadline_ms / trace_context are None on the forwarding path -> wire-omitted.
    assert "deadline_ms" not in params
    assert "trace_context" not in params


@pytest.mark.asyncio
async def test_step1_extensions_absent_wire_omitted():
    # ctx with no Cwd/BehaviorVersion -> both fields None -> wire-omitted
    # (ToolCallParams.to_wire skips None deadline/behavior/cwd/trace).
    conn = _RecordingConnection()
    gen = await dispatch_via_connection(
        conn, _tid(), _session_id(), {"q": 1}, _ctx()
    )
    await _collect(gen)  # drive to terminal -> request body executes
    params = conn.requests[0].params
    assert "cwd" not in params
    assert "behavior_version" not in params


@pytest.mark.asyncio
async def test_step1_call_id_threads_into_params_and_subscribe_key():
    # ctx.call_id is BOTH the params' tool_call_id AND the subscribe key
    # (Rust uses the same id for the wire field and subscribe_progress).
    conn = _RecordingConnection()
    cid = _call_id()
    gen = await dispatch_via_connection(
        conn, _tid(), _session_id(), {"q": 1}, _ctx(call_id=cid)
    )
    await _collect(gen)  # drive to terminal -> request body executes
    assert conn.requests[0].params["tool_call_id"] == cid
    assert conn.subscribed == [cid]


# ---------------------------------------------------------------------------
# Step 2 — subscribe_progress BEFORE request (R122 contract: subscribers
# must register before the request is sent or early progress frames lost).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step2_subscribes_before_sending_request():
    order: list[str] = []

    class _OrderConn(_RecordingConnection):
        async def subscribe_progress(self, tool_call_id):
            order.append("subscribe")
            return await super().subscribe_progress(tool_call_id)

        async def request(self, request):
            order.append("request")
            return await super().request(request)

    conn = _OrderConn()
    gen = await dispatch_via_connection(
        conn, _tid(), _session_id(), {"q": 1}, _ctx()
    )
    await _collect(gen)  # drive to terminal -> request body executes
    assert order == ["subscribe", "request"]


# ---------------------------------------------------------------------------
# Steps 3/4 — ToolCallParams + JsonRpcRequest construction.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step34_request_envelope_shape():
    conn = _RecordingConnection()
    sid = _session_id()
    tid = _tid()
    args = {"q": "hello", "n": 2}
    gen = await dispatch_via_connection(conn, tid, sid, args, _ctx())
    await _collect(gen)  # drive to terminal -> request body executes
    req = conn.requests[0]
    assert isinstance(req, JsonRpcRequest)
    # method is the tool_call_request wire string (Method.ToolCallRequest).
    assert req.method == Method.ToolCallRequest.as_wire_str()
    assert req.method == "tool_call_request"
    # session_id threads through; jsonrpc is the 2.0 unit; id is a string id.
    assert req.session_id == sid
    assert isinstance(req.jsonrpc, JsonRpcVersion)
    assert isinstance(req.id, JsonRpcIdString)
    # params carry tool_id + arguments verbatim.
    assert req.params["tool_id"] == tid
    assert req.params["arguments"] == args


@pytest.mark.asyncio
async def test_step34_request_id_is_fresh_per_call():
    # Each dispatch mints a fresh uuid4 correlation id (Rust new_uuid_v7;
    # Python uuid4 — surface contract is "fresh unique id").
    conn = _RecordingConnection()
    g1 = await dispatch_via_connection(conn, _tid(), _session_id(), {}, _ctx())
    await _collect(g1)  # drive to terminal -> request body executes
    g2 = await dispatch_via_connection(conn, _tid(), _session_id(), {}, _ctx())
    await _collect(g2)  # drive to terminal -> request body executes
    assert conn.requests[0].id.value != conn.requests[1].id.value


# ---------------------------------------------------------------------------
# Step 4 error arm — a params-encode failure short-circuits to a single
# error Terminal (Rust serde_json::to_value map_err). subscribe already
# registered (step 2 precedes step 4); the request is never sent.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step4_params_encode_failure_short_circuits_to_error_terminal(
    monkeypatch,
):
    def _boom(self):
        raise RuntimeError("encode broke")

    monkeypatch.setattr(ToolCallParams, "to_wire", _boom)
    conn = _RecordingConnection()
    cid = _call_id()
    gen = await dispatch_via_connection(
        conn, _tid(), _session_id(), {"q": 1}, _ctx(call_id=cid)
    )
    items = await _collect(gen)
    # Single error terminal; the encode failure is wrapped as a CUSTOM
    # ToolError carrying code "request_encoding".
    assert len(items) == 1
    assert items[0].kind == "terminal"
    assert items[0].is_error()
    terminal = items[0].terminal
    assert isinstance(terminal, ToolError)
    assert terminal.kind == ToolErrorKind.CUSTOM
    assert terminal.details == {"code": "request_encoding"}
    assert "encode broke" in str(terminal.detail)
    # subscribe ran BEFORE the encode (step 2 < step 4); request never sent.
    assert conn.subscribed == [cid]
    assert conn.requests == []


# ---------------------------------------------------------------------------
# Step 6 — assembly: the returned stream IS _request_stream's interleaved
# progress/response output, driven end-to-end through dispatch_via_connection.
# Three terminal paths: success (immediate response), error passthrough, and
# progress-backfill-then-terminal (scripted ordering).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step6_immediate_success_response_yields_single_terminal():
    # _RecordingConnection.request resolves immediately -> request priority
    # (R123 invariant 1): the response wins the first wait cycle and any
    # concurrent progress is dropped, so the stream is a single terminal.
    conn = _RecordingConnection()
    conn.next_response = _ok_resp()
    gen = await dispatch_via_connection(
        conn, _tid(), _session_id(), {"q": 1}, _ctx()
    )
    items = await _collect(gen)
    assert [it.kind for it in items] == ["terminal"]
    assert isinstance(items[-1].terminal, TypedToolOutput)


@pytest.mark.asyncio
async def test_step6_error_response_passthrough_yields_error_terminal():
    # A ToolError response surfaces as an error Terminal via _terminal_item's
    # Err passthrough, end-to-end through dispatch_via_connection.
    conn = _RecordingConnection()
    err = ToolError.custom("network_error", "connection reset")
    conn.next_response = err
    gen = await dispatch_via_connection(
        conn, _tid(), _session_id(), {"q": 1}, _ctx()
    )
    items = await _collect(gen)
    assert [it.kind for it in items] == ["terminal"]
    assert items[-1].is_error()
    assert items[-1].terminal is err


@pytest.mark.asyncio
async def test_step6_progress_backfilled_before_terminal():
    # Hand-stepped driving (no background _resolve_after) gives a deterministic
    # Progress* -> Terminal ordering that does NOT depend on sleep(0) race
    # timing between the scripted frames and a future-backed response. The two
    # frames are drained first while the response is still pending; the
    # response is then resolved, and the next pull yields the single terminal.
    # A background-resolve with a fixed cycle count is flaky here: under
    # asyncio's ready-queue interleaving the response can win before the second
    # frame is lifted (a still-legal Progress* Terminal shape, but not the one
    # this test means to assert).
    cid = _call_id()
    frames = [
        ToolCallProgressFrame(tool_call_id=cid, kind="chunk", body={"n": 1}),
        ToolCallProgressFrame(tool_call_id=cid, kind="chunk", body={"n": 2}),
    ]
    fut = asyncio.get_running_loop().create_future()
    conn = _ScriptedConn(frames, fut)
    gen = await dispatch_via_connection(
        conn, _tid(), _session_id(), {"q": 1}, _ctx(call_id=cid)
    )
    # Step 1: drain the two scripted progress frames; the response future is
    # still pending, so each pull lifts one frame via progress_from_frame.
    item1 = await gen.__anext__()
    assert item1.kind == "progress"
    item2 = await gen.__anext__()
    assert item2.kind == "progress"
    # Step 2: resolve the response. The next pull races the (now-exhausted)
    # progress against the resolved request; request priority (R123 invariant 1)
    # hands the win to the response -> a single terminal, concurrent progress
    # discarded.
    fut.set_result(_ok_resp())
    item3 = await gen.__anext__()
    assert item3.kind == "terminal"
    assert isinstance(item3.terminal, TypedToolOutput)
    # The stream is exhausted after the terminal (R123 invariant 3: exactly one).
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
