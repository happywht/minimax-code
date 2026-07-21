"""Tests for memory.mmr (R237, ``xai-grok-memory`` ``mmr.rs``).

Covers the migrated pure-logic MMR re-ranking leaf: :func:`tokenize` /
:func:`jaccard_similarity` primitives, the :func:`mmr_rerank` greedy selection
(no-op guards, diversity promotion, case-insensitive similarity, count/field
preservation), and the in-place reordering contract. Also verifies R237 is a
real consumer of R66's :class:`MmrConfig` -- the ``lambda_`` attribute maps to
grok's ``config.lambda``.

Mirrors grok's ``mmr.rs`` tests: ``test_disabled_is_noop`` /
``test_lambda_one_is_noop`` / ``test_single_result_is_noop`` /
``test_mmr_ranks_on_relevance_not_clamped_score`` /
``test_diverse_results_promoted`` / ``test_identical_snippets_heavily_penalized``
/ ``test_case_insensitive_similarity`` / ``test_preserves_result_count`` /
``test_scores_and_snippets_preserved`` + the Jaccard unit tests
(``test_jaccard_identical`` / ``_disjoint`` / ``_partial_overlap`` /
``_both_empty`` / ``_one_empty``) + ``test_tokenize_splits_on_punctuation``.
"""

from __future__ import annotations

import math

import pytest

from minimax_code.config_types import MmrConfig
from minimax_code.memory import SearchResult, mmr_rerank
from minimax_code.memory.mmr import jaccard_similarity, tokenize


def _make_result(id_: str, snippet: str, score: float) -> SearchResult:
    """Build a :class:`SearchResult` with stable ancillary fields (mirrors grok)."""
    return SearchResult(
        chunk_id=id_,
        path=f"{id_}.md",
        start_line=0,
        end_line=1,
        score=score,
        snippet=snippet,
        source="workspace",
        created_at=1_700_000_000,
    )


def _enabled_config(lam: float) -> MmrConfig:
    """``MmrConfig { enabled: true, lambda: lam }`` (grok) -> ``lambda_`` (R66)."""
    return MmrConfig(enabled=True, lambda_=lam)


def _rerank(results: list[SearchResult], config: MmrConfig) -> None:
    """Re-rank using each result's own ``score`` as its relevance (grok helper).

    Mirrors the pre-split behaviour the existing assertions were written for:
    relevance is the unclamped ranking score, here identical to ``.score``.
    """
    relevance = [r.score for r in results]
    mmr_rerank(results, relevance, config)


# ---------------------------------------------------------------------------
# No-op guards.
# ---------------------------------------------------------------------------


def test_disabled_is_noop() -> None:
    """``enabled = false`` (the default) leaves order untouched."""
    results = [
        _make_result("a", "rust async", 1.0),
        _make_result("b", "rust async patterns", 0.9),
    ]
    original_order = [r.chunk_id for r in results]
    _rerank(results, MmrConfig())  # default enabled=False
    assert [r.chunk_id for r in results] == original_order


def test_lambda_one_is_noop() -> None:
    """``lambda = 1.0`` is pure relevance -- no diversity term, no reordering."""
    results = [
        _make_result("a", "rust async", 1.0),
        _make_result("b", "python sync", 0.5),
    ]
    _rerank(results, _enabled_config(1.0))
    assert results[0].chunk_id == "a"
    assert results[1].chunk_id == "b"


def test_single_result_is_noop() -> None:
    """A single result has nothing to diversify against."""
    results = [_make_result("a", "rust async", 1.0)]
    _rerank(results, _enabled_config(0.7))
    assert len(results) == 1
    assert results[0].chunk_id == "a"


# ---------------------------------------------------------------------------
# Core re-ranking behaviour.
# ---------------------------------------------------------------------------


def test_mmr_ranks_on_relevance_not_clamped_score() -> None:
    """Regression guard: MMR ranks on ``relevance``, not clamped ``.score``.

    Both results tie at ``score == 1.0``; the higher-relevance result is placed
    SECOND in input so a buggy ``.score`` read would keep input order and land
    "low" first. Mirrors grok's regression guard.
    """
    results = [
        _make_result("low", "alpha topic one", 1.0),
        _make_result("high", "beta subject two", 1.0),
    ]
    relevance = [1.0, 1.25]
    mmr_rerank(results, relevance, _enabled_config(0.7))

    assert results[0].chunk_id == "high"
    assert results[1].chunk_id == "low"


def test_diverse_results_promoted() -> None:
    """Two similar (rust async) + one diverse (python web): the diverse result
    is promoted above the redundant one."""
    results = [
        _make_result("a", "rust async programming patterns", 1.0),
        _make_result("b", "rust async programming tutorial", 0.95),
        _make_result("c", "python web framework flask", 0.9),
    ]
    _rerank(results, _enabled_config(0.5))

    assert results[0].chunk_id == "a"  # highest relevance stays first
    assert results[1].chunk_id == "c"  # diverse promoted over redundant "b"
    assert results[2].chunk_id == "b"


