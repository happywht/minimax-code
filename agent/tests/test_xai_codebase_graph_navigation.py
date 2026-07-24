"""Black-box tests for ``navigation`` (R307 -- direction (2), brick 8).

Ported **by function** from grok ``xai-codebase-graph/src/navigation.rs``:

* ``Location`` (navigation.rs L22-L53) -- the lightweight
  ``(path, line, symbol?)`` triple, distinct from ``types::Location``.
* ``NavigationResult`` (L13-L19) -- ``(symbol, locations)``.
* ``NavigationError`` (L55-L99) -- 6-variant enum; Python models each variant
  as a subclass so callers can ``except`` the specific failure.
* ``Navigator`` (L101-L344) -- 3 accessors + 6 navigation methods.

The cursor hit-test + parser resolver are **shared** with
:mod:`minimax_code.xai_codebase_graph.index_manager` (R306g) and
:mod:`minimax_code.xai_codebase_graph.manager.builder` (R305e) -- the DRY
correction of grok's duplicate definitions. These tests pin the navigation
surface and the error-mapping contract; the shared helpers themselves are
pinned in ``test_xai_codebase_graph_index_manager_reindex.py``.

tree-sitter is absent in this environment, so the parser is a duck-typed
fake (any object with ``parse(src) -> tree``; ``tree.root_node``; nodes with
``type`` / ``start_byte`` / ``end_byte`` / ``start_point`` / ``end_point`` /
``children`` satisfies the hit-test contract).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import minimax_code.xai_codebase_graph as xcg_root
from minimax_code.xai_codebase_graph import navigation as nav
from minimax_code.xai_codebase_graph.navigation import (
    FileNotFound,
    IoError,
    Location,
    NavigationError,
    NavigationResult,
    Navigator,
    NoSymbolAtPosition,
    ParseError,
    PositionOutOfBounds,
    UnsupportedLanguage,
)

# === Duck-typed tree-sitter fakes =======================================
# Mirrors the fakes in test_xai_codebase_graph_index_manager_reindex.py --
# duplicated rather than imported to keep each suite standalone (the reindex
# fakes are private helpers, not a shared test fixture module).


class _Point:
    """tree-sitter ``Point`` stand-in: ``row`` + ``column``."""

    def __init__(self, row: int, column: int) -> None:
        self.row = row
        self.column = column


class _FakeNode:
    """Minimal tree-sitter ``Node`` stand-in for the hit-test + decode paths."""

    def __init__(
        self,
        type_: str,
        *,
        children: list[_FakeNode] | None = None,
        start_point: tuple[int, int] = (0, 0),
        end_point: tuple[int, int] = (0, 0),
        start_byte: int = 0,
        end_byte: int = 0,
    ) -> None:
        self.type = type_
        self.children = children if children is not None else []
        self.start_point = _Point(*start_point)
        self.end_point = _Point(*end_point)
        self.start_byte = start_byte
        self.end_byte = end_byte


class _FakeTree:
    """tree-sitter ``Tree`` stand-in: just a ``root_node``."""

    def __init__(self, root_node: _FakeNode) -> None:
        self.root_node = root_node


class _FakeParser:
    """tree-sitter ``Parser`` stand-in: ``parse`` returns a fixed tree."""

    def __init__(self, tree: _FakeTree | None) -> None:
        self._tree = tree

    def parse(self, _src: bytes) -> _FakeTree | None:
        return self._tree


class _FakeConfig:
    """Sentinel language config -- only identity matters (non-``None``)."""


class _FakeRegistry:
    """``LanguageRegistry`` stand-in: configurable ``for_file_path`` result."""

    def __init__(self, config: object | None = _FakeConfig()) -> None:
        self._config = config

    def for_file_path(self, _path: object) -> object | None:
        return self._config


class _FakeIndex:
    """``ScopeGraphIndex`` stand-in: canned definition/reference results.

    Records the ``(symbol, context_file, registry)`` triple each smart-query
    method received so the navigation mapping can assert the threading, plus
    the canned ``(path, line)`` / ``(symbol, path, line)`` tuples to return.
    """

    def __init__(
        self,
        definitions: list[tuple[str, int]] | None = None,
        references: list[tuple[str, str, int]] | None = None,
    ) -> None:
        self.definitions = definitions or []
        self.references = references or []
        self.def_calls: list[tuple[str, str | None, object]] = []
        self.ref_calls: list[tuple[str, str | None, object]] = []

    def find_definitions_smart(
        self, symbol: str, context_file: str | None, registry: object
    ) -> list[tuple[str, int]]:
        self.def_calls.append((symbol, context_file, registry))
        return list(self.definitions)

    def find_references_smart(
        self, symbol: str, context_file: str | None, registry: object
    ) -> list[tuple[str, str, int]]:
        self.ref_calls.append((symbol, context_file, registry))
        return list(self.references)


# === Location ===========================================================


def test_location_new_has_no_symbol() -> None:
    """``Location::new`` yields a symbol-less location (grok L37-L41)."""
    loc = Location.new("src/lib.py", 12)
    assert loc.path == "src/lib.py"
    assert loc.line == 12
    assert loc.symbol is None


def test_location_with_symbol_carries_name() -> None:
    """``Location::with_symbol`` tags the matched spelling (grok L43-L47)."""
    loc = Location.with_symbol("src/lib.py", 7, "alias_name")
    assert loc.symbol == "alias_name"


def test_location_default_symbol_is_none() -> None:
    """Direct construction defaults ``symbol`` to ``None`` (grok ``Option``)."""
    assert Location(path="x", line=1).symbol is None


def test_location_as_path_returns_pathlib() -> None:
    """``as_path`` returns a :class:`~pathlib.Path` (grok L49-L53).

    ``as_posix()`` keeps the assertion stable across Windows / POSIX (``str()``
    would flip the separators on Windows, masking a regression).
    """
    p = Location.new("a/b/c.py", 1).as_path()
    assert isinstance(p, Path)
    assert p.as_posix() == "a/b/c.py"


def test_location_is_frozen() -> None:
    """grok derives no interior mutability; Python freezes the dataclass."""
    loc = Location.new("x", 1)
    with pytest.raises((AttributeError, Exception)):
        loc.path = "y"  # type: ignore[misc]


def test_location_is_hashable_and_equal_by_all_fields() -> None:
    """grok ``#[derive(PartialEq, Eq, Hash)]`` -> frozen + hashable + field-wise eq."""
    a = Location.with_symbol("x", 1, "foo")
    b = Location.with_symbol("x", 1, "foo")
    c = Location.with_symbol("x", 1, "bar")  # different symbol
    assert a == b
    assert a != c
    assert hash(a) == hash(b)
    assert {a, b, c} == {a, c}  # a/b collapse; c distinct


