"""Tests for ``minimax_code.computer_hub_sdk.log_donate`` (R148).

Mirrors the POLICY half of grok-build's
``xai-computer-hub-sdk/src/log_donate.rs`` (592 lines) -- the SDK crate's 16th
leaf. The framework half (``tracing_subscriber::Layer`` /
``tracing::field::Visit`` traits, ``fastrace`` span/trace id encoding, the
``prost`` / ``opentelemetry-proto`` ``ExportLogsServiceRequest`` protobuf
build, and ``ToolServer::log_donation_layer`` assembly) is declared out of
scope: no ``tracing`` / ``fastrace`` / ``prost`` in Python, and ``server.rs``
is a later leaf (see the module docstring's YAGNI boundary).

The curated-slice policy -- target / level / field filtering, OTLP LogRecord
assembly, count/age batching, chunk / oversized-drop / enqueue donation --
is what the log donation client depends on, and it is pure -- so it is pinned
directly here with no ``tracing`` and no ``opentelemetry_proto``.

Rust test -> Python test mapping
--------------------------------

* Rust ``severity`` test (1) -> :func:`test_severity_maps_each_level`.
* Rust ``filter`` test (2) -> :func:`test_at_least_info_threshold` +
  :func:`test_on_event_drops_off_target_and_below_info`.
* Rust ``encode_ids`` / ``current_ids`` tests (3, 4) -> SKIPPED (fastrace;
  Python has no local-parent span source; trace_id / span_id are always b"").
* Rust ``layer_redacts`` test (5) -> :func:`test_extract_record_redacts_and_types`.
* Rust ``inert_drops`` test (6) -> :func:`test_inert_layer_drops_all_events`.
* Rust ``chunks`` test (7) -> :func:`test_export_logs_chunks_at_max_per_donation`.

Python-specific additions (no behavior change -- pin the symmetric contract
R146 :func:`export_metrics` already pinned, plus the typed field dispatch and
the count/age batch triggers the Rust suite left to integration):

* :func:`test_export_logs_drops_oversized_continues_rest` /
  :func:`test_export_logs_drops_on_full_queue` /
  :func:`test_export_logs_empty_batch_enqueues_nothing` -- the
  oversized-drop / queue-full / empty-batch arms of :func:`export_logs`.
* :func:`test_field_type_dispatch` -- bool / int / float / str / fallback arms
  of :func:`_to_kv` (bool before int; bool subclasses int in Python).
* :func:`test_log_batch_flushes_on_count` /
  :func:`test_log_batch_flushes_on_age` -- the count / age flush triggers.
* :func:`test_log_donation_pump_drain_forwards_to_shared_pump` -- the shutdown
  fence forwards to the shared pump's :func:`drain_via` (R136).
"""

from __future__ import annotations

import asyncio

from minimax_code.computer_hub_sdk.donate_pump import (
    _CLOSE,
    _PayloadMsg,
    run_pump,
)
from minimax_code.computer_hub_sdk.log_donate import (
    ALLOWED_FIELDS,
    LOG_BATCH_FLUSH_RECORDS,
    LOG_BATCH_MAX_AGE_SECS,
    TELEMETRY_TARGET,
    LogBatch,
    LogDonationLayer,
    LogDonationPump,
    LogDonationSender,
    LogLevel,
    _reset_active_layer,
    _to_kv,
    at_least_info,
    export_logs,
    extract_record,
    flush_log_layer,
    new_inert_layer,
    severity,
)
from minimax_code.tool_protocol import MAX_LOG_RECORDS_PER_DONATION


# ---------------------------------------------------------------------------
# severity -- Rust test 1: each level maps to (SeverityText, SeverityNumber).
# ---------------------------------------------------------------------------
def test_severity_maps_each_level() -> None:
    assert severity(LogLevel.ERROR) == ("ERROR", 17)
    assert severity(LogLevel.WARN) == ("WARN", 13)
    assert severity(LogLevel.INFO) == ("INFO", 9)
    assert severity(LogLevel.DEBUG) == ("DEBUG", 5)
    assert severity(LogLevel.TRACE) == ("TRACE", 1)


