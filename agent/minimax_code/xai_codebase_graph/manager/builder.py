"""Parallel pipelined index builder (R305g).

Ported from grok ``xai-codebase-graph/src/manager/builder.rs`` (507 lines) --
the builder that walks a workspace, parses each supported file with a
thread-local-cached tree-sitter parser/query, extracts lightweight symbol
occurrences (definitions / references / aliases), and merges them into a
single :class:`ScopeGraphIndex` with bounded two-phase peak memory.

This module owns four concerns and nothing else:

1. **Error hierarchy** -- :class:`IndexBuildError` + 3 subclasses mirroring
   grok's ``enum IndexError`` (``WalkError`` / ``ThreadPanic`` / ``IoError``).
   Named ``IndexBuildError`` (not ``IndexError``) to avoid shadowing the
   Python built-in :class:`IndexError`.
2. **IndexBuilder** -- the builder-pattern configurator (threads / chunk /
   batch sizes / gitignore / hidden toggles) plus the :meth:`build` entry.
3. **File collection** -- dual strategy: ``git ls-files`` subprocess (mirrors
   grok ``git2`` index read) with an ``os.walk`` fallback (mirrors grok
   ``ignore::WalkBuilder``).
4. **Orchestration** -- :func:`process_file_fast` (single-file extraction,
   reuses R305e :func:`extract_symbols_fast`) + :meth:`IndexBuilder._build_fast`
   (parallel :class:`~concurrent.futures.ThreadPoolExecutor` + bounded
   merge-batches).

Functional clone, not line-by-line: grok's ``extract_symbols_fast_inline`` is
an inlined hot-loop copy of :func:`scope_graph.bridge.extract_symbols_fast`
(R305e). The Python port reuses the R305e free function instead of
duplicating the capture-walk logic -- the symbol-extraction behaviour is
identical, the only divergence is the call boundary (inlined vs. function
call), a micro-optimisation with no measurable effect under the GIL. The
parallelisation primitive also diverges by necessity: grok uses ``rayon``
(work-stealing thread pool + ``par_chunks``); the Python port uses
:class:`~concurrent.futures.ThreadPoolExecutor`, which provides the same
N-workers parallelism and bounded-batch merge semantics -- though the GIL
means true CPU parallelism is limited to the C-level tree-sitter parse. The
two-phase memory bound (``build_batch_size``) is preserved exactly.

Naming: grok's ``respect_gitignore`` / ``skip_hidden`` builder methods share
their names with the struct fields (legal in Rust, illegal in Python -- a
field assignment would shadow the method). The Python port keeps the grok
method names (API parity, no ``with_`` prefix -- they are boolean toggles)
and names the backing fields ``_respect_gitignore`` / ``_skip_hidden``.

Consumes: R300 (``types`` -- :class:`FileMeta` / :class:`SymbolAlias` /
:class:`SymbolOccurrence`), R304 (:class:`LanguageRegistry`), R305c
(:class:`ScopeGraphIndex`), R305e (:func:`extract_symbols_fast`), R44
(:func:`paths.to_relative_path`). ``MAX_INDEXABLE_FILE_SIZE`` is defined
locally here (5 MiB, mirroring grok ``index_manager.rs:42``) until the
``index_manager`` module lands and lifts it to a shared constant (YAGNI:
the builder is currently its only consumer).
"""

from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any

from minimax_code.paths import to_relative_path
from minimax_code.xai_codebase_graph.languages import LanguageRegistry
from minimax_code.xai_codebase_graph.scope_graph import ScopeGraphIndex
from minimax_code.xai_codebase_graph.scope_graph.bridge import extract_symbols_fast
from minimax_code.xai_codebase_graph.types import (
    FileMeta,
    SymbolAlias,
    SymbolOccurrence,
)

if TYPE_CHECKING:
    from os import PathLike

    from minimax_code.xai_codebase_graph.languages.types import TSLanguageConfig

__all__ = [
    "MAX_INDEXABLE_FILE_SIZE",
    "IndexBuildError",
    "IndexBuilder",
    "IndexIOError",
    "IndexThreadPanic",
    "IndexWalkError",
]

