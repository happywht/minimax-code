"""Owned loopback SSH forwarding to an already installed remote agent.

This connects transport only. Remote authentication still uses the agent's
normal browser pairing/token flow; no credentials are copied or persisted.
"""

from __future__ import annotations

import asyncio
import re
import socket
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


def _port(value: Any, *, allow_zero: bool = False) -> int:
    if type(value) is not int or not (0 if allow_zero else 1) <= value <= 65535:
        raise ValueError("port must be an integer in " + ("0..65535" if allow_zero else "1..65535"))
    return value


def build_tunnel_args(host: dict[str, Any], local_port: int, remote_port: int) -> list[str]:
    """Use SSH's authenticated host policy, with loopback on both ends."""
    hostname = host["hostname"]
    username = host["username"]
    if (not isinstance(hostname, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", hostname)
            or not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", username)):
        raise ValueError("invalid SSH hostname or username")
    argv = [
        "ssh", "-N", "-T", "-p", str(_port(host.get("port", 22))),
        "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
        "-o", "ControlMaster=no", "-o", "ControlPath=none",
        "-L", f"127.0.0.1:{_port(local_port)}:127.0.0.1:{_port(remote_port)}",
    ]
    if host.get("identity_file"):
        argv += ["-i", str(Path(host["identity_file"]).expanduser())]
    return [*argv, "-l", username, "--", hostname]


async def _health(url: str) -> bool:
    # Do not let HTTP_PROXY route this local connection outside the machine.
    async with httpx.AsyncClient(trust_env=False, timeout=1.0, follow_redirects=False) as client:
        response = await client.get(url + "/health")
        data = response.json()
        return (response.status_code == 200 and isinstance(data, dict)
                and data.get("ok") is True and data.get("db") is True
                and data.get("auth_required") is True
                and isinstance(data.get("version"), str) and bool(data["version"]))


@dataclass
class _Tunnel:
    host_id: str
    remote_port: int
    local_port: int
    process: Any
    stderr: str = ""
    reader: asyncio.Task | None = None
    watcher: asyncio.Task | None = None
    health_monitor: asyncio.Task | None = None
    state: str = "connecting"
    error: str | None = None
    identity: tuple = field(default_factory=tuple)
    argv: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {"host_id": self.host_id, "state": self.state,
                "local_url": f"http://127.0.0.1:{self.local_port}" if self.state == "connected" else None,
                "remote_port": self.remote_port, "error": self.error}


class HostTunnelManager:
    """One owned process per host; disconnect/shutdown reap before returning."""

    def __init__(self, *, spawn: Any = None, health: Any = None, startup_timeout: float = 15,
                 reconnect_delays: tuple[float, ...] = (1, 2, 4)):
        self._spawn = spawn or asyncio.create_subprocess_exec
        self._health = health or _health
        self._timeout = startup_timeout
        self._reconnect_delays = reconnect_delays
        self._tunnels: dict[str, _Tunnel] = {}
        self._failures: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    def connection(self, host_id: str) -> dict[str, Any]:
        if host_id in self._failures:
            return dict(self._failures[host_id])
        tunnel = self._tunnels.get(host_id)
        if tunnel is not None:
            if tunnel.state == "connected" and tunnel.process.returncode is not None:
                tunnel.state = "reconnecting"
                tunnel.error = tunnel.stderr.strip() or "SSH tunnel exited"
            return tunnel.snapshot()
        return {"host_id": host_id, "state": "disconnected", "local_url": None,
                "remote_port": None, "error": None}

    async def _drain(self, tunnel: _Tunnel) -> None:
        while chunk := await tunnel.process.stderr.read(4096):
            tunnel.stderr = (tunnel.stderr + chunk.decode("utf-8", errors="replace"))[-2000:]

    async def _spawn_owned(self, argv: list[str], host_id: str, remote_port: int,
                           local_port: int) -> Any:
        spawning = asyncio.create_task(self._spawn(
            *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL))
        try:
            return await asyncio.shield(spawning)
        except asyncio.CancelledError:
            while not spawning.done():
                with suppress(asyncio.CancelledError):
                    await asyncio.shield(spawning)
            if not spawning.cancelled() and spawning.exception() is None:
                abandoned = _Tunnel(host_id, remote_port, local_port, spawning.result())
                abandoned.reader = asyncio.create_task(self._drain(abandoned))
                await self._stop(abandoned)
            raise

    async def _ready(self, tunnel: _Tunnel) -> None:
        async with asyncio.timeout(self._timeout):
            while tunnel.process.returncode is None:
                try:
                    ready = await self._health(f"http://127.0.0.1:{tunnel.local_port}")
                except (httpx.HTTPError, ValueError, OSError):
                    ready = False
                if ready and tunnel.process.returncode is None:
                    return
                await asyncio.sleep(0.1)
        raise OSError(tunnel.stderr.strip() or "SSH tunnel exited during connection")

    async def _reap_process(self, tunnel: _Tunnel) -> None:
        if tunnel.process.returncode is None:
            with suppress(ProcessLookupError):
                tunnel.process.kill()
        await tunnel.process.wait()
        if tunnel.reader:
            await tunnel.reader

    async def _monitor_health(self, tunnel: _Tunnel) -> None:
        # SSH can remain alive after the remote HTTP agent disappears. Probe
        # only the existing loopback forward; never start the remote service.
        failures = 0
        while True:
            await asyncio.sleep(5)
            if tunnel.state not in {"connected", "degraded"} or tunnel.process.returncode is not None:
                failures = 0
                continue
            process = tunnel.process
            try:
                healthy = await self._health(f"http://127.0.0.1:{tunnel.local_port}")
            except (httpx.HTTPError, ValueError, OSError, TimeoutError):
                healthy = False
            if process is not tunnel.process or tunnel.state not in {"connected", "degraded"} or process.returncode is not None:
                failures = 0
                continue
            if healthy:
                failures = 0
                if tunnel.state == "degraded":
                    tunnel.state = "connected"
                    tunnel.error = None
            else:
                failures += 1
                if failures >= 2:
                    tunnel.state = "degraded"
                    tunnel.error = "SSH tunnel is running, but remote agent health is unavailable"

    async def _watch(self, tunnel: _Tunnel) -> None:
        # The attempt budget applies to the whole tunnel lifetime, including flapping.
        delays = iter(self._reconnect_delays)
        while True:
            await tunnel.process.wait()
            if tunnel.reader:
                await tunnel.reader
            if tunnel.state not in {"connected", "degraded", "reconnecting"}:
                return
            tunnel.state = "reconnecting"
            tunnel.error = tunnel.stderr.strip() or "SSH tunnel exited"
            while True:
                delay = next(delays, None)
                if delay is None:
                    tunnel.state = "failed"
                    return
                await asyncio.sleep(delay)
                try:
                    tunnel.process = await self._spawn_owned(
                        tunnel.argv, tunnel.host_id, tunnel.remote_port, tunnel.local_port)
                    tunnel.stderr = ""
                    tunnel.reader = asyncio.create_task(self._drain(tunnel))
                    await self._ready(tunnel)
                except (TimeoutError, OSError) as exc:
                    await self._reap_process(tunnel)
                    tunnel.error = (str(exc) or tunnel.stderr.strip()
                                    or "Timed out waiting for authenticated remote agent health")
                    continue
                tunnel.state = "connected"
                tunnel.error = None
                break

    async def _stop(self, tunnel: _Tunnel) -> None:
        async def reap() -> None:
            if tunnel.health_monitor:
                tunnel.health_monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await tunnel.health_monitor
            if tunnel.watcher:
                tunnel.watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await tunnel.watcher
            await self._reap_process(tunnel)
        task = asyncio.create_task(reap())
        # Retain ownership even if the caller is repeatedly cancelled.
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def connect(self, host: dict[str, Any], *, remote_port: int = 8765,
                      local_port: int = 0) -> dict[str, Any]:
        _port(remote_port)
        _port(local_port, allow_zero=True)
        if not host.get("enabled"):
            raise ValueError("host is disabled")
        host_id = host["id"]
        identity = tuple(host.get(key) for key in ("hostname", "username", "port", "identity_file"))
        # Validate even when reusing an existing connection.
        build_tunnel_args(host, local_port or 1, remote_port)
        async with self._lock:
            if self._closed:
                raise ValueError("tunnel manager is closed")
            old = self._tunnels.get(host_id)
            if (old and old.state in {"connected", "degraded", "reconnecting"}
                    and old.identity == identity and old.remote_port == remote_port
                    and local_port in {0, old.local_port}):
                return self.connection(host_id)
            preferred_port = (old.local_port if old and old.state == "failed"
                              and old.identity == identity and old.remote_port == remote_port
                              and local_port == 0 else 0)
            if old:
                old.state = "disconnected"
                await self._stop(old)
            try:
                with socket.socket() as reservation:
                    try:
                        reservation.bind(("127.0.0.1", preferred_port or local_port))
                    except OSError:
                        if not preferred_port:
                            raise
                        reservation.bind(("127.0.0.1", 0))
                    local_port = reservation.getsockname()[1]
            except OSError as exc:
                failure = {"host_id": host_id, "state": "failed", "local_url": None,
                           "remote_port": remote_port, "error": f"Local tunnel port unavailable: {exc}"}
                self._failures[host_id] = failure
                return dict(failure)
            argv = build_tunnel_args(host, local_port, remote_port)
            try:
                process = await self._spawn_owned(argv, host_id, remote_port, local_port)
            except OSError as exc:
                failure = {"host_id": host_id, "state": "failed", "local_url": None,
                           "remote_port": remote_port, "error": f"SSH client unavailable: {exc}"}
                self._failures[host_id] = failure
                return dict(failure)
            self._failures.pop(host_id, None)
            tunnel = _Tunnel(host_id, remote_port, local_port, process, identity=identity, argv=argv)
            self._tunnels[host_id] = tunnel
            tunnel.reader = asyncio.create_task(self._drain(tunnel))
            tunnel.watcher = asyncio.create_task(self._watch(tunnel))
            try:
                await self._ready(tunnel)
                tunnel.state = "connected"
                tunnel.health_monitor = asyncio.create_task(self._monitor_health(tunnel))
                return tunnel.snapshot()
            except asyncio.CancelledError:
                tunnel.state = "disconnected"
                await self._stop(tunnel)
                raise
            except (TimeoutError, OSError) as exc:
                tunnel.state = "failed"
                tunnel.error = str(exc) or "Timed out waiting for authenticated remote agent health"
                await self._stop(tunnel)
                return tunnel.snapshot()

    async def disconnect(self, host_id: str) -> dict[str, Any]:
        async with self._lock:
            self._failures.pop(host_id, None)
            tunnel = self._tunnels.get(host_id)
            if tunnel:
                tunnel.state = "disconnected"
                tunnel.error = None
                await self._stop(tunnel)
            return self.connection(host_id)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            cancelled = False
            for tunnel in self._tunnels.values():
                tunnel.state = "disconnected"
                try:
                    await self._stop(tunnel)
                except asyncio.CancelledError:
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
