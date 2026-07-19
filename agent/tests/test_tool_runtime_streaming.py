"""Tests for the R111 tool_runtime streaming module.

Covers the migration of ``xai-tool-runtime/src/streaming.rs``:

- ``PartialResultPayload``: ``deny_unknown_fields`` strict decode (unknown
  key is a hard error, opposite of ``render``'s flatten-extra leniency),
  absent optional flags fall back to defaults, required
  ``delta``/``total_bytes`` enforced, all-field wire round trip.
- ``_incomplete_utf8_suffix_len``: the byte-walk that mirrors Rust's
  ``std::str::from_utf8`` ``error_len().is_none()`` branch — complete
  ASCII / multibyte -> 0; incomplete 2/3/4-byte suffix -> run length;
  invalid lead byte / bare continuation -> 0 (never hold back).
- ``stream_chunk``: no-new-bytes short-circuit + ``last_total`` untouched;
  suffix delta advances ``last_total`` in place (single-element list
  simulating ``&mut u64``); gap flag when new exceeds surviving tail;
  caller-supplied ``truncated`` passes through verbatim and is distinct
  from per-tick ``gap``; multibyte split across ticks is held back and
  reassembled whole (NOT emitted as U+FFFD); over-cap bursts are paced
  into capped frames losslessly; cap cut backs off UTF-8 boundaries;
  backlog drains in exactly ``ceil(new / cap)`` ticks; ASCII frames fill
  to the cap with no spurious trailing frame; exact-cap single frame;
  cap below one char still makes progress (pathological full-char emit);
  UTF-8 backoff stays within 3 bytes of the cap; a gap paces only the
  surviving tail and never re-scans the dropped middle.

NOTE: Rust ``String::len()`` is a BYTE count; Python ``len(str)`` is a
CHARACTER count. Every assertion that mirrors a Rust ``delta.len()`` uses
``len(p.delta.encode("utf-8"))`` to stay byte-accurate.
"""

from __future__ import annotations

import pytest

from minimax_code.tool_protocol.capabilities import StreamingSpec
from minimax_code.tool_runtime.streaming import (
    DEFAULT_MAX_DELTA_BYTES,
    PartialResultPayload,
    _incomplete_utf8_suffix_len,
    stream_chunk,
)

# ---------------------------------------------------------------------------
# Test helpers — mirror the Rust `spec_with` / `run` harness.
# ---------------------------------------------------------------------------


def _spec_with(max_delta_bytes: int | None) -> StreamingSpec:
    return StreamingSpec(subkind="test_chunk", max_delta_bytes=max_delta_bytes)


def _run(
    spec: StreamingSpec,
    tail: bytes,
    total: int,
    last: list[int],
    truncated: bool,
) -> PartialResultPayload:
    """Run ``stream_chunk``, assert it produced a frame, decode the payload."""
    progress = stream_chunk(spec, tail, total, last, truncated)
    assert progress is not None, "expected a progress frame"
    assert progress.kind == "custom"
    assert progress.subkind == "test_chunk"
    return PartialResultPayload.from_dict(progress.payload)


# ---------------------------------------------------------------------------
# DEFAULT_MAX_DELTA_BYTES constant.
# ---------------------------------------------------------------------------


def test_default_max_delta_bytes_is_16_kib():
    assert DEFAULT_MAX_DELTA_BYTES == 16 * 1024


# ---------------------------------------------------------------------------
# _incomplete_utf8_suffix_len — the byte-walk helper.
# ---------------------------------------------------------------------------


def test_incomplete_suffix_empty_is_zero():
    assert _incomplete_utf8_suffix_len(b"") == 0


def test_incomplete_suffix_ascii_complete_is_zero():
    assert _incomplete_utf8_suffix_len(b"abc") == 0


def test_incomplete_suffix_complete_multibyte_is_zero():
    # "é" = 0xC3 0xA9 — complete 2-byte sequence.
    assert _incomplete_utf8_suffix_len("é".encode()) == 0
    # "😀" = 4 bytes — complete 4-byte sequence.
    assert _incomplete_utf8_suffix_len("😀".encode()) == 0


