"""Metric donation: Prometheus -> OTLP conversion + donation policy (R146).

Fusion of grok-build's ``xai-computer-hub-sdk/src/metric_donate.rs`` (396
lines) -- the SDK crate's 14th leaf (after R133 error / R134 handshake /
R135 refcount / R136 donate_pump / R137 trace_donate / R138 connection_borrow
/ R139 auth / R140 observability / R141 cancel / R142 admission / R143 pool /
R144 notification / R145 oidc_provider).

The metric-side client of the shared donation pump (R136). A
``MetricDonationReporter`` periodically snapshots the process's Prometheus
registry, converts each ``MetricFamily`` to native OTLP metrics
(Counter->Sum, Gauge->Gauge, Histogram->Histogram, labels preserved,
cumulative temporality), and pumps the batch over the shared
:mod:`~minimax_code.computer_hub_sdk.donate_pump` substrate. Because it
gathers the whole registry, every current and future metric is exported
with zero per-metric wiring. Metrics are process-aggregate -- the reporter
requires no bound session.

What migrates vs what does NOT (R132/R137-style YAGNI boundary declaration)
-------------------------------------------------------------------------

The Rust original is half pure-conversion-policy, half Prometheus + OTLP +
tokio framework glue. MiniMax Code has no ``prometheus`` crate equivalent
(no ``prometheus_client`` dependency), no ``opentelemetry-proto`` / ``prost``
dependency, and the periodic-gatherer + process-global exporter is wired to
``ToolServer`` (``server.rs``, a later leaf). The framework-glue half is
declared out of scope here rather than ported into dead code -- exactly the
boundary R137 drew for the symmetric trace-donation client.

MIGRATED (transport-agnostic pure policy + neutral data shapes):

* Neutral Prometheus-shape dataclasses (:class:`PromMetricType`,
  :class:`PromLabel`, :class:`PromBucket`, :class:`PromHistogram`,
  :class:`PromMetric`, :class:`PromMetricFamily`) -- stand-ins for
  ``prometheus::proto::{MetricFamily, MetricType, LabelPair, Metric}`` so the
  conversion runs with no ``prometheus_client`` dependency. A caller that
  gathers a real registry adapts it into these shapes before
  :func:`convert_families`.
* Neutral OTLP-shape dataclasses (:class:`OtlpMetric`, :class:`OtlpSum`,
  :class:`OtlpGauge`, :class:`OtlpHistogram`, :class:`OtlpNumberPoint`,
  :class:`OtlpHistogramPoint`) -- stand-ins for
  ``opentelemetry_proto::tonic::metrics::v1::*`` so the conversion emits no
  protobuf. :class:`~minimax_code.computer_hub_sdk.donate_pump.OtlpKeyValue`
  is reused for label attributes.
* :data:`AGGREGATION_TEMPORALITY_CUMULATIVE` -- the OTLP enum int
  (``AggregationTemporality::Cumulative as i32``).
* :func:`convert_families` -- Counter->Sum (monotonic, cumulative) /
  Gauge->Gauge / Histogram->Histogram (cumulative) / Summary+Untyped skip;
  labels preserved. The core algorithm.
* :func:`_histogram_point` -- the cumulative-bucket differencing + implicit
  ``+Inf`` bucket append (Prometheus buckets are cumulative ``le`` counts;
  OTLP wants per-bucket counts).
* :func:`export_metrics` -- the donate-export loop: chunk ->
  encode -> drop-oversized -> enqueue (drop on full). Parameterized over an
  ``encode_chunk`` closure (returns the base64 payload or ``None`` for an
  oversized chunk) so it has no prost/protobuf dependency; returns the count
  of payloads enqueued. Mirrors ``MetricExporter::export``.
* :class:`MetricDonationPump` -- shutdown drain fence; thin handle over
  :func:`~minimax_code.computer_hub_sdk.donate_pump.drain_via`.

NOT MIGRATED (framework glue with no Python equivalent):

* ``ACTIVE_METRIC_EXPORTER`` process-global (``ArcSwapOption<MetricExporter>``)
  -- needs a real registry + a live pump ``tx``.
* Module-level :func:`gather_and_send` (Rust) -- ``prometheus::gather()``
  process-global registry snapshot. No ``prometheus_client`` in Python.
* ``clear_active_exporter`` -- teardown pairing for the global.
* ``MetricDonationReporter::run`` -- the periodic gatherer
  (``tokio::time::interval`` + ``CancellationToken``). Its gather step needs
  the real registry; the periodic-loop geometry is just
  ``asyncio.create_task`` over a sleep loop, deferred to the leaf that owns
  the registry.
* ``ToolServer::metric_donation_reporter`` -- the assembly point.
  ``ToolServer`` (``server.rs``, 2649 lines) is a later leaf. Its
  spawn-pump + donate-closure geometry is ``run_pump(rx, donate)`` over an
  :class:`asyncio.Queue` with ``donate`` returning ``(ok, payload)`` --
  identical to R136's pump tests; that leaf wires it to
  ``server.donate_metrics``.
* ``ExportMetricsServiceRequest`` protobuf encode + ``request.encode_to_vec``
  + base64 -- ``prost`` / ``opentelemetry_proto`` transforms. The encoding
  is the ``encode_chunk`` closure a caller supplies; the protobuf machinery
  is the caller's concern (R137 declared the same boundary for trace
  donation, R136 for the OTLP wire helpers).

Python-specific adaptations (no behavior change)
------------------------------------------------

* ``prometheus::proto::{MetricFamily, MetricType, LabelPair, Metric,
  Bucket}`` -> the neutral :class:`PromMetricFamily` /
  :class:`PromMetricType` / :class:`PromLabel` / :class:`PromMetric` /
  :class:`PromBucket` dataclasses. A :class:`PromMetric` carries its labels
  plus a single ``value: float | PromHistogram`` union; the family's
  ``field_type`` decides how to read it (Counter/Gauge -> ``float``,
  Histogram -> :class:`PromHistogram`), mirroring Rust's
  ``match family.get_field_type()`` dispatch onto
  ``m.get_counter().value()`` / ``m.get_gauge().value()`` /
  ``m.get_histogram()``.
* ``opentelemetry_proto::tonic::metrics::v1::*`` (Metric oneof Sum/Gauge/
  Histogram, NumberDataPoint, HistogramDataPoint) -> neutral OTLP-shape
  dataclasses. ``aggregation_temporality`` is the int enum value
  (:data:`AGGREGATION_TEMPORALITY_CUMULATIVE`), not the protobuf enum.
* ``AggregationTemporality::Cumulative as i32`` -> ``2``.
* ``prost::Message::encode_to_vec`` + ``base64::encode`` -> the
  ``encode_chunk`` closure a caller supplies (returns the base64 payload or
  ``None`` for an oversized chunk). Mirrors R137's ``export_spans`` seam.
* ``mpsc::Sender::try_send`` -> the caller's ``enqueue`` closure (typically
  :func:`~minimax_code.computer_hub_sdk.trace_donate.try_enqueue`, which
  wraps ``asyncio.Queue.put_nowait`` + ``QueueFull``).
* ``saturating_sub`` -> ``max(0, a - b)``: a cumulative count never underflows
  below zero even if the registry briefly reports a smaller cumulative
  (defensive, matches Rust's ``u64::saturating_sub``).
* ``tracing::debug!`` -> :func:`logging.debug`.
* ``make_resource`` / ``now_unix_nanos`` / ``string_kv`` -> reused from R136
  (:mod:`donate_pump`); ``chunk_donation_batches`` reused from R137
  (:mod:`trace_donate`) -- the donation clients share one encoding surface.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from minimax_code.computer_hub_sdk.donate_pump import (
    OtlpKeyValue,
    PumpMsg,
    drain_via,
    now_unix_nanos,
    string_kv,
)
from minimax_code.computer_hub_sdk.trace_donate import chunk_donation_batches
from minimax_code.tool_protocol import MAX_METRICS_PER_DONATION

__all__ = [
    # Prometheus-shape inputs (neutral stand-ins for prometheus::proto::*).
    "PromMetricType",
    "PromLabel",
    "PromBucket",
    "PromHistogram",
    "PromMetric",
    "PromMetricFamily",
    # OTLP-shape outputs (neutral stand-ins for opentelemetry_proto metrics).
    "OtlpMetric",
    "OtlpSum",
    "OtlpGauge",
    "OtlpHistogram",
    "OtlpNumberPoint",
    "OtlpHistogramPoint",
    "AGGREGATION_TEMPORALITY_CUMULATIVE",
    # Core conversion + donation policy.
    "convert_families",
    "export_metrics",
    # Drain fence.
    "MetricDonationPump",
]

_log = logging.getLogger(__name__)

#: OTLP ``AggregationTemporality::Cumulative`` (``as i32``). Prometheus
#: counters and histograms are cumulative (a snapshot is the running total
#: since process start), so every converted Sum/Histogram carries this
#: temporality; the server re-stamps attribution.
AGGREGATION_TEMPORALITY_CUMULATIVE: int = 2


# ---------------------------------------------------------------------------
# Prometheus-shape inputs (neutral mirrors of prometheus::proto::*).
#
# MiniMax Code has no prometheus_client dependency, so the conversion accepts
# these neutral dataclasses instead. A caller that gathers a real registry
# adapts each MetricFamily into a PromMetricFamily before convert_families.
# ---------------------------------------------------------------------------
class PromMetricType(Enum):
    """The kind of a :class:`PromMetricFamily` (``prometheus::proto::MetricType``)."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"
    UNTYPED = "untyped"


