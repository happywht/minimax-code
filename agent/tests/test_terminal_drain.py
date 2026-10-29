from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from minimax_code.ipc import handlers_terminal as terminal
from minimax_code.ipc.handler_utils import HandlerError


@pytest.fixture(autouse=True)
def isolated_terminal(monkeypatch):
    monkeypatch.setattr(terminal, "_SESSIONS", {})
    monkeypatch.setattr(terminal, "_TERMINAL_CLOSING", False)
    monkeypatch.setattr(terminal, "_TERMINAL_DRAIN_TASK", None)
    monkeypatch.setattr(terminal, "require_host_session", AsyncMock())


async def start():
    return await terminal.start_terminal_command(
        command="echo test", cwd=".", timeout_s=5, chat_session_id=None, emit=AsyncMock()
    )


async def test_drain_waits_for_session_and_blocks_new_starts(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()

    async def run(session, timeout, emit):
        started.set()
        await release.wait()
        session.status = "completed"

    monkeypatch.setattr(terminal, "_run_session", run)
    session = await start()
    await started.wait()
    waiter = asyncio.create_task(terminal.drain_terminal_sessions())
    await asyncio.sleep(0)
    with pytest.raises(HandlerError, match="closing"):
        await start()
    assert not waiter.done()
    release.set()
    await waiter
    assert session.task.done()
    await terminal.drain_terminal_sessions()


async def test_start_waiting_for_host_gate_is_rejected_after_fence(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def gate(session_id):
        entered.set()
        await release.wait()

    monkeypatch.setattr(terminal, "require_host_session", gate)
    pending = asyncio.create_task(start())
    await entered.wait()
    await terminal.drain_terminal_sessions()
    release.set()
    with pytest.raises(HandlerError, match="closing"):
        await pending
    assert not terminal._SESSIONS


async def test_cancelled_waiter_does_not_cancel_drain(monkeypatch):
    release = asyncio.Event()

    async def run(session, timeout, emit):
        await release.wait()

    monkeypatch.setattr(terminal, "_run_session", run)
    await start()
    waiter = asyncio.create_task(terminal.drain_terminal_sessions())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not terminal._TERMINAL_DRAIN_TASK.done()
    release.set()
    await terminal.drain_terminal_sessions()


async def test_live_process_cannot_be_reported_drained():
    session = terminal.TerminalSession(id="term", command="", cwd=".")
    session.process = SimpleNamespace(returncode=None)
    terminal._SESSIONS[session.id] = session
    with pytest.raises(terminal.TerminalDrainError, match="still running"):
        await terminal.drain_terminal_sessions()


async def test_exited_process_without_cleanup_attempt_is_not_drained():
    session = terminal.TerminalSession(id="term", command="", cwd=".")
    session.process = SimpleNamespace(returncode=0)
    terminal._SESSIONS[session.id] = session
    with pytest.raises(terminal.TerminalDrainError, match="cleanup"):
        await terminal.drain_terminal_sessions()


async def test_failed_session_cleanup_cannot_be_reported_drained():
    async def fail():
        raise RuntimeError("cleanup failed")

    session = terminal.TerminalSession(id="term", command="", cwd=".")
    session.task = asyncio.create_task(fail())
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await session.task
    terminal._SESSIONS[session.id] = session
    with pytest.raises(terminal.TerminalDrainError, match="cleanup"):
        await terminal.drain_terminal_sessions()
