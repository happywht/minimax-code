"""IPC handlers — ``runtime.*`` namespace (R12 crash recovery).

Read-only diagnostic surface for the boot-time crash-recovery pipeline.
The heavy lifting lives in :func:`minimax_code.app._run_crash_recovery`
(invoked once from :func:`_maybe_open_db`); these handlers just expose
its result so the frontend Settings page can surface "recovered N runs
after an unexpected shutdown".

Methods
-------
``runtime.recovery_status``
    Snapshot of the last boot's recovery: whether the previous run
    crashed, how many orphan runs were marked failed, and the run ids.
    Returns ``available:False`` before the first recovery has run.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import ValidationError

from .handler_utils import HandlerError
from .protocol import INTERNAL_ERROR
from .server import Context

logger = logging.getLogger(__name__)


async def _handle_runtime_recovery_status(params: Any, ctx: Context) -> None:
    try:
        from ..app import get_recovery_result

        result = get_recovery_result()
        if result is None:
            await ctx.reply(
                {
                    "available": False,
                    "clean_start": True,
                    "reason": "recovery has not run yet",
                }
            )
            return
        await ctx.reply({"available": True, **result})
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message)
    except Exception:
        logger.exception("runtime.recovery_status failed")
        await ctx.reply_error(INTERNAL_ERROR, "runtime.recovery_status failed")


async def _research_config(params: Any, ctx: Context, *, write: bool) -> None:
    from ..app import ensure_db
    from ..research_settings import get_config, set_config
    from .protocol import INVALID_PARAMS, ResearchConfigGetParams, ResearchConfigSetParams

    try:
        values = (ResearchConfigSetParams if write else ResearchConfigGetParams).model_validate(params)
        db = await ensure_db()
        if db is None:
            raise RuntimeError("Storage unavailable")
        if write:
            from .. import secrets
            from ..research_settings import validate_endpoint
            if values.search_url is not None:
                validate_endpoint(values.search_url)
            if values.brave_key is not None:
                key = values.brave_key
                if not key or key != key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in key) or values.clear_brave_key:
                    raise ValueError("Invalid search key")
                await asyncio.to_thread(secrets.set_provider_key, "research-brave", key)
            if values.clear_brave_key:
                await asyncio.to_thread(secrets.clear_provider_key, "research-brave")
            result = await asyncio.to_thread(set_config, db.path.parent, values.search_url, values.provider)
        else:
            result = await asyncio.to_thread(get_config, db.path.parent)
        await ctx.reply(result)
    except (ValidationError, ValueError):
        await ctx.reply_error(INVALID_PARAMS, "Invalid or unavailable research settings")
    except Exception:
        logger.exception("research settings operation failed")
        await ctx.reply_error(INTERNAL_ERROR, "Research settings unavailable")


async def _handle_research_get(params: Any, ctx: Context) -> None:
    await _research_config(params, ctx, write=False)


async def _handle_research_set(params: Any, ctx: Context) -> None:
    await _research_config(params, ctx, write=True)


async def _handle_service_identity(params: Any, ctx: Context) -> None:
    from ..app import ensure_db
    from ..storage.dao.text_artifacts import ArtifactDAO
    from .protocol import INVALID_PARAMS, ResearchConfigGetParams

    try:
        ResearchConfigGetParams.model_validate(params)
        db = await ensure_db()
        if db is None:
            raise RuntimeError("Storage unavailable")
        await ctx.reply({"service_id": await ArtifactDAO(db).service_id()})
    except ValidationError:
        await ctx.reply_error(INVALID_PARAMS, "Invalid parameters")
    except Exception:
        logger.exception("runtime.service_identity failed")
        await ctx.reply_error(INTERNAL_ERROR, "Service identity unavailable")


async def _handle_workspace_check(params: Any, ctx: Context) -> None:
    """Check the same local workspace selection as Native admission, without running a command."""
    import os
    from pathlib import PureWindowsPath

    from ..app import ensure_db
    from ..storage.dao.text_artifacts import ArtifactDAO
    from ..workspace_ctx import require_directory
    from .host_session_gate import require_host_session
    from .protocol import INVALID_PARAMS, RuntimeWorkspaceCheckParams

    try:
        request = RuntimeWorkspaceCheckParams.model_validate(params)
        db = await ensure_db()
        if db is None:
            raise RuntimeError("Storage unavailable")

        async def binding():
            session = await db.fetchone("SELECT id,project_id,workspace_path,engine FROM sessions WHERE id=?", (request.session_id,))
            if not session or session["project_id"] != request.project_id:
                raise ValueError("Session or project binding changed")
            project = await db.fetchone("SELECT id,root_path FROM projects WHERE id=?", (session["project_id"],)) if session["project_id"] else None
            identity = await ArtifactDAO(db).service_id()
            return dict(session), dict(project) if project else None, identity

        session, project, identity = await binding()
        base = {"session_id": request.session_id, "project_id": request.project_id, "service_id": identity}
        try:
            await require_host_session(request.session_id)
        except HandlerError:
            if await binding() != (session, project, identity):
                raise ValueError("Session or project binding changed during check") from None
            await ctx.reply({**base, "status": "unknown", "source": "unknown", "path": None,
                             "reason": "execution_boundary_unavailable"})
            return
        session_path = str(session.get("workspace_path") or "").strip()
        project_path = str((project or {}).get("root_path") or "").strip()
        source = "session" if session_path else "project"
        if source == "project" and session["project_id"] and not project:
            result = {"status": "error", "source": source, "path": None, "reason": "project_missing"}
        else:
            raw = session_path if source == "session" else project_path
            if not raw:
                source, raw = "default", os.environ.get("MINIMAX_CODE_WORKSPACE") or os.getcwd()
            if PureWindowsPath(str(raw)).drive.startswith("\\\\"):
                result = {"status": "unknown", "source": source, "path": None, "reason": "network_path_not_checked"}
            else:
                try:
                    slots = getattr(ctx.server, "_workspace_diagnostic_slots", None)
                    if slots is None:
                        slots = ctx.server._workspace_diagnostic_slots = asyncio.Semaphore(2)
                    await asyncio.wait_for(slots.acquire(), timeout=0.05)
                    task = asyncio.create_task(asyncio.to_thread(require_directory, str(raw)))
                    def finished(done):
                        slots.release()
                        if not done.cancelled():
                            done.exception()  # consume late errors after a timeout
                    task.add_done_callback(finished)
                    root = await asyncio.wait_for(asyncio.shield(task), timeout=2)
                    result = {"status": "ok", "source": source, "path": str(root), "reason": None}
                except TimeoutError:
                    result = {"status": "unknown", "source": source, "path": None, "reason": "check_timed_out"}
                except (OSError, RuntimeError):
                    result = {"status": "unknown", "source": source, "path": None, "reason": "check_unavailable"}
                except ValueError:
                    result = {"status": "error", "source": source, "path": None, "reason": "directory_unavailable"}
        if await binding() != (session, project, identity):
            raise ValueError("Session or project binding changed during check")
        if source == "default" and raw != (os.environ.get("MINIMAX_CODE_WORKSPACE") or os.getcwd()):
            raise ValueError("Default workspace changed during check")
        await require_host_session(request.session_id)
        await ctx.reply({**base, **result})
    except (ValidationError, ValueError, HandlerError) as exc:
        await ctx.reply_error(INVALID_PARAMS, str(exc))
    except Exception:
        logger.exception("workspace diagnostic failed")
        await ctx.reply_error(INTERNAL_ERROR, "Workspace diagnostic unavailable")


def register_runtime_handlers(server: Any) -> None:
    """Register all ``runtime.*`` JSON-RPC methods on *server*."""
    server.register("runtime.recovery_status", _handle_runtime_recovery_status)
    server.register("runtime.research_get", _handle_research_get)
    server.register("runtime.research_set", _handle_research_set)
    server.register("runtime.service_identity", _handle_service_identity)
    server.register("runtime.workspace_check", _handle_workspace_check)
    logger.debug("registered runtime.* handlers")


__all__ = ["register_runtime_handlers"]
