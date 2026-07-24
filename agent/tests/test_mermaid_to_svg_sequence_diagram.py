"""Black-box + white-box tests for the migrated sequenceDiagram renderer (R298).

Exercises :mod:`minimax_code.mermaid.to_svg.sequence_diagram` -- the 29th
per-diagram leaf of the R269--R297 stack and the 19th self-contained SVG
emitter (direction (1) brick 29, the LAST per-diagram renderer). Mirrors grok
``sequence_diagram.rs`` (1326 lines): a custom temporal geometry (fixed
participant columns + a 37 px event-row grid + activation-bar stack + fragment
nesting-depth inset) that does NOT consume the dagre layout -- unlike the
R294/R295/R296 dagre consumers, this emitter owns its geometry end to end.

The R298 migration empties ``_UNSUPPORTED_DIAGRAM_TYPES`` (1 -> 0): every
diagram-type token now routes to a dedicated dispatch arm, so the
unsupported-type raise is a dead branch that never fires.

Coverage groups:

* Dispatch smoke -- the ``sequenceDiagram`` token routes through the dedicated
  arm, not the unsupported surface; the leaf's public entry returns a
  well-formed SVG with a viewBox.
* Public surface -- the single-symbol ``__all__`` and the dispatch no longer
  lists ``sequenceDiagram`` as unsupported.
* Marker defs -- the four arrowhead markers (``seq_arrow`` / ``seq_arrow_rev``
  / ``seq_cross`` / ``seq_open``) are always emitted.
* Arrow-head dispatch -- the 10 operators map onto the right marker reference
  (filled / cross / open / none) and the bidirectional start marker.
* Stroke widths -- the three signature widths: lifeline 0.5, activation 0.8,
  message 1.5.
* Self-message loop -- a ``from == to`` message emits a cubic-Bezier path
  (not a straight ``<line>``).
* Note -- the sticky-note rect ``#fff2b0`` fill + dark ``#333333`` text fill.
* Fragment -- the dashed lavender frame + tab label + else separator.
* Title -- the bold title text.
* Autonumber -- the ``autonumber`` / ``autonumber off`` state machine prefixes
  message text with the running counter.
* Multi-line participant label -- ``<br/>`` splits into ``<tspan>`` rows.
* Theme awareness -- the 4 channels (background / node_fill / node_stroke /
  edge_color / text_color) flow into the emitted SVG; dark differs from light.
* Parse errors -- an unrecognized line raises a typed ``ParseError`` carrying
  the line number.
* White-box helpers -- ``_ArrowHead`` / ``_FragmentKind`` (``tab_label``) /
  ``_ARROWS`` (fixity order) / ``_AutoNumber`` / ``_strip_keyword_ci`` /
  ``_parse_message_line`` / ``_parse_fragment_line`` / ``_escape_xml`` /
  ``_decode_sequence_text``.
"""

from __future__ import annotations

import pytest

from minimax_code.mermaid.to_svg import MermaidTheme
from minimax_code.mermaid.to_svg import render as render_mod
from minimax_code.mermaid.to_svg.error import ParseError
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg
from minimax_code.mermaid.to_svg.sequence_diagram import (
    _ARROWS,
    _FRAGMENT_END,
    _ArrowHead,
    _AutoNumber,
    _decode_sequence_text,
    _escape_xml,
    _FragmentKind,
    _parse_fragment_line,
    _parse_message_line,
    _strip_keyword_ci,
    render_sequence_diagram_to_svg,
)

# A canonical two-participant source used by several structural + theme tests.
_TWO_PARTY = "sequenceDiagram\n  Alice->>Bob: Hello\n  Bob-->>Alice: Hi"


# === dispatch smoke =========================================================


def test_render_mermaid_to_svg_sequence_dispatches_to_renderer() -> None:
    """A ``sequenceDiagram`` block routes through the dedicated renderer."""
    svg = render_mermaid_to_svg(_TWO_PARTY)
    assert isinstance(svg, str)
    assert "<svg" in svg
    assert "</svg>" in svg
    # Both participant labels survive the round trip.
    assert "Alice" in svg
    assert "Bob" in svg