# === NavigationResult ===================================================


def test_navigation_result_holds_fields() -> None:
    """``NavigationResult`` carries the symbol + its locations (grok L13-L19)."""
    locs = [Location.new("a.py", 3), Location.with_symbol("b.py", 9, "foo")]
    res = NavigationResult(symbol="foo", locations=locs)
    assert res.symbol == "foo"
    assert res.locations is locs


def test_navigation_result_is_frozen() -> None:
    """Frozen dataclass (grok ``Debug, Clone``; no interior mutability)."""
    res = NavigationResult(symbol="foo", locations=[])
    with pytest.raises((AttributeError, Exception)):
        res.symbol = "bar"  # type: ignore[misc]


# === NavigationError hierarchy ==========================================


def test_navigation_error_is_exception() -> None:
    """``NavigationError`` subclasses :class:`Exception` (grok ``thiserror::Error``)."""
    assert issubclass(NavigationError, Exception)


@pytest.mark.parametrize(
    "variant",
    [FileNotFound, PositionOutOfBounds, NoSymbolAtPosition,
     UnsupportedLanguage, ParseError, IoError],
)
def test_each_variant_subclasses_navigation_error(variant: type) -> None:
    """All 6 grok enum variants are :class:`NavigationError` subclasses."""
    assert issubclass(variant, NavigationError)


