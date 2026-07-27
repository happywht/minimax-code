"""JSON-RPC handlers for the ``project.*`` namespace.

Projects are user-defined folders that group sessions (tasks). There is
one reserved project, ``inbox`` (收件箱), which cannot be renamed,
archived, or deleted. Deleting a regular project moves its sessions back
to the inbox instead of dropping them.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from .handler_utils import HandlerError, check_params
from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)

INBOX_ID = "inbox"


def register_project_handlers(server: Any, *, dao: Any = None) -> None:
    """Register the ``project.*`` handlers on ``server``."""
    dao_factory = _make_dao_factory(dao)

    async def handle_project_list(params: Any, ctx: Context) -> None:
        try:
            proj_dao = await dao_factory()
            p = params or {}
            archived_raw = p.get("archived")
            archived: bool | None = None if archived_raw is None else bool(archived_raw)
            projects = await proj_dao.list(archived=archived)
            await ctx.reply({"projects": projects})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("project.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "project.list failed")

    async def handle_project_create(params: Any, ctx: Context) -> None:
        try:
            proj_dao = await dao_factory()
            check_params(params, expected_keys={"name"})
            name = str(params["name"]).strip()
            if not name:
                raise HandlerError(INVALID_PARAMS, "project name cannot be empty")
            description = params.get("description", "")
            if not isinstance(description, str):
                raise HandlerError(INVALID_PARAMS, "description must be a string")
            new_id = f"proj_{uuid.uuid4().hex[:8]}"
            row = await proj_dao.create(
                id=new_id,
                name=name,
                description=description.strip(),
            )
            await ctx.reply({"project": row})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("project.create failed")
            await ctx.reply_error(INTERNAL_ERROR, f"project.create failed: {exc}")

    async def handle_project_update(params: Any, ctx: Context) -> None:
        try:
            proj_dao = await dao_factory()
            check_params(params, expected_keys={"project_id"})
            project_id = str(params["project_id"])
            if project_id == INBOX_ID:
                raise HandlerError(INVALID_PARAMS, "cannot update the inbox project")
            updates: dict[str, Any] = {}
            if "name" in params and params["name"] is not None:
                name = str(params["name"]).strip()
                if not name:
                    raise HandlerError(INVALID_PARAMS, "project name cannot be empty")
                updates["name"] = name
            if "description" in params and params["description"] is not None:
                updates["description"] = str(params["description"]).strip()
            if not updates:
                raise HandlerError(INVALID_PARAMS, "project.update: no fields to update")
            row = await proj_dao.update(project_id, **updates)
            if row is None:
                raise HandlerError(INVALID_PARAMS, f"unknown project_id: {project_id!r}")
            await ctx.reply({"ok": True, "project": row})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("project.update failed")
            await ctx.reply_error(INTERNAL_ERROR, f"project.update failed: {exc}")

    async def handle_project_delete(params: Any, ctx: Context) -> None:
        try:
            proj_dao = await dao_factory()
            check_params(params, expected_keys={"project_id"})
            project_id = str(params["project_id"])
            if project_id == INBOX_ID:
                raise HandlerError(INVALID_PARAMS, "cannot delete the inbox project")
            ok = await proj_dao.delete(project_id)
            if not ok:
                raise HandlerError(INVALID_PARAMS, f"unknown project_id: {project_id!r}")
            await ctx.reply({"ok": True, "project_id": project_id})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("project.delete failed")
            await ctx.reply_error(INTERNAL_ERROR, f"project.delete failed: {exc}")

    async def handle_project_archive(params: Any, ctx: Context) -> None:
        try:
            proj_dao = await dao_factory()
            check_params(params, expected_keys={"project_id"})
            project_id = str(params["project_id"])
            row = await proj_dao.set_archived(project_id, True)
            if row is None:
                raise HandlerError(INVALID_PARAMS, f"unknown project_id: {project_id!r}")
            await ctx.reply({"ok": True, "project": row})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("project.archive failed")
            await ctx.reply_error(INTERNAL_ERROR, f"project.archive failed: {exc}")

    async def handle_project_unarchive(params: Any, ctx: Context) -> None:
        try:
            proj_dao = await dao_factory()
            check_params(params, expected_keys={"project_id"})
            project_id = str(params["project_id"])
            row = await proj_dao.set_archived(project_id, False)
            if row is None:
                raise HandlerError(INVALID_PARAMS, f"unknown project_id: {project_id!r}")
            await ctx.reply({"ok": True, "project": row})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("project.unarchive failed")
            await ctx.reply_error(INTERNAL_ERROR, f"project.unarchive failed: {exc}")

    server.register("project.list", handle_project_list)
    server.register("project.create", handle_project_create)
    server.register("project.update", handle_project_update)
    server.register("project.delete", handle_project_delete)
    server.register("project.archive", handle_project_archive)
    server.register("project.unarchive", handle_project_unarchive)


def _make_dao_factory(dao: Any | None) -> Any:
    """Return an async factory yielding a :class:`ProjectsDAO`."""
    if dao is not None:

        async def _factory() -> Any:
            return dao

        return _factory

    async def _factory() -> Any:
        from ..app import get_projects_dao, init_runtime

        existing = get_projects_dao()
        if existing is not None:
            return existing
        try:
            await init_runtime()
        except Exception:
            pass
        return get_projects_dao()

    return _factory


__all__ = ["register_project_handlers"]
