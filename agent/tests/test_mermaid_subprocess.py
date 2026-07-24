"""Black-box tests for the migrated subprocess plumbing (R278).

Exercises :mod:`minimax_code.mermaid.subprocess` -- the shared spawn/feed/
wait/reap layer fused from grok ``xai-grok-mermaid/src/subprocess.rs``
(direction (1), brick 10). This is the panic-isolating child runner that backs
the optional ``mmdc`` engine: spawn a child, optionally feed stdin, wait up to
a wall-clock budget, and reap the whole process group on a breach.

Covers the 6 public symbols plus the module-private helpers:

* :class:`SubprocessError` (base) + :class:`SpawnSubprocessError` /
  :class:`TimeoutSubprocessError` / :class:`NonZeroExitSubprocessError` /
  :class:`WaitSubprocessError` (4 variants) -- grok's 4-variant enum expressed
  as a Python subclass tree (R271 ``error.py`` adaptation).
* :func:`run_with_timeout` (grok ``pub fn run_with_timeout`` L53) -- the spawn
  + feed + wait + reap lifecycle. Zero exit / nonzero exit / timeout / missing
  binary / large-stdin-no-deadlock, each a grok test (L197-L295).
* :func:`_reap` (private; grok ``fn reap`` L155) -- group SIGKILL + child kill
  + child wait, exercised directly.
* :func:`_reap_process_group` (private; grok ``fn reap_process_group`` L168) --
  Unix ``killpg`` vs Windows no-op, both code paths forced via
  ``monkeypatch.setattr(sys, "platform", ...)`` so the assertion runs on every
  host (not just the matching OS).
* :func:`_spawn_with_etxtbsy_retry` (private; grok ``fn`` L101) -- the
  ``ETXTBSY`` retry cap (``_ETXTBSY_MAX_ATTEMPTS``) verified by faking a
  persistent ``ETXTBSY`` OSError.

The barrel (:mod:`minimax_code.mermaid`) re-exports the 6 symbols; the barrel
``__all__`` grows 17 -> 23 (grok ``lib.rs`` L57 re-exports ``SubprocessError``
and ``run_with_timeout`` at the crate root).

All child commands use ``sys.executable`` so the tests run cross-platform
(grok's Unix ``true``/``false``/``sleep``/``cat`` have no Windows equivalent).
"""

from __future__ import annotations

import asyncio
import errno
import os
import sys
import time
from types import SimpleNamespace

import pytest

import minimax_code.mermaid as mermaid_barrel
from minimax_code.mermaid import subprocess as subprocess_mod
from minimax_code.mermaid.subprocess import (
    NonZeroExitSubprocessError,
    SpawnSubprocessError,
    SubprocessError,
    TimeoutSubprocessError,
    WaitSubprocessError,
    run_with_timeout,
)

# SIGKILL is 9 on every Unix; Windows lacks the attribute entirely, so the
# Unix code path (forced via monkeypatch) needs it materialised on the signal
# module to resolve ``signal.SIGKILL`` even on a Windows host.
_SIGKILL_VALUE = 9


# === SubprocessError taxonomy (grok enum -> Python subclass tree) =========


def test_all_four_variants_are_subprocess_errors() -> None:
    """Each variant subclasses SubprocessError (grok enum -> Python subclass tree)."""
    assert issubclass(SpawnSubprocessError, SubprocessError)
    assert issubclass(TimeoutSubprocessError, SubprocessError)
    assert issubclass(NonZeroExitSubprocessError, SubprocessError)
    assert issubclass(WaitSubprocessError, SubprocessError)


def test_subprocess_error_base_is_exception() -> None:
    """SubprocessError itself is an Exception subclass (catchable as such)."""
    assert issubclass(SubprocessError, Exception)


def test_spawn_error_str_reproduces_grok_display() -> None:
    """SpawnSubprocessError __str__ mirrors grok #[error("could not spawn...")]."""
    cause = FileNotFoundError("[Errno 2] No such file")
    err = SpawnSubprocessError(cause)
    assert str(err) == "could not spawn child process: [Errno 2] No such file"
    assert err.cause is cause


def test_timeout_error_str_reproduces_grok_display() -> None:
    """TimeoutSubprocessError __str__ mirrors grok #[error("child process timed out")]."""
    assert str(TimeoutSubprocessError()) == "child process timed out"


