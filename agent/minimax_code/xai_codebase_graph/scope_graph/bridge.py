"""Tree-sitter bridge free functions for scope-graph construction (R305e).

Ported from grok ``xai-codebase-graph``:

* ``Range::for_tree_node`` (types/range.rs L170-L189) -> :func:`range_for_node`
* ``scope_graph_from_definitions_query`` (scope_graph/graph.rs L485-L556)
* ``extract_symbols_fast`` (scope_graph/graph.rs L565-L633)
* ``build_scope_graph`` (scope_graph/mod.rs L30-L38) -> :func:`build_scope_graph`

In grok the two free functions live in ``graph.rs`` and ``build_scope_graph`` +
``ScopeGraphResult`` in ``mod.rs``; the Python port splits the 1600-line
``graph.rs`` into focused modules (``graph`` runtime, ``sgix`` ser/de,
``bridge`` tree-sitter seam) -- a functional re-organisation, not a
line-by-line split. The public surface (what ``scope_graph`` re-exports)
matches grok ``mod.rs`` L11-L14 + L30.

Deferred-binding兑现点: the grok originals bind to the ``tree_sitter`` crate
(``QueryCursor::new`` + ``cursor.matches`` + ``node.range()`` +
``node.byte_range()``). range.py L9-L14 and types.py L11-L24 both declare
tree-sitter runtime bindings delayed to keep the lower leaves stdlib-clean.
This module is the兑现点 of that deferral: it is the *only* module that
touches tree-sitter, and it does so via a lazy import guarded by a clear
``RuntimeError`` (mirrors R303 ``TSLanguageConfig.language()`` /
``compile_query()``). The lower leaves stay zero-dependency; the bridge is the
single seam.

Duck-typed, not bound: grok's ``node.range()`` / ``node.byte_range()`` /
``query.capture_names()`` / ``cursor.matches()`` are reached through Python
duck typing. The bridge reads ``node.start_byte`` / ``node.end_byte`` /
``node.start_point.{row,column}`` / ``node.end_point.{row,column}`` directly
(the same data ``node.range()`` returns) and iterates ``cursor.matches(query,
root_node, src)``. A real ``tree_sitter.QueryCursor`` / ``Query`` / ``Node``
satisfies this contract; the tests use lightweight fakes. This is a functional
clone, not a line-by-line clone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from minimax_code.xai_codebase_graph.scope_graph.graph import (
    ExtractedSymbols,
    ScopeGraph,
    ScopeGraphResult,
)
from minimax_code.xai_codebase_graph.scope_graph.nodes import LocalDef, Reference
from minimax_code.xai_codebase_graph.types.range import Position, Range

if TYPE_CHECKING:
    # TSLanguageConfig appears only in annotations; the bridge reaches the
    # language config via duck typing (``primary_language_id`` / ``symbol_id_of``)
    # and never binds the concrete type at runtime. Deferring the import under
    # TYPE_CHECKING also breaks the ``scope_graph`` <-> ``languages.types`` cycle
    # (languages.types -> scope_graph.nodes -> scope_graph pkg-init -> bridge ->
    # languages.types), so the barrel can re-export the bridge without a
    # circular-import failure -- an extension of the same deferred-binding
    # discipline that keeps ``_new_query_cursor``'s ``tree_sitter`` import lazy.
    from minimax_code.xai_codebase_graph.languages.types import TSLanguageConfig


def range_for_node(node: Any) -> Range:
    """Build a :class:`Range` from a tree-sitter-like node (grok ``Range::for_tree_node``).

    Mirrors grok ``Range::for_tree_node`` (range.rs L170-L184): reads the
    node's byte + point extents and assembles start/end :class:`Position`
    objects. grok goes through ``node.range()``; the bridge reads the node's
    direct attributes (``start_byte`` / ``end_byte`` / ``start_point`` /
    ``end_point``) -- the same underlying data, reached without the binding
    indirection that range.py L9-L14 keeps deferred for the zero-dependency
    lower leaves.
    """
    return Range(
        Position(node.start_point.row, node.start_point.column, node.start_byte),
        Position(node.end_point.row, node.end_point.column, node.end_byte),
    )


def _new_query_cursor() -> Any:
    """Construct a tree-sitter ``QueryCursor`` (grok ``QueryCursor::new()``).

    Mirrors R303's deferred-binding pattern: the lower leaves stay
    stdlib-clean, and the tree-sitter runtime is imported lazily here -- the
    bridge is the single seam. A missing ``tree_sitter`` package raises
    ``RuntimeError`` (not ``ImportError``) so callers see the same "bridge
    needs the binding" signal R303's ``language()`` / ``compile_query()`` emit.
    """
    try:
        import tree_sitter
    except ImportError as exc:
        raise RuntimeError(
            "scope_graph bridge requires the 'tree_sitter' Python package; "
            "install it to build scope graphs from real syntax trees."
        ) from exc
    return tree_sitter.QueryCursor()


def _capture_names(query: Any) -> list[str]:
    """Return a query's capture-name table (grok ``query.capture_names()``).

    Tolerates both the property form (modern ``tree_sitter.Query.capture_names``
    is a ``list``) and a callable form so the bridge stays binding-version
    agnostic.
    """
    names = query.capture_names
    return names() if callable(names) else names


def scope_graph_from_definitions_query(
    query: Any,
    root_node: Any,
    src: bytes,
    language: TSLanguageConfig,
) -> tuple[ScopeGraph, list[tuple[str, str]]]:
    """Build a :class:`ScopeGraph` from a file-definitions query (grok L485-L556).

    Walks ``cursor.matches(query, root_node, src)``, classifies each capture by
    its dotted name (``name.definition.<sym>`` / ``name.reference.<sym>`` /
    ``alias.original`` / ``alias.name``), then inserts definitions into their
    tightest enclosing scope and references unconditionally. Returns the graph
    plus alias ``(alias_name, original_name)`` pairs.

    Simpler than a full scope-resolution pass: it ignores local-scoping rules
    and hoists every definition to its tightest scope via
    :meth:`ScopeGraph.insert_global_def`. This matches the existing
    ``TSLanguageConfig`` file-definition query patterns.
    """
    scope_graph = ScopeGraph.new(range_for_node(root_node), language.primary_language_id())
    cursor = _new_query_cursor()
    capture_names = _capture_names(query)

    def_captures: list[tuple[Range, Any]] = []
    ref_captures: list[tuple[Range, Any]] = []
    alias_pairs: list[tuple[str, str]] = []

    for match_ in cursor.matches(query, root_node, src):
        alias_original: str | None = None
        alias_name: str | None = None
        for capture in match_.captures:
            range_ = range_for_node(capture.node)
            capture_name = capture_names[capture.index]
            text = src[range_.start_byte() : range_.end_byte()].decode("utf-8", "replace")
            parts = capture_name.split(".")
            if len(parts) == 3 and parts[0] == "name" and parts[1] == "definition":
                def_captures.append((range_, language.symbol_id_of(parts[2])))
            elif len(parts) == 3 and parts[0] == "name" and parts[1] == "reference":
                ref_captures.append((range_, language.symbol_id_of(parts[2])))
            elif parts == ["alias", "original"]:
                alias_original = text
            elif parts == ["alias", "name"]:
                alias_name = text
        if alias_original is not None and alias_name is not None:
            alias_pairs.append((alias_name, alias_original))

    for range_, symbol_id in def_captures:
        local_scope = scope_graph.find_tightest_local_scope(range_)
        scope_graph.insert_global_def(LocalDef.new(range_, symbol_id, local_scope))

    for range_, symbol_id in ref_captures:
        scope_graph.insert_ref_unconditional(Reference.new(range_, symbol_id))

    return scope_graph, alias_pairs


def extract_symbols_fast(
    query: Any,
    root_node: Any,
    src: bytes,
    _lang_config: TSLanguageConfig,
) -> ExtractedSymbols:
    """Lightweight symbol extraction without building a full graph (grok L565-L633).

    Pre-computes per-capture-index classification (``is_def`` / ``is_ref``
    boolean vectors + alias slot indices) so the hot match loop does index
    lookups instead of per-capture string comparisons, then walks the matches
    once to collect ``(name, range)`` tuples for definitions / references and
    ``(alias_name, original_name)`` pairs. ~2-3x faster than
    :func:`scope_graph_from_definitions_query` when only the symbol table is
    needed (no scope graph required).

    ``_lang_config`` is accepted for signature parity with grok but unused --
    the fast path classifies captures purely by their dotted names, with no
    symbol-id resolution (the caller resolves names to symbol ids later via the
    language config if needed).
    """
    capture_names = _capture_names(query)
    is_def = [name.startswith("name.definition.") for name in capture_names]
    is_ref = [name.startswith("name.reference.") for name in capture_names]
    alias_original_idx: int | None = None
    alias_name_idx: int | None = None
    for i, name in enumerate(capture_names):
        if name == "alias.original":
            alias_original_idx = i
        elif name == "alias.name":
            alias_name_idx = i

    definitions: list[tuple[str, Range]] = []
    references: list[tuple[str, Range]] = []
    alias_pairs: list[tuple[str, str]] = []

    cursor = _new_query_cursor()
    for match_ in cursor.matches(query, root_node, src):
        alias_original: bytes | None = None
        alias_name: bytes | None = None
        for capture in match_.captures:
            idx = capture.index
            node = capture.node
            range_ = range_for_node(node)
            start = range_.start_byte()
            end = range_.end_byte()
            if 0 <= idx < len(is_def) and is_def[idx]:
                definitions.append((src[start:end].decode("utf-8", "replace"), range_))
            elif 0 <= idx < len(is_ref) and is_ref[idx]:
                references.append((src[start:end].decode("utf-8", "replace"), range_))
            elif idx == alias_original_idx:
                alias_original = src[start:end]
            elif idx == alias_name_idx:
                alias_name = src[start:end]
        if alias_original is not None and alias_name is not None:
            alias_pairs.append(
                (
                    alias_name.decode("utf-8", "replace"),
                    alias_original.decode("utf-8", "replace"),
                )
            )

    return definitions, references, alias_pairs


def build_scope_graph(
    query: Any,
    root_node: Any,
    src: bytes,
    language: TSLanguageConfig,
) -> ScopeGraphResult:
    """Thin convenience wrapper (grok ``build_scope_graph``, mod.rs L30-L38).

    Delegates to :func:`scope_graph_from_definitions_query` and wraps the
    ``(graph, aliases)`` pair in a :class:`ScopeGraphResult`.
    """
    graph, aliases = scope_graph_from_definitions_query(query, root_node, src, language)
    return ScopeGraphResult(graph=graph, aliases=aliases)


__all__ = [
    "build_scope_graph",
    "extract_symbols_fast",
    "range_for_node",
    "scope_graph_from_definitions_query",
]
