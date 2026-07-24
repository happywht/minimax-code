"""Black-box tests for the codebase-graph scope-graph node/edge layer (R301).

Exercises :mod:`minimax_code.xai_codebase_graph.scope_graph` -- the node / edge
type layer fused from grok's ``xai-codebase-graph/src/scope_graph/`` (direction
(2), brick 2). Covers ``EdgeKind``, the six node value types (``Symbol`` /
``SymbolId`` / ``LocalScope`` / ``LocalDef`` / ``LocalImport`` / ``Reference``)
and the ``NodeKind`` tagged-union port (+ ``NodeKindKind`` discriminator).

Migration decision matrix (grok ``scope_graph/edges.rs`` + ``nodes.rs`` +
``mod.rs`` inline ``mod tests``)
--------------------------------------------------------------

EdgeKind (edges.rs L5-L22):
* ``edge_kind_has_five_varants_with_grok_names`` -- **migrated**: 5 variants,
  member value == grok variant name (serde-friendly).
* ``edge_kind_is_hashable_and_iterable`` -- **migrated**: grok ``Eq + Hash`` ->
  ``enum.Enum`` member identity (hashable); all variants reachable via
  iteration.

Symbol (nodes.rs L10-L23):
* ``symbol_new_sets_kind_and_range`` -- **migrated**: ``Symbol::new`` factory.
* ``symbol_frozen_hashable_immutable`` -- **migrated**: grok struct ->
  frozen+slots dataclass (Arc<str> -> str); hashable + immutable.

SymbolId (nodes.rs L26-L49):
* ``symbol_id_new_and_value_equality`` -- **migrated**: ``SymbolId::new`` +
  value equality (grok ``Eq + Copy`` -> frozen dataclass).
* ``symbol_id_name_looks_up_namespace`` -- **migrated**: in-bounds lookup.
* ``symbol_id_name_none_out_of_bounds`` -- **migrated**: grok ``.get()`` ->
  ``Option`` -> ``None`` on either index OOB.

LocalScope (nodes.rs L53-L64):
* ``local_scope_new_and_hashable`` -- **migrated**.

LocalDef (nodes.rs L67-L96):
* ``local_def_new_sets_fields`` -- **migrated**: ``LocalDef::new``.
* ``local_def_name_slices_identifier_bytes`` -- **migrated**: ``name(src)``
  byte-slice contract (``[start_byte, end_byte)``).
* ``local_def_scope_range_returns_scope_range`` -- **migrated**:
  ``scope_range()`` accessor.

LocalImport (nodes.rs L99-L115):
* ``local_import_new_and_name`` -- **migrated**.

Reference (nodes.rs L118-L136):
* ``reference_new_and_name`` -- **migrated**.

NodeKind + NodeKindKind (nodes.rs L139-L180):
* ``node_kind_kind_has_four_variants`` -- **migrated**: the discriminator
  (Pythonic; no grok counterpart).
* ``node_kind_scope_node_constructor`` -- **migrated**: ``NodeKind::scope(range)``.
* ``node_kind_range_dispatches_per_variant`` -- **migrated**: ``range()``
  returns the scope range for DEF (full context), else identifier range.
* ``node_kind_identifier_range_dispatches_per_variant`` -- **migrated**:
  ``identifier_range()`` returns the identifier's own range for every variant.
* ``node_kind_frozen_hashable`` -- **migrated**.

barrel contract:
* ``scope_graph_barrel_exports_nine_symbols`` -- **migrated**: the
  ``scope_graph/__init__`` ``__all__`` surface (7 nodes + EdgeKind +
  NodeKindKind).
* ``crate_root_barrel_mirrors_scope_graph_nodes`` -- **migrated**: grok
  ``lib.rs`` re-exports the 7 node symbols at the crate root; both import paths
  work. EdgeKind / NodeKindKind stay subpackage-only (mirrors grok lib.rs which
  does not re-export EdgeKind).

YAGNI boundaries (R301, documented in source, NOT tested here):
* serde (``Serialize`` / ``Deserialize``) on all types -- dropped; land with
  ``manager/`` cache migration.
* ``scope_graph/graph.rs`` runtime (``ScopeGraph`` / ``build_scope_graph`` /
  ``extract_symbols_fast`` / ``QueryVersion`` / ``Snippet`` etc.) -- not
  migrated; depends on ``interner/`` + ``tree_sitter`` + ``languages/``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import minimax_code.xai_codebase_graph as xcg_root
from minimax_code.xai_codebase_graph import (
    LocalDef as LocalDefFromRoot,
)
from minimax_code.xai_codebase_graph import (
    LocalImport as LocalImportFromRoot,
)
from minimax_code.xai_codebase_graph import (
    LocalScope as LocalScopeFromRoot,
)
from minimax_code.xai_codebase_graph import (
    NodeKind as NodeKindFromRoot,
)
from minimax_code.xai_codebase_graph import (
    Reference as ReferenceFromRoot,
)
from minimax_code.xai_codebase_graph import (
    Symbol as SymbolFromRoot,
)
from minimax_code.xai_codebase_graph import (
    SymbolId as SymbolIdFromRoot,
)
from minimax_code.xai_codebase_graph.scope_graph import (
    EdgeKind,
    LocalDef,
    LocalImport,
    LocalScope,
    NodeKind,
    NodeKindKind,
    Reference,
    Symbol,
    SymbolId,
)
from minimax_code.xai_codebase_graph.scope_graph import (
    edges as edges_leaf,
)
from minimax_code.xai_codebase_graph.scope_graph import (
    nodes as nodes_leaf,
)
from minimax_code.xai_codebase_graph.types.range import Position, Range


def _range(start_byte: int, end_byte: int) -> Range:
    """Build a minimal Range spanning ``[start_byte, end_byte)`` bytes."""
    return Range(
        start_position=Position(byte_offset=start_byte),
        end_position=Position(byte_offset=end_byte),
    )


# === EdgeKind ==============================================================


def test_edge_kind_has_five_variants_with_grok_names() -> None:
    """grok ``EdgeKind`` -> 5-variant ``enum.Enum``; value == grok variant name."""
    assert EdgeKind.SCOPE_TO_SCOPE.value == "ScopeToScope"
    assert EdgeKind.DEF_TO_SCOPE.value == "DefToScope"
    assert EdgeKind.IMPORT_TO_SCOPE.value == "ImportToScope"
    assert EdgeKind.REF_TO_DEF.value == "RefToDef"
    assert EdgeKind.REF_TO_IMPORT.value == "RefToImport"


def test_edge_kind_is_hashable_and_iterable() -> None:
    """grok ``Eq + Hash`` -> Enum member identity; all 5 variants reachable."""
    members = {member.value: member for member in EdgeKind}
    assert len(members) == 5
    # hashable: usable as dict keys / set members
    assert EdgeKind.REF_TO_DEF in {EdgeKind.REF_TO_DEF, EdgeKind.REF_TO_IMPORT}
    assert hash(EdgeKind.DEF_TO_SCOPE) == hash(EdgeKind.DEF_TO_SCOPE)


# === Symbol ================================================================


def test_symbol_new_sets_kind_and_range() -> None:
    """grok ``Symbol::new`` -> ``Symbol.new`` classmethod (Arc<str> -> str)."""
    rng = _range(0, 3)
    sym = Symbol.new("function", rng)
    assert sym.kind == "function"
    assert sym.range is rng


def test_symbol_frozen_hashable_immutable() -> None:
    """grok struct -> frozen+slots dataclass: hashable + immutable."""
    sym = Symbol.new("class", _range(4, 9))
    assert hash(sym) == hash(Symbol.new("class", _range(4, 9)))
    with pytest.raises(FrozenInstanceError):
        sym.kind = "method"  # type: ignore[misc]


# === SymbolId ==============================================================


def test_symbol_id_new_and_value_equality() -> None:
    """grok ``SymbolId::new`` + ``Eq + Copy`` -> frozen dataclass value equality."""
    sid_a = SymbolId.new(2, 5)
    sid_b = SymbolId.new(2, 5)
    sid_c = SymbolId.new(2, 6)
    assert sid_a == sid_b
    assert sid_a != sid_c
    assert hash(sid_a) == hash(sid_b)


def test_symbol_id_name_looks_up_namespace() -> None:
    """grok ``SymbolId::name`` in-bounds lookup -> ``str``."""
    namespaces = [["foo", "bar"], ["baz", "qux", "quux"]]
    assert SymbolId.new(0, 1).name(namespaces) == "bar"
    assert SymbolId.new(1, 2).name(namespaces) == "quux"


def test_symbol_id_name_none_out_of_bounds() -> None:
    """grok ``.get()`` -> ``Option`` -> ``None`` on either index OOB."""
    namespaces = [["foo"]]
    assert SymbolId.new(5, 0).name(namespaces) is None  # namespace OOB
    assert SymbolId.new(0, 9).name(namespaces) is None  # symbol OOB
    assert SymbolId.new(0, 0).name([]) is None  # empty namespaces


# === LocalScope ============================================================


def test_local_scope_new_and_hashable() -> None:
    """grok ``LocalScope::new`` + ``Eq + Hash`` -> frozen dataclass."""
    rng = _range(0, 10)
    scope = LocalScope.new(rng)
    assert scope.range is rng
    assert hash(scope) == hash(LocalScope.new(rng))
    with pytest.raises(FrozenInstanceError):
        scope.range = _range(1, 2)  # type: ignore[misc]


# === LocalDef ==============================================================


def test_local_def_new_sets_fields() -> None:
    """grok ``LocalDef::new`` -> ``LocalDef.new`` classmethod."""
    rng = _range(0, 3)
    sid = SymbolId.new(0, 0)
    scope = LocalScope.new(_range(0, 20))
    defn = LocalDef.new(rng, sid, scope)
    assert defn.range is rng
    assert defn.symbol_id is sid
    assert defn.scope is scope


def test_local_def_name_slices_identifier_bytes() -> None:
    """grok ``LocalDef::name(src: &[u8])`` -> byte-slice ``src[start:end)``."""
    src = b"foo = bar()"
    defn = LocalDef.new(_range(0, 3), None, LocalScope.new(_range(0, 11)))
    assert defn.name(src) == b"foo"
    # byte contract: caller decodes for str
    assert defn.name(src).decode() == "foo"


def test_local_def_scope_range_returns_scope_range() -> None:
    """grok ``LocalDef::scope_range`` -> the scope's range, not the def's."""
    scope_rng = _range(0, 20)
    defn = LocalDef.new(_range(0, 3), None, LocalScope.new(scope_rng))
    assert defn.scope_range() is scope_rng


# === LocalImport ===========================================================


def test_local_import_new_and_name() -> None:
    """grok ``LocalImport::new`` + ``name(src)`` byte-slice."""
    src = b"import os"
    imp = LocalImport.new(_range(7, 9))
    assert imp.name(src) == b"os"
    assert hash(imp) == hash(LocalImport.new(_range(7, 9)))


# === Reference =============================================================


def test_reference_new_and_name() -> None:
    """grok ``Reference::new`` + ``name(src)`` byte-slice."""
    src = b"call_foo()"
    ref = Reference.new(_range(5, 8), SymbolId.new(1, 2))
    assert ref.name(src) == b"foo"
    assert ref.symbol_id == SymbolId.new(1, 2)
    assert hash(ref) == hash(Reference.new(_range(5, 8), SymbolId.new(1, 2)))


# === NodeKindKind + NodeKind ===============================================


def test_node_kind_kind_has_four_variants() -> None:
    """Pythonic discriminator (no grok counterpart); 4 variants == grok variants."""
    assert NodeKindKind.SCOPE.value == "Scope"
    assert NodeKindKind.DEF.value == "Def"
    assert NodeKindKind.IMPORT.value == "Import"
    assert NodeKindKind.REF.value == "Ref"
    assert len(list(NodeKindKind)) == 4


def test_node_kind_scope_node_constructor() -> None:
    """grok ``NodeKind::scope(range)`` -> ``NodeKind.scope_node`` classmethod."""
    rng = _range(0, 10)
    nk = NodeKind.scope_node(rng)
    assert nk.kind is NodeKindKind.SCOPE
    assert nk.scope is not None
    assert nk.scope.range is rng
    # other payloads default to None (tagged-union invariant)
    assert nk.definition is None
    assert nk.import_ is None
    assert nk.reference is None


def test_node_kind_range_dispatches_per_variant() -> None:
    """grok ``NodeKind::range`` -> match dispatch; DEF returns scope range."""
    scope_rng = _range(0, 20)
    ident_rng = _range(0, 3)
    # SCOPE: range == scope range
    scope_nk = NodeKind.scope_node(scope_rng)
    assert scope_nk.range() == scope_rng
    # DEF: range == the def's SCOPE range (full context), NOT the identifier
    defn = LocalDef.new(ident_rng, None, LocalScope.new(scope_rng))
    def_nk = NodeKind(kind=NodeKindKind.DEF, definition=defn)
    assert def_nk.range() == scope_rng
    # IMPORT: range == import range
    imp_rng = _range(5, 7)
    imp_nk = NodeKind(kind=NodeKindKind.IMPORT, import_=LocalImport.new(imp_rng))
    assert imp_nk.range() == imp_rng
    # REF: range == reference range
    ref_rng = _range(8, 11)
    ref_nk = NodeKind(kind=NodeKindKind.REF, reference=Reference.new(ref_rng, None))
    assert ref_nk.range() == ref_rng


def test_node_kind_identifier_range_dispatches_per_variant() -> None:
    """grok ``NodeKind::identifier_range`` -> always the identifier's own range."""
    # For DEF, identifier_range == def.range (NOT the scope range) -- the
    # distinguishing case vs. range().
    scope_rng = _range(0, 20)
    ident_rng = _range(0, 3)
    defn = LocalDef.new(ident_rng, None, LocalScope.new(scope_rng))
    def_nk = NodeKind(kind=NodeKindKind.DEF, definition=defn)
    assert def_nk.identifier_range() == ident_rng
    assert def_nk.identifier_range() != def_nk.range()
    # SCOPE / IMPORT / REF: identifier_range == their own range (== range())
    scope_nk = NodeKind.scope_node(scope_rng)
    assert scope_nk.identifier_range() == scope_rng
    imp_rng = _range(5, 7)
    imp_nk = NodeKind(kind=NodeKindKind.IMPORT, import_=LocalImport.new(imp_rng))
    assert imp_nk.identifier_range() == imp_rng
    ref_rng = _range(8, 11)
    ref_nk = NodeKind(kind=NodeKindKind.REF, reference=Reference.new(ref_rng, None))
    assert ref_nk.identifier_range() == ref_rng


