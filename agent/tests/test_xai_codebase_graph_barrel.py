"""Crate-root barrel reconciliation tests for xai_codebase_graph.

R308 reconciled the crate-root barrel to mirror grok ``lib.rs`` L84-L102:

* ``Location`` binds to the **navigation** flavor (``path`` / ``line`` /
  ``symbol``), not the ``types`` flavor (``file_path`` / ``line`` /
  ``column`` / ``range``). grok's crate root carries only the navigation
  flavor (L94); the types flavor stays reachable via the ``types``
  subpackage.
* ``FileEvent`` / ``FileEventKind`` bind to the **index_manager** batch
  container (``paths: list[str]`` + ``REMOVED`` variant), not the ``types``
  single-file tagged union (single ``path`` + ``DELETED`` variant). grok
  re-exports the batch container at L84.
* The navigation quartet (``Location`` / ``NavigationError`` /
  ``NavigationResult`` / ``Navigator``) reaches the crate root (grok L94).
* ``IndexManager`` (the actor runtime) reaches the crate root (grok L85).
* The 6 ``NavigationError`` subclasses stay leaf-module-only (grok re-exports
  only the base ``NavigationError``).

These tests pin the barrel surface so a future edit cannot silently rebind
``Location`` / ``FileEvent`` to the wrong flavor -- the R300 placement and
the R306a deferral both left the crate root bound to the ``types`` flavor;
R308 is the atomic switch grok's lib.rs prescribes.
"""

from __future__ import annotations

import dataclasses

import minimax_code.xai_codebase_graph as xcg
from minimax_code.xai_codebase_graph import index_manager as im
from minimax_code.xai_codebase_graph import navigation as nav
from minimax_code.xai_codebase_graph import types as types_mod

# === R308: Location flavor switch (types -> navigation) =================


def test_root_location_is_navigation_flavor() -> None:
    """Crate-root Location carries the navigation fields (path/line/symbol)."""
    fields = {f.name for f in dataclasses.fields(xcg.Location)}
    assert fields == {"path", "line", "symbol"}
    assert xcg.Location is nav.Location


def test_root_location_is_not_types_flavor() -> None:
    """Crate-root Location is NOT the types flavor (file_path/column/range)."""
    types_fields = {f.name for f in dataclasses.fields(types_mod.Location)}
    assert types_fields == {"file_path", "line", "column", "range"}
    assert xcg.Location is not types_mod.Location
    assert xcg.Location is nav.Location


def test_types_location_still_reachable_via_subpackage() -> None:
    """The types-flavored Location stays reachable via the types subpackage."""
    assert types_mod.Location is not nav.Location
    # the types flavor still carries the LSP-style file_path/column/range
    fields = {f.name for f in dataclasses.fields(types_mod.Location)}
    assert {"file_path", "column", "range"} <= fields


# === R308: FileEvent / FileEventKind flavor switch (types -> index_manager) ===


def test_root_file_event_is_index_manager_batch_container() -> None:
    """Crate-root FileEvent binds to the index_manager batch container (grok L84)."""
    assert xcg.FileEvent is im.FileEvent
    assert xcg.FileEventKind is im.FileEventKind
    # batch container: ``paths`` is a list (vs the types single-path union)
    fields = {f.name for f in dataclasses.fields(xcg.FileEvent)}
    assert "paths" in fields


def test_root_file_event_kind_uses_removed_variant() -> None:
    """The index_manager FileEventKind spells the delete variant ``REMOVED``."""
    assert xcg.FileEventKind.REMOVED.value == "removed"
    assert not hasattr(xcg.FileEventKind, "DELETED")


def test_types_file_event_still_reachable_via_subpackage() -> None:
    """The types-flavored FileEvent/FileEventKind stay reachable via types."""
    assert types_mod.FileEvent is not im.FileEvent
    assert types_mod.FileEventKind is not im.FileEventKind
    # the types union spells the delete variant ``DELETED`` (vs ``REMOVED``)
    assert types_mod.FileEventKind.DELETED.value == "deleted"
    assert not hasattr(types_mod.FileEventKind, "REMOVED")
    # types FileEvent carries a single ``path`` (not the batch ``paths`` list)
    types_fields = {f.name for f in dataclasses.fields(types_mod.FileEvent)}
    im_fields = {f.name for f in dataclasses.fields(im.FileEvent)}
    assert "paths" in im_fields
    assert "paths" not in types_fields
    assert "path" in types_fields  # single-file union


# === R308: navigation quartet + IndexManager reach the crate root ========


def test_navigation_quartet_reachable_at_crate_root() -> None:
    """grok lib.rs L94 re-exports Location/NavigationError/NavigationResult/Navigator."""
    assert xcg.Location is nav.Location
    assert xcg.NavigationError is nav.NavigationError
    assert xcg.NavigationResult is nav.NavigationResult
    assert xcg.Navigator is nav.Navigator


def test_index_manager_reachable_at_crate_root() -> None:
    """grok lib.rs L85 re-exports IndexManager (the actor runtime)."""
    assert xcg.IndexManager is im.IndexManager


def test_navigation_error_subclasses_leaf_only() -> None:
    """The 6 NavigationError subclasses stay leaf-module-only (grok L94 base only)."""
    leaf_only = [
        "FileNotFound",
        "PositionOutOfBounds",
        "NoSymbolAtPosition",
        "UnsupportedLanguage",
        "ParseError",
        "IoError",
    ]
    for name in leaf_only:
        # NOT at the crate root
        assert not hasattr(xcg, name), f"{name} must not reach the crate root"
        assert name not in xcg.__all__
        # but reachable via the navigation leaf module, and is a NavigationError subclass
        sub = getattr(nav, name)
        assert issubclass(sub, nav.NavigationError)


# === R308: __all__ surface integrity ===================================


def test_all_has_no_duplicates() -> None:
    """__all__ must not list any symbol twice (R308 moved FileEvent/FileEventKind)."""
    assert len(xcg.__all__) == len(set(xcg.__all__))


def test_all_contains_r308_additions() -> None:
    """__all__ includes the R308 additions (nav quartet + IndexManager)."""
    for name in (
        "Location",
        "NavigationError",
        "NavigationResult",
        "Navigator",
        "IndexManager",
        "FileEvent",
        "FileEventKind",
    ):
        assert name in xcg.__all__


def test_star_import_resolves_all() -> None:
    """``from package import *`` resolves every __all__ entry to a real attribute."""
    star_ns: dict[str, object] = {}
    exec("from minimax_code.xai_codebase_graph import *", star_ns)
    for name in xcg.__all__:
        assert name in star_ns, f"{name!r} in __all__ but not star-imported"


# === grok lib.rs L84-L86 parity (index_manager surface) =================


def test_index_manager_surface_matches_grok_l84() -> None:
    """All 11 grok lib.rs L84-L86 index_manager re-exports reach the crate root."""
    expected = {
        "MAX_INDEXABLE_FILE_SIZE",
        "FileEvent",
        "FileEventKind",
        "IndexCommand",
        "IndexManager",
        "IndexManagerConfig",
        "IndexManagerHandle",
        "QueryError",
        "QueryResult",
        "SymbolLocation",
        "is_binary_content",
    }
    assert expected <= set(xcg.__all__)
    for name in expected:
        assert hasattr(xcg, name)
