"""Intra-compaction trigger decision (R28).

Ports the pure decision half of grok-build's
``xai-grok-compaction::intra_compaction::trigger`` — the
:func:`should_compact` predicate plus the :class:`IntraCompactionTrigger`
result it returns. No sampler, no LLM call, no state commit: the caller
threads the four inputs (policy, last prompt token count, context window,
current step) and gets back ``None`` or a trigger record.

This is the direct downstream consumer of R27's token estimation: the
``percent`` field is computed with
:func:`minimax_code.token_estimation.usage_percentage_truncated_u8`, so the
rendered percentage and the trigger threshold cross at the same token — the
same invariant grok locks in by sharing the bytes/4 arithmetic across both
helpers.

Boundary semantics
------------------
grok's trigger is **strictly greater than**: ``last_prompt_tokens <= threshold``
returns ``None``. This differs from R27's :func:`~minimax_code.token_estimation.exceeds_threshold`
which uses ``>=`` — so the trigger does *not* delegate to that helper, it
computes its own integer threshold and compares against it directly. At
``cw=100_000, pct=85`` the threshold is ``85_000``; ``85_000`` does NOT fire
(``<=``), ``85_001`` does.
"""

from __future__ import annotations

from dataclasses import dataclass

from minimax_code.token_estimation import usage_percentage_truncated_u8

from .config import IntraCompactionConfig, IntraCompactionMode

__all__ = ["IntraCompactionTrigger", "should_compact"]


@dataclass
class IntraCompactionTrigger:
    """Why intra-compaction was triggered.

    Constructed by :func:`should_compact` and threaded through to the
    compaction pass and the event stream.
    """

    #: Token count of the prompt most recently sent to the model.
    last_prompt_tokens: int
    #: Context window of the agent's current sampler (``max_len``).
    context_window: int
    #: ``last_prompt_tokens / context_window`` as an integer percentage,
    #: truncated (not rounded) and clamped to ``[0, 100]``.
    percent: int
    #: Step index (0-based) at which the trigger fired.
    step: int


def should_compact(
    policy: IntraCompactionConfig,
    last_prompt_tokens: int,
    context_window: int,
    current_step: int,
) -> IntraCompactionTrigger | None:
    """Pure decision: should intra-compaction trigger now?

    Returns a trigger record if all gating conditions are met; ``None``
    otherwise. The caller must additionally check any feature flag / global
    kill switch — this function deals only with the policy + step state.

    Gating order (mirrors grok):

    1. ``policy.enabled`` is ``False`` → ``None``.
    2. ``context_window == 0`` → ``None`` (missing window).
    3. Partial modes (non-``FullReplace``) below ``min_steps_before_compact``
       → ``None``. ``FullReplace`` matches grok-build's full-replace trigger
       (token threshold alone) so a large first-step prompt can still compact.
    4. ``last_prompt_tokens <= threshold`` → ``None`` (strict ``>``
       boundary; ``threshold = context_window * trigger_threshold_percent // 100``).

    The ``percent`` field reuses R27's truncated helper so the rendered
    percentage agrees with the threshold crossing.
    """
    if not policy.enabled:
        return None
    if context_window == 0:
        return None
    # FullReplace: token threshold only. Partial modes: skip early steps.
    if (
        policy.mode is not IntraCompactionMode.FULL_REPLACE
        and current_step < policy.min_steps_before_compact
    ):
        return None

    threshold = context_window * policy.trigger_threshold_percent // 100
    if last_prompt_tokens <= threshold:
        return None

    percent = usage_percentage_truncated_u8(last_prompt_tokens, context_window)
    return IntraCompactionTrigger(
        last_prompt_tokens=last_prompt_tokens,
        context_window=context_window,
        percent=percent,
        step=current_step,
    )