def test_incomplete_suffix_two_byte_lead_held_back():
    # Lone lead byte 0xC3 (é without its continuation) -> hold back 1.
    assert _incomplete_utf8_suffix_len(b"a\xC3") == 1


def test_incomplete_suffix_three_byte_partial_held_back():
    # 0xE2 0x82 — incomplete 3-byte sequence -> hold back 2.
    assert _incomplete_utf8_suffix_len(b"\xE2\x82") == 2


def test_incomplete_suffix_four_byte_partial_held_back():
    # 0xF0 0x9F 0x98 — incomplete 4-byte sequence (😀 minus its last byte).
    assert _incomplete_utf8_suffix_len(b"\xF0\x9F\x98") == 3


def test_incomplete_suffix_invalid_lead_byte_is_zero():
    # 0xFF is never a valid lead byte -> do not hold back.
    assert _incomplete_utf8_suffix_len(b"\xFF") == 0
    assert _incomplete_utf8_suffix_len(b"a\xFF") == 0


def test_incomplete_suffix_bare_continuation_is_zero():
    # A bare continuation byte (0x80–0xBF) with no lead -> never valid.
    assert _incomplete_utf8_suffix_len(b"\x80") == 0


def test_incomplete_suffix_complete_then_orphan_continuation_is_zero():
    # "a\xC3\x80\x80": À (valid) followed by an orphan continuation —
    # the slice does NOT end on an incomplete sequence, so 0.
    assert _incomplete_utf8_suffix_len(b"a\xC3\x80\x80") == 0


# ---------------------------------------------------------------------------
# PartialResultPayload — deny_unknown_fields strict decode.
# ---------------------------------------------------------------------------


def test_payload_round_trip_all_fields():
    p = PartialResultPayload(
        delta="chunk", total_bytes=99, truncated=True, gap=True
    )
    d = p.to_dict()
    assert d == {
        "delta": "chunk",
        "total_bytes": 99,
        "truncated": True,
        "gap": True,
    }
    back = PartialResultPayload.from_dict(d)
    assert back.delta == "chunk"
    assert back.total_bytes == 99
    assert back.truncated is True
    assert back.gap is True


def test_payload_decodes_with_optional_flags_absent():
    # Wire tolerance: a payload missing the bool fields still decodes
    # (serde defaults), matching the ToolCapabilities convention.
    p = PartialResultPayload.from_dict({"delta": "x", "total_bytes": 1})
    assert p.delta == "x"
    assert p.total_bytes == 1
    assert not p.truncated
    assert not p.gap


def test_payload_rejects_unknown_field():
    # Strict (deny_unknown_fields): a stale/typo'd field — e.g. a removed
    # `accumulation` from an un-updated producer — is a hard error.
    with pytest.raises(ValueError):
        PartialResultPayload.from_dict(
            {
                "delta": "x",
                "total_bytes": 1,
                "truncated": False,
                "gap": False,
                "accumulation": "append",
            }
        )


def test_payload_requires_delta_and_total_bytes():
    with pytest.raises(ValueError):
        PartialResultPayload.from_dict({"total_bytes": 1})
    with pytest.raises(ValueError):
        PartialResultPayload.from_dict({"delta": "x"})


def test_payload_defaults_flags_false():
    p = PartialResultPayload(delta="x", total_bytes=1)
    assert p.truncated is False
    assert p.gap is False


# ---------------------------------------------------------------------------
# stream_chunk — no new bytes.
# ---------------------------------------------------------------------------


def test_no_new_bytes_returns_none_and_leaves_last_total():
    spec = _spec_with(None)
    last = [10]
    assert stream_chunk(spec, b"abc", 10, last, False) is None
    assert stream_chunk(spec, b"abc", 5, last, False) is None
    assert last == [10], "last_total is untouched when total does not advance"


# ---------------------------------------------------------------------------
# stream_chunk — suffix delta advances last_total in place.
# ---------------------------------------------------------------------------


