"""Shared donation transport: bounded retry buffer + in-order drain barrier (R136).

Fusion of grok-build's ``xai-computer-hub-sdk/src/donate_pump.rs``. Traces,
logs and metrics all pump through this shared substrate: a background task
owns a bounded retry buffer (:data:`RETRY_CAP`) of base64 OTLP payloads, an
in-order drain barrier (:func:`drain_via`) fences a producer's flush against
the pump's send attempts, and a failed send retains the payload without
cloning so a transient disconnect/reconnect window drops no telemetry.
Overflow beyond the cap evicts the OLDEST payload -- telemetry, never
correctness.

Three concerns live here so the three donation clients (``trace_donate`` /
``log_donate`` / ``metric_donate``, later leaves) share one encoding surface
and one pump instead of copy-pasting per client:

* OTLP wire helpers (:func:`now_unix_nanos`, :func:`string_value`,
  :func:`string_kv`, :func:`make_resource`) -- the ``AnyValue`` / ``KeyValue``
  / ``Resource`` construction that log + metric donation both emit;
* the :data:`PumpMsg` sum type + :func:`run_pump` driver + :func:`drain_via`
  barrier -- the transport-agnostic pump core;
* :func:`_attempt_sends` -- the in-order send-with-retry loop.

Concurrency model
-----------------

The Rust original runs the pump as a ``tokio::spawn`` task communicating
with its producers over a bounded ``mpsc::channel(PENDING_FLUSHES)``;
barriers round-trip through a ``oneshot`` channel. Python mirrors each
primitive faithfully under asyncio:

* ``mpsc::channel(N)`` -> :class:`asyncio.Queue` with ``maxsize=N``. The
  bounded send (``tx.send(..).await``) blocks the producer until a slot is
  free, exactly as ``await queue.put(..)`` does.
* ``oneshot::channel`` -> :class:`asyncio.Future`. :func:`drain_via` creates
  a fresh future, ships a :class:`_BarrierMsg` carrying it, and ``await``s
  its resolution -- the pump resolves it after its drain attempt.
* ``tokio::spawn`` -> :func:`asyncio.create_task` (the caller owns the task
  handle; the pump exits when the queue closes, below).

asyncio is single-threaded and cooperative: the pump's ``await rx.get()``
and ``await donate(..)`` are the only suspension points, so the retry
buffer (:class:`collections.deque`) is never touched concurrently. No lock
is needed, mirroring the Rust original's ownership of ``retry`` by the pump
task alone. This is the same lock-free-degenerates-to-no-await argument
that justified ``DashMap -> dict`` in ``refcount`` (R135) -- a synchronous
critical section between two ``await`` points is atomic under asyncio.

Channel-close semantics
-----------------------

Rust's ``run_pump`` loops on ``rx.recv().await``; when every sender is
dropped, ``recv`` returns ``None`` and the loop exits cleanly (the Rust
tests rely on ``drop(tx); pump.await``). :class:`asyncio.Queue` has no
"all-producers-gone" signal, so this landing closes the queue with an
explicit :data:`_CLOSE` sentinel: the producer puts :data:`_CLOSE` once it
is done, the pump returns on receipt. The Rust caller drops the sender
handle; the Python caller puts the sentinel -- both express "no more
payloads, drain and exit". (The ``drain_via`` ``is_ok`` guard in Rust -- a
receiver-gone send is a no-op -- has no light asyncio equivalent; in
practice no caller drains after closing, so the case does not arise and is
left unhandled under YAGNI.)

Saturation / overflow
---------------------

Send-overflow is bounded by :data:`RETRY_CAP`: when the retry buffer is
full, a new payload evicts the OLDEST entry (:meth:`collections.deque.popleft`)
rather than growing unbounded. ``donate`` returning ``ok=False`` re-enqueues
the payload at the front (:meth:`collections.deque.appendleft`) and halts the
drain, so an in-order consumer never sees a gap behind a successful send.

Module visibility
-----------------

The crate's ``lib.rs`` declares ``pub(crate) mod donate_pump`` (line 31) --
a crate-private module, NOT re-exported from the barrel (lines 48-71). This
landing matches that: nothing here is re-exported from the package barrel;
callers reach it as ``minimax_code.computer_hub_sdk.donate_pump``. The OTLP
helpers, :data:`PumpMsg` arms and the pump driver are all
crate-private-equivalent: importable by later SDK leaves but not part of the
SDK's public surface.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

__all__ = [
    "PENDING_FLUSHES",
    "RETRY_CAP",
    "PumpMsg",
    "OtlpAnyValue",
    "OtlpKeyValue",
    "OtlpResource",
    "now_unix_nanos",
    "string_value",
    "string_kv",
    "make_resource",
    "drain_via",
    "run_pump",
]

_log = logging.getLogger(__name__)

#: Bound on payloads queued before the pump drains them. Mirrors the Rust
#: ``mpsc::channel(PENDING_FLUSHES)`` capacity -- the producer blocks once
#: this many payloads are pending a drain.
PENDING_FLUSHES: int = 8

#: Payloads retained across failed sends (disconnect/reconnect window).
#: Overflow evicts the OLDEST entry; telemetry is bounded, never unbounded.
RETRY_CAP: int = 8


# ---------------------------------------------------------------------------
# Shared OTLP encoding helpers (pure dataclass mirrors of the protobuf wire).
#
# Reused by the log and metric donation clients so the AnyValue / KeyValue /
# Resource construction lives in one place instead of being copy-pasted per
# client. (trace_donate builds its payload via opentelemetry_sdk's own
# conversion and does not use these -- it lands with the trace client.)
# ---------------------------------------------------------------------------
@dataclass
class OtlpAnyValue:
    """OTLP ``AnyValue`` wire mirror -- a single-tagged value (R136).

    The Rust original uses ``opentelemetry_proto::tonic::common::v1::AnyValue``
    (a protobuf oneof). MiniMax Code has no ``opentelemetry_proto`` dependency,
    so this dataclass mirrors the wire shape the donation clients serialize:
    a dict with a single ``"stringValue"`` key. Only the string arm is needed
    today (the SDK donates string-valued attributes); the structure leaves
    room for ``intValue`` / ``bytesValue`` etc. to land with the client that
    needs them, rather than speculatively here.
    """

    value: dict[str, str]


@dataclass
class OtlpKeyValue:
    """OTLP ``KeyValue`` wire mirror -- a ``(key, value)`` attribute (R136)."""

    key: str
    value: OtlpAnyValue | None = None


@dataclass
class OtlpResource:
    """OTLP ``Resource`` wire mirror -- a bag of identifying attributes (R136)."""

    attributes: list[OtlpKeyValue] = field(default_factory=list)


def now_unix_nanos() -> int:
    """Wall-clock nanoseconds since the Unix epoch (OTLP ``time_unix_nano``).

    Mirrors Rust ``SystemTime::now().duration_since(UNIX_EPOCH)``. Python's
    :func:`time.time_ns` is the faithful equivalent (wall clock, int nanos
    since epoch); the Rust ``unwrap_or(0)`` arm guards against a clock set
    before epoch, which cannot occur on Python, so no fallback is carried.
    """
    return time.time_ns()


def string_value(s: str) -> OtlpAnyValue:
    """OTLP string ``AnyValue`` -- ``{"stringValue": s}`` (R136)."""
    return OtlpAnyValue(value={"stringValue": s})


def string_kv(key: str, value: str) -> OtlpKeyValue:
    """OTLP string-valued ``KeyValue`` attribute (R136)."""
    return OtlpKeyValue(key=key, value=string_value(value))


def make_resource(service_name: str) -> OtlpResource:
    """OTLP ``Resource`` carrying just ``service.name`` (R136)."""
    return OtlpResource(attributes=[string_kv("service.name", service_name)])


# ---------------------------------------------------------------------------
# Pump core.
# ---------------------------------------------------------------------------
@dataclass
class _PayloadMsg:
    """A base64 OTLP request, ready for the wire (R136)."""

    payload: str


@dataclass
class _BarrierMsg:
    """In-order drain fence -- a barrier, not a timeout (R136).

    The pump resolves :attr:`ack` after every payload queued before this
    barrier has had a send attempt, so :func:`drain_via` returning means the
    producer's prior flush is "best-effort delivered".
    """

    ack: asyncio.Future[None]


@dataclass
class _Close:
    """Sentinel: all producers dropped -> pump exits cleanly (R136).

    Mirrors Rust ``mpsc::Receiver::recv() == None`` when every sender is
    dropped. The producer puts this once it is done; the pump returns on
    receipt. (See the module docstring's "Channel-close semantics" section.)
    """


#: Module-level singleton signalling "all producers dropped" (R136). The
#: producer puts this once it is done; the pump returns on receipt. A
#: singleton (not a fresh instance per close) makes the intent explicit and
#: mirrors the conventional asyncio sentinel pattern; ``isinstance`` matching
#: in :func:`run_pump` would accept any :class:`_Close` instance, but a single
#: shared value is clearer at the call site. Rust has no equivalent value --
#: ``mpsc::Receiver::recv()`` returns ``None`` when every sender is dropped --
#: so this singleton IS the asyncio expression of "channel closed".
_CLOSE: _Close = _Close()


#: Union of the three pump messages. The two real arms (:class:`_PayloadMsg`
#: / :class:`_BarrierMsg`) mirror the Rust ``PumpMsg`` enum; :class:`_Close`
#: is the asyncio close-signal (see module docstring).
PumpMsg = _PayloadMsg | _BarrierMsg | _Close


#: Donate closure signature: hands the payload back so a failed send retains
#: it without cloning. Returns ``(ok, payload)`` -- ``ok=False`` re-enqueues
#: ``payload`` at the front of the retry buffer.
Donate = Callable[[str], Awaitable[tuple[bool, str]]]


async def drain_via(tx: asyncio.Queue[PumpMsg]) -> None:
    """Resolve once every payload queued before this call has had a send attempt.

    Call after the producer's flush (e.g. the trace/log SDK's ``flush()``).
    Ships a :class:`_BarrierMsg` and awaits its ack. Mirrors Rust
    ``drain_via``: ``tx.send(Barrier(ack)).await.is_ok()`` gates the
    ``ack_rx.await`` -- a closed pump never gets the barrier, and the caller
    treats the no-resolution as "no more drainage possible".
    """
    loop = asyncio.get_running_loop()
    ack: asyncio.Future[None] = loop.create_future()
    await tx.put(_BarrierMsg(ack))
    try:
        await ack
    except Exception:
        # ``let _ = ack_rx.await`` -- never propagate a cancelled/errored ack.
        pass


async def run_pump(rx: asyncio.Queue[PumpMsg], donate: Donate) -> None:
    """Drive the donation pump until the queue closes (R136).

    Owns the retry buffer (:data:`RETRY_CAP` payloads). Each
    :class:`_PayloadMsg` enters the buffer (evicting the oldest on
    overflow); each :class:`_BarrierMsg` triggers an in-order drain and
    acks the barrier; :class:`_Close` exits the loop. ``donate`` is the
    per-payload send closure, returning ``(ok, payload)`` -- a failed send
    re-enqueues at the front and halts the drain so an in-order consumer
    sees no gap.
    """
    retry: deque[str] = deque()
    while True:
        msg = await rx.get()
        if isinstance(msg, _Close):
            return
        if isinstance(msg, _BarrierMsg):
            await _attempt_sends(retry, donate)
            if not msg.ack.done():
                msg.ack.set_result(None)
            continue
        # _PayloadMsg -- bounded retry buffer, evict oldest on overflow.
        if len(retry) == RETRY_CAP:
            retry.popleft()
            _log.debug("donation retry buffer full; dropping oldest payload")
        retry.append(msg.payload)
        await _attempt_sends(retry, donate)


async def _attempt_sends(retry: deque[str], donate: Donate) -> None:
    """Send in order, stopping at the first failure (R136).

    The remainder stays queued for the next wake (the failed payload is
    re-enqueued at the front via :meth:`collections.deque.appendleft`), so a
    downstream consumer observing successful sends in order never sees a gap
    behind a payload whose send has not yet succeeded.
    """
    while retry:
        payload = retry.popleft()
        ok, payload = await donate(payload)
        if not ok:
            _log.debug("donation send failed; retaining payload for retry")
            retry.appendleft(payload)
            break
