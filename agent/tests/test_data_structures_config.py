"""Barrel surface regression baseline for ``minimax_code.data_structures`` (R241+).

Locks the public symbol set re-exported by ``minimax_code.data_structures``
so a future leaf migration (R243b+) must update both the count assertion and
the set baseline, or append a new group -- preventing silent barrel drift (a
symbol renamed in a leaf but not in the barrel would silently disappear from
the public API).

Baseline (R241): 2 symbols = 2 classes (``Entry`` + ``OrderedHashMap`` from
``ordered_hashmap.py``). Both ``Entry`` and ``OrderedHashMap`` are
barrel-public; the module has no module-private helpers (every grok method
maps to a public method on one of the two classes).

Baseline (R242): +2 symbols = ``Edge`` + ``GraphOption`` (frozen value objects
from ``graphlib.py``); the 3 sentinel constants and the 3 edge-id encoding
helpers are module-level in ``graphlib.py`` and intentionally NOT
barrel-public (they mirror grok's module-private ``fn`` / non-re-exported
``pub const``).

Baseline (R243): +1 symbol = ``Graph`` (the ``Graph<GL, N, E>`` core struct
from ``graphlib.py``); ``NodeLabelValue`` / ``NodeLabelFactory`` /
``DefaultNodeLabel`` and the 3 sentinel constants stay module-level (not
barrel-public) -- they are construction-time vocabulary consumed by ``Graph``
methods, mirroring grok's non-re-exported ``enum`` + module-private items.
"""

from __future__ import annotations

import minimax_code.data_structures as data_structures


def test_package_barrel_exposes_five_symbols() -> None:
    """R243 -- 5 symbols (2 from R241 + 2 from R242 + 1 from R243).

    Future leaves must update count + set, or append a new migration-round
    group below.
    """
    assert len(data_structures.__all__) == 5
    assert set(data_structures.__all__) == {
        # R241 (ordered_hashmap.py): insertion-ordered hash map + entry.
        "Entry",
        "OrderedHashMap",
        # R242 (graphlib.py): graphlib edge-encoding value objects.
        "Edge",
        "GraphOption",
        # R243 (graphlib.py): Graph core struct + node primitives.
        "Graph",
    }


def test_all_exported_names_resolve_to_real_attributes() -> None:
    """Every ``__all__`` entry must resolve to a real attribute on the package
    -- catches stale ``__all__`` entries pointing at renamed/removed symbols."""
    for name in data_structures.__all__:
        assert hasattr(data_structures, name), (
            f"data_structures missing exported symbol: {name}"
        )


def test_barrel_all_is_global_ascii_sorted() -> None:
    """The barrel ``__all__`` list itself must be pure global ASCII sort.

    Guards against accidental grouping-by-migration-round IN the barrel list;
    the set assertion above is free-form, but the barrel must stay sorted so
    re-exports read deterministically across leaves.
    """
    assert data_structures.__all__ == sorted(data_structures.__all__)
