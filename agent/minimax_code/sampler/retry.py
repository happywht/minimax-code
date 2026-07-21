"""Retry classification, backoff, and decision-making -- fusion of grok's
``xai-grok-sampler`` ``retry.rs`` (R198 pure-logic subset + R199 decision layer).

``xai-grok-sampler`` is the actor-based sampling/inference layer; ``retry.rs``
is its self-marked pure-logic leaf (``//! Pure logic only: no I/O, no
notifications, no logging side-effects. The actor (M4) wraps this with the
actual retry loop.``). It owns three concerns: (1) resolving the per-call retry
budget from env/model/default, (2) the backoff schedule (exponential with ±20 %
jitter for ordinary retries, near-instant for doom-loop retries), and (3)
classifying a :class:`~minimax_code.sampler.types.SamplingError` into a retry
decision. (1) and (2) landed in R198; (3) lands here in R199 now that
:class:`~minimax_code.sampler.types.SamplingError` has migrated.

Migrated (R198, pure logic -- backoff + budget)
-----------------------------------------------

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

Migrated (R199, decision layer -- consumes SamplingError)
---------------------------------------------------------

* :class:`RetryDecision` -- the 6-variant discriminated union
  (:class:`Retry` / :class:`RetryWithBackoff` / :class:`RetryWithImageStrip` /
  :class:`RetryWithClientRebuild` / :class:`EmitToSession` / :class:`Fatal`).
* :func:`classify_error` -- the 10-guard classification matrix (auth +
  encrypted-content -> emit; 413 + image-error -> strip; ``x-should-retry:
  false`` + context-length -> fatal; doom-loop -> near-instant retry; 429 ->
  rate-limited backoff capped at the threshold; generic retryable -> first
  rebuild / later backoff; else fatal).
* :func:`format_sampling_error` -- the 12-variant human-readable formatter
  (telemetry-friendly, with status-code hints for the ``Api`` variant).
* :func:`clone_error` -- reconstruct an owned copy (grok ``clone_error``).

YAGNI / deferred
----------------

* **RetryPolicy struct** -- YAGNI per the :mod:`minimax_code.sampler.config`
  ledger: the platform already has two retry policies
  (:mod:`minimax_code.reliability.retry` + :mod:`minimax_code.resilience.retry_policy`);
  a third copy from grok is not carried. The pure *backoff arithmetic* +
  *decision matrix* land here for any consumer that wants grok's exact schedule.
* **Global jitter entropy** -- grok derives jitter from a process-global
  ``static JITTER_SEQ: AtomicU64`` (``fetch_add`` per call) + thread id /
  retry count hashed with ``DefaultHasher``. That global mutable state is the
  caller's job: :func:`retry_backoff_with_jitter` / :func:`doom_loop_backoff`
  take ``jitter_unit`` in ``[0.0, 1.0]`` and :func:`classify_error` threads it
  through to whichever backoff arm fires. This mirrors the R197
  :mod:`minimax_code.sqlite_journal` decision to inject the hostname rather
  than fetch it inside the pure core.

Product-fusion note
-------------------

The platform's existing retry policies use their own schedules; this module
does **not** replace them. It exposes grok's exact backoff arithmetic and
decision matrix as a pure, side-effect-free library so that (a) any future
consumer that wants to match grok's retry cadence can, and (b) the migrated
:class:`~minimax_code.sampler.types.SamplingError` has its decision consumer
landed and tested. No I/O, no globals, no LLM coupling -- just integers,
:class:`~datetime.timedelta`, and pattern matching.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta

from minimax_code.sampler.types import (
    Api,
    Auth,
    DoomLoopDetected,
    EmptyResponse,
    EventStreamError,
    Http,
    IdleTimeout,
    InvalidConfiguration,
    MaxTokensTruncation,
    SamplingError,
    Serialization,
    StreamError,
)

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


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """What the actor should do next given a sampling error (grok
    ``RetryDecision``).

    Pure data: callers (the actor's per-request task) perform the actual sleep,
    image strip, client rebuild, or emit. The 6 variant subclasses below carry
    the variant-specific fields.
    """


@dataclass(frozen=True, slots=True)
class Retry(RetryDecision):
    """Sleep ``backoff`` and retry with the same client (grok ``Retry``)."""

    backoff: timedelta


@dataclass(frozen=True, slots=True)
class RetryWithBackoff(RetryDecision):
    """Rate-limited retry: sleep ``backoff``, flag the rate-limit for budget
    accounting (grok ``RetryWithBackoff``)."""

    backoff: timedelta
    is_rate_limited: bool


@dataclass(frozen=True, slots=True)
class RetryWithImageStrip(RetryDecision):
    """Strip inline images and retry once (grok ``RetryWithImageStrip``):
    413 / image-processing errors."""


@dataclass(frozen=True, slots=True)
class RetryWithClientRebuild(RetryDecision):
    """First generic retry: rebuild the reqwest client before sleeping (grok
    ``RetryWithClientRebuild``) to clear a poisoned connection pool."""

    backoff: timedelta


@dataclass(frozen=True, slots=True)
class EmitToSession(RetryDecision):
    """Surface ``error`` to the user verbatim, ending the turn (grok
    ``EmitToSession``): auth / encrypted-content errors."""

    error: SamplingError


@dataclass(frozen=True, slots=True)
class Fatal(RetryDecision):
    """Give up: budget exhausted or error is non-retryable (grok ``Fatal``)."""

    error: SamplingError


def _backoff_or_retry_after(
    err: SamplingError, next_attempt: int, jitter_unit: float
) -> timedelta:
    """``Retry-After`` header (seconds) when present, else exponential backoff.

    Pure. Shared by the rate-limited and generic-retryable arms of
    :func:`classify_error` (grok inlines this twice).
    """
    retry_after = err.retry_after()
    if retry_after is not None:
        return timedelta(seconds=retry_after)
    return retry_backoff_with_jitter(next_attempt, jitter_unit)


def classify_error(
    err: SamplingError,
    retry_count: int,
    max_retries: int,
    rate_limit_threshold: int,
    jitter_unit: float = 0.5,
) -> RetryDecision:
    """Classify a sampling error into a :class:`RetryDecision` (grok
    ``classify_error``).

    Pure. ``retry_count`` is the number of retries already performed (0 on the
    first failure); ``max_retries`` is the total budget; ``rate_limit_threshold``
    caps consecutive 429 retries (see :data:`RATE_LIMIT_RETRY_THRESHOLD`);
    ``jitter_unit`` in ``[0.0, 1.0]`` is the entropy source threaded into
    whichever backoff arm fires (grok's global ``AtomicU64`` is the caller's
    job -- see the module docstring).

    Guard order is load-bearing (mirrors grok's match arms): auth +
    encrypted-content emit to the session before any retry; 413 +
    image-processing strip images before honoring ``x-should-retry: false`` (a
    strip changes the payload, so a "don't retry" on the original does not
    apply to the stripped request); context-length overflow is fatal before the
    generic retryable arm.
    """
    # Auth + encrypted-content are session-owned: surface the raw error.
    if err.is_auth_error():
        return EmitToSession(clone_error(err))
    if err.is_encrypted_content_error():
        return EmitToSession(clone_error(err))

    # 413 / image processing -> strip images and retry once.
    if err.is_payload_too_large():
        return RetryWithImageStrip()
    if err.is_image_processing_error():
        return RetryWithImageStrip()

    # Server says don't retry (x-should-retry: false). Checked AFTER image-strip
    # guards: stripping changes the payload, so a "don't retry" on the original
    # doesn't apply to the stripped request.
    if err.should_retry_header() is False:
        return Fatal(clone_error(err))

    # Context-window overflow is deterministic -> never retry.
    if err.is_context_length_error():
        return Fatal(clone_error(err))

    # Doom-loop -> near-instant retry (the recovery loop owns the real budget).
    if isinstance(err, DoomLoopDetected):
        return Retry(backoff=doom_loop_backoff(jitter_unit))

    # 429 -> rate-limited backoff, capped at min(max_retries, threshold).
    if err.is_rate_limited():
        next_attempt = retry_count + 1
        effective_cap = min(max_retries, rate_limit_threshold)
        if next_attempt >= effective_cap:
            return Fatal(clone_error(err))
        backoff = _backoff_or_retry_after(err, next_attempt, jitter_unit)
        return RetryWithBackoff(backoff=backoff, is_rate_limited=True)

    # Generic retryable transport / 5xx: first retry rebuilds the client.
    if err.is_retryable():
        next_attempt = retry_count + 1
        if next_attempt >= max_retries:
            return Fatal(clone_error(err))
        backoff = _backoff_or_retry_after(err, next_attempt, jitter_unit)
        if next_attempt == 1:
            return RetryWithClientRebuild(backoff=backoff)
        return Retry(backoff=backoff)

    return Fatal(clone_error(err))


def _api_status_hint(status: int) -> str:
    """Per-status human hint for the ``Api`` variant (grok status_hint match).

    Pure. Empty string for statuses grok leaves unhinted.
    """
    if status == 400:
        return " (bad request - check your input)"
    if status in (401, 403):
        return " (authentication issue - check your API key)"
    if status == 404:
        return " (endpoint not found - check model configuration)"
    if status == 413:
        return " (request too large - try /compact or start new session)"
    if status == 429:
        return " (rate limited - please wait and retry)"
    if status == 500:
        return " (server internal error)"
    if status in (502, 503, 504):
        return " (server unavailable - please retry)"
    return ""


def format_sampling_error(err: SamplingError, retry_count: int | None) -> str:
    """Build a telemetry-friendly description of ``err`` (grok
    ``format_sampling_error``).

    ``retry_count``, when present, prefixes a ``"Request failed after N
    retries. "`` banner. Pure string formatting: no logging, no I/O. The
    ``Http`` arm renders the purified message directly (grok's reqwest introspection
    -- timeout / connect / status / url -- is the caller's job); the
    ``Serialization`` arm renders the message (which already carries
    ``line N column M: ...`` from grok's ``Display``).
    """
    prefix = f"Request failed after {retry_count} retries. " if retry_count is not None else ""

    if isinstance(err, Auth):
        return (
            f"{prefix}Authentication failed: {err.message}. "
            "Please check your API key configuration."
        )
    if isinstance(err, InvalidConfiguration):
        return (
            f"{prefix}Invalid configuration: {err.message}. "
            "Please check your model settings."
        )
    if isinstance(err, Http):
        return (
            f"{prefix}HTTP request failed: {err.message}. "
            "This may be a network issue or the API endpoint may be unavailable."
        )
    if isinstance(err, Serialization):
        return (
            f"{prefix}Failed to parse API response: {err.message}. "
            "This indicates an unexpected response format from the server."
        )
    if isinstance(err, Api):
        hint = _api_status_hint(err.status)
        return f"{prefix}API error (HTTP {err.status}{hint}): {err.message}"
    if isinstance(err, EventStreamError):
        return (
            f"{prefix}Event stream error: {err.message}. "
            "The connection to the server was interrupted."
        )
    if isinstance(err, StreamError):
        return (
            f"{prefix}Server stream error ({err.error_type}): {err.message}. "
            "The server encountered an error while streaming the response."
        )
    if isinstance(err, IdleTimeout):
        return (
            f"{prefix}Model stopped responding after {err.elapsed_secs}s. "
            "The model may be overloaded or stuck. Try again or use a different model."
        )
    if isinstance(err, EmptyResponse):
        ctx = err.context
        return (
            f"{prefix}Empty response from model ({ctx.reason}): model={ctx.model}, "
            f"had_reasoning={ctx.had_reasoning}, finish_reason={ctx.finish_reason_str()}, "
            f"completion_tokens={ctx.completion_tokens if ctx.completion_tokens is not None else 0}"
        )
    if isinstance(err, MaxTokensTruncation):
        return f"{prefix}Response truncated by max_tokens."
    if isinstance(err, DoomLoopDetected):
        return (
            f"{prefix}Server detected a reasoning loop ({', '.join(err.triggers)}); "
            "resampling the response."
        )
    # Unreachable: the union is exhaustive.
    raise AssertionError(f"unhandled SamplingError variant: {type(err).__name__}")


def clone_error(err: SamplingError) -> SamplingError:
    """Reconstruct an owned :class:`SamplingError` (grok ``clone_error``).

    grok's ``Http`` / ``Serialization`` arms downgrade because
    ``reqwest::Error`` / ``serde_json::Error`` are not ``Clone``; the purified
    variants carry a ``str`` message, so every variant clones as-is (the
    ``Http`` -> ``EventStreamError`` fallback is no longer needed and would
    needlessly change the variant type). ``EmptyResponse.context`` is a frozen
    dataclass -- sharing the reference is equivalent to grok's ``.clone()``.
    """
    if isinstance(
        err, (Auth, InvalidConfiguration, Http, Serialization, EventStreamError)
    ):
        return type(err)(message=err.message)
    if isinstance(err, Api):
        return Api(
            status=err.status,
            message=err.message,
            model_metadata=err.model_metadata,
            retry_after_secs=err.retry_after_secs,
            should_retry=err.should_retry,
        )
    if isinstance(err, StreamError):
        return StreamError(error_type=err.error_type, message=err.message)
    if isinstance(err, IdleTimeout):
        return IdleTimeout(elapsed_secs=err.elapsed_secs)
    if isinstance(err, EmptyResponse):
        return EmptyResponse(context=err.context)
    if isinstance(err, MaxTokensTruncation):
        return MaxTokensTruncation()
    if isinstance(err, DoomLoopDetected):
        return DoomLoopDetected(
            triggers=err.triggers, aborted_at_chunk=err.aborted_at_chunk
        )
    raise AssertionError(f"unhandled SamplingError variant: {type(err).__name__}")


__all__ = [
    "BACKOFF_BASE_MS",
    "BACKOFF_CAP_MS",
    "DEFAULT_MAX_RETRIES",
    "DOOM_LOOP_BOUND_MS",
    "EmitToSession",
    "Fatal",
    "RATE_LIMIT_RETRY_THRESHOLD",
    "Retry",
    "RetryDecision",
    "RetryWithBackoff",
    "RetryWithClientRebuild",
    "RetryWithImageStrip",
    "backoff_base_ms",
    "classify_error",
    "clone_error",
    "doom_loop_backoff",
    "format_sampling_error",
    "resolve_max_retries",
    "resolve_max_retries_with_env",
    "retry_backoff_with_jitter",
]
