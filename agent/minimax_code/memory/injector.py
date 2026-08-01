"""Inject relevant memories into a system-prompt context block."""

from __future__ import annotations

from .store import MemoriesDAO


class MemoryInjector:
    """Convenience wrapper around :func:`build_memory_context`."""

    def __init__(self, dao: MemoriesDAO | None = None) -> None:
        self._dao = dao

    async def build_context(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
        query: str | None = None,
    ) -> str:
        """Build a formatted memory context block."""
        return await build_memory_context(
            project_id=project_id,
            session_id=session_id,
            query=query,
            dao=self._dao,
        )


async def build_memory_context(
    *,
    project_id: str | None = None,
    session_id: str | None = None,
    query: str | None = None,
    dao: MemoriesDAO | None = None,
) -> str:
    """Build a formatted memory context block.

    When ``query`` is provided, memories are ranked by a keyword search
    over ``content``. Otherwise the most recent memories for the given
    ``project_id`` / ``session_id`` are returned.

    Returns an empty string when no memories match.
    """
    if dao is None:
        return ""

    if query:
        memories = await dao.search(query, project_id=project_id, limit=20)
    else:
        memories = await dao.list(
            project_id=project_id,
            session_id=session_id,
            limit=20,
        )

    if not memories:
        return ""

    lines = ["## Relevant memories"]
    for memory in memories:
        category = memory.get("category") or "fact"
        content = memory.get("content") or ""
        lines.append(f"- [{category}] {content}")
    return "\n".join(lines)
