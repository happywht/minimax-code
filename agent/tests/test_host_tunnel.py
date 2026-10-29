"""Controlled tunnel ownership, readiness and IPC tests; never contacts SSH."""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from minimax_code.hosts.tunnel import HostTunnelManager, _health, build_tunnel_args
from minimax_code.ipc.handlers_hosts import register_host_handlers

HOST = {"id": "host_1", "enabled": True, "hostname": "linux-box", "username": "me",
        "port": 2222, "identity_file": ""}


class Process:
    def __init__(self):
        self.returncode = None
        self.stderr = asyncio.StreamReader()
        self.exited = asyncio.Event()
        self.killed = False

    def exit(self, code=0, stderr=b""):
        self.returncode = code
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.exited.set()

    def kill(self):
        self.killed = True
        self.exit(-9)

    async def wait(self):
        await self.exited.wait()
        return self.returncode


def test_tunnel_argv_is_authenticated_and_loopback_only():
    argv = build_tunnel_args(HOST, 12345, 8765)
    assert "StrictHostKeyChecking=yes" in argv
    assert "ExitOnForwardFailure=yes" in argv
    assert "BatchMode=yes" in argv
    assert "ServerAliveCountMax=3" in argv
    assert argv[argv.index("-L") + 1] == "127.0.0.1:12345:127.0.0.1:8765"
    assert argv[-4:] == ["-l", "me", "--", "linux-box"]
    assert "-N" in argv and "-T" in argv


@pytest.mark.parametrize("update", [{"hostname": "-oProxyCommand=bad"},
                                    {"username": "x@evil"}, {"hostname": "x y"},
                                    {"port": True}, {"port": 0}])
def test_unsafe_ssh_coordinates_rejected(update):
    with pytest.raises(ValueError):
        build_tunnel_args({**HOST, **update}, 12345, 8765)


async def test_connect_reuse_disconnect_reaps_and_close_refuses_new_process():
    process = Process()
    spawn = AsyncMock(return_value=process)
    manager = HostTunnelManager(spawn=spawn, health=AsyncMock(return_value=True))
    result = await manager.connect(HOST)
    assert result["state"] == "connected"
    assert result["local_url"].startswith("http://127.0.0.1:")
    assert await manager.connect(HOST) == result
    assert spawn.await_count == 1
    assert (await manager.disconnect(HOST["id"]))["state"] == "disconnected"
    assert process.killed
    await manager.close()
    with pytest.raises(ValueError, match="closed"):
        await manager.connect(HOST)


async def test_dead_remote_health_times_out_and_kills_owned_ssh():
    process = Process()
    manager = HostTunnelManager(spawn=AsyncMock(return_value=process),
                                health=AsyncMock(return_value=False), startup_timeout=0.01)
    result = await manager.connect(HOST)
    assert result["state"] == "failed" and result["local_url"] is None
    assert "Timed out" in result["error"]
    assert process.killed
    assert manager.connection(HOST["id"]) == result


async def test_ssh_exit_reports_bounded_diagnostics():
    process = Process()

    async def health(_):
        process.exit(255, b"Permission denied" + b"!" * 5000)
        return False

    manager = HostTunnelManager(spawn=AsyncMock(return_value=process), health=health)
    result = await manager.connect(HOST)
    assert result["state"] == "failed"
    assert len(result["error"]) <= 2000
    assert process.returncode == 255


async def test_transport_exit_invalidates_connected_url():
    process = Process()
    manager = HostTunnelManager(spawn=AsyncMock(return_value=process),
                                health=AsyncMock(return_value=True))
    await manager.connect(HOST)
    process.exit(255, b"Connection reset")
    await manager._tunnels[HOST["id"]].watcher
    state = manager.connection(HOST["id"])
    assert state["state"] == "failed" and state["local_url"] is None
    assert state["error"] == "Connection reset"
    await manager.close()


async def test_cancel_during_health_reaps_process():
    process = Process()
    entered = asyncio.Event()

    async def health(_):
        entered.set()
        await asyncio.Event().wait()

    manager = HostTunnelManager(spawn=AsyncMock(return_value=process), health=health)
    task = asyncio.create_task(manager.connect(HOST))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed
    assert manager.connection(HOST["id"])["state"] == "disconnected"


async def test_cancel_during_spawn_retains_process_until_reaped():
    entered, release = asyncio.Event(), asyncio.Event()
    process = Process()

    async def spawn(*args, **kwargs):
        entered.set()
        await release.wait()
        return process

    manager = HostTunnelManager(spawn=spawn)
    task = asyncio.create_task(manager.connect(HOST))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed


