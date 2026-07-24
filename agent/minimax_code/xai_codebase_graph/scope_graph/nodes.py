"""Node types for the ScopeGraph.

Mirrors grok ``xai-codebase-graph/src/scope_graph/nodes.rs`` (direction (2),
brick 2). Defines the per-node value types a scope graph stores: symbols,
their opaque IDs, local scopes / defs / imports / references, and the
``NodeKind`` tagged union that the graph indexes.

YAGNI boundary (R301): grok derives ``Serialize`` / ``Deserialize`` on these
types for cache persistence. The Python port keeps this module stdlib-only
(no serde / pydantic); cache (de)serialization lands with the ``manager/``
migration. ``name(src)`` preserves grok's byte-slice contract (``src`` is the
source file as ``bytes``; tree-sitter works on byte offsets) -- callers
``.decode()`` for a ``str``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from minimax_code.xai_codebase_graph.types.range import Range


@dataclass(frozen=True, slots=True)
class Symbol:
    """A symbol extracted from the code (grok ``Symbol``, nodes.rs L10-L23).

    grok stores ``kind`` as ``Arc<str>`` to share the kind string cheaply
    across many symbols; Python ``str`` is already interned / shared by
    reference, so the field is a plain ``str`` (same Arc<str> -> str note as
    the R300 ``SymbolOccurrence`` / ``SymbolAlias`` port).
    """

    kind: str
    """The kind/type of symbol (e.g. ``"function"``, ``"class"``)."""

    range: Range
    """The range where the symbol appears."""

    @classmethod
    def new(cls, kind: str, range: Range) -> Symbol:
        """Create a new symbol (grok ``Symbol::new``)."""
        return cls(kind, range)


@dataclass(frozen=True, slots=True)
class SymbolId:
    """An opaque identifier for every symbol in a language (grok ``SymbolId``).

    Two-level index: ``(namespace_idx, symbol_idx)`` into the language's
    ``namespaces: Vec<Vec<String>>`` array. grok derives ``Copy + Eq + Hash``;
    the Python port is a frozen dataclass (value-equal + hashable; ``Copy`` is
    meaningless in Python).
    """

    namespace_idx: int
    """Index into the namespace array."""

    symbol_idx: int
    """Index within the namespace."""

    @classmethod
    def new(cls, namespace_idx: int, symbol_idx: int) -> SymbolId:
        """Create a new symbol ID (grok ``SymbolId::new``)."""
        return cls(namespace_idx, symbol_idx)

    def name(self, namespaces: Sequence[Sequence[str]]) -> str | None:
        """Look up this symbol's name in ``namespaces``.

        Mirrors grok ``SymbolId::name`` (nodes.rs L44-L49): returns ``None``
        if either index is out of bounds (grok's ``.get()`` -> ``Option``).
        """
        if 0 <= self.namespace_idx < len(namespaces):
            ns = namespaces[self.namespace_idx]
            if 0 <= self.symbol_idx < len(ns):
                return ns[self.symbol_idx]
        return None


@dataclass(frozen=True, slots=True)
class LocalScope:
    """A local scope in the source code (grok ``LocalScope``, nodes.rs L53-L64).

    grok derives ``Eq + Hash``; frozen dataclass (value-equal + hashable).
    """

    range: Range
    """The range of this scope."""

    @classmethod
    def new(cls, range: Range) -> LocalScope:
        """Create a new local scope (grok ``LocalScope::new``)."""
        return cls(range)


@dataclass(frozen=True, slots=True)
class LocalDef:
    """A local definition in the source code (grok ``LocalDef``, nodes.rs L67-L96).

    grok derives ``Eq + Hash``; frozen dataclass (value-equal + hashable).
    """

    range: Range
    """The range of the identifier being defined."""

    symbol_id: SymbolId | None
    """Optional symbol ID for type-aware resolution."""

    scope: LocalScope
    """The scope where this definition is visible."""

    @classmethod
    def new(
        cls, range: Range, symbol_id: SymbolId | None, scope: LocalScope
    ) -> LocalDef:
        """Create a new local definition (grok ``LocalDef::new``)."""
        return cls(range, symbol_id, scope)

    def name(self, src: bytes) -> bytes:
        """Slice the definition's identifier bytes from ``src``.

        Mirrors grok ``LocalDef::name`` (nodes.rs L88-L90): the slice spans
        ``[range.start_byte(), range.end_byte())``.
        """
        return src[self.range.start_byte() : self.range.end_byte()]

    def scope_range(self) -> Range:
        """The range of this definition's scope (grok ``LocalDef::scope_range``)."""
        return self.scope.range


@dataclass(frozen=True, slots=True)
class LocalImport:
    """A local import in the source code (grok ``LocalImport``, nodes.rs L99-L115).

    grok derives ``Eq + Hash``; frozen dataclass (value-equal + hashable).
    """

    range: Range
    """The range of the import identifier."""

    @classmethod
    def new(cls, range: Range) -> LocalImport:
        """Create a new local import (grok ``LocalImport::new``)."""
        return cls(range)

    def name(self, src: bytes) -> bytes:
        """Slice the import's identifier bytes from ``src`` (grok ``LocalImport::name``)."""
        return src[self.range.start_byte() : self.range.end_byte()]


@dataclass(frozen=True, slots=True)
class Reference:
    """A reference to a symbol in the source code (grok ``Reference``, nodes.rs L118-L136).

    grok derives only ``Debug + Clone + Serialize + Deserialize`` (no ``Eq`` /
    ``Hash``). The Python port stays frozen for immutability, which makes it
    incidentally hashable -- harmless, and useful for set-based dedup downstream.
    """

    range: Range
    """The range of the reference."""

    symbol_id: SymbolId | None
    """Optional symbol ID for type-aware resolution."""

    @classmethod
    def new(cls, range: Range, symbol_id: SymbolId | None) -> Reference:
        """Create a new reference (grok ``Reference::new``)."""
        return cls(range, symbol_id)

    def name(self, src: bytes) -> bytes:
        """Slice the reference's identifier bytes from ``src`` (grok ``Reference::name``)."""
        return src[self.range.start_byte() : self.range.end_byte()]


class NodeKindKind(Enum):
    """Discriminator for :class:`NodeKind`'s tagged-union port (no grok counterpart).

    grok ``NodeKind`` is a ``match``-able enum with four payload-carrying
    variants (``Scope(LocalScope)`` / ``Def(LocalDef)`` / ``Import(LocalImport)``
    / ``Ref(Reference)``). Python has no enum-with-payload, so the port
    collapses to a frozen dataclass holding a ``kind`` discriminator plus four
    optional payloads (see :class:`NodeKind`). Member names mirror grok's
    variant names verbatim; the values are kept identical for clarity.
    """

    SCOPE = "Scope"
    DEF = "Def"
    IMPORT = "Import"
    REF = "Ref"


@dataclass(frozen=True, slots=True)
class NodeKind:
    """The type of a node in the ScopeGraph (grok ``NodeKind``, nodes.rs L139-L180).

    Grok models this as a tagged enum -- ``Scope(LocalScope)`` /
    ``Def(LocalDef)`` / ``Import(LocalImport)`` / ``Ref(Reference)``. Python
    has no payload-carrying enum, so the port collapses the four variants
    into one frozen dataclass with a ``kind`` discriminator
    (:class:`NodeKindKind`) and four optional payload slots. Exactly one
    payload is set, matching the variant's kind (an invariant enforced by the
    constructors; a mismatch raises ``AssertionError`` from
    :meth:`range` / :meth:`identifier_range`). The same tagged-union-collapse
    pattern was used for R300's
    :class:`~minimax_code.xai_codebase_graph.types.FileEvent`.

    Payload field names dodge Python keywords: ``definition`` (``Def``) and
    ``import_`` (``Import``); ``scope`` holds the :class:`LocalScope` payload.
    """

    kind: NodeKindKind
    scope: LocalScope | None = None
    definition: LocalDef | None = None
    import_: LocalImport | None = None
    reference: Reference | None = None

    @classmethod
    def scope_node(cls, range: Range) -> NodeKind:
        """Construct a scope node from a range (grok ``NodeKind::scope``).

        The other three variants are constructed via their full payloads
        (``NodeKind(kind=NodeKindKind.DEF, definition=local_def, ...)``) --
        matching grok's ``NodeKind::Def(local_def)`` enum construction. grok
        only exposes ``scope(range)`` as a public constructor; the other
        variants are built inline by ``graph.rs`` (which lands in a later
        slice), so no dedicated constructors are provided here yet.
        """
        return cls(kind=NodeKindKind.SCOPE, scope=LocalScope.new(range))

    def range(self) -> Range:
        """The range spanned by this node (grok ``NodeKind::range``).

        For definitions this returns the *scope* range (full context), not the
        identifier range -- see :meth:`identifier_range` for the latter.
        """
        match self.kind:
            case NodeKindKind.SCOPE:
                assert self.scope is not None, "SCOPE node missing scope payload"
                return self.scope.range
            case NodeKindKind.DEF:
                assert self.definition is not None, "DEF node missing definition payload"
                return self.definition.scope.range
            case NodeKindKind.REF:
                assert self.reference is not None, "REF node missing reference payload"
                return self.reference.range
            case NodeKindKind.IMPORT:
                assert self.import_ is not None, "IMPORT node missing import payload"
                return self.import_.range

    def identifier_range(self) -> Range:
        """The identifier range of this node (grok ``NodeKind::identifier_range``).

        Unlike :meth:`range`, this always returns the identifier's own range
        (for ``Def``, the defined identifier rather than its scope).
        """
        match self.kind:
            case NodeKindKind.SCOPE:
                assert self.scope is not None, "SCOPE node missing scope payload"
                return self.scope.range
            case NodeKindKind.DEF:
                assert self.definition is not None, "DEF node missing definition payload"
                return self.definition.range
            case NodeKindKind.REF:
                assert self.reference is not None, "REF node missing reference payload"
                return self.reference.range
            case NodeKindKind.IMPORT:
                assert self.import_ is not None, "IMPORT node missing import payload"
                return self.import_.range
