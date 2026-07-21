"""Memory subsystem barrel (R237+, ``xai-grok-memory``).

Re-exports the pure-logic memory leaves migrated from grok's
``xai-grok-memory`` crate. This module opens the ``memory`` Python package:
cross-session knowledge persistence primitives -- the intelligence layer that
lets the agent recall curated knowledge across sessions.

The first leaf (:mod:`minimax_code.memory.mmr`, R237) is the first runtime
consumer of :class:`minimax_code.config_types.MmrConfig` (R66), closing the
config-types contract loop (the type sat unused as a pure contract until MMR
consumed its ``enabled`` / ``lambda_`` fields here).

Migrated leaves:
- R237 (2026-07-22): ``mmr.py`` -- :class:`SearchResult` input contract
  (localised from ``search.rs``) + :func:`mmr_rerank` greedy MMR selection.
  2 exported symbols. Mirrors ``mmr.rs`` pure-logic subset (tokenise +
  Jaccard similarity + greedy relevance/diversity re-rank).
- R238 (2026-07-22): ``query_expansion.py`` -- :func:`extract_keywords` FTS
  query preprocessing (stop-word removal + 2-char minimum + pure-numeric
  filter + ordered de-dup). 1 exported symbol. Mirrors
  ``query_expansion.rs`` (``STOP_WORDS`` LazyLock HashSet -> ``frozenset``,
  ``split(|c| !is_alphanumeric() && c != '_')`` -> ``\\w+``). Shares the word
  split primitive with ``mmr.tokenize`` but consumes it as an ordered keyword
  list, not a de-duplicated set.

Deferred (runtime layer -- YAGNI until a Python memory backend exists):
``MemoryIndex`` + sqlite-vec hybrid scoring (``search.rs``), ``MemoryStorage``
(``storage.rs``), embedding providers (``embedding.rs``), text chunker
(``chunker.rs``; pure-logic candidate deferred -- depends on ``blake3`` +
``MemoryIndexConfig``), file watcher (``watcher.rs``), dream consolidation
(``dream.rs``), archive (``archive.rs``). These depend on rusqlite / sqlite-vec
/ git2 / notify / reqwest -- heavy runtime stacks with no current Python
consumer; only their pure-logic leaves (mmr, query_expansion; text_utils still
pending) migrate.
"""

from __future__ import annotations

from minimax_code.memory.mmr import SearchResult, mmr_rerank
from minimax_code.memory.query_expansion import extract_keywords

__all__ = [
    "SearchResult",
    "extract_keywords",
    "mmr_rerank",
]
