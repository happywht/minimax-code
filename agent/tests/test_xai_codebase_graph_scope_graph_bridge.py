"""Tests for the tree-sitter bridge free functions (R305e).

Ported from grok ``xai-codebase-graph/src/scope_graph/graph.rs``:

* ``Range::for_tree_node`` (types/range.rs L170-L189) -> :func:`range_for_node`
* ``scope_graph_from_definitions_query`` (graph.rs L485-L556)
* ``extract_symbols_fast`` (graph.rs L565-L633)
* ``build_scope_graph`` (mod.rs L30-L38)

The bridge binds to the ``tree_sitter`` runtime via duck typing (node's
``start_byte`` / ``end_byte`` / ``start_point`` / ``end_point``; query's
``capture_names``; cursor's ``matches``). These tests use lightweight fakes
that satisfy the contract, mirroring the R305c ``_FakeGraph`` style -- no real
tree-sitter grammar is parsed, which keeps the tests hermetic and fast.

The cursor factory :func:`bridge._new_query_cursor` is monkeypatched to return
a fake cursor, bypassing the lazy ``tree_sitter`` import. The
``RuntimeError``-when-absent path is covered directly by neutering
``sys.modules['tree_sitter']``.
"""

from __future__ import annotations

import sys

import pytest

from minimax_code.xai_codebase_graph.languages.types import TSLanguageConfig
from minimax_code.xai_codebase_graph.scope_graph import bridge
from minimax_code.xai_codebase_graph.scope_graph.bridge import (
    build_scope_graph,
    extract_symbols_fast,
    range_for_node,
    scope_graph_from_definitions_query,
)
from minimax_code.xai_codebase_graph.scope_graph.graph import ScopeGraphResult
from minimax_code.xai_codebase_graph.types.range import Position, Range

# === fakes =================================================================


class _FakePoint:
    """Stand-in for ``tree_sitter.Point`` (duck-typed ``.row`` / ``.column``)."""

    def __init__(self, row: int, column: int) -> None:
        self.row = row
        self.column = column


class _FakeNode:
    """Stand-in for ``tree_sitter.Node``.

    Exposes the six byte/point extents the bridge reads directly (the same
    data ``node.range()`` returns in grok -- reached without the binding
    indirection).
    """

    def __init__(
        self,
        start_byte: int,
        end_byte: int,
        start_point: _FakePoint,
        end_point: _FakePoint,
    ) -> None:
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.start_point = start_point
        self.end_point = end_point


class _FakeCapture:
    """Stand-in for ``tree_sitter.QueryCapture`` (duck-typed ``.index`` / ``.node``)."""

    def __init__(self, index: int, node: _FakeNode) -> None:
        self.index = index
        self.node = node


class _FakeMatch:
    """Stand-in for ``tree_sitter.QueryMatch`` (duck-typed ``.captures``)."""

    def __init__(self, captures: list[_FakeCapture]) -> None:
        self.captures = captures


class _FakeCursor:
    """Stand-in for ``tree_sitter.QueryCursor`` (duck-typed ``.matches``)."""

    def __init__(self, matches: list[_FakeMatch]) -> None:
        self._matches = matches

    def matches(self, query, root_node, src):  # noqa: ANN001 - duck-typed grok surface
        """Yield the pre-built matches (mirrors ``QueryCursor::matches``)."""
        return iter(self._matches)


class _FakeQuery:
    """Stand-in for ``tree_sitter.Query`` -- ``capture_names`` as a ``list`` property."""

    def __init__(self, capture_names: list[str]) -> None:
        self.capture_names = capture_names


class _FakeQueryCallable:
    """Stand-in for ``tree_sitter.Query`` -- ``capture_names`` as a callable (old binding)."""

    def __init__(self, capture_names: list[str]) -> None:
        self._names = capture_names

    def capture_names(self) -> list[str]:  # noqa: D401 - duck-typed grok surface
        return self._names


def _node(start: int, end: int, row: int, col0: int, col1: int) -> _FakeNode:
    """Build a fake node covering bytes ``[start, end)`` on a single line."""
    return _FakeNode(start, end, _FakePoint(row, col0), _FakePoint(row, col1))


def _lang() -> TSLanguageConfig:
    """Build a minimal Python-ish config (no grammar -- bridge never calls ``language()``).

    Namespace row 0 maps ``function`` -> ``SymbolId(0, 0)`` and ``class`` ->
    ``SymbolId(0, 1)``, so ``symbol_id_of`` resolves the dotted-capture symbol.
    """
    return TSLanguageConfig.new(
        language_ids=["python"],
        file_extensions=[".py"],
        namespaces=[["function", "class"]],
        file_definition_queries="(dummy)",
    )


