"""JSON-RPC handlers for the ``host.*`` namespace (M5).

Remote Linux hosts: configuration, diagnosis/version handshake, execution,
verified file transfer and queryable upload publication journals.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from ..hosts.ssh import fetch_file, probe_host, push_file, run_ssh, upload_action
from ..hosts.tunnel import HostTunnelManager
from ..storage.dao.hosts import HostsDAO
from .handler_utils import HandlerError, check_params
from .protocol import INTERNAL_ERROR, INVALID_PARAMS, HostPushParams, HostTransferStatusParams
from .server import Context

logger = logging.getLogger(__name__)


def _validate_port(port: Any) -> int:
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        raise HandlerError(INVALID_PARAMS, "port must be an integer") from None
    if not 1 <= port_int <= 65535:
        raise HandlerError(INVALID_PARAMS, "port must be 1..65535")
    return port_int


def _upload_target(value: str) -> str:
    target = PurePosixPath(value)
    if not target.is_absolute() or ".." in target.parts or not target.name or "\0" in value:
        raise HandlerError(INVALID_PARAMS, "remote_path must be an absolute file path")
    return str(target)


def register_host_handlers(server: Any, *, dao: Any = None, ssh_runner: Any = None,
                           tunnel_manager: Any = None) -> None:
    dao_factory = _make_dao_factory(dao)
    runner = ssh_runner  # injectable for tests; None = real ssh
    tunnels = tunnel_manager or HostTunnelManager()
    server._host_tunnel_manager = tunnels
    lifecycle_lock = asyncio.Lock()

    async def _require_host(host_id: str) -> dict[str, Any]:
        host = await (await dao_factory()).get(host_id)
        if host is None:
            raise HandlerError(INVALID_PARAMS, f"unknown host_id: {host_id!r}")
        return host

    async def handle_host_create(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"name", "hostname", "username"})
            name = str(params["name"]).strip()
            hostname = str(params["hostname"]).strip()
            username = str(params["username"]).strip()
            if not name or not hostname or not username:
                raise HandlerError(INVALID_PARAMS, "name, hostname and username are required")
            port = _validate_port(params.get("port", 22))
            identity_file = str(params.get("identity_file") or "").strip()
            if identity_file and not Path(identity_file).expanduser().exists():
                raise HandlerError(INVALID_PARAMS,
                                   f"identity_file does not exist: {identity_file}")
            dao_obj = await dao_factory()
            if await dao_obj.get_by_name(name) is not None:
                raise HandlerError(INVALID_PARAMS, f"host name already taken: {name!r}")
            row = await dao_obj.create(name=name, hostname=hostname, username=username,
                                       port=port, identity_file=identity_file)
            await ctx.reply({"host": row})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("host.create failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.create failed")

    async def handle_host_list(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys=set())
            p = params or {}
            enabled = p.get("enabled")
            hosts = await (await dao_factory()).list(
                enabled=None if enabled is None else bool(enabled))
            await ctx.reply({"hosts": hosts})
        except Exception:  # pragma: no cover — defensive
            logger.exception("host.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.list failed")

    async def handle_host_get(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"host_id"})
            host = await _require_host(str(params["host_id"]))
            await ctx.reply({"host": host})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)

    async def handle_host_update(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"host_id"})
            host_id = str(params["host_id"])
            updates: dict[str, Any] = {}
            for key in ("name", "hostname", "username"):
                if params.get(key) is not None:
                    value = str(params[key]).strip()
                    if not value:
                        raise HandlerError(INVALID_PARAMS, f"{key} cannot be empty")
                    updates[key] = value
            if params.get("port") is not None:
                updates["port"] = _validate_port(params["port"])
            if params.get("identity_file") is not None:
                identity = str(params["identity_file"]).strip()
                if identity and not Path(identity).expanduser().exists():
                    raise HandlerError(INVALID_PARAMS,
                                       f"identity_file does not exist: {identity}")
                updates["identity_file"] = identity
            if params.get("enabled") is not None:
                updates["enabled"] = bool(params["enabled"])
            if not updates:
                raise HandlerError(INVALID_PARAMS, "host.update: no fields to update")
            async with lifecycle_lock:
                await _require_host(host_id)
                # Editing connection coordinates or disabling a host cannot
                # leave a live tunnel attached to its obsolete configuration.
                if ({"hostname", "username", "port", "identity_file"} & updates.keys()
                        or updates.get("enabled") is False):
                    await tunnels.disconnect(host_id)
                row = await (await dao_factory()).update(host_id, **updates)
            if row is None:
                raise HandlerError(INVALID_PARAMS, f"unknown host_id: {host_id!r}")
            await ctx.reply({"ok": True, "host": row})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("host.update failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.update failed")

    async def handle_host_delete(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"host_id"})
            async with lifecycle_lock:
                await tunnels.disconnect(str(params["host_id"]))
                deleted = await (await dao_factory()).delete(str(params["host_id"]))
            if not deleted:
                raise HandlerError(INVALID_PARAMS,
                                   f"unknown host_id: {params['host_id']!r}")
            await ctx.reply({"ok": True, "host_id": str(params["host_id"])})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("host.delete failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.delete failed")

    async def handle_host_probe(params: Any, ctx: Context) -> None:
        """Connection diagnosis + version handshake, persisted (M5)."""
        try:
            check_params(params, expected_keys={"host_id"})
            dao_obj = await dao_factory()
            host = await dao_obj.get(str(params["host_id"]))
            if host is None:
                raise HandlerError(INVALID_PARAMS,
                                   f"unknown host_id: {params['host_id']!r}")
            if not host.get("enabled"):
                raise HandlerError(INVALID_PARAMS, "host is disabled")
            info = await probe_host(host, runner=runner)
            await dao_obj.record_probe(host["id"], ok=bool(info.get("ok")),
                                       agent_version=str(info.get("agent_version") or ""))
            await ctx.reply({"probe": info, "host": await dao_obj.get(host["id"])})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("host.probe failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.probe failed")

    async def handle_host_exec(params: Any, ctx: Context) -> None:
        """Execute one command on the remote host (M5 execution base)."""
        try:
            check_params(params, expected_keys={"host_id", "command"})
            dao_obj = await dao_factory()
            host = await dao_obj.get(str(params["host_id"]))
            if host is None:
                raise HandlerError(INVALID_PARAMS,
                                   f"unknown host_id: {params['host_id']!r}")
            if not host.get("enabled"):
                raise HandlerError(INVALID_PARAMS, "host is disabled")
            command = str(params["command"])
            if not command.strip():
                raise HandlerError(INVALID_PARAMS, "command cannot be empty")
            timeout = float(params.get("timeout_s") or 300.0)
            result = await run_ssh(host, command, timeout=timeout, runner=runner)
            await ctx.reply({
                "ok": result.ok,
                "exit_code": result.exit_code,
                "stdout": result.stdout[-20000:],
                "stderr": result.stderr[-20000:],
                "timed_out": result.timed_out,
            })
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("host.exec failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.exec failed")

    async def handle_host_push(params: Any, ctx: Context) -> None:
        """Upload a local file to the host with sha256 verification (M5)."""
        try:
            try:
                params = HostPushParams.model_validate(params).model_dump()
            except ValidationError as exc:
                raise HandlerError(INVALID_PARAMS, str(exc)) from exc
            dao_obj = await dao_factory()
            host = await dao_obj.get(str(params["host_id"]))
            if host is None:
                raise HandlerError(INVALID_PARAMS,
                                   f"unknown host_id: {params['host_id']!r}")
            if not host.get("enabled"):
                raise HandlerError(INVALID_PARAMS, "host is disabled")
            local_path = Path(str(params["local_path"])).expanduser()
            if not local_path.is_file():
                raise HandlerError(INVALID_PARAMS,
                                   f"local_path is not a file: {local_path}")
            remote_path = _upload_target(params["remote_path"])
            result = await push_file(host, local_path, remote_path,
                                     runner=runner, transfer_id=params.get("transfer_id"))
            await ctx.reply({
                "ok": result.ok,
                "exit_code": result.exit_code,
                "stderr": result.stderr[-2000:],
                "remote_path": remote_path,
                "transfer_id": result.transfer_id,
                "publication_state": result.publication_state,
                "backup_path": result.backup_path,
            })
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("host.push failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.push failed")

    async def handle_host_transfer_status(params: Any, ctx: Context) -> None:
        """Query a known upload journal without repeating a publication."""
        try:
            try:
                validated = HostTransferStatusParams.model_validate(params)
            except ValidationError as exc:
                raise HandlerError(INVALID_PARAMS, str(exc)) from exc
            host = await _require_host(validated.host_id)
            if not host.get("enabled"):
                raise HandlerError(INVALID_PARAMS, "host is disabled")
            remote_path = _upload_target(validated.remote_path)
            result = await upload_action(host, remote_path, validated.transfer_id,
                                         "status", runner=runner)
            await ctx.reply({
                "ok": result.ok, "exit_code": result.exit_code,
                "stderr": result.stderr[-2000:], "remote_path": remote_path,
                "transfer_id": result.transfer_id,
                "publication_state": result.publication_state,
                "backup_path": result.backup_path,
            })
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("host.transfer_status failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.transfer_status failed")

    async def handle_host_fetch(params: Any, ctx: Context) -> None:
        """Download a remote file to the local machine with sha256 verification."""
        try:
            check_params(params, expected_keys={"host_id", "remote_path", "local_path"})
            dao_obj = await dao_factory()
            host = await dao_obj.get(str(params["host_id"]))
            if host is None:
                raise HandlerError(INVALID_PARAMS,
                                   f"unknown host_id: {params['host_id']!r}")
            if not host.get("enabled"):
                raise HandlerError(INVALID_PARAMS, "host is disabled")
            remote_path = str(params["remote_path"]).strip()
            if not remote_path or remote_path.startswith("-"):
                raise HandlerError(INVALID_PARAMS, "remote_path must be an absolute path")
            local_path = Path(str(params["local_path"])).expanduser()
            local_path.parent.mkdir(parents=True, exist_ok=True)
            result = await fetch_file(host, remote_path, local_path,
                                      runner=runner)
            await ctx.reply({
                "ok": result.ok,
                "exit_code": result.exit_code,
                "stderr": result.stderr[-2000:],
                "local_path": str(local_path),
            })
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("host.fetch failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.fetch failed")

    async def handle_host_connect(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"host_id"})
            if set(params) - {"host_id", "remote_port", "local_port"}:
                raise HandlerError(INVALID_PARAMS, "unexpected connection parameters")
            async with lifecycle_lock:
                host = await _require_host(str(params["host_id"]))
                connection = await tunnels.connect(
                    host, remote_port=params.get("remote_port", 8765),
                    local_port=params.get("local_port", 0))
            await ctx.reply({"connection": connection})
        except (HandlerError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("host.connect failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.connect failed")

    async def handle_host_disconnect(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"host_id"})
            async with lifecycle_lock:
                host = await _require_host(str(params["host_id"]))
                connection = await tunnels.disconnect(host["id"])
            await ctx.reply({"connection": connection})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)

    async def handle_host_connection(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"host_id"})
            host = await _require_host(str(params["host_id"]))
            await ctx.reply({"connection": tunnels.connection(host["id"])})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)

    server.register("host.connect", handle_host_connect)
    server.register("host.disconnect", handle_host_disconnect)
    server.register("host.connection", handle_host_connection)
    server.register("host.create", handle_host_create)
    server.register("host.list", handle_host_list)
    server.register("host.get", handle_host_get)
    server.register("host.update", handle_host_update)
    server.register("host.delete", handle_host_delete)
    server.register("host.probe", handle_host_probe)
    server.register("host.exec", handle_host_exec)
    server.register("host.push", handle_host_push)
    server.register("host.fetch", handle_host_fetch)
    server.register("host.transfer_status", handle_host_transfer_status)


def _make_dao_factory(dao: Any | None) -> Any:
    if dao is not None:

        async def _injected() -> Any:
            return dao

        return _injected

    async def _lazy() -> Any:
        from ..app import get_db, init_runtime

        await init_runtime()
        db = get_db()
        if db is None:
            raise HandlerError(INTERNAL_ERROR, "storage unavailable")
        return HostsDAO(db)

    return _lazy


__all__ = ["register_host_handlers"]
