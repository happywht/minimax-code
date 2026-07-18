"""Pure shared token-estimation primitives (R27).

Ports grok-build's ``xai-token-estimation`` crate — the single source of
truth for the bytes/4 heuristic and the derived context-window arithmetic
that ``/context``, ``/session-info``, auto-compact gates, the preflight
overflow check, and every renderer use to talk about context-window usage.

This module is intentionally dependency-free and side-effect-free: two
constants and nine pure functions, exactly like the Rust original. The
host (AgentCore, the upcoming compaction pipeline, the IPC ``/context``
handler) consumes it; nothing here reaches back into the host.

Integer-overflow parity
-----------------------
grok is defensive about ``u64`` overflow with ``saturating_mul`` /
``saturating_sub``. Python ``int`` is unbounded, so multiplication never
wraps and the saturation on ``*_mul`` helpers is a no-op. The single place
saturation is *observable* is :func:`exceeds_threshold_with_headroom`,
where ``saturating_sub`` can clamp the right-hand side to zero — there we
mirror it explicitly with ``max(0, ...)`` so the boolean agrees across the
two implementations for every input.

Rounding parity
---------------
:func:`usage_percentage_u8` mirrors Rust's ``f64::round`` —
round-half-away-from-zero. Python 3's built-in :func:`round` is banker's
rounding (half-to-even), which would turn ``42.5`` into ``42`` instead of
grok's ``43``; :func:`_round_half_up` (``floor(x + 0.5)``) reproduces the
non-negative half-up behaviour exactly.
"""

from __future__ import annotations

import math

__all__ = [
    "BYTES_PER_TOKEN",
    "IMAGE_TOKEN_ESTIMATE",
    "estimate_tokens",
    "estimate_chars",
    "estimate_image_tokens",
    "usage_percentage",
    "usage_percentage_u8",
    "usage_percentage_truncated_u8",
    "free_tokens",
    "exceeds_threshold",
    "exceeds_threshold_with_headroom",
]

#: Bytes per token under the rough character-based heuristic.
BYTES_PER_TOKEN: int = 4

#: Per-image approximate token cost when summing low-resolution image patches.
IMAGE_TOKEN_ESTIMATE: int = 765


def estimate_tokens(s: str) -> int:
    """Bytes/4 estimate of a string's token count.

    Parameters
    ----------
    s:
        The string to size. Length is measured in bytes of the UTF-8 view
        — parity with Rust's ``s.len()`` on ``&str``, which counts UTF-8
        bytes (not Unicode code points). A single CJK character is 3 bytes,
        so ``len(s.encode("utf-8"))`` is used rather than ``len(s)``.
    """
    return len(s.encode("utf-8")) // BYTES_PER_TOKEN


def estimate_chars(tokens: int) -> int:
    """Inverse of :func:`estimate_tokens`: convert a token budget into a char budget.

    Used by skill discovery to size text passages against the model's
    context window. grok uses ``saturating_mul``; Python ints are unbounded
    so the saturation is a no-op here.
    """
    return tokens * BYTES_PER_TOKEN


def estimate_image_tokens(image_count: int) -> int:
    """Token estimate for ``image_count`` images at :data:`IMAGE_TOKEN_ESTIMATE` each."""
    return image_count * IMAGE_TOKEN_ESTIMATE


def usage_percentage(used: int, total: int) -> float:
    """Usage percentage as ``f64``, clamped to ``100.0``.

    Returns ``0.0`` when ``total == 0`` so callers do not have to special-case
    missing windows.
    """
    if total == 0:
        return 0.0
    return min(used / total * 100.0, 100.0)


def usage_percentage_u8(used: int, total: int) -> int:
    """Usage percentage rounded to an int (round-half-up), clamped to ``100``.

    Mirrors grok's ``usage_percentage(...).round() as u8``. The clamp lives
    inside :func:`usage_percentage`; rounding a clamped value can never
    exceed ``100``. See the module docstring for why this is not
    :func:`round`.
    """
    return _round_half_up(usage_percentage(used, total))


def usage_percentage_truncated_u8(used: int, total: int) -> int:
    """Integer-arithmetic (truncating) usage percentage, clamped to ``100``.

    Differs from :func:`usage_percentage_u8` in two ways: no ``float``
    round-trip, and the result is **truncated** (not rounded). The result
    therefore agrees with :func:`exceeds_threshold` — the auto-compact gate
    and the rendered percentage cross the threshold at the same token.
    """
    if total == 0:
        return 0
    # Integer floor-division truncates toward zero (operands are non-negative);
    # min() mirrors Rust's ``.min(100)`` before the ``as u8`` cast.
    return min(used * 100 // total, 100)


def free_tokens(total: int, used: int) -> int:
    """``total - used``, saturating at zero.

    The "free" portion of the context window for ``/context`` rendering.
    """
    return max(0, total - used)


def exceeds_threshold(used: int, context_window: int, threshold_percent: int) -> bool:
    """``True`` when ``used >= context_window * threshold_percent / 100``.

    Returns ``False`` for ``context_window == 0`` so callers do not have to
    special-case missing windows. Computed in integer arithmetic to match
    the existing auto-compact gate semantics (the ``>=`` boundary fires one
    token earlier than the legacy ``>`` gate).
    """
    if context_window == 0:
        return False
    return used * 100 >= context_window * threshold_percent


def exceeds_threshold_with_headroom(
    used: int,
    context_window: int,
    threshold_percent: int,
    headroom: int,
) -> bool:
    """Scaled :func:`exceeds_threshold` minus a token headroom.

    ``True`` when
    ``used * 100 >= context_window * threshold_percent - headroom * 100``,
    with the right-hand side saturating at zero (mirrors grok's
    ``saturating_sub``). Returns ``False`` for ``context_window == 0``.
    """
    if context_window == 0:
        return False
    # Saturating subtraction: a headroom larger than the scaled threshold
    # clamps the right-hand side to 0, at which point any non-negative
    # ``used * 100`` satisfies the ``>=`` and the gate fires.
    rhs = max(0, context_window * threshold_percent - headroom * 100)
    return used * 100 >= rhs


def _round_half_up(x: float) -> int:
    """Round half up — matches Rust ``f64::round`` for non-negative inputs.

    Rust's ``f64::round`` is round-half-away-from-zero; for the non-negative
    percentages produced here that is round-half-up, reproduced exactly by
    ``floor(x + 0.5)``. Python's built-in :func:`round` uses banker's
    rounding (half-to-even) and would disagree at ``.5`` boundaries.
    """
    return math.floor(x + 0.5)
