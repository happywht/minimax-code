"""Compound-hierarchy order-constraint propagation for the order sweep (R263).

Mirrors ``third_party/dagre_rust/src/layout/order/add_subgraph_constraints.rs``
(upstream ``warpdotdev/mermaid-to-svg``, Apache-2.0). ``order::mod`` calls
:func:`add_subgraph_constraints` once per sweep rank (``mod.rs`` line 96),
right after :func:`sort_subgraph` sequences the layer graph -- the partner of
R262's :func:`build_layer_graph`. For every node ``v`` in the sorted within-rank
sequence ``vs`` it walks ``v``'s compound ancestor chain via ``g.parent`` and,
for each ancestor that has already seen a *different* previous descendant this
sweep, records a ``prev_child -> child`` constraint edge on the shared
constraint graph ``cg``. The first descendant encountered under each ancestor
becomes that ancestor's ``prev`` slot; the first parentless node becomes
``_root_prev``. The result is the compound hierarchy's left-to-right order
preserved as explicit ``cg`` edges that :func:`resolve_conflicts` (R261) honors
on the next sweep -- so a sub-graph's children keep their relative order across
sweeps even as the barycenter heuristic re-sequences them.

Twelfth zero-semantic-clone leaf (thirteenth borrow-checker framework reuse):
every grok ``clone()`` is a borrow-release artefact (``g.parent(v).cloned()``,
``g.parent(&child).cloned()``, ``prev.get(...).cloned()`` -- ``Option<&str>`` /
``Option<&String>`` -> ``Option<String>``) or an ownership-transfer artefact
(``child.clone()``, ``_parent.clone()``, ``_root_prev.clone()``,
``_prev_child.clone().unwrap_or(...)`` -- ``Option<String>`` rebinds into the
``while`` loop state); no structural ``copy.deepcopy`` survives. Python passes
the live ``str`` values directly -- ``g.parent`` returns ``str | None``,
``dict.get`` returns the value -- so the nine ``clone()`` calls collapse to
zero. The ``return ()`` early-exit from grok's ``for_each`` closure maps to a
``break`` out of the ``while`` (end this ``v``'s propagation, advance to the
next).
"""

from __future__ import annotations

from minimax_code.data_structures import Graph

__all__ = ["add_subgraph_constraints"]


def add_subgraph_constraints(g: Graph, cg: Graph, vs: list[str]) -> None:
    """Propagate compound-hierarchy left-to-right order constraints onto ``cg``.

    For each ``v`` in ``vs`` (the sorted within-rank node sequence), walk
    ``v``'s ancestor chain via ``g.parent``. Under each ancestor, the first
    descendant seen is recorded in ``prev`` (keyed by the ancestor id); the
    first parentless node is recorded in ``_root_prev``. A later *different*
    descendant under the same ancestor triggers
    ``cg.set_edge(prev_child, child, None, None)`` and ends that ``v``'s
    propagation (mirrors grok's ``return ()`` early-exit from the ``for_each``
    closure -- here a ``break`` out of the ``while``).

    ``g`` is the read-only source compound graph (``parent`` requires
    ``compound=True``); ``cg`` is the mutable constraint graph accumulated
    across ranks (``order::mod`` reuses one ``cg`` for the whole sweep). Edge
    labels default to the constraint graph's ``edge_default_factory`` -- grok
    passes ``None`` for the value, which :meth:`Graph.set_edge` resolves via
    :meth:`Graph.default_edge_label` on first creation.
    """
    prev: dict[str, str] = {}
    _root_prev: str | None = None

    for v in vs:
        child: str | None = g.parent(v)
        _parent: str | None = None
        _prev_child: str | None = None
        while child is not None:
            _parent = g.parent(child)
            if _parent is not None:
                _prev_child = prev.get(_parent)
                prev[_parent] = child
            else:
                _prev_child = _root_prev
                _root_prev = child

            if _prev_child is not None and _prev_child != child:
                cg.set_edge(_prev_child, child, None, None)
                break
            child = _parent
