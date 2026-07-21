"""Compaction request-header decision kernel (R208,
``xai-grok-sampling-types`` ``types.rs``).

R208 lands the compaction-header decision enums -- two zero-dependency
``#[serde(untagged)]`` newtype-style enums that drive the ``x-compactions-*``
request headers, each carrying a ``resolve()`` decision method. These are the
purest "decision primitive" leaves in the types crate (no wire struct shape,
just a bool/int polymorphic knob + a resolve rule), which is why they close
dependency-free against the std scalars alone:

- :class:`CompactionAtTokens` (``#[serde(untagged)] enum`` + ``resolve``) --
  the ``x-compactions-at-tokens`` header knob. ``Enabled(bool)`` toggles the
  auto-computed value (``true`` -> ``context_window * threshold_percent / 100``,
  ``false`` -> header disabled); ``Fixed(u64)`` sends a constant token count.
  :meth:`resolve` returns the absolute count or ``None`` when disabled.
- :class:`CompactionsRemaining` (``#[serde(untagged)] enum`` + ``resolve``) --
  the ``x-compactions-remaining`` header knob. ``Dynamic(bool)`` toggles the
  dynamic value (``true`` -> ``1`` on the uncompacted prefix, ``0`` once the
  session has a compaction summary; ``false`` -> disabled); ``Fixed(u8)`` sends
  a constant. :meth:`resolve` returns the header value or ``None``.

Dependency closure: zero external (no ``crate::rs``, no ``serde_helpers``, no
R206/R207 leaves -- only ``bool`` / ``u64`` / ``u8`` scalars). The two enums are
the crate's most self-contained decision primitive.

This module is no-I/O. Migration map (grok -> Python):

- ``#[serde(untagged)] enum`` (:class:`CompactionAtTokens` /
  :class:`CompactionsRemaining`) -> frozen+slots union base + subclasses;
  ``from_payload`` matches on JSON scalar shape. serde's ``untagged`` tries the
  variants in declaration order; grok declares ``Enabled(bool)`` /
  ``Dynamic(bool)`` first, then ``Fixed(integer)``. Python mirrors that order
  with the **bool-before-int guard**: ``isinstance(payload, bool)`` MUST be
  tested before ``isinstance(payload, int)`` (``bool`` is a subclass of ``int``
  in Python, so ``isinstance(True, int)`` is ``True`` -- without the guard a
  ``true`` / ``false`` wire value would mis-parse as ``Fixed(1)`` / ``Fixed(0)``).
  Any other shape (string / list / dict / ``None``) raises ``ValueError``
  (mirrors serde's untagged failure -- no catch-all).
- ``Enabled(bool)`` / ``Dynamic(bool)`` tuple variant -> subclass with an
  ``enabled`` / ``dynamic`` bool field (the grok tuple variant is anonymous; the
  field name is descriptive). ``Fixed(u64)`` / ``Fixed(u8)`` -> subclass with a
  ``value`` int field.
- ``impl Enum { pub fn resolve(...) -> Option<...> }`` -> ``resolve()`` instance
  method on the union base (dispatches via ``isinstance``), returning ``int |
  None``. The integer division mirrors grok's unsigned-integer arithmetic
  (``context_window * threshold_percent // 100``); the ``!has_compaction_summary``
  bool-to-u8 coercion mirrors ``u8::from(!bool)`` (``True`` -> 0, ``False`` ->
  1).

Naming: the union bases keep their grok names (no peer collision -- the barrel
holds no other ``Compaction*`` symbol). The variant subclasses take the union
name as a prefix (``CompactionAtTokensEnabled`` / ``CompactionAtTokensFixed`` /
``CompactionsRemainingDynamic`` / ``CompactionsRemainingFixed``) so the barrel
flattens without ambiguity. Note the singular ``CompactionAtTokens`` vs the
plural ``CompactionsRemaining`` -- both are faithful to grok (the ``at-tokens``
header carries one count; the ``remaining`` header carries a count of remaining
compactions).

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip -- ``from_payload``
covers the parse direction the header builder needs; ``resolve`` covers the
decision the platform wires into the request header.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# CompactionAtTokens: x-compactions-at-tokens header knob (untagged bool/int).
# ---------------------------------------------------------------------------
#
# `Enabled(true)` resolves to the auto-computed `context_window * p / 100`;
# `Enabled(false)` (or absent) disables the header; `Fixed(N)` sends the
# constant N. serde tries `Enabled(bool)` before `Fixed(u64)`; `from_payload`
# mirrors that with the bool-before-int guard (bool is a subclass of int in
# Python, so `True` would otherwise mis-parse as `Fixed(1)`).


@dataclass(frozen=True, slots=True)
class CompactionAtTokens:
    """``x-compactions-at-tokens`` header knob union base (untagged on the
    wire -- a JSON bool or integer). Use :meth:`from_payload` for the
    JSON-value -> variant mapping, :meth:`resolve` for the absolute token count
    (or ``None`` when disabled)."""

    @classmethod
    def from_payload(cls, payload: Any) -> CompactionAtTokens:
        """Match on JSON scalar shape (mirrors serde's ``#[serde(untagged)]``
        with ``Enabled(bool)`` declared before ``Fixed(u64)``). A JSON bool ->
        :class:`CompactionAtTokensEnabled`; a JSON integer ->
        :class:`CompactionAtTokensFixed`. The bool test precedes the int test
        (``bool`` subclasses ``int`` -- without the guard ``True`` / ``False``
        would mis-parse as ``Fixed(1)`` / ``Fixed(0)``). Anything else raises
        ``ValueError``."""
        # bool MUST be tested before int: isinstance(True, int) is True.
        if isinstance(payload, bool):
            return CompactionAtTokensEnabled(enabled=payload)
        if isinstance(payload, int):
            return CompactionAtTokensFixed(value=payload)
        raise ValueError(
            f"compaction-at-tokens must be bool or int, got {type(payload).__name__}"
        )

    def resolve(self, context_window: int, threshold_percent: int) -> int | None:
        """Mirror ``CompactionAtTokens::resolve``: ``Enabled(false)`` -> ``None``
        (header disabled); ``Enabled(true)`` -> ``context_window *
        threshold_percent // 100`` (unsigned-integer arithmetic, mirroring
        grok's ``u64`` division); ``Fixed(n)`` -> ``n``."""
        if isinstance(self, CompactionAtTokensEnabled):
            if not self.enabled:
                return None
            return context_window * threshold_percent // 100
        if isinstance(self, CompactionAtTokensFixed):
            return self.value
        raise TypeError(f"unknown CompactionAtTokens variant: {type(self).__name__}")


