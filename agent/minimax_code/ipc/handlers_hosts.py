"""JSON-RPC handlers for the ``host.*`` namespace (M5).

Remote Linux hosts: configuration, diagnosis/version handshake, execution,
verified file transfer and queryable upload publication journals.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import re
import shlex
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from ..hosts.ssh import fetch_file, probe_host, push_file, run_ssh, upload_action
from ..hosts.tunnel import HostTunnelManager, build_tunnel_args
from ..storage.dao.hosts import HostsDAO
from .handler_utils import HandlerError, check_params
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    PERMISSION_DENIED,
    HostBindingParams,
    HostBoundTransferStatusParams,
    HostBoundFetchParams,
    HostDirectoryListParams,
    HostFetchPreviewParams,
    HostPushParams,
    HostServiceAutostartParams,
    HostServiceLogsParams,
    HostServiceStartParams,
    HostServiceStatusParams,
    HostServiceStopParams,
    HostTransferStatusParams,
)
from .server import Context

logger = logging.getLogger(__name__)


def _validate_port(port: Any) -> int:
    if isinstance(port, bool) or not isinstance(port, (int, str)):
        raise HandlerError(INVALID_PARAMS, "port must be an integer")
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
            remote_port = _validate_port(params.get("remote_port", 8765))
            identity_file = str(params.get("identity_file") or "").strip()
            if identity_file and not Path(identity_file).expanduser().exists():
                raise HandlerError(INVALID_PARAMS,
                                   f"identity_file does not exist: {identity_file}")
            dao_obj = await dao_factory()
            if await dao_obj.get_by_name(name) is not None:
                raise HandlerError(INVALID_PARAMS, f"host name already taken: {name!r}")
            row = await dao_obj.create(name=name, hostname=hostname, username=username,
                                       port=port, identity_file=identity_file,
                                       remote_port=remote_port)
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
            if params.get("remote_port") is not None:
                updates["remote_port"] = _validate_port(params["remote_port"])
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
                if ({"hostname", "username", "port", "identity_file", "remote_port"} & updates.keys()
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
            from ..execution import ExecutionDeniedError, network_tools_restricted, require_write_access
            from .handlers_terminal import terminal_permission_action

            try:
                require_write_access()
                if network_tools_restricted():
                    raise ExecutionDeniedError("Remote commands are unavailable under this task's network restriction")
            except ExecutionDeniedError as exc:
                raise HandlerError(PERMISSION_DENIED, str(exc)) from exc
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
            action = await terminal_permission_action(ctx.server, "host.exec")
            if action != "allow":
                raise HandlerError(PERMISSION_DENIED,
                    "Remote command denied by permission policy" if action == "deny" else
                    "Remote command requires approval; host.exec has no interactive approval route")
            latest = await dao_obj.get(host["id"])
            fields = ("hostname", "username", "port", "identity_file", "enabled")
            if not latest or not latest.get("enabled") or any(latest.get(k) != host.get(k) for k in fields):
                raise HandlerError(INVALID_PARAMS, "Host configuration changed; retry after review")
            if await terminal_permission_action(ctx.server, "host.exec") != "allow":
                raise HandlerError(PERMISSION_DENIED, "Remote command permission changed; retry after review")
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
            from ..execution import require_write_access

            require_write_access()
            check_params(params, expected_keys={"host_id", "remote_path", "local_path"})
            dao_obj = await dao_factory()
            host = await dao_obj.get(str(params["host_id"]))
            if host is None:
                raise HandlerError(INVALID_PARAMS,
                                   f"unknown host_id: {params['host_id']!r}")
            if not host.get("enabled"):
                raise HandlerError(INVALID_PARAMS, "host is disabled")
            remote_path = _upload_target(str(params["remote_path"]).strip())
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

    async def _bound_host(request: HostBindingParams) -> dict[str, Any]:
        # Caller holds lifecycle_lock while checking and using this host snapshot.
        from ..app import ensure_db
        from ..storage.dao.text_artifacts import ArtifactDAO

        host = await _require_host(request.host_id)
        if not host.get("enabled"):
            raise HandlerError(INVALID_PARAMS, "host is disabled")
        db = await ensure_db()
        if db is None or await ArtifactDAO(db).service_id() != request.expected_service_id:
            raise HandlerError(INVALID_PARAMS, "service identity changed or unavailable")
        for key in ("hostname", "username", "port", "identity_file", "remote_port"):
            if host.get(key) != getattr(request, key):
                raise HandlerError(INVALID_PARAMS, "host configuration changed; review again")
        build_tunnel_args(host, 1, request.remote_port)
        return host

    async def _bound_service_host(request: HostServiceStartParams) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service", request.unit):
            raise HandlerError(INVALID_PARAMS, "invalid service unit")
        return await _bound_host(request)

    async def handle_host_directory_list(params: Any, ctx: Context) -> None:
        """Read a page of a bound host directory without following links."""
        from ..hosts.directory import directory_command

        try:
            request = HostDirectoryListParams.model_validate(params)
            path = request.remote_path
            if (not path.startswith("/") or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in path) or
                    (path != "/" and any(part in {"", ".", ".."} for part in path.split("/")[1:]))):
                raise HandlerError(INVALID_PARAMS, "invalid remote directory")
            async with lifecycle_lock:
                host = await _bound_host(request)
                result = await run_ssh(host, directory_command(path, request.offset), timeout=15,
                                       runner=runner, strict_host_key=True)
            if not result.ok or len(result.stdout) > 20000:
                raise ValueError("Remote directory unavailable")
            data = json.loads(result.stdout)
            if (not isinstance(data, dict) or set(data) != {"path", "entries", "offset", "next_offset", "limited"}
                    or data["path"] != path or type(data["offset"]) is not int or data["offset"] != request.offset
                    or type(data["limited"]) is not bool or not isinstance(data["entries"], list)
                    or len(data["entries"]) > 200):
                raise ValueError("Invalid remote directory reply")
            next_offset = data["next_offset"]
            if next_offset is not None and (type(next_offset) is not int or not request.offset < next_offset <= 10000):
                raise ValueError("Invalid directory cursor")
            for entry in data["entries"]:
                if (not isinstance(entry, dict) or set(entry) != {"name", "kind"}
                        or not isinstance(entry["name"], str) or not entry["name"] or len(entry["name"]) > 4096
                        or entry["name"] in {".", ".."} or "/" in entry["name"]
                        or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in entry["name"])
                        or entry["kind"] not in {"directory", "file"}):
                    raise ValueError("Invalid directory entry")
            await ctx.reply(data)
        except (ValidationError, HandlerError, ValueError, UnicodeError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("host.directory_list failed")
            await ctx.reply_error(INTERNAL_ERROR, "Remote directory unavailable")

    async def handle_host_fetch_bound(params: Any, ctx: Context) -> None:
        """Copy one bound remote regular file to a new local service path."""
        from ..execution import ExecutionDeniedError, network_tools_restricted, require_write_access
        from ..host_download_http import _read

        try:
            request = HostBoundFetchParams.model_validate(params)
            try:
                require_write_access()
                if network_tools_restricted():
                    raise ExecutionDeniedError("Remote transfers are unavailable under this task's network restriction")
            except ExecutionDeniedError as exc:
                raise HandlerError(PERMISSION_DENIED, str(exc)) from exc
            parts = request.remote_path.split("/")
            if (not request.remote_path.startswith("/") or any(p in {"", ".", ".."} for p in parts[1:])
                    or "\0" in request.remote_path):
                raise HandlerError(INVALID_PARAMS, "invalid remote path")
            destination = Path(request.local_path).expanduser()
            if not destination.is_absolute() or not destination.name:
                raise HandlerError(INVALID_PARAMS, "local path must be an absolute file path")
            destination = destination.absolute()
            if not destination.parent.is_dir() or destination.parent.resolve() != destination.parent:
                raise HandlerError(INVALID_PARAMS, "local parent must exist without symbolic links")
            before = destination.parent.stat()
            if not before.st_ino:
                raise HandlerError(INVALID_PARAMS, "local parent directory identity is unavailable")
            parent_identity = before.st_dev, before.st_ino
            async with lifecycle_lock:
                host = await _bound_host(request)
                if os.path.lexists(destination):
                    await ctx.reply({"ok": False, "reason": "exists", "sha256": None, "size_bytes": None})
                    return
                data, digest = await _read(host, request.remote_path, strict_host_key=True)
                stage_fd, stage_name = tempfile.mkstemp(prefix=".minimax-bound-fetch-", dir=destination.parent)
                staged = Path(stage_name)
                stage_identity = os.fstat(stage_fd)
                if not stage_identity.st_ino:
                    os.close(stage_fd)
                    raise ValueError("Local staging identity is unavailable; inspect temporary file")
                try:
                    with os.fdopen(stage_fd, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    with staged.open("rb") as stream:
                        opened = os.fstat(stream.fileno())
                        if (opened.st_dev, opened.st_ino) != (stage_identity.st_dev, stage_identity.st_ino):
                            raise ValueError("Local staging identity changed")
                        staged_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                    if staged_hash != digest:
                        raise ValueError("Staged local file failed checksum verification")
                    current = destination.parent.stat()
                    if destination.parent.resolve() != destination.parent or (current.st_dev, current.st_ino) != parent_identity:
                        raise ValueError("Local destination directory changed; review before retrying")
                    stage_now = staged.lstat()
                    if staged.is_symlink() or stage_now.st_nlink != 1 or (stage_now.st_dev, stage_now.st_ino) != (stage_identity.st_dev, stage_identity.st_ino):
                        raise ValueError("Local staging identity changed")
                    try:
                        os.link(staged, destination)
                    except FileExistsError:
                        await ctx.reply({"ok": False, "reason": "exists", "sha256": None, "size_bytes": None})
                        return
                finally:
                    try:
                        now = staged.lstat()
                        if not staged.is_symlink() and (now.st_dev, now.st_ino) == (stage_identity.st_dev, stage_identity.st_ino):
                            staged.unlink()
                    except FileNotFoundError:
                        pass
            await ctx.reply({"ok": True, "reason": None, "sha256": digest, "size_bytes": len(data)})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except (ValidationError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("host.fetch_bound failed")
            await ctx.reply_error(INTERNAL_ERROR, "Download not confirmed; inspect destination before retrying")

    async def handle_host_fetch_preview(params: Any, ctx: Context) -> None:
        """Publish only the exact previewed remote bytes to an unused local path."""
        from ..execution import require_write_access
        from ..host_download_http import _read

        try:
            request = HostFetchPreviewParams.model_validate(params)
            require_write_access()
            parts = request.remote_path.split("/")
            if (not request.remote_path.startswith("/") or any(p in {"", ".", ".."} for p in parts[1:])
                    or "\0" in request.remote_path):
                raise HandlerError(INVALID_PARAMS, "invalid remote path")
            destination = Path(request.local_path).expanduser()
            if not destination.is_absolute() or not destination.name:
                raise HandlerError(INVALID_PARAMS, "local path must be an absolute file path")
            destination = destination.absolute()
            if (not destination.parent.is_dir() or destination.parent.resolve() != destination.parent):
                raise HandlerError(INVALID_PARAMS, "local parent must exist without symbolic links")
            parent_info = destination.parent.stat()
            if not parent_info.st_ino:
                raise HandlerError(INVALID_PARAMS, "local parent directory identity is unavailable")
            parent_identity = (parent_info.st_dev, parent_info.st_ino)
            async with lifecycle_lock:
                host = await _bound_host(request)
                data, digest = await _read(host, request.remote_path, strict_host_key=True)
                if digest != request.expected_sha256:
                    await ctx.reply({"ok": False, "reason": "changed"})
                    return
                # Same filesystem, private stage and atomic create-if-absent.
                with tempfile.TemporaryDirectory(prefix=".minimax-preview-", dir=destination.parent) as directory:
                    staged = Path(directory) / "payload"
                    with staged.open("xb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    try:
                        current_parent = destination.parent.stat()
                        if (destination.parent.resolve() != destination.parent or
                                (current_parent.st_dev, current_parent.st_ino) != parent_identity):
                            raise ValueError("Destination directory changed; review before saving")
                        os.link(staged, destination)
                    except FileExistsError:
                        await ctx.reply({"ok": False, "reason": "exists"})
                        return
            await ctx.reply({"ok": True, "reason": None})
        except (ValidationError, HandlerError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("host.fetch_preview failed")
            await ctx.reply_error(INTERNAL_ERROR, "Previewed file could not be saved; inspect destination")

    async def handle_host_reconnect_bound(params: Any, ctx: Context) -> None:
        """Replace an owned SSH forward after verifying its saved host binding."""
        try:
            request = HostBindingParams.model_validate(params)
            async with lifecycle_lock:
                host = await _bound_host(request)
                # This affects only the local forward, never the remote service.
                await tunnels.disconnect(host["id"])
                connection = await tunnels.connect(host, remote_port=request.remote_port)
            await ctx.reply({"connection": connection})
        except (ValidationError, HandlerError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("host.reconnect_bound failed")
            await ctx.reply_error(INTERNAL_ERROR, "Tunnel replacement was not confirmed")

    async def handle_host_transfer_status_bound(params: Any, ctx: Context) -> None:
        """Reconcile a journal against an explicitly bound local service and host."""
        try:
            request = HostBoundTransferStatusParams.model_validate(params)
            remote_path = _upload_target(request.remote_path)
            async with lifecycle_lock:
                host = await _bound_host(request)
                result = await upload_action(host, remote_path, request.transfer_id, "status",
                                             runner=runner, strict_host_key=True)
            await ctx.reply({"ok": result.ok, "exit_code": result.exit_code,
                             "stderr": "" if result.ok else "Transfer status unavailable",
                             "remote_path": remote_path, "transfer_id": result.transfer_id,
                             "publication_state": result.publication_state,
                             "backup_path": result.backup_path})
        except (ValidationError, HandlerError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("host.transfer_status_bound failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.transfer_status_bound unavailable")

    async def handle_host_service_status(params: Any, ctx: Context) -> None:
        """Read bounded systemd fields, without returning logs or SSH diagnostics."""
        try:
            request = HostServiceStatusParams.model_validate(params)
            async with lifecycle_lock:
                host = await _bound_service_host(request)
                # Cap remote output and discard diagnostics: they may contain paths or secrets.
                command = ("LC_ALL=C systemctl --user show --no-pager "
                           "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus,MainPID,UnitFileState "
                           f"-- {shlex.quote(request.unit)} 2>/dev/null | head -c 4097")
                result = await run_ssh(host, command, timeout=20, runner=runner, strict_host_key=True)
            fields = {}
            expected = {"LoadState", "ActiveState", "SubState", "Result", "ExecMainStatus", "MainPID"}
            allowed = expected | {"UnitFileState"}
            if result.ok and len(result.stdout) <= 4096:
                for line in result.stdout.splitlines():
                    key, separator, value = line.partition("=")
                    if (not separator or key not in allowed or key in fields
                            or (not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)
                                and not (key == "UnitFileState" and value == ""))):
                        fields = {}
                        break
                    fields[key] = value
            if (not expected.issubset(fields) or not fields.get("ExecMainStatus", "").isdigit()
                    or not fields.get("MainPID", "").isdigit()):
                await ctx.reply({"ok": False, "reason": "timeout" if result.timed_out
                                 else "unavailable", "status": None})
                return
            fields["UnitFileState"] = fields.get("UnitFileState") or "unknown"
            await ctx.reply({"ok": True, "reason": None, "status": fields})
        except (ValidationError, HandlerError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("host.service_status failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.service_status unavailable")

    async def handle_host_service_logs(params: Any, ctx: Context) -> None:
        """Return only an explicitly requested, bounded user-service journal excerpt."""
        try:
            request = HostServiceLogsParams.model_validate(params)
            async with lifecycle_lock:
                host = await _bound_service_host(request)
                command = ("LC_ALL=C SYSTEMD_COLORS=0 journalctl --user --no-pager "
                           "--output=short-iso --lines=100 "
                           f"--unit={shlex.quote(request.unit)} 2>/dev/null | head -c 16385")
                result = await run_ssh(host, command, timeout=20, runner=runner, strict_host_key=True)
            if result.timed_out or not result.ok or not result.stdout:
                await ctx.reply({"ok": False, "reason": "timeout" if result.timed_out
                                 else "unavailable_or_empty", "text": None, "truncated": False})
                return
            raw = result.stdout.encode("utf-8")
            truncated = len(raw) > 16384
            excerpt = raw[:16384].decode("utf-8", errors="ignore")
            # Eliminate terminal control and directional formatting; this is not credential redaction.
            excerpt = "".join(c for c in excerpt if c in "\n\t" or
                              (unicodedata.category(c) not in {"Cc", "Cf", "Cs"}))
            await ctx.reply({"ok": True, "reason": None, "text": excerpt, "truncated": truncated})
        except (ValidationError, HandlerError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("host.service_logs failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.service_logs unavailable")

    async def handle_host_service_autostart(params: Any, ctx: Context) -> None:
        """Enable or disable a user unit without starting or stopping it."""
        from ..execution import require_write_access

        try:
            request = HostServiceAutostartParams.model_validate(params)
            require_write_access()
            async with lifecycle_lock:
                host = await _bound_service_host(request)
                action = "enable" if request.enabled else "disable"
                result = await run_ssh(
                    host, f"systemctl --user {action} -- {shlex.quote(request.unit)}",
                    timeout=30, runner=runner, strict_host_key=True)
            await ctx.reply({"ok": result.ok, "timed_out": result.timed_out})
        except (ValidationError, HandlerError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("host.service_autostart failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.service_autostart failed; outcome may be unknown")

    async def handle_host_service_stop(params: Any, ctx: Context) -> None:
        """Stop one confirmed existing user unit without disabling or restarting it."""
        from ..execution import require_write_access

        try:
            request = HostServiceStopParams.model_validate(params)
            if request.acknowledge_interrupt is not True:
                raise HandlerError(INVALID_PARAMS, "Explicit interruption acknowledgement is required")
            require_write_access()
            async with lifecycle_lock:
                host = await _bound_service_host(request)
                result = await run_ssh(
                    host, f"systemctl --user --no-ask-password stop -- {shlex.quote(request.unit)}",
                    timeout=90, runner=runner, strict_host_key=True)
            await ctx.reply({"ok": result.ok, "timed_out": result.timed_out})
        except (ValidationError, HandlerError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("host.service_stop failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.service_stop failed; outcome may be unknown")

    async def handle_host_service_start(params: Any, ctx: Context) -> None:
        """Explicitly start one already installed user unit; never install or restart it."""
        from ..execution import require_write_access

        try:
            request = HostServiceStartParams.model_validate(params)
            require_write_access()
            async with lifecycle_lock:
                host = await _bound_service_host(request)
                result = await run_ssh(host, f"systemctl --user start -- {shlex.quote(request.unit)}",
                                       timeout=30, runner=runner, strict_host_key=True)
            await ctx.reply({"ok": result.ok, "exit_code": result.exit_code,
                             "stderr": "" if result.ok else "Remote service start was not confirmed",
                             "timed_out": result.timed_out})
        except (ValidationError, HandlerError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("host.service_start failed")
            await ctx.reply_error(INTERNAL_ERROR, "host.service_start failed; outcome may be unknown")

    async def handle_host_connect(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"host_id"})
            if set(params) - {"host_id", "remote_port", "local_port"}:
                raise HandlerError(INVALID_PARAMS, "unexpected connection parameters")
            async with lifecycle_lock:
                host = await _require_host(str(params["host_id"]))
                connection = await tunnels.connect(
                    host, remote_port=params.get("remote_port", host.get("remote_port", 8765)),
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

    server.register("host.directory_list", handle_host_directory_list)
    server.register("host.fetch_bound", handle_host_fetch_bound)
    server.register("host.fetch_preview", handle_host_fetch_preview)
    server.register("host.reconnect_bound", handle_host_reconnect_bound)
    server.register("host.transfer_status_bound", handle_host_transfer_status_bound)
    server.register("host.service_autostart", handle_host_service_autostart)
    server.register("host.service_logs", handle_host_service_logs)
    server.register("host.service_status", handle_host_service_status)
    server.register("host.service_stop", handle_host_service_stop)
    server.register("host.service_start", handle_host_service_start)
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
