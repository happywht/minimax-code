"""Maximal Marginal Relevance (MMR) diversity re-ranking (R237, ``xai-grok-memory`` ``mmr.rs``).

Migrates the pure-logic MMR re-ranking leaf from grok's memory subsystem.
Without MMR, multiple memory chunks about the same topic yield near-identical
top results; MMR penalises redundancy by greedily selecting results that
balance relevance with diversity:

.. code-block:: text

    MMR(d) = λ × relevance(d) - (1-λ) × max_similarity(d, selected)

Similarity is Jaccard on tokenised snippets (no embeddings needed). O(n²) but
n is tiny (typically 6–18 candidates after hybrid scoring).

**Consumer of R66.** This leaf is the first runtime consumer of
:class:`minimax_code.config_types.MmrConfig` -- closing the config-types
contract loop the way R46/R47 consumed the R45 model vocabulary. The grok
field ``config.lambda`` maps to the R66 attribute ``config.lambda_`` (``lambda``
is a Python keyword; R66 aliases it to the wire name ``"lambda"``).

:class:`SearchResult` is localised here from grok's ``search.rs`` because
``mmr`` is the first memory leaf to migrate and needs its input contract; the
heavier ``search.rs`` runtime (``MemoryIndex`` + sqlite-vec hybrid scoring)
stays deferred. A future ``search.py`` leaf can promote this into a shared
``search_types`` module if another consumer appears.

Deliberately out of scope (YAGNI -- runtime layer): grok's ``placeholder_result``
helper exists only to enable ``std::mem::replace`` (move-without-Clone) when
reordering the result vec; Python list reordering constructs a new list
directly, so no placeholder is needed.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from minimax_code.config_types import MmrConfig

#: One or more word characters (Unicode alphanumeric + underscore). Mirrors
#: grok's ``split(|c| !c.is_alphanumeric() && c != '_')``: a token is a maximal
#: run of alphanumeric-or-underscore characters. ``re`` is Unicode-aware by
#: default for ``str`` patterns, matching grok's ``char::is_alphanumeric()``.
_TOKEN_RE = re.compile(r"\w+")


@dataclass(frozen=True)
class SearchResult:
    """A single scored memory chunk (mirrors grok ``search::SearchResult``).

    The re-ranking input contract: ``snippet`` drives tokenisation for the
    Jaccard diversity term; the other fields are carried through untouched.
    ``start_line`` / ``end_line`` are 1-based (grok ``usize``); ``created_at``
    is a Unix timestamp (grok ``i64``). Frozen so callers can hash/cache
    results. Localised from ``search.rs`` (the heavier hybrid-scoring runtime
    stays deferred); see module docstring on promotion to a shared leaf.
    """

    chunk_id: str
    path: str
    start_line: int
    end_line: int
    score: float
    snippet: str
    source: str
    created_at: int


def tokenize(text: str) -> set[str]:
    """Tokenise text into a set of alphanumeric+underscore words (Jaccard input).

    Splits on every run of non-word characters, mirroring grok's
    ``split(|c| !c.is_alphanumeric() && c != '_')``. Expects **pre-lowered**
    input -- callers should lowercase snippets before calling (casing varies
    across markdown sources; lowercasing happens in :func:`mmr_rerank`).
    No stop-word removal: full token overlap is wanted for similarity scoring.
    """
    return set(_TOKEN_RE.findall(text))


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Jaccard similarity ``|A ∩ B| / |A ∪ B|``.

    Two empty sets are defined as identical (``1.0``) -- they carry no
    distinguishing tokens; one empty set is fully disjoint (``0.0``).
    Mirrors grok ``jaccard_similarity``.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a) + len(b) - intersection
    if union == 0:
        return 0.0
    return intersection / union


def mmr_rerank(
    results: list[SearchResult],
    relevance: Sequence[float],
    config: MmrConfig,
) -> None:
    """Re-rank ``results`` in place to balance relevance with diversity.

    No-op when ``config.enabled`` is false, ``config.lambda_`` is ``1.0``, or
    there are fewer than 2 results. ``relevance`` is the per-result unclamped
    ranking score, aligned index-for-index with ``results`` on entry; it is
    passed separately (rather than read from the clamped ``SearchResult.score``)
    because clamping saturates top chunks to ``1.0`` and loses the
    access-frequency boost tiebreak.

    Mirrors grok ``mmr::mmr_rerank``. The grok field ``config.lambda`` is read
    here as ``config.lambda_`` (R66 renames the Python keyword; wire alias
    ``"lambda"`` is preserved for TOML/JSON deserialisation). Reorders
    ``results`` in place via slice assignment (``results[:] = ...``); the
    caller's ``relevance`` sequence is stale after this call and must not be
    read again.
    """
    if not config.enabled or len(results) <= 1:
        return
    if config.lambda_ == 1.0:
        return
    assert len(relevance) == len(results), "relevance must be aligned with results"

    # Lowercase snippets once, then tokenise. Ensures "Rust" and "rust" are
    # treated as the same token -- casing varies across markdown sources.
    token_cache = [tokenize(r.snippet.lower()) for r in results]

    max_score = max(relevance)
    min_score = min(relevance)
    # Guard against divide-by-zero when every result ties on relevance; grok
    # uses ``f64::EPSILON`` -- ``sys.float_info.epsilon`` is the IEEE-754 double
    # equivalent (≈ 2.22e-16).
    score_range = max(max_score - min_score, sys.float_info.epsilon)

    lambda_ = config.lambda_
    selected: list[int] = []
    remaining = list(range(len(results)))

    while remaining:
        best_pos = 0
        best_mmr = float("-inf")
        for pos, candidate in enumerate(remaining):
            normalized = (relevance[candidate] - min_score) / score_range
            max_sim = max(
                (
                    jaccard_similarity(token_cache[candidate], token_cache[sel])
                    for sel in selected
                ),
                default=0.0,
            )
            mmr_score = lambda_ * normalized - (1.0 - lambda_) * max_sim
            # Strictly-greater wins; on exact tie the higher-relevance candidate
            # wins (stable deterministic order). Mirrors grok's tiebreak.
            if mmr_score > best_mmr or (
                mmr_score == best_mmr
                and relevance[candidate] > relevance[remaining[best_pos]]
            ):
                best_mmr = mmr_score
                best_pos = pos
        selected.append(remaining.pop(best_pos))

    results[:] = [results[i] for i in selected]
