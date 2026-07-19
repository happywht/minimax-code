"""Tests for the R110 tool_runtime render module.

Covers:

- ``extract_content_blocks`` 5-strategy extractor: single ContentBlock,
  array-of-blocks, MCP ``CallToolResult`` (content array +
  ``structuredContent`` surfacing), mixed-object split (block fields
  extracted, remainder JSON-text), and scalar fallback. Plus the
  ``ToolRunResult`` special case (``prompt_text`` verbatim).
- serde_json **compact** fidelity: stringified values match the
  space-less wire shape (``{"value":42}`` / ``[1,2,3]`` / ``null`` /
  ``true``).
- ``extractor_for``: passes through a non-empty ``model_output``;
  falls back to ``extract_content_blocks`` on an empty one; returns
  ``None`` on a malformed value.
- ``ToolChatCompletion``: ``#[serde(default)]`` sender/message
  fall-back, ``#[serde(flatten)]`` extra round trip, optional-field
  skip-on-None.
- ``ToolChatCompletionResponse``: default serialises to ``{}``
  (skip-on-None on both fields).
- ``ToolCodeExecutionResult``: ``Default`` wire shape (every field
  emitted).
- ``ToolStreamError``: ``typed_error`` skip-on-None.
- **R110 loop-closure**: ``TypedToolOutput.from_value`` resolves to the
  REAL ``render.extract_content_blocks`` (no fake ``sys.modules``
  injection) — proves the tool->render seam is closed.
"""

from __future__ import annotations

from typing import Any

from minimax_code.tool_protocol.ids import ToolId
from minimax_code.tool_runtime.render import (
    ModelOutputExtractor,
    ToolChatCompletion,
    ToolChatCompletionResponse,
    ToolCodeExecutionResult,
    ToolOutput,
    ToolStreamError,
    extract_content_blocks,
    extractor_for,
)
from minimax_code.tool_runtime.tool import ContentBlock, TypedToolOutput

# ---------------------------------------------------------------------------
# Fake ToolOutput implementor (for extractor_for tests).
# ---------------------------------------------------------------------------


class _AutoExtractOutput:
    """Minimal ToolOutput that signals "use automatic extraction".

    ``model_output`` returns ``[]`` (the Rust default-body convention),
    so :func:`extractor_for` falls back to
    :func:`extract_content_blocks` on the raw value.
    """

    def __init__(self, value: Any) -> None:
        self.value = value

    @classmethod
    def from_dict(cls, value: Any) -> _AutoExtractOutput:
        return cls(value)

    def model_output(self) -> list[ContentBlock]:
        return []

    def chat_completion_output(self) -> ToolChatCompletionResponse | None:
        return None


class _PassThroughOutput:
    """Minimal ToolOutput returning a fixed, non-empty ``model_output``."""

    def __init__(self, value: Any) -> None:
        self.value = value

    @classmethod
    def from_dict(cls, value: Any) -> _PassThroughOutput:
        return cls(value)

    def model_output(self) -> list[ContentBlock]:
        return [ContentBlock.Text("passed-through")]

    def chat_completion_output(self) -> ToolChatCompletionResponse | None:
        return None


class _MalformedOutput:
    """ToolOutput whose ``from_dict`` always raises (malformed payload)."""

    @classmethod
    def from_dict(cls, value: Any) -> _MalformedOutput:  # noqa: ARG003
        raise ValueError("malformed")

    def model_output(self) -> list[ContentBlock]:
        return []

    def chat_completion_output(self) -> ToolChatCompletionResponse | None:
        return None


# ---------------------------------------------------------------------------
# extract_content_blocks — strategy 1 (single ContentBlock).
# ---------------------------------------------------------------------------


def test_extract_text_value_creates_single_text_block():
    blocks = extract_content_blocks({"type": "text", "text": "hi"})
    assert len(blocks) == 1
    assert blocks[0].type == "text"
    assert blocks[0].text == "hi"


def test_extract_image_value_creates_single_image_block():
    blocks = extract_content_blocks(
        {"type": "image", "mime_type": "image/png", "data": "abc=="}
    )
    assert len(blocks) == 1
    assert blocks[0].type == "image"
    assert blocks[0].mime_type == "image/png"


