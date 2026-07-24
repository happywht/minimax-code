"""Crate-root render dispatch -- fusion of Grok Build's ``mermaid-to-svg/src/lib.rs``.

Direction (1) brick 9 (R277). Migrates grok's crate-root public API surface
-- ``render_mermaid_to_svg`` + ``strip_mermaid_frontmatter`` +
``is_mermaid_diagram`` plus the private ``first_diagram_type_token`` -- as a
zero-semantic clone. This leaf is the entry point that turns mermaid source
into an SVG string, mirroring grok ``lib.rs`` lines L36-L185.

Dispatch model (mirrors grok ``render_mermaid_to_svg`` L36-L163)
---------------------------------------------------------------

1. Parse the front-matter (``parse_mermaid_frontmatter``) into a
   :class:`ParsedMermaidSource` (body + front-matter + typed config).
2. Resolve the theme with grok's priority: an explicit ``theme`` arg wins;
   otherwise the front-matter ``config`` block's palette (``to_mermaid_theme``);
   otherwise the default light palette (``MermaidTheme.default``).
3. Extract the first diagram-type token from the body
   (:func:`first_diagram_type_token`).
4. Dispatch:
   * The ``info`` renderer (R279) has its own dedicated arm above the
     unsupported check -- :func:`render_info_diagram_to_svg` emits the static
     version card (``v11.12.2``).
   * The ``stateDiagram`` / ``stateDiagram-v2`` parser (R280) has its own
     dedicated arm too -- :func:`parse_state_diagram` emits a FlowchartGraph
     that rides the dagre stack (``compute_layout`` + ``render``, no config),
     mirroring grok lib.rs L63-L68.
   * The ``radar-beta`` renderer (R281) has its own dedicated arm --
     :func:`render_radar_diagram_to_svg` emits the radar / spider chart
     (concentric graticule + polar axis spokes + closed Catmull-Rom series
     curves), mirroring grok lib.rs L94-L96.
   * The ``pie`` renderer (R282) has its own dedicated arm --
     :func:`render_pie_diagram_to_svg` emits the pie / donut chart (one colored
     wedge per >=1% slice, d3.pie() descending sort, a right-side legend),
     mirroring grok lib.rs L70-L72.
   * The 16 remaining independent per-diagram renderers (er / class / mindmap
     / gantt / requirement / packet / block / sankey / sequence
     / gitgraph / timeline / journey / kanban / quadrant / xychart / c4)
     raise :class:`UnsupportedDiagramType` here. Their per-diagram leaves ship
     in later rounds (R283+); until then the dispatch reports the type as
     unsupported rather than running a renderer that does not exist yet.
   * The default path runs the already-migrated flowchart stack
     (``parser.parse_mermaid`` -> ``layout.compute_layout[_with_config]`` ->
     ``svg_renderer.render[_with_config]``), which is the only path the
     dagre-backed engine serves today. Flowcharts (``graph`` / ``flowchart``)
     pass the typed config; every other token still falls through to the
     generic parser + layouter (matching grok's unconditional
     ``parser::parse_mermaid`` at L150).

The experimental ``mermaid_port`` flowchart port stays sealed behind its
HERMETIC VENDORING PATCH (R276 YAGNI decision): grok's ``is_enabled() ->
false`` skips the port unconditionally, and so does this clone -- the default
``layout`` path routes cycles correctly, which is the whole reason the dagre
engine was adopted.

Public surface (3 symbols): ``render_mermaid_to_svg``,
``strip_mermaid_frontmatter``, ``is_mermaid_diagram``. The barrel
(:mod:`minimax_code.mermaid.to_svg`) re-exports them, growing its ``__all__``
from 15 to 18. ``first_diagram_type_token`` stays module-private (grok's
``fn`` at L174 has no ``pub``); tests reach it via a direct module import.
"""

from __future__ import annotations

from .config import parse_mermaid_frontmatter
from .error import UnsupportedDiagramType
from .info_diagram import render_info_diagram_to_svg
from .layout import compute_layout, compute_layout_with_config
from .parser import parse_mermaid
from .pie_diagram import render_pie_diagram_to_svg
from .radar_diagram import render_radar_diagram_to_svg
from .state_diagram import parse_state_diagram
from .svg_renderer import render, render_with_config
from .theme import MermaidTheme

