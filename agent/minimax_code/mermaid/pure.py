"""Pure-Python default engine -- behavioral-equivalent port of grok's ``pure.rs``.

Direction (1) brick 11 (R278c). Ports grok's ``pure.rs`` (the default
``PureRustEngine`` host-shell leaf) as a **behavioral-equivalent port**:
each function's contract (what it does, the errors it raises, the taxonomy
it exposes to callers) is preserved, but the implementation is idiomatic
Python -- NOT a line-by-line translation of the Rust. Where grok leans on
Rust language features, the port reaches for the Python equivalent that
expresses the same semantics:

* grok's ``#[derive(Debug, Default, Clone, Copy)]`` unit struct ->
  ``__slots__ = ()`` + ``__eq__`` / ``__hash__`` / ``__repr__`` (the four
  semantics a unit struct carries: stateless, value-equal, hashable,
  printable) -- hand-written dunders, not a translated derive macro.
* grok's ``?`` operator (early error return) -> ``try`` / ``except`` /
  ``raise ... from exc`` (the same error-propagation contract).
* grok's ``match`` over a 6-variant enum -> ``isinstance`` over a tuple
  of the variants grouped by the host's 3 categories (the same
  classification, expressed as Python sees it).

The default, offline engine composes the SVG half (the dagre layout + SVG
render stack migrated in R269--R277) with the raster half that turns the
SVG into PNG bytes.

The raster half is YAGNI here (the R278b ruling): grok's
``PureRustEngine::render`` (pure.rs L23--L27) runs ``build_svg(source,
params.theme)?`` and then ``crate::rasterize(&svg, params)`` -- the pure-Rust
``resvg`` / ``usvg`` / ``tiny-skia`` / ``fontdb`` stack plus a bundled
``Roboto-Regular.ttf`` face. There is no pure-Python equivalent (cairosvg /
svglib / ``resvg-py`` are C/Rust bindings, not ports), this project ships no
Rust toolchain, and PNG is an optional output (the React front end renders SVG
directly). So :meth:`PureRustEngine.render` runs the SVG half (propagating
parse / layout / unsupported errors exactly as grok's ``?`` does) and then
raises :class:`~minimax_code.mermaid.errors.MermaidRasterizeError` at the
raster step rather than fabricating PNG bytes. ``build_svg`` / ``theme_for`` /
``map_engine_error`` are fully migrated and unit-tested below; a future wiring
round that adopts a Python rasterizer swaps in that one call and the rest is
unchanged.

The SVG path itself is exercised every time ``render`` is called (it runs
before the raster step raises), so the default engine is not dead code -- it is
the protocol-compliant skeleton whose SVG half is live and whose raster half is
an honest, typed "not wired" sentinel.

Mapping
-------

* ``PureRustEngine`` (grok ``#[derive(Debug, Default, Clone, Copy)]`` unit
  struct) -> a stateless class (``__slots__ = ()``); ``Default`` /
  ``PureRustEngine::new()`` -> the no-arg constructor; ``Clone + Copy`` ->
  instances are interchangeable (value-equal and hashable). ``impl MermaidEngine
  for PureRustEngine`` -> the ``render`` method satisfying the
  :class:`~minimax_code.mermaid.engine.MermaidEngine` protocol.
* ``build_svg`` (grok private ``fn``, pure.rs L35--L38) -> a module-level
  function (not in ``__all__``); tests reach it by direct import, mirroring
  grok's ``mod pure`` tests reaching ``super::build_svg``.
* ``theme_for`` (grok ``fn``, pure.rs L59--L72) maps the host's coarse
  :class:`~minimax_code.mermaid.types.MermaidTheme` (light / dark) onto the
  render stack's richer :class:`~minimax_code.mermaid.to_svg.theme.MermaidTheme`
  (7 resolved colors), overriding only the diagram surface to the host's
  single-source-of-truth :data:`~minimax_code.mermaid.types.LIGHT_SURFACE` /
  :data:`~minimax_code.mermaid.types.DARK_SURFACE` so the painted background
  blends with the surface the diagram sits on.
* ``map_engine_error`` (grok ``fn``, pure.rs L42--L51) maps the vendored
  stack's 6-variant :class:`~minimax_code.mermaid.to_svg.error.MermaidError`
  onto the host's coarse 3-category split (parse / layout / unsupported),
  preserving the observability distinction a caller switches on.

Public surface (1 symbol): :class:`PureRustEngine` (grok ``lib.rs`` L55
``pub use pure::PureRustEngine``). ``build_svg`` / ``theme_for`` /
``map_engine_error`` stay module-private (grok's ``fn``s have no ``pub``).
"""

