"""Explicit recovery of verified native pre-edit backups into new workspace files."""
from __future__ import annotations

import hashlib
import asyncio
import heapq
import json
import logging
import os
import re
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from ..agent.tools.file_ops import safe_resolve
from ..app import ensure_db, get_sessions_dao
from ..file_mutations import atomic_create_bytes
from ..storage.dao.text_artifacts import ArtifactDAO
from ..text_artifacts import _identity, _regular, _safe_open
from ..workspace.child import _included
from ..workspace_ctx import resolve_root_for_session
from .handler_utils import HandlerError
from .handlers_permissions import _ensure_permission_store
from .host_session_gate import require_chat_session
from .protocol import (INTERNAL_ERROR, INVALID_PARAMS, PERMISSION_DENIED,
                       FileBackupListParams, FileBackupRestoreParams)

logger = logging.getLogger(__name__)
_NAME = re.compile(r"^\d{8}_\d{12}_[0-9a-f]{16}_[0-9a-f]{32}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_MAX = 16 * 1024 * 1024


def _read(path: Path, limit: int) -> bytes:
    with _safe_open(path) as fd:
        before = os.fstat(fd)
        _regular(before)
        if before.st_size > limit:
            raise ValueError("Backup exceeds the size limit")
        chunks = []
        remaining = limit + 1
        while remaining:
            part = os.read(fd, min(65536, remaining))
            if not part:
                break
            chunks.append(part)
            remaining -= len(part)
        data = b"".join(chunks)
        if (len(data) != before.st_size or len(data) > limit
                or _identity(before) != _identity(os.fstat(fd))
                or _identity(before) != _identity(path.lstat())):
            raise ValueError("Backup changed while reading")
        return data


def _relative(name: str, root: Path) -> Path:
    p = PurePosixPath(name)
    if (not name or len(name) > 1024 or p.is_absolute() or p.as_posix() != name
            or any(c in name for c in "\\:\x00") or ".." in p.parts
            or any(ord(c) < 32 for c in name) or not _included(name)):
        raise ValueError("Invalid or excluded workspace path")
    target = safe_resolve(name, workspace=root)
    if target != root / Path(name):
        raise ValueError("Path contains a link or non-canonical component")
    return target


def _record(root: Path, backup_id: str) -> tuple[dict, bytes]:
    if not _NAME.fullmatch(backup_id):
        raise ValueError("Invalid backup identifier")
    directory = root / ".minimax" / "backups"
    manifest = directory / (backup_id + ".json")
    backup = directory / (backup_id + ".bak")
    metadata = json.loads(_read(manifest, 8192).decode("utf-8"))
    if not isinstance(metadata, dict) or metadata.get("backup_path") != str(backup):
        raise ValueError("Backup manifest does not match its location")
    original = metadata.get("original_path")
    sha = metadata.get("sha256")
    if (not isinstance(original, str) or not isinstance(sha, str) or not _SHA.fullmatch(sha)
            or not isinstance(metadata.get("timestamp"), str)):
        raise ValueError("Invalid backup manifest")
    original_path = Path(original)
    try:
        name = original_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("Backup source is outside the current workspace") from exc
    _relative(name, root)
    data = _read(backup, _MAX)
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError("Backup digest does not match its manifest")
    return {"id": backup_id, "original_path": name, "sha256": sha,
            "timestamp": metadata.get("timestamp"), "bytes": len(data)}, data


async def _binding(p, ctx, *, write=False):
    await require_chat_session(p.session_id)
    db = await ensure_db()
    dao = get_sessions_dao()
    session = await dao.get(p.session_id) if dao else None
    if (not session or session.get("archived") or session.get("project_id") != p.project_id
            or await ArtifactDAO(db).service_id() != p.expected_service_id):
        raise HandlerError(INVALID_PARAMS, "Service, session or project binding changed")
    store = await _ensure_permission_store(ctx.server)
    for tool in (("read_file", "write_file") if write else ("read_file",)):
        rule = store.lookup(tool) if store else None
        if not rule or str(rule.get("action") or "").lower() != "allow" or rule.get("scope") not in (None, "", "global"):
            raise HandlerError(PERMISSION_DENIED, f"{tool} requires a global allow rule")
    root = await resolve_root_for_session(p.session_id)
    info = root.stat()
    return root, (info.st_dev, info.st_ino)


def _list_backups(root: Path) -> tuple[list[dict], bool]:
        directory = root / ".minimax" / "backups"
        items = []
        truncated = False
        try:
            with _safe_open(directory, directory=True), os.scandir(directory) as entries:
                names = heapq.nlargest(501, (entry.name for entry in entries
                                               if entry.name.endswith(".json")))
            truncated = len(names) > 500
            for name in names[:500]:
                try:
                    item, _ = _record(root, name[:-5])
                    items.append(item)
                except (OSError, ValueError, UnicodeError):
                    continue
        except FileNotFoundError:
            pass
        return items, truncated


async def handle_list(params, ctx):
    try:
        p = FileBackupListParams.model_validate(params)
        root, identity = await _binding(p, ctx)
        items, truncated = await asyncio.to_thread(_list_backups, root)
        if await _binding(p, ctx) != (root, identity):
            raise ValueError("Workspace changed during listing")
        await ctx.reply({"items": items, "truncated": truncated, "workspace": str(root),
                         "workspace_identity": f"{identity[0]}:{identity[1]}", "service_id": p.expected_service_id})
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except (ValidationError, OSError, ValueError, UnicodeError) as exc:
        await ctx.reply_error(INVALID_PARAMS, str(exc))
    except Exception:
        logger.exception("file_backup.list failed")
        await ctx.reply_error(INTERNAL_ERROR, "Backup listing failed")


async def handle_restore(params, ctx):
    try:
        p = FileBackupRestoreParams.model_validate(params)
        root, identity = await _binding(p, ctx, write=True)
        if str(root) != p.workspace or f"{identity[0]}:{identity[1]}" != p.workspace_identity:
            raise ValueError("Workspace changed since backup selection")
        item, data = await asyncio.to_thread(_record, root, p.backup_id)
        if item["sha256"] != p.expected_sha256:
            raise ValueError("Backup changed since selection")
        destination = _relative(p.destination, root)
        if not destination.parent.is_dir():
            raise ValueError("Destination parent must already exist")
        if await _binding(p, ctx, write=True) != (root, identity):
            raise ValueError("Workspace changed before restore")
        await asyncio.to_thread(atomic_create_bytes, destination, data)
        await ctx.reply({"destination": p.destination, "sha256": item["sha256"], "backup_id": p.backup_id})
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except (ValidationError, OSError, ValueError, UnicodeError) as exc:
        await ctx.reply_error(INVALID_PARAMS, str(exc))
    except Exception:
        logger.exception("file_backup.restore_copy failed")
        await ctx.reply_error(INTERNAL_ERROR, "Backup restore outcome may be uncertain; inspect destination")


def register_file_backup_handlers(server):
    server.register("file_backup.list", handle_list)
    server.register("file_backup.restore_copy", handle_restore)
