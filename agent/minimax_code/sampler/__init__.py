"""``xai-grok-sampler`` platform package (R195+, crate migration in progress).

Fuses grok's ``xai-grok-sampler`` crate (actor-based sampling/inference layer:
HTTP streaming + retry, no shell coupling). The crate ships 12 modules; this
package is being filled leaf-by-leaf. Currently landed: ``config`` (R195,
pure-type subset) + ``retry`` (R198 backoff subset + R199 decision layer) +
``types`` (R199, ``xai-grok-sampling-types`` ``error.rs``). See each leaf
module's docstring for its migration map + YAGNI ledger.

Leaf order (crate ``lib.rs`` re-exports, in migration order):

1. ``config`` (R195) -- :class:`OriginClientInfo` + :class:`AuthScheme` (pure
   types). Closes the R193 ``OriginClientInfo`` source-of-truth commitment.
   Remaining ``config.rs`` symbols (:class:`SamplerConfig` / :class:`RetryPolicy`
   / 2 traits) are deferred or YAGNI -- see ``config.py`` docstring.
2. ``retry`` (R198+R199) -- :data:`DEFAULT_MAX_RETRIES` /
   :data:`RATE_LIMIT_RETRY_THRESHOLD` + :func:`resolve_max_retries_with_env` +
   the backoff numerical core (:func:`backoff_base_ms` /
   :func:`retry_backoff_with_jitter` / :func:`doom_loop_backoff`) [R198]; plus
   the decision layer (:class:`RetryDecision` + 6 variants +
   :func:`classify_error` + :func:`format_sampling_error` + :func:`clone_error`)
   [R199] consuming the migrated :class:`SamplingError`.
3. ``types`` (R199) -- :class:`SamplingError` discriminated union (12 variants,
   ``Http``/``Serialization`` purified to ``str``) + :class:`EmptyReason` +
   :class:`EmptyResponseContext` + :class:`ResponseModelMetadata` + the
   :data:`SERIALIZATION_DISPLAY_PREFIX` constant + :func:`is_context_length_error`
   free function, from ``xai-grok-sampling-types`` ``error.rs`` (no-I/O leaf).
"""

from minimax_code.sampler.config import (
    DEFAULT_AUTH_SCHEME,
    AuthScheme,
    OriginClientInfo,
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
    "DEFAULT_AUTH_SCHEME",
    "DEFAULT_MAX_RETRIES",
    "DOOM_LOOP_BOUND_MS",
    "EmitToSession",
    "EmptyReason",
    "EmptyResponseContext",
    "Fatal",
    "OriginClientInfo",
    "RATE_LIMIT_RETRY_THRESHOLD",
    "ResponseModelMetadata",
    "Retry",
    "RetryDecision",
    "RetryWithBackoff",
    "RetryWithClientRebuild",
    "RetryWithImageStrip",
    "SERIALIZATION_DISPLAY_PREFIX",
    "SamplingError",
    "backoff_base_ms",
    "classify_error",
    "clone_error",
    "doom_loop_backoff",
    "format_sampling_error",
    "is_context_length_error",
    "resolve_max_retries",
    "resolve_max_retries_with_env",
    "retry_backoff_with_jitter",
]
