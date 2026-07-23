"""Black-box tests for the migrated mermaid-to-svg error taxonomy (R271).

Exercises :mod:`minimax_code.mermaid.to_svg.error` -- the :class:`MermaidError`
exception hierarchy fused from grok's ``mermaid-to-svg/src/error.rs``
``thiserror::Error`` enum (direction (1), leaf 3, paired with ``ast.rs``).

This is a **different layer** from the R38 ``xai-grok-mermaid`` host error
type (:class:`~minimax_code.mermaid.types.MermaidError`, a coarse single
class): the render stack's taxonomy is a base + 6 variant subclasses, one per
grok enum variant, each reproducing grok's ``#[error("...")]`` Display string
in ``__str__``. Covers:

* each subclass constructs, stamps its structured fields, and renders the
  grok Display template verbatim -- ``ParseError`` (struct variant) carries
  ``line`` + ``message``; the five tuple variants carry their offending
  value under a named attribute (``direction`` / ``shape`` / ``detail`` /
  ``diagram_type``),
* the hierarchy: every subclass instance is an instance of
  :class:`MermaidError` (so ``except MermaidError`` catches the whole
  taxonomy, mirroring grok's single enum type), and ``except`` clauses catch
  variant-specifically (Python's idiomatic equivalent of grok's ``match``),
* the barrel surface contract: the ``to_svg`` sub-package re-exports the
  base + 6 subclasses (7 error symbols); the R38 ``mermaid`` root barrel is
  untouched (``__all__`` stays at 17, ``to_svg`` not promoted).
"""

from __future__ import annotations

import pytest

import minimax_code.mermaid as mermaid
import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import (
    DotGenerationError,
    InvalidDirection,
    InvalidNodeShape,
    MermaidError,
    ParseError,
    RenderError,
    UnsupportedDiagramType,
)

# === ParseError: struct variant (line + message) ===========================


def test_parse_error_stamps_fields_and_renders_template() -> None:
    """``ParseError`` carries ``line`` + ``message``; ``str`` mirrors grok Display.

    Reproduces ``#[error("Parse error at line {line}: {message}")]`` verbatim.
    """
    err = ParseError(line=42, message="unexpected '}'")
    assert err.line == 42
    assert err.message == "unexpected '}'"
    assert str(err) == "Parse error at line 42: unexpected '}'"


# === Tuple variants: offending value under a named attribute ===============


def test_invalid_direction_stamps_field_and_renders_template() -> None:
    """``InvalidDirection`` carries ``direction``; ``str`` mirrors grok Display."""
    err = InvalidDirection("sideways")
    assert err.direction == "sideways"
    assert str(err) == "Invalid graph direction: sideways"


def test_invalid_node_shape_stamps_field_and_renders_template() -> None:
    """``InvalidNodeShape`` carries ``shape``; ``str`` mirrors grok Display."""
    err = InvalidNodeShape("octagon")
    assert err.shape == "octagon"
    assert str(err) == "Invalid node shape: octagon"


def test_dot_generation_error_stamps_field_and_renders_template() -> None:
    """``DotGenerationError`` carries ``detail``; ``str`` mirrors grok Display."""
    err = DotGenerationError("node id collision")
    assert err.detail == "node id collision"
    assert str(err) == "DOT generation error: node id collision"


def test_render_error_stamps_field_and_renders_template() -> None:
    """``RenderError`` carries ``detail``; ``str`` mirrors grok Display."""
    err = RenderError("font not found")
    assert err.detail == "font not found"
    assert str(err) == "SVG rendering error: font not found"


def test_unsupported_diagram_type_stamps_field_and_renders_template() -> None:
    """``UnsupportedDiagramType`` carries ``diagram_type``; ``str`` mirrors grok."""
    err = UnsupportedDiagramType("pizzaDiagram")
    assert err.diagram_type == "pizzaDiagram"
    assert str(err) == "Unsupported diagram type: pizzaDiagram"


# === Hierarchy: base catch + variant-specific catch ========================


@pytest.mark.parametrize(
    "err",
    [
        ParseError(1, "boom"),
        InvalidDirection("x"),
        InvalidNodeShape("x"),
        DotGenerationError("x"),
        RenderError("x"),
        UnsupportedDiagramType("x"),
    ],
)
def test_every_variant_is_a_mermaid_error(err: MermaidError) -> None:
    """Every subclass instance is an instance of :class:`MermaidError`.

    Mirrors grok's single enum type: ``except MermaidError`` catches the
    whole taxonomy. (Transitively also a plain ``Exception``.)
    """
    assert isinstance(err, MermaidError)
    assert isinstance(err, Exception)


def test_except_base_catches_any_variant() -> None:
    """``except MermaidError`` catches every variant (taxonomy-wide catch)."""

    def raise_one() -> None:
        raise RenderError("nope")

    with pytest.raises(MermaidError):
        raise_one()


def test_except_specific_variant_catches_its_own() -> None:
    """``except ParseError`` catches a raised ``ParseError``."""
    with pytest.raises(ParseError):
        raise ParseError(7, "bad")


def test_except_specific_variant_does_not_catch_sibling() -> None:
    """``except ParseError`` does NOT catch a sibling ``RenderError``.

    Variant-specific ``except`` is Python's idiomatic equivalent of grok's
    ``match err { MermaidError::ParseError { .. } => ... }`` -- a sibling
    arm must fall through.
    """
    with pytest.raises(RenderError):
        try:
            raise RenderError("nope")
        except ParseError:  # noqa: BLE001 -- intentional fall-through probe
            pytest.fail("ParseError caught a RenderError")


# === barrel surface contract ==============================================


def test_to_svg_barrel_includes_error_symbols() -> None:
    """The ``to_svg`` barrel re-exports the base + 6 subclasses (7 symbols).

    R269/R270/R271 progressively grow the barrel; this test locks only the
    error-symbols subset (not the full barrel list, which is owned by the
    latest leaf) so the error layer stays valid as the barrel grows.
    """
    for name in (
        "MermaidError",
        "ParseError",
        "InvalidDirection",
        "InvalidNodeShape",
        "DotGenerationError",
        "RenderError",
        "UnsupportedDiagramType",
    ):
        assert name in to_svg.__all__, name
    # The barrel-level symbols are the exact same objects as the deep imports.
    assert to_svg.MermaidError is MermaidError
    assert to_svg.ParseError is ParseError
    assert to_svg.UnsupportedDiagramType is UnsupportedDiagramType


def test_mermaid_root_barrel_unchanged_by_error_leaf() -> None:
    """R271 grows the ``to_svg`` sub-package; the R38 root surface stays at 17."""
    assert len(mermaid.__all__) == 17
    assert "to_svg" not in mermaid.__all__
