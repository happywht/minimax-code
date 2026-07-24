"""Index cache wrapper for fast SGIX loading (R305f).

Ported from grok ``xai-codebase-graph/src/manager/cache.rs`` -- the thin
file-system + error-classification layer that wraps the SGIX binary
serialisation landed in R305d (``scope_graph.sgix``). This module owns three
concerns and nothing else:

1. **Path derivation** -- :func:`get_cache_path` joins a repo root to the
   canonical cache file name ``.goto_index.bin`` (grok ``CACHE_FILE_NAME``).
2. **Existence / size probes** -- :func:`cache_exists` /
   :func:`cache_size` mirror ``Path::exists`` / ``std::fs::metadata``.
3. **Load / save delegation** -- :func:`load_index` / :func:`save_index`
   delegate to :func:`scope_graph.sgix.load` / :func:`scope_graph.sgix.save`
   and re-map the tri-state result into a 4-variant exception hierarchy
   (:class:`CacheError` + 4 subclasses) so callers can
   ``except LegacyCacheFormat`` to trigger a rebuild, mirroring grok's
   ``match CacheError::LegacyFormat`` arm.

Async save: grok spawns an OS thread (``std::thread::spawn``) so the caller
returns immediately while the write happens off-thread. The Python port uses a
daemon :class:`threading.Thread` for the same fire-and-forget semantics -- the
SGIX write is synchronous blocking I/O, so an asyncio task would be the wrong
primitive (it would only schedule onto the running loop and still block the
event thread). A daemon thread dies with the interpreter on exit, exactly
matching grok's drop-on-process-end behaviour.

Error-class design: grok models cache failure as a single ``enum CacheError``
with four variants (``IoError`` / ``SerializeError`` / ``DeserializeError`` /
``LegacyFormat``) that implements ``std::error::Error``. Because it is a real
exception type, the idiomatic Python clone is an inheritance hierarchy (one
base + one subclass per variant) rather than a single class with a ``kind``
field. ``except CacheError`` still catches every variant (mirrors
``Result<T, CacheError>``); ``except LegacyCacheFormat`` targets the rebuild
trigger precisely (mirrors ``match CacheError::LegacyFormat``). This is a
functional clone -- the four distinguishable failure modes are preserved --
not a structural one.

Consumes: R305d (``sgix.load`` / ``sgix.save``) + R305c
(:class:`ScopeGraphIndex`). The ``builder`` / ``lock`` siblings (grok
``builder.rs`` / ``lock.rs``) land in later bricks -- the ``manager`` barrel
exposes the cache subset only until they arrive.
"""

from __future__ import annotations

import logging
import struct
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from minimax_code.xai_codebase_graph.scope_graph import ScopeGraphIndex
from minimax_code.xai_codebase_graph.scope_graph.sgix import (
    load as _sgix_load,
)
from minimax_code.xai_codebase_graph.scope_graph.sgix import (
    save as _sgix_save,
)

if TYPE_CHECKING:
    from os import PathLike

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

_logger = logging.getLogger(__name__)

#: Canonical on-disk cache file name (grok ``CACHE_FILE_NAME``).
CACHE_FILE_NAME: str = ".goto_index.bin"

#: Decoding failures surfaced by SGIX deserialisation that grok funnels into
#: ``io::Error`` via ``?`` -- caught here so :func:`load_index` honours its
#: "only raises :class:`CacheError`" contract. ``ValueError`` is what
#: :func:`scope_graph.sgix.read_from` raises on bad magic / unsupported
#: version / truncated body (it wraps the raw ``struct.error`` / ``EOFError``);
#: ``OSError`` covers file-level failures grok maps through
#: ``From<io::Error>``. ``struct.error`` is kept defensively in case a future
#: sgix change lets one escape ``read_from``.
_SGIX_DECODE_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    struct.error,
    ValueError,
)


# === Error hierarchy ======================================================
# grok ``enum CacheError`` -> base + 4 subclasses (see module docstring).


class CacheError(Exception):
    """Base for cache operations (mirrors grok ``CacheError`` enum)."""


class CacheIOError(CacheError):
    """IO failure during load / save (mirrors ``CacheError::IoError``).

    Wraps the underlying :class:`OSError` / :class:`struct.error` (file
    not found, permission denied, corrupt-bytes decode failure) -- grok
    funnels every ``io::Error`` here via ``?`` + ``From<io::Error>``.
    """

    def __init__(self, cause: OSError | struct.error | str) -> None:
        self.cause = cause
        super().__init__(str(cause))