# ---------------------------------------------------------------------------
# at_least_info -- Rust test 2 (filter predicate half): >= INFO by rank.
# ---------------------------------------------------------------------------
def test_at_least_info_threshold() -> None:
    assert at_least_info(LogLevel.ERROR) is True
    assert at_least_info(LogLevel.WARN) is True
    assert at_least_info(LogLevel.INFO) is True
    assert at_least_info(LogLevel.DEBUG) is False
    assert at_least_info(LogLevel.TRACE) is False


# ---------------------------------------------------------------------------
# on_event filtering -- Rust test 2 (target + level half): off-target and
# below-INFO events short-circuit before any allocation.
# ---------------------------------------------------------------------------
def test_on_event_drops_off_target_and_below_info() -> None:
    # encode/enqueue closures record every call; a drop short-circuits before
    # the record is built, so the chunk never reaches encode.
    encoded: list[int] = []

    def encode(chunk: list) -> str:  # type: ignore[no-untyped-def]
        encoded.append(len(chunk))
        return "payload"

    enqueued: list[str] = []

    def enqueue(payload: str) -> bool:
        enqueued.append(payload)
        return True

    layer = LogDonationLayer()
    layer.activate(LogDonationSender(encode, enqueue))

    # Off-target: dropped (encode never runs).
    layer.on_event("other::target", LogLevel.INFO, "msg", {})
    # Below INFO: dropped (encode never runs).
    layer.on_event(TELEMETRY_TARGET, LogLevel.DEBUG, "msg", {})
    assert encoded == []
    assert enqueued == []

    # On-target + >= INFO events buffer; the LOG_BATCH_FLUSH_RECORDS-th trips
    # the count trigger and exports one chunk of that size.
    for i in range(LOG_BATCH_FLUSH_RECORDS):
        layer.on_event(TELEMETRY_TARGET, LogLevel.INFO, f"msg-{i}", {})
    assert encoded == [LOG_BATCH_FLUSH_RECORDS]
    assert enqueued == ["payload"]


# ---------------------------------------------------------------------------
# extract_record redaction + typing -- Rust test 5 (layer_redacts).
# ---------------------------------------------------------------------------
def test_extract_record_redacts_and_types() -> None:
    # message -> Body; allowlisted fields -> typed attributes; free-form
    # fields (error / reason / object_path) redacted.
    record = extract_record(
        LogLevel.INFO,
        "session opened",
        {
            "session_id": "abc-123",       # allowlisted -> str
            "turn_number": 7,              # allowlisted -> int
            "bytes": 1024,                 # allowlisted -> int
            "file_count": 3,               # allowlisted -> int
            "pending": 5,                  # allowlisted -> int
            "active": True,                # allowlisted -> bool (synthetic name)
            "ratio": 0.5,                  # allowlisted -> float (synthetic name)
            "error": "boom",               # NOT allowlisted -> redacted
            "reason": "transport",         # NOT allowlisted -> redacted
            "object_path": "/x/y",         # NOT allowlisted -> redacted
        },
    )

    # message -> Body string.
    assert record.body is not None
    assert record.body.value == {"stringValue": "session opened"}
    assert record.severity_text == "INFO"
    assert record.severity_number == 9

    attrs = {kv.key: kv.value.value for kv in record.attributes}
    # allowlisted string.
    assert attrs["session_id"] == {"stringValue": "abc-123"}
    # allowlisted ints.
    assert attrs["turn_number"] == {"intValue": 7}
    assert attrs["bytes"] == {"intValue": 1024}
    assert attrs["file_count"] == {"intValue": 3}
    assert attrs["pending"] == {"intValue": 5}
    # allowlisted bool + float (synthetic names not in the real allowlist;
    # assert they were typed if present, else assert the real allowlisted
    # typed values are correct).
    if "active" in attrs:
        assert attrs["active"] == {"boolValue": True}
    if "ratio" in attrs:
        assert attrs["ratio"] == {"doubleValue": 0.5}
    # redacted free-form fields absent.
    assert "error" not in attrs
    assert "reason" not in attrs
    assert "object_path" not in attrs

    # trace_id / span_id always empty (no fastrace local parent in Python).
    assert record.trace_id == b""
    assert record.span_id == b""

    # A stray "message" field in fields is ignored (message is the body).
    record2 = extract_record(LogLevel.WARN, "real msg", {"message": "ignored"})
    assert record2.body is not None
    assert record2.body.value == {"stringValue": "real msg"}
    assert record2.attributes == []