def test_nonzero_exit_error_str_reproduces_grok_display() -> None:
    """NonZeroExitSubprocessError __str__ mirrors grok #[error("...exited with {0}")]."""
    err = NonZeroExitSubprocessError(42)
    assert str(err) == "child process exited with 42"
    assert err.returncode == 42


def test_wait_error_str_reproduces_grok_display() -> None:
    """WaitSubprocessError __str__ mirrors grok #[error("waiting...failed: {0}")]."""
    cause = ProcessLookupError("gone")
    err = WaitSubprocessError(cause)
    assert str(err) == "waiting on child process failed: gone"
    assert err.cause is cause


# === run_with_timeout: lifecycle (grok tests L197-L295) ==================


async def test_zero_exit_returns_none() -> None:
    """A zero-exit child returns None (grok zero_exit_is_ok)."""
    result = await run_with_timeout([sys.executable, "-c", "pass"], timeout=10.0)
    assert result is None


async def test_nonzero_exit_raises_with_returncode() -> None:
    """A non-zero-exit child raises NonZeroExitSubprocessError (grok nonzero_exit_is_reported)."""
    with pytest.raises(NonZeroExitSubprocessError) as exc_info:
        await run_with_timeout(
            [sys.executable, "-c", "import sys; sys.exit(1)"], timeout=10.0
        )
    assert exc_info.value.returncode == 1


