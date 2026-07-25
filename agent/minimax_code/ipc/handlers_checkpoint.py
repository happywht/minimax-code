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
import uuid
from pathlib import Path
from typing import Any

from ..storage.dao.checkpoints import CheckpointDAO
from ..storage.db import ensure_data_dir
from ..workspace import Checkpoint, CheckpointManager, CheckpointRestoreResult
from .handler_utils import HandlerError, check_params
from .protocol import INTERNAL_ERROR, INVALID_PARAMS
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
            workdir = await _resolve_cwd(p)

            ckpt_dao = await dao_factory()
            mgr = await manager_factory()
            checkpoint_id = f"ckpt_{uuid.uuid4().hex[:12]}"
            snapshot = await mgr.create(
                cwd=workdir,
                checkpoint_id=checkpoint_id,
                session_id=session_id,
                label=label,
                message=message,
            )
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
            )
            await ctx.reply({"checkpoint": row})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("checkpoint.create failed")
            await ctx.reply_error(INTERNAL_ERROR, f"checkpoint.create failed: {exc}")

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
            workdir = await _resolve_cwd(p)
            ckpt_dao = await dao_factory()
            row = await ckpt_dao.get(checkpoint_id)
            if row is None:
                raise HandlerError(
                    INVALID_PARAMS, f"unknown checkpoint_id: {checkpoint_id!r}"
                )
            mgr = await manager_factory()
            result = await mgr.restore(workdir, _row_to_checkpoint(row))
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
            workdir = await _resolve_cwd(p)
            ckpt_dao = await dao_factory()
            row = await ckpt_dao.get(checkpoint_id)
            if row is None:
                raise HandlerError(
                    INVALID_PARAMS, f"unknown checkpoint_id: {checkpoint_id!r}"
                )
            mgr = await manager_factory()
            diff = await mgr.diff(workdir, _row_to_checkpoint(row))
            await ctx.reply(
                {
                    "checkpoint_id": diff.checkpoint_id,
                    "available": diff.available,
                    "patch": diff.patch,
                    "files": diff.files,
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
            deleted = await ckpt_dao.delete(checkpoint_id)
            if not deleted:
                raise HandlerError(
                    INVALID_PARAMS, f"unknown checkpoint_id: {checkpoint_id!r}"
                )
            mgr = await manager_factory()
            removed_snapshot = mgr.delete_snapshot(checkpoint_id)
            await ctx.reply({"ok": True, "removed_snapshot": removed_snapshot})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("checkpoint.delete failed")
            await ctx.reply_error(INTERNAL_ERROR, f"checkpoint.delete failed: {exc}")

    server.register("checkpoint.create", handle_create)
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


async def _resolve_cwd(params: dict[str, Any]) -> Path:
    """Resolve the working tree to snapshot/rewind.

    Honours an explicit ``cwd`` (absolute or repo-relative); otherwise
    falls back to the current git repo root. A non-repo cwd is a
    user-facing error — storing an empty snapshot is pointless.
    """
    explicit = _string_param(params, "cwd")
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        return candidate
    return await _git_repo_root()


async def _git_repo_root() -> Path:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "rev-parse",
        "--show-toplevel",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
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
        "skipped_existing": result.skipped_existing,
        "warnings": result.warnings,
    }


__all__ = ["register_checkpoint_handlers"]
