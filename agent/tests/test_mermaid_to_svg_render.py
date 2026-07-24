"""Black-box tests for the migrated mermaid-to-svg crate-root render dispatch
(R277).

Exercises :mod:`minimax_code.mermaid.to_svg.render` -- the crate-root public
API fused from grok ``mermaid-to-svg/src/lib.rs`` (direction (1), leaf 9). This
is the dispatch entry that turns mermaid source into an SVG string, wiring the
R269--R275c render stack (front-matter parser + theme resolver + diagram-type
token + parser/layout/renderer) behind a single :func:`render_mermaid_to_svg`
call -- mirroring grok ``lib.rs`` L36-L185.

Covers the four migrated symbols plus the module-private helper:

* :func:`first_diagram_type_token` (private; grok ``fn`` L174, no ``pub``) --
  the first whitespace token of the first non-empty / non-``%%`` line. Drives
  the dispatch; ``None`` when every line is blank or a comment.
* :func:`render_mermaid_to_svg` (grok ``pub fn`` L36) -- the dispatch:
  front-matter parse -> theme resolve (``theme`` arg > front-matter
  ``config.theme`` > default light) -> token extract -> unsupported-type raise
  vs. the flowchart default path (``parser`` -> ``layout`` -> ``svg_renderer``).
* :func:`strip_mermaid_frontmatter` (grok ``pub fn`` L170) -- body extraction.
* :func:`is_mermaid_diagram` (grok ``pub fn`` L182) -- language-tag check.

Dispatch invariants asserted (zero-semantic clone):

1. The 12 diagram-type tokens whose dedicated renderers ship in R291+ raise
   :class:`UnsupportedDiagramType` *before* the flowchart path runs -- this
   matches grok's per-diagram ``if`` arms (L51-L139). The ``info`` renderer
   shipped in R279, the ``stateDiagram`` / ``stateDiagram-v2`` parser shipped
   in R280, the ``radar-beta`` renderer shipped in R281, the ``pie`` renderer
   shipped in R282, the ``packet-beta`` renderer shipped in R283, the
   ``sankey-beta`` renderer shipped in R284, the ``gantt`` renderer shipped
   in R285, the ``kanban`` renderer shipped in R286, the ``timeline``
   renderer shipped in R287, the ``quadrantChart`` renderer shipped in
   R288, the ``block-beta`` renderer shipped in R289, and the ``journey``
   renderer shipped in R290 (their dedicated dispatch arms no longer raise);
   the 12 remaining tokens still do.
2. Unknown tokens (not in the unsupported set, not ``graph``/``flowchart``)
   fall through to the generic parser -- matching grok's unconditional
   ``parser::parse_mermaid`` at L150. A bare ``flowchart``/``graph`` token
   selects the ``_with_config`` layout/render pair; anything else selects the
   config-less pair (mirrors grok L141/L151-L161).
3. Theme priority is grok's: explicit ``theme`` arg > front-matter
   ``config.theme`` preset > :meth:`MermaidTheme.default` (light). Asserted by
   the resolved palette's ``node_fill`` hex appearing in the emitted SVG
   (``#ECECFF`` light / ``#2d2d2d`` dark).

The barrel (:mod:`minimax_code.mermaid.to_svg`) re-exports the three public
symbols; the barrel ``__all__`` grows 15 -> 18.
"""

from __future__ import annotations

import pytest

import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import MermaidTheme, UnsupportedDiagramType
from minimax_code.mermaid.to_svg import render as render_mod
from minimax_code.mermaid.to_svg.render import (
    first_diagram_type_token,
    is_mermaid_diagram,
    render_mermaid_to_svg,
    strip_mermaid_frontmatter,
)

# Light palette node fill (theme.MermaidTheme.light) -- the default theme's
# signature color, used to assert the default-theme branch fired.
_LIGHT_NODE_FILL = "#ECECFF"
# Dark palette node fill (theme.MermaidTheme.dark) -- used to assert the
# explicit-theme and front-matter-theme branches fired.
_DARK_NODE_FILL = "#2d2d2d"


# === first_diagram_type_token (private helper, grok L174) ==================


def test_first_diagram_type_token_flowchart() -> None:
    """``flowchart TD`` yields the ``flowchart`` token."""
    assert first_diagram_type_token("flowchart TD\n  A --> B") == "flowchart"


def test_first_diagram_type_token_graph() -> None:
    """``graph LR`` yields the ``graph`` token."""
    assert first_diagram_type_token("graph LR\n  A --> B") == "graph"


