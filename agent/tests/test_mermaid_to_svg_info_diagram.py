"""Black-box tests for the ``info`` diagram renderer (R279) -- the behavioral-
equivalent port of grok's ``info_diagram.rs``.

Direction (1) brick 10 (R279). The ``info`` diagram is mermaid's version card
-- a static, fixed ``400 x 150`` SVG whose only content is the pinned mermaid
version string (``v11.12.2``). It is the simplest per-diagram renderer (no
parsing, no layout, no edge-routing), which makes it the natural first leaf
to wire into the dispatch (R279). This file exercises the renderer's contract
surface directly and through the dispatch, mirroring grok's own coverage:

* **Dispatch smoke** (grok ``lib.rs`` ``test_simple_info_diagram`` L608-L616):
  :func:`render_mermaid_to_svg` with the bare token ``"info"`` succeeds and the
  emitted SVG carries ``v11.12.2``. This is the single integration assertion
  grok makes for the ``info`` arm.
* **Light palette compaction** (grok ``info_diagram.rs`` L20-L29): the
  canonical light palette ``#ffffff`` / ``#333333`` renders as the CSS
  shorthand forms ``white`` / ``#333`` (mermaid's own ``info`` card compaction).
  Asserted via the exact substrings in the SVG ``style`` block.
* **Dark palette passthrough** (grok L20-L29): any non-canonical palette flows
  through verbatim -- dark's ``#1e1e1e`` / ``#ffffff`` / ``#888888`` appear
  unchanged (no shorthand compaction fires).
* **Edge-color asymmetry** (grok L37-L38): the edge color is used as-is in the
  ``.marker`` rule regardless of palette -- light's edge stays ``#333333``
  (NOT compacted to ``#333``), dark's stays ``#888888``. This is the one place
  grok diverges from the text/background compaction, and the port mirrors it.
* **Token guard** (grok L13-L18): the renderer is a public entry reachable
  directly (not only through dispatch), so a non-``info`` first token raises
  :class:`ParseError` with ``line == 1`` and the verbatim message
  ``"Expected 'info' declaration"``.
* **Module surface**: the three pinned constants (``INFO_WIDTH`` /
  ``INFO_HEIGHT`` / ``PINNED_MERMAID_VERSION``) and the single-symbol
  ``__all__``. The renderer stays out of the :mod:`.to_svg` barrel -- it is
  reached only through the dispatch in :mod:`.render` (mirrors grok's crate
  root never re-exporting ``info_diagram``'s symbols).
"""

from __future__ import annotations

import pytest

import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import MermaidTheme
from minimax_code.mermaid.to_svg import info_diagram as info_diagram_mod
from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.info_diagram import (
    INFO_HEIGHT,
    INFO_WIDTH,
    PINNED_MERMAID_VERSION,
    render_info_diagram_to_svg,
)
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg

# The version string grok pins (info_diagram.rs L7). Asserted as a substring of
# every emitted SVG -- the whole point of the ``info`` card is to show it.
_PINNED_VERSION = "11.12.2"


# === dispatch smoke (grok lib.rs test_simple_info_diagram L608-L616) =======


def test_render_mermaid_to_svg_info_dispatch_emits_version() -> None:
    """The bare ``info`` token dispatches to the version card (grok L608-L616).

    Mirrors grok's sole integration assertion for the ``info`` arm:
    :func:`render_mermaid_to_svg` with ``"info"`` and no theme succeeds and the
    SVG carries the pinned version string. The default (light) theme fires
    because no explicit theme and no front-matter theme are passed.
    """
    svg = render_mermaid_to_svg("info", None)
    assert isinstance(svg, str)
    assert _PINNED_VERSION in svg
    assert "<svg" in svg
    assert "</svg>" in svg


# === light palette compaction (grok info_diagram.rs L20-L29) ==============


def test_info_diagram_light_compacts_background_to_white() -> None:
    """Light's ``#ffffff`` background renders as the ``white`` shorthand (grok L20-L24)."""
    svg = render_info_diagram_to_svg("info", MermaidTheme.light())
    assert "background-color: white;" in svg
    # The un-compacted hex must NOT appear in the background slot.
    assert "background-color: #ffffff;" not in svg


def test_info_diagram_light_compacts_text_to_hash333() -> None:
    """Light's ``#333333`` text renders as the ``#333`` shorthand (grok L25-L29).

    The ``#my-svg`` rule's ``fill`` carries the compacted form.
    """
    svg = render_info_diagram_to_svg("info", MermaidTheme.light())
    assert "16px;fill:#333;}" in svg


def test_info_diagram_light_edge_is_not_compacted() -> None:
    """Light's edge stays ``#333333`` verbatim -- the asymmetry vs text (grok L37-L38).

    grok threads ``theme.edge_color`` into the ``.marker`` rule as-is; the
    ``#333333`` -> ``#333`` compaction that fires for the text color does NOT
    fire here. This is the one place the renderer diverges from uniform
    compaction, and the port mirrors it exactly.
    """
    svg = render_info_diagram_to_svg("info", MermaidTheme.light())
    assert "#my-svg .marker{fill:#333333;stroke:#333333;}" in svg


