"""Tests for multimodal (image) content support in LLM transports (v0.6.0).

Covers:
  - Anthropic transport: image block → Anthropic native format
  - OpenAI transport: image block → OpenAI vision format
  - Mock transport: accepts list content without error
  - Backward compatibility: plain string content still works
"""
from __future__ import annotations

import pytest

from minimax_code.agent.transports.anthropic_transport import _convert_messages
from minimax_code.agent.transports.openai_transport import _convert_openai_messages
from minimax_code.agent.transports.mock_transport import _extract_text


# ---------------------------------------------------------------------------
# Anthropic transport — image conversion
# ---------------------------------------------------------------------------


class TestAnthropicImageConversion:
    """Verify image blocks are converted to Anthropic's native format."""

    def test_image_block_converted(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {
                        "type": "image",
                        "media_type": "image/png",
                        "data": "iVBORw0KGgo=",
                    },
                ],
            }
        ]
        result = _convert_messages(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        blocks = result[0]["content"]
        assert len(blocks) == 2
        # First block: text passthrough
        assert blocks[0] == {"type": "text", "text": "Describe this image"}
        # Second block: image converted to Anthropic format
        assert blocks[1]["type"] == "image"
        assert blocks[1]["source"]["type"] == "base64"
        assert blocks[1]["source"]["media_type"] == "image/png"
        assert blocks[1]["source"]["data"] == "iVBORw0KGgo="

    def test_mixed_text_and_image(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Here's a screenshot:"},
                    {"type": "image", "media_type": "image/jpeg", "data": "/9j/4AAQ"},
                    {"type": "text", "text": "What do you see?"},
                ],
            }
        ]
        result = _convert_messages(messages)
        blocks = result[0]["content"]
        assert len(blocks) == 3
        assert blocks[0]["type"] == "text"
        assert blocks[1]["type"] == "image"
        assert blocks[1]["source"]["media_type"] == "image/jpeg"
        assert blocks[2]["type"] == "text"

    def test_plain_string_backward_compat(self) -> None:
        """Plain string content should still work (backward compatibility)."""
        messages = [{"role": "user", "content": "Hello world"}]
        result = _convert_messages(messages)
        assert len(result) == 1
        assert result[0]["content"] == [{"type": "text", "text": "Hello world"}]

    def test_base64_data_passthrough(self) -> None:
        """Large base64 data should pass through without modification."""
        big_data = "A" * 100_000
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "media_type": "image/png", "data": big_data},
                ],
            }
        ]
        result = _convert_messages(messages)
        assert result[0]["content"][0]["source"]["data"] == big_data

    def test_default_media_type(self) -> None:
        """If media_type is missing, default to image/png."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "data": "abc123"},
                ],
            }
        ]
        result = _convert_messages(messages)
        assert result[0]["content"][0]["source"]["media_type"] == "image/png"

    def test_non_user_messages_unchanged(self) -> None:
        """Assistant and tool messages should not be affected."""
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = _convert_messages(messages)
        assert len(result) == 2
        assert result[1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# OpenAI transport — image conversion
# ---------------------------------------------------------------------------


class TestOpenAIImageConversion:
    """Verify image blocks are converted to OpenAI vision format."""

    def test_image_block_converted(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this"},
                    {
                        "type": "image",
                        "media_type": "image/png",
                        "data": "iVBORw0KGgo=",
                    },
                ],
            }
        ]
        result = _convert_openai_messages(messages)
        assert len(result) == 1
        blocks = result[0]["content"]
        assert len(blocks) == 2
        # Text block unchanged
        assert blocks[0] == {"type": "text", "text": "Analyze this"}
        # Image block → image_url format
        assert blocks[1]["type"] == "image_url"
        url = blocks[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        assert "iVBORw0KGgo=" in url

    def test_jpeg_media_type(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "media_type": "image/jpeg", "data": "/9j/test"},
                ],
            }
        ]
        result = _convert_openai_messages(messages)
        url = result[0]["content"][0]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")

    def test_plain_string_unchanged(self) -> None:
        """Non-list content should pass through unchanged."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        result = _convert_openai_messages(messages)
        assert result[0]["content"] == "You are helpful."
        assert result[1]["content"] == "Hello"

    def test_non_user_list_content_unchanged(self) -> None:
        """List content on non-user messages should pass through."""
        messages = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hi"}],
            }
        ]
        result = _convert_openai_messages(messages)
        assert result[0]["content"] == [{"type": "text", "text": "Hi"}]


# ---------------------------------------------------------------------------
# Mock transport — list content handling
# ---------------------------------------------------------------------------


class TestMockMultimodal:
    """Verify mock transport handles list content gracefully."""

    def test_extract_text_from_string(self) -> None:
        messages = [{"role": "user", "content": "Hello world"}]
        text = _extract_text(messages)
        assert "Hello world" in text

    def test_extract_text_from_list(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "See this:"},
                    {"type": "image", "media_type": "image/png", "data": "abc"},
                ],
            }
        ]
        text = _extract_text(messages)
        assert "See this:" in text
        # Image data should NOT appear in extracted text
        assert "abc" not in text

    def test_empty_messages(self) -> None:
        text = _extract_text([])
        assert text == ""

    def test_multiple_messages(self) -> None:
        messages = [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "Hi"},
        ]
        text = _extract_text(messages)
        assert "Be helpful" in text
        assert "Hi" in text
