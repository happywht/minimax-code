"""Tests for ``minimax_code.computer_hub_sdk.pool`` (R143).

Mirrors grok-build's ``xai-computer-hub-sdk/src/pool.rs`` (344 lines) -- the
SDK crate's 11th leaf. Process-wide connection pool keyed by ``(url, principal)``:
dedup-on-hit, open-on-miss, race-resolve, ABA-safe ``forget_if``, unused+idle
eviction, and a background reaper. The pool's bookkeeping is exercised
through an injected fake opener so no real WebSocket is opened (the concrete
``HubConnection::connect`` lives in ``connection.rs``, a later leaf).

Python-specific adjustments (no behavior change):

* ``Arc<HubConnection>`` strong-count semantics -> an explicit ``in_use``
  borrow counter on the pooled entry; ``Arc::strong_count == 1`` (only the
  pool holds it) is reproduced as ``in_use == 0``. Tests probe ``in_use``
  directly (private member access; SLF001 is off the ruff select list).
* ``tokio::spawn`` + ``interval`` reaper -> ``asyncio.Task``; the reaper test
  only checks that a cancellable task is returned (the actual sweep cadence
  needs real wall-clock sleeps, deliberately not exercised here).
* ``#[tokio::test(start_paused = true)]`` virtual time -> real
  ``time.monotonic()``; idle entries are constructed with a back-dated
  ``last_handout`` so ``sweep_idle`` is deterministic without sleeping.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

import minimax_code.computer_hub_sdk.pool as pool_mod
from minimax_code.computer_hub_sdk.auth import AuthProvider, PrincipalKey
from minimax_code.computer_hub_sdk.error import InsecureScheme, InvalidConfig
from minimax_code.computer_hub_sdk.pool import (
    DEFAULT_POOL_IDLE_TTL,
    ConnKey,
    HubConnectionPool,
    PooledConnection,
    _Pooled,
    host_is_loopback,
    shared,
)
from minimax_code.tool_protocol.connection import ConnectionKind


# ---------------------------------------------------------------------------
# fixtures + helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_pool_globals() -> None:
    # The process singleton (_SHARED) and default-opener slot (_DEFAULT_OPENER)
    # would leak across tests; reset both before and after each test so every
    # test sees a clean global state.
    pool_mod._SHARED = None
    pool_mod._DEFAULT_OPENER = None
    yield
    pool_mod._SHARED = None
    pool_mod._DEFAULT_OPENER = None


class _FakeCredential:
    """Minimal :class:`AuthProvider` stand-in keyed on a fingerprint (R143)."""

    def __init__(self, fingerprint: str = "fp-1") -> None:
        self._pk = PrincipalKey(fingerprint=fingerprint)

    def current(self) -> None:
        return None

    def principal_key(self) -> PrincipalKey:
        return self._pk

    def identity(self) -> None:
        return None


def _cred(fingerprint: str = "fp-1") -> AuthProvider:
    return _FakeCredential(fingerprint)


def _fake_conn(kind: ConnectionKind = ConnectionKind.Harness) -> object:
    # Duck-typed HubConnection: any object with a kind() method satisfies the
    # runtime-checkable Protocol. SimpleNamespace keeps the tests dependency-free.
    return SimpleNamespace(kind=lambda: kind)


def _key(url: str = "wss://svc.example.com", cred: AuthProvider | None = None) -> ConnKey:
    return ConnKey(url=url, principal=(cred or _cred()).principal_key())


async def _noop_opener(key: ConnKey, cred: AuthProvider, kind: ConnectionKind) -> object:
    return _fake_conn(kind)


# ---------------------------------------------------------------------------
# host_is_loopback -- the scheme-guard predicate (connection.rs helper, local copy)
# ---------------------------------------------------------------------------
def test_host_is_loopback_classifies_loopback_and_public() -> None:
    assert host_is_loopback("ws://localhost:8080") is True
    assert host_is_loopback("ws://127.0.0.1:8080") is True
    assert host_is_loopback("ws://[::1]:8080") is True
    assert host_is_loopback("wss://example.com:443") is False
    # A URL with no parseable host fails closed (treated as non-loopback) so
    # the caller lands in InsecureScheme rather than silently allowing it.
    assert host_is_loopback("not a url") is False


# ---------------------------------------------------------------------------
# get_or_connect -- miss opens + inserts
# ---------------------------------------------------------------------------
async def test_get_or_connect_opens_on_miss_and_inserts() -> None:
    pool = HubConnectionPool.new(opener=_noop_opener)
    cred = _cred()
    key = _key(cred=cred)

    pc = await pool.get_or_connect("wss://svc.example.com", cred, ConnectionKind.Harness)

    assert isinstance(pc, PooledConnection)
    assert pc.conn is pool._connections[key].conn, "fresh connection stored under key"
    assert pool._connections[key].in_use == 1, "hand-out increments the borrow count"
    assert len(pool) == 1


# ---------------------------------------------------------------------------
# get_or_connect -- hit dedups (no reopen) and refreshes last_handout
# ---------------------------------------------------------------------------
async def test_get_or_connect_dedups_on_hit_without_reopening() -> None:
    calls = 0

    async def counting_opener(key: ConnKey, cred: AuthProvider, kind: ConnectionKind) -> object:
        nonlocal calls
        calls += 1
        return _fake_conn(kind)

    pool = HubConnectionPool.new(opener=counting_opener)
    cred = _cred()

    pc1 = await pool.get_or_connect("wss://svc.example.com", cred, ConnectionKind.Harness)
    pc2 = await pool.get_or_connect("wss://svc.example.com", cred, ConnectionKind.Harness)

    assert calls == 1, "second acquire must reuse the pooled connection, not reopen"
    assert pc1.conn is pc2.conn
    assert len(pool) == 1


async def test_get_or_connect_refreshes_last_handout_on_hit() -> None:
    pool = HubConnectionPool.new(opener=_noop_opener)
    cred = _cred()
    key = _key(cred=cred)

    await pool.get_or_connect("wss://svc.example.com", cred, ConnectionKind.Harness)
    pool._connections[key].last_handout = 0.0  # back-date to the epoch

    await pool.get_or_connect("wss://svc.example.com", cred, ConnectionKind.Harness)

    assert pool._connections[key].last_handout > 0.0, "hit must refresh last_handout"


# ---------------------------------------------------------------------------
# get_or_connect -- kind mismatch on hit is a caller error
# ---------------------------------------------------------------------------
async def test_get_or_connect_kind_mismatch_on_hit_raises_invalid_config() -> None:
    pool = HubConnectionPool.new(opener=_noop_opener)
    cred = _cred()

    await pool.get_or_connect("wss://svc.example.com", cred, ConnectionKind.Harness)

    with pytest.raises(InvalidConfig):
        await pool.get_or_connect("wss://svc.example.com", cred, ConnectionKind.ToolServer)


# ---------------------------------------------------------------------------
# get_or_connect -- race loser adopts the winner and drops its fresh connection
# ---------------------------------------------------------------------------
async def test_get_or_connect_race_loser_adopts_winner_and_drops_loser() -> None:
    winner = _fake_conn()
    loser = _fake_conn()
    pool = HubConnectionPool.new()  # opener set below
    cred = _cred()
    key = _key(cred=cred)

    async def racey_opener(k: ConnKey, c: AuthProvider, kind: ConnectionKind) -> object:
        # Simulate another caller winning the race WHILE we await the open:
        # it inserts the winner entry, so our freshly-opened `loser` must be
        # dropped and the winner adopted (entry().or_insert_with semantics).
        pool._connections[key] = _Pooled(
            conn=winner, last_handout=time.monotonic(), in_use=0
        )
        return loser

    pool._opener = racey_opener

    pc = await pool.get_or_connect("wss://svc.example.com", cred, ConnectionKind.Harness)

    assert pc.conn is winner, "race loser adopts the winner's connection"
    assert pool._connections[key].conn is winner
    assert pool._connections[key].in_use == 1, "winner's borrow count increments"
    assert len(pool) == 1, "no duplicate entry"


# ---------------------------------------------------------------------------
# scheme guard -- plaintext ws:// to non-loopback is rejected
# ---------------------------------------------------------------------------
async def test_insecure_non_loopback_ws_rejected() -> None:
    pool = HubConnectionPool.new(opener=_noop_opener)
    with pytest.raises(InsecureScheme):
        await pool.get_or_connect("ws://example.com:80", _cred(), ConnectionKind.Harness)


async def test_loopback_ws_allowed() -> None:
    pool = HubConnectionPool.new(opener=_noop_opener)
    cred = _cred()
    pc = await pool.get_or_connect("ws://localhost:8080", cred, ConnectionKind.Harness)
    assert isinstance(pc, PooledConnection)
    assert len(pool) == 1


async def test_insecure_ws_allowed_with_explicit_opt_in() -> None:
    pool = HubConnectionPool.new(opener=_noop_opener)
    pc = await pool.get_or_connect(
        "ws://example.com:80", _cred(), ConnectionKind.Harness, allow_insecure_ws=True
    )
    assert isinstance(pc, PooledConnection)


# ---------------------------------------------------------------------------
# opener boundary -- no opener registered -> InvalidConfig (YAGNI: connection.rs)
# ---------------------------------------------------------------------------
async def test_get_or_connect_without_opener_raises_invalid_config() -> None:
    # Neither a pool opener nor the module default is set -> the open path is
    # unavailable (connection.rs not yet migrated). Fail fast with a clear
    # InvalidConfig instead of a cryptic AttributeError.
    pool = HubConnectionPool.new()
    with pytest.raises(InvalidConfig):
        await pool.get_or_connect("wss://svc.example.com", _cred(), ConnectionKind.Harness)


# ---------------------------------------------------------------------------
# sweep_idle -- evict unused + idle; keep in-use; keep recently handed out
# ---------------------------------------------------------------------------
def test_sweep_idle_evicts_unused_and_idle() -> None:
    pool = HubConnectionPool.new()
    key = _key()
    pool._connections[key] = _Pooled(
        conn=_fake_conn(), last_handout=time.monotonic() - 999.0, in_use=0
    )

    evicted = pool.sweep_idle(DEFAULT_POOL_IDLE_TTL)

    assert evicted == 1
    assert key not in pool._connections


def test_sweep_idle_keeps_in_use_connections() -> None:
    # A connection with a live consumer (in_use > 0) is never evictable, no
    # matter how long since last_handout -- mirrors Arc::strong_count > 1.
    pool = HubConnectionPool.new()
    key = _key()
    pool._connections[key] = _Pooled(
        conn=_fake_conn(), last_handout=time.monotonic() - 999.0, in_use=1
    )

    evicted = pool.sweep_idle(DEFAULT_POOL_IDLE_TTL)

    assert evicted == 0
    assert key in pool._connections


def test_sweep_idle_keeps_recently_handed_out() -> None:
    pool = HubConnectionPool.new()
    key = _key()
    pool._connections[key] = _Pooled(
        conn=_fake_conn(), last_handout=time.monotonic(), in_use=0
    )

    evicted = pool.sweep_idle(DEFAULT_POOL_IDLE_TTL)

    assert evicted == 0, "idle window not yet elapsed"
    assert key in pool._connections


# ---------------------------------------------------------------------------
# forget / forget_if -- explicit eviction (forget_if is ABA-safe)
# ---------------------------------------------------------------------------
def test_forget_removes_entry() -> None:
    pool = HubConnectionPool.new()
    key = _key()
    pool._connections[key] = _Pooled(conn=_fake_conn(), last_handout=time.monotonic())

    pool.forget(key)

    assert key not in pool._connections
    assert pool.is_empty()


def test_forget_if_predicate_false_keeps_entry() -> None:
    pool = HubConnectionPool.new()
    key = _key()
    conn = _fake_conn()
    pool._connections[key] = _Pooled(conn=conn, last_handout=time.monotonic())

    removed = pool.forget_if(key, predicate=lambda c: c is not conn)

    assert removed is False
    assert key in pool._connections


def test_forget_if_predicate_true_removes_entry() -> None:
    pool = HubConnectionPool.new()
    key = _key()
    conn = _fake_conn()
    pool._connections[key] = _Pooled(conn=conn, last_handout=time.monotonic())

    removed = pool.forget_if(key, predicate=lambda c: c is conn)

    assert removed is True
    assert key not in pool._connections


# ---------------------------------------------------------------------------
# PooledConnection.release -- decrements in_use, idempotent
# ---------------------------------------------------------------------------
async def test_release_decrements_in_use_and_is_idempotent() -> None:
    pool = HubConnectionPool.new(opener=_noop_opener)
    cred = _cred()
    key = _key(cred=cred)

    pc = await pool.get_or_connect("wss://svc.example.com", cred, ConnectionKind.Harness)

    assert pool._connections[key].in_use == 1
    pc.release()
    assert pool._connections[key].in_use == 0
    pc.release()  # idempotent: a second release does not underflow
    assert pool._connections[key].in_use == 0


async def test_pooled_connection_async_context_releases_on_exit() -> None:
    pool = HubConnectionPool.new(opener=_noop_opener)
    cred = _cred()
    key = _key(cred=cred)

    pc = await pool.get_or_connect("wss://svc.example.com", cred, ConnectionKind.Harness)
    async with pc as conn:
        # `async with pc as conn` yields the underlying HubConnection; the
        # coroutine must be awaited first to obtain the PooledConnection handle.
        assert conn.kind() == ConnectionKind.Harness
        assert pool._connections[key].in_use == 1

    assert pool._connections[key].in_use == 0, "exit releases the slot"


# ---------------------------------------------------------------------------
# shared -- the process singleton inits once and spawns a single reaper
# ---------------------------------------------------------------------------
async def test_shared_returns_same_instance_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    # Avoid real reaper side-effects: capture the spawn call instead of
    # creating a long-lived asyncio task that would outlive the test.
    spawned: list[tuple[float, float]] = []
    monkeypatch.setattr(
        HubConnectionPool,
        "spawn_idle_reaper",
        lambda self, idle_ttl, sweep_interval: spawned.append((idle_ttl, sweep_interval)),
    )

    p1 = await shared()
    p2 = await shared()

    assert p1 is p2, "singleton initializes exactly once"
    assert len(spawned) == 1, "reaper spawned exactly once on first init"
    assert spawned[0] == (DEFAULT_POOL_IDLE_TTL, 60.0)


# ---------------------------------------------------------------------------
# spawn_idle_reaper -- returns a cancellable background task (Weak-ref shape)
# ---------------------------------------------------------------------------
async def test_spawn_idle_reaper_returns_cancellable_task() -> None:
    pool = HubConnectionPool.new()
    task = pool.spawn_idle_reaper(DEFAULT_POOL_IDLE_TTL, 3600.0)

    assert isinstance(task, asyncio.Task)
    assert not task.done()

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.cancelled()
