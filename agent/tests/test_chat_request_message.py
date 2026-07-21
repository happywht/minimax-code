"""Tests for sampler.chat_request_message (R210, ``xai-grok-sampling-types`` ``types.rs``).

Covers the fifth ``types.rs`` slice -- the OpenAI ChatCompletion request
message body:

- :class:`ChatRequestMessage` (struct) -- ``role`` (required) + ``content``
  (required) + ``name`` / ``tool_calls`` / ``tool_call_id`` / ``model_id`` /
  ``reasoning_content`` (all optional). Carries the 5 grok constructors +
  ``is_system_message`` / ``text_content`` read-only helpers +
  ``with_text_content`` / ``with_appended_text`` copy-on-work mutators.

The slice closes against the R206 :class:`Role` + the R207
:class:`ChatMessageContent` / :class:`ToolCallRequest` atomics. Migration
invariants tested below:

- ``role`` strictly required (missing / non-string / unknown -> ``ValueError``);
  ``content`` tolerant (missing / null -> empty :class:`ChatTextContent`).
- ``tool_calls`` ``Vec<ToolCallRequest>`` with ``#[serde(default)]`` -> a tuple,
  a missing / null / non-list wire value tolerates to the empty tuple (NOT
  ``None``).
- non-dict payload raises ``ValueError`` (a dict is required to read ``role``
  -- contrast the R209 :class:`SearchParameters` all-Option struct, which
  tolerates a non-dict to the all-None instance).
- ``&mut self`` mutators renamed to ``with_*`` copy-on-work (``frozen=True``
  forbids in-place mutation).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler.chat_completion_leaves import Role
from minimax_code.sampler.chat_completion_mid import (
    ChatBlocksContent,
    ChatTextContent,
    ToolCallRequest,
)
from minimax_code.sampler.chat_request_message import ChatRequestMessage

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_one_symbol() -> None:
    """1 struct = 1 re-exported symbol."""
    import minimax_code.sampler.chat_request_message as chat_request_message

    assert chat_request_message.__all__ == ["ChatRequestMessage"]


def test_package_barrel_re_exports_chat_request_message() -> None:
    """The package barrel flattens the ChatRequestMessage symbol."""
    import minimax_code.sampler as sampler

    assert "ChatRequestMessage" in sampler.__all__


# ---------------------------------------------------------------------------
# from_payload: role is strictly required, content is tolerant.
# ---------------------------------------------------------------------------


def test_from_payload_maps_all_seven_fields() -> None:
    result = ChatRequestMessage.from_payload(
        {
            "role": "assistant",
            "content": "hi",
            "name": "bot",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "f", "arguments": "{}"},
                }
            ],
            "tool_call_id": "call_0",
            "model_id": "grok-1",
            "reasoning_content": "thinking...",
        }
    )
    assert result.role == Role.ASSISTANT
    assert isinstance(result.content, ChatTextContent)
    assert result.content.text == "hi"
    assert result.name == "bot"
    assert result.tool_call_id == "call_0"
    assert result.model_id == "grok-1"
    assert result.reasoning_content == "thinking..."
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_1"


@pytest.mark.parametrize("payload", ["assistant", None, 42, 1.5, ["role", "user"]])
def test_from_payload_non_dict_raises_value_error(payload: object) -> None:
    """A non-dict payload cannot supply the required ``role`` field -> raises
    (contrast the R209 SearchParameters all-Option struct, which tolerates a
    non-dict to the all-None instance)."""
    with pytest.raises(ValueError):
        ChatRequestMessage.from_payload(payload)


def test_from_payload_missing_role_raises() -> None:
    """``role`` is required (no ``#[serde(default)]``) -> a missing role raises
    (mirrors serde's missing-required-field failure)."""
    with pytest.raises(ValueError):
        ChatRequestMessage.from_payload({"content": "hi"})


@pytest.mark.parametrize("role", ["robot", 5, None, True])
def test_from_payload_invalid_role_raises(role: object) -> None:
    """``Role.from_payload`` is strict (no catch-all) -> an unknown / non-string
    role raises."""
    with pytest.raises(ValueError):
        ChatRequestMessage.from_payload({"role": role})


def test_from_payload_missing_content_tolerates_to_empty_text() -> None:
    """``content`` is tolerant: a missing / null wire value -> the empty
    :class:`ChatTextContent` (forward-compat -- ``ChatMessageContent.from_payload``
    tolerates None to Text(""))."""
    result = ChatRequestMessage.from_payload({"role": "user"})
    assert isinstance(result.content, ChatTextContent)
    assert result.content.text == ""

    result2 = ChatRequestMessage.from_payload({"role": "user", "content": None})
    assert isinstance(result2.content, ChatTextContent)
    assert result2.content.text == ""


def test_from_payload_content_as_blocks_list() -> None:
    """``content`` as a list -> :class:`ChatBlocksContent` (each item parsed
    through the :class:`ChatContentBlock` tagged-union dispatcher)."""
    result = ChatRequestMessage.from_payload(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "a"},
                {"type": "text", "text": "b"},
            ],
        }
    )
    assert isinstance(result.content, ChatBlocksContent)
    assert len(result.content.blocks) == 2


