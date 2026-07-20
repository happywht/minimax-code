"""Wire-contract tests for ``mcp_adapter.types`` (R179).

R179 ports grok-build's ``xai-computer-hub-mcp-adapter/src/types.rs`` (108
lines) -- the crate's 1st leaf. These tests pin four invariants:

1. **camelCase round-trip** -- the ``#[serde(rename_all = "camelCase")]``
   fields (``inputSchema`` / ``isError`` / ``mimeType``) survive a pydantic
   ``model_dump_json(by_alias=True)`` -> ``model_validate_json`` round trip
   and accept *both* the wire alias and the snake_case attribute.
2. **Defaults** -- ``#[serde(default)]`` fields (``capabilities`` /
   ``description`` / ``input_schema`` / ``content`` / ``is_error`` /
   resource ``mime_type`` / ``text``) take their documented default when
   absent on the wire.
3. **Discriminated content union** -- ``McpContent``'s ``type`` tag routes a
   payload to the right variant (``isinstance``), and an unknown tag raises
   ``ValidationError`` (forward-incompatible content is rejected, not
   silently dropped).
4. **Error union** -- the four ``thiserror`` variants subclass the
   ``McpError`` base, format the Rust ``#[error(...)]`` template into
   ``str(err)``, and expose their structured attributes.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from minimax_code.mcp_adapter import (
    McpCallResult,
    McpContent,
    McpDecodeError,
    McpError,
    McpImageContent,
    McpProtocolError,
    McpResourceContent,
    McpServerInfo,
    McpTextContent,
    McpTimeoutError,
    McpToolDefinition,
    McpTransportError,
)

# ---------------------------------------------------------------------------
# McpServerInfo
# ---------------------------------------------------------------------------


def test_server_info_defaults_capabilities_to_none_when_absent() -> None:
    """``#[serde(default)]`` on ``serde_json::Value`` -> ``None`` (Value::Null)."""
    info = McpServerInfo.model_validate({"name": "linear", "version": "1.2.0"})
    assert info.name == "linear"
    assert info.version == "1.2.0"
    assert info.capabilities is None


def test_server_info_preserves_arbitrary_capabilities_json() -> None:
    """``serde_json::Value`` accepts any JSON shape, not just an object."""
    info = McpServerInfo.model_validate(
        {"name": "github", "version": "0", "capabilities": {"tools": {"listChanged": True}}}
    )
    assert info.capabilities == {"tools": {"listChanged": True}}


def test_server_info_round_trips_through_camel_case_alias() -> None:
    info = McpServerInfo(name="s", version="v", capabilities={"a": 1})
    revived = McpServerInfo.model_validate_json(info.model_dump_json(by_alias=True))
    assert revived == info


# ---------------------------------------------------------------------------
# McpToolDefinition
# ---------------------------------------------------------------------------


def test_tool_definition_accepts_camel_case_input_schema_alias() -> None:
    td = McpToolDefinition.model_validate(
        {"name": "create_issue", "inputSchema": {"type": "object"}}
    )
    assert td.name == "create_issue"
    assert td.input_schema == {"type": "object"}
    assert td.description is None


def test_tool_definition_also_accepts_snake_case_attribute() -> None:
    """``populate_by_name=True`` -> the Python attribute name is accepted too."""
    td = McpToolDefinition.model_validate(
        {"name": "x", "input_schema": {"type": "object"}}
    )
    assert td.input_schema == {"type": "object"}


def test_tool_definition_emits_camel_case_on_dump_by_alias() -> None:
    td = McpToolDefinition(name="x", input_schema={"type": "object"})
    dumped = td.model_dump(by_alias=True)
    assert "inputSchema" in dumped
    assert "input_schema" not in dumped


def test_tool_definition_minimal_payload() -> None:
    td = McpToolDefinition.model_validate({"name": "ping"})
    assert td.name == "ping"
    assert td.description is None
    assert td.input_schema is None


# ---------------------------------------------------------------------------
# McpContent discriminated union
# ---------------------------------------------------------------------------


def test_call_result_routes_content_variants_by_type_tag() -> None:
    """The ``type`` discriminator dispatches each block to its variant."""
    result = McpCallResult.model_validate(
        {
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "image", "mimeType": "image/png", "data": "QUJD"},
                {"type": "resource", "uri": "file:///x"},
            ],
            "isError": True,
        }
    )
    assert result.is_error is True
    assert isinstance(result.content[0], McpTextContent)
    assert result.content[0].text == "hello"
    assert isinstance(result.content[1], McpImageContent)
    assert result.content[1].mime_type == "image/png"
    assert result.content[1].data == "QUJD"
    assert isinstance(result.content[2], McpResourceContent)
    assert result.content[2].uri == "file:///x"
    assert result.content[2].mime_type is None
    assert result.content[2].text is None


def test_call_result_defaults_content_empty_and_is_error_false() -> None:
    result = McpCallResult.model_validate({})
    assert result.content == []
    assert result.is_error is False


def test_call_result_emits_camel_case_is_error_on_dump() -> None:
    result = McpCallResult(content=[McpTextContent(text="x")], is_error=True)
    dumped = result.model_dump(by_alias=True)
    assert dumped["isError"] is True
    assert dumped["content"][0]["type"] == "text"


def test_unknown_content_type_tag_is_rejected() -> None:
    """A tag outside Text/Image/Resource must raise, not silently drop the block."""
    with pytest.raises(ValidationError):
        McpCallResult.model_validate({"content": [{"type": "audio", "data": "..."}]})


def test_resource_content_accepts_optional_fields() -> None:
    block = McpResourceContent.model_validate(
        {"type": "resource", "uri": "u", "mimeType": "text/plain", "text": "body"}
    )
    assert block.mime_type == "text/plain"
    assert block.text == "body"


def test_content_union_alias_is_importable_and_annotated() -> None:
    """``McpContent`` is the Annotated union alias (a type, not a model class)."""
    assert McpContent is not None
    # It is used as the element type of McpCallResult.content; verify a plain
    # construction round-trips.
    result = McpCallResult(content=[McpTextContent(text="ok")])
    assert result.content[0].type == "text"


# ---------------------------------------------------------------------------
# McpError union
# ---------------------------------------------------------------------------


def test_transport_error_formats_rust_error_template() -> None:
    err = McpTransportError("connection refused")
    assert str(err) == "transport error: connection refused"
    assert err.message == "connection refused"


def test_protocol_error_carries_code_and_message() -> None:
    err = McpProtocolError(-32601, "method not found")
    assert str(err) == "protocol error (code -32601): method not found"
    assert err.code == -32601
    assert err.message == "method not found"


def test_timeout_error_formats_rust_error_template() -> None:
    assert str(McpTimeoutError("30s")) == "timeout: 30s"


def test_decode_error_formats_rust_error_template() -> None:
    assert str(McpDecodeError("bad json")) == "decode error: bad json"


def test_every_variant_subclasses_mcp_error_base() -> None:
    """``except McpError`` catches all four variants (the enum discriminant)."""
    variants = [
        McpTransportError("a"),
        McpProtocolError(1, "b"),
        McpTimeoutError("c"),
        McpDecodeError("d"),
    ]
    for err in variants:
        assert isinstance(err, McpError)


def test_mcp_error_variants_are_catchable_via_base() -> None:
    try:
        raise McpProtocolError(-32700, "parse error")
    except McpError as err:
        assert isinstance(err, McpProtocolError)
        assert err.code == -32700
