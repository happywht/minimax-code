"""Retry backoff + max-retries resolution -- fusion of grok's
``xai-grok-sampler`` retry core (R198, ``retry.rs`` pure-logic subset).

``xai-grok-sampler`` is the actor-based sampling/inference layer; ``retry.rs``
is its self-marked pure-logic leaf (``//! Pure logic only: no I/O, no
notifications, no logging side-effects. The actor (M4) wraps this with the
actual retry loop.``). It owns three concerns: (1) resolving the per-call
retry budget from env/model/default, (2) the backoff schedule (exponential
with ±20 % jitter for ordinary retries, near-instant for doom-loop retries),
and (3) classifying a :class:`SamplingError` into a retry decision. This
module migrates (1) and (2); (3) is deferred -- see the YAGNI ledger below.

Migrated (this round, pure logic)
---------------------------------

* :data:`DEFAULT_MAX_RETRIES` -- grok ``DEFAULT_MAX_RETRIES`` (15). Retries 1-4
  back off exponentially (2 s + 4 s + 8 s + 16 s ~= 30 s); retries 5-15 each
  wait ~30 s (capped), totalling ~6 min before the actor gives up. Tuned so a
  transient Grok outage recovers within one retry budget without blocking the
  session forever.
* :data:`RATE_LIMIT_RETRY_THRESHOLD` -- grok ``RATE_LIMIT_RETRY_THRESHOLD`` (2).
  A persistent 429 retries at most twice (the third is fatal); a sustained
  rate limit is a config/capacity problem, not a transient blip.
* :data:`BACKOFF_BASE_MS` / :data:`BACKOFF_CAP_MS` -- the exponential base
  (2 s, doubled per retry) and the 30 s ceiling (grok clamps ``base_ms``).
* :data:`DOOM_LOOP_BOUND_MS` -- doom-loop backoff upper bound (grok
  ``hash % 251`` -> ``[0, 250]`` ms).
* :func:`resolve_max_retries_with_env` -- pure core of grok
  ``resolve_max_retries``: env override (parsed as a non-negative int, mirroring
  grok's ``u32``) -> model -> :data:`DEFAULT_MAX_RETRIES`.
* :func:`resolve_max_retries` -- thin I/O wrapper reading ``GROK_MAX_RETRIES``.
* :func:`backoff_base_ms` -- the pre-jitter exponential base for an upcoming
  retry (grok ``retry_backoff_with_jitter`` base): 2 s/2 s/4 s/8 s/16 s/30 s ...
* :func:`retry_backoff_with_jitter` -- backoff for an upcoming retry, ±20 %
  jitter (grok ``retry_backoff_with_jitter``): retry 1 -> [1.6, 2.4] s,
  2 -> [3.2, 4.8] s, 10 -> [24, 36] s.
* :func:`doom_loop_backoff` -- near-instant doom-loop backoff (grok
  ``doom_loop_backoff``): [0, 250] ms.

YAGNI / deferred (this round)
-----------------------------

* **SamplingError decision layer** -- grok ``RetryDecision`` (the enum:
  ``Retry`` / ``RetryWithBackoff`` / ``RetryWithImageStrip`` /
  ``RetryWithClientRebuild`` / ``EmitToSession`` / ``Fatal``, several carrying a
  :class:`SamplingError`) + ``classify_error`` (the matrix: 5xx/connection
  retryable, 429 rate-limited up to the threshold, 413/image-error strips the
  image and retries once, 400/401/403/404/408/422 + Auth/InvalidConfiguration/
  IdleTimeout/Serialization/MaxTokensTruncation fatal, ``x-should-retry: false``
  overrides to fatal) + ``format_sampling_error`` (12-variant display) +
  ``clone_error`` (SamplingError has no ``Clone`` -- Http falls back to
  EventStreamError, Serialization is preserved). All four depend on
  :class:`SamplingError` from ``xai_grok_sampling_types`` (not yet migrated);
  they land in the round that migrates that crate.
* **RetryPolicy struct** -- YAGNI per the :mod:`minimax_code.sampler.config`
  ledger: the platform already has two retry policies
  (:mod:`minimax_code.reliability.retry` + :mod:`minimax_code.resilience.retry_policy`);
  a third copy from grok is not carried. The pure *backoff arithmetic* lands
  here for any consumer that wants grok's exact schedule.
* **Global jitter entropy** -- grok derives jitter from a process-global
  ``static JITTER_SEQ: AtomicU64`` (``fetch_add`` per call) + thread id /
  retry count hashed with ``DefaultHasher``. That global mutable state is the
  caller's job: the pure functions here take ``jitter_unit`` in ``[0.0, 1.0]``
  and the actor (unmigrated, M4) supplies the entropy. This mirrors the R197
  :mod:`minimax_code.sqlite_journal` decision to inject the hostname rather
  than fetch it inside the pure core.

Product-fusion note
-------------------

The platform's existing retry policies use their own schedules; this module
does **not** replace them. It exposes grok's exact backoff arithmetic as a
pure, side-effect-free library so that (a) any future consumer that wants to
match grok's retry cadence can, and (b) the decision-layer migration in a
later round has its numerical foundation already landed and tested. No I/O,
no globals, no LLM coupling -- just integers and :class:`~datetime.timedelta`.
"""

from __future__ import annotations

import os
from datetime import timedelta