#: Maximum indexable file size, 5 MiB (grok ``index_manager.rs:42``).
#:
#: Defined locally here until ``index_manager.py`` lands and lifts this to a
#: shared constant -- the builder is currently its only consumer (YAGNI).
MAX_INDEXABLE_FILE_SIZE: int = 5 * 1024 * 1024

#: Prefix-read length for the binary-detection probe. grok's
#: ``process_file_fast`` reads an 8000-byte prefix and treats a NUL byte as
#: evidence of binary content; mirroring the 8KB probe avoids reading the
#: whole file just to classify it.
_BINARY_PROBE_BYTES: int = 8000

#: Errors from the tree-sitter binding layer that :func:`_get_parser_and_query`
#: swallows to degrade gracefully. ``ImportError`` = the ``tree_sitter``
#: package is absent; ``RuntimeError`` = :meth:`TSLanguageConfig.language` /
#: :meth:`compile_query` raised because no grammar is bound (R303 deferred
#: binding). Both mirror grok's ``?`` short-circuit when ``parser.parse``
#: yields ``None``.
_TS_BINDING_ERRORS: tuple[type[BaseException], ...] = (ImportError, RuntimeError)


# === Error hierarchy ======================================================
# grok ``enum IndexError`` -> base + 3 subclasses (functional clone; see
# cache.py R305f for the subclass-per-variant rationale).


class IndexBuildError(Exception):
    """Base for index building (mirrors grok ``IndexError`` enum).

    Named ``IndexBuildError`` rather than ``IndexError`` to avoid shadowing
    the Python built-in :class:`IndexError` -- grok's enum lives in a
    separate namespace so the collision does not arise there.
    """


class IndexWalkError(IndexBuildError):
    """Directory walk / thread-pool build failure (mirrors ``IndexError::WalkError``)."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class IndexThreadPanic(IndexBuildError):
    """Worker thread raised an unexpected exception (mirrors ``IndexError::ThreadPanic``)."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class IndexIOError(IndexBuildError):
    """IO failure during build (mirrors ``IndexError::IoError(io::Error)``).

    Wraps the underlying :class:`OSError` so callers can inspect ``.cause``.
    """

    def __init__(self, cause: OSError | str) -> None:
        self.cause = cause
        super().__init__(str(cause))


# === Thread-local parser / query cache ====================================
# grok ``thread_local!{ PARSER_CACHE, QUERY_CACHE }`` keyed by language id.

_local = threading.local()


def _set_language(parser: Any, ts_lang: Any) -> bool:
    """Bind ``ts_lang`` to ``parser``; return success (mirrors grok ``let _ =``).

    Tolerates tree_sitter API versions: ``>= 0.21`` uses the
    ``parser.language`` property, ``< 0.21`` uses the ``parser.set_language``
    method. Returns ``False`` if both fail so the caller degrades to
    ``(None, None)`` -- a parser with no bound language yields ``None`` from
    ``parse``, which :func:`process_file_fast` treats as "unsupported file"
    (mirrors grok's ``?`` short-circuit on ``parser.parse() == None``).
    """
    try:
        parser.language = ts_lang  # type: ignore[attr-defined]
        return True
    except (AttributeError, TypeError):
        pass
    try:
        parser.set_language(ts_lang)  # type: ignore[attr-defined]
        return True
    except Exception:  # noqa: BLE001 -- swallow: degrade via None-return path
        return False


def _get_parser_and_query(
    lang_config: TSLanguageConfig,
) -> tuple[Any, Any]:
    """Return cached ``(parser, query)`` for ``lang_config``'s language, else ``(None, None)``.

    Mirrors grok's per-thread ``PARSER_CACHE`` / ``QUERY_CACHE`` (keyed by
    language id via ``thread_local!``). Returns ``(None, None)`` when the
    tree-sitter runtime (``ImportError``) or the language grammar
    (``RuntimeError`` from :meth:`TSLanguageConfig.language` /
    :meth:`compile_query`) is unavailable, so :func:`process_file_fast` can
    degrade gracefully -- the Python equivalent of grok's ``?`` short-circuit
    when ``parser.parse`` yields ``None``.
    """
    lang_id = lang_config.primary_language_id()
    cache = getattr(_local, "by_lang", None)
    if cache is None:
        cache = {}
        _local.by_lang = cache
    cached = cache.get(lang_id)
    if cached is not None:
        return cached
    pq: tuple[Any, Any] = (None, None)
    try:
        import tree_sitter  # noqa: PLC0415 -- lazy: single tree-sitter seam (mirrors bridge.py)

        ts_lang = lang_config.language()
        if ts_lang is not None:
            parser = tree_sitter.Parser()
            # grok ``let _ = p.set_language(&ts_lang)`` ignores bind failure;
            # _set_language tolerates tree_sitter API versions and reports failure.
            if _set_language(parser, ts_lang):
                query = lang_config.compile_query()
                pq = (parser, query)
    except _TS_BINDING_ERRORS:
        pq = (None, None)
    cache[lang_id] = pq
    return pq