__all__ = [
    "is_mermaid_diagram",
    "render_mermaid_to_svg",
    "strip_mermaid_frontmatter",
]


#: Diagram-type tokens whose renderers ship as independent per-diagram leaves
#: in later rounds (R283+). Until those leaves land, the dispatch raises
#: :class:`UnsupportedDiagramType` -- mirrors grok's per-diagram ``if`` arms
#: (lib.rs L51-L139) minus the renderer bodies. The ``info`` renderer shipped
#: in R279, the ``stateDiagram`` / ``stateDiagram-v2`` parser shipped in
#: R280, the ``radar-beta`` renderer shipped in R281, and the ``pie``
#: renderer shipped in R282 (their dedicated arms sit above this check);
#: the 20 tokens below are the remaining unsupported surface.
#: The five ``C4*`` tokens share grok's single ``c4_diagram`` renderer
#: (lib.rs L130-L139).
_UNSUPPORTED_DIAGRAM_TYPES: frozenset[str] = frozenset(
    {
        "erDiagram",
        "classDiagram",
        "mindmap",
        "gantt",
        "requirementDiagram",
        "packet-beta",
        "block-beta",
        "sankey-beta",
        "sequenceDiagram",
        "gitGraph",
        "timeline",
        "journey",
        "kanban",
        "quadrantChart",
        "xychart-beta",
        "C4Context",
        "C4Container",
        "C4Component",
        "C4Dynamic",
        "C4Deployment",
    }
)

#: Flowchart-type tokens (grok lib.rs L141 ``matches!(..., Some("graph") | Some("flowchart"))``).
_FLOWCHART_TOKENS: frozenset[str] = frozenset({"graph", "flowchart"})

#: State-diagram tokens (grok lib.rs L63 ``matches!(..., Some("stateDiagram")
#: | Some("stateDiagram-v2"))``). Dispatched to the dedicated state parser
#: (R280) which emits a FlowchartGraph riding the dagre stack -- NOT the
#: generic flowchart path, even though both end up calling
#: ``compute_layout`` + ``render``.
_STATE_DIAGRAM_TOKENS: frozenset[str] = frozenset({"stateDiagram", "stateDiagram-v2"})


def first_diagram_type_token(input: str) -> str | None:
    """Return the first diagram-type token in ``input`` (grok ``first_diagram_type_token``).

    Scans lines, trims each, and returns the first whitespace-delimited token
    of the first non-empty line that does not start with a mermaid comment
    (``%%``). ``None`` when every line is empty or a comment. Mirrors grok
    lib.rs L174-L180.
    """
    for line in input.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("%%"):
            return stripped.split()[0]
    return None


