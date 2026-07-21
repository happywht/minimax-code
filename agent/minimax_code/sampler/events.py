"""Sampling event stream -- fusion of grok's ``xai-grok-sampler`` ``events.rs``
(R235, whole-leaf migration, ``xai-grok-sampler`` deepening round 3).

``events.rs`` is the sampler's in-program event log: the typed stream the
sampling loop emits into a channel so observers (metrics, telemetry, the UI
timeline) can react without coupling to the loop's internals. It owns four
public entities:

1. :class:`SamplingChannel` -- the 2-variant ``Text`` / ``Reasoning`` split
   that lets a consumer account visible content vs. private reasoning
   separately without re-parsing the stream.
2. :class:`SamplingEvent` -- the 10-variant tagged union emitted into the
   channel (``StreamStarted`` / ``FirstToken`` / ``ChannelToken`` /
   ``EventToolCallDelta`` / ``Completed`` / ``Retrying`` / ``Failed`` /
   ``ModelMetadata`` / ``BackendToolCallStarted`` / ``BackendToolCallCompleted``).
   ``#[derive(Debug, Clone)]`` only -- NO serde, so this is a pure in-program
   stream (never crosses the wire as-is).
3. :class:`SamplingErrorInfo` -- the structured wire error payload carried
   inside a ``Failed`` event. ``#[derive(Serialize, Deserialize)]`` -- this IS
   a wire struct, unlike the event union.
4. :class:`SamplingErrorKind` + :func:`from_sampling_error` -- the taxonomy tag
   + the ``From<&SamplingError> for SamplingErrorInfo`` projection that lifts a
   retry-layer :class:`SamplingError` into a :class:`SamplingErrorInfo`.

Dependency closure
------------------

R234 (``metrics.rs``) lifted the deferred blocker this leaf carried:
``events.rs``'s ``metrics: InferenceLatencyStats`` field (R233 had flagged it
YAGNI-pending). With :class:`~minimax_code.sampler.metrics.InferenceLatencyStats`
landed, the remaining closure is:

* :class:`~minimax_code.sampler.types.RequestId` -- the request-ID newtype
  (``types.rs``), added to :mod:`minimax_code.sampler.types` this round.
* :class:`~minimax_code.sampler.types.ResponseModelMetadata` /
  :class:`~minimax_code.sampler.types.EmptyResponseContext` /
  :class:`~minimax_code.sampler.types.SamplingError` (R199) -- already present.

Two gaps are held open by YAGNI (see "YAGNI / deferred" below): the
``ConversationResponse`` box (``Completed.response``) and the chat-completion
``ToolCallDelta`` barrel collision.

Migration map (grok -> Python)
------------------------------

* ``enum SamplingChannel { Text, Reasoning }`` -> :class:`SamplingChannel`
  ``StrEnum`` (wire PascalCase -- grok derives ``Serialize`` / ``Deserialize``
  without ``rename_all``, so the variant name IS the wire string).
* ``enum SamplingEvent { ... }`` -> :class:`SamplingEvent` frozen+slots union
  base + 10 variant subclasses (``isinstance`` dispatch -- the same pattern
  R199 :class:`SamplingError` and R217 :class:`DanglingToolCallReason` use).
* ``struct SamplingErrorInfo { ... }`` -> :class:`SamplingErrorInfo` frozen
  dataclass; the three ``#[serde(default, skip_serializing_if)]`` fields keep
  their ``None`` default.
* ``enum SamplingErrorKind { ... }`` -> :class:`SamplingErrorKind` ``StrEnum``
  (wire PascalCase) + :meth:`as_str` returning the lowercase telemetry label.
* ``impl From<&SamplingError> for SamplingErrorInfo`` ->
  :func:`from_sampling_error`.

YAGNI / deferred (this round)
-----------------------------

* **``ConversationResponse`` box** -- grok ``Completed { response:
  Box<ConversationResponse>, ... }``. ``ConversationResponse`` is the
  9481-line conversation-representation mega-module's core union (its
  ``StopReason`` / ``TokenUsage`` sub-types are themselves still deferred --
  see R217 / R219). ``events.rs`` never destructures the box (it only forwards
  it), so :class:`Completed.response` holds it as opaque ``Any``. One call-site
  type swap closes this when ``ConversationResponse`` lands.
* **``ToolCallDelta`` barrel collision** -- grok ``SamplingEvent::ToolCallDelta``
  is renamed :class:`EventToolCallDelta`: the package barrel already re-exports
  a chat-completion ``ToolCallDelta`` wire struct (``chat_completion_*``), and
  the two collide at the package surface. A lighter touch than R217's
  ``StopReason`` *deferral*: the two types do not overlap functionally (one is
  an in-program event variant, the other a wire delta struct), only in name,
  so a rename unblocks both without deferring either. The grok name is
  preserved in the variant's docstring.
* **``SamplingError`` Display** -- grok fills ``SamplingErrorInfo.message``
  from ``err.to_string()`` (the ``thiserror::Error`` ``#[error(...)]``
  templates). Python's R199 :mod:`minimax_code.sampler.types` purifies the
  I/O-wrapping variants but does not (yet) carry a ``Display`` equivalent for
  every variant, so the templates are rebuilt at the consumption site in
  :func:`_render_sampling_error_message`. When ``types`` grows a full
  ``__str__``, the helper collapses to ``str(err)``.
* **``serde_json::Value`` result** -- grok ``BackendToolCallCompleted { result:
  Option<serde_json::Value> }``. A JSON value is a native Python ``dict`` /
  ``list`` / scalar / ``None``, so :class:`BackendToolCallCompleted.result`
  holds it as ``Any`` (no wrapper type needed).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from minimax_code.sampler.metrics import InferenceLatencyStats
from minimax_code.sampler.types import (
    SERIALIZATION_DISPLAY_PREFIX,
    Api,
    Auth,
    DoomLoopDetected,
    EmptyResponse,
    EmptyResponseContext,
    EventStreamError,
    Http,
    IdleTimeout,
    InvalidConfiguration,
    MaxTokensTruncation,
    RequestId,
    ResponseModelMetadata,
    SamplingError,
    Serialization,
    StreamError,
)


class SamplingChannel(StrEnum):
    """The channel a streaming token belongs to (grok ``SamplingChannel``).

    Wire values are PascalCase: grok derives ``Serialize`` / ``Deserialize``
    without ``rename_all``, so the variant name IS the wire string (``"Text"``
    / ``"Reasoning"``). The two channels let a consumer split a mixed response
    stream (visible content vs. private reasoning) for separate accounting
    without re-parsing.
    """

    TEXT = "Text"
    REASONING = "Reasoning"


class SamplingErrorKind(StrEnum):
    """Taxonomy tag for a :class:`SamplingErrorInfo` (grok ``SamplingErrorKind``).

    Two string forms coexist (mirrors grok):

    * **Wire form** (this enum's ``value``): PascalCase, what serde serializes
      (``"Auth"``, ``"RateLimited"``, ...). Grok derives ``Serialize`` /
      ``Deserialize`` without ``rename_all``, so the variant name IS the wire
      string.
    * **Telemetry form** (:meth:`as_str`): lowercase snake_case, the stable
      label emitted to metrics / analytics columns / signal histograms
      (``"auth"``, ``"rate_limited"``, ...). Mirrors the shell's
      ``stream_conversation_with_retries`` error classifier so tags stay
      consistent across surfaces.

    The split exists because telemetry labels are a frozen public surface (a
    dashboard query depends on ``"rate_limited"`` forever), while the wire
    form follows serde's PascalCase convention.
    """

    AUTH = "Auth"
    HTTP = "Http"
    API = "Api"
    SERIALIZATION = "Serialization"
    IDLE_TIMEOUT = "IdleTimeout"
    RATE_LIMITED = "RateLimited"
    EMPTY_RESPONSE = "EmptyResponse"
    MAX_TOKENS_TRUNCATION = "MaxTokensTruncation"
    DOOM_LOOP_DETECTED = "DoomLoopDetected"

    def as_str(self) -> str:
        """The lowercase telemetry label (grok ``SamplingErrorKind::as_str``).

        Distinct from the wire ``value`` (PascalCase) -- see class docstring."""
        return _SAMPLING_ERROR_KIND_TELEMETRY[self]


#: Lowercase telemetry labels keyed by wire-form kind. Lives at module scope so
#: :meth:`SamplingErrorKind.as_str` is a single dict lookup (the Python
#: equivalent of grok's exhaustive ``match self``). Values mirror grok's
#: ``impl SamplingErrorKind { fn as_str(...) }`` arm-for-arm.
_SAMPLING_ERROR_KIND_TELEMETRY: dict[SamplingErrorKind, str] = {
    SamplingErrorKind.AUTH: "auth",
    SamplingErrorKind.HTTP: "http",
    SamplingErrorKind.API: "api",
    SamplingErrorKind.SERIALIZATION: "serialization",
    SamplingErrorKind.IDLE_TIMEOUT: "idle_timeout",
    SamplingErrorKind.RATE_LIMITED: "rate_limited",
    SamplingErrorKind.EMPTY_RESPONSE: "empty_response",
    SamplingErrorKind.MAX_TOKENS_TRUNCATION: "max_tokens_truncation",
    SamplingErrorKind.DOOM_LOOP_DETECTED: "doom_loop_detected",
}


@dataclass(frozen=True, slots=True)
class SamplingErrorInfo:
    """Structured error payload surfaced via :class:`Failed` (grok
    ``SamplingErrorInfo``).

    ``#[derive(Serialize, Deserialize)]`` -- this IS a wire struct (unlike the
    in-program :class:`SamplingEvent` union, it crosses the wire inside a
    ``Failed`` event). The three ``#[serde(default, skip_serializing_if =
    "Option::is_none")]`` fields (``empty_response_context`` /
    ``doom_loop_triggers`` / ``doom_loop_aborted_at_chunk``) default to ``None``
    and round-trip identically when absent. Frozen + slots for value semantics
    + low overhead.
    """

    kind: SamplingErrorKind
    status_code: int | None
    message: str
    is_retryable: bool
    retry_after_secs: int | None
    model_metadata: ResponseModelMetadata | None
    empty_response_context: EmptyResponseContext | None = None
    doom_loop_triggers: list[str] | None = None
    doom_loop_aborted_at_chunk: int | None = None


@dataclass(frozen=True, slots=True)
class SamplingEvent:
    """Base of the ``SamplingEvent`` tagged union (grok ``SamplingEvent``).

    ``#[derive(Debug, Clone)]`` only -- NO serde, so this is a pure in-program
    event stream (the sampler emits these into a channel; they never cross the
    wire as-is). Use a concrete variant below; dispatch via ``isinstance``.
    """


@dataclass(frozen=True, slots=True)
class StreamStarted(SamplingEvent):
    """Stream lifecycle opened at a wall-clock timestamp (grok ``StreamStarted``)."""

    request_id: RequestId
    timestamp_ms: int


@dataclass(frozen=True, slots=True)
class FirstToken(SamplingEvent):
    """First content token observed (grok ``FirstToken``)."""

    request_id: RequestId


@dataclass(frozen=True, slots=True)
class ChannelToken(SamplingEvent):
    """A decoded text chunk on a channel (grok ``ChannelToken``)."""

    request_id: RequestId
    channel: SamplingChannel
    text: str
    chunk_index: int


@dataclass(frozen=True, slots=True)
class EventToolCallDelta(SamplingEvent):
    """Incremental tool-call argument fragment (grok ``ToolCallDelta``).

    Renamed ``EventToolCallDelta`` (NOT ``ToolCallDelta``): the package barrel
    already re-exports a chat-completion ``ToolCallDelta`` wire struct, and the
    two collide at the package surface. The grok variant name is preserved in
    this docstring; only the Python symbol is renamed -- a lighter touch than
    R217's ``StopReason`` deferral (the two types do not overlap functionally,
    only in name, so a rename unblocks both without deferring either).
    """

    request_id: RequestId
    tool_index: int
    id: str | None
    name: str | None
    arguments_delta: str | None


@dataclass(frozen=True, slots=True)
class Completed(SamplingEvent):
    """Stream finished with a full response (grok ``Completed``).

    ``response`` is held as opaque ``Any``: grok boxes a
    ``ConversationResponse`` (the 9481-line mega-module's core union, whose
    ``StopReason`` / ``TokenUsage`` sub-types are still deferred). ``events.rs``
    never destructures the box -- it only forwards it -- so an untyped hold
    keeps this leaf dependency-free until ``ConversationResponse`` lands (a
    single call-site type swap to close).
    """

    request_id: RequestId
    response: Any
    metrics: InferenceLatencyStats


@dataclass(frozen=True, slots=True)
class Retrying(SamplingEvent):
    """Retry loop firing (grok ``Retrying``)."""

    request_id: RequestId
    attempt: int
    max_retries: int
    kind: SamplingErrorKind
    reason: str
    doom_loop_triggers: list[str] | None
    doom_loop_aborted_at_chunk: int | None


@dataclass(frozen=True, slots=True)
class Failed(SamplingEvent):
    """All retries exhausted (grok ``Failed``)."""

    request_id: RequestId
    error: SamplingErrorInfo


@dataclass(frozen=True, slots=True)
class ModelMetadata(SamplingEvent):
    """Backend model metadata surfaced mid-stream (grok ``ModelMetadata``)."""

    request_id: RequestId
    metadata: ResponseModelMetadata


@dataclass(frozen=True, slots=True)
class BackendToolCallStarted(SamplingEvent):
    """A backend / hosted tool call began (grok ``BackendToolCallStarted``)."""

    request_id: RequestId
    call_id: str
    name: str


@dataclass(frozen=True, slots=True)
class BackendToolCallCompleted(SamplingEvent):
    """A backend / hosted tool call finished (grok ``BackendToolCallCompleted``).

    ``result`` is held as opaque ``Any`` (grok ``Option<serde_json::Value>``):
    a JSON value is a native Python ``dict`` / ``list`` / scalar / ``None``, so
    no wrapper type is needed.
    """

    request_id: RequestId
    call_id: str
    name: str
    result: Any


def _render_sampling_error_message(err: SamplingError) -> str:
    """Render the human-readable error message (grok ``SamplingError::Display``).

    Grok's ``From<&SamplingError> for SamplingErrorInfo`` fills ``message`` from
    ``err.to_string()`` -- the ``thiserror::Error`` ``#[error(...)]`` templates.
    Python's R199 :mod:`minimax_code.sampler.types` purifies the I/O-wrapping
    variants but does not (yet) carry a ``Display`` equivalent for every
    variant, so the templates are rebuilt here at the consumption site. Each
    branch mirrors the grok ``#[error(...)]`` format string verbatim; when
    ``types`` grows a full ``__str__`` this collapses to ``str(err)``.
    """
    if isinstance(err, Auth):
        return err.message
    if isinstance(err, InvalidConfiguration):
        return f"invalid client configuration: {err.message}"
    if isinstance(err, Http):
        return f"request error: {err.message}"
    if isinstance(err, Serialization):
        return f"{SERIALIZATION_DISPLAY_PREFIX}{err.message}"
    if isinstance(err, Api):
        return f"API error (status {err.status}): {err.message}"
    if isinstance(err, EventStreamError):
        return f"reqwest error stream: {err.message}"
    if isinstance(err, StreamError):
        return f"stream error ({err.error_type}): {err.message}"
    if isinstance(err, IdleTimeout):
        return f"inference idle timeout after {err.elapsed_secs}s with no chunks"
    if isinstance(err, EmptyResponse):
        # EmptyReason is a StrEnum; str() yields its snake_case value, matching
        # grok's EmptyReason Display (as_str).
        return f"empty response from model ({err.context.reason})"
    if isinstance(err, MaxTokensTruncation):
        return "response truncated by max_tokens"
    # DoomLoopDetected -- the union is closed (11 variants), so this is the
    # only remaining branch. triggers is tuple[str, ...]; join mirrors grok's
    # `triggers.join(", ")`.
    return f"doom loop detected: {', '.join(err.triggers)}"


def from_sampling_error(err: SamplingError) -> SamplingErrorInfo:
    """Build a :class:`SamplingErrorInfo` from a :class:`SamplingError` (grok
    ``From<&SamplingError> for SamplingErrorInfo``).

    The ``message`` field is rebuilt from the grok ``Display`` templates (see
    :func:`_render_sampling_error_message`); every other field maps directly
    from the variant. ``Api`` maps to :attr:`SamplingErrorKind.RATE_LIMITED`
    when the status is 429 (``err.is_rate_limited()``), else stays
    :attr:`SamplingErrorKind.API`. ``empty_response_context`` is populated only
    for :class:`EmptyResponse`; ``doom_loop_triggers`` /
    ``doom_loop_aborted_at_chunk`` only for :class:`DoomLoopDetected` (mirrors
    grok's three separate ``match`` blocks).
    """
    is_retryable = err.is_retryable()
    message = _render_sampling_error_message(err)

    # kind + status_code + retry_after_secs + model_metadata (grok match #1).
    if isinstance(err, Auth):
        kind = SamplingErrorKind.AUTH
        status_code = None
        retry_after_secs = None
        model_metadata = None
    elif isinstance(err, InvalidConfiguration):
        kind = SamplingErrorKind.API
        status_code = None
        retry_after_secs = None
        model_metadata = None
    elif isinstance(err, Http):
        kind = SamplingErrorKind.HTTP
        status_code = None
        retry_after_secs = None
        model_metadata = None
    elif isinstance(err, Serialization):
        kind = SamplingErrorKind.SERIALIZATION
        status_code = None
        retry_after_secs = None
        model_metadata = None
    elif isinstance(err, Api):
        kind = (
            SamplingErrorKind.RATE_LIMITED
            if err.is_rate_limited()
            else SamplingErrorKind.API
        )
        status_code = err.status
        retry_after_secs = err.retry_after_secs
        model_metadata = err.model_metadata
    elif isinstance(err, EventStreamError):
        kind = SamplingErrorKind.HTTP
        status_code = None
        retry_after_secs = None
        model_metadata = None
    elif isinstance(err, StreamError):
        kind = SamplingErrorKind.API
        status_code = None
        retry_after_secs = None
        model_metadata = None
    elif isinstance(err, IdleTimeout):
        kind = SamplingErrorKind.IDLE_TIMEOUT
        status_code = None
        retry_after_secs = None
        model_metadata = None
    elif isinstance(err, EmptyResponse):
        kind = SamplingErrorKind.EMPTY_RESPONSE
        status_code = None
        retry_after_secs = None
        model_metadata = None
    elif isinstance(err, MaxTokensTruncation):
        kind = SamplingErrorKind.MAX_TOKENS_TRUNCATION
        status_code = None
        retry_after_secs = None
        model_metadata = None
    else:  # DoomLoopDetected (the union is closed: 11 variants).
        kind = SamplingErrorKind.DOOM_LOOP_DETECTED
        status_code = None
        retry_after_secs = None
        model_metadata = None

    # empty_response_context (grok match #2).
    empty_response_context = err.context if isinstance(err, EmptyResponse) else None

    # doom_loop_triggers + doom_loop_aborted_at_chunk (grok match #3). grok's
    # `triggers.clone()` -> list(tuple) copy; `*aborted_at_chunk` -> the Option
    # value carried as-is (int | None).
    if isinstance(err, DoomLoopDetected):
        doom_loop_triggers: list[str] | None = list(err.triggers)
        doom_loop_aborted_at_chunk: int | None = err.aborted_at_chunk
    else:
        doom_loop_triggers = None
        doom_loop_aborted_at_chunk = None

    return SamplingErrorInfo(
        kind=kind,
        status_code=status_code,
        message=message,
        is_retryable=is_retryable,
        retry_after_secs=retry_after_secs,
        model_metadata=model_metadata,
        empty_response_context=empty_response_context,
        doom_loop_triggers=doom_loop_triggers,
        doom_loop_aborted_at_chunk=doom_loop_aborted_at_chunk,
    )


__all__ = [
    "BackendToolCallCompleted",
    "BackendToolCallStarted",
    "ChannelToken",
    "Completed",
    "EventToolCallDelta",
    "Failed",
    "FirstToken",
    "ModelMetadata",
    "Retrying",
    "SamplingChannel",
    "SamplingErrorInfo",
    "SamplingErrorKind",
    "SamplingEvent",
    "StreamStarted",
    "from_sampling_error",
]