# === Single-file extraction ===============================================


@dataclass(slots=True)
class _FileSymbols:
    """Lightweight per-file extraction result (grok ``FileSymbols`` struct).

    Private (underscore prefix) because grok's struct is module-private too;
    only :func:`process_file_fast` constructs it and
    :meth:`IndexBuilder._build_fast` consumes it. ``slots=True`` reduces the
    per-instance memory footprint -- the builder holds up to
    ``build_batch_size`` of these simultaneously.
    """

    path: str
    definitions: list[SymbolOccurrence]
    references: list[SymbolOccurrence]
    aliases: list[SymbolAlias]
    file_meta: FileMeta


def process_file_fast(
    path: str | PathLike[str],
    root_path: str | PathLike[str],
    registry: LanguageRegistry,
) -> _FileSymbols | None:
    """Extract symbols from one file (grok ``process_file_fast``).

    Returns a :class:`_FileSymbols` or ``None``. ``None`` is returned for any
    of: unsupported file type (no language config), empty / over-size file,
    binary content (NUL in the 8KB prefix), unreadable file, or a missing
    tree-sitter binding (the parse step degrades to ``None``, mirroring
    grok's ``?`` short-circuit).

    Symbol extraction reuses R305e :func:`extract_symbols_fast`; the only
    post-processing is converting the tree-sitter :class:`~.types.Range`
    (0-indexed ``line``) into 1-indexed :class:`SymbolOccurrence` line
    numbers and the absolute path into a repo-relative path via
    :func:`paths.to_relative_path`.
    """
    lang_config = registry.for_file_path(path)
    if lang_config is None:
        return None

    try:
        stat = os.stat(path)
    except OSError:
        return None

    size = stat.st_size
    if size == 0 or size > MAX_INDEXABLE_FILE_SIZE:
        return None

    # 8KB binary-prefix probe (grok NUL check) -- avoids reading the whole file.
    try:
        with open(path, "rb") as f:
            prefix = f.read(_BINARY_PROBE_BYTES)
    except OSError:
        return None
    if b"\x00" in prefix:
        return None

    try:
        content = Path(path).read_bytes()
    except OSError:
        return None

    # Parse + extract (thread-local cached parser/query). Degrades to None
    # when tree-sitter runtime / grammar is unavailable (RuntimeError /
    # ImportError), mirroring grok's ``?`` short-circuit on parse() == None.
    parser, query = _get_parser_and_query(lang_config)
    if parser is None or query is None:
        return None
    tree = parser.parse(content)
    if tree is None:
        return None
    root_node = tree.root_node

    definitions, references, alias_pairs = extract_symbols_fast(
        query, root_node, content, lang_config
    )

    rel_path = to_relative_path(root_path, path)
    return _FileSymbols(
        path=_relative_path_str(rel_path),
        definitions=[
            SymbolOccurrence.new(name, rng.start.line + 1) for name, rng in definitions
        ],
        references=[
            SymbolOccurrence.new(name, rng.start.line + 1) for name, rng in references
        ],
        aliases=[SymbolAlias.new(alias, original) for alias, original in alias_pairs],
        file_meta=FileMeta.from_stat(stat),
    )


def _relative_path_str(rel_path: PurePath) -> str:
    """Render a relative path with ``/`` separators (grok ``to_string_lossy``)."""
    return rel_path.as_posix()


# === IndexBuilder =========================================================


