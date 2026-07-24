"""Rust language configuration (direction (2) brick 5).

Ported from grok ``xai-codebase-graph/src/languages/rust.rs``. :func:`rust_lang`
builds the
:class:`~minimax_code.xai_codebase_graph.languages.types.TSLanguageConfig` for
Rust: ids ``["Rust", "rust", "rs"]``, extension ``"rs"``, a thirteen-symbol
namespace mirroring tree-sitter's Rust symbol kinds (``const`` / ``function``
/ ``variable`` / ``struct`` / ``enum`` / ``union`` / ``typedef`` / ``interface``
/ ``field`` / ``enumerator`` / ``module`` / ``label`` / ``lifetime``), and the
tree-sitter definitions query covering ADT / function / method / trait / module
/ macro / const definitions plus call, implementation, use/import, alias and
type references.

Migration decision -- the grammar closure (``|| tree_sitter_rust::LANGUAGE.into()``
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

#: Tree-sitter definitions query for Rust (semantic port of grok rust.rs).
_RUST_QUERY = r"""
        ; ADT definitions
        (struct_item
            name: (type_identifier) @name.definition.class) @definition.class

        (enum_item
            name: (type_identifier) @name.definition.class) @definition.class

        (union_item
            name: (type_identifier) @name.definition.class) @definition.class

        ; type aliases
        (type_item
            name: (type_identifier) @name.definition.class) @definition.class

        ; method definitions
        (declaration_list
            (function_item
                name: (identifier) @name.definition.method)) @definition.method

        ; function definitions
        (function_item
            name: (identifier) @name.definition.function) @definition.function

        ; trait definitions
        (trait_item
            name: (type_identifier) @name.definition.interface) @definition.interface

        ; module definitions
        (mod_item
            name: (identifier) @name.definition.module) @definition.module

        ; macro definitions
        (macro_definition
            name: (identifier) @name.definition.macro) @definition.macro

        ; const and static definitions
        (const_item
            name: (identifier) @name.definition.variable) @definition.variable

        (static_item
            name: (identifier) @name.definition.variable) @definition.variable

        ; ============ REFERENCES ============

        ; Function and method calls
        (call_expression
            function: (identifier) @name.reference.call) @reference.call

        (call_expression
            function: (field_expression
                field: (field_identifier) @name.reference.call)) @reference.call

        (macro_invocation
            macro: (identifier) @name.reference.call) @reference.call

        ; implementations
        (impl_item
            trait: (type_identifier) @name.reference.implementation) @reference.implementation

        (impl_item
            type: (type_identifier) @name.reference.implementation
            !trait) @reference.implementation

        ; ============ USE/IMPORT REFERENCES ============

        ; Simple use: use Foo;
        (use_declaration
            argument: (identifier) @name.reference.import) @reference.import

        ; Scoped use: use foo::Bar;
        (use_declaration
            argument: (scoped_identifier
                name: (identifier) @name.reference.import)) @reference.import

        ; Use list: use foo::{Bar, Baz};
        (use_declaration
            argument: (scoped_use_list
                list: (use_list
                    (identifier) @name.reference.import)))

        ; Nested scoped use: use foo::bar::{Baz, Qux};
        (use_declaration
            argument: (scoped_use_list
                list: (use_list
                    (scoped_identifier
                        name: (identifier) @name.reference.import))))

        ; Use with alias: use Foo as Bar;
        (use_declaration
            argument: (use_as_clause
                path: (identifier) @name.reference.import))

        (use_declaration
            argument: (use_as_clause
                path: (scoped_identifier
                    name: (identifier) @name.reference.import)))

        ; ============ ALIAS TRACKING ============
        ; These patterns capture alias relationships for unified lookups

        ; use Foo as Bar - captures original and alias
        (use_declaration
            argument: (use_as_clause
                path: (identifier) @alias.original
                alias: (identifier) @alias.name))

        ; use foo::Bar as Baz - scoped version
        (use_declaration
            argument: (use_as_clause
                path: (scoped_identifier
                    name: (identifier) @alias.original)
                alias: (identifier) @alias.name))

        ; ============ TYPE REFERENCES ============

        ; Type identifiers in function parameters
        (parameter
            type: (type_identifier) @name.reference.type)

        ; Return types
        (function_item
            return_type: (type_identifier) @name.reference.type)

        ; Struct fields
        (field_declaration
            type: (type_identifier) @name.reference.type)

        ; Let bindings with type annotation
        (let_declaration
            type: (type_identifier) @name.reference.type)

        ; Generic type arguments: Vec<Foo>
        (type_arguments
            (type_identifier) @name.reference.type)

        ; Scoped type identifier: foo::Bar
        (scoped_type_identifier
            name: (type_identifier) @name.reference.type)

        ; Reference types: &Foo
        (reference_type
            type: (type_identifier) @name.reference.type)

        ; Tuple struct patterns
        (tuple_struct_pattern
            type: (identifier) @name.reference.type)

        ; Struct expressions: Foo { ... }
        (struct_expression
            name: (type_identifier) @name.reference.type)

        ; Tuple struct expressions: Foo(...)
        (call_expression
            function: (scoped_identifier
                name: (identifier) @name.reference.call))

        ; Path segments in scoped identifiers: foo::Bar::baz()
        ; This captures types used in paths like SomeType::method()
        (scoped_identifier
            path: (scoped_identifier
                name: (identifier) @name.reference.type))

        ; Direct scoped calls with type in path: Foo::bar()
        (scoped_identifier
            path: (identifier) @name.reference.type
            name: (identifier))
        """


def rust_lang(grammar: GrammarFn | None = None) -> TSLanguageConfig:
    """Build the Rust tree-sitter config (grok ``rust_lang``).

    ``grammar`` defaults to ``None`` (deferred binding); pass a
    ``tree_sitter_rust`` factory when wiring up real parsing.
    """
    return TSLanguageConfig.new(
        language_ids=["Rust", "rust", "rs"],
        file_extensions=["rs"],
        namespaces=[
            [
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
            ]
        ],
        file_definition_queries=_RUST_QUERY,
        grammar=grammar,
    )
