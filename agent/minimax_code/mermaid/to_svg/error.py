"""Mermaid-to-svg error taxonomy -- fusion of Grok Build's ``mermaid-to-svg/src/error.rs``.

Direction (1) leaf 3 (type-foundation pair with ``ast.rs``). Mirrors grok's
``thiserror::Error`` enum ``MermaidError`` (6 variants) as a Python exception
hierarchy: one base class :class:`MermaidError` + one subclass per variant.

Mapping rationale
-----------------

grok constructs ``MermaidError::ParseError { line, message }`` and dispatches
via ``match``; Python raises ``ParseError(line, message)`` and dispatches via
``except ParseError``. A single ``Exception`` with a ``kind`` discriminator
would lose Python's idiomatic per-variant catch, so each variant becomes its
own subclass. All subclasses inherit :class:`MermaidError`, so
``except MermaidError`` still catches the whole taxonomy (mirrors grok's
single-enum type).

Each subclass ``__init__`` stamps the structured fields (``line`` / ``message``
for :class:`ParseError`; the offending value for the tuple variants) and
forwards a pre-formatted message to ``Exception.__init__``. ``str(exc)``
therefore reproduces grok's ``#[error("...")]`` Display output verbatim:

* ``ParseError``              -> ``"Parse error at line {line}: {message}"``
* ``InvalidDirection``        -> ``"Invalid graph direction: {value}"``
* ``InvalidNodeShape``        -> ``"Invalid node shape: {value}"``
* ``DotGenerationError``      -> ``"DOT generation error: {value}"``
* ``RenderError``             -> ``"SVG rendering error: {value}"``
* ``UnsupportedDiagramType``  -> ``"Unsupported diagram type: {value}"``

Public surface (7 symbols): the base + 6 subclasses. grok's crate root
re-exports only the enum (``pub use error::MermaidError``); the ``to_svg``
barrel surfaces the subclasses too so consumers can ``except ParseError``
without a deep import -- a Pythonic adaptation (grok's
``MermaidError::ParseError`` path has no direct Python equivalent).
"""

from __future__ import annotations


class MermaidError(Exception):
    """Base for all mermaid-to-svg errors (mirrors grok's thiserror enum).

    All variant-specific errors inherit from this, so ``except MermaidError``
    catches the whole taxonomy just like grok's single enum type.
    """


class ParseError(MermaidError):
    """Raised when mermaid source fails to parse (grok ``ParseError``).

    Carries the 1-based source ``line`` and a human-readable ``message``.
    ``str(exc)`` reproduces grok's
    ``#[error("Parse error at line {line}: {message}")]``.
    """

    def __init__(self, line: int, message: str) -> None:
        self.line = line
        self.message = message
        super().__init__(f"Parse error at line {line}: {message}")


class InvalidDirection(MermaidError):
    """Raised when a flowchart direction token is unrecognized (grok ``InvalidDirection``)."""

    def __init__(self, direction: str) -> None:
        self.direction = direction
        super().__init__(f"Invalid graph direction: {direction}")


class InvalidNodeShape(MermaidError):
    """Raised when a node shape token is unrecognized (grok ``InvalidNodeShape``)."""

    def __init__(self, shape: str) -> None:
        self.shape = shape
        super().__init__(f"Invalid node shape: {shape}")


class DotGenerationError(MermaidError):
    """Raised when DOT (graphviz) generation fails (grok ``DotGenerationError``)."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"DOT generation error: {detail}")


class RenderError(MermaidError):
    """Raised when SVG rendering fails (grok ``RenderError``)."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"SVG rendering error: {detail}")


class UnsupportedDiagramType(MermaidError):
    """Raised when the diagram type token has no renderer (grok ``UnsupportedDiagramType``)."""

    def __init__(self, diagram_type: str) -> None:
        self.diagram_type = diagram_type
        super().__init__(f"Unsupported diagram type: {diagram_type}")


__all__ = [
    "DotGenerationError",
    "InvalidDirection",
    "InvalidNodeShape",
    "MermaidError",
    "ParseError",
    "RenderError",
    "UnsupportedDiagramType",
]