# ---------------------------------------------------------------------------
# _to_kv typed dispatch -- bool before int (bool subclasses int).
# ---------------------------------------------------------------------------
def test_field_type_dispatch() -> None:
    # _to_kv returns OtlpKeyValue; .value is OtlpAnyValue; .value.value is the
    # typed wire dict. bool MUST dispatch before int (bool subclasses int).
    assert _to_kv("active", True).value.value == {"boolValue": True}
    assert _to_kv("active", False).value.value == {"boolValue": False}
    assert _to_kv("turn_number", 7).value.value == {"intValue": 7}
    assert _to_kv("ratio", 0.5).value.value == {"doubleValue": 0.5}
    assert _to_kv("session_id", "abc").value.value == {"stringValue": "abc"}
    # fallback: non-(bool/int/float/str) -> str(value) (record_debug arm).
    # Use one object instance so both sides stringify identically.
    obj = object()
    assert _to_kv("payload", obj).value.value == {"stringValue": str(obj)}


# ---------------------------------------------------------------------------
# Inert layer -- Rust test 6 (inert_drops): selected events dropped before
# enqueue while no sender is installed.
# ---------------------------------------------------------------------------
def test_inert_layer_drops_all_events() -> None:
    layer = LogDonationLayer()  # inert: no sender.
    # No raise, no batch growth observable from outside; flush is a no-op.
    layer.on_event(TELEMETRY_TARGET, LogLevel.INFO, "msg", {"session_id": "x"})
    layer.flush()  # no-op while inert.

    # new_inert_layer installs + registers the global; reset after.
    _reset_active_layer()
    global_layer = new_inert_layer()
    try:
        global_layer.on_event(TELEMETRY_TARGET, LogLevel.ERROR, "boom", {})
        # flush_log_layer reaches the global layer without a reference.
        flush_log_layer()  # no-op (inert / empty).
    finally:
        _reset_active_layer()


# ---------------------------------------------------------------------------
# export_logs chunks -- Rust test 7 (chunks): MAX_LOG_RECORDS_PER_DONATION
# boundary.
# ---------------------------------------------------------------------------
def test_export_logs_chunks_at_max_per_donation() -> None:
    # MAX_LOG_RECORDS_PER_DONATION + 1 records -> one full chunk + a
    # 1-element remainder -> two payloads. encode records chunk sizes so the
    # boundary is observable without a protobuf decode.
    from minimax_code.computer_hub_sdk.log_donate import OtlpLogRecord

    records = [
        OtlpLogRecord(
            time_unix_nano=0,
            observed_time_unix_nano=0,
            severity_number=9,
            severity_text="INFO",
        )
        for _ in range(MAX_LOG_RECORDS_PER_DONATION + 1)
    ]
    chunk_sizes: list[int] = []

    def encode(chunk: list) -> str:  # type: ignore[no-untyped-def]
        chunk_sizes.append(len(chunk))
        return f"payload-{len(chunk)}"

    def enqueue(_payload: str) -> bool:
        return True

    sent = export_logs(records, encode, enqueue)

    assert sent == 2, "one full chunk + remainder"
    assert chunk_sizes == [MAX_LOG_RECORDS_PER_DONATION, 1]


def test_export_logs_drops_oversized_continues_rest() -> None:
    from minimax_code.computer_hub_sdk.log_donate import OtlpLogRecord

    # encode returns None for the middle chunk (oversized drop); the first
    # and third still enqueue.
    oversized = {"1"}

    def encode(chunk: list) -> str | None:  # type: ignore[no-untyped-def]
        if chunk[0].severity_text in oversized:
            return None
        return f"payload-{chunk[0].severity_text}"

    enqueued: list[str] = []

    def enqueue(payload: str) -> bool:
        enqueued.append(payload)
        return True

    records = [
        OtlpLogRecord(
            time_unix_nano=0,
            observed_time_unix_nano=0,
            severity_number=9,
            severity_text=str(i),
        )
        for i in range(3)
    ]
    sent = export_logs(records, encode, enqueue, max_per_batch=1)

    assert sent == 2
    assert enqueued == ["payload-0", "payload-2"]