# ---------------------------------------------------------------------------
# strategy 2 (array containing >= 1 ContentBlock).
# ---------------------------------------------------------------------------


def test_extract_array_with_blocks_converts_each_element():
    blocks = extract_content_blocks(
        [
            {"type": "text", "text": "hi"},
            "raw",
            {"type": "image", "mime_type": "image/png", "data": "x"},
        ]
    )
    assert len(blocks) == 3
    assert blocks[0].text == "hi"
    # Non-block scalar element -> Text with the raw string.
    assert blocks[1].text == "raw"
    assert blocks[2].type == "image"


def test_extract_array_of_scalars_returns_single_text_block():
    # [1, 2, 3] has no ContentBlock-shaped element -> falls through to
    # the scalar-text fallback (compact serde_json shape).
    blocks = extract_content_blocks([1, 2, 3])
    assert len(blocks) == 1
    assert blocks[0].type == "text"
    assert blocks[0].text == "[1,2,3]"


# ---------------------------------------------------------------------------
# strategy 4 (mixed object: block-shaped field values extracted).
# ---------------------------------------------------------------------------


def test_extract_object_with_block_array_field_extracts_blocks():
    value = {
        "metadata": {"source": "db"},
        "results": [
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
        ],
    }
    blocks = extract_content_blocks(value)
    assert len(blocks) == 3
    # Remainder (the non-block `metadata` field) serialised first as
    # compact JSON text — the whole {"metadata": {...}} object, since
    # metadata's value is not block-shaped.
    assert blocks[0].text == '{"metadata":{"source":"db"}}'
    assert blocks[1].text == "a"
    assert blocks[2].text == "b"


def test_extract_mixed_object_separates_blocks_and_remainder():
    value = {
        "summary": "done",
        "image": {"type": "image", "mime_type": "image/png", "data": "z"},
    }
    blocks = extract_content_blocks(value)
    assert len(blocks) == 2
    # Remainder first (contains summary), then the extracted image.
    assert "summary" in blocks[0].text
    assert blocks[1].type == "image"


def test_extract_object_with_only_block_fields_no_remainder():
    # When every field is block-shaped, no remainder text block is emitted.
    value = {"img": {"type": "image", "mime_type": "image/png", "data": "d"}}
    blocks = extract_content_blocks(value)
    assert len(blocks) == 1
    assert blocks[0].type == "image"


def test_extract_object_with_no_block_fields_is_single_text():
    # Plain scalar fields -> the whole object stringifies as one text block.
    value = {"a": 1, "b": 2}
    blocks = extract_content_blocks(value)
    assert len(blocks) == 1
    assert blocks[0].type == "text"
    assert blocks[0].text == '{"a":1,"b":2}'


# ---------------------------------------------------------------------------
# strategy 3 (MCP CallToolResult: object with content array).
# ---------------------------------------------------------------------------


def test_extract_object_with_content_array_returns_blocks():
    value = {"content": [{"type": "text", "text": "hi"}]}
    blocks = extract_content_blocks(value)
    assert len(blocks) == 1
    assert blocks[0].text == "hi"


def test_extract_content_with_structured_content_surfaces_id():
    # structuredContent is surfaced as a compact-JSON text block so IDs the
    # server expects the model to round-trip are not dropped.
    value = {
        "content": [{"type": "text", "text": "rendered"}],
        "structuredContent": {"drawing_id": "abc123", "title": "sketch"},
    }
    blocks = extract_content_blocks(value)
    assert len(blocks) == 2
    # structuredContent first, then the content array.
    assert "abc123" in blocks[0].text
    assert blocks[0].text == '{"drawing_id":"abc123","title":"sketch"}'
    assert blocks[1].text == "rendered"


# ---------------------------------------------------------------------------
# ToolRunResult special case (prompt_text verbatim).
# ---------------------------------------------------------------------------