# ---------------------------------------------------------------------------
# tool_calls: Vec<ToolCallRequest> with #[serde(default)] -> tuple.
# ---------------------------------------------------------------------------


def test_from_payload_tool_calls_missing_defaults_to_empty_tuple() -> None:
    """``#[serde(default)] Vec<ToolCallRequest>``: a missing key -> the empty
    tuple (NOT ``None`` -- mirrors grok's ``Vec::new()`` default)."""
    result = ChatRequestMessage.from_payload({"role": "user", "content": "hi"})
    assert result.tool_calls == ()
    assert isinstance(result.tool_calls, tuple)


def test_from_payload_tool_calls_null_to_empty_tuple() -> None:
    result = ChatRequestMessage.from_payload(
        {"role": "user", "content": "hi", "tool_calls": None}
    )
    assert result.tool_calls == ()


def test_from_payload_tool_calls_non_list_to_empty_tuple() -> None:
    """A non-list ``tool_calls`` wire value tolerates to the empty tuple
    (forward-compat, same posture as the R209 SearchSourceRss.links)."""
    result = ChatRequestMessage.from_payload(
        {"role": "user", "content": "hi", "tool_calls": "oops"}
    )
    assert result.tool_calls == ()


def test_from_payload_tool_calls_is_tuple_of_tool_call_requests() -> None:
    """``Vec<ToolCallRequest>`` -> ``tuple[ToolCallRequest, ...]``, each item
    parsed through ToolCallRequest.from_payload."""
    result = ChatRequestMessage.from_payload(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "a",
                    "type": "function",
                    "function": {"name": "f1", "arguments": "{}"},
                },
                {
                    "id": "b",
                    "type": "function",
                    "function": {"name": "f2", "arguments": "{}"},
                },
            ],
        }
    )
    assert isinstance(result.tool_calls, tuple)
    assert len(result.tool_calls) == 2
    assert all(isinstance(tc, ToolCallRequest) for tc in result.tool_calls)
    assert result.tool_calls[0].id == "a"
    assert result.tool_calls[1].id == "b"


def test_from_payload_tool_calls_skips_non_dict_items() -> None:
    """A non-dict item in the tool_calls list is skipped (the malformed item is
    dropped, the rest still parse -- forward-compat tolerance)."""
    result = ChatRequestMessage.from_payload(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "a",
                    "type": "function",
                    "function": {"name": "f", "arguments": "{}"},
                },
                "junk",
                7,
                {
                    "id": "b",
                    "type": "function",
                    "function": {"name": "g", "arguments": "{}"},
                },
            ],
        }
    )
    assert len(result.tool_calls) == 2
    assert result.tool_calls[0].id == "a"
    assert result.tool_calls[1].id == "b"