def test_catch_specific_variant_via_base() -> None:
    """``except NavigationError`` catches every variant (the grok ``?`` path)."""
    with pytest.raises(NavigationError):
        raise PositionOutOfBounds(0, 5)


def test_file_not_found_stores_path_and_message() -> None:
    """``FileNotFound(PathBuf)`` keeps the path + grok ``Display`` text."""
    err = FileNotFound("src/missing.py")
    assert err.path == "src/missing.py"
    assert "src/missing.py" in str(err)
    assert "File not found" in str(err)


def test_position_out_of_bounds_stores_row_col() -> None:
    """``PositionOutOfBounds`` carries the rejected coordinates."""
    err = PositionOutOfBounds(0, 7)
    assert err.row == 0
    assert err.col == 7
    assert "0:7" in str(err)


def test_no_symbol_at_position_stores_row_col() -> None:
    """``NoSymbolAtPosition`` carries the empty coordinates."""
    err = NoSymbolAtPosition(3, 5)
    assert err.row == 3
    assert err.col == 5
    assert "3:5" in str(err)


def test_unsupported_language_stores_ext() -> None:
    """``UnsupportedLanguage(String)`` keeps the offending extension."""
    err = UnsupportedLanguage("xyz")
    assert err.ext == "xyz"
    assert "xyz" in str(err)


def test_parse_error_stores_message() -> None:
    """``ParseError(String)`` keeps the detail string."""
    err = ParseError("boom")
    assert err.message == "boom"
    assert "boom" in str(err)


def test_io_error_stores_message() -> None:
    """``IoError`` keeps the detail string (grok ``From<io::Error>`` carrier)."""
    err = IoError("disk gone")
    assert err.message == "disk gone"
    assert "disk gone" in str(err)


# === Navigator: construction + accessors ================================


def test_navigator_index_accessor_returns_injected_index() -> None:
    """``index()`` borrows the wrapped :class:`ScopeGraphIndex` (grok L110-L114)."""
    idx = _FakeIndex()
    nav_obj = Navigator(_index=idx)
    assert nav_obj.index is idx


def test_navigator_index_mut_returns_same_object() -> None:
    """``index_mut()`` returns the same object -- no Arc CoW in Python.

    grok uses ``Arc::make_mut`` (copy-on-write); the Python port documents the
    divergence: mutations are visible to every holder of the same index. The
    accessor still yields the wrapped object for in-place mutation.
    """
    idx = _FakeIndex()
    nav_obj = Navigator(_index=idx)
    assert nav_obj.index_mut is idx


def test_navigator_default_registry_is_fresh_language_registry() -> None:
    """Omitting ``registry`` defaults to a fresh :class:`LanguageRegistry` (grok L116-L124)."""
    from minimax_code.xai_codebase_graph.languages import LanguageRegistry

    nav_obj = Navigator(_index=_FakeIndex())
    assert isinstance(nav_obj._registry, LanguageRegistry)


# === Navigator.get_symbol_at_position: error mapping ====================
# Mirrors grok navigation.rs L153-L203. The 6 failure modes each map to their
# own NavigationError variant.


def _make_navigator(
    *,
    config: object | None = _FakeConfig(),
) -> tuple[Navigator, _FakeIndex, _FakeRegistry]:
    """Build a Navigator wired so ``get_symbol_at_position`` uses fakes.

    The parser resolver (``_get_parser_and_query``) is monkeypatched per-test
    via the ``patch_resolver`` fixture; this helper only wires the registry +
    index so the smart-query methods have somewhere to record calls.
    """
    idx = _FakeIndex()
    registry = _FakeRegistry(config=config)
    nav_obj = Navigator(_index=idx, _registry=registry)
    return nav_obj, idx, registry


