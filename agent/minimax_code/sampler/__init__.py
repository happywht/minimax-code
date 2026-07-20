"""``xai-grok-sampler`` platform package (R195+, crate migration in progress).

Fuses grok's ``xai-grok-sampler`` crate (actor-based sampling/inference layer:
HTTP streaming + retry, no shell coupling). The crate ships 12 modules; this
package is being filled leaf-by-leaf. Currently landed: ``config`` (R195,
pure-type subset) + ``retry`` (R198, backoff/max-retries pure-logic subset).
See :mod:`minimax_code.sampler.config` and :mod:`minimax_code.sampler.retry`
for each leaf's migration map + YAGNI ledger.

Leaf order (crate ``lib.rs`` re-exports, in migration order):

1. ``config`` (R195) -- :class:`OriginClientInfo` + :class:`AuthScheme` (pure
   types). Closes the R193 ``OriginClientInfo`` source-of-truth commitment.
   Remaining ``config.rs`` symbols (:class:`SamplerConfig` / :class:`RetryPolicy`
   / 2 traits) are deferred or YAGNI -- see ``config.py`` docstring.
2. ``retry`` (R198) -- :data:`DEFAULT_MAX_RETRIES` / :data:`RATE_LIMIT_RETRY_THRESHOLD`
   + :func:`resolve_max_retries_with_env` + the backoff numerical core
   (:func:`backoff_base_ms` / :func:`retry_backoff_with_jitter` /
   :func:`doom_loop_backoff`). Remaining ``retry.rs`` symbols
   (:class:`RetryDecision` + ``classify_error`` + ``format_sampling_error`` +
   ``clone_error``) depend on the unmigrated ``SamplingError`` (from
   ``xai_grok_sampling_types``) -- deferred until that crate lands.
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
    backoff_base_ms,
    doom_loop_backoff,
    resolve_max_retries,
    resolve_max_retries_with_env,
    retry_backoff_with_jitter,
)

__all__ = [
    "AuthScheme",
    "BACKOFF_BASE_MS",
    "BACKOFF_CAP_MS",
    "DEFAULT_AUTH_SCHEME",
    "DEFAULT_MAX_RETRIES",
    "DOOM_LOOP_BOUND_MS",
    "OriginClientInfo",
    "RATE_LIMIT_RETRY_THRESHOLD",
    "backoff_base_ms",
    "doom_loop_backoff",
    "resolve_max_retries",
    "resolve_max_retries_with_env",
    "retry_backoff_with_jitter",
]
