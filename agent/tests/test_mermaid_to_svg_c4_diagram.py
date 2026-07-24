"""Black-box tests for the migrated C4 diagram renderer (R297).

Exercises :mod:`minimax_code.mermaid.to_svg.c4_diagram` -- the single shared
renderer fused from grok ``mermaid-to-svg/src/c4_diagram.rs`` (direction (1),
brick 28). This is the 18th self-contained SVG emitter of the bespoke-geometry
family (R279-R296); C4 does **not** consume dagre -- it computes its own grid
coordinates via a port of Mermaid's ``Bounds`` class. One public entry
(:func:`render_c4_diagram_to_svg`, grok L786) serves all five diagram-type
tokens (``C4Context`` / ``C4Container`` / ``C4Component`` / ``C4Dynamic`` /
``C4Deployment``); the five tokens are matched at the dispatch layer
(``render.py`` mirrors grok ``lib.rs`` L130-L139) and only ``C4Dynamic`` has
divergent render behaviour (relationship labels carry the 1-based
``{index}: {label}`` prefix, grok L1149-L1152).

Functional contracts asserted (zero-semantic clone, Pythonic shape):

1. **Colour mapping** (grok ``bg_color_for`` / ``border_color_for`` L23-L73) --
   20 lowercase keys each (person x2 + each of system/container/component x6:
   base + external twin + ``_db`` pair + ``_queue`` pair) and a ``#1168BD`` /
   ``#3C7FC0`` default. ``type_c4`` is stored **lowercase** (``"system"`` not ``"System"``)
   so the dict lookup hits -- mirrors grok L25/L442/L461.
2. **Text helpers** -- ``_escape_xml`` emits the ``&quot;`` named entity for the
   double quote (distinct from er's ``&apos;`` and class's ``&#39;``);
   ``_estimate_text_width`` is byte-accurate (``len(text.encode('utf-8'))``)
   so non-ASCII labels measure like Rust ``str::len()``; ``_fmt`` strips the
   trailing ``.0``.
3. **Parser** (grok L371-L693) -- line-oriented state machine: the first
   non-blank / non-``%%`` line must be a C4 variant header (else
   :class:`ParseError`); ``title`` sets the title; ``System(alias, "Name")``
   is a 3-arg shape (no techn) while ``Container(alias, "Name", "Techn")`` is
   4-arg (with techn); ``Rel`` / ``BiRel`` collect relationships;
   ``System_Boundary`` opens a nesting scope (``parent_boundary`` tracks the
   owning alias); the ``Update*Style`` / ``UpdateLayoutConfig`` calls are
   ignored.
4. **Bounds grid layout** (grok L127-L210) -- ``insert`` places one shape,
   doubling the inter-shape margin after the first in a row and wrapping to a
   fresh row when ``next_cnt > C4_SHAPE_IN_ROW (4)`` or the width limit is
   breached (counter resets to 1 on wrap).
5. **Rendering** -- the SVG root carries ``id="my-svg"``; ``person`` /
   ``external_person`` shapes inject their 48x48 PNG icon (``data:image/png``);
   ``C4Dynamic`` prefixes the 1-based index to relationship labels; a non-empty
   ``techn`` emits an italic ``[techn]`` subtitle under the rel label.
6. **Dispatch integration** -- all five ``C4*`` tokens route through the C4
   renderer via :func:`render_mermaid_to_svg` (the crate-root dispatch); none
   raises :class:`UnsupportedDiagramType`.
7. **bucket-guard sync** -- the five ``C4*`` tokens were removed from
   ``render._UNSUPPORTED_DIAGRAM_TYPES`` (6 -> 1); R298 then removed
   ``sequenceDiagram`` (1 -> 0), so the unsupported surface is EMPTY.
"""

from __future__ import annotations

import pytest

