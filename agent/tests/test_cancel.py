"""Tests for ``minimax_code.computer_hub_sdk.cancel`` (R141).

Mirrors grok-build's ``xai-computer-hub-sdk/src/cancel.rs`` (301 lines) --
the SDK crate's 9th leaf. A near-total forward-port: :class:`CancelRegistry`
is a pure data structure whose Rust concurrency primitives (``DashMap`` /
``DashSet`` / ``AtomicBool`` / ``CancellationToken``) map cleanly to
``dict`` / ``set`` / ``bool`` / :class:`asyncio.Event` (the Event mapping
established by R138 ``connection_borrow``). All 8 Rust tests port directly.

Python-specific adjustments (no behavior change):

* ``cid()`` uses fixed ``ToolCallId("call-N")`` strings for determinism
  (the Rust tests use ``ToolCallId::new_v7()`` UUIDs); indices keep the ids
  unique within each test.
* ``token.is_cancelled()`` -> ``event.is_set()``; ``CancellationToken::new()``
  -> ``asyncio.Event()``.
"""

from __future__ import annotations

import asyncio

from minimax_code.computer_hub_sdk.cancel import (
    MAX_PENDING_TOMBSTONES,
    CancelRegistry,
)
from minimax_code.tool_protocol.ids import ToolCallId


def _cid(i: int = 0) -> ToolCallId:
    # Rust uses ToolCallId::new_v7(); Python uses fixed strings for
    # deterministic tests (matching the existing tool_protocol test style).
    return ToolCallId(f"call-{i}")


def _token() -> asyncio.Event:
    return asyncio.Event()


# ---------------------------------------------------------------------------
# cancel -- live hit fires + removes the entry
# ---------------------------------------------------------------------------
def test_cancel_live_token_fires_and_removes_entry() -> None:
    reg = CancelRegistry()
    call_id = _cid()
    token = _token()
    assert reg.register(call_id, token) is False, "fresh register, no tombstone"
    assert reg.live_count() == 1

    assert reg.cancel(call_id) is True, "live token must report a hit"
    assert token.is_set(), "the registered token must be cancelled"
    assert reg.live_count() == 0, "cancel removes the live entry"
    assert reg.pending_count() == 0, "a live hit leaves no tombstone"


# ---------------------------------------------------------------------------
# cancel before registration -> tombstone -> register pre-cancels
# ---------------------------------------------------------------------------
def test_cancel_before_registration_tombstones_then_register_pre_cancels() -> None:
    reg = CancelRegistry()
    call_id = _cid()

    # Cancel arrives first: no live token, so it tombstones.
    assert reg.cancel(call_id) is False, "no live token yet -> miss"
    assert reg.pending_count() == 1
    assert reg.live_count() == 0

    # Registration consumes the tombstone and starts cancelled.
    token = _token()
    assert reg.register(call_id, token) is True, "register must report the pre-cancel"
    assert token.is_set(), "tombstone must pre-cancel the token"
    assert reg.pending_count() == 0, "tombstone consumed at registration"
    assert reg.live_count() == 1


# ---------------------------------------------------------------------------
# deregister -- clear live entry WITHOUT cancelling
# ---------------------------------------------------------------------------
def test_deregister_clears_live_entry_without_cancel() -> None:
    reg = CancelRegistry()
    call_id = _cid()
    token = _token()
    reg.register(call_id, token)

    reg.deregister(call_id)
    assert reg.live_count() == 0
    assert not token.is_set(), "deregister on normal completion must NOT cancel the token"
    # A later cancel for a completed call only tombstones (harmless).
    assert reg.cancel(call_id) is False
    assert reg.pending_count() == 1


# ---------------------------------------------------------------------------
# cancel_all -- drain + cancel every live token (idempotent)
# ---------------------------------------------------------------------------
def test_cancel_all_drains_and_cancels_every_live_token() -> None:
    reg = CancelRegistry()
    ids = [_cid(i) for i in range(5)]
    tokens: list[asyncio.Event] = []
    for call_id in ids:
        t = _token()
        reg.register(call_id, t)
        tokens.append(t)
    assert reg.live_count() == 5

    assert reg.cancel_all() == 5, "cancel_all reports every drained token"
    assert reg.live_count() == 0, "registry is empty after teardown"
    for token in tokens:
        assert token.is_set(), "every live token must be cancelled"
    # Idempotent: a second teardown cancels nothing.
    assert reg.cancel_all() == 0


# ---------------------------------------------------------------------------
# register after cancel_all -> starts cancelled (teardown race regression)
# ---------------------------------------------------------------------------
def test_register_after_cancel_all_starts_cancelled() -> None:
    # Teardown race regression: once cancel_all has closed the registry, a
    # call dispatched in the teardown window must start cancelled and must
    # NOT linger as a live, uncancellable entry.
    reg = CancelRegistry()
    assert reg.cancel_all() == 0, "empty teardown cancels nothing"

    call_id = _cid()
    token = _token()
    assert (
        reg.register(call_id, token) is True
    ), "register on a closed registry must report pre-cancel"
    assert token.is_set(), "a call dispatched after teardown must start cancelled"
    assert (
        reg.live_count() == 0
    ), "a closed-registry register must not leave a live (orphan) entry"


# ---------------------------------------------------------------------------
# register without tombstone -> does not cancel
# ---------------------------------------------------------------------------
def test_register_without_tombstone_does_not_cancel() -> None:
    reg = CancelRegistry()
    call_id = _cid()
    token = _token()
    assert reg.register(call_id, token) is False
    assert not token.is_set(), "a clean registration must leave the token live"


# ---------------------------------------------------------------------------
# cancel_all clears pending tombstones
# ---------------------------------------------------------------------------
def test_cancel_all_clears_pending_tombstones() -> None:
    reg = CancelRegistry()
    reg.cancel(_cid(1))
    reg.cancel(_cid(2))
    assert reg.pending_count() == 2
    reg.cancel_all()
    assert reg.pending_count() == 0, "teardown must drop pending tombstones"


# ---------------------------------------------------------------------------
# pending tombstones stay bounded under spurious cancels
# ---------------------------------------------------------------------------
def test_pending_tombstones_stay_bounded_under_spurious_cancels() -> None:
    # A long-lived session that keeps receiving cancels for call_ids that
    # never register (e.g. cancels racing call completion) must not grow
    # `pending` without bound.
    reg = CancelRegistry()
    for i in range(MAX_PENDING_TOMBSTONES + 256):
        assert reg.cancel(_cid(i)) is False, "never-registered id is a miss"
    assert reg.pending_count() <= MAX_PENDING_TOMBSTONES, (
        f"tombstone set must stay within its cap, got {reg.pending_count()}"
    )
