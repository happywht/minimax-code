"""Tests for ``minimax_code.computer_hub_sdk.admission`` (R142).

Mirrors grok-build's ``xai-computer-hub-sdk/src/admission.rs`` (333 lines) --
the SDK crate's 10th leaf. Three-tier semaphore admission + bounded-wait
backpressure. Most-local-first acquisition (session -> conn -> global) is
deadlock-free; a single shared deadline bounds total admission latency.

Python-specific adjustments (no behavior change):

* ``#[tokio::test(start_paused = true)]`` freezes virtual time at the 150ms
  deadline; Python cannot freeze the event loop, so the timeout tests use a
  real 50ms ``wait_timeout`` (:func:`asyncio.wait_for`). The
  saturation/timeout semantics are identical, just wall-clock instead of
  virtual.
* ``OwnedSemaphorePermit`` RAII drop -> explicit :meth:`AdmitGuard.release`
  (Python has no drop); tests call ``release()`` where Rust uses ``drop(g)``.
* ``tokio::sync::Semaphore::close`` -> :meth:`_ClosableSemaphore.close`
  (asyncio's Semaphore has no close); test 6 imports the private wrapper.
"""

from __future__ import annotations

from minimax_code.computer_hub_sdk.admission import (
    TOOL_BUSY_CODE,
    Admission,
    AdmitGuard,
    Overloaded,
    _ClosableSemaphore,
    overloaded_response,
    resolve_global_cap,
)
from minimax_code.tool_protocol.envelope import JsonRpcIdString
from minimax_code.tool_protocol.ids import SessionId


def _sid(s: str) -> SessionId:
    return SessionId(s)


def _test_admission(
    session_max: int, conn_max: int, global_max: int
) -> Admission:
    # 50ms wait_timeout (real wall-clock; Rust uses 150ms virtual under
    # start_paused). Short enough to keep the suite fast, long enough that
    # a ready permit does not false-timeout under CI jitter.
    return Admission(
        session_max,
        conn_max,
        _ClosableSemaphore(global_max),
        0.05,
    )


# ---------------------------------------------------------------------------
# resolve_global_cap -- pure env parser (fall-back-never-panic)
# ---------------------------------------------------------------------------
def test_resolve_global_cap_falls_back_on_bad_input_and_honors_valid() -> None:
    # Absent / non-numeric / negative / empty / zero / whitespace-padded -> default.
    assert resolve_global_cap(None, 1024) == 1024
    assert resolve_global_cap("abc", 1024) == 1024
    assert resolve_global_cap("-5", 1024) == 1024
    assert resolve_global_cap("", 1024) == 1024
    assert resolve_global_cap("0", 1024) == 1024
    assert resolve_global_cap("  7", 1024) == 1024  # leading space -> parse fails
    # A valid positive integer overrides the default.
    assert resolve_global_cap("2048", 1024) == 2048
    assert resolve_global_cap("1", 1024) == 1


# ---------------------------------------------------------------------------
# overloaded_response -- the single source of the -32016 wire shape
# ---------------------------------------------------------------------------
def test_overloaded_response_carries_minus_32016_and_data_marker() -> None:
    resp = overloaded_response(JsonRpcIdString.new_string("call-1"), _sid("s1"))
    wire = resp.to_wire()
    assert wire["error"]["code"] == TOOL_BUSY_CODE
    assert wire["error"]["code"] == -32016
    assert wire["error"]["data"]["code"] == "tool_busy"
    assert wire["error"]["data"]["retryable"] is True
    assert wire["session_id"] == "s1"
    assert wire["id"] == "call-1"
    assert "result" not in wire, "overload is an error, never a result"


# ---------------------------------------------------------------------------
# admit -- session saturation -> Timeout
# ---------------------------------------------------------------------------
async def test_admit_times_out_when_session_saturated() -> None:
    admission = _test_admission(2, 16, 64)
    session = _sid("sat")
    admission.ensure_session(session)

    # Hold both session permits.
    g1 = await admission.admit(session)
    assert isinstance(g1, AdmitGuard)
    g2 = await admission.admit(session)
    assert isinstance(g2, AdmitGuard)

    # Third admit must elapse the deadline -> Timeout (not a hang).
    result = await admission.admit(session)
    assert isinstance(result, Overloaded)
    assert result == Overloaded.TIMEOUT

    # Releasing one permit frees a slot for the next admit.
    g1.release()
    g3 = await admission.admit(session)
    assert isinstance(g3, AdmitGuard), "permit released -> admit succeeds"


# ---------------------------------------------------------------------------
# admit -- connection scope binds across sessions
# ---------------------------------------------------------------------------
async def test_admit_blocks_on_connection_scope_when_conn_saturated() -> None:
    # conn_max = 1 is the binding constraint even though session has room;
    # a second admit on a *different* session still times out.
    admission = _test_admission(8, 1, 64)
    a = _sid("a")
    b = _sid("b")
    admission.ensure_session(a)
    admission.ensure_session(b)

    held = await admission.admit(a)
    assert isinstance(held, AdmitGuard)
    result = await admission.admit(b)
    assert isinstance(result, Overloaded)
    assert result == Overloaded.TIMEOUT, "connection cap binds across sessions"


# ---------------------------------------------------------------------------
# admit -- succeeds repeatedly under capacity
# ---------------------------------------------------------------------------
async def test_admit_succeeds_repeatedly_under_capacity() -> None:
    admission = _test_admission(4, 16, 64)
    session = _sid("ok")
    admission.ensure_session(session)
    guards: list[AdmitGuard] = []
    for _ in range(4):
        g = await admission.admit(session)
        assert isinstance(g, AdmitGuard), "within capacity"
        guards.append(g)
    assert len(guards) == 4


# ---------------------------------------------------------------------------
# admit -- closed semaphore -> Shutdown
# ---------------------------------------------------------------------------
async def test_closed_semaphore_maps_to_shutdown() -> None:
    global_sem = _ClosableSemaphore(0)
    global_sem.close()
    admission = Admission(4, 16, global_sem, 5.0)
    session = _sid("closed")
    admission.ensure_session(session)
    result = await admission.admit(session)
    assert isinstance(result, Overloaded)
    assert result == Overloaded.SHUTDOWN


# ---------------------------------------------------------------------------
# admit -- straggler after remove uses a private fallback permit
# ---------------------------------------------------------------------------
async def test_straggler_admit_after_remove_uses_private_permit() -> None:
    admission = _test_admission(1, 16, 64)
    session = _sid("gone")
    # No ensure_session: simulate a straggler after unbind removed it.
    admission.remove_session(session)
    # Falls back to a private semaphore and still admits (no panic, no
    # leaked tracked entry).
    g = await admission.admit(session)
    assert isinstance(g, AdmitGuard), "private fallback"
    assert (
        session not in admission._session_sems
    ), "straggler must not recreate a tracked entry"
