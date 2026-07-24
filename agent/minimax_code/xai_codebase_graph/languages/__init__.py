"""Language registry + per-language tree-sitter configs (direction (2) brick 4+5).

Ported from grok ``xai-codebase-graph/src/languages/``. R303 landed the
per-language configuration type (``types.py`` ->
:class:`TSLanguageConfig`); R304 lands :class:`LanguageRegistry` (grok
``languages/mod.rs``) plus the five language factories (``python`` /
``golang`` / ``javascript`` / ``rust`` / ``typescript``).

The subpackage barrel mirrors grok ``languages/mod.rs`` L13-L18, which
re-exports ``TSLanguageConfig``, the five ``*_lang`` factories, and the
``LanguageRegistry``. ``GrammarFn`` (the grammar-factory type alias) stays in
``types.py`` -- grok ``mod.rs`` does not re-export it.

Import order matters: ``registry`` imports the five factories, so it is
imported last (after ``types`` and every factory) to keep the
``import languages`` path cycle-free.
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.languages.golang import golang
from minimax_code.xai_codebase_graph.languages.javascript import js_lang
from minimax_code.xai_codebase_graph.languages.python import python_lang
from minimax_code.xai_codebase_graph.languages.registry import LanguageRegistry
from minimax_code.xai_codebase_graph.languages.rust import rust_lang
from minimax_code.xai_codebase_graph.languages.types import TSLanguageConfig
from minimax_code.xai_codebase_graph.languages.typescript import ts_lang

__all__ = [
    "LanguageRegistry",
    "TSLanguageConfig",
    "golang",
    "js_lang",
    "python_lang",
    "rust_lang",
    "ts_lang",
]
