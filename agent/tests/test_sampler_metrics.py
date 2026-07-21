"""Tests for sampler.metrics (R234, ``xai-grok-sampler`` ``src/metrics.rs``
whole-leaf migration).

Covers the two migrated public symbols: the :func:`compute_percentiles` pure
algorithm (p50/p99/max/mean/sum from a caller-sorted slice) and the
:class:`InferenceLatencyStats` dataclass (9 fields) with its
:meth:`from_timestamps` classmethod (Instant -> float, round-to-ms) and
:meth:`to_log_fields` dict (the Python equivalent of grok's ``record_on_span``,
decoupled from ``tracing::Span``). The leaf clears the ``events.rs``
deferred-dependency blocker (``metrics::InferenceLatencyStats`` -- the
``events.rs`` ``metrics: InferenceLatencyStats`` field).
"""

from __future__ import annotations

import pytest

from minimax_code import sampler
from minimax_code.sampler import metrics as sampler_metrics
from minimax_code.sampler.metrics import (
    InferenceLatencyStats,
    compute_percentiles,
)

# ---------------------------------------------------------------------------
# Barrel surface (module + package).
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_two_symbols() -> None:
    assert len(sampler_metrics.__all__) == 2
    assert set(sampler_metrics.__all__) == {
        "InferenceLatencyStats",
        "compute_percentiles",
    }


def test_package_barrel_re_exports_metrics_symbols() -> None:
    """The package barrel re-exports both metrics symbols (R234 adds 2 ->
    the sampler ``__all__`` grows from 205 to 207; the total is asserted in
    ``test_sampler_config.test_package_barrel_exposes_...``)."""
    for name in sampler_metrics.__all__:
        assert name in sampler.__all__
        assert hasattr(sampler, name)
    # Identity: the package symbol IS the module symbol (re-export, not a copy).
    assert sampler.InferenceLatencyStats is InferenceLatencyStats
    assert sampler.compute_percentiles is compute_percentiles


# ---------------------------------------------------------------------------
# compute_percentiles: pure algorithm over a caller-sorted slice.
# ---------------------------------------------------------------------------


def test_compute_percentiles_returns_five_tuple_of_ints() -> None:
    result = compute_percentiles([42])
    assert isinstance(result, tuple)
    assert len(result) == 5
    for value in result:
        assert isinstance(value, int)


def test_compute_percentiles_single_element() -> None:
    """A one-element slice: every aggregate equals that element."""
    p50, p99, mx, mean, total = compute_percentiles([42])
    assert p50 == 42
    assert p99 == 42
    assert mx == 42
    assert mean == 42
    assert total == 42


