"""``xai-grok-sampler`` platform package (R195+, crate migration in progress).

Fuses grok's ``xai-grok-sampler`` crate (actor-based sampling/inference layer:
HTTP streaming + retry, no shell coupling). The crate ships 12 modules; this
package is being filled leaf-by-leaf. Currently landed: ``config`` (pure-type
subset). See :mod:`minimax_code.sampler.config` for the migration map + YAGNI
ledger.

Leaf order (crate ``lib.rs`` re-exports, in migration order):

1. ``config`` (R195) -- :class:`OriginClientInfo` + :class:`AuthScheme` (pure
   types). Closes the R193 ``OriginClientInfo`` source-of-truth commitment.
   Remaining ``config.rs`` symbols (:class:`SamplerConfig` / :class:`RetryPolicy`
   / 2 traits) are deferred or YAGNI -- see ``config.py`` docstring.
"""

from minimax_code.sampler.config import (
    DEFAULT_AUTH_SCHEME,
    AuthScheme,
    OriginClientInfo,
)

__all__ = [
    "AuthScheme",
    "DEFAULT_AUTH_SCHEME",
    "OriginClientInfo",
]
