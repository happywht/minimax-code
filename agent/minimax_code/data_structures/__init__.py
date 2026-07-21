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
"""

from __future__ import annotations

from minimax_code.data_structures.ordered_hashmap import (
    Entry,
    OrderedHashMap,
)

__all__ = [
    "Entry",
    "OrderedHashMap",
]
