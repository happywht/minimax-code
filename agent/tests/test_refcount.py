"""Tests for ``minimax_code.computer_hub_sdk.refcount`` (R135).

Mirrors grok-build's ``xai-computer-hub-sdk/src/refcount.rs`` test suite
verbatim (4 cases). The Rust suite pins the four observable contracts:

* :meth:`RefCountedSet.increment` returns ``(prev, new)`` and drives the
  ``0 -> 1`` register edge;
* :meth:`RefCountedSet.decrement` removes the entry exactly at zero and
  reports the post-decrement count;
* :meth:`RefCountedSet.decrement` on an unknown key is an idempotent
  ``None`` (no spurious unregister);
* :meth:`RefCountedSet.increment` saturates at :data:`U64_MAX` rather
  than rolling over (the wire borrow-count is a ``u64``).

The saturation case pre-loads the internal map at ``U64_MAX - 1`` (the
public API only reaches that region via impossible-in-practice
overflow); it pins the defensive cap so a future edit cannot silently
regress to plain ``+ 1``.
"""

from __future__ import annotations

from minimax_code.computer_hub_sdk.refcount import U64_MAX, RefCountedSet


def test_increment_returns_new_count() -> None:
    set_ = RefCountedSet[str]()
    assert set_.increment("a") == (0, 1)
    assert set_.increment("a") == (1, 2)
    assert set_.increment("b") == (0, 1)
    assert len(set_) == 2


def test_decrement_removes_at_zero() -> None:
    set_ = RefCountedSet[str]()
    set_.increment("a")
    set_.increment("a")
    assert set_.decrement("a") == 1
    assert not set_.is_empty()
    assert set_.decrement("a") == 0
    assert set_.is_empty()


def test_decrement_unknown_returns_none() -> None:
    set_ = RefCountedSet[str]()
    assert set_.decrement("missing") is None


def test_increment_saturates_at_u64_max() -> None:
    set_ = RefCountedSet[str]()
    # Pre-load the entry to MAX-1 via direct map access. The public API
    # only ever reaches this region via overflow, which is impossible in
    # practice; this test pins the saturating defensive line so it
    # can't silently regress to plain ``+ 1``.
    set_._counts["max"] = U64_MAX - 1
    assert set_.increment("max") == (U64_MAX - 1, U64_MAX)
    assert set_.increment("max") == (U64_MAX, U64_MAX)
