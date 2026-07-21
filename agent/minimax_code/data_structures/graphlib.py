"""Graphlib edge-encoding primitives (R242, vendored ``third_party/graphlib_rust``).

Mirrors the type vocabulary + edge-id encoding helpers of grok's vendored
``graphlib_rust`` 0.0.2 crate (upstream ``r3alst/graphlib-rust``,
Apache-2.0) -- the Rust port of dagre.js's graphlib, used by the vendored
``dagre_rust`` layout engine and the ``mermaid-to-svg`` render path.

**Second leaf of the ``data_structures`` package (R242)** -- continues the
data-structure primitives migration chain opened by ``ordered_hashmap`` (R241).
``graphlib_rust``'s only dependency is ``ordered_hashmap`` (single path dep,
migrated in R241); the crate is pure logic with no external crates, and the
re-audit checklist attests "No ``unsafe``, no filesystem / env / network I/O".

This leaf migrates the **edge-encoding vocabulary layer** -- the types and
pure functions that ``Graph`` (next leaf, R243) builds on:

- :data:`DEFAULT_EDGE_NAME`, :data:`GRAPH_NODE`, :data:`EDGE_KEY_DELIM` --
  sentinel + delimiter constants driving edge-id composition.
- :class:`Edge` -- the ``{v, w, name}`` edge object (frozen value object).
- :class:`GraphOption` -- the ``{directed, multigraph, compound}`` construction
  options (mirrors ``#[derive(Default)]`` -- all fields ``None``).
- :func:`edge_args_to_id` / :func:`edge_args_to_obj` / :func:`edge_obj_to_id` --
  the pure edge-id encoding helpers (module-private in grok; the ``Graph``
  methods call them to canonicalise ``(v, w, name)`` into a unique key).

The :class:`Graph` struct itself (~40 methods, ~966 lines) and the graph
algorithms (``dfs`` / ``preorder`` / ``postorder``) migrate in subsequent
leaves; this leaf carries only the zero-``Graph``-dependency vocabulary.

Edge-id encoding
----------------
An edge is uniquely identified by composing its canonical ``(v, w, name)``
triple with :data:`EDGE_KEY_DELIM` (``\\x01``). For an undirected graph the
endpoints are swapped so ``v <= w`` lexicographically, making ``(v, w)`` and
``(w, v)`` produce the same id (the same edge). When no name is supplied the
:data:`DEFAULT_EDGE_NAME` sentinel (``\\x00``) is substituted, so named and
anonymous edges never collide.
"""

from __future__ import annotations

from dataclasses import dataclass

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
