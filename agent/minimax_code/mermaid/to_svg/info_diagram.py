"""Info diagram renderer -- behavioral-equivalent port of grok's ``info_diagram.rs``.

Direction (1) brick 10 (R279). The first per-diagram renderer leaf. The
``info`` diagram is mermaid's version card -- a static, fixed-size SVG that
shows the pinned mermaid version string (``v11.12.2``). It is the simplest
per-diagram renderer (no parsing, no layout, no edge-routing -- just a
hard-coded SVG with one version text node), which makes it the natural
first leaf to wire into the dispatch (R279+ lands the remaining 18
per-diagram renderers one brick at a time).

Behavioral-equivalence mapping (same framework as R278c / R278d)
----------------------------------------------------------------

* grok ``const INFO_WIDTH: f64 = 400.0`` / ``INFO_HEIGHT: f64 = 150.0`` ->
  module ``int`` constants. The SVG viewBox carries no fractional pixels,
  and Rust's ``Display`` for ``400.0_f64`` renders ``"400"`` (drops the
  trailing ``.0``); ``int(400)`` renders the same ``"400"``. Identical SVG
  output, the Pythonic type for a pixel count.
* grok ``PINNED_MERMAID_VERSION: &str = "11.12.2"`` -> module ``str`` verbatim.
  Mermaid's real ``info`` diagram shows its build version; the port pins the
  same string grok does so the rendered card is byte-for-byte identical.
* grok ``pub fn render_info_diagram_to_svg(src, theme) -> Result<String,
  MermaidError>`` -> ``render_info_diagram_to_svg(src, theme) -> str``. grok's
  ``Result<...>`` (``Ok(svg)`` / ``Err(ParseError)``) maps to "return the SVG
  / raise :class:`ParseError`" -- the Pythonic shape for a fallible render.
* grok's token guard ``first_diagram_type_token(src) != Some("info")`` ->
  :func:`_first_diagram_type_token` (an inline copy -- see below).
* grok's color shortcuts (``#ffffff`` -> ``"white"``, ``#333333`` ->
  ``"#333"``) -> inline conditional expressions, verbatim. These reproduce
  mermaid's own ``info`` CSS compaction (``white`` / ``#333`` are the
  canonical shorthand forms the upstream renderer emits for those palettes).

Why an inline ``_first_diagram_type_token`` (function-not-line)
---------------------------------------------------------------

grok inlines a private ``first_diagram_type_token`` in every per-diagram
renderer (``info_diagram.rs`` L50-L56 is a verbatim copy of ``lib.rs``
L174-L180). That is a Rust necessity: the crate-root helper is a private
``fn``, invisible to sibling modules, so each renderer carries its own copy.

The port mirrors that self-containment rather than importing the helper
from :mod:`.render`. Importing it would close a circular edge:
``render.render_mermaid_to_svg`` calls ``info_diagram.render_info_diagram_to_svg``
(so ``render`` imports ``info_diagram`` at module top), and a reverse import
of ``first_diagram_type_token`` from ``render`` would fire while ``render``
is still mid-load (the helper is defined at L106, after the L49
``info_diagram`` import) -> ``ImportError``. A 6-line inline copy sidesteps
the cycle and keeps each per-diagram renderer self-contained -- the same
trade-off grok made, for the same reason, expressed the Pythonic way.

SVG assembly (function-not-line)
--------------------------------

grok builds the SVG from four ``push_str(format!(...))`` segments; the port
assembles the same fixed string from one module-level template with six
``__FOO__`` placeholders, substituted via :meth:`str.replace`. The
template keeps every CSS ``{}`` brace verbatim (an f-string or
:meth:`str.format` would force doubling each brace to ``{{`` / ``}}`` --
noisy and brittle across ~30 CSS rules); ``.replace`` sidesteps that
entirely. The output is byte-for-byte the SVG grok emits.

Public surface (1 symbol): :func:`render_info_diagram_to_svg` (grok
``pub fn``). The constants and the token helper stay module-private; grok's
crate root never re-exports them, and neither does the :mod:`.to_svg` barrel
-- the renderer is reached only through the dispatch in :mod:`.render`.
"""

from __future__ import annotations

from .error import ParseError
from .theme import MermaidTheme

__all__ = ["render_info_diagram_to_svg"]

#: SVG canvas width (grok ``INFO_WIDTH: f64 = 400.0``). Carried as ``int``
#: because the viewBox carries no fractional pixels; renders the same ``"400"``
#: as grok's ``Display`` for ``400.0_f64``.
INFO_WIDTH: int = 400

#: SVG canvas height (grok ``INFO_HEIGHT: f64 = 150.0``). Same int-for-f64
#: rationale as :data:`INFO_WIDTH`.
INFO_HEIGHT: int = 150

#: The mermaid version string this renderer pins (grok
#: ``PINNED_MERMAID_VERSION: &str = "11.12.2"``). Mermaid's real ``info``
#: diagram shows its build version; the port pins the same string so the
#: rendered card is identical to grok's.
PINNED_MERMAID_VERSION: str = "11.12.2"

