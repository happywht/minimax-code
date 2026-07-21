"""Barrel surface regression baseline for ``minimax_code.memory`` (R237+R238).

Locks the public symbol set re-exported by ``minimax_code.memory`` so a future
leaf migration (R239+) must update both the count assertion and the set
baseline, or append a new group -- preventing silent barrel drift (a symbol
renamed in a leaf but not in the barrel would silently disappear from the
public API).

Baseline (2026-07-22): 5 symbols = 1 class (``SearchResult``, R237) +
4 functions (``mmr_rerank`` R237, ``extract_keywords`` R238,
``has_markdown_headers`` + ``is_no_reply`` R239).
``tokenize`` / ``jaccard_similarity`` (mmr) and ``_STOP_WORDS`` (query_expansion)
are module-public but intentionally NOT barrel-exported -- they are leaf-internal
helpers, mirroring grok's private ``fn`` / ``static`` visibility (no ``pub``).
``text_utils`` has no module-private helpers -- both predicates are barrel-public.
"""

from __future__ import annotations

import minimax_code.memory as memory


def test_package_barrel_exposes_five_symbols() -> None:
    """R237 + R238 + R239 -- 5 symbols (1 class + 4 fns).

    Future leaves must update count + set, or append a new migration-round
    group below.
    """
    assert len(memory.__all__) == 5
    assert set(memory.__all__) == {
        # R237 (mmr.py): SearchResult input contract + mmr_rerank selection.
        "SearchResult",
        "mmr_rerank",
        # R238 (query_expansion.py): FTS query keyword extraction.
        "extract_keywords",
        # R239 (text_utils.py): response-classification predicates.
        "has_markdown_headers",
        "is_no_reply",
    }


def test_all_exported_names_resolve_to_real_attributes() -> None:
    """Every ``__all__`` entry must resolve to a real attribute on the package
    -- catches stale ``__all__`` entries pointing at renamed/removed symbols
    (the set assertion above only checks membership, not resolvability)."""
    for name in memory.__all__:
        assert hasattr(memory, name), f"memory missing exported symbol: {name}"


def test_barrel_all_is_global_ascii_sorted() -> None:
    """The barrel ``__all__`` list itself must be pure global ASCII sort
    (uppercase 65-90 before lowercase 97-122, alphabetical within each group).

    Guards against accidental grouping-by-migration-round IN the barrel list;
    the set assertion above is free-form, but the barrel must stay sorted so
    re-exports read deterministically across leaves."""
    assert memory.__all__ == sorted(memory.__all__)
