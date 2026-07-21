"""OpenAI ChatCompletion streaming delta leaf cluster (R208,
``xai-grok-sampling-types`` ``types.rs``).

R208 lands the third slice of ``types.rs`` -- the streaming-delta leaves of the
OpenAI-compatible ChatCompletion chunk family. A streamed response arrives as a
sequence of ``ChatCompletionChunk`` frames; each carries a list of
:class:`ChatChunkChoice`, whose :class:`ChatChunkDelta` body aggregates the
incremental text / tool-call fragments for one choice. This module lands the 4
delta leaves (the chunk envelope itself, ``ChatCompletionChunk``, still depends
on ``crate::serde_helpers::empty_string_as_none`` via its ``system_fingerprint``
field and is therefore deferred -- these 4 leaves are the zero-helper subset
that closes against the R206 atomic slice alone):

- :class:`ToolCallFunctionDelta` (``#[derive(Default)]`` struct) -- the
  ``name`` / ``arguments`` fragments of one streamed tool call. ``name`` arrives
  only in the first chunk; ``arguments`` may arrive across many.
- :class:`ToolCallDelta` (``#[derive(Default)]`` struct) -- one streamed tool
  call: ``index`` (positional, correlates fragments), ``id`` / ``kind`` (wire
  ``type``, only in the first chunk), ``function`` (the name/arguments fragment).
  All fields bar ``index`` are optional so every chunk deserializes.
- :class:`ChatChunkDelta` (``#[derive(Default)]`` struct) -- the delta body of
  one choice: ``role`` (only in the first chunk) / ``content`` /
  ``reasoning_content`` / ``tool_calls`` (the streamed tool-call list) /
  ``tool_call_id``. Consumes :class:`Role` + :class:`ToolCallDelta`.
- :class:`ChatChunkChoice` (struct) -- one choice in a chunk: ``index`` /
  ``delta`` / ``finish_reason``. Consumes :class:`ChatChunkDelta` +
  :class:`FinishReason`.

Dependency closure: zero external (no ``crate::rs``, no ``serde_helpers``). The
4 leaves close against the R206 slice (:class:`Role` / :class:`FinishReason`)
plus each other. The grok ``deserialize_with = "deserialize_null_default"``
helper on ``ChatChunkDelta.tool_calls`` is a 6-line local free function in
``types.rs`` (``Option::<T>::deserialize(...).map(|opt| opt.unwrap_or_default())``)
-- it is inlined here as a null-tolerant parser (a ``null`` / missing /
non-list ``tool_calls`` -> empty tuple), NOT pulled in as a helper module.

This module is no-I/O (``serde_json::Value`` -> ``dict`` / wire value).
Migration map (grok -> Python):

- ``#[derive(Default)] struct`` (:class:`ToolCallFunctionDelta` /
  :class:`ToolCallDelta` / :class:`ChatChunkDelta`) ->
  ``@dataclass(frozen=True, slots=True)`` + ``default()`` classmethod (mirrors
  ``Default::default``) + a tolerant ``from_payload``.
- plain ``#[derive(Serialize, Deserialize)] struct`` (:class:`ChatChunkChoice`)
  -> ``@dataclass(frozen=True, slots=True)`` + a tolerant ``from_payload``
  (``finish_reason`` defaults to ``None``; a non-dict payload -> a zero-index
  empty-delta fallback so a malformed chunk never crashes the stream).
- ``#[serde(rename="type")] Option<String>`` (:class:`ToolCallDelta.kind`) ->
  ``kind`` field holding the wire ``type`` value (a free-form string here, NOT
  the :class:`ToolType` enum -- grok types it as ``Option<String>`', unlike the
  R207 :class:`ToolCallRequest.kind` which is :class:`ToolType`).
- ``#[serde(deserialize_with="deserialize_null_default")] Vec<ToolCallDelta>``
  (:class:`ChatChunkDelta.tool_calls``) -> ``tuple[ToolCallDelta, ...]`` with a
  null/missing/non-list tolerant parser (inlined ``deserialize_null_default``).
- ``#[serde(skip_serializing_if="Option::is_none")]`` -> ``T | None = None``;
  ``Option<Role>`` / ``Option<FinishReason>`` parse through the enum's strict
  ``from_payload`` only when the wire value is a string (a ``null`` / non-string
  stays ``None`` -- mirrors ``Option::None``).

Naming: the 4 leaves carry no ``Chat`` prefix collision risk --
:class:`ToolCallDelta` / :class:`ToolCallFunctionDelta` are new (distinct from
the R207 :class:`ToolCallRequest` and the R206 :class:`ToolCallFunction`, which
model the non-streaming tool-call shapes); :class:`ChatChunkDelta` /
:class:`ChatChunkChoice` are new (the streaming-chunk envelope has no Messages
API peer -- the R201 :class:`MessageDeltaBody` is the Anthropic streaming body,
a flat struct rather than a delta-with-choices).

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip -- ``from_payload``
covers the parse direction the streaming consumer needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minimax_code.sampler.chat_completion_leaves import (
    FinishReason,
    Role,
)

# ---------------------------------------------------------------------------
# ToolCallFunctionDelta: streamed function name/arguments fragment (Default).
# ---------------------------------------------------------------------------
#
# `name` arrives only in the first chunk for a tool call; `arguments` may
# arrive across many chunks (each carrying a partial-JSON fragment). Both
# optional + default so every chunk deserializes.


@dataclass(frozen=True, slots=True)
class ToolCallFunctionDelta:
    """The ``name`` / ``arguments`` fragment of one streamed tool call.

    ``name`` is present only in the first chunk; ``arguments`` may be empty or a
    partial-JSON fragment that the consumer concatenates across chunks. Use
    :meth:`default` for the all-``None`` fallback (mirrors ``Default::default``),
    :meth:`from_payload` for the wire dict -> struct mapping."""

    name: str | None = None
    arguments: str | None = None

    @classmethod
    def default(cls) -> ToolCallFunctionDelta:
        """Mirror ``#[derive(Default)]``: both fields ``None``."""
        return cls()

    @classmethod
    def from_payload(cls, payload: Any) -> ToolCallFunctionDelta:
        """Tolerant constructor: a missing / null / non-dict payload -> the
        all-``None`` default; otherwise ``name`` / ``arguments`` map straight
        through (each staying ``None`` when its key is absent)."""
        if not isinstance(payload, dict):
            return cls.default()
        return cls(name=payload.get("name"), arguments=payload.get("arguments"))


