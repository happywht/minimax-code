"""Per-language tree-sitter configuration (direction (2) brick 4).

Ported from grok ``xai-codebase-graph/src/languages/types.rs``. Each
:class:`TSLanguageConfig` bundles the data a parser needs to index one
programming language: the canonical language ids, the file extensions it owns,
the symbol namespaces (used to resolve a symbol-type name to a
:class:`~minimax_code.xai_codebase_graph.scope_graph.nodes.SymbolId`), and the
tree-sitter *definitions* query string that extracts definitions from a parsed
file.

Migration decision -- tree-sitter is a hard external dependency in grok:
``GrammarFn = fn() -> tree_sitter::Language`` (``types.rs`` L4) plus a
per-language grammar crate such as ``tree_sitter_python::LANGUAGE``
(``languages/python.rs`` L36). Following the clone-by-function discipline, the
pure-data + pure-logic half of the contract (language ids / extensions /
namespaces / query string / accessors / ``symbol_id_of``) is migrated verbatim,
and the tree-sitter runtime binding is *deferred*: ``grammar`` becomes an
optional :data:`GrammarFn` that a caller binds only when the tree-sitter Python
package is available. :meth:`TSLanguageConfig.language` and
:meth:`TSLanguageConfig.compile_query` keep their grok signatures but raise a
``RuntimeError`` when no grammar is bound, instead of dragging tree-sitter into
the type layer. This keeps ``types.py`` stdlib-clean (mirrors R300-R302) while
preserving the full surface for ``scope_graph/graph.py`` (R305+) to wire up
later.

Naming note: grok stores the five fields private (``language_ids`` etc. are
``Vec<String>``) and exposes them via identically-named accessor methods
(``pub fn language_ids(&self) -> &[String]``). The Python port mirrors that
shape with underscore-prefixed private fields + grok-named accessor methods, so
downstream ports (``scope_graph/graph.py``, ``manager/``) can call
``config.language_ids()`` exactly as in grok -- the API surface is part of the
functional contract.

Public surface mirrors grok ``lib.rs`` L88
``pub use languages::{LanguageRegistry, TSLanguageConfig};`` -- ``TSLanguageConfig``
is re-exported at the crate root here; ``LanguageRegistry`` lands with R304
(the ``languages/mod.rs`` port).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from minimax_code.xai_codebase_graph.scope_graph.nodes import SymbolId

#: Type of a tree-sitter language-grammar factory (deferred runtime binding).
#:
#: Mirrors grok ``GrammarFn = fn() -> tree_sitter::Language``. ``Any`` stands
#: in for the tree-sitter ``Language`` object whose Python binding is optional
#: (bound lazily by the caller that wires up real parsing in
#: ``scope_graph/graph.py``). Kept public in ``types.py`` (matching grok's
#: ``pub type``); not lifted into the ``languages`` barrel (matching grok
#: ``mod.rs`` which does not re-export it).
GrammarFn = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class TSLanguageConfig:
    """Per-language tree-sitter configuration bundle.

    Wraps the five pieces of data a parser needs for one language plus the
    optional tree-sitter grammar factory. Construct via :meth:`new` (accepts
    iterables, coerces to immutable tuples); the five fields are stored
    underscore-prefixed and surfaced through grok-named accessors so the API
    surface matches grok ``TSLanguageConfig`` exactly.
    """

    _language_ids: tuple[str, ...]
    _file_extensions: tuple[str, ...]
    _namespaces: tuple[tuple[str, ...], ...]
    _file_definition_queries: str
    _grammar: GrammarFn | None = None

    @classmethod
    def new(
        cls,
        language_ids: Iterable[str],
        file_extensions: Iterable[str],
        namespaces: Iterable[Iterable[str]],
        file_definition_queries: str,
        grammar: GrammarFn | None = None,
    ) -> TSLanguageConfig:
        """Build a config from iterables, coercing to immutable tuples.

        Mirrors grok ``TSLanguageConfig::new``. ``grammar`` is optional
        (``None`` defers the tree-sitter binding); grok always passes a real
        ``GrammarFn`` because the grammar crates are compile-time deps.
        """
        return cls(
            _language_ids=tuple(language_ids),
            _file_extensions=tuple(file_extensions),
            _namespaces=tuple(tuple(ns) for ns in namespaces),
            _file_definition_queries=file_definition_queries,
            _grammar=grammar,
        )

    def language_ids(self) -> tuple[str, ...]:
        """Canonical language ids for this config.

        Mirrors grok ``TSLanguageConfig::language_ids(&self) -> &[String]``;
        returns an immutable tuple in place of the borrowed slice.
        """
        return self._language_ids

    def primary_language_id(self) -> str:
        """First language id, or ``"unknown"`` if there are none.

        Mirrors grok ``TSLanguageConfig::primary_language_id`` (``first()``
        then ``unwrap_or("unknown")``).
        """
        return self._language_ids[0] if self._language_ids else "unknown"

    def file_extensions(self) -> tuple[str, ...]:
        """File extensions owned by this language.

        Mirrors grok ``TSLanguageConfig::file_extensions(&self) -> &[String]``.
        """
        return self._file_extensions

    def namespaces(self) -> tuple[tuple[str, ...], ...]:
        """Symbol namespaces (rows of symbol-type names) for this language.

        Mirrors grok ``TSLanguageConfig::namespaces(&self) -> &[Vec<String>]``.
        Used by :meth:`symbol_id_of` to resolve a name to a
        :class:`SymbolId`.
        """
        return self._namespaces

    def file_definition_queries(self) -> str:
        """Tree-sitter definitions query string for this language.

        Mirrors grok ``TSLanguageConfig::file_definition_queries(&self) -> &str``.
        Compiled lazily by :meth:`compile_query` when a grammar is bound.
        """
        return self._file_definition_queries

    def language(self) -> Any:
        """Return the bound tree-sitter ``Language`` object.

        Mirrors grok ``TSLanguageConfig::language(&self) -> tree_sitter::Language``
        -- invokes the stored ``grammar`` factory. Raises ``RuntimeError`` if no
        grammar is bound (tree-sitter runtime not configured); bind one via
        :meth:`new` when the ``tree_sitter`` Python package is available.
        """
        grammar = self._grammar
        if grammar is None:
            raise RuntimeError(
                "TSLanguageConfig.language() requires a bound tree-sitter "
                "grammar; pass grammar=<factory> to TSLanguageConfig.new() "
                "after installing the tree_sitter Python package."
            )
        return grammar()

    def compile_query(self) -> Any:
        """Compile the definitions query against the bound tree-sitter language.

        Mirrors grok ``TSLanguageConfig::compile_query(&self)
        -> Result<tree_sitter::Query, tree_sitter::QueryError>``. Raises
        ``RuntimeError`` if no grammar is bound (propagated from
        :meth:`language`, matching grok's ``Query::new(&self.language(), ...)``
        evaluation order). When a grammar is bound, lazily imports
        :mod:`tree_sitter` and compiles the query string; an ``ImportError``
        surfaces naturally if the package is not installed.
        """
        language = self.language()  # RuntimeError if grammar unbound (grok order)
        import tree_sitter

        return tree_sitter.Query(language, self._file_definition_queries)

    def symbol_id_of(self, symbol_type: str) -> SymbolId | None:
        """Find the :class:`SymbolId` for a symbol-type name, or ``None``.

        Scans the namespace rows in order; on the first match returns
        ``SymbolId(namespace_index, symbol_index)``. Mirrors grok
        ``TSLanguageConfig::symbol_id_of(&self, &str) -> Option<SymbolId>`` --
        pure logic, no tree-sitter dependency.
        """
        for ns_idx, namespace in enumerate(self._namespaces):
            for sym_idx, sym in enumerate(namespace):
                if sym == symbol_type:
                    return SymbolId.new(ns_idx, sym_idx)
        return None
