"""Functional clone of grok's ``gitgraph_diagram.rs`` (direction (1) brick 22).

This is the 13th per-diagram leaf and the 12th **self-contained SVG emitter**
in the R269--R290 stack (flowchart uses the dagre layout + renderer pair;
every diagram below instead owns its own parser+layout+renderer in one file,
mirroring grok's monolithic per-diagram modules). It turns a ``gitGraph``
mermaid block into an SVG string -- branches laid out on horizontal lanes,
commits as bullets, and arrows encoding the three edge kinds
(same-branch / branch-down / merge-up).

Layout (mirrors grok ``render_gitgraph_diagram_to_svg`` L43--L312, in order):

1. ``_parse_gitgraph`` (grok L411) -- line-oriented state machine over the
   four commands (``commit`` / ``branch`` / ``checkout`` / ``merge``). Builds
   the ``_GitGraph`` model: an ordered commit list with parent ids, a branch
   registration order, and a head pointer. The first non-blank / non-``%%``
   line must be the ``gitGraph`` header.
2. Geometry -- every commit is placed at ``_commit_x(seq)`` on the lane of its
   branch (``y = idx * BRANCH_Y_GAP``). The view box is the union of the branch
   label rectangles (top-left) and the commit label rectangles (bottom-right).
3. SVG emission -- style block + five ``<g>`` groups (branches+labels,
   commit-arrows, commit-bullets, commit-labels) plus the two placeholder
   groups grok emits up front (``commit-bullets`` / ``commit-labels`` are
   re-opened later; the leading empty ``<g/>`` is a grok artefact).

Theme awareness (4 channels, grok L329--L387):

* ``background`` -- the ``<svg>`` ``style`` background colour.
* ``text_color`` -- ``#my-svg`` base fill, ``.gitTitleText`` fill, ``.tag-hole``
  fill.
* ``edge_color`` -- ``.branch`` stroke.
* ``node_fill`` -- ``.tag-label-bkg``, ``.commit-merge``, ``.commit-reverse``,
  ``.commit-highlight-inner`` fill.

Float-formatting bridge: grok formats every coordinate with ``format!("{}", f)``
(Rust ``f64::Display``), which drops the trailing ``.0`` of integer-valued
floats (``10.0`` -> ``"10"``) and prints the shortest round-trip repr
otherwise. Commit-label widths pass through ``line_width`` with
``GITGRAPH_CHAR_WIDTH = 9.5`` and are rescaled by ``COMMIT_LABEL_FONT_SIZE /
16.0 = 0.625``, producing ``.5`` / ``.375`` fractions, so the same Display
semantics are needed here. ``_fmt`` mirrors block_diagram's R289 helper.

XML escaping: grok ``escape_xml`` (L570) maps ``'`` to the **named entity**
``&apos;`` -- the variant shared with pie / packet / gantt / kanban / timeline
/ quadrant / block / journey. ``_escape_xml`` mirrors that variant verbatim
(radar / sankey use the numeric ``&#39;`` variant instead).

Public surface (1 symbol): :func:`render_gitgraph_diagram_to_svg`. The leaf is
reachable only through :mod:`minimax_code.mermaid.to_svg.render` dispatch (the
``"gitGraph"`` arm); it does not enter the ``to_svg`` barrel ``__all__``.

References to grok line numbers throughout this module point at
``grok-build/third_party/mermaid-to-svg/src/gitgraph_diagram.rs`` (576 lines).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .error import ParseError
from .text_wrap import line_width
from .theme import MermaidTheme

# --- Layout constants (grok L24--L41, in source order) ---------------------
LAYOUT_OFFSET: float = 10.0
COMMIT_STEP: float = 40.0
PX: float = 4.0
PY: float = 2.0
GITGRAPH_CHAR_WIDTH: float = 9.5
BRANCH_Y_GAP: float = 90.0
COMMIT_RADIUS: float = 10.0
MERGE_OUTER_RADIUS: float = 9.0
MERGE_INNER_RADIUS: float = 6.0
ARROW_STROKE_WIDTH: float = 8.0
TURN_RADIUS: float = 20.0
THEME_COLOR_LIMIT: int = 8
BRANCH_LABEL_BG_PADDING: float = 18.0
BRANCH_LABEL_BG_X_TRANSLATE: float = -19.0
BRANCH_LABEL_BG_Y: float = -1.5
BRANCH_LABEL_BG_HEIGHT: float = 23.0
VIEWBOX_MARGIN: float = 8.0
COMMIT_LABEL_FONT_SIZE: float = 10.0
COMMIT_LABEL_RECT_HEIGHT: float = 15.0
COMMIT_LABEL_RECT_Y_OFFSET: float = 13.5
COMMIT_LABEL_TEXT_Y_OFFSET: float = 25.0

# Fraction of the 16px-default ``line_width`` that a 10px commit label occupies.
_COMMIT_LABEL_WIDTH_SCALE: float = COMMIT_LABEL_FONT_SIZE / 16.0  # = 0.625

# 8-branch HSL palette (grok ``branch_color`` L547). Index 8+ collapses onto
# index 4's hue (the default branch).
_BRANCH_COLORS: tuple[str, ...] = (
    "hsl(240, 100%, 46.2745098039%)",
    "hsl(60, 100%, 43.5294117647%)",
    "hsl(80, 100%, 46.2745098039%)",
    "hsl(210, 100%, 46.2745098039%)",
    "hsl(180, 100%, 46.2745098039%)",
    "hsl(150, 100%, 46.2745098039%)",
    "hsl(300, 100%, 46.2745098039%)",
    "hsl(0, 100%, 46.2745098039%)",
)
_BRANCH_DEFAULT_COLOR: str = "hsl(180, 100%, 46.2745098039%)"

# Branch-label text fill: white on the two pale-on-dark hues (0, 3), black
# elsewhere (grok ``branch_label_color`` L561).
_WHITE_BRANCH_INDICES: frozenset[int] = frozenset({0, 3})


def _fmt(value: float) -> str:
    """Mirror Rust ``f64::Display`` for layout-scale coordinates.

    Integer-valued floats drop the trailing ``.0`` (``10.0`` -> ``"10"``);
    non-integers use the shortest round-trip repr (Python ``repr``), matching
    Rust's ``{}`` format on the ``.5`` / ``.375`` fractions produced by
    ``line_width``. Identical to block_diagram's R289 helper -- duplicated
    here so the leaf stays self-contained (no cross-leaf import for a 3-line
    bridge).
    """
    ivalue = int(value)
    if value == ivalue:
        return str(ivalue)
    return repr(value)


def _escape_xml(s: str) -> str:
    """Escape the five XML-significant characters (grok ``escape_xml`` L570).

    ``&`` is replaced first; ``'`` maps to the named entity ``&apos;`` -- the
    variant shared with pie / packet / gantt / kanban / timeline / quadrant /
    block / journey (radar / sankey use ``&#39;``).
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


class _CommitKind(Enum):
    """A normal single-parent commit vs. a merge commit (>=2 parents)."""

    NORMAL = "normal"
    MERGE = "merge"


@dataclass
class _Commit:
    """One parsed commit: ordinal, owning branch, parent commit ids, kind."""

    seq: int
    branch: str
    parents: list[str] = field(default_factory=list)
    kind: _CommitKind = _CommitKind.NORMAL


@dataclass
class _GitGraph:
    """Parsed graph: branch registration order + ordered commits + id index."""

    main_branch: str
    branch_order: list[str] = field(default_factory=list)
    commits: list[_Commit] = field(default_factory=list)
    commit_by_id: dict[str, int] = field(default_factory=dict)


def _commit_x(seq: int) -> float:
    """Horizontal pixel of a commit's bullet (grok ``commit_x`` L313)."""
    return seq * (COMMIT_STEP + LAYOUT_OFFSET) + LAYOUT_OFFSET


def _commit_label_text(seq: int) -> str:
    """Synthetic commit id ``"{seq}-{hash:07x}"`` (grok L318).

    Bridges grok's Rust hash ``((seq as u32).wrapping_mul(0x9E37_79B9) ^
    0x079A_D076) & 0x0FFF_FFFF``: Python has no fixed-width ``u32`` mul, so we
    mask to 32 bits *before* the XOR (Rust's ``wrapping_mul`` discards overflow
    above 2**32; XOR-ing afterwards stays in-range). The final ``& 0x0FFFFFFF``
    caps the value at 7 hex digits for the ``:07x`` format.
    """
    hashed = (((seq * 0x9E3779B9) & 0xFFFFFFFF) ^ 0x079AD076) & 0x0FFFFFFF
    return f"{seq}-{hashed:07x}"


def _commit_id_class(seq: int) -> str:
    """The CSS class fragment carried by each commit bullet (grok L324).

    Grok reuses the synthetic id text as the per-commit class, so a bullet's
    ``class`` attribute reads ``commit <id> commit<N>``.
    """
    return _commit_label_text(seq)


def _branch_color(order: int) -> str:
    """Lane stroke / bullet fill for the Nth registered branch (grok L547).

    Indices 0..7 map to the 8-hue palette; index 8+ collapses onto index 4's
    hue (the default), matching grok's fallthrough arm.
    """
    if 0 <= order < len(_BRANCH_COLORS):
        return _BRANCH_COLORS[order]
    return _BRANCH_DEFAULT_COLOR


def _branch_label_color(order: int) -> str:
    """Text fill for the Nth branch label (grok L561).

    White on the two pale-on-dark hues (0, 3); black elsewhere.
    """
    return "#ffffff" if order in _WHITE_BRANCH_INDICES else "black"


def _parse_gitgraph(input_str: str) -> _GitGraph:
    """Parse a ``gitGraph`` block into a :class:`_GitGraph` (grok L411).

    Four commands drive a head-pointer state machine:

    * ``commit``        -- append a Normal commit whose parent is the current
      head; advance head to the new commit id.
    * ``branch <name>`` -- register ``<name>`` (forked from the current head)
      without switching to it.
    * ``checkout <name>`` -- switch the current branch (registering it first if
      unseen) and reload its head.
    * ``merge <name>``  -- append a Merge commit whose parents are the current
      head *and* ``<name>``'s head; advance head.

    The first non-blank / non-``%%`` line must be the bare ``gitGraph``
    header; any other leading token (or a missing header at EOF) raises
    :class:`ParseError`. Unknown commands and commands missing their branch
    argument raise likewise.
    """
    main_branch = "main"
    branch_order: list[str] = [main_branch]
    branches: dict[str, str | None] = {main_branch: None}
    current_branch = main_branch
    head: str | None = None
    commits: list[_Commit] = []
    commit_by_id: dict[str, int] = {}
    found_header = False

    for idx, raw in enumerate(input_str.splitlines()):
        line_no = idx + 1
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue

        parts = line.split()

        if not found_header:
            if parts[0] != "gitGraph":
                raise ParseError(line_no, "Expected 'gitGraph' declaration")
            found_header = True
            continue

        cmd = parts[0]

        if cmd == "commit":
            seq = len(commits)
            cid = str(seq)
            commits.append(
                _Commit(
                    seq=seq,
                    branch=current_branch,
                    parents=[head] if head is not None else [],
                    kind=_CommitKind.NORMAL,
                )
            )
            commit_by_id[cid] = seq
            head = cid
            branches[current_branch] = cid
        elif cmd == "branch":
            if len(parts) < 2:
                raise ParseError(line_no, "Expected branch name after 'branch'")
            name = parts[1]
            if name not in branches:
                branches[name] = head
                branch_order.append(name)
        elif cmd == "checkout":
            if len(parts) < 2:
                raise ParseError(line_no, "Expected branch name after 'checkout'")
            name = parts[1]
            if name not in branches:
                branches[name] = head
                branch_order.append(name)
            current_branch = name
            head = branches.get(name)
        elif cmd == "merge":
            if len(parts) < 2:
                raise ParseError(line_no, "Expected branch name after 'merge'")
            other = parts[1]
            other_head = branches.get(other)
            parents: list[str] = []
            if head is not None:
                parents.append(head)
            if other_head is not None:
                parents.append(other_head)
            seq = len(commits)
            cid = str(seq)
            commits.append(
                _Commit(
                    seq=seq,
                    branch=current_branch,
                    parents=parents,
                    kind=_CommitKind.MERGE,
                )
            )
            commit_by_id[cid] = seq
            head = cid
            branches[current_branch] = cid
        else:
            raise ParseError(line_no, f"Unrecognized gitGraph command: {cmd}")

    if not found_header:
        raise ParseError(1, "Expected 'gitGraph' declaration")

    return _GitGraph(
        main_branch=main_branch,
        branch_order=branch_order,
        commits=commits,
        commit_by_id=commit_by_id,
    )


def _emit_style_block(svg: list[str], theme: MermaidTheme) -> None:
    """Append the ``<style>`` block (grok ``emit_style_block`` L329).

    The block carries the 4 theme channels (text fill, branch stroke, node
    fill for merge / tag bkg, background via the ``<svg>`` element) plus the
    per-branch palette expansion (``branch-label{i}`` / ``commit{i}`` /
    ``commit-highlight{i}`` / ``label{i}`` / ``arrow{i}`` for i in 0..7).
    """
    svg.append(
        '<style>#my-svg{font-family:"trebuchet ms",verdana,arial,sans-serif;'
        f'font-size:16px;fill:{theme.text_color};}}'
    )
    svg.append(
        "#my-svg .commit-id,#my-svg .commit-msg,#my-svg .branch-label"
        "{fill:lightgrey;color:lightgrey;font-family:'trebuchet ms',"
        "verdana,arial,sans-serif;font-family:var(--mermaid-font-family);}"
    )
    for i in range(THEME_COLOR_LIMIT):
        color = _branch_color(i)
        label_color = _branch_label_color(i)
        svg.append(f"#my-svg .branch-label{i}{{fill:{label_color};}}")
        svg.append(f"#my-svg .commit{i}{{stroke:{color};fill:{color};}}")
        svg.append(f"#my-svg .commit-highlight{i}{{stroke:{color};fill:{color};}}")
        svg.append(f"#my-svg .label{i}{{fill:{color};}}")
        svg.append(f"#my-svg .arrow{i}{{stroke:{color};}}")
    svg.append(
        f"#my-svg .branch{{stroke-width:1;stroke:{theme.edge_color};stroke-dasharray:2;}}"
    )
    svg.append("#my-svg .commit-label{font-size:10px;fill:#000021;}")
    svg.append("#my-svg .commit-label-bkg{font-size:10px;fill:#ffffde;opacity:0.5;}")
    svg.append("#my-svg .tag-label{font-size:10px;fill:#131300;}")
    svg.append(
        f"#my-svg .tag-label-bkg{{fill:{theme.node_fill};"
        "stroke:hsl(240, 60%, 86.2745098039%);}}"
    )
    svg.append(f"#my-svg .tag-hole{{fill:{theme.text_color};}}")
    svg.append(f"#my-svg .commit-merge{{stroke:{theme.node_fill};fill:{theme.node_fill};}}")
    svg.append(
        f"#my-svg .commit-reverse{{stroke:{theme.node_fill};fill:{theme.node_fill};"
        "stroke-width:3;}}"
    )
    svg.append(
        f"#my-svg .commit-highlight-inner{{stroke:{theme.node_fill};fill:{theme.node_fill};}}"
    )
    svg.append(
        f"#my-svg .arrow{{stroke-width:{_fmt(ARROW_STROKE_WIDTH)};stroke-linecap:round;"
        "fill:none;}}"
    )
    svg.append(
        f"#my-svg .gitTitleText{{text-anchor:middle;font-size:18px;fill:{theme.text_color};}}"
    )
    svg.append("</style>")


def render_gitgraph_diagram_to_svg(
    mermaid_source: str,
    theme: MermaidTheme,
) -> str:
    """Render a ``gitGraph`` mermaid block to an SVG string (grok L43).

    Theme-aware (4 channels -- see the module docstring); the ``theme`` arg is
    consumed by :func:`_emit_style_block` and the ``<svg>`` background.
    """
    graph = _parse_gitgraph(mermaid_source)

    # Pre-compute the lane index and y for each registered branch.
    branch_index = {name: idx for idx, name in enumerate(graph.branch_order)}
    y_for_branch: dict[str, float] = {
        name: idx * BRANCH_Y_GAP for idx, name in enumerate(graph.branch_order)
    }

    # Branch-label widths (16px-default ``line_width`` of the branch name).
    branch_bbox_widths = [
        line_width(name, GITGRAPH_CHAR_WIDTH) for name in graph.branch_order
    ]
    # grok sizes the label ``<g>`` to a 19px-tall bounding box (L71).
    bbox_height = 19.0

    commits = graph.commits
    max_pos = len(commits) * (COMMIT_STEP + LAYOUT_OFFSET) if commits else 0.0

    # --- View box (grok L77--L116) -----------------------------------------
    min_x = 0.0
    min_y = 0.0
    max_x = max_pos
    y_end = (len(graph.branch_order) - 1) * BRANCH_Y_GAP
    max_y = y_end + COMMIT_RADIUS

    for idx, branch in enumerate(graph.branch_order):
        y = y_for_branch[branch]
        text_w = branch_bbox_widths[idx]
        bg_x = -(text_w + PX + 30.0)
        bg_translate_y = y - bbox_height / 2.0
        label_left = BRANCH_LABEL_BG_X_TRANSLATE + bg_x
        label_top = bg_translate_y + BRANCH_LABEL_BG_Y
        if label_left < min_x:
            min_x = label_left
        if label_top < min_y:
            min_y = label_top

    for commit in commits:
        if commit.kind != _CommitKind.NORMAL:
            continue
        y = y_for_branch.get(commit.branch)
        if y is None:
            continue
        label_text = _commit_label_text(commit.seq)
        text_w = line_width(label_text, GITGRAPH_CHAR_WIDTH) * _COMMIT_LABEL_WIDTH_SCALE
        r_y = 10.0 + text_w / 25.0 * 8.5
        label_bottom = y + r_y + COMMIT_LABEL_RECT_Y_OFFSET + COMMIT_LABEL_RECT_HEIGHT
        if label_bottom > max_y:
            max_y = label_bottom

    vb_x = min_x - VIEWBOX_MARGIN
    vb_y = min_y - VIEWBOX_MARGIN
    vb_w = (max_x - min_x) + 2.0 * VIEWBOX_MARGIN
    vb_h = (max_y - min_y) + 2.0 * VIEWBOX_MARGIN

    svg: list[str] = []
    svg.append(
        '<svg id="my-svg" width="100%" xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'style="max-width: {_fmt(vb_w)}px; background-color: {theme.background};" '
        f'viewBox="{_fmt(vb_x)} {_fmt(vb_y)} {_fmt(vb_w)} {_fmt(vb_h)}" '
        'role="graphics-document document" aria-roledescription="gitGraph">'
    )

    _emit_style_block(svg, theme)

    # Leading placeholder groups (grok L127--L129 artefacts).
    svg.append("<g/>")
    svg.append('<g class="commit-bullets"/>')
    svg.append('<g class="commit-labels"/>')

    # --- Branches + their labels (grok L132--L162) -------------------------
    svg.append("<g>")
    for idx, branch in enumerate(graph.branch_order):
        y = y_for_branch[branch]
        svg.append(
            f'<line x1="0" y1="{_fmt(y)}" x2="{_fmt(max_pos)}" y2="{_fmt(y)}" '
            f'class="branch branch{idx}"/>'
        )
        text_w = branch_bbox_widths[idx]
        bg_w = text_w + BRANCH_LABEL_BG_PADDING
        bg_x = -(text_w + PX + 30.0)
        bg_translate_y = y - bbox_height / 2.0
        svg.append(
            f'<rect class="branchLabelBkg label{idx}" rx="4" ry="4" '
            f'x="{_fmt(bg_x)}" y="{_fmt(BRANCH_LABEL_BG_Y)}" '
            f'width="{_fmt(bg_w)}" height="{_fmt(BRANCH_LABEL_BG_HEIGHT)}" '
            f'transform="translate({_fmt(BRANCH_LABEL_BG_X_TRANSLATE)}, '
            f'{_fmt(bg_translate_y)})"/>'
        )
        label_x = -(text_w + 14.0 + 30.0)
        label_y = y - bbox_height / 2.0 - 1.0
        svg.append('<g class="branchLabel">')
        svg.append(
            f'<g class="label branch-label{idx}" '
            f'transform="translate({_fmt(label_x)}, {_fmt(label_y)})">'
            '<text><tspan xml:space="preserve" dy="1em" x="0" '
            f'class="row">{_escape_xml(branch)}</tspan></text></g>'
        )
        svg.append("</g>")
    svg.append("</g>")

    # --- Commit arrows (grok L164--L224) -----------------------------------
    svg.append('<g class="commit-arrows">')
    for commit in commits:
        x = _commit_x(commit.seq)
        y = y_for_branch.get(commit.branch, 0.0)
        commit_branch_idx = branch_index.get(commit.branch, 0)
        for parent in commit.parents:
            if parent not in graph.commit_by_id:
                continue
            parent_commit = commits[graph.commit_by_id[parent]]
            px = _commit_x(parent_commit.seq)
            py = y_for_branch.get(parent_commit.branch, 0.0)
            if commit.kind == _CommitKind.MERGE:
                arrow_idx = branch_index.get(parent_commit.branch, 0)
            else:
                arrow_idx = commit_branch_idx
            cls = f"arrow arrow{arrow_idx}"
            if abs(py - y) < 0.1:
                # Same lane: a straight segment.
                svg.append(
                    f'<path d="M {_fmt(px)} {_fmt(py)} L {_fmt(x)} {_fmt(y)}" '
                    f'class="{cls}"/>'
                )
            elif y > py:
                # Branches downward: vertical, arc right, horizontal to target.
                bend_y = y - TURN_RADIUS
                arc_end_x = px + TURN_RADIUS
                svg.append(
                    f'<path d="M {_fmt(px)} {_fmt(py)} L {_fmt(px)} {_fmt(bend_y)} '
                    f"A {_fmt(TURN_RADIUS)} {_fmt(TURN_RADIUS)}, 0, 0, 0, "
                    f'{_fmt(arc_end_x)} {_fmt(y)} L {_fmt(x)} {_fmt(y)}" class="{cls}"/>'
                )
            else:
                # Merges upward: horizontal, arc up, vertical to target.
                bend_x = x - TURN_RADIUS
                arc_end_y = py - TURN_RADIUS
                svg.append(
                    f'<path d="M {_fmt(px)} {_fmt(py)} L {_fmt(bend_x)} {_fmt(py)} '
                    f"A {_fmt(TURN_RADIUS)} {_fmt(TURN_RADIUS)}, 0, 0, 0, "
                    f'{_fmt(x)} {_fmt(arc_end_y)} L {_fmt(x)} {_fmt(y)}" class="{cls}"/>'
                )
    svg.append("</g>")

    # --- Commit bullets (grok L227--L258) ----------------------------------
    svg.append('<g class="commit-bullets">')
    for commit in commits:
        x = _commit_x(commit.seq)
        y = y_for_branch.get(commit.branch, 0.0)
        branch_idx = branch_index.get(commit.branch, 0)
        id_class = _commit_id_class(commit.seq)
        if commit.kind == _CommitKind.MERGE:
            svg.append(
                f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="{_fmt(MERGE_OUTER_RADIUS)}" '
                f'class="commit {id_class} commit{branch_idx}"/>'
            )
            svg.append(
                f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="{_fmt(MERGE_INNER_RADIUS)}" '
                f'class="commit commit-merge {id_class} commit{branch_idx}"/>'
            )
        else:
            svg.append(
                f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="{_fmt(COMMIT_RADIUS)}" '
                f'class="commit {id_class} commit{branch_idx}"/>'
            )
    svg.append("</g>")

    # --- Commit labels (Normal commits only, grok L261--L305) --------------
    svg.append('<g class="commit-labels">')
    for commit in commits:
        if commit.kind != _CommitKind.NORMAL:
            continue
        y = y_for_branch.get(commit.branch)
        if y is None:
            continue
        pos = commit.seq * (COMMIT_STEP + LAYOUT_OFFSET)
        x = _commit_x(commit.seq)
        label_text = _commit_label_text(commit.seq)
        text_w = line_width(label_text, GITGRAPH_CHAR_WIDTH) * _COMMIT_LABEL_WIDTH_SCALE
        rect_w = text_w + 2.0 * PY
        rect_x = x - text_w / 2.0 - PY
        rect_y = y + COMMIT_LABEL_RECT_Y_OFFSET
        text_x = x - text_w / 2.0
        text_y = y + COMMIT_LABEL_TEXT_Y_OFFSET
        r_x = -7.5 - (text_w + 10.0) / 25.0 * 9.5
        r_y = 10.0 + text_w / 25.0 * 8.5
        svg.append(
            f'<g transform="translate({_fmt(r_x)}, {_fmt(r_y)}) '
            f'rotate(-45, {_fmt(pos)}, {_fmt(y)})">'
        )
        svg.append(
            f'<rect class="commit-label-bkg" x="{_fmt(rect_x)}" y="{_fmt(rect_y)}" '
            f'width="{_fmt(rect_w)}" height="{_fmt(COMMIT_LABEL_RECT_HEIGHT)}"/>'
        )
        svg.append(
            f'<text x="{_fmt(text_x)}" y="{_fmt(text_y)}" '
            f'class="commit-label">{_escape_xml(label_text)}</text>'
        )
        svg.append("</g>")
    svg.append("</g>")

    svg.append("</svg>")
    return "".join(svg)


__all__ = ["render_gitgraph_diagram_to_svg"]