def render_mermaid_to_svg(
    mermaid_source: str, theme: MermaidTheme | None = None
) -> str:
    """Render mermaid source into an SVG string (grok ``render_mermaid_to_svg``).

    Parses the front-matter, resolves the theme (``theme`` arg > front-matter
    ``config`` block > the default light palette), extracts the diagram-type
    token, and dispatches. The flowchart default path runs the dagre-backed
    stack (``parser`` -> ``layout`` -> ``svg_renderer``); the ``info``
    renderer (R279) emits mermaid's version card via
    :func:`render_info_diagram_to_svg`; the ``stateDiagram`` /
    ``stateDiagram-v2`` parser (R280) emits a FlowchartGraph via
    :func:`parse_state_diagram`; the ``radar-beta`` renderer (R281) emits the
    radar chart via :func:`render_radar_diagram_to_svg`; the ``pie`` renderer
    (R282) emits the pie chart via :func:`render_pie_diagram_to_svg`; the 16
    remaining per-diagram renderers raise :class:`UnsupportedDiagramType`
    until their leaves ship (R283+). Mirrors grok lib.rs L36-L163.

    Raises:
        UnsupportedDiagramType: when the diagram-type token names a diagram
            kind whose dedicated renderer is not migrated yet.
        MermaidError: when parsing or laying out the body fails (propagated
            from ``parser`` / ``layout`` / ``svg_renderer`` -- mirrors grok's
            ``?`` operator).
    """
    parsed_source = parse_mermaid_frontmatter(mermaid_source)
    default_theme = MermaidTheme.default()
    configured_theme = parsed_source.config.to_mermaid_theme()
    if theme is not None:
        resolved_theme = theme
    elif configured_theme is not None:
        resolved_theme = configured_theme
    else:
        resolved_theme = default_theme

    body = parsed_source.body
    diagram_type = first_diagram_type_token(body)

    # ``info`` has its own dedicated renderer (R279): mermaid's version card.
    # Mirrors grok lib.rs L82-L84, with the per-diagram dispatch contract
    # lifted in R282: grok lib.rs L47 shadows ``mermaid_source`` with the
    # front-matter-stripped ``body`` before any per-diagram arm runs, so
    # :func:`render_info_diagram_to_svg` receives the body (not the raw
    # source). On a source without front-matter the body equals the source,
    # so R279's fixtures are unchanged.
    if diagram_type == "info":
        return render_info_diagram_to_svg(body, resolved_theme)

    # ``stateDiagram`` / ``stateDiagram-v2`` (R280): parse into a
    # FlowchartGraph via the dedicated state parser and ride the dagre stack
    # with NO config -- mirrors grok lib.rs L63-L68 (``parse_state_diagram``
    # -> ``compute_layout`` -> ``render``, the config-less pair, since state
    # diagrams do not consume the flowchart curve / spacing knobs).
    if diagram_type in _STATE_DIAGRAM_TOKENS:
        graph = parse_state_diagram(body)
        layout_result = compute_layout(graph)
        return render(layout_result, resolved_theme)

    # ``radar-beta`` (R281): the dedicated radar-chart renderer. Mirrors grok
    # lib.rs L94-L96 -- :func:`render_radar_diagram_to_svg` receives the
    # front-matter-stripped body (R282 lifted the arm to the per-diagram
    # dispatch contract; the body equals the raw source when no front-matter
    # is present, so R281's fixtures are unchanged). The renderer scans for
    # the ``radar-beta`` header itself.
    if diagram_type == "radar-beta":
        return render_radar_diagram_to_svg(body, resolved_theme)

    # ``pie`` (R282): the dedicated pie-chart renderer. Mirrors grok lib.rs
    # L70-L72 -- :func:`render_pie_diagram_to_svg` receives the front-matter-
    # stripped body and emits a fixed-height SVG with one colored wedge per
    # >=1% slice (d3.pie() descending sort) plus a right-side legend.
    if diagram_type == "pie":
        return render_pie_diagram_to_svg(body, resolved_theme)

    if diagram_type in _UNSUPPORTED_DIAGRAM_TYPES:
        raise UnsupportedDiagramType(diagram_type)

    is_flowchart = diagram_type in _FLOWCHART_TOKENS
    # The experimental ``mermaid_port`` flowchart port stays sealed (R276
    # YAGNI): grok's ``is_enabled() -> false`` skips it unconditionally, and
    # so does this clone. The default ``layout`` path routes cycles correctly.
    graph = parse_mermaid(body)
    if is_flowchart:
        layout_result = compute_layout_with_config(graph, parsed_source.config)
        svg = render_with_config(layout_result, resolved_theme, parsed_source.config)
    else:
        layout_result = compute_layout(graph)
        svg = render(layout_result, resolved_theme)
    return svg


def strip_mermaid_frontmatter(source: str) -> str:
    """Strip a leading mermaid YAML front-matter block (grok ``strip_mermaid_frontmatter``).

    Mermaid supports frontmatter at the start of a diagram for per-diagram
    metadata and configuration. Delegates to :func:`parse_mermaid_frontmatter`
    and returns the body (source verbatim when no ``---`` fence). Mirrors grok
    lib.rs L170-L172.
    """
    return parse_mermaid_frontmatter(source).body


def is_mermaid_diagram(lang: str) -> bool:
    """Return whether ``lang`` identifies a mermaid diagram block (grok ``is_mermaid_diagram``).

    Matches ``mermaid`` exactly or a ``mermaid ...`` prefix, case-insensitively.
    Mirrors grok lib.rs L182-L185.
    """
    lang_lower = lang.lower()
    return lang_lower == "mermaid" or lang_lower.startswith("mermaid ")
