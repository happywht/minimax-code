"""``xai-grok-sampler`` platform package (R195+, crate migration in progress).

Fuses grok's ``xai-grok-sampler`` crate (actor-based sampling/inference layer:
HTTP streaming + retry, no shell coupling). The crate ships 12 modules; this
package is being filled leaf-by-leaf. Currently landed: ``config`` (R195,
pure-type subset) + ``doom_loop`` (R200, ``xai-grok-sampling-types``
``doom_loop.rs`` wire contract + tolerant parsers) + ``messages`` (R201,
``xai-grok-sampling-types`` ``messages.rs`` stop-reason + usage + delta-body
cluster) + ``retry`` (R198 backoff subset + R199 decision layer) + ``types``
(R199, ``xai-grok-sampling-types`` ``error.rs``). See each leaf module's
docstring for its migration map + YAGNI ledger.

Leaf order (crate ``lib.rs`` re-exports, in migration order):

1. ``config`` (R195) -- :class:`OriginClientInfo` + :class:`AuthScheme` (pure
   types). Closes the R193 ``OriginClientInfo`` source-of-truth commitment.
   Remaining ``config.rs`` symbols (:class:`SamplerConfig` / :class:`RetryPolicy`
   / 2 traits) are deferred or YAGNI -- see ``config.py`` docstring.
2. ``doom_loop`` (R200) -- server-side doom-loop wire contract + tolerant
   parsers, from ``xai-grok-sampling-types`` ``doom_loop.rs`` (no-I/O leaf).
   5 wire constants + byte-exact fixtures, the 3-variant
   :class:`DoomLoopSignalKind` + :class:`DoomLoopPeek` discriminated unions,
   :class:`DoomLoopSignal` parse/tightest, :class:`DoomLoopRecoveryPolicy`
   clamp/is_confident/confident_triggers/from_payload, and the
   :func:`peek_doom_loop` + :func:`is_check_event` free functions. Parsed
   ``raw`` labels feed the R199 :class:`SamplingError` ``DoomLoopDetected``
   variant.
3. ``retry`` (R198+R199) -- :data:`DEFAULT_MAX_RETRIES` /
   :data:`RATE_LIMIT_RETRY_THRESHOLD` + :func:`resolve_max_retries_with_env` +
   the backoff numerical core (:func:`backoff_base_ms` /
   :func:`retry_backoff_with_jitter` / :func:`doom_loop_backoff`) [R198]; plus
   the decision layer (:class:`RetryDecision` + 6 variants +
   :func:`classify_error` + :func:`format_sampling_error` + :func:`clone_error`)
   [R199] consuming the migrated :class:`SamplingError`.
4. ``types`` (R199) -- :class:`SamplingError` discriminated union (12 variants,
   ``Http``/``Serialization`` purified to ``str``) + :class:`EmptyReason` +
   :class:`EmptyResponseContext` + :class:`ResponseModelMetadata` + the
   :data:`SERIALIZATION_DISPLAY_PREFIX` constant + :func:`is_context_length_error`
   free function, from ``xai-grok-sampling-types`` ``error.rs`` (no-I/O leaf).
5. ``messages`` (R201) -- Anthropic Messages API (``/v1/messages``) stop-reason
   + usage + delta-body cluster, from ``xai-grok-sampling-types``
   ``messages.rs`` (no-I/O leaf). The tolerant :class:`StopReason` snake_case
   enum + :class:`UnknownStopReason` catch-all (+:func:`parse_stop_reason` /
   :func:`stop_reason_to_wire` faithful round-trip) + :class:`MessagesUsage` /
   :class:`MessageDeltaUsage` token counters + :class:`StopDetails` /
   :class:`MessageDeltaBody` terminal body + :class:`StreamError`. The request
   types (``MessagesRequest`` + ``ContentBlock`` union + the full
   ``MessageStreamEvent`` wrapper) land in later rounds -- they pull in the
   larger ``ContentBlock`` discriminated union.
"""