def _patch_cursor(monkeypatch, matches: list[_FakeMatch]) -> _FakeQuery:
    """Inject a fake cursor into the bridge + return a query wired to the capture names.

    The capture-name table is the union of indices the matches reference; the
    tests below each lay out a specific ordering so the dotted-name /
    precomputed-index classification is exercised.
    """
    names = [
        "alias.original",  # idx 0
        "alias.name",  # idx 1
        "name.definition.function",  # idx 2
        "name.reference.function",  # idx 3
        "name.comment",  # idx 4 -- not def/ref/alias, must be ignored
    ]
    query = _FakeQuery(names)
    cursor = _FakeCursor(matches)
    monkeypatch.setattr(bridge, "_new_query_cursor", lambda: cursor)
    return query


# A 3-line Python-ish source with known byte offsets (see comments below).
#   b"import numpy as np\n"   -> 19 bytes  (numpy @ 7..12, np @ 16..18)
#   b"def foo(): pass\n"      -> 16 bytes  (foo def @ 23..26)
#   b"foo()\n"                -> 6 bytes   (foo ref @ 35..38)
_SRC = b"import numpy as np\ndef foo(): pass\nfoo()\n"


# === range_for_node ========================================================


def test_range_for_node_assembles_six_extents_into_positions() -> None:
    """``range_for_node`` reads the node's 6 extents and builds start/end Positions."""
    node = _node(23, 26, 1, 4, 7)
    range_ = range_for_node(node)
    assert isinstance(range_, Range)
    assert range_.start_position == Position(1, 4, 23)
    assert range_.end_position == Position(1, 7, 26)


def test_range_for_node_round_trips_byte_offsets() -> None:
    """The byte offsets survive into the Positions (used for src slicing downstream)."""
    node = _FakeNode(35, 38, _FakePoint(2, 0), _FakePoint(2, 3))
    range_ = range_for_node(node)
    assert range_.start_byte() == 35
    assert range_.end_byte() == 38


# === _capture_names ========================================================


def test_capture_names_supports_property_form() -> None:
    """``capture_names`` exposed as a ``list`` attribute is returned as-is."""
    assert bridge._capture_names(_FakeQuery(["a", "b"])) == ["a", "b"]


def test_capture_names_supports_callable_form() -> None:
    """``capture_names`` exposed as a bound method is invoked (binding-version agnostic)."""
    assert bridge._capture_names(_FakeQueryCallable(["a", "b"])) == ["a", "b"]


# === _new_query_cursor =====================================================


def test_new_query_cursor_raises_runtime_error_when_tree_sitter_absent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A missing ``tree_sitter`` package surfaces as ``RuntimeError`` (mirrors R303)."""
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    with pytest.raises(RuntimeError, match="tree_sitter"):
        bridge._new_query_cursor()


# === scope_graph_from_definitions_query ====================================


def test_scope_graph_from_definitions_query_classifies_all_four_capture_types(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """def / ref / alias captures are classified; unknown captures are ignored."""
    numpy_node = _node(7, 12, 0, 7, 12)  # "numpy"
    np_node = _node(16, 18, 0, 16, 18)  # "np"
    foo_def_node = _node(23, 26, 1, 4, 7)  # "foo" definition
    foo_ref_node = _node(35, 38, 2, 0, 3)  # "foo" reference

    matches = [
        _FakeMatch([_FakeCapture(0, numpy_node), _FakeCapture(1, np_node)]),  # alias pair
        _FakeMatch([_FakeCapture(2, foo_def_node)]),  # definition
        _FakeMatch([_FakeCapture(3, foo_ref_node)]),  # reference
        _FakeMatch([_FakeCapture(4, foo_def_node)]),  # name.comment -> ignored
    ]
    query = _patch_cursor(monkeypatch, matches)
    root_node = _FakeNode(0, len(_SRC), _FakePoint(0, 0), _FakePoint(3, 0))

    graph, alias_pairs = scope_graph_from_definitions_query(query, root_node, _SRC, _lang())

    # alias pair order is (alias_name, original_name).
    assert alias_pairs == [("np", "numpy")]
    # one definition + one reference inserted, decoded from src.
    assert graph.get_definitions(_SRC) == [("foo", range_for_node(foo_def_node))]
    assert graph.get_references(_SRC) == [("foo", range_for_node(foo_ref_node))]
    # graph: root scope (1) + 1 def + 1 ref == 3 nodes; name.comment dropped.
    assert len(graph.graph) == 3
    assert graph.lang == "python"


def test_scope_graph_from_definitions_query_root_range_from_root_node(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The scope graph's root node carries the root_node's range."""
    matches: list[_FakeMatch] = []
    query = _patch_cursor(monkeypatch, matches)
    root_node = _FakeNode(0, 40, _FakePoint(0, 0), _FakePoint(3, 0))

    graph, _alias_pairs = scope_graph_from_definitions_query(query, root_node, _SRC, _lang())

    root_kind = graph.graph[graph.root_idx]
    # NodeKind.range() on a SCOPE node returns the scope's range (root_node here).
    assert root_kind.range() == range_for_node(root_node)


