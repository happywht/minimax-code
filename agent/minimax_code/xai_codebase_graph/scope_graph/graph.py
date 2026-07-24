"""ScopeGraph runtime -- per-file symbol graph (direction (2), brick 6).

Mirrors grok ``xai-codebase-graph/src/scope_graph/graph.rs``. R305a landed the
pure-data foundation:

- Type aliases: :data:`NodeIndex` (graph node handle),
  :data:`SymbolWithRange`, :data:`ReferenceWithDefinition`,
  :data:`ExtractedSymbols`.
- :class:`QueryVersion` -- tracks the tree-sitter query hash used to build an
  index, so stale indexes (``Legacy`` / mismatched version) trigger a rebuild.
- :class:`Snippet` -- a code fragment with its text, line range, and
  associated symbols.

R305b lands the graph algorithms:

- :class:`_DiGraph` -- a tiny in-module directed graph replacing grok's
  ``petgraph::Graph<NodeKind, EdgeKind>``.
- :class:`ScopeGraph` -- the per-file scope/def/ref/import graph (22 methods).
- :class:`ScopeStack` -- iterator walking a node's enclosing scopes to root.
- :class:`ScopeGraphResult` -- ``{graph, aliases}`` container (grok
  ``scope_graph/mod.rs`` L20-L25).

R305b is still **zero tree-sitter**: every symbol here consumes only R300
:class:`~minimax_code.xai_codebase_graph.types.Range` and R301
:class:`~minimax_code.xai_codebase_graph.scope_graph.nodes` node types. The
``ScopeGraphIndex`` runtime + binary ser/de (R305c) and the two tree-sitter
bridge free functions (R305d) land in subsequent leaves.

Functional-clone decisions (not line-by-line):

- ``NodeIndex``: grok ``petgraph::graph::NodeIndex<u32>`` is a newtype around
  ``u32`` with no behavioural surface beyond indexing, so the Python port uses
  a bare ``int`` alias.
- ``petgraph::Graph<NodeKind, EdgeKind>`` -> :class:`_DiGraph`. The surface
  ``scope_graph/graph.rs`` actually exercises is narrow (``add_node`` / index
  access / ``add_edge`` / ``node_indices`` / in- and out-neighbours), so a
  bespoke adjacency list beats pulling in networkx. **Edge direction follows
  grok's convention** (preserved faithfully because the algorithms depend on
  it): ``add_edge(child, parent, w)`` records a child->parent link, so a node's
  *outgoing* neighbours are its parents (``parent_scope`` walks Outgoing) and
  its *incoming* neighbours are its children
  (``find_tightest_local_scope`` / ``insert_ref`` walk Incoming).
- ``matches!(nk, NodeKind::Def(_))`` -> ``nk.kind == NodeKindKind.DEF``.
- ``NodeKind::Scope(s)`` / ``Def(d)`` / ``Import(i)`` / ``Ref(r)`` enum
  construction -> ``NodeKind(kind=..., <payload>=...)`` (R301 tagged-union port).
- ``name(src: &[u8])`` byte comparisons preserved as-is (R301 ``name`` returns
  ``bytes``); ``String::from_utf8_lossy`` -> ``bytes.decode("utf-8", "replace")``.
- ``ScopeStack`` Rust iterator struct -> a Python class with ``__iter__`` /
  ``__next__`` (grok exposes it as a pub type; the iterator protocol carries
  the same semantics: yield current, advance to parent via the Outgoing
  ScopeToScope edge, stop at root).
- grok's ``pub(crate) graph`` / private ``root_idx`` / ``lang`` fields map to
  public Python attributes (Python has no crate visibility; R305c
  :class:`~minimax_code.xai_codebase_graph.scope_graph...ScopeGraphIndex` and
  the tests read them directly).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TypeAlias

from minimax_code.xai_codebase_graph.scope_graph.edges import EdgeKind
from minimax_code.xai_codebase_graph.scope_graph.nodes import (
    LocalDef,
    LocalImport,
    LocalScope,
    NodeKind,
    NodeKindKind,
    Reference,
    Symbol,
)
from minimax_code.xai_codebase_graph.types import Range

# === type aliases ==========================================================
# These mirror grok ``graph.rs`` L23-L39 ``pub type`` aliases. They are public
# (grok ``pub``) but only ``NodeIndex`` / ``QueryVersion`` / ``Snippet`` /
# ``ScopeGraph`` / ``ScopeStack`` are re-exported by ``scope_graph/mod.rs``;
# the three compound aliases stay internal to the crate (consumed by the
# bridge functions landing in R305d).

#: Graph node handle. grok ``petgraph::graph::NodeIndex<u32>`` -- a wrapped
#: ``u32`` indexing a node slot. Python uses a bare ``int``: petgraph's
#: newtype carries no behaviour beyond indexing.
NodeIndex: TypeAlias = int

#: A symbol name paired with its source range.
#: grok ``(Arc<str>, Range)`` -- Python ``str`` is already an immutable shared
#: reference, so no ``Arc`` analogue is needed.
SymbolWithRange: TypeAlias = tuple[str, Range]

#: A reference and the definition it resolved to (if any).
#: Format: ``(ref_name, ref_range, optional (def_name, def_range))``.
ReferenceWithDefinition: TypeAlias = tuple[str, Range, tuple[str, Range] | None]

#: Result of symbol extraction: ``(definitions, references, aliases)``.
#: Aliases are ``(alias_name, original_name)`` string pairs. grok uses
#: ``Arc<str>`` for alias halves to avoid extra allocation when merged into
#: the index; the Python port drops the ``Arc`` (``str`` suffices).
ExtractedSymbols: TypeAlias = tuple[
    "list[SymbolWithRange]",
    "list[SymbolWithRange]",
    "list[tuple[str, str]]",
]


# === QueryVersion ==========================================================


@dataclass(frozen=True)
class QueryVersion:
    """Version stamp tracking which tree-sitter queries built an index.

    Mirrors grok ``enum QueryVersion { #[default] Legacy, Version(u64) }`` as
    a frozen dataclass where ``version is None`` encodes ``Legacy`` and
    ``version == Some(v)`` encodes ``Version(v)``. This collapses the
    two-variant enum into a single optional field -- behaviourally identical
    (``None`` is the default, matching grok's ``#[default] Legacy``) and
    friendlier to serialise when the binary index format lands in R305c.

    A rebuild is needed when the index is ``Legacy`` (unknown provenance) or
    when the stamped version differs from the current query hash.
    """

    version: int | None = None

    def needs_rebuild(self, current_version: int) -> bool:
        """Return whether the index must be rebuilt for ``current_version``.

        Mirrors grok ``QueryVersion::needs_rebuild``:

        - ``Legacy`` (``version is None``) -> always rebuild (unknown queries).
        - ``Version(v)`` (``version == v``) -> rebuild iff ``v != current``.
        """
        return self.version is None or self.version != current_version


# === Snippet ===============================================================


@dataclass
class Snippet:
    """A code fragment with its text, line range, and associated symbols.

    Mirrors grok ``struct Snippet { data: String, line_range: Range<usize>,
    symbols: Vec<Symbol> }``. ``line_range`` is a half-open ``(start, end)``
    pair (grok ``std::ops::Range<usize>``). Not frozen -- grok derives
    ``Clone`` (not ``Copy``), so the Python mirror stays mutable to match.
    """

    data: str
    line_range: tuple[int, int]
    symbols: list[Symbol]


# === _DiGraph (R305b -- petgraph replacement) ==============================


class _DiGraph:
    """Lightweight directed graph mirroring the petgraph surface ScopeGraph uses.

    grok stores the scope graph in ``petgraph::Graph<NodeKind, EdgeKind>``. The
    only operations ``scope_graph/graph.rs`` exercises are: ``add_node`` /
    ``Graph::new`` / index access (``graph[idx]``) / ``add_edge`` /
    ``node_indices`` / ``edges_directed(_, Incoming|Outgoing)``. A bespoke
    adjacency list implements exactly that surface with zero dependencies.

    **Edge direction follows grok's convention** (preserved faithfully because
    the algorithms depend on it): ``add_edge(child, parent, w)`` records a
    child->parent link. Consequently a node's *outgoing* neighbours are its
    parents (``parent_scope`` walks Outgoing) and its *incoming* neighbours are
    its children (``find_tightest_local_scope`` / ``insert_ref`` walk Incoming
    to collect a scope's defs/imports/sub-scopes).
    """

    __slots__ = ("nodes", "out_adj", "in_adj")

    def __init__(self) -> None:
        self.nodes: list[NodeKind] = []
        # node -> list of (target, weight); target is the OUT-neighbour (parent)
        self.out_adj: list[list[tuple[int, EdgeKind]]] = []
        # node -> list of (source, weight); source is the IN-neighbour (child)
        self.in_adj: list[list[tuple[int, EdgeKind]]] = []

    def add_node(self, weight: NodeKind) -> int:
        """Add a node, returning its stable integer index (petgraph parity)."""
        idx = len(self.nodes)
        self.nodes.append(weight)
        self.out_adj.append([])
        self.in_adj.append([])
        return idx

    def __getitem__(self, idx: int) -> NodeKind:
        """Node weight access (grok ``graph[idx]``)."""
        return self.nodes[idx]

    def __len__(self) -> int:
        return len(self.nodes)

    def add_edge(self, a: int, b: int, weight: EdgeKind) -> None:
        """Add a directed edge ``a -> b`` with ``weight`` (grok ``add_edge``)."""
        self.out_adj[a].append((b, weight))
        self.in_adj[b].append((a, weight))

    def node_indices(self) -> Iterator[int]:
        """Iterate all node indices (grok ``node_indices``)."""
        return iter(range(len(self.nodes)))

    def out_edges(self, idx: int) -> list[tuple[int, EdgeKind]]:
        """Outgoing edges of ``idx`` as ``(target, weight)`` (Direction::Outgoing).

        Targets are ``idx``'s parents under grok's edge-direction convention.
        """
        return self.out_adj[idx]

    def in_edges(self, idx: int) -> list[tuple[int, EdgeKind]]:
        """Incoming edges of ``idx`` as ``(source, weight)`` (Direction::Incoming).

        Sources are ``idx``'s children under grok's edge-direction convention.
        """
        return self.in_adj[idx]


# === ScopeGraph (R305b) ====================================================


@dataclass
class ScopeGraph:
    """A graph of scopes and names in a single syntax tree.

    Mirrors grok ``ScopeGraph`` (graph.rs L70-L81): a petgraph DiGraph of
    :class:`NodeKind` nodes linked by :class:`EdgeKind`, plus the root scope
    index and the language string. grok's ``pub(crate) graph`` / private
    ``root_idx`` / ``lang`` map to public Python attributes -- Python has no
    crate visibility, and R305c's ``ScopeGraphIndex`` plus these tests read
    them directly.

    The 22 public methods group into six families (mirroring grok's layout):

    - construction: :meth:`new`, :meth:`from_symbols` (+ private scope_stack)
    - node-kind predicates: :meth:`is_definition`, :meth:`is_reference`,
      :meth:`is_import`
    - node lookup: :meth:`node_by_range`, :meth:`tightest_node_for_range`
      (+ private ``_scope_by_range`` / ``_parent_scope``)
    - scope insertion: :meth:`insert_local_scope`,
      :meth:`find_tightest_local_scope`
    - def/import/ref insertion: :meth:`insert_hoisted_def`,
      :meth:`insert_global_def`, :meth:`insert_local_def`,
      :meth:`insert_local_import`, :meth:`insert_ref`,
      :meth:`insert_ref_unconditional`
    - queries: :meth:`get_definitions`, :meth:`get_references`,
      :meth:`get_references_with_definitions`, :meth:`find_definition`,
      :meth:`find_references`
    """

    graph: _DiGraph
    root_idx: int
    lang: str

    @classmethod
    def new(cls, range: Range, lang: str) -> ScopeGraph:
        """Build an empty graph with a single root scope node (grok ``new``)."""
        graph = _DiGraph()
        root_idx = graph.add_node(NodeKind.scope_node(range))
        return cls(graph, root_idx, lang)

    # --- node-kind predicates (grok is_definition / is_reference / is_import) ---

    def is_definition(self, node_idx: int) -> bool:
        """``True`` iff node ``node_idx`` is a definition (grok ``is_definition``)."""
        return self.graph[node_idx].kind == NodeKindKind.DEF

    def is_reference(self, node_idx: int) -> bool:
        """``True`` iff node ``node_idx`` is a reference (grok ``is_reference``)."""
        return self.graph[node_idx].kind == NodeKindKind.REF

    def is_import(self, node_idx: int) -> bool:
        """``True`` iff node ``node_idx`` is an import (grok ``is_import``)."""
        return self.graph[node_idx].kind == NodeKindKind.IMPORT

    # --- node lookup ---

    def node_by_range(self, start_byte: int, end_byte: int) -> int | None:
        """First def/ref/import node whose range contains ``[start_byte, end_byte]``.

        Mirrors grok ``node_by_range`` (graph.rs L106-L114): iterates def/ref/
        import nodes (uses :meth:`NodeKind.range`, which for DEF is the scope
        range) and returns the first whose range contains the byte window.
        """
        for idx in self.graph.node_indices():
            if self.is_definition(idx) or self.is_reference(idx) or self.is_import(idx):
                node = self.graph[idx]
                node_range = node.range()
                if start_byte >= node_range.start_byte() and end_byte <= node_range.end_byte():
                    return idx
        return None

    def tightest_node_for_range(self, start_byte: int, end_byte: int) -> int | None:
        """Smallest (by byte size) definition node within ``[start_byte, end_byte]``.

        Mirrors grok ``tightest_node_for_range`` (graph.rs L116-L132): collects
        def nodes fully inside the window and returns the one with the smallest
        byte size (closest fit). Returns ``None`` if no def fits.
        """
        candidates: list[tuple[int, int]] = []
        for idx in self.graph.node_indices():
            if not self.is_definition(idx):
                continue
            node = self.graph[idx]
            node_range = node.range()
            if node_range.start_byte() >= start_byte and node_range.end_byte() <= end_byte:
                candidates.append((node_range.byte_size(), idx))
        if not candidates:
            return None
        candidates.sort(key=lambda pair: pair[0])
        return candidates[0][1]

    def _scope_by_range(self, range: Range, start: int) -> int | None:
        """Smallest scope at/below ``start`` that encompasses ``range`` (private).

        Mirrors grok ``scope_by_range`` (graph.rs L135-L152): descend through
        Incoming ScopeToScope children (sub-scopes) while they still contain
        ``range``; return the tightest match, else ``start`` itself.
        """
        target = self.graph[start].range()
        if target.contains(range):
            # Incoming ScopeToScope edges come FROM child scopes (edge direction
            # convention); their sources are the sub-scopes to recurse into.
            child_scopes = [
                source
                for source, weight in self.graph.in_edges(start)
                if weight == EdgeKind.SCOPE_TO_SCOPE
            ]
            for child_scope in child_scopes:
                tightest = self._scope_by_range(range, child_scope)
                if tightest is not None:
                    return tightest
            return start
        return None

    # --- scope insertion / lookup ---

    def insert_local_scope(self, new: LocalScope) -> None:
        """Insert a scope nested under its tightest enclosing scope (grok L155-L162).

        Locates the smallest existing scope containing ``new.range`` (starting
        from root), then links ``new`` to it as a child via ScopeToScope.
        """
        parent_scope = self._scope_by_range(new.range, self.root_idx)
        if parent_scope is not None:
            new_idx = self.graph.add_node(NodeKind(kind=NodeKindKind.SCOPE, scope=new))
            self.graph.add_edge(new_idx, parent_scope, EdgeKind.SCOPE_TO_SCOPE)

    def find_tightest_local_scope(self, range: Range) -> LocalScope:
        """Find the tightest local scope containing ``range`` (grok L165-L190).

        Walks Incoming ScopeToScope edges downward (children) as long as a
        child scope still contains ``range``. The root always qualifies, so a
        result is always returned (mirrors grok, which starts at ``root_idx``).
        """
        current = self.root_idx
        while True:
            found = False
            for source, weight in self.graph.in_edges(current):
                if weight != EdgeKind.SCOPE_TO_SCOPE:
                    continue
                node = self.graph[source]
                if node.kind == NodeKindKind.SCOPE and node.scope is not None:
                    if node.scope.range.contains(range):
                        current = source
                        found = True
                        break
            if not found:
                break
        result = self.graph[current]
        # grok ``unreachable!()`` if current isn't a scope -- root_idx
        # guarantees it (the root is always a scope node).
        assert result.scope is not None, "tightest local scope resolved to non-scope node"
        return result.scope

    # --- def insertion ---

    def _parent_scope(self, start: int) -> int | None:
        """Parent scope of ``start`` via Outgoing ScopeToScope (grok L226-L236).

        Only meaningful for scope nodes: walks the Outgoing ScopeToScope edge
        (under grok's convention, the parent) and returns its target.
        """
        node = self.graph[start]
        if node.kind == NodeKindKind.SCOPE:
            for target, weight in self.graph.out_edges(start):
                if weight == EdgeKind.SCOPE_TO_SCOPE:
                    return target
        return None

    def insert_hoisted_def(self, new: LocalDef) -> None:
        """Insert a def hoisted to its defining scope's parent (grok L202-L215).

        The def is attached not to the scope containing its identifier but to
        that scope's parent -- modelling languages (e.g. JS ``var``) that hoist
        declarations to the enclosing function/global scope. Falls back to the
        defining scope when there is no parent (already at root).
        """
        defining_scope = self._scope_by_range(new.range, self.root_idx)
        if defining_scope is not None:
            # grok ``parent_scope(defining_scope).unwrap_or(defining_scope)``: the
            # explicit ``is not None`` (NOT ``or``) is load-bearing -- ``or`` would
            # wrongly treat a legitimate root ``NodeIndex`` of 0 as falsy and fall
            # back to the defining scope, mis-hoisting root-level defs.
            parent = self._parent_scope(defining_scope)
            target_scope = parent if parent is not None else defining_scope
            new_idx = self.graph.add_node(NodeKind(kind=NodeKindKind.DEF, definition=new))
            self.graph.add_edge(new_idx, target_scope, EdgeKind.DEF_TO_SCOPE)

    def insert_global_def(self, new: LocalDef) -> None:
        """Insert a def at the root scope (grok L217-L223)."""
        new_idx = self.graph.add_node(NodeKind(kind=NodeKindKind.DEF, definition=new))
        self.graph.add_edge(new_idx, self.root_idx, EdgeKind.DEF_TO_SCOPE)

    def insert_local_def(self, new: LocalDef) -> None:
        """Insert a def in its defining scope (grok L238-L246).

        Attaches the def to the smallest scope containing its identifier.
        """
        defining_scope = self._scope_by_range(new.range, self.root_idx)
        if defining_scope is not None:
            new_idx = self.graph.add_node(NodeKind(kind=NodeKindKind.DEF, definition=new))
            self.graph.add_edge(new_idx, defining_scope, EdgeKind.DEF_TO_SCOPE)

    # --- import / ref insertion ---

    def insert_local_import(self, new: LocalImport) -> None:
        """Insert an import in its defining scope (grok L192-L200).

        Attaches the import to the smallest scope containing its identifier.
        """
        defining_scope = self._scope_by_range(new.range, self.root_idx)
        if defining_scope is not None:
            new_idx = self.graph.add_node(NodeKind(kind=NodeKindKind.IMPORT, import_=new))
            self.graph.add_edge(new_idx, defining_scope, EdgeKind.IMPORT_TO_SCOPE)

    def _scope_stack(self, start: int) -> ScopeStack:
        """Build a :class:`ScopeStack` iterator rooted at ``start`` (grok L248-L253)."""
        return ScopeStack(self, start)

    def insert_ref(self, new: Reference, src: bytes) -> None:
        """Insert a reference, resolving it to in-scope defs/imports (grok L255-L315).

        Walks the scope stack from the reference's enclosing scope up to root.
        In each scope, collects defs/imports whose identifier matches the
        reference's. Namespace filtering: if both ref and def carry a symbol_id
        but in different namespaces, the def is skipped (an empty symbol on
        either side matches all namespaces). Only adds the ref node if at least
        one candidate def/import was found, then links it to each via RefToDef
        / RefToImport.
        """
        possible_defs: list[int] = []
        possible_imports: list[int] = []
        local_scope_idx = self._scope_by_range(new.range, self.root_idx)
        if local_scope_idx is not None:
            ref_name = new.name(src)
            for scope in self._scope_stack(local_scope_idx):
                # candidate definitions attached to this scope (Incoming
                # DefToScope -- edge direction convention: def -> scope).
                for source, weight in self.graph.in_edges(scope):
                    if weight != EdgeKind.DEF_TO_SCOPE:
                        continue
                    node = self.graph[source]
                    if node.kind != NodeKindKind.DEF or node.definition is None:
                        continue
                    defn = node.definition
                    if ref_name != defn.name(src):
                        continue
                    d_sym = defn.symbol_id
                    r_sym = new.symbol_id
                    # both carry a symbol id but in different namespaces -> skip
                    if (
                        d_sym is not None
                        and r_sym is not None
                        and d_sym.namespace_idx != r_sym.namespace_idx
                    ):
                        continue
                    possible_defs.append(source)
                # candidate imports attached to this scope
                for source, weight in self.graph.in_edges(scope):
                    if weight != EdgeKind.IMPORT_TO_SCOPE:
                        continue
                    node = self.graph[source]
                    if node.kind != NodeKindKind.IMPORT or node.import_ is None:
                        continue
                    if ref_name == node.import_.name(src):
                        possible_imports.append(source)
        if possible_defs or possible_imports:
            ref_idx = self.graph.add_node(NodeKind(kind=NodeKindKind.REF, reference=new))
            for def_idx in possible_defs:
                self.graph.add_edge(ref_idx, def_idx, EdgeKind.REF_TO_DEF)
            for imp_idx in possible_imports:
                self.graph.add_edge(ref_idx, imp_idx, EdgeKind.REF_TO_IMPORT)

    def insert_ref_unconditional(self, new: Reference) -> None:
        """Insert a reference node without resolution (grok L317-L322, cross-file).

        Adds the ref as a free node with no RefToDef/RefToImport edges. Used
        for cross-file references where the definition lives in another file
        and is resolved later by :class:`ScopeGraphIndex`.
        """
        self.graph.add_node(NodeKind(kind=NodeKindKind.REF, reference=new))

    # --- queries ---

    def get_definitions(self, src: bytes) -> list[tuple[str, Range]]:
        """All definitions with their decoded names (grok L324-L337).

        Returns ``(name, identifier_range)`` for each def node. The range is
        the identifier range (``LocalDef.range``), not the scope range.
        """
        result: list[tuple[str, Range]] = []
        for idx in self.graph.node_indices():
            node = self.graph[idx]
            if node.kind == NodeKindKind.DEF and node.definition is not None:
                name = node.definition.name(src).decode("utf-8", "replace")
                result.append((name, node.definition.range))
        return result

    def get_references(self, src: bytes) -> list[tuple[str, Range]]:
        """All references with their decoded names (grok L339-L352)."""
        result: list[tuple[str, Range]] = []
        for idx in self.graph.node_indices():
            node = self.graph[idx]
            if node.kind == NodeKindKind.REF and node.reference is not None:
                name = node.reference.name(src).decode("utf-8", "replace")
                result.append((name, node.reference.range))
        return result

    def get_references_with_definitions(
        self, src: bytes
    ) -> list[ReferenceWithDefinition]:
        """All references with their resolved definition (if any) (grok L354-L383).

        For each ref node, walks the first Outgoing RefToDef edge to its def
        target and carries ``(def_name, def_range)``; unresolved refs carry
        ``None`` (e.g. :meth:`insert_ref_unconditional` nodes or refs that
        resolved only to imports).
        """
        result: list[ReferenceWithDefinition] = []
        for idx in self.graph.node_indices():
            node = self.graph[idx]
            if node.kind != NodeKindKind.REF or node.reference is None:
                continue
            reference = node.reference
            ref_name = reference.name(src).decode("utf-8", "replace")
            ref_range = reference.range
            def_info: tuple[str, Range] | None = None
            for target, weight in self.graph.out_edges(idx):
                if weight != EdgeKind.REF_TO_DEF:
                    continue
                target_node = self.graph[target]
                if target_node.kind == NodeKindKind.DEF and target_node.definition is not None:
                    def_name = target_node.definition.name(src).decode("utf-8", "replace")
                    def_info = (def_name, target_node.definition.range)
                    break
            result.append((ref_name, ref_range, def_info))
        return result

    def find_definition(self, name: str, src: bytes) -> Range | None:
        """Range of the first definition whose name matches (grok L385-L395)."""
        needle = name.encode("utf-8")
        for idx in self.graph.node_indices():
            node = self.graph[idx]
            if node.kind == NodeKindKind.DEF and node.definition is not None:
                if node.definition.name(src) == needle:
                    return node.definition.range
        return None

    def find_references(self, name: str, src: bytes) -> list[Range]:
        """Ranges of all references whose name matches (grok L397-L410)."""
        needle = name.encode("utf-8")
        result: list[Range] = []
        for idx in self.graph.node_indices():
            node = self.graph[idx]
            if node.kind == NodeKindKind.REF and node.reference is not None:
                if node.reference.name(src) == needle:
                    result.append(node.reference.range)
        return result

    @classmethod
    def from_symbols(
        cls,
        definitions: list[tuple[str, Range]],
        references: list[tuple[str, Range]],
    ) -> ScopeGraph:
        """Build a minimal graph from pre-extracted symbols (grok L416-L452).

        Used for fast indexing where defs/refs are already extracted. The root
        scope spans the first def/ref's range (or a default empty range);
        defs attach to root via DefToScope; refs are added as free nodes (no
        resolution edges).
        """
        if definitions:
            root_range = definitions[0][1]
        elif references:
            root_range = references[0][1]
        else:
            root_range = Range()
        graph = _DiGraph()
        root_idx = graph.add_node(NodeKind.scope_node(root_range))
        for _name, rng in definitions:
            local_def = LocalDef.new(rng, None, LocalScope.new(root_range))
            def_idx = graph.add_node(NodeKind(kind=NodeKindKind.DEF, definition=local_def))
            graph.add_edge(def_idx, root_idx, EdgeKind.DEF_TO_SCOPE)
        for _name, rng in references:
            reference = Reference.new(rng, None)
            graph.add_node(NodeKind(kind=NodeKindKind.REF, reference=reference))
        return cls(graph, root_idx, "")


# === ScopeStack (R305b) ====================================================


class ScopeStack:
    """Iterator walking a node's enclosing scopes up to root (grok L455-L479).

    grok models this as ``struct ScopeStack<'a> { scope_graph, start }`` impl
    ``Iterator``: each ``next`` yields the current node then advances ``start``
    to its parent (the Outgoing ScopeToScope edge's target). The Python port
    mirrors that -- a stateful iterator carrying the graph + current index.
    """

    __slots__ = ("_scope_graph", "_current")

    def __init__(self, scope_graph: ScopeGraph, start: int) -> None:
        self._scope_graph = scope_graph
        self._current: int | None = start

    def __iter__(self) -> ScopeStack:
        return self

    def __next__(self) -> int:
        current = self._current
        if current is None:
            raise StopIteration
        # advance to parent via the Outgoing ScopeToScope edge (grok parity)
        parent: int | None = None
        for target, weight in self._scope_graph.graph.out_edges(current):
            if weight == EdgeKind.SCOPE_TO_SCOPE:
                parent = target
                break
        self._current = parent
        return current


# === ScopeGraphResult (R305b, grok mod.rs L20-L25) =========================


@dataclass
class ScopeGraphResult:
    """Result of building a scope graph: the graph plus alias pairs.

    Mirrors grok ``ScopeGraphResult`` (scope_graph/mod.rs L20-L25). The
    ``build_scope_graph`` convenience wrapper that produces this (grok mod.rs
    L30-L38) lands with the tree-sitter bridge in R305d; the container itself
    is pure data and lands here alongside :class:`ScopeGraph`.
    """

    graph: ScopeGraph
    aliases: list[tuple[str, str]]


__all__ = [
    "ExtractedSymbols",
    "NodeIndex",
    "QueryVersion",
    "ReferenceWithDefinition",
    "ScopeGraph",
    "ScopeGraphResult",
    "ScopeStack",
    "Snippet",
    "SymbolWithRange",
]
