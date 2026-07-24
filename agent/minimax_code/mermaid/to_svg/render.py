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
   * The ``packet-beta`` renderer (R283) has its own dedicated arm --
     :func:`render_packet_diagram_to_svg` lays contiguous bit ranges out on a
     32-bit-per-row grid (one ``#efefef`` rectangle per block fragment with
     centered label + start/end bit indices), mirroring grok lib.rs L74-L76.
   * The ``sankey-beta`` renderer (R284) has its own dedicated arm --
     :func:`render_sankey_diagram_to_svg` lays a weighted flow graph out
     left-to-right by longest-path depth (throughput-proportional node bars +
     flow-proportional gradient-stroked cubic-Bezier ribbons), mirroring grok
     lib.rs L98-L100.
   * The ``gantt`` renderer (R285) has its own dedicated arm --
     :func:`render_gantt_diagram_to_svg` lays a project schedule out as
     horizontal task bars on a day-scaled timeline (section background bands
     + bottom date axis with daily grid ticks + inside/outside task labels +
     left-side section legend), mirroring grok lib.rs L74-L76.
   * The ``kanban`` renderer (R286) has its own dedicated arm --
     :func:`render_kanban_diagram_to_svg` lays a board out as titled columns
     of task cards (per-column cluster rect + cluster-label + per-card node
     with label/assigned placeholders and an optional priority indicator
     stripe), mirroring grok lib.rs L118-L120.
   * The ``timeline`` renderer (R287) has its own dedicated arm --
     :func:`render_timeline_diagram_to_svg` lays a chronological board out as
     a horizontal axis of period nodes each trailing a vertical stack of event
     cards, grouped under optional colored section banners, with a left-to-
     right direction arrow underneath, mirroring grok lib.rs L108-L110.
   * The ``quadrantChart`` renderer (R288) has its own dedicated arm --
     :func:`render_quadrant_chart_to_svg` lays a 500x500 canvas out as four
     labelled quadrants split by an internal cross, with optional x/y axis
     range pairs, a title, and clamped [0, 1] data points, mirroring grok
     lib.rs L104-L106.
   * The ``block-beta`` renderer (R289) has its own dedicated arm --
     :func:`render_block_diagram_to_svg` lays labelled rectangular nodes out
     on a ``columns``-driven grid connected by D3 ``curveBasis`` edges
     (five-phase pipeline: node sizing -> normalize to max size -> grid
     layout -> findBounds -> SVG emission), mirroring grok lib.rs L90-L92.
     Block is the most theme-aware renderer in the family: all five
     ``MermaidTheme`` channels flow into the SVG.
   * The ``journey`` renderer (R290) has its own dedicated arm --
     :func:`render_journey_diagram_to_svg` lays a user-journey map out as a
     titled timeline of tasks grouped into sections, where each task carries a
     1-5 satisfaction score (rendered as a happy/neutral/sad face icon) and the
     actors responsible for it, mirroring grok lib.rs L124-L126.
   * The ``gitGraph`` renderer (R291) has its own dedicated arm --
     :func:`render_gitgraph_diagram_to_svg` lays branches out on horizontal
     lanes with commits as bullets and arrows encoding the three edge kinds
     (same-branch / branch-down / merge-up), mirroring grok lib.rs L122-L123.
     gitGraph is theme-aware through 4 channels (background / text_color /
     edge_color / node_fill).
   * The ``mindmap`` renderer (R292) has its own dedicated arm --
     :func:`render_mindmap_diagram_to_svg` parses an indented node tree,
     assigns each root branch a section colour from the 8-hue palette, lays the
     tree out radially around a central root, and emits an SVG with
     quadratic-bezier edges, six node shapes (default / rect / rounded-rect /
     circle / bang / hexagon), and underline decoration, mirroring grok lib.rs
     L120-L121. mindmap is NOT theme-aware -- grok hard-codes mermaid's default
     mindmap palette (navy root + 8 section hues) and ignores the resolved
     theme (the ``_theme`` param is accepted for dispatch symmetry only).
   * The ``requirementDiagram`` renderer (R294) has its own dedicated arm --
     :func:`render_requirement_diagram_to_svg` parses 7 node kinds + relations,
     hands them to the dagre layout engine (``compound: false``), and emits a
     theme-aware SVG (5 channels: background / text_color / edge_color /
     node_fill / node_stroke). It is the first per-diagram renderer that
     consumes dagre (phase 2 of direction (1)).
   * The ``erDiagram`` renderer (R295) has its own dedicated arm --
     :func:`render_er_diagram_to_svg` parses entity tables + crow's-foot
     relationships, hands them to the dagre layout engine (``compound: false``),
     and emits a theme-aware SVG with the 8 classic ER markers. It is the
     second per-diagram renderer that consumes dagre (phase 2 of direction (1)).
   * The ``sequenceDiagram`` renderer (R298) has its own dedicated arm above
     the unsupported check -- :func:`render_sequence_diagram_to_svg` emits the
     sequence / timing diagram (fixed participant columns + a 37px event-row
     grid + an activation-bar stack matched to activate/deactivate pairs + a
     fragment nesting-depth inset for alt/loop/opt/par/critical/break/rect).
     It is the LAST per-diagram renderer to ship and is dagre-FREE -- it does
     not consume the dagre layout engine, mirroring grok lib.rs L102-L104.
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

