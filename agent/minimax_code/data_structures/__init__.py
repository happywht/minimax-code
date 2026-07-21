"""Data-structure primitives barrel (R241+, vendored ``third_party``).

Re-exports the pure-logic data-structure leaves migrated from grok's vendored
``third_party/`` crates. This module opens the ``data_structures`` Python
package: low-level data-structure primitives shared across subsystems -- the
foundation beneath the mermaid render pipeline's graphlib/dagre layout stack
and any other subsystem that needs an audited, dependency-free primitive.

First leaf (R241): ``ordered_hashmap`` -- insertion-ordered hash map mirroring
the vendored ``ordered_hashmap`` 0.0.3 crate (upstream ``r3alst/ordered-hashmap``,
Apache-2.0). It is the strict zero-dependency base (``[dependencies]`` empty
in the vendored ``Cargo.toml``) of ``graphlib_rust`` (single path dep) and
``dagre_rust`` (two path deps) -- the next leaves on this migration chain.

Second leaf (R242): ``graphlib`` -- the edge-encoding vocabulary layer of the
vendored ``graphlib_rust`` 0.0.2 crate (upstream ``r3alst/graphlib-rust``,
Apache-2.0). Carries the ``Edge`` / ``GraphOption`` value objects and the
edge-id encoding helpers that ``Graph`` (next leaf, R243) builds on; the crate
is pure logic (only path dep is the migrated ``ordered_hashmap``, no
``unsafe``, no I/O).

Third leaf (R243): ``graphlib`` extended -- the ``Graph`` core struct + its
node-primitive method subset (construction, flag queries, graph-label
accessors, default-node-label machinery, node CRUD, compound parent/child
queries, adjacency queries). The edge-method subset and the node methods that
depend on them (``remove_node`` / ``filter_nodes``) migrate in R243b; this
leaf carries only the zero-edge-dependency node surface so every migrated
method is self-contained and testable. ``Graph`` is the fifth barrel symbol.
"""

from __future__ import annotations

from minimax_code.data_structures.graphlib import (
    Edge,
    Graph,
    GraphOption,
)
from minimax_code.data_structures.ordered_hashmap import (
    Entry,
    OrderedHashMap,
)

__all__ = [
    "Edge",
    "Entry",
    "Graph",
    "GraphOption",
    "OrderedHashMap",
]
