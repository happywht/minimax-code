"""JavaScript / JSX language configuration (direction (2) brick 5).

Ported from grok ``xai-codebase-graph/src/languages/javascript.rs``.
:func:`js_lang` builds the
:class:`~minimax_code.xai_codebase_graph.languages.types.TSLanguageConfig` for
JavaScript: ids ``["JavaScript", "javascript", "js", "jsx"]``, extensions
``["js", "jsx"]``, a five-symbol namespace (``function`` / ``class`` /
``variable`` / ``const`` / ``let``), and the tree-sitter definitions query
covering class / function / arrow / method / variable declarations plus call,
member, JSX, import and export references.

Migration decision -- the grammar closure
(``|| tree_sitter_javascript::LANGUAGE.into()`` in grok) is deferred:
``grammar`` defaults to ``None`` (R303 discipline); wire a real factory from
the caller that owns parsing (R305+). The query text is a semantic port of
grok's (token sequence identical; inter-token whitespace normalised).
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.languages.types import (
    GrammarFn,
    TSLanguageConfig,
)

#: Tree-sitter definitions query for JavaScript (semantic port of grok javascript.rs).
_JS_QUERY = r"""
        ; Class definitions
        (class_declaration
            name: (identifier) @name.definition.class) @definition.class

        ; Function definitions
        (function_declaration
            name: (identifier) @name.definition.function) @definition.function

        ; Arrow function with variable
        (lexical_declaration
            (variable_declarator
                name: (identifier) @name.definition.function
                value: (arrow_function))) @definition.function

        ; Method definitions
        (method_definition
            name: (property_identifier) @name.definition.method) @definition.method

        ; Variable declarations
        (lexical_declaration
            (variable_declarator
                name: (identifier) @name.definition.variable)) @definition.variable

        ; Var declarations
        (variable_declaration
            (variable_declarator
                name: (identifier) @name.definition.variable)) @definition.variable

        ; ============ REFERENCES ============

        ; Function calls
        (call_expression
            function: (identifier) @name.reference.call) @reference.call

        ; Method calls
        (call_expression
            function: (member_expression
                property: (property_identifier) @name.reference.call)) @reference.call

        ; JSX element names
        (jsx_opening_element
            name: (identifier) @name.reference.jsx)

        (jsx_self_closing_element
            name: (identifier) @name.reference.jsx)

        ; ============ IMPORTS ============

        ; Named imports: import { Foo } from 'bar'
        (import_specifier
            name: (identifier) @name.reference.import)

        ; Default import: import Foo from 'bar'
        (import_clause
            (identifier) @name.reference.import)

        ; Import alias: import { Foo as Bar } from 'bar'
        (import_specifier
            name: (identifier) @alias.original
            alias: (identifier) @alias.name)

        ; Named exports: export { Foo }
        (export_specifier
            name: (identifier) @name.reference.export)

        ; Array element identifiers: [foo, bar] (e.g., React useCallback/useEffect dependency arrays)
        (array
            (identifier) @name.reference.variable)
        """


def js_lang(grammar: GrammarFn | None = None) -> TSLanguageConfig:
    """Build the JavaScript tree-sitter config (grok ``js_lang``).

    ``grammar`` defaults to ``None`` (deferred binding); pass a
    ``tree_sitter_javascript`` factory when wiring up real parsing.
    """
    return TSLanguageConfig.new(
        language_ids=["JavaScript", "javascript", "js", "jsx"],
        file_extensions=["js", "jsx"],
        namespaces=[["function", "class", "variable", "const", "let"]],
        file_definition_queries=_JS_QUERY,
        grammar=grammar,
    )