@pytest.fixture()
def patch_resolver(monkeypatch: pytest.MonkeyPatch):
    """Patch the parser resolver imported into :mod:`navigation`.

    Returns a mutable holder so each test can set the canned ``(parser, query)``
    pair the resolver should return.
    """
    holder = {"parser": None, "query": None}

    def _fake_resolver(_cfg: object):
        return holder["parser"], holder["query"]

    monkeypatch.setattr(nav, "_get_parser_and_query", _fake_resolver)
    return holder


def test_get_symbol_row_zero_raises_position_out_of_bounds(
    tmp_path: Path, patch_resolver
) -> None:
    """``row == 0`` is the no-cursor sentinel -> :class:`PositionOutOfBounds`."""
    nav_obj, _, _ = _make_navigator()
    f = tmp_path / "x.py"
    f.write_bytes(b"foo")
    with pytest.raises(PositionOutOfBounds):
        nav_obj.get_symbol_at_position(f, 0, 1)


def test_get_symbol_col_zero_raises_position_out_of_bounds(
    tmp_path: Path, patch_resolver
) -> None:
    """``col == 0`` is the no-cursor sentinel -> :class:`PositionOutOfBounds`."""
    nav_obj, _, _ = _make_navigator()
    f = tmp_path / "x.py"
    f.write_bytes(b"foo")
    with pytest.raises(PositionOutOfBounds):
        nav_obj.get_symbol_at_position(f, 1, 0)


def test_get_symbol_missing_file_raises_file_not_found(
    tmp_path: Path, patch_resolver
) -> None:
    """Read failure (deleted between request/parse) -> :class:`FileNotFound`."""
    nav_obj, _, _ = _make_navigator()
    missing = tmp_path / "gone.py"
    with pytest.raises(FileNotFound):
        nav_obj.get_symbol_at_position(missing, 1, 1)


def test_get_symbol_unsupported_language_raises(
    tmp_path: Path, patch_resolver
) -> None:
    """``for_file_path`` -> ``None`` -> :class:`UnsupportedLanguage`."""
    nav_obj, _, _ = _make_navigator(config=None)
    f = tmp_path / "weird.xyz"
    f.write_bytes(b"foo")
    with pytest.raises(UnsupportedLanguage) as exc_info:
        nav_obj.get_symbol_at_position(f, 1, 1)
    # grok passes the extension; the port falls back to "unknown" for empty ext.
    assert exc_info.value.ext in {"xyz", "unknown"}


def test_get_symbol_parser_unavailable_raises_parse_error(
    tmp_path: Path, patch_resolver
) -> None:
    """``_get_parser_and_query`` -> ``(None, None)`` -> :class:`ParseError`."""
    patch_resolver["parser"] = None  # parser unavailable
    nav_obj, _, _ = _make_navigator()
    f = tmp_path / "x.py"
    f.write_bytes(b"foo")
    with pytest.raises(ParseError):
        nav_obj.get_symbol_at_position(f, 1, 1)


def test_get_symbol_parse_yields_no_tree_raises_parse_error(
    tmp_path: Path, patch_resolver
) -> None:
    """``parser.parse`` -> ``None`` -> :class:`ParseError`."""
    patch_resolver["parser"] = _FakeParser(tree=None)  # parse returns None
    nav_obj, _, _ = _make_navigator()
    f = tmp_path / "x.py"
    f.write_bytes(b"foo")
    with pytest.raises(ParseError):
        nav_obj.get_symbol_at_position(f, 1, 1)


def test_get_symbol_cursor_on_non_identifier_raises_no_symbol(
    tmp_path: Path, patch_resolver
) -> None:
    """Cursor on a grammar keyword node -> :class:`NoSymbolAtPosition`."""
    # ``return`` is a grammar keyword, not an identifier kind -> hit-test None.
    root = _FakeNode("return_statement", start_point=(0, 0), end_point=(0, 6))
    patch_resolver["parser"] = _FakeParser(_FakeTree(root))
    nav_obj, _, _ = _make_navigator()
    f = tmp_path / "x.py"
    f.write_bytes(b"return")
    with pytest.raises(NoSymbolAtPosition):
        nav_obj.get_symbol_at_position(f, 1, 1)