def test_info_diagram_light_carries_canvas_and_version() -> None:
    """The light SVG carries the fixed viewBox and the pinned version text node."""
    svg = render_info_diagram_to_svg("info", MermaidTheme.light())
    assert 'viewBox="0 0 400 150"' in svg
    assert f">v{_PINNED_VERSION}</text>" in svg


# === dark palette passthrough (grok info_diagram.rs L20-L29) ==============


def test_info_diagram_dark_background_passes_through_verbatim() -> None:
    """Dark's ``#1e1e1e`` background is not the canonical ``#ffffff``, so no compaction."""
    svg = render_info_diagram_to_svg("info", MermaidTheme.dark())
    assert "background-color: #1e1e1e;" in svg


def test_info_diagram_dark_text_passes_through_verbatim() -> None:
    """Dark's ``#ffffff`` text is not the canonical ``#333333``, so no compaction."""
    svg = render_info_diagram_to_svg("info", MermaidTheme.dark())
    assert "16px;fill:#ffffff;}" in svg


def test_info_diagram_dark_edge_passes_through_verbatim() -> None:
    """Dark's ``#888888`` edge threads into the ``.marker`` rule as-is (grok L37-L38)."""
    svg = render_info_diagram_to_svg("info", MermaidTheme.dark())
    assert "#my-svg .marker{fill:#888888;stroke:#888888;}" in svg


def test_info_diagram_dark_carries_canvas_and_version() -> None:
    """The dark SVG carries the same fixed viewBox and pinned version text node."""
    svg = render_info_diagram_to_svg("info", MermaidTheme.dark())
    assert 'viewBox="0 0 400 150"' in svg
    assert f">v{_PINNED_VERSION}</text>" in svg


# === token guard (grok info_diagram.rs L13-L18) ==========================


def test_info_diagram_non_info_token_raises_parse_error() -> None:
    """A first token other than ``info`` raises :class:`ParseError` (grok L13-L18).

    The renderer is a public entry reachable directly, so the guard defends
    against misuse (not only the dispatch path). A ``flowchart`` body is the
    natural wrong-token fixture. The error carries ``line == 1`` and grok's
    verbatim message.
    """
    with pytest.raises(ParseError) as exc_info:
        render_info_diagram_to_svg("flowchart LR\n  A --> B", MermaidTheme.light())
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Expected 'info' declaration"
    assert str(exc_info.value) == "Parse error at line 1: Expected 'info' declaration"


def test_info_diagram_empty_source_raises_parse_error() -> None:
    """An empty source has no first token -> ``None`` != ``"info"`` -> ParseError."""
    with pytest.raises(ParseError) as exc_info:
        render_info_diagram_to_svg("", MermaidTheme.light())
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Expected 'info' declaration"


# === module surface (constants + __all__) ================================


def test_info_width_constant_matches_grok() -> None:
    """``INFO_WIDTH`` mirrors grok ``INFO_WIDTH: f64 = 400.0`` (carried as ``int``)."""
    assert INFO_WIDTH == 400
    assert isinstance(INFO_WIDTH, int)


def test_info_height_constant_matches_grok() -> None:
    """``INFO_HEIGHT`` mirrors grok ``INFO_HEIGHT: f64 = 150.0`` (carried as ``int``)."""
    assert INFO_HEIGHT == 150
    assert isinstance(INFO_HEIGHT, int)


def test_pinned_mermaid_version_constant_matches_grok() -> None:
    """``PINNED_MERMAID_VERSION`` mirrors grok ``PINNED_MERMAID_VERSION: &str = "11.12.2"``."""
    assert PINNED_MERMAID_VERSION == _PINNED_VERSION


def test_info_diagram_module_all_is_single_public_symbol() -> None:
    """The leaf exports exactly its one public symbol (grok ``pub fn``).

    The token helper and the constants stay module-private; grok's crate root
    never re-exports them. ``render_info_diagram_to_svg`` is the sole entry,
    reached through the dispatch in :mod:`.render`.
    """
    assert info_diagram_mod.__all__ == ["render_info_diagram_to_svg"]


def test_info_diagram_symbol_is_not_in_to_svg_barrel() -> None:
    """The renderer stays out of the barrel -- dispatch-only reach (grok crate root).

    grok's ``lib.rs`` never re-exports ``info_diagram``'s symbols at the crate
    root; the renderer is invoked only via the ``info`` dispatch arm. The port
    mirrors that: the barrel ``__all__`` (18 symbols after R277) does not grow
    for R279.
    """
    assert "render_info_diagram_to_svg" not in to_svg.__all__
    assert not hasattr(to_svg, "render_info_diagram_to_svg")