@dataclass(frozen=True)
class PromLabel:
    """A Prometheus label pair (``prometheus::proto::LabelPair``)."""

    name: str
    value: str


@dataclass(frozen=True)
class PromBucket:
    """A cumulative Prometheus histogram bucket (``prometheus::proto::Bucket``).

    ``cumulative_count`` is the running count of observations ``<=
    upper_bound`` (Prometheus buckets are cumulative ``le`` counts, NOT
    per-bucket); :func:`_histogram_point` differences them into OTLP
    per-bucket counts.
    """

    upper_bound: float
    cumulative_count: int


@dataclass(frozen=True)
class PromHistogram:
    """A Prometheus histogram value (``prometheus::proto::Histogram``)."""

    sample_count: int
    sample_sum: float
    buckets: tuple[PromBucket, ...]


@dataclass(frozen=True)
class PromMetric:
    """One labeled sample in a family (``prometheus::proto::Metric``).

    ``value`` is the union of the protobuf oneof arms this SDK converts:
    ``float`` for Counter/Gauge families, :class:`PromHistogram` for
    Histogram families. Summary/Untyped families are skipped, so their value
    shape is never read. The family's :attr:`PromMetricFamily.field_type`
    decides how to interpret ``value`` (mirrors Rust's
    ``match family.get_field_type()`` dispatch).
    """

    labels: tuple[PromLabel, ...]
    value: float | PromHistogram


