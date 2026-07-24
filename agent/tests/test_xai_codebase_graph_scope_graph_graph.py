"""Tests for ``xai_codebase_graph.scope_graph.graph`` (R305a, brick 6 leaf a).

Mirrors grok ``scope_graph/graph.rs`` pure-data foundation: the four ``pub
type`` aliases (:data:`NodeIndex`, :data:`SymbolWithRange`,
:data:`ReferenceWithDefinition`, :data:`ExtractedSymbols`), the
:class:`QueryVersion` version-stamp enum (collapsed to a frozen dataclass)
and its :meth:`QueryVersion.needs_rebuild` logic, and the :class:`Snippet`
mutable struct. Plus the asymmetric double-barrel surface: ``scope_graph/
mod.rs`` re-exports ``NodeIndex`` / ``QueryVersion`` / ``Snippet`` (+3) while
the crate root ``lib.rs`` re-exports only ``QueryVersion`` (+1).

Zero tree-sitter: consumes only R300 :class:`Range` and R301 :class:`Symbol`.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import minimax_code.xai_codebase_graph as xcg
from minimax_code.xai_codebase_graph.scope_graph import (
    NodeIndex,
    QueryVersion,
    Snippet,
)
from minimax_code.xai_codebase_graph.scope_graph import graph as graph_leaf
from minimax_code.xai_codebase_graph.scope_graph.nodes import Symbol
from minimax_code.xai_codebase_graph.types import Range


def _range() -> Range:
    """A minimal 0-indexed range (default Position fields -> all-zero)."""
    return Range()


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


# === barrel contracts =======================================================


def test_scope_graph_barrel_reexports_graph_symbols() -> None:
    """``scope_graph`` barrel re-exports ``NodeIndex`` / ``QueryVersion`` / ``Snippet``.

    Mirrors grok ``scope_graph/mod.rs`` L11-L14 (3 of the 8 graph symbols; the
    rest -- ``ScopeGraph`` / ``ScopeGraphIndex`` / ``ScopeStack`` / the two
    bridge functions -- land in R305b-d).
    """
    from minimax_code.xai_codebase_graph import scope_graph

    assert {"NodeIndex", "QueryVersion", "Snippet"}.issubset(set(scope_graph.__all__))


def test_scope_graph_barrel_symbols_are_graph_leaf() -> None:
    """Barrel re-exports point at the ``graph`` leaf module's objects."""
    assert NodeIndex is graph_leaf.NodeIndex
    assert QueryVersion is graph_leaf.QueryVersion
    assert Snippet is graph_leaf.Snippet


def test_crate_root_barrel_reexports_queryversion_only() -> None:
    """crate root re-exports ``QueryVersion`` but NOT ``NodeIndex`` / ``Snippet``.

    Mirrors grok ``lib.rs`` L95-L98: the crate root cherry-picks
    ``QueryVersion`` from ``scope_graph`` (alongside the node symbols), while
    ``NodeIndex`` / ``Snippet`` stay subpackage-local (only in ``mod.rs``).
    """
    assert "QueryVersion" in xcg.__all__
    assert xcg.QueryVersion is QueryVersion
    # NodeIndex / Snippet are deliberately absent from the crate root (grok
    # lib.rs omits them) -- they live under scope_graph only.
    assert "NodeIndex" not in xcg.__all__
    assert "Snippet" not in xcg.__all__


def test_graph_leaf_all_contains_six_public_symbols() -> None:
    """``graph`` leaf ``__all__`` exposes all 6 public symbols."""
    assert set(graph_leaf.__all__) == {
        "ExtractedSymbols",
        "NodeIndex",
        "QueryVersion",
        "ReferenceWithDefinition",
        "Snippet",
        "SymbolWithRange",
    }