from __future__ import annotations

from .errors import (
    MermaidError,
    MermaidLayoutError,
    MermaidParseError,
    MermaidRasterizeError,
    MermaidUnsupportedError,
)
from .to_svg import MermaidTheme as _EngineTheme
from .to_svg import render_mermaid_to_svg
from .to_svg.error import (
    DotGenerationError as _DotGenerationError,
)
from .to_svg.error import (
    InvalidDirection as _InvalidDirection,
)
from .to_svg.error import (
    InvalidNodeShape as _InvalidNodeShape,
)
from .to_svg.error import (
    MermaidError as _EngineMermaidError,
)
from .to_svg.error import (
    ParseError as _ParseError,
)
from .to_svg.error import (
    RenderError as _RenderError,
)
from .to_svg.error import (
    UnsupportedDiagramType as _UnsupportedDiagramType,
)
from .types import (
    DARK_SURFACE,
    LIGHT_SURFACE,
    MermaidTheme,
    RenderedDiagram,
    RenderParams,
)

__all__ = ["PureRustEngine"]


class PureRustEngine:
    """The default, offline engine (grok ``PureRustEngine``, pure.rs L8--L27).

    Composes the dagre-backed SVG path (:func:`build_svg`) with the raster
    step. grok's raster step (``crate::rasterize``) is YAGNI here (R278b): the
    pure-Rust ``resvg`` / ``usvg`` / ``tiny-skia`` / ``fontdb`` stack has no
    pure-Python equivalent and PNG is an optional output, so :meth:`render`
    runs the SVG half and then raises
    :class:`~minimax_code.mermaid.errors.MermaidRasterizeError` at the raster
    step rather than fabricating PNG bytes. A future wiring round that adopts a
    Python rasterizer swaps in that one call.

    Stateless (mirrors grok's ``#[derive(Default, Clone, Copy)]`` unit struct):
    every instance is interchangeable. Value equality + hashing lets a caller
    treat the engine as a singleton without enforcing one.
    """

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        # grok's unit struct derives ``PartialEq + Eq``: any two instances compare
        # equal (there is no state to differ on).
        return isinstance(other, PureRustEngine)

    def __hash__(self) -> int:
        # Hashable so the engine can sit in a set / dict key (grok's ``Eq``).
        return hash(PureRustEngine)

    def __repr__(self) -> str:
        # Mirrors grok's ``Debug`` derive (the unit struct prints as ``PureRustEngine``).
        return "PureRustEngine()"

    def render(self, source: str, params: RenderParams) -> RenderedDiagram:
        """Render mermaid ``source`` under ``params`` (grok ``render``, pure.rs L22--L27).

        Runs the SVG half first (:func:`build_svg`) so parse / layout /
        unsupported errors propagate exactly as grok's ``build_svg(source,
        params.theme)?`` does. The SVG return value is discarded because the
        raster half is YAGNI here (R278b): the pure-Rust
        ``resvg`` / ``usvg`` / ``tiny-skia`` / ``fontdb`` stack has no
        pure-Python equivalent, this project ships no Rust toolchain, and PNG
        is an optional output (the React front end renders SVG directly).

        Raises:
            MermaidParseError: the source could not be parsed.
            MermaidLayoutError: the dagre layout / SVG render step failed.
            MermaidUnsupportedError: the diagram type is not yet rendered.
            MermaidRasterizeError: the SVG could not be rasterized to PNG
                (always, until a Python rasterizer is wired in -- R278b).
        """
        # SVG half (live): resolves the theme and runs the dagre stack. Propagates
        # the mapped host error taxonomy on failure (grok's ``?``).
        build_svg(source, params.theme)
        # Raster half (YAGNI, R278b): grok calls ``crate::rasterize(&svg, params)``
        # to produce ``RenderedDiagram { png, width_px, height_px }``. The
        # pure-Rust raster stack is unportable and PNG is optional here, so raise
        # the typed sentinel rather than fabricating PNG bytes. A future wiring
        # round that adopts a Python rasterizer replaces this one statement.
        raise MermaidRasterizeError(
            "SVG -> PNG rasterization is not available in the Python port "
            "(R278b): the pure-Rust resvg/usvg/tiny-skia/fontdb stack has no "
            "pure-Python equivalent, and PNG is an optional output"
        )


