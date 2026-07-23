"""Shared subprocess plumbing -- fusion of Grok Build's ``xai-grok-mermaid``.

Direction (1) brick 10 (R278). Migrates grok's ``subprocess.rs`` as a
zero-semantic clone: spawn a child, optionally feed it stdin, wait up to a
wall-clock budget, and reap the whole process group on a breach. Used by the
optional ``mmdc`` engine (which shells out to the ``mmdc`` CLI / headless
Chromium) and the pager's out-of-process render child. The wall-clock timeout
is a *real* process kill, not a soft signal: a panic or a runaway render in the
child is contained because the parent kills and reaps it (the whole reason the
pager renders each diagram in a short-lived child under ``panic = "abort"``).

Python adaptation (asyncio)
---------------------------

grok's ``run_with_timeout`` is synchronous (``std::thread::scope`` isolates the
blocking wait). The natural Python equivalent on MiniMax Code's asyncio stack
is :func:`run_with_timeout` as a coroutine:

* :func:`asyncio.create_subprocess_exec` spawns the child.
  ``start_new_session=True`` mirrors grok's ``xai_tty_utils::detach_std_command``
  ``setsid`` so the child is its own session/group leader (pgid == pid).
* A concurrent task feeds stdin (mirrors grok's scoped-thread writer) so a full
  pipe buffer can never deadlock the wait.
* :func:`asyncio.wait_for` enforces the deadline.
* On a breach the process group is ``SIGKILL``ed on Unix (:func:`os.killpg`,
  matching grok's ``libc::killpg``) and best-effort killed on Windows (grok's
  ``reap_process_group`` is a no-op there; only the direct child dies, which is
  sufficient for the render child with no grandchildren).

Error taxonomy
--------------

grok's ``SubprocessError`` is a single enum with four variants (Spawn / Timeout
/ NonZeroExit / Wait). Python expresses the same four cases as a base class
plus one subclass per variant -- mirroring the R271 ``error.py`` adaptation of
grok's ``MermaidError`` enum -- so callers ``except TimeoutSubprocessError``
without matching an enum. Each subclass reproduces grok's ``#[error("...")]``
Display string in ``__str__``. grok ``lib.rs`` L57 re-exports ``SubprocessError``
and ``run_with_timeout`` at the crate root; the Python barrel re-exports the
base class, the four subclasses, and the function (6 symbols).

Public surface (6 symbols): :class:`SubprocessError` (base) +
:class:`SpawnSubprocessError` / :class:`TimeoutSubprocessError` /
:class:`NonZeroExitSubprocessError` / :class:`WaitSubprocessError` (variants) +
:func:`run_with_timeout`. The private helpers mirror grok's private ``fn``s and
are reached by tests via a direct module import.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import signal
import subprocess as _sp
import sys
from collections.abc import Mapping, Sequence

__all__ = [
    "NonZeroExitSubprocessError",
    "SpawnSubprocessError",
    "SubprocessError",
    "TimeoutSubprocessError",
    "WaitSubprocessError",
    "run_with_timeout",
]

#: Linux ``ETXTBSY`` ("Text file busy") retry cap -- grok ``MAX_ATTEMPTS`` (L102).
#: Exec'ing a binary another thread/process still holds open for writing is a
#: transient fork->execve race that clears within milliseconds; retry a few
#: times with a short backoff. No-op on the steady-state path.
_ETXTBSY_MAX_ATTEMPTS: int = 5

#: Backoff base for the ``ETXTBSY`` retry -- grok ``20ms * attempt`` (L112).
_ETXTBSY_BACKOFF_BASE_SECONDS: float = 0.020


class SubprocessError(Exception):
    """Why a child subprocess run did not complete successfully (grok ``SubprocessError``).

    Base class for the four-variant taxonomy. Mirrors grok ``subprocess.rs``
    L22-L36. Raise a subclass (never the base) from :func:`run_with_timeout`;
    callers ``except`` the subclass for a specific case or ``except
    SubprocessError`` for any run failure.
    """

    __slots__ = ()


class SpawnSubprocessError(SubprocessError):
    """The child could not be spawned (binary missing, fork failure).

    Mirrors grok ``SubprocessError::Spawn(io::Error)`` --
    ``#[error("could not spawn child process: {0}")]``.
    """

    __slots__ = ("cause",)

    def __init__(self, cause: BaseException) -> None:
        self.cause = cause
        super().__init__(f"could not spawn child process: {cause}")

    def __str__(self) -> str:
        return f"could not spawn child process: {self.cause}"


class TimeoutSubprocessError(SubprocessError):
    """The child exceeded its wall-clock budget and was killed and reaped.

    Mirrors grok ``SubprocessError::Timeout`` --
    ``#[error("child process timed out")]``. Carries no payload.
    """

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__("child process timed out")

    def __str__(self) -> str:
        return "child process timed out"


class NonZeroExitSubprocessError(SubprocessError):
    """The child ran to completion but exited non-zero.

    Mirrors grok ``SubprocessError::NonZeroExit(ExitStatus)`` --
    ``#[error("child process exited with {0}")]``.
    """

    __slots__ = ("returncode",)

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        super().__init__(f"child process exited with {returncode}")

    def __str__(self) -> str:
        return f"child process exited with {self.returncode}"


class WaitSubprocessError(SubprocessError):
    """Waiting on the child itself failed; the child was reaped defensively.

    Mirrors grok ``SubprocessError::Wait(io::Error)`` --
    ``#[error("waiting on child process failed: {0}")]``.
    """

    __slots__ = ("cause",)

    def __init__(self, cause: BaseException) -> None:
        self.cause = cause
        super().__init__(f"waiting on child process failed: {cause}")

    def __str__(self) -> str:
        return f"waiting on child process failed: {self.cause}"


async def _spawn_with_etxtbsy_retry(
    cmd: Sequence[str],
    *,
    stdin: int,
    stdout: int,
    stderr: int,
    env: Mapping[str, str] | None,
) -> asyncio.subprocess.Process:
    """Spawn ``cmd``, retrying briefly on ``ETXTBSY`` (grok ``spawn_with_etxtbsy_retry``).

    On Linux, exec'ing a binary that another thread/process still holds open
    for writing fails with ``ETXTBSY`` ("Text file busy"). It is transient and
    clears within milliseconds, so retry a few times with a short backoff
    (grok ``subprocess.rs`` L101-L117). No-op on the steady-state path.
    """
    attempt = 0
    while True:
        try:
            return await asyncio.create_subprocess_exec(
                *cmd,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                env=dict(env) if env is not None else None,
                start_new_session=True,
            )
        except OSError as exc:
            if exc.errno == errno.ETXTBSY and attempt + 1 < _ETXTBSY_MAX_ATTEMPTS:
                attempt += 1
                await asyncio.sleep(_ETXTBSY_BACKOFF_BASE_SECONDS * attempt)
                continue
            raise


async def _feed_stdin(stream: asyncio.StreamWriter, payload: bytes) -> None:
    """Write ``payload`` to the child's stdin then close it (grok scoped-thread writer).

    Errors are expected if the child exits/dies first (``EPIPE`` /
    ``ConnectionReset``); they are ignored -- mirrors grok's
    ``let _ = sink.write_all(payload)`` in the scoped thread (subprocess.rs
    L83). Closing the pipe lets the child observe EOF.
    """
    try:
        stream.write(payload)
        await stream.drain()
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        return
    finally:
        try:
            stream.close()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def _reap_process_group(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the child's process group so grandchildren are reaped (grok ``reap_process_group``).

    ``start_new_session=True`` runs ``setsid``, so the child is its own group
    leader and its pgid equals its pid (mirrors grok's
    ``xai_tty_utils::detach_std_command``). ``SIGKILL`` is sent directly to the
    pgid so grandchildren (e.g. an opt-in ``mmdc`` engine's headless Chromium)
    are torn down. On Windows this is a no-op: grok does not implement
    Job-Object group teardown here either, and the caller's ``proc.kill()``
    still terminates the direct child (subprocess.rs L168-L181).
    """
    if sys.platform == "win32":
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # ESRCH (group leader already gone) / EPERM -- harmless no-op, matches grok.
        pass