from .block_diagram import render_block_diagram_to_svg
from .c4_diagram import render_c4_diagram_to_svg
from .class_diagram import render_class_diagram_to_svg
from .config import parse_mermaid_frontmatter
from .er_diagram import render_er_diagram_to_svg
from .error import UnsupportedDiagramType
from .gantt_diagram import render_gantt_diagram_to_svg
from .gitgraph_diagram import render_gitgraph_diagram_to_svg
from .info_diagram import render_info_diagram_to_svg
from .journey_diagram import render_journey_diagram_to_svg
from .kanban_diagram import render_kanban_diagram_to_svg
from .layout import compute_layout, compute_layout_with_config
from .mindmap_diagram import render_mindmap_diagram_to_svg
from .packet_diagram import render_packet_diagram_to_svg
from .parser import parse_mermaid
from .pie_diagram import render_pie_diagram_to_svg
from .quadrant_diagram import render_quadrant_chart_to_svg
from .radar_diagram import render_radar_diagram_to_svg
from .requirement_diagram import render_requirement_diagram_to_svg
from .sankey_diagram import render_sankey_diagram_to_svg
from .sequence_diagram import render_sequence_diagram_to_svg
from .state_diagram import parse_state_diagram
from .svg_renderer import render, render_with_config
from .theme import MermaidTheme
from .timeline_diagram import render_timeline_diagram_to_svg
from .xychart_diagram import render_xychart_diagram_to_svg

__all__ = [
    "is_mermaid_diagram",
    "render_mermaid_to_svg",
    "strip_mermaid_frontmatter",
]


