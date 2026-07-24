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
   * The 19 remaining independent per-diagram renderers (er / class / mindmap
     / state / pie / gantt / requirement / packet / block / radar / sankey /
     sequence / gitgraph / timeline / journey / kanban / quadrant / xychart /
     c4) raise :class:`UnsupportedDiagramType` here. Their per-diagram leaves
     ship in later rounds (R280+); until then the dispatch reports the type
     as unsupported rather than running a renderer that does not exist yet.
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
from .svg_renderer import render, render_with_config
from .theme import MermaidTheme

__all__ = [
    "is_mermaid_diagram",
    "render_mermaid_to_svg",
    "strip_mermaid_frontmatter",
]


#: Diagram-type tokens whose renderers ship as independent per-diagram leaves
#: in later rounds (R280+). Until those leaves land, the dispatch raises
#: :class:`UnsupportedDiagramType` -- mirrors grok's per-diagram ``if`` arms
#: (lib.rs L51-L139) minus the renderer bodies. The ``info`` renderer shipped
#: in R279 (its dedicated arm sits above this check); the 24 tokens below are
#: the remaining unsupported surface. The state tokens (``stateDiagram`` /
#: ``stateDiagram-v2``) appear here too: grok routes them through
#: ``state_diagram::parse_state_diagram`` (a dedicated parser), not the
#: generic flowchart path, so they are not served by the default stack today.
#: The five ``C4*`` tokens share grok's single ``c4_diagram`` renderer
#: (lib.rs L130-L139).
_UNSUPPORTED_DIAGRAM_TYPES: frozenset[str] = frozenset(
    {
        "erDiagram",
        "classDiagram",
        "mindmap",
        "stateDiagram",
        "stateDiagram-v2",
        "pie",
        "gantt",
        "requirementDiagram",
        "packet-beta",
        "block-beta",
        "radar-beta",
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
    :func:`render_info_diagram_to_svg`; the 19 remaining per-diagram
    renderers raise :class:`UnsupportedDiagramType` until their leaves ship
    (R280+). Mirrors grok lib.rs L36-L163.

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
    # Mirrors grok lib.rs L82-L84 -- the raw ``mermaid_source`` (front-matter
    # and all) flows to :func:`render_info_diagram_to_svg`, which re-extracts
    # the token defensively and ignores the body otherwise.
    if diagram_type == "info":
        return render_info_diagram_to_svg(mermaid_source, resolved_theme)

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
