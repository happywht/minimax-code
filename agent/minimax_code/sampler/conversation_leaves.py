"""Conversation-layer pure leaves (R217, ``xai-grok-sampling-types``
``conversation.rs`` first slice).

R217 opens the ``conversation.rs`` migration (the 9481-line API-agnostic
conversation-representation mega-module) with its three zero-dependency pure
leaves. The ``types.rs`` 1030-1521 band was exhausted at R216 (every remaining
candidate carries an un-landed blocking dep -- ``ToolDefinition`` /
``crate::rs::ResponseFormat`` / ``crate::messages::MessagesRequest`` /
``Box<dyn TraceContext>`` YAGNI); this round pivots to strategy D and mines the
``conversation.rs`` leaf layer for symbols whose dependency closure is empty.

The three landed symbols:

1. :func:`reported_cost_ticks` (``conversation.rs`` ~732) -- the cost-ticks
   normalizer. A pure ``Option<i64>`` filter (``raw.filter(|&t| t > 0)``): a
   present positive ``int`` -> itself; ``None`` / ``0`` / negative / non-int /
   ``bool`` -> ``None``. The REST layer backfills ``0`` for "unreported cost"
   and negative ticks are never valid, so both collapse to ``None``
   ("unreported", never "free"). Every ingestion path must route through this
   before storing :attr:`ConversationResponse.cost_usd_ticks` (the consumer
   struct lands in a later round).
2. :func:`truncate_bytes` (``conversation.rs`` ~1624) -- the UTF-8
   char-boundary-safe byte truncator. ``s.len() <= max_bytes`` -> ``s``
   unchanged; otherwise the prefix is cut at ``max_bytes`` bytes and walked back
   to the last UTF-8 char boundary (a multi-byte char split at the cut is
   dropped, mirroring ``while !s.is_char_boundary(end) { end -= 1 }``). A
   ``max_bytes <= 0`` (or a non-int / ``bool`` -- impossible in grok's
   ``usize``) -> the empty string.
3. :class:`DanglingToolCallReason` (``conversation.rs`` ~2753) -- the
   "why was this tool call left dangling" tagged union, the ``reason`` parameter
   of the later :func:`repair_dangling_tool_calls`. ``#[derive(Debug, Clone,
   Copy)]`` only -- NO ``Serialize`` / ``Deserialize``, so this is a pure
   in-program enum (it never crosses the wire and has no ``from_payload``).
   Two variants: :class:`UserCancelled` (Ctrl+C / abort / unknown cause, the
   default fallback) + :class:`HarnessHalted` (the harness stopped the turn --
   internal error / policy guard -- carrying a ``class`` taxonomy tag).

Dependency closure: zero external for all three. ``reported_cost_ticks`` +
``truncate_bytes`` are free functions over builtins (``int`` / ``str``);
``DanglingToolCallReason`` is a self-contained union base + 2 subclass variants
(the same frozen+slots union pattern the R201 :class:`StopReason` uses).

Naming: the union keeps its grok name verbatim at the base +
``UserCancelled``; the ``HarnessHalted.class`` field is renamed ``class_`` --
``class`` is a Python hard keyword (it is NOT a Rust keyword, so grok uses it
freely), and the trailing-underscore dodge is PEP 8's canonical fix. The
``StopReason`` enum (``conversation.rs`` ~606) is deferred to a later cluster
round: it collides with the already-migrated R201 :class:`StopReason`
(Anthropic Messages API, structurally distinct) at the barrel surface, and its
``From<FinishReason>`` impl + ``TokenUsage`` sibling form a "response stop +
usage" cluster that lands together once :class:`Usage` migrates.

This module is no-I/O (pure value-level). Migration map (grok -> Python):

- ``fn reported_cost_ticks(raw: Option<i64>) -> Option<i64>`` ->
  :func:`reported_cost_ticks`: ``raw.filter(|&t| t > 0)`` -> a ``bool``-excluding
  ``int`` filter (``bool`` is an ``int`` subclass in Python but grok ``i64``
  rejects a JSON ``true``); a present ``int > 0`` -> the value, anything else
  -> ``None``.
- ``fn truncate_bytes(s: &str, max_bytes: usize) -> &str`` ->
  :func:`truncate_bytes`: ``s.len() <= max_bytes`` -> ``s``; else the
  ``max_bytes``-byte prefix walked back to the last UTF-8 char boundary
  (``errors="ignore"`` drops a split trailing multi-byte char, equivalent to
  the ``is_char_boundary`` walk-back); ``max_bytes <= 0`` / non-int / ``bool``
  -> ``""`` (``usize`` admits no negative / non-int value).
- ``enum DanglingToolCallReason { UserCancelled, HarnessHalted { class } }``
  -> :class:`DanglingToolCallReason` frozen+slots union base +
  :class:`UserCancelled` (field-less) + :class:`HarnessHalted` (``class`` ->
  ``class_`` ``str`` field). No ``from_payload`` (the enum is not serialized).

YAGNI: ``Serialize`` / ``Deserialize`` round-trip for the union (grok itself
does not derive them); the ``repair_dangling_tool_calls`` consumer (it mutates
a ``Vec<ConversationItem>``, which depends on the un-migrated
:class:`ConversationItem` union -- lands in a later round).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def reported_cost_ticks(raw: Any) -> int | None:
    """Normalize a wire cost-ticks value at capture.

    Mirrors grok ``raw.filter(|&t| t > 0)``: a present positive ``int`` ->
    itself; ``None`` / ``0`` / negative / non-int / ``bool`` -> ``None``. The
    REST layer backfills ``0`` for unreported cost and negative ticks are never
    valid, so both become ``None`` ("unreported", never "free"). ``bool`` is an
    ``int`` subclass in Python but grok ``i64`` rejects a JSON ``true`` -- the
    explicit exclusion mirrors that."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if raw > 0 else None