@dataclass(frozen=True)
class PromMetricFamily:
    """A gathered metric family (``prometheus::proto::MetricFamily``)."""

    name: str
    field_type: PromMetricType
    metric: tuple[PromMetric, ...]


# ---------------------------------------------------------------------------
# OTLP-shape outputs (neutral mirrors of opentelemetry_proto::tonic::metrics).
#
# No opentelemetry-proto/prost dependency: these dataclasses mirror the wire
# shapes convert_families produces. A caller that donates over the wire
# serializes them (the encode_chunk closure); the shapes themselves carry no
# protobuf machinery.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OtlpNumberPoint:
    """OTLP ``NumberDataPoint`` -- a Counter/Gauge reading (R146)."""

    attributes: tuple[OtlpKeyValue, ...]
    time_unix_nano: int
    value: float


@dataclass(frozen=True)
class OtlpHistogramPoint:
    """OTLP ``HistogramDataPoint`` -- per-bucket counts + bounds (R146).

    ``bucket_counts`` are per-bucket (NOT cumulative); ``explicit_bounds`` are
    the bucket upper bounds; the implicit ``+Inf`` bucket's count is the last
    entry of ``bucket_counts``. Mirrors the OTLP histogram wire shape
    :func:`_histogram_point` produces.
    """

    attributes: tuple[OtlpKeyValue, ...]
    time_unix_nano: int
    count: int
    sum: float | None
    bucket_counts: tuple[int, ...]
    explicit_bounds: tuple[float, ...]


