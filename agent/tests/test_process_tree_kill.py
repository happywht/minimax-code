"""v1.2.2 regression tests — terminal process-tree kills.

The bug: both spawn sites (``exec_command`` tool and
``terminal.start`` handler) launched plain children, and every
kill/timeout path only signalled the *direct* child
(``proc.kill()`` / ``proc.terminate()``). A command that spawned its
own workers (build tool, dev server) left them orphaned after a
timeout or user stop — still holding ports and file locks.

The fix: children now lead their own process group / session
(:func:`_child_spawn_kwargs`) and every signal goes through
:func:`_signal_process_tree` (Windows ``taskkill /F /T``, POSIX
``killpg``), with a single-process fallback.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from minimax_code.agent.tools.terminal import (
    _SIGKILL,
    _child_spawn_kwargs,
    _signal_process_tree,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        # Localised "no tasks" message never contains the bare pid digits.
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def _wait_pid_gone(pid: int, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        await asyncio.sleep(0.1)
    return not _pid_alive(pid)


async def _spawn_sleep(secs: float = 60) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        f"import time; time.sleep({secs})",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        **_child_spawn_kwargs(),
    )


# ---------------------------------------------------------------------------
# _child_spawn_kwargs
# ---------------------------------------------------------------------------


def test_spawn_kwargs_win32_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert _child_spawn_kwargs() == {}


def test_spawn_kwargs_posix_new_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert _child_spawn_kwargs() == {"start_new_session": True}


# ---------------------------------------------------------------------------
# _signal_process_tree
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill path is Windows-only")
@pytest.mark.asyncio
async def test_taskkill_kills_grandchild(tmp_path: Path) -> None:
    """The core regression: a grandchild must die with the tree."""
    pid_file = (tmp_path / "grandchild.pid").as_posix()
    script = (
        "import subprocess, sys, time\n"
        "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open(r'{pid_file}', 'w').write(str(gc.pid))\n"
        "time.sleep(120)\n"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        **_child_spawn_kwargs(),
    )
    grandchild_pid = 0
    try:
        for _ in range(200):
            f = Path(pid_file)
            if f.exists():
                grandchild_pid = int(f.read_text().strip())
                break
            await asyncio.sleep(0.05)
        assert grandchild_pid, "grandchild pid file never appeared"
        assert _pid_alive(grandchild_pid)

        await _signal_process_tree(proc, _SIGKILL)
        await proc.wait()

        assert await _wait_pid_gone(grandchild_pid), (
            f"grandchild pid {grandchild_pid} survived the tree kill"
        )
    finally:
        if proc.returncode is None:  # pragma: no cover — cleanup on failure
            await _signal_process_tree(proc, _SIGKILL)
            await proc.wait()
        if grandchild_pid and _pid_alive(grandchild_pid):  # pragma: no cover
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(grandchild_pid)],
                capture_output=True,
                timeout=10,
            )


@pytest.mark.asyncio
async def test_posix_branch_falls_back_to_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """killpg failure (stale pgid) must fall back to a plain kill."""
    monkeypatch.setattr(sys, "platform", "linux")
    calls: list[int] = []

    def _fake_killpg(pgid: int, sig: int) -> None:
        calls.append(sig)
        raise ProcessLookupError(f"no such process group {pgid}")

    monkeypatch.setattr(os, "getpgid", lambda pid: 424242, raising=False)
    monkeypatch.setattr(os, "killpg", _fake_killpg, raising=False)

    proc = await _spawn_sleep()
    await _signal_process_tree(proc, _SIGKILL)
    await asyncio.wait_for(proc.wait(), timeout=10)

    assert calls == [_SIGKILL], "killpg was not attempted"
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_finished_process_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reaped process must not trigger any tree-wide call."""
    dispatched: list[list[str]] = []
    monkeypatch.setattr(
        subprocess,
        "call",
        lambda *a, **k: dispatched.append(list(a[0])) or 0,
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "pass",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    assert proc.returncode is not None

    await _signal_process_tree(proc, _SIGKILL)
    assert dispatched == []


# ---------------------------------------------------------------------------
# Callers: exec tool timeout + IPC terminate ladder
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="ping flag syntax is Windows")
@pytest.mark.asyncio
async def test_exec_tool_timeout_reports_and_kills() -> None:
    """Tool wiring: the timeout branch goes through the tree kill."""
    from minimax_code.agent.tools.terminal import ExecCommandTool

    result = await ExecCommandTool().run(
        cmd=["ping", "-n", "60", "127.0.0.1"], timeout=1
    )
    assert not result.success
    output = result.output if isinstance(result.output, dict) else {}
    assert output.get("timed_out") is True


@pytest.mark.asyncio
async def test_terminate_process_stops_tree() -> None:
    """IPC wiring: the terminate ladder ends with a dead process."""
    from minimax_code.ipc.handlers_terminal import _terminate_process

    proc = await _spawn_sleep()
    await asyncio.wait_for(_terminate_process(proc), timeout=15)
    assert proc.returncode is not None
