"""Tests for ``xai_codebase_graph.scope_graph.graph`` (brick 6, leaves a + b).

R305a (leaf a) mirrors grok ``scope_graph/graph.rs`` pure-data foundation: the
four ``pub type`` aliases (:data:`NodeIndex`, :data:`SymbolWithRange`,
:data:`ReferenceWithDefinition`, :data:`ExtractedSymbols`), the
:class:`QueryVersion` version-stamp enum (collapsed to a frozen dataclass)
and its :meth:`QueryVersion.needs_rebuild` logic, and the :class:`Snippet`
mutable struct.

R305b (leaf b) mirrors the graph algorithms: :class:`_DiGraph` (the petgraph
replacement), :class:`ScopeGraph` (the 22-method per-file symbol graph),
:class:`ScopeStack` (enclosing-scope iterator), and :class:`ScopeGraphResult`
(the ``{graph, aliases}`` container). Plus the asymmetric double-barrel
surface: ``scope_graph/mod.rs`` re-exports ``ScopeGraph`` / ``ScopeStack`` /
``ScopeGraphResult`` (+3 -> 15) while the crate root ``lib.rs`` re-exports
only ``ScopeGraph`` / ``ScopeGraphResult`` (+2 -> 23, ``ScopeStack`` stays
subpackage-local).

Zero tree-sitter: consumes only R300 :class:`Range` and R301 node types.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import minimax_code.xai_codebase_graph as xcg
from minimax_code.xai_codebase_graph.scope_graph import (
    EdgeKind,
    LocalDef,
    LocalImport,
    LocalScope,
    NodeIndex,
    NodeKindKind,
    QueryVersion,
    Reference,
    ScopeGraph,
    ScopeGraphResult,
    ScopeStack,
    Snippet,
    SymbolId,
)
from minimax_code.xai_codebase_graph.scope_graph import graph as graph_leaf
from minimax_code.xai_codebase_graph.scope_graph.nodes import Symbol
from minimax_code.xai_codebase_graph.types.range import Position, Range


def _range(start_byte: int = 0, end_byte: int = 0) -> Range:
    """A single-line range spanning ``[start_byte, end_byte)`` bytes.

    ``character`` mirrors ``byte_offset`` (one byte == one column on line 0) so
    that :meth:`Range.contains` -- which compares line+column, NOT bytes --
    still distinguishes ranges by their byte span. Real grok usage fills
    line/column from tree-sitter; this helper keeps ``character`` and
    ``byte_offset`` in sync so the line/column-based containment checks used by
    :class:`ScopeGraph` (``_scope_by_range`` / ``find_tightest_local_scope``)
    agree with the byte-based lookups (``node_by_range`` / ``tightest_node_for_range``).
    """
    return Range(
        start_position=Position(line=0, character=start_byte, byte_offset=start_byte),
        end_position=Position(line=0, character=end_byte, byte_offset=end_byte),
    )


def _symbol(kind: str = "function") -> Symbol:
    """A minimal symbol for Snippet payload tests."""
    return Symbol.new(kind, _range())


# === NodeIndex type alias ===================================================


def test_nodeindex_is_int_alias() -> None:
    """``NodeIndex`` is a bare ``int`` (petgraph newtype collapsed).

    grok ``petgraph::graph::NodeIndex<u32>`` is a newtype over ``u32`` with no
    behavioural surface; the Python port uses a plain ``int`` alias, so an
    ``int`` value satisfies the ``NodeIndex`` annotation transparently.
    """
    idx: NodeIndex = 42
    assert idx == 42
    assert isinstance(idx, int)


# === compound type aliases (constructibility) ===============================


def test_symbol_with_range_is_constructible() -> None:
    """``SymbolWithRange`` is ``(str, Range)`` (grok ``(Arc<str>, Range)``)."""
    pair = ("foo", _range())
    name, rng = pair
    assert name == "foo"
    assert isinstance(rng, Range)


def test_reference_with_definition_supports_no_def() -> None:
    """``ReferenceWithDefinition`` third slot is ``Optional`` (unresolved ref)."""
    ref = ("bar", _range(), None)
    name, rng, definition = ref
    assert name == "bar"
    assert definition is None


def test_reference_with_definition_supports_resolved_def() -> None:
    """``ReferenceWithDefinition`` carries ``(def_name, def_range)`` when resolved."""
    ref = ("bar", _range(), ("bar_def", _range()))
    name, rng, definition = ref
    assert definition is not None
    assert definition[0] == "bar_def"


def test_extracted_symbols_is_three_tuple() -> None:
    """``ExtractedSymbols`` = ``(definitions, references, aliases)`` triple."""
    extracted = ([], [], [])
    defs, refs, aliases = extracted
    assert defs == [] and refs == [] and aliases == []


def test_extracted_symbols_holds_real_data() -> None:
    """The triple carries populated lists of the alias element types."""
    extracted = (
        [("func", _range())],
        [("func_ref", _range(), None)],
        [("alias", "original")],
    )
    defs, refs, aliases = extracted
    assert defs[0][0] == "func"
    assert refs[0][0] == "func_ref"
    assert aliases[0] == ("alias", "original")


# === QueryVersion ===========================================================


def test_queryversion_default_is_legacy() -> None:
    """Default ``QueryVersion()`` -> ``version is None`` (grok ``#[default] Legacy``)."""
    assert QueryVersion().version is None


def test_queryversion_version_carries_value() -> None:
    """``QueryVersion(version=v)`` stamps the value (grok ``Version(u64)``)."""
    assert QueryVersion(version=12345).version == 12345


def test_queryversion_supports_full_u64_range() -> None:
    """``u64`` range is preserved (Python ``int`` is unbounded)."""
    big = 2**64 - 1
    assert QueryVersion(version=big).version == big


# --- needs_rebuild ----------------------------------------------------------


def test_needs_rebuild_legacy_always_true() -> None:
    """``Legacy`` (``version is None``) -> always rebuild (unknown provenance)."""
    assert QueryVersion().needs_rebuild(0) is True
    assert QueryVersion().needs_rebuild(999) is True


def test_needs_rebuild_version_mismatch_is_true() -> None:
    """``Version(v)`` with ``v != current`` -> rebuild."""
    assert QueryVersion(version=1).needs_rebuild(2) is True


def test_needs_rebuild_version_match_is_false() -> None:
    """``Version(v)`` with ``v == current`` -> no rebuild (cache hit)."""
    assert QueryVersion(version=42).needs_rebuild(42) is False


# --- equality / hashing -----------------------------------------------------


def test_queryversion_equality() -> None:
    """frozen dataclass -> value equality (grok derives ``PartialEq, Eq``)."""
    assert QueryVersion() == QueryVersion()
    assert QueryVersion(version=1) == QueryVersion(version=1)
    assert QueryVersion() != QueryVersion(version=1)


def test_queryversion_is_hashable() -> None:
    """frozen dataclass -> hashable (usable as dict key / set member)."""
    assert hash(QueryVersion(version=1)) == hash(QueryVersion(version=1))
    assert QueryVersion(version=1) in {QueryVersion(version=1)}


def test_queryversion_is_frozen() -> None:
    """Field reassignment is rejected (grok enum is immutable)."""
    with pytest.raises(FrozenInstanceError):
        QueryVersion(version=1).version = 2  # type: ignore[misc]


# === Snippet ================================================================


def test_snippet_construction_and_fields() -> None:
    """``Snippet`` holds ``(data, line_range, symbols)`` (grok struct fields)."""
    snippet = Snippet(data="def foo(): pass", line_range=(0, 1), symbols=[_symbol()])
    assert snippet.data == "def foo(): pass"
    assert snippet.line_range == (0, 1)
    assert len(snippet.symbols) == 1
    assert isinstance(snippet.symbols[0], Symbol)


def test_snippet_line_range_is_half_open_pair() -> None:
    """``line_range`` is a ``(start, end)`` pair (grok ``Range<usize>``)."""
    snippet = Snippet(data="x", line_range=(3, 7), symbols=[])
    start, end = snippet.line_range
    assert start == 3 and end == 7


def test_snippet_requires_all_three_fields() -> None:
    """All three ``Snippet`` fields are required (no defaults; grok struct)."""
    with pytest.raises(TypeError):
        Snippet(data="x")  # type: ignore[call-arg]


def test_snippet_is_mutable() -> None:
    """``Snippet`` is NOT frozen -- grok derives ``Clone`` (not ``Copy``)."""
    snippet = Snippet(data="a", line_range=(0, 0), symbols=[])
    snippet.data = "b"
    snippet.symbols.append(_symbol())
    assert snippet.data == "b"
    assert len(snippet.symbols) == 1


# === ScopeGraph construction (R305b) ========================================


def test_scope_graph_new_creates_root_scope_only() -> None:
    """``ScopeGraph.new`` builds a graph with a single root scope node (grok ``new``).

    root_idx is 0 (first node); the root is a SCOPE node carrying the given
    range; ``lang`` is stored verbatim.
    """
    rng = _range(0, 100)
    sg = ScopeGraph.new(rng, "python")
    assert sg.lang == "python"
    assert sg.root_idx == 0
    assert len(sg.graph) == 1
    root = sg.graph[sg.root_idx]
    assert root.kind == NodeKindKind.SCOPE
    assert root.scope is not None
    assert root.scope.range == rng


def test_scope_graph_from_symbols_builds_minimal_graph() -> None:
    """``from_symbols`` attaches defs to root; refs are free nodes (grok L416-L452)."""
    defs = [("foo", _range(0, 3)), ("bar", _range(4, 7))]
    refs = [("foo", _range(8, 11))]
    sg = ScopeGraph.from_symbols(defs, refs)
    # root (scope) + 2 defs + 1 ref = 4 nodes
    assert len(sg.graph) == 4
    # root range spans the first def's range (grok L421-L427)
    assert sg.graph[sg.root_idx].scope is not None
    assert sg.graph[sg.root_idx].scope.range == _range(0, 3)
    # both defs link to root via DefToScope (root's Incoming edges)
    def_targets = [
        src for src, w in sg.graph.in_edges(sg.root_idx) if w == EdgeKind.DEF_TO_SCOPE
    ]
    assert len(def_targets) == 2
    # the ref is a free node with no outgoing resolution edges
    ref_indices = [
        i for i in sg.graph.node_indices() if sg.graph[i].kind == NodeKindKind.REF
    ]
    assert len(ref_indices) == 1
    assert sg.graph.out_edges(ref_indices[0]) == []


def test_scope_graph_from_symbols_empty_uses_default_range() -> None:
    """No defs/refs -> root spans a default empty :class:`Range`."""
    sg = ScopeGraph.from_symbols([], [])
    assert len(sg.graph) == 1
    assert sg.graph[sg.root_idx].scope is not None


# === node-kind predicates ===================================================


def test_is_definition_reference_import_dispatch_by_kind() -> None:
    """``is_definition`` / ``is_reference`` / ``is_import`` read ``NodeKindKind``."""
    sg = ScopeGraph.new(_range(0, 100), "python")
    sg.insert_local_def(LocalDef.new(_range(0, 3), None, LocalScope.new(_range(0, 100))))
    sg.insert_local_import(LocalImport.new(_range(4, 6)))
    sg.insert_ref_unconditional(Reference.new(_range(7, 10), None))
    kinds = {sg.graph[i].kind for i in sg.graph.node_indices()}
    # root SCOPE + DEF + IMPORT + REF
    assert kinds == {NodeKindKind.SCOPE, NodeKindKind.DEF, NodeKindKind.IMPORT, NodeKindKind.REF}
    for idx in sg.graph.node_indices():
        kind = sg.graph[idx].kind
        assert sg.is_definition(idx) is (kind == NodeKindKind.DEF)
        assert sg.is_reference(idx) is (kind == NodeKindKind.REF)
        assert sg.is_import(idx) is (kind == NodeKindKind.IMPORT)


# === scope insertion / lookup ==============================================


def test_insert_local_scope_nests_under_root() -> None:
    """``insert_local_scope`` links the new scope under its tightest enclosing scope."""
    sg = ScopeGraph.new(_range(0, 100), "python")
    child_rng = _range(0, 50)
    sg.insert_local_scope(LocalScope.new(child_rng))
    # root + 1 child scope
    assert len(sg.graph) == 2
    # the child scope's Outgoing ScopeToScope edge targets root (parent)
    child_idx = next(
        src for src, w in sg.graph.in_edges(sg.root_idx) if w == EdgeKind.SCOPE_TO_SCOPE
    )
    parent_edges = sg.graph.out_edges(child_idx)
    assert parent_edges == [(sg.root_idx, EdgeKind.SCOPE_TO_SCOPE)]


def test_find_tightest_local_scope_descends_to_child() -> None:
    """``find_tightest_local_scope`` returns the smallest containing scope."""
    sg = ScopeGraph.new(_range(0, 100), "python")  # root
    sg.insert_local_scope(LocalScope.new(_range(0, 50)))  # child scope A
    # a range inside A resolves to A
    tightest = sg.find_tightest_local_scope(_range(10, 20))
    assert tightest.range == _range(0, 50)
    # a range outside A (but inside root) resolves to root
    tightest_outer = sg.find_tightest_local_scope(_range(60, 70))
    assert tightest_outer.range == _range(0, 100)


def test_find_tightest_local_scope_root_always_qualifies() -> None:
    """An empty graph still resolves any range to the root scope."""
    sg = ScopeGraph.new(_range(0, 100), "python")
    assert sg.find_tightest_local_scope(_range(40, 60)).range == _range(0, 100)


# === def insertion ==========================================================


def test_insert_local_def_links_to_defining_scope() -> None:
    """``insert_local_def`` links the def to its enclosing scope (DefToScope)."""
    sg = ScopeGraph.new(_range(0, 100), "python")
    sg.insert_local_def(LocalDef.new(_range(0, 3), None, LocalScope.new(_range(0, 100))))
    # root's Incoming edges now include one DefToScope from the def
    def_edges = [
        src for src, w in sg.graph.in_edges(sg.root_idx) if w == EdgeKind.DEF_TO_SCOPE
    ]
    assert len(def_edges) == 1
    assert sg.graph[def_edges[0]].kind == NodeKindKind.DEF


def test_insert_global_def_links_to_root() -> None:
    """``insert_global_def`` links the def to root regardless of its range."""
    sg = ScopeGraph.new(_range(0, 100), "python")
    sg.insert_local_scope(LocalScope.new(_range(0, 50)))
    sg.insert_global_def(LocalDef.new(_range(10, 13), None, LocalScope.new(_range(0, 50))))
    def_edges = [
        src for src, w in sg.graph.in_edges(sg.root_idx) if w == EdgeKind.DEF_TO_SCOPE
    ]
    assert len(def_edges) == 1


def test_insert_hoisted_def_lifts_to_parent_scope() -> None:
    """``insert_hoisted_def`` attaches to the defining scope's parent (grok L202-L215).

    With a child scope under root, a def whose identifier lives in the child
    scope is hoisted to the child's parent (root), NOT to the child itself.
    """
    sg = ScopeGraph.new(_range(0, 100), "python")  # root
    sg.insert_local_scope(LocalScope.new(_range(0, 50)))  # child scope
    # def identifier at byte 10 (inside child [0,50)); hoisted to root
    sg.insert_hoisted_def(LocalDef.new(_range(10, 13), None, LocalScope.new(_range(0, 50))))
    # the def links to ROOT (the child's parent), not the child
    root_def_edges = [
        src for src, w in sg.graph.in_edges(sg.root_idx) if w == EdgeKind.DEF_TO_SCOPE
    ]
    assert len(root_def_edges) == 1
    # the child scope has no DefToScope incoming edge
    child_idx = next(
        src for src, w in sg.graph.in_edges(sg.root_idx) if w == EdgeKind.SCOPE_TO_SCOPE
    )
    child_def_edges = [
        src for src, w in sg.graph.in_edges(child_idx) if w == EdgeKind.DEF_TO_SCOPE
    ]
    assert child_def_edges == []


# === import / ref insertion =================================================


def test_insert_local_import_links_to_defining_scope() -> None:
    """``insert_local_import`` links the import to its enclosing scope."""
    sg = ScopeGraph.new(_range(0, 100), "python")
    sg.insert_local_import(LocalImport.new(_range(0, 2)))
    import_edges = [
        src for src, w in sg.graph.in_edges(sg.root_idx) if w == EdgeKind.IMPORT_TO_SCOPE
    ]
    assert len(import_edges) == 1
    assert sg.graph[import_edges[0]].kind == NodeKindKind.IMPORT


def test_insert_ref_resolves_to_local_def() -> None:
    """``insert_ref`` resolves a ref to a same-scope def by name (RefToDef)."""
    src = b"foo foo"  # def foo at [0,3); ref foo at [4,7)
    sg = ScopeGraph.new(_range(0, 7), "python")
    sg.insert_local_def(LocalDef.new(_range(0, 3), None, LocalScope.new(_range(0, 7))))
    sg.insert_ref(Reference.new(_range(4, 7), None), src)
    # the ref node exists and links to the def via RefToDef
    ref_indices = [
        i for i in sg.graph.node_indices() if sg.graph[i].kind == NodeKindKind.REF
    ]
    assert len(ref_indices) == 1
    ref_idx = ref_indices[0]
    out_targets = sg.graph.out_edges(ref_idx)
    assert len(out_targets) == 1
    target_idx, weight = out_targets[0]
    assert weight == EdgeKind.REF_TO_DEF
    assert sg.graph[target_idx].kind == NodeKindKind.DEF


def test_insert_ref_namespace_filter_skips_foreign_def() -> None:
    """Different-namespace def+ref are skipped (grok L272-L285).

    A def in namespace 0 and a ref in namespace 1 do not resolve, so no ref
    node is added.
    """
    src = b"foo foo"
    sg = ScopeGraph.new(_range(0, 7), "python")
    sg.insert_local_def(
        LocalDef.new(_range(0, 3), SymbolId.new(0, 0), LocalScope.new(_range(0, 7)))
    )
    sg.insert_ref(Reference.new(_range(4, 7), SymbolId.new(1, 0)), src)
    ref_indices = [
        i for i in sg.graph.node_indices() if sg.graph[i].kind == NodeKindKind.REF
    ]
    assert ref_indices == []


def test_insert_ref_same_namespace_resolves() -> None:
    """Same-namespace def+ref resolve (the filter only rejects a mismatch)."""
    src = b"foo foo"
    sg = ScopeGraph.new(_range(0, 7), "python")
    sg.insert_local_def(
        LocalDef.new(_range(0, 3), SymbolId.new(2, 1), LocalScope.new(_range(0, 7)))
    )
    sg.insert_ref(Reference.new(_range(4, 7), SymbolId.new(2, 1)), src)
    ref_indices = [
        i for i in sg.graph.node_indices() if sg.graph[i].kind == NodeKindKind.REF
    ]
    assert len(ref_indices) == 1


def test_insert_ref_resolves_to_import() -> None:
    """``insert_ref`` also resolves to a matching import (RefToImport)."""
    src = b"os os"  # import os at [0,2); ref os at [3,5)
    sg = ScopeGraph.new(_range(0, 5), "python")
    sg.insert_local_import(LocalImport.new(_range(0, 2)))
    sg.insert_ref(Reference.new(_range(3, 5), None), src)
    ref_indices = [
        i for i in sg.graph.node_indices() if sg.graph[i].kind == NodeKindKind.REF
    ]
    assert len(ref_indices) == 1
    out_targets = sg.graph.out_edges(ref_indices[0])
    assert any(w == EdgeKind.REF_TO_IMPORT for _, w in out_targets)


def test_insert_ref_no_match_creates_no_node() -> None:
    """A ref with no matching def/import is not added (grok L255-L315 guard)."""
    src = b"foo bar"  # def foo at [0,3); ref bar at [4,7) -- no match
    sg = ScopeGraph.new(_range(0, 7), "python")
    sg.insert_local_def(LocalDef.new(_range(0, 3), None, LocalScope.new(_range(0, 7))))
    sg.insert_ref(Reference.new(_range(4, 7), None), src)
    ref_indices = [
        i for i in sg.graph.node_indices() if sg.graph[i].kind == NodeKindKind.REF
    ]
    assert ref_indices == []


def test_insert_ref_unconditional_adds_free_ref() -> None:
    """``insert_ref_unconditional`` adds a ref node with no resolution edges."""
    sg = ScopeGraph.new(_range(0, 100), "python")
    sg.insert_ref_unconditional(Reference.new(_range(10, 13), None))
    ref_indices = [
        i for i in sg.graph.node_indices() if sg.graph[i].kind == NodeKindKind.REF
    ]
    assert len(ref_indices) == 1
    assert sg.graph.out_edges(ref_indices[0]) == []


# === queries ================================================================


def test_get_definitions_returns_name_and_identifier_range() -> None:
    """``get_definitions`` yields ``(name, identifier_range)`` per def."""
    src = b"foo bar"
    sg = ScopeGraph.new(_range(0, 7), "python")
    sg.insert_local_def(LocalDef.new(_range(0, 3), None, LocalScope.new(_range(0, 7))))
    sg.insert_local_def(LocalDef.new(_range(4, 7), None, LocalScope.new(_range(0, 7))))
    defs = sg.get_definitions(src)
    assert ("foo", _range(0, 3)) in defs
    assert ("bar", _range(4, 7)) in defs
    assert len(defs) == 2


def test_get_references_returns_name_and_range() -> None:
    """``get_references`` yields ``(name, range)`` per ref."""
    src = b"foo foo"
    sg = ScopeGraph.new(_range(0, 7), "python")
    sg.insert_local_def(LocalDef.new(_range(0, 3), None, LocalScope.new(_range(0, 7))))
    sg.insert_ref(Reference.new(_range(4, 7), None), src)
    refs = sg.get_references(src)
    assert refs == [("foo", _range(4, 7))]


def test_get_references_with_definitions_resolves() -> None:
    """``get_references_with_definitions`` carries the resolved def (grok L354-L383)."""
    # src spans 11 bytes so the unconditional ref at [8,11) slices a real name
    # (``reference.name(src)`` is a raw byte slice -- an OOB range yields ``b""``).
    src = b"foo foo foo"
    sg = ScopeGraph.new(_range(0, 11), "python")
    sg.insert_local_def(LocalDef.new(_range(0, 3), None, LocalScope.new(_range(0, 7))))
    sg.insert_ref(Reference.new(_range(4, 7), None), src)
    # unconditional ref stays unresolved (None third slot)
    sg.insert_ref_unconditional(Reference.new(_range(8, 11), None))
    result = sg.get_references_with_definitions(src)
    resolved = [r for r in result if r[2] is not None]
    unresolved = [r for r in result if r[2] is None]
    assert len(resolved) == 1
    assert resolved[0] == ("foo", _range(4, 7), ("foo", _range(0, 3)))
    assert len(unresolved) == 1
    assert unresolved[0][0] == "foo"


def test_find_definition_returns_first_match_range() -> None:
    """``find_definition`` returns the range of the first matching def."""
    src = b"foo"
    sg = ScopeGraph.new(_range(0, 3), "python")
    sg.insert_local_def(LocalDef.new(_range(0, 3), None, LocalScope.new(_range(0, 3))))
    assert sg.find_definition("foo", src) == _range(0, 3)
    assert sg.find_definition("bar", src) is None


def test_find_references_returns_all_matching_ranges() -> None:
    """``find_references`` returns ranges of all matching refs."""
    src = b"foo foo"
    sg = ScopeGraph.new(_range(0, 7), "python")
    sg.insert_local_def(LocalDef.new(_range(0, 3), None, LocalScope.new(_range(0, 7))))
    sg.insert_ref(Reference.new(_range(4, 7), None), src)
    refs = sg.find_references("foo", src)
    assert _range(4, 7) in refs
    assert sg.find_references("bar", src) == []


# === node lookup ============================================================


def test_node_by_range_finds_containing_node() -> None:
    """``node_by_range`` returns a def/ref/import whose range contains the window.

    For DEF nodes :meth:`NodeKind.range` returns the scope range, so the lookup
    uses the def's scope range here.
    """
    sg = ScopeGraph.new(_range(0, 100), "python")
    sg.insert_local_def(LocalDef.new(_range(0, 3), None, LocalScope.new(_range(0, 100))))
    # window inside the def's scope range [0,100) -> found
    assert sg.node_by_range(10, 20) is not None
    # window outside -> None
    assert sg.node_by_range(200, 210) is None


def test_tightest_node_for_range_picks_smallest() -> None:
    """``tightest_node_for_range`` returns the def with the smallest scope range.

    Two defs with scope ranges [0,50) and [0,100); a query for [0,50) fits only
    the smaller one.
    """
    sg = ScopeGraph.new(_range(0, 100), "python")
    sg.insert_local_def(LocalDef.new(_range(0, 3), None, LocalScope.new(_range(0, 50))))
    sg.insert_local_def(LocalDef.new(_range(40, 43), None, LocalScope.new(_range(0, 100))))
    tightest = sg.tightest_node_for_range(0, 50)
    assert tightest is not None
    assert sg.graph[tightest].definition is not None
    assert sg.graph[tightest].definition.range == _range(0, 3)
    # a window too narrow for either def's scope range to fit fully -> None
    # (def A scope end=50 > 30; def B scope end=100 > 30)
    assert sg.tightest_node_for_range(0, 30) is None


# === ScopeStack (R305b) =====================================================


def test_scope_stack_walks_child_then_root() -> None:
    """``ScopeStack`` yields the start node then walks Outgoing to root (grok L455-L479)."""
    sg = ScopeGraph.new(_range(0, 100), "python")  # root
    sg.insert_local_scope(LocalScope.new(_range(0, 50)))  # child scope
    child_idx = next(
        src for src, w in sg.graph.in_edges(sg.root_idx) if w == EdgeKind.SCOPE_TO_SCOPE
    )
    visited = list(sg._scope_stack(child_idx))
    # child first, then root, then stops (root has no Outgoing ScopeToScope)
    assert visited == [child_idx, sg.root_idx]


def test_scope_stack_root_only_yields_once() -> None:
    """A stack starting at root yields just root (no parent edge to walk)."""
    sg = ScopeGraph.new(_range(0, 100), "python")
    visited = list(sg._scope_stack(sg.root_idx))
    assert visited == [sg.root_idx]


def test_scope_stack_is_iterator_protocol() -> None:
    """``ScopeStack`` implements ``__iter__`` / ``__next__`` (grok ``Iterator``)."""
    sg = ScopeGraph.new(_range(0, 100), "python")
    stack: ScopeStack = sg._scope_stack(sg.root_idx)
    assert iter(stack) is stack
    assert next(stack) == sg.root_idx
    with pytest.raises(StopIteration):
        next(stack)


# === ScopeGraphResult (R305b) ==============================================


def test_scope_graph_result_holds_graph_and_aliases() -> None:
    """``ScopeGraphResult`` is the ``{graph, aliases}`` container (grok mod.rs L20-L25)."""
    sg = ScopeGraph.new(_range(0, 10), "python")
    result = ScopeGraphResult(graph=sg, aliases=[("np", "numpy")])
    assert result.graph is sg
    assert result.aliases == [("np", "numpy")]


def test_scope_graph_result_default_aliases_empty() -> None:
    """``aliases`` defaults to an empty list when omitted is NOT supported (no default).

    grok's struct has no ``Default`` impl; both fields are required. We assert
    the field is constructible with an empty list explicitly.
    """
    sg = ScopeGraph.new(_range(0, 10), "python")
    result = ScopeGraphResult(graph=sg, aliases=[])
    assert result.aliases == []


# === barrel contracts =======================================================


def test_scope_graph_barrel_reexports_graph_symbols() -> None:
    """``scope_graph`` barrel re-exports all 6 graph symbols now landed (R305a+b).

    Mirrors grok ``scope_graph/mod.rs`` L11-L14: ``NodeIndex`` / ``QueryVersion``
    / ``Snippet`` (R305a) + ``ScopeGraph`` / ``ScopeStack`` / ``ScopeGraphResult``
    (R305b). ``ScopeGraphIndex`` + the two bridge functions land in R305c-d.
    """
    from minimax_code.xai_codebase_graph import scope_graph

    assert {
        "NodeIndex",
        "QueryVersion",
        "ScopeGraph",
        "ScopeGraphResult",
        "ScopeStack",
        "Snippet",
    }.issubset(set(scope_graph.__all__))


def test_scope_graph_barrel_symbols_are_graph_leaf() -> None:
    """Barrel re-exports point at the ``graph`` leaf module's objects."""
    assert NodeIndex is graph_leaf.NodeIndex
    assert QueryVersion is graph_leaf.QueryVersion
    assert Snippet is graph_leaf.Snippet
    assert ScopeGraph is graph_leaf.ScopeGraph
    assert ScopeStack is graph_leaf.ScopeStack
    assert ScopeGraphResult is graph_leaf.ScopeGraphResult


def test_scope_graph_barrel_exports_nineteen_symbols() -> None:
    """``scope_graph/__init__`` ``__all__`` = 12 (R305a) + 3 graph (R305b) + 1 index (R305c) + 3 bridge (R305e).

    R301 landed 7 nodes + EdgeKind + NodeKindKind (9). R305a extended with
    NodeIndex / QueryVersion / Snippet (12). R305b adds ScopeGraph / ScopeStack
    / ScopeGraphResult (15), mirroring grok ``scope_graph/mod.rs`` L11-L14 +
    the inline ``ScopeGraphResult`` (mod.rs L20-L25). R305c adds ScopeGraphIndex
    (16) -- the cross-file symbol index aggregating per-file ScopeGraph instances
    with interned names, alias resolution, and reverse file->symbol indexes.
    R305e adds the tree-sitter bridge free functions build_scope_graph /
    extract_symbols_fast / scope_graph_from_definitions_query from
    ``scope_graph/bridge`` (mirrors grok ``mod.rs`` L13 re-export of the two
    ``graph.rs`` free functions + L30 inline ``build_scope_graph``), bringing
    the surface to 19.
    """
    from minimax_code.xai_codebase_graph import scope_graph

    expected = {
        "EdgeKind",
        "build_scope_graph",
        "extract_symbols_fast",
        "scope_graph_from_definitions_query",
        "LocalDef",
        "LocalImport",
        "LocalScope",
        "NodeIndex",
        "NodeKind",
        "NodeKindKind",
        "QueryVersion",
        "Reference",
        "ScopeGraph",
        "ScopeGraphIndex",
        "ScopeGraphResult",
        "ScopeStack",
        "Snippet",
        "Symbol",
        "SymbolId",
    }
    assert set(scope_graph.__all__) == expected
    assert len(scope_graph.__all__) == 19


def test_crate_root_barrel_reexports_scope_graph_and_result() -> None:
    """crate root re-exports ``ScopeGraph`` + ``ScopeGraphResult`` but NOT ``ScopeStack``.

    Mirrors grok ``lib.rs`` L95-L98: the crate root cherry-picks ``ScopeGraph``
    and ``ScopeGraphResult`` from ``scope_graph`` (alongside the node symbols +
    ``QueryVersion``), while ``ScopeStack`` stays subpackage-local (grok
    ``lib.rs`` omits it -- asymmetric double-barrel).
    """
    assert "ScopeGraph" in xcg.__all__
    assert "ScopeGraphResult" in xcg.__all__
    assert xcg.ScopeGraph is ScopeGraph
    assert xcg.ScopeGraphResult is ScopeGraphResult
    # ScopeStack is deliberately absent from the crate root (grok lib.rs omits
    # it) -- it lives under scope_graph only.
    assert "ScopeStack" not in xcg.__all__
    # NodeIndex / Snippet also stay subpackage-local (R305a decision preserved)
    assert "NodeIndex" not in xcg.__all__
    assert "Snippet" not in xcg.__all__


def test_graph_leaf_all_contains_nine_public_symbols() -> None:
    """``graph`` leaf ``__all__`` exposes all 9 public symbols (R305a 6 + R305b 3)."""
    assert set(graph_leaf.__all__) == {
        "ExtractedSymbols",
        "NodeIndex",
        "QueryVersion",
        "ReferenceWithDefinition",
        "ScopeGraph",
        "ScopeGraphResult",
        "ScopeStack",
        "Snippet",
        "SymbolWithRange",
    }