@dataclass(frozen=True)
class OtlpSum:
    """OTLP ``Sum`` -- a monotonic Counter export (R146)."""

    data_points: tuple[OtlpNumberPoint, ...]
    aggregation_temporality: int
    is_monotonic: bool


@dataclass(frozen=True)
class OtlpGauge:
    """OTLP ``Gauge`` -- a Gauge export (R146)."""

    data_points: tuple[OtlpNumberPoint, ...]


@dataclass(frozen=True)
class OtlpHistogram:
    """OTLP ``Histogram`` -- a Histogram export (R146)."""

    data_points: tuple[OtlpHistogramPoint, ...]
    aggregation_temporality: int


@dataclass(frozen=True)
class OtlpMetric:
    """OTLP ``Metric`` -- one named metric + its data oneof (R146)."""

    name: str
    data: OtlpSum | OtlpGauge | OtlpHistogram


# ---------------------------------------------------------------------------
# Conversion helpers (pure -- no prometheus/OTLP framework types).
# ---------------------------------------------------------------------------
def _labels_to_kv(labels: Sequence[PromLabel]) -> tuple[OtlpKeyValue, ...]:
    """Prometheus label pairs -> OTLP ``KeyValue`` attributes (R146).

    Mirrors Rust ``labels_to_kv``: each ``LabelPair`` becomes a string-valued
    :class:`~minimax_code.computer_hub_sdk.donate_pump.OtlpKeyValue` via the
    shared :func:`~minimax_code.computer_hub_sdk.donate_pump.string_kv`
    helper (R136).
    """
    return tuple(string_kv(label.name, label.value) for label in labels)


def _number_point(metric: PromMetric, value: float, now: int) -> OtlpNumberPoint:
    """Build an OTLP ``NumberDataPoint`` for a Counter/Gauge reading (R146).

    Mirrors Rust ``number_point``: the metric's labels become attributes, the
    snapshot instant is ``now`` (shared across the whole conversion so a
    batch is timestamp-coherent), and the value is the double reading.
    """
    return OtlpNumberPoint(
        attributes=_labels_to_kv(metric.labels),
        time_unix_nano=now,
        value=value,
    )


def _histogram_point(metric: PromMetric, now: int) -> OtlpHistogramPoint:
    """Build an OTLP ``HistogramDataPoint`` with cumulative buckets differenced (R146).

    Prometheus histogram buckets are cumulative (``le`` counts: bucket[i] is
    the count of observations ``<= explicit_bounds[i]``); OTLP wants
    per-bucket counts plus an implicit ``+Inf`` bucket. The cumulative counts
    are differenced here (``saturating_sub`` -> ``max(0, ..)`` so a briefly
    shrinking cumulative never underflows), and the total sample count minus
    the last explicit bucket's cumulative becomes the ``+Inf`` bucket count.

    Mirrors Rust ``histogram_point``.
    """
    hist = metric.value  # contract: Histogram-family metric -> PromHistogram
    bucket_counts: list[int] = []
    explicit_bounds: list[float] = []
    prev = 0
    for bucket in hist.buckets:
        cumulative = bucket.cumulative_count
        bucket_counts.append(max(0, cumulative - prev))
        explicit_bounds.append(bucket.upper_bound)
        prev = cumulative
    total = hist.sample_count
    bucket_counts.append(max(0, total - prev))
    return OtlpHistogramPoint(
        attributes=_labels_to_kv(metric.labels),
        time_unix_nano=now,
        count=total,
        sum=hist.sample_sum,
        bucket_counts=tuple(bucket_counts),
        explicit_bounds=tuple(explicit_bounds),
    )