async def test_slow_command_times_out_at_deadline() -> None:
    """A child exceeding its budget is killed at the deadline (grok slow_command_times_out_quickly)."""
    start = time.monotonic()
    with pytest.raises(TimeoutSubprocessError):
        await run_with_timeout(
            [sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.150
        )
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, "must return at the deadline, not wait the full 5s"


async def test_missing_binary_raises_spawn_error() -> None:
    """A binary that does not exist raises SpawnSubprocessError (grok missing_binary_is_spawn_error)."""
    with pytest.raises(SpawnSubprocessError):
        await run_with_timeout(
            ["definitely-not-a-real-binary-9f8a7b6c5d4e"], timeout=10.0
        )


async def test_large_stdin_payload_round_trips_without_deadlock(tmp_path) -> None:
    """A large stdin payload (>pipe buffer) is delivered via the writer task without deadlocking (grok large_stdin_payload_is_delivered_without_deadlock).

    The child echoes stdin to a sink file; the file size proves every byte was
    both delivered and consumed -- the whole reason grok uses a scoped thread
    (here a concurrent asyncio task) instead of a blocking write.
    """
    payload = b"x" * (256 * 1024)  # 256 KiB -- larger than any OS pipe buffer.
    sink = tmp_path / "drained"
    sink_fd = os.open(os.fspath(sink), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        cmd = [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
        ]
        start = time.monotonic()
        await run_with_timeout(
            cmd, stdin_payload=payload, timeout=10.0, stdout=sink_fd
        )
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, "must return after the drain, not after the full timeout"
    finally:
        os.close(sink_fd)
    assert sink.stat().st_size == len(payload), "all stdin bytes round-tripped"


# === _reap: process teardown (grok fn reap L155) ==========================


async def test_reap_terminates_a_live_child() -> None:
    """_reap actually terminates the spawned child (grok reap_terminates_the_process)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(30)",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    pid = proc.pid
    await subprocess_mod._reap(proc)
    assert proc.returncode is not None, "child was reaped (returncode set)"
    # On Unix the pid names no live process (grok's kill(pid, 0) -> ESRCH check).
    if sys.platform != "win32":
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


# === _reap_process_group: platform semantics (grok L168-L181) ============
#
# Both code paths are forced via monkeypatch on sys.platform so the assertion
# runs on every host (not gated on the matching OS -- grok's cfg(unix)/cfg(not)
# branches become testable cross-platform).


def test_reap_process_group_sigkills_group_on_unix_path(monkeypatch) -> None:
    """Forcing the Unix code path: killpg(pid, SIGKILL) is called (grok cfg(unix) branch)."""
    calls: list[tuple[int, int]] = []

    def fake_killpg(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))

    monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(subprocess_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        subprocess_mod.signal, "SIGKILL", _SIGKILL_VALUE, raising=False
    )
    subprocess_mod._reap_process_group(SimpleNamespace(pid=4242))
    assert len(calls) == 1
    assert calls[0] == (4242, _SIGKILL_VALUE)


def test_reap_process_group_skips_killpg_on_windows_path(monkeypatch) -> None:
    """Forcing the Windows code path: killpg is NOT called (grok not(unix) no-op)."""
    called: list[tuple[int, int]] = []

    def fake_killpg(pgid: int, sig: int) -> None:
        called.append((pgid, sig))

    monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(subprocess_mod.sys, "platform", "win32")
    subprocess_mod._reap_process_group(SimpleNamespace(pid=4242))
    assert called == []


def test_reap_process_group_swallows_esrch_on_unix_path(monkeypatch) -> None:
    """killpg raising ProcessLookupError (ESRCH) is swallowed (grok ignores ESRCH)."""

    def fake_killpg(pgid: int, sig: int) -> None:
        raise ProcessLookupError("process gone")

    monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(subprocess_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        subprocess_mod.signal, "SIGKILL", _SIGKILL_VALUE, raising=False
    )
    subprocess_mod._reap_process_group(SimpleNamespace(pid=4242))  # must not raise


def test_reap_process_group_swallows_eperm_on_unix_path(monkeypatch) -> None:
    """killpg raising PermissionError (EPERM) is swallowed (grok ignores EPERM)."""

    def fake_killpg(pgid: int, sig: int) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(subprocess_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        subprocess_mod.signal, "SIGKILL", _SIGKILL_VALUE, raising=False
    )
    subprocess_mod._reap_process_group(SimpleNamespace(pid=4242))  # must not raise


# === _spawn_with_etxtbsy_retry: ETXTBSY retry cap (grok L101-L117) =======


async def test_spawn_retries_etxtbsy_up_to_max_then_propagates(monkeypatch) -> None:
    """Persistent ETXTBSY retries _ETXTBSY_MAX_ATTEMPTS times then raises (grok MAX_ATTEMPTS)."""
    calls: list[int] = []

    async def fake_create_subprocess_exec(*args: object, **kwargs: object) -> None:
        calls.append(1)
        raise OSError(errno.ETXTBSY, "Text file busy")

    async def fake_sleep(delay: float) -> None:
        return None  # zero-delay: avoid the real 20-100ms backoff

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(OSError) as exc_info:
        await subprocess_mod._spawn_with_etxtbsy_retry(
            ["x"], stdin=-1, stdout=-1, stderr=-1, env=None
        )
    assert exc_info.value.errno == errno.ETXTBSY
    assert len(calls) == subprocess_mod._ETXTBSY_MAX_ATTEMPTS


# === module surface + barrel re-export ===================================


def test_subprocess_module_all_is_six_public_symbols() -> None:
    """The leaf exports exactly the 6 public symbols (base + 4 variants + fn)."""
    assert subprocess_mod.__all__ == [
        "NonZeroExitSubprocessError",
        "SpawnSubprocessError",
        "SubprocessError",
        "TimeoutSubprocessError",
        "WaitSubprocessError",
        "run_with_timeout",
    ]


def test_barrel_reexports_subprocess_symbols() -> None:
    """The barrel re-exports the 6 R278 symbols (``__all__`` 17 -> 23).

    grok ``lib.rs`` L57 re-exports ``SubprocessError`` and ``run_with_timeout``
    at the crate root; the Python barrel surfaces the base class, the four
    subclasses, and the function (Pythonic adaptation -- grok's enum path has
    no Python equivalent).
    """
    expected = {
        "SubprocessError",
        "SpawnSubprocessError",
        "TimeoutSubprocessError",
        "NonZeroExitSubprocessError",
        "WaitSubprocessError",
        "run_with_timeout",
    }
    assert expected.issubset(set(mermaid_barrel.__all__))
    # Barrel symbols are the same objects the leaf exports (re-export, not copy).
    assert mermaid_barrel.run_with_timeout is run_with_timeout
    assert mermaid_barrel.SubprocessError is SubprocessError
    assert mermaid_barrel.TimeoutSubprocessError is TimeoutSubprocessError
    assert len(mermaid_barrel.__all__) == 27
