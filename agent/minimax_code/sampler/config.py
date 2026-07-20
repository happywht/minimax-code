"""Sampler configuration types (pure) -- fusion of grok's ``xai-grok-sampler``
(R195, ``src/config.rs`` pure-type subset, crate first round).

``xai-grok-sampler`` is grok's actor-based sampling/inference layer (HTTP
streaming + retry, no shell coupling). The crate's ``config.rs`` ships six
symbols; this round migrates the two with no external dependency and records
the rest as YAGNI or deferred (cross-crate dependency chain / reqwest
coupling / platform duplication).

Migrated (this round)
---------------------

* :class:`OriginClientInfo` -- the crate's faithful source. **Closes the R193
  source-of-truth commitment**: ``grok_http`` (R193) defined this locally with
  the note "the sampler crate is not yet migrated, so this module is the
  platform's source of truth; when the sampler lands it will import from here
  (dependency direction inverted)." This module is that landing; ``grok_http``
  now imports + re-exports it from here, retiring its local definition.
* :class:`AuthScheme` -- the bearer/x-api-key auth-scheme enum (grok
  ``AuthScheme``, ``#[serde(rename_all="snake_case")]``, default ``Bearer``).
  Pure :class:`enum.StrEnum`; consumed by ``SamplerConfig.auth_scheme`` when
  that struct lands.

YAGNI / deferred (this round)
-----------------------------

* :class:`SamplerConfig` (24-field per-request config) -- deep cross-crate
  chain: ``attribution::SharedAttributionCallback`` (unmigrated), ``retry::
  {DEFAULT_MAX_RETRIES, RATE_LIMIT_RETRY_THRESHOLD}`` (``retry.rs`` 856 lines
  unmigrated), ``sampling_types::{CompactionAtTokens, CompactionsRemaining,
  DoomLoopRecoveryPolicy}`` (sub-types not all migrated in R53-R63), and
  ``reqwest::header::HeaderMap`` (HeaderInjector). Deferred until those land.
* :class:`RetryPolicy` -- **YAGNI**: the platform already has two retry
  policies (``agent/minimax_code/agent/reliability/retry.py`` +
  ``resilience/retry_policy.py``); a third sampler copy duplicates. The
  constants ``DEFAULT_MAX_RETRIES=15`` / ``RATE_LIMIT_RETRY_THRESHOLD=2``
  serve only ``RetryPolicy::default`` + ``retry.rs``'s reqwest loop -- the
  platform's R17-R21 circuit breaker + R188 retry_middleware own retry.
* ``BearerResolver`` / ``HeaderInjector`` traits + ``SharedBearerResolver`` /
  ``SharedHeaderInjector`` aliases -- ``Arc<dyn Trait>`` over ``reqwest::
  header::HeaderMap`` with no Python/httpx analogue (the bearer comes from
  ``secrets`` R15; the OTel traceparent is injected by R131 ``http_client``).

The crate's actor core (``client.rs`` 2745 + ``retry.rs`` 856 + ``actor/`` +
``stream/``) overlaps the platform's ``agent/llm.py`` + resilience stack and
is YAGNI or multi-round. See the package ``__init__`` for the leaf order.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AuthScheme(StrEnum):
    """How the sampler authenticates an outbound request (grok ``AuthScheme``).

    ``#[serde(rename_all = "snake_case")]`` -> bare lowercase labels (grok
    ``Bearer`` -> ``bearer``; ``XApiKey`` -> ``x_api_key``). grok's
    ``#[default] Bearer`` is mirrored by :data:`DEFAULT_AUTH_SCHEME` (a Python
    ``StrEnum`` has no default slot). Consumed by ``SamplerConfig.auth_scheme``
    when that struct lands.
    """

    BEARER = "bearer"
    X_API_KEY = "x_api_key"


#: The default auth scheme (grok ``#[default] Bearer`` on ``AuthScheme``).
DEFAULT_AUTH_SCHEME: AuthScheme = AuthScheme.BEARER


@dataclass(frozen=True, slots=True)
class OriginClientInfo:
    """Identity of the client that originated a request (grok ``OriginClientInfo``).

    The platform's faithful source: ``product`` is the client's product name;
    ``version`` is its version string when known. ``grok_http`` (R193) imports
    + re-exports this from here -- the R193 source-of-truth commitment is now
    closed (the local definition is retired, dependency direction inverted).
    """

    product: str
    version: str | None = None


__all__ = [
    "AuthScheme",
    "DEFAULT_AUTH_SCHEME",
    "OriginClientInfo",
]