def test_leaf_entry_returns_well_formed_svg() -> None:
    """The leaf's public entry returns an SVG that opens and closes."""
    svg = render_sequence_diagram_to_svg(_TWO_PARTY, MermaidTheme.light())
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_svg_carries_viewbox() -> None:
    """The root ``<svg>`` carries a ``viewBox`` attribute."""
    svg = render_sequence_diagram_to_svg(_TWO_PARTY, MermaidTheme.light())
    assert "viewBox=" in svg


def test_default_participant_added_when_none_declared() -> None:
    """A ``sequenceDiagram`` with no participant lines still renders (grok
    synthesizes a default ``Participant`` so a bare message does not panic)."""
    svg = render_sequence_diagram_to_svg(
        "sequenceDiagram\n  A->>B: hi", MermaidTheme.light()
    )
    assert "<svg" in svg
    assert "hi" in svg


# === public surface =========================================================


def test_module_all_is_single_symbol() -> None:
    """The leaf exports exactly the one public render entry."""
    from minimax_code.mermaid.to_svg import sequence_diagram

    assert sequence_diagram.__all__ == ["render_sequence_diagram_to_svg"]


def test_render_dispatch_no_longer_lists_sequence_unsupported() -> None:
    """R298 removed ``sequenceDiagram`` (1 -> 0); the unsupported set is EMPTY.

    Mirrors the cascading shrink: R291 gitGraph (12 -> 11), R292 mindmap
    (11 -> 10), R293 xychart-beta (10 -> 9), R294 requirementDiagram (9 -> 8),
    R295 erDiagram (8 -> 7), R296 classDiagram (7 -> 6), R297 five C4 tokens
    (6 -> 1), R298 sequenceDiagram (1 -> 0).
    """
    assert "sequenceDiagram" not in render_mod._UNSUPPORTED_DIAGRAM_TYPES
    assert len(render_mod._UNSUPPORTED_DIAGRAM_TYPES) == 0


# === marker defs (4 markers always emitted) =================================


def test_all_four_markers_emitted() -> None:
    """The ``<defs>`` block always carries the four arrowhead markers."""
    svg = render_sequence_diagram_to_svg(_TWO_PARTY, MermaidTheme.light())
    assert 'id="seq_arrow"' in svg
    assert 'id="seq_arrow_rev"' in svg
    assert 'id="seq_cross"' in svg
    assert 'id="seq_open"' in svg


def test_filled_marker_path_is_triangle() -> None:
    """``seq_arrow`` is a filled triangle ``M0,0 L8,4 L0,8 Z``."""
    svg = render_sequence_diagram_to_svg(_TWO_PARTY, MermaidTheme.light())
    assert 'd="M0,0 L8,4 L0,8 Z"' in svg


def test_cross_marker_path_is_two_strokes() -> None:
    """``seq_cross`` is an X made of two strokes ``M1,1 L9,9 M9,1 L1,9``."""
    svg = render_sequence_diagram_to_svg(_TWO_PARTY, MermaidTheme.light())
    assert 'd="M1,1 L9,9 M9,1 L1,9"' in svg


# === arrow-head dispatch (10 operators -> marker references) ================


def test_arrow_solid_filled_uses_seq_arrow_marker() -> None:
    """``->>`` (solid filled) references ``seq_arrow``."""
    svg = render_sequence_diagram_to_svg(
        "sequenceDiagram\n  A->>B: hi", MermaidTheme.light()
    )
    assert 'marker-end="url(#seq_arrow)"' in svg


def test_arrow_dashed_filled_carries_dasharray() -> None:
    """``-->>`` (dashed filled) references ``seq_arrow`` + the 5,4 dasharray."""
    svg = render_sequence_diagram_to_svg(
        "sequenceDiagram\n  A-->>B: hi", MermaidTheme.light()
    )
    assert 'marker-end="url(#seq_arrow)"' in svg
    assert 'stroke-dasharray="5,4"' in svg


