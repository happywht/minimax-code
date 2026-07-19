"""Canonical partial-result streaming contract (R111).

Fusion of grok-build's ``xai-tool-runtime/src/streaming.rs``. A tool
declares a :class:`~minimax_code.tool_protocol.capabilities.StreamingSpec`
in its capabilities and emits deltas from ``execute`` via
:func:`stream_chunk`, which materialises the spec into a
:class:`PartialResultPayload` carried by ``ToolProgress.Custom``.
Downstream layers dispatch on the envelope's ``subkind`` rather than on the
tool's identity.

Deltas are **append-only and lossless**: when a delta would end mid-way
through a multi-byte UTF-8 sequence, or exceeds the per-frame cap, the
excess bytes are held back — ``last_total`` advances only past the emitted
bytes, so the next call re-slices the remainder from the (still-growing)
tail. Concatenated deltas are therefore always valid UTF-8 and lossless.

No circular dependency: this module consumes :class:`StreamingSpec`
(R86, :mod:`minimax_code.tool_protocol.capabilities`) and ``ToolProgress``
(R109, :mod:`minimax_code.tool_runtime.tool`) one-way. It is a leaf in the
tool_runtime barrel and the unblocker for the streaming tool-execution
path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minimax_code.tool_protocol.capabilities import StreamingSpec
from minimax_code.tool_runtime.tool import ToolProgress

__all__ = [
    "DEFAULT_MAX_DELTA_BYTES",
    "PartialResultPayload",
    "stream_chunk",
]

#: Per-frame ``delta`` byte cap used when
#: :attr:`StreamingSpec.max_delta_bytes` is unset. Guards against a single
#: oversized tick flooding the harness in one frame. Deliberately
#: independent of ``ToolCapabilities.max_frame_bytes``, which caps whole
#: frames (16 MiB ceiling), not deltas.
DEFAULT_MAX_DELTA_BYTES: int = 16 * 1024

#: Known wire fields of :class:`PartialResultPayload` (used for the
#: ``deny_unknown_fields`` strict-decode check).
_PAYLOAD_KNOWN_FIELDS: frozenset[str] = frozenset(
    {"delta", "total_bytes", "truncated", "gap"}
)


@dataclass
class PartialResultPayload:
    """Canonical payload carried by a streaming tool's ``ToolProgress.Custom``.

    Downstream layers dispatch on the envelope's ``subkind``. Deltas are
    append-only and lossless (see :func:`stream_chunk`).

    Parsed strictly (Rust ``#[serde(deny_unknown_fields)]``): an unexpected
    field is a hard decode error rather than being silently ignored, so
    producer/consumer schema drift (e.g. a stale field from an un-updated
    producer) is caught instead of misinterpreted. This is the deliberate
    opposite of ``render.ToolChatCompletion``'s ``#[serde(flatten)] extra``
    leniency: partial results are a tight wire contract where unknown keys
    signal a bug, while chat-completion extras are an open extension point.
    """

    #: Content produced since the previous tick (the delta).
    delta: str
    #: Monotonic total bytes produced so far (NOT the current buffer length).
    total_bytes: int
    #: Cumulative content was lost upstream and will never be delivered
    #: (distinct from a single-tick ``gap``).
    truncated: bool = False
    #: This delta has a gap: a single oversized tick overflowed the tail
    #: buffer and its middle was dropped.
    gap: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the wire dict (all four fields always emitted)."""
        return {
            "delta": self.delta,
            "total_bytes": self.total_bytes,
            "truncated": self.truncated,
            "gap": self.gap,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PartialResultPayload:
        """Strict decode (``deny_unknown_fields``): unknown keys raise."""
        unknown = set(data) - _PAYLOAD_KNOWN_FIELDS
        if unknown:
            raise ValueError(
                f"unknown PartialResultPayload fields: {sorted(unknown)}"
            )
        if "delta" not in data or "total_bytes" not in data:
            raise ValueError(
                "PartialResultPayload requires 'delta' and 'total_bytes'"
            )
        return cls(
            delta=str(data["delta"]),
            total_bytes=int(data["total_bytes"]),
            truncated=bool(data.get("truncated", False)),
            gap=bool(data.get("gap", False)),
        )


def _incomplete_utf8_suffix_len(data: bytes) -> int:
    """Byte count of an incomplete UTF-8 sequence at the end of ``data``, else 0.

    Mirrors Rust's ``incomplete_utf8_suffix_len``: returns 0 when the slice
    ends on a complete sequence, OR on invalid bytes that can never become
    valid (those are surfaced lossily instead of held forever). Only a
    genuinely incomplete (still-arriving) multi-byte sequence at the very
    end returns its held-back length.

    Implementation walks back over trailing continuation bytes
    (``10xxxxxx``) to the lead byte, then compares the observed run length
    against the lead byte's expected sequence length. This matches
    ``std::str::from_utf8``'s ``error_len().is_none()`` branch (the only
    case worth holding back) without needing the stdlib's exact error
    introspection.
    """
    n = len(data)
    if n == 0:
        return 0
    i = n - 1
    while i > 0 and (data[i] & 0xC0) == 0x80:
        i -= 1
    lead = data[i]
    if (lead & 0x80) == 0x00:  # 0xxxxxxx — ASCII
        expected = 1
    elif (lead & 0xE0) == 0xC0:  # 110xxxxx
        expected = 2
    elif (lead & 0xF0) == 0xE0:  # 1110xxxx
        expected = 3
    elif (lead & 0xF8) == 0xF0:  # 11110xxx
        expected = 4
    else:
        # Invalid lead byte (0xF8–0xFF) or a bare continuation byte at
        # index 0 — never valid, do not hold back.
        return 0
    actual = n - i
    if actual < expected:
        return actual  # incomplete sequence at the end -> hold back
    return 0  # the final sequence is complete; nothing to hold


def stream_chunk(
    spec: StreamingSpec,
    tail: bytes,
    total: int,
    last_total: list[int],
    truncated: bool,
) -> ToolProgress | None:
    """Build at most one ``ToolProgress.Custom`` delta from a monotonic byte source.

    UTF-8-safe slicing at both the tick boundary and the per-frame cap.

    ``tail`` is the source's (possibly truncated) tail buffer — the newest
    bytes are always at its end. ``total`` is the monotonic count of bytes
    produced so far; ``last_total`` records how much has already been
    surfaced and is **advanced in place** — it is a single-element ``list``
    simulating Rust's ``&mut u64`` (the caller passes ``[value]`` and the
    function mutates ``last_total[0]``). Returns ``None`` when ``total``
    has not advanced (no new bytes).

    Deltas are **append-only and lossless**: when a delta would end mid-way
    through a multi-byte UTF-8 sequence, or exceeds the per-frame cap
    (:attr:`StreamingSpec.max_delta_bytes`, default 16 KiB), the excess
    bytes are *held back* — ``last_total[0]`` advances only past the
    emitted bytes, so the next call re-slices the remainder from the
    (still-growing) tail.

    ``truncated`` is the caller's cumulative upstream-truncation flag
    (e.g. a source that hit a hard output cap and will never deliver the
    elided bytes). It is copied into the payload verbatim and is
    intentionally distinct from the per-tick ``gap`` (a single oversized
    tick overflowed the tail buffer and its middle was dropped upstream).
    Sources with no cumulative-truncation notion pass ``False``.
    """
    if total <= last_total[0]:
        return None
    new = total - last_total[0]
    tail_len = len(tail)
    # Deltas are keyed off the monotonic `total`, not the buffer length:
    # when all genuinely-new bytes still fit in the tail we slice its
    # suffix; when a single tick's burst exceeded the buffer the middle was
    # dropped upstream, so we emit what survived plus a `gap` marker.
    if new <= tail_len:
        delta_bytes = tail[tail_len - new :]
        gap = False
    else:
        delta_bytes = tail
        gap = True

    cap = (
        spec.max_delta_bytes
        if spec.max_delta_bytes is not None
        else DEFAULT_MAX_DELTA_BYTES
    )

    # Defer: emit the longest prefix that fits the cap AND ends on a
    # complete UTF-8 sequence; hold the rest back for the next call (the
    # tail still contains it, since last_total only advances past the
    # emitted bytes). Nothing is dropped.
    cut = min(len(delta_bytes), cap)
    while cut > 0 and _incomplete_utf8_suffix_len(delta_bytes[:cut]) > 0:
        cut -= 1
    # A cap smaller than one multi-byte char would deadlock at cut == 0
    # while bytes remain; emit the full first char in that pathological
    # case rather than stalling forever.
    if cut == 0 and delta_bytes:
        cut = min(len(delta_bytes), 4)
        while (
            cut < len(delta_bytes)
            and _incomplete_utf8_suffix_len(delta_bytes[:cut]) > 0
        ):
            cut += 1
    if cut == 0:
        return None

    delta = delta_bytes[:cut].decode("utf-8", errors="replace")
    consumed = cut

    # Advance only past what was emitted (gap case: the upstream-dropped
    # middle counts as consumed — those bytes can never be re-sliced).
    if gap:
        last_total[0] = total - (
            len(delta_bytes) - min(consumed, len(delta_bytes))
        )
    else:
        last_total[0] = last_total[0] + consumed

    payload = PartialResultPayload(
        delta=delta,
        total_bytes=total,
        truncated=truncated,
        gap=gap,
    )
    return ToolProgress.Custom(spec.subkind, payload.to_dict())
