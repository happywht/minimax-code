"""Tests for ``minimax_code.computer_hub_sdk.trace_donate`` (R137).

Mirrors the POLICY half of grok-build's
``xai-computer-hub-sdk/src/trace_donate.rs`` (the framework half -- fastrace
Reporter / OTel SpanExporter / protobuf encode / ToolServer assembly -- is
declared out of scope: no fastrace runtime, no opentelemetry-sdk/proto in
Python; see the module docstring's YAGNI boundary).

The chunk + oversized-drop + queue-full-drop policy is what the donation
clients depend on, and it is pure -- so it is pinned directly here without
an ``opentelemetry_sdk::trace::SpanExporter``:

* :func:`chunk_donation_batches` -- split at ``MAX_SPANS_PER_DONATION``;
* :func:`try_enqueue` -- drop on :class:`asyncio.QueueFull`;
* :func:`export_spans` -- oversized chunks skipped, queue-full drops, rest
  enqueued, count returned;
* :class:`TraceDonationPump.drain` -- forwards to the shared pump's
  :func:`drain_via`.
"""

from __future__ import annotations

import asyncio

from minimax_code.computer_hub_sdk.donate_pump import (
    _CLOSE,
    _PayloadMsg,
    run_pump,
)
from minimax_code.computer_hub_sdk.trace_donate import (
    TraceDonationPump,
    chunk_donation_batches,
    export_spans,
    try_enqueue,
)


# ---------------------------------------------------------------------------
# chunk_donation_batches
# ---------------------------------------------------------------------------
def test_chunk_donation_batches_splits_at_cap() -> None:
    items = list(range(600))
    chunks = list(chunk_donation_batches(items))
    assert [len(c) for c in chunks] == [512, 88]
    assert chunks[0] == list(range(512))
    assert chunks[1] == list(range(512, 600))


def test_chunk_donation_batches_empty_yields_nothing() -> None:
    assert list(chunk_donation_batches([])) == []


def test_chunk_donation_batches_exact_multiple() -> None:
    items = list(range(1024))
    chunks = list(chunk_donation_batches(items))
    assert [len(c) for c in chunks] == [512, 512]


def test_chunk_donation_batches_respects_custom_cap() -> None:
    chunks = list(chunk_donation_batches(list(range(10)), max_per_batch=4))
    assert [len(c) for c in chunks] == [4, 4, 2]


# ---------------------------------------------------------------------------
# try_enqueue
# ---------------------------------------------------------------------------
def test_try_enqueue_returns_true_when_space() -> None:
    tx: asyncio.Queue = asyncio.Queue(maxsize=2)
    assert try_enqueue(tx, "a") is True
    assert try_enqueue(tx, "b") is True


def test_try_enqueue_returns_false_on_full() -> None:
    tx: asyncio.Queue = asyncio.Queue(maxsize=1)
    assert try_enqueue(tx, "a") is True
    assert try_enqueue(tx, "b") is False  # dropped -- queue full


# ---------------------------------------------------------------------------
# export_spans
# ---------------------------------------------------------------------------
def test_export_spans_drops_oversized_continues_rest() -> None:
    # encode_chunk returns None for the middle chunk (oversized drop);
    # the first and third still enqueue.
    oversized = {1}

    def encode(chunk: list[int]) -> str | None:
        if chunk[0] in oversized:
            return None
        return f"payload-{chunk[0]}"

    enqueued: list[str] = []

    def enqueue(payload: str) -> bool:
        enqueued.append(payload)
        return True

    sent = export_spans([0, 1, 2], encode, enqueue, max_per_batch=1)
    assert sent == 2
    assert enqueued == ["payload-0", "payload-2"]


def test_export_spans_drops_on_full_queue() -> None:
    def encode(chunk: list[int]) -> str | None:
        return f"payload-{chunk[0]}"

    state = {"calls": 0}

    def enqueue(_payload: str) -> bool:
        state["calls"] += 1
        # accept only the first; second onward drop (queue full).
        return state["calls"] == 1

    sent = export_spans([0, 1, 2], encode, enqueue, max_per_batch=1)
    assert sent == 1


def test_export_spans_returns_enqueued_count() -> None:
    def encode(chunk: list[int]) -> str | None:
        return ",".join(str(x) for x in chunk)

    def enqueue(_payload: str) -> bool:
        return True

    sent = export_spans(list(range(600)), encode, enqueue)
    assert sent == 2  # [0..511], [512..599]


def test_export_spans_empty_batch_enqueues_nothing() -> None:
    def encode(_chunk: list[int]) -> str | None:
        raise AssertionError("encode must not run on an empty batch")

    def enqueue(_payload: str) -> bool:
        raise AssertionError("enqueue must not run on an empty batch")

    assert export_spans([], encode, enqueue) == 0


# ---------------------------------------------------------------------------
# TraceDonationPump.drain -- integration with the shared pump (R136).
# ---------------------------------------------------------------------------
async def test_trace_donation_pump_drain_forwards_to_shared_pump() -> None:
    sent: list[str] = []

    async def donate(payload: str) -> tuple[bool, str]:
        sent.append(payload)
        return True, payload

    tx: asyncio.Queue = asyncio.Queue(maxsize=4)
    pump = asyncio.create_task(run_pump(tx, donate))
    handle = TraceDonationPump(tx)

    # queue a payload then drain; the pump must attempt it before ack.
    await tx.put(_PayloadMsg("span-a"))
    await handle.drain()
    assert sent == ["span-a"]

    await tx.put(_CLOSE)
    await pump