def test_from_payload_optional_string_knobs_default_to_none() -> None:
    """``name`` / ``tool_call_id`` / ``model_id`` / ``reasoning_content`` are
    ``Option<String>`` -> default to ``None`` when absent."""
    result = ChatRequestMessage.from_payload({"role": "user", "content": "hi"})
    assert result.name is None
    assert result.tool_call_id is None
    assert result.model_id is None
    assert result.reasoning_content is None


# ---------------------------------------------------------------------------
# Constructors: system / user / assistant / assistant_tool_call / tool.
# ---------------------------------------------------------------------------


def test_system_constructor() -> None:
    msg = ChatRequestMessage.system("you are helpful")
    assert msg.role == Role.SYSTEM
    assert isinstance(msg.content, ChatTextContent)
    assert msg.content.text == "you are helpful"
    assert msg.name is None
    assert msg.tool_calls == ()
    assert msg.tool_call_id is None
    assert msg.model_id is None
    assert msg.reasoning_content is None


def test_user_constructor() -> None:
    msg = ChatRequestMessage.user("hello")
    assert msg.role == Role.USER
    assert msg.content.text == "hello"


def test_assistant_constructor_with_reasoning() -> None:
    msg = ChatRequestMessage.assistant("hi", "grok-1", "because")
    assert msg.role == Role.ASSISTANT
    assert msg.content.text == "hi"
    assert msg.model_id == "grok-1"
    assert msg.reasoning_content == "because"


def test_assistant_constructor_without_reasoning() -> None:
    """grok ``reasoning_content: Option<String>`` -> defaults to ``None``."""
    msg = ChatRequestMessage.assistant("hi", "grok-1")
    assert msg.reasoning_content is None


def test_assistant_tool_call_constructor() -> None:
    tc = ToolCallRequest.with_function("f", "{}")
    msg = ChatRequestMessage.assistant_tool_call(tc)
    assert msg.role == Role.ASSISTANT
    assert msg.content.text == ""  # empty text body
    assert msg.tool_calls == (tc,)


def test_tool_constructor() -> None:
    msg = ChatRequestMessage.tool("call_0", "result data")
    assert msg.role == Role.TOOL
    assert msg.content.text == "result data"
    assert msg.tool_call_id == "call_0"


# ---------------------------------------------------------------------------
# Read-only helpers: is_system_message + text_content.
# ---------------------------------------------------------------------------


def test_is_system_message_true_for_system_role() -> None:
    assert ChatRequestMessage.system("p").is_system_message() is True


def test_is_system_message_false_for_other_roles() -> None:
    assert ChatRequestMessage.user("p").is_system_message() is False
    assert ChatRequestMessage.assistant("p", "m").is_system_message() is False
    assert ChatRequestMessage.tool("id", "p").is_system_message() is False


def test_text_content_extracts_text_from_string_body() -> None:
    assert ChatRequestMessage.user("hello world").text_content() == "hello world"


def test_text_content_extracts_text_from_blocks_body() -> None:
    """text_content joins the ``text`` field of every text block with ``\\n``
    (image-url blocks contribute nothing)."""
    result = ChatRequestMessage.from_payload(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "line1"},
                {"type": "image_url", "image_url": {"url": "http://x/a.png"}},
                {"type": "text", "text": "line2"},
            ],
        }
    )
    assert result.text_content() == "line1\nline2"


def test_text_content_empty_for_image_only_body() -> None:
    result = ChatRequestMessage.from_payload(
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "http://x/a.png"}}],
        }
    )
    assert result.text_content() == ""


# ---------------------------------------------------------------------------
# Copy-on-work mutators: with_text_content + with_appended_text.
# ---------------------------------------------------------------------------