import minimax_code.mermaid.to_svg.c4_diagram as c4_mod
from minimax_code.mermaid.to_svg import MermaidTheme
from minimax_code.mermaid.to_svg import render as render_mod
from minimax_code.mermaid.to_svg.c4_diagram import (
    _BG_COLOR_FOR,
    _BORDER_COLOR_FOR,
    _DEFAULT_BG_COLOR,
    _DEFAULT_BORDER_COLOR,
    _bg_color_for,
    _border_color_for,
    _Bounds,
    _escape_xml,
    _estimate_text_height,
    _estimate_text_width,
    _fmt,
    _parse_c4_diagram,
    render_c4_diagram_to_svg,
)
from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg

_C4_VARIANTS = (
    "C4Context",
    "C4Container",
    "C4Component",
    "C4Dynamic",
    "C4Deployment",
)


# === module surface =========================================================


def test_module_all_exports_single_public_entry() -> None:
    """The leaf exports exactly one public symbol (grok single pub fn L786)."""
    assert c4_mod.__all__ == ["render_c4_diagram_to_svg"]


# === dispatch integration (5 variants via crate-root render_mermaid_to_svg) ==


@pytest.mark.parametrize("variant", _C4_VARIANTS)
def test_render_mermaid_to_svg_routes_all_c4_variants(variant: str) -> None:
    """Each of the 5 C4 tokens routes through the C4 renderer, not the
    unsupported-type arm (mirrors grok lib.rs L130-L139).

    Had the token remained in ``_UNSUPPORTED_DIAGRAM_TYPES`` this would raise
    :class:`UnsupportedDiagramType` instead of returning an SVG.
    """
    svg = render_mermaid_to_svg(f'{variant}\n  System(alias, "Name")')
    assert isinstance(svg, str)
    assert "<svg" in svg
    assert "</svg>" in svg


def test_render_c4_context_returns_svg_with_root_id() -> None:
    """The SVG root carries ``id="my-svg"`` (grok's fixed root id)."""
    svg = render_c4_diagram_to_svg(
        'C4Context\n  System(alias, "Name")', MermaidTheme.light()
    )
    assert 'id="my-svg"' in svg
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_render_many_shapes_does_not_crash() -> None:
    """Six shapes exercise the Bounds grid wrap (C4_SHAPE_IN_ROW = 4)."""
    body = "\n".join(f'  System(n{i}, "Node {i}")' for i in range(6))
    svg = render_c4_diagram_to_svg(f"C4Context\n{body}", MermaidTheme.light())
    assert "<svg" in svg and "</svg>" in svg


# === colour mapping (grok bg_color_for / border_color_for L23-L73) ==========


@pytest.mark.parametrize(
    "type_c4,expected",
    [
        ("person", "#08427B"),
        ("external_person", "#686868"),
        ("system", "#1168BD"),
        ("external_system", "#999999"),
        ("system_db", "#1168BD"),
        ("system_queue", "#1168BD"),
        ("external_system_db", "#999999"),
        ("container", "#438DD5"),
        ("external_container", "#B3B3B3"),
        ("container_db", "#438DD5"),
        ("container_queue", "#438DD5"),
        ("component", "#85BBF0"),
        ("external_component", "#CCCCCC"),
        ("component_db", "#85BBF0"),
    ],
)
def test_bg_color_for_known_types(type_c4: str, expected: str) -> None:
    """Each known lowercase type resolves to its grok fill hex."""
    assert _bg_color_for(type_c4) == expected


def test_bg_color_for_unknown_falls_to_default() -> None:
    """An unknown type falls back to the ``#1168BD`` default fill."""
    assert _bg_color_for("nonsense") == _DEFAULT_BG_COLOR == "#1168BD"


@pytest.mark.parametrize(
    "type_c4,expected",
    [
        ("person", "#073B6F"),
        ("external_person", "#8A8A8A"),
        ("system", "#3C7FC0"),
        ("external_system", "#8A8A8A"),
        ("container", "#3C7FC0"),
        ("external_container", "#A6A6A6"),
        ("component", "#78A8D8"),
        ("external_component", "#BFBFBF"),
    ],
)
def test_border_color_for_known_types(type_c4: str, expected: str) -> None:
    """Each known lowercase type resolves to its grok stroke hex."""
    assert _border_color_for(type_c4) == expected


