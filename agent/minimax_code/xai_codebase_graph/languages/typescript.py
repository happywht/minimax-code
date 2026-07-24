"""TypeScript / TSX language configuration (direction (2) brick 5).

Ported from grok ``xai-codebase-graph/src/languages/ts.rs``. :func:`ts_lang`
builds the
:class:`~minimax_code.xai_codebase_graph.languages.types.TSLanguageConfig` for
TypeScript: ids ``["Typescript", "TSX", "typescript", "tsx"]``, extensions
``["ts", "tsx"]``, an eight-symbol namespace (``function`` / ``class`` /
``interface`` / ``type`` / ``enum`` / ``variable`` / ``const`` / ``let``), and
the comprehensive tree-sitter definitions query covering signatures,
declarations, destructuring patterns, JSX, imports / exports, and member /
call / type references.

File name note: grok's source leaf is ``ts.rs`` (factory ``ts_lang``); the
Python port names the module ``typescript.py`` for clarity (avoiding
confusion with ``types.py``) while keeping the factory name ``ts_lang`` so
grok ``mod.rs`` L17 ``pub use ts::ts_lang`` maps verbatim.

Migration decision -- the grammar closure
(``|| tree_sitter_typescript::LANGUAGE_TSX.into()`` in grok) is deferred:
``grammar`` defaults to ``None`` (R303 discipline); wire a real factory from
the caller that owns parsing (R305+). The query text is a semantic port of
grok's (token sequence identical; inter-token whitespace normalised --
tree-sitter ignores it).
"""

from __future__ import annotations

from minimax_code.xai_codebase_graph.languages.types import (
    GrammarFn,
    TSLanguageConfig,
)