#: grok ``DEFAULT_MAX_RETRIES`` -- wait for a transient Grok outage to recover
#: without blocking the session forever (see module docstring for the budget).
DEFAULT_MAX_RETRIES: int = 15

#: grok ``RATE_LIMIT_RETRY_THRESHOLD`` -- a 429 retries at most twice; the third
#: is fatal (sustained rate limit = config/capacity problem, not a blip).
RATE_LIMIT_RETRY_THRESHOLD: int = 2

#: Exponential backoff base (grok ``retry_backoff_with_jitter``): doubled per
#: retry (2 s, 4 s, 8 s, 16 s, ...) before :data:`BACKOFF_CAP_MS` clamps it.
BACKOFF_BASE_MS: int = 2000

#: Backoff ceiling (grok clamps ``base_ms`` at 30 s).
BACKOFF_CAP_MS: int = 30_000

#: Doom-loop backoff upper bound (grok ``doom_loop_backoff``: ``hash % 251``
#: yields ``[0, 250]`` ms). Doom-loop retries fire near-instantly so a
#: self-reinforcing retry storm cannot starve the session.
DOOM_LOOP_BOUND_MS: int = 250


def resolve_max_retries_with_env(
    env_override: str | None, model_max_retries: int | None
) -> int:
    """Resolve the per-call retry budget (grok ``resolve_max_retries_with_env``).

    Pure. Precedence: ``env_override`` (parsed as a non-negative int, mirroring
    grok's ``u32`` -- a negative or non-numeric value falls through) ->
    ``model_max_retries`` -> :data:`DEFAULT_MAX_RETRIES`. The ``std::env::var``
    read is the caller's job; pass the already-read value as ``env_override``
    (``None`` = unset). An explicit ``"0"`` disables retries.
    """
    if env_override is not None:
        try:
            parsed = int(env_override)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None and parsed >= 0:
            return parsed
    if model_max_retries is not None:
        return model_max_retries
    return DEFAULT_MAX_RETRIES


def resolve_max_retries(model_max_retries: int | None) -> int:
    """Resolve the retry budget, reading ``GROK_MAX_RETRIES`` (grok wrapper).

    Thin I/O wrapper over :func:`resolve_max_retries_with_env`: reads
    ``GROK_MAX_RETRIES`` from the environment, then defers to the pure core.
    """
    return resolve_max_retries_with_env(
        os.environ.get("GROK_MAX_RETRIES"), model_max_retries
    )


def backoff_base_ms(retry_count: int) -> int:
    """Exponential backoff base for ``retry_count`` (grok
    ``retry_backoff_with_jitter`` base, pre-jitter).

    Pure. ``BACKOFF_BASE_MS << saturating_sub(retry_count, 1)`` clamped at
    :data:`BACKOFF_CAP_MS`: retry 0/1 -> 2 s, 2 -> 4 s, 3 -> 8 s, 4 -> 16 s,
    5+ -> 30 s. ``retry_count`` is the upcoming 1-indexed retry attempt (grok
    passes 1 for the first retry); 0 is accepted and treated as 1 for safety.
    The ``shift >= 4`` early return mirrors grok's ``checked_shl`` -> ``None``
    -> cap path without computing a huge intermediate.
    """
    shift = max(retry_count - 1, 0)
    if shift >= 4:  # 2000 << 4 = 32000 > cap; any larger shift stays capped
        return BACKOFF_CAP_MS
    return min(BACKOFF_BASE_MS << shift, BACKOFF_CAP_MS)


def retry_backoff_with_jitter(retry_count: int, jitter_unit: float) -> timedelta:
    """Backoff for the upcoming retry, plus or minus ~20 % jitter (grok
    ``retry_backoff_with_jitter``).

    Pure. ``jitter_unit`` in ``[0.0, 1.0]`` is the entropy source -- grok
    derives it from a process-global ``AtomicU64`` counter + thread id hashed
    with ``DefaultHasher``; that global state is the caller's job to supply.
    The result lands in ``[base - base/5, base + base/5]`` (±20 %): retry 1 ->
    [1.6, 2.4] s, 2 -> [3.2, 4.8] s, 10 -> [24, 36] s.
    """
    base = backoff_base_ms(retry_count)
    jitter_range = base // 5
    jitter = round(jitter_unit * (2 * jitter_range))
    return timedelta(milliseconds=base - jitter_range + jitter)


def doom_loop_backoff(jitter_unit: float) -> timedelta:
    """Near-instant backoff for doom-loop retries (grok ``doom_loop_backoff``).

    Pure. ``jitter_unit`` in ``[0.0, 1.0]`` is the entropy source (grok hashes
    a process-global counter + ``retry_count`` with ``DefaultHasher``; that
    state is the caller's job). Result in ``[0, DOOM_LOOP_BOUND_MS]`` ms (grok
    ``hash % 251``) -- fast enough to break a self-reinforcing retry storm
    without stalling the session.
    """
    ms = round(jitter_unit * DOOM_LOOP_BOUND_MS)
    return timedelta(milliseconds=ms)


__all__ = [
    "BACKOFF_BASE_MS",
    "BACKOFF_CAP_MS",
    "DEFAULT_MAX_RETRIES",
    "DOOM_LOOP_BOUND_MS",
    "RATE_LIMIT_RETRY_THRESHOLD",
    "backoff_base_ms",
    "doom_loop_backoff",
    "resolve_max_retries",
    "resolve_max_retries_with_env",
    "retry_backoff_with_jitter",
]