def test_border_color_for_unknown_falls_to_default() -> None:
    """An unknown type falls back to the ``#3C7FC0`` default stroke."""
    assert _border_color_for("nonsense") == _DEFAULT_BORDER_COLOR == "#3C7FC0"


def test_colour_dicts_have_20_entries_each() -> None:
    """20 keys per mapping: person x2 + (system/container/component each x6:
    base + external twin + _db pair + _queue pair)."""
    assert len(_BG_COLOR_FOR) == 20
    assert len(_BORDER_COLOR_FOR) == 20
    # Every key is lowercase (the parser stores type_c4 lowercase).
    assert all(k == k.lower() for k in _BG_COLOR_FOR)
    assert all(k == k.lower() for k in _BORDER_COLOR_FOR)
    # The two mappings share the exact same key set.
    assert set(_BG_COLOR_FOR) == set(_BORDER_COLOR_FOR)


# === text helpers ===========================================================


def test_escape_xml_uses_quot_entity() -> None:
    """The double quote becomes ``&quot;`` (distinct from er / class variants)."""
    assert _escape_xml('a"b <c>') == "a&quot;b &lt;c&gt;"
    assert _escape_xml("a&b") == "a&amp;b"
    assert _escape_xml("safe text") == "safe text"


def test_estimate_text_width_is_byte_accurate() -> None:
    """Width = byte length * font_size * 0.6 (Rust ``str::len`` semantics)."""
    # 5 ASCII bytes * 14 * 0.6 = 42.0
    assert _estimate_text_width("hello", 14.0) == pytest.approx(42.0)
    # "你好" is 6 UTF-8 bytes (3 per CJK char).
    assert _estimate_text_width("你好", 14.0) == pytest.approx(6 * 14 * 0.6)


def test_estimate_text_height_is_font_size_plus_two() -> None:
    """Line-height heuristic: font_size + 2.0 (grok L219-L221)."""
    assert _estimate_text_height(14.0) == pytest.approx(16.0)


def test_fmt_strips_trailing_zero() -> None:
    """``_fmt`` drops the ``.0`` from whole floats but keeps fractional parts."""
    assert _fmt(10.0) == "10"
    assert _fmt(1.5) == "1.5"


# === parser (grok L371-L693) ================================================


def test_parse_system_is_3arg_shape() -> None:
    """``System(alias, "Name")`` is a 3-arg shape: no techn, descr at arg 2."""
    d = _parse_c4_diagram('C4Context\n  System(alias, "My System")')
    assert d.c4_type == "C4Context"
    assert len(d.shapes) == 1
    s = d.shapes[0]
    assert s.alias == "alias"
    assert s.type_c4 == "system"  # stored lowercase
    assert s.label == "My System"
    assert s.techn == ""
    assert s.descr == ""
    assert s.parent_boundary == "global"


def test_parse_container_is_4arg_with_techn() -> None:
    """``Container(alias, "Name", "Techn")`` is a 4-arg shape: techn at arg 2."""
    d = _parse_c4_diagram('C4Container\n  Container(app, "App", "Go")')
    s = d.shapes[0]
    assert s.type_c4 == "container"
    assert s.label == "App"
    assert s.techn == "Go"
    assert s.descr == ""


def test_parse_external_shape_uses_external_type() -> None:
    """``System_Ext`` maps to the ``external_system`` type."""
    d = _parse_c4_diagram('C4Context\n  System_Ext(alias, "Ext")')
    assert d.shapes[0].type_c4 == "external_system"


def test_parse_person_shape() -> None:
    """``Person`` maps to the ``person`` type (carries the PNG icon at render)."""
    d = _parse_c4_diagram('C4Context\n  Person(user, "User")')
    assert d.shapes[0].type_c4 == "person"