# ---------------------------------------------------------------------------
# ToolCallDelta: one streamed tool call (Default, index/id/kind/function).
# ---------------------------------------------------------------------------
#
# All fields bar `index` are optional so every chunk deserializes: the first
# chunk carries `id` + `kind` (wire `type`) + the function `name` + the start of
# `arguments`; subsequent chunks carry only `index` + an `arguments` fragment.


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    """One streamed tool call.

    ``index`` is the positional index correlating fragments across chunks
    (defaults to 0); ``id`` / ``kind`` (wire ``type``) arrive only in the first
    chunk; ``function`` carries the name/arguments fragment. Use
    :meth:`default` / :meth:`from_payload`."""

    index: int = 0
    id: str | None = None
    kind: str | None = None
    function: ToolCallFunctionDelta | None = None

    @classmethod
    def default(cls) -> ToolCallDelta:
        """Mirror ``#[derive(Default)]``: ``index`` 0, the rest ``None``."""
        return cls()

    @classmethod
    def from_payload(cls, payload: Any) -> ToolCallDelta:
        """Tolerant constructor: a missing / null / non-dict payload -> the
        default; otherwise ``index`` defaults to 0, ``kind`` reads the wire
        ``type`` key, ``function`` parses through :meth:`ToolCallFunctionDelta.
        from_payload` only when present + dict-valued (else ``None``)."""
        if not isinstance(payload, dict):
            return cls.default()
        raw_func = payload.get("function")
        return cls(
            index=payload.get("index", 0),
            id=payload.get("id"),
            kind=payload.get("type"),
            function=(
                ToolCallFunctionDelta.from_payload(raw_func)
                if isinstance(raw_func, dict)
                else None
            ),
        )