def test_scope_graph_from_definitions_query_unknown_capture_ignored(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A dotted name that matches none of the 4 patterns is dropped silently."""
    matches = [_FakeMatch([_FakeCapture(4, _node(0, 3, 0, 0, 3))])]  # name.comment
    query = _patch_cursor(monkeypatch, matches)
    root_node = _FakeNode(0, len(_SRC), _FakePoint(0, 0), _FakePoint(3, 0))

    graph, alias_pairs = scope_graph_from_definitions_query(query, root_node, _SRC, _lang())

    assert alias_pairs == []
    assert graph.get_definitions(_SRC) == []
    assert graph.get_references(_SRC) == []
    assert len(graph.graph) == 1  # root scope only


def test_scope_graph_from_definitions_query_alias_requires_both_halves(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An ``alias.original`` without a matching ``alias.name`` (or vice versa) yields no pair."""
    numpy_node = _node(7, 12, 0, 7, 12)  # "numpy"
    matches = [
        _FakeMatch([_FakeCapture(0, numpy_node)]),  # only alias.original, no alias.name
        _FakeMatch([_FakeCapture(1, _node(16, 18, 0, 16, 18))]),  # only alias.name
    ]
    query = _patch_cursor(monkeypatch, matches)
    root_node = _FakeNode(0, len(_SRC), _FakePoint(0, 0), _FakePoint(3, 0))

    _graph, alias_pairs = scope_graph_from_definitions_query(query, root_node, _SRC, _lang())

    assert alias_pairs == []


def test_scope_graph_from_definitions_query_symbol_id_resolution(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``symbol_id_of`` resolves the dotted-capture symbol to a ``SymbolId`` on defs/refs."""
    foo_def_node = _node(23, 26, 1, 4, 7)
    matches = [_FakeMatch([_FakeCapture(2, foo_def_node)])]  # name.definition.function
    query = _patch_cursor(monkeypatch, matches)
    root_node = _FakeNode(0, len(_SRC), _FakePoint(0, 0), _FakePoint(3, 0))

    graph, _alias_pairs = scope_graph_from_definitions_query(query, root_node, _SRC, _lang())

    # function -> SymbolId(namespace=0, symbol=0); the def node carries it.
    def_node_idx = next(
        idx
        for idx in graph.graph.node_indices()
        if idx != graph.root_idx and graph.is_definition(idx)
    )
    def_kind = graph.graph[def_node_idx]
    assert def_kind.definition.symbol_id is not None
    assert def_kind.definition.symbol_id.namespace_idx == 0
    assert def_kind.definition.symbol_id.symbol_idx == 0


# === extract_symbols_fast ==================================================


def test_extract_symbols_fast_classifies_all_three_paths(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """definitions / references / alias_pairs collected; unknown captures ignored."""
    numpy_node = _node(7, 12, 0, 7, 12)
    np_node = _node(16, 18, 0, 16, 18)
    foo_def_node = _node(23, 26, 1, 4, 7)
    foo_ref_node = _node(35, 38, 2, 0, 3)

    matches = [
        _FakeMatch([_FakeCapture(0, numpy_node), _FakeCapture(1, np_node)]),
        _FakeMatch([_FakeCapture(2, foo_def_node)]),
        _FakeMatch([_FakeCapture(3, foo_ref_node)]),
        _FakeMatch([_FakeCapture(4, foo_def_node)]),  # name.comment -> ignored
    ]
    query = _patch_cursor(monkeypatch, matches)
    root_node = _FakeNode(0, len(_SRC), _FakePoint(0, 0), _FakePoint(3, 0))

    definitions, references, alias_pairs = extract_symbols_fast(query, root_node, _SRC, _lang())

    assert definitions == [("foo", range_for_node(foo_def_node))]
    assert references == [("foo", range_for_node(foo_ref_node))]
    assert alias_pairs == [("np", "numpy")]


def test_extract_symbols_fast_unknown_index_ignored(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A capture whose index is outside the def/ref/alias slots is dropped."""
    matches = [_FakeMatch([_FakeCapture(4, _node(0, 3, 0, 0, 3))])]  # name.comment
    query = _patch_cursor(monkeypatch, matches)
    root_node = _FakeNode(0, len(_SRC), _FakePoint(0, 0), _FakePoint(3, 0))

    definitions, references, alias_pairs = extract_symbols_fast(query, root_node, _SRC, _lang())

    assert definitions == []
    assert references == []
    assert alias_pairs == []


def test_extract_symbols_fast_alias_order_is_alias_then_original(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Alias pair order is ``(alias_name, original_name)`` (matches grok L628)."""
    numpy_node = _node(7, 12, 0, 7, 12)  # "numpy" (original)
    np_node = _node(16, 18, 0, 16, 18)  # "np" (alias)
    matches = [_FakeMatch([_FakeCapture(0, numpy_node), _FakeCapture(1, np_node)])]
    query = _patch_cursor(monkeypatch, matches)
    root_node = _FakeNode(0, len(_SRC), _FakePoint(0, 0), _FakePoint(3, 0))

    _defs, _refs, alias_pairs = extract_symbols_fast(query, root_node, _SRC, _lang())

    assert alias_pairs == [("np", "numpy")]


def test_extract_symbols_fast_alias_requires_both_halves(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An ``alias.original`` without a matching ``alias.name`` yields no pair."""
    matches = [_FakeMatch([_FakeCapture(0, _node(7, 12, 0, 7, 12))])]  # only original
    query = _patch_cursor(monkeypatch, matches)
    root_node = _FakeNode(0, len(_SRC), _FakePoint(0, 0), _FakePoint(3, 0))

    _defs, _refs, alias_pairs = extract_symbols_fast(query, root_node, _SRC, _lang())

    assert alias_pairs == []


def test_extract_symbols_fast_supports_callable_capture_names(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The fast path also tolerates ``capture_names`` as a callable."""
    foo_def_node = _node(23, 26, 1, 4, 7)
    cursor = _FakeCursor([_FakeMatch([_FakeCapture(2, foo_def_node)])])
    monkeypatch.setattr(bridge, "_new_query_cursor", lambda: cursor)
    query = _FakeQueryCallable(
        [
            "alias.original",
            "alias.name",
            "name.definition.function",
            "name.reference.function",
            "name.comment",
        ]
    )
    root_node = _FakeNode(0, len(_SRC), _FakePoint(0, 0), _FakePoint(3, 0))

    definitions, _references, _alias_pairs = extract_symbols_fast(query, root_node, _SRC, _lang())

    assert definitions == [("foo", range_for_node(foo_def_node))]


# === build_scope_graph =====================================================


def test_build_scope_graph_wraps_result(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``build_scope_graph`` delegates + wraps the pair in a ``ScopeGraphResult``."""
    numpy_node = _node(7, 12, 0, 7, 12)
    np_node = _node(16, 18, 0, 16, 18)
    foo_def_node = _node(23, 26, 1, 4, 7)
    matches = [
        _FakeMatch([_FakeCapture(0, numpy_node), _FakeCapture(1, np_node)]),
        _FakeMatch([_FakeCapture(2, foo_def_node)]),
    ]
    query = _patch_cursor(monkeypatch, matches)
    root_node = _FakeNode(0, len(_SRC), _FakePoint(0, 0), _FakePoint(3, 0))

    result = build_scope_graph(query, root_node, _SRC, _lang())

    assert isinstance(result, ScopeGraphResult)
    assert result.graph.get_definitions(_SRC) == [("foo", range_for_node(foo_def_node))]
    assert result.aliases == [("np", "numpy")]


def test_build_scope_graph_empty_matches(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An empty match stream yields an empty graph + empty aliases."""
    query = _patch_cursor(monkeypatch, [])
    root_node = _FakeNode(0, len(_SRC), _FakePoint(0, 0), _FakePoint(3, 0))

    result = build_scope_graph(query, root_node, _SRC, _lang())

    assert isinstance(result, ScopeGraphResult)
    assert result.graph.get_definitions(_SRC) == []
    assert result.aliases == []
    assert len(result.graph.graph) == 1  # root scope only
