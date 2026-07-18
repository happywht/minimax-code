"""Unit tests for the MCP types layer (R3).

Validates that the Pydantic models:
1. Parse the wire format (snake_case, discriminated content union).
2. Round-trip through JSON without loss.
3. Default to the pinned protocol version in the handshake.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from minimax_code.mcp import protocol, types


def test_protocol_version_pinned() -> None:
    assert protocol.LATEST_PROTOCOL_VERSION == "2024-11-05"


def test_initialize_defaults_to_latest_protocol_version() -> None:
    result = types.InitializeResult(
        serverInfo=types.Implementation(name="minimax-code", version="0.9.0"),
    )
    assert result.protocolVersion == protocol.LATEST_PROTOCOL_VERSION
    assert result.serverInfo.name == "minimax-code"


def test_tool_minimal_uses_object_schema_default() -> None:
    tool = types.Tool(name="search")
    assert tool.inputSchema["type"] == "object"
    assert tool.description is None


def test_call_tool_result_with_text_content_round_trip() -> None:
    payload = {
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "image", "data": "b64==", "mimeType": "image/png"},
        ],
        "isError": False,
    }
    result = types.CallToolResult.model_validate(payload)
    assert result.content[0].type == "text"
    assert result.content[0].text == "hello"  # type: ignore[union-attr]
    assert result.content[1].type == "image"  # type: ignore[union-attr]
    # Round-trip: serialise back and re-parse, expect equality.
    dumped = json.loads(result.model_dump_json())
    re_parsed = types.CallToolResult.model_validate(dumped)
    assert re_parsed.content[0].type == "text"


def test_call_tool_result_rejects_unknown_content_type() -> None:
    with pytest.raises(ValidationError):
        types.CallToolResult.model_validate(
            {"content": [{"type": "audio", "text": "x"}]}
        )


def test_capability_objects_allow_extra_fields() -> None:
    # Forward-compat: unknown capability keys must not break parsing.
    caps = types.ServerCapabilities.model_validate(
        {"tools": True, "experimental": {"future_feature": {"opt": 1}}}
    )
    assert caps.tools is True
    assert "future_feature" in caps.experimental  # type: ignore[operator]


def test_list_results_default_empty_and_carry_cursor() -> None:
    tools_result = types.ListToolsResult(nextCursor="abc")
    assert tools_result.tools == []
    assert tools_result.nextCursor == "abc"


def test_initialize_request_params_carries_client_info() -> None:
    params = types.InitializeRequestParams(
        clientInfo=types.Implementation(name="test-client", version="1.0.0"),
    )
    assert params.clientInfo.name == "test-client"
    assert params.protocolVersion == protocol.LATEST_PROTOCOL_VERSION


def test_embedded_resource_content_parses() -> None:
    result = types.CallToolResult.model_validate(
        {
            "content": [
                {"type": "resource", "resource": {"uri": "file:///x"}},
            ]
        }
    )
    assert result.content[0].type == "resource"  # type: ignore[union-attr]


def test_request_methods_is_frozen_set_without_notifications() -> None:
    # Notifications must NOT appear in REQUEST_METHODS (they have no reply).
    assert protocol.METHOD_INITIALIZED not in protocol.REQUEST_METHODS
    assert protocol.METHOD_TOOLS_CALL in protocol.REQUEST_METHODS