def test_arrow_cross_uses_seq_cross_marker() -> None:
    """``-x`` (solid cross) references ``seq_cross``."""
    svg = render_sequence_diagram_to_svg(
        "sequenceDiagram\n  A-xB: fail", MermaidTheme.light()
    )
    assert 'marker-end="url(#seq_cross)"' in svg


def test_arrow_open_uses_seq_open_marker() -> None:
    """``-)`` (solid open) references ``seq_open``."""
    svg = render_sequence_diagram_to_svg(
        "sequenceDiagram\n  A-)B: open", MermaidTheme.light()
    )
    assert 'marker-end="url(#seq_open)"' in svg


def test_arrow_none_has_no_marker_end() -> None:
    """``->`` (solid none) emits no ``marker-end`` at all."""
    svg = render_sequence_diagram_to_svg(
        "sequenceDiagram\n  A->B: plain", MermaidTheme.light()
    )
    assert "marker-end=" not in svg


def test_bidirectional_carries_marker_start() -> None:
    """``<<->>`` (bidirectional filled) emits the reverse start marker."""
    svg = render_sequence_diagram_to_svg(
        "sequenceDiagram\n  A<<->>B: sync", MermaidTheme.light()
    )
    assert 'marker-start="url(#seq_arrow_rev)"' in svg
    assert 'marker-end="url(#seq_arrow)"' in svg


# === stroke widths (lifeline 0.5 / activation 0.8 / message 1.5) ===========


def test_lifeline_has_zero_five_stroke_width() -> None:
    """Each participant's lifeline ``<line>`` carries ``stroke-width="0.5"``."""
    svg = render_sequence_diagram_to_svg(_TWO_PARTY, MermaidTheme.light())
    assert 'stroke-width="0.5"' in svg


def test_activation_bar_has_zero_eight_stroke_width() -> None:
    """An ``activate``/``+`` shorthand yields an activation bar with
    ``stroke-width="0.8"``."""
    src = "sequenceDiagram\n  Alice->>+Bob: hi\n  Bob-->>-Alice: yo"
    svg = render_sequence_diagram_to_svg(src, MermaidTheme.light())
    assert 'stroke-width="0.8"' in svg


def test_message_line_has_one_five_stroke_width() -> None:
    """A message ``<line>`` carries ``stroke-width="1.5"``."""
    svg = render_sequence_diagram_to_svg(_TWO_PARTY, MermaidTheme.light())
    assert 'stroke-width="1.5"' in svg


# === self-message loop (cubic Bezier, not a straight line) =================


def test_self_message_emits_cubic_bezier_path() -> None:
    """A ``from == to`` message emits a cubic-Bezier ``<path>`` (the `` C ``
    segment is unique to the self-loop; marker / tab paths use ``L`` / ``h``)."""
    svg = render_sequence_diagram_to_svg(
        "sequenceDiagram\n  A->>A: loop", MermaidTheme.light()
    )
    assert "<path" in svg
    assert " C " in svg


def test_self_message_text_anchor_is_start() -> None:
    """The self-loop label is left-anchored (``text-anchor="start"``), unlike
    the centered ordinary-message label."""
    svg = render_sequence_diagram_to_svg(
        "sequenceDiagram\n  A->>A: loop", MermaidTheme.light()
    )
    assert 'text-anchor="start"' in svg


# === note (sticky-note fill + dark text) ===================================


def test_note_uses_sticky_yellow_fill() -> None:
    """A ``note right of`` emits a rect with the hardcoded ``#fff2b0`` fill."""
    src = "sequenceDiagram\n  Alice->>Bob: hi\n  note right of Alice: hello"
    svg = render_sequence_diagram_to_svg(src, MermaidTheme.light())
    assert 'fill="#fff2b0"' in svg