def truncate_bytes(s: str, max_bytes: int) -> str:
    """Truncate ``s`` to at most ``max_bytes`` UTF-8 bytes on a char boundary.

    Mirrors grok ``truncate_bytes``: if the UTF-8 encoding is already within
    ``max_bytes``, return ``s`` unchanged; otherwise cut the prefix at
    ``max_bytes`` bytes and walk back to the last char boundary (a multi-byte
    char split at the cut is dropped -- ``errors="ignore"`` is equivalent to
    the ``while !s.is_char_boundary(end) { end -= 1 }`` walk-back). A
    ``max_bytes <= 0`` (or a non-int / ``bool``, impossible in grok's
    ``usize``) -> the empty string."""
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
        # usize admits only a non-negative int; bool / non-int is impossible.
        return ""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    if max_bytes <= 0:
        return ""
    # Slice at max_bytes, then drop any trailing split multi-byte char
    # (errors="ignore" == the is_char_boundary walk-back to a valid prefix).
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


@dataclass(frozen=True, slots=True)
class DanglingToolCallReason:
    """Why a tool call was left without a result (tagged-union base).

    ``#[derive(Debug, Clone, Copy)]`` only -- NO serde, so this is a pure
    in-program enum (never crosses the wire, no ``from_payload``). The
    ``reason`` parameter of the later :func:`repair_dangling_tool_calls`.
    Use a concrete variant: :class:`UserCancelled` (default fallback) or
    :class:`HarnessHalted` (carries a taxonomy tag)."""


@dataclass(frozen=True, slots=True)
class UserCancelled(DanglingToolCallReason):
    """User pressed Ctrl+C / aborted, or the cause cannot be determined.

    Default fallback when no more specific reason is plumbed through."""


@dataclass(frozen=True, slots=True)
class HarnessHalted(DanglingToolCallReason):
    """Harness halted the turn (internal error, policy guard, etc.).

    ``class_`` (grok ``class``; renamed -- ``class`` is a Python hard keyword,
    not a Rust one) is a stable taxonomy tag used by metrics and the synthetic
    message; every call site is known at compile time in grok
    (``&'static str``)."""

    class_: str


__all__ = [
    "DanglingToolCallReason",
    "HarnessHalted",
    "UserCancelled",
    "reported_cost_ticks",
    "truncate_bytes",
]