from minimax_code.sampler.config import (
    DEFAULT_AUTH_SCHEME,
    AuthScheme,
    OriginClientInfo,
)
from minimax_code.sampler.doom_loop import (
    DOOM_LOOP_CHECK_EVENT_TYPE,
    DOOM_LOOP_CHECK_HEADER,
    SAMPLE_CHECK_EVENT_DATA,
    SAMPLE_CHECK_EVENT_DATA_CUMULATIVE,
    THINKING_CHANNEL,
    CheckEvent,
    DoomLoopPeek,
    DoomLoopRecoveryPolicy,
    DoomLoopSignal,
    DoomLoopSignalKind,
    LowLogprob,
    NoDoomLoop,
    ResponseField,
    TailRepetition,
    Unknown,
    is_check_event,
    peek_doom_loop,
)
from minimax_code.sampler.messages import (
    EndTurn,
    MaxTokens,
    MessageDeltaBody,
    MessageDeltaUsage,
    MessagesUsage,
    ModelContextWindowExceeded,
    PauseTurn,
    Refusal,
    StopDetails,
    StopReason,
    StopSequence,
    StreamError,
    ToolUse,
    UnknownStopReason,
    parse_stop_reason,
    stop_reason_to_wire,
)
from minimax_code.sampler.retry import (
    BACKOFF_BASE_MS,
    BACKOFF_CAP_MS,
    DEFAULT_MAX_RETRIES,
    DOOM_LOOP_BOUND_MS,
    RATE_LIMIT_RETRY_THRESHOLD,
    EmitToSession,
    Fatal,
    Retry,
    RetryDecision,
    RetryWithBackoff,
    RetryWithClientRebuild,
    RetryWithImageStrip,
    backoff_base_ms,
    classify_error,
    clone_error,
    doom_loop_backoff,
    format_sampling_error,
    resolve_max_retries,
    resolve_max_retries_with_env,
    retry_backoff_with_jitter,
)
from minimax_code.sampler.types import (
    SERIALIZATION_DISPLAY_PREFIX,
    EmptyReason,
    EmptyResponseContext,
    ResponseModelMetadata,
    SamplingError,
    is_context_length_error,
)

__all__ = [
    "AuthScheme",
    "BACKOFF_BASE_MS",
    "BACKOFF_CAP_MS",
    "CheckEvent",
    "DEFAULT_AUTH_SCHEME",
    "DEFAULT_MAX_RETRIES",
    "DOOM_LOOP_BOUND_MS",
    "DOOM_LOOP_CHECK_EVENT_TYPE",
    "DOOM_LOOP_CHECK_HEADER",
    "DoomLoopPeek",
    "DoomLoopRecoveryPolicy",
    "DoomLoopSignal",
    "DoomLoopSignalKind",
    "EmitToSession",
    "EmptyReason",
    "EmptyResponseContext",
    "EndTurn",
    "Fatal",
    "LowLogprob",
    "MaxTokens",
    "MessageDeltaBody",
    "MessageDeltaUsage",
    "MessagesUsage",
    "ModelContextWindowExceeded",
    "NoDoomLoop",
    "OriginClientInfo",
    "PauseTurn",
    "RATE_LIMIT_RETRY_THRESHOLD",
    "Refusal",
    "ResponseField",
    "ResponseModelMetadata",
    "Retry",
    "RetryDecision",
    "RetryWithBackoff",
    "RetryWithClientRebuild",
    "RetryWithImageStrip",
    "SAMPLE_CHECK_EVENT_DATA",
    "SAMPLE_CHECK_EVENT_DATA_CUMULATIVE",
    "SERIALIZATION_DISPLAY_PREFIX",
    "SamplingError",
    "StopDetails",
    "StopReason",
    "StopSequence",
    "StreamError",
    "THINKING_CHANNEL",
    "TailRepetition",
    "ToolUse",
    "Unknown",
    "UnknownStopReason",
    "backoff_base_ms",
    "classify_error",
    "clone_error",
    "doom_loop_backoff",
    "format_sampling_error",
    "is_check_event",
    "is_context_length_error",
    "parse_stop_reason",
    "peek_doom_loop",
    "resolve_max_retries",
    "resolve_max_retries_with_env",
    "retry_backoff_with_jitter",
    "stop_reason_to_wire",
]
