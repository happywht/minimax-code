"""Long-term memory backend (v0.11.0 Milestone 3)."""

from __future__ import annotations

from .chunker import Chunk, chunk_hash, chunk_markdown
from .extractor import MemoryExtractor  # noqa: F401
from .injector import MemoryInjector, build_memory_context  # noqa: F401
from .mmr import SearchResult, mmr_rerank
from .query_expansion import extract_keywords
from .store import MemoriesDAO  # noqa: F401
from .text_utils import has_markdown_headers, is_no_reply

# Barrel surface (R237-R240): only the zero-I/O leaf symbols are public.
# Runtime modules (store/extractor/injector) may be imported directly.
__all__ = [
    "Chunk",
    "SearchResult",
    "chunk_hash",
    "chunk_markdown",
    "extract_keywords",
    "has_markdown_headers",
    "is_no_reply",
    "mmr_rerank",
]
