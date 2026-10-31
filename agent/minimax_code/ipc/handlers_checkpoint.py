"""JSON-RPC handlers for ``checkpoint.*`` — workspace snapshot layer (R310).

Exposes five methods over the ``checkpoint.*`` namespace (distinct from
the worktree-only ``workspace.*`` namespace in
:mod:`minimax_code.ipc.handlers_workspace`):

* ``checkpoint.create``  — snapshot a working tree (git stash + untracked
  copy) and persist the row.
* ``checkpoint.list``    — page through a session's checkpoints.
* ``checkpoint.restore`` — rewind a working tree to a checkpoint.
* ``checkpoint.diff``    — preview the tracked-change patch a checkpoint
  would restore.
* ``checkpoint.delete``  — drop the row and its on-disk untracked copy.

The handlers are thin glue: parameter validation + lazy DAO/Manager
resolution. All git/file heavy lifting lives in
:class:`~minimax_code.workspace.checkpoint.CheckpointManager`, which is
fault-tolerant (git failures become warnings, never exceptions). The
handler layer adds a hard requirement that the resolved ``cwd`` is
inside a git repo — an empty snapshot is pointless to store, so a
non-repo cwd is a user-facing error, not a degraded row.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..execution import ExecutionDeniedError, capture_output, process_cwd, spawn_exec
from ..storage.dao.checkpoints import CheckpointDAO
from ..storage.db import ensure_data_dir
from ..workspace import Checkpoint, CheckpointManager, CheckpointRestoreResult
from ..workspace_ctx import (
    WorkspaceUnavailableError,
    require_directory,
    resolve_root_for_session,
)
from .handler_utils import HandlerError, check_params, ensure_cwd_within_root
from .host_session_gate import require_host_session
from .protocol import INTERNAL_ERROR, INVALID_PARAMS, CheckpointRebindParams
from .server import Context

logger = logging.getLogger(__name__)

#: Caps for caller-supplied limit/offset so a typo can't pull the whole
#: table or page past the end forever.
_MAX_LIMIT = 200
_MAX_OFFSET = 10_000


def register_checkpoint_handlers(
    server: Any,
    *,
    dao: Any = None,
    manager: Any = None,
) -> None:
    """Register the ``checkpoint.*`` namespace on ``server``.

    ``dao`` / ``manager`` are optional test seams. When omitted the
    handlers resolve the process-wide DAO (lazily opening the DB on
    first call) and build a :class:`CheckpointManager` rooted at
    ``<data_dir>/checkpoints`` — the same lazy-singleton posture as the
    other storage-backed namespaces.
    """
    dao_factory = _make_dao_factory(dao)
    manager_factory = _make_manager_factory(manager)

    # ------------------------------------------------------------------
    # checkpoint.create
    # ------------------------------------------------------------------

    async def handle_create(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"session_id"})
            p = params or {}
            session_id = _string_required(p, "session_id")
            label = _string_param(p, "label") or "Checkpoint"
            message = _string_param(p, "message") or ""
            workdir = await _resolve_cwd(p, session_id=session_id)

            ckpt_dao = await dao_factory()
            mgr = await manager_factory()
            checkpoint_id = f"ckpt_{uuid.uuid4().hex[:12]}"
            from ..runs import get_run_service

            try:
                async with get_run_service(ctx.server).workspace_maintenance(str(workdir)) as verify_workspace:
                    if await _resolve_cwd(p, session_id=session_id) != workdir:
                        raise ValueError("Checkpoint workspace changed; retry after review")
                    verify_workspace()
                    snapshot = await mgr.create(
                        cwd=workdir,
                        checkpoint_id=checkpoint_id,
                        session_id=session_id,
                        label=label,
                        message=message,
                    )
                    if await _resolve_cwd(p, session_id=session_id) != workdir:
                        raise ValueError("Checkpoint workspace changed during capture; retry after review")
                    verify_workspace()
                    row = await ckpt_dao.create(
                        session_id=session_id,
                        label=label,
                        message=message,
                        git_stash_ref=snapshot.git_stash_ref,
                        branch=snapshot.branch,
                        tracked_files=snapshot.tracked_files,
                        untracked_files=snapshot.untracked_files,
                        has_untracked_snapshot=snapshot.has_untracked_snapshot,
                        checkpoint_id=checkpoint_id,
                        created_at=snapshot.created_at,
                        source_root=str(workdir),
                    )
            except ValueError as exc:
                raise HandlerError(INVALID_PARAMS, str(exc)) from exc
            await ctx.reply({"checkpoint": row, "warnings": snapshot.warnings[:100],
                             "warnings_truncated": len(snapshot.warnings) > 100})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("checkpoint.create failed")
            await ctx.reply_error(INTERNAL_ERROR, f"checkpoint.create failed: {exc}")

    # ------------------------------------------------------------------
    # checkpoint.rebind
    # ------------------------------------------------------------------

    async def handle_rebind(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"checkpoint_id", "cwd"})
            p = params or {}
            checkpoint_id = _string_required(p, "checkpoint_id")
            if not Path(_string_required(p, "cwd")).is_absolute():
                raise HandlerError(INVALID_PARAMS, "Checkpoint target must be an absolute path")
            if p.get("confirm_rebind") is not True or p.get("confirm_trusted_bundle") is not True:
                raise HandlerError(INVALID_PARAMS, "Explicit target and trusted bundle confirmations are required")
            try:
                CheckpointRebindParams.model_validate(p)
            except ValidationError as exc:
                raise HandlerError(INVALID_PARAMS, "Invalid checkpoint rebind parameters") from exc
            ckpt_dao = await dao_factory()
            row = await ckpt_dao.get(checkpoint_id)
            if row is None:
                raise HandlerError(INVALID_PARAMS, f"unknown checkpoint_id: {checkpoint_id!r}")
            session_id = str(row.get("session_id") or "") or None
            oid = row.get("git_stash_ref")
            if not session_id or not isinstance(oid, str) or not oid:
                raise HandlerError(INVALID_PARAMS, "Checkpoint has no verifiable Git object")
            if not isinstance(row.get("source_root"), str) or not row["source_root"]:
                raise HandlerError(INVALID_PARAMS, "Legacy checkpoint source is unknown; its file paths cannot be safely rebound")
            await require_host_session(session_id)
            workdir = await _resolve_cwd(p, session_id=session_id)
            try:
                process_cwd(workdir)
            except ExecutionDeniedError as exc:
                raise HandlerError(INVALID_PARAMS, str(exc)) from exc
            try:
                workspace = await resolve_root_for_session(session_id)
                from ..bundle import MAX_TOTAL_BYTES, checkpoint_git_metadata
                from ..checkpoint_git_bundle import import_pack

                pack, metadata = await asyncio.to_thread(
                    checkpoint_git_metadata, ensure_data_dir(), checkpoint_id, oid
                )
                await asyncio.to_thread(import_pack, pack, oid, row.get("source_root") or "",
                                        str(workdir), MAX_TOTAL_BYTES, metadata,
                                        relocated_workspace=str(workspace), verify_only=True)
            except (ValueError, OSError, WorkspaceUnavailableError) as exc:
                raise HandlerError(INVALID_PARAMS, f"Checkpoint target could not be verified: {exc}") from exc
            if await _resolve_cwd(p, session_id=session_id) != workdir:
                raise HandlerError(INVALID_PARAMS, "Session workspace changed during verification")
            await require_host_session(session_id)
            try:
                process_cwd(workdir)
            except ExecutionDeniedError as exc:
                raise HandlerError(INVALID_PARAMS, str(exc)) from exc
            updated = await ckpt_dao.rebind_source(
                checkpoint_id, session_id=session_id, previous_source=row.get("source_root"),
                git_stash_ref=oid, source_root=str(workdir),
            )
            if not updated:
                raise HandlerError(INVALID_PARAMS, "Checkpoint changed during verification; retry")
            await ctx.reply({"checkpoint": await ckpt_dao.get(checkpoint_id)})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            logger.exception("checkpoint.rebind failed")
            await ctx.reply_error(INTERNAL_ERROR, f"checkpoint.rebind failed: {exc}")

    # ------------------------------------------------------------------
    # checkpoint.list
    # ------------------------------------------------------------------

    async def handle_list(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"session_id"})
            p = params or {}
            session_id = _string_required(p, "session_id")
            # Clamp caller-supplied paging so a typo can't pull the
            # whole table or page past the end forever.
            limit = min(_int_param(p, "limit", default=50), _MAX_LIMIT)
            offset = min(_int_param(p, "offset", default=0), _MAX_OFFSET)
            ckpt_dao = await dao_factory()
            rows = await ckpt_dao.list_by_session(
                session_id=session_id, limit=limit, offset=offset
            )
            await ctx.reply({"checkpoints": rows, "count": len(rows)})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("checkpoint.list failed")
            await ctx.reply_error(INTERNAL_ERROR, f"checkpoint.list failed: {exc}")

    # ------------------------------------------------------------------
    # checkpoint.restore
    # ------------------------------------------------------------------

    async def handle_restore(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"checkpoint_id"})
            p = params or {}
            checkpoint_id = _string_required(p, "checkpoint_id")
            ckpt_dao = await dao_factory()
            row = await ckpt_dao.get(checkpoint_id)
            if row is None:
                raise HandlerError(
                    INVALID_PARAMS, f"unknown checkpoint_id: {checkpoint_id!r}"
                )
            # Resolve cwd after the row lookup — the checkpoint's owning
            # session decides the project-scoped root (v1.3.0).
            workdir = await _resolve_checkpoint_cwd(p, row)
            from ..runs import get_run_service

            try:
                async with get_run_service(ctx.server).workspace_maintenance(str(workdir)) as verify_workspace:
                    # The session binding can change while the reservation is acquired.
                    if await _resolve_checkpoint_cwd(p, row) != workdir:
                        raise ValueError("Checkpoint workspace changed; retry after review")
                    mgr = await manager_factory()
                    verify_workspace()
                    checkpoint = _row_to_checkpoint(row)
                    if "tracked_file" in p:
                        if "untracked_file" in p or "expected_sha256" in p:
                            raise HandlerError(INVALID_PARAMS, "tracked_file cannot be combined with untracked options")
                        if not row.get("source_root"):
                            raise HandlerError(INVALID_PARAMS, "Tracked file recovery requires a recorded repository root")
                        try:
                            result = await mgr.restore_tracked_file(
                                workdir, checkpoint, _string_required(p, "tracked_file"),
                                **({"destination": _string_required(p, "destination")} if "destination" in p else {}),
                            )
                        except ValueError as exc:
                            raise HandlerError(INVALID_PARAMS, str(exc)) from exc
                    elif "destination" in p:
                        raise HandlerError(INVALID_PARAMS, "destination requires tracked_file")
                    elif "untracked_file" in p:
                        relative = _string_required(p, "untracked_file")
                        expected = p.get("expected_sha256")
                        if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
                            raise HandlerError(INVALID_PARAMS, "expected_sha256 must be a lowercase SHA-256 digest")
                        try:
                            result = mgr.restore_untracked_file(workdir, checkpoint, relative, **({"expected_sha256": expected} if expected is not None else {}))
                        except ValueError as exc:
                            raise HandlerError(INVALID_PARAMS, str(exc)) from exc
                    else:
                        if "expected_sha256" in p:
                            raise HandlerError(INVALID_PARAMS, "expected_sha256 requires untracked_file")
                        result = await mgr.restore(workdir, checkpoint)
            except ValueError as exc:
                raise HandlerError(INVALID_PARAMS, str(exc)) from exc
            await ctx.reply({"result": _result_to_dict(result)})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("checkpoint.restore failed")
            await ctx.reply_error(INTERNAL_ERROR, f"checkpoint.restore failed: {exc}")

    # ------------------------------------------------------------------
    # checkpoint.diff
    # ------------------------------------------------------------------

    async def handle_diff(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"checkpoint_id"})
            p = params or {}
            checkpoint_id = _string_required(p, "checkpoint_id")
            ckpt_dao = await dao_factory()
            row = await ckpt_dao.get(checkpoint_id)
            if row is None:
                raise HandlerError(
                    INVALID_PARAMS, f"unknown checkpoint_id: {checkpoint_id!r}"
                )
            workdir = await _resolve_checkpoint_cwd(p, row)
            mgr = await manager_factory()
            diff = await mgr.diff(workdir, _row_to_checkpoint(row))
            await ctx.reply(
                {
                    "checkpoint_id": diff.checkpoint_id,
                    "available": diff.available,
                    "patch": diff.patch,
                    "files": diff.files,
                    "untracked_preview": await asyncio.to_thread(mgr.preview_untracked, workdir, _row_to_checkpoint(row)),
                }
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("checkpoint.diff failed")
            await ctx.reply_error(INTERNAL_ERROR, f"checkpoint.diff failed: {exc}")

    # ------------------------------------------------------------------
    # checkpoint.delete
    # ------------------------------------------------------------------

    async def handle_delete(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"checkpoint_id"})
            p = params or {}
            checkpoint_id = _string_required(p, "checkpoint_id")
            ckpt_dao = await dao_factory()
            row = await ckpt_dao.get(checkpoint_id)
            if row is None:
                raise HandlerError(
                    INVALID_PARAMS, f"unknown checkpoint_id: {checkpoint_id!r}"
                )
            mgr = await manager_factory()
            removed_snapshot = mgr.delete_snapshot(checkpoint_id)
            await ckpt_dao.delete(checkpoint_id)
            await ctx.reply({"ok": True, "removed_snapshot": removed_snapshot})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("checkpoint.delete failed")
            await ctx.reply_error(INTERNAL_ERROR, f"checkpoint.delete failed: {exc}")

    server.register("checkpoint.create", handle_create)
    server.register("checkpoint.rebind", handle_rebind)
    server.register("checkpoint.list", handle_list)
    server.register("checkpoint.restore", handle_restore)
    server.register("checkpoint.diff", handle_diff)
    server.register("checkpoint.delete", handle_delete)


# ---------------------------------------------------------------------------
# Parameter helpers
# ---------------------------------------------------------------------------


def _string_param(params: dict[str, Any], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HandlerError(INVALID_PARAMS, f"{key} must be a string")
    value = value.strip()
    return value or None


def _string_required(params: dict[str, Any], key: str) -> str:
    value = _string_param(params, key)
    if value is None:
        raise HandlerError(INVALID_PARAMS, f"{key} is required")
    return value


def _int_param(
    params: dict[str, Any], key: str, *, default: int
) -> int:
    value = params.get(key)
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise HandlerError(INVALID_PARAMS, f"{key} must be an integer")
    if value < 0:
        return default
    return value


# ---------------------------------------------------------------------------
# Lazy factories
# ---------------------------------------------------------------------------


def _make_dao_factory(dao: Any | None) -> Any:
    if dao is not None:

        async def _factory_injected() -> Any:
            return dao

        return _factory_injected

    async def _factory_lazy() -> Any:
        from ..app import ensure_db

        # ensure_db() is the process-wide DB gate: returns the already
        # open AsyncDatabase, or opens (and migrates) a fresh one. One
        # call is all the lazy wiring the checkpoint namespace needs —
        # mirroring the SessionsDAO factory in handlers_workspace but
        # wrapping CheckpointDAO instead.
        return CheckpointDAO(await ensure_db())

    return _factory_lazy


def _make_manager_factory(manager: Any | None) -> Any:
    if manager is not None:

        async def _factory_injected() -> Any:
            return manager

        return _factory_injected

    async def _factory_lazy() -> Any:
        root = ensure_data_dir() / "checkpoints"
        root.mkdir(parents=True, exist_ok=True)
        return CheckpointManager(root)

    return _factory_lazy


# ---------------------------------------------------------------------------
# cwd resolution
# ---------------------------------------------------------------------------


async def _resolve_cwd(
    params: dict[str, Any], *, session_id: str | None = None, allow_subdir: bool = False
) -> Path:
    """Resolve the working tree to snapshot/rewind.

    Both the working directory and Git repository root must stay inside
    the verified session root. Invalid bindings never fall back to another
    checkout. Git stash operates on the whole repository.
    """
    try:
        root = await resolve_root_for_session(session_id)
    except WorkspaceUnavailableError as exc:
        raise HandlerError(INVALID_PARAMS, str(exc)) from exc
    explicit = _string_param(params, "cwd")
    if explicit:
        target = require_directory(ensure_cwd_within_root(explicit, root))
    else:
        target = root

    # Git stash operates on the entire repository, even from a subdirectory.
    # The repository must therefore be inside the session workspace, not just
    # the directory supplied to Git.
    repo = await _git_repo_root(target)
    if not repo.is_relative_to(root):
        raise HandlerError(INVALID_PARAMS, "Git repository extends outside the session workspace")
    # Existing checkpoint records store untracked paths relative to their
    # original cwd, without recording that cwd. Silently changing it to the
    # repository root could restore old files into the wrong directory.
    if target != repo and not allow_subdir:
        raise HandlerError(INVALID_PARAMS, "Checkpoint working directory must be the Git repository root")
    return target


async def _resolve_checkpoint_cwd(params: dict[str, Any], row: dict[str, Any]) -> Path:
    source = row.get("source_root")
    session_id = str(row.get("session_id") or "") or None
    if not source:
        if params.get("legacy_source_confirmed") is not True or not _string_param(params, "cwd"):
            raise HandlerError(INVALID_PARAMS, "Checkpoint source is unknown. Verify its original working directory, then supply cwd and legacy_source_confirmed=true.")
        return await _resolve_cwd(params, session_id=session_id, allow_subdir=True)
    if not isinstance(source, str):
        raise HandlerError(INVALID_PARAMS, "Invalid checkpoint source")
    target_params = dict(params)
    target_params.setdefault("cwd", source)
    target = await _resolve_cwd(target_params, session_id=session_id)
    if target != Path(source).resolve():
        raise HandlerError(INVALID_PARAMS, "Checkpoint source does not match the selected repository root")
    return target



async def _git_repo_root(cwd: Path | None = None) -> Path:
    proc = await spawn_exec(
        "git",
        "rev-parse",
        "--show-toplevel",
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await capture_output(proc, timeout=30)
    if proc.returncode != 0:
        raise HandlerError(
            INVALID_PARAMS,
            "current project is not inside a Git repository "
            "(pass an explicit 'cwd' to checkpoint the session workspace)",
            {"stderr": err.decode(errors="replace")},
        )
    return Path(out.decode().strip()).resolve()


# ---------------------------------------------------------------------------
# Row <-> value-type conversion
# ---------------------------------------------------------------------------


def _row_to_checkpoint(row: dict[str, Any]) -> Checkpoint:
    """Convert a hydrated DAO row back into a :class:`Checkpoint`."""
    return Checkpoint(
        id=str(row.get("id", "")),
        session_id=str(row.get("session_id", "")),
        label=str(row.get("label", "")),
        message=str(row.get("message", "")),
        git_stash_ref=row.get("git_stash_ref"),
        branch=row.get("branch"),
        tracked_files=list(row.get("tracked_files") or []),
        untracked_files=list(row.get("untracked_files") or []),
        has_untracked_snapshot=bool(row.get("has_untracked_snapshot")),
        created_at=str(row.get("created_at", "")),
    )


def _result_to_dict(result: CheckpointRestoreResult) -> dict[str, Any]:
    return {
        "checkpoint_id": result.checkpoint_id,
        "restored": result.restored,
        "applied_stash": result.applied_stash,
        "restored_untracked": result.restored_untracked,
        "restored_tracked": result.restored_tracked,
        "skipped_existing": result.skipped_existing,
        "warnings": result.warnings,
    }


__all__ = ["register_checkpoint_handlers"]
