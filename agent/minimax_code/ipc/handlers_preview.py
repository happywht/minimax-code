"""JSON-RPC handlers for the ``preview.*`` namespace (v1.7.1).

The live-preview server (``preview/server.py``) historically anchored to a
single process-level root resolved once at boot — the last v1.3.0 known
limitation. ``preview.set_root`` closes that gap: the frontend calls it on
every project switch so the preview iframe, health probe and SSE hot-reload
stream all follow the selected project's ``root_path``.

Resolution semantics (mirroring the v1.3.0 project-scope conventions):

* ``project_id`` omitted / empty → the process-default root
  (``MINIMAX_CODE_WORKSPACE`` or CWD). This is also what a project
  *without* a bound ``root_path`` degrades to.
* Unknown ``project_id`` → ``-32602`` fast fail (nothing is switched).
* ``root_path`` pointing at a missing directory → ``-32602`` fast fail —
  the caller should fix the project binding rather than silently preview
  a ghost root.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .handler_utils import HandlerError
from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)


def register_preview_handlers(
    server: Any, *, state: Any = None, dao: Any = None
) -> None:
    """Register the ``preview.*`` handlers on ``server``.

    ``state`` / ``dao`` are test seams: inject a :class:`PreviewState` and
    a :class:`ProjectsDAO` to bypass the process singletons.
    """
    from ..preview.server import ensure_preview_state

    async def handle_preview_set_root(params: Any, ctx: Context) -> None:
        try:
            p = params or {}
            project_id = str(p.get("project_id") or "").strip() or None

            if project_id is None:
                from ..workspace_ctx import env_or_cwd_root

                new_root = env_or_cwd_root()
            else:
                new_root = await _resolve_project_root(project_id, dao)

            preview = state if state is not None else ensure_preview_state()
            await preview.set_root(new_root)
            await ctx.reply(
                {
                    "ok": True,
                    "workspace": str(preview.workspace),
                    "project_id": project_id,
                }
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("preview.set_root failed")
            await ctx.reply_error(INTERNAL_ERROR, f"preview.set_root failed: {exc}")

    server.register("preview.set_root", handle_preview_set_root)


async def _resolve_project_root(project_id: str, dao: Any = None) -> Path:
    """Resolve a project's ``root_path`` for preview re-rooting.

    Raises :class:`HandlerError` (``-32602``) for unknown projects and for
    roots that no longer exist on disk. Returns the process-default root
    for projects without a bound ``root_path`` — same degradation as
    ``workspace_ctx.resolve_root_for_project_id``.
    """
    proj_dao = dao
    if proj_dao is None:
        from ..app import get_projects_dao, init_runtime

        proj_dao = get_projects_dao()
        if proj_dao is None:
            try:
                await init_runtime()
            except Exception:
                pass
            proj_dao = get_projects_dao()
    if proj_dao is None:  # pragma: no cover — storage unavailable
        raise HandlerError(
            INTERNAL_ERROR, "project storage unavailable; cannot resolve project root"
        )

    project = await proj_dao.get(project_id)
    if not project:
        raise HandlerError(INVALID_PARAMS, f"unknown project_id: {project_id!r}")

    raw = str(project.get("root_path") or "").strip()
    if not raw:
        from ..workspace_ctx import env_or_cwd_root

        return env_or_cwd_root()

    path = Path(raw).expanduser()
    if not path.is_dir():
        raise HandlerError(
            INVALID_PARAMS,
            f"project root_path is not an existing directory: {raw}",
        )
    return path.resolve()


__all__ = ["register_preview_handlers"]
