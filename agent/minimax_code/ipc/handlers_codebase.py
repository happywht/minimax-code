"""Codebase RAG IPC handlers (v0.11.0 Milestone 2, per-root in v1.3.0)."""

from __future__ import annotations

import logging
from typing import Any

from ..app import ensure_codebase_indexer, get_codebase_indexer
from ..codebase import CodebaseIndexer, CodebaseRetriever
from ..workspace_ctx import resolve_root_for_project_id
from .handler_utils import HandlerError
from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)


async def _indexer_for(params: Any) -> CodebaseIndexer:
    """Pick the indexer for this request (v1.3.0 per-project routing).

    A ``project_id`` in the params resolves that project's workspace root
    and returns (creating if needed) its own indexer. Without one — or
    for a project with no bound root — this falls back to the default
    root's indexer, which is exactly the pre-1.3.0 behaviour.
    """
    p = params if isinstance(params, dict) else {}
    project_id = p.get("project_id")
    if isinstance(project_id, str) and project_id:
        root = await resolve_root_for_project_id(project_id)
        indexer = ensure_codebase_indexer(root)
    else:
        indexer = get_codebase_indexer()
    if indexer is None:
        raise HandlerError(INTERNAL_ERROR, "codebase indexer not available")
    return indexer


def _retriever(indexer: CodebaseIndexer) -> CodebaseRetriever:
    return CodebaseRetriever(
        indexer._store,
        indexer=indexer,
        embedder=indexer.embedder,
        root=indexer.root_key,
    )


async def handle_codebase_status(params: Any, ctx: Context) -> None:
    """``codebase.status`` — return current indexing progress."""
    try:
        indexer = await _indexer_for(params)
        stats = await indexer._store.get_stats(root=indexer.root_key)
        result = indexer.to_dict()
        result["stats"] = stats
        await ctx.reply(result)
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except Exception:
        logger.exception("codebase.status failed")
        await ctx.reply_error(INTERNAL_ERROR, "codebase.status failed")


async def handle_codebase_build_index(params: Any, ctx: Context) -> None:
    """``codebase.build_index`` — start (or report) a full index build."""
    try:
        p = params if isinstance(params, dict) else {}
        force = bool(p.get("force", False))
        indexer = await _indexer_for(p)
        result = await indexer.build_index(force=force)
        stats = await indexer._store.get_stats(root=indexer.root_key)
        result["stats"] = stats
        await ctx.reply(result)
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except Exception:
        logger.exception("codebase.build_index failed")
        await ctx.reply_error(INTERNAL_ERROR, "codebase.build_index failed")


async def handle_codebase_search(params: Any, ctx: Context) -> None:
    """``codebase.search`` — keyword search over indexed code."""
    try:
        p = params if isinstance(params, dict) else {}
        query = p.get("query")
        if not isinstance(query, str) or not query.strip():
            raise HandlerError(INVALID_PARAMS, "'query' must be a non-empty string")
        file_pattern = p.get("file_pattern")
        if file_pattern is not None and not isinstance(file_pattern, str):
            raise HandlerError(INVALID_PARAMS, "'file_pattern' must be a string")
        limit = int(p.get("limit", 20))
        offset = int(p.get("offset", 0))
        if limit <= 0 or limit > 100:
            limit = 100
        if offset < 0:
            offset = 0

        indexer = await _indexer_for(p)
        retriever = _retriever(indexer)
        result = await retriever.search(
            query=query,
            file_pattern=file_pattern,
            limit=limit,
            offset=offset,
        )
        await ctx.reply(result)
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except Exception:
        logger.exception("codebase.search failed")
        await ctx.reply_error(INTERNAL_ERROR, "codebase.search failed")


async def handle_codebase_summarize(params: Any, ctx: Context) -> None:
    """``codebase.summarize`` — return a structured summary for a path."""
    try:
        p = params if isinstance(params, dict) else {}
        path = p.get("path")
        if not isinstance(path, str) or not path:
            raise HandlerError(INVALID_PARAMS, "'path' must be a non-empty string")
        indexer = await _indexer_for(p)
        retriever = _retriever(indexer)
        result = await retriever.summarize(path)
        await ctx.reply(result)
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except Exception:
        logger.exception("codebase.summarize failed")
        await ctx.reply_error(INTERNAL_ERROR, "codebase.summarize failed")


def register_codebase_handlers(server: Any) -> None:
    """Register ``codebase.*`` handlers on ``server``."""
    server.register("codebase.status", handle_codebase_status)
    server.register("codebase.build_index", handle_codebase_build_index)
    server.register("codebase.search", handle_codebase_search)
    server.register("codebase.summarize", handle_codebase_summarize)