async def test_missing_client_is_queryable_failure():
    manager = HostTunnelManager(spawn=AsyncMock(side_effect=FileNotFoundError("ssh")))
    result = await manager.connect(HOST)
    assert result["state"] == "failed" and "unavailable" in result["error"]
    assert manager.connection(HOST["id"]) == result
    assert (await manager.disconnect(HOST["id"]))["state"] == "disconnected"


@pytest.mark.parametrize("kwargs", [{"remote_port": 0}, {"local_port": -1},
                                    {"local_port": True}, {"remote_port": 2.5}])
async def test_port_validation_before_spawn(kwargs):
    spawn = AsyncMock()
    manager = HostTunnelManager(spawn=spawn)
    with pytest.raises(ValueError):
        await manager.connect(HOST, **kwargs)
    spawn.assert_not_awaited()


async def test_disabled_host_does_not_spawn():
    spawn = AsyncMock()
    with pytest.raises(ValueError, match="disabled"):
        await HostTunnelManager(spawn=spawn).connect({**HOST, "enabled": False})
    spawn.assert_not_awaited()


async def test_changed_coordinates_replace_old_owned_process():
    old, new = Process(), Process()
    spawn = AsyncMock(side_effect=[old, new])
    manager = HostTunnelManager(spawn=spawn, health=AsyncMock(return_value=True))
    await manager.connect(HOST)
    result = await manager.connect({**HOST, "hostname": "new-host"})
    assert result["state"] == "connected"
    assert old.killed and not new.killed
    await manager.close()
    assert new.killed


async def test_occupied_local_port_fails_without_starting_ssh():
    spawn = AsyncMock()
    manager = HostTunnelManager(spawn=spawn)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        result = await manager.connect(HOST, local_port=occupied.getsockname()[1])
    assert result["state"] == "failed" and "Local tunnel port unavailable" in result["error"]
    assert manager.connection(HOST["id"]) == result
    spawn.assert_not_awaited()


@pytest.mark.parametrize("body,expected", [
    ({"ok": True, "db": True, "auth_required": True, "version": "1.6.0"}, True),
    ({"ok": True, "db": False, "auth_required": True, "version": "1.6.0"}, False),
    ({"ok": True, "db": True, "auth_required": False, "version": "1.6.0"}, False),
    ({"ok": True, "db": True, "auth_required": True}, False),
    (["unrelated service"], False),
])
async def test_health_requires_ready_authenticated_agent_shape(monkeypatch, body, expected):
    response = httpx.Response(200, json=body)
    client = SimpleNamespace(get=AsyncMock(return_value=response))
    factory = AsyncMock()
    factory.return_value.__aenter__.return_value = client
    # AsyncClient's constructor is synchronous, its context entry asynchronous.
    from unittest.mock import Mock

    constructor = Mock(return_value=factory.return_value)
    monkeypatch.setattr("minimax_code.hosts.tunnel.httpx.AsyncClient", constructor)
    assert await _health("http://127.0.0.1:12345") is expected
    constructor.assert_called_once_with(trust_env=False, timeout=1.0, follow_redirects=False)
    client.get.assert_awaited_once_with("http://127.0.0.1:12345/health")


async def test_ipc_connection_update_and_delete_close_transport():
    process = Process()
    manager = HostTunnelManager(spawn=AsyncMock(return_value=process),
                                health=AsyncMock(return_value=True))
    dao = SimpleNamespace(get=AsyncMock(return_value=HOST),
                          update=AsyncMock(return_value={**HOST, "enabled": False}),
                          delete=AsyncMock(return_value=True))
    handlers = {}
    server = SimpleNamespace(register=lambda name, handler: handlers.update({name: handler}))
    register_host_handlers(server, dao=dao, tunnel_manager=manager)

    async def call(method, params):
        ctx = SimpleNamespace(reply=AsyncMock(), reply_error=AsyncMock())
        await handlers[method](params, ctx)
        ctx.reply_error.assert_not_awaited()
        return ctx.reply.await_args.args[0]

    result = await call("host.connect", {"host_id": HOST["id"]})
    assert result["connection"]["state"] == "connected"
    assert await call("host.connection", {"host_id": HOST["id"]}) == result
    await call("host.update", {"host_id": HOST["id"], "enabled": False})
    assert process.killed
    await call("host.delete", {"host_id": HOST["id"]})
    assert manager.connection(HOST["id"])["state"] == "disconnected"
    await manager.close()
