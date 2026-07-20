"""Tests for ``minimax_code.computer_hub_sdk.metric_donate`` (R146).

Mirrors the POLICY half of grok-build's
``xai-computer-hub-sdk/src/metric_donate.rs`` (the framework half -- the
process-global ``ACTIVE_METRIC_EXPORTER``, ``prometheus::gather``, the
periodic ``MetricDonationReporter``, ``ExportMetricsServiceRequest`` protobuf
encode, and ``ToolServer::metric_donation_reporter`` assembly -- is declared
out of scope: no prometheus_client, no opentelemetry-proto/prost in Python,
and ``server.rs`` is a later leaf; see the module docstring's YAGNI boundary).

The Prometheus -> OTLP conversion + chunk/encode/enqueue donation policy is
what the metric donation client depends on, and it is pure -- so it is pinned
directly here with no ``prometheus::Registry`` and no ``opentelemetry_proto``:

* :func:`convert_families` -- Counter->Sum / Gauge->Gauge / Histogram->
  Histogram (cumulative, per-bucket differenced) / Summary+Untyped skip;
  labels preserved;
* :func:`export_metrics` -- chunk at ``MAX_METRICS_PER_DONATION``, oversized
  chunks skipped, queue-full drops, rest enqueued, count returned;
* :class:`MetricDonationPump.drain` -- forwards to the shared pump's
  :func:`drain_via`.

Python-specific adjustments (no behavior change):

* Rust ``prometheus::{IntCounterVec, IntGauge, Histogram, Registry}`` ->
  neutral :class:`PromMetricFamily` / :class:`PromMetric` /
  :class:`PromHistogram` / :class:`PromBucket` / :class:`PromLabel`
  dataclasses (the conversion accepts these stand-ins instead of the real
  registry). A Rust ``IntCounterVec`` labeled ``reason=zdr`` with value 5
  becomes ``PromMetricFamily(field_type=COUNTER, metric=(PromMetric(labels=(
  PromLabel("reason","zdr"),), value=5.0),))``.
* Rust ``match metric.data { metric::Data::Sum(sum) => .. }`` ->
  ``isinstance(metric.data, OtlpSum)`` (the frozen-dataclass + Union
  translation of the protobuf oneof).
* Rust ``label_map`` extracts ``KeyValue.value.as_ref().and_then(
  |v| v.value.clone())`` of the ``AnyValue::StringValue`` arm -> the
  :class:`OtlpKeyValue` (R136) access path ``kv.value.value["stringValue"]``
  (``OtlpAnyValue.value`` is the ``{"stringValue": s}`` dict).
* Rust ``decode(payload)`` (base64 + prost ``ExportMetricsServiceRequest``) ->
  the ``encode_chunk`` closure records chunk sizes directly (Python has no
  protobuf decode in scope); the chunk-boundary assertion
  ``[MAX_METRICS_PER_DONATION, 1]`` replaces the Rust total-count decode.
* Rust ``saturating_sub`` cumulative differencing is exercised by the
  histogram bucket_counts assertion ``[1, 1, 1]`` (per-bucket + ``+Inf``).
"""

from __future__ import annotations

import asyncio

from minimax_code.computer_hub_sdk.donate_pump import (
    _CLOSE,
    _PayloadMsg,
    run_pump,
)
from minimax_code.computer_hub_sdk.metric_donate import (
    AGGREGATION_TEMPORALITY_CUMULATIVE,
    MetricDonationPump,
    OtlpGauge,
    OtlpHistogram,
    OtlpMetric,
    OtlpSum,
    PromBucket,
    PromHistogram,
    PromLabel,
    PromMetric,
    PromMetricFamily,
    PromMetricType,
    convert_families,
    export_metrics,
)
from minimax_code.tool_protocol import MAX_METRICS_PER_DONATION


def _counter_family() -> PromMetricFamily:
    return PromMetricFamily(
        name="grok_test_total",
        field_type=PromMetricType.COUNTER,
        metric=(
            PromMetric(
                labels=(PromLabel("reason", "zdr"),),
                value=5.0,
            ),
        ),
    )


def _gauge_family() -> PromMetricFamily:
    return PromMetricFamily(
        name="grok_test_pending",
        field_type=PromMetricType.GAUGE,
        metric=(PromMetric(labels=(), value=7.0),),
    )


def _histogram_family() -> PromMetricFamily:
    # hist.observe(0.25); hist.observe(0.75); hist.observe(5.0)
    # buckets=[0.5, 1.0] -> cumulative (<=0.5)=1, (<=1.0)=2 ; sample_count=3, sum=6.0
    return PromMetricFamily(
        name="grok_test_seconds",
        field_type=PromMetricType.HISTOGRAM,
        metric=(
            PromMetric(
                labels=(),
                value=PromHistogram(
                    sample_count=3,
                    sample_sum=6.0,
                    buckets=(
                        PromBucket(upper_bound=0.5, cumulative_count=1),
                        PromBucket(upper_bound=1.0, cumulative_count=2),
                    ),
                ),
            ),
        ),
    )


def _label_map(attrs) -> dict[str, str]:  # type: ignore[no-untyped-def]
    # OtlpKeyValue.value.value["stringValue"] -- the R136 OtlpAnyValue dict.
    return {
        kv.key: kv.value.value["stringValue"]
        for kv in attrs
        if kv.value is not None
    }