def test_get_symbol_success_decodes_identifier_text(
    tmp_path: Path, patch_resolver
) -> None:
    """Hit an identifier leaf -> the UTF-8 decode of its byte span (grok L196-L203)."""
    # ``foo`` spans bytes [0,3); cursor at (1,1) -> hit-test (0,0) lands inside.
    leaf = _FakeNode(
        "identifier",
        start_point=(0, 0),
        end_point=(0, 3),
        start_byte=0,
        end_byte=3,
    )
    patch_resolver["parser"] = _FakeParser(_FakeTree(leaf))
    nav_obj, _, _ = _make_navigator()
    f = tmp_path / "x.py"
    f.write_bytes(b"foo")
    assert nav_obj.get_symbol_at_position(f, 1, 1) == "foo"


def test_get_symbol_1_indexed_to_0_indexed_offset(
    tmp_path: Path, patch_resolver
) -> None:
    """``row=2,col=4`` resolves to 0-indexed ``(1,3)`` (tree-sitter coordinates)."""
    # Identifier at row 1, cols [3,6); cursor (2,4) -> (1,3) inside the leaf.
    leaf = _FakeNode(
        "identifier",
        start_point=(1, 3),
        end_point=(1, 6),
        start_byte=0,
        end_byte=3,
    )
    patch_resolver["parser"] = _FakeParser(_FakeTree(leaf))
    nav_obj, _, _ = _make_navigator()
    f = tmp_path / "x.py"
    f.write_bytes(b"foo")
    assert nav_obj.get_symbol_at_position(f, 2, 4) == "foo"


# === Navigator.goto_definition / goto_references ========================


def test_goto_definition_resolves_symbol_and_maps_locations(
    tmp_path: Path, patch_resolver
) -> None:
    """Resolves the symbol then maps ``(path, line)`` defs to :class:`Location`."""
    leaf = _FakeNode(
        "identifier", start_point=(0, 0), end_point=(0, 3),
        start_byte=0, end_byte=3,
    )
    patch_resolver["parser"] = _FakeParser(_FakeTree(leaf))
    idx = _FakeIndex(definitions=[("src/lib.py", 42)])
    nav_obj = Navigator(
        _index=idx, _registry=_FakeRegistry(),
    )
    f = tmp_path / "x.py"
    f.write_bytes(b"foo")
    res = nav_obj.goto_definition(f, 1, 1)
    assert res.symbol == "foo"
    assert res.locations == [Location.new("src/lib.py", 42)]
    # context_file is the file's string form; registry threaded through.
    assert idx.def_calls == [("foo", str(f), nav_obj._registry)]


def test_goto_references_maps_locations_with_symbol(
    tmp_path: Path, patch_resolver
) -> None:
    """References carry the per-site spelling via :meth:`Location.with_symbol`."""
    leaf = _FakeNode(
        "identifier", start_point=(0, 0), end_point=(0, 3),
        start_byte=0, end_byte=3,
    )
    patch_resolver["parser"] = _FakeParser(_FakeTree(leaf))
    idx = _FakeIndex(references=[("alias", "a.py", 5), ("foo", "b.py", 9)])
    nav_obj = Navigator(_index=idx, _registry=_FakeRegistry())
    f = tmp_path / "x.py"
    f.write_bytes(b"foo")
    res = nav_obj.goto_references(f, 1, 1, include_definition=False)
    assert res.locations == [
        Location.with_symbol("a.py", 5, "alias"),
        Location.with_symbol("b.py", 9, "foo"),
    ]
    assert idx.def_calls == []  # include_definition=False -> no def lookup


