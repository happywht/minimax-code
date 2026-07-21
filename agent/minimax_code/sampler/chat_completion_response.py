"""OpenAI ChatCompletion response + streaming-chunk envelopes (R215,
``xai-grok-sampling-types`` ``types.rs``).

R215 lands the sixth slice of ``types.rs`` -- the outer envelopes of the
OpenAI-compatible ChatCompletion response family. R206 landed the atomic
leaves (Role / FinishReason / ToolCallFunction), R207 the middle-layer leaves
(ChatUsage, the rename of grok ``Usage``), R208 the streaming-delta *interior*
(ChatChunkChoice / ChatChunkDelta / ToolCallDelta / ToolCallFunctionDelta);
R208 explicitly deferred the *exterior* envelopes because
``ChatCompletionChunk`` depends on ``crate::serde_helpers::empty_string_as_none``
(then un-landed). R211 landed that helper, unblocking the cluster. R215 closes
the exterior:

- :class:`ToolCallResponse` (struct) -- ``id`` / ``kind`` (wire ``type``, a
  free-form ``String`` here, NOT the :class:`ToolType` enum -- contrast the
  R207 :class:`ToolCallRequest.kind`) / ``function``. The non-streaming peer
  of the R208 :class:`ToolCallDelta`. Consumes :class:`ToolCallFunction`.
- :class:`ChatResponseMessage` (struct) -- the assistant message body inside a
  non-streaming :class:`ChatChoice`: ``role`` (required) + ``content`` /
  ``reasoning_content`` / ``tool_call_id`` / ``citations`` (optional) +
  ``tool_calls`` (the :class:`ToolCallResponse` list). Consumes :class:`Role`
  + :class:`ToolCallResponse`.
- :class:`ChatChoice` (struct) -- one choice in a non-streaming response:
  ``index`` + ``message`` (a :class:`ChatResponseMessage`) + ``finish_reason``.
  Consumes :class:`ChatResponseMessage` + :class:`FinishReason`.
- :class:`ChatCompletionResponse` (struct) -- the non-streaming reply
  envelope: ``id`` / ``object`` / ``created`` / ``model`` / ``choices`` (the
  :class:`ChatChoice` list) + ``usage`` / ``citations`` (optional). Consumes
  :class:`ChatChoice` + :class:`ChatUsage`.
- :class:`ChatCompletionChunk` (struct) -- the streaming reply envelope:
  ``id`` / ``object`` / ``created`` / ``model`` / ``choices`` (the R208
  :class:`ChatChunkChoice` list) + ``usage`` (optional) + ``system_fingerprint``
  (optional, the R211 ``empty_string_as_none`` hook -- an empty wire string
  normalizes to ``None``). Consumes :class:`ChatChunkChoice` + :class:`ChatUsage`
  + :func:`empty_string_as_none`.

Dependency closure: zero external (no ``crate::rs``, no ``xai-grok-tools``).
All 5 envelopes close against the R206 atomic slice + the R207 :class:`ChatUsage`
+ the R208 :class:`ChatChunkChoice` + the R211 :func:`empty_string_as_none`.
The R208 deferral of ``ChatCompletionChunk`` is lifted: its single external dep
(``empty_string_as_none``) landed in R211.

This module is no-I/O (``serde_json::Value`` -> ``dict`` / wire value).
Migration map (grok -> Python):

- plain ``#[derive(Serialize, Deserialize)] struct`` -> ``@dataclass(frozen=True,
  slots=True)`` + a ``from_payload`` constructor.
- ``pub role: Role`` (required, no ``#[serde(default)]``) -> a required
  ``role: Role`` field parsed strictly through :meth:`Role.from_payload` (a
  missing / non-string / unknown role raises ``ValueError`` -- mirrors serde's
  missing-required-field failure; a non-dict payload raises too, same posture
  as the R210 :class:`ChatRequestMessage`).
- ``pub index: u32`` / ``pub created: u64`` (required numerics) -> ``int`` with
  a ``0`` fallback when the key is absent (mirrors the R208
  :class:`ChatChunkChoice.index` tolerant posture -- a malformed frame never
  crashes the parse).
- ``#[serde(rename="type")] pub kind: String`` (:class:`ToolCallResponse.kind`)
  -> ``kind`` field reading the wire ``type`` key (a free-form string, NOT the
  :class:`ToolType` enum -- the response carries the raw discriminator the
  server emitted, mirroring the R208 :class:`ToolCallDelta.kind`).
- ``#[serde(skip_serializing_if="Option::is_none")] Option<T>`` -> ``T | None
  = None``.
- ``#[serde(default, skip_serializing_if="Vec::is_empty")] Vec<T>``
  (``tool_calls``) -> ``tuple[T, ...] = ()`` -- ``default`` means a missing
  field deserializes to the empty Vec (NOT a failure); ``from_payload``
  tolerates a missing / null / non-list wire value to the empty tuple, parsing
  each dict item through its ``from_payload`` (non-dict items skipped).
- ``Option<Vec<String>>`` (``citations``) -> ``tuple[str, ...] | None`` via the
  inlined :func:`_optional_string_tuple` helper (a list -> tuple of strings
  with non-string items skipped; missing / null / non-list -> ``None``).
- ``#[serde(deserialize_with="empty_string_as_none")] Option<String>``
  (:class:`ChatCompletionChunk.system_fingerprint`) -> the R211
  :func:`empty_string_as_none` value-level normalizer (an empty wire string ->
  ``None``).
- :class:`ToolCallResponse` uses the tolerant posture (a non-dict payload ->
  the all-empty instance) so a single malformed tool call never aborts the
  surrounding response parse -- mirroring the R207 :class:`ToolCallRequest`.

Naming: the 5 envelopes keep their grok names verbatim -- they have no
Anthropic Messages API peer in the package barrel (the R201
:class:`MessageDeltaBody` is the Anthropic streaming body, a flat struct; the
R205 :class:`MessagesResponse` is the Anthropic non-streaming reply,
structurally distinct). :class:`ChatUsage` is the R207 rename of grok
``Usage`` (the ``usage`` field type on both envelopes).

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip -- ``from_payload``
covers the parse direction the platform needs. The ``ChatCompletionRequest``
request envelope is still deferred (three un-landed deps:
``ToolDefinition`` + ``crate::rs::ResponseFormat`` + ``Box<dyn TraceContext>``);
:class:`SamplingConfig` (``indexmap`` + ``NonZeroU64``) is still deferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from minimax_code.sampler.chat_completion_leaves import (
    FinishReason,
    Role,
    ToolCallFunction,
)
from minimax_code.sampler.chat_completion_mid import ChatUsage
from minimax_code.sampler.chat_completion_streaming import ChatChunkChoice
from minimax_code.sampler.serde_helpers import empty_string_as_none


def _optional_string_tuple(value: Any) -> tuple[str, ...] | None:
    """Parse an ``Option<Vec<String>>`` wire value (the ``citations`` field on
    :class:`ChatResponseMessage` + :class:`ChatCompletionResponse`).

    A JSON list -> a tuple of its string items (non-string items skipped, so a
    heterogeneous list never crashes the parse); a missing / null / non-list
    wire value -> ``None`` (mirrors grok ``Option::None``). An empty list
    yields the empty tuple (mirrors grok ``Some(vec![])`` -- ``Option::is_none``
    gates the field on ``None``, not on empty)."""
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return None


# ---------------------------------------------------------------------------
# ToolCallResponse: a non-streaming assistant tool call (struct).
# ---------------------------------------------------------------------------
#
# The non-streaming peer of the R208 `ToolCallDelta`. `id` is the call id the
# server assigns; `kind` reads the wire `type` discriminator (a free-form
# string, NOT the ToolType enum -- the response echoes whatever the server
# emitted); `function` carries the resolved name + arguments. All three are
# required on the wire (grok has no Option/default on them) but the dataclass
# gives them defaults so `from_payload` can fall back to the all-empty instance
# on a malformed payload -- mirroring the R207 ToolCallRequest posture (a
# single bad tool call never aborts the surrounding response parse).


@dataclass(frozen=True, slots=True)
class ToolCallResponse:
    """A non-streaming assistant tool call (one entry in
    :attr:`ChatResponseMessage.tool_calls`).

    ``id`` is the server-assigned call id; ``kind`` is the wire ``type``
    discriminator (a free-form string, NOT the :class:`ToolType` enum);
    ``function`` carries the resolved name + arguments. Use :meth:`from_payload`
    for the wire dict -> instance mapping (a non-dict payload -> the all-empty
    instance)."""

    id: str = ""
    kind: str = ""
    function: ToolCallFunction = field(default_factory=ToolCallFunction)

    @classmethod
    def from_payload(cls, payload: Any) -> ToolCallResponse:
        """Tolerant constructor: a non-dict payload -> the all-empty instance;
        otherwise ``id`` reads the ``id`` key (default ``""``), ``kind`` reads
        the wire ``type`` key (default ``""``), and ``function`` parses through
        :meth:`ToolCallFunction.from_payload` when the ``function`` value is a
        dict (else falls back to an empty :class:`ToolCallFunction`)."""
        if not isinstance(payload, dict):
            return cls()
        raw_func = payload.get("function")
        return cls(
            id=payload.get("id", ""),
            kind=payload.get("type", ""),
            function=(
                ToolCallFunction.from_payload(raw_func)
                if isinstance(raw_func, dict)
                else ToolCallFunction()
            ),
        )


# ---------------------------------------------------------------------------
# ChatResponseMessage: the assistant message body inside a non-streaming choice.
# ---------------------------------------------------------------------------
#
# `role` is required (strict -- a missing/non-string/unknown role raises,
# mirroring serde's missing-required-field failure); `content` /
# `reasoning_content` / `tool_call_id` are optional strings; `tool_calls` is the
# optional ToolCallResponse list (default empty -- a missing/null/non-list wire
# value tolerates to the empty tuple); `citations` is an optional string list.


@dataclass(frozen=True, slots=True)
class ChatResponseMessage:
    """The assistant message body inside a non-streaming :class:`ChatChoice`.

    ``role`` is the required :class:`Role`; ``content`` / ``reasoning_content``
    / ``tool_call_id`` are optional strings; ``tool_calls`` is the optional
    :class:`ToolCallResponse` list; ``citations`` is an optional string list.
    Use :meth:`from_payload` (a non-dict payload raises -- a dict is required
    to read the required ``role`` field)."""

    role: Role
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: tuple[ToolCallResponse, ...] = ()
    tool_call_id: str | None = None
    citations: tuple[str, ...] | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> ChatResponseMessage:
        """Tolerant constructor. ``role`` parses strictly through
        :meth:`Role.from_payload` (a missing / non-string / unknown role raises
        ``ValueError``); ``content`` / ``reasoning_content`` / ``tool_call_id``
        default to ``None``; ``tool_calls`` parses each dict item through
        :meth:`ToolCallResponse.from_payload` (non-dict items skipped, a
        missing / null / non-list wire value -> the empty tuple); ``citations``
        parses through :func:`_optional_string_tuple`. A non-dict payload raises
        ``ValueError`` (a dict is required to read the required ``role``
        field)."""
        if not isinstance(payload, dict):
            raise ValueError(
                f"chat response message must be a dict, got {type(payload).__name__}"
            )
        raw_tool_calls = payload.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            tool_calls = tuple(
                ToolCallResponse.from_payload(item)
                for item in raw_tool_calls
                if isinstance(item, dict)
            )
        else:
            tool_calls = ()
        return cls(
            role=Role.from_payload(payload.get("role")),
            content=payload.get("content"),
            reasoning_content=payload.get("reasoning_content"),
            tool_calls=tool_calls,
            tool_call_id=payload.get("tool_call_id"),
            citations=_optional_string_tuple(payload.get("citations")),
        )


# ---------------------------------------------------------------------------
# ChatChoice: one choice in a non-streaming response (struct).
# ---------------------------------------------------------------------------
#
# `index` is the positional choice index (defaults to 0 when absent); `message`
# is the required ChatResponseMessage (strict -- a missing message raises via
# ChatResponseMessage.from_payload); `finish_reason` is optional.


@dataclass(frozen=True, slots=True)
class ChatChoice:
    """One choice in a non-streaming :class:`ChatCompletionResponse`.

    ``index`` is the positional choice index; ``message`` is the
    :class:`ChatResponseMessage`; ``finish_reason`` is non-``None`` only when
    the choice terminated. Use :meth:`from_payload` (a non-dict payload raises
    -- a dict is required to read the required ``message`` field)."""

    index: int
    message: ChatResponseMessage
    finish_reason: FinishReason | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> ChatChoice:
        """Tolerant constructor: ``index`` defaults to 0; ``message`` parses
        through :meth:`ChatResponseMessage.from_payload` (a missing / non-dict
        message raises -- the required ``role`` field lives there);
        ``finish_reason`` parses through :meth:`FinishReason.from_payload` only
        when the wire value is a string (a ``null`` / non-string stays
        ``None``). A non-dict payload raises ``ValueError``."""
        if not isinstance(payload, dict):
            raise ValueError(
                f"chat choice must be a dict, got {type(payload).__name__}"
            )
        raw_finish = payload.get("finish_reason")
        return cls(
            index=payload.get("index", 0),
            message=ChatResponseMessage.from_payload(payload.get("message")),
            finish_reason=(
                FinishReason.from_payload(raw_finish)
                if isinstance(raw_finish, str)
                else None
            ),
        )


# ---------------------------------------------------------------------------
# ChatCompletionResponse: the non-streaming reply envelope (struct).
# ---------------------------------------------------------------------------
#
# The top-level non-streaming reply. `id` / `object` / `created` / `model` are
# the required envelope metadata (tolerant -- default to "" / 0 when absent, so
# a trimmed envelope still parses); `choices` is the ChatChoice list (a missing
# / null / non-list tolerates to the empty tuple); `usage` is the optional
# ChatUsage; `citations` is the optional string list.


@dataclass(frozen=True, slots=True)
class ChatCompletionResponse:
    """The non-streaming ChatCompletion reply envelope.

    ``id`` / ``object`` / ``created`` / ``model`` are the envelope metadata;
    ``choices`` is the :class:`ChatChoice` list; ``usage`` is the optional
    :class:`ChatUsage`; ``citations`` is the optional string list. Use
    :meth:`from_payload` (a non-dict payload raises)."""

    id: str = ""
    object: str = ""
    created: int = 0
    model: str = ""
    choices: tuple[ChatChoice, ...] = ()
    usage: ChatUsage | None = None
    citations: tuple[str, ...] | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> ChatCompletionResponse:
        """Tolerant constructor: ``id`` / ``object`` / ``model`` default to
        ``""`` and ``created`` to ``0``; ``choices`` parses each dict item
        through :meth:`ChatChoice.from_payload` (non-dict items skipped, a
        missing / null / non-list wire value -> the empty tuple); ``usage``
        parses through :meth:`ChatUsage.from_payload` when the wire value is a
        dict (else ``None``); ``citations`` parses through
        :func:`_optional_string_tuple`. A non-dict payload raises
        ``ValueError``."""
        if not isinstance(payload, dict):
            raise ValueError(
                f"chat completion response must be a dict, got {type(payload).__name__}"
            )
        raw_choices = payload.get("choices")
        if isinstance(raw_choices, list):
            choices = tuple(
                ChatChoice.from_payload(item)
                for item in raw_choices
                if isinstance(item, dict)
            )
        else:
            choices = ()
        raw_usage = payload.get("usage")
        return cls(
            id=payload.get("id", ""),
            object=payload.get("object", ""),
            created=payload.get("created", 0),
            model=payload.get("model", ""),
            choices=choices,
            usage=ChatUsage.from_payload(raw_usage) if isinstance(raw_usage, dict) else None,
            citations=_optional_string_tuple(payload.get("citations")),
        )


# ---------------------------------------------------------------------------
# ChatCompletionChunk: the streaming reply envelope (struct).
# ---------------------------------------------------------------------------
#
# The top-level streaming reply (one per SSE frame). `id` / `object` / `created`
# / `model` are the envelope metadata; `choices` is the ChatChunkChoice list
# (the R208 streaming-delta interior); `usage` is the optional ChatUsage
# (present only on the terminal frame); `system_fingerprint` is the optional
# server fingerprint, normalized through the R211 empty_string_as_none hook
# (an empty wire string -> None -- some upstream services emit "" to mean
# "absent").


@dataclass(frozen=True, slots=True)
class ChatCompletionChunk:
    """The streaming ChatCompletion reply envelope (one SSE frame).

    ``id`` / ``object`` / ``created`` / ``model`` are the envelope metadata;
    ``choices`` is the :class:`ChatChunkChoice` list; ``usage`` is the optional
    :class:`ChatUsage` (terminal frame only); ``system_fingerprint`` is the
    optional server fingerprint (an empty wire string normalizes to ``None``
    via :func:`empty_string_as_none`). Use :meth:`from_payload` (a non-dict
    payload raises)."""

    id: str = ""
    object: str = ""
    created: int = 0
    model: str = ""
    choices: tuple[ChatChunkChoice, ...] = ()
    usage: ChatUsage | None = None
    system_fingerprint: str | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> ChatCompletionChunk:
        """Tolerant constructor: ``id`` / ``object`` / ``model`` default to
        ``""`` and ``created`` to ``0``; ``choices`` parses each dict item
        through :meth:`ChatChunkChoice.from_payload` (non-dict items skipped,
        a missing / null / non-list wire value -> the empty tuple); ``usage``
        parses through :meth:`ChatUsage.from_payload` when the wire value is a
        dict (else ``None``); ``system_fingerprint`` normalizes through
        :func:`empty_string_as_none` (an empty wire string -> ``None``). A
        non-dict payload raises ``ValueError``."""
        if not isinstance(payload, dict):
            raise ValueError(
                f"chat completion chunk must be a dict, got {type(payload).__name__}"
            )
        raw_choices = payload.get("choices")
        if isinstance(raw_choices, list):
            choices = tuple(
                ChatChunkChoice.from_payload(item)
                for item in raw_choices
                if isinstance(item, dict)
            )
        else:
            choices = ()
        raw_usage = payload.get("usage")
        return cls(
            id=payload.get("id", ""),
            object=payload.get("object", ""),
            created=payload.get("created", 0),
            model=payload.get("model", ""),
            choices=choices,
            usage=ChatUsage.from_payload(raw_usage) if isinstance(raw_usage, dict) else None,
            system_fingerprint=empty_string_as_none(payload.get("system_fingerprint")),
        )


__all__ = [
    "ChatChoice",
    "ChatCompletionChunk",
    "ChatCompletionResponse",
    "ChatResponseMessage",
    "ToolCallResponse",
]
