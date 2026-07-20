"""Curated log donation client: filter, redact, batch, chunk (R148).

Fusion of grok-build's ``xai-computer-hub-sdk/src/log_donate.rs`` (592 lines) --
the SDK crate's 16th leaf (after R133 error / R134 handshake / R135 refcount /
R136 donate_pump / R137 trace_donate / R138 connection_borrow / R139 auth /
R140 observability / R141 cancel / R142 admission / R143 pool / R144
notification / R145 oidc_provider / R146 metric_donate / R147 metrics).

The Rust original is a ``tracing_subscriber::Layer`` that forwards a *curated
slice* of ``tracing`` events to the connected server over the WebSocket
transport (``logs.donate``). Three filters narrow the slice:

* **Target** -- only events on ``workspace::telemetry`` are forwarded (the
  workspace's stable telemetry target; global ``RUST_LOG`` is ignored).
* **Level** -- only ``>= INFO`` (ERROR / WARN / INFO); DEBUG / TRACE are
  dropped. Tracing orders ``ERROR < WARN < INFO < DEBUG < TRACE``, so this is
  ``level <= INFO``.
* **Fields** -- only the 16 allowlisted names are forwarded; free-form fields
  such as ``error`` / ``reason`` / ``object_path`` / ``gcs_path`` are redacted
  (they may carry secrets or user paths).

A ``tracing::field::Visit`` (``AllowlistVisitor``) walks each event: the
``message`` field becomes the OTLP ``Body``, allowlisted fields become OTLP
``KeyValue`` attributes (typed by arm -- str / i64 / u64 / bool / f64), and
everything else is dropped. Records buffer in a ``LogBatch`` that flushes on
count (32) or age (2s); a ``PumpLogExporter`` chunks at
``MAX_LOG_RECORDS_PER_DONATION``, drops payloads over ``MAX_DONATION_BYTES``,
base64-encodes, and ``try_send``s onto the shared pump. A ``DonatingLogLayer``
is installed **inert** at startup and activated post-connect by swapping in a
``LogDonationSender``; a process-global ``ACTIVE_LOG_LAYER`` lets
``flush_log_layer()`` drive a teardown flush without a reference.

What migrates vs what does NOT (YAGNI boundary)
-----------------------------------------------

MiniMax Code has no ``tracing`` / ``tracing-subscriber`` ecosystem, no
``fastrace`` (the local-parent span source), and no ``prost`` /
``opentelemetry-proto`` protobuf encoder. The Layer + Visit traits, the
fastrace id encoding, and the protobuf ``ExportLogsServiceRequest`` build are
therefore framework glue with no Python equivalent. But the module's *value*
is the **pure policy logic** sitting between the tracing event and the wire --
target / level / field filtering, the OTLP ``LogRecord`` assembly, the
count/age batching, and the chunk / oversized-drop / enqueue donation policy
-- and that is fully transport-agnostic. R148 migrates the policy and declares
the glue out of scope.

MIGRATED (transport-agnostic policy):

* :data:`TELEMETRY_TARGET` -- the stable target the layer selects on.
* :data:`ALLOWED_FIELDS` -- the 16-name forwardable field allowlist (the
  redaction boundary).
* :data:`LOG_BATCH_FLUSH_RECORDS` / :data:`LOG_BATCH_MAX_AGE_SECS` -- the
  count / age flush triggers.
* :class:`LogLevel` -- the 5 tracing levels (ERROR / WARN / INFO / DEBUG /
  TRACE) as a ``StrEnum``, carrying the ``ERROR < WARN < INFO < DEBUG < TRACE``
  ordering that ``at_least_info`` depends on.
* :func:`severity` / :func:`at_least_info` -- the level -> OTLP
  ``(SeverityText, SeverityNumber)`` map and the ``>= INFO`` predicate.
* :func:`_to_kv` -- the typed ``AllowlistVisitor`` arm dispatch: bool ->
  ``boolValue`` / int -> ``intValue`` / float -> ``doubleValue`` / str ->
  ``stringValue`` (the Python equivalent of Rust's ``record_bool`` /
  ``record_i64`` / ``record_u64`` / ``record_f64`` / ``record_str``). bool is
  checked BEFORE int because ``bool`` subclasses ``int`` in Python.
* :class:`OtlpLogRecord` -- the OTLP ``LogRecord`` wire mirror (time /
  observed / severity_number / severity_text / body / attributes / trace_id /
  span_id).
* :func:`extract_record` -- the ``AllowlistVisitor`` + ``build_log_record``
  equivalent: message -> Body, allowlisted fields -> typed attributes,
  non-allowlisted fields dropped, ``trace_id`` / ``span_id`` empty (no
  fastrace local parent in Python).
* :func:`export_logs` -- the ``PumpLogExporter::export`` equivalent: chunk at
  ``MAX_LOG_RECORDS_PER_DONATION``, drop oversized chunks (encode returns
  ``None``), enqueue the rest (``enqueue`` returns ``False`` on a full queue),
  return the count sent. Symmetric to R146 :func:`export_metrics`.
* :class:`LogBatch` -- the count / age buffer with ``push`` (returns due
  records) and ``flush`` (forced drain).
* :class:`LogDonationSender` -- the activation handle; ``export(records)``
  runs the chunk / encode / enqueue pipeline.
* :class:`LogDonationLayer` -- the inert / activate / ``on_event`` / ``flush``
  lifecycle. ``on_event`` is the policy entry point a caller drives with
  ``(target, level, message, fields)`` (Python has no ``tracing::Event``, so
  the event's parts are passed explicitly).
* :func:`new_inert_layer` / :func:`flush_log_layer` -- the process-global
  inert install + reference-free teardown flush (the ``ACTIVE_LOG_LAYER``
  ``ArcSwapOption`` equivalent).
* :class:`LogDonationPump` / :meth:`LogDonationPump.drain` -- the shutdown
  fence, forwarding to the shared pump's :func:`drain_via` (R136).

NOT MIGRATED (framework glue with no Python equivalent):

* ``tracing_subscriber::Layer`` / ``tracing::field::Visit`` traits -- Python
  has no tracing subscriber. The event-walking visitor is replaced by
  :func:`extract_record` taking the event parts explicitly; the Layer
  lifecycle is a plain class with ``on_event``.
* ``fastrace::collector::SpanContext`` / :func:`current_ids` /
  :func:`encode_ids` -- the local-parent trace / span id source. Python has no
  fastrace; ``trace_id`` / ``span_id`` are always ``b""`` (the Rust common
  case for detached producer tasks with no local parent).
* ``prost::Message::encode_to_vec`` / ``ExportLogsServiceRequest`` protobuf
  build + ``base64`` encode -- the wire serialization. Python has no ``prost``
  in scope; :func:`export_logs` takes an injectable ``encode`` callback (the
  caller owns the OTLP envelope build + base64), symmetric to R146.
* ``arc_swap::ArcSwapOption`` process-global -- replaced by a module-level
  ``_ACTIVE_LAYER`` variable (single-threaded asyncio; no concurrent swap
  pressure).
* ``ToolServer::log_donation_layer`` -- the post-connect pump spawn + sender
  wiring. ``server.rs`` is a later SDK leaf; R148 lands the policy the wiring
  will drive.

Python-specific adaptations (no behavior change)
------------------------------------------------

* ``tracing::Level`` -> :class:`LogLevel` (``StrEnum``). The ordering
  ``ERROR < WARN < INFO < DEBUG < TRACE`` is captured in ``_LEVEL_ORDER`` so
  ``at_least_info`` mirrors ``*level <= Level::INFO``.
* ``AllowlistVisitor::record_*`` arm dispatch -> :func:`_to_kv`
  ``isinstance`` ladder. bool precedes int (bool subclasses int); the
  ``record_debug`` fallback (``format!("{value:?}")``) becomes ``str(value)``
  for non-(bool/int/float/str) values.
* ``Instant::now()`` / ``t.elapsed()`` for batch age ->
  :func:`time.monotonic` (the monotonic clock is the faithful interval
  measure; unaffected by wall-clock adjustments).
* ``mpsc::Sender::try_send`` -> the ``enqueue`` callback returning ``bool``
  (``True`` = queued, ``False`` = queue full -> drop). The bounded pump queue
  itself lives in R136 :func:`donate_pump.run_pump`.
* ``ACTIVE_LOG_LAYER: LazyLock<ArcSwapOption<..>>`` -> module-level
  ``_ACTIVE_LAYER`` variable; :func:`_reset_active_layer` is the test seam.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from minimax_code.computer_hub_sdk.donate_pump import (
    OtlpAnyValue,
    OtlpKeyValue,
    drain_via,
    now_unix_nanos,
    string_kv,
    string_value,
)
from minimax_code.tool_protocol import MAX_LOG_RECORDS_PER_DONATION

__all__ = [
    "TELEMETRY_TARGET",
    "ALLOWED_FIELDS",
    "LOG_BATCH_FLUSH_RECORDS",
    "LOG_BATCH_MAX_AGE_SECS",
    "LogLevel",
    "severity",
    "at_least_info",
    "OtlpLogRecord",
    "extract_record",
    "export_logs",
    "LogBatch",
    "LogDonationSender",
    "LogDonationLayer",
    "new_inert_layer",
    "flush_log_layer",
    "LogDonationPump",
]


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------
#: Stable target the workspace routes selected events through. The layer
#: selects exactly this target, ignoring the global log filter; the server
#: re-stamps it as the OTLP scope name.
TELEMETRY_TARGET: str = "workspace::telemetry"

#: Forwardable field names -- guaranteed-literal or numeric. Only the listed
#: fields are included; free-form fields such as ``error`` / ``reason`` /
#: ``object_path`` / ``gcs_path`` are omitted (they may carry secrets or user
#: paths). A frozenset gives O(1) membership without a mutable alias.
ALLOWED_FIELDS: frozenset[str] = frozenset(
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

#: Flush a buffered batch once it reaches this many records.
LOG_BATCH_FLUSH_RECORDS: int = 32

#: Flush a partial batch once its oldest record is at least this old (seconds,
#: checked on the next event; the tail is fenced by teardown). Mirrors Rust
#: ``Duration::from_secs(2)``.
LOG_BATCH_MAX_AGE_SECS: float = 2.0


# ---------------------------------------------------------------------------
# Level mapping.
#
# Rust ``tracing::Level`` orders ERROR < WARN < INFO < DEBUG < TRACE. The
# StrEnum carries the names; ``_LEVEL_ORDER`` carries the ordering so
# ``at_least_info`` mirrors ``*level <= Level::INFO``.
# ---------------------------------------------------------------------------
class LogLevel(StrEnum):
    """The 5 ``tracing`` levels, in their severity order (R148).

    ``ERROR < WARN < INFO < DEBUG < TRACE`` -- the same ordering Rust's
    ``tracing::Level`` uses, captured here so :func:`at_least_info` can compare
    levels by rank rather than by name.
    """

    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"
    DEBUG = "DEBUG"
    TRACE = "TRACE"


#: Numeric rank for each level, mirroring ``tracing``'s ``ERROR < WARN < INFO
#: < DEBUG < TRACE`` ordering. ``at_least_info`` is ``rank(level) <= rank(INFO)``.
_LEVEL_ORDER: dict[LogLevel, int] = {
    LogLevel.ERROR: 0,
    LogLevel.WARN: 1,
    LogLevel.INFO: 2,
    LogLevel.DEBUG: 3,
    LogLevel.TRACE: 4,
}


def severity(level: LogLevel) -> tuple[str, int]:
    """Map a level to its OTLP ``(SeverityText, SeverityNumber)`` (R148).

    Mirrors Rust ``severity``: ERROR -> ("ERROR", 17), WARN -> ("WARN", 13),
    INFO -> ("INFO", 9), DEBUG -> ("DEBUG", 5), TRACE -> ("TRACE", 1). The
    severity numbers follow the OTLP logs spec (ERROR=17 ... TRACE=1).
    """
    if level is LogLevel.ERROR:
        return ("ERROR", 17)
    if level is LogLevel.WARN:
        return ("WARN", 13)
    if level is LogLevel.INFO:
        return ("INFO", 9)
    if level is LogLevel.DEBUG:
        return ("DEBUG", 5)
    # LogLevel.TRACE
    return ("TRACE", 1)


def at_least_info(level: LogLevel) -> bool:
    """``>= INFO`` in severity terms (INFO / WARN / ERROR) -- R148.

    Mirrors Rust ``at_least_info``: tracing orders
    ``ERROR < WARN < INFO < DEBUG < TRACE``, so this is ``level <= INFO`` by
    rank. DEBUG and TRACE return ``False``.
    """
    return _LEVEL_ORDER[level] <= _LEVEL_ORDER[LogLevel.INFO]


def _is_allowed(name: str) -> bool:
    """Whether ``name`` is on the forwardable field allowlist (R148)."""
    return name in ALLOWED_FIELDS


# ---------------------------------------------------------------------------
# Typed OTLP attribute dispatch (the AllowlistVisitor arm dispatch).
#
# R136's ``OtlpAnyValue.value`` is typed ``dict[str, str]`` (only the string
# arm was needed by the trace/metric clients). The R136 docstring explicitly
# leaves room for ``intValue`` / ``boolValue`` / ``doubleValue`` to "land with
# the client that needs them" -- R148 is that client. This ladder builds the
# non-string AnyValue arms inline (the dict accepts heterogeneous values at
# runtime; ruff does not type-check the annotation), reusing R136's
# ``string_kv`` for the str arm so string construction stays single-sourced.
# ---------------------------------------------------------------------------
def _to_kv(name: str, value: object) -> OtlpKeyValue:
    """Build a typed ``KeyValue`` for one allowlisted field (R148).

    Dispatches by Python type to the OTLP ``AnyValue`` arm, mirroring the
    ``AllowlistVisitor::record_*`` methods: bool -> ``boolValue``,
    int -> ``intValue``, float -> ``doubleValue``, str -> ``stringValue``.
    bool MUST precede int (``bool`` subclasses ``int`` in Python). Anything
    else falls back to ``str(value)`` (the ``record_debug`` arm).
    """
    if isinstance(value, bool):
        return OtlpKeyValue(key=name, value=OtlpAnyValue(value={"boolValue": value}))
    if isinstance(value, int):
        return OtlpKeyValue(key=name, value=OtlpAnyValue(value={"intValue": value}))
    if isinstance(value, float):
        return OtlpKeyValue(key=name, value=OtlpAnyValue(value={"doubleValue": value}))
    if isinstance(value, str):
        return string_kv(name, value)
    # record_debug fallback: stringify (Rust's format!("{value:?}")).
    return string_kv(name, str(value))


# ---------------------------------------------------------------------------
# OTLP LogRecord assembly.
# ---------------------------------------------------------------------------
@dataclass
class OtlpLogRecord:
    """OTLP ``LogRecord`` wire mirror -- one curated log event (R148).

    Mirrors ``opentelemetry_proto::tonic::logs::v1::LogRecord``. The fields
    the donation policy populates are surfaced; the protobuf-only fields
    (``dropped_attributes_count`` / ``flags``) are dropped under YAGNI (Rust
    uses ``..Default::default()`` for them). ``trace_id`` / ``span_id`` are
    always ``b""`` in Python (no fastrace local parent -- the Rust common case
    for detached producer tasks).
    """

    time_unix_nano: int
    observed_time_unix_nano: int
    severity_number: int
    severity_text: str
    body: OtlpAnyValue | None = None
    attributes: list[OtlpKeyValue] = field(default_factory=list)
    trace_id: bytes = b""
    span_id: bytes = b""


def extract_record(
    level: LogLevel,
    message: str | None,
    fields: Mapping[str, object],
) -> OtlpLogRecord:
    """Build an :class:`OtlpLogRecord` from one event's parts (R148).

    The Python equivalent of Rust's ``AllowlistVisitor`` walk +
    ``build_log_record``: ``message`` becomes the OTLP ``Body`` (as a string
    ``AnyValue``), each allowlisted field becomes a typed ``KeyValue``
    attribute (see :func:`_to_kv`), and every other field is dropped. The
    record's ``trace_id`` / ``span_id`` are ``b""`` -- Python has no fastrace
    local parent (the Rust common case for detached producer tasks).

    ``fields`` must not carry a ``"message"`` key (the message is the explicit
    ``message`` argument); a stray ``"message"`` entry is skipped defensively.
    """
    now_nanos = now_unix_nanos()
    text, number = severity(level)
    body = string_value(message) if message is not None else None
    attributes: list[OtlpKeyValue] = []
    for name, value in fields.items():
        if name == "message":
            # The message is the explicit body; a stray entry is ignored.
            continue
        if not _is_allowed(name):
            continue
        attributes.append(_to_kv(name, value))
    return OtlpLogRecord(
        time_unix_nano=now_nanos,
        observed_time_unix_nano=now_nanos,
        severity_number=number,
        severity_text=text,
        body=body,
        attributes=attributes,
        trace_id=b"",
        span_id=b"",
    )


# ---------------------------------------------------------------------------
# Donation exporter (symmetric to R146 export_metrics).
# ---------------------------------------------------------------------------
def export_logs(
    records: list[OtlpLogRecord],
    encode: Callable[[list[OtlpLogRecord]], str | None],
    enqueue: Callable[[str], bool],
    max_per_batch: int = MAX_LOG_RECORDS_PER_DONATION,
) -> int:
    """Chunk + encode + enqueue log records onto the pump; return count sent.

    Mirrors ``PumpLogExporter::export`` and is symmetric to R146
    :func:`export_metrics`. For each chunk of at most ``max_per_batch``
    records:

    * ``encode(chunk)`` serializes the chunk to a wire payload (the caller owns
      the OTLP ``ExportLogsServiceRequest`` build + base64 -- no ``prost`` in
      Python). Returning ``None`` flags an oversized payload
      (``> MAX_DONATION_BYTES``) -- the chunk is dropped and export continues
      with the next, so one giant record never blocks the pipeline.
    * ``enqueue(payload)`` is the bounded pump ``try_send``: ``True`` = queued,
      ``False`` = queue full -> drop this payload and continue.

    Returns the number of payloads successfully enqueued. An empty ``records``
    list enqueues nothing and returns 0 without calling either callback.
    """
    sent = 0
    for start in range(0, len(records), max_per_batch):
        chunk = records[start : start + max_per_batch]
        payload = encode(chunk)
        if payload is None:
            # Oversized -> drop, continue to the next chunk.
            continue
        if enqueue(payload):
            sent += 1
        # else: queue full -> drop, continue.
    return sent


# ---------------------------------------------------------------------------
# Count / age batch buffer.
# ---------------------------------------------------------------------------
class LogBatch:
    """Buffer log records; flush on count or age (R148).

    Mirrors Rust ``LogBatch`` + ``LogLayerShared::push``. The first record
    stamps ``oldest`` (a :func:`time.monotonic` reading); each subsequent
    record appends. A push returns the buffered records once either flush
    trigger fires -- count ``>= LOG_BATCH_FLUSH_RECORDS`` or age
    ``>= LOG_BATCH_MAX_AGE_SECS`` -- and resets the buffer; otherwise it
    returns an empty list. :meth:`flush` is the forced teardown drain.
    """

    __slots__ = ("records", "oldest")

    def __init__(self) -> None:
        self.records: list[OtlpLogRecord] = []
        self.oldest: float | None = None

    def push(self, record: OtlpLogRecord) -> list[OtlpLogRecord]:
        """Buffer ``record``; return the due batch (empty if not yet due).

        The first record stamps ``oldest``; a flush trigger (count or age)
        returns and clears the buffer. Mirrors ``LogLayerShared::push``.
        """
        if not self.records:
            self.oldest = time.monotonic()
        self.records.append(record)
        due = len(self.records) >= LOG_BATCH_FLUSH_RECORDS or (
            self.oldest is not None
            and time.monotonic() - self.oldest >= LOG_BATCH_MAX_AGE_SECS
        )
        if due:
            self.oldest = None
            due_records = self.records
            self.records = []
            return due_records
        return []

    def flush(self) -> list[OtlpLogRecord]:
        """Force the buffered batch out (teardown analogue of ``flush()``).

        Returns and clears the buffer regardless of count / age. Mirrors
        ``LogLayerShared::flush``'s batch drain (the sender export is the
        caller's responsibility -- a no-op while inert or empty).
        """
        records = self.records
        self.records = []
        self.oldest = None
        return records


# ---------------------------------------------------------------------------
# Activation handle + Layer lifecycle.
# ---------------------------------------------------------------------------
class LogDonationSender:
    """Activation handle swapped into an inert :class:`LogDonationLayer` (R148).

    Wraps the ``encode`` / ``enqueue`` closures the post-connect wiring
    supplies (the OTLP envelope build + base64, and the bounded pump
    ``try_send``). ``export(records)`` runs the chunk / encode / enqueue
    pipeline via :func:`export_logs` -- the ``PumpLogExporter::export``
    equivalent. Constructed by the later ``server.rs`` leaf's
    ``log_donation_layer`` wiring.
    """

    __slots__ = ("_encode", "_enqueue")

    def __init__(
        self,
        encode: Callable[[list[OtlpLogRecord]], str | None],
        enqueue: Callable[[str], bool],
    ) -> None:
        self._encode = encode
        self._enqueue = enqueue

    def export(self, records: list[OtlpLogRecord]) -> int:
        """Chunk + encode + enqueue ``records``; return count sent (R148)."""
        return export_logs(records, self._encode, self._enqueue)


class LogDonationLayer:
    """Curated log donation layer: filter, redact, batch (R148).

    Mirrors ``DonatingLogLayer``. Installed **inert`` (no sender); selected
    events are dropped until :meth:`activate` swaps a sender in. The policy
    entry point is :meth:`on_event` -- Python has no ``tracing::Event``, so a
    caller drives the layer with the event's explicit parts
    ``(target, level, message, fields)``. Inert + off-target + below-INFO
    events short-circuit before any allocation; otherwise the record is built
    and buffered, and a due batch is exported.

    The process-global :data:`_ACTIVE_LAYER` handle (set by
    :func:`new_inert_layer`) lets :func:`flush_log_layer` drive a teardown
    flush without holding a reference -- the ``ACTIVE_LOG_LAYER``
    ``ArcSwapOption`` equivalent.
    """

    __slots__ = ("_sender", "_batch")

    def __init__(self) -> None:
        self._sender: LogDonationSender | None = None
        self._batch: LogBatch = LogBatch()

    def activate(self, sender: LogDonationSender) -> None:
        """Swap in the donation sender, activating donation (R148)."""
        self._sender = sender

    def on_event(
        self,
        target: str,
        level: LogLevel,
        message: str | None,
        fields: Mapping[str, object],
    ) -> None:
        """Filter + redact + buffer one event; export a due batch (R148).

        Mirrors ``Layer::on_event``. Short-circuits while inert (no sender),
        off-target (``target != TELEMETRY_TARGET``), or below INFO. Otherwise
        builds the record, buffers it, and exports the batch if a flush
        trigger fired.
        """
        sender = self._sender
        if sender is None:
            # Inert: selected events are dropped before enqueueing.
            return
        if target != TELEMETRY_TARGET:
            return
        if not at_least_info(level):
            return
        record = extract_record(level, message, fields)
        due = self._batch.push(record)
        if due:
            sender.export(due)

    def flush(self) -> None:
        """Force the buffered batch onto the pump (R148).

        No-op while inert or empty. Mirrors ``LogLayerShared::flush``: drains
        the batch, then exports it via the active sender (if any).
        """
        records = self._batch.flush()
        if records and self._sender is not None:
            self._sender.export(records)


#: Process-global handle to the active layer so :func:`flush_log_layer` can
#: drive a teardown flush without a reference. Mirrors Rust's
#: ``ACTIVE_LOG_LAYER: LazyLock<ArcSwapOption<LogLayerShared>>``. asyncio is
#: single-threaded, so a plain module variable replaces the atomic swap.
_ACTIVE_LAYER: LogDonationLayer | None = None


def new_inert_layer() -> LogDonationLayer:
    """Install an inert layer and register it as the flush target (R148).

    Mirrors ``DonatingLogLayer::new_inert``: the layer carries no sender
    (selected events are dropped until :meth:`LogDonationLayer.activate`),
    and stores itself in :data:`_ACTIVE_LAYER` so
    :func:`flush_log_layer` can reach it without a reference.
    """
    global _ACTIVE_LAYER
    layer = LogDonationLayer()
    _ACTIVE_LAYER = layer
    return layer


def flush_log_layer() -> None:
    """Flush the active layer's in-memory batch onto the pump (R148).

    Called from ``ToolServer`` teardown before the pump drain so a crash-y
    shutdown does not abandon a partial batch. No-op if no layer was installed
    or the installed layer is inert / empty.
    """
    if _ACTIVE_LAYER is not None:
        _ACTIVE_LAYER.flush()


def _reset_active_layer() -> None:
    """Test seam: clear the process-global layer handle (R148).

    Restores the pre-install state so a test that calls
    :func:`new_inert_layer` does not leak the handle to later tests.
    """
    global _ACTIVE_LAYER
    _ACTIVE_LAYER = None


# ---------------------------------------------------------------------------
# Shutdown fence.
# ---------------------------------------------------------------------------
class LogDonationPump:
    """Shutdown fence: drains queued log donations before close (R148).

    Mirrors ``LogDonationPump``. Holds the bounded pump sender (an
    :class:`asyncio.Queue` of :class:`donate_pump.PumpMsg`); :meth:`drain`
    forwards to the shared pump's :func:`drain_via` (R136) so a teardown can
    await "every payload queued before this call has had a send attempt".
    """

    __slots__ = ("_tx",)

    def __init__(self, tx: object) -> None:
        # ``tx`` is an ``asyncio.Queue[donate_pump.PumpMsg]``; typed ``object``
        # to avoid importing the PumpMsg union alias here (the layer does not
        # construct pump messages, only drains).
        self._tx = tx

    async def drain(self) -> None:
        """Resolve once every queued payload has had a send attempt (R148)."""
        await drain_via(self._tx)  # type: ignore[arg-type]
