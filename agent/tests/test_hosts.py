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
import json
import shlex
import shutil
import sys
import uuid
from pathlib import Path
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
    assert host["remote_port"] == 8765

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
        "host_id": host["id"], "enabled": False, "port": 22, "remote_port": 9999}, host_server.ctx())
    updated = await dao.get(host["id"])
    assert updated["enabled"] is False and updated["port"] == 22
    assert updated["remote_port"] == 9999

    ok = await dao.delete(host["id"])
    assert ok is True
    assert await dao.get(host["id"]) is None


async def test_host_probe_unknown_id_errors(host_server) -> None:
    ctx = host_server.ctx()
    await host_server.server._handlers["host.probe"]({"host_id": "ghost"}, ctx)
    ctx.reply_error.assert_awaited_once()


# ---------------------------------------------------------------------------
# M5-B — host.exec / host.push / host.fetch
# ---------------------------------------------------------------------------


async def test_host_exec_runs_command_via_ssh(host_server) -> None:
    srv = host_server.server
    ctx = host_server.ctx()
    await srv._handlers["host.create"]({
        "name": "exec-1", "hostname": "10.0.0.5", "username": "ci"}, ctx)
    host = ctx.reply.await_args.args[0]["host"]

    captured = {}

    async def exec_runner(argv):
        captured["argv"] = argv
        assert argv[0] == "ssh" and "10.0.0.5" in argv[-2]
        assert "echo hello" in argv[-1]
        return 0, b"hello\n", b""

    host_server.runner_slot["fn"] = exec_runner
    await srv._handlers["host.exec"]({
        "host_id": host["id"], "command": "echo hello"}, host_server.ctx())
    # Re-fetch handler registry entry bound to the latest slot.
    result = None
    probe_ctx = host_server.ctx()
    await srv._handlers["host.probe"]({"host_id": host["id"]}, probe_ctx)
    exec_ctx = host_server.ctx()
    await srv._handlers["host.exec"]({
        "host_id": host["id"], "command": "echo hello"}, exec_ctx)
    result = exec_ctx.reply.await_args.args[0]
    assert result["ok"] is True
    assert "hello" in result["stdout"]

    # Disabled hosts refuse execution.
    await srv._handlers["host.update"]({
        "host_id": host["id"], "enabled": False}, host_server.ctx())
    denied = host_server.ctx()
    await srv._handlers["host.exec"]({
        "host_id": host["id"], "command": "echo no"}, denied)
    assert "disabled" in str(denied.reply_error.await_args.args[1])


async def test_host_push_fetch_with_sha_verification(host_server, tmp_path) -> None:
    srv = host_server.server
    ctx = host_server.ctx()
    await srv._handlers["host.create"]({
        "name": "file-1", "hostname": "10.0.0.7", "username": "ci"}, ctx)
    host = ctx.reply.await_args.args[0]["host"]

    local = tmp_path / "payload.txt"
    local.write_text("round-trip", encoding="utf-8")
    remote_file = tmp_path / "remote-side.txt"  # stands in for the remote copy

    calls: list[list[str]] = []

    import hashlib

    async def scp_simulator(argv):
        calls.append(argv)
        if argv[0] == "scp":
            # Upload: argv[-2]=local, argv[-1]=remote:dest — mirror the
            # file so the remote-side sha256sum has something to hash.
            if str(local) in argv[-2]:
                staged_remote = argv[-1].split(":", 1)[1]
                staged = remote_file.parent / Path(staged_remote).parent.name / "payload"
                assert staged.parent.is_dir()  # created by the actual remote helper
                shutil.copyfile(local, staged)
            # Download: argv[-2]=remote:src, argv[-1]=local dest.
            else:
                Path(argv[-1]).write_text(
                    remote_file.read_text(encoding="utf-8"), encoding="utf-8")
            return 0, b"", b""
        if argv[-1].startswith("python3 "):
            # Execute the exact transmitted bootstrap/helper, mapping only the
            # remote destination into this test's isolated local directory.
            _, _, bootstrap, request = shlex.split(argv[-1])
            data = json.loads(request)
            data["target"] = str(remote_file)
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-c", bootstrap, json.dumps(data),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await process.communicate()
            return process.returncode, out, err
        # ssh sha256sum — return the REAL hash of the mirrored file.
        digest = hashlib.sha256(remote_file.read_bytes()).hexdigest()
        return 0, f"{digest}  /tmp/payload.txt\n".encode(), b""

    host_server.runner_slot["fn"] = scp_simulator

    # Push: the sha256sum round-trip reads the "remote" file we mirrored.
    push_ctx = host_server.ctx()
    await srv._handlers["host.push"]({
        "host_id": host["id"], "local_path": str(local),
        "remote_path": "/tmp/payload.txt"}, push_ctx)
    push_result = push_ctx.reply.await_args.args[0]
    assert push_result["ok"] is True, push_result
    assert push_result["publication_state"] == "published"

    status_ctx = host_server.ctx()
    await srv._handlers["host.transfer_status"]({
        "host_id": host["id"], "remote_path": "/tmp/payload.txt",
        "transfer_id": push_result["transfer_id"]}, status_ctx)
    assert status_ctx.reply.await_args.args[0]["publication_state"] == "published"

    # Fetch into a fresh local path.
    download_target = tmp_path / "downloaded.txt"
    fetch_ctx = host_server.ctx()
    await srv._handlers["host.fetch"]({
        "host_id": host["id"], "remote_path": "/tmp/payload.txt",
        "local_path": str(download_target)}, fetch_ctx)
    fetched = fetch_ctx.reply.await_args.args[0]
    assert fetched["ok"] is True, fetched
    assert download_target.read_text(encoding="utf-8") == "round-trip"

    # A missing remote file must fail sha verification (sha256sum of a
    # non-existent path returns nothing to match).
    from minimax_code.hosts.ssh import fetch_file

    async def empty_runner(argv):
        return 0, b"", b""

    bad = await fetch_file(
        {"hostname": "h", "username": "u", "port": 22, "identity_file": ""},
        "/tmp/missing.txt", tmp_path / "out.bin", runner=empty_runner)
    assert bad.ok is False


