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
* :mod:`.subprocess` (R278) — :func:`run_with_timeout` and the
  :class:`SubprocessError` taxonomy (Spawn / Timeout / NonZeroExit / Wait).
  The panic-isolating child runner: spawn a child, optionally feed stdin,
  wait up to a wall-clock budget, and reap the whole process group on a
  breach. Backs the optional ``mmdc`` engine (R278d) -- grok ``subprocess.rs``
  re-exported at the crate root (``lib.rs`` L57).

What is here vs deferred
------------------------

* **Layout + SVG render (R269--R277)**: the vendored dagre layout engine and
  the SVG renderer are fully migrated under :mod:`.to_svg`, and
  :func:`~minimax_code.mermaid.to_svg.render_mermaid_to_svg` wires mermaid
  source end-to-end to an SVG string. The dagre stack routes cyclic
  flowcharts correctly -- the defect that motivated adopting dagre.
* **Subprocess isolation (R278a)**: :mod:`.subprocess` lands the
  panic-isolating child runner (spawn / feed stdin / wait to a wall-clock
  budget / reap the whole process group on a breach) that the optional
  ``mmdc`` engine will consume.
* **SVG -> PNG rasterization -- YAGNI, not migrated (R278b)**: grok
  ``raster.rs`` rasterizes SVG to PNG via the pure-Rust ``resvg`` / ``usvg``
  / ``tiny-skia`` / ``fontdb`` stack plus a bundled ``Roboto-Regular.ttf``
  face. There is no pure-Python equivalent (cairosvg / svglib / ``resvg-py``
  are C/Rust bindings, not ports), and this project ships no Rust toolchain.
  PNG is also an optional output here -- the React front end renders SVG
  directly -- so the raster path is deferred, not reimplemented.
  :class:`MermaidRasterizeError` stays in the taxonomy so a future wiring
  round that adopts a Python rasterizer surfaces failures through the same
  error class.
* **Default engine (R278c)**: :mod:`.pure` lands the default
  :class:`PureRustEngine` -- the offline engine composing the dagre SVG path
  (:func:`~minimax_code.mermaid.to_svg.render_mermaid_to_svg`) with the raster
  step. The raster step is the R278b YAGNI gap, so ``render`` runs the SVG half
  and raises :class:`MermaidRasterizeError` at the raster step rather than
  fabricating PNG bytes.
* **Host wrapper leaf (R278d)**: :mod:`.mmdc` lands the optional
  :class:`MmdcEngine` -- the CLI engine that shells out to ``mmdc`` / headless
  Chromium. Off by default; a caller must construct it explicitly (via
  :meth:`~minimax_code.mermaid.mmdc.MmdcEngine.detect`). Its raster step is the
  same R278b YAGNI gap (``render`` runs the SVG half then raises
  :class:`MermaidRasterizeError`). :func:`default_engine` is the crate-root
  factory (grok ``lib.rs`` L197--L203) returning the offline
  :class:`PureRustEngine` -- ``mmdc`` is never selected automatically.
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
from .mmdc import MmdcEngine, detect_mmdc
from .pure import PureRustEngine
from .subprocess import (
    NonZeroExitSubprocessError,
    SpawnSubprocessError,
    SubprocessError,
    TimeoutSubprocessError,
    WaitSubprocessError,
    run_with_timeout,
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


def default_engine() -> PureRustEngine:
    """Construct the default engine: the offline :class:`PureRustEngine` (grok ``default_engine``).

    Mirrors grok ``lib.rs`` L197--L203: the default is always the pure-Rust
    engine. ``mmdc`` is never selected automatically -- a caller must construct
    :class:`MmdcEngine` explicitly (via
    :meth:`~minimax_code.mermaid.mmdc.MmdcEngine.detect`) to opt into the CLI
    engine. Returns a fresh :class:`PureRustEngine` each call (stateless, so
    interchangeable -- value-equal and hashable per R278c).
    """
    return PureRustEngine()


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
    # subprocess (R278)
    "SubprocessError",
    "SpawnSubprocessError",
    "TimeoutSubprocessError",
    "NonZeroExitSubprocessError",
    "WaitSubprocessError",
    "run_with_timeout",
    # pure (R278c)
    "PureRustEngine",
    # mmdc (R278d)
    "MmdcEngine",
    "detect_mmdc",
    # default_engine factory (R278d, grok lib.rs crate root L197-L203)
    "default_engine",
]
