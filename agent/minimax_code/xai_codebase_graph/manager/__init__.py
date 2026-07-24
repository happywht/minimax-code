"""``manager`` subpackage barrel (R305f cache + R305g builder + R305h lock).

Mirrors grok ``xai-codebase-graph/src/manager/mod.rs`` -- the thin re-export
layer that aggregates the cache / builder / lock siblings into a single
namespace. R305f landed the **cache subset** (grok ``cache.rs``); R305g added
the **builder subset** (grok ``builder.rs``); R305h adds the **lock subset**
(grok ``lock.rs``). The ``mod.rs`` barrel merge is now complete: all three
siblings are re-exported here.

grok ``manager/mod.rs`` re-exports exactly 8 cache symbols
(``CACHE_FILE_NAME``, ``CacheError``, ``cache_exists``, ``cache_size``,
``get_cache_path``, ``load_index``, ``save_index``, ``save_index_async``),
3 builder symbols (``IndexBuilder``, ``IndexError``, ``Result`` -- the last
is a ``type Result<T> = std::result::Result<T, IndexError>`` alias with no
Python equivalent), and 5 lock symbols (``IndexOperation``, ``LockResult``,
``WorkspaceLockGuard``, ``is_operation_in_progress``, ``try_lock``). The
Python port additionally re-exports the :class:`CacheError` subclasses (4)
and the :class:`IndexBuildError` subclasses (3) because grok distinguishes
failure modes via ``enum`` variants on a single type, while the Python
functional clone uses one subclass per variant -- the subclasses *are* the
distinguishable failure modes, so they must be reachable at the package
surface for ``except LegacyCacheFormat`` / ``except IndexWalkError`` arms.
This is a faithful functional clone, not a structural one (see ``cache.py``
/ ``builder.py`` module docstrings for the error-class rationale). The lock
surface is identical to grok's: 5 symbols, no enum-variant subclasses to
hoist, so the barrel is a verbatim mirror of grok's re-export.

Naming: grok's ``IndexError`` is renamed :class:`IndexBuildError` here to
avoid shadowing the Python built-in :class:`IndexError` -- grok's enum lives
in a separate namespace so the collision does not arise there.
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.manager.builder import (
    IndexBuilder,
    IndexBuildError,
    IndexIOError,
    IndexThreadPanic,
    IndexWalkError,
)
from minimax_code.xai_codebase_graph.manager.cache import (
    CACHE_FILE_NAME,
    CacheDeserializeError,
    CacheError,
    CacheIOError,
    CacheSerializeError,
    LegacyCacheFormat,
    cache_exists,
    cache_size,
    get_cache_path,
    load_index,
    save_index,
    save_index_async,
)
from minimax_code.xai_codebase_graph.manager.lock import (
    IndexOperation,
    LockResult,
    WorkspaceLockGuard,
    is_operation_in_progress,
    try_lock,
)

__all__ = [
    # cache (R305f) -- 12 symbols.
    "CACHE_FILE_NAME",
    "CacheDeserializeError",
    "CacheError",
    "CacheIOError",
    "CacheSerializeError",
    "LegacyCacheFormat",
    "cache_exists",
    "cache_size",
    "get_cache_path",
    "load_index",
    "save_index",
    "save_index_async",
    # builder (R305g) -- 5 symbols (1 builder + base + 3 variants).
    "IndexBuildError",
    "IndexBuilder",
    "IndexIOError",
    "IndexThreadPanic",
    "IndexWalkError",
    # lock (R305h) -- 5 symbols (mirrors grok mod.rs re-export verbatim).
    "IndexOperation",
    "LockResult",
    "WorkspaceLockGuard",
    "is_operation_in_progress",
    "try_lock",
]
