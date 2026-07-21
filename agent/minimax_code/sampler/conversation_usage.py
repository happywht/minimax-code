"""Conversation response stop + usage cluster (R219, ``xai-grok-sampling-types``
``conversation.rs`` third slice).

R219 lands the deferred "response stop + usage" cluster first noted in R217
:mod:`conversation_leaves`: :class:`ConversationStopReason` + :class:`TokenUsage`
+ their two ``From`` conversion impls. The deferral was two-fold:

1. The grok ``StopReason`` (``conversation.rs`` ~606) collides at the barrel
   surface with the already-migrated R201 :class:`messages.StopReason` (the
   Anthropic Messages API peer -- a structurally distinct frozen+slots
   ``Unknown(String)`` union that preserves arbitrary wire strings). Renamed
   to :class:`ConversationStopReason`, mirroring the ``Usage`` ->
   :class:`ChatUsage` barrel-collision rename precedent (R207).
2. The ``From<Usage> for TokenUsage`` impl needs the grok ``Usage`` type,
   which only landed in R207 as :class:`ChatUsage`. That blocker is now
   cleared, so the cluster lands together.

The four landed symbols:

1. :class:`ConversationStopReason` (``conversation.rs`` ~606) -- the
   ``#[serde(rename_all = "snake_case")]`` strict StrEnum. Four variants
   (Stop / Length / ToolCalls / ContentFilter), NO ``#[serde(other)]``
   catch-all -- an unknown wire string raises (strict parity with
   :class:`FinishReason` / :class:`Role`; contrast the R201 ``StopReason``
   catch-all which must never fail a terminal stream).
2. :class:`TokenUsage` (``conversation.rs`` ~644) -- the conversation-side
   token counter, a flat 5x ``u32`` struct (``prompt_tokens`` /
   ``completion_tokens`` / ``total_tokens`` / ``reasoning_tokens`` /
   ``cached_prompt_tokens``, the last with ``#[serde(default)]``). Distinct
   from :class:`ChatUsage`: ``ChatUsage`` is the wire shape received from the
   API (nested ``*_details`` breakdowns + the xAI ``cost_in_usd_ticks``
   extension); :class:`TokenUsage` is the flattened internal projection
   consumed by the conversation layer (the two nested breakdowns collapse to
   their single salient field each -- ``cached_tokens`` and
   ``reasoning_tokens``).
3. :func:`from_finish_reason` (``impl From<FinishReason> for StopReason``,
   ~629) -- the finish-reason projection. 1:1 for Stop / Length /
   ContentFilter; grok ``FinishReason::ToolCalls | FunctionCall`` both
   collapse to ``ToolCalls`` (the legacy function-call finish is observed as
   a tool-call stop on the conversation side).
4. :func:`from_usage` (``impl From<Usage> for TokenUsage``, ~667) -- the usage
   projection. ``prompt`` / ``completion`` / ``total`` pass straight through;
   ``cached_prompt_tokens`` is pulled from
   ``prompt_tokens_details.cached_tokens`` (0 when the breakdown is absent);
   ``reasoning_tokens`` from ``completion_tokens_details.reasoning_tokens``
   (0 when absent).

Dependency closure: :class:`FinishReason` + :func:`_parse_strict_enum`
(:mod:`chat_completion_leaves`, R206) + :class:`ChatUsage`
(:mod:`chat_completion_mid`, R207) + the two nested breakdown structs
(:class:`PromptTokensDetails` / :class:`CompletionTokensDetails`, R206) -- all
landed. Zero external deps beyond the sampler barrel.

This module is no-I/O (pure value-level). Migration map (grok -> Python):

- ``enum StopReason { Stop, Length, ToolCalls, ContentFilter }`` ->
  :class:`ConversationStopReason` (renamed) strict StrEnum with
  :meth:`ConversationStopReason.from_payload` (strict -- unknown raises) +
  :meth:`ConversationStopReason.as_str`.
- ``impl From<FinishReason> for StopReason`` ->
  :func:`from_finish_reason` (``ToolCalls | FunctionCall`` -> ``ToolCalls``).
- ``struct TokenUsage { prompt_tokens, completion_tokens, total_tokens,
  reasoning_tokens, #[serde(default)] cached_prompt_tokens }`` ->
  :class:`TokenUsage` frozen+slots dataclass +
  :meth:`TokenUsage.from_payload` + :meth:`TokenUsage.default`.
- ``impl From<Usage> for TokenUsage`` -> :func:`from_usage`.

YAGNI: ``TokenUsage::record_on_span(&tracing::Span)`` (``conversation.rs``
~658) records the five counters as structured fields on a Rust
``tracing::Span``. Python has no ``tracing::Span`` equivalent -- the
platform's observability layer is the R11+ ``TelemetryEngine``, not a
per-call span port. The five counters are already reachable as plain
attributes on the dataclass; a future consumer wanting span-style recording
would build it on the platform's own observability surface, not on a port of
Rust ``tracing``. Declared YAGNI.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from minimax_code.sampler.chat_completion_leaves import (
    FinishReason,
    _parse_strict_enum,
)
from minimax_code.sampler.chat_completion_mid import ChatUsage


class ConversationStopReason(StrEnum):
    """Why the conversation turn stopped (``#[serde(rename_all="snake_case")]``).

    The conversation-layer stop reason -- distinct from the R201
    :class:`messages.StopReason` (the Anthropic Messages API peer, a
    frozen+slots ``Unknown(String)`` union that preserves arbitrary wire
    strings). This one is a strict 4-variant ``snake_case`` enum with NO
    catch-all: an unknown wire string raises (parity with
    :class:`FinishReason` / :class:`Role`). ``Stop`` -> ``"stop"``;
    ``Length`` -> ``"length"``; ``ToolCalls`` -> ``"tool_calls"``;
    ``ContentFilter`` -> ``"content_filter"``.

    Renamed from the grok ``StopReason`` to dodge the barrel collision with
    the R201 :class:`StopReason` (mirrors the ``Usage`` -> :class:`ChatUsage`
    rename). The ``Conversation`` prefix marks the conversation-layer scope."""

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"

    @classmethod
    def from_payload(cls, raw: object) -> ConversationStopReason:
        """Strict wire-string parser (no catch-all).

        An unknown wire string or a non-string raises ``ValueError`` (mirrors
        serde's enum failure on this ``#[serde(other)]``-less enum)."""
        return _parse_strict_enum(cls, raw, "conversation stop reason")

    def as_str(self) -> str:
        """The ``snake_case`` wire string (parity with the grok ``as_str`` method).

        ``str(member)`` already returns the value for a :class:`StrEnum`; this
        method exists to mirror the grok surface verbatim."""
        return str(self)


def from_finish_reason(fr: FinishReason) -> ConversationStopReason:
    """Project a :class:`FinishReason` to a :class:`ConversationStopReason`.

    Mirrors ``impl From<FinishReason> for StopReason``: 1:1 for ``Stop`` /
    ``Length`` / ``ContentFilter``; both ``ToolCalls`` and ``FunctionCall``
    collapse to ``ToolCalls`` (the legacy function-call finish is observed as a
    tool-call stop on the conversation side). Exhaustive over the current
    5-variant :class:`FinishReason` (the first three explicit, the
    ``ToolCalls`` / ``FunctionCall`` pair falls through to ``ToolCalls``)."""
    if fr is FinishReason.STOP:
        return ConversationStopReason.STOP
    if fr is FinishReason.LENGTH:
        return ConversationStopReason.LENGTH
    if fr is FinishReason.CONTENT_FILTER:
        return ConversationStopReason.CONTENT_FILTER
    # FinishReason.TOOL_CALLS and FinishReason.FUNCTION_CALL both collapse to
    # TOOL_CALLS (a function-call finish is observed as a tool-call stop on
    # the conversation side).
    return ConversationStopReason.TOOL_CALLS


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Conversation-side token usage (flat 5x ``u32`` projection).

    ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` are the bare
    counters; ``reasoning_tokens`` / ``cached_prompt_tokens`` are the two
    salient breakdown fields the conversation layer keeps (the rest of the
    ``*_details`` breakdowns -- audio / prediction tokens -- are dropped at
    this projection). ``cached_prompt_tokens`` carries ``#[serde(default)]``
    in grok; extended uniformly, every field defaults to 0 (mirrors grok's
    ``#[derive(Default)]``).

    Distinct from :class:`ChatUsage` (the wire shape with nested breakdowns +
    the xAI cost extension); build one with :func:`from_usage` rather than
    copying fields by hand."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_prompt_tokens: int = 0

    @classmethod
    def default(cls) -> TokenUsage:
        """Mirror ``#[derive(Default)]``: every counter 0."""
        return cls()

    @classmethod
    def from_payload(cls, payload: Any) -> TokenUsage:
        """Tolerant constructor: each counter defaults to 0 when the payload is
        missing / null / not a dict / lacks the key.

        Mirrors grok's ``#[serde(default)]`` on ``cached_prompt_tokens``,
        extended uniformly -- every field is optional at the wire edge.
        Tolerance parity with :meth:`ChatUsage.from_payload` (a non-dict
        payload returns the all-zero default)."""
        if not isinstance(payload, dict):
            return cls()
        return cls(
            prompt_tokens=payload.get("prompt_tokens", 0),
            completion_tokens=payload.get("completion_tokens", 0),
            total_tokens=payload.get("total_tokens", 0),
            reasoning_tokens=payload.get("reasoning_tokens", 0),
            cached_prompt_tokens=payload.get("cached_prompt_tokens", 0),
        )


def from_usage(usage: ChatUsage) -> TokenUsage:
    """Project a :class:`ChatUsage` to a :class:`TokenUsage`.

    Mirrors ``impl From<Usage> for TokenUsage``: ``prompt_tokens`` /
    ``completion_tokens`` / ``total_tokens`` pass straight through;
    ``cached_prompt_tokens`` is pulled from
    ``prompt_tokens_details.cached_tokens`` (0 when the breakdown is absent);
    ``reasoning_tokens`` from ``completion_tokens_details.reasoning_tokens``
    (0 when absent). The ``cost_in_usd_ticks`` extension and the non-salient
    breakdown fields (audio / prediction tokens) are dropped at this
    projection."""
    cached_prompt_tokens = (
        usage.prompt_tokens_details.cached_tokens
        if usage.prompt_tokens_details is not None
        else 0
    )
    reasoning_tokens = (
        usage.completion_tokens_details.reasoning_tokens
        if usage.completion_tokens_details is not None
        else 0
    )
    return TokenUsage(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_prompt_tokens=cached_prompt_tokens,
    )


__all__ = [
    "ConversationStopReason",
    "TokenUsage",
    "from_finish_reason",
    "from_usage",
]
