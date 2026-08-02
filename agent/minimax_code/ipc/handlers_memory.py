"""JSON-RPC handlers for the ``memory.*`` namespace (v0.11.0 Milestone 3)."""

from __future__ import annotations

import logging
from typing import Any

from ..memory import MemoriesDAO, MemoryExtractor
from .handler_utils import HandlerError, check_params
from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)

_VALID_CATEGORIES: frozenset[str] = frozenset(
    {"preference", "decision", "lesson", "fact"}
)


def _dao() -> MemoriesDAO:
    """Resolve the process-wide database and build a :class:`MemoriesDAO`."""
    from ..app import get_db

    db = get_db()
    if db is None:
        raise HandlerError(INTERNAL_ERROR, "database not available")
    return MemoriesDAO(db)


def _clamp_limit(value: Any, default: int = 20, maximum: int = 100) -> int:
    """Coerce a caller-supplied limit into a sensible range."""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    if limit <= 0:
        return default
    return min(limit, maximum)


def _clamp_offset(value: Any) -> int:
    """Coerce a caller-supplied offset to a non-negative integer."""
    try:
        offset = int(value)
    except (TypeError, ValueError):
        return 0
    return max(offset, 0)


def register_memory_handlers(server: Any) -> None:
    """Register the ``memory.*`` handlers on ``server``."""

    async def handle_memory_list(params: Any, ctx: Context) -> None:
        """``memory.list`` — list memories with optional filters."""
        try:
            p = params if isinstance(params, dict) else {}
            project_id = p.get("project_id")
            session_id = p.get("session_id")
            category = p.get("category")
            if category is not None and category not in _VALID_CATEGORIES:
                raise HandlerError(
                    INVALID_PARAMS,
                    f"category must be one of {sorted(_VALID_CATEGORIES)}",
                )
            limit = _clamp_limit(p.get("limit", 20))
            offset = _clamp_offset(p.get("offset", 0))

            dao = _dao()
            memories = await dao.list(
                project_id=project_id,
                session_id=session_id,
                category=category,
                limit=limit,
                offset=offset,
            )
            total = await dao.count(
                project_id=project_id,
                session_id=session_id,
                category=category,
            )
            await ctx.reply({"memories": memories, "total": total})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("memory.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "memory.list failed")

    async def handle_memory_add(params: Any, ctx: Context) -> None:
        """``memory.add`` — manually add a memory."""
        try:
            p = params if isinstance(params, dict) else {}
            check_params(p, expected_keys={"content"})
            content = p["content"]
            if not isinstance(content, str) or not content.strip():
                raise HandlerError(INVALID_PARAMS, "content must be a non-empty string")

            category = p.get("category", "fact")
            if category not in _VALID_CATEGORIES:
                raise HandlerError(
                    INVALID_PARAMS,
                    f"category must be one of {sorted(_VALID_CATEGORIES)}",
                )

            confidence_raw = p.get("confidence", 1.0)
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError) as exc:
                raise HandlerError(
                    INVALID_PARAMS,
                    f"confidence must be a number: {exc}",
                ) from exc
            if not 0.0 <= confidence <= 1.0:
                raise HandlerError(
                    INVALID_PARAMS,
                    "confidence must be between 0.0 and 1.0",
                )

            memory = await _dao().create(
                content=content,
                project_id=p.get("project_id"),
                session_id=p.get("session_id"),
                category=category,
                confidence=confidence,
                source=p.get("source"),
            )
            await ctx.reply({"memory": memory})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("memory.add failed")
            await ctx.reply_error(INTERNAL_ERROR, "memory.add failed")

    async def handle_memory_delete(params: Any, ctx: Context) -> None:
        """``memory.delete`` — delete a memory by id."""
        try:
            p = params if isinstance(params, dict) else {}
            check_params(p, expected_keys={"id"})
            memory_id = p["id"]
            if not isinstance(memory_id, str) or not memory_id:
                raise HandlerError(INVALID_PARAMS, "id must be a non-empty string")
            ok = await _dao().delete(memory_id)
            await ctx.reply({"ok": ok, "id": memory_id})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("memory.delete failed")
            await ctx.reply_error(INTERNAL_ERROR, "memory.delete failed")

    async def handle_memory_search(params: Any, ctx: Context) -> None:
        """``memory.search`` — keyword search over memory content."""
        try:
            p = params if isinstance(params, dict) else {}
            check_params(p, expected_keys={"query"})
            query = p["query"]
            if not isinstance(query, str) or not query.strip():
                raise HandlerError(INVALID_PARAMS, "query must be a non-empty string")

            project_id = p.get("project_id")
            session_id = p.get("session_id")
            category = p.get("category")
            if category is not None and category not in _VALID_CATEGORIES:
                raise HandlerError(
                    INVALID_PARAMS,
                    f"category must be one of {sorted(_VALID_CATEGORIES)}",
                )
            limit = _clamp_limit(p.get("limit", 20))

            dao = _dao()
            memories = await dao.search(
                query,
                project_id=project_id,
                session_id=session_id,
                category=category,
                limit=limit,
            )
            total = await dao.count(
                project_id=project_id,
                session_id=session_id,
                category=category,
                query=query,
            )
            await ctx.reply({"memories": memories, "total": total})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("memory.search failed")
            await ctx.reply_error(INTERNAL_ERROR, "memory.search failed")

    async def handle_memory_extract(params: Any, ctx: Context) -> None:
        """``memory.extract`` — extract facts from raw text."""
        try:
            p = params if isinstance(params, dict) else {}
            check_params(p, expected_keys={"text"})
            text = p["text"]
            if not isinstance(text, str) or not text.strip():
                raise HandlerError(INVALID_PARAMS, "text must be a non-empty string")
            facts = MemoryExtractor().extract_facts(text)
            await ctx.reply({"facts": facts})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("memory.extract failed")
            await ctx.reply_error(INTERNAL_ERROR, "memory.extract failed")

    server.register("memory.list", handle_memory_list)
    server.register("memory.add", handle_memory_add)
    server.register("memory.delete", handle_memory_delete)
    server.register("memory.search", handle_memory_search)
    server.register("memory.extract", handle_memory_extract)


__all__ = ["register_memory_handlers"]
