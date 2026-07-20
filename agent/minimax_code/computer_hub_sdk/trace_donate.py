"""Trace donation: chunk spans into donation-sized payloads + drain fence (R137).

Fusion of grok-build's ``xai-computer-hub-sdk/src/trace_donate.rs`` (206
lines). This is the trace-side client of the shared donation pump (R136):
it owns the chunking policy that bounds each OTLP export request and the
shutdown drain fence. The bounded retry buffer + drain barrier itself lives
in :mod:`minimax_code.computer_hub_sdk.donate_pump`; this module feeds it.

What migrates vs what does NOT (R132-style YAGNI boundary declaration)
---------------------------------------------------------------------

The Rust original is half pure-policy, half OTel/fastrace framework glue.
MiniMax Code has no ``fastrace`` runtime (xai-tracing R127-R132 is a
self-contained lightweight tracing layer, NOT fastrace) and no
``opentelemetry-sdk`` / ``opentelemetry-proto`` / ``prost`` Python
dependency, so the framework-glue half is declared out of scope here
rather than ported into dead code.

MIGRATED (transport-agnostic pure policy):

* :func:`chunk_donation_batches` -- split a span batch into contiguous
  chunks of at most :data:`~minimax_code.tool_protocol.MAX_SPANS_PER_DONATION`.
  Pure slicing, no OTel types. Mirrors the Rust ``export`` loop's
  ``split_off`` tail recursion.
* :func:`try_enqueue` -- bounded non-blocking enqueue onto the pump's
  queue; drop on full (mirrors ``mpsc::Sender::try_send`` ``Err`` -> drop).
* :func:`export_spans` -- the donate-export loop: chunk -> encode ->
  drop oversized -> enqueue (drop on full). Parameterized over an
  ``encode_chunk`` closure (returns the base64 payload or ``None`` for an
  oversized chunk) so it has no OTel/prost dependency; returns the count
  of payloads enqueued.
* :class:`TraceDonationPump` -- shutdown drain fence; thin handle over
  :func:`~minimax_code.computer_hub_sdk.donate_pump.drain_via`.

NOT MIGRATED (framework glue with no Python equivalent):

* ``HubDonatingReporter`` -- ``fastrace::collector::Reporter`` wrapping
  ``OpenTelemetryReporter``. No fastrace runtime in Python.
* ``PumpSpanExporter`` -- ``opentelemetry_sdk::trace::SpanExporter``: the
  OTel SDK trait that the export loop hangs off. The loop's POLICY migrates
  (above); the trait integration does not.
* ``ExportTraceServiceRequest`` protobuf encode +
  ``group_spans_by_resource_and_scope`` -- ``prost`` /
  ``opentelemetry_proto`` transforms. The encoding is the ``encode_chunk``
  closure a caller supplies; the protobuf machinery is the caller's concern
  (R136 declared the same boundary for the OTLP wire helpers).
* ``ToolServer::trace_donation_reporter`` -- the assembly point.
  ``ToolServer`` (``server.rs``, 2649 lines) is a later leaf. Its
  spawn-pump + donate-closure geometry is just ``run_pump(rx, donate)``
  over an :class:`asyncio.Queue` with ``donate`` returning
  ``(ok, payload)`` -- identical to R136's pump tests; that leaf wires it
  to ``server.donate_traces``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import TypeVar

from minimax_code.computer_hub_sdk.donate_pump import (
    PumpMsg,
    _PayloadMsg,
    drain_via,
)
from minimax_code.tool_protocol import MAX_SPANS_PER_DONATION

__all__ = [
    "chunk_donation_batches",
    "try_enqueue",
    "export_spans",
    "TraceDonationPump",
]

_log = logging.getLogger(__name__)

T = TypeVar("T")


def chunk_donation_batches(
    items: Sequence[T],
    max_per_batch: int = MAX_SPANS_PER_DONATION,
) -> Iterator[list[T]]:
    """Split ``items`` into contiguous chunks of at most ``max_per_batch``.

    Mirrors the Rust ``export`` loop's ``split_off(MAX_SPANS_PER_DONATION)``
    tail recursion: the batch is chunked into per-donation slices before each
    is encoded and enqueued. A batch of 600 spans at the default 512 cap
    yields ``[512, 88]``; an empty batch yields nothing.

    Pure slicing -- no OTel types -- so the policy is testable without an
    ``opentelemetry_sdk::trace::SpanExporter``.
    """
    for start in range(0, len(items), max_per_batch):
        yield list(items[start : start + max_per_batch])


def try_enqueue(tx: asyncio.Queue[PumpMsg], payload: str) -> bool:
    """Bounded non-blocking enqueue; drop on full (R137).

    Returns ``True`` if enqueued, ``False`` if the bounded queue is full (the
    payload is dropped). Matches the Rust ``tx.try_send(PumpMsg::Payload(..))
    .is_err()`` -> drop path: the exporter runs on fastrace's collector
    thread and must never block, so a full queue drops the batch rather than
    awaiting a slot.
    """
    try:
        tx.put_nowait(_PayloadMsg(payload))
    except asyncio.QueueFull:
        return False
    return True


def export_spans(
    spans: Sequence[T],
    encode_chunk: Callable[[list[T]], str | None],
    enqueue: Callable[[str], bool],
    max_per_batch: int = MAX_SPANS_PER_DONATION,
) -> int:
    """Run the donate-export loop: chunk -> encode -> drop-oversized -> enqueue.

    For each chunk of at most :data:`MAX_SPANS_PER_DONATION`:

    * call ``encode_chunk``; ``None`` means the chunk's wire size exceeds
      ``MAX_DONATION_BYTES`` -> drop it (continue to the next chunk);
    * otherwise hand the payload to ``enqueue``; ``False`` means the pump's
      bounded queue is full -> drop it.

    Returns the count of payloads successfully enqueued. Mirrors
    ``PumpSpanExporter::export``: oversized chunks are skipped but the loop
    keeps draining subsequent chunks; a full queue drops the batch without
    propagating. Telemetry, never correctness.
    """
    sent = 0
    for chunk in chunk_donation_batches(spans, max_per_batch):
        payload = encode_chunk(chunk)
        if payload is None:
            _log.debug("dropping oversized donation payload")
            continue
        if enqueue(payload):
            sent += 1
        else:
            _log.debug("trace donation queue full; dropping span batch")
    return sent


@dataclass
class TraceDonationPump:
    """Shutdown fence: drain queued trace donations before close (R137).

    Thin handle over the shared pump's :func:`drain_via`. Call
    :meth:`drain` after the trace SDK's flush so in-flight span batches get
    a send attempt before the connection tears down. Mirrors Rust
    ``TraceDonationPump { tx }`` whose ``drain(&self)`` is
    ``drain_via(&self.tx).await``.
    """

    tx: asyncio.Queue[PumpMsg]

    async def drain(self) -> None:
        """Resolve once every payload queued before this call has a send attempt."""
        await drain_via(self.tx)