# ---------------------------------------------------------------------------
# convert_families -- Counter -> Sum (monotonic, cumulative), label preserved
# ---------------------------------------------------------------------------
def test_convert_families_counter_gauge_histogram_with_labels() -> None:
    metrics = convert_families([_counter_family(), _gauge_family(), _histogram_family()])
    by_name = {m.name: m for m in metrics}

    # Counter -> Sum (monotonic, cumulative), label preserved.
    counter = by_name["grok_test_total"]
    assert isinstance(counter.data, OtlpSum)
    assert counter.data.is_monotonic is True
    assert counter.data.aggregation_temporality == AGGREGATION_TEMPORALITY_CUMULATIVE
    cdp = counter.data.data_points[0]
    assert cdp.value == 5.0
    assert _label_map(cdp.attributes).get("reason") == "zdr"

    # Gauge -> Gauge.
    gauge = by_name["grok_test_pending"]
    assert isinstance(gauge.data, OtlpGauge)
    assert gauge.data.data_points[0].value == 7.0

    # Histogram -> Histogram with cumulative buckets differenced and a +Inf
    # bucket appended.
    hist = by_name["grok_test_seconds"]
    assert isinstance(hist.data, OtlpHistogram)
    assert hist.data.aggregation_temporality == AGGREGATION_TEMPORALITY_CUMULATIVE
    hdp = hist.data.data_points[0]
    assert hdp.count == 3
    assert hdp.sum == 6.0
    assert hdp.explicit_bounds == (0.5, 1.0)
    # (<=0.5): 0.25 -> 1 ; (0.5,1.0]: 0.75 -> 1 ; (+Inf): 5.0 -> 1
    assert hdp.bucket_counts == (1, 1, 1)


# ---------------------------------------------------------------------------
# convert_families -- SUMMARY / UNTYPED skipped, empty -> empty
# ---------------------------------------------------------------------------
def test_summary_and_untyped_families_are_skipped() -> None:
    # An empty family list converts to nothing -- mirrors Rust's empty-registry test.
    assert convert_families([]) == []

    # SUMMARY / UNTYPED families are skipped defensively (none registered today).
    summary = PromMetricFamily(
        name="grok_summary",
        field_type=PromMetricType.SUMMARY,
        metric=(PromMetric(labels=(), value=0.0),),
    )
    untyped = PromMetricFamily(
        name="grok_untyped",
        field_type=PromMetricType.UNTYPED,
        metric=(PromMetric(labels=(), value=0.0),),
    )
    assert convert_families([summary, untyped]) == []


# ---------------------------------------------------------------------------
# export_metrics -- chunks at MAX_METRICS_PER_DONATION
# ---------------------------------------------------------------------------
def test_export_metrics_chunks_at_max_per_donation() -> None:
    # MAX_METRICS_PER_DONATION + 1 metrics -> one full chunk + a 1-element
    # remainder -> two payloads. encode_chunk records each chunk's size so the
    # boundary is observable without a protobuf decode (Rust decodes the
    # payload; Python has no prost in scope).
    metrics = [
        OtlpMetric(name=f"m{i}", data=OtlpGauge(data_points=()))        for i in range(MAX_METRICS_PER_DONATION + 1)
    ]

    chunk_sizes: list[int] = []

    def encode(chunk: list) -> str | None:  # type: ignore[no-untyped-def]
        chunk_sizes.append(len(chunk))
        return f"payload-{len(chunk)}"

    def enqueue(_payload: str) -> bool:
        return True

    sent = export_metrics(metrics, encode, enqueue)

    assert sent == 2, "one full chunk + remainder"
    assert chunk_sizes == [MAX_METRICS_PER_DONATION, 1]


def test_export_metrics_drops_oversized_continues_rest() -> None:
    # encode_chunk returns None for the middle chunk (oversized drop); the
    # first and third still enqueue.
    oversized = {"1"}

    def encode(chunk: list) -> str | None:  # type: ignore[no-untyped-def]
        if chunk[0].name in oversized:
            return None
        return f"payload-{chunk[0].name}"

    enqueued: list[str] = []

    def enqueue(payload: str) -> bool:
        enqueued.append(payload)
        return True

    metrics = [
        OtlpMetric(name=str(i), data=OtlpGauge(data_points=()))        for i in range(3)
    ]
    sent = export_metrics(metrics, encode, enqueue, max_per_batch=1)

    assert sent == 2
    assert enqueued == ["payload-0", "payload-2"]


def test_export_metrics_drops_on_full_queue() -> None:
    def encode(chunk: list) -> str | None:  # type: ignore[no-untyped-def]
        return f"payload-{chunk[0].name}"

    state = {"calls": 0}

    def enqueue(_payload: str) -> bool:
        state["calls"] += 1
        # accept only the first; second onward drop (queue full).
        return state["calls"] == 1

    metrics = [
        OtlpMetric(name=str(i), data=OtlpGauge(data_points=()))        for i in range(3)
    ]
    sent = export_metrics(metrics, encode, enqueue, max_per_batch=1)

    assert sent == 1


def test_export_metrics_empty_batch_enqueues_nothing() -> None:
    def encode(_chunk: list) -> str | None:  # type: ignore[no-untyped-def]
        raise AssertionError("encode must not run on an empty batch")

    def enqueue(_payload: str) -> bool:
        raise AssertionError("enqueue must not run on an empty batch")

    assert export_metrics([], encode, enqueue) == 0


# ---------------------------------------------------------------------------
# MetricDonationPump.drain -- integration with the shared pump (R136).
# ---------------------------------------------------------------------------
async def test_metric_donation_pump_drain_forwards_to_shared_pump() -> None:
    sent: list[str] = []

    async def donate(payload: str) -> tuple[bool, str]:
        sent.append(payload)
        return True, payload

    tx: asyncio.Queue = asyncio.Queue(maxsize=4)
    pump = asyncio.create_task(run_pump(tx, donate))
    handle = MetricDonationPump(tx)

    # queue a payload then drain; the pump must attempt it before ack.
    await tx.put(_PayloadMsg("metric-a"))
    await handle.drain()
    assert sent == ["metric-a"]

    await tx.put(_CLOSE)
    await pump
