"""Ephemeral per-run registry of pages actually read by the research tool."""
from __future__ import annotations

import uuid
from typing import Any

MAX_SOURCES = 30
MAX_EXCERPT = 1000


class ResearchSources:
    def __init__(self) -> None:
        self._pages: dict[str, dict[str, Any]] = {}

    def remember(self, page: dict[str, Any]) -> str | None:
        if len(self._pages) >= MAX_SOURCES:
            return None
        source_id = "page_" + uuid.uuid4().hex
        title = page["title"]
        excerpt = page["excerpt"]
        self._pages[source_id] = {
            "title": title[:200] if title else "（页面未提供标题）",
            "title_truncated": len(title) > 200,
            "url": page["url"], "accessed_at": page["accessed_at"],
            "excerpt": excerpt[:MAX_EXCERPT] or None,
            "excerpt_truncated": bool(page["truncated"] or len(excerpt) > MAX_EXCERPT),
        }
        return source_id

    def resolve(self, source_ids: list[str] | None, manual: list[dict[str, Any]] | None):
        if source_ids is None:
            source_ids = []
        if (not isinstance(source_ids, list) or len(source_ids) > MAX_SOURCES
                or any(not isinstance(value, str) for value in source_ids)
                or len(set(source_ids)) != len(source_ids)):
            raise ValueError("page_source_ids must contain at most 30 unique source IDs")
        if any(value not in self._pages for value in source_ids):
            raise ValueError("Unknown page source ID; read the page in this task first")
        combined = list(manual or []) + [self._pages[value].copy() for value in source_ids]
        if len(combined) > MAX_SOURCES:
            raise ValueError("A report can contain at most 30 sources")
        return combined