def test_note_text_uses_dark_fill() -> None:
    """The note label uses the hardcoded ``#333333`` text fill (NOT the theme
    text_color -- the note palette is fixed regardless of theme)."""
    src = "sequenceDiagram\n  Alice->>Bob: hi\n  note right of Alice: hello"
    svg = render_sequence_diagram_to_svg(src, MermaidTheme.light())
    assert 'fill="#333333"' in svg


def test_note_over_two_participants_spans() -> None:
    """``note over A,B`` places the note across both participants (emits the
    sticky-note rect)."""
    src = "sequenceDiagram\n  Alice->>Bob: hi\n  note over Alice,Bob: span"
    svg = render_sequence_diagram_to_svg(src, MermaidTheme.light())
    assert 'fill="#fff2b0"' in svg
    assert "span" in svg


# === fragment (dashed lavender frame + tab label + else separator) =========


def test_fragment_frame_uses_dashed_lavender_stroke() -> None:
    """A ``loop`` fragment border carries the hardcoded ``#d7c8f8`` stroke and
    the ``3,3`` dasharray."""
    src = (
        "sequenceDiagram\n  Alice->>Bob: hi\n"
        "  loop daily\n    Bob->>Alice: bye\n  end"
    )
    svg = render_sequence_diagram_to_svg(src, MermaidTheme.light())
    assert 'stroke="#d7c8f8"' in svg
    assert 'stroke-dasharray="3,3"' in svg


def test_fragment_tab_carries_loop_label() -> None:
    """The fragment tab text is the kind keyword (``loop``)."""
    src = (
        "sequenceDiagram\n  Alice->>Bob: hi\n"
        "  loop daily\n    Bob->>Alice: bye\n  end"
    )
    svg = render_sequence_diagram_to_svg(src, MermaidTheme.light())
    assert ">loop<" in svg


def test_fragment_tab_text_uses_lavender_text_color() -> None:
    """The tab label uses the hardcoded ``#4c3f6f`` text fill."""
    src = (
        "sequenceDiagram\n  Alice->>Bob: hi\n"
        "  loop daily\n    Bob->>Alice: bye\n  end"
    )
    svg = render_sequence_diagram_to_svg(src, MermaidTheme.light())
    assert 'fill="#4c3f6f"' in svg


def test_alt_else_fragment_emits_else_label() -> None:
    """An ``alt``/``else`` fragment emits the else-arm label in ``[...]``."""
    src = (
        "sequenceDiagram\n  Alice->>Bob: hi\n"
        "  alt success\n    Bob->>Alice: ok\n"
        "  else failure\n    Bob->>Alice: err\n  end"
    )
    svg = render_sequence_diagram_to_svg(src, MermaidTheme.light())
    assert ">alt<" in svg
    assert "[failure]" in svg


# === title (bold text) ======================================================


def test_title_emits_bold_text() -> None:
    """A ``title`` line emits a bold title ``<text>`` carrying the title body."""
    src = "sequenceDiagram\n  title My Flow\n  Alice->>Bob: hi"
    svg = render_sequence_diagram_to_svg(src, MermaidTheme.light())
    assert 'font-weight="bold"' in svg
    assert "My Flow" in svg


def test_title_uses_thirteen_point_font() -> None:
    """The title text carries the grok ``font-size="13"``."""
    src = "sequenceDiagram\n  title My Flow\n  Alice->>Bob: hi"
    svg = render_sequence_diagram_to_svg(src, MermaidTheme.light())
    assert 'font-size="13"' in svg


# === autonumber (running counter prefixes message text) ====================


def test_autonumber_prefixes_message_text() -> None:
    """``autonumber`` prefixes subsequent messages with the running counter."""
    src = (
        "sequenceDiagram\n  autonumber\n"
        "  Alice->>Bob: place order\n  Bob->>Alice: confirm"
    )
    svg = render_sequence_diagram_to_svg(src, MermaidTheme.light())
    assert "1. place order" in svg
    assert "2. confirm" in svg