def _default_num_threads() -> int:
    """N-1 cores, minimum 1 (grok ``default_num_threads``)."""
    return max(1, (os.cpu_count() or 1) - 1)


class IndexBuilder:
    """Builder for a workspace symbol index (grok ``IndexBuilder``).

    Uses parallel processing with thread-local parser/query caching,
    chunked parsing for cache locality, lightweight symbol extraction
    (reuses R305e :func:`extract_symbols_fast`), and bounded merge-batching
    to cap two-phase peak memory.

    The builder-pattern setters return ``self`` for chaining, mirroring
    grok's ``#[must_use] fn ... -> Self`` API. ``respect_gitignore`` and
    ``skip_hidden`` keep their grok method names (no ``with_`` prefix --
    they are boolean toggles); the backing fields are named
    ``_respect_gitignore`` / ``_skip_hidden`` because Python, unlike Rust,
    does not let a field and a method share a name.
    """

    def __init__(self) -> None:
        self.registry: LanguageRegistry = LanguageRegistry()
        self.num_threads: int = _default_num_threads()
        self._respect_gitignore: bool = True
        self._skip_hidden: bool = True
        self.chunk_size: int = 100
        self.build_batch_size: int = 5_000

    # --- construction ------------------------------------------------------

    @classmethod
    def with_registry(cls, registry: LanguageRegistry) -> IndexBuilder:
        """Build with a custom language registry (grok ``with_registry``)."""
        b = cls()
        b.registry = registry
        return b

    # --- setters (chainable) ----------------------------------------------

    def with_threads(self, count: int) -> IndexBuilder:
        """Set the worker-thread count (default: N-1 cores)."""
        self.num_threads = count
        return self

    def with_chunk_size(self, size: int) -> IndexBuilder:
        """Set the parse-chunk size for cache locality (default: 100)."""
        self.chunk_size = size
        return self

    def with_build_batch_size(self, size: int) -> IndexBuilder:
        """Set the merge-batch size (default: 5000 files per batch).

        Values below ``chunk_size`` are clamped to ``chunk_size`` at build
        time, so the call order of :meth:`with_build_batch_size` and
        :meth:`with_chunk_size` does not matter.
        """
        self.build_batch_size = size
        return self

    def respect_gitignore(self, respect: bool) -> IndexBuilder:
        """Toggle ``.gitignore`` respect (default: ``True``)."""
        self._respect_gitignore = respect
        return self

    def skip_hidden(self, skip: bool) -> IndexBuilder:
        """Toggle skipping hidden files/directories (default: ``True``)."""
        self._skip_hidden = skip
        return self

    # --- build entry -------------------------------------------------------

    def build(self, root_path: str | PathLike[str]) -> ScopeGraphIndex:
        """Build an index from ``root_path`` (grok ``build``).

        Walks the directory tree (``git ls-files`` when available, else
        ``os.walk``), parses each supported file, and merges the results
        into a single :class:`ScopeGraphIndex`. An empty workspace still
        yields a valid index with the query-version stamp set so cache
        validation works. File paths are stored **relative** to
        ``root_path`` for portability.
        """
        file_paths = self._collect_files(root_path)
        if not file_paths:
            index = ScopeGraphIndex()
            # Set query version even for an empty index so cache validation works.
            index.set_query_version(self.registry.compute_query_hash())
            return index
        return self._build_fast(root_path, file_paths)

    # --- file collection (dual strategy) ----------------------------------

    def _collect_files(self, root_path: str | PathLike[str]) -> list[str]:
        """Collect supported files: git index first, walk fallback (grok ``collect_files``)."""
        files = self._collect_files_git(root_path)
        if files:
            return files
        return self._collect_files_walk(root_path)

    def _collect_files_git(self, root_path: str | PathLike[str]) -> list[str]:
        """Collect tracked files via ``git ls-files`` (grok ``collect_files_git`` via git2).

        grok opens the repository with ``git2`` and iterates the index; the
        Python port shells out to ``git ls-files`` (the closest equivalent
        without a native libgit2 binding). Untracked files are excluded for
        the same reasons grok gives: they are a small minority, will be
        picked up by fsnotify on creation, and enumerating them is slow.

        Returns an empty list (not an error) when the path is not a git
        repository or git is unavailable -- the caller falls back to
        :meth:`_collect_files_walk`.
        """
        try:
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=str(root_path),
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        root = str(root_path)
        files: list[str] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line and self.registry.is_supported(line):
                files.append(os.path.join(root, line))
        return files

    def _collect_files_walk(self, root_path: str | PathLike[str]) -> list[str]:
        """Collect files by walking the tree (grok ``collect_files_walk`` via ignore).

        grok uses ``ignore::WalkBuilder`` with full gitignore semantics
        (``.gitignore`` / ``.git/info/exclude`` / global gitignore). The
        Python port uses :func:`os.walk` with a simplified policy: prune
        hidden entries when :attr:`_skip_hidden` is set, and always prune
        ``.git`` to avoid descending into repository internals. Full
        gitignore parsing is deliberately omitted (YAGNI): the walk path is
        only taken when ``git ls-files`` returns nothing, which means the
        directory is not a git repository -- and a non-git directory has no
        ``.gitignore`` to honour. ``respect_gitignore`` is therefore a
        no-op on this path but kept on the builder for API parity with grok.
        """
        files: list[str] = []
        root = Path(root_path)
        for dirpath, dirnames, filenames in os.walk(root):
            if self._skip_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            # Always prune .git so the walk never descends into repo internals.
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for fn in filenames:
                if self._skip_hidden and fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                if self.registry.is_supported(full):
                    files.append(full)
        return files

    # --- parallel build orchestration -------------------------------------

    def _build_fast(
        self,
        root_path: str | PathLike[str],
        file_paths: list[str],
    ) -> ScopeGraphIndex:
        """Two-phase parallel build (grok ``build_fast``).

        Phase 1 (parallel): parse each file and extract lightweight
        :class:`_FileSymbols`. Phase 2 (sequential): merge into one
        :class:`ScopeGraphIndex` so all names deduplicate through a single
        interner. File paths are stored relative to ``root_path`` for
        portability.

        Files are processed in bounded merge-batches of
        ``build_batch_size`` (clamped against ``chunk_size``) to cap
        two-phase peak memory -- peak is ``O(build_batch_size)`` symbols
        plus the growing index, never ``O(total_files)``.
        """
        if self.num_threads < 1:
            # grok's rayon ThreadPoolBuilder failure maps to IndexError::WalkError;
            # an invalid thread count is the Python equivalent trigger.
            raise IndexWalkError(
                f"num_threads must be >= 1, got {self.num_threads}"
            )

        chunk_size = self.chunk_size
        # Clamp against chunk_size so setter call order does not matter (grok L300).
        build_batch_size = max(self.build_batch_size, chunk_size)
        registry = self.registry

        index = ScopeGraphIndex()

        def _process_one(path: str) -> _FileSymbols | None:
            try:
                return process_file_fast(path, root_path, registry)
            except Exception as exc:  # noqa: BLE001 -- wrap as ThreadPanic (grok rayon panic)
                raise IndexThreadPanic(str(exc)) from exc

        with ThreadPoolExecutor(max_workers=self.num_threads) as pool:
            for start in range(0, len(file_paths), build_batch_size):
                batch = file_paths[start : start + build_batch_size]
                # Phase 1: parallel parse + extract for the batch.
                batch_symbols = list(pool.map(_process_one, batch))
                # Phase 2: sequential merge into the single interner-backed index.
                for file_syms in batch_symbols:
                    if file_syms is None:
                        continue
                    path_str = file_syms.path
                    for sym in file_syms.definitions:
                        index.add_definition(sym.name, path_str, sym.line)
                    for sym in file_syms.references:
                        index.add_reference(sym.name, path_str, sym.line)
                    for alias in file_syms.aliases:
                        index.add_alias(alias.alias, alias.original)
                    index.set_file_meta(path_str, file_syms.file_meta)
                # batch_symbols rebound next iteration -- the previous batch's
                # parallel-extracted symbols are freed before the next parses.

        # Set the query-version hash so query changes invalidate the cache.
        index.set_query_version(registry.compute_query_hash())
        # Reclaim over-allocated capacity from the bulk merge (grok ``compact``).
        index.compact()
        return index