def test_emits_suffix_delta_and_advances_last_total():
    spec = _spec_with(None)
    last = [2]
    # total 2 -> 5: 3 genuinely-new bytes, all present in the tail suffix.
    p = _run(spec, b"abcde", 5, last, False)
    assert p.delta == "cde"
    assert p.total_bytes == 5
    assert not p.gap
    assert not p.truncated
    assert last == [5], "last_total advanced in place"


# ---------------------------------------------------------------------------
# stream_chunk — gap when new exceeds surviving tail.
# ---------------------------------------------------------------------------


def test_gap_set_when_new_exceeds_surviving_tail():
    # 100 new bytes but only a 4-byte tail survived upstream: the middle
    # was dropped, so the whole tail is emitted with gap = True.
    spec = _spec_with(None)
    last = [0]
    p = _run(spec, b"tail", 100, last, False)
    assert p.delta == "tail"
    assert p.total_bytes == 100
    assert p.gap
    assert not p.truncated, "a per-tick gap must not set cumulative truncated"
    assert last == [100], "fully-emitted gap delta consumes the total"


def test_truncated_is_caller_supplied_and_distinct_from_gap():
    spec = _spec_with(None)
    last = [0]
    # Caller reports cumulative upstream truncation; no per-tick gap here.
    p = _run(spec, b"abc", 3, last, True)
    assert p.truncated, "caller's cumulative flag passes through verbatim"
    assert not p.gap, "no tail overflow this tick"


# ---------------------------------------------------------------------------
# stream_chunk — UTF-8 safe slicing.
# ---------------------------------------------------------------------------


def test_append_multibyte_split_across_ticks_is_held_back_and_reassembled():
    # Tick 1 delivers "aé" cut mid-'é' (0xC3 without 0xA9). The lone lead
    # byte is held back, NOT emitted as U+FFFD.
    spec = _spec_with(None)
    last = [0]
    p = _run(spec, b"a\xC3", 2, last, False)
    assert p.delta == "a", "incomplete UTF-8 suffix held back"
    assert last == [1], "last_total advances only past emitted bytes"

    # Tick 2: the continuation byte arrives; the held bytes re-slice from
    # the tail and the char comes out whole.
    p = _run(spec, "aé".encode(), 3, last, False)
    assert p.delta == "é", "held bytes reassemble into a whole char"
    assert last == [3]


def test_append_over_cap_defers_remainder_to_next_call_without_loss():
    # Cap 4: a 9-byte burst is paced out over capped frames; nothing is
    # dropped and the concatenation is lossless.
    spec = _spec_with(4)
    last = [0]
    out = ""
    while last[0] < 9:
        p = _run(spec, b"abcdefghi", 9, last, False)
        assert len(p.delta) <= 4, "every frame respects the cap"
        out += p.delta
    assert out == "abcdefghi", "deferred remainders are all emitted"
    assert last == [9]


def test_append_cap_cut_respects_utf8_boundaries():
    # 7 ASCII bytes + 'é' (2 bytes) = 9 bytes. A cap of 8 would split the
    # 'é'; the cut backs off and the 'é' is deferred whole.
    tail = "aaaaaaaé".encode()
    spec = _spec_with(8)
    last = [0]
    p = _run(spec, tail, len(tail), last, False)
    assert p.delta == "aaaaaaa", "backed off the split multibyte char"
    p = _run(spec, tail, len(tail), last, False)
    assert p.delta == "é"


# ---------------------------------------------------------------------------
# stream_chunk — limit / latency invariants.
# ---------------------------------------------------------------------------


def test_drains_backlog_in_minimum_ticks():
    # A backlog drains in exactly ceil(new / cap) calls — no extra round-trips.
    cap = 4
    spec = _spec_with(cap)
    data = b"abcdefghij"
    total = len(data)
    last = [0]
    ticks = 0
    out = ""
    while last[0] < total:
        out += _run(spec, data, total, last, False).delta
        ticks += 1
        assert ticks <= 100, "must terminate"
    assert out == "abcdefghij", "lossless"
    assert ticks == (len(data) + cap - 1) // cap, "no extra ticks beyond ceil"