def test_extract_run_result_returns_prompt_text_only():
    value = {
        "prompt_text": "Reminders appended here",
        "output": {"structured": "result"},
        "effective_tool_name": "GrokBuild:fs.read",
    }
    blocks = extract_content_blocks(value)
    assert len(blocks) == 1
    # The model sees prompt_text verbatim, never a JSON dump.
    assert blocks[0].text == "Reminders appended here"


def test_extract_run_result_requires_all_three_marker_fields():
    # Missing effective_tool_name -> not a ToolRunResult; falls through to
    # the mixed-object / fallback path.
    value = {"prompt_text": "x", "output": {"k": 1}}
    blocks = extract_content_blocks(value)
    # No content array, no block fields -> single text fallback.
    assert len(blocks) == 1


# ---------------------------------------------------------------------------
# strategy 5 (fallback: scalar / null / bool / number / unknown).
# ---------------------------------------------------------------------------


def test_extract_string_returns_text_block_with_raw_string():
    blocks = extract_content_blocks("hello")
    assert len(blocks) == 1
    assert blocks[0].text == "hello"


def test_extract_bool_value_creates_text_block():
    blocks = extract_content_blocks(True)
    assert len(blocks) == 1
    assert blocks[0].text == "true"


def test_extract_number_value_creates_text_block():
    blocks = extract_content_blocks(42)
    assert len(blocks) == 1
    assert blocks[0].text == "42"


def test_extract_null_value_creates_text_block():
    blocks = extract_content_blocks(None)
    assert len(blocks) == 1
    assert blocks[0].text == "null"


def test_extract_unknown_block_type_falls_back_to_text():
    # {"type": "unknown"} is dict-shaped but not a valid ContentBlock
    # discriminator -> whole object stringifies as text.
    blocks = extract_content_blocks({"type": "unknown"})
    assert len(blocks) == 1
    assert blocks[0].type == "text"
    assert "unknown" in blocks[0].text


def test_extract_empty_array_returns_single_text():
    # Empty array has no blocks -> fallback stringifies to "[]".
    blocks = extract_content_blocks([])
    assert len(blocks) == 1
    assert blocks[0].text == "[]"


def test_extract_empty_object_returns_single_text():
    blocks = extract_content_blocks({})
    assert len(blocks) == 1
    assert blocks[0].text == "{}"


# ---------------------------------------------------------------------------
# extractor_for — type-erased ModelOutputExtractor.
# ---------------------------------------------------------------------------


def test_extractor_for_passes_through_non_empty_model_output():
    extract = extractor_for(_PassThroughOutput)
    # value shape is irrelevant: the pass-through output always wins.
    blocks = extract({"anything": True})
    assert blocks == [ContentBlock.Text("passed-through")]


def test_extractor_for_falls_back_to_extract_content_blocks():
    extract = extractor_for(_AutoExtractOutput)
    blocks = extract(
        [
            {"type": "text", "text": "a"},
            {"type": "image", "mime_type": "image/png", "data": "d"},
        ]
    )
    assert len(blocks) == 2
    assert blocks[0].text == "a"
    assert blocks[1].type == "image"


def test_extractor_for_returns_none_on_malformed_value():
    extract = extractor_for(_MalformedOutput)
    assert extract({"nope": True}) is None


def test_model_output_extractor_alias_is_callable_type():
    # ModelOutputExtractor is a TypeAlias for Callable[[Any], list | None].
    extract: ModelOutputExtractor = extractor_for(_AutoExtractOutput)
    result = extract({"type": "text", "text": "ok"})
    assert result is not None
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# ToolOutput Protocol default semantics.
# ---------------------------------------------------------------------------


def test_tool_output_default_model_output_is_empty():
    # An implementor following the "auto-extract" convention returns [].
    output = _AutoExtractOutput({"x": 1})
    assert output.model_output() == []


def test_chat_completion_default_is_none():
    output = _AutoExtractOutput({"x": 1})
    assert output.chat_completion_output() is None


def test_tool_output_protocol_satisfied_structurally():
    # _AutoExtractOutput satisfies ToolOutput structurally (has both
    # methods) without declaring it — Python structural typing.
    output: ToolOutput = _AutoExtractOutput({"x": 1})  # type: ignore[assignment]
    assert output.model_output() == []


