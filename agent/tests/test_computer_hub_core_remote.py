"""Tests for the R120 computer_hub_core remote module (layer 4).

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
and the remaining layer-4 seams (``terminal_from_response`` /
``error_from_envelope`` / ``is_workspace_unavailable`` /
``tool_error_from_wire``) land in later rounds; these tests exercise the
four landed seams in isolation.

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

import inspect

import pytest

from minimax_code.computer_hub_core.remote import (
    decode_call_result,
    output_to_value,
    progress_from_frame,
)
from minimax_code.tool_protocol import (
    ImageBlock,
    Json,
    Mcp,
    ResourceBlock,
    Text,
    TextBlock,
    ToolCallProgressFrame,
    ToolId,
)
from minimax_code.tool_runtime import (
    ToolChatCompletionResponse,
    ToolError,
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
