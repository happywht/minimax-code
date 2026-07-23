"""Mermaid-to-SVG render stack -- fusion of Grok Build's ``mermaid-to-svg``.

The layout + render stack that turns mermaid source into SVG. This is a
**different crate** from the R38 ``xai-grok-mermaid`` host (the engine
shell with its color primitive, coarse light/dark theme, render-parameter
model, and error taxonomy). The host picks a raster surface and an engine;
this stack is what the engine runs -- the front-matter config, the resolved
palette, the parser/AST, the dagre layout bridge, the text-wrap measurer,
and the SVG renderer.

This sub-package opens with direction (1) -- wiring the dagre layout ported
in R246--R268 so mermaid source actually renders. Each leaf migrates one
``mermaid-to-svg/src/*.rs`` file as a zero-semantic clone, mirroring the
dagre leaf-by-leaf migration discipline (barrel-coordinated, ASCII-sorted
``__all__``, one black-box test file per leaf, ruff + target pytest + full
regression).

Scope of this sub-package
-------------------------

* :mod:`.theme` (R269) -- :class:`MermaidTheme` (7 resolved colors + 5
  named-preset factories), :class:`MermaidThemePreset` (5-variant enum with
  ``parse`` / ``to_theme``), :class:`MermaidThemeVariables` (7-slot override
  bag with ``is_empty`` / ``apply_mermaid_alias`` / ``apply_to``). Pure data
  layer; zero non-stdlib dependency. The first render-stack leaf.

What is NOT here yet (later leaves): ``config.rs`` (front-matter YAML parser
that consumes ``MermaidThemePreset`` / ``MermaidThemeVariables`` to build a
``RenderConfig``), ``ast.rs`` / ``parser.rs`` (the flowchart AST + parser),
``layout.rs`` / ``text_wrap.rs`` / ``svg_renderer.rs`` (the dagre-backed
layout bridge, the text measurer, the SVG emitter), the ``mermaid_port/``
dagre adapters, and the per-diagram renderers.
"""

from __future__ import annotations

from .theme import MermaidTheme, MermaidThemePreset, MermaidThemeVariables

__all__ = [
    "MermaidTheme",
    "MermaidThemePreset",
    "MermaidThemeVariables",
]
