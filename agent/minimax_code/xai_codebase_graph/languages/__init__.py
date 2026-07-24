"""Language registry + per-language tree-sitter configs (direction (2) brick 4+).

Ported from grok ``xai-codebase-graph/src/languages/``. R303 lands the
per-language configuration type (``types.py`` -> :class:`TSLanguageConfig`).
``LanguageRegistry`` (grok ``languages/mod.rs``) follows in R304, and the five
language factories (``python`` / ``golang`` / ``javascript`` / ``rust`` /
``typescript``) land alongside or after the registry.

The subpackage barrel mirrors grok ``languages/mod.rs`` L13-L18, which
re-exports ``TSLanguageConfig`` plus the five ``*_lang`` factories.
``TSLanguageConfig`` is the only symbol landed so far; the factories and
``LanguageRegistry`` will extend ``__all__`` as their bricks ship. ``GrammarFn``
(the grammar-factory type alias) stays in ``types.py`` -- grok ``mod.rs`` does
not re-export it.
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.languages.types import TSLanguageConfig

__all__ = [
    "TSLanguageConfig",
]