#: Tree-sitter definitions query for TypeScript (semantic port of grok ts.rs).
_TS_QUERY = r"""
          ;; === DEFINITIONS ===

          (function_signature
            name: (identifier) @name.definition.function) @definition.function

          (method_signature
            name: (property_identifier) @name.definition.method) @definition.method

          (abstract_method_signature
            name: (property_identifier) @name.definition.method) @definition.method

          (abstract_class_declaration
            name: (type_identifier) @name.definition.class) @definition.class

          (module
            name: (identifier) @name.definition.module) @definition.module

          (interface_declaration
            name: (type_identifier) @name.definition.interface) @definition.interface

          (function_declaration
            name: (identifier) @name.definition.function) @definition.function

          (method_definition
            name: (property_identifier) @name.definition.method) @definition.method

          (class_declaration
            name: (type_identifier) @name.definition.class) @definition.class

          (type_alias_declaration
            name: (type_identifier) @name.definition.type) @definition.type

          (enum_declaration
            name: (identifier) @name.definition.enum) @definition.enum

          ;; Arrow function assigned to variable: const foo = () => {}
          (lexical_declaration
            (variable_declarator
              name: (identifier) @name.definition.function
              value: (arrow_function))) @definition.function

          ;; React component patterns: const Foo = React.forwardRef(...), React.memo(...)
          (lexical_declaration
            (variable_declarator
              name: (identifier) @name.definition.function
              value: (call_expression))) @definition.function

          ;; Variable declarations (const/let)
          (lexical_declaration
            (variable_declarator
              name: (identifier) @name.definition.variable)) @definition.variable

          ;; Var declarations
          (variable_declaration
            (variable_declarator
              name: (identifier) @name.definition.variable)) @definition.variable

          ;; Exported variable declarations: export const foo = ...
          (export_statement
            (lexical_declaration
              (variable_declarator
                name: (identifier) @name.definition.variable))) @definition.variable

          ;; === DESTRUCTURING DEFINITIONS ===

          ;; For-of/for-in loop with array destructuring: for (const [a, b] of items)
          (for_in_statement
            left: (array_pattern
              (identifier) @name.definition.variable))

          ;; For-of/for-in loop with object destructuring: for (const { a, b } of items)
          (for_in_statement
            left: (object_pattern
              (shorthand_property_identifier_pattern) @name.definition.variable))

          ;; For-of/for-in loop with object destructuring (aliased): for (const { a: b } of items)
          (for_in_statement
            left: (object_pattern
              (pair_pattern
                value: (identifier) @name.definition.variable)))

          ;; Regular array destructuring: const [a, b] = someArray
          (lexical_declaration
            (variable_declarator
              name: (array_pattern
                (identifier) @name.definition.variable)))

          ;; Regular object destructuring (shorthand): const { a, b } = someObject
          (lexical_declaration
            (variable_declarator
              name: (object_pattern
                (shorthand_property_identifier_pattern) @name.definition.variable)))

          ;; Regular object destructuring (aliased): const { a: b } = someObject
          (lexical_declaration
            (variable_declarator
              name: (object_pattern
                (pair_pattern
                  value: (identifier) @name.definition.variable))))

          ;; Var array destructuring: var [a, b] = someArray
          (variable_declaration
            (variable_declarator
              name: (array_pattern
                (identifier) @name.definition.variable)))

          ;; Var object destructuring (shorthand): var { a, b } = someObject
          (variable_declaration
            (variable_declarator
              name: (object_pattern
                (shorthand_property_identifier_pattern) @name.definition.variable)))

          ;; Var object destructuring (aliased): var { a: b } = someObject
          (variable_declaration
            (variable_declarator
              name: (object_pattern
                (pair_pattern
                  value: (identifier) @name.definition.variable))))

          ;; Function parameters with array destructuring: function foo([a, b]) {}
          (formal_parameters
            (required_parameter
              pattern: (array_pattern
                (identifier) @name.definition.variable)))

          ;; Function parameters with object destructuring (shorthand): function foo({ a, b }) {}
          (formal_parameters
            (required_parameter
              pattern: (object_pattern
                (shorthand_property_identifier_pattern) @name.definition.variable)))

          ;; Function parameters with object destructuring (aliased): function foo({ a: b }) {}
          (formal_parameters
            (required_parameter
              pattern: (object_pattern
                (pair_pattern
                  value: (identifier) @name.definition.variable))))

          ;; Function parameters (simple): function foo(a, b) {}
          (formal_parameters
            (required_parameter
              pattern: (identifier) @name.definition.variable))

          ;; === REFERENCES ===

          ;; Member expression object: foo.bar (capture foo as reference)
          (member_expression
            object: (identifier) @name.reference.variable)

          ;; Capture ALL type identifiers as references (comprehensive)
          (type_identifier) @name.reference.type

          ;; new expressions: new SomeClass()
          (new_expression
            constructor: (identifier) @name.reference.class) @reference.class

          ;; Named imports (simple): import { DiffViewer } from './code-viewer'
          (import_specifier
            name: (identifier) @name.reference.variable
            !alias) @reference.import

          ;; Named imports with alias: import { Foo as Bar } from './module'
          (import_specifier
            name: (identifier) @alias.original
            alias: (identifier) @alias.name) @reference.import.alias

          ;; Default imports: import Foo from 'bar'
          (import_clause
            (identifier) @name.reference.import)

          ;; JSX opening element: <DiffViewer ...>
          (jsx_opening_element
            name: (identifier) @name.reference.class)

          ;; JSX self-closing element: <DiffViewer ... />
          (jsx_self_closing_element
            name: (identifier) @name.reference.class)

          ;; JSX member expression element: <Foo.Bar />
          (jsx_opening_element
            name: (member_expression
              object: (identifier) @name.reference.variable))

          (jsx_self_closing_element
            name: (member_expression
              object: (identifier) @name.reference.variable))

          ;; Function calls: someFunction()
          (call_expression
            function: (identifier) @name.reference.call)

          ;; Method calls on objects: object.method()
          (call_expression
            function: (member_expression
              object: (identifier) @name.reference.variable))

          ;; Extends clause in class: class Foo extends Bar
          (class_heritage
            (extends_clause
              value: (identifier) @name.reference.class))

          ;; Implements clause: class Foo implements Bar
          (class_heritage
            (implements_clause
              (type_identifier) @name.reference.interface))

          ;; Named exports: export { Foo }
          (export_specifier
            name: (identifier) @name.reference.export)

          ;; Array element identifiers: [foo, bar] (e.g., React useCallback/useEffect dependency arrays)
          (array
            (identifier) @name.reference.variable)
        """


def ts_lang(grammar: GrammarFn | None = None) -> TSLanguageConfig:
    """Build the TypeScript tree-sitter config (grok ``ts_lang``).

    ``grammar`` defaults to ``None`` (deferred binding); pass a
    ``tree_sitter_typescript`` factory when wiring up real parsing.
    """
    return TSLanguageConfig.new(
        language_ids=["Typescript", "TSX", "typescript", "tsx"],
        file_extensions=["ts", "tsx"],
        namespaces=[
            ["function", "class", "interface", "type", "enum", "variable", "const", "let"]
        ],
        file_definition_queries=_TS_QUERY,
        grammar=grammar,
    )