async def _reap(proc: asyncio.subprocess.Process) -> None:
    """Best-effort teardown: SIGKILL the group, then kill and reap (grok ``reap``).

    Mirrors grok ``subprocess.rs`` L155-L159. Tears down any grandchildren via
    the process group, then unconditionally kills and reaps the direct child.
    """
    _reap_process_group(proc)
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.wait()
    except ProcessLookupError:
        pass


async def run_with_timeout(
    cmd: Sequence[str],
    *,
    stdin_payload: bytes | None = None,
    timeout: float,
    stdout: int = _sp.DEVNULL,
    stderr: int = _sp.DEVNULL,
    env: Mapping[str, str] | None = None,
) -> None:
    """Spawn ``cmd``, optionally feed stdin, wait up to ``timeout``, reap on breach.

    The caller supplies the command list (program + args), an optional stdin
    payload, the wall-clock budget, and the stdio/env configuration. Mirrors
    grok ``run_with_timeout`` (subprocess.rs L53-L89), with one Pythonic
    tightening: stdin is piped automatically when ``stdin_payload`` is given,
    so the "payload dropped because the caller forgot to pipe stdin" foot-gun
    grok defends against (its ``debug_assert!`` at L73) cannot arise here.

    To pass ``stdin_payload`` is to pipe stdin; the payload is written from a
    concurrent task so a child that stops reading cannot wedge a write of a
    large payload and deadlock the wait (the whole reason grok uses a scoped
    thread for the writer). On timeout or a failed wait the child is killed and
    reaped: on Unix the whole process group is ``SIGKILL``ed so grandchildren
    (e.g. an opt-in ``mmdc`` engine's headless Chromium) are reaped too; on
    Windows only the direct child is killed.

    Returns ``None`` (``Ok(())``) only on a zero-exit run; otherwise raises the
    matching :class:`SubprocessError` subclass.

    Raises:
        SpawnSubprocessError: the child could not be spawned.
        TimeoutSubprocessError: the child exceeded its wall-clock budget.
        NonZeroExitSubprocessError: the child exited non-zero.
        WaitSubprocessError: waiting on the child failed.
    """
    stdin = _sp.PIPE if stdin_payload is not None else _sp.DEVNULL
    try:
        proc = await _spawn_with_etxtbsy_retry(
            cmd, stdin=stdin, stdout=stdout, stderr=stderr, env=env
        )
    except OSError as exc:
        raise SpawnSubprocessError(exc) from exc

    writer_task: asyncio.Task[None] | None = None
    if stdin_payload is not None and proc.stdin is not None:
        writer_task = asyncio.create_task(_feed_stdin(proc.stdin, stdin_payload))

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except TimeoutError:
        await _reap(proc)
        raise TimeoutSubprocessError() from None
    except ProcessLookupError as exc:
        # wait failed (the child vanished underneath us); reap defensively.
        await _reap(proc)
        raise WaitSubprocessError(exc) from exc
    finally:
        if writer_task is not None and not writer_task.done():
            writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await writer_task

    # wait succeeded (grok ``Ok(Some(status))``): reap the group on every exit
    # path so grandchildren are torn down regardless of exit code (subprocess.rs
    # L132-L139). The direct child is already reaped by ``proc.wait()`` above.
    _reap_process_group(proc)
    returncode = proc.returncode
    if returncode is None:
        # Unreachable: wait_for returned without timeout -> child reaped ->
        # returncode set. Defensive sentinel only.
        returncode = -1
    if returncode != 0:
        raise NonZeroExitSubprocessError(returncode)
