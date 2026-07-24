"""Graphlib edge-encoding + Graph node/edge primitives (R242+R243+R244).

Mirrors the type vocabulary + edge-id encoding helpers + the ``Graph``
struct's node-primitive method subset of grok's vendored ``graphlib_rust``
0.0.2 crate (upstream ``r3alst/graphlib-rust``, Apache-2.0) -- the Rust port
of dagre.js's graphlib, used by the vendored ``dagre_rust`` layout engine and
the ``mermaid-to-svg`` render path.

``graphlib_rust``'s only dependency is ``ordered_hashmap`` (single path dep,
migrated in R241); the crate is pure logic with no external crates, and the
re-audit checklist attests "No ``unsafe``, no filesystem / env / network I/O".

**Second leaf of the ``data_structures`` package (R242) -- edge-encoding
vocabulary.** The types and pure functions that :class:`Graph` builds on:

- :data:`DEFAULT_EDGE_NAME`, :data:`GRAPH_NODE`, :data:`EDGE_KEY_DELIM` --
  sentinel + delimiter constants driving edge-id composition.
- :class:`Edge` -- the ``{v, w, name}`` edge object (frozen value object).
- :class:`GraphOption` -- the ``{directed, multigraph, compound}`` construction
  options (mirrors ``#[derive(Default)]`` -- all fields ``None``).
- :func:`edge_args_to_id` / :func:`edge_args_to_obj` / :func:`edge_obj_to_id` --
  the pure edge-id encoding helpers (module-private in grok; the ``Graph``
  methods call them to canonicalise ``(v, w, name)`` into a unique key).

**Third leaf of the ``data_structures`` package (R243) -- ``Graph`` struct +
node primitives.** Building on the R242 vocabulary:

- :class:`NodeLabelValue` / :class:`NodeLabelFactory` -- the tagged-union
  default-node-label variants (mirrors grok ``enum DefaultNodeLabel``).
- :class:`Graph` -- the ``Graph<GL, N, E>`` core struct with construction +
  flag queries + graph-label accessors + default-node-label machinery + node
  CRUD + compound parent/child queries + adjacency queries.

**Fourth leaf of the ``data_structures`` package (R244) -- ``Graph`` edge
methods + edge-dependent node methods.** Closes the complete Graph CRUD
surface, building on the R242 vocabulary + R243 node primitives:

- :class:`EdgeLabelValue` / :class:`EdgeLabelFactory` -- the tagged-union
  default-edge-label variants (mirrors grok ``enum DefaultEdgeLabel``).
- :meth:`Graph.set_edge` / :meth:`Graph.edge` / :meth:`Graph.edge_mut` /
  :meth:`Graph.remove_edge` / :meth:`Graph.has_edge` / :meth:`Graph.in_edges`
  / :meth:`Graph.out_edges` / :meth:`Graph.node_edges` / :meth:`Graph.edge_count`
  / :meth:`Graph.edges` / :meth:`Graph.set_path` + the edge-dependent node
  methods :meth:`Graph.remove_node` (cascade edge delete + parent/child detach)
  and :meth:`Graph.filter_nodes` (predicate filter with parent-chain
  re-threading). The two OrderedHashMap multiplicity helpers
  (:func:`_increment_or_init_entry` / :func:`_decrement_or_remove_entry`) and
  the parent-resolution helper (:func:`_find_parent`) are module-private.

The graph algorithms (``dfs`` / ``preorder`` / ``postorder``) migrate in a
later leaf.

Edge-id encoding
----------------
An edge is uniquely identified by composing its canonical ``(v, w, name)``
triple with :data:`EDGE_KEY_DELIM` (``\\x01``). For an undirected graph the
endpoints are swapped so ``v <= w`` lexicographically, making ``(v, w)`` and
``(w, v)`` produce the same id (the same edge). When no name is supplied the
:data:`DEFAULT_EDGE_NAME` sentinel (``\\x00``) is substituted, so named and
anonymous edges never collide.

Graph node primitives
---------------------
:class:`Graph` models a directed/undirected, optionally-multigraph,
optionally-compound graph. ``GL`` is the graph-label type, ``N`` the
node-label type, ``E`` the edge-label type -- all unbound at runtime
(Python's duck typing stands in for grok's ``N: Default + Clone + Debug``
bounds). The grok ``N::default()`` fallback (used when a node is created
with no explicit label and the default-label variant is ``Val(None)``) maps
to a caller-supplied ``node_default_factory`` (default ``lambda: None``) --
dagre's typical ``N`` is the unit ``()``, for which ``None`` is the faithful
Python stand-in.

The grok constructor (``graph.rs`` lines 132-148) has a ``multigraph``
double-check bug -- it checks ``multigraph`` twice (lines 132-136 and
138-142, the latter dead code) where the second block was meant to check
``compound``. The migrated constructor checks each flag exactly once, which
is what grok intended; see :meth:`Graph.__init__`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from minimax_code.data_structures.ordered_hashmap import OrderedHashMap

# Sentinel substituted for an absent edge name in the id composition -- chosen
# below EDGE_KEY_DELIM so a named edge can never collide with an anonymous one.
DEFAULT_EDGE_NAME = "\x00"

# The synthetic root node of a compound graph's parent/child forest.
GRAPH_NODE = "\x00"

# Delimiter joining (v, w, name) into a unique edge-id string.
EDGE_KEY_DELIM = "\x01"


@dataclass(frozen=True, slots=True)
class Edge:
    """An edge object ``{v, w, name}`` (mirrors grok ``Edge``, ``#[derive(Debug, Clone)]``).

    ``v`` is the source node id, ``w`` the target, and ``name`` the optional
    multigraph discriminator (``None`` for anonymous edges). Frozen + slotted
    because an edge is a value object: once created its identity is immutable,
    and grok's ``Clone`` semantics map to Python's implicit reference copying.
    """

    v: str
    w: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class GraphOption:
    """Construction options for :class:`Graph` (mirrors grok ``GraphOption``).

    ``directed`` / ``multigraph`` / ``compound`` default to ``None`` (mirrors
    ``#[derive(Default)]``); :class:`Graph`'s constructor treats ``None`` as
    "use the default for that flag" (directed=True, multigraph=False,
    compound=False), so a wholly-default ``GraphOption()`` is equivalent to
    passing no options at all.
    """

    directed: bool | None = None
    multigraph: bool | None = None
    compound: bool | None = None


def edge_args_to_id(is_directed: bool, v: str, w: str, name: str | None) -> str:
    """Compose the canonical edge-id string for ``(v, w, name)``.

    For an undirected graph the endpoints are swapped when ``v > w`` so that
    ``(v, w)`` and ``(w, v)`` produce the same id (the same edge). The
    optional ``name`` is substituted with :data:`DEFAULT_EDGE_NAME` when
    absent, so a named edge never collides with an anonymous one.
    """
    if not is_directed and v > w:
        v, w = w, v
    name_part = name if name is not None else DEFAULT_EDGE_NAME
    return f"{v}{EDGE_KEY_DELIM}{w}{EDGE_KEY_DELIM}{name_part}"


def edge_args_to_obj(is_directed: bool, v: str, w: str, name: str | None) -> Edge:
    """Build the canonical :class:`Edge` object for ``(v, w, name)``.

    Applies the same endpoint-swap normalisation as :func:`edge_args_to_id`
    for undirected graphs, so the returned :class:`Edge` is the canonical
    form stored in the graph's edge tables.
    """
    if not is_directed and v > w:
        v, w = w, v
    return Edge(v=v, w=w, name=name)


def edge_obj_to_id(is_directed: bool, edge: Edge) -> str:
    """Compose the canonical edge-id for an :class:`Edge` object.

    Thin adapter over :func:`edge_args_to_id` -- the canonical id of an edge
    object is the id of its ``(v, w, name)`` triple.
    """
    return edge_args_to_id(is_directed, edge.v, edge.w, edge.name)


# === R243: Graph node primitives ===========================================
#
# Default-node-label tagged union + the ``Graph<GL, N, E>`` core struct's
# node-primitive method subset. Mirrors grok ``graphlib_rust/src/graph.rs``
# lines 25-33 (``enum DefaultNodeLabel``) and 45-611 (``Graph`` struct +
# node methods). Edge methods (lines 613-889) + the node methods that depend
# on them (``remove_node`` 374-416, ``filter_nodes`` 576-611) migrate in R243b.

#: Node-label type parameter (the label carried by each graph node).
N = TypeVar("N")

#: Graph-label type parameter (the label on the graph itself).
GL = TypeVar("GL")

#: Edge-label type parameter (the label carried by each graph edge).
E = TypeVar("E")


@dataclass(frozen=True, slots=True)
class NodeLabelValue(Generic[N]):
    """Default-node-label variant: a fixed value (mirrors ``DefaultNodeLabel::Val``).

    When ``value`` is ``None``, :meth:`Graph.default_node_label` falls back to
    the graph's ``node_default_factory`` (the Python stand-in for grok's
    ``N::default()``). Frozen + slotted because this is a value object: once
    set, the default does not mutate. Carries a field so the R236
    ``frozen+slots+fieldless`` trap does not apply.
    """

    value: N | None = None


@dataclass(slots=True)
class NodeLabelFactory(Generic[N]):
    """Default-node-label variant: a per-node factory (mirrors ``DefaultNodeLabel::Func``).

    The factory is invoked with the node id each time :meth:`Graph.set_node`
    needs a label for a node that was created without an explicit value. Not
    frozen: it wraps a callable (a behaviour, not a value), so value-object
    immutability does not apply.
    """

    factory: Callable[[str], N | None]


#: The tagged union of default-node-label variants (mirrors ``enum DefaultNodeLabel``).
DefaultNodeLabel = NodeLabelValue[N] | NodeLabelFactory[N]


@dataclass(frozen=True, slots=True)
class EdgeLabelValue(Generic[E]):
    """Default-edge-label variant: a fixed value (mirrors ``DefaultEdgeLabel::Val``).

    When ``value`` is ``None``, :meth:`Graph.default_edge_label` falls back to
    the graph's ``edge_default_factory`` (the Python stand-in for grok's
    ``E::default()``). Frozen + slotted because this is a value object: once
    set, the default does not mutate. Carries a field so the R236
    ``frozen+slots+fieldless`` trap does not apply.
    """

    value: E | None = None


@dataclass(slots=True)
class EdgeLabelFactory(Generic[E]):
    """Default-edge-label variant: a per-edge factory (mirrors ``DefaultEdgeLabel::Func``).

    The factory is invoked with the canonical edge id each time
    :meth:`Graph.set_edge` needs a label for an edge that was created without
    an explicit value. Not frozen: it wraps a callable (a behaviour, not a
    value), so value-object immutability does not apply.
    """

    factory: Callable[[str], E | None]


#: The tagged union of default-edge-label variants (mirrors ``enum DefaultEdgeLabel``).
DefaultEdgeLabel = EdgeLabelValue[E] | EdgeLabelFactory[E]


class Graph(Generic[GL, N, E]):
    """A directed/undirected, optionally-multigraph, optionally-compound graph.

    Mirrors grok ``Graph<GL, N, E>`` -- the core struct of the vendored
    ``graphlib_rust`` crate. R243 migrates the struct skeleton + the
    node-primitive method subset (construction, flag queries, graph-label
    accessors, default-node-label machinery, node CRUD, compound parent/child
    queries, adjacency queries). The edge-method subset (``set_edge`` /
    ``edge`` / ``remove_edge`` / ``in_edges`` / ``out_edges`` / ...) and the
    node methods that depend on them (``remove_node`` / ``filter_nodes``)
    migrate in R243b; this leaf carries only the zero-edge-dependency node
    surface so every migrated method is self-contained and testable.
    """

    __slots__ = (
        "_is_directed",
        "_is_multigraph",
        "_is_compound",
        "_label",
        "_default_node_label_fn",
        "_node_default_factory",
        "_default_edge_label_fn",
        "_edge_default_factory",
        "_nodes",
        "_in",
        "_preds",
        "_out",
        "_sucs",
        "_edge_objs",
        "_edge_labels",
        "_node_count",
        "_edge_count",
        "_parent",
        "_children",
    )

    def __init__(
        self,
        opts: GraphOption | None = None,
        *,
        node_default_factory: Callable[[], N] | None = None,
        edge_default_factory: Callable[[], E] | None = None,
    ) -> None:
        """Construct a new graph (mirrors grok ``Graph::new`` + ``Graph::default``).

        ``opts`` follows grok ``GraphOption``: each flag defaults to its grok
        default when ``None`` (directed=True, multigraph=False, compound=False),
        so ``Graph()`` and ``Graph(GraphOption())`` are equivalent.

        ``node_default_factory`` is the Python stand-in for grok's
        ``N::default()``: invoked when a node is created with no explicit label
        and the default-node-label variant is :class:`NodeLabelValue` with
        ``value=None``. Defaults to ``lambda: None`` (the faithful stand-in for
        dagre's typical ``N = ()`` unit type).

        ``edge_default_factory`` is the symmetric stand-in for grok's
        ``E::default()``: invoked when an edge is created with no explicit label
        and the default-edge-label variant is :class:`EdgeLabelValue` with
        ``value=None``. Defaults to ``lambda: None``.

        .. note::

            The grok constructor (``graph.rs`` lines 132-148) has a
            ``multigraph`` double-check bug -- it checks ``multigraph`` twice
            (lines 132-136 and 138-142, the latter dead code) where the second
            block was meant to check ``compound``. This migration checks each
            flag exactly once, which is what grok intended.
        """
        # mirrors grok Graph::default() field initialisers
        self._is_directed: bool = True
        self._is_multigraph: bool = False
        self._is_compound: bool = False
        self._label: GL | None = None
        self._default_node_label_fn: DefaultNodeLabel[N] = NodeLabelValue()
        self._node_default_factory: Callable[[], N] = (
            node_default_factory if node_default_factory is not None else (lambda: None)
        )
        self._default_edge_label_fn: DefaultEdgeLabel[E] = EdgeLabelValue()
        self._edge_default_factory: Callable[[], E] = (
            edge_default_factory if edge_default_factory is not None else (lambda: None)
        )
        self._nodes: OrderedHashMap[str, N] = OrderedHashMap()
        self._in: OrderedHashMap[str, OrderedHashMap[str, Edge]] = OrderedHashMap()
        self._preds: OrderedHashMap[str, OrderedHashMap[str, int]] = OrderedHashMap()
        self._out: OrderedHashMap[str, OrderedHashMap[str, Edge]] = OrderedHashMap()
        self._sucs: OrderedHashMap[str, OrderedHashMap[str, int]] = OrderedHashMap()
        self._edge_objs: OrderedHashMap[str, Edge] = OrderedHashMap()
        self._edge_labels: OrderedHashMap[str, E] = OrderedHashMap()
        self._node_count: int = 0
        self._edge_count: int = 0
        self._parent: OrderedHashMap[str, str] = OrderedHashMap()
        self._children: OrderedHashMap[str, OrderedHashMap[str, bool]] = OrderedHashMap()

        if opts is not None:
            self._is_directed = opts.directed if opts.directed is not None else True
            self._is_multigraph = (
                opts.multigraph if opts.multigraph is not None else False
            )
            self._is_compound = opts.compound if opts.compound is not None else False

        if self._is_compound:
            # mirrors grok graph.rs:151-159 -- seed the synthetic GRAPH_NODE's
            # child list so the parent/child forest has a single implicit root.
            self._children.insert(GRAPH_NODE, OrderedHashMap())

    # === Graph functions ============================================

    def is_directed(self) -> bool:
        """Whether the graph was created with ``directed=True`` (mirrors grok)."""
        return self._is_directed

    def is_multigraph(self) -> bool:
        """Whether the graph was created with ``multigraph=True`` (mirrors grok)."""
        return self._is_multigraph

    def is_compound(self) -> bool:
        """Whether the graph was created with ``compound=True`` (mirrors grok)."""
        return self._is_compound

    def set_graph(self, label: GL) -> Graph[GL, N, E]:
        """Set the graph label, returning ``self`` for chaining (mirrors grok)."""
        self._label = label
        return self

    def graph(self) -> GL | None:
        """Return the graph label (mirrors grok; ``None`` until :meth:`set_graph`)."""
        return self._label

    def graph_mut(self) -> GL | None:
        """Return a mutable handle on the graph label (mirrors grok ``graph_mut``).

        Python values are boxed behind references, so the returned object IS
        the live label -- mutating it (when mutable) mutates the graph's label
        in place.
        """
        return self._label

    # === Node functions =============================================

    def set_default_node_label(
        self, new_default: DefaultNodeLabel[N]
    ) -> Graph[GL, N, E]:
        """Set the default node label (mirrors grok ``set_default_node_label``).

        ``new_default`` is either a :class:`NodeLabelValue` (fixed value, or
        ``None`` to fall back to ``node_default_factory``) or a
        :class:`NodeLabelFactory` (invoked per node id).
        """
        self._default_node_label_fn = new_default
        return self

    def default_node_label(self, node_id: str) -> N:
        """Resolve the default label for ``node_id`` (mirrors grok).

        - :class:`NodeLabelFactory` -> invoke ``factory(node_id)``; if it
          returns ``None``, fall back to ``node_default_factory()`` (grok
          would ``unwrap()``-panic on a ``None`` factory return).
        - :class:`NodeLabelValue` with a value -> that value.
        - :class:`NodeLabelValue` with ``value=None`` ->
          ``node_default_factory()`` (the Python stand-in for grok
          ``N::default()``).
        """
        default_fn = self._default_node_label_fn
        if isinstance(default_fn, NodeLabelFactory):
            label = default_fn.factory(node_id)
            if label is not None:
                return label
            return self._node_default_factory()
        if default_fn.value is not None:
            return default_fn.value
        return self._node_default_factory()

    def node_count(self) -> int:
        """Return the number of nodes (mirrors grok; O(1))."""
        return self._node_count

    def nodes(self) -> list[str]:
        """Return all node ids in insertion order (mirrors grok; O(1)).

        A node's membership is independent of its parent in the compound
        forest -- subnodes are included.
        """
        return list(self._nodes.keys())

    def sources(self) -> list[str]:
        """Return nodes with no in-edges (mirrors grok; O(|V|)).

        A node counts as a source when its ``_in`` table is absent or empty.
        Until edges migrate (R243b) every node is a source.
        """
        result: list[str] = []
        for node_id in self._nodes.keys():
            in_edges = self._in.get(node_id)
            if in_edges is None or len(in_edges) == 0:
                result.append(node_id)
        return result

    def sinks(self) -> list[str]:
        """Return nodes with no out-edges (mirrors grok; O(|V|)).

        A node counts as a sink when its ``_out`` table is absent or empty.
        Until edges migrate (R243b) every node is a sink.
        """
        result: list[str] = []
        for node_id in self._nodes.keys():
            out_edges = self._out.get(node_id)
            if out_edges is None or len(out_edges) == 0:
                result.append(node_id)
        return result

    def set_nodes(self, node_ids: list[str], value: N | None) -> Graph[GL, N, E]:
        """Invoke :meth:`set_node` for each id (mirrors grok ``set_nodes``)."""
        for node_id in node_ids:
            self.set_node(node_id, value)
        return self

    def set_node(self, v: str, value: N | None) -> Graph[GL, N, E]:
        """Create or update node ``v`` (mirrors grok ``set_node``; O(1)).

        If ``v`` already exists and ``value`` is supplied, the label is
        updated; if ``value`` is ``None`` the existing label is left untouched.
        On first creation, a missing ``value`` resolves to
        :meth:`default_node_label`. The compound branch seeds the parent/child
        forest (``_parent[v] = GRAPH_NODE`` + registers ``v`` under
        ``GRAPH_NODE``'s children); the adjacency tables (``_in`` / ``_preds``
        / ``_out`` / ``_sucs``) are initialised empty for the new node.
        """
        # NOTE: guard on KEY presence (``v in self._nodes``), not value
        # non-None. Node labels may legitimately be ``None`` (the default
        # node factory returns ``lambda: None``), so ``self._nodes.get(v) is
        # not None`` wrongly evaluates to False for a node whose label is
        # ``None`` and falls through to the create branch -- which re-runs
        # ``self._children.insert(v, OrderedHashMap())`` and REPLACES the
        # node's existing child list with a fresh empty one. That silently
        # orphaned every child during ``set_parent`` re-parenting (the
        # ``set_node(v, None)`` it issues would wipe the new parent's
        # children). Mirrors grok's ``self.nodes.contains_key(v)`` key
        # presence check, not an ``Option::is_some`` on the value.
        if v in self._nodes:
            if value is not None:
                self._nodes.insert(v, value)
            return self

        if value is not None:
            self._nodes.insert(v, value)
        else:
            self._nodes.insert(v, self.default_node_label(v))

        if self._is_compound:
            self._parent.insert(v, GRAPH_NODE)
            self._children.insert(v, OrderedHashMap())
            graph_node_children = self._children.get(GRAPH_NODE)
            if graph_node_children is None:
                graph_node_children = OrderedHashMap()
                self._children.insert(GRAPH_NODE, graph_node_children)
            graph_node_children.insert(v, True)

        self._in.insert(v, OrderedHashMap())
        self._preds.insert(v, OrderedHashMap())
        self._out.insert(v, OrderedHashMap())
        self._sucs.insert(v, OrderedHashMap())
        self._node_count += 1
        return self

    def node(self, v: str) -> N | None:
        """Return node ``v``'s label, or ``None`` if absent (mirrors grok)."""
        return self._nodes.get(v)

    def node_mut(self, v: str) -> N | None:
        """Return a mutable handle on node ``v``'s label (mirrors grok ``node_mut``)."""
        return self._nodes.get_mut(v)

    def has_node(self, v: str) -> bool:
        """Return ``True`` if node ``v`` exists (mirrors grok)."""
        return v in self._nodes

    def remove_node(self, v: str) -> Graph[GL, N, E]:
        """Remove node ``v`` and its incident edges (mirrors grok ``remove_node``).

        No-op if ``v`` is absent. In a compound graph, detaches ``v`` from its
        parent, re-parents each child onto the synthetic ``GRAPH_NODE`` root,
        and removes ``v`` from the child forest. All in-edges and out-edges are
        removed via :meth:`remove_edge_with_obj` (decrementing the
        predecessor/successor counts), then the per-node adjacency tables are
        dropped. Mirrors grok's average-case O(1) structural work; the per-edge
        removal is O(degree(v)).
        """
        if v not in self._nodes:
            return self

        self._nodes.remove(v)

        if self._is_compound:
            self._remove_from_parents_child_list(v)
            if v in self._parent:
                self._parent.remove(v)
            # Re-parent each surviving child onto the synthetic root before
            # dropping v's child list (children() reads _children, so it must
            # still be intact here).
            for child_id in self.children(v):
                self.set_parent(child_id, None)
            self._children.remove(v)

        # Cascade-remove every incident edge via the edge removal path so the
        # predecessor/successor integer counters and adjacency maps stay
        # consistent. grok snapshots the edge ids first because remove mutates
        # the very map being iterated.
        in_edges = self._in.get(v)
        if in_edges is not None:
            edge_ids = list(in_edges.keys())
            for edge_id in edge_ids:
                edge = self._edge_objs.get(edge_id)
                if edge is not None:
                    self.remove_edge_with_obj(edge)
            self._in.remove(v)

        self._preds.remove(v)

        out_edges = self._out.get(v)
        if out_edges is not None:
            edge_ids = list(out_edges.keys())
            for edge_id in edge_ids:
                edge = self._edge_objs.get(edge_id)
                if edge is not None:
                    self.remove_edge_with_obj(edge)
            self._out.remove(v)

        self._sucs.remove(v)
        self._node_count -= 1
        return self

    def set_parent(self, v: str, parent: str | None) -> Graph[GL, N, E]:
        """Set ``parent`` as the parent of ``v``, or detach ``v`` (mirrors grok).

        ``parent=None`` detaches ``v`` back to the synthetic ``GRAPH_NODE`` root.
        Raises ``RuntimeError`` on a non-compound graph, or when ``parent`` is
        a descendant of ``v`` (would create a cycle). Both endpoints are
        ensured to exist via :meth:`set_node`.
        """
        if not self._is_compound:
            raise RuntimeError("Cannot set parent in a non-compound graph")

        if parent is None:
            new_parent = GRAPH_NODE
        else:
            new_parent = parent
            # cycle check (grok graph.rs:439-450): walk parent's ancestor chain;
            # if it crosses v, setting parent would close a cycle through v.
            ancestor = new_parent
            while True:
                next_ancestor = self.parent(ancestor)
                if next_ancestor is None:
                    break
                if next_ancestor == v:
                    raise RuntimeError(
                        f"Setting {new_parent} as parent of {v} would create a cycle"
                    )
                ancestor = next_ancestor
            self.set_node(new_parent, None)

        self.set_node(v, None)
        self._remove_from_parents_child_list(v)
        self._parent.insert(v, new_parent)
        siblings = self._children.get(new_parent)
        if siblings is None:
            siblings = OrderedHashMap()
            self._children.insert(new_parent, siblings)
        siblings.insert(v, True)
        return self

    def _remove_from_parents_child_list(self, v: str) -> None:
        """Detach ``v`` from its current parent's child list (mirrors grok)."""
        current_parent = self._parent.get(v)
        if current_parent is not None:
            siblings = self._children.get(current_parent)
            if siblings is not None:
                siblings.remove(v)

    def parent(self, v: str) -> str | None:
        """Return ``v``'s parent, or ``None`` (mirrors grok; compound only).

        Returns ``None`` for the synthetic ``GRAPH_NODE`` root or any node in
        a non-compound graph.
        """
        if self._is_compound:
            p = self._parent.get(v)
            if p is not None and p != GRAPH_NODE:
                return p
        return None

    def children(self, v: str) -> list[str]:
        """Return ``v``'s direct children (mirrors grok; O(1)).

        In a non-compound graph, ``children(GRAPH_NODE)`` returns every node
        (the implicit single-level forest); any other node id returns ``[]``.
        """
        if self._is_compound:
            siblings = self._children.get(v)
            if siblings is None:
                return []
            return list(siblings.keys())
        if v == GRAPH_NODE:
            return list(self._nodes.keys())
        if self.has_node(v):
            return []
        return []

    def predecessors(self, v: str) -> list[str] | None:
        """Return ``v``'s predecessors, or ``None`` if ``v`` is absent (mirrors grok).

        Behaviour is undefined for undirected graphs -- use :meth:`neighbors`.
        """
        preds = self._preds.get(v)
        if preds is None:
            return None
        return list(preds.keys())

    def successors(self, v: str) -> list[str] | None:
        """Return ``v``'s successors, or ``None`` if ``v`` is absent (mirrors grok).

        Behaviour is undefined for undirected graphs -- use :meth:`neighbors`.
        """
        sucs = self._sucs.get(v)
        if sucs is None:
            return None
        return list(sucs.keys())

    def neighbors(self, v: str) -> list[str] | None:
        """Return ``v``'s predecessors + successors (mirrors grok; O(|V|)).

        Returns ``None`` if ``v`` is absent. Unlike grok (which collects into
        an iteration-disordered ``HashSet``), this preserves first-seen order
        (predecessors first, then any new successors) for deterministic output.
        """
        preds = self.predecessors(v)
        if preds is None:
            return None
        seen: dict[str, None] = dict.fromkeys(preds)
        sucs = self.successors(v)
        if sucs is not None:
            for successor in sucs:
                seen.setdefault(successor, None)
        return list(seen.keys())

    def is_leaf(self, v: str) -> bool:
        """Return ``True`` if ``v`` has no outgoing adjacency (mirrors grok).

        Directed graphs check :meth:`successors`; undirected graphs check
        :meth:`neighbors`. An absent node is treated as a leaf (matches grok).
        """
        adjacent = self.successors(v) if self._is_directed else self.neighbors(v)
        return adjacent is None or len(adjacent) == 0

    # === Edge functions ============================================

    def set_default_edge_label(
        self, new_default: DefaultEdgeLabel[E]
    ) -> Graph[GL, N, E]:
        """Set the default edge label (mirrors grok ``set_default_edge_label``).

        ``new_default`` is either an :class:`EdgeLabelValue` (fixed value, or
        ``None`` to fall back to ``edge_default_factory``) or an
        :class:`EdgeLabelFactory` (invoked per edge id).
        """
        self._default_edge_label_fn = new_default
        return self

    def default_edge_label(self, edge_id: str) -> E:
        """Resolve the default label for ``edge_id`` (mirrors grok).

        Three-way fallback mirroring :meth:`default_node_label`:

        - :class:`EdgeLabelFactory` -> invoke ``factory(edge_id)``; if it
          returns ``None``, fall back to ``edge_default_factory()``.
        - :class:`EdgeLabelValue` with a value -> that value.
        - :class:`EdgeLabelValue` with ``value=None`` ->
          ``edge_default_factory()`` (the Python stand-in for grok
          ``E::default()``; defaults to ``None`` for dagre's ``E = ()``).
        """
        default_fn = self._default_edge_label_fn
        if isinstance(default_fn, EdgeLabelFactory):
            label = default_fn.factory(edge_id)
            if label is not None:
                return label
            return self._edge_default_factory()
        if default_fn.value is not None:
            return default_fn.value
        return self._edge_default_factory()

    def edge_count(self) -> int:
        """Return the number of edges (mirrors grok; O(1))."""
        return self._edge_count

    def edges(self) -> list[Edge]:
        """Return all edge objects in insertion order (mirrors grok; O(|E|))."""
        return list(self._edge_objs.values())

    def set_path(self, vs: list[str], value: E | None) -> None:
        """Establish edges over consecutive node pairs in ``vs`` (mirrors grok).

        For ``[a, b, c, d]`` sets edges ``a->b``, ``b->c``, ``c->d``, each with
        ``value`` as the label. grok folds over the list with ``reduce``; the
        ``zip(vs, vs[1:])`` form is the idiomatic Python equivalent. Returns
        ``None`` (grok's ``set_path`` returns ``()`` -- the only setter that
        does not return ``&mut Self``).
        """
        for v, w in zip(vs, vs[1:], strict=False):
            self.set_edge(v, w, value, None)

    def set_edge(
        self,
        v: str,
        w: str,
        edge_label: E | None,
        name: str | None,
    ) -> Graph[GL, N, E]:
        """Create or update edge ``(v, w[, name])`` (mirrors grok ``set_edge``).

        If the edge already exists and ``edge_label`` is supplied, its label is
        updated; if not supplied the existing label is left untouched. On first
        creation, a missing ``edge_label`` resolves to
        :meth:`default_edge_label`. Both endpoints are ensured to exist via
        :meth:`set_node` (which also seeds their empty adjacency tables).

        Raises ``RuntimeError`` when ``name`` is supplied on a non-multigraph
        (grok returns ``Err``): the ``name`` discriminator only distinguishes
        parallel edges in a multigraph.
        """
        e = edge_args_to_id(self._is_directed, v, w, name)
        if e in self._edge_labels:
            if edge_label is not None:
                self._edge_labels.insert(e, edge_label)
            return self

        if name is not None and not self._is_multigraph:
            raise RuntimeError("Cannot set a named edge when isMultigraph = false")

        # It didn't exist, so we need to create it. set_node ensures both
        # endpoints (and their empty adjacency tables) exist.
        self.set_node(v, None)
        self.set_node(w, None)

        if edge_label is not None:
            self._edge_labels.insert(e, edge_label)
        else:
            self._edge_labels.insert(e, self.default_edge_label(e))

        edge_obj = edge_args_to_obj(self._is_directed, v, w, name)
        self._edge_objs.insert(e, edge_obj)

        preds = self._preds.get(w)
        if preds is not None:
            _increment_or_init_entry(preds, v)
        sucs = self._sucs.get(v)
        if sucs is not None:
            _increment_or_init_entry(sucs, w)

        in_edges = self._in.get(w)
        if in_edges is None:
            in_edges = OrderedHashMap()
            self._in.insert(w, in_edges)
        in_edges.insert(e, edge_obj)

        out_edges = self._out.get(v)
        if out_edges is None:
            out_edges = OrderedHashMap()
            self._out.insert(v, out_edges)
        out_edges.insert(e, edge_obj)

        self._edge_count += 1
        return self

    def set_edge_with_obj(self, e: Edge, edge_label: E | None) -> Graph[GL, N, E]:
        """Edge-object overload of :meth:`set_edge` (mirrors grok)."""
        return self.set_edge(e.v, e.w, edge_label, None)

    def edge(self, v: str, w: str, name: str | None) -> E | None:
        """Return the label for edge ``(v, w[, name])`` (mirrors grok; O(1)).

        ``None`` means the edge does not exist (grok's ``Option::None``). For a
        graph whose ``E`` is the unit type, an existing edge's stored label is
        also ``None`` -- disambiguate existence via :meth:`has_edge`.
        """
        e = edge_args_to_id(self._is_directed, v, w, name)
        return self._edge_labels.get(e)

    def edge_with_obj(self, edge: Edge) -> E | None:
        """Edge-object overload of :meth:`edge` (mirrors grok; O(1))."""
        e = edge_obj_to_id(self._is_directed, edge)
        return self._edge_labels.get(e)

    def edge_mut(self, v: str, w: str, name: str | None) -> E | None:
        """Return a mutable handle on the label of edge ``(v, w[, name])``.

        Python values are boxed behind references, so mutating the returned
        object (when mutable) mutates the stored label in place. Mirrors grok
        ``edge_mut``.
        """
        e = edge_args_to_id(self._is_directed, v, w, name)
        return self._edge_labels.get_mut(e)

    def edge_mut_with_obj(self, edge: Edge) -> E | None:
        """Edge-object overload of :meth:`edge_mut` (mirrors grok)."""
        e = edge_obj_to_id(self._is_directed, edge)
        return self._edge_labels.get_mut(e)

    def has_edge(self, v: str, w: str, name: str | None) -> bool:
        """Return ``True`` if edge ``(v, w[, name])`` exists (mirrors grok; O(1))."""
        e = edge_args_to_id(self._is_directed, v, w, name)
        return e in self._edge_labels

    def has_edge_with_obj(self, edge: Edge) -> bool:
        """Edge-object overload of :meth:`has_edge` (mirrors grok)."""
        e = edge_obj_to_id(self._is_directed, edge)
        return e in self._edge_labels

    def remove_edge(self, v: str, w: str, name: str | None) -> Graph[GL, N, E]:
        """Remove edge ``(v, w[, name])`` (mirrors grok ``remove_edge``; O(1)).

        No-op if the edge is absent. Decrements the predecessor/successor
        counts (removing the entry when it reaches zero via
        :func:`_decrement_or_remove_entry`) and drops the edge object + label +
        the per-node adjacency entries.
        """
        e = edge_args_to_id(self._is_directed, v, w, name)
        edge = self._edge_objs.get(e)
        if edge is None:
            return self

        # Snapshot the canonical endpoints from the stored edge object (for an
        # undirected graph edge_args_to_id may have swapped v/w during id
        # construction; the edge object carries the canonical orientation).
        v_canon = edge.v
        w_canon = edge.w
        self._edge_labels.remove(e)
        self._edge_objs.remove(e)

        preds = self._preds.get(w_canon)
        if preds is not None:
            _decrement_or_remove_entry(preds, v_canon)
        sucs = self._sucs.get(v_canon)
        if sucs is not None:
            _decrement_or_remove_entry(sucs, w_canon)

        in_edges = self._in.get(w_canon)
        if in_edges is not None:
            in_edges.remove(e)

        out_edges = self._out.get(v_canon)
        if out_edges is not None:
            out_edges.remove(e)

        self._edge_count -= 1
        return self

    def remove_edge_with_obj(self, e_obj: Edge) -> Graph[GL, N, E]:
        """Edge-object overload of :meth:`remove_edge` (mirrors grok)."""
        return self.remove_edge(e_obj.v, e_obj.w, None)

    def in_edges(self, v: str, u: str | None) -> list[Edge] | None:
        """Return edges pointing to ``v`` (mirrors grok; O(|E|)).

        With ``u`` set, filter to just those coming from ``u``. Returns
        ``None`` if ``v`` is absent (no adjacency table); an empty list if
        ``v`` exists but has no in-edges. Behaviour is undefined for undirected
        graphs -- use :meth:`node_edges`.
        """
        in_edges = self._in.get(v)
        if in_edges is None:
            return None
        result = list(in_edges.values())
        if u is None:
            return result
        return [edge for edge in result if edge.v == u]

    def out_edges(self, v: str, w: str | None) -> list[Edge] | None:
        """Return edges pointed at by ``v`` (mirrors grok; O(|E|)).

        With ``w`` set, filter to just those pointing to ``w``. Returns
        ``None`` if ``v`` is absent; an empty list if ``v`` exists but has no
        out-edges. Behaviour is undefined for undirected graphs -- use
        :meth:`node_edges`.
        """
        out_edges = self._out.get(v)
        if out_edges is None:
            return None
        result = list(out_edges.values())
        if w is None:
            return result
        return [edge for edge in result if edge.w == w]

    def node_edges(self, v: str, w: str | None) -> list[Edge] | None:
        """Return all edges to or from ``v`` regardless of direction (mirrors grok).

        With ``w`` set, filter to just those between ``v`` and ``w`` regardless
        of direction (in-edges from ``w`` plus out-edges to ``w``). Returns
        ``None`` if ``v`` is absent.
        """
        in_result = self.in_edges(v, w)
        if in_result is None:
            return None
        out_result = self.out_edges(v, w)
        if out_result is not None:
            in_result = in_result + out_result
        return in_result

    def filter_nodes(self, filter: Callable[[str], bool]) -> Graph[GL, N, E]:
        """Return a new graph with nodes filtered by ``filter`` (mirrors grok).

        The predicate is applied to each node independently (no recursive
        descent): a rejected node's descendants are NOT auto-rejected -- they
        survive and re-thread. Edges incident to a rejected node are dropped.
        In a compound graph the parent chain of each surviving node is
        re-threaded onto its closest surviving ancestor via
        :func:`_find_parent` (with memoisation in ``parents``).
        """
        copy: Graph[GL, N, E] = Graph(
            GraphOption(
                directed=self._is_directed,
                multigraph=self._is_multigraph,
                compound=self._is_compound,
            ),
            node_default_factory=self._node_default_factory,
            edge_default_factory=self._edge_default_factory,
        )

        for node_id, value in self._nodes.iter():
            if filter(node_id):
                copy.set_node(node_id, value)

        for e_obj in self._edge_objs.values():
            if e_obj.v in copy._nodes and e_obj.w in copy._nodes:
                # grok guards with ``if let Some(label) = edge_with_obj``. The
                # Option outer layer signals edge *existence*, not the E value
                # (which for a unit-typed E is ``None`` in Python). Use
                # has_edge_with_obj so the ``None`` label of an E=() graph is
                # not mistaken for a missing edge.
                if self.has_edge_with_obj(e_obj):
                    copy.set_edge_with_obj(e_obj, self.edge_with_obj(e_obj))

        if self._is_compound:
            parents: OrderedHashMap[str, str] = OrderedHashMap()
            for node_id in list(copy._nodes.keys()):
                parent = _find_parent(node_id, parents, copy, self)
                copy.set_parent(node_id, parent)

        return copy


# === Module-private edge helpers (R244) ========================
# Mirror grok's module-private ``increment_or_init_entry`` /
# ``decrement_or_remove_entry`` / ``find_parent`` fns -- consumed by
# :meth:`Graph.set_edge` / :meth:`Graph.remove_edge` / :meth:`Graph.filter_nodes`
# respectively. Module-private (underscore prefix) and intentionally NOT
# re-exported via the barrel -- they are vocabulary internal to the Graph
# edge mechanics, mirroring grok's non-``pub`` item visibility.


def _increment_or_init_entry(counter_map: OrderedHashMap[str, int], key: str) -> None:
    """Increment ``counter_map[key]``, initialising to 1 if absent.

    Mirrors grok ``increment_or_init_entry``: on the occupied branch the count
    is incremented in place (the key keeps its insertion position, matching
    grok's ``get_mut`` semantics -- :meth:`OrderedHashMap.insert` overwrites an
    existing key's value without moving it); on the vacant branch the key is
    appended with value 1. Used by :meth:`Graph.set_edge` to bump the
    predecessor/successor multiplicity counters.
    """
    if counter_map.contains_key(key):
        counter_map.insert(key, counter_map.get(key) + 1)
    else:
        counter_map.insert(key, 1)


def _decrement_or_remove_entry(counter_map: OrderedHashMap[str, int], key: str) -> None:
    """Decrement ``counter_map[key]``, removing it when the count hits zero.

    Mirrors grok ``decrement_or_remove_entry`` (graph.rs L899-906): a MISSING
    key is a no-op. grok's ``if let Some(value) = map.get_mut(k)`` skips the
    whole block when the key is absent -- a legal state under the multigraph
    anonymous-edge (``name = None``) semantics, where :meth:`remove_edge` can
    hit an edge object whose endpoint was never seeded into the peer's
    counter map (e.g. dagre network_simplex back-edge tree nodes ``_bt*``).
    The R269-R271 port lost this guard and crashed on ``None -= 1``
    (TypeError); R299 restores the grok no-op contract. Above zero the count
    is written back in place (position-preserving, matching grok's in-place
    ``*value -= 1``); at zero the entry is dropped entirely.
    Used by :meth:`Graph.remove_edge`.
    """
    value = counter_map.get(key)
    if value is None:
        return  # grok: missing key -> no-op (the ``if let Some`` guard).
    value -= 1
    if value <= 0:
        counter_map.remove(key)
    else:
        counter_map.insert(key, value)


def _find_parent(
    v: str,
    parents: OrderedHashMap[str, str],
    copy: Graph[GL, N, E],
    graph: Graph[GL, N, E],
) -> str | None:
    """Resolve ``v``'s surviving parent in ``copy`` (mirrors grok ``find_parent``).

    Used by :meth:`Graph.filter_nodes` to re-thread the parent chain of each
    survivor onto its closest surviving ancestor. ``parents`` memoises the
    resolution so a filtered-out ancestor is walked only once.

    Three branches (faithful to grok graph.rs 948-964):

    - ``v`` has no parent, or its original parent survived in ``copy`` -> the
      original parent (memorised under ``v`` when present; ``None`` when ``v``
      has no parent and not memorised).
    - the original parent was filtered out but its resolution is already
      memorised in ``parents`` -> the memorised value.
    - otherwise -> recurse on the original parent.
    """
    parent = graph.parent(v)
    if parent is None or copy.has_node(parent):
        if parent is not None:
            parents.insert(v, parent)
            return parent
        return None
    memoized = parents.get(parent)
    if memoized is not None:
        return memoized
    return _find_parent(parent, parents, copy, graph)