@dataclass(frozen=True, slots=True)
class CompactionAtTokensEnabled(CompactionAtTokens):
    """The ``Enabled(bool)`` variant -- ``true`` resolves to the auto-computed
    token count, ``false`` disables the header."""

    enabled: bool


@dataclass(frozen=True, slots=True)
class CompactionAtTokensFixed(CompactionAtTokens):
    """The ``Fixed(u64)`` variant -- sends the constant token count ``value``."""

    value: int


# ---------------------------------------------------------------------------
# CompactionsRemaining: x-compactions-remaining header knob (untagged bool/int).
# ---------------------------------------------------------------------------
#
# `Dynamic(true)` resolves to the dynamic value (1 on the uncompacted prefix, 0
# once the session has a compaction summary); `Dynamic(false)` (or absent)
# disables the header; `Fixed(N)` sends the constant N. Same bool-before-int
# untagged parse as :class:`CompactionAtTokens`.


@dataclass(frozen=True, slots=True)
class CompactionsRemaining:
    """``x-compactions-remaining`` header knob union base (untagged on the
    wire -- a JSON bool or integer). Use :meth:`from_payload` for the
    JSON-value -> variant mapping, :meth:`resolve` for the header value (or
    ``None`` when disabled)."""

    @classmethod
    def from_payload(cls, payload: Any) -> CompactionsRemaining:
        """Match on JSON scalar shape (mirrors serde's ``#[serde(untagged)]``
        with ``Dynamic(bool)`` declared before ``Fixed(u8)``). A JSON bool ->
        :class:`CompactionsRemainingDynamic`; a JSON integer ->
        :class:`CompactionsRemainingFixed`. The bool test precedes the int test
        (``bool`` subclasses ``int`` -- without the guard ``True`` / ``False``
        would mis-parse as ``Fixed(1)`` / ``Fixed(0)``). Anything else raises
        ``ValueError``."""
        # bool MUST be tested before int: isinstance(True, int) is True.
        if isinstance(payload, bool):
            return CompactionsRemainingDynamic(dynamic=payload)
        if isinstance(payload, int):
            return CompactionsRemainingFixed(value=payload)
        raise ValueError(
            f"compactions-remaining must be bool or int, got {type(payload).__name__}"
        )

    def resolve(self, has_compaction_summary: bool) -> int | None:
        """Mirror ``CompactionsRemaining::resolve``: ``Dynamic(false)`` ->
        ``None`` (header disabled); ``Dynamic(true)`` -> ``1`` on the
        uncompacted prefix, ``0`` once the session has a compaction summary
        (mirrors grok's ``u8::from(!has_compaction_summary)`` -- ``True`` -> 0,
        ``False`` -> 1); ``Fixed(n)`` -> ``n``."""
        if isinstance(self, CompactionsRemainingDynamic):
            if not self.dynamic:
                return None
            return int(not has_compaction_summary)
        if isinstance(self, CompactionsRemainingFixed):
            return self.value
        raise TypeError(f"unknown CompactionsRemaining variant: {type(self).__name__}")


@dataclass(frozen=True, slots=True)
class CompactionsRemainingDynamic(CompactionsRemaining):
    """The ``Dynamic(bool)`` variant -- ``true`` resolves to the dynamic value
    (1 / 0 by compaction state), ``false`` disables the header."""

    dynamic: bool


@dataclass(frozen=True, slots=True)
class CompactionsRemainingFixed(CompactionsRemaining):
    """The ``Fixed(u8)`` variant -- sends the constant header value ``value``."""

    value: int


__all__ = [
    "CompactionAtTokens",
    "CompactionAtTokensEnabled",
    "CompactionAtTokensFixed",
    "CompactionsRemaining",
    "CompactionsRemainingDynamic",
    "CompactionsRemainingFixed",
]
