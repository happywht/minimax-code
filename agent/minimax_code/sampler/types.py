"""Sampling error types -- fusion of grok's ``xai-grok-sampling-types``
``error.rs`` (R199, pure-type leaf).

``xai-grok-sampling-types`` is grok's API-agnostic type crate for the sampling /
chat-completion layer; ``error.rs`` is its self-declared **no-I/O** error leaf
(crate ``lib.rs``: "no HTTP clients, no file system access"). It owns the
``SamplingError`` discriminated union (12 variants) consumed by the sampler's
retry decision layer, plus the structured ``EmptyReason`` /
``EmptyResponseContext`` / ``ResponseModelMetadata`` types the variants carry.

This module migrates the pure core: the 12-variant union (with the two I/O
wrappers -- ``Http(reqwest::Error)`` / ``Serialization(serde_json::Error)`` --
purified to carry a rendered ``str`` message), the status-code + message-text
predicates (``is_auth_error`` / ``is_rate_limited`` / ``is_payload_too_large`` /
``is_encrypted_content_error`` / ``is_image_processing_error`` /
``is_context_length_error``), the ``is_retryable`` matrix, the
``retry_after`` / ``should_retry_header`` accessors, the free-function
``is_context_length_error(message)`` matcher, and the
``serialization_message`` / ``serialization_from_rendered`` rebuilders.

Migrated (this round, pure logic)
---------------------------------

* :class:`EmptyReason` -- grok ``EmptyReason`` (``#[serde(rename_all="snake_case")]``
  2-variant enum: ``reasoning_only`` / ``no_visible_content``).
* :class:`ResponseModelMetadata` -- grok ``ResponseModelMetadata`` (3 ``Option``
  fields, ``#[derive(Default)]``).
* :class:`EmptyResponseContext` -- grok ``EmptyResponseContext`` (10 fields) +
  :meth:`EmptyResponseContext.finish_reason_str`.
* :data:`SERIALIZATION_DISPLAY_PREFIX` -- grok ``const SERIALIZATION_DISPLAY_PREFIX``.
* :class:`SamplingError` -- discriminated-union base; the 11 variant subclasses
  (:class:`Auth`, :class:`InvalidConfiguration`, :class:`Http`, :class:`Serialization`,
  :class:`Api`, :class:`EventStreamError`, :class:`StreamError`, :class:`IdleTimeout`,
  :class:`EmptyResponse`, :class:`MaxTokensTruncation`, :class:`DoomLoopDetected`).
* Predicate + accessor methods on the base (``is_auth_error`` / ... / ``is_retryable`` /
  ``retry_after`` / ``should_retry_header``).
* :func:`is_context_length_error` -- free-function message matcher (5 backend variants).
* :meth:`Serialization.serialization_message` / :meth:`Serialization.serialization_from_rendered`
  -- rebuilders (class methods on the variant).

YAGNI / deferred (this round)
-----------------------------

* **reqwest::Error introspection** -- grok ``is_likely_body_rejected`` (``Http``
  variant inspects ``is_request`` / ``is_body`` / ``is_timeout`` / ``is_connect``)
  and ``is_retryable_reqwest`` (the ``Http`` arm of ``is_retryable``). The purified
  ``Http`` carries only a rendered message, so these predicates are the caller's
  job -- the ``Http`` arm of :meth:`SamplingError.is_retryable` returns ``True``
  (transport errors are retryable on grok's main path: timeout / connect /
  request / body all return ``true``; only ``is_status && !(server_error || 429)``
  returns ``false``, and the purified variant has no status to inspect).
* **JSON error-format parsing** -- grok ``try_parse_error`` / ``parse_error_bytes`` /
  ``try_parse_stream_error`` (parse OpenAI/flat error envelopes from response
  bytes). These are pure JSON work but coupled to the HTTP response pipeline
  (not the retry decision layer); they land in a later round with the HTTP leaf.
* **``tracing`` side-effects** -- grok ``From<serde_json::Error>`` logs at ``debug``
  and ``try_parse_stream_error`` logs at ``warn``; the purified core has no
  logging side-effects (the caller logs).
* **``From`` conversions** -- grok ``From<reqwest::Error>`` /
  ``From<serde_json::Error>`` build ``Http`` / ``Serialization`` from the raw
  I/O error objects; without those objects the conversions are vacuous and
  callers construct the variants directly with a rendered message.

Purification decisions
----------------------

The two I/O-wrapping variants are the crux:

* grok ``Http(reqwest::Error)`` -> :class:`Http` with ``message: str`` (the
  rendered ``Display`` of the reqwest error). ``reqwest::Error`` is not ``Clone``
  and not available in Python; the rendered string preserves the human-readable
  description (and ``clone_error`` no longer needs grok's
  ``Http -> EventStreamError`` fallback -- the purified variant is a plain
  ``str`` and clones as-is).
* grok ``Serialization(serde_json::Error)`` -> :class:`Serialization` with
  ``message: str``. Same rationale; the line/column text lives inside the
  rendered message (grok's ``Display`` emits ``line N column M: ...``).

Product-fusion note
-------------------

This leaf unblocks the R198 retry decision layer (:mod:`minimax_code.sampler.retry`
``classify_error`` / ``format_sampling_error`` / ``clone_error``), which consume
:class:`SamplingError`. The union is a value type (not a raised exception): the
retry loop pattern-matches it into a
:class:`~minimax_code.sampler.retry.RetryDecision`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: grok ``const SERIALIZATION_DISPLAY_PREFIX`` -- shared between the
#: ``Serialization`` variant's ``#[error(...)]`` template and
#: :meth:`Serialization.serialization_from_rendered` so the strip can never
#: drift from what ``Display`` emits.
SERIALIZATION_DISPLAY_PREFIX: str = "serialization error: "


class EmptyReason(StrEnum):
    """Why the model's response classified as empty (grok ``EmptyReason``).

    Wire values mirror grok's ``#[serde(rename_all="snake_case")]`` labels.
    """

    REASONING_ONLY = "reasoning_only"
    NO_VISIBLE_CONTENT = "no_visible_content"


@dataclass(frozen=True, slots=True)
class ResponseModelMetadata:
    """Model metadata from response headers (grok ``ResponseModelMetadata``)."""

    context_window: int | None = None
    max_completion_tokens: int | None = None
    models_etag: str | None = None


@dataclass(frozen=True, slots=True)
class EmptyResponseContext:
    """Structured context captured when a response classifies as empty (grok
    ``EmptyResponseContext``)."""

    reason: EmptyReason
    had_reasoning: bool
    content_len: int
    tool_call_count: int
    finish_reason: str | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    prompt_tokens: int | None
    model: str
    first_choice_seen: bool

    def finish_reason_str(self) -> str:
        """The ``finish_reason`` or ``"none"`` when absent (grok accessor)."""
        return self.finish_reason if self.finish_reason is not None else "none"


def is_context_length_error(message: str) -> bool:
    """True when ``message`` indicates a context-window overflow (grok free fn).

    Backends report this inconsistently with no stable error code, so grok
    matches the message text; it is deterministic (re-sending the same payload
    always fails), so callers must not retry. Case-insensitive (grok
    ``to_ascii_lowercase``).
    """
    lowered = message.lower()
    return (
        "too long for this model" in lowered
        or "prompt is too long" in lowered
        or "maximum prompt length" in lowered
        or "maximum context length" in lowered
        or "context_length_exceeded" in lowered
    )


@dataclass(frozen=True, slots=True)
class SamplingError:
    """Base of the ``SamplingError`` discriminated union (grok
    ``SamplingError``).

    The 11 variant subclasses below carry the variant-specific fields. Predicate
    and accessor methods live here and dispatch on the concrete subclass
    (mirroring grok's central ``match self`` in the ``impl`` block). This is a
    value type consumed by the retry decision layer, not a raised exception.
    """

    def is_auth_error(self) -> bool:
        """grok ``is_auth_error``: ``Auth`` variant or ``Api`` with status 401.

        403 Forbidden is intentionally excluded (policy denial, not a credential
        rejection -- see grok's rationale on avoiding pointless OIDC refresh).
        """
        if isinstance(self, Auth):
            return True
        return isinstance(self, Api) and self.status == 401

    def is_rate_limited(self) -> bool:
        """grok ``is_rate_limited``: ``Api`` with status 429."""
        return isinstance(self, Api) and self.status == 429

    def is_payload_too_large(self) -> bool:
        """grok ``is_payload_too_large``: ``Api`` with status 413."""
        return isinstance(self, Api) and self.status == 413

    def is_encrypted_content_error(self) -> bool:
        """grok ``is_encrypted_content_error``: ``Api`` 400 whose message
        mentions ``encrypted_content`` (cross-model-family ciphertext)."""
        return (
            isinstance(self, Api)
            and self.status == 400
            and "encrypted_content" in self.message
        )

    def is_image_processing_error(self) -> bool:
        """grok ``is_image_processing_error``: ``Api`` 400 or 500 whose message
        contains ``Could not process image`` (direct or proxy-wrapped)."""
        return (
            isinstance(self, Api)
            and self.status in (400, 500)
            and "Could not process image" in self.message
        )

    def is_context_length_error(self) -> bool:
        """grok ``is_context_length_error`` method: delegates to the free
        function for ``Api`` / ``StreamError`` messages; ``False`` otherwise."""
        if isinstance(self, (Api, StreamError)):
            return is_context_length_error(self.message)
        return False

    def is_retryable(self) -> bool:
        """grok ``is_retryable`` matrix.

        ``Auth`` / ``InvalidConfiguration`` / ``Serialization`` / ``IdleTimeout`` /
        ``MaxTokensTruncation`` -> ``False``; ``Api`` -> status in
        ``{429, 500, 502, 503, 504, 520}``; ``Http`` -> ``True`` (purified --
        see module docstring: reqwest introspection is the caller's job);
        ``EventStreamError`` / ``StreamError`` / ``EmptyResponse`` /
        ``DoomLoopDetected`` -> ``True``.
        """
        if isinstance(
            self,
            (Auth, InvalidConfiguration, Serialization, IdleTimeout, MaxTokensTruncation),
        ):
            return False
        if isinstance(self, Http):
            return True
        if isinstance(self, Api):
            return self.status in (429, 500, 502, 503, 504, 520)
        # EventStreamError / StreamError / EmptyResponse / DoomLoopDetected.
        return True

    def retry_after(self) -> int | None:
        """grok ``retry_after``: the ``Api.retry_after_secs`` header value."""
        return self.retry_after_secs if isinstance(self, Api) else None

    def should_retry_header(self) -> bool | None:
        """grok ``should_retry_header``: the ``Api.should_retry`` header value."""
        return self.should_retry if isinstance(self, Api) else None


@dataclass(frozen=True, slots=True)
class Auth(SamplingError):
    """Credential rejection (grok ``Auth(String)``)."""

    message: str


@dataclass(frozen=True, slots=True)
class InvalidConfiguration(SamplingError):
    """Client misconfiguration (grok ``InvalidConfiguration(&'static str)``)."""

    message: str


@dataclass(frozen=True, slots=True)
class Http(SamplingError):
    """HTTP transport failure (grok ``Http(reqwest::Error)``, purified).

    Carries the rendered ``Display`` of the reqwest error; reqwest introspection
    (timeout / connect / status / url) is the caller's job -- see the module
    docstring's YAGNI ledger.
    """

    message: str


@dataclass(frozen=True, slots=True)
class Serialization(SamplingError):
    """Response parse failure (grok ``Serialization(serde_json::Error)``,
    purified).

    Carries the rendered message (grok's ``Display`` emits
    ``line N column M: ...``); ``serde_json::Error`` is not available in Python.
    """

    message: str

    def __str__(self) -> str:
        """grok ``Display``: the prefix + the message (so the
        :meth:`serialization_from_rendered` round-trip is exact)."""
        return f"{SERIALIZATION_DISPLAY_PREFIX}{self.message}"

    @classmethod
    def serialization_message(cls, message: object) -> Serialization:
        """Build a ``Serialization`` from a rendered message (grok
        ``serialization_message``).

        Stays ``Serialization`` so it remains non-retryable.
        """
        return cls(message=str(message))

    @classmethod
    def serialization_from_rendered(cls, rendered: str) -> Serialization:
        """Rebuild from a full rendered ``Display``, stripping the prefix so it
        is not emitted twice (grok ``serialization_from_rendered``)."""
        return cls(message=rendered.removeprefix(SERIALIZATION_DISPLAY_PREFIX))


@dataclass(frozen=True, slots=True)
class Api(SamplingError):
    """API error with status + message + headers (grok ``Api`` struct variant)."""

    status: int
    message: str
    model_metadata: ResponseModelMetadata | None = None
    retry_after_secs: int | None = None
    should_retry: bool | None = None


@dataclass(frozen=True, slots=True)
class EventStreamError(SamplingError):
    """reqwest event-stream failure (grok ``EventStreamError(String)``)."""

    message: str


@dataclass(frozen=True, slots=True)
class StreamError(SamplingError):
    """Server-side stream error sent as JSON in the SSE stream (grok
    ``StreamError`` struct variant)."""

    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class IdleTimeout(SamplingError):
    """Per-chunk idle timeout (grok ``IdleTimeout`` -- NOT retryable)."""

    elapsed_secs: int


@dataclass(frozen=True, slots=True)
class EmptyResponse(SamplingError):
    """Model returned no content / tool calls (grok ``EmptyResponse``)."""

    context: EmptyResponseContext


@dataclass(frozen=True, slots=True)
class MaxTokensTruncation(SamplingError):
    """Response truncated by ``max_tokens`` (grok ``MaxTokensTruncation``)."""


@dataclass(frozen=True, slots=True)
class DoomLoopDetected(SamplingError):
    """Server-reported reasoning loop (grok ``DoomLoopDetected``).

    Retryable on the recovery loop's own budget. Carries the raw trigger labels
    (never generation content) plus the stream chunk index a mid-stream abort
    fired at (``None`` when only seen on the completed response).
    """

    triggers: tuple[str, ...]
    aborted_at_chunk: int | None = None


__all__ = [
    "Api",
    "Auth",
    "DoomLoopDetected",
    "EmptyReason",
    "EmptyResponse",
    "EmptyResponseContext",
    "EventStreamError",
    "Http",
    "IdleTimeout",
    "InvalidConfiguration",
    "MaxTokensTruncation",
    "ResponseModelMetadata",
    "SERIALIZATION_DISPLAY_PREFIX",
    "SamplingError",
    "Serialization",
    "StreamError",
    "is_context_length_error",
]
