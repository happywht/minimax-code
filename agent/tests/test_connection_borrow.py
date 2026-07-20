"""Tests for ``minimax_code.computer_hub_sdk.connection_borrow`` (R138).

Mirrors the LIFECYCLE half of grok-build's
``xai-computer-hub-sdk/src/connection_borrow.rs`` (the ``acquire`` factory
half -- pool + auth + connection framework glue -- is declared out of scope:
pool.rs / auth.rs / connection.rs are later leaves; see the module
docstring's YAGNI boundary).

The at-most-once teardown guard is the pure, pool-agnostic invariant the
server/harness leaves depend on, so it is pinned directly here WITHOUT a
``HubConnectionPool`` or axum mock server (the Rust tests spawn one only to
exercise ``acquire``; the lifecycle policy itself is connection-agnostic):

* :meth:`ConnectionBorrow.begin_teardown` -- first caller wins, the rest
  observe torn-down, the transition is sticky;
* :meth:`ConnectionBorrow.request_shutdown` -- win then set the fence;
* :meth:`ConnectionBorrow.from_connection` -- pool-free factory + accessor;
* :meth:`ConnectionBorrow.shutdown_token` -- the shared shutdown fence.
"""

from __future__ import annotations

import asyncio
import threading

from minimax_code.computer_hub_sdk.connection_borrow import ConnectionBorrow


# ---------------------------------------------------------------------------
# begin_teardown -- at-most-once guard
# ---------------------------------------------------------------------------
def test_begin_teardown_returns_true_once_and_false_after() -> None:
    borrow = ConnectionBorrow.from_connection(object())
    assert borrow.begin_teardown() is True, "first call wins the at-most-once transition"
    assert borrow.begin_teardown() is False, "subsequent calls observe torn-down"
    assert borrow.begin_teardown() is False, "the transition is sticky"


def test_is_torn_down_reflects_begin_teardown() -> None:
    borrow = ConnectionBorrow.from_connection(object())
    assert borrow.is_torn_down is False
    borrow.begin_teardown()
    assert borrow.is_torn_down is True
    borrow.begin_teardown()
    assert borrow.is_torn_down is True


async def test_begin_teardown_is_atomic_under_concurrent_async_callers() -> None:
    # asyncio analogue of the Rust multi_thread test: gather many coroutines
    # that each try to win. Single-threaded, so it pins the at-most-once LOGIC
    # (the threading.Lock's true-concurrency guarantee is pinned separately).
    borrow = ConnectionBorrow.from_connection(object())
    n_callers = 64
    results = await asyncio.gather(
        *(_win(borrow) for _ in range(n_callers))
    )
    wins = sum(1 for r in results if r)
    assert wins == 1, f"exactly one of {n_callers} async callers must win, got {wins}"


async def _win(borrow: ConnectionBorrow) -> bool:
    # yield once so the gather interleaves callers before they race.
    await asyncio.sleep(0)
    return borrow.begin_teardown()


def test_begin_teardown_is_atomic_under_concurrent_threads() -> None:
    # asyncio is single-threaded; this pins the threading.Lock guarantee that
    # makes the CAS safe under REAL concurrency (future-proofing for thread
    # callers). Mirrors the Rust ``multi_thread`` 64-caller -> 1-win test.
    borrow = ConnectionBorrow.from_connection(object())
    n_callers = 32
    barrier = threading.Barrier(n_callers)
    results: list[bool] = [False] * n_callers

    def call(idx: int) -> None:
        barrier.wait()  # release all threads simultaneously
        results[idx] = borrow.begin_teardown()

    threads = [threading.Thread(target=call, args=(i,)) for i in range(n_callers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    wins = sum(1 for r in results if r)
    assert wins == 1, f"exactly one of {n_callers} threads must win, got {wins}"


# ---------------------------------------------------------------------------
# from_connection + connection accessor
# ---------------------------------------------------------------------------
def test_from_connection_exposes_connection_handle() -> None:
    handle = object()
    borrow = ConnectionBorrow.from_connection(handle)
    assert borrow.connection is handle
    assert borrow.is_torn_down is False


# ---------------------------------------------------------------------------
# shutdown_token + request_shutdown
# ---------------------------------------------------------------------------
def test_shutdown_token_unset_until_request_shutdown_wins() -> None:
    borrow = ConnectionBorrow.from_connection(object())
    token = borrow.shutdown_token()
    assert token.is_set() is False
    assert borrow.request_shutdown() is True
    assert token.is_set() is True


def test_request_shutdown_returns_false_after_first_winner() -> None:
    borrow = ConnectionBorrow.from_connection(object())
    assert borrow.request_shutdown() is True
    # second caller loses AND must not have re-set an already-set fence
    assert borrow.request_shutdown() is False
    assert borrow.shutdown_token().is_set() is True


def test_shutdown_token_is_shared_reference() -> None:
    borrow = ConnectionBorrow.from_connection(object())
    token_a = borrow.shutdown_token()
    token_b = borrow.shutdown_token()
    assert token_a is token_b, "shutdown_token() returns the shared Event by reference"
    borrow.request_shutdown()
    assert token_a.is_set() and token_b.is_set()


# ---------------------------------------------------------------------------
# __repr__
# ---------------------------------------------------------------------------
def test_repr_exposes_torn_down_state() -> None:
    borrow = ConnectionBorrow.from_connection(object())
    assert "torn_down=False" in repr(borrow)
    borrow.begin_teardown()
    assert "torn_down=True" in repr(borrow)
