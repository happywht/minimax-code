"""Model-facing output extraction (R110).

Fusion of grok-build's ``xai-tool-runtime::render``. Tool outputs carry
both structured data (for agent/client logic) and a *model-facing*
representation as MCP content blocks. :class:`ToolOutput` is the trait
the runtime uses to extract the model-facing part; the default
implementation serialises the output to JSON, then walks the structure
looking for embedded :class:`ContentBlock`-shaped values (images,
resources). Embedded blocks are promoted to proper content-block types;
everything else becomes :class:`ContentBlock.Text`.

The tool -> render loop closure
-------------------------------

``tool.rs`` and ``render.rs`` form a mutually-referencing pair in the
crate. R109 (``tool.py``) kept ``tool`` free of any runtime ``render``
import — every ``render`` reference was a deferred annotation string
plus one function-local lazy import in
:meth:`TypedToolOutput.from_value`. This round (R110) lands ``render.py``
importing :class:`ContentBlock` from ``tool.py`` **one-way at module
level**. The runtime dependency edge is then single-directional
(``render -> tool``); the cycle is closed without ever creating a
top-level import cycle. After R110,
:meth:`TypedToolOutput.from_value`'s lazy import resolves to the *real*
:func:`extract_content_blocks` defined here (R109's tests injected a
fake ``render`` module; R110's tests additionally exercise the real one
to prove the seam is closed).

serde_json fidelity
-------------------

:func:`extract_content_blocks` walks a ``serde_json::Value``. Python's
natural equivalent is plain ``dict`` / ``list`` / scalar values, so the
value parameter is ``Any`` and the helpers dispatch on ``isinstance``.
One subtlety: Rust ``Value::to_string()`` serialises in **compact,
space-less** form (``{"value":42}``, ``[1,2,3]``), whereas Python's
:func:`json.dumps` defaults to a pretty form with spaces. The internal
:func:`_stringify` helper uses ``json.dumps(value, separators=(",", ":"))``
to reproduce the serde_json wire shape exactly — several strategies
(:func:`extract_content_blocks` strategy 3 ``structuredContent``,
strategy 4 remainder, and strategy 5 fallback) emit these stringified
values as :class:`ContentBlock.Text`, so the spacing matters.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias

from minimax_code.tool_runtime.tool import ContentBlock

__all__ = [
    "ModelOutputExtractor",
    "ToolChatCompletion",
    "ToolChatCompletionResponse",
    "ToolCodeExecutionResult",
    "ToolOutput",
    "ToolStreamError",
    "extract_content_blocks",
    "extractor_for",
]


# -----------------------------------------------------------------------
# ToolOutput — unified trait for typed tool outputs.
# -----------------------------------------------------------------------


class ToolOutput(Protocol):
    """Unified contract for typed tool outputs (Rust ``trait ToolOutput``).

    Combines model-facing content extraction and optional chat-completion
    response generation into a single contract. Rust's trait has two
    methods with default bodies (``model_output`` -> empty ``Vec``;
    ``chat_completion_output`` -> ``None``). A Python :class:`Protocol`
    carries no default bodies, so:

    * **Default semantics are a documented convention.** An implementor
      that wants the "automatic extraction" default returns ``[]`` from
      :meth:`model_output`; the runtime then calls
      :func:`extract_content_blocks` on the serialised JSON value
      instead. An implementor that wants "no chat-completion card"
      returns ``None`` from :meth:`chat_completion_output`.
    * The blanket Rust impls (``impl ToolOutput for Value`` / ``String``
      / ``Box<T>`` / several ``xai_tool_types`` structs) have no Python
      equivalent under a structural :class:`Protocol`: any object
      exposing the two methods satisfies the contract, so no explicit
      registration is needed (Python is dynamically typed).

    R109's :class:`TypedToolOutput` realises this contract by *field* —
    its ``model_output`` / ``chat_completion_output`` fields ARE the
    trait-method implementations (a field is a readable attribute), so
    it satisfies the Protocol structurally without declaring the
    methods.
    """

    def model_output(self) -> list[ContentBlock]:
        """Model-facing content blocks for this output.

        Return an empty list to signal "use automatic extraction" — the
        runtime then calls :func:`extract_content_blocks` on the
        serialised JSON value instead (Rust default body returns
        ``Vec::new()``).
        """
        ...

    def chat_completion_output(self) -> ToolChatCompletionResponse | None:
        """Build a chat-completion response frame from this output.

        Returns ``None`` by default (no card). Rust default body returns
        ``None``.
        """
        ...


# -----------------------------------------------------------------------
# ToolChatCompletionResponse — minimal completion envelope to the frontend.
# -----------------------------------------------------------------------


@dataclass
class ToolChatCompletionResponse:
    """Minimal chat-completion response streamed to the frontend.

    Rust ``#[derive(Serialize, Deserialize)]`` with both fields
    ``skip_serializing_if = "Option::is_none"`` — a default instance
    serialises to ``{}``.
    """

    #: The main completion payload (``None`` = no payload).
    result: ToolChatCompletion | None = None
    #: Structured stream error, e.g. rate-limit / tool failure.
    stream_error: ToolStreamError | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise with the crate's skip-on-None omit policy."""
        out: dict[str, Any] = {}
        if self.result is not None:
            out["result"] = self.result.to_dict()
        if self.stream_error is not None:
            out["stream_error"] = self.stream_error.to_dict()
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolChatCompletionResponse:
        """Deserialise (per-field ``Option`` fall-back to ``None``)."""
        result_raw = data.get("result")
        stream_raw = data.get("stream_error")
        return cls(
            result=(
                ToolChatCompletion.from_dict(result_raw)
                if isinstance(result_raw, dict)
                else None
            ),
            stream_error=(
                ToolStreamError.from_dict(stream_raw)
                if isinstance(stream_raw, dict)
                else None
            ),
        )