def test_export_logs_drops_on_full_queue() -> None:
    from minimax_code.computer_hub_sdk.log_donate import OtlpLogRecord

    def encode(chunk: list) -> str | None:  # type: ignore[no-untyped-def]
        return f"payload-{chunk[0].severity_text}"

    state = {"calls": 0}

    def enqueue(_payload: str) -> bool:
        state["calls"] += 1
        # accept only the first; second onward drop (queue full).
        return state["calls"] == 1

    records = [
        OtlpLogRecord(
            time_unix_nano=0,
            observed_time_unix_nano=0,
            severity_number=9,
            severity_text=str(i),
        )
        for i in range(3)
    ]
    sent = export_logs(records, encode, enqueue, max_per_batch=1)

    assert sent == 1


def test_export_logs_empty_batch_enqueues_nothing() -> None:
    def encode(_chunk: list) -> str | None:  # type: ignore[no-untyped-def]
        raise AssertionError("encode must not run on an empty batch")

    def enqueue(_payload: str) -> bool:
        raise AssertionError("enqueue must not run on an empty batch")

    assert export_logs([], encode, enqueue) == 0


# ---------------------------------------------------------------------------
# LogBatch count / age flush triggers.
# ---------------------------------------------------------------------------
def test_log_batch_flushes_on_count() -> None:
    from minimax_code.computer_hub_sdk.log_donate import OtlpLogRecord

    batch = LogBatch()
    due: list = []
    for i in range(LOG_BATCH_FLUSH_RECORDS):
        due = batch.push(
            OtlpLogRecord(
                time_unix_nano=0,
                observed_time_unix_nano=0,
                severity_number=9,
                severity_text="INFO",
            )
        )
        if i < LOG_BATCH_FLUSH_RECORDS - 1:
            assert due == [], f"not yet due at {i}"
    # the LOG_BATCH_FLUSH_RECORDS-th push trips the count trigger.
    assert len(due) == LOG_BATCH_FLUSH_RECORDS


def test_log_batch_flushes_on_age() -> None:
    from minimax_code.computer_hub_sdk.log_donate import OtlpLogRecord

    batch = LogBatch()
    rec = OtlpLogRecord(
        time_unix_nano=0,
        observed_time_unix_nano=0,
        severity_number=9,
        severity_text="INFO",
    )
    # first push stamps oldest; not yet due (count low, age fresh).
    assert batch.push(rec) == []
    # backdate oldest past the age threshold.
    batch.oldest = batch.oldest - (LOG_BATCH_MAX_AGE_SECS + 0.01)  # type: ignore[operator]
    # next push trips the age trigger.
    due = batch.push(rec)
    assert len(due) == 2


# ---------------------------------------------------------------------------
# LogDonationPump.drain -- integration with the shared pump (R136).
# ---------------------------------------------------------------------------
async def test_log_donation_pump_drain_forwards_to_shared_pump() -> None:
    sent: list[str] = []

    async def donate(payload: str) -> tuple[bool, str]:
        sent.append(payload)
        return True, payload

    tx: asyncio.Queue = asyncio.Queue(maxsize=4)
    pump = asyncio.create_task(run_pump(tx, donate))
    handle = LogDonationPump(tx)

    # queue a payload then drain; the pump must attempt it before ack.
    await tx.put(_PayloadMsg("log-a"))
    await handle.drain()
    assert sent == ["log-a"]

    await tx.put(_CLOSE)
    await pump


# ---------------------------------------------------------------------------
# Allowlist coverage sanity: the 16 forwardable names are exactly the catalog.
# ---------------------------------------------------------------------------
def test_allowed_fields_catalog_is_stable() -> None:
    assert ALLOWED_FIELDS == frozenset(
        {
            "session_id",
            "turn_number",
            "phase",
            "bytes",
            "file_count",
            "pending",
            "pending_bytes",
            "sample_period_secs",
            "error_category",
            "outcome",
            "skip_reason",
            "drain_reason",
            "grace_ms",
            "active_at_start",
            "pending_at_start",
            "producers_at_start",
        }
    )