def test_node_kind_frozen_hashable() -> None:
    """grok ``NodeKind`` -> frozen dataclass: hashable + immutable."""
    nk = NodeKind.scope_node(_range(0, 5))
    assert hash(nk) == hash(NodeKind.scope_node(_range(0, 5)))
    with pytest.raises(FrozenInstanceError):
        nk.kind = NodeKindKind.REF  # type: ignore[misc]


# === barrel contract =======================================================


def test_scope_graph_barrel_exports_nine_symbols() -> None:
    """``scope_graph/__init__`` ``__all__`` = 7 nodes + EdgeKind + NodeKindKind."""
    from minimax_code.xai_codebase_graph import scope_graph

    expected = {
        "EdgeKind",
        "LocalDef",
        "LocalImport",
        "LocalScope",
        "NodeKind",
        "NodeKindKind",
        "Reference",
        "Symbol",
        "SymbolId",
    }
    assert set(scope_graph.__all__) == expected
    # leaf modules carry the symbols
    assert edges_leaf.EdgeKind is EdgeKind
    assert nodes_leaf.NodeKind is NodeKind
    assert nodes_leaf.NodeKindKind is NodeKindKind


def test_crate_root_barrel_mirrors_scope_graph_nodes() -> None:
    """grok ``lib.rs`` re-exports 7 node symbols at crate root; both paths work."""
    # the 7 node symbols are re-exported at the crate root
    assert SymbolFromRoot is Symbol
    assert SymbolIdFromRoot is SymbolId
    assert LocalScopeFromRoot is LocalScope
    assert LocalDefFromRoot is LocalDef
    assert LocalImportFromRoot is LocalImport
    assert ReferenceFromRoot is Reference
    assert NodeKindFromRoot is NodeKind
    # __all__ grew from R300's 9 to R301's 16 (added 7 scope_graph nodes)
    assert set(xcg_root.__all__) == {
        "FileEvent",
        "FileEventKind",
        "FileMeta",
        "IndexStats",
        "LocalDef",
        "LocalImport",
        "LocalScope",
        "Location",
        "NodeKind",
        "Position",
        "Range",
        "Reference",
        "Symbol",
        "SymbolAlias",
        "SymbolId",
        "SymbolOccurrence",
    }
    # EdgeKind (grok lib.rs does not re-export it) + NodeKindKind (no grok
    # counterpart) stay subpackage-only -- not in the crate-root surface.
    assert "EdgeKind" not in xcg_root.__all__
    assert "NodeKindKind" not in xcg_root.__all__