def test_first_diagram_type_token_pie() -> None:
    """A non-flowchart diagram token is returned verbatim (dispatch decides)."""
    assert first_diagram_type_token("pie title Pets\n  \"Dogs\" : 50") == "pie"


def test_first_diagram_type_token_skips_blank_lines() -> None:
    """Leading blank lines are skipped before the first token."""
    assert first_diagram_type_token("\n\n   \nflowchart TD\n  A") == "flowchart"


def test_first_diagram_type_token_skips_comment_lines() -> None:
    """Leading ``%%`` comment lines are skipped (mermaid comment syntax)."""
    assert (
        first_diagram_type_token("%% setup\n%% more\nflowchart TD\n  A")
        == "flowchart"
    )


def test_first_diagram_type_token_empty_returns_none() -> None:
    """An empty body yields ``None`` (no diagram-type token)."""
    assert first_diagram_type_token("") is None


def test_first_diagram_type_token_all_comments_returns_none() -> None:
    """A body of only blank / comment lines yields ``None``."""
    assert first_diagram_type_token("%% a\n\n%% b\n   ") is None


# === is_mermaid_diagram (grok L182) ========================================


def test_is_mermaid_diagram_exact_lowercase() -> None:
    """The bare ``mermaid`` language tag identifies a mermaid block."""
    assert is_mermaid_diagram("mermaid") is True


def test_is_mermaid_diagram_case_insensitive() -> None:
    """Mixed-case / upper-case tags still match (grok lowercases)."""
    assert is_mermaid_diagram("Mermaid") is True
    assert is_mermaid_diagram("MERMAID") is True


def test_is_mermaid_diagram_prefix_with_space() -> None:
    """A ``mermaid <suffix>`` tag (space separator) matches."""
    assert is_mermaid_diagram("mermaid {1}") is True
    assert is_mermaid_diagram("mermaid diagram") is True


def test_is_mermaid_diagram_rejects_no_space_suffix() -> None:
    """``mermaidchart`` (no space) is NOT a mermaid tag -- avoids false prefix."""
    assert is_mermaid_diagram("mermaidchart") is False


def test_is_mermaid_diagram_rejects_unrelated() -> None:
    """An unrelated language tag does not match."""
    assert is_mermaid_diagram("python") is False
    assert is_mermaid_diagram("rust") is False


def test_is_mermaid_diagram_empty() -> None:
    """An empty string is not a mermaid tag."""
    assert is_mermaid_diagram("") is False


# === strip_mermaid_frontmatter (grok L170) =================================


def test_strip_mermaid_frontmatter_no_block_returns_verbatim() -> None:
    """With no ``---`` fence the whole source is the body, verbatim."""
    source = "flowchart TD\n  A --> B"
    assert strip_mermaid_frontmatter(source) == source


def test_strip_mermaid_frontmatter_strips_yaml_block() -> None:
    """A ``---...---`` front-matter block is stripped, leaving the body."""
    source = "---\ntitle: Demo\n---\nflowchart TD\n  A --> B"
    assert strip_mermaid_frontmatter(source) == "flowchart TD\n  A --> B"


# === render_mermaid_to_svg: unsupported-diagram dispatch (grok L51-L139) ===


@pytest.mark.parametrize(
    "diagram_type",
    [
        "erDiagram",
        "sequenceDiagram",
        "classDiagram",
        "mindmap",
        "gitGraph",
        "C4Context",
        "C4Container",
        "C4Component",
        "C4Dynamic",
        "C4Deployment",
        "requirementDiagram",
        "xychart-beta",
    ],
)
def test_render_mermaid_to_svg_unsupported_type_raises(diagram_type: str) -> None:
    """Each of the 12 R291+ diagram tokens raises before the flowchart path.

    Mirrors grok's per-diagram ``if`` arms (lib.rs L51-L139): the dedicated
    renderer is not migrated yet, so dispatch reports the type as
    unsupported. The ``info`` renderer (R279), the ``stateDiagram`` /
    ``stateDiagram-v2`` parser (R280), the ``radar-beta`` renderer (R281),
    the ``pie`` renderer (R282), the ``packet-beta`` renderer (R283), the
    ``sankey-beta`` renderer (R284), the ``gantt`` renderer (R285), the
    ``kanban`` renderer (R286), the ``timeline`` renderer (R287), the
    ``quadrantChart`` renderer (R288), the ``block-beta`` renderer
    (R289), and the ``journey`` renderer (R290) are asserted separately;
    none raises here. The raised
    :class:`UnsupportedDiagramType` carries the diagram-type token verbatim.
    """
    source = f"{diagram_type}\n  body"
    with pytest.raises(UnsupportedDiagramType) as exc_info:
        render_mermaid_to_svg(source)
    assert exc_info.value.diagram_type == diagram_type
    assert str(exc_info.value) == f"Unsupported diagram type: {diagram_type}"


