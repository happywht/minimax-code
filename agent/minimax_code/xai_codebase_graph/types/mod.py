"""Core index types: stats, symbol occurrences, aliases, file metadata.

Mirrors grok ``xai-codebase-graph/src/types/mod.rs`` (direction (2), brick 1)
-- the structural-type definitions that live alongside the barrel ``pub use``
re-exports. (The Python port splits the definitions into this module and
re-exports them from ``types/__init__.py``, keeping the barrel pure.)

YAGNI boundary (R300): grok derives ``Serialize`` / ``Deserialize`` on
:class:`FileMeta` for cache persistence. The Python port keeps this module
stdlib-only (no serde / pydantic); cache (de)serialization lands with the
``manager/`` migration, which is where it is actually consumed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IndexStats:
    """Statistics about an index (grok ``IndexStats``, mod.rs L14-L22).

    ``Copy + Eq`` in grok -> frozen dataclass (value-equal + hashable).
    """

    files: int = 0
    definitions: int = 0
    references: int = 0

    @classmethod
    def new(cls, files: int, definitions: int, references: int) -> IndexStats:
        """Create new index stats (grok ``IndexStats::new``)."""
        return cls(files, definitions, references)


@dataclass(frozen=True, slots=True)
class SymbolOccurrence:
    """A symbol with its line number (1-indexed).

    Mirrors grok ``SymbolOccurrence`` (mod.rs L37-L43). grok uses ``Arc<str>``
    to share the name cheaply across index merges; Python ``str`` is already
    interned / shared by reference, so the field is a plain ``str``.
    """

    name: str
    line: int

    @classmethod
    def new(cls, name: str, line: int) -> SymbolOccurrence:
        """Create a new symbol occurrence (grok ``SymbolOccurrence::new``)."""
        return cls(name, line)


@dataclass(frozen=True, slots=True)
class SymbolAlias:
    """An alias mapping (alias_name -> original_name).

    Mirrors grok ``SymbolAlias`` (mod.rs L54-L60). Same ``Arc<str>`` -> ``str``
    note as :class:`SymbolOccurrence`.
    """

    alias: str
    original: str

    @classmethod
    def new(cls, alias: str, original: str) -> SymbolAlias:
        """Create a new symbol alias (grok ``SymbolAlias::new``)."""
        return cls(alias, original)


@dataclass(frozen=True, slots=True)
class FileMeta:
    """File metadata for staleness detection (grok ``FileMeta``, mod.rs L73-L119).

    Stores size and modification time so the indexer can detect whether a file
    changed without re-reading its contents. ``Copy + Eq + Serialize`` in grok
    -> frozen dataclass (value-equal + hashable); serde lands with the
    ``manager/`` cache migration (see module docstring).
    """

    size: int
    mtime_secs: int
    mtime_nanos: int

    @classmethod
    def new(cls, size: int, mtime_secs: int, mtime_nanos: int) -> FileMeta:
        """Create new file metadata (grok ``FileMeta::new``)."""
        return cls(size, mtime_secs, mtime_nanos)

    @classmethod
    def from_stat(cls, stat: os.stat_result) -> FileMeta:
        """Build metadata from an :func:`os.stat` result.

        Mirrors grok ``FileMeta::from_metadata`` (mod.rs L94-L107): size from
        ``st_size``; mtime split into epoch-seconds + subsecond nanos. grok's
        ``unwrap_or((0, 0))`` (mtime before UNIX epoch -> ``Duration`` error)
        maps to ``(0, 0)`` here.
        """
        size = stat.st_size
        mtime_ns = stat.st_mtime_ns
        if mtime_ns < 0:
            # grok: duration_since(UNIX_EPOCH) errors for pre-epoch mtimes.
            mtime_secs, mtime_nanos = 0, 0
        else:
            mtime_secs = mtime_ns // 1_000_000_000
            mtime_nanos = mtime_ns % 1_000_000_000
        return cls(size, mtime_secs, mtime_nanos)

    @classmethod
    def from_path(cls, path: str | os.PathLike[str]) -> FileMeta:
        """Build metadata by :func:`os.stat`-ing ``path`` (convenience wrapper)."""
        return cls.from_stat(os.stat(path))

    def is_stale(self, path: str | os.PathLike[str]) -> bool:
        """True if the on-disk file differs from this metadata (or is gone).

        Mirrors grok ``FileMeta::is_stale`` (mod.rs L110-L118): a missing or
        inaccessible file is stale (``Err(_) -> true``); otherwise stale iff
        the freshly-read metadata differs from ``self``.
        """
        try:
            current = self.from_path(path)
        except OSError:
            return True
        return self != current