def test_autonumber_off_disables_prefixing() -> None:
    """``autonumber off`` stops the counter; later messages are bare."""
    src = (
        "sequenceDiagram\n  autonumber\n  Alice->>Bob: place order\n"
        "  autonumber off\n  Bob->>Alice: confirm"
    )
    svg = render_sequence_diagram_to_svg(src, MermaidTheme.light())
    assert "1. place order" in svg
    assert "confirm" in svg
    # After ``off``, the second message is NOT numbered.
    assert "2. confirm" not in svg


# === multi-line participant label (<br/> -> <tspan>) =======================


def test_multiline_participant_label_uses_tspan() -> None:
    """A ``<br/>`` in a participant alias splits the label into ``<tspan>``."""
    src = (
        "sequenceDiagram\n"
        "  participant Alice as Alice<br/>Smith\n"
        "  Alice->>Bob: hi"
    )
    svg = render_sequence_diagram_to_svg(src, MermaidTheme.light())
    assert "<tspan" in svg
    assert "Smith" in svg


# === theme awareness (4 channels flow into the SVG) ========================


def test_theme_background_flows_into_root_rect() -> None:
    """The root ``<rect>`` fill is ``theme.background``."""
    dark = MermaidTheme.dark()
    svg = render_sequence_diagram_to_svg(_TWO_PARTY, dark)
    assert f'fill="{dark.background}"' in svg


def test_theme_node_fill_in_participant_header() -> None:
    """The participant header rect fill is ``theme.node_fill``."""
    light = MermaidTheme.light()
    svg = render_sequence_diagram_to_svg(_TWO_PARTY, light)
    assert f'fill="{light.node_fill}"' in svg


def test_theme_node_stroke_in_participant_header() -> None:
    """The participant header rect stroke is ``theme.node_stroke``."""
    light = MermaidTheme.light()
    svg = render_sequence_diagram_to_svg(_TWO_PARTY, light)
    assert f'stroke="{light.node_stroke}"' in svg


def test_theme_edge_color_in_markers_and_lifeline() -> None:
    """``theme.edge_color`` flows into the marker paths (fill) and the lifeline
    + message strokes."""
    light = MermaidTheme.light()
    svg = render_sequence_diagram_to_svg(_TWO_PARTY, light)
    # marker path fill + lifeline/message stroke both use edge_color.
    assert f'fill="{light.edge_color}"' in svg
    assert f'stroke="{light.edge_color}"' in svg


def test_theme_text_color_in_participant_label() -> None:
    """The participant label text fill is ``theme.text_color``."""
    light = MermaidTheme.light()
    svg = render_sequence_diagram_to_svg(_TWO_PARTY, light)
    assert f'fill="{light.text_color}">Alice' in svg


def test_dark_theme_svg_differs_from_light() -> None:
    """Light and dark themes produce different SVG bytes."""
    light_svg = render_sequence_diagram_to_svg(_TWO_PARTY, MermaidTheme.light())
    dark_svg = render_sequence_diagram_to_svg(_TWO_PARTY, MermaidTheme.dark())
    assert light_svg != dark_svg


# === parse errors ===========================================================


def test_unrecognized_line_raises_parse_error() -> None:
    """A line that is not a recognized statement raises ``ParseError``."""
    with pytest.raises(ParseError):
        render_sequence_diagram_to_svg(
            "sequenceDiagram\n  optimize the flow", MermaidTheme.light()
        )


def test_parse_error_carries_line_number() -> None:
    """The ``ParseError`` stringifies with the 1-based line number."""
    with pytest.raises(ParseError) as exc_info:
        render_sequence_diagram_to_svg(
            "sequenceDiagram\n  Alice->>Bob: hi\n  bogus line here",
            MermaidTheme.light(),
        )
    assert "line 3" in str(exc_info.value)


# === white-box: _ArrowHead enum ============================================


