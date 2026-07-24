"""Index manager actor: type + helper + command layer (R306a + R306b + R306c, direction (2) brick 7).

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

R306c lands the channel-command layer (:class:`IndexCommand` -- the 14-
variant tagged union that flows through the actor mailbox, grok L110-L168).
grok models it as a Rust ``enum``; the Python port uses a sealed class
hierarchy (one ``@dataclass`` per variant) so the actor loop (R306f)
dispatches via ``isinstance``. The brick also fixes the tokio -> asyncio
channel-adaptation contract: ``mpsc::channel<IndexCommand>`` ->
``asyncio.Queue[IndexCommand]``, ``oneshot::Sender<T>`` ->
``asyncio.Future[T]``, ``Arc<ScopeGraphIndex>`` -> a bare
``ScopeGraphIndex`` (Python refs are already shared), and ``Result<T, E>``
-> ``T | E`` (a query miss is a value, not an exception).

The remaining actor runtime lands brick by brick:
:class:`IndexManagerHandle` (R306d, the producer side) shipped; R306e adds
the ``ExitBeacon`` test-only exit guard + the ``_ACTIVE_MANAGERS``
workspace-dedup singleton; :class:`IndexManager` / the actor loop follow in
R306f-R306g. The type, helper, and command layers above are
zero-runtime-dependency and land first.

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

import asyncio
import time
import weakref
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePath

# R306f lifts the R306a stdlib-only contract: the actor loop *calls*
# IndexBuilder / LanguageRegistry / load_index / save_index for real, so the
# scope-graph / languages / manager / types symbols move to top-level runtime
# imports. No circular dependency -- none of those modules import
# index_manager. (QueryVersion / ScopeGraphIndex / IndexStats were
# TYPE_CHECKING-only through R306e; R306f promotes them to runtime imports.)
from minimax_code.xai_codebase_graph.languages import LanguageRegistry
from minimax_code.xai_codebase_graph.manager import (
    CacheError,
    IndexBuilder,
    load_index,
    save_index,
)
from minimax_code.xai_codebase_graph.scope_graph import (
    QueryVersion,
    ScopeGraphIndex,
)
from minimax_code.xai_codebase_graph.types import IndexStats

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


# === index commands (channel-actor mailbox) ==============================
#
# R306c -- grok ``index_manager::IndexCommand`` (L110-L168): a 14-variant
# tagged union that flows through the actor mailbox. grok models it as a Rust
# ``enum``; the Python port uses a sealed class hierarchy (one ``@dataclass``
# per variant) so the actor loop (R306f) dispatches via ``isinstance`` --
# Python's tagged-union ``match``.
#
# Channel adaptation (the R306c design core; the runtime lands in R306d /
# R306f):
#
#   grok (tokio)                                  Python (asyncio)
#   ``mpsc::Sender<IndexCommand>``                ``asyncio.Queue[IndexCommand]``
#   ``mpsc::Receiver<IndexCommand>``              ``asyncio.Queue[IndexCommand]`` (actor)
#   ``oneshot::Sender<T>``                        ``asyncio.Future[T]``
#   ``Arc<ScopeGraphIndex>``                      ``ScopeGraphIndex`` (no Arc)
#   ``Result<QueryResult, QueryError>``           ``QueryResult | QueryError``
#
# The caller (``IndexManagerHandle``, R306d) creates the Future, pushes the
# command onto the queue, and ``await``s the Future; the actor (R306f) drains
# the queue, runs the query, and resolves the Future. Fire-and-forget variants
# carry no Future -- the actor just consumes them. ``Result`` is mapped to a
# union value (not an exception): a query miss is normal control flow, so the
# actor ``set_result`` on both arms and reserves ``set_exception`` for real
# runtime faults.


class IndexCommand:
    """Base marker for the 14 mailbox commands (grok ``IndexCommand``, L110-L168).

    A sealed class hierarchy: every concrete variant below subclasses this.
    The actor loop (R306f) dispatches via ``isinstance(cmd, <Variant>)`` --
    Python's tagged-union match. ``__slots__ = ()`` on the base keeps the
    ``@dataclass(slots=True)`` subclasses ``__dict__``-free (the mailbox can
    hold thousands of these, so per-command memory must be lean).
    """

    __slots__ = ()


# --- fire-and-forget variants (no response) ------------------------------


@dataclass(slots=True)
class FileEventCommand(IndexCommand):
    """Process a single :class:`FileEvent` (grok ``FileEvent(FileEvent)``)."""

    event: FileEvent


@dataclass(slots=True)
class FileEventBatchCommand(IndexCommand):
    """Process a batch of file events (grok ``FileEventBatch(Vec<FileEvent>)``).

    More efficient than one :class:`FileEventCommand` per event: the actor
    coalesces the whole batch in a single drain cycle.
    """

    events: list[FileEvent]


@dataclass(slots=True)
class RebuildCommand(IndexCommand):
    """Rebuild the entire index from scratch (grok ``Rebuild``).

    The actor walks ``root_path`` and reindexes every file. No payload, no
    response -- the caller observes completion via the index state change.
    """


@dataclass(slots=True)
class BackgroundRefreshCommand(IndexCommand):
    """Reindex stale / new files in the background (grok ``BackgroundRefresh``).

    ``stale_files`` are reindexed; ``deleted_files`` are evicted. Fire-and-
    forget -- the caller does not wait (the background sweep runs to completion
    on its own schedule).
    """

    stale_files: list[str]
    deleted_files: list[str]


@dataclass(slots=True)
class ShutdownCommand(IndexCommand):
    """Shut the actor down (grok ``Shutdown``).

    The actor drains remaining fire-and-forget work, drops its queue, and
    exits. No response -- the ``ExitBeacon`` / ``ACTIVE_MANAGERS`` registry
    (R306e) tracks completion.
    """


# --- request-response variants (oneshot -> Future) -----------------------
#
# Each variant carries an ``asyncio.Future`` the actor resolves. The Future's
# result type mirrors the grok ``oneshot::Sender<T>`` payload. ``QueryResult |
# QueryError`` keeps grok's ``Result`` value semantics (a miss is a value, not
# a raised exception).


@dataclass(slots=True)
class GetSnapshotCommand(IndexCommand):
    """Get a shared snapshot of the current index (grok ``GetSnapshot``).

    grok returns ``Arc<ScopeGraphIndex>``; Python collapses the ``Arc``
    (object references are already shared). The snapshot is read-only --
    callers must not mutate the returned index.
    """

    response: asyncio.Future[ScopeGraphIndex]


@dataclass(slots=True)
class GotoDefinitionCommand(IndexCommand):
    """Go-to-definition query (grok ``GotoDefinition``).

    ``file_path`` is relative to the index ``root_path``; ``row`` / ``col``
    are 0-indexed (grok ``usize``, matching tree-sitter's point model).
    """

    file_path: str
    row: int
    col: int
    response: asyncio.Future[QueryResult | QueryError]


@dataclass(slots=True)
class GotoReferencesCommand(IndexCommand):
    """Go-to-references query (grok ``GotoReferences``).

    ``include_definition`` folds the definition site into the reference list
    when ``True`` (grok L133).
    """

    file_path: str
    row: int
    col: int
    include_definition: bool
    response: asyncio.Future[QueryResult | QueryError]


@dataclass(slots=True)
class FindDefinitionsCommand(IndexCommand):
    """Find definitions by symbol name (grok ``FindDefinitions``).

    ``context_file`` (optional) biases same-file / local definitions when the
    name is ambiguous (grok L139).
    """

    symbol: str
    context_file: str | None
    response: asyncio.Future[list[SymbolLocation]]


@dataclass(slots=True)
class FindReferencesCommand(IndexCommand):
    """Find references by symbol name (grok ``FindReferences``).

    Mirrors :class:`FindDefinitionsCommand` for the reference side.
    """

    symbol: str
    context_file: str | None
    response: asyncio.Future[list[SymbolLocation]]


@dataclass(slots=True)
class GetFileCountCommand(IndexCommand):
    """Number of indexed files (grok ``GetFileCount``).

    Lightweight -- no index clone, just a length read.
    """

    response: asyncio.Future[int]


@dataclass(slots=True)
class GetStatsCommand(IndexCommand):
    """Index statistics (grok ``GetStats``).

    Lightweight aggregate -- :class:`minimax_code.xai_codebase_graph.types.IndexStats`.
    """

    response: asyncio.Future[IndexStats]


@dataclass(slots=True)
class GetQueryVersionCommand(IndexCommand):
    """Query-version stamp of the current index (grok ``GetQueryVersion``).

    Lightweight -- the stamp that drives rebuilds when queries change.
    """

    response: asyncio.Future[QueryVersion]


@dataclass(slots=True)
class HasDefinitionCommand(IndexCommand):
    """Does the symbol have any definitions? (grok ``HasDefinition``).

    Lightweight boolean probe -- cheaper than :class:`FindDefinitionsCommand`
    when the caller only needs existence.
    """

    symbol: str
    response: asyncio.Future[bool]


# === IndexManagerHandle (R306d) ==========================================


class ManagerClosedError(RuntimeError):
    """The actor behind an :class:`IndexManagerHandle` has already exited.

    Functional equivalent of grok ``crossbeam::channel::SendError<IndexCommand>``:
    the mailbox receiver (the actor loop, R306f) is gone, so a command
    cannot be delivered. Raised by the fire-and-forget senders
    (:meth:`IndexManagerHandle.send_event` / :meth:`send_events` /
    :meth:`rebuild` / :meth:`shutdown`) and by the strict request-response
    send half -- never by the lightweight ``*_async`` probes, which swallow
    the closed state and return ``None`` (mirrors grok's ``Option``-returning
    lightweight queries). grok has no crate-root ``SendError`` re-export, so
    this stays leaf-module-only (callers import it from ``index_manager``).
    """


class IndexManagerHandle:
    """Sender-side handle to the channel-actor index manager (R306d).

    Ports grok ``IndexManagerHandle`` (``index_manager.rs`` L233-L495) **by
    function**, not line-by-line. The handle is the *producer* side of the
    actor mailbox: it owns nothing but a reference to the shared command
    queue and a closed flag, and every method either enqueues a
    fire-and-forget command or builds a request-response command +
    :class:`asyncio.Future` pair and awaits the response.

    Channel adaptation (tokio -> asyncio, fixed in R306c):

    * grok ``crossbeam::Sender<IndexCommand>``  ->  an unbounded
      :class:`asyncio.Queue[IndexCommand]`. ``put_nowait`` mirrors
      crossbeam's synchronous ``send`` -- it never blocks (the queue is
      unbounded) and the closed state is surfaced via the ``_closed`` flag
      rather than a ``SendError`` return value.
    * grok ``tokio::sync::oneshot::Sender<T>``  ->  the
      :class:`asyncio.Future[T]` carried inside each request-response
      variant (the R306c ``IndexCommand`` subclasses).
    * grok ``Arc<ScopeGraphIndex>``  ->  a bare :class:`ScopeGraphIndex`
      reference (Python has no ``Arc``; the snapshot is shared by
      reference, exactly as grok's ``Arc`` clone when no mutation is in
      flight).

    Deliberate functional divergence from grok:

    * **No ``*_blocking`` variants.** grok runs the actor on a dedicated OS
      thread, so a caller in a synchronous context can ``blocking_recv``
      the oneshot without deadlocking. The Python port runs the actor on
      the same :mod:`asyncio` loop as every caller, so a synchronous
      ``Future.result()`` would deadlock the loop (the actor would never
      run to resolve it). Every request-response method is therefore
      ``async``; callers that need a synchronous answer should drive the
      coroutine from a thread that does not own the actor's loop.
    * **No ``Clone``.** Python passes the handle by reference, so multiple
      callers naturally share one mailbox -- there is no ownership to
      duplicate. (Cloning the handle in grok clones the ``Sender``; the
      Python equivalent is "hand another caller the same object".)
    * **``is_closed`` is public, not ``#[cfg(test)]``.** grok gates
      ``has_run_loop_exited`` behind ``#[cfg(test)]`` because production
      code detects a dropped actor via ``SendError``. The Python port
      exposes the same signal as a public property so production callers
      can poll liveness without trapping an exception.

    R306d ships the producer surface only; the actor loop that *drains*
    the mailbox lands in R306f. The request-response methods therefore
    await a :class:`asyncio.Future` that a later brick resolves.

    R306e extends this slot list with ``"__weakref__"`` so the handle is
    weakly referenceable: the ``_ACTIVE_MANAGERS`` workspace-dedup singleton
    (R306e) holds :class:`weakref.ref` instances pointing at live handles,
    mirroring grok's ``DashMap<PathBuf, Weak<IndexManagerHandle>>``. grok wraps
    the handle in ``Arc`` so ``Arc::downgrade`` yields a ``Weak`` for free; a
    slot-restricted Python class must declare ``__weakref__`` explicitly.
    """

    __slots__ = ("_mailbox", "_closed", "__weakref__")

    def __init__(
        self, mailbox: asyncio.Queue[IndexCommand], closed: asyncio.Event
    ) -> None:
        """Bind the handle to a shared mailbox + closed flag.

        Both arguments are owned by the actor (R306f ``IndexManager.spawn``
        constructs them); the handle holds them by reference. Sharing the
        same handle across callers gives them the same mailbox -- exactly
        grok's multi-producer ``Sender::clone`` semantics, minus the clone.
        """
        self._mailbox = mailbox
        self._closed = closed

    # -- actor-liveness probe --------------------------------------------

    @property
    def is_closed(self) -> bool:
        """``True`` once the actor loop has exited (R306f sets the flag).

        Functional equivalent of grok ``has_run_loop_exited`` (gated
        ``#[cfg(test)]`` there) -- exposed publicly here so production
        callers can poll actor liveness without trapping
        :class:`ManagerClosedError`.
        """
        return self._closed.is_set()

    # -- fire-and-forget senders (raise on dead actor) ------------------

    def _send_strict(self, command: IndexCommand) -> None:
        """Enqueue a command or raise :class:`ManagerClosedError`.

        Mirrors grok ``self.command_tx.send(command)`` -- a synchronous,
        non-blocking enqueue that fails fast (instead of blocking) when the
        actor is gone. The queue is unbounded so ``put_nowait`` never raises
        :class:`asyncio.QueueFull`; the only failure mode is the closed flag.
        """
        if self._closed.is_set():
            raise ManagerClosedError(
                "IndexManager actor has exited; command cannot be delivered"
            )
        self._mailbox.put_nowait(command)

    def send_event(self, event: FileEvent) -> None:
        """Forward a single :class:`FileEvent` to the actor (fire-and-forget).

        grok ``send_event`` returns ``Result<(), SendError>``; the Python
        port returns ``None`` on success and raises
        :class:`ManagerClosedError` on a dead actor (the idiomatic Python
        shape for "this cannot fail unless the destination is gone").
        """
        self._send_strict(FileEventCommand(event=event))

    def send_events(self, events: list[FileEvent]) -> None:
        """Forward a batch of events in one mailbox hop (not ``len(events)``).

        Short-circuits on an empty list (grok L257-L259 returns ``Ok(())``
        without sending) -- avoids a no-op round-trip through the actor.
        """
        if not events:
            return
        self._send_strict(FileEventBatchCommand(events=events))

    def rebuild(self) -> None:
        """Request a full index rebuild (fire-and-forget)."""
        self._send_strict(RebuildCommand())

    def shutdown(self) -> None:
        """Tell the actor to exit (fire-and-forget).

        The actor loop (R306f) treats :class:`ShutdownCommand` as the
        sentinel that breaks its drain loop; the ``_closed`` flag is set
        as the loop exits, after which further sends raise
        :class:`ManagerClosedError`.
        """
        self._send_strict(ShutdownCommand())

    # -- async request-response (strict: raises on dead actor) ----------

    async def get_snapshot_async(self) -> ScopeGraphIndex:
        """Snapshot of the current index (async, strict).

        grok ``get_snapshot_async`` returns
        ``Result<Arc<ScopeGraphIndex>, SendError>`` with
        ``rx.await.expect(...)`` (panic if the actor drops mid-query). The
        Python port returns the bare :class:`ScopeGraphIndex` reference
        (no ``Arc``) and surfaces a dead actor via
        :class:`ManagerClosedError` on the send half; the await itself does
        not panic because a well-behaved actor (R306f) always resolves the
        Future before exit.
        """
        fut: asyncio.Future[ScopeGraphIndex] = (
            asyncio.get_running_loop().create_future()
        )
        self._send_strict(GetSnapshotCommand(response=fut))
        return await fut

    async def goto_definition(
        self, file_path: str, row: int, col: int
    ) -> QueryResult | QueryError:
        """Resolve the definition at ``(row, col)`` in ``file_path`` (async).

        grok ``goto_definition`` returns
        ``Result<Result<QueryResult, QueryError>, SendError>``. The outer
        ``Result`` (channel send) becomes :class:`ManagerClosedError`; the
        inner ``Result<QueryResult, QueryError>`` becomes a union value
        (R306c contract: the actor ``set_result`` on both arms, so a query
        *miss* is a value the caller inspects via ``isinstance``, not a
        raise).
        """
        fut: asyncio.Future[QueryResult | QueryError] = (
            asyncio.get_running_loop().create_future()
        )
        self._send_strict(
            GotoDefinitionCommand(file_path=file_path, row=row, col=col, response=fut)
        )
        return await fut

    async def goto_references(
        self, file_path: str, row: int, col: int, include_definition: bool
    ) -> QueryResult | QueryError:
        """Resolve references at ``(row, col)``; ``include_definition`` adds the def site."""
        fut: asyncio.Future[QueryResult | QueryError] = (
            asyncio.get_running_loop().create_future()
        )
        self._send_strict(
            GotoReferencesCommand(
                file_path=file_path,
                row=row,
                col=col,
                include_definition=include_definition,
                response=fut,
            )
        )
        return await fut

    async def find_definitions(
        self, symbol: str, context_file: str | None
    ) -> list[SymbolLocation]:
        """Locate every definition site of ``symbol`` by name (async)."""
        fut: asyncio.Future[list[SymbolLocation]] = (
            asyncio.get_running_loop().create_future()
        )
        self._send_strict(
            FindDefinitionsCommand(
                symbol=symbol, context_file=context_file, response=fut
            )
        )
        return await fut

    async def find_references(
        self, symbol: str, context_file: str | None
    ) -> list[SymbolLocation]:
        """Locate every reference site of ``symbol`` by name (async)."""
        fut: asyncio.Future[list[SymbolLocation]] = (
            asyncio.get_running_loop().create_future()
        )
        self._send_strict(
            FindReferencesCommand(symbol=symbol, context_file=context_file, response=fut)
        )
        return await fut

    # -- async lightweight probes (Option semantics: None on dead actor) =

    def _send_probe(self, command: IndexCommand) -> bool:
        """Enqueue a lightweight probe; return ``False`` if the actor is gone.

        Mirrors grok ``self.command_tx.send(cmd).ok()`` -- the lightweight
        queries swallow the closed state into a falsy return rather than
        raising (grok ``Option``-returning ``get_file_count`` /
        ``get_stats`` / ``get_query_version`` / ``has_definition_blocking``).
        Returns ``True`` on a successful enqueue; the response Future lives
        on the command itself.
        """
        if self._closed.is_set():
            return False
        self._mailbox.put_nowait(command)
        return True

    async def get_file_count_async(self) -> int | None:
        """Indexed-file count without cloning the index (``None`` if actor gone).

        grok ``get_file_count`` swallows both the send error and the recv
        error into ``None`` (``Option``-returning lightweight query). The
        Python port mirrors that: a closed actor returns ``None`` rather
        than raising.
        """
        fut: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        if not self._send_probe(GetFileCountCommand(response=fut)):
            return None
        return await fut

    async def get_stats_async(self) -> IndexStats | None:
        """Index statistics without cloning the index (``None`` if actor gone)."""
        fut: asyncio.Future[IndexStats] = asyncio.get_running_loop().create_future()
        if not self._send_probe(GetStatsCommand(response=fut)):
            return None
        return await fut

    async def get_query_version_async(self) -> QueryVersion | None:
        """Query-version stamp without cloning the index (``None`` if actor gone)."""
        fut: asyncio.Future[QueryVersion] = (
            asyncio.get_running_loop().create_future()
        )
        if not self._send_probe(GetQueryVersionCommand(response=fut)):
            return None
        return await fut

    async def has_definition_async(self, symbol: str) -> bool | None:
        """Boolean existence check for ``symbol`` (``None`` if actor gone).

        Cheaper than :meth:`find_definitions` when the caller only needs to
        know *whether* a definition exists (no location list).
        """
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        if not self._send_probe(HasDefinitionCommand(symbol=symbol, response=fut)):
            return None
        return await fut


# === actor-lifecycle test beacon + workspace-dedup singleton (R306e) ======


class ExitBeacon:
    """Test-only RAII guard that flips a flag when the actor loop exits.

    Functional equivalent of grok ``ExitBeacon`` (``index_manager.rs``
    L540-L550): a ``#[cfg(test)]`` struct wrapping an
    ``Arc<std::sync::atomic::AtomicBool>`` whose ``Drop`` impl flips the flag
    to ``true``, so a test can confirm the actor thread *actually* ran to
    completion (not merely that the last ``Weak`` stopped upgrading). grok
    constructs one around the actor at L761
    (``let _exit_beacon = ExitBeacon(exit_signal);``) and lets it drop as
    ``run_loop`` returns.

    Adapted to asyncio, not line-ported:

    * **Mutable ``bool``, not ``Arc<AtomicBool>``.** grok needs the atomic
      because the actor thread and the test thread race on the flag; the
      Python actor runs on the *same* single event-loop thread as the test,
      so there is no data race and a plain ``bool`` is sufficient. No task
      ever ``await``s this signal (it is polled synchronously after the loop
      returns), so an :class:`asyncio.Event` would add allocation + scheduling
      cost for nothing.
    * **Unconditional delivery.** Python has no ``#[cfg(test)]`` conditional
      compilation, so the class ships in every build. It is kept out of
      ``__all__`` (test-only visibility, same surface as grok's test gate)
      and its docstring marks it test-only; R306f's ``run_loop`` will
      construct one but production callers never read ``exited``.
    * **Context manager + explicit ``close()``.** grok relies on ``Drop``;
      Python gives R306f both shapes -- ``with ExitBeacon() as b: ...`` for
      scoped use and ``b.close()`` / ``b.exited`` for a ``finally`` block --
      whichever the ``run_loop`` return path finds natural.
    """

    __slots__ = ("_flag",)

    def __init__(self) -> None:
        self._flag = False

    def __enter__(self) -> ExitBeacon:
        return self

    def __exit__(self, *exc: object) -> None:
        self._flag = True

    def close(self) -> None:
        """Functional equivalent of grok ``Drop`` -- flip the exit flag."""
        self._flag = True

    @property
    def exited(self) -> bool:
        """``True`` once the beacon has been closed / its context exited."""
        return self._flag


def _canonicalize_root(root_path: str | Path) -> str:
    """Resolve a workspace root to its canonical absolute string.

    Functional equivalent of grok
    ``dunce::canonicalize(&config.root_path).unwrap_or_else(|_| config.root_path.clone())``
    (``index_manager.rs`` L591) -- the dedup key the ``_ACTIVE_MANAGERS``
    singleton hashes by. Two paths that point at the same on-disk workspace
    must collide in the map so a second ``spawn`` reuses the live manager
    instead of forking a duplicate actor.

    Adapted to asyncio, not line-ported:

    * **:meth:`Path.resolve`, not ``dunce::canonicalize``.** ``dunce`` exists
      only to strip the ``\\\\?\\`` verbatim prefix that Rust's
      ``std::fs::canonicalize`` prepends on Windows (which would break path
      equality). Python's :meth:`pathlib.Path.resolve` never adds that prefix,
      so ``dunce``'s sole job is a no-op here -- ``resolve()`` alone is
      byte-faithful to the "canonicalize, but keep it comparable" intent.
    * **``strict=False`` (the default) mirrors ``unwrap_or_else``.** grok falls
      back to the raw path when canonicalize fails (the workspace may not
      exist yet on first ``spawn``); :meth:`Path.resolve` with
      ``strict=False`` (Python's default) returns the best-effort absolute
      path instead of raising, the same fallback.
    """
    return str(Path(root_path).resolve())


#: Per-process singleton that dedups live :class:`IndexManagerHandle` actors by
#: workspace. Functional equivalent of grok ``ACTIVE_MANAGERS``
#: (``index_manager.rs`` L58:
#: ``static ACTIVE_MANAGERS: Lazy<DashMap<PathBuf, Weak<IndexManagerHandle>>>``).
#:
#: Adapted to asyncio, not line-ported:
#:
#: * **:class:`weakref.WeakValueDictionary`, not ``DashMap<PathBuf, Weak<_>>``.**
#:   grok's ``DashMap`` shards for concurrent access from many threads; the
#:   Python port runs one event-loop thread, so there is no contention to
#:   shard against and a plain dict-backed weak map is sufficient. The *value*
#:   side is what matters: a weak ref to the handle, so the moment the last
#:   caller drops its strong ref the entry becomes collectable -- exactly
#:   grok's ``Weak<IndexManagerHandle>`` semantics.
#: * **No dead-weak-removal retry loop.** grok's ``spawn`` (L600-L626) loops
#:   on ``ACTIVE_MANAGERS.entry(k)`` and, on an occupied-but-dead slot,
#:   ``remove``s it then retries. :class:`weakref.WeakValueDictionary`
#:   *automatically* drops a dead entry on GC, so the occupied-but-dead case
#:   can never be observed in Python -- the whole ``remove + retry`` arm is
#:   subsumed by the weak map's own lifecycle and is not ported.
#: * **``__weakref__`` slot.** grok wraps the handle in ``Arc`` so
#:   ``Arc::downgrade`` yields a ``Weak`` for free; Python requires the target
#:   class to declare ``__weakref__`` in ``__slots__`` to be weakly
#:   referenceable, so R306e adds ``"__weakref__"`` to
#:   :class:`IndexManagerHandle.__slots__`.
#:
#: Private (underscored) and not in ``__all__`` -- grok's ``static`` is a
#: crate-private symbol too; R306f's ``spawn`` will ``__getitem__`` /
#: ``__setitem__`` against it directly.
_ACTIVE_MANAGERS: weakref.WeakValueDictionary[str, IndexManagerHandle] = (
    weakref.WeakValueDictionary()
)


#: Minimum interval (seconds) between automatic cache saves during a file-event
#: drain burst (grok ``CACHE_SAVE_INTERVAL_SECS``). A rapid burst of file
#: events does not hit the disk on every event -- the actor saves at most once
#: per interval, then again on shutdown. Functional equivalent of grok's
#: ``if last_cache_save.elapsed() >= CACHE_SAVE_INTERVAL_SECS`` gate.
_CACHE_SAVE_INTERVAL_SECS: float = 30.0


#: Strong references to live actor tasks, so the event loop's weak task ref
#: does not garbage-collect a spawned actor mid-run. An entry is added in
#: :meth:`IndexManager.spawn` and removed when the task completes (the
#: ``done_callback`` discards it). grok's OS thread has no equivalent -- a
#: ``JoinHandle`` keeps the thread alive -- so this set is a Python-only
#: adaptation for :func:`asyncio.create_task`'s weak-ref semantics.
_ACTIVE_ACTOR_TASKS: set[asyncio.Task[None]] = set()


def _resolve_response(fut: asyncio.Future[object], value: object) -> None:
    """Resolve a response Future, swallowing an already-done state.

    Functional equivalent of grok ``let _ = response_tx.send(value)`` -- the
    crossbeam oneshot sender ignores a dropped receiver (the caller may have
    timed out, cancelled, or moved on). The asyncio carrier mirrors that
    semantics: if the Future was cancelled or already resolved
    (``done()``), ``set_result`` is skipped to avoid
    :class:`asyncio.InvalidStateError`. Only real runtime faults surface as
    Future exceptions (set by the caller, never here).
    """
    if not fut.done():
        fut.set_result(value)


# === IndexManager actor (R306f) ===========================================
#
# R306f lands the channel-actor core -- the consumer side of the mailbox --
# ported by function (not line) from grok ``index_manager.rs``:
#
# * struct ``IndexManager`` (L556-L574) + the 7-field actor state.
# * ``spawn`` (L590-L769) -- the production entrypoint.
# * ``run_loop`` (L797-L828) -- the drain loop.
# * ``process_command_coalesced`` (L838-L953) -- the 14-arm command dispatch.
# * ``apply_coalesced`` (L1133-L1171) -- the file-event batch applier.
# * ``rebuild_index`` / ``save_cache`` / ``build_fresh_index``
#   (L1252+).
#
# grok runs the actor on a dedicated OS thread blocked on a crossbeam
# receiver; the Python port runs it as an asyncio task blocked on
# ``await mailbox.get()`` -- same single-owner, single-consumer contract,
# cooperative instead of preemptive.
#
# Runtime-import boundary: R306a-R306e kept this module stdlib-clean (the
# scope-graph / types / manager symbols were TYPE_CHECKING-only). R306f lifts
# that -- the actor *calls* ``IndexBuilder.build`` / ``LanguageRegistry.new``
# / ``load_index`` / ``save_index`` for real, so they are now top-level
# runtime imports (no circular dependency: scope_graph / languages / manager
# never import index_manager).


class IndexManager:
    """Channel-actor index manager: drains the mailbox, serves queries.

    Functional equivalent of grok ``IndexManager`` (``index_manager.rs``
    L556-L574) -- owns a :class:`ScopeGraphIndex` + :class:`LanguageRegistry`,
    drains the mailbox of :class:`IndexCommand`, and resolves each
    request-response Future. Adapted to asyncio, not line-ported:

    * **No ``Arc`` / no ``parser_cache`` / no ``query_cache``.** grok wraps the
      index in ``Arc`` (shared across threads) and caches tree-sitter
      ``Parser`` / ``Query`` objects on the struct. Python references are
      already shared (no ``Arc``), and the tree-sitter caches land with the
      parser bridge in R306g (deferred -- the R306f subset serves in-memory
      queries that need no parser).
    * **asyncio.Queue mailbox, not crossbeam receiver.** grok's
      ``command_rx.recv()`` blocks the OS thread; ``await self._mailbox.get()``
      yields the event loop. The drain helper uses ``get_nowait()`` (the
      ``try_recv`` analog).
    * **Explicit ``ShutdownCommand``, not channel-disconnect.** grok breaks
      the loop when ``recv()`` returns ``Err`` (all senders dropped); an
      :class:`asyncio.Queue` has no disconnect signal, so shutdown is driven
      by an explicit :class:`ShutdownCommand` (the caller invokes
      :meth:`IndexManagerHandle.shutdown`). The GC-driven
      :data:`_ACTIVE_MANAGERS` cleanup handles the caller-gone case.
    * **``ExitBeacon`` closed in ``finally``.** grok drops the beacon (its
      ``Drop`` flips the flag) as ``run_loop`` returns; the Python port calls
      :meth:`ExitBeacon.close` in a ``finally`` block so the flag flips even
      on an exception path.
    """

    __slots__ = (
        "_index",
        "_registry",
        "_config",
        "_mailbox",
        "_updates_processed",
        "_last_cache_save",
        "_beacon",
    )

    def __init__(
        self,
        *,
        index: ScopeGraphIndex,
        registry: LanguageRegistry,
        config: IndexManagerConfig,
        mailbox: asyncio.Queue[IndexCommand],
        beacon: ExitBeacon,
    ) -> None:
        self._index = index
        self._registry = registry
        self._config = config
        self._mailbox = mailbox
        self._updates_processed = 0
        self._last_cache_save = 0.0
        self._beacon = beacon

    # -- actor entrypoint --------------------------------------------------

    async def run_loop(self) -> None:
        """Drain the mailbox until a :class:`ShutdownCommand` arrives.

        Functional equivalent of grok ``run_loop`` (L797-L828): seed
        ``last_cache_save``, loop ``{ recv -> process; break on false }``,
        then ``save_cache`` on exit. The beacon is closed in a ``finally``
        block so the exit flag flips even if the loop raises.
        """
        self._last_cache_save = time.monotonic()
        try:
            while True:
                cmd = await self._mailbox.get()
                if not self._process_command(cmd):
                    break
        finally:
            self._save_cache()
            self._beacon.close()

    # -- 14-variant dispatch ----------------------------------------------

    def _process_command(self, cmd: IndexCommand) -> bool:
        """Dispatch one command; return ``False`` to break the run_loop.

        Functional equivalent of grok ``process_command_coalesced``
        (L838-L953): a 14-arm ``match`` dispatched via ``isinstance`` (the
        Python analog of a tagged-union match). File events coalesce + drain;
        queries resolve the response Future via ``self._index``;
        ``Shutdown`` returns ``False``. Synchronous -- every query here is an
        in-memory read (the tree-sitter parse path lands in R306g behind its
        own command wiring).
        """
        # --- fire-and-forget: file events (coalesce + drain) ---------------
        if isinstance(cmd, FileEventCommand):
            coalesced = CoalescedEvents()
            coalesced.add(cmd.event)
            return self._drain_and_apply(coalesced)
        if isinstance(cmd, FileEventBatchCommand):
            coalesced = CoalescedEvents()
            for event in cmd.events:
                coalesced.add(event)
            return self._drain_and_apply(coalesced)

        # --- fire-and-forget: rebuild / background refresh / shutdown ------
        if isinstance(cmd, RebuildCommand):
            self._rebuild_index()
            return True
        if isinstance(cmd, BackgroundRefreshCommand):
            self._process_background_refresh(cmd)
            return True
        if isinstance(cmd, ShutdownCommand):
            return False

        # --- request-response: snapshot + goto (symbol extraction R306g) ---
        if isinstance(cmd, GetSnapshotCommand):
            _resolve_response(cmd.response, self._index)
            return True
        if isinstance(cmd, GotoDefinitionCommand):
            _resolve_response(cmd.response, self._handle_goto_definition(cmd))
            return True
        if isinstance(cmd, GotoReferencesCommand):
            _resolve_response(cmd.response, self._handle_goto_references(cmd))
            return True

        # --- request-response: name queries (in-memory, fully wired) -------
        if isinstance(cmd, FindDefinitionsCommand):
            _resolve_response(cmd.response, self._handle_find_definitions(cmd))
            return True
        if isinstance(cmd, FindReferencesCommand):
            _resolve_response(cmd.response, self._handle_find_references(cmd))
            return True

        # --- request-response: lightweight probes --------------------------
        if isinstance(cmd, GetFileCountCommand):
            _resolve_response(cmd.response, self._index.file_count())
            return True
        if isinstance(cmd, GetStatsCommand):
            _resolve_response(cmd.response, IndexStats.new(*self._index.stats()))
            return True
        if isinstance(cmd, GetQueryVersionCommand):
            _resolve_response(cmd.response, self._index.query_version)
            return True
        if isinstance(cmd, HasDefinitionCommand):
            _resolve_response(cmd.response, self._index.has_definition(cmd.symbol))
            return True

        # Forward-tolerant default: an unknown variant is ignored (grok's
        # enum is non-exhaustive across versions; the actor stays alive).
        return True

    def _drain_and_apply(self, coalesced: CoalescedEvents) -> bool:
        """Drain pending file events into ``coalesced``; apply; recurse on non-file.

        Functional equivalent of grok's ``try_recv`` drain loop inside the
        ``FileEvent`` arm (L862-L883): keep pulling while the next command is
        a file event, coalescing each into the batch; on the first non-file
        command, flush the batch (``apply_coalesced``) then process that
        command at depth 1 (it cannot be another file event, so the
        recursion terminates immediately). Returns ``False`` if that drained
        command was :class:`ShutdownCommand` (the stop signal propagates
        through to the run_loop).
        """
        while True:
            try:
                other = self._mailbox.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(other, FileEventCommand):
                coalesced.add(other.event)
            elif isinstance(other, FileEventBatchCommand):
                for event in other.events:
                    coalesced.add(event)
            else:
                self._apply_coalesced(coalesced)
                return self._process_command(other)
        self._apply_coalesced(coalesced)
        return True

    # -- coalesced apply / index mutation ---------------------------------

    def _apply_coalesced(self, coalesced: CoalescedEvents) -> None:
        """Apply a coalesced file-event batch to the index.

        Functional equivalent of grok ``apply_coalesced`` (L1133-L1171):
        iterate the ``path -> kind`` map, gate each on ``should_index``,
        ``Created`` / ``Modified`` / ``Renamed`` -> ``reindex_file``,
        ``Removed`` -> ``remove_file``, then save to cache at most once per
        :data:`_CACHE_SAVE_INTERVAL_SECS`.
        """
        for path, kind in coalesced.events.items():
            if not self._should_index(path):
                continue
            if kind is FileEventKind.REMOVED:
                self._index.remove_file(path)
            else:
                self._reindex_file(path)
            self._updates_processed += 1
        if self._config.save_to_cache and self._updates_processed > 0:
            now = time.monotonic()
            if now - self._last_cache_save >= _CACHE_SAVE_INTERVAL_SECS:
                self._last_cache_save = now
                self._save_cache()

    def _should_index(self, path: str) -> bool:
        """Gate: should ``path`` be indexed? (grok ``should_index``, L1186+).

        The R306f subset applies the hidden-directory classifier only (a
        pure-path test, no I/O). The binary-content probe, the size ceiling,
        and the language-registry gate land with the tree-sitter bridge in
        R306g (they need a file read + a grammar lookup).
        """
        if is_under_hidden_dir(path):
            return False
        return True

    def _reindex_file(self, path: str) -> None:
        """Reindex one file (tree-sitter parse -> index update).

        Lands in R306g (requires the tree-sitter runtime). The R306f stub is
        a no-op so the actor-loop wiring (coalescing, drain, cache-save
        throttling) is testable in isolation against an in-memory index.
        """
        # TODO(R306g): read file, resolve language via the registry,
        # parse with tree-sitter, extract symbols, merge into self._index
        # (intern_symbols_directly).
        return None

    def _rebuild_index(self) -> None:
        """Rebuild the index from scratch (grok ``rebuild_index``, L1252-L1256).

        Swaps in a fresh :class:`ScopeGraphIndex` over ``root_path`` and saves
        it to cache. The fresh walk delegates to :class:`IndexBuilder`
        (R305g); R306g refines the tree-sitter binding behind the same call.
        """
        self._index = self._build_fresh_index()
        self._save_cache()

    def _build_fresh_index(self) -> ScopeGraphIndex:
        """Build a fresh index over ``config.root_path`` (grok ``build_fresh_index``).

        Delegates to :class:`IndexBuilder` (R305g). grok walks the workspace
        on a thread pool; the Python port walks on the calling coroutine
        (the actor task) -- acceptable for a rebuild triggered by an explicit
        :class:`RebuildCommand`, not a hot path.
        """
        return IndexBuilder().build(self._config.root_path)

    def _save_cache(self) -> None:
        """Persist the index to cache (grok ``save_cache``, L1259+).

        No-op when ``save_to_cache`` is ``False`` or ``cache_path`` is
        ``None`` (grok L1259 ``if !self.config.save_to_cache { return; }``).
        Uses the R305f :func:`save_index` helper.
        """
        if not self._config.save_to_cache or self._config.cache_path is None:
            return
        save_index(self._config.cache_path, self._index)

    # -- query handlers ----------------------------------------------------

    def _handle_find_definitions(
        self, cmd: FindDefinitionsCommand
    ) -> list[SymbolLocation]:
        """FindDefinitions: rank definitions toward ``context_file``'s language.

        Delegates to :meth:`ScopeGraphIndex.find_definitions_smart` (R305c),
        which de-duplicates alias-resolved definitions and sorts same-language-
        family entries first.
        """
        results = self._index.find_definitions_smart(
            cmd.symbol, cmd.context_file, self._registry
        )
        return [SymbolLocation.new(path, line) for path, line in results]

    def _handle_find_references(
        self, cmd: FindReferencesCommand
    ) -> list[SymbolLocation]:
        """FindReferences: rank references toward ``context_file``'s language."""
        results = self._index.find_references_smart(
            cmd.symbol, cmd.context_file, self._registry
        )
        return [
            SymbolLocation.with_symbol(path, line, name)
            for name, path, line in results
        ]

    def _handle_goto_definition(
        self, cmd: GotoDefinitionCommand
    ) -> QueryResult | QueryError:
        """GotoDefinition: symbol under cursor -> definition sites.

        Two phases (grok ``handle_goto_definition``): extract the symbol at
        ``(row, col)`` (tree-sitter, R306g), then ``find_definitions_smart``
        resolves it. R306f wires the resolve phase + the error-path guards;
        the symbol-extraction parse lands in R306g (``_get_symbol_at_position``
        currently returns ``NoSymbolAtPosition`` past the row/col guard), so
        every in-bounds GotoDefinition / GotoReferences resolves to the error
        arm until R306g -- the wiring is testable, the parse is not.
        """
        symbol_or_err = self._get_symbol_at_position(cmd.file_path, cmd.row, cmd.col)
        if isinstance(symbol_or_err, QueryError):
            return symbol_or_err
        symbol = symbol_or_err
        results = self._index.find_definitions_smart(
            symbol, cmd.file_path, self._registry
        )
        return QueryResult(
            symbol=symbol,
            locations=[SymbolLocation.new(path, line) for path, line in results],
        )

    def _handle_goto_references(
        self, cmd: GotoReferencesCommand
    ) -> QueryResult | QueryError:
        """GotoReferences: symbol under cursor -> reference sites.

        Folds the definition site into the list when ``include_definition``
        (grok L133).
        """
        symbol_or_err = self._get_symbol_at_position(cmd.file_path, cmd.row, cmd.col)
        if isinstance(symbol_or_err, QueryError):
            return symbol_or_err
        symbol = symbol_or_err
        ref_results = self._index.find_references_smart(
            symbol, cmd.file_path, self._registry
        )
        locations = [
            SymbolLocation.with_symbol(path, line, name)
            for name, path, line in ref_results
        ]
        if cmd.include_definition:
            def_results = self._index.find_definitions_smart(
                symbol, cmd.file_path, self._registry
            )
            for path, line in def_results:
                locations.append(SymbolLocation.with_symbol(path, line, symbol))
        return QueryResult(symbol=symbol, locations=locations)

    def _get_symbol_at_position(
        self, file_path: str, row: int, col: int
    ) -> str | QueryError:
        """Extract the symbol name under ``(row, col)`` in ``file_path``.

        Mirrors grok ``get_symbol_at_position`` (L1046+). R306f implements
        the error-path guards (``row == 0`` or ``col == 0`` ->
        :class:`QueryError` ``NoSymbolAtPosition``); the tree-sitter parse +
        symbol extraction lands in R306g. Until then every in-bounds position
        also reports ``NoSymbolAtPosition`` (the parse half is absent).
        """
        if row == 0 or col == 0:
            return QueryError.no_symbol_at_position(row=row, col=col)
        # TODO(R306g): read file, resolve language via registry.for_file_path,
        # parse with tree-sitter, extract the symbol at (row, col).
        return QueryError.no_symbol_at_position(row=row, col=col)

    def _process_background_refresh(self, cmd: BackgroundRefreshCommand) -> None:
        """Reindex stale files + evict deleted files in the background.

        R306g owns the tree-sitter reindex of ``stale_files`` + the
        background-refresh scheduling (grok L734-L754). The R306f stub evicts
        ``deleted_files`` (a pure ``remove_file`` call, no parse) so the
        command wiring + the eviction path are testable without the parser.
        """
        for path in cmd.deleted_files:
            if self._should_index(path):
                self._index.remove_file(path)
                self._updates_processed += 1
        # TODO(R306g): reindex cmd.stale_files via tree-sitter.

    # -- spawn (production entrypoint) -------------------------------------

    @classmethod
    async def spawn(cls, config: IndexManagerConfig) -> IndexManagerHandle:
        """Spawn an :class:`IndexManager` actor for ``config.root_path``.

        Functional equivalent of grok ``IndexManager::spawn`` (L590-L769),
        adapted to asyncio (not line-ported):

        * **Dedup first.** Canonicalize the root (R306e
          :func:`_canonicalize_root`) and reuse a live handle from
          :data:`_ACTIVE_MANAGERS` if one exists for the same workspace (the
          ``WeakValueDictionary`` auto-drops a dead entry on GC, subsuming
          grok's occupied-but-dead ``remove`` arm).
        * **asyncio.Queue channel.** grok's ``mpsc::channel`` -> one unbounded
          :class:`asyncio.Queue` shared by the handle (producer) and the
          actor task (consumer).
        * **asyncio task, not OS thread.** grok spawns an OS thread that
          blocks on ``recv``; Python spawns a task on the current loop that
          awaits ``mailbox.get()``. The task reference is held in
          :data:`_ACTIVE_ACTOR_TASKS` so the loop's weak task ref does not
          garbage-collect it mid-run.
        * **No ``drop(command_tx)``.** grok drops the actor's sender clone so
          the channel closes when the last handle drops; an
          :class:`asyncio.Queue` has no sender-side lifecycle, so shutdown is
          driven by an explicit :class:`ShutdownCommand`.
        * **Load-or-build + cache.** grok loads from cache when
          ``load_from_cache`` (and a cache exists), else builds fresh and
          saves. The Python port reuses :func:`load_index` (R305f) +
          :class:`IndexBuilder` (R305g).

        Returns the live :class:`IndexManagerHandle`; the actor runs in the
        background on the current event loop.
        """
        root_key = _canonicalize_root(config.root_path)
        existing = _ACTIVE_MANAGERS.get(root_key)
        if existing is not None and not existing.is_closed:
            return existing

        mailbox: asyncio.Queue[IndexCommand] = asyncio.Queue()
        closed = asyncio.Event()
        handle = IndexManagerHandle(mailbox=mailbox, closed=closed)
        _ACTIVE_MANAGERS[root_key] = handle

        index = await cls._load_or_build_index(config)
        beacon = ExitBeacon()
        actor = cls(
            index=index,
            registry=LanguageRegistry.new(),
            config=config,
            mailbox=mailbox,
            beacon=beacon,
        )
        task = asyncio.create_task(actor._run_until_closed(closed))
        _ACTIVE_ACTOR_TASKS.add(task)
        task.add_done_callback(_ACTIVE_ACTOR_TASKS.discard)
        return handle

    async def _run_until_closed(self, closed: asyncio.Event) -> None:
        """Run the loop, then flip ``closed`` (the spawn-task wrapper).

        grok closes the channel by dropping the sender; the Python port flips
        the handle's ``_closed`` :class:`asyncio.Event` once :meth:`run_loop`
        returns (normal shutdown or exception), so callers probing
        :attr:`IndexManagerHandle.is_closed` see the actor as gone.
        """
        try:
            await self.run_loop()
        finally:
            closed.set()

    @staticmethod
    async def _load_or_build_index(
        config: IndexManagerConfig,
    ) -> ScopeGraphIndex:
        """Load the index from cache, else build a fresh one (grok L666-L709).

        grok loads when ``load_from_cache`` and a cache file is present;
        otherwise it builds fresh and (when ``save_to_cache``) writes the
        cache. The Python port reuses :func:`load_index` (R305f) +
        :class:`IndexBuilder` (R305g). Cache-staleness detection (query-hash
        mismatch) lands with the full manager wiring; R306f trusts a present
        cache file.
        """
        if config.load_from_cache and config.cache_path is not None:
            try:
                return load_index(config.cache_path)
            except (OSError, CacheError):
                pass  # fall through to a fresh build
        return IndexBuilder().build(config.root_path)


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
    # R306c -- the 14 mailbox-command variants. The base :class:`IndexCommand`
    # is re-exported at the crate root (grok ``lib.rs`` L84 re-exports the
    # enum); the 14 variant subclasses stay leaf-module-only (grok models
    # them as enum members, not free symbols) -- callers reach them via
    # ``minimax_code.xai_codebase_graph.index_manager.FileEventCommand`` etc.
    "IndexCommand",
    "FileEventCommand",
    "FileEventBatchCommand",
    "RebuildCommand",
    "BackgroundRefreshCommand",
    "ShutdownCommand",
    "GetSnapshotCommand",
    "GotoDefinitionCommand",
    "GotoReferencesCommand",
    "FindDefinitionsCommand",
    "FindReferencesCommand",
    "GetFileCountCommand",
    "GetStatsCommand",
    "GetQueryVersionCommand",
    "HasDefinitionCommand",
    # R306d -- the actor sender handle. grok ``lib.rs`` L85 re-exports
    # ``IndexManagerHandle`` at the crate root, so the Python crate-root
    # barrel mirrors it; grok has no crate-root ``SendError`` re-export (the
    # crossbeam channel error lives leaf-module-only), so the Python-only
    # ``ManagerClosedError`` -- the channel-closed carrier that replaces
    # crossbeam's ``SendError`` in the asyncio port -- stays leaf-module-only
    # too (callers import it from ``index_manager``).
    "IndexManagerHandle",
    "ManagerClosedError",
]