def test_compute_percentiles_ten_ascending() -> None:
    """grok fixture: ``[10,20,...,100]`` -> p50=60, p99=100, max=100,
    mean=55, sum=550."""
    p50, p99, mx, mean, total = compute_percentiles(
        [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    )
    assert p50 == 60  # sorted[10 // 2] = sorted[5]
    assert p99 == 100  # sorted[min(ceil(10*0.99)-1, 9)] = sorted[9]
    assert mx == 100
    assert mean == 55  # 550 // 10
    assert total == 550


def test_compute_percentiles_two_elements() -> None:
    """len=2: p50=sorted[1] (the higher), p99=sorted[1]."""
    p50, p99, mx, mean, total = compute_percentiles([3, 7])
    assert p50 == 7  # sorted[2 // 2] = sorted[1]
    assert p99 == 7  # min(ceil(2*0.99)-1, 1) = min(1, 1) = 1
    assert mx == 7
    assert mean == 5  # 10 // 2
    assert total == 10


def test_compute_percentiles_empty_raises() -> None:
    """Empty slice violates the grok ``assert!(!sorted.is_empty())``
    precondition."""
    with pytest.raises(AssertionError):
        compute_percentiles([])


def test_compute_percentiles_all_same_no_overflow() -> None:
    """100 identical values: p99 index stays in bounds (the
    ``saturating_sub(1).min(len-1)`` clamp in grok)."""
    p50, p99, mx, mean, total = compute_percentiles([10] * 100)
    assert p50 == 10
    assert p99 == 10
    assert mx == 10
    assert mean == 10
    assert total == 1000


# ---------------------------------------------------------------------------
# InferenceLatencyStats: dataclass default shape (grok #[derive(Default)]).
# ---------------------------------------------------------------------------


def test_stats_defaults_match_grok_derive_default() -> None:
    stats = InferenceLatencyStats()
    assert stats.time_to_first_token_ms is None
    assert stats.time_to_last_byte_ms == 0
    assert stats.chunk_count == 0
    assert stats.itl_intervals_ms == []
    assert stats.itl_p50_ms is None
    assert stats.itl_p99_ms is None
    assert stats.itl_max_ms is None
    assert stats.itl_mean_ms is None
    assert stats.attempts == 0


def test_stats_default_intervals_is_distinct_list_per_instance() -> None:
    """``field(default_factory=list)`` gives each instance its own list (the
    classic mutable-default pitfall -- two defaults must NOT share a list)."""
    a = InferenceLatencyStats()
    b = InferenceLatencyStats()
    a.itl_intervals_ms.append(99)
    assert b.itl_intervals_ms == []


def test_stats_is_mutable_attempts_field() -> None:
    """grok sets ``attempts`` from the retry loop on success; the dataclass is
    intentionally NOT frozen so a caller can mutate it post-construction."""
    stats = InferenceLatencyStats()
    assert stats.attempts == 0
    stats.attempts = 3
    assert stats.attempts == 3


# ---------------------------------------------------------------------------
# from_timestamps: the Instant -> float constructor (grok from_timestamps).
# ---------------------------------------------------------------------------


def test_from_timestamps_empty_chunks() -> None:
    """Empty-chunk early return: only ``time_to_last_byte_ms`` is set; every
    other field stays at default."""
    stats = InferenceLatencyStats.from_timestamps(
        stream_start=1.0,
        chunk_timestamps=[],
        stream_end=1.5,
    )
    assert stats.time_to_first_token_ms is None
    assert stats.time_to_last_byte_ms == 500  # (1.5 - 1.0) * 1000
    assert stats.chunk_count == 0
    assert stats.itl_intervals_ms == []
    assert stats.itl_p50_ms is None
    assert stats.itl_p99_ms is None
    assert stats.itl_max_ms is None
    assert stats.itl_mean_ms is None
    assert stats.attempts == 0


def test_from_timestamps_single_chunk() -> None:
    """One chunk: ttfb set, but no intervals -> percentiles are None."""
    stats = InferenceLatencyStats.from_timestamps(
        stream_start=0.0,
        chunk_timestamps=[0.100],
        stream_end=0.150,
    )
    assert stats.time_to_first_token_ms == 100
    assert stats.time_to_last_byte_ms == 150
    assert stats.chunk_count == 1
    assert stats.itl_intervals_ms == []
    assert stats.itl_p50_ms is None
    assert stats.itl_p99_ms is None
    assert stats.itl_max_ms is None
    assert stats.itl_mean_ms is None


def test_from_timestamps_two_chunks_round_recovers_intended_ms() -> None:
    """Two chunks: one interval -> percentiles all equal that interval.

    Validates the round-to-ms choice: ``0.150 - 0.100`` suffers subtractive
    cancellation in float (``0.04999999999999999``), so :func:`round` recovers
    50 where ``int`` truncation would yield 49."""
    stats = InferenceLatencyStats.from_timestamps(
        stream_start=0.0,
        chunk_timestamps=[0.100, 0.150],
        stream_end=0.200,
    )
    assert stats.time_to_first_token_ms == 100
    assert stats.time_to_last_byte_ms == 200
    assert stats.chunk_count == 2
    assert stats.itl_intervals_ms == [50]
    assert stats.itl_p50_ms == 50
    assert stats.itl_p99_ms == 50
    assert stats.itl_max_ms == 50
    assert stats.itl_mean_ms == 50


def test_from_timestamps_many_chunks() -> None:
    """grok ``test_many_chunks`` fixture: 11 chunks producing intervals
    [10,20,...,100] -> p50=60, p99=100, max=100, mean=55.

    Chunk offsets (seconds) place the first chunk at 10 ms and accumulate the
    interval sums so adjacent deltas are 10 ms, 20 ms, ..., 100 ms."""
    offsets_ms = [10, 20, 40, 70, 110, 160, 220, 290, 370, 460, 560]
    chunks = [ms / 1000 for ms in offsets_ms]
    stats = InferenceLatencyStats.from_timestamps(
        stream_start=0.0,
        chunk_timestamps=chunks,
        stream_end=0.600,
    )
    assert stats.chunk_count == 11
    assert stats.time_to_first_token_ms == 10
    assert stats.time_to_last_byte_ms == 600
    assert stats.itl_intervals_ms == [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert stats.itl_p50_ms == 60  # sorted[5]
    assert stats.itl_p99_ms == 100  # sorted[9]
    assert stats.itl_max_ms == 100
    assert stats.itl_mean_ms == 55  # 550 // 10


def test_from_timestamps_p99_no_overflow() -> None:
    """grok ``test_p99_does_not_overflow``: 101 chunks spaced 10 ms apart
    (100 intervals, all 10 ms) -> p99 index stays in bounds and equals 10."""
    chunks = [(i + 1) * 0.010 for i in range(101)]  # 0.010, 0.020, ..., 1.010
    stats = InferenceLatencyStats.from_timestamps(
        stream_start=0.0,
        chunk_timestamps=chunks,
        stream_end=1.020,
    )
    assert stats.chunk_count == 101
    assert stats.time_to_first_token_ms == 10
    assert stats.time_to_last_byte_ms == 1020
    assert stats.itl_intervals_ms == [10] * 100
    assert stats.itl_p50_ms == 10
    assert stats.itl_p99_ms == 10  # no overflow / no IndexError
    assert stats.itl_max_ms == 10
    assert stats.itl_mean_ms == 10


def test_from_timestamps_ttlb_uses_stream_end_not_last_chunk() -> None:
    """grok ``test_ttlb_uses_stream_end_not_last_chunk``: ``time_to_last_byte_ms``
    is measured at stream exhaustion (``stream_end``), NOT at the last content
    chunk. Here ``stream_end`` is 5x past the last chunk."""
    stats = InferenceLatencyStats.from_timestamps(
        stream_start=0.0,
        chunk_timestamps=[0.100, 0.200],
        stream_end=1.000,
    )
    assert stats.time_to_last_byte_ms == 1000  # stream_end, not last chunk (0.200)
    assert stats.time_to_first_token_ms == 100


# ---------------------------------------------------------------------------
# to_log_fields: the record_on_span Python equivalent (flat dict).
# ---------------------------------------------------------------------------


def test_to_log_fields_empty_stats() -> None:
    """Default stats: only the two non-optional fields (ttlb_ms + chunk_count)
    land in the dict; the optional fields are omitted (None -> not in dict)."""
    fields = InferenceLatencyStats().to_log_fields()
    assert fields == {"ttlb_ms": 0, "chunk_count": 0}


def test_to_log_fields_full_stats_records_exactly_five_fields() -> None:
    """Fully-populated stats: the 5 grok-recorded fields land in the dict.

    ``itl_max_ms`` / ``itl_mean_ms`` / ``attempts`` / the interval list are
    NOT in the dict -- grok ``record_on_span`` omits them."""
    stats = InferenceLatencyStats.from_timestamps(
        stream_start=0.0,
        chunk_timestamps=[0.100, 0.150],
        stream_end=0.200,
    )
    fields = stats.to_log_fields()
    assert fields == {
        "ttfb_ms": 100,
        "ttlb_ms": 200,
        "chunk_count": 2,
        "itl_p50_ms": 50,
        "itl_p99_ms": 50,
    }


def test_to_log_fields_values_are_all_int() -> None:
    """Every recorded value is an ``int`` (no float leakage from the ms
    conversion)."""
    stats = InferenceLatencyStats.from_timestamps(
        stream_start=0.0,
        chunk_timestamps=[0.100, 0.150, 0.200],
        stream_end=0.250,
    )
    fields = stats.to_log_fields()
    for value in fields.values():
        assert isinstance(value, int)
