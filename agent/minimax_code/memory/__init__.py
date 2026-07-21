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
- R239 (2026-07-22): ``text_utils.py`` -- :func:`has_markdown_headers` +
  :func:`is_no_reply` response-classification predicates (structured-output
  guard + NO_REPLY convention). 2 exported symbols. Mirrors ``text_utils.rs``
  zero-dependency pure-logic subset. Independent of the mmr/query_expansion
  word-split primitive -- a fully self-contained classifier pair, no sibling
  coupling.

Deferred (runtime layer -- YAGNI until a Python memory backend exists):
``MemoryIndex`` + sqlite-vec hybrid scoring (``search.rs``), ``MemoryStorage``
(``storage.rs``), embedding providers (``embedding.rs``), text chunker
(``chunker.rs``; pure-logic candidate deferred -- depends on ``blake3`` +
``MemoryIndexConfig``), file watcher (``watcher.rs``), dream consolidation
(``dream.rs``), archive (``archive.rs``). These depend on rusqlite / sqlite-vec
/ git2 / notify / reqwest -- heavy runtime stacks with no current Python
consumer; only their pure-logic leaves (mmr, query_expansion, text_utils)
migrate.
"""

from __future__ import annotations

from minimax_code.memory.mmr import SearchResult, mmr_rerank
from minimax_code.memory.query_expansion import extract_keywords
from minimax_code.memory.text_utils import has_markdown_headers, is_no_reply

__all__ = [
    "SearchResult",
    "extract_keywords",
    "has_markdown_headers",
    "is_no_reply",
    "mmr_rerank",
]
