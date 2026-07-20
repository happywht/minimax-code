"""Tests for ``minimax_code.tracing.fastrace`` (R129).

Mirrors grok-build's ``xai-tracing/src/fastrace.rs`` pure-logic subset
plus ``testing.rs``'s ``parse_traceparent``. The suite covers:

* the W3C traceparent codec: encode/decode round-trip, the field-width
  regex, the all-zero trace-id / span-id rejection (W3C §3.3.1.1/2), and a
  battery of malformed inputs that must yield ``None``;
* :meth:`SpanContext.random` length, hex-ness, and uniqueness across calls;
* :func:`current_trace_id` reading the :mod:`contextvars` current context
  (``None`` when none active);
* :func:`local_or_random_span_ctx` returning the current context when one
  is active, falling back to a fresh random one otherwise;
* :func:`enter_span_with_traceparent` installing a decoded context for the
  ``with`` block and restoring the previous one on exit, plus the
  malformed-traceparent fallback (returns ``None``, leaves current
  untouched);
* the core :mod:`asyncio` contract — a :class:`SpanContext` set in one
  coroutine is visible to a task it spawns (the reason this module uses
  :class:`~contextvars.ContextVar`).

Context isolation: the suite uses a ``clean_span_context`` fixture that
forces ``_current_span_context`` to ``None`` for the test body (and
restores the prior value on teardown), so a leak in one test cannot flip a
later "no context active" assertion. Tests that *set* a context use the
``enter_span_with_traceparent`` ``with`` block, which restores via its
saved token on exit.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator

import pytest

from minimax_code.tracing.fastrace import (
    SpanContext,
    current_trace_id,
    enter_span_with_traceparent,
    local_or_random_span_ctx,
)

#: A well-formed W3C traceparent for round-trip / install assertions.
_VALID_TRACEPARENT = "00-aabbccddeeff00112233445566778899-0011223344556677-01"
_TRACE_ID = "aabbccddeeff00112233445566778899"
_SPAN_ID = "0011223344556677"


@pytest.fixture
def clean_span_context() -> Iterator[None]:
    """Force the active SpanContext to None for the test body (R129).

    Other tests in the suite (or a prior session of the process) may have
    set ``_current_span_context`` and not reset it; without this fixture a
    leak would flip "no context active" assertions. ``set(None)`` returns a
    token that restores the prior value on teardown.
    """
    from minimax_code.tracing.fastrace import _current_span_context

    token = _current_span_context.set(None)
    try:
        yield
    finally:
        _current_span_context.reset(token)


# ---------------------------------------------------------------------------
# W3C traceparent codec
# ---------------------------------------------------------------------------


def test_encode_w3c_traceparent_formats_canonical_string() -> None:
    ctx = SpanContext(_TRACE_ID, _SPAN_ID, trace_flags=0x01)
    assert ctx.encode_w3c_traceparent() == _VALID_TRACEPARENT


def test_encode_zero_pads_trace_flags_to_two_hex() -> None:
    # trace_flags=1 must render as "01", not "1"; flags=0 renders "00".
    assert SpanContext(_TRACE_ID, _SPAN_ID, trace_flags=0x01).encode_w3c_traceparent()[
        -2:
    ] == "01"
    assert SpanContext(_TRACE_ID, _SPAN_ID, trace_flags=0x00).encode_w3c_traceparent()[
        -2:
    ] == "00"


def test_decode_encode_round_trip() -> None:
    ctx = SpanContext.decode_w3c_traceparent(_VALID_TRACEPARENT)
    assert ctx is not None
    assert ctx.trace_id == _TRACE_ID
    assert ctx.span_id == _SPAN_ID
    assert ctx.trace_flags == 0x01
    # the encoder must reproduce the input byte-for-byte for a version-00 input
    assert ctx.encode_w3c_traceparent() == _VALID_TRACEPARENT


@pytest.mark.parametrize(
    "bad",
    [
        "",  # empty
        "garbage",  # no separators
        # wrong field widths
        "00-aabb-aabb-01",
        "00-" + "a" * 31 + "-" + "b" * 16 + "-01",  # trace-id 31 chars
        "00-" + "a" * 33 + "-" + "b" * 16 + "-01",  # trace-id 33 chars
        "00-" + "a" * 32 + "-" + "b" * 15 + "-01",  # span-id 15 chars
        "00-" + "a" * 32 + "-" + "b" * 17 + "-01",  # span-id 17 chars
        "00-" + "a" * 32 + "-" + "b" * 16 + "-1",  # flags 1 char
        "00-" + "a" * 32 + "-" + "b" * 16 + "-001",  # flags 3 chars
        # non-hex in fields
        "00-" + "z" * 32 + "-" + "b" * 16 + "-01",
        "00-" + "a" * 32 + "-" + "Z" * 16 + "-01",
        "0g-" + "a" * 32 + "-" + "b" * 16 + "-01",  # non-hex version
        # wrong separator (space instead of dash)
        "00 " + "a" * 32 + " " + "b" * 16 + " 01",
        # trailing garbage after a valid-looking prefix
        "00-" + "a" * 32 + "-" + "b" * 16 + "-01-extra",
    ],
)
def test_decode_rejects_malformed_traceparent(bad: str) -> None:
    assert SpanContext.decode_w3c_traceparent(bad) is None


def test_decode_rejects_all_zero_trace_id() -> None:
    # W3C §3.3.1.1 forbids an all-zero trace-id
    tp = "00-" + "0" * 32 + "-" + "1" * 16 + "-01"
    assert SpanContext.decode_w3c_traceparent(tp) is None


def test_decode_rejects_all_zero_span_id() -> None:
    # W3C §3.3.1.2 forbids an all-zero parent-id (span-id)
    tp = "00-" + "1" * 32 + "-" + "0" * 16 + "-01"
    assert SpanContext.decode_w3c_traceparent(tp) is None


def test_decode_accepts_unsampled_flags() -> None:
    # trace_flags=0x00 is valid (unsampled) and must round-trip
    tp = "00-" + "a" * 32 + "-" + "b" * 16 + "-00"
    ctx = SpanContext.decode_w3c_traceparent(tp)
    assert ctx is not None
    assert ctx.trace_flags == 0x00


# ---------------------------------------------------------------------------
# SpanContext.random
# ---------------------------------------------------------------------------


def test_random_mints_canonical_lengths() -> None:
    ctx = SpanContext.random()
    assert len(ctx.trace_id) == 32
    assert len(ctx.span_id) == 16
    # both fields lower-case hex
    assert re.fullmatch(r"[0-9a-f]{32}", ctx.trace_id)
    assert re.fullmatch(r"[0-9a-f]{16}", ctx.span_id)


def test_random_encodes_to_valid_traceparent() -> None:
    # a random context must encode to something decode accepts (round-trip)
    tp = SpanContext.random().encode_w3c_traceparent()
    assert SpanContext.decode_w3c_traceparent(tp) is not None


def test_random_is_unique_across_calls() -> None:
    # 16-byte trace-id + 8-byte span-id from secrets — collisions astronomically
    # unlikely; this guards against a stub that returns a constant.
    contexts = {SpanContext.random().encode_w3c_traceparent() for _ in range(64)}
    assert len(contexts) == 64


def test_random_default_flags_sampled() -> None:
    assert SpanContext.random().trace_flags == 0x01


# ---------------------------------------------------------------------------
# current_trace_id — reads the contextvars current context
# ---------------------------------------------------------------------------


def test_current_trace_id_none_when_no_context(clean_span_context: None) -> None:
    assert current_trace_id() is None


def test_current_trace_id_returns_traceparent_when_context_set(clean_span_context: None) -> None:
    with enter_span_with_traceparent("test", _VALID_TRACEPARENT):
        assert current_trace_id() == _VALID_TRACEPARENT


# ---------------------------------------------------------------------------
# local_or_random_span_ctx — current or fresh
# ---------------------------------------------------------------------------


def test_local_or_random_mints_when_no_context(clean_span_context: None) -> None:
    ctx = local_or_random_span_ctx()
    # minted context must encode to a valid traceparent
    assert SpanContext.decode_w3c_traceparent(ctx.encode_w3c_traceparent()) is not None


def test_local_or_random_returns_current_when_set(clean_span_context: None) -> None:
    with enter_span_with_traceparent("test", _VALID_TRACEPARENT):
        ctx = local_or_random_span_ctx()
        assert ctx.encode_w3c_traceparent() == _VALID_TRACEPARENT


# ---------------------------------------------------------------------------
# enter_span_with_traceparent — context manager install / restore
# ---------------------------------------------------------------------------


def test_enter_returns_decoded_context(clean_span_context: None) -> None:
    with enter_span_with_traceparent("test", _VALID_TRACEPARENT) as ctx:
        assert ctx is not None
        assert ctx.encode_w3c_traceparent() == _VALID_TRACEPARENT


def test_enter_installs_context_for_block_then_restores(clean_span_context: None) -> None:
    assert current_trace_id() is None
    with enter_span_with_traceparent("test", _VALID_TRACEPARENT):
        assert current_trace_id() == _VALID_TRACEPARENT
    assert current_trace_id() is None


def test_enter_restores_prior_context_not_none(clean_span_context: None) -> None:
    # nested enters: the outer context must be restored after the inner exits,
    # not reset to None.
    outer_tp = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    inner_tp = "00-" + "3" * 32 + "-" + "4" * 16 + "-01"
    with enter_span_with_traceparent("outer", outer_tp):
        with enter_span_with_traceparent("inner", inner_tp):
            assert current_trace_id() == inner_tp
        assert current_trace_id() == outer_tp
    assert current_trace_id() is None


def test_enter_returns_none_and_leaves_current_untouched_on_malformed(
    clean_span_context: None,
) -> None:
    # fastrace falls back to the local parent on a bad traceparent; the Python
    # equivalent is "do not change current", so None is returned and the
    # surrounding context (here None) is preserved.
    assert current_trace_id() is None
    with enter_span_with_traceparent("test", "not-a-traceparent") as ctx:
        assert ctx is None
        assert current_trace_id() is None
    assert current_trace_id() is None


def test_enter_malformed_does_not_disturb_existing_context(clean_span_context: None) -> None:
    outer_tp = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    with enter_span_with_traceparent("outer", outer_tp):
        with enter_span_with_traceparent("bad", "garbage") as ctx:
            assert ctx is None
            # the outer context remains active — the malformed enter did not
            # clobber it, matching fastrace's local-parent fallback
            assert current_trace_id() == outer_tp
        assert current_trace_id() == outer_tp


# ---------------------------------------------------------------------------
# asyncio propagation — the core contract of using contextvars
# ---------------------------------------------------------------------------


def test_context_propagates_to_spawned_asyncio_task(clean_span_context: None) -> None:
    async def child() -> str | None:
        # a task spawned via asyncio.create_task inherits a copy of the
        # current contextvars context, so the SpanContext set in the parent
        # is visible here — this is the contract the tokio leaf will express.
        return current_trace_id()

    async def driver() -> str | None:
        with enter_span_with_traceparent("test", _VALID_TRACEPARENT):
            task = asyncio.create_task(child())
            return await task

    assert asyncio.run(driver()) == _VALID_TRACEPARENT


def test_context_reset_in_parent_does_not_leak_to_unrelated_task(
    clean_span_context: None,
) -> None:
    # a task that runs *after* the with-block exits must not see the context —
    # the token reset on __exit__ removes it from the parent context, and
    # unrelated tasks do not inherit it.
    async def child() -> str | None:
        return current_trace_id()

    async def driver() -> str | None:
        with enter_span_with_traceparent("test", _VALID_TRACEPARENT):
            pass  # context entered and exited inside driver
        task = asyncio.create_task(child())
        return await task

    assert asyncio.run(driver()) is None
