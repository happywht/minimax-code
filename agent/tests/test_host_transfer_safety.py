"""No live SSH: preserve local data on failed or racing downloads."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from minimax_code.hosts.ssh import _run_scp, fetch_file, run_ssh

HOST = {"hostname": "test.invalid", "username": "user"}


@pytest.mark.parametrize("failure", ["checksum", "partial", "cancel", "created"])
async def test_download_failure_preserves_destination(tmp_path, failure):
    dest = tmp_path / "report.txt"

    async def runner(argv):
        if argv[0] == "scp":
            staged = Path(argv[-1])
            assert staged != dest
            staged.write_bytes(b"incoming")
            if failure == "partial":
                return 1, b"", b"connection lost"
            if failure == "cancel":
                raise asyncio.CancelledError
            if failure == "created":
                dest.write_bytes(b"concurrent user edit")
            return 0, b"", b""
        digest = "wrong" if failure == "checksum" else hashlib.sha256(b"incoming").hexdigest()
        return 0, f"{digest}  /remote\n".encode(), b""

    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await fetch_file(HOST, "/remote", dest, runner=runner)
    else:
        result = await fetch_file(HOST, "/remote", dest, runner=runner)
        assert not result.ok
    if failure == "created":
        assert dest.read_bytes() == b"concurrent user edit"
    else:
        assert not dest.exists()
    assert not list(tmp_path.glob(".minimax-*"))


async def test_download_refuses_existing_destination(tmp_path):
    dest = tmp_path / "report.txt"
    dest.write_bytes(b"original")

    async def runner(argv):
        raise AssertionError("existing destination should be rejected before transfer")

    result = await fetch_file(HOST, "/remote", dest, runner=runner)
    assert not result.ok
    assert dest.read_bytes() == b"original"
    assert not list(tmp_path.glob(".minimax-*"))


@pytest.mark.parametrize("transport", ["ssh", "scp"])
@pytest.mark.parametrize("stop", ["cancel", "timeout"])
async def test_transport_reaps_child_on_stop(monkeypatch, transport, stop):
    started = asyncio.Event()

    class Process:
        returncode = None
        killed = False
        reaped = False

        async def communicate(self):
            started.set()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True

        async def wait(self):
            self.returncode = -9
            self.reaped = True

    process = Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    timeout = 0.01 if stop == "timeout" else 30
    operation = run_ssh(HOST, "true", timeout=timeout) if transport == "ssh" else _run_scp(["scp"], timeout=timeout)
    task = asyncio.create_task(operation)
    await started.wait()
    if stop == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert (await task).timed_out
    assert process.killed and process.reaped
