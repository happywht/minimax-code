"""Go language configuration (direction (2) brick 5).

Ported from grok ``xai-codebase-graph/src/languages/golang.rs``. :func:`golang`
builds the
:class:`~minimax_code.xai_codebase_graph.languages.types.TSLanguageConfig` for
Go: ids ``["Go", "go"]``, the ``.go`` extension, a seven-symbol namespace
(``function`` / ``type`` / ``struct`` / ``interface`` / ``const`` / ``var`` /
``package``), and the tree-sitter definitions query covering function / method
declarations, type / const / var specs, plus call, type, package and import
references.

Migration decision -- the grammar closure (``|| tree_sitter_go::LANGUAGE.into()``
in grok) is deferred: ``grammar`` defaults to ``None`` (R303 discipline); wire
a real factory from the caller that owns parsing (R305+). The query text is a
semantic port of grok's (token sequence identical; inter-token whitespace
normalised -- tree-sitter ignores it).
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.languages.types import (
    GrammarFn,
    TSLanguageConfig,
)

#: Tree-sitter definitions query for Go (semantic port of grok golang.rs).
_GOLANG_QUERY = r"""
        ; Function definitions
        (function_declaration
            name: (identifier) @name.definition.function) @definition.function

        ; Method definitions
        (method_declaration
            name: (field_identifier) @name.definition.method) @definition.method

        ; Type definitions (struct, interface, etc.)
        (type_declaration
            (type_spec
                name: (type_identifier) @name.definition.type)) @definition.type

        ; Const declarations
        (const_declaration
            (const_spec
                name: (identifier) @name.definition.const)) @definition.const

        ; Var declarations
        (var_declaration
            (var_spec
                name: (identifier) @name.definition.var)) @definition.var

        ; ============ REFERENCES ============

        ; Function calls
        (call_expression
            function: (identifier) @name.reference.call) @reference.call

        ; Method calls
        (call_expression
            function: (selector_expression
                field: (field_identifier) @name.reference.call)) @reference.call

        ; Type references
        (type_identifier) @name.reference.type

        ; Package references in qualified names
        (qualified_type
            package: (package_identifier) @name.reference.package
            name: (type_identifier) @name.reference.type)

        ; ============ IMPORTS ============

        ; import "package"
        (import_spec
            path: (interpreted_string_literal) @name.reference.import)

        ; import alias "package"
        (import_spec
            name: (package_identifier) @alias.name
            path: (interpreted_string_literal) @alias.original)
        """


def golang(grammar: GrammarFn | None = None) -> TSLanguageConfig:
    """Build the Go tree-sitter config (grok ``golang``).

    ``grammar`` defaults to ``None`` (deferred binding); pass a
    ``tree_sitter_go`` factory when wiring up real parsing.
    """
    return TSLanguageConfig.new(
        language_ids=["Go", "go"],
        file_extensions=["go"],
        namespaces=[
            ["function", "type", "struct", "interface", "const", "var", "package"]
        ],
        file_definition_queries=_GOLANG_QUERY,
        grammar=grammar,
    )