def convert_families(families: Sequence[PromMetricFamily]) -> list[OtlpMetric]:
    """Convert gathered Prometheus families to OTLP metrics (R146).

    Counter -> :class:`OtlpSum` (``is_monotonic=True``, cumulative
    temporality); Gauge -> :class:`OtlpGauge`; Histogram ->
    :class:`OtlpHistogram` (cumulative temporality, per-bucket counts);
    Summary / Untyped are skipped defensively (none registered today). The
    snapshot instant ``now`` is shared across every data point so a batch is
    timestamp-coherent (mirrors Rust ``now_unix_nanos()`` called once per
    convert).

    Mirrors Rust ``convert_families``.
    """
    now = now_unix_nanos()
    cumulative = AGGREGATION_TEMPORALITY_CUMULATIVE
    out: list[OtlpMetric] = []
    for family in families:
        name = family.name
        ftype = family.field_type
        if ftype == PromMetricType.COUNTER:
            data: OtlpSum | OtlpGauge | OtlpHistogram = OtlpSum(
                data_points=tuple(
                    _number_point(m, float(m.value), now) for m in family.metric
                ),
                aggregation_temporality=cumulative,
                is_monotonic=True,
            )
        elif ftype == PromMetricType.GAUGE:
            data = OtlpGauge(
                data_points=tuple(
                    _number_point(m, float(m.value), now) for m in family.metric
                ),
            )
        elif ftype == PromMetricType.HISTOGRAM:
            data = OtlpHistogram(
                data_points=tuple(_histogram_point(m, now) for m in family.metric),
                aggregation_temporality=cumulative,
            )
        else:
            # SUMMARY | UNTYPED -> skip defensively.
            continue
        out.append(OtlpMetric(name=name, data=data))
    return out


# ---------------------------------------------------------------------------
# Donation export policy (transport-agnostic; encode is the caller's closure).
# ---------------------------------------------------------------------------
def export_metrics(
    metrics: Sequence[OtlpMetric],
    encode_chunk: Callable[[list[OtlpMetric]], str | None],
    enqueue: Callable[[str], bool],
    max_per_batch: int = MAX_METRICS_PER_DONATION,
) -> int:
    """Run the donate-export loop: chunk -> encode -> drop-oversized -> enqueue (R146).

    For each chunk of at most :data:`MAX_METRICS_PER_DONATION`:

    * call ``encode_chunk``; ``None`` means the chunk's wire size exceeds
      ``MAX_DONATION_BYTES`` -> drop it (continue to the next chunk);
    * otherwise hand the payload to ``enqueue``; ``False`` means the pump's
      bounded queue is full -> drop it.

    Returns the count of payloads successfully enqueued. Mirrors
    ``MetricExporter::export``: oversized chunks are skipped but the loop
    keeps draining subsequent chunks; a full queue drops the batch without
    propagating. Telemetry, never correctness. The symmetric trace-side
    loop is :func:`~minimax_code.computer_hub_sdk.trace_donate.export_spans`
    (R137) -- the two donation clients each own their export loop in the
    Rust original; both reduce to this same chunk/encode/enqueue policy.
    """
    sent = 0
    for chunk in chunk_donation_batches(metrics, max_per_batch):
        payload = encode_chunk(chunk)
        if payload is None:
            _log.debug("dropping oversized metric donation payload")
            continue
        if enqueue(payload):
            sent += 1
        else:
            _log.debug("metric donation queue full; dropping metric batch")
    return sent


@dataclass
class MetricDonationPump:
    """Shutdown fence: drain queued metric donations before close (R146).

    Thin handle over the shared pump's :func:`drain_via`. Call :meth:`drain`
    after a final registry gather so in-flight metric batches get a send
    attempt before the connection tears down. Mirrors Rust
    ``MetricDonationPump { tx }`` whose ``drain(&self)`` is
    ``crate::donate_pump::drain_via(&self.tx).await``.
    """

    tx: asyncio.Queue[PumpMsg]

    async def drain(self) -> None:
        """Resolve once every payload queued before this call has had a send attempt."""
        await drain_via(self.tx)