def test_with_text_content_replaces_content() -> None:
    """Mirror set_text_content: returns a copy with content replaced (the
    original is unchanged -- frozen copy-on-work)."""
    original = ChatRequestMessage.user("old")
    rewritten = original.with_text_content("new")
    assert original.content.text == "old"  # unchanged
    assert rewritten.content.text == "new"
    assert rewritten.role == Role.USER  # other fields preserved


def test_with_text_content_preserves_tool_calls_and_knobs() -> None:
    tc = ToolCallRequest.with_function("f", "{}")
    original = ChatRequestMessage(
        role=Role.ASSISTANT,
        content=ChatTextContent(text="old"),
        tool_calls=(tc,),
        model_id="grok-1",
    )
    rewritten = original.with_text_content("new")
    assert rewritten.tool_calls == (tc,)
    assert rewritten.model_id == "grok-1"


def test_with_appended_text_on_empty_content_replaces() -> None:
    """An empty content delegates to with_text_content (grok's append on empty
    is a replace)."""
    original = ChatRequestMessage.assistant("", "grok-1")
    result = original.with_appended_text("first")
    assert result.content.text == "first"


def test_with_appended_text_on_text_content_concatenates() -> None:
    original = ChatRequestMessage.user("hello")
    result = original.with_appended_text(" world")
    assert result.content.text == "hello world"
    assert isinstance(result.content, ChatTextContent)


def test_with_appended_text_on_blocks_content_appends_text_block() -> None:
    original = ChatRequestMessage.from_payload(
        {"role": "user", "content": [{"type": "text", "text": "a"}]}
    )
    assert isinstance(original.content, ChatBlocksContent)
    result = original.with_appended_text("b")
    assert isinstance(result.content, ChatBlocksContent)
    assert len(result.content.blocks) == 2
    assert result.content.blocks[0].text == "a"
    assert result.content.blocks[1].text == "b"
    # original unchanged (copy-on-work)
    assert len(original.content.blocks) == 1


def test_with_appended_text_preserves_role_and_knobs() -> None:
    original = ChatRequestMessage.tool("call_0", "partial")
    result = original.with_appended_text(" more")
    assert result.role == Role.TOOL
    assert result.tool_call_id == "call_0"
    assert result.content.text == "partial more"


# ---------------------------------------------------------------------------
# Value semantics: frozen + slots + hashable.
# ---------------------------------------------------------------------------


def test_chat_request_message_is_frozen() -> None:
    """``@dataclass(frozen=True)`` -> mutating the first declared slot raises
    (B010-safe: the attribute name is a variable, the first declared slot)."""
    msg = ChatRequestMessage.user("hi")
    field_name = next(iter(type(msg).__slots__))
    with pytest.raises(FrozenInstanceError):
        setattr(msg, field_name, "rewritten")  # type: ignore[misc]


def test_chat_request_message_is_hashable_and_equal() -> None:
    a = ChatRequestMessage.user("hi")
    b = ChatRequestMessage.user("hi")
    assert a == b
    assert hash(a) == hash(b)

    tc = ToolCallRequest.with_function("f", "{}")
    c = ChatRequestMessage(
        role=Role.ASSISTANT,
        content=ChatTextContent(text=""),
        tool_calls=(tc,),
    )
    d = ChatRequestMessage(
        role=Role.ASSISTANT,
        content=ChatTextContent(text=""),
        tool_calls=(tc,),
    )
    assert c == d
    assert hash(c) == hash(d)


def test_chat_request_message_declares_slots() -> None:
    """``slots=True`` -> the class declares ``__slots__`` over its 7 fields (no
    per-instance ``__dict__``)."""
    assert ChatRequestMessage.__slots__ == (
        "role",
        "content",
        "name",
        "tool_calls",
        "tool_call_id",
        "model_id",
        "reasoning_content",
    )
    msg = ChatRequestMessage.user("hi")
    assert not hasattr(msg, "__dict__")