def test_ascii_frames_fill_to_cap():
    # ASCII frames fill to the cap — no under-fill, no empty trailing frame.
    spec = _spec_with(4)
    data = b"abcdefgh"
    last = [0]
    assert _run(spec, data, 8, last, False).delta == "abcd"
    assert _run(spec, data, 8, last, False).delta == "efgh"
    assert stream_chunk(spec, data, 8, last, False) is None, (
        "no spurious empty trailing frame"
    )


def test_exact_cap_emits_single_frame():
    # A delta exactly at the cap is one frame, no gap, nothing deferred.
    cap = 8
    spec = _spec_with(cap)
    last = [0]
    p = _run(spec, b"abcdefgh", cap, last, False)
    assert p.delta == "abcdefgh"
    assert not p.gap
    assert last == [cap]
    assert stream_chunk(spec, b"abcdefgh", cap, last, False) is None


def test_tiny_cap_below_char_still_makes_progress():
    # A cap smaller than one char still emits a whole char — never stalls.
    spec = _spec_with(1)
    last = [0]
    p = _run(spec, "é".encode(), 2, last, False)
    assert p.delta == "é", "emits the whole first char despite cap < charlen"
    assert last == [2], "and makes forward progress"


def test_utf8_backoff_stays_within_three_bytes_of_cap():
    # UTF-8 backoff loses at most 3 bytes, so frames stay within 3 of the cap.
    cap = 7  # splits a 4-byte char -> backs off to 4 (cap - 3)
    spec = _spec_with(cap)
    data = "😀😀😀😀".encode()
    total = len(data)
    last = [0]
    while last[0] < total:
        p = _run(spec, data, total, last, False)
        # Rust delta.len() is bytes — re-encode to stay byte-accurate.
        n = len(p.delta.encode("utf-8"))
        assert n % 4 == 0 and n >= 4, f"emits whole 4-byte chars, got {n}"
        assert n >= cap - 3, f"frame stays within 3 bytes of the cap, got {n}"
        assert n <= cap, f"frame respects the cap, got {n}"
    assert last == [total], "drains losslessly"


def test_gap_with_cap_paces_surviving_tail_and_terminates():
    # A gap drains only the surviving tail; never re-scans the dropped middle.
    spec = _spec_with(4)
    tail = b"abcdefgh"
    total = 1000  # only 8 of 1000 bytes survived in the tail
    last = [0]
    ticks = 0
    emitted = 0
    saw_gap = False
    while last[0] < total:
        progress = stream_chunk(spec, tail, total, last, False)
        if progress is None:
            break
        assert progress.kind == "custom"
        p = PartialResultPayload.from_dict(progress.payload)
        saw_gap = saw_gap or p.gap
        emitted += len(p.delta.encode("utf-8"))
        ticks += 1
        assert ticks <= 4, (
            "gap pacing must drain only the surviving tail, "
            "not re-scan the dropped middle"
        )
    assert saw_gap, "first frame reports the gap"
    assert emitted == len(tail), (
        "emits exactly the surviving tail bytes — no replay of dropped middle"
    )


# ---------------------------------------------------------------------------
# stream_chunk — subkind stamping on the ToolProgress.Custom envelope.
# ---------------------------------------------------------------------------


def test_stream_chunk_stamps_subkind_on_envelope():
    spec = StreamingSpec(subkind="bash_output_chunk", max_delta_bytes=None)
    last = [0]
    progress = stream_chunk(spec, b"hi", 2, last, False)
    assert progress is not None
    assert progress.kind == "custom"
    assert progress.subkind == "bash_output_chunk"
    # Payload is the wire dict (not yet wrapped in PartialResultPayload).
    payload = PartialResultPayload.from_dict(progress.payload)
    assert payload.delta == "hi"
    assert payload.total_bytes == 2