def test_arrow_head_has_four_variants() -> None:
    """``_ArrowHead`` enumerates exactly FILLED / CROSS / OPEN / NONE."""
    members = {m.name for m in _ArrowHead}
    assert members == {"FILLED", "CROSS", "OPEN", "NONE"}


def test_arrow_head_values_are_lowercase_strings() -> None:
    """Each member's value is its lowercase name (grok ``#[derive]`` wire form)."""
    assert _ArrowHead.FILLED.value == "filled"
    assert _ArrowHead.CROSS.value == "cross"
    assert _ArrowHead.OPEN.value == "open"
    assert _ArrowHead.NONE.value == "none"


# === white-box: _FragmentKind + tab_label ==================================


def test_fragment_kind_has_seven_variants() -> None:
    """``_FragmentKind`` enumerates the 7 fragment-opening keywords."""
    members = {m.name for m in _FragmentKind}
    assert members == {"ALT", "LOOP", "OPT", "PAR", "CRITICAL", "BREAK", "RECT"}


def test_fragment_kind_tab_label_returns_value() -> None:
    """``tab_label`` returns the lowercase keyword for every non-RECT kind."""
    assert _FragmentKind.ALT.tab_label() == "alt"
    assert _FragmentKind.LOOP.tab_label() == "loop"
    assert _FragmentKind.OPT.tab_label() == "opt"
    assert _FragmentKind.PAR.tab_label() == "par"
    assert _FragmentKind.CRITICAL.tab_label() == "critical"
    assert _FragmentKind.BREAK.tab_label() == "break"


def test_fragment_kind_rect_tab_label_is_none() -> None:
    """RECT carries no tab label (grok renders a bare rectangle for ``rect``)."""
    assert _FragmentKind.RECT.tab_label() is None


# === white-box: _ARROWS table (10 operators, fixity order) =================


def test_arrows_table_has_ten_operators() -> None:
    """The dispatch table carries exactly the 10 mermaid sequence operators."""
    assert len(_ARROWS) == 10


def test_arrows_fixity_order_longest_first() -> None:
    """The table is ordered longest / most-specific first so a substring search
    does not mis-match (e.g. ``<<-->>`` before ``-->>`` before ``->>``)."""
    operators = [entry[0] for entry in _ARROWS]
    assert operators == [
        "<<-->>",
        "<<->>",
        "-->>",
        "->>",
        "--x",
        "-x",
        "--)",
        "-)",
        "-->",
        "->",
    ]


def test_arrows_solid_filled_mapping() -> None:
    """``->>`` is (dashed=False, FILLED, bidirectional=False)."""
    assert ("->>", False, _ArrowHead.FILLED, False) in _ARROWS


def test_arrows_bidirectional_mapping() -> None:
    """``<<->>`` is (dashed=False, FILLED, bidirectional=True)."""
    assert ("<<->>", False, _ArrowHead.FILLED, True) in _ARROWS


def test_arrows_cross_and_open_and_none_mapping() -> None:
    """``-x`` -> CROSS, ``-)`` -> OPEN, ``->`` -> NONE (all solid)."""
    assert ("-x", False, _ArrowHead.CROSS, False) in _ARROWS
    assert ("-)", False, _ArrowHead.OPEN, False) in _ARROWS
    assert ("->", False, _ArrowHead.NONE, False) in _ARROWS


# === white-box: _AutoNumber state machine ==================================


def test_autonumber_inactive_returns_text_unchanged() -> None:
    """A fresh ``_AutoNumber`` is inactive: ``number`` is a passthrough."""
    auto = _AutoNumber()
    assert auto.active is False
    assert auto.number("hello") == "hello"


def test_autonumber_bare_activates_starting_at_one() -> None:
    """``apply("")`` (bare ``autonumber``) activates at 1, step 1."""
    auto = _AutoNumber()
    auto.apply("")
    assert auto.active is True
    assert auto.number("place order") == "1. place order"
    assert auto.number("pay") == "2. pay"