#: Diagram-type tokens whose dedicated renderers have NOT shipped yet.
#: Mirrors grok's per-diagram ``if`` arms (lib.rs L51-L139): every arm now
#: has a migrated renderer, so the unsupported surface is EMPTY (R298 was
#: the last leaf). The ``info`` renderer shipped in R279, the ``stateDiagram``
#: / ``stateDiagram-v2`` parser shipped in R280, the ``radar-beta`` renderer
#: shipped in R281, the ``pie`` renderer shipped in R282, the ``packet-beta``
#: renderer shipped in R283, the ``sankey-beta`` renderer shipped in R284,
#: the ``gantt`` renderer shipped in R285, the ``kanban`` renderer shipped
#: in R286, the ``timeline`` renderer shipped in R287, the ``quadrantChart``
#: renderer shipped in R288, the ``block-beta`` renderer shipped in R289,
#: the ``journey`` renderer shipped in R290, the ``gitGraph`` renderer
#: shipped in R291, the ``mindmap`` renderer shipped in R292, the
#: ``xychart-beta`` renderer shipped in R293, the ``requirementDiagram``
#: renderer shipped in R294, the ``erDiagram`` renderer shipped in R295,
#: the ``classDiagram`` renderer shipped in R296, the five ``C4*`` tokens
#: shipped in R297, and the ``sequenceDiagram`` renderer shipped in R298
#: (all their dedicated arms sit above this check, which now never fires).
_UNSUPPORTED_DIAGRAM_TYPES: frozenset[str] = frozenset()

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
    (R282) emits the pie chart via :func:`render_pie_diagram_to_svg`; the
    ``packet-beta`` renderer (R283) emits the packet diagram via
    :func:`render_packet_diagram_to_svg`; the ``sankey-beta`` renderer (R284)
    emits the sankey flow diagram via
    :func:`render_sankey_diagram_to_svg`; the ``gantt`` renderer (R285) emits
    the project-schedule gantt chart via
    :func:`render_gantt_diagram_to_svg`; the ``kanban`` renderer (R286) emits
    the board-card kanban chart via
    :func:`render_kanban_diagram_to_svg`; the ``timeline`` renderer (R287)
    emits the chronological timeline board via
    :func:`render_timeline_diagram_to_svg`; the ``quadrantChart`` renderer
    (R288) emits the four-quadrant chart via
    :func:`render_quadrant_chart_to_svg`; the ``journey`` renderer (R290)
    emits the user-journey map via :func:`render_journey_diagram_to_svg`;
    the ``gitGraph`` renderer (R291) emits the git-graph via
    :func:`render_gitgraph_diagram_to_svg`; the ``mindmap`` renderer (R292)
    emits the mind-map via :func:`render_mindmap_diagram_to_svg`; the
    ``xychart-beta`` renderer (R293) emits the cartesian line-chart via
    :func:`render_xychart_diagram_to_svg`; the ``requirementDiagram`` renderer
    (R294) emits the requirement diagram via
    :func:`render_requirement_diagram_to_svg`; the ``erDiagram`` renderer (R295)
    emits the entity-relationship diagram via
    :func:`render_er_diagram_to_svg`; the ``classDiagram`` renderer (R296)
    emits the UML class diagram via :func:`render_class_diagram_to_svg`; the
    five ``C4*`` tokens (R297) share :func:`render_c4_diagram_to_svg`; the
    ``sequenceDiagram`` renderer (R298) emits the sequence / timing diagram
    via :func:`render_sequence_diagram_to_svg` -- the LAST per-diagram leaf,
    dagre-FREE (bespoke temporal geometry). Mirrors grok lib.rs L36-L163.

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

    # ``packet-beta`` (R283): the dedicated packet-diagram renderer. Mirrors
    # grok lib.rs L74-L76 -- :func:`render_packet_diagram_to_svg` receives
    # the front-matter-stripped body and lays the contiguous bit ranges out
    # on a 32-bit-per-row grid (one ``#efefef`` rectangle per block fragment
    # with centered label + start/end bit indices). The renderer hard-codes
    # its palette and ignores the resolved theme.
    if diagram_type == "packet-beta":
        return render_packet_diagram_to_svg(body, resolved_theme)

    # ``sankey-beta`` (R284): the dedicated sankey / flow-diagram renderer.
    # Mirrors grok lib.rs L98-L100 -- :func:`render_sankey_diagram_to_svg`
    # receives the front-matter-stripped body and lays the weighted flow graph
    # out left-to-right by longest-path depth (throughput-proportional node
    # bars + flow-proportional gradient-stroked cubic-Bezier ribbons). Unlike
    # the pie / packet renderers, sankey IS theme-aware: ``theme.background``
    # flows into the SVG root style and the full-canvas background rect.
    if diagram_type == "sankey-beta":
        return render_sankey_diagram_to_svg(body, resolved_theme)

    # ``gantt`` (R285): the dedicated gantt / project-schedule renderer.
    # Mirrors grok lib.rs L74-L76 -- :func:`render_gantt_diagram_to_svg`
    # receives the front-matter-stripped body and lays the tasks out as
    # horizontal bars on a day-scaled timeline (section background bands +
    # bottom date axis with daily grid ticks + inside/outside task labels +
    # left-side section legend). Like pie / packet, gantt is NOT theme-aware
    # -- grok hard-codes mermaid 11.12.2's default gantt palette and ignores
    # the resolved theme.
    if diagram_type == "gantt":
        return render_gantt_diagram_to_svg(body, resolved_theme)

    # ``kanban`` (R286): the dedicated kanban / board-card renderer. Mirrors
    # grok lib.rs L118-L120 -- :func:`render_kanban_diagram_to_svg` receives
    # the front-matter-stripped body and lays the board out as titled columns
    # of task cards (per-column cluster rect + cluster-label + per-card node
    # with label/assigned placeholders and an optional priority indicator
    # stripe). Like sankey, kanban IS theme-aware: ``theme.text_color`` flows
    # into the root font fill + cluster/label text fills, and
    # ``theme.node_stroke`` into the node rect + ticket-link strokes (the
    # SVG-root ``background-color`` and the full-canvas background rect stay
    # hard-coded ``white``, NOT ``theme.background``).
    if diagram_type == "kanban":
        return render_kanban_diagram_to_svg(body, resolved_theme)

    # ``timeline`` (R287): the dedicated timeline / chronological renderer.
    # Mirrors grok lib.rs L108-L110 -- :func:`render_timeline_diagram_to_svg`
    # receives the front-matter-stripped body and lays the periods out as a
    # horizontal axis of nodes, each trailing a vertical stack of event cards,
    # grouped under optional colored section banners, with a left-to-right
    # direction arrow underneath. Like pie / packet / gantt, timeline is NOT
    # theme-aware -- grok hard-codes mermaid 11.12.2's default timeline palette
    # (``#333`` text + three fixed ``cScale`` 12-hue palettes) and ignores the
    # resolved theme.
    if diagram_type == "timeline":
        return render_timeline_diagram_to_svg(body, resolved_theme)

    # ``quadrantChart`` (R288): the dedicated quadrant-chart renderer. Mirrors
    # grok lib.rs L104-L106 -- :func:`render_quadrant_chart_to_svg` receives
    # the front-matter-stripped body and lays a 500x500 canvas out as four
    # labelled quadrants split by an internal cross, with optional x/y axis
    # range pairs, a title, and clamped [0,1] data points. Like sankey /
    # kanban, quadrant IS theme-aware: a dark/light palette is selected by
    # ``theme.background``'s hex prefix and ``theme.background`` flows into
    # the full-canvas background rect. Unlike the other renderers, quadrant
    # formats its dynamic coordinates with Rust ``{:.1}`` (one decimal place)
    # rather than ``Display`` -- the renderer carries its own ``_fmt1`` bridge.
    if diagram_type == "quadrantChart":
        return render_quadrant_chart_to_svg(body, resolved_theme)

    # ``block-beta`` (R289): the dedicated block-diagram renderer. Mirrors
    # grok lib.rs L90-L92 -- :func:`render_block_diagram_to_svg` receives the
    # front-matter-stripped body and lays labelled rectangular nodes out on a
    # ``columns``-driven grid connected by D3 ``curveBasis`` edges. Block is
    # the most theme-aware renderer in the family: all five ``MermaidTheme``
    # channels flow into the SVG (``background`` -> root bg color + canvas,
    # ``text_color`` -> CSS font fill + every label text, ``edge_color`` ->
    # marker / arrowhead / flowchart-link strokes, ``node_fill`` /
    # ``node_stroke`` -> the ``.node rect`` fill / stroke).
    if diagram_type == "block-beta":
        return render_block_diagram_to_svg(body, resolved_theme)

    # ``journey`` (R290): the dedicated journey-map renderer. Mirrors grok
    # lib.rs L124-L126 -- :func:`render_journey_diagram_to_svg` receives the
    # front-matter-stripped body and lays a user-journey map out as a titled
    # timeline of tasks grouped into sections (each task carries a 1-5
    # satisfaction score rendered as a happy/neutral/sad face icon and the
    # actors responsible for it). Like pie / packet / gantt / timeline,
    # journey is NOT theme-aware -- grok hard-codes mermaid 11.12.2's default
    # journey palette (``#333`` text + ``#FFF8DC`` faces + the
    # ``SECTION_FILLS`` / ``SECTION_SVG_FILLS`` / ``ACTOR_COLOURS`` tables) and
    # ignores the resolved theme.
    if diagram_type == "journey":
        return render_journey_diagram_to_svg(body, resolved_theme)

    # ``gitGraph`` (R291): the dedicated git-graph renderer. Mirrors grok
    # lib.rs L122-L123 -- :func:`render_gitgraph_diagram_to_svg` receives the
    # front-matter-stripped body and lays branches out on horizontal lanes
    # with commits as bullets and arrows encoding the three edge kinds
    # (same-branch / branch-down / merge-up). gitGraph IS theme-aware
    # (4 channels): ``background`` -> root bg, ``text_color`` -> base font
    # fill + gitTitleText + tag-hole, ``edge_color`` -> branch stroke,
    # ``node_fill`` -> tag-label-bkg + commit-merge/reverse/highlight.
    if diagram_type == "gitGraph":
        return render_gitgraph_diagram_to_svg(body, resolved_theme)

    # ``mindmap`` (R292): the dedicated mindmap renderer. Mirrors grok lib.rs
    # L120-L121 -- :func:`render_mindmap_diagram_to_svg` receives the
    # front-matter-stripped body and lays an indented node tree out radially
    # around a central root, with quadratic-bezier edges and six node shapes
    # (default / rect / rounded-rect / circle / bang / hexagon). mindmap is
    # NOT theme-aware (grok hard-codes mermaid's default palette and ignores
    # the resolved theme); the ``_theme`` param is accepted for dispatch
    # symmetry only.
    if diagram_type == "mindmap":
        return render_mindmap_diagram_to_svg(body, resolved_theme)

    # ``xychart-beta`` (R293): the dedicated cartesian line-chart renderer.
    # Mirrors grok lib.rs L94-L96 -- :func:`render_xychart_diagram_to_svg`
    # receives the front-matter-stripped body and lays a ``line`` series out on
    # a fixed 700x500 canvas with a d3-style tick layout (a ``bar`` series is
    # silently ignored -- grok ships only the ``line`` path). xychart is
    # theme-aware (2 channels): ``text_color`` -> every axis-line stroke + every
    # text fill, ``background`` -> the SVG root ``background-color`` style + the
    # ``main`` group's background rect; the series colors come from the fixed
    # Tableau-10 palette cycled by series index, NOT the theme.
    if diagram_type == "xychart-beta":
        return render_xychart_diagram_to_svg(body, resolved_theme)

    # ``requirementDiagram`` (R294): the dedicated requirement-diagram
    # renderer. Mirrors grok lib.rs L82-L84 --
    # :func:`render_requirement_diagram_to_svg` receives the front-matter-
    # stripped body and parses 7 node kinds (element / requirement /
    # functionalrequirement / interfacerequirement / performancerequirement /
    # physicalrequirement / designconstraint) + ``{ key: value }`` property
    # blocks + ``src -rel-> dst`` relations, hands the nodes + edges to the
    # dagre layout engine (``compound: false``), and emits a theme-aware SVG.
    # It is the FIRST per-diagram renderer to consume dagre (phase 2 of
    # direction (1)); every prior per-diagram leaf computed its geometry by
    # hand. requirementDiagram is theme-aware (5 channels): ``background`` ->
    # root bg style, ``text_color`` -> CSS fill + every label fill,
    # ``edge_color`` -> marker strokes + relationshipLine stroke, ``node_fill``
    # -> node rect fill, ``node_stroke`` -> node rect stroke + divider line.
    if diagram_type == "requirementDiagram":
        return render_requirement_diagram_to_svg(body, resolved_theme)

    # ``erDiagram`` (R295): the dedicated ER-diagram renderer. Mirrors grok
    # lib.rs L82-L84 -- :func:`render_er_diagram_to_svg` receives the front-
    # matter-stripped body and parses entity tables (header + ``type name``
    # attribute rows) + crow's-foot relationships (``A ||--o{ B : role``),
    # hands the nodes + edges to the dagre layout engine (``compound: false``),
    # and emits a theme-aware SVG whose edges carry the 8 classic ER markers.
    # It is the SECOND per-diagram renderer to consume dagre (phase 2 of
    # direction (1)); erDiagram is theme-aware (5 channels): ``background`` ->
    # root bg style, ``text_color`` -> CSS fill + every label fill,
    # ``edge_color`` -> marker strokes + relationshipLine stroke, ``node_fill``
    # -> entityBox fill, ``node_stroke`` -> entityBox stroke + divider lines.
    if diagram_type == "erDiagram":
        return render_er_diagram_to_svg(body, resolved_theme)

    # ``classDiagram`` (R296): the dedicated UML class-diagram renderer.
    # Mirrors grok lib.rs L51-L60 -- :func:`render_class_diagram_to_svg`
    # receives the front-matter-stripped body and parses class boxes
    # (name + optional ``<<stereotype>>`` + attribute/method member partitions)
    # + binary relationships annotated with one of eight UML relation operators,
    # hands the nodes + edges to the dagre layout engine (``compound: false``),
    # and emits a theme-aware SVG whose nodes carry the title / attribute-
    # divider / method-divider three-band layout and whose edges carry the
    # classic UML markers (extension / composition / aggregation / dependency
    # x Start/End). It is the THIRD per-diagram renderer to consume dagre
    # (phase 3 of direction (1)); classDiagram is theme-aware (4 channels):
    # ``node_fill`` -> node rect fill, ``node_stroke`` -> border + divider
    # lines, ``edge_color`` -> marker strokes + relation path stroke,
    # ``text_color`` -> CSS fill + every label fill.
    if diagram_type == "classDiagram":
        return render_class_diagram_to_svg(body, resolved_theme)

    # ``C4Context`` / ``C4Container`` / ``C4Component`` / ``C4Dynamic`` /
    # ``C4Deployment`` (R297): the dedicated C4 renderer. Mirrors grok lib.rs
    # L130-L139 -- all five C4 diagram-type tokens route to one shared
    # :func:`render_c4_diagram_to_svg`, mirroring grok's single ``c4_diagram``
    # renderer. C4 does NOT consume dagre -- it uses bespoke grid geometry
    # (a port of Mermaid's ``Bounds`` class) that self-computes shape
    # coordinates, so it shares nothing with the dagre consumers (R294
    # requirement / R295 er / R296 class). ``C4Dynamic`` is the only variant
    # with divergent render behaviour: relationship labels carry the 1-based
    # ``{index}: {label}`` prefix; the other four emit the label verbatim.
    # C4 is theme-aware (3 channels): ``background`` -> root bg style
    # (normalised ``#ffffff`` -> ``white``), ``text_color`` -> CSS ``fill``
    # (normalised ``#333333`` -> ``#333``), ``edge_color`` -> the ``.marker``
    # class fill/stroke; the shape/boundary palette is the fixed C4 palette.
    if diagram_type in (
        "C4Context",
        "C4Container",
        "C4Component",
        "C4Dynamic",
        "C4Deployment",
    ):
        return render_c4_diagram_to_svg(body, resolved_theme)

    # ``sequenceDiagram`` (R298): the dedicated sequence / timing-diagram
    # renderer. Mirrors grok lib.rs L102-L104 --
    # :func:`render_sequence_diagram_to_svg` receives the front-matter-
    # stripped body and lays the diagram out with bespoke temporal geometry
    # (fixed participant columns + a 37px event-row grid + an activation-bar
    # stack matched to activate/deactivate pairs + a fragment nesting-depth
    # inset for alt/loop/opt/par/critical/break/rect). sequenceDiagram is the
    # LAST per-diagram renderer to ship and is dagre-FREE -- it shares nothing
    # with the dagre consumers (R294 requirement / R295 er / R296 class). It is
    # theme-aware (5 channels): ``background`` -> full-canvas background rect,
    # ``node_fill`` -> participant head/footer box + activation bar + fragment
    # tab fill, ``node_stroke`` -> participant head/footer box + activation bar
    # stroke, ``edge_color`` -> lifeline + message arrow + fragment frame
    # stroke, ``text_color`` -> title + participant labels + message text +
    # fragment labels; the note palette (``#fff2b0`` fill / ``#333333`` text)
    # is hard-coded like grok.
    if diagram_type == "sequenceDiagram":
        return render_sequence_diagram_to_svg(body, resolved_theme)

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
