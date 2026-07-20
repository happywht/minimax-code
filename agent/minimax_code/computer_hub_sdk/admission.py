"""Three-tier semaphore admission + bounded-wait backpressure (R142).

Fusion of grok-build's ``xai-computer-hub-sdk/src/admission.rs`` (333 lines).
This is the SDK crate's 10th leaf (after R133 error / R134 handshake / R135
refcount / R136 donate_pump / R137 trace_donate / R138 connection_borrow /
R139 auth / R140 observability / R141 cancel): bounds concurrent *running*
tool calls at three scopes acquired in a fixed **session -> connection ->
global** order. A consistent most-local-first order is deadlock-free (never
holds a scarce global permit while blocking on a local one). A single shared
deadline spans all three acquisitions, so total admission latency is bounded
by ``wait_timeout``, not ``3 x wait_timeout``.

Under moderate pressure ``admit`` waits; under very high pressure the
deadline elapses and the caller emits the shared overloaded JSON-RPC error
(``-32016`` "tool_busy") via :func:`overloaded_response` instead of silently
dropping the request.

What migrates vs what does NOT (R132-style YAGNI boundary declaration)
---------------------------------------------------------------------

MIGRATED (transport-agnostic admission logic):

* Five default constants + ``TOOL_BUSY_CODE``/``MESSAGE`` + ``SCOPES`` +
  ``GLOBAL_MAX_INFLIGHT_ENV``.
* :class:`Overloaded` enum (Timeout / Shutdown).
* :func:`resolve_global_cap` -- pure env-var parser (fall-back-never-panic).
* :func:`overloaded_response` -- the single source of the ``-32016`` wire
  shape (consumes R84 envelope types).
* :class:`_ClosableSemaphore` -- a closeable counting semaphore. asyncio's
  :class:`asyncio.Semaphore` has NO ``close()`` semantics, so a lightweight
  wrapper is needed to reproduce the Rust ``Semaphore::close`` teardown path
  (the Shutdown rejection) faithfully.
* :class:`AdmitGuard` -- holds the three acquired permits; :meth:`release`
  frees them in reverse acquisition order (global -> conn -> session).
* :class:`Admission` -- per-connection three-tier controller:
  :meth:`ensure_session` / :meth:`remove_session` / :meth:`admit`.
* :func:`global_semaphore` -- process-wide singleton with env override.

NOT MIGRATED (framework glue with no Python equivalent yet):

* ``crate::metrics::tool_call_inflight_inc`` / ``..._dec`` /
  ``admission_wait_observe`` -- the inflight gauges + admission-wait
  histogram live in ``metrics.rs`` (552 lines), a later leaf. They are
  replaced here by no-op stubs whose signatures are preserved so the
  metrics leaf replaces the bodies without touching call sites (the same
  YAGNI pattern R140 established for ``session_event``).

Python-specific adaptations (no behavior change):

* ``tokio::time::timeout_at(deadline, fut)`` -> :func:`asyncio.wait_for`
  against a ``remaining`` computed from a shared ``time.monotonic``
  deadline. Rust tests use ``#[tokio::test(start_paused = true)]`` to
  freeze virtual time at the 150ms deadline; Python cannot freeze the
  event loop, so the tests use a real short timeout (50ms) instead.
* ``OwnedSemaphorePermit`` RAII drop -> explicit :meth:`AdmitGuard.release`;
  callers release in a ``finally`` block. :meth:`Admission.admit` rolls back
  any already-acquired permits if a later scope fails (Rust relies on drop
  on the ``?`` error path).
* ``OnceLock<Arc<Semaphore>>`` -> a module-level ``Optional`` singleton
  (asyncio is single-threaded, so no lock is needed around first-use init).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from enum import StrEnum
from typing import Any

from minimax_code.tool_protocol.envelope import (
    JsonRpcError,
    JsonRpcId,
    JsonRpcResponse,
    JsonRpcVersion,
    ResponseError,
)
from minimax_code.tool_protocol.ids import SessionId

__all__ = [
    "TOOL_BUSY_CODE",
    "TOOL_BUSY_MESSAGE",
    "DEFAULT_GLOBAL_MAX_INFLIGHT",
    "DEFAULT_SESSION_MAX_INFLIGHT",
    "DEFAULT_CONN_MAX_INFLIGHT",
    "DEFAULT_ADMISSION_WAIT_TIMEOUT",
    "GLOBAL_MAX_INFLIGHT_ENV",
    "SCOPES",
    "Overloaded",
    "AdmitGuard",
    "Admission",
    "overloaded_response",
    "global_semaphore",
    "resolve_global_cap",
]

#: Numeric JSON-RPC code for overload rejection
#: (``xai-tool-protocol error_codes.rs``: ``-32016`` "tool_busy").
TOOL_BUSY_CODE: int = -32016

TOOL_BUSY_MESSAGE: str = "tool server busy; tool call rejected"

#: Default ceiling for the process-wide concurrency guard.
DEFAULT_GLOBAL_MAX_INFLIGHT: int = 1024
#: Default per-session concurrent running calls.
DEFAULT_SESSION_MAX_INFLIGHT: int = 16
#: Default per-connection concurrent running calls.
DEFAULT_CONN_MAX_INFLIGHT: int = 256
#: Default bounded wait (seconds) before an overloaded rejection.
DEFAULT_ADMISSION_WAIT_TIMEOUT: float = 3.0

#: Ops-tunable override for the process-wide global cap (Helm ``env:``).
GLOBAL_MAX_INFLIGHT_ENV: str = "XAI_TOOL_SERVER_GLOBAL_MAX_INFLIGHT"

#: Inflight-gauge scope labels, in acquisition order. A held
#: :class:`AdmitGuard` counts against all three.
SCOPES: tuple[str, ...] = ("session", "conn", "global")


def _metrics_tool_call_inflight_inc(scope: str) -> None:
    """YAGNI boundary: ``crate::metrics::tool_call_inflight_inc`` (R142).

    No-op until the ``metrics.rs`` leaf (552 lines) lands. Signature matches
    the Rust call so the metrics leaf replaces the body without touching
    :meth:`Admission.admit` / :meth:`AdmitGuard.release` call sites.
    """
    return None


def _metrics_tool_call_inflight_dec(scope: str) -> None:
    """YAGNI boundary: ``crate::metrics::tool_call_inflight_dec`` (R142)."""
    return None


def _metrics_admission_wait_observe(elapsed_seconds: float) -> None:
    """YAGNI boundary: ``crate::metrics::admission_wait_observe`` (R142)."""
    return None


class _ClosableSemaphore:
    """A closeable counting semaphore (R142 internal).

    asyncio's :class:`asyncio.Semaphore` has no ``close()`` semantics, but
    the SDK's teardown path needs to reject new admissions with
    :attr:`Overloaded.SHUTDOWN` when a semaphore closes (e.g. the
    process-wide global sem on shutdown). This wrapper reproduces Rust's
    ``tokio::sync::Semaphore::close``: a closed semaphore immediately
    rejects all waiters (and future acquirers) by returning ``False``.

    :meth:`acquire` returns ``True`` on success, ``False`` if the semaphore
    was closed (either before or during the wait). :meth:`release` is a
    no-op once closed (matching Rust, where releasing into a closed sem is
    benign). Single-threaded asyncio means no internal locking is needed;
    the only await point is the per-waiter future, and the value/mutex
    mutations between await points are atomic.
    """

    def __init__(self, value: int) -> None:
        self._value = value
        self._waiters: deque[asyncio.Future[None]] = deque()
        self._closed = False

    def close(self) -> None:
        """Mark closed and wake every waiter so it observes the close."""
        self._closed = True
        for fut in self._waiters:
            if not fut.done():
                fut.set_result(None)  # signal "recheck"
        self._waiters.clear()

    async def acquire(self) -> bool:
        """Acquire one permit; ``True`` on success, ``False`` if closed (R142).

        Loops on wake: a wake from :meth:`release` means recheck the value
        (and claim a permit if one is now free); a wake from :meth:`close`
        means recheck ``closed`` and return ``False``.
        """
        while True:
            if self._closed:
                return False
            if self._value > 0:
                self._value -= 1
                return True
            # Block until woken (by release or close), then recheck.
            fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiters.append(fut)
            try:
                await fut
            finally:
                try:
                    self._waiters.remove(fut)
                except ValueError:
                    pass

    def release(self) -> None:
        """Release one permit; no-op if the semaphore is closed (R142)."""
        if self._closed:
            return
        self._value += 1
        # Wake one waiter to recheck the value.
        while self._waiters:
            fut = self._waiters.popleft()
            if not fut.done():
                fut.set_result(None)
                return


def overloaded_response(id_: JsonRpcId, session_id: SessionId) -> JsonRpcResponse[Any]:
    """Build the shared overloaded (``-32016`` "tool_busy") JSON-RPC error response (R142).

    The single source of the overload wire shape, reused by BOTH the
    admission-timeout path (``server::execute_call``) and the demux
    inbox-full path (``demux::route_session``) so the two never drift.
    """
    return JsonRpcResponse(
        jsonrpc=JsonRpcVersion(),
        id=id_,
        session_id=session_id,
        outcome=ResponseError(
            error=JsonRpcError(
                code=TOOL_BUSY_CODE,
                message=TOOL_BUSY_MESSAGE,
                data={"code": "tool_busy", "retryable": True},
            )
        ),
    )


def resolve_global_cap(raw: str | None, default_cap: int) -> int:
    """Resolve the process-wide global cap from the raw env value (R142).

    Pure (no global state) so the fall-back-never-panic guarantee is
    unit-tested: a non-numeric, negative, empty, whitespace-padded, or zero
    value all yield ``default_cap``. Mirrors Rust ``usize::from_str`` strict
    semantics (no leading/trailing whitespace, no sign) -- Python ``int()``
    accepts both ``"  7"`` and ``"+7"``, so an explicit ``str.isdigit()`` +
    ``str.isascii()`` gate reproduces the strict parse (and also rejects
    unicode digits, which ``int()`` would accept).
    """
    if raw is None:
        return default_cap
    if not raw or not raw.isascii() or not raw.isdigit():
        return default_cap
    n = int(raw)
    return n if n > 0 else default_cap


# Process-wide global admission semaphore singleton (mirrors Rust OnceLock).
# ``None`` until first-use init; asyncio is single-threaded so the first-use
# read+init is atomic (no concurrent caller can interleave mid-init).
_GLOBAL_SEM: _ClosableSemaphore | None = None


def global_semaphore(default_cap: int) -> _ClosableSemaphore:
    """Process-wide global admission semaphore, shared by every connection (R142).

    Initialized once at first use: the value comes from
    :data:`GLOBAL_MAX_INFLIGHT_ENV` when present and parseable as a positive
    integer, otherwise ``default_cap`` (the builder knob, default
    :data:`DEFAULT_GLOBAL_MAX_INFLIGHT`). Because the singleton initializes
    exactly once, the first caller's ``default_cap`` and the env var at that
    instant fix the process-wide capacity (mirrors Rust ``OnceLock``).
    """
    global _GLOBAL_SEM
    if _GLOBAL_SEM is None:
        raw = os.environ.get(GLOBAL_MAX_INFLIGHT_ENV)
        _GLOBAL_SEM = _ClosableSemaphore(resolve_global_cap(raw, default_cap))
    return _GLOBAL_SEM


class Overloaded(StrEnum):
    """Why admission was refused (R142).

    :attr:`TIMEOUT` -- the bounded admission deadline elapsed under very high
    pressure. :attr:`SHUTDOWN` -- a semaphore was closed; the server is
    shutting down.
    """

    TIMEOUT = "timeout"
    SHUTDOWN = "shutdown"


class AdmitGuard:
    """Guard holding all three permits for the call's lifetime (R142).

    Permits release in reverse of acquisition: global -> connection ->
    session (mirrors Rust's declaration-order drop). Python has no RAII drop,
    so callers MUST invoke :meth:`release` (typically in a ``finally`` block);
    :meth:`release` is idempotent. The inflight-gauge decrement fires on
    :meth:`release` (YAGNI stub until the metrics leaf).
    """

    def __init__(
        self,
        session_sem: _ClosableSemaphore,
        conn_sem: _ClosableSemaphore,
        global_sem: _ClosableSemaphore,
    ) -> None:
        self._session = session_sem
        self._conn = conn_sem
        self._global = global_sem
        self._released = False

    def release(self) -> None:
        """Release all three permits (idempotent); reverse acquisition order (R142).

        Decrements the inflight gauge for each scope, then frees the permits
        global -> conn -> session so a scarce local permit is freed last.
        """
        if self._released:
            return
        self._released = True
        for scope in SCOPES:
            _metrics_tool_call_inflight_dec(scope)
        self._global.release()
        self._conn.release()
        self._session.release()


async def _acquire_until(
    sem: _ClosableSemaphore, deadline: float
) -> Overloaded | None:
    """Acquire one permit before ``deadline`` (R142 internal).

    Returns ``None`` on success, or the matching :class:`Overloaded` variant
    on failure (Timeout if the deadline elapsed, Shutdown if the semaphore
    was closed). Mirrors Rust ``acquire_until``: a single shared deadline
    spans all three scope acquisitions, so the total wait is bounded by
    ``wait_timeout`` rather than ``3 x wait_timeout``.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return Overloaded.TIMEOUT
    try:
        ok = await asyncio.wait_for(sem.acquire(), timeout=remaining)
    except TimeoutError:
        return Overloaded.TIMEOUT
    if not ok:
        return Overloaded.SHUTDOWN
    return None


class Admission:
    """Three-tier admission controller (R142).

    One per connection (``conn_sem``); the per-session map is created/
    destroyed alongside each session loop via :meth:`ensure_session` /
    :meth:`remove_session`. Acquires one permit at each scope
    (session -> connection -> global) against a single shared deadline, so
    total admission latency is bounded by ``wait_timeout``, not
    ``3 x wait_timeout``.
    """

    def __init__(
        self,
        session_max: int,
        conn_max: int,
        global_sem: _ClosableSemaphore,
        wait_timeout: float,
    ) -> None:
        self._session_sems: dict[SessionId, _ClosableSemaphore] = {}
        self._session_max = session_max
        self._conn_sem = _ClosableSemaphore(conn_max)
        self._global_sem = global_sem
        self._wait_timeout = wait_timeout

    def ensure_session(self, session_id: SessionId) -> None:
        """Create the per-session semaphore entry (R142).

        Called from ``bind_session_local`` so the entry's lifetime is tied to
        the session-loop task, not lazily minted in :meth:`admit` (which
        would leak an entry for a straggler call after unbind).
        """
        if session_id not in self._session_sems:
            self._session_sems[session_id] = _ClosableSemaphore(self._session_max)

    def remove_session(self, session_id: SessionId) -> None:
        """Remove the per-session semaphore entry on unbind / loop exit (R142)."""
        self._session_sems.pop(session_id, None)

    async def admit(self, session_id: SessionId) -> AdmitGuard | Overloaded:
        """Acquire one permit at each scope against a single shared deadline (R142).

        Returns an :class:`AdmitGuard` on success, or the matching
        :class:`Overloaded` variant if the deadline elapsed or a semaphore
        was closed. Already-acquired permits are rolled back if a later scope
        fails (Rust relies on ``OwnedSemaphorePermit`` drop on the ``?`` error
        path; Python has no drop, so the rollback is explicit). A straggler
        call admitted just after unbind cleanup falls back to a private,
        un-tracked semaphore rather than recreating a leaked entry.
        """
        start = time.monotonic()
        deadline = start + self._wait_timeout
        session_sem = self._session_sems.get(session_id)
        if session_sem is None:
            # Straggler after unbind: private untracked fallback (no leak).
            session_sem = _ClosableSemaphore(self._session_max)

        err = await _acquire_until(session_sem, deadline)
        if err is not None:
            return err
        err = await _acquire_until(self._conn_sem, deadline)
        if err is not None:
            session_sem.release()
            return err
        err = await _acquire_until(self._global_sem, deadline)
        if err is not None:
            session_sem.release()
            self._conn_sem.release()
            return err

        for scope in SCOPES:
            _metrics_tool_call_inflight_inc(scope)
        _metrics_admission_wait_observe(time.monotonic() - start)
        return AdmitGuard(session_sem, self._conn_sem, self._global_sem)