def test_autonumber_off_deactivates() -> None:
    """``apply("off")`` deactivates the counter."""
    auto = _AutoNumber()
    auto.apply("")
    auto.apply("off")
    assert auto.active is False
    assert auto.number("msg") == "msg"


def test_autonumber_start_and_step() -> None:
    """``apply("100 5")`` seeds ``next=100`` and ``step=5``."""
    auto = _AutoNumber()
    auto.apply("100 5")
    assert auto.next == 100
    assert auto.step == 5
    assert auto.number("msg") == "100. msg"
    assert auto.number("msg2") == "105. msg2"


def test_autonumber_empty_text_returns_dot_only() -> None:
    """An empty message text yields ``"{N}."`` (no trailing space)."""
    auto = _AutoNumber()
    auto.apply("")
    assert auto.number("") == "1."


# === white-box: _strip_keyword_ci ==========================================


def test_strip_keyword_returns_remainder() -> None:
    """``_strip_keyword_ci("participant Alice", "participant")`` -> ``"Alice"``."""
    assert _strip_keyword_ci("participant Alice", "participant") == "Alice"


def test_strip_keyword_case_insensitive() -> None:
    """The prefix match is case-insensitive (``Participant`` vs ``participant``)."""
    assert _strip_keyword_ci("Participant Alice", "participant") == "Alice"
    assert _strip_keyword_ci("PARTICIPANT Alice", "participant") == "Alice"


def test_strip_keyword_rejects_no_space_suffix() -> None:
    """``participantX`` (no whitespace after the keyword) does NOT match --
    avoids treating ``participantX`` as ``participant`` + ``X``."""
    assert _strip_keyword_ci("participantX", "participant") is None


def test_strip_keyword_accepts_tab_separator() -> None:
    """A tab after the keyword is an accepted separator."""
    assert _strip_keyword_ci("participant\tAlice", "participant") == "Alice"


def test_strip_keyword_eos_returns_empty() -> None:
    """The keyword at end-of-string yields an empty remainder (still a match)."""
    assert _strip_keyword_ci("participant", "participant") == ""


# === white-box: _parse_message_line ========================================


def test_parse_message_solid_filled() -> None:
    """``Alice->>Bob: hello`` -> from=Alice, to=Bob, text=hello, solid FILLED."""
    msg = _parse_message_line("Alice->>Bob: hello")
    assert msg is not None
    assert msg.from_ == "Alice"
    assert msg.to == "Bob"
    assert msg.text == "hello"
    assert msg.dashed is False
    assert msg.head is _ArrowHead.FILLED
    assert msg.bidirectional is False


def test_parse_message_dashed_filled() -> None:
    """``Alice-->>Bob: hi`` -> dashed=True, FILLED."""
    msg = _parse_message_line("Alice-->>Bob: hi")
    assert msg is not None
    assert msg.dashed is True
    assert msg.head is _ArrowHead.FILLED


def test_parse_message_cross_and_open() -> None:
    """``-x`` -> CROSS, ``-)`` -> OPEN (both solid)."""
    cross = _parse_message_line("A-xB: fail")
    assert cross is not None
    assert cross.head is _ArrowHead.CROSS
    assert cross.dashed is False
    opened = _parse_message_line("A-)B: open")
    assert opened is not None
    assert opened.head is _ArrowHead.OPEN


def test_parse_message_none_arrow() -> None:
    """``->`` (the shortest operator, last in fixity) -> NONE."""
    msg = _parse_message_line("A->B: plain")
    assert msg is not None
    assert msg.head is _ArrowHead.NONE
    assert msg.dashed is False


def test_parse_message_bidirectional() -> None:
    """``<<->>`` -> bidirectional=True, FILLED."""
    msg = _parse_message_line("A<<->>B: sync")
    assert msg is not None
    assert msg.bidirectional is True
    assert msg.head is _ArrowHead.FILLED


