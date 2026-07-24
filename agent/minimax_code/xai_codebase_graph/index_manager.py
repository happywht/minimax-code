"""Index manager actor: type + helper layer (R306a + R306b, direction (2) brick 7).

Ported **by function** from grok ``xai-codebase-graph/src/index_manager.rs``.

R306a lands the pure-data type layer -- the 7 symbols that carry no runtime
dependency on the channel / tree-sitter / scope-graph machinery:

* ``MAX_INDEXABLE_FILE_SIZE`` -- the 5 MB ceiling above which files are skipped.
* :class:`FileEventKind` / :class:`FileEvent` -- the **batch** file-event
  container that flows through the channel (``paths: list[str]`` + ``kind``).
* :class:`QueryResult` / :class:`SymbolLocation` -- query response payloads.
* :class:`QueryError` -- the 4-variant query-failure tagged union.
* :class:`IndexManagerConfig` -- the spawn config (root path + cache toggles).

R306b lands the pure-logic helpers that sit beside the type layer (still
zero runtime dependency on the channel / tree-sitter machinery):

* :class:`CoalescedEvents` -- folds a rapid stream of :class:`FileEvent`
  batches into one ``path -> kind`` map (the actor drains this each cycle).
* :func:`is_binary_content` -- git-style NUL-byte heuristic over a buffer
  (PUB: re-exported at the crate root, grok ``lib.rs`` L86).
* :func:`is_binary_file` / :func:`is_under_hidden_dir` -- the private
  disk-read and hidden-directory classifier helpers.

The channel-actor runtime (:class:`IndexManager` / :class:`IndexManagerHandle`
/ :class:`IndexCommand` / the ``ACTIVE_MANAGERS`` singleton / ``ExitBeacon``)
lands in R306c-R306g -- the type + helper layer is zero-dependency and lands
first.

FileEvent naming -- a deliberate split (mirrors R300):
    grok ships **two** ``FileEvent`` types with different semantics:

    * ``types::FileEvent`` (file_event.rs) -- single-file tagged union
      (``Created{path}`` / ``Modified{path}`` / ``Deleted{path}`` /
      ``Renamed{from, to}``); landed in Python R300 as the crate-root
      ``FileEvent``.
    * ``index_manager::FileEvent`` (index_manager.rs L75-L107) -- **batch**
      container (``paths: Vec<PathBuf>`` + ``kind``); landed here.

    The two ``FileEventKind`` enums diverge on the delete variant:
    ``types`` uses ``Deleted``, ``index_manager`` uses ``Removed`` (grok
    L67-L68 -- the doc comment even says "File was deleted" while the variant
    is named ``Removed``, a fossil of independent evolution). The Python port
    keeps both 1:1 -- **no unification** -- mirroring the
    ``types::Location`` / ``navigation::Location`` split. The crate-root
    ``FileEvent`` / ``FileEventKind`` stay bound to the ``types`` version (the
    R300 placement); this module's pair is reachable as
    ``minimax_code.xai_codebase_graph.index_manager.FileEvent`` and is **not**
    re-exported at the crate root to avoid clobbering the ``types`` binding.
    A barrel-reconciliation brick (post-``navigation``) will align the crate
    root with grok ``lib.rs`` L84-L86 in one atomic switch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath

# === constants ============================================================


#: Maximum file size we attempt to index (5 MB).
#:
#: Files larger than this are skipped to avoid pathological memory usage from
#: tree-sitter AST construction on huge or binary files. Mirrors grok
#: ``index_manager.rs`` L42 ``pub const MAX_INDEXABLE_FILE_SIZE: u64``.
MAX_INDEXABLE_FILE_SIZE: int = 5 * 1024 * 1024


# === file events (batch container) ========================================


class FileEventKind(Enum):
    """The 4 variants of grok ``index_manager::FileEventKind`` (L62-L71).

    Maps from ``notify::EventKind``. Note the delete variant is ``Removed``
    (not ``Deleted`` -- that is the ``types::FileEventKind`` spelling; the
    two enums are intentionally distinct, see module docstring).
    """

    CREATED = "created"
    MODIFIED = "modified"
    REMOVED = "removed"
    RENAMED = "renamed"


@dataclass(slots=True)
class FileEvent:
    """A file-system event that triggers index updates.

    Mirrors grok ``index_manager::FileEvent`` (L75-L107): a **batch** container
    -- ``paths`` carries every affected path (one for create/modify/remove,
    two ``[from, to]`` for a rename), ``kind`` discriminates the event. This
    is distinct from :class:`minimax_code.xai_codebase_graph.types.FileEvent`
    (the single-file tagged union): same name, different crate-local
    semantics, no unification.
    """

    paths: list[str]
    kind: FileEventKind

    @classmethod
    def new(cls, paths: list[str], kind: FileEventKind) -> FileEvent:
        """Construct from an explicit path list + kind (grok ``FileEvent::new``)."""
        return cls(paths=list(paths), kind=kind)

    @classmethod
    def created(cls, path: str) -> FileEvent:
        """A ``file created`` event (grok ``FileEvent::created``)."""
        return cls(paths=[path], kind=FileEventKind.CREATED)

    @classmethod
    def modified(cls, path: str) -> FileEvent:
        """A ``file modified`` event (grok ``FileEvent::modified``)."""
        return cls(paths=[path], kind=FileEventKind.MODIFIED)

    @classmethod
    def removed(cls, path: str) -> FileEvent:
        """A ``file removed`` event (grok ``FileEvent::removed``)."""
        return cls(paths=[path], kind=FileEventKind.REMOVED)

    @classmethod
    def renamed(cls, from_path: str, to_path: str) -> FileEvent:
        """A ``file renamed`` event (grok ``FileEvent::renamed``).

        ``paths`` stores ``[from_path, to_path]`` (grok L104-L106).
        """
        return cls(paths=[from_path, to_path], kind=FileEventKind.RENAMED)


# === query payloads =======================================================


@dataclass(slots=True)
class SymbolLocation:
    """A symbol location in a file.

    Mirrors grok ``SymbolLocation`` (L184-L216). ``path`` is stored
    **relative** to the index ``root_path`` (for portability across
    machines/sessions); ``line`` is 1-indexed; ``matched_symbol`` carries the
    matched name when useful for alias resolution.
    """

    path: str
    line: int
    matched_symbol: str | None = None

    @classmethod
    def new(cls, path: str, line: int) -> SymbolLocation:
        """A location with no matched symbol (grok ``SymbolLocation::new``)."""
        return cls(path=path, line=line, matched_symbol=None)

    @classmethod
    def with_symbol(cls, path: str, line: int, symbol: str) -> SymbolLocation:
        """A location carrying the matched symbol name (grok ``with_symbol``)."""
        return cls(path=path, line=line, matched_symbol=symbol)

    def as_path(self) -> PurePath:
        """The location path as a :class:`pathlib.PurePath` (grok ``as_path``).

        Returns a ``PurePath`` (no filesystem access) mirroring grok's
        ``Path::new(&self.path)`` reference -- the path is relative to the
        index ``root_path`` and may use either separator.
        """
        return PurePath(self.path)


@dataclass(slots=True)
class QueryResult:
    """Result of a query operation.

    Mirrors grok ``QueryResult`` (L172-L177): the symbol found at the query
    position plus the list of locations where it is defined/referenced.
    """

    symbol: str
    locations: list[SymbolLocation]


# === query errors =========================================================


@dataclass(slots=True)
class QueryError:
    """Error type for query operations.

    Mirrors grok ``QueryError`` (L218-L229), a 4-variant tagged union. Python
    collapses the variants into a single class with a ``kind`` discriminator
    (mirrors the :class:`LockResult` pattern from R305h): each variant has a
    factory classmethod, and the payload fields surface as attributes. The
    ``KIND_*`` constants are the discriminator strings (class attributes --
    they do not collide with the slot fields).
    """

    kind: str
    path: str | None = None
    row: int | None = None
    col: int | None = None
    language: str | None = None
    message: str | None = None

    KIND_FILE_NOT_FOUND = "file_not_found"
    KIND_NO_SYMBOL_AT_POSITION = "no_symbol_at_position"
    KIND_UNSUPPORTED_LANGUAGE = "unsupported_language"
    KIND_PARSE_ERROR = "parse_error"

    @classmethod
    def file_not_found(cls, path: str) -> QueryError:
        """File not found or could not be read (grok ``FileNotFound(PathBuf)``)."""
        return cls(kind=cls.KIND_FILE_NOT_FOUND, path=path)

    @classmethod
    def no_symbol_at_position(cls, row: int, col: int) -> QueryError:
        """No symbol found at the given position (grok ``NoSymbolAtPosition``)."""
        return cls(kind=cls.KIND_NO_SYMBOL_AT_POSITION, row=row, col=col)

    @classmethod
    def unsupported_language(cls, language: str) -> QueryError:
        """Language not supported (grok ``UnsupportedLanguage(String)``)."""
        return cls(kind=cls.KIND_UNSUPPORTED_LANGUAGE, language=language)

    @classmethod
    def parse_error(cls, message: str) -> QueryError:
        """Parse error (grok ``ParseError(String)``)."""
        return cls(kind=cls.KIND_PARSE_ERROR, message=message)


# === config ===============================================================


@dataclass(slots=True)
class IndexManagerConfig:
    """Configuration for the :class:`IndexManager` (lands R306f).

    Mirrors grok ``IndexManagerConfig`` (L499-L538). The builder methods are
    fluent (return ``self``), matching grok's ``mut self -> Self`` move
    semantics; ``new`` defaults ``load_from_cache`` / ``save_to_cache`` to
    ``True`` and ``cache_path`` to ``None``.
    """

    root_path: str
    cache_path: str | None = None
    load_from_cache: bool = True
    save_to_cache: bool = True

    @classmethod
    def new(cls, root_path: str) -> IndexManagerConfig:
        """A config with just the root path (grok ``IndexManagerConfig::new``).

        ``cache_path`` defaults to ``None``; ``load_from_cache`` /
        ``save_to_cache`` default to ``True`` (grok L513-L518).
        """
        return cls(root_path=root_path)

    def with_cache_path(self, path: str) -> IndexManagerConfig:
        """Set the cache path (grok ``with_cache_path``, fluent)."""
        self.cache_path = path
        return self

    def without_cache_load(self) -> IndexManagerConfig:
        """Disable cache loading on startup (grok ``without_cache_load``)."""
        self.load_from_cache = False
        return self

    def without_cache_save(self) -> IndexManagerConfig:
        """Disable cache saving (grok ``without_cache_save``)."""
        self.save_to_cache = False
        return self


# === event coalescing ====================================================


class CoalescedEvents:
    """Coalesce a stream of :class:`FileEvent` batches into one kind per path.

    Mirrors grok ``index_manager::CoalescedEvents`` (L1520-L1570, private).
    File-system watchers fire rapidly (a save can produce Created then
    Modified then Removed in quick succession); the actor drains the
    coalesced map so each path is reindexed / evicted at most once per
    drain cycle. The merge rules (grok L1513-L1519):

    * ``Created`` + ``Modified`` -> ``Modified`` (already tracked, reindex)
    * ``Created`` / ``Modified`` + ``Removed`` -> cancelled (entry dropped)
    * ``Removed`` + ``Created`` / ``Modified`` -> ``Created`` (replaced)
    * multiple ``Modified`` -> single ``Modified`` (last writer wins)

    Renames carry two paths (``[from, to]``): ``from`` is inserted as
    ``Removed`` and ``to`` as ``Created`` (grok L1532-L1538).
    """

    def __init__(self) -> None:
        self.events: dict[str, FileEventKind] = {}

    def add(self, event: FileEvent) -> None:
        """Fold one batch :class:`FileEvent` into the coalesced map.

        Renames split into two inserts: ``paths[0]`` (from) -> ``Removed``,
        ``paths[1]`` (to) -> ``Created``; every other kind inserts each
        path with the event's kind (grok ``CoalescedEvents::add``).
        """
        if event.kind is FileEventKind.RENAMED and len(event.paths) >= 2:
            self._insert(event.paths[0], FileEventKind.REMOVED)
            self._insert(event.paths[1], FileEventKind.CREATED)
            return
        for path in event.paths:
            self._insert(path, event.kind)

    def _insert(self, path: str, kind: FileEventKind) -> None:
        """Insert one ``path -> kind`` entry, applying the merge rules.

        Mirrors grok ``CoalescedEvents::insert`` (the Rust ``HashMap::entry``
        API collapses to a ``dict`` membership check + branch).
        """
        prev = self.events.get(path)
        if prev is None:
            # Vacant entry -> just set it.
            self.events[path] = kind
            return
        # Created/Modified then Removed -> cancel (drop the entry).
        if prev in (FileEventKind.CREATED, FileEventKind.MODIFIED) and kind is FileEventKind.REMOVED:
            del self.events[path]
            return
        # Removed then Created/Modified -> file replaced, treat as Created.
        if prev is FileEventKind.REMOVED and kind in (FileEventKind.CREATED, FileEventKind.MODIFIED):
            self.events[path] = FileEventKind.CREATED
            return
        # Same or compatible kinds -> last writer wins.
        self.events[path] = kind


# === binary detection ====================================================


def is_binary_content(content: bytes) -> bool:
    """Heuristic: does ``content`` look binary? (grok ``is_binary_content``).

    Scans the first 8000 bytes for a NUL byte -- the same heuristic git uses
    (``buffer-is-binary``). PUB: re-exported at the crate root (grok
    ``lib.rs`` L86) for callers that already hold the buffer (e.g. the
    indexer that just read a file). Python ``bytes.__contains__`` subsumes
    grok's ``content[..check_len].contains(&0)`` slice scan.
    """
    return b"\x00" in content[:8000]


def is_binary_file(path: str) -> bool:
    """Like :func:`is_binary_content` but reads only an 8 KB prefix from disk.

    Mirrors grok ``is_binary_file`` (L1584-L1594, private). Avoids loading a
    huge file into memory just to discover it is binary. Any I/O failure
    (missing file, permission denied, ...) returns ``False`` -- the caller
    treats unreadable as "not binary" so the indexer surfaces a clearer
    parse error downstream rather than silently skipping the file.
    """
    try:
        with open(path, "rb") as f:
            prefix = f.read(8000)
    except OSError:
        return False
    return b"\x00" in prefix


# === path classification =================================================


def is_under_hidden_dir(path: str) -> bool:
    """Does ``path`` have any component starting with ``.`` (len > 1)?

    Mirrors grok ``is_under_hidden_dir`` (L1601-L1607, private). Returns
    ``True`` for paths like ``.claude/worktrees/x/src/main.rs`` or
    ``.grok/cache/index.bin`` (tool-managed worktrees / caches that should
    not be indexed). The ``len > 1`` guard excludes the ``.`` current-dir
    component. ``PurePath`` is used (no filesystem access).
    """
    return any(p.startswith(".") and len(p) > 1 for p in PurePath(path).parts)


__all__ = [
    "MAX_INDEXABLE_FILE_SIZE",
    "FileEvent",
    "FileEventKind",
    "QueryResult",
    "SymbolLocation",
    "QueryError",
    "IndexManagerConfig",
    # R306b -- grok ``is_binary_content`` is a PUB ``fn`` re-exported at the
    # crate root (grok ``lib.rs`` L86). The 3 private helpers
    # (``CoalescedEvents`` / ``is_binary_file`` / ``is_under_hidden_dir``)
    # stay module-level (no crate-root re-export, mirroring grok visibility).
    "is_binary_content",
]