# -----------------------------------------------------------------------
# ToolChatCompletion — minimal completion payload for a tool result.
# -----------------------------------------------------------------------


@dataclass
class ToolChatCompletion:
    """Minimal chat-completion response for a tool result (sent to client).

    Rust ``#[derive(Serialize, Deserialize)]``: ``sender`` / ``message``
    carry ``#[serde(default)]`` (always serialised; default to ``""`` on
    a missing field); the optional fields carry
    ``skip_serializing_if = "Option::is_none"``; ``extra`` carries
    ``#[serde(flatten)]`` so unknown keys round-trip at the top level.
    """

    #: Always ``"assistant"`` on the wire (set by the sender). ``#[serde(default)]``.
    sender: str = ""
    #: Text body of the response. ``#[serde(default)]``.
    message: str = ""
    #: Tag discriminator: ``"final"``, ``"raw_function_result"``,
    #: ``"tool_usage_card"``, ``"tool_partial_output"``, etc.
    message_tag: str | None = None
    #: Identifies the tool-usage card this result belongs to.
    tool_usage_card_id: str | None = None
    #: JSON-encoded card attachment (images, render cards, files).
    card_attachment: str | None = None
    #: Media generation type: ``"image_gen"``, ``"video_gen"``, etc.
    media_gen_type: str | None = None
    #: Code execution result.
    code_execution_result: ToolCodeExecutionResult | None = None
    #: Catch-all for additional fields. ``#[serde(flatten)]`` — merged
    #: into the top level on serialise; unknown keys collect here on
    #: deserialise.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise: emit ``sender``/``message`` always, optionals on non-None,
        then flatten :attr:`extra` at the top level.
        """
        out: dict[str, Any] = {
            "sender": self.sender,
            "message": self.message,
        }
        if self.message_tag is not None:
            out["message_tag"] = self.message_tag
        if self.tool_usage_card_id is not None:
            out["tool_usage_card_id"] = self.tool_usage_card_id
        if self.card_attachment is not None:
            out["card_attachment"] = self.card_attachment
        if self.media_gen_type is not None:
            out["media_gen_type"] = self.media_gen_type
        if self.code_execution_result is not None:
            out["code_execution_result"] = self.code_execution_result.to_dict()
        out.update(self.extra)  # #[serde(flatten)]
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolChatCompletion:
        """Deserialise: known fields by key, unknown keys into :attr:`extra`.

        Mirrors Rust ``#[serde(default)]`` (missing ``sender``/``message``
        -> ``""``) and ``#[serde(flatten)]`` (unknown keys collect into
        ``extra``). A non-string ``sender``/``message`` falls back to
        ``""`` (serde would fail the whole struct; this keeps siblings).
        """
        known = {
            "sender",
            "message",
            "message_tag",
            "tool_usage_card_id",
            "card_attachment",
            "media_gen_type",
            "code_execution_result",
        }
        cer_raw = data.get("code_execution_result")
        sender_raw = data.get("sender")
        message_raw = data.get("message")
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(
            sender=sender_raw if isinstance(sender_raw, str) else "",
            message=message_raw if isinstance(message_raw, str) else "",
            message_tag=_opt_str(data.get("message_tag")),
            tool_usage_card_id=_opt_str(data.get("tool_usage_card_id")),
            card_attachment=_opt_str(data.get("card_attachment")),
            media_gen_type=_opt_str(data.get("media_gen_type")),
            code_execution_result=(
                ToolCodeExecutionResult.from_dict(cer_raw)
                if isinstance(cer_raw, dict)
                else None
            ),
            extra=extra,
        )


# -----------------------------------------------------------------------
# ToolCodeExecutionResult — lightweight code-exec result on the completion.
# -----------------------------------------------------------------------


@dataclass
class ToolCodeExecutionResult:
    """Lightweight code-execution result carried on the completion.

    Rust ``#[derive(Default, Serialize, Deserialize)]`` with every field
    ``#[serde(default)]`` — a default instance serialises to
    ``{"stdout":"","stderr":"","exit_code":0,"command_timed_out":false}``.
    """

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    command_timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise (every field emitted; ``#[serde(default)]``)."""
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "command_timed_out": self.command_timed_out,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCodeExecutionResult:
        """Deserialise with per-field fall-back (``#[serde(default)]``)."""
        stdout_raw = data.get("stdout")
        stderr_raw = data.get("stderr")
        exit_raw = data.get("exit_code")
        timed_out_raw = data.get("command_timed_out")
        return cls(
            stdout=stdout_raw if isinstance(stdout_raw, str) else "",
            stderr=stderr_raw if isinstance(stderr_raw, str) else "",
            exit_code=exit_raw if isinstance(exit_raw, int) and not isinstance(
                exit_raw, bool
            ) else 0,
            command_timed_out=(
                timed_out_raw if isinstance(timed_out_raw, bool) else False
            ),
        )