# ---------------------------------------------------------------------------
# ToolChatCompletion — #[serde(default)] + #[serde(flatten)] extra.
# ---------------------------------------------------------------------------


def test_tool_chat_completion_defaults_sender_and_message():
    cc = ToolChatCompletion()
    d = cc.to_dict()
    # #[serde(default)] -> always emitted, default "".
    assert d == {"sender": "", "message": ""}


def test_tool_chat_completion_round_trip_with_optional_fields():
    cc = ToolChatCompletion(
        sender="assistant",
        message="hi",
        message_tag="final",
        tool_usage_card_id="card-1",
    )
    d = cc.to_dict()
    assert d["sender"] == "assistant"
    assert d["message"] == "hi"
    assert d["message_tag"] == "final"
    assert d["tool_usage_card_id"] == "card-1"
    back = ToolChatCompletion.from_dict(d)
    assert back.sender == "assistant"
    assert back.message_tag == "final"
    assert back.tool_usage_card_id == "card-1"


def test_tool_chat_completion_omits_none_optionals():
    cc = ToolChatCompletion(sender="assistant", message="hi")
    d = cc.to_dict()
    assert "message_tag" not in d
    assert "tool_usage_card_id" not in d
    assert "card_attachment" not in d
    assert "media_gen_type" not in d
    assert "code_execution_result" not in d


def test_tool_chat_completion_flattens_extra_on_serialize():
    cc = ToolChatCompletion(
        sender="assistant", message="hi", extra={"custom_field": 42, "tag": "x"}
    )
    d = cc.to_dict()
    # #[serde(flatten)] -> extra merged at the top level.
    assert d["custom_field"] == 42
    assert d["tag"] == "x"
    assert d["sender"] == "assistant"


def test_tool_chat_completion_collects_unknown_keys_into_extra():
    back = ToolChatCompletion.from_dict(
        {"sender": "assistant", "message": "hi", "unknown_a": 1, "unknown_b": "z"}
    )
    assert back.sender == "assistant"
    assert back.message == "hi"
    assert back.extra == {"unknown_a": 1, "unknown_b": "z"}


def test_tool_chat_completion_round_trips_extra_losslessly():
    cc = ToolChatCompletion(
        sender="assistant",
        message="hi",
        extra={"render_card": {"type": "chart", "data": [1, 2]}},
    )
    wire = cc.to_dict()
    back = ToolChatCompletion.from_dict(wire)
    assert back.extra == {"render_card": {"type": "chart", "data": [1, 2]}}


def test_tool_chat_completion_with_code_execution_result_round_trips():
    cer = ToolCodeExecutionResult(stdout="ok", stderr="", exit_code=0)
    cc = ToolChatCompletion(
        sender="assistant", message="ran", code_execution_result=cer
    )
    d = cc.to_dict()
    assert d["code_execution_result"] == {
        "stdout": "ok",
        "stderr": "",
        "exit_code": 0,
        "command_timed_out": False,
    }
    back = ToolChatCompletion.from_dict(d)
    assert back.code_execution_result is not None
    assert back.code_execution_result.stdout == "ok"
    assert back.code_execution_result.exit_code == 0


def test_tool_chat_completion_malformed_sender_falls_back_to_empty():
    # serde would fail the whole struct on a type mismatch; Python keeps
    # the siblings and drops the bad field to its default.
    back = ToolChatCompletion.from_dict({"sender": 123, "message": "hi"})
    assert back.sender == ""
    assert back.message == "hi"


# ---------------------------------------------------------------------------
# ToolChatCompletionResponse — skip-on-None on both fields.
# ---------------------------------------------------------------------------


def test_tool_chat_completion_response_default_serialises_to_empty():
    r = ToolChatCompletionResponse()
    assert r.to_dict() == {}
    assert r.result is None
    assert r.stream_error is None


