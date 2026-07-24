"""Location-based navigation APIs for go-to-definition / go-to-references.

Functional port (not line-by-line) of grok ``xai-codebase-graph/src/navigation.rs``
(direction (2), brick 8). Wraps a :class:`ScopeGraphIndex` and answers "where
is this symbol defined / used?" from a file path + 1-indexed ``(row, col)``.

DRY note: grok **duplicates** ``find_smallest_named_node_at_point`` /
``is_identifier_like`` between ``navigation.rs`` (L346-L394) and
``index_manager.rs`` (L1610-L1654). The Python port keeps a single source --
the module-level helpers in
:mod:`minimax_code.xai_codebase_graph.index_manager` (R306g) -- and this module
imports them rather than re-defining a second copy. The parser/query resolver
(:func:`minimax_code.xai_codebase_graph.manager.builder._get_parser_and_query`,
R305e) is likewise reused. This is the only deliberate divergence from grok's
source layout; it is a correction of an upstream DRY violation, not a
behavior change.

``Location`` here is the navigation-flavored location (``path`` / ``line`` /
optional ``symbol``), **distinct** from
:class:`minimax_code.xai_codebase_graph.types.Location` (``file_path`` /
``line`` / ``column`` / ``range``). grok ships both under separate module
paths and this port keeps them 1:1 (no unification) -- see the docstring in
:mod:`minimax_code.xai_codebase_graph.types.location`. The crate-root barrel
reconciliation (which ``Location`` the crate root re-exports) lands in a
follow-up brick (R308).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from minimax_code.xai_codebase_graph.index_manager import (
    _find_smallest_named_node_at_position,
)
from minimax_code.xai_codebase_graph.languages import LanguageRegistry
from minimax_code.xai_codebase_graph.manager.builder import _get_parser_and_query
from minimax_code.xai_codebase_graph.scope_graph import ScopeGraphIndex

# A path argument accepted by every Navigator method. grok takes ``&Path``;
# Python accepts ``str`` / ``os.PathLike`` and normalizes to ``str`` (the form
# ``ScopeGraphIndex.find_*_smart`` consumes as ``context_file``).
_PathArg = "str | os.PathLike[str]"


# === Location (navigation flavor) =======================================


@dataclass(frozen=True, slots=True)
class Location:
    """A location in a file (navigation flavor).

    The lightweight ``(path, line, optional symbol)`` triple that
    go-to-definition / go-to-references returns. Distinct from
    :class:`minimax_code.xai_codebase_graph.types.Location` (which carries a
    full ``Range`` + column) -- grok models the two as separate types under
    separate module paths and this port preserves that split.

    Mirrors grok ``navigation::Location`` (navigation.rs L22-L53): frozen +
    hashable (grok ``#[derive(PartialEq, Eq)]``); ``symbol`` is optional
    (``None`` for plain definition lookups, ``Some(name)`` for per-reference
    alias tagging).
    """

    path: str
    line: int
    symbol: str | None = None

    @classmethod
    def new(cls, path: str, line: int) -> Location:
        """Construct a location without a symbol tag (grok ``Location::new``)."""
        return cls(path=path, line=line, symbol=None)

    @classmethod
    def with_symbol(cls, path: str, line: int, symbol: str) -> Location:
        """Construct a location carrying its matched symbol name (grok ``with_symbol``).

        Useful for alias resolution: a reference's location carries the symbol
        spelling actually seen at that site, which may differ from the query
        symbol (an alias).
        """
        return cls(path=path, line=line, symbol=symbol)

    def as_path(self) -> Path:
        """The path as a :class:`~pathlib.Path` reference (grok ``as_path``)."""
        return Path(self.path)


# === NavigationResult ===================================================


@dataclass(frozen=True, slots=True)
class NavigationResult:
    """Result of a navigation operation (grok ``NavigationResult``).

    Carries the resolved symbol name plus the list of locations where it is
    defined / referenced. Mirrors navigation.rs L13-L19. Frozen (grok derives
    ``Debug, Clone``); the ``locations`` list is mutable in place but cannot
    be re-bound (matching grok's ``Vec`` semantics on a non-``Copy`` struct).
    Not hashable (grok does not derive ``Hash`` -- a ``Vec`` field is not
    hashable in Rust either).
    """

    symbol: str
    locations: list[Location]


# === NavigationError ====================================================


class NavigationError(Exception):
    """Base class for navigation failures (grok ``NavigationError`` enum).

    grok models this as a 6-variant enum (``FileNotFound`` /
    ``PositionOutOfBounds`` / ``NoSymbolAtPosition`` / ``UnsupportedLanguage``
    / ``ParseError`` / ``IoError``) with a ``Display`` impl. The Python port
    models each variant as a subclass so callers can ``except`` the specific
    failure mode; each subclass formats the same message grok's ``Display``
    produces. ``From<io::Error>`` (grok's automatic conversion) has no direct
    Python equivalent -- read failures explicitly raise :class:`FileNotFound`
    (mirroring grok's ``get_symbol_at_position`` read site) and other
    :class:`OSError` carriers raise :class:`IoError`.
    """


class FileNotFound(NavigationError):
    """File not found or could not be read (grok ``FileNotFound(PathBuf)``)."""

    def __init__(self, path: _PathArg) -> None:
        self.path = str(path)
        super().__init__(f"File not found: {self.path}")


class PositionOutOfBounds(NavigationError):
    """Position is out of bounds for the file (grok ``PositionOutOfBounds``).

    Raised when ``row == 0`` or ``col == 0`` (the 1-indexed sentinel for "no
    cursor"). Distinct from :class:`NoSymbolAtPosition`: a zero coordinate is
    an invalid query, not a valid position that happens to sit on a
    non-symbol node.
    """

    def __init__(self, row: int, col: int) -> None:
        self.row = row
        self.col = col
        super().__init__(f"Position out of bounds: {row}:{col}")


class NoSymbolAtPosition(NavigationError):
    """No symbol found at the given position (grok ``NoSymbolAtPosition``).

    The cursor sits on a non-identifier node (a keyword, bracket, or
    whitespace); the hit-test returned ``None``.
    """

    def __init__(self, row: int, col: int) -> None:
        self.row = row
        self.col = col
        super().__init__(f"No symbol found at position {row}:{col}")


class UnsupportedLanguage(NavigationError):
    """Language not supported for this file type (grok ``UnsupportedLanguage(String)``)."""

    def __init__(self, ext: str) -> None:
        self.ext = ext
        super().__init__(f"Unsupported language: {ext}")


class ParseError(NavigationError):
    """Parse error (grok ``ParseError(String)``).

    Covers: tree-sitter parser unavailable (grammar not loaded), parse yielded
    no tree, or the matched byte span is not valid UTF-8.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"Parse error: {message}")


class IoError(NavigationError):
    """IO error (grok ``IoError(std::io::Error)``).

    The carrier for grok's ``From<io::Error>`` conversion. Read failures at
    the ``get_symbol_at_position`` site map to :class:`FileNotFound` (matching
    grok); :class:`IoError` covers any other OS-level IO failure a caller may
    wrap via ``from``.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"IO error: {message}")


# === Navigator ==========================================================


def _context_file_str(context_file: _PathArg | None) -> str | None:
    """Normalize a ``context_file`` argument to ``str | None`` (grok ``Option<&Path>``)."""
    if context_file is None:
        return None
    return str(context_file)


@dataclass(slots=True)
class Navigator:
    """Location-based code navigator (grok ``Navigator``).

    Wraps a shared :class:`ScopeGraphIndex` + :class:`LanguageRegistry` and
    answers go-to-definition / go-to-references from a file path + 1-indexed
    ``(row, col)``. Mirrors navigation.rs L101-L344.

    grok stores ``index: Arc<ScopeGraphIndex>`` (shared ownership, refcounted
    across clones); Python holds a direct reference -- :py:mod:`gc` refcounting
    replaces ``Arc``, so there is no explicit ``Arc`` wrapper. The
    ``index_mut`` accessor documents the copy-on-write divergence below.
    """

    _index: ScopeGraphIndex
    _registry: LanguageRegistry = field(default_factory=LanguageRegistry)

    @property
    def index(self) -> ScopeGraphIndex:
        """The underlying index (grok ``index() -> &ScopeGraphIndex``)."""
        return self._index

    @property
    def index_mut(self) -> ScopeGraphIndex:
        """Mutable index access (grok ``index_mut() -> &mut ScopeGraphIndex``).

        grok uses ``Arc::make_mut`` copy-on-write: if other ``Arc`` clones of
        the index exist, the index is cloned before yielding the mutable
        reference, so the caller's mutations never leak to other holders.
        Python holds a direct reference and mutates in place -- there is no
        CoW layer, so mutations **are** visible to every holder of the same
        index object. Callers that need isolation must snapshot the index
        before mutating (Pythonic explicit copy, vs. grok's implicit CoW).
        """
        return self._index

    # -- position-based lookups -------------------------------------------

    def get_symbol_at_position(self, file_path: _PathArg, row: int, col: int) -> str:
        """Symbol name under ``(row, col)`` in ``file_path`` (grok L153-L203).

        ``row`` / ``col`` are **1-indexed** (the LSP convention); the cursor
        hit-test receives ``row - 1`` / ``col - 1`` (0-indexed, tree-sitter's
        coordinate system). ``row == 0`` or ``col == 0`` is the "no cursor"
        sentinel and raises :class:`PositionOutOfBounds` (grok guards the same).

        Failure modes (each its own :class:`NavigationError` subclass, matching
        grok's enum variants):

        * :class:`PositionOutOfBounds` -- ``row == 0`` or ``col == 0``.
        * :class:`FileNotFound` -- read failure (deleted between request/parse).
        * :class:`UnsupportedLanguage` -- no language config for the extension.
        * :class:`ParseError` -- parser unavailable, parse yielded no tree, or
          the symbol's byte span is not valid UTF-8.
        * :class:`NoSymbolAtPosition` -- the cursor sits on a non-identifier
          node (keyword / bracket / whitespace).

        Reuses :func:`_get_parser_and_query` (R305e) for the cached parser +
        :func:`_find_smallest_named_node_at_position` (R306g) for the hit-test
        -- the same single source :class:`IndexManager` uses, eliminating
        grok's duplicate definitions.
        """
        if row == 0 or col == 0:
            raise PositionOutOfBounds(row, col)

        try:
            content = Path(os.fspath(file_path)).read_bytes()
        except OSError:
            raise FileNotFound(file_path) from None

        lang_config = self._registry.for_file_path(file_path)
        if lang_config is None:
            ext = Path(os.fspath(file_path)).suffix.lstrip(".") or "unknown"
            raise UnsupportedLanguage(ext)

        parser, _query = _get_parser_and_query(lang_config)
        if parser is None:
            raise ParseError("tree-sitter parser unavailable")

        tree = parser.parse(content)
        if tree is None:
            raise ParseError("tree-sitter parse yielded no tree")

        node = _find_smallest_named_node_at_position(tree.root_node, row - 1, col - 1)
        if node is None:
            raise NoSymbolAtPosition(row, col)

        try:
            return content[node.start_byte:node.end_byte].decode("utf-8")
        except UnicodeDecodeError:
            raise ParseError("symbol byte span is not valid UTF-8") from None

    def goto_definition(
        self, file_path: _PathArg, row: int, col: int
    ) -> NavigationResult:
        """Go to definition for the symbol at ``(row, col)`` (grok L214-L233).

        Resolves the symbol at the position, then looks up its definition
        locations via :meth:`ScopeGraphIndex.find_definitions_smart` (ranked
        toward ``file_path``'s language family). Raises any
        :class:`NavigationError` from :meth:`get_symbol_at_position`.
        """
        symbol = self.get_symbol_at_position(file_path, row, col)
        defs = self._index.find_definitions_smart(
            symbol, str(os.fspath(file_path)), self._registry
        )
        locations = [Location.new(path, line) for path, line in defs]
        return NavigationResult(symbol=symbol, locations=locations)

    def goto_references(
        self,
        file_path: _PathArg,
        row: int,
        col: int,
        include_definition: bool,
    ) -> NavigationResult:
        """Go to references for the symbol at ``(row, col)`` (grok L247-L284).

        Resolves the symbol, then finds all references (with alias resolution)
        via :meth:`ScopeGraphIndex.find_references_smart`. When
        ``include_definition`` is set, definition locations are prepended
        (de-duplicated against existing reference sites by ``path`` + ``line``)
        -- mirroring grok's ``insert(0, loc)`` prepend + O(n) dedup.
        """
        symbol = self.get_symbol_at_position(file_path, row, col)
        refs = self._index.find_references_smart(
            symbol, str(os.fspath(file_path)), self._registry
        )
        locations = [
            Location.with_symbol(path, line, sym) for sym, path, line in refs
        ]

        if include_definition:
            defs = self._index.find_definitions_smart(
                symbol, str(os.fspath(file_path)), self._registry
            )
            for path, line in defs:
                loc = Location.new(path, line)
                if not any(
                    existing.path == loc.path and existing.line == loc.line
                    for existing in locations
                ):
                    locations.insert(0, loc)

        return NavigationResult(symbol=symbol, locations=locations)

    # -- name-based lookups (no position parse) ---------------------------

    def goto_definition_by_name(
        self,
        symbol: str,
        context_file: _PathArg | None = None,
    ) -> NavigationResult:
        """Go to definition by symbol name directly (grok L287-L305).

        Skips the position parse -- the caller already knows the symbol name.
        Never raises: returns an empty ``locations`` list when nothing matches
        (grok returns ``NavigationResult`` directly, not ``Result``).
        """
        defs = self._index.find_definitions_smart(
            symbol, _context_file_str(context_file), self._registry
        )
        locations = [Location.new(path, line) for path, line in defs]
        return NavigationResult(symbol=symbol, locations=locations)

    def goto_references_by_name(
        self,
        symbol: str,
        context_file: _PathArg | None = None,
        include_definition: bool = False,
    ) -> NavigationResult:
        """Go to references by symbol name directly (grok L308-L343).

        Skips the position parse. When ``include_definition`` is set,
        definitions are prepended (de-duplicated by ``path`` + ``line``). Never
        raises (mirrors grok's non-``Result`` return type).
        """
        refs = self._index.find_references_smart(
            symbol, _context_file_str(context_file), self._registry
        )
        locations = [
            Location.with_symbol(path, line, sym) for sym, path, line in refs
        ]

        if include_definition:
            defs = self._index.find_definitions_smart(
                symbol, _context_file_str(context_file), self._registry
            )
            for path, line in defs:
                loc = Location.new(path, line)
                if not any(
                    existing.path == loc.path and existing.line == loc.line
                    for existing in locations
                ):
                    locations.insert(0, loc)

        return NavigationResult(symbol=symbol, locations=locations)


__all__ = [
    "Location",
    "NavigationResult",
    "NavigationError",
    # grok models these as enum variants of ``NavigationError``; the Python
    # port exposes each variant as a subclass so callers can ``except`` the
    # specific failure mode. grok ``lib.rs`` L94 re-exports only the base
    # ``NavigationError`` at the crate root (the 6 subclasses stay
    # leaf-module-only, mirroring the ``CacheError`` / ``IndexBuildError``
    # pattern); the crate-root barrel reconciliation lands in R308.
    "FileNotFound",
    "PositionOutOfBounds",
    "NoSymbolAtPosition",
    "UnsupportedLanguage",
    "ParseError",
    "IoError",
    "Navigator",
]
