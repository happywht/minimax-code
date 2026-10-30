"""Codebase RAG IPC handlers (v0.11.0 Milestone 2, per-root in v1.3.0)."""

from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath
from typing import Any

from ..app import ensure_codebase_indexer, get_codebase_indexer
from ..codebase import CodebaseIndexer, CodebaseRetriever
from ..workspace_ctx import resolve_root_for_project_id, resolve_root_for_session
from .handler_utils import HandlerError
from .protocol import INTERNAL_ERROR, INVALID_PARAMS, PERMISSION_DENIED
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
    session_id = p.get("session_id")
    if session_id is not None:
        if not isinstance(session_id, str) or not session_id:
            raise HandlerError(INVALID_PARAMS, "Invalid session_id")
        from ..app import get_sessions_dao
        from .host_session_gate import require_chat_session

        await require_chat_session(session_id)
        dao = get_sessions_dao()
        session = await dao.get(session_id) if dao is not None else None
        if not session or session.get("archived") or session.get("project_id") != project_id:
            raise HandlerError(INVALID_PARAMS, "Session or project binding changed")
        root = await resolve_root_for_session(session_id)
        indexer = ensure_codebase_indexer(root)
    elif isinstance(project_id, str) and project_id:
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
        if p.get("session_id") is not None and await _indexer_for(p) is not indexer:
            raise HandlerError(INVALID_PARAMS, "Session workspace changed during search")
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
        if p.get("session_id") is not None and await _indexer_for(p) is not indexer:
            raise HandlerError(INVALID_PARAMS, "Session workspace changed during summary")
        await ctx.reply(result)
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except Exception:
        logger.exception("codebase.summarize failed")
        await ctx.reply_error(INTERNAL_ERROR, "codebase.summarize failed")


async def handle_codebase_read_current(params: Any, ctx: Context) -> None:
    """Read a bounded current file range for an explicitly selected session."""
    try:
        from ..agent.tools.file_ops import safe_resolve
        from ..app import get_sessions_dao, ensure_db
        from ..storage.dao.text_artifacts import ArtifactDAO
        from ..text_artifacts import _identity, _regular, _safe_open
        from .host_session_gate import require_chat_session
        from .handlers_permissions import _ensure_permission_store

        p = params if isinstance(params, dict) else {}
        if set(p) != {"session_id", "project_id", "expected_service_id", "path", "start_line", "end_line"}:
            raise HandlerError(INVALID_PARAMS, "Invalid current-source request")
        session_id, project_id, service_id = p.get("session_id"), p.get("project_id"), p.get("expected_service_id")
        name, start, end = p.get("path"), p.get("start_line"), p.get("end_line")
        if not all(isinstance(v, str) and v for v in (session_id, project_id, service_id, name)):
            raise HandlerError(INVALID_PARAMS, "Session, project, service and path are required")
        relative = PurePosixPath(name)
        if (len(name) > 1024 or relative.is_absolute() or relative.as_posix() != name
                or ".." in relative.parts or any(c in name for c in "\\:\x00")
                or any(ord(c) < 32 for c in name)):
            raise HandlerError(INVALID_PARAMS, "Invalid project-relative path")
        if type(start) is not int or type(end) is not int or start < 1 or end < start or end - start >= 100:
            raise HandlerError(INVALID_PARAMS, "Choose an inclusive range of at most 100 lines")
        if os.name != "nt" and not all(hasattr(os, a) for a in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")):
            raise HandlerError(INVALID_PARAMS, "Safe file reading is unavailable on this platform")

        db = await ensure_db()
        async def binding():
            await require_chat_session(session_id)
            dao = get_sessions_dao()
            session = await dao.get(session_id) if dao is not None else None
            if (not session or session.get("archived") or session.get("project_id") != project_id
                    or await ArtifactDAO(db).service_id() != service_id):
                raise HandlerError(INVALID_PARAMS, "Service, session or project binding changed")
            root = await resolve_root_for_session(session_id)
            info = root.stat()
            return root, (info.st_dev, info.st_ino)

        async def authorize():
            store = await _ensure_permission_store(ctx.server)
            if store is None:
                raise HandlerError(PERMISSION_DENIED, "Read permission policy is unavailable")
            rule = store.lookup("read_file") or {}
            action = str(rule.get("action") or "").lower()
            if action != "allow" or rule.get("scope") not in (None, "", "global"):
                raise HandlerError(PERMISSION_DENIED, "Current file read requires an allow rule; this action has no approval route")

        await authorize()
        root, identity = await binding()
        target = safe_resolve(name, workspace=root)
        # Open the unresolved path too, rejecting links in every component.
        if target != root / Path(name):
            raise ValueError("File path contains a link or non-canonical component")
        with _safe_open(target) as fd:
            before = os.fstat(fd)
            _regular(before)
            if before.st_size > 65536:
                raise ValueError("File exceeds 64 KiB")
            data = b""
            while len(data) <= 65536:
                chunk = os.read(fd, 65537 - len(data))
                if not chunk:
                    break
                data += chunk
            if (len(data) > 65536 or _identity(before) != _identity(os.fstat(fd))
                    or len(data) != before.st_size or _identity(before)[:4] != _identity(target.lstat())[:4]):
                raise ValueError("File changed while reading")
        decoded = data.decode("utf-8")
        if any(ord(c) < 32 and c not in "\n\r\t" for c in decoded):
            raise ValueError("Binary or control characters are not supported")
        lines = decoded.splitlines()
        if start > len(lines):
            raise ValueError("The indexed line range no longer exists in the current file")
        content = "\n".join(lines[start - 1:end])
        if len(content) > 6000:
            raise ValueError("Selected range exceeds 6000 characters")
        await authorize()
        if await binding() != (root, identity):
            raise HandlerError(INVALID_PARAMS, "Workspace changed during reading")
        await ctx.reply({"path": name, "start_line": start, "end_line": min(end, len(lines)), "content": content})
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except (OSError, ValueError, UnicodeError) as exc:
        await ctx.reply_error(INVALID_PARAMS, str(exc))
    except Exception:
        logger.exception("codebase.read_current failed")
        await ctx.reply_error(INTERNAL_ERROR, "codebase.read_current failed")


def register_codebase_handlers(server: Any) -> None:
    """Register ``codebase.*`` handlers on ``server``."""
    server.register("codebase.status", handle_codebase_status)
    server.register("codebase.build_index", handle_codebase_build_index)
    server.register("codebase.search", handle_codebase_search)
    server.register("codebase.summarize", handle_codebase_summarize)
    server.register("codebase.read_current", handle_codebase_read_current)