def test_tool_chat_completion_response_round_trip_with_result():
    cc = ToolChatCompletion(sender="assistant", message="hi")
    r = ToolChatCompletionResponse(result=cc)
    d = r.to_dict()
    assert "stream_error" not in d
    assert d["result"]["sender"] == "assistant"
    back = ToolChatCompletionResponse.from_dict(d)
    assert back.result is not None
    assert back.result.sender == "assistant"
    assert back.stream_error is None


def test_tool_chat_completion_response_round_trip_with_stream_error():
    err = ToolStreamError(message="rate-limited")
    r = ToolChatCompletionResponse(stream_error=err)
    d = r.to_dict()
    assert "result" not in d
    assert d["stream_error"]["message"] == "rate-limited"
    back = ToolChatCompletionResponse.from_dict(d)
    assert back.result is None
    assert back.stream_error is not None
    assert back.stream_error.message == "rate-limited"


# ---------------------------------------------------------------------------
# ToolCodeExecutionResult — #[derive(Default)] wire shape.
# ---------------------------------------------------------------------------


def test_tool_code_execution_result_defaults_all_fields():
    r = ToolCodeExecutionResult()
    assert r.stdout == ""
    assert r.stderr == ""
    assert r.exit_code == 0
    assert r.command_timed_out is False
    # #[serde(default)] -> every field emitted.
    assert r.to_dict() == {
        "stdout": "",
        "stderr": "",
        "exit_code": 0,
        "command_timed_out": False,
    }


def test_tool_code_execution_result_round_trip():
    r = ToolCodeExecutionResult(
        stdout="hello", stderr="warn", exit_code=2, command_timed_out=True
    )
    d = r.to_dict()
    back = ToolCodeExecutionResult.from_dict(d)
    assert back.stdout == "hello"
    assert back.stderr == "warn"
    assert back.exit_code == 2
    assert back.command_timed_out is True


def test_tool_code_execution_result_partial_decode_uses_defaults():
    # Missing fields fall back to defaults (#[serde(default)]).
    back = ToolCodeExecutionResult.from_dict({"exit_code": 5})
    assert back.exit_code == 5
    assert back.stdout == ""
    assert back.command_timed_out is False


# ---------------------------------------------------------------------------
# ToolStreamError — message default + typed_error skip-on-None.
# ---------------------------------------------------------------------------


def test_tool_stream_error_default_message_empty():
    e = ToolStreamError()
    assert e.message == ""
    assert e.typed_error is None
    d = e.to_dict()
    # message always emitted; typed_error omitted when None.
    assert d == {"message": ""}


def test_tool_stream_error_round_trip_with_typed_error():
    e = ToolStreamError(message="boom", typed_error={"code": 500, "retry": False})
    d = e.to_dict()
    assert d["message"] == "boom"
    assert d["typed_error"] == {"code": 500, "retry": False}
    back = ToolStreamError.from_dict(d)
    assert back.message == "boom"
    assert back.typed_error == {"code": 500, "retry": False}


# ---------------------------------------------------------------------------
# R110 loop-closure: TypedToolOutput.from_value uses the REAL render.
# ---------------------------------------------------------------------------


def test_typed_tool_output_from_value_uses_real_render_single_block():
    # No fake sys.modules injection: from_value's lazy import resolves to
    # the real render.extract_content_blocks landed in R110.
    tto = TypedToolOutput.from_value(
        ToolId("fs:read"), {"type": "text", "text": "hello world"}
    )
    assert str(tto.tool_id) == "fs:read"
    assert tto.model_output == [ContentBlock.Text("hello world")]


def test_typed_tool_output_from_value_uses_real_render_compact_json():
    # The real extractor uses the compact serde_json shape.
    tto = TypedToolOutput.from_value(ToolId("calc"), {"value": 42})
    assert len(tto.model_output) == 1
    assert tto.model_output[0].text == '{"value":42}'


def test_typed_tool_output_from_value_uses_real_render_array():
    tto = TypedToolOutput.from_value(
        ToolId("search"),
        [
            {"type": "text", "text": "a"},
            {"type": "image", "mime_type": "image/png", "data": "x"},
        ],
    )
    blocks = tto.model_output
    assert len(blocks) == 2
    assert blocks[0].text == "a"
    assert blocks[1].type == "image"
