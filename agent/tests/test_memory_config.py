"""Barrel surface regression baseline for ``minimax_code.memory`` (R237).

Locks the public symbol set re-exported by ``minimax_code.memory`` so a future
leaf migration (R238+) must update both the count assertion and the set
baseline, or append a new group -- preventing silent barrel drift (a symbol
renamed in a leaf but not in the barrel would silently disappear from the
public API).

R237 baseline (mmr.py, 2026-07-22): 2 symbols = 1 class (``SearchResult``) +
1 function (``mmr_rerank``). ``tokenize`` / ``jaccard_similarity`` are
module-public (tested directly in ``test_memory_mmr.py``) but intentionally
NOT barrel-exported -- they are mmr-internal helpers, mirroring grok's private
``fn`` visibility (no ``pub``).
"""

from __future__ import annotations

import minimax_code.memory as memory


def test_package_barrel_exposes_two_symbols() -> None:
    """R237 opens the memory package with mmr.py -- 2 symbols (1 class + 1 fn).

    Future leaves must update count + set, or append a new migration-round
    group below.
    """
    assert len(memory.__all__) == 2
    assert set(memory.__all__) == {
        # R237 (mmr.py): SearchResult input contract + mmr_rerank selection.
        "SearchResult",
        "mmr_rerank",
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