def test_parse_title_directive() -> None:
    """A ``title`` line sets the diagram title."""
    d = _parse_c4_diagram('C4Context\n  title My Diagram\n  System(a, "A")')
    assert d.title == "My Diagram"


def test_parse_rel_collects_relationship() -> None:
    """``Rel(from, to, label)`` yields a 4-field C4Rel with rel_type ``rel``."""
    d = _parse_c4_diagram(
        'C4Context\n  System(a, "A")\n  System(b, "B")\n  Rel(a, b, "calls")'
    )
    assert len(d.rels) == 1
    r = d.rels[0]
    assert r.rel_type == "rel"
    assert r.from_ == "a"  # ``from`` is a Python keyword -> ``from_``
    assert r.to == "b"
    assert r.label == "calls"


def test_parse_birel_uses_birel_type() -> None:
    """``BiRel`` yields a C4Rel tagged ``birel`` (renders identically to rel)."""
    d = _parse_c4_diagram(
        'C4Context\n  System(a, "A")\n  System(b, "B")\n  BiRel(a, b, "link")'
    )
    assert d.rels[0].rel_type == "birel"


def test_parse_boundary_nests_parent() -> None:
    """A ``System_Boundary`` block opens a scope; inner shapes inherit the alias."""
    d = _parse_c4_diagram(
        'C4Context\n  System_Boundary(s, "Boundary") {\n    System(x, "Inner")\n  }'
    )
    # The implicit ``global`` boundary plus the declared ``s`` boundary.
    assert len(d.boundaries) == 2
    assert d.shapes[0].parent_boundary == "s"


def test_parse_ignored_style_funcs_do_not_collect() -> None:
    """``UpdateElementStyle`` / ``UpdateRelStyle`` / ``UpdateLayoutConfig``
    are styling updates -- parsed but not collected as shapes or rels."""
    d = _parse_c4_diagram(
        'C4Context\n  System(a, "A")\n'
        '  UpdateElementStyle(a, "#f00")\n'
        "  UpdateRelStyle(a, b)\n"
        "  UpdateLayoutConfig()"
    )
    assert len(d.shapes) == 1
    assert len(d.rels) == 0


@pytest.mark.parametrize("variant", _C4_VARIANTS)
def test_parse_each_variant_sets_c4_type(variant: str) -> None:
    """The first header token becomes ``c4_type`` verbatim."""
    d = _parse_c4_diagram(f'{variant}\n  System(a, "A")')
    assert d.c4_type == variant


def test_parse_wrong_first_line_raises() -> None:
    """A non-C4 first line raises ParseError naming the offending token."""
    with pytest.raises(ParseError) as exc:
        _parse_c4_diagram("flowchart TD\n  A --> B")
    assert exc.value.line == 1
    assert "flowchart" in exc.value.message


def test_parse_blank_or_comment_only_raises() -> None:
    """A body with no header line raises the declaration-missing ParseError."""
    with pytest.raises(ParseError) as exc:
        _parse_c4_diagram("   \n  %% comment only\n")
    assert exc.value.line == 1


# === Bounds grid layout (grok L127-L210) ====================================


def test_bounds_set_data_seeds_extent_and_cursor() -> None:
    """``set_data`` copies the rectangle into both the running extent and the
    next-row cursor."""
    b = _Bounds()
    b.set_data(10.0, 20.0, 5.0, 15.0)
    assert (b.startx, b.stopx, b.starty, b.stopy) == (10.0, 20.0, 5.0, 15.0)
    assert (b.next_startx, b.next_stopx, b.next_starty, b.next_stopy) == (
        10.0,
        20.0,
        5.0,
        15.0,
    )


def _shape(alias: str, width: float = 100.0, height: float = 50.0):
    """Build a minimal C4Shape for Bounds placement (layout sets x/y)."""
    return c4_mod.C4Shape(
        alias=alias,
        type_c4="system",
        label=alias,
        techn="",
        descr="",
        parent_boundary="global",
        width=width,
        height=height,
    )


