"""Memory-subsystem search shape (R67).

Fusion of grok-build's ``xai-grok-workspace-types::types::memory``.

:class:`MemoryChunk` is one entry returned from a memory search,
surfaced via ``OpsChunk::MemoryChunks``. It carries an optional ``f32``
``score`` — and because ``f32`` is ``PartialEq`` but not ``Eq``, the
struct (like the Rust original) deliberately drops the ``Eq`` derive.
"""

from __future__ import annotations

from minimax_code.workspace_types._wire import WireModel

__all__ = ["MemoryChunk"]


class MemoryChunk(WireModel):
    """One entry returned from a memory search.

    ``source`` is the optional path the chunk was derived from;
    ``score`` is the optional relevance score (``f32``).
    """

    id: str
    content: str = ""
    source: str | None = None
    score: float | None = None

    @classmethod
    def default(cls) -> MemoryChunk:
        return cls(id="")
