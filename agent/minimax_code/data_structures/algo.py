"""Graph traversal algorithms (R245, vendored ``third_party``).

Mirrors the ``algo`` submodule of the vendored ``graphlib_rust`` 0.0.2 crate
(upstream ``r3alst/graphlib-rust``, Apache-2.0): a pre-/post-order DFS that
navigates via ``successors`` (directed) or ``neighbors`` (undirected), plus
the ``preorder`` / ``postorder`` thin wrappers.

This is the fifth leaf of the ``data_structures`` migration chain and the
third and final layer of the graphlib stack (vocabulary R242 -> Graph
R243/R244 -> algo R245). It depends only on the public ``Graph`` interface
(``is_directed`` / ``has_node`` / ``successors`` / ``neighbors``), all of
which are in place after R243+R244, so traversal closes the complete
graphlib surface and unblocks the downstream ``dagre_rust`` layout stack.

Visibility mirrors grok: ``algo`` is a public module (``pub mod algo`` in
``lib.rs``) but its symbols are NOT re-exported at the crate root -- only
``graph.rs`` symbols earn a top-level ``pub use``. Accordingly this module
is importable as ``minimax_code.data_structures.algo`` but its symbols are
NOT added to the ``data_structures`` barrel ``__all__`` (count stays 5).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from minimax_code.data_structures.graphlib import Graph

__all__ = ["dfs", "postorder", "preorder"]


def dfs(
    g: Graph[Any, Any, Any],
    vs: list[str],
    order: str,
) -> list[str]:
    """Perform a pre- or post-order DFS traversal (mirrors grok ``algo::dfs``).

    Returns the nodes of ``g`` in the order they were visited, starting the
    traversal from each entry in ``vs`` (already-visited nodes are skipped).
    If the graph is directed, navigation uses :meth:`Graph.successors`; if
    undirected, :meth:`Graph.neighbors`.

    ``order`` selects post-order only when it is exactly ``"post"``; any
    other value (including ``"pre"``) selects pre-order -- this mirrors the
    grok ``match`` arm fallthrough (``"post" => post_order_dfs, _ =>
    pre_order_dfs``).

    Args:
        g: The graph to traverse (read-only; the grok signature takes
            ``&mut`` but no mutation occurs).
        vs: Entry node ids to start traversals from.
        order: ``"post"`` for post-order, anything else for pre-order.

    Returns:
        The visited node ids in traversal order.

    Raises:
        ValueError: if any node id in ``vs`` is absent from ``g`` (mirrors
            the grok ``Err(format!("Graph does not have node: {}", v))``).
    """
    navigation: Callable[[str, Graph[Any, Any, Any]], list[str]] = (
        _directed_navigation if g.is_directed() else _undirected_navigation
    )

    order_func = _post_order_dfs if order == "post" else _pre_order_dfs

    acc: list[str] = []
    visited: dict[str, bool] = {}
    for v in vs:
        if not g.has_node(v):
            raise ValueError(f"Graph does not have node: {v}")
        order_func(v, navigation, visited, acc, g)

    return acc


def postorder(g: Graph[Any, Any, Any], vs: list[str]) -> list[str]:
    """Return ``vs``'s reachable nodes in post-order (mirrors grok ``postorder``).

    Thin wrapper over :func:`dfs` with ``order="post"``. Returns ``[]`` if
    any entry node is absent -- the grok source silently swallows the
    ``Err`` arm (``Ok(t) => t, _ => vec![]``; upstream TODO: "need to
    check if exceptions are required"). We preserve that forgiving
    contract so downstream layout code (dagre) behaves identically.
    """
    try:
        return dfs(g, vs, "post")
    except ValueError:
        return []


def preorder(g: Graph[Any, Any, Any], vs: list[str]) -> list[str]:
    """Return ``vs``'s reachable nodes in pre-order (mirrors grok ``preorder``).

    Thin wrapper over :func:`dfs` with ``order="pre"``. See :func:`postorder`
    for the silent-swallow contract on absent entry nodes.

    Note: the upstream grok ``preorder`` is unusable because
    ``pre_order_dfs`` has an off-by-one panic (see :func:`_pre_order_dfs`);
    this migrated version returns a correct pre-order instead.
    """
    try:
        return dfs(g, vs, "pre")
    except ValueError:
        return []


def _directed_navigation(v: str, g: Graph[Any, Any, Any]) -> list[str]:
    """Navigate via successors for directed graphs (mirrors grok closure arm)."""
    return g.successors(v) or []


def _undirected_navigation(v: str, g: Graph[Any, Any, Any]) -> list[str]:
    """Navigate via neighbors for undirected graphs (mirrors grok closure arm)."""
    return g.neighbors(v) or []


def _post_order_dfs(
    v: str,
    navigation: Callable[[str, Graph[Any, Any, Any]], list[str]],
    visited: dict[str, bool],
    acc: list[str],
    g: Graph[Any, Any, Any],
) -> None:
    """Iterative post-order DFS with an explicit ``(node, is_post)`` stack.

    Mirrors grok ``algo::dfs::post_order_dfs``. For each unvisited node we
    push a post-visit marker ``(node, True)`` then push its neighbours in
    reverse so the first neighbour is popped first (left-to-right descent).
    The marker re-enters the loop after all descendants are emitted,
    appending the node itself -- yielding a true post-order.
    """
    stack: list[tuple[str, bool]] = [(v, False)]
    while stack:
        node, is_post = stack.pop()
        if is_post:
            acc.append(node)
        elif node not in visited:
            visited[node] = True
            stack.append((node, True))
            nav_nodes = navigation(node, g)
            idx = len(nav_nodes)
            while idx > 0:
                idx -= 1
                stack.append((nav_nodes[idx], False))


def _pre_order_dfs(
    v: str,
    navigation: Callable[[str, Graph[Any, Any, Any]], list[str]],
    visited: dict[str, bool],
    acc: list[str],
    g: Graph[Any, Any, Any],
) -> None:
    """Iterative pre-order DFS with an explicit node stack.

    Mirrors grok ``algo::dfs::pre_order_dfs`` but **fixes an upstream
    off-by-one panic**: the grok source seeds ``idx = len`` then indexes
    ``_navigation_nodes.get(idx)`` which is always out of bounds
    (``get(len) == None`` -> ``unwrap()`` panics on the very first
    iteration), so the upstream ``preorder`` is unusable. We use the same
    correct reverse-iteration pattern as :func:`_post_order_dfs`
    (``idx > 0`` + ``nav_nodes[idx - 1]``) so children are visited in their
    original insertion order, matching post-order's left-to-right descent.
    The bug is recorded here (and in the R245 iteration log) rather than
    preserved, because a panicking traversal serves no downstream consumer.
    """
    stack: list[str] = [v]
    while stack:
        curr = stack.pop()
        if curr not in visited:
            visited[curr] = True
            acc.append(curr)
            nav_nodes = navigation(curr, g)
            idx = len(nav_nodes)
            while idx > 0:
                idx -= 1
                stack.append(nav_nodes[idx])