# -----------------------------------------------------------------------
# ToolStreamError — structured stream error alongside the completion.
# -----------------------------------------------------------------------


@dataclass
class ToolStreamError:
    """Structured stream error returned alongside the completion.

    Rust ``#[derive(Default, Serialize, Deserialize)]``: ``message``
    always serialised (``#[serde(default)]``); ``typed_error`` carries
    ``skip_serializing_if = "Option::is_none"``.
    """

    message: str = ""
    #: Opaque typed-error payload. The downstream chat layer
    #: deserialises this into the concrete proto enum variant.
    typed_error: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise: ``message`` always, ``typed_error`` on non-None."""
        out: dict[str, Any] = {"message": self.message}
        if self.typed_error is not None:
            out["typed_error"] = self.typed_error
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolStreamError:
        """Deserialise (``message`` falls back to ``""``, ``typed_error`` to ``None``)."""
        message_raw = data.get("message")
        return cls(
            message=message_raw if isinstance(message_raw, str) else "",
            typed_error=data.get("typed_error"),
        )


# -----------------------------------------------------------------------
# extract_content_blocks — the 5-strategy model-output extractor.
# -----------------------------------------------------------------------

#: The ``ContentBlock`` enum is ``#[serde(tag = "type", rename_all =
#: "snake_case")]``, so a JSON object can only be a content block when its
#: ``"type"`` field is one of these three values.
_CONTENT_BLOCK_TYPES: tuple[str, ...] = ("text", "image", "resource")


def _looks_like_content_block(value: Any) -> bool:
    """Cheap check: could ``value`` plausibly parse as a :class:`ContentBlock`?

    Only objects with a ``"type"`` field whose value is one of the known
    discriminators pass. Avoids a full :meth:`ContentBlock.from_dict` on
    the vast majority of values.
    """
    return isinstance(value, dict) and value.get("type") in _CONTENT_BLOCK_TYPES


def _try_parse_block(value: Any) -> ContentBlock | None:
    """Try to parse ``value`` as a :class:`ContentBlock`.

    Returns ``None`` immediately when the value fails the cheap
    :func:`_looks_like_content_block` check, avoiding a full parse for
    non-matching shapes. Any parse error maps to ``None`` (Rust's
    ``serde_json::from_value(...).ok()``).
    """
    if not _looks_like_content_block(value):
        return None
    try:
        return ContentBlock.from_dict(value)
    except (ValueError, TypeError, KeyError):
        return None


def _stringify(value: Any) -> str:
    """Serialise ``value`` as compact JSON (Rust ``Value::to_string``).

    ``serde_json`` serialises with no whitespace; Python's
    :func:`json.dumps` defaults to a spaced form. The explicit
    ``separators=(",", ":")`` reproduces the serde_json wire shape so
    stringified values match the Rust output byte-for-byte (e.g.
    ``{"value":42}`` not ``{"value": 42}``).
    """
    return json.dumps(value, separators=(",", ":"))


def _value_to_block(value: Any) -> ContentBlock:
    """Convert a single value to a :class:`ContentBlock`.

    Uses :func:`_try_parse_block` first; on failure wraps the value as
    :class:`ContentBlock.Text`. Strings are used verbatim (no extra JSON
    quoting); all other types go through :func:`_stringify`.
    """
    block = _try_parse_block(value)
    if block is not None:
        return block
    if isinstance(value, str):
        return ContentBlock.Text(value)
    return ContentBlock.Text(_stringify(value))


def _classify_field(value: Any) -> list[ContentBlock] | None:
    """Classify an object field value as block content.

    Returns the list of content blocks the field contributes, or
    ``None`` when the value does not look like block content (in which
    case the caller keeps it in the remainder). Conservative for arrays:
    every element must parse as a :class:`ContentBlock`; mixed arrays go
    to ``None`` so ambiguous data (e.g. ``"scores": [0.9, 0.8]``) is not
    silently dropped.

    Mirrors Rust ``enum FieldShape { Block(ContentBlock),
    Blocks(Vec<ContentBlock>), Other }`` — the ``Block`` and ``Blocks``
    variants both contribute their blocks to the caller's
    ``extracted`` list, so they collapse to a single ``list`` return
    (``[block]`` vs ``blocks``); ``Other`` is ``None``.
    """
    # Single block.
    block = _try_parse_block(value)
    if block is not None:
        return [block]
    # Array of blocks — strict: every element must parse.
    if isinstance(value, list) and value and all(
        _looks_like_content_block(v) for v in value
    ):
        try:
            return [ContentBlock.from_dict(v) for v in value]
        except (ValueError, TypeError, KeyError):
            return None
    return None


def extract_content_blocks(value: Any) -> list[ContentBlock]:
    """Extract MCP-compatible :class:`ContentBlock`\\ s from a JSON value.

    Strategies are tried in order — first match wins:

    +---+-------------------------------------------------------------+-------------------------------------------+
    | # | Shape                                                       | Result                                    |
    +===+=============================================================+===========================================+
    | 1 | Value is itself a ``ContentBlock`` (``{"type":"text",...}``) | ``[block]``                               |
    +---+-------------------------------------------------------------+-------------------------------------------+
    | 2 | Array containing >= 1 ``ContentBlock``                      | each element: block or text               |
    +---+-------------------------------------------------------------+-------------------------------------------+
    | 3 | Object with ``"content": [...]`` (MCP ``CallToolResult``)   | ``structuredContent`` (if any) as JSON    |
    |   |                                                             | text, followed by the content array       |
    +---+-------------------------------------------------------------+-------------------------------------------+
    | 4 | Object with mixed fields                                    | block-shaped fields extracted, rest as    |
    |   |                                                             | JSON text                                 |
    +---+-------------------------------------------------------------+-------------------------------------------+
    | 5 | Anything else                                               | ``ContentBlock.Text`` with the            |
    |   |                                                             | stringified value                         |
    +---+-------------------------------------------------------------+-------------------------------------------+

    A special case ahead of strategy 3 recognises the grok-build
    ``ToolRunResult`` shape (object with ``prompt_text`` + ``output`` +
    ``effective_tool_name``): the model sees ``prompt_text`` verbatim,
    never a JSON dump of the structured result.
    """
    # 1. Value IS a single ContentBlock.
    block = _try_parse_block(value)
    if block is not None:
        return [block]

    # 2. Array: convert each element (block-shaped -> block, else -> text).
    #    Only enter this path when at least one element looks like a
    #    ContentBlock so plain arrays like [1,2,3] fall through to text.
    if isinstance(value, list) and value and any(
        _looks_like_content_block(v) for v in value
    ):
        return [_value_to_block(v) for v in value]

    if isinstance(value, dict):
        # grok-build ToolRunResult: the model sees prompt_text (reminders
        # appended), never a JSON dump of the structured result.
        prompt_text = value.get("prompt_text")
        if (
            isinstance(prompt_text, str)
            and "output" in value
            and "effective_tool_name" in value
        ):
            return [ContentBlock.Text(prompt_text)]

        # 3. Object with a "content" array -> the standard MCP
        #    CallToolResult shape.
        content = value.get("content")
        if (
            isinstance(content, list)
            and content
            and any(_looks_like_content_block(v) for v in content)
        ):
            # Surface structuredContent so IDs/handles the server
            # expects the model to round-trip aren't dropped.
            structured = value.get("structuredContent")
            blocks: list[ContentBlock] = []
            if structured is not None:
                blocks.append(ContentBlock.Text(_stringify(structured)))
            blocks.extend(_value_to_block(v) for v in content)
            return blocks

        # 4. Mixed object -> pull block-shaped field values out; collect
        #    the remaining fields into a single JSON text block.
        extracted: list[ContentBlock] = []
        remainder: dict[str, Any] = {}
        for key, val in value.items():
            classified = _classify_field(val)
            if classified is None:
                remainder[key] = val
            else:
                extracted.extend(classified)

        if extracted:
            result: list[ContentBlock] = []
            if remainder:
                result.append(ContentBlock.Text(_stringify(remainder)))
            result.extend(extracted)
            return result

    # 5. Fallback -> render the whole value as text.
    return [_value_to_block(value)]


# -----------------------------------------------------------------------
# Type-erased extractor (used by the toolbox registry).
# -----------------------------------------------------------------------


#: Type-erased model-output extractor. Rust
#: ``Arc<dyn Fn(&Value) -> Option<Vec<ContentBlock>> + Send + Sync>``.
#: Python objects are already reference-shared (no ``Arc``), and a
#: callable is the natural type-erased function handle.
ModelOutputExtractor: TypeAlias = Callable[[Any], list[ContentBlock] | None]


def extractor_for(output_type: type) -> ModelOutputExtractor:
    """Build a :type:`ModelOutputExtractor` for a concrete output type.

    Rust ``extractor_for<T>()`` where ``T: ToolOutput + DeserializeOwned
    + 'static``. Python is dynamically typed, so the output type is
    passed explicitly. The type must expose:

    * a ``from_dict`` classmethod (the ``DeserializeOwned`` equivalent)
      taking the raw value and returning an instance, raising on a
      malformed value;
    * the :class:`ToolOutput` surface (``model_output`` /
      ``chat_completion_output``).

    The returned extractor deserialises the value; on failure returns
    ``None``. On success it calls ``model_output()``; an empty result
    signals "use automatic extraction", so the extractor falls back to
    :func:`extract_content_blocks` on the raw value (mirroring the Rust
    blanket ``ToolDyn`` behaviour).
    """
    from_dict = getattr(output_type, "from_dict", None)

    def extract(value: Any) -> list[ContentBlock] | None:
        if from_dict is None:
            return None
        try:
            output = from_dict(value)
        except Exception:
            return None
        blocks = output.model_output()
        if not blocks:
            return extract_content_blocks(value)
        return blocks

    return extract


# -----------------------------------------------------------------------
# Internal helpers (module-private).
# -----------------------------------------------------------------------


def _opt_str(raw: Any) -> str | None:
    """Return ``raw`` if it is a string, else ``None`` (serde ``Option<String>``)."""
    return raw if isinstance(raw, str) else None