#: The fixed SVG template (grok emits the same string from four
#: ``push_str(format!(...))`` calls). Six ``__FOO__`` placeholders carry the
#: theme-driven substitutions; every CSS ``{}`` brace stays verbatim (an
#: f-string / :meth:`str.format` would force ``{{`` / ``}}`` escaping across
#: ~30 rules -- :meth:`str.replace` avoids that entirely). Built from
#: adjacent string literals so each CSS rule sits on its own source line for
#: diff readability against grok's single-line ``format!``.
_SVG_TEMPLATE: str = (
    '<svg aria-roledescription="info" role="graphics-document document" '
    'viewBox="0 0 __WIDTH__ __HEIGHT__" '
    'style="max-width: __WIDTH__px; background-color: __BACKGROUND__;" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" '
    'xmlns="http://www.w3.org/2000/svg" width="100%" id="my-svg">'
    "<style>"
    '#my-svg{font-family:"trebuchet ms",verdana,arial,sans-serif;'
    "font-size:16px;fill:__TEXT__;}"
    "@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}"
    "@keyframes dash{to{stroke-dashoffset:0;}}"
    "#my-svg .edge-animation-slow{stroke-dasharray:9,5!important;"
    "stroke-dashoffset:900;animation:dash 50s linear infinite;"
    "stroke-linecap:round;}"
    "#my-svg .edge-animation-fast{stroke-dasharray:9,5!important;"
    "stroke-dashoffset:900;animation:dash 20s linear infinite;"
    "stroke-linecap:round;}"
    "#my-svg .error-icon{fill:#552222;}"
    "#my-svg .error-text{fill:#552222;stroke:#552222;}"
    "#my-svg .edge-thickness-normal{stroke-width:1px;}"
    "#my-svg .edge-thickness-thick{stroke-width:3.5px;}"
    "#my-svg .edge-pattern-solid{stroke-dasharray:0;}"
    "#my-svg .edge-thickness-invisible{stroke-width:0;fill:none;}"
    "#my-svg .edge-pattern-dashed{stroke-dasharray:3;}"
    "#my-svg .edge-pattern-dotted{stroke-dasharray:2;}"
    "#my-svg .marker{fill:__EDGE__;stroke:__EDGE__;}"
    "#my-svg .marker.cross{stroke:__EDGE__;}"
    '#my-svg svg{font-family:"trebuchet ms",verdana,arial,sans-serif;'
    "font-size:16px;}"
    "#my-svg p{margin:0;}"
    '#my-svg :root{--mermaid-font-family:"trebuchet ms",verdana,arial,'
    "sans-serif;}"
    "</style>"
    "<g/>"
    '<g><text style="text-anchor: middle;" font-size="32" class="version" '
    'y="40" x="100">v__VERSION__</text></g>'
    "</svg>"
)


def _first_diagram_type_token(input: str) -> str | None:
    """Return the first diagram-type token in ``input`` (grok inline copy).

    Self-contained copy of :func:`render.first_diagram_type_token` -- grok
    inlines one per per-diagram renderer (Rust module visibility: the
    crate-root helper is private to ``lib.rs``). The port mirrors that
    self-containment rather than importing across a circular
    ``render -> info_diagram -> render`` edge (see the module docstring's
    "Why an inline" section). Same semantics: the first whitespace token of
    the first non-empty / non-``%%`` line, else ``None``.
    """
    for line in input.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("%%"):
            return stripped.split()[0]
    return None


def render_info_diagram_to_svg(
    mermaid_source: str, theme: MermaidTheme
) -> str:
    """Render an ``info`` diagram into a static version-card SVG.

    Direction (1) brick 10 (R279). Mirrors grok ``info_diagram.rs``
    ``render_info_diagram_to_svg``: the ``info`` diagram is mermaid's version
    card -- a fixed ``400 x 150`` SVG showing the pinned mermaid version
    (``v11.12.2``). No parsing or layout runs; once the ``info`` token is
    confirmed, the body is irrelevant.

    A token guard raises :class:`ParseError` when the source's first token is
    not ``info`` (defensive -- the renderer is a public entry, reachable
    directly, not only through dispatch). The theme's background / text /
    edge colors flow into the SVG ``style`` block, with grok's
    ``#ffffff`` -> ``white`` and ``#333333`` -> ``#333`` shorthand
    compaction. The version text node sits at ``x=100, y=40``.

    Args:
        mermaid_source: mermaid source whose first diagram-type token is
            ``info`` (the caller -- typically :func:`render.render_mermaid_to_svg`
            dispatch -- passes the source verbatim, mirroring grok lib.rs L82).
        theme: the resolved :class:`MermaidTheme` palette (background /
            text_color / edge_color feed the SVG ``style`` block).

    Returns:
        The version-card SVG string.

    Raises:
        ParseError: when the source's first diagram-type token is not
            ``info`` (grok ``ParseError { line: 1, message: "Expected 'info'
            declaration" }``).
    """
    if _first_diagram_type_token(mermaid_source) != "info":
        raise ParseError(1, "Expected 'info' declaration")

    # grok's color shorthand compaction (info_diagram.rs L20-L29): mermaid's
    # canonical light palette (#ffffff / #333333) renders as the CSS
    # shorthand forms ``white`` / ``#333``; any other palette passes through
    # verbatim. The edge color needs no compaction -- grok uses it as-is.
    background_color = "white" if theme.background == "#ffffff" else theme.background
    text_color = "#333" if theme.text_color == "#333333" else theme.text_color
    edge_color = theme.edge_color

    return (
        _SVG_TEMPLATE.replace("__WIDTH__", str(INFO_WIDTH))
        .replace("__HEIGHT__", str(INFO_HEIGHT))
        .replace("__BACKGROUND__", background_color)
        .replace("__TEXT__", text_color)
        .replace("__EDGE__", edge_color)
        .replace("__VERSION__", PINNED_MERMAID_VERSION)
    )