def test_bounds_insert_first_shape_uses_single_margin() -> None:
    """The first shape in a row takes a single margin off the cursor stop."""
    b = _Bounds()
    b.set_data(0.0, 0.0, 0.0, 0.0)
    b.width_limit = 800.0
    s = _shape("a")
    b.insert(s)
    # next_stopx(0) + C4_SHAPE_MARGIN(50) = 50
    assert s.x == pytest.approx(50.0)
    assert b.next_cnt == 1


def test_bounds_insert_wraps_at_row_cap() -> None:
    """Inserting a 5th shape (next_cnt > C4_SHAPE_IN_ROW=4) wraps the row and
    resets the counter to 1."""
    b = _Bounds()
    b.set_data(0.0, 0.0, 0.0, 0.0)
    b.width_limit = 800.0
    for i in range(5):
        b.insert(_shape(f"n{i}"))
    assert b.next_cnt == 1


def test_bounds_bump_last_margin_pads_extent() -> None:
    """``bump_last_margin`` grows the running extent by one margin (grok L206)."""
    b = _Bounds()
    b.set_data(0.0, 100.0, 0.0, 60.0)
    b.bump_last_margin()
    assert b.stopx == pytest.approx(100.0 + 50.0)
    assert b.stopy == pytest.approx(60.0 + 50.0)


# === rendering specifics ====================================================


def test_render_person_injects_png_icon() -> None:
    """A ``person`` shape carries its 48x48 PNG icon as a data URI."""
    svg = render_c4_diagram_to_svg(
        'C4Context\n  Person(user, "User")', MermaidTheme.light()
    )
    assert "data:image/png" in svg


def test_render_system_has_no_png_icon() -> None:
    """A non-person shape does not carry the person PNG icon."""
    svg = render_c4_diagram_to_svg(
        'C4Context\n  System(alias, "Name")', MermaidTheme.light()
    )
    assert "data:image/png" not in svg


def test_render_c4dynamic_rel_has_index_prefix() -> None:
    """``C4Dynamic`` prefixes the 1-based index to relationship labels."""
    svg = render_c4_diagram_to_svg(
        'C4Dynamic\n  System(a, "A")\n  System(b, "B")\n  Rel(a, b, "calls")',
        MermaidTheme.light(),
    )
    assert "1: calls" in svg


def test_render_non_dynamic_rel_has_no_index_prefix() -> None:
    """Non-Dynamic variants emit the label verbatim (no ``N:`` prefix)."""
    svg = render_c4_diagram_to_svg(
        'C4Context\n  System(a, "A")\n  System(b, "B")\n  Rel(a, b, "calls")',
        MermaidTheme.light(),
    )
    assert "1: calls" not in svg
    assert "calls" in svg


def test_render_rel_with_techn_emits_bracketed_subtitle() -> None:
    """A non-empty rel ``techn`` emits an italic ``[techn]`` subtitle."""
    svg = render_c4_diagram_to_svg(
        'C4Context\n  System(a, "A")\n  System(b, "B")\n  Rel(a, b, "calls", "HTTP")',
        MermaidTheme.light(),
    )
    assert "[HTTP]" in svg


# === bucket-guard sync (render._UNSUPPORTED_DIAGRAM_TYPES 6 -> 1 -> 0) =====


def test_c4_tokens_removed_from_unsupported_set() -> None:
    """The 5 C4 tokens are gone from the unsupported set (their renderer ships)."""
    for token in _C4_VARIANTS:
        assert token not in render_mod._UNSUPPORTED_DIAGRAM_TYPES


def test_unsupported_set_is_now_empty() -> None:
    """R298 removed ``sequenceDiagram`` (the last leaf) -- the unsupported
    surface is EMPTY."""
    assert render_mod._UNSUPPORTED_DIAGRAM_TYPES == frozenset()
    assert len(render_mod._UNSUPPORTED_DIAGRAM_TYPES) == 0
