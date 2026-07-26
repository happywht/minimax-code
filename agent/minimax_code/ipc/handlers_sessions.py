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

``session.create``   -> ``{ session_id, title, created_at, reused }``
``session.list``     -> ``{ sessions: [...], total: N }``
``session.get``      -> ``{ session: {...}, recent_messages: [...] }``
``session.archive``  -> ``{ ok: true, session: {...} }``
``session.unarchive``-> ``{ ok: true, session: {...} }``
``session.delete``   -> ``{ ok: true }``
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from .handler_utils import HandlerError, check_params
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
                raise HandlerError(
                    INVALID_PARAMS, "title must be a string if provided"
                )
            title = title.strip()

            # Reuse the currently selected local session when it is still
            # empty. This keeps repeated "New task" clicks idempotent and
            # prevents abandoned blank rows from accumulating in history.
            reuse_id = _optional_string(p, "reuse_empty_session_id")
            if reuse_id:
                existing = await sess_dao.get(reuse_id)
                if (
                    existing is not None
                    and not existing.get("archived")
                    and existing.get("workspace_mode", "local") == "local"
                    and not await sess_dao.get_messages(reuse_id, limit=1)
                ):
                    if existing.get("title") != title:
                        existing = await sess_dao.update(reuse_id, title=title)
                    await ctx.reply(
                        {
                            "session_id": existing["id"],
                            "title": existing["title"],
                            "created_at": existing["created_at"],
                            "session": existing,
                            "reused": True,
                        }
                    )
                    return

            # Generate a new id; we use the same ``ses_`` prefix
            # convention the chat send_message flow uses so the
            # id format stays consistent across the codebase.
            new_id = f"ses_{uuid.uuid4().hex[:8]}"
            workspace_mode = _optional_string(p, "workspace_mode") or "local"
            if workspace_mode not in {"local", "worktree"}:
                raise HandlerError(
                    INVALID_PARAMS,
                    "workspace_mode must be 'local' or 'worktree'",
                )
            row = await sess_dao.create(
                id=new_id,
                title=title,
                workspace_mode=workspace_mode,
                workspace_path=_optional_string(p, "workspace_path"),
                worktree_branch=_optional_string(p, "worktree_branch"),
                base_branch=_optional_string(p, "base_branch"),
            )
            # ``row`` is the persisted dict; echo back the
            # subset the frontend's CreateSessionResult wants
            # plus a human-readable created_at for the UI.
            await ctx.reply(
                {
                    "session_id": row["id"],
                    "title": row["title"],
                    "created_at": row["created_at"],
                    "session": row,
                    "reused": False,
                }
            )
        except HandlerError as exc:
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
            check_params(params, expected_keys=set())
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
                raise HandlerError(
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
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("session.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "session.list failed")

    # ------------------------------------------------------------------- get

    async def handle_session_get(params: Any, ctx: Context) -> None:
        try:
            sess_dao = await dao_factory()
            check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            session = await sess_dao.get(session_id)
            if session is None:
                raise HandlerError(
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
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("session.get failed")
            await ctx.reply_error(INTERNAL_ERROR, "session.get failed")

    # -------------------------------------------------------------- archive

    async def handle_session_archive(params: Any, ctx: Context) -> None:
        try:
            sess_dao = await dao_factory()
            check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            session = await sess_dao.set_archived(session_id, True)
            if session is None:
                raise HandlerError(
                    INVALID_PARAMS, f"unknown session_id: {session_id!r}"
                )
            await ctx.reply({"ok": True, "session": session})
        except HandlerError as exc:
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
            check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            session = await sess_dao.set_archived(session_id, False)
            if session is None:
                raise HandlerError(
                    INVALID_PARAMS, f"unknown session_id: {session_id!r}"
                )
            await ctx.reply({"ok": True, "session": session})
        except HandlerError as exc:
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
            check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            ok = await sess_dao.delete(session_id)
            if not ok:
                raise HandlerError(
                    INVALID_PARAMS, f"unknown session_id: {session_id!r}"
                )
            await ctx.reply({"ok": True, "session_id": session_id})
        except HandlerError as exc:
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
            check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            updates: dict[str, Any] = {}
            if "title" in params and params["title"] is not None:
                updates["title"] = str(params["title"])
            if not updates:
                raise HandlerError(
                    INVALID_PARAMS, "session.update: no fields to update"
                )
            row = await sess_dao.update(session_id, **updates)
            if row is None:
                raise HandlerError(
                    INVALID_PARAMS, f"unknown session_id: {session_id!r}"
                )
            await ctx.reply({"ok": True, "session": row})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("session.update failed")
            await ctx.reply_error(
                INTERNAL_ERROR, f"session.update failed: {exc}"
            )

    # ------------------------------------------------------------------ stats

    async def handle_session_stats(params: Any, ctx: Context) -> None:
        """``session.stats`` → aggregate counts for the UI footer."""
        try:
            sess_dao = await dao_factory()
            if sess_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "session.stats: database not available")
                return
            stats = await sess_dao.stats()
            await ctx.reply(stats)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("session.stats failed")
            await ctx.reply_error(INTERNAL_ERROR, f"session.stats failed: {exc}")

    # ------------------------------------------------------------------ export

    async def handle_session_export(params: Any, ctx: Context) -> None:
        """``session.export`` → session messages as Markdown.

        Returns ``{ markdown: string }`` suitable for a file download.
        """
        try:
            check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            sess_dao = await dao_factory()
            if sess_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "session.export: database not available")
                return
            session = await sess_dao.get(session_id)
            if session is None:
                raise HandlerError(
                    INVALID_PARAMS, f"unknown session_id: {session_id!r}"
                )
            rows = await sess_dao.get_messages(session_id, limit=1000)
            # Rows are newest-first; render chronologically.
            lines: list[str] = []
            lines.append(f"# {session.get('title') or 'Untitled session'}\n")
            lines.append(f"<!-- session_id: {session_id} -->\n")
            for r in reversed(rows):
                role = r.get("role", "assistant")
                content = r.get("content") or ""
                created = r.get("created_at") or ""
                lines.append(f"<!-- created_at: {created} -->\n")
                if role == "user":
                    lines.append("## User\n")
                    lines.append(f"{content}\n")
                elif role == "tool":
                    tool_name = r.get("tool_name") or "tool"
                    lines.append(f"### Tool: {tool_name}\n")
                    lines.append(f"{content}\n")
                else:
                    lines.append("## Assistant\n")
                    lines.append(f"{content}\n")
                lines.append("")
            await ctx.reply({"markdown": "\n".join(lines).strip()})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("session.export failed")
            await ctx.reply_error(INTERNAL_ERROR, f"session.export failed: {exc}")

    # -------------------------------------------------------------- message.list

    async def handle_message_list(params: Any, ctx: Context) -> None:
        """``message.list`` → ``{ messages: [...] }``.

        Returns messages for a given session, ordered by ``created_at``.
        The frontend calls this when switching sessions in the sidebar
        to repopulate the chat panel with the conversation history.
        """
        try:
            check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            limit = _clamp_int(params.get("limit"), default=100, lo=1, hi=500)
            before = params.get("before")
            if before is not None and not isinstance(before, str):
                raise HandlerError(
                    INVALID_PARAMS, "before must be an ISO timestamp string"
                )
            from ..app import init_runtime
            from ..storage.dao.messages import MessagesDAO

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
            tool_index = _tool_call_index(rows)
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
                    if r.get("role") == "tool":
                        tool_info = tool_index.get(str(r["tool_call_id"]))
                        if tool_info:
                            msg["tool_name"] = tool_info.get("name")
                            msg["tool_args"] = tool_info.get("args")
                if r.get("metadata"):
                    msg["metadata"] = r["metadata"]
                if r.get("tool_calls"):
                    msg["tool_calls"] = r["tool_calls"]
                messages.append(msg)
            await ctx.reply({"messages": messages})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("message.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "message.list failed")

    server.register("session.create", handle_session_create)
    server.register("session.list", handle_session_list)
    server.register("session.get", handle_session_get)
    server.register("session.archive", handle_session_archive)
    server.register("session.unarchive", handle_session_unarchive)
    server.register("session.delete", handle_session_delete)
    server.register("session.update", handle_session_update)
    server.register("session.stats", handle_session_stats)
    server.register("session.export", handle_session_export)
    server.register("message.list", handle_message_list)


def _tool_call_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map persisted tool_call ids to their display name and args.

    Tool result rows only store ``tool_call_id`` because that is what
    model APIs need for replay. The UI, however, needs the human-readable
    tool name and arguments after a session reload. We recover those from
    the preceding assistant ``tool_calls`` payload.
    """
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        calls = row.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id") or "").strip()
            if not call_id:
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = str(call.get("name") or fn.get("name") or "tool")
            raw_args = call.get("args")
            if raw_args is None:
                raw_args = call.get("arguments")
            if raw_args is None:
                raw_args = fn.get("arguments")
            index[call_id] = {"name": name, "args": _decode_tool_args(raw_args)}
    return index


def _decode_tool_args(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"arguments": raw}
        return parsed if isinstance(parsed, dict) else {"arguments": parsed}
    return {"arguments": raw}

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


def _optional_string(params: dict[str, Any], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HandlerError(INVALID_PARAMS, f"{key} must be a string")
    return value.strip() or None

__all__ = ["register_session_handlers"]