def test_goto_references_include_definition_prepends_dedup(
    tmp_path: Path, patch_resolver
) -> None:
    """``include_definition=True`` prepends defs, de-duped by path+line (grok L272-L280)."""
    leaf = _FakeNode(
        "identifier", start_point=(0, 0), end_point=(0, 3),
        start_byte=0, end_byte=3,
    )
    patch_resolver["parser"] = _FakeParser(_FakeTree(leaf))
    # def at (c.py, 20); a reference already at (c.py, 20) -> dedup drops the dup.
    idx = _FakeIndex(
        definitions=[("c.py", 20), ("d.py", 30)],
        references=[("foo", "c.py", 20), ("foo", "e.py", 40)],
    )
    nav_obj = Navigator(_index=idx, _registry=_FakeRegistry())
    f = tmp_path / "x.py"
    f.write_bytes(b"foo")
    res = nav_obj.goto_references(f, 1, 1, include_definition=True)
    # (c.py,20) deduped against the reference; (d.py,30) prepended; refs follow.
    paths = [(loc.path, loc.line) for loc in res.locations]
    assert paths[0] == ("d.py", 30)  # prepended def (c.py,20 deduped away)
    assert ("c.py", 20) in paths  # the reference survives (not the dup def)
    assert paths.count(("c.py", 20)) == 1  # exactly one occurrence


# === Navigator.goto_*_by_name (no position parse) =======================


def test_goto_definition_by_name_no_raise_empty() -> None:
    """Name-based lookup returns empty locations, never raises (grok L287-L305)."""
    idx = _FakeIndex(definitions=[])
    nav_obj = Navigator(_index=idx, _registry=_FakeRegistry())
    res = nav_obj.goto_definition_by_name("absent", context_file="ctx.py")
    assert res.symbol == "absent"
    assert res.locations == []
    assert idx.def_calls == [("absent", "ctx.py", nav_obj._registry)]


def test_goto_definition_by_name_none_context() -> None:
    """``context_file=None`` threads through as ``None`` (grok ``Option<&Path>``)."""
    idx = _FakeIndex(definitions=[("lib.rs", 8)])
    nav_obj = Navigator(_index=idx, _registry=_FakeRegistry())
    res = nav_obj.goto_definition_by_name("foo")
    assert res.locations == [Location.new("lib.rs", 8)]
    assert idx.def_calls == [("foo", None, nav_obj._registry)]


def test_goto_references_by_name_no_raise_empty() -> None:
    """Name-based reference lookup never raises (grok L308-L343)."""
    idx = _FakeIndex(references=[])
    nav_obj = Navigator(_index=idx, _registry=_FakeRegistry())
    res = nav_obj.goto_references_by_name("absent", include_definition=False)
    assert res.symbol == "absent"
    assert res.locations == []


def test_goto_references_by_name_include_definition_prepends() -> None:
    """Name-based path also honors ``include_definition`` prepend+dedup."""
    idx = _FakeIndex(
        definitions=[("d.py", 1)],
        references=[("foo", "r.py", 2)],
    )
    nav_obj = Navigator(_index=idx, _registry=_FakeRegistry())
    res = nav_obj.goto_references_by_name("foo", include_definition=True)
    paths = [(loc.path, loc.line) for loc in res.locations]
    assert paths[0] == ("d.py", 1)  # def prepended
    assert paths[1] == ("r.py", 2)


# === __all__ + barrel (R308 boundary) ===================================


def test_all_exports_complete() -> None:
    """``__all__`` exposes the 4 grok crate-root symbols + 6 error variants + Navigator."""
    assert set(nav.__all__) == {
        "Location", "NavigationResult", "NavigationError",
        "FileNotFound", "PositionOutOfBounds", "NoSymbolAtPosition",
        "UnsupportedLanguage", "ParseError", "IoError", "Navigator",
    }


def test_navigation_symbols_not_yet_at_crate_root() -> None:
    """R308 boundary: the 4 navigation symbols are NOT yet in the crate-root barrel.

    grok ``lib.rs`` L94 ``pub use navigation::{...}`` re-exports them at the
    crate root (with ``navigation::Location`` shadowing ``types::Location``).
    The Python barrel reconciliation lands in R308; until then the navigation
    symbols stay leaf-module-only -- this assertion pins the pre-R308 state so
    R308's atomic switch is a visible, reviewed change.
    """
    for symbol in ("Navigator", "NavigationResult"):
        assert symbol not in xcg_root.__all__