def test_parse_message_shorthand_activate() -> None:
    """A ``+`` prefix on the target activates it (``Alice->>+Bob``)."""
    msg = _parse_message_line("Alice->>+Bob: hi")
    assert msg is not None
    assert msg.activate_target is True
    assert msg.to == "Bob"


def test_parse_message_shorthand_deactivate() -> None:
    """A ``-`` prefix on the target deactivates the source (``Bob-->>-Alice``)."""
    msg = _parse_message_line("Bob-->>-Alice: yo")
    assert msg is not None
    assert msg.deactivate_source is True
    assert msg.to == "Alice"


def test_parse_message_no_arrow_returns_none() -> None:
    """A line with no recognized operator returns ``None``."""
    assert _parse_message_line("just some text") is None
    assert _parse_message_line("Alice to Bob") is None


def test_parse_message_no_colon_treats_text_as_empty() -> None:
    """A line with an arrow but no ``:`` yields an empty text body."""
    msg = _parse_message_line("Alice->>Bob")
    assert msg is not None
    assert msg.text == ""
    assert msg.from_ == "Alice"
    assert msg.to == "Bob"


# === white-box: _parse_fragment_line =======================================


def test_parse_fragment_start_alt() -> None:
    """``alt success path`` -> FragmentStart(ALT, ``"success path"``)."""
    result = _parse_fragment_line("alt success path")
    assert result is not None
    assert result.kind is _FragmentKind.ALT
    assert result.label == "success path"


def test_parse_fragment_start_loop_opt_par() -> None:
    """``loop`` / ``opt`` / ``par`` each open their respective kind."""
    loop = _parse_fragment_line("loop 5 times")
    assert loop is not None
    assert loop.kind is _FragmentKind.LOOP
    assert loop.label == "5 times"
    opt = _parse_fragment_line("opt maybe")
    assert opt is not None
    assert opt.kind is _FragmentKind.OPT
    par = _parse_fragment_line("par branch")
    assert par is not None
    assert par.kind is _FragmentKind.PAR


def test_parse_fragment_start_critical_break_rect() -> None:
    """``critical`` / ``break`` / ``rect`` each open their respective kind."""
    assert _parse_fragment_line("critical must").kind is _FragmentKind.CRITICAL
    assert _parse_fragment_line("break on err").kind is _FragmentKind.BREAK
    rect = _parse_fragment_line("rect rgb(255,0,0)")
    assert rect is not None
    assert rect.kind is _FragmentKind.RECT
    assert rect.label == "rgb(255,0,0)"


def test_parse_fragment_else_and_option() -> None:
    """``else`` / ``and`` / ``option`` each produce an else-arm event."""
    for line, expected in (
        ("else failure", "failure"),
        ("and another", "another"),
        ("option cond", "cond"),
    ):
        result = _parse_fragment_line(line)
        assert result is not None
        assert result.label == expected


def test_parse_fragment_end_returns_singleton() -> None:
    """``end`` returns the module-level ``_FRAGMENT_END`` sentinel."""
    assert _parse_fragment_line("end") is _FRAGMENT_END


def test_parse_fragment_garbage_returns_none() -> None:
    """A line that is not a fragment control keyword returns ``None``."""
    assert _parse_fragment_line("not a fragment keyword") is None


# === white-box: _escape_xml + _decode_sequence_text ========================


def test_escape_xml_uses_apos_named_entity() -> None:
    """``'`` -> ``&apos;`` (named entity shared with er/gitgraph/journey)."""
    assert _escape_xml("it's") == "it&apos;s"
    assert _escape_xml("a&b<c>d") == "a&amp;b&lt;c&gt;d"


def test_decode_sequence_text_maps_semicolon_entity() -> None:
    """Mermaid's ``#59;`` entity decodes to a literal ``;``."""
    assert _decode_sequence_text("a#59;b") == "a;b"
    assert _decode_sequence_text("plain") == "plain"
