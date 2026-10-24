"""M5-A tests — host registry, connection diagnosis, version handshake.

- SSH argv contract: BatchMode / ConnectTimeout / identity / port are
  assembled exactly (no network touched — the runner is injected).
- host.* dispatch: create (with identity + duplicate-name guards),
  update tri-state, probe persistence (ok and failing), delete.
- build_ssh_args and probe_host parsing unit coverage.
"""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from minimax_code.config import Config
from minimax_code.hosts.ssh import build_ssh_args, probe_host
from minimax_code.ipc.handlers_hosts import register_host_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.hosts import HostsDAO
from minimax_code.storage.db import AsyncDatabase

# ---------------------------------------------------------------------------
# Unit — argv contract & probe parsing
# ---------------------------------------------------------------------------


def test_build_ssh_args_contract() -> None:
    argv = build_ssh_args(hostname="linux-box", username="me", port=2222,
                          identity_file="C:/keys/id_ed25519",
                          connect_timeout=8.0,
                          remote_command="uname -a")
    assert argv[:2] == ["ssh", "-p"]
    assert "2222" in argv
    assert "BatchMode=yes" in argv           # never prompt
    assert "ConnectTimeout=8" in argv
    assert "StrictHostKeyChecking=accept-new" in argv
    assert argv[argv.index("-i") + 1] == "C:/keys/id_ed25519"
    assert argv[-2] == "me@linux-box"
    assert argv[-1] == "uname -a"


def test_probe_host_parses_handshake() -> None:
    async def fake_runner(argv):
        return 0, (
            b"Linux ubuntu 6.8.0\n"
            b"minimax-host-probe\n"
            b"Python 3.12.3\n"
            b"codex-cli 0.163.0\n"
        ), b""

    host = {"hostname": "h", "username": "u", "port": 22, "identity_file": ""}
    info = asyncio.run(probe_host(host, runner=fake_runner))
    assert info["ok"] is True
    assert "Linux ubuntu" in info["os"]
    assert info["python_version"] == "Python 3.12.3"
    assert info["agent_version"] == "codex-cli 0.163.0"


def test_probe_host_reports_ssh_failure() -> None:
    async def fake_runner(argv):
        return 255, b"", b"Permission denied (publickey)"

    host = {"hostname": "h", "username": "u", "port": 22, "identity_file": ""}
    info = asyncio.run(probe_host(host, runner=fake_runner))
    assert info["ok"] is False
    assert "Permission denied" in info["error"]


# ---------------------------------------------------------------------------
# host.* dispatch (injected DAO)
# ---------------------------------------------------------------------------


class Ctx:
    def __init__(self, server):
        self.server = server
        self.reply = AsyncMock()
        self.reply_error = AsyncMock()


@pytest.fixture
async def host_server(tmp_path):
    db = AsyncDatabase(tmp_path / "hosts.db")
    await db.connect()
    await db.migrate()
    srv = IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO())
    dao = HostsDAO(db)
    # Registration is final — the runner goes through a mutable slot so
    # tests can swap probe outcomes without re-registering handlers.
    runner_slot = {"fn": None}

    async def dispatch_runner(argv):
        return await runner_slot["fn"](argv)

    register_host_handlers(srv, dao=dao, ssh_runner=dispatch_runner)
    key_file = tmp_path / "id_ed25519"
    key_file.write_text("fake-key", encoding="utf-8")
    env = SimpleNamespace(db=db, server=server_obj(srv), dao=srv, hosts=dao,
                          key=key_file, runner_slot=runner_slot)
    env.ctx = lambda: Ctx(srv)
    try:
        yield env
    finally:
        await db.close()


def server_obj(srv):
    return srv


async def test_host_create_update_probe_delete(host_server) -> None:
    srv, dao = host_server.server, host_server.hosts
    ctx = host_server.ctx()

    key_file = host_server.key
    await srv._handlers["host.create"]({
        "name": "build-1", "hostname": "10.0.0.9", "username": "deploy",
        "port": 2200, "identity_file": str(key_file),
    }, ctx)
    host = ctx.reply.await_args.args[0]["host"]
    assert host["port"] == 2200 and host["enabled"] is True

    # Duplicate names are rejected.
    dup = host_server.ctx()
    await srv._handlers["host.create"]({"name": "build-1", "hostname": "x",
                                        "username": "y"}, dup)
    dup.reply_error.assert_awaited_once()

    # Probe with an injected failing runner — outcome persisted.
    async def fail_runner(argv):
        return 255, b"", b"connection refused"

    host_server.runner_slot["fn"] = fail_runner
    probe_ctx = host_server.ctx()
    await srv._handlers["host.probe"]({"host_id": host["id"]}, probe_ctx)
    probe = probe_ctx.reply.await_args.args[0]["probe"]
    assert probe["ok"] is False
    refreshed = await dao.get(host["id"])
    assert refreshed["last_probe_ok"] is False
    assert refreshed["last_probe_at"] is not None

    # Update tri-state + disable.
    await srv._handlers["host.update"]({
        "host_id": host["id"], "enabled": False, "port": 22}, host_server.ctx())
    updated = await dao.get(host["id"])
    assert updated["enabled"] is False and updated["port"] == 22

    ok = await dao.delete(host["id"])
    assert ok is True
    assert await dao.get(host["id"]) is None


async def test_host_probe_unknown_id_errors(host_server) -> None:
    ctx = host_server.ctx()
    await host_server.server._handlers["host.probe"]({"host_id": "ghost"}, ctx)
    ctx.reply_error.assert_awaited_once()
