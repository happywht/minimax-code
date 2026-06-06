"""JSON-RPC handlers for the ``session.*`` namespace.

This namespace exposes the durable session list / archive / search
surface the frontend's sidebar uses. Read-side methods hit the
``sessions`` table directly; write-side methods round-trip a row
back so the UI can confirm the new state in the same envelope.

Wire-up
-------

:func:`register_session_handlers` is called from
:func:`minimax_code.app.register_app_handlers` after the
:class:`~minimax_code.storage.db.AsyncDatabase` has been migrated.
The DAO is built lazily on the first ``session.*`` request — the
factory pattern in :func:`_make_dao_factory` mirrors the one used
by :mod:`handlers_scheduled` and :mod:`handlers_tasks`.

Schema
------

``session.create``   -> ``{ session_id, title, created_at }``
``session.list``     -> ``{ sessions: [...], total: N }``
``session.get``      -> ``{ session: {...}, recent_messages: [...] }``
``session.archive``  -> ``{ ok: true, session: {...} }``
``session.unarchive``-> ``{ ok: true, session: {...} }``
``session.delete``   -> ``{ ok: true }``
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)


# Default title used when the frontend calls ``session.create``
# without supplying one (e.g. the "new task" button in the
# sidebar). Format matches what the UI shows before the user
# types a real prompt.
_DEFAULT_TITLE_FMT = "New chat — %Y-%m-%d %H:%M"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class _HandlerError(Exception):
    """Internal sentinel — handlers raise it with a JSON-RPC code."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_session_handlers(server: Any, *, dao: Any = None) -> None:
    """Register the ``session.*`` handlers on ``server``.

    Parameters
    ----------
    server:
        The :class:`~minimax_code.ipc.server.IPCServer` instance.
    dao:
        Optional pre-built :class:`SessionsDAO`. When ``None``,
        handlers resolve the singleton via
        :func:`minimax_code.app.get_sessions_dao` on each call.
    """
    dao_factory = _make_dao_factory(dao)

    # ----------------------------------------------------------------- create

    async def handle_session_create(params: Any, ctx: Context) -> None:
        try:
            sess_dao = await dao_factory()
            p = params or {}
            # Title is optional — the "new task" button in the
            # sidebar calls us with no title at all and we want a
            # sensible default like "New chat — 2026-06-02 15:30"
            # so the user immediately sees *something* in the
            # history pane. An explicit empty string is treated
            # the same as omitted — there's no value in saving
            # a session with a blank title that the UI would
            # then have to special-case.
            title = p.get("title")
            if title is None or (isinstance(title, str) and not title.strip()):
                title = datetime.now().strftime(_DEFAULT_TITLE_FMT)
            elif not isinstance(title, str):
                raise _HandlerError(
                    INVALID_PARAMS, "title must be a string if provided"
                )
            # Generate a new id; we use the same ``ses_`` prefix
            # convention the chat send_message flow uses so the
            # id format stays consistent across the codebase.
            new_id = f"ses_{uuid.uuid4().hex[:8]}"
            row = await sess_dao.create(id=new_id, title=title)
            # ``row`` is the persisted dict; echo back the
            # subset the frontend's CreateSessionResult wants
            # plus a human-readable created_at for the UI.
            await ctx.reply(
                {
                    "session_id": row["id"],
                    "title": row["title"],
                    "created_at": row["created_at"],
                }
            )
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("session.create failed")
            await ctx.reply_error(
                INTERNAL_ERROR, f"session.create failed: {exc}"
            )

    # ------------------------------------------------------------------ list

    async def handle_session_list(params: Any, ctx: Context) -> None:
        try:
            sess_dao = await dao_factory()
            _check_params(params, expected_keys=set())
            p = params or {}
            # The frontend passes ``archived`` as a tri-state: missing
            # (no filter), ``true`` (only archived), ``false`` (active
            # sessions). Anything truthy is "only archived"; ``False``
            # explicitly is "only active". A missing key means
            # "all sessions" — used by the search box.
            archived_raw = p.get("archived")
            if archived_raw is None:
                archived: bool | None = None
            else:
                archived = bool(archived_raw)
            search = p.get("search")
            if search is not None and not isinstance(search, str):
                raise _HandlerError(
                    INVALID_PARAMS, "search must be a string if provided"
                )
            # Clamp pagination so a typo can't drag a million rows back.
            limit = _clamp_int(p.get("limit"), default=50, lo=1, hi=500)
            offset = max(0, int(p.get("offset") or 0))
            sessions = await sess_dao.list(
                archived=archived,
                search=search if search else None,
                limit=limit,
                offset=offset,
            )
            # ``total`` must reflect the *same* filter combo as the
            # page above — otherwise a paginated search returns
            # e.g. ``{"sessions": [..2 rows..], "total": 17}`` and
            # the UI's "page X of Y" indicator is wrong. This is
            # the bug the previous attempt shipped: count() did
            # not accept a search kwarg so the search filter was
            # silently dropped from the total.
            total = await sess_dao.count(
                archived=archived,
                search=search if search else None,
            )
            await ctx.reply({"sessions": sessions, "total": total})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("session.list failed")
            await ctx.reply_error(INTERNAL_ERROR, f"session.list failed: {exc}")

    # ------------------------------------------------------------------- get

    async def handle_session_get(params: Any, ctx: Context) -> None:
        try:
            sess_dao = await dao_factory()
            _check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            session = await sess_dao.get(session_id)
            if session is None:
                raise _HandlerError(
                    INVALID_PARAMS, f"unknown session_id: {session_id!r}"
                )
            # Default to 100 recent messages; the chat UI scrolls
            # the older history on demand via a dedicated query.
            limit = _clamp_int(
                params.get("messages_limit"), default=100, lo=1, hi=500
            )
            recent_messages = await sess_dao.get_messages(
                session_id, limit=limit
            )
            await ctx.reply(
                {"session": session, "recent_messages": recent_messages}
            )
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("session.get failed")
            await ctx.reply_error(INTERNAL_ERROR, f"session.get failed: {exc}")

    # -------------------------------------------------------------- archive

    async def handle_session_archive(params: Any, ctx: Context) -> None:
        try:
            sess_dao = await dao_factory()
            _check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            session = await sess_dao.set_archived(session_id, True)
            if session is None:
                raise _HandlerError(
                    INVALID_PARAMS, f"unknown session_id: {session_id!r}"
                )
            await ctx.reply({"ok": True, "session": session})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("session.archive failed")
            await ctx.reply_error(
                INTERNAL_ERROR, f"session.archive failed: {exc}"
            )

    # ------------------------------------------------------------ unarchive

    async def handle_session_unarchive(params: Any, ctx: Context) -> None:
        try:
            sess_dao = await dao_factory()
            _check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            session = await sess_dao.set_archived(session_id, False)
            if session is None:
                raise _HandlerError(
                    INVALID_PARAMS, f"unknown session_id: {session_id!r}"
                )
            await ctx.reply({"ok": True, "session": session})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("session.unarchive failed")
            await ctx.reply_error(
                INTERNAL_ERROR, f"session.unarchive failed: {exc}"
            )

    # --------------------------------------------------------------- delete

    async def handle_session_delete(params: Any, ctx: Context) -> None:
        try:
            sess_dao = await dao_factory()
            _check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            ok = await sess_dao.delete(session_id)
            if not ok:
                raise _HandlerError(
                    INVALID_PARAMS, f"unknown session_id: {session_id!r}"
                )
            await ctx.reply({"ok": True, "session_id": session_id})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("session.delete failed")
            await ctx.reply_error(
                INTERNAL_ERROR, f"session.delete failed: {exc}"
            )

    # ----------------------------------------------------------------- update

    async def handle_session_update(params: Any, ctx: Context) -> None:
        try:
            sess_dao = await dao_factory()
            _check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            updates: dict[str, Any] = {}
            if "title" in params and params["title"] is not None:
                updates["title"] = str(params["title"])
            if not updates:
                raise _HandlerError(
                    INVALID_PARAMS, "session.update: no fields to update"
                )
            row = await sess_dao.update(session_id, **updates)
            if row is None:
                raise _HandlerError(
                    INVALID_PARAMS, f"unknown session_id: {session_id!r}"
                )
            await ctx.reply({"ok": True, "session": row})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("session.update failed")
            await ctx.reply_error(
                INTERNAL_ERROR, f"session.update failed: {exc}"
            )

    # -------------------------------------------------------------- message.list

    async def handle_message_list(params: Any, ctx: Context) -> None:
        """``message.list`` → ``{ messages: [...] }``.

        Returns messages for a given session, ordered by ``created_at``.
        The frontend calls this when switching sessions in the sidebar
        to repopulate the chat panel with the conversation history.
        """
        try:
            _check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            limit = _clamp_int(params.get("limit"), default=100, lo=1, hi=500)
            before = params.get("before")
            if before is not None and not isinstance(before, str):
                raise _HandlerError(
                    INVALID_PARAMS, "before must be an ISO timestamp string"
                )
            from ..storage.dao.messages import MessagesDAO
            from ..app import get_db, init_runtime

            await init_runtime()
            from ..app import get_db as _get_db

            db = _get_db()
            if db is None:
                await ctx.reply({"messages": []})
                return
            msg_dao = MessagesDAO(db)
            rows = await msg_dao.list_for_session(
                session_id, limit=limit, before=before, order_by="created_at ASC"
            )
            # Convert backend rows to the frontend Message shape.
            # Backend: {id, role, content, tool_calls, tool_call_id, metadata, created_at}
            # Frontend: {id, role, text, streaming, created_at, metadata, ...}
            messages = []
            for r in rows:
                msg: dict[str, Any] = {
                    "id": r.get("id", ""),
                    "role": r.get("role", "user"),
                    "text": r.get("content") or "",
                    "streaming": False,
                    "created_at": r.get("created_at", ""),
                }
                if r.get("tool_call_id"):
                    msg["tool_call_id"] = r["tool_call_id"]
                if r.get("metadata"):
                    msg["metadata"] = r["metadata"]
                if r.get("tool_calls"):
                    msg["tool_calls"] = r["tool_calls"]
                messages.append(msg)
            await ctx.reply({"messages": messages})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("message.list failed")
            await ctx.reply_error(INTERNAL_ERROR, f"message.list failed: {exc}")

    server.register("session.create", handle_session_create)
    server.register("session.list", handle_session_list)
    server.register("session.get", handle_session_get)
    server.register("session.archive", handle_session_archive)
    server.register("session.unarchive", handle_session_unarchive)
    server.register("session.delete", handle_session_delete)
    server.register("session.update", handle_session_update)
    server.register("message.list", handle_message_list)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _make_dao_factory(dao: Any | None) -> Any:
    """Return an async factory yielding a :class:`SessionsDAO`.

    Mirrors :func:`handlers_scheduled._make_scheduler_factory`:
    the test path uses the explicit ``dao=`` injection, the
    runtime path lazily resolves the singleton on first call.
    """
    if dao is not None:

        async def _factory() -> Any:
            return dao

        return _factory

    async def _factory() -> Any:
        # Lazy import: avoid a circular dep at module load time.
        from ..app import get_sessions_dao, init_runtime

        existing = get_sessions_dao()
        if existing is not None:
            return existing
        # ``init_runtime`` opens the DB and populates every
        # storage-backed singleton (tracker, sessions_dao, ...).
        # We swallow any boot error and return ``None``; the
        # handler will surface a clean ``INTERNAL_ERROR``.
        try:
            await init_runtime()
        except Exception:
            pass
        return get_sessions_dao()

    return _factory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_params(params: Any, *, expected_keys: set[str]) -> None:
    """Validate the JSON-RPC params shape; raise :class:`_HandlerError`.

    Mirrors the helper in :mod:`handlers_tasks` /
    :mod:`handlers_scheduled` so all three handler modules
    present the same JSON-RPC surface to the frontend.
    """
    if not expected_keys:
        return
    if params is None or not isinstance(params, dict):
        raise _HandlerError(
            INVALID_PARAMS,
            "params must be a JSON object with the required keys",
        )
    missing = expected_keys - set(params.keys())
    if missing:
        raise _HandlerError(
            INVALID_PARAMS,
            f"missing required param(s): {sorted(missing)}",
        )
    for key in expected_keys:
        if params[key] is None or (
            isinstance(params[key], str) and not params[key].strip()
        ):
            raise _HandlerError(
                INVALID_PARAMS, f"param {key!r} must be a non-empty value"
            )


def _clamp_int(value: Any, *, default: int, lo: int, hi: int) -> int:
    """Coerce a possibly-bad int param into ``[lo, hi]``.

    Used for ``limit`` / ``offset`` / ``messages_limit`` so the
    frontend can omit them (or pass a typo) without crashing the
    IPC layer. ``value=None`` falls back to ``default``; anything
    that can't be parsed as ``int`` also falls back.
    """
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi))


__all__ = ["register_session_handlers"]