class CacheSerializeError(CacheError):
    """Serialisation failure (mirrors ``CacheError::SerializeError``)."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class CacheDeserializeError(CacheError):
    """Deserialisation failure (mirrors ``CacheError::DeserializeError``)."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class LegacyCacheFormat(CacheError):
    """Legacy on-disk format detected -- caller should rebuild.

    Mirrors grok ``CacheError::LegacyFormat``: the file exists but its magic
    bytes are not ``SGIX``, so :func:`scope_graph.sgix.load` returned ``None``.
    """


# === Path + probe primitives ==============================================
# grok cache.rs L48-L51 + L98-L106.


def get_cache_path(root_path: str | PathLike[str]) -> Path:
    """Resolve the canonical cache path for ``root_path`` (grok ``get_cache_path``).

    grok joins ``root_path`` with ``CACHE_FILE_NAME`` into a ``PathBuf``; the
    Python port returns a :class:`pathlib.Path` for cross-platform
    ``/``-join semantics.
    """
    return Path(root_path) / CACHE_FILE_NAME


def cache_exists(cache_path: str | PathLike[str]) -> bool:
    """Return ``True`` iff a cache file exists at ``cache_path`` (grok ``cache_exists``)."""
    return Path(cache_path).exists()


def cache_size(cache_path: str | PathLike[str]) -> int | None:
    """Return the cache file size in bytes, or ``None`` if it is absent.

    Mirrors grok ``cache_size`` which returns ``Option<u64>`` from
    ``std::fs::metadata``; the Python port surfaces ``None`` on missing /
    unreadable metadata (``stat`` raising).
    """
    try:
        return Path(cache_path).stat().st_size
    except OSError:
        return None


# === Load / save delegation ===============================================
# grok cache.rs L53-L96.


def load_index(cache_path: str | PathLike[str]) -> ScopeGraphIndex:
    """Load an index from ``cache_path``; raise on failure (grok ``load_index``).

    Mirrors grok ``load_index`` (cache.rs L58-L79): probe existence first
    (NotFound -> :class:`CacheIOError`), then delegate to
    :func:`scope_graph.sgix.load`.

    Result mapping (grok tri-state ``Result<Option<_>, io::Error>`` -> raise):

    * ``Ok(Some(index))`` -> return the index.
    * ``Ok(None)`` (magic bytes are not ``SGIX``) -> :class:`LegacyCacheFormat`.
    * ``Err(io::Error)`` -> :class:`CacheIOError` (covers both the not-found
      probe and any decode error grok funnels through ``?`` into ``io::Error``).
    """
    path = Path(cache_path)
    if not path.exists():
        raise CacheIOError(FileNotFoundError(f"Cache file not found: {path}"))
    try:
        index = _sgix_load(path)
    except _SGIX_DECODE_ERRORS as exc:
        raise CacheIOError(exc) from exc
    if index is None:
        raise LegacyCacheFormat(f"Legacy cache format at: {path}")
    return index


def save_index(cache_path: str | PathLike[str], index: ScopeGraphIndex) -> None:
    """Persist ``index`` to ``cache_path`` (grok ``save_index``).

    Delegates to :func:`scope_graph.sgix.save`; an :class:`OSError` is
    re-raised as :class:`CacheIOError` (grok ``.map_err(CacheError::IoError)``).
    """
    try:
        _sgix_save(index, cache_path)
    except OSError as exc:
        raise CacheIOError(exc) from exc


def _save_index_worker(
    cache_path: str | PathLike[str], index: ScopeGraphIndex
) -> None:
    """Background worker for :func:`save_index_async` (grok thread body).

    Mirrors the ``move ||`` closure body: swallow the error, log a warning --
    fire-and-forget semantics mean the caller never sees the failure.
    """
    try:
        save_index(cache_path, index)
    except CacheError as exc:
        _logger.warning("Failed to save index cache: %s", exc)


def save_index_async(
    cache_path: str | PathLike[str], index: ScopeGraphIndex
) -> None:
    """Save ``index`` to ``cache_path`` on a background daemon thread.

    Mirrors grok ``save_index_async`` (cache.rs L90-L96): ``std::thread::spawn``
    fires a detached OS thread that performs the blocking write while the
    caller returns immediately. The Python port uses a daemon
    :class:`threading.Thread` -- daemon so it dies with the interpreter on
    exit (matching grok's drop-on-process-end), and a real thread (not an
    asyncio task) because the SGIX write is synchronous blocking I/O that
    would otherwise stall the event loop.
    """
    thread = threading.Thread(
        target=_save_index_worker,
        args=(cache_path, index),
        name="xcg-cache-save",
        daemon=True,
    )
    thread.start()
