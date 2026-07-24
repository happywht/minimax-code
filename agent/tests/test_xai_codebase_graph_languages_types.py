"""Tests for ``xai_codebase_graph.languages.types`` (direction (2) brick 4).

Mirrors grok ``languages/types.rs``: ``TSLanguageConfig`` construction
(iterable -> immutable tuple coercion), the five pure-data accessors,
``symbol_id_of`` lookup (pure logic, consumes R301 ``SymbolId``), and the
deferred tree-sitter binding (``language`` / ``compile_query`` raise
``RuntimeError`` when no grammar is bound). Plus Pythonic surface coverage
(frozen+slots immutability, ``GrammarFn`` alias, ``languages/`` + crate-root
barrel contracts).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import minimax_code.xai_codebase_graph as xcg
from minimax_code.xai_codebase_graph import TSLanguageConfig
from minimax_code.xai_codebase_graph.languages import (
    TSLanguageConfig as TSLanguageConfigFromSubpkg,
)
from minimax_code.xai_codebase_graph.languages import (
    types as types_leaf,
)
from minimax_code.xai_codebase_graph.scope_graph import SymbolId


def _sample_config(grammar=None) -> TSLanguageConfig:
    """Build a minimal config mirroring grok ``python_lang``'s data shape."""
    return TSLanguageConfig.new(
        language_ids=["Python", "python", "py"],
        file_extensions=["py"],
        namespaces=[["function", "class", "variable", "module"]],
        file_definition_queries="(function_definition name: ...) @definition",
        grammar=grammar,
    )


# === construction =========================================================


def test_new_coerces_iterables_to_tuples() -> None:
    """grok ``TSLanguageConfig::new`` -> iterables stored as immutable tuples."""
    cfg = _sample_config()
    assert cfg.language_ids() == ("Python", "python", "py")
    assert cfg.file_extensions() == ("py",)
    assert cfg.namespaces() == (("function", "class", "variable", "module"),)
    assert cfg.file_definition_queries() == "(function_definition name: ...) @definition"


def test_file_definition_queries_returns_str() -> None:
    """The query string is surfaced verbatim."""
    cfg = TSLanguageConfig.new(["x"], ["x"], [[]], "(definition) @def")
    assert cfg.file_definition_queries() == "(definition) @def"


# === accessors ============================================================


def test_primary_language_id_returns_first() -> None:
    """grok ``primary_language_id`` -> first id."""
    assert _sample_config().primary_language_id() == "Python"


def test_primary_language_id_unknown_when_empty() -> None:
    """Empty ``language_ids`` -> ``"unknown"`` (grok ``unwrap_or``)."""
    cfg = TSLanguageConfig.new([], [], [], "query")
    assert cfg.primary_language_id() == "unknown"


# === symbol_id_of (pure logic, consumes R301 SymbolId) ====================


def test_symbol_id_of_finds_name() -> None:
    """grok ``symbol_id_of`` -> ``SymbolId(ns_idx, sym_idx)`` on first match."""
    cfg = _sample_config()
    # namespaces[0] = ["function", "class", "variable", "module"]
    assert cfg.symbol_id_of("function") == SymbolId.new(0, 0)
    assert cfg.symbol_id_of("class") == SymbolId.new(0, 1)
    assert cfg.symbol_id_of("module") == SymbolId.new(0, 3)


def test_symbol_id_of_across_namespaces() -> None:
    """Lookup scans all namespace rows in order (nested namespaces)."""
    cfg = TSLanguageConfig.new(
        ["rust"],
        ["rs"],
        [["function", "struct"], ["trait", "impl"]],
        "query",
    )
    assert cfg.symbol_id_of("function") == SymbolId.new(0, 0)
    assert cfg.symbol_id_of("struct") == SymbolId.new(0, 1)
    assert cfg.symbol_id_of("trait") == SymbolId.new(1, 0)
    assert cfg.symbol_id_of("impl") == SymbolId.new(1, 1)


def test_symbol_id_of_first_match_wins() -> None:
    """Duplicate names collapse to the first ``(ns, sym)`` (grok order)."""
    cfg = TSLanguageConfig.new(["x"], ["x"], [["dup"], ["dup"]], "q")
    assert cfg.symbol_id_of("dup") == SymbolId.new(0, 0)


def test_symbol_id_of_returns_none_when_missing() -> None:
    """Unknown symbol-type name -> ``None`` (grok ``Option::None``)."""
    cfg = _sample_config()
    assert cfg.symbol_id_of("nonexistent") is None


def test_symbol_id_of_returns_none_for_empty_namespaces() -> None:
    """Empty namespaces -> ``None`` (loop body never runs)."""
    cfg = TSLanguageConfig.new(["x"], ["x"], [], "query")
    assert cfg.symbol_id_of("function") is None


# === deferred tree-sitter binding =========================================


def test_language_raises_when_no_grammar() -> None:
    """``language()`` raises RuntimeError if no grammar is bound."""
    cfg = _sample_config(grammar=None)
    with pytest.raises(RuntimeError, match="tree-sitter grammar"):
        cfg.language()


def test_compile_query_raises_when_no_grammar() -> None:
    """``compile_query()`` raises RuntimeError if no grammar is bound.

    Matches grok's ``Query::new(&self.language(), ...)`` evaluation order --
    ``language()`` is evaluated first, so the grammar-unbound error surfaces
    before any tree-sitter import.
    """
    cfg = _sample_config(grammar=None)
    with pytest.raises(RuntimeError, match="tree-sitter grammar"):
        cfg.compile_query()


def test_language_invokes_bound_grammar() -> None:
    """``language()`` calls the bound grammar factory and returns its result."""
    sentinel = object()
    cfg = _sample_config(grammar=lambda: sentinel)
    assert cfg.language() is sentinel


# === immutability =========================================================


def test_config_is_frozen() -> None:
    """grok struct -> frozen dataclass: field reassignment is rejected."""
    cfg = _sample_config()
    with pytest.raises(FrozenInstanceError):
        cfg._language_ids = ("changed",)  # type: ignore[misc]


# === barrel contracts =====================================================


def test_grammar_fn_alias_lives_in_types_leaf() -> None:
    """``GrammarFn`` is a public ``pub type`` in ``types.py`` (Callable[[], Any]).

    Matches grok ``types.rs`` L4 ``pub type GrammarFn``; grok ``mod.rs`` does
    NOT re-export it, so it stays out of the ``languages`` barrel.
    """
    assert hasattr(types_leaf, "GrammarFn")
    # Usable as a type annotation for a grammar factory.

    def _lang_factory() -> object:
        return "lang"

    annotated: types_leaf.GrammarFn = _lang_factory
    assert annotated() == "lang"


def test_languages_barrel_exports_tslanguage_config() -> None:
    """``languages/__init__`` ``__all__`` = TSLanguageConfig (grok mod.rs L13-18)."""
    from minimax_code.xai_codebase_graph import languages

    assert set(languages.__all__) == {"TSLanguageConfig"}
    assert languages.TSLanguageConfig is types_leaf.TSLanguageConfig
    assert TSLanguageConfigFromSubpkg is TSLanguageConfig


def test_crate_root_barrel_exports_tslanguage_config() -> None:
    """grok ``lib.rs`` L88 re-exports TSLanguageConfig at the crate root."""
    assert "TSLanguageConfig" in xcg.__all__
    assert xcg.TSLanguageConfig is TSLanguageConfig
