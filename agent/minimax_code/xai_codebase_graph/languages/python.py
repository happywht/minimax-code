"""Python language configuration (direction (2) brick 5).

Ported from grok ``xai-codebase-graph/src/languages/python.rs``. :func:`python_lang`
builds the :class:`~minimax_code.xai_codebase_graph.languages.types.TSLanguageConfig`
for Python: the canonical language ids, the ``.py`` extension, the single
symbol namespace (``function`` / ``class`` / ``variable`` / ``module``), and
the tree-sitter *definitions* query that extracts class / function definitions
and call references.

Migration decision -- the grammar closure (``|| tree_sitter_python::LANGUAGE.into()``
in grok) is a compile-time dep. Following the clone-by-function discipline and
R303's deferred-binding decision, :func:`python_lang` accepts an optional
``grammar`` that defaults to ``None``: the query string + namespaces (the data
contract) ship verbatim, the tree-sitter runtime binding is wired by whichever
caller owns real parsing (R305+ ``scope_graph/graph.py``). The query text is a
semantic port of grok's (tree-sitter ignores inter-token whitespace, so blank
lines are normalised; the token sequence is identical).
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.languages.types import (
    GrammarFn,
    TSLanguageConfig,
)

#: Tree-sitter definitions query for Python (semantic port of grok python.rs).
_PYTHON_QUERY = r"""
        ; Class definitions
        (class_definition
            name: (identifier) @name.definition.class) @definition.class

        ; Function definitions
        (function_definition
            name: (identifier) @name.definition.function) @definition.function

        ; ============ REFERENCES ============

        ; Function calls (direct and method calls)
        (call
            function: [
                (identifier) @name.reference.call
                (attribute
                    attribute: (identifier) @name.reference.call)
            ]) @reference.call
        """


def python_lang(grammar: GrammarFn | None = None) -> TSLanguageConfig:
    """Build the Python tree-sitter config (grok ``python_lang``).

    Mirrors grok ``python_lang()``: ids ``["Python", "python", "py"]``,
    extension ``"py"``, namespace ``["function", "class", "variable",
    "module"]``, plus the definitions query. ``grammar`` defaults to ``None``
    (deferred binding); pass a ``tree_sitter_python`` factory when wiring up
    real parsing.
    """
    return TSLanguageConfig.new(
        language_ids=["Python", "python", "py"],
        file_extensions=["py"],
        namespaces=[["function", "class", "variable", "module"]],
        file_definition_queries=_PYTHON_QUERY,
        grammar=grammar,
    )