def test_identical_snippets_heavily_penalized() -> None:
    """An identical duplicate is penalised below a wholly different result."""
    results = [
        _make_result("a", "exact same content here", 1.0),
        _make_result("b", "exact same content here", 0.99),
        _make_result("c", "completely different topic", 0.5),
    ]
    _rerank(results, _enabled_config(0.5))

    assert results[0].chunk_id == "a"
    assert results[1].chunk_id == "c"  # different beats identical duplicate


def test_case_insensitive_similarity() -> None:
    """``"Rust Async"`` and ``"rust async"`` are treated as identical (both
    lowercased before tokenisation). Without lowercasing they would only have
    ~0.5 Jaccard overlap."""
    results = [
        _make_result("a", "Rust Async Programming", 1.0),
        _make_result("b", "rust async programming", 0.95),
        _make_result("c", "Python Web Framework", 0.9),
    ]
    _rerank(results, _enabled_config(0.5))

    assert results[0].chunk_id == "a"
    assert results[1].chunk_id == "c"  # case-only diff detected as redundant


def test_preserves_result_count() -> None:
    """Re-ranking reorders; it never drops or duplicates results."""
    results = [
        _make_result("a", "one", 1.0),
        _make_result("b", "two", 0.9),
        _make_result("c", "three", 0.8),
        _make_result("d", "four", 0.7),
    ]
    _rerank(results, _enabled_config(0.7))
    assert len(results) == 4


def test_scores_and_snippets_preserved() -> None:
    """Every field is intact after re-ranking (no data loss in the move)."""
    results = [
        _make_result("a", "rust programming", 1.0),
        _make_result("b", "python scripting", 0.5),
    ]
    _rerank(results, _enabled_config(0.7))
    for r in results:
        assert r.chunk_id != ""
        assert r.snippet != ""
        assert r.score > 0.0


# ---------------------------------------------------------------------------
# Jaccard similarity unit tests.
# ---------------------------------------------------------------------------


def test_jaccard_identical() -> None:
    a = {"rust", "async"}
    b = {"rust", "async"}
    assert math.isclose(jaccard_similarity(a, b), 1.0)


def test_jaccard_disjoint() -> None:
    a = {"rust", "async"}
    b = {"python", "web"}
    assert math.isclose(jaccard_similarity(a, b), 0.0)


def test_jaccard_partial_overlap() -> None:
    a = {"rust", "async", "programming"}
    b = {"rust", "web", "programming"}
    # intersection = {rust, programming} = 2, union = 4 -> 0.5
    assert math.isclose(jaccard_similarity(a, b), 0.5)


def test_jaccard_both_empty() -> None:
    assert math.isclose(jaccard_similarity(set(), set()), 1.0)


def test_jaccard_one_empty() -> None:
    assert math.isclose(jaccard_similarity({"rust"}, set()), 0.0)


# ---------------------------------------------------------------------------
# Tokeniser.
# ---------------------------------------------------------------------------


def test_tokenize_splits_on_punctuation() -> None:
    tokens = tokenize("hello, world! rust_code")
    assert "hello" in tokens
    assert "world" in tokens
    assert "rust_code" in tokens
    assert "," not in tokens


# ---------------------------------------------------------------------------
# Python-specific + R66 consumer contract.
# ---------------------------------------------------------------------------


def test_mmr_rerank_is_in_place() -> None:
    """Python reordering mutates the same list object (mirrors grok ``&mut Vec``).

    ``results[:] = reordered`` keeps the identity stable -- callers holding a
    reference see the new order without reassignment.
    """
    results = [
        _make_result("a", "rust async programming patterns", 1.0),
        _make_result("b", "rust async programming tutorial", 0.95),
        _make_result("c", "python web framework flask", 0.9),
    ]
    identity = id(results)
    _rerank(results, _enabled_config(0.5))
    assert id(results) == identity  # same list object, reordered in place


def test_mmr_rerank_consumes_r66_mmr_config() -> None:
    """R237 is the first runtime consumer of R66 ``config_types.MmrConfig``.

    The grok field ``config.lambda`` is read as ``config.lambda_`` (R66 renames
    the Python keyword; wire alias stays ``"lambda"``). A mid-range lambda must
    reorder results -- proving the config object is actually consulted, not a
    dead parameter.
    """
    config = MmrConfig(enabled=True, lambda_=0.5)
    assert config.lambda_ == 0.5
    results = [
        _make_result("a", "rust async programming patterns", 1.0),
        _make_result("b", "rust async programming tutorial", 0.95),
        _make_result("c", "python web framework flask", 0.9),
    ]
    relevance = [r.score for r in results]
    mmr_rerank(results, relevance, config)
    assert results[1].chunk_id == "c"  # diverse promoted


def test_mmr_rerank_rejects_misaligned_relevance() -> None:
    """Misaligned relevance length raises (mirrors grok ``assert_eq!``)."""
    results = [_make_result("a", "x", 1.0), _make_result("b", "y", 0.5)]
    with pytest.raises(AssertionError):
        mmr_rerank(results, [1.0], _enabled_config(0.7))  # len mismatch


def test_search_result_is_frozen() -> None:
    """``SearchResult`` is frozen -- fields cannot be reassigned (hashable/cacheable)."""
    from dataclasses import FrozenInstanceError

    r = _make_result("a", "x", 1.0)
    with pytest.raises(FrozenInstanceError):
        r.score = 0.0  # type: ignore[misc]
