"""Codebase graph index (direction (2) -- xai-codebase-graph crate port).

grok ``xai-codebase-graph`` builds a tree-sitter-backed symbol index over a
workspace: it tracks file metadata, parses definitions / references, and
serves scope-graph queries for navigation and code review.

R300 lands the type foundation (``types/`` subpackage): ``Position`` /
``Range`` / ``Location`` / ``FileEvent`` / ``FileMeta`` / ``IndexStats`` /
``SymbolOccurrence`` / ``SymbolAlias``.

R301 adds the scope-graph node / edge type layer (``scope_graph/``
subpackage): ``Symbol`` / ``SymbolId`` / ``LocalScope`` / ``LocalDef`` /
``LocalImport`` / ``Reference`` / ``NodeKind`` (plus ``EdgeKind`` /
``NodeKindKind``, exported from the subpackage only -- see below).

R302 adds the string interner (``interner`` module): ``StringId`` /
``StringInterner`` -- dedup byte-string store backing symbol name storage.

R303 adds the per-language tree-sitter config type (``languages/`` subpackage):
``TSLanguageConfig`` -- bundles language ids / extensions / namespaces / the
definitions query string, resolves symbol-type names to ``SymbolId``, and
defers the tree-sitter runtime binding (``grammar`` optional) so the type layer
stays stdlib-clean.

R304 lands the language registry (grok ``languages/mod.rs``):
:class:`LanguageRegistry` -- preloaded with five language configs (python /
golang / javascript / rust / typescript factories) and serving lookup by
extension / id / file path, a same-language-family check, and a stable
``compute_query_hash`` (``hashlib.blake2b``) used to invalidate the index when
queries change. Mirrors grok ``lib.rs`` L88
``pub use languages::{LanguageRegistry, TSLanguageConfig};``.

R305a began the ``scope_graph/graph.rs`` runtime port (pure-data foundation
only): :class:`QueryVersion` -- the tree-sitter query version stamp that
drives index rebuilds.

R305b lands the :class:`ScopeGraph` runtime itself (the per-file scope/def/
ref/import graph, 22 methods) plus the :class:`ScopeGraphResult`
``{graph, aliases}`` container.

R305c lands the :class:`ScopeGraphIndex` runtime -- the cross-file symbol
index (~36 in-memory methods) that aggregates per-file :class:`ScopeGraph`
instances with interned names, alias resolution, and reverse file->symbol
indexes. Mirrors grok ``lib.rs`` L95-L98, which re-exports ``ScopeGraph`` /
``ScopeGraphResult`` / ``ScopeGraphIndex`` at the crate root. The SGIX binary
ser/de (R305d) + the tree-sitter bridge (R305e) follow under ``scope_graph``.

R305f lands the ``manager`` subpackage cache layer (grok ``cache.rs``): the
8 cache symbols re-exported here (``CACHE_FILE_NAME`` / ``CacheError`` + the
6 path / probe / load / save helpers) mirror grok ``lib.rs`` L89-L93.

R305g adds the ``manager`` builder sibling (grok ``builder.rs``): the
:class:`IndexBuilder` runtime + the :class:`IndexBuildError` base are
re-exported here, mirroring grok ``lib.rs`` re-exporting ``IndexBuilder`` +
the ``IndexError`` enum. The 3 :class:`IndexBuildError` subclasses
(:class:`IndexWalkError` / :class:`IndexThreadPanic` / :class:`IndexIOError`)
stay ``manager``-subpackage-only (mirrors the cache design: grok's single
enum sits at the crate root, the distinguishable failure modes live one
layer down). The ``navigation`` module lands in R307 + R308.

R305h adds the ``manager`` lock sibling (grok ``lock.rs``): the workspace-
level locking runtime (in-memory same-process dedup + cross-process lock
files + stale detection). All 5 lock symbols (:class:`IndexOperation` /
:class:`LockResult` / :class:`WorkspaceLockGuard` / :func:`is_operation_in_progress`
/ :func:`try_lock`) are re-exported at the crate root, mirroring grok
``lib.rs`` L89-L93 re-exporting the same five verbatim. Unlike cache /
builder, lock exposes no enum-variant subclasses, so nothing stays
``manager``-subpackage-only -- the crate-root surface is byte-identical to
grok's.

R306a lands the ``index_manager`` type-layer foundation (grok
``index_manager.rs`` L42-L538): the 7 pure-data symbols that carry no
runtime dependency on the channel / tree-sitter machinery --
``MAX_INDEXABLE_FILE_SIZE`` + :class:`FileEventKind` / :class:`FileEvent`
(the **batch** container, distinct from the ``types`` single-file union) +
:class:`QueryResult` / :class:`SymbolLocation` + :class:`QueryError` +
:class:`IndexManagerConfig`. The 5 non-colliding symbols are re-exported at
the crate root; ``FileEvent`` / ``FileEventKind`` stay bound to the
``types`` version (R300 placement) -- see the module docstring in
:mod:`minimax_code.xai_codebase_graph.index_manager` for the split rationale.

R306c lands the channel-command layer: :class:`IndexCommand` -- the 14-variant
tagged union that flows through the actor mailbox (grok ``index_manager.rs``
L110-L168) -- re-exported at the crate root (grok ``lib.rs`` L84). The 14
variant subclasses stay leaf-module-only (grok models them as enum members).
The tokio -> asyncio channel-adaptation contract (``mpsc`` ->
``asyncio.Queue``, ``oneshot`` -> ``asyncio.Future``) is fixed here.

R306d lands the actor sender: :class:`IndexManagerHandle` -- the producer
side of the mailbox (grok ``index_manager.rs`` L233-L495), re-exported at
the crate root (grok ``lib.rs`` L85). It ports grok's three method families
(fire-and-forget senders / strict request-response / lightweight probes) to
asyncio, drops the ``*_blocking`` variants (a single-threaded loop would
deadlock on ``Future.result()``), and exposes the Python-only
:class:`ManagerClosedError` (crossbeam ``SendError`` equivalent) at the leaf
module only. The remaining actor runtime (ACTIVE_MANAGERS / ExitBeacon / the
actor loop) lands in R306e-R306g.

R307 lands the ``navigation`` module (grok ``navigation.rs``): the
:class:`Navigator` runtime + :class:`Location` (navigation flavor) +
:class:`NavigationResult` + the :class:`NavigationError` 6-variant hierarchy
-- the go-to-definition / go-to-references orchestrator. R308 reconciles
the crate-root barrel: ``Location`` / ``NavigationError`` /
``NavigationResult`` / ``Navigator`` reach the crate root (grok ``lib.rs``
L94); ``FileEvent`` / ``FileEventKind`` switch to the ``index_manager``
batch-container version (grok L84); and ``IndexManager`` (the actor runtime)
joins the crate root (grok L85). The ``types``-flavored ``Location`` /
``FileEvent`` / ``FileEventKind`` stay reachable via the ``types``
subpackage (grok keeps them split the same way).

The crate-root barrel mirrors grok ``lib.rs``: grok re-exports ``types``,
``scope_graph`` node symbols, and the ``interner`` pair at the crate root.
The Python port keeps them under their subpackages and re-exports them
here so both import paths work::

    from minimax_code.xai_codebase_graph import Position          # crate root
    from minimax_code.xai_codebase_graph.types import Position    # subpackage
    from minimax_code.xai_codebase_graph import Symbol, NodeKind  # crate root
    from minimax_code.xai_codebase_graph.scope_graph import Symbol  # subpackage
    from minimax_code.xai_codebase_graph import StringId          # crate root
    from minimax_code.xai_codebase_graph.interner import StringId  # module

Naming note: grok ``lib.rs`` does **not** re-export ``EdgeKind`` at the crate
root (it is only consumed inside ``scope_graph``). The Python port follows
the same surface: ``EdgeKind`` (and the Pythonic ``NodeKindKind``
discriminator, which has no grok counterpart) live under the
``scope_graph`` subpackage barrel only.

YAGNI: the ``types`` layer, the ``scope_graph`` node / edge type layer, the
``interner`` module, the ``scope_graph/graph.py`` pure-data foundation
(``QueryVersion`` / ``Snippet`` / ``NodeIndex``), the graph algorithms
(``ScopeGraph`` / ``ScopeGraphIndex``), the full ``manager`` subpackage
(cache R305f / builder R305g / lock R305h), the ``navigation`` module
(R307), and the full ``index_manager`` surface (R306a-R306g + R308 barrel
reconciliation) are public. See the per-brick paragraphs above for the
crate-root barrel bindings (``Location`` -> navigation flavor,
``FileEvent`` / ``FileEventKind`` -> index_manager batch container,
``IndexManager`` actor runtime).
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.index_manager import (
    MAX_INDEXABLE_FILE_SIZE,
    FileEvent,
    FileEventKind,
    IndexCommand,
    IndexManager,
    IndexManagerConfig,
    IndexManagerHandle,
    QueryError,
    QueryResult,
    SymbolLocation,
    is_binary_content,
)
from minimax_code.xai_codebase_graph.interner import StringId, StringInterner
from minimax_code.xai_codebase_graph.languages import (
    LanguageRegistry,
    TSLanguageConfig,
)
from minimax_code.xai_codebase_graph.manager import (
    CACHE_FILE_NAME,
    CacheError,
    IndexBuilder,
    IndexBuildError,
    IndexOperation,
    LockResult,
    WorkspaceLockGuard,
    cache_exists,
    cache_size,
    get_cache_path,
    is_operation_in_progress,
    load_index,
    save_index,
    save_index_async,
    try_lock,
)
from minimax_code.xai_codebase_graph.navigation import (
    Location,
    NavigationError,
    NavigationResult,
    Navigator,
)
from minimax_code.xai_codebase_graph.scope_graph import (
    LocalDef,
    LocalImport,
    LocalScope,
    NodeKind,
    QueryVersion,
    Reference,
    ScopeGraph,
    ScopeGraphIndex,
    ScopeGraphResult,
    Symbol,
    SymbolId,
)
from minimax_code.xai_codebase_graph.types import (
    FileMeta,
    IndexStats,
    Position,
    Range,
    SymbolAlias,
    SymbolOccurrence,
)

__all__ = [
    "FileMeta",
    "IndexStats",
    "LanguageRegistry",
    "LocalDef",
    "LocalImport",
    "LocalScope",
    # navigation (R308) -- grok lib.rs L94 re-exports the navigation quartet
    # at the crate root. ``Location`` switches from the ``types`` flavor
    # (file_path/column/range, R300) to the ``navigation`` flavor
    # (path/line/symbol) -- grok's crate root carries only the navigation
    # flavor; the ``types::Location`` stays reachable via ``types.Location``.
    # The 6 ``NavigationError`` subclasses (FileNotFound / PositionOutOfBounds
    # / NoSymbolAtPosition / UnsupportedLanguage / ParseError / IoError) stay
    # leaf-module-only (grok re-exports just the base ``NavigationError``).
    "Location",
    "NavigationError",
    "NavigationResult",
    "Navigator",
    "NodeKind",
    "Position",
    "QueryVersion",
    "Range",
    "Reference",
    "ScopeGraph",
    "ScopeGraphIndex",
    "ScopeGraphResult",
    "StringId",
    "StringInterner",
    "Symbol",
    "SymbolAlias",
    "SymbolId",
    "SymbolOccurrence",
    "TSLanguageConfig",
    # manager (R305f) -- grok lib.rs L89-L93 cache subset (8 symbols).
    # The 4 CacheError subclasses stay manager-subpackage-only (mirrors grok,
    # which re-exports just the ``CacheError`` enum at the crate root);
    # ``except LegacyCacheFormat`` arms import from ``manager.cache`` directly.
    "CACHE_FILE_NAME",
    "CacheError",
    "cache_exists",
    "cache_size",
    "get_cache_path",
    "load_index",
    "save_index",
    "save_index_async",
    # manager (R305g) -- grok lib.rs re-exports ``IndexBuilder`` + the
    # ``IndexError`` enum at the crate root. The Python port exposes the
    # :class:`IndexBuilder` runtime + the :class:`IndexBuildError` base (the
    # enum equivalent); the 3 subclasses stay manager-subpackage-only.
    "IndexBuildError",
    "IndexBuilder",
    # manager (R305h) -- grok lib.rs L89-L93 re-exports the 5 lock symbols
    # verbatim. No enum-variant subclasses exist for lock, so the crate-root
    # surface matches grok's exactly (nothing stays subpackage-only).
    "IndexOperation",
    "LockResult",
    "WorkspaceLockGuard",
    "is_operation_in_progress",
    "try_lock",
    # index_manager (R306a-R306g + R308 barrel reconciliation) -- grok
    # ``lib.rs`` L84-L86 re-exports the full index_manager surface: the type
    # layer, ``is_binary_content`` (grok L86 PUB ``fn``), the ``IndexCommand``
    # enum (R306c, grok L84), the ``IndexManagerHandle`` actor sender (R306d,
    # grok L85), and ``IndexManager`` itself (the actor runtime, grok L85).
    # R308 completes the barrel reconciliation flagged in R306a:
    # ``FileEvent`` / ``FileEventKind`` now bind to the ``index_manager``
    # batch-container version (the multi-event carrier grok re-exports), no
    # longer the ``types`` single-file union; the ``types`` ``FileEvent`` /
    # ``FileEventKind`` / ``Location`` are reachable only via the ``types``
    # subpackage. The 14 ``IndexCommand`` variant subclasses stay
    # leaf-module-only (grok models them as enum members, not free symbols).
    # The Python-only ``ManagerClosedError`` (the asyncio carrier for
    # crossbeam's ``SendError``) is NOT re-exported here -- grok has no
    # crate-root ``SendError`` re-export, so it stays leaf-module-only
    # (``from ...index_manager import ManagerClosedError``).
    "MAX_INDEXABLE_FILE_SIZE",
    "FileEvent",
    "FileEventKind",
    "IndexCommand",
    "IndexManager",
    "IndexManagerConfig",
    "IndexManagerHandle",
    "QueryError",
    "QueryResult",
    "SymbolLocation",
    "is_binary_content",
]
