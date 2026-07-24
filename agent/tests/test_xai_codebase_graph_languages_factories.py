"""Tests for ``xai_codebase_graph.languages`` per-language factories (R304).

Mirrors grok ``languages/{python,golang,javascript,rust,ts}.rs``: each
factory returns a :class:`TSLanguageConfig` whose pure-data contract (language
ids / extensions / namespaces / definitions query string) matches grok's data
values verbatim, with the tree-sitter grammar binding deferred (``grammar``
defaults to ``None``; ``language()`` raises ``RuntimeError`` until a grammar
is bound). Covers the five factories, the grok-named surface, the
``languages`` barrel re-export of all five factories, and the crate-root
``LanguageRegistry`` consumer.
"""

from __future__ import annotations

import pytest

import minimax_code.xai_codebase_graph.languages as languages_pkg
from minimax_code.xai_codebase_graph import LanguageRegistry, TSLanguageConfig
from minimax_code.xai_codebase_graph.languages import (
    golang,
    js_lang,
    python_lang,
    rust_lang,
    ts_lang,
)

# === python_lang ============================================================


def test_python_lang_returns_config_instance():
    """``python_lang`` builds a frozen :class:`TSLanguageConfig`."""
    config = python_lang()
    assert isinstance(config, TSLanguageConfig)


def test_python_lang_data_contract():
    """``python_lang`` mirrors grok python.rs ids / extensions / namespace."""
    config = python_lang()
    assert tuple(config.language_ids()) == ("Python", "python", "py")
    assert tuple(config.file_extensions()) == ("py",)
    assert config.namespaces() == (("function", "class", "variable", "module"),)
    assert config.primary_language_id() == "Python"


def test_python_lang_query_nonempty():
    """``python_lang`` ships a non-empty tree-sitter definitions query."""
    assert python_lang().file_definition_queries().strip()


# === golang =================================================================


def test_golang_returns_config_instance():
    """``golang`` builds a frozen :class:`TSLanguageConfig`."""
    assert isinstance(golang(), TSLanguageConfig)


def test_golang_data_contract():
    """``golang`` mirrors grok golang.rs (7-symbol namespace)."""
    config = golang()
    assert tuple(config.language_ids()) == ("Go", "go")
    assert tuple(config.file_extensions()) == ("go",)
    assert config.namespaces() == (
        ("function", "type", "struct", "interface", "const", "var", "package"),
    )
    assert config.primary_language_id() == "Go"
    assert config.file_definition_queries().strip()


# === js_lang ================================================================


def test_js_lang_returns_config_instance():
    """``js_lang`` builds a frozen :class:`TSLanguageConfig`."""
    assert isinstance(js_lang(), TSLanguageConfig)


def test_js_lang_data_contract():
    """``js_lang`` mirrors grok javascript.rs (ids incl. jsx, two extensions)."""
    config = js_lang()
    assert tuple(config.language_ids()) == ("JavaScript", "javascript", "js", "jsx")
    assert tuple(config.file_extensions()) == ("js", "jsx")
    assert config.namespaces() == (("function", "class", "variable", "const", "let"),)
    assert config.primary_language_id() == "JavaScript"
    assert config.file_definition_queries().strip()


# === rust_lang ==============================================================


def test_rust_lang_returns_config_instance():
    """``rust_lang`` builds a frozen :class:`TSLanguageConfig`."""
    assert isinstance(rust_lang(), TSLanguageConfig)


def test_rust_lang_data_contract():
    """``rust_lang`` mirrors grok rust.rs (13 tree-sitter symbol kinds)."""
    config = rust_lang()
    assert tuple(config.language_ids()) == ("Rust", "rust", "rs")
    assert tuple(config.file_extensions()) == ("rs",)
    assert config.namespaces() == (
        (
            "const",
            "function",
            "variable",
            "struct",
            "enum",
            "union",
            "typedef",
            "interface",
            "field",
            "enumerator",
            "module",
            "label",
            "lifetime",
        ),
    )
    assert config.primary_language_id() == "Rust"
    assert config.file_definition_queries().strip()


# === ts_lang ================================================================


def test_ts_lang_returns_config_instance():
    """``ts_lang`` builds a frozen :class:`TSLanguageConfig`."""
    assert isinstance(ts_lang(), TSLanguageConfig)


def test_ts_lang_data_contract():
    """``ts_lang`` mirrors grok ts.rs (note: capital-T ``Typescript``)."""
    config = ts_lang()
    assert tuple(config.language_ids()) == ("Typescript", "TSX", "typescript", "tsx")
    assert tuple(config.file_extensions()) == ("ts", "tsx")
    assert config.namespaces() == (
        ("function", "class", "interface", "type", "enum", "variable", "const", "let"),
    )
    assert config.primary_language_id() == "Typescript"
    assert config.file_definition_queries().strip()


# === grammar deferred binding ===============================================


@pytest.mark.parametrize(
    "factory",
    [python_lang, golang, js_lang, rust_lang, ts_lang],
)
def test_factory_default_grammar_is_none(factory):
    """Every factory defers the tree-sitter binding (``grammar=None``)."""
    config = factory()
    # language() must raise RuntimeError because no grammar is bound yet;
    # the runtime binding is owned by scope_graph/graph.py (R305+).
    with pytest.raises(RuntimeError):
        config.language()


def test_factory_accepts_explicit_grammar():
    """Factories forward an explicit ``grammar`` (deferred-binding seam)."""
    sentinel = object()

    def fake_grammar() -> object:
        return sentinel

    config = python_lang(grammar=fake_grammar)
    assert config.language() is sentinel  # bound factory is invoked


# === barrel surface =========================================================


def test_languages_barrel_reexports_factories():
    """``languages`` barrel re-exports all five factories (grok mod.rs L13-L18)."""
    assert languages_pkg.python_lang is python_lang
    assert languages_pkg.golang is golang
    assert languages_pkg.js_lang is js_lang
    assert languages_pkg.rust_lang is rust_lang
    assert languages_pkg.ts_lang is ts_lang


def test_languages_barrel_all_contains_factories():
    """``languages.__all__`` lists the registry + config + five factories."""
    assert set(languages_pkg.__all__) == {
        "LanguageRegistry",
        "TSLanguageConfig",
        "golang",
        "js_lang",
        "python_lang",
        "rust_lang",
        "ts_lang",
    }


def test_factories_consumed_by_registry():
    """The five factories back :class:`LanguageRegistry`'s built-in configs."""
    registry = LanguageRegistry()
    configs = registry.all_configs()
    assert len(configs) == 5
    # Insertion order (rust, ts, js, golang, python) -> primary ids.
    assert [c.primary_language_id() for c in configs] == [
        "Rust",
        "Typescript",
        "JavaScript",
        "Go",
        "Python",
    ]
