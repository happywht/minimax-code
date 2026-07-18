"""Mermaid rendering — fusion of Grok Build's ``xai-grok-mermaid``.

The guard and type layer for rendering mermaid diagram source to PNG. Where
MiniMax Code's front end renders model markdown (which frequently contains
mermaid diagram blocks), this package is the back-end vocabulary for a future
render-fidelity pass: the color primitive, the light/dark theme and surface
colors, the render-parameter model, the error taxonomy, the resource cap, and
the panic-isolating render entry point.

Scope of this package
---------------------

* :mod:`.types` (R38) — :class:`Rgba`, :class:`MermaidTheme`, the light/dark
  surface single-source-of-truth constants, :class:`RenderParams`, and
  :class:`RenderedDiagram`. Pure data shapes; no renderer pulled in.
* :mod:`.errors` (R38) — the :class:`MermaidError` taxonomy (parse/layout/
  rasterize/timeout/unsupported/panic), one subclass per grok variant.
* :mod:`.engine` (R38) — :class:`RenderLimits`, the :class:`MermaidEngine`
  protocol, and :func:`render_checked` (size cap + panic isolation).

What is NOT here (wiring round): the layout engine (grok's vendored
``mermaid-to-svg`` dagre port) and the SVG rasterizer (``resvg``/``usvg``/
``tiny-skia``) are Rust rendering stacks. A future wiring round picks a Python
renderer (``mmdc`` CLI subprocess or ``mermaid.js`` over a headless browser)
and implements :class:`MermaidEngine`; the guard logic here wraps any such
engine unchanged.
"""

from __future__ import annotations

from .engine import MermaidEngine, RenderLimits, render_checked
from .errors import (
    MermaidError,
    MermaidLayoutError,
    MermaidPanicError,
    MermaidParseError,
    MermaidRasterizeError,
    MermaidTimeoutError,
    MermaidUnsupportedError,
)
from .types import (
    DARK_SURFACE,
    DEFAULT_THEME,
    LIGHT_SURFACE,
    MermaidTheme,
    RenderedDiagram,
    RenderParams,
    Rgba,
)

__all__ = [
    # engine (R38)
    "RenderLimits",
    "MermaidEngine",
    "render_checked",
    # errors (R38)
    "MermaidError",
    "MermaidParseError",
    "MermaidLayoutError",
    "MermaidRasterizeError",
    "MermaidTimeoutError",
    "MermaidUnsupportedError",
    "MermaidPanicError",
    # types (R38)
    "Rgba",
    "MermaidTheme",
    "LIGHT_SURFACE",
    "DARK_SURFACE",
    "DEFAULT_THEME",
    "RenderParams",
    "RenderedDiagram",
]