def build_svg(source: str, theme: MermaidTheme) -> str:
    """Mermaid source -> SVG string (the layout half, grok ``build_svg`` pure.rs L35--L38).

    Resolves the host theme onto a render-stack theme (:func:`theme_for`) and
    runs the dagre-backed SVG renderer
    (:func:`~minimax_code.mermaid.to_svg.render_mermaid_to_svg`). A free
    function (no engine state) so the SVG can be tested directly and reused by
    :meth:`PureRustEngine.render`; maps the vendored stack's error onto the
    host taxonomy (:func:`map_engine_error`) on failure.

    Raises:
        MermaidParseError: the source could not be parsed.
        MermaidLayoutError: the dagre layout / SVG render step failed.
        MermaidUnsupportedError: the diagram type is not yet rendered.
    """
    engine_theme = theme_for(theme)
    try:
        return render_mermaid_to_svg(source, engine_theme)
    except _EngineMermaidError as exc:
        # grok's ``.map_err(map_engine_error)``: re-categorize the vendored
        # stack's 6-variant error onto the host's coarse 3-category split.
        raise map_engine_error(exc) from exc


def theme_for(theme: MermaidTheme) -> _EngineTheme:
    """Map the host theme onto a render-stack theme (grok ``theme_for``, pure.rs L59--L72).

    The host's :class:`~minimax_code.mermaid.types.MermaidTheme` is coarse
    (light / dark); the render stack's
    :class:`~minimax_code.mermaid.to_svg.theme.MermaidTheme` carries 7 resolved
    colors. We start from the render stack's light / dark preset and override
    only the diagram surface (``background``) to the host's
    single-source-of-truth surface constant, so the painted background blends
    with the surface the diagram sits on (grok's ``LIGHT_SURFACE`` /
    ``DARK_SURFACE``, R38). Other channels (node fill, edge color, ...) keep the
    render stack's defaults.
    """
    if theme is MermaidTheme.LIGHT:
        engine_theme = _EngineTheme.light()
        engine_theme.background = LIGHT_SURFACE.to_hex()
        return engine_theme
    # MermaidTheme.DARK (the only other variant).
    engine_theme = _EngineTheme.dark()
    engine_theme.background = DARK_SURFACE.to_hex()
    return engine_theme


def map_engine_error(exc: _EngineMermaidError) -> MermaidError:
    """Map the vendored stack's error onto the host taxonomy (grok ``map_engine_error``, pure.rs L42--L51).

    The render stack's 6-variant :class:`~minimax_code.mermaid.to_svg.error.MermaidError`
    collapses onto the host's coarse 3 categories that a caller switches on:
    parse failures (bad source), layout / render failures (the dagre stack
    could not place or draw the graph), and unsupported diagram types. The
    original message is preserved verbatim so no detail is lost.
    """
    if isinstance(exc, (_ParseError, _InvalidDirection, _InvalidNodeShape)):
        return MermaidParseError(str(exc))
    if isinstance(exc, (_DotGenerationError, _RenderError)):
        return MermaidLayoutError(str(exc))
    if isinstance(exc, _UnsupportedDiagramType):
        return MermaidUnsupportedError(str(exc))
    # grok's enum match is exhaustive over 6 variants; the fallthrough is
    # unreachable for a well-typed EngineMermaidError but keeps the mapping
    # total (a future variant surfaces as a generic MermaidError rather than
    # leaking the vendored type).
    return MermaidError(str(exc))