# === render_mermaid_to_svg: flowchart default path (grok L150-L162) ========


def test_render_mermaid_to_svg_flowchart_returns_svg() -> None:
    """A ``flowchart`` diagram renders through the dagre stack to an SVG."""
    svg = render_mermaid_to_svg("flowchart TD\n  A --> B")
    assert isinstance(svg, str)
    assert "<svg" in svg
    assert "</svg>" in svg


def test_render_mermaid_to_svg_graph_token_returns_svg() -> None:
    """The ``graph`` token also routes through the flowchart stack."""
    svg = render_mermaid_to_svg("graph LR\n  A --> B")
    assert isinstance(svg, str)
    assert "<svg" in svg


# === render_mermaid_to_svg: theme-priority dispatch (grok L40-L49) =========


def test_render_mermaid_to_svg_explicit_theme_wins() -> None:
    """An explicit ``theme`` arg overrides front-matter and the default.

    Asserted via the dark palette's ``node_fill`` hex appearing in the SVG
    (grok's resolved theme flows into :meth:`svg_renderer.render_with_config`).
    """
    svg = render_mermaid_to_svg("flowchart TD\n  A --> B", theme=MermaidTheme.dark())
    assert _DARK_NODE_FILL in svg


def test_render_mermaid_to_svg_default_theme_when_none() -> None:
    """With no theme arg and no front-matter theme, the default light fires."""
    svg = render_mermaid_to_svg("flowchart TD\n  A --> B")
    assert _LIGHT_NODE_FILL in svg


def test_render_mermaid_to_svg_frontmatter_theme_when_no_explicit() -> None:
    """A front-matter ``config.theme`` preset resolves when no arg is passed.

    Mirrors grok L43-L46: ``theme`` arg is ``None`` -> fall to the parsed
    ``config`` block's ``to_mermaid_theme`` -> fall to default. The dark
    preset in the front-matter wins over the default-light palette.
    """
    source = "---\nconfig:\n  theme: dark\n---\nflowchart TD\n  A --> B"
    svg = render_mermaid_to_svg(source)
    assert _DARK_NODE_FILL in svg


def test_render_mermaid_to_svg_explicit_theme_overrides_frontmatter() -> None:
    """The explicit ``theme`` arg beats a conflicting front-matter theme.

    Front-matter says dark, the arg says light -> light wins (priority order).
    """
    source = "---\nconfig:\n  theme: dark\n---\nflowchart TD\n  A --> B"
    svg = render_mermaid_to_svg(source, theme=MermaidTheme.light())
    assert _LIGHT_NODE_FILL in svg


# === render_mermaid_to_svg: front-matter stripping before dispatch =========


def test_render_mermaid_to_svg_strips_frontmatter_before_parse() -> None:
    """A front-matter block (no theme) is stripped before the body is parsed.

    Without stripping, the ``---`` fence would reach the flowchart parser and
    break it; the rendered SVG proves dispatch saw the clean body.
    """
    source = "---\ntitle: Demo\n---\nflowchart TD\n  A --> B"
    svg = render_mermaid_to_svg(source)
    assert "<svg" in svg
    assert _LIGHT_NODE_FILL in svg


# === barrel + module surface ==============================================


def test_render_module_all_is_three_public_symbols_sorted() -> None:
    """The render leaf exports exactly the 3 grok crate-root public symbols."""
    assert render_mod.__all__ == [
        "is_mermaid_diagram",
        "render_mermaid_to_svg",
        "strip_mermaid_frontmatter",
    ]


def test_to_svg_barrel_reexports_render_symbols() -> None:
    """The barrel re-exports the 3 R277 symbols (``__all__`` 15 -> 18)."""
    assert "render_mermaid_to_svg" in to_svg.__all__
    assert "strip_mermaid_frontmatter" in to_svg.__all__
    assert "is_mermaid_diagram" in to_svg.__all__
    assert len(to_svg.__all__) == 18
    # Barrel symbols are the same objects the leaf exports (re-export, not copy).
    assert to_svg.render_mermaid_to_svg is render_mermaid_to_svg
    assert to_svg.strip_mermaid_frontmatter is strip_mermaid_frontmatter
    assert to_svg.is_mermaid_diagram is is_mermaid_diagram