# ---------------------------------------------------------------------------
# ChatChunkDelta: the delta body of one streamed choice (Default).
# ---------------------------------------------------------------------------
#
# `role` arrives only in the first chunk; `content` / `reasoning_content` are
# the incremental text fragments; `tool_calls` is the streamed tool-call list
# (the grok `deserialize_null_default` helper tolerates a `null` JSON value as
# the empty vec -- inlined here as a null/missing/non-list -> empty tuple);
# `tool_call_id` is the tool-call correlation id.


@dataclass(frozen=True, slots=True)
class ChatChunkDelta:
    """The delta body of one streamed ChatCompletion choice.

    ``role`` is present only in the first chunk (else ``None``); ``content`` /
    ``reasoning_content`` are incremental text fragments; ``tool_calls`` is the
    streamed tool-call list (a ``null`` / missing / non-list wire value
    tolerates to the empty tuple -- the inlined ``deserialize_null_default``);
    ``tool_call_id`` is the optional correlation id. Use :meth:`default` /
    :meth:`from_payload`."""

    role: Role | None = None
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: tuple[ToolCallDelta, ...] = ()
    tool_call_id: str | None = None

    @classmethod
    def default(cls) -> ChatChunkDelta:
        """Mirror ``#[derive(Default)]``: ``role`` / ``content`` /
        ``reasoning_content`` / ``tool_call_id`` ``None``, ``tool_calls`` the
        empty tuple."""
        return cls()

    @classmethod
    def from_payload(cls, payload: Any) -> ChatChunkDelta:
        """Tolerant constructor: a missing / null / non-dict payload -> the
        default. ``role`` parses through :meth:`Role.from_payload` only when the
        wire value is a string (a ``null`` / non-string stays ``None``); the
        ``tool_calls`` list tolerates ``null`` / missing / non-list as the empty
        tuple (inlined ``deserialize_null_default``), recursively parsing each
        dict item through :meth:`ToolCallDelta.from_payload`."""
        if not isinstance(payload, dict):
            return cls.default()
        raw_role = payload.get("role")
        raw_tool_calls = payload.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            tool_calls = tuple(
                ToolCallDelta.from_payload(item)
                for item in raw_tool_calls
                if isinstance(item, dict)
            )
        else:
            tool_calls = ()
        return cls(
            role=Role.from_payload(raw_role) if isinstance(raw_role, str) else None,
            content=payload.get("content"),
            reasoning_content=payload.get("reasoning_content"),
            tool_calls=tool_calls,
            tool_call_id=payload.get("tool_call_id"),
        )


# ---------------------------------------------------------------------------
# ChatChunkChoice: one choice in a streamed chunk (index + delta + finish).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChatChunkChoice:
    """One choice in a streamed ChatCompletion chunk.

    ``index`` is the positional choice index; ``delta`` is the incremental body
    (a :class:`ChatChunkDelta`); ``finish_reason`` is non-``None`` only on the
    terminal chunk for this choice. Use :meth:`from_payload` for the wire dict
    -> struct mapping (a non-dict payload -> a zero-index empty-delta fallback
    so a malformed chunk never crashes the stream)."""

    index: int
    delta: ChatChunkDelta
    finish_reason: FinishReason | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> ChatChunkChoice:
        """Tolerant constructor: a missing / null / non-dict payload -> a
        zero-index empty-delta fallback; otherwise ``index`` defaults to 0,
        ``delta`` parses through :meth:`ChatChunkDelta.from_payload`, and
        ``finish_reason`` parses through :meth:`FinishReason.from_payload` only
        when the wire value is a string (a ``null`` / non-string stays
        ``None``)."""
        if not isinstance(payload, dict):
            return cls(index=0, delta=ChatChunkDelta.default())
        raw_finish = payload.get("finish_reason")
        return cls(
            index=payload.get("index", 0),
            delta=ChatChunkDelta.from_payload(payload.get("delta")),
            finish_reason=(
                FinishReason.from_payload(raw_finish)
                if isinstance(raw_finish, str)
                else None
            ),
        )


__all__ = [
    "ChatChunkChoice",
    "ChatChunkDelta",
    "ToolCallDelta",
    "ToolCallFunctionDelta",
]
