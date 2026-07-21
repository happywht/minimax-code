"""Per-response inference latency statistics (pure) -- fusion of grok's
``xai-grok-sampler`` (R234, ``src/metrics.rs`` whole-leaf migration, crate
deepening round 2).

``xai-grok-sampler`` is grok's actor-based sampling/inference layer (HTTP
streaming + retry, no shell coupling). The crate's ``metrics.rs`` ships two
public symbols + a constructor + a span-recording helper, all with **zero
external crate dependency** (only ``std::time::Instant`` + ``serde``). This
module migrates the whole leaf faithfully and **clears the ``events.rs``
deferred-dependency blocker**: ``events.rs`` carries a ``metrics:
InferenceLatencyStats`` field (``use crate::metrics::InferenceLatencyStats``)
that could not migrate until this leaf landed. With this round the
:class:`InferenceLatencyStats` type is available to the eventual ``events.rs``
migration. The remaining ``SamplerConfig`` blockers in
:mod:`minimax_code.sampler.config` (``retry`` / ``sampling_types`` sub-types /
``HeaderMap``) are unchanged -- this leaf unblocks ``events.rs``, not
``SamplerConfig`` directly.

Migration map (grok Rust -> platform Python)
--------------------------------------------

* ``pub fn compute_percentiles(sorted: &[u64]) -> (u64, u64, u64, u64, u64)``
  -> :func:`compute_percentiles` (module-level pure function). Returns
  ``(p50, p99, max, mean, sum)`` from a **caller-sorted** slice. The ``assert!
  (!sorted.is_empty())`` precondition is preserved (``AssertionError`` on an
  empty slice). The p99 index mirrors grok's ``((len as f64 * 0.99).ceil() as
  usize).saturating_sub(1).min(len - 1)``: ``min(max(0, ceil(len*0.99)-1),
  len-1)``. Because Python ``float`` is IEEE-754 double (identical to Rust
  ``f64``), ``len * 0.99`` rounds identically in both languages -- the index
  selection is byte-for-byte faithful.
* ``pub struct InferenceLatencyStats`` (``#[derive(Debug, Clone, Default,
  Serialize, Deserialize)]``, 9 fields) -> :class:`InferenceLatencyStats`
  (:func:`@dataclass(slots=True)`, mutable). ``Option<u64>`` -> ``int | None``;
  ``u64`` / ``u32`` -> ``int``; ``Vec<u64>`` -> ``list[int]``. The Rust struct
  is mutable (``attempts`` is "set by retry loop on success"), so the dataclass
  is **not** frozen -- a caller mutates ``attempts`` directly or uses
  :func:`dataclasses.replace`. The ``#[derive(Default)]`` shape is mirrored by
  per-field defaults (every ``Option`` defaults to ``None``, every counter to
  ``0``, the interval list to ``[]``).
* ``impl InferenceLatencyStats { fn from_timestamps(stream_start: Instant,
  chunk_timestamps: &[Instant], stream_end: Instant) -> Self }`` ->
  :meth:`InferenceLatencyStats.from_timestamps` classmethod. Rust ``Instant``
  (monotonic clock, nanosecond precision via ``Duration``) has no Python
  analogue -- :func:`time.monotonic` returns a ``float`` second count (~15
  significant digits), so the signature takes ``float`` seconds and converts
  differences to milliseconds internally.

  .. note:: **Round, not truncate.** Rust's ``Duration::as_millis`` is an
     integer-nanosecond divide (exact truncation). Python ``float`` seconds
     suffer subtractive cancellation on "nice" values (``0.150 - 0.100 ==
     0.04999999999999999``), so :meth:`from_timestamps` uses :func:`round`
     rather than :func:`int` truncation when converting to milliseconds.
     ``round`` is the more accurate choice for float inputs (it recovers the
     intended 50 ms where truncation would yield 49); for inputs that are
     already exact integer milliseconds, ``round`` and truncation agree. This
     is the one deliberate behavioral deviation from grok, recorded here for
     fidelity. The statistical aggregates (percentiles) are insensitive to a
     ±1 ms rounding boundary.

* ``impl InferenceLatencyStats { fn record_on_span(&self, span: &tracing::
  Span) }`` -> :meth:`InferenceLatencyStats.to_log_fields` (returns
  ``dict[str, int]``). grok records the ``Some``-valued fields onto a
  ``tracing::Span``; Python has no ``tracing`` equivalent (the platform's
  structured logging is a separate concern). Returning a flat ``dict`` lets
  the caller attach the fields to whatever observability sink it owns,
  decoupling the stats type from any logging framework. The recorded field
  set mirrors grok exactly: ``ttfb_ms`` (only when set), ``ttlb_ms``,
  ``chunk_count``, ``itl_p50_ms`` (only when set), ``itl_p99_ms`` (only when
  set) -- the ``itl_max_ms`` / ``itl_mean_ms`` / ``attempts`` / interval list
  are NOT recorded on the span in grok and are omitted here too.

YAGNI boundary
--------------

The crate's actor core (``client.rs`` 2745 + ``retry.rs`` 856 + ``actor/`` +
``stream/``) that *consumes* these stats overlaps the platform's
``agent/llm.py`` + resilience stack and is multi-round or YAGNI. This leaf
lands the pure type + algorithm so the eventual ``events.rs`` migration has
its dependency satisfied; no actor/runtime wiring is added here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field


def compute_percentiles(
    sorted_values: Sequence[int],
) -> tuple[int, int, int, int, int]:
    """Compute ``(p50, p99, max, mean, sum)`` from a **sorted** slice (grok
    ``compute_percentiles``).

    The caller MUST sort the input ascending (grok takes ``sorted: &[u64]``
    after a ``sort_unstable``; this function does not re-sort). The slice must
    be non-empty (``AssertionError`` otherwise, mirroring grok's ``assert!``).

    * ``p50`` -- the median element at ``len // 2`` (grok uses integer-index
      median, not interpolated).
    * ``p99`` -- the element at index ``min(max(0, ceil(len*0.99)-1),
      len-1)``. For ``len=10`` this is index ``9`` (the maximum); for
      ``len=100`` it is index ``98`` or ``99`` depending on float rounding of
      ``100*0.99`` (identical to Rust ``f64``).
    * ``max`` -- ``sorted[len-1]`` (the caller sorted ascending, so the last
      element is the max).
    * ``mean`` -- integer division ``sum // len`` (grok ``sum / len as u64``).
    * ``sum`` -- the running total (grok ``sorted.iter().sum()``).
    """
    length = len(sorted_values)
    assert length > 0, "Cannot compute percentiles from an empty slice"
    p50 = sorted_values[length // 2]
    # grok: ((len as f64 * 0.99).ceil() as usize).saturating_sub(1).min(len - 1).
    # saturating_sub(1) clamps the 0 case to 0; min clamps to the last index.
    # length >= 1 guarantees ceil(length*0.99) >= 1, so the inner max is
    # defensive only (mirrors the Rust saturate for fidelity).
    p99_idx = min(max(0, math.ceil(length * 0.99) - 1), length - 1)
    p99 = sorted_values[p99_idx]
    max_value = sorted_values[length - 1]
    total = sum(sorted_values)
    mean = total // length
    return (p50, p99, max_value, mean, total)


@dataclass(slots=True)
class InferenceLatencyStats:
    """Per-response inference latency statistics (grok
    ``InferenceLatencyStats``).

    Captures the timing shape of one inference response: time-to-first-byte,
    time-to-last-byte, the inter-token-latency (ITL) interval list + its
    percentiles, the chunk count, and the retry-attempt counter. Constructed
    by :meth:`from_timestamps` from a stream of monotonic timestamps; the
    fields are then read by observability code via :meth:`to_log_fields`.

    The dataclass is **mutable** (not frozen): grok sets ``attempts`` from the
    retry loop on success (``1`` = no retries), and a caller is expected to
    mutate that field after construction. Use :func:`dataclasses.replace` for a
    functional update if preferred.

    Field semantics
    ---------------

    * ``time_to_first_token_ms`` -- wall-clock ms from stream start to the
      first content chunk. ``None`` when the stream produced no chunks (the
      empty-stream early return in :meth:`from_timestamps`).
    * ``time_to_last_byte_ms`` -- measured at **stream exhaustion**
      (``stream_end``), NOT at the last content chunk. This is the load-bearing
      distinction from grok: a stream may emit trailing keepalive / SSE
      terminators after the final content byte, and the last-byte time must
      reflect when the stream actually closed.
    * ``chunk_count`` -- number of content chunks observed (``len(chunk_
      timestamps)``).
    * ``itl_intervals_ms`` -- adjacent-chunk deltas in ms (``windows(2)`` in
      grok). Empty for a single-chunk stream.
    * ``itl_p50_ms`` / ``itl_p99_ms`` / ``itl_max_ms`` / ``itl_mean_ms`` --
      percentiles over the sorted intervals. ``None`` when there are fewer
      than two chunks (no intervals to aggregate).
    * ``attempts`` -- inference attempts for this response (``0`` on
      construction; the retry loop sets it to the count of attempts that
      preceded success, so ``1`` means "succeeded on the first try").
    """

    time_to_first_token_ms: int | None = None
    time_to_last_byte_ms: int = 0
    chunk_count: int = 0
    itl_intervals_ms: list[int] = field(default_factory=list)
    itl_p50_ms: int | None = None
    itl_p99_ms: int | None = None
    itl_max_ms: int | None = None
    itl_mean_ms: int | None = None
    attempts: int = 0

    @classmethod
    def from_timestamps(
        cls,
        stream_start: float,
        chunk_timestamps: Sequence[float],
        stream_end: float,
    ) -> InferenceLatencyStats:
        """Build stats from a stream of monotonic timestamps (grok
        ``from_timestamps``).

        ``stream_start`` / ``stream_end`` bracket the whole stream (``stream_
        end`` is when the stream exhausted, not the last content chunk -- see
        the class docstring). ``chunk_timestamps`` are the per-chunk emission
        instants in ascending order. Timestamps are ``float`` seconds
        (e.g. :func:`time.monotonic` values); differences are converted to ms
        via :func:`round` (see the module docstring's "Round, not truncate"
        note for why ``round`` is chosen over ``int`` truncation).

        Empty-chunk early return: when ``chunk_timestamps`` is empty the stats
        carry only ``time_to_last_byte_ms`` (every other field stays at its
        default -- ``time_to_first_token_ms`` is ``None``, no intervals, no
        percentiles), mirroring grok's ``if chunk_timestamps.is_empty() {
        return Self { ... }`` branch.
        """
        ttlb_ms = round((stream_end - stream_start) * 1000)
        if not chunk_timestamps:
            return cls(time_to_last_byte_ms=ttlb_ms)

        ttfb_ms = round((chunk_timestamps[0] - stream_start) * 1000)
        intervals_ms = [
            round((chunk_timestamps[i + 1] - chunk_timestamps[i]) * 1000)
            for i in range(len(chunk_timestamps) - 1)
        ]
        if intervals_ms:
            sorted_intervals = sorted(intervals_ms)
            p50_ms, p99_ms, max_ms, mean_ms, _sum = compute_percentiles(sorted_intervals)
        else:
            p50_ms = p99_ms = max_ms = mean_ms = None

        return cls(
            time_to_first_token_ms=ttfb_ms,
            time_to_last_byte_ms=ttlb_ms,
            chunk_count=len(chunk_timestamps),
            itl_intervals_ms=intervals_ms,
            itl_p50_ms=p50_ms,
            itl_p99_ms=p99_ms,
            itl_max_ms=max_ms,
            itl_mean_ms=mean_ms,
            attempts=0,
        )

    def to_log_fields(self) -> dict[str, int]:
        """Return the stats as a flat ``{field: value}`` dict for logging
        (Python equivalent of grok ``record_on_span``).

        grok records the ``Some``-valued fields onto a ``tracing::Span``;
        Python has no ``tracing`` analogue, so this method returns a dict the
        caller can attach to whatever structured-logging sink it owns. The
        recorded field set mirrors grok exactly:

        * ``ttfb_ms`` -- only when ``time_to_first_token_ms`` is set.
        * ``ttlb_ms`` -- always (non-optional).
        * ``chunk_count`` -- always (non-optional).
        * ``itl_p50_ms`` -- only when set.
        * ``itl_p99_ms`` -- only when set.

        The ``itl_max_ms`` / ``itl_mean_ms`` / ``attempts`` / interval list
        are NOT recorded on the span in grok and are omitted here too.
        """
        fields: dict[str, int] = {
            "ttlb_ms": self.time_to_last_byte_ms,
            "chunk_count": self.chunk_count,
        }
        if self.time_to_first_token_ms is not None:
            fields["ttfb_ms"] = self.time_to_first_token_ms
        if self.itl_p50_ms is not None:
            fields["itl_p50_ms"] = self.itl_p50_ms
        if self.itl_p99_ms is not None:
            fields["itl_p99_ms"] = self.itl_p99_ms
        return fields


__all__ = [
    "InferenceLatencyStats",
    "compute_percentiles",
]