async def test_status_rpc_resolves_after_both_initial_receipts_are_lost(host_server, tmp_path):
    from minimax_code.ipc.protocol import HostTransferResult

    host = await host_server.hosts.create(name="upload", hostname="unused", username="user")
    local = tmp_path / "source.txt"
    local.write_bytes(b"incoming")
    remote = tmp_path / "remote.txt"
    remote.write_bytes(b"original")
    transfer_id = uuid.uuid4().hex
    actions = []

    async def transport(argv):
        if argv[0] == "scp":
            staged = tmp_path / (".minimax-upload-" + transfer_id) / "payload"
            shutil.copyfile(local, staged)
            return 0, b"", b""
        _, _, bootstrap, encoded = shlex.split(argv[-1])
        request = json.loads(encoded)
        actions.append(request["action"])
        request["target"] = str(remote)
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", bootstrap, json.dumps(request),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate()
        if request["action"] == "commit" or actions.count("status") == 1:
            return 255, b"", b"connection lost"
        return process.returncode, out, err

    host_server.runner_slot["fn"] = transport
    push_ctx = host_server.ctx()
    await host_server.server._handlers["host.push"]({
        "host_id": host["id"], "local_path": str(local), "remote_path": "/remote.txt",
        "transfer_id": transfer_id}, push_ctx)
    pushed = HostTransferResult.model_validate(push_ctx.reply.await_args.args[0])
    assert not pushed.ok and pushed.publication_state == "unknown"
    assert pushed.transfer_id == transfer_id
    status_ctx = host_server.ctx()
    await host_server.server._handlers["host.transfer_status"]({
        "host_id": host["id"], "remote_path": "/remote.txt",
        "transfer_id": transfer_id}, status_ctx)
    status = HostTransferResult.model_validate(status_ctx.reply.await_args.args[0])
    assert status.ok and status.publication_state == "published"
    assert Path(status.backup_path).read_bytes() == b"original"
    assert remote.read_bytes() == b"incoming"
    assert actions == ["prepare", "commit", "status", "status"]


@pytest.mark.parametrize("invalid", [
    {"transfer_id": "../bad"}, {"transfer_id": 123}, {"remote_path": "relative"},
    {"remote_path": "/"}, {"remote_path": "/tmp/../target"}, {"unknown": True},
])
async def test_transfer_status_invalid_params_are_structured(host_server, invalid):
    host = await host_server.hosts.create(name="upload", hostname="unused", username="user")
    ctx = host_server.ctx()
    await host_server.server._handlers["host.transfer_status"]({
        "host_id": host["id"], "remote_path": "/remote.txt",
        "transfer_id": uuid.uuid4().hex, **invalid}, ctx)
    ctx.reply.assert_not_awaited()
    assert ctx.reply_error.await_args.args[0] == -32602


async def test_transfer_status_unknown_host_is_structured(host_server):
    ctx = host_server.ctx()
    await host_server.server._handlers["host.transfer_status"]({
        "host_id": "ghost", "remote_path": "/remote.txt", "transfer_id": uuid.uuid4().hex}, ctx)
    assert ctx.reply_error.await_args.args[0] == -32602


async def test_transfer_status_unknown_id_never_prepares_or_commits(host_server, tmp_path):
    host = await host_server.hosts.create(name="upload", hostname="unused", username="user")
    remote = tmp_path / "unchanged.txt"
    remote.write_bytes(b"original")

    async def transport(argv):
        _, _, bootstrap, encoded = shlex.split(argv[-1])
        request = json.loads(encoded)
        assert request["action"] == "status"
        request["target"] = str(remote)
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", bootstrap, json.dumps(request),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate()
        return process.returncode, out, err

    host_server.runner_slot["fn"] = transport
    ctx = host_server.ctx()
    await host_server.server._handlers["host.transfer_status"]({
        "host_id": host["id"], "remote_path": "/remote.txt", "transfer_id": uuid.uuid4().hex}, ctx)
    result = ctx.reply.await_args.args[0]
    assert not result["ok"] and result["publication_state"] == "unknown"
    assert remote.read_bytes() == b"original"
    assert not list(tmp_path.glob(".minimax-upload-*"))


@pytest.mark.parametrize("port", [0, 65536, True, 2.5])
async def test_host_rejects_invalid_remote_port(host_server, port):
    ctx = host_server.ctx()
    await host_server.server._handlers["host.create"](
        {"name": "bad", "hostname": "linux", "username": "me", "remote_port": port}, ctx)
    ctx.reply_error.assert_awaited_once()
    assert await host_server.hosts.list() == []
