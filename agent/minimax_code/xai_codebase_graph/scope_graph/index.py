"""ScopeGraphIndex runtime -- cross-file symbol index (direction (2), brick 6c).

Ported from grok ``xai-codebase-graph/src/scope_graph/graph.rs`` (the
``ScopeGraphIndex`` in-memory runtime). The SGIX binary ser/de block at the
tail of grok ``graph.rs`` lands separately in R305d; the tree-sitter bridge
free functions (``build_scope_graph`` / ``extract_symbols_fast``) land in
R305e.

A :class:`ScopeGraphIndex` aggregates many per-file :class:`ScopeGraph`
instances into a single cross-file symbol index: every definition / reference
name is interned once, every ``(symbol, (path, line))`` occurrence is
recorded, and aliases (``fn`` -> ``function`` etc.) resolve across the index.
Reverse indexes (``file_to_defs`` / ``file_to_refs``) make per-file removal /
rename O(symbols in the file) instead of O(all symbols).

Migration decision matrix (grok ``graph.rs`` ScopeGraphIndex runtime)
--------------------------------------------------------------------

Struct fields (11): Pythonic substitutions preserve the functional contract.
* ``interner: StringInterner`` -- reused verbatim (R302).
* ``graphs: HashMap<StringId, ScopeGraph>`` -> ``dict``.
* ``definitions`` / ``references: HashMap<StringId, Vec<(StringId, u32)>>``
  -> ``dict[StringId, list[tuple[StringId, int]]]``.
* ``aliases: HashMap<StringId, StringId>`` -> ``dict``.
* ``reverse_aliases: HashMap<StringId, AHashSet<StringId>>`` -> ``dict[.., set]``.
* ``file_meta: HashMap<StringId, FileMeta>`` -> ``dict``.
* ``query_version: QueryVersion`` -- reused verbatim (R305a).
* ``file_to_defs`` / ``file_to_refs: HashMap<StringId, AHashSet<StringId>>``
  -> ``dict[.., set]``.

u32 line saturation: grok stores line as ``u32`` via
``line.min(u32::MAX as usize) as u32``. Python has no ``u32`` type, so lines
are stored as ``int`` but saturated to ``U32_MAX`` via :func:`_saturate_line`
-- preserving the functional contract (huge line numbers do not overflow or
panic) and keeping the stored value byte-identical to grok's SGIX output.

Path parameters: grok takes ``&Path`` / ``PathBuf`` and stringifies via
``to_string_lossy()`` before interning. Python takes ``str`` directly (paths
are already strings), so there is no lossy-conversion step.

Extension extraction: grok ``Path::new(p).extension()``. The Pythonic
``os.path.splitext(p)[1].lstrip(".")`` matches it (only the final extension
segment, no leading dot, ``""`` when absent).

smart ranking (``find_*_smart``): grok sorts "same-extension-family first,
then lexicographic". Python expresses that as
``sorted(key=lambda x: (not same_lang, path))`` -- ``False`` sorts before
``True``, so same-language entries surface first.

``compact()``: grok calls ``Vec::shrink_to_fit`` on every container plus
``interner.shrink_to_fit``. Python ``list`` / ``dict`` / ``set`` do not retain
``Vec``-style 2x excess capacity, so the only shrink hook is
:meth:`StringInterner.shrink_to_fit` (itself a no-op). ``compact`` calls it
for API parity and is idempotent.

YAGNI boundaries (land later, NOT in R305c):
* SGIX v1 binary ser/de (``save`` / ``load`` / ``write_to`` / ``read_from``)
  -- R305d. Requires adapting the Python interner's ``to_parts`` /
  ``from_parts`` single-array format to grok's arena+offsets layout (or
  defining a Python-native SGIX envelope).
* tree-sitter bridge free functions -- R305e (needs the ``tree_sitter`` PyPI
  binding).
* ``manager/`` (IndexManager + cache ser/de), ``navigation`` -- post-R305e.

Public surface: :class:`ScopeGraphIndex` is re-exported from both the
``scope_graph`` subpackage barrel (mirrors grok ``scope_graph/mod.rs``) and
the crate root (mirrors grok ``lib.rs`` re-export alongside ``ScopeGraph``).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import TYPE_CHECKING

from minimax_code.xai_codebase_graph.interner import StringId, StringInterner
from minimax_code.xai_codebase_graph.scope_graph.graph import (
    QueryVersion,
    ScopeGraph,
)
from minimax_code.xai_codebase_graph.types import FileMeta

if TYPE_CHECKING:
    # ``LanguageRegistry`` is only a parameter type on ``find_*_smart`` /
    # ``_extensions_same_language``; the methods duck-type the registry at call
    # time, so importing it at runtime would create a circular import
    # (``languages`` -> ``scope_graph.nodes`` -> this module).
    from minimax_code.xai_codebase_graph.languages import LanguageRegistry

# u32 saturation boundary. grok stores line numbers as ``u32`` and saturates
# at ``u32::MAX``; the Python port stores lines as ``int`` but clamps to this
# value so the stored data is byte-identical to grok's SGIX output (R305d)
# and the functional contract (no overflow / panic) is preserved.
U32_MAX: int = 0xFFFFFFFF


def _saturate_line(line: int) -> int:
    """Saturate ``line`` to the ``u32`` range.

    Mirrors grok ``line.min(u32::MAX as usize) as u32``: any line number past
    ``U32_MAX`` collapses to ``U32_MAX`` rather than overflowing or raising.
    """
    return line if line < U32_MAX else U32_MAX


def _ext_of(path: str) -> str:
    """Return ``path``'s final extension without the leading dot.

    Mirrors grok ``Path::new(path).extension().and_then(|e| e.to_str()).unwrap_or("")``:
    only the last extension segment is returned, with no leading dot, and an
    absent extension yields ``""``. ``os.path.splitext`` keeps the dot, so we
    strip it; ``lstrip(".")`` is safe on the empty string.
    """
    return os.path.splitext(path)[1].lstrip(".")


class ScopeGraphIndex:
    """Cross-file symbol index over many per-file :class:`ScopeGraph` instances.

    Holds interned symbol names, per-symbol definition / reference locations,
    alias mappings (with reverse lookup), per-file metadata for staleness
    checks, the per-index query version stamp, and reverse file->symbol
    indexes for O(symbols-in-file) removal / rename.

    The runtime is in-memory only in R305c; SGIX binary ser/de lands in R305d.
    """

    __slots__ = (
        "interner",
        "graphs",
        "definitions",
        "references",
        "aliases",
        "reverse_aliases",
        "file_meta",
        "query_version",
        "file_to_defs",
        "file_to_refs",
    )

    def __init__(self) -> None:
        """Initialise an empty index (mirrors grok ``ScopeGraphIndex::new``)."""
        self.interner: StringInterner = StringInterner()
        self.graphs: dict[StringId, ScopeGraph] = {}
        self.definitions: dict[StringId, list[tuple[StringId, int]]] = {}
        self.references: dict[StringId, list[tuple[StringId, int]]] = {}
        self.aliases: dict[StringId, StringId] = {}
        self.reverse_aliases: dict[StringId, set[StringId]] = {}
        self.file_meta: dict[StringId, FileMeta] = {}
        # Legacy (version is None) -- forces a rebuild until stamped.
        self.query_version: QueryVersion = QueryVersion()
        self.file_to_defs: dict[StringId, set[StringId]] = {}
        self.file_to_refs: dict[StringId, set[StringId]] = {}

    # === String interning helpers ===========================================

    def intern(self, s: str) -> StringId:
        """Intern ``s`` and return its stable :class:`StringId`.

        Mirrors grok ``ScopeGraphIndex::intern`` -- a thin delegate to the
        backing :class:`StringInterner`.
        """
        return self.interner.intern(s)

    def get_str(self, string_id: StringId) -> str | None:
        """Return the interned string for ``string_id``, or ``None``.

        Mirrors grok ``ScopeGraphIndex::get_str``. Returns ``None`` when the
        id is out of range or the stored bytes are not valid UTF-8.
        """
        return self.interner.get(string_id)

    def get_id(self, s: str) -> StringId | None:
        """Return the :class:`StringId` for ``s`` if interned, else ``None``.

        Mirrors grok ``ScopeGraphIndex::get_id``.
        """
        return self.interner.get_id(s)

    # === File metadata ======================================================

    def update_file_meta(self, path: str) -> None:
        """Stat ``path`` and record its :class:`FileMeta`.

        Mirrors grok ``update_file_meta``: a missing file is silently skipped
        (grok ``if let Ok(meta) = std::fs::metadata(path)``).
        """
        try:
            stat = os.stat(path)
        except OSError:
            return
        path_id = self.intern(path)
        self.file_meta[path_id] = FileMeta.from_stat(stat)

    def is_file_stale(self, path: str) -> bool:
        """True if ``path`` is not indexed or its on-disk state changed.

        Mirrors grok ``is_file_stale``: an unknown path or a missing cached
        entry are both stale; otherwise delegates to
        :meth:`FileMeta.is_stale` (which itself returns ``True`` if the file
        is gone on disk).
        """
        path_id = self.get_id(path)
        if path_id is None:
            return True
        cached = self.file_meta.get(path_id)
        if cached is None:
            return True
        return cached.is_stale(path)

    # === Aliases ============================================================

    def add_alias(self, alias_name: str, original_name: str) -> None:
        """Record that ``alias_name`` resolves to ``original_name``.

        Mirrors grok ``add_alias``: updates the forward ``aliases`` map and
        the reverse ``reverse_aliases`` index. Both names are interned
        (created if absent). grok's ``add_alias_arc`` overload (``Arc<str>``
        input) is not ported -- Python ``str`` already shares storage like an
        ``Arc``, so there is no separate entry point.
        """
        alias_id = self.intern(alias_name)
        original_id = self.intern(original_name)
        self.aliases[alias_id] = original_id
        self.reverse_aliases.setdefault(original_id, set()).add(alias_id)

    # === Symbol insertion ===================================================

    def add_definition_with_path_id(
        self, symbol: str, path_id: StringId, line: int
    ) -> None:
        """Record a definition of ``symbol`` at ``(path_id, line)``.

        Mirrors grok ``add_definition_with_path_id``: saturates the line to
        ``u32``, interns the symbol, appends the location, and updates the
        reverse ``file_to_defs`` index.
        """
        line_u32 = _saturate_line(line)
        symbol_id = self.intern(symbol)
        self.definitions.setdefault(symbol_id, []).append((path_id, line_u32))
        self.file_to_defs.setdefault(path_id, set()).add(symbol_id)

    def add_definition(self, symbol: str, path: str, line: int) -> None:
        """Record a definition of ``symbol`` at ``(path, line)``.

        Mirrors grok ``add_definition``: interns ``path`` first, then
        delegates to :meth:`add_definition_with_path_id`.
        """
        path_id = self.intern(path)
        self.add_definition_with_path_id(symbol, path_id, line)

    def add_reference_with_path_id(
        self, symbol: str, path_id: StringId, line: int
    ) -> None:
        """Record a reference to ``symbol`` at ``(path_id, line)``.

        Mirrors grok ``add_reference_with_path_id``: saturates the line,
        interns the symbol, appends the location, and updates the reverse
        ``file_to_refs`` index.
        """
        line_u32 = _saturate_line(line)
        symbol_id = self.intern(symbol)
        self.references.setdefault(symbol_id, []).append((path_id, line_u32))
        self.file_to_refs.setdefault(path_id, set()).add(symbol_id)

    def add_reference(self, symbol: str, path: str, line: int) -> None:
        """Record a reference to ``symbol`` at ``(path, line)``.

        Mirrors grok ``add_reference``: interns ``path`` first, then delegates
        to :meth:`add_reference_with_path_id`.
        """
        path_id = self.intern(path)
        self.add_reference_with_path_id(symbol, path_id, line)

    def set_file_meta(self, path: str, meta: FileMeta) -> None:
        """Attach ``meta`` to ``path`` (caller-supplied, no stat).

        Mirrors grok ``set_file_meta``.
        """
        path_id = self.intern(path)
        self.file_meta[path_id] = meta

    def has_definition(self, symbol: str) -> bool:
        """True if any definition of ``symbol`` is indexed.

        Mirrors grok ``has_definition``.
        """
        symbol_id = self.get_id(symbol)
        return symbol_id is not None and symbol_id in self.definitions

    def get_file_meta(self, path: str) -> FileMeta | None:
        """Return the cached :class:`FileMeta` for ``path``, or ``None``.

        Mirrors grok ``get_file_meta``.
        """
        path_id = self.get_id(path)
        if path_id is None:
            return None
        return self.file_meta.get(path_id)

    def file_paths_with_meta(self) -> Iterator[tuple[str, FileMeta]]:
        """Yield ``(path, meta)`` for every file with cached metadata.

        Mirrors grok ``file_paths_with_meta``: an iterator over the interned
        paths that have a ``FileMeta`` entry.
        """
        for path_id, meta in self.file_meta.items():
            path = self.get_str(path_id)
            if path is not None:
                yield (path, meta)

    # === File operations ====================================================

    def add_file(self, file_path: str, graph: ScopeGraph, src: bytes) -> None:
        """Index ``graph``'s definitions / references under ``file_path``.

        Mirrors grok ``add_file``: interns the path, walks the graph's
        definitions and references, converts each range's 0-indexed start line
        to 1-indexed (``start_line() + 1``) and saturates to ``u32``, appends
        the locations, maintains the reverse indexes, and stores the graph
        keyed by path id.

        ``src`` is the raw file bytes the graph was built from; the graph uses
        it to decode identifier names.
        """
        path_id = self.intern(file_path)
        for name, rng in graph.get_definitions(src):
            name_id = self.intern(name)
            line = _saturate_line(rng.start_line() + 1)
            self.definitions.setdefault(name_id, []).append((path_id, line))
            self.file_to_defs.setdefault(path_id, set()).add(name_id)
        for name, rng in graph.get_references(src):
            name_id = self.intern(name)
            line = _saturate_line(rng.start_line() + 1)
            self.references.setdefault(name_id, []).append((path_id, line))
            self.file_to_refs.setdefault(path_id, set()).add(name_id)
        self.graphs[path_id] = graph

    def remove_file(self, file_path: str) -> None:
        """Drop ``file_path`` and all its symbols from the index.

        Mirrors grok ``remove_file``: uses the reverse ``file_to_defs`` /
        ``file_to_refs`` indexes to touch only the symbols that appeared in
        this file (O(symbols in file), not O(all symbols)), prunes emptied
        location lists, and clears the graph + metadata.
        """
        path_id = self.get_id(file_path)
        if path_id is None:
            return
        self.graphs.pop(path_id, None)
        self.file_meta.pop(path_id, None)

        def _prune(
            forward: dict[StringId, list[tuple[StringId, int]]],
            reverse_map: dict[StringId, set[StringId]],
        ) -> None:
            symbol_ids = reverse_map.pop(path_id, None)
            if symbol_ids is None:
                return
            for symbol_id in symbol_ids:
                locs = forward.get(symbol_id)
                if locs is None:
                    continue
                # Retain only locations whose path is NOT the removed file.
                locs[:] = [(p, ln) for (p, ln) in locs if p != path_id]
                if not locs:
                    forward.pop(symbol_id, None)

        _prune(self.definitions, self.file_to_defs)
        _prune(self.references, self.file_to_refs)

    def indexed_files(self) -> Iterator[str]:
        """Yield every interned path that has cached metadata.

        Mirrors grok ``indexed_files``.
        """
        for path_id in self.file_meta:
            path = self.get_str(path_id)
            if path is not None:
                yield path

    def rename_file(self, from_path: str, to_path: str) -> None:
        """Move ``from_path``'s index entries to ``to_path``.

        Mirrors grok ``rename_file``: uses the reverse indexes to enumerate
        only this file's symbols (O(symbols in file)), rewrites the path
        component of each location in place, re-keys the graph / meta /
        reverse-index entries under the new path id, and drops the old path
        id. A path that was never indexed is a no-op.
        """
        from_id = self.get_id(from_path)
        if from_id is None:
            return
        to_id = self.intern(to_path)

        def _rewrite(
            forward: dict[StringId, list[tuple[StringId, int]]],
            reverse_map: dict[StringId, set[StringId]],
        ) -> None:
            symbol_ids = reverse_map.pop(from_id, None)
            if symbol_ids is None:
                return
            for symbol_id in symbol_ids:
                locs = forward.get(symbol_id)
                if locs is None:
                    continue
                # Rewrite the path component of each matching location in place.
                locs[:] = [
                    (to_id, ln) if p == from_id else (p, ln) for (p, ln) in locs
                ]
            reverse_map.setdefault(to_id, set()).update(symbol_ids)

        _rewrite(self.definitions, self.file_to_defs)
        _rewrite(self.references, self.file_to_refs)

        graph = self.graphs.pop(from_id, None)
        if graph is not None:
            self.graphs[to_id] = graph
        meta = self.file_meta.pop(from_id, None)
        if meta is not None:
            self.file_meta[to_id] = meta

    def is_indexed(self, path: str) -> bool:
        """True if ``path`` has a cached :class:`FileMeta` entry.

        Mirrors grok ``is_indexed``.
        """
        path_id = self.get_id(path)
        return path_id is not None and path_id in self.file_meta

    def get_graph(self, path: str) -> ScopeGraph | None:
        """Return the cached :class:`ScopeGraph` for ``path``, or ``None``.

        Mirrors grok ``get_graph``.
        """
        path_id = self.get_id(path)
        if path_id is None:
            return None
        return self.graphs.get(path_id)

    def file_count(self) -> int:
        """Number of indexed files (those with cached metadata).

        Mirrors grok ``file_count``.
        """
        return len(self.file_meta)

    # === Query ==============================================================

    def find_definitions(self, symbol: str) -> list[tuple[str, int]]:
        """All ``(path, line)`` definitions of ``symbol`` (alias-resolved).

        Mirrors grok ``find_definitions``: returns the symbol's own
        definitions followed by its alias target's definitions (if the symbol
        is a known alias). Locations are returned with their interned path
        string; ids whose path can no longer resolve are skipped.
        """
        results: list[tuple[str, int]] = []
        symbol_id = self.get_id(symbol)
        if symbol_id is None:
            return results
        for source_id in self._definition_sources(symbol_id):
            for path_id, line in self.definitions.get(source_id, ()):
                path = self.get_str(path_id)
                if path is not None:
                    results.append((path, line))
        return results

    def find_references(self, symbol: str) -> list[tuple[str, int]]:
        """All ``(path, line)`` references to ``symbol`` (alias-resolved).

        Mirrors grok ``find_references``: returns references to the symbol
        itself, then to its alias target, then to every alias that points at
        it (reverse aliases).
        """
        results: list[tuple[str, int]] = []
        symbol_id = self.get_id(symbol)
        if symbol_id is None:
            return results
        for source_id in self._reference_sources(symbol_id):
            for path_id, line in self.references.get(source_id, ()):
                path = self.get_str(path_id)
                if path is not None:
                    results.append((path, line))
        return results

    def find_references_with_names(
        self, symbol: str
    ) -> list[tuple[str, str, int]]:
        """All ``(matched_name, path, line)`` references to ``symbol``.

        Mirrors grok ``find_references_with_names``: like
        :meth:`find_references` but carries the matched symbol name per
        location (the symbol itself, its alias target, or each reverse
        alias), so callers can show which spelling was used at each site.
        """
        results: list[tuple[str, str, int]] = []
        symbol_id = self.get_id(symbol)
        if symbol_id is None:
            return results
        for source_id in self._reference_sources(symbol_id):
            name = self.get_str(source_id)
            if name is None:
                continue
            for path_id, line in self.references.get(source_id, ()):
                path = self.get_str(path_id)
                if path is not None:
                    results.append((name, path, line))
        return results

    def find_definitions_smart(
        self,
        symbol: str,
        context_file: str | None,
        language_registry: LanguageRegistry | None,
    ) -> list[tuple[str, int]]:
        """Definitions of ``symbol`` ranked toward ``context_file``'s language.

        Mirrors grok ``find_definitions_smart``: de-duplicates the
        alias-resolved definitions, then (when a context file is given) sorts
        same-language-family entries before the rest, breaking ties
        lexicographically by path.
        """
        deduped = self._dedupe_locations(self.find_definitions(symbol))
        if context_file is not None:
            ctx_ext = _ext_of(context_file)
            deduped.sort(
                key=lambda pl: (
                    not self._extensions_same_language(
                        _ext_of(pl[0]), ctx_ext, language_registry
                    ),
                    pl[0],
                )
            )
        return deduped

    def find_references_smart(
        self,
        symbol: str,
        context_file: str | None,
        language_registry: LanguageRegistry | None,
    ) -> list[tuple[str, str, int]]:
        """References to ``symbol`` ranked toward ``context_file``'s language.

        Mirrors grok ``find_references_smart``: sorts the name-tagged
        reference list by same-language-family first, then by path. No
        de-duplication (a given ``(name, path, line)`` already appears at
        most once).
        """
        results = self.find_references_with_names(symbol)
        if context_file is not None:
            ctx_ext = _ext_of(context_file)
            results.sort(
                key=lambda npl: (
                    not self._extensions_same_language(
                        _ext_of(npl[1]), ctx_ext, language_registry
                    ),
                    npl[1],
                )
            )
        return results

    @staticmethod
    def _extensions_same_language(
        ext1: str,
        ext2: str,
        language_registry: LanguageRegistry | None,
    ) -> bool:
        """True if ``ext1`` and ``ext2`` name the same language family.

        Mirrors grok ``extensions_same_language``: identical extensions are
        trivially same; otherwise the registry decides. A ``None`` registry
        (no language context) falls back to exact match only.
        """
        if ext1 == ext2:
            return True
        if language_registry is None:
            return False
        return language_registry.extensions_same_language(ext1, ext2)

    def find_definitions_by_extension(
        self, symbol: str, extensions: set[str]
    ) -> list[tuple[str, int]]:
        """Definitions of ``symbol`` whose path's extension is in ``extensions``.

        Mirrors grok ``find_definitions_by_extension``. ``extensions`` may be
        any container supporting ``in`` (a ``set`` is the natural choice).
        """
        return [
            (path, line)
            for path, line in self.find_definitions(symbol)
            if _ext_of(path) in extensions
        ]

    def find_references_by_extension(
        self, symbol: str, extensions: set[str]
    ) -> list[tuple[str, str, int]]:
        """References to ``symbol`` whose path's extension is in ``extensions``.

        Mirrors grok ``find_references_by_extension``.
        """
        return [
            (name, path, line)
            for name, path, line in self.find_references_with_names(symbol)
            if _ext_of(path) in extensions
        ]

    # === Statistics =========================================================

    def stats(self) -> tuple[int, int, int]:
        """Return ``(file_count, definition_count, reference_count)``.

        Mirrors grok ``stats``.
        """
        return (
            len(self.file_meta),
            sum(len(locs) for locs in self.definitions.values()),
            sum(len(locs) for locs in self.references.values()),
        )

    def alias_count(self) -> int:
        """Number of recorded aliases.

        Mirrors grok ``alias_count``.
        """
        return len(self.aliases)

    def top_referenced_symbols(self, limit: int) -> list[tuple[str, int]]:
        """The ``limit`` most-referenced symbols as ``(name, ref_count)``.

        Mirrors grok ``top_referenced_symbols``: sorted by reference count
        descending. Ties keep insertion order (Python's stable sort over dict
        insertion order; grok leaves same-count order unspecified, so this is
        a benign, deterministic refinement).
        """
        counts = [
            (name, len(locs))
            for symbol_id, locs in self.references.items()
            if (name := self.get_str(symbol_id)) is not None
        ]
        counts.sort(key=lambda nc: nc[1], reverse=True)
        return counts[:limit]

    def set_query_version(self, version: int) -> None:
        """Stamp the index with the tree-sitter query version ``version``.

        Mirrors grok ``set_query_version``: records a ``Version(version)``
        stamp so future :meth:`needs_query_rebuild` checks can detect a query
        change.
        """
        self.query_version = QueryVersion(version=version)

    def needs_query_rebuild(self, current_version: int) -> bool:
        """True if the index was built under a different query version.

        Mirrors grok ``needs_query_rebuild``: a Legacy (unstamped) index
        always needs a rebuild; a stamped one needs a rebuild iff its stamp
        differs from ``current_version``.
        """
        return self.query_version.needs_rebuild(current_version)

    # === Maintenance ========================================================

    def compact(self) -> None:
        """Release unused capacity across the index.

        Mirrors grok ``compact``: grok calls ``Vec::shrink_to_fit`` on every
        container plus ``interner.shrink_to_fit``. Python ``list`` / ``dict``
        / ``set`` do not retain ``Vec``-style 2x excess capacity, so the only
        shrink hook is :meth:`StringInterner.shrink_to_fit` (itself a no-op in
        Python). The call is kept for API parity; the method is idempotent.
        """
        self.interner.shrink_to_fit()

    # === Internal helpers (collapse grok alias-walk duplication) ============

    def _definition_sources(self, symbol_id: StringId) -> tuple[StringId, ...]:
        """Ids whose definitions count as ``symbol_id``'s: itself + alias target.

        Mirrors the alias-resolution prefix shared by grok ``find_definitions``
        and ``find_definitions_smart``.
        """
        original_id = self.aliases.get(symbol_id)
        if original_id is None:
            return (symbol_id,)
        return (symbol_id, original_id)

    def _reference_sources(self, symbol_id: StringId) -> tuple[StringId, ...]:
        """Ids whose references count as ``symbol_id``'s.

        Mirrors the alias-resolution prefix shared by grok ``find_references``
        / ``find_references_with_names`` / ``find_references_smart``: the
        symbol itself, its alias target (if it is an alias), and every alias
        that points back at it (reverse aliases). Order is de-duplicated while
        preserving the grok walk order.
        """
        sources: list[StringId] = [symbol_id]
        original_id = self.aliases.get(symbol_id)
        if original_id is not None:
            sources.append(original_id)
        sources.extend(self.reverse_aliases.get(symbol_id, ()))
        return tuple(dict.fromkeys(sources))

    @staticmethod
    def _dedupe_locations(
        locs: list[tuple[str, int]]
    ) -> list[tuple[str, int]]:
        """De-duplicate ``(path, line)`` locations, preserving first-seen order.

        Mirrors grok ``find_definitions_smart``'s ``HashSet``-backed
        de-duplication pass (the symbol's own defs and its alias target's defs
        may overlap).
        """
        seen: set[tuple[str, int]] = set()
        out: list[tuple[str, int]] = []
        for loc in locs:
            if loc not in seen:
                seen.add(loc)
                out.append(loc)
        return out


__all__ = ["ScopeGraphIndex"]
