"""File change events for live index updates.

Mirrors grok ``xai-codebase-graph/src/types/file_event.rs`` (direction (2),
brick 1). NOTE: this is ``types::FileEvent``, distinct from the
``index_manager::FileEvent`` re-export (a later migration) -- the grok crate
ships both; this port keeps them 1:1 (no unification), mirroring the
``types::Location`` / ``navigation::Location`` split.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FileEventKind(Enum):
    """The 4 variants of grok ``FileEvent`` (file_event.rs L7-L33)."""

    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass(frozen=True, slots=True)
class FileEvent:
    """A file change event that can trigger an index update.

    Grok models this as a 4-variant enum with payload (``Created{path}``,
    ``Modified{path}``, ``Deleted{path}``, ``Renamed{from, to}``). Python has
    no enum-with-payload, so the port collapses the variants into a single
    tagged dataclass -- ``kind`` discriminates the variant, ``path`` carries
    the primary path (the ``to`` target for renames), and ``from_path``
    carries the rename source. The full variant contract is preserved via
    factory constructors (:meth:`created` / :meth:`modified` / :meth:`deleted`
    / :meth:`renamed`) and the three query methods (:meth:`primary_path` /
    :meth:`requires_reparse` / :meth:`affects_existing`).
    """

    kind: FileEventKind
    path: str
    from_path: str | None = None

    # === factory constructors (mirror grok enum variant construction) ===

    @classmethod
    def created(cls, path: str) -> FileEvent:
        """A new file was created at ``path`` (grok ``FileEvent::Created``)."""
        return cls(FileEventKind.CREATED, path)

    @classmethod
    def modified(cls, path: str) -> FileEvent:
        """An existing file at ``path`` was modified (grok ``FileEvent::Modified``)."""
        return cls(FileEventKind.MODIFIED, path)

    @classmethod
    def deleted(cls, path: str) -> FileEvent:
        """The file at ``path`` was deleted (grok ``FileEvent::Deleted``)."""
        return cls(FileEventKind.DELETED, path)

    @classmethod
    def renamed(cls, from_path: str, to: str) -> FileEvent:
        """A file moved from ``from_path`` to ``to`` (grok ``FileEvent::Renamed``).

        ``path`` stores the ``to`` target so :meth:`primary_path` returns it
        (mirroring grok ``path()`` returning ``to`` for renames).
        """
        return cls(FileEventKind.RENAMED, to, from_path)

    # === queries (grok path() / requires_reparse() / affects_existing()) ===

    def primary_path(self) -> str:
        """Primary path: the file itself, or the ``to`` target for renames.

        Mirrors grok ``path()`` (file_event.rs L37-L44): ``Created`` /
        ``Modified`` / ``Deleted`` return ``path``; ``Renamed`` returns
        ``to`` (stored in :attr:`path`).
        """
        return self.path

    def requires_reparse(self) -> bool:
        """True if the file content must be reparsed (Created / Modified).

        Mirrors grok ``requires_reparse`` (file_event.rs L47-L54): ``Deleted``
        and ``Renamed`` only need a path update, no reparse.
        """
        return self.kind in (FileEventKind.CREATED, FileEventKind.MODIFIED)

    def affects_existing(self) -> bool:
        """True if this event touches an already-indexed file.

        Mirrors grok ``affects_existing`` (file_event.rs L57-L64): ``Created``
        is False (brand-new file); the other three are True.
        """
        return self.kind != FileEventKind.CREATED

    def __str__(self) -> str:
        """Human-readable form (grok ``Display`` impl)."""
        if self.kind is FileEventKind.CREATED:
            return f"Created: {self.path}"
        if self.kind is FileEventKind.MODIFIED:
            return f"Modified: {self.path}"
        if self.kind is FileEventKind.DELETED:
            return f"Deleted: {self.path}"
        # RENAMED: ``path`` is ``to``, ``from_path`` is ``from``.
        return f"Renamed: {self.from_path} -> {self.path}"
