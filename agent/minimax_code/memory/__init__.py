"""Memory subsystem barrel (R237, ``xai-grok-memory``).

Re-exports the pure-logic MMR diversity re-ranking leaf migrated in R237 from
grok's ``xai-grok-memory/src/mmr.rs``. This module opens the ``memory`` Python
package: cross-session knowledge persistence primitives -- the intelligence
layer that lets the agent recall curated knowledge across sessions.

This leaf is the first runtime consumer of
:class:`minimax_code.config_types.MmrConfig` (R66), closing the config-types
contract loop (the type sat unused as a pure contract until MMR consumed its
``enabled`` / ``lambda_`` fields here).

Migrated leaves:
- R237 (2026-07-22): ``mmr.py`` -- :class:`SearchResult` input contract
  (localised from ``search.rs``) + :func:`mmr_rerank` greedy MMR selection.
  2 exported symbols. Mirrors ``mmr.rs`` pure-logic subset (tokenise +
  Jaccard similarity + greedy relevance/diversity re-rank).

Deferred (runtime layer -- YAGNI until a Python memory backend exists):
``MemoryIndex`` + sqlite-vec hybrid scoring (``search.rs``), ``MemoryStorage``
(``storage.rs``), embedding providers (``embedding.rs``), text chunker
(``chunker.rs``), file watcher (``watcher.rs``), dream consolidation
(``dream.rs``), archive (``archive.rs``). These depend on rusqlite / sqlite-vec
/ git2 / notify / reqwest -- heavy runtime stacks with no current Python
consumer; only their pure-logic leaves (mmr, and later text_utils /
query_expansion) migrate.
"""

from __future__ import annotations

from minimax_code.memory.mmr import SearchResult, mmr_rerank

__all__ = [
    "SearchResult",
    "mmr_rerank",
]
