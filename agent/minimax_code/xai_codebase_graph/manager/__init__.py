"""``manager`` subpackage barrel (R305f).

Mirrors grok ``xai-codebase-graph/src/manager/mod.rs`` -- the thin re-export
layer that aggregates the cache / builder / lock siblings into a single
namespace. R305f lands the **cache subset only** (grok ``cache.rs``); the
``builder.rs`` / ``lock.rs`` siblings arrive in later bricks and will be
merged into this barrel when they land.

grok ``manager/mod.rs`` re-exports exactly 8 cache symbols
(``CACHE_FILE_NAME``, ``CacheError``, ``cache_exists``, ``cache_size``,
``get_cache_path``, ``load_index``, ``save_index``, ``save_index_async``).
The Python port additionally re-exports the 4 :class:`CacheError` subclasses
(``CacheIOError`` / ``CacheSerializeError`` / ``CacheDeserializeError`` /
``LegacyCacheFormat``) because grok distinguishes failure modes via ``enum``
variants on a single type, while the Python functional clone uses one
subclass per variant -- the subclasses *are* the distinguishable failure
modes, so they must be reachable at the package surface for
``except LegacyCacheFormat`` arms. This is a faithful functional clone, not a
structural one (see ``cache.py`` module docstring for the error-class
rationale).
"""

from __future__ import annotations

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

__all__ = [
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
]
