"""Black-box tests for the ``packet-beta`` renderer (R283) -- the behavioral-
equivalent port of grok's ``packet_diagram.rs``.

Direction (1) brick 16 (R283). The fifth per-diagram leaf and the fourth
per-diagram *renderer* (a self-contained SVG emitter, like R279's ``info``,
R281's ``radar``, and R282's ``pie``; unlike R280's ``stateDiagram`` parser
which rides the dagre stack). The ``packet-beta`` diagram is mermaid's
network-packet / bit-field visualizer: a sequence of contiguous bit ranges
``<start>-<end>: "<label>"`` laid out as labeled rectangles on a 32-bit-per-
row grid, each block annotated with its start / end bit index. This file
exercises the renderer's contract surface directly and through the dispatch,
mirroring grok's own coverage:

* **Dispatch smoke** (grok ``lib.rs`` ``test_simple_packet_diagram`` L596-L599):
  :func:`render_mermaid_to_svg` with the canonical packet fixture succeeds and
  emits a well-formed ``<svg>...</svg>``. This is the integration assertion
  grok makes for the packet arm.
* **Header recognition** (grok packet_diagram.rs L131-L140): the first
  substantial line's leading token must be ``packet-beta``; any other first
  token raises :class:`ParseError`, and an all-blank body raises pinning
  line 1.
* **Title grammar** (grok L142-L148): a ``title <text>`` line sets the
  diagram title; a bare ``title`` keyword (no trailing text, after grok's
  whole-line ``trim``) misses the ``title `` prefix and falls through to the
  block parser, which rejects it as ``Invalid packet block``; absent title
  leaves it ``None``.
* **Block grammar** (grok L150-L161, L183-L218, L220-L239): ``<range>:
  "<label>"`` / ``<range>: <label>`` populates the block list; a surrounding
  pair of double- or single-quotes is stripped from the label; a ``start-end``
  range and a lone ``bit`` index both parse as u32.
* **Block errors** (grok L150-L155, L190-L200, L212-L215, L231-L236): a block
  line with no ``:`` separator raises ``Invalid packet block``; a non-numeric
  range raises ``Invalid packet start`` / ``Invalid packet end`` / ``Invalid
  packet bit index``; an empty label raises ``Packet block label cannot be
  empty``; a diagram with zero blocks raises pinning line 1.
* **Range validity** (grok L202-L207): ``end < start`` raises ``Packet block
  {start}-{end} is invalid (end < start)``.
* **Contiguity** (grok L241-L262): each block's start must be one past the
  prior block's end, else ``is not contiguous`` pinning line 1.
* **Row split** (grok L264-L323): a block crossing the 32-bit row boundary is
  split into a fitting fragment and a remainder carried to the next row;
  ``svg_height`` grows by one ``total_row_height`` (47px) per row.
* **Geometry** (grok L17-L26, L46-L82): fixed ``svg_width`` 1026, viewBox
  dims via Rust ``Display`` (integer floats drop ``.0``); each block's rect
  x/y/width/height and label / bit-index coordinates.
* **show_bits** (grok L64-L82): a single-bit block emits one centered index;
  a multi-bit block emits a start (left, ``text-anchor="start"``) and end
  (right, ``text-anchor="end"``) index.
* **escape_xml** (grok L325-L331): the five XML-significant characters; grok's
  packet maps ``'`` -> ``&apos;`` (named entity, same as pie; radar uses
  ``&#39;``).
* **Float bridge** (:func:`_fmt`): integer-valued floats drop ``.0``.
* **u32 parse bridge** (:func:`_parse_u32`): strict ``\\d+`` full-match (rejects
  ``+12`` / ``1_2`` / empty the way Rust ``u32::from_str`` does).
* **Module surface**: the single-symbol ``__all__``; the renderer stays out
  of the :mod:`.to_svg` barrel (dispatch-only reach, mirrors R279/R280/R281/
  R282); the two dataclasses are frozen.
* **Front-matter dispatch** (R283 contract lift): a packet source carrying a
  ``---`` front-matter block still renders -- the dispatch passes the
  front-matter-stripped body (mirrors grok lib.rs L47 shadow).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import packet_diagram as packet_mod
from minimax_code.mermaid.to_svg.error import ParseError, UnsupportedDiagramType
from minimax_code.mermaid.to_svg.packet_diagram import (
    DEFAULT_BIT_WIDTH,
    DEFAULT_BITS_PER_ROW,
    DEFAULT_PADDING_X,
    DEFAULT_PADDING_Y,
    DEFAULT_ROW_HEIGHT,
    DEFAULT_SHOW_BITS,
    PacketBlock,
    PacketDiagram,
    _escape_xml,
    _fmt,
    _parse_u32,
    parse_packet_diagram,
    render_packet_diagram_to_svg,
)
from minimax_code.mermaid.to_svg.render import render_mermaid_to_svg
from minimax_code.mermaid.to_svg.theme import MermaidTheme

# === dispatch smoke (grok lib.rs test_simple_packet_diagram L596-L599) =====


def test_render_mermaid_to_svg_packet_dispatch_emits_svg() -> None:
    """The canonical packet fixture dispatches end-to-end to an SVG (grok L596-L599).

    Mirrors grok's integration assertion for the packet arm: the
    ``packet-beta`` header plus three contiguous blocks (one multi-bit range,
    one multi-bit range, one single-bit) renders through
    ``parse_packet_diagram`` -> ``render_packet_diagram_to_svg`` and yields a
    well-formed SVG. Same fixture, same two assertions (``<svg`` /
    ``packetBlock``) as the Rust test.
    """
    source = 'packet-beta\n0-3: "Header"\n4-7: "Payload"\n8: "CRC"'
    svg = render_mermaid_to_svg(source)
    assert isinstance(svg, str)
    assert "<svg" in svg
    assert "packetBlock" in svg


def test_render_mermaid_to_svg_packet_is_not_unsupported() -> None:
    """``packet-beta`` no longer raises :class:`UnsupportedDiagramType` (R283).

    Pre-R283 ``packet-beta`` sat in ``_UNSUPPORTED_DIAGRAM_TYPES``; R283 lifts
    it into a dedicated dispatch arm (mirrors grok lib.rs L74-L76). The token
    now renders instead of raising.
    """
    source = 'packet-beta\n0-3: "Header"\n4-7: "Payload"\n8: "CRC"'
    try:
        svg = render_mermaid_to_svg(source)
    except UnsupportedDiagramType:
        pytest.fail("packet-beta must not raise UnsupportedDiagramType after R283")
    assert "<svg" in svg


def test_render_packet_diagram_to_svg_returns_svg_directly() -> None:
    """The renderer entry returns a well-formed SVG string for a valid source."""
    svg = render_packet_diagram_to_svg(
        'packet-beta\n0-3: "Header"\n4-7: "Payload"\n8: "CRC"',
        MermaidTheme.default(),
    )
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "packetBlock" in svg
    assert "packetLabel" in svg
    assert "packetTitle" in svg


# === header recognition (grok packet_diagram.rs L131-L140) =================


def test_parse_packet_header_skips_blank_and_comment_lines() -> None:
    """Leading blank / ``%%`` comment lines are skipped before the header."""
    diagram = parse_packet_diagram('\n%% a comment\n\n  packet-beta\n0: "A"')
    assert diagram.rows == [[PacketBlock(start=0, end=0, label="A")]]


def test_parse_packet_missing_header_raises_line_one() -> None:
    """A body whose first token is not ``packet-beta`` raises (grok L131-L140)."""
    with pytest.raises(ParseError) as exc_info:
        parse_packet_diagram("flowchart TD\n  A --> B")
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Expected 'packet-beta' declaration"


def test_parse_packet_wrong_first_token_carries_that_line_number() -> None:
    """The ParseError carries the offending header line's 1-based number."""
    with pytest.raises(ParseError) as exc_info:
        parse_packet_diagram("\n\nradar-beta\naxis A, B")
    assert exc_info.value.line == 3
    assert exc_info.value.message == "Expected 'packet-beta' declaration"


def test_parse_packet_all_blank_raises_line_one() -> None:
    """An all-blank / comment body raises :class:`ParseError` pinning line 1."""
    with pytest.raises(ParseError) as exc_info:
        parse_packet_diagram("\n%% only\n   \n")
    assert exc_info.value.line == 1


# === title grammar (grok L142-L148) =======================================


def test_parse_packet_title_line_sets_title() -> None:
    """A ``title <text>`` line sets the diagram title (grok strip_prefix)."""
    diagram = parse_packet_diagram('packet-beta\ntitle My Packet\n0: "A"')
    assert diagram.title == "My Packet"


def test_parse_packet_bare_title_keyword_raises() -> None:
    """A bare ``title`` keyword (no trailing text) raises ``Invalid packet block``.

    grok trims the whole line first (L129), so ``"title   "`` collapses to
    ``"title"``; ``strip_prefix("title ")`` then misses (no trailing space),
    and the line falls through to the block parser, which rejects it for
    having no ``:`` separator. A title needs actual trailing text to set.
    """
    with pytest.raises(ParseError) as exc_info:
        parse_packet_diagram('packet-beta\ntitle   \n0: "A"')
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Invalid packet block: title"


def test_parse_packet_no_title_is_none() -> None:
    """With no ``title`` line the diagram title stays ``None``."""
    diagram = parse_packet_diagram('packet-beta\n0: "A"')
    assert diagram.title is None


# === block grammar (grok L150-L161, L183-L218, L220-L239) =================


def test_parse_packet_range_block() -> None:
    """A ``start-end`` range parses into a block spanning those bits."""
    diagram = parse_packet_diagram('packet-beta\n0-3: "Header"')
    assert diagram.rows == [[PacketBlock(start=0, end=3, label="Header")]]


def test_parse_packet_single_bit_block() -> None:
    """A lone bit index parses into a single-bit block (start == end)."""
    diagram = parse_packet_diagram('packet-beta\n8: "CRC"')
    assert diagram.rows == [[PacketBlock(start=8, end=8, label="CRC")]]


def test_parse_packet_double_quoted_label_strips_quotes() -> None:
    """A surrounding pair of double-quotes is stripped from the label."""
    diagram = parse_packet_diagram('packet-beta\n0: "Payload"')
    assert diagram.rows[0][0].label == "Payload"


def test_parse_packet_single_quoted_label_strips_quotes() -> None:
    """A surrounding pair of single-quotes is also stripped (grok L227-L229)."""
    diagram = parse_packet_diagram("packet-beta\n0: 'Payload'")
    assert diagram.rows[0][0].label == "Payload"


def test_parse_packet_unquoted_label_accepted() -> None:
    """An unquoted label is accepted verbatim (grok takes it as-is)."""
    diagram = parse_packet_diagram("packet-beta\n0: Payload")
    assert diagram.rows[0][0].label == "Payload"


def test_parse_packet_multiple_blocks_preserve_order() -> None:
    """Blocks land in the row in insertion (source) order."""
    diagram = parse_packet_diagram('packet-beta\n0-3: "A"\n4-7: "B"\n8: "C"')
    assert len(diagram.rows) == 1
    assert [(b.start, b.end, b.label) for b in diagram.rows[0]] == [
        (0, 3, "A"),
        (4, 7, "B"),
        (8, 8, "C"),
    ]


# === block errors (grok L150-L155, L190-L200, L212-L215, L231-L236) ========


def test_parse_packet_block_without_colon_raises() -> None:
    """A block line with no ``:`` separator raises ``Invalid packet block``."""
    with pytest.raises(ParseError) as exc_info:
        parse_packet_diagram("packet-beta\nNoColonHere")
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Invalid packet block: NoColonHere"


def test_parse_packet_non_numeric_start_raises() -> None:
    """A non-numeric range start raises ``Invalid packet start`` (grok L190-L193)."""
    with pytest.raises(ParseError) as exc_info:
        parse_packet_diagram('packet-beta\nabc-3: "A"')
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Invalid packet start: abc"


def test_parse_packet_non_numeric_end_raises() -> None:
    """A non-numeric range end raises ``Invalid packet end`` (grok L197-L200)."""
    with pytest.raises(ParseError) as exc_info:
        parse_packet_diagram('packet-beta\n0-xyz: "A"')
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Invalid packet end: xyz"


def test_parse_packet_non_numeric_single_bit_raises() -> None:
    """A non-numeric single bit index raises ``Invalid packet bit index`` (grok L212-L215)."""
    with pytest.raises(ParseError) as exc_info:
        parse_packet_diagram('packet-beta\nabc: "A"')
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Invalid packet bit index: abc"


def test_parse_packet_empty_label_raises() -> None:
    """An empty unquoted label raises ``Packet block label cannot be empty``."""
    with pytest.raises(ParseError) as exc_info:
        parse_packet_diagram("packet-beta\n0:")
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Packet block label cannot be empty"


def test_parse_packet_no_blocks_raises_line_one() -> None:
    """A diagram with no block lines raises pinning line 1 (grok L170-L175)."""
    with pytest.raises(ParseError) as exc_info:
        parse_packet_diagram("packet-beta\ntitle Only a title")
    assert exc_info.value.line == 1
    assert exc_info.value.message == "Packet diagram requires at least one block"


# === range validity (grok L202-L207) ======================================


def test_parse_packet_end_before_start_raises() -> None:
    """``end < start`` raises the invalid-range message (grok L202-L207)."""
    with pytest.raises(ParseError) as exc_info:
        parse_packet_diagram('packet-beta\n5-2: "A"')
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Packet block 5-2 is invalid (end < start)"


# === contiguity (grok L241-L262) ==========================================


def test_parse_packet_non_contiguous_raises_line_one() -> None:
    """A gap between blocks raises the contiguity message pinning line 1."""
    # Block 0-3 ends at bit 3; next block starts at 5 (should start at 4).
    with pytest.raises(ParseError) as exc_info:
        parse_packet_diagram('packet-beta\n0-3: "A"\n5-7: "B"')
    assert exc_info.value.line == 1
    assert exc_info.value.message == (
        "Packet block 5-7 is not contiguous. It should start from 4."
    )


def test_parse_packet_contiguous_blocks_accepted() -> None:
    """Contiguous blocks (each start == prior end + 1) parse without error."""
    diagram = parse_packet_diagram('packet-beta\n0-3: "A"\n4-7: "B"\n8: "C"')
    assert len(diagram.rows[0]) == 3


# === row split (grok L264-L323) ===========================================


def test_parse_packet_block_crossing_row_boundary_splits() -> None:
    """A block crossing the 32-bit row boundary splits across two rows.

    A single 0-35 block (36 bits) spans row 1 (bits 0-31) and row 2 (bits
    32-35); grok's ``split_block_at_row_boundary`` cuts it into a fitting
    fragment (0-31) and a remainder (32-35). The diagram ends up with two
    rows; each fragment inherits the block's label.
    """
    diagram = parse_packet_diagram('packet-beta\n0-35: "Big"')
    assert len(diagram.rows) == 2
    assert diagram.rows[0] == [PacketBlock(start=0, end=31, label="Big")]
    assert diagram.rows[1] == [PacketBlock(start=32, end=35, label="Big")]


def test_parse_packet_full_row_then_single_bit() -> None:
    """A full 32-bit block fills row 1 exactly; the next block opens row 2.

    Block 0-31 fills row 1 (word[-1].end+1 == 32 closes the row); block 32
    opens row 2. Mirrors grok's word-completion branch (L276-L282).
    """
    diagram = parse_packet_diagram('packet-beta\n0-31: "Row1"\n32: "Row2"')
    assert len(diagram.rows) == 2
    assert diagram.rows[0] == [PacketBlock(start=0, end=31, label="Row1")]
    assert diagram.rows[1] == [PacketBlock(start=32, end=32, label="Row2")]


# === geometry (grok L17-L26, L46-L82) =====================================


def test_render_packet_viewbox_dims_drop_trailing_dot_zero() -> None:
    """The viewBox dims route through Rust ``Display`` (integer floats drop ``.0``).

    Canonical fixture: one row, no title -> svg_height = 47*2 - 32 = 62,
    svg_width = 32*32 + 2 = 1026. Both render as bare integers.
    """
    svg = render_packet_diagram_to_svg(
        'packet-beta\n0-3: "Header"\n4-7: "Payload"\n8: "CRC"',
        MermaidTheme.default(),
    )
    assert 'viewBox="0 0 1026 62"' in svg


def test_render_packet_viewbox_grows_with_rows() -> None:
    """An extra row adds one ``total_row_height`` (47) to the canvas height.

    Two rows, no title -> svg_height = 47*3 - 32 = 109.
    """
    svg = render_packet_diagram_to_svg(
        'packet-beta\n0-31: "Row1"\n32: "Row2"', MermaidTheme.default()
    )
    assert 'viewBox="0 0 1026 109"' in svg


def test_render_packet_title_reserves_extra_row_height() -> None:
    """A present title drops the ``- ROW_HEIGHT`` term -> 47px taller canvas.

    One row, with title -> svg_height = 47*2 - 0 = 94 (vs. 62 without title).
    """
    svg = render_packet_diagram_to_svg(
        'packet-beta\ntitle T\n0-3: "Header"', MermaidTheme.default()
    )
    assert 'viewBox="0 0 1026 94"' in svg


def test_render_packet_block_rect_geometry() -> None:
    """The first block's rect has the computed x/y/width/height (grok L49-L55).

    Block 0-3: block_x = 1 + 0*32 = 1; width = 4*32 - 5 = 123; word_y (row 0)
    = 0*47 + 15 = 15; height = 32.
    """
    svg = render_packet_diagram_to_svg(
        'packet-beta\n0-3: "Header"', MermaidTheme.default()
    )
    assert 'class="packetBlock" x="1" y="15" width="123" height="32"' in svg


def test_render_packet_block_x_advances_with_start() -> None:
    """A block starting at bit 8 has block_x = 1 + 8*32 = 257 (grok L49)."""
    svg = render_packet_diagram_to_svg(
        'packet-beta\n0-7: "A"\n8: "B"', MermaidTheme.default()
    )
    assert 'x="257"' in svg


# === show_bits (grok L64-L82) =============================================


def test_render_packet_single_bit_emits_one_centered_index() -> None:
    """A single-bit block emits one centered bit index (grok L66-L70).

    Block 8: label_x = 257 + 27/2 = 270.5; the index text uses
    ``text-anchor="middle"`` and the start class.
    """
    svg = render_packet_diagram_to_svg(
        'packet-beta\n8: "CRC"', MermaidTheme.default()
    )
    assert 'class="packetByte start" x="270.5"' in svg
    assert ">8</text>" in svg
    # No end index for a single-bit block.
    assert 'class="packetByte end"' not in svg


def test_render_packet_multi_bit_emits_start_and_end_indices() -> None:
    """A multi-bit block emits a left start index and a right end index (grok L71-L81).

    Block 0-3: start at x=1 (text-anchor="start"), end at x = 1 + 123 = 124
    (text-anchor="end").
    """
    svg = render_packet_diagram_to_svg(
        'packet-beta\n0-3: "Header"', MermaidTheme.default()
    )
    assert 'class="packetByte start" x="1" y="13" text-anchor="start"' in svg
    assert 'class="packetByte end" x="124" y="13" text-anchor="end"' in svg
    assert ">0</text>" in svg
    assert ">3</text>" in svg


def test_render_packet_label_is_centered() -> None:
    """The block label is centered in the rect (grok L57-L62).

    Block 0-3: label_x = 1 + 123/2 = 62.5; label_y = 15 + 16 = 31.
    """
    svg = render_packet_diagram_to_svg(
        'packet-beta\n0-3: "Header"', MermaidTheme.default()
    )
    assert 'class="packetLabel" x="62.5" y="31"' in svg
    assert ">Header</text>" in svg


# === title emission (grok L87-L96) ========================================


def test_render_packet_title_emitted_centered() -> None:
    """The title is centered in the bottom row (grok L87-L96).

    One row, title present -> svg_height = 94; title_x = 1026/2 = 513;
    title_y = 94 - 47/2 = 70.5.
    """
    svg = render_packet_diagram_to_svg(
        'packet-beta\ntitle My Packet\n0-3: "Header"', MermaidTheme.default()
    )
    assert 'class="packetTitle" x="513" y="70.5"' in svg
    assert ">My Packet</text>" in svg


def test_render_packet_no_title_emits_empty_text() -> None:
    """With no title, an empty ``<text>`` is still emitted (grok unwrap_or_default)."""
    svg = render_packet_diagram_to_svg(
        'packet-beta\n0-3: "Header"', MermaidTheme.default()
    )
    assert 'class="packetTitle"' in svg
    # The title text element is empty between the tags.
    assert 'dominant-baseline="middle"></text>' in svg


def test_render_packet_title_is_xml_escaped() -> None:
    """The title text is XML-escaped (grok L94 -> escape_xml)."""
    svg = render_packet_diagram_to_svg(
        'packet-beta\ntitle A & B <C>\n0-3: "X"', MermaidTheme.default()
    )
    assert "A &amp; B &lt;C&gt;" in svg


def test_render_packet_label_is_xml_escaped() -> None:
    """The block label is XML-escaped (grok L61 -> escape_xml)."""
    svg = render_packet_diagram_to_svg(
        'packet-beta\n0-3: "A & B"', MermaidTheme.default()
    )
    assert "A &amp; B" in svg


# === escape_xml (grok L325-L331) ==========================================


def test_escape_xml_replaces_all_five_significant_chars() -> None:
    """The five XML-significant characters become entities (grok packet variant).

    grok's ``packet_diagram.rs`` maps ``'`` -> ``&apos;`` (XML named entity),
    same as the pie renderer; the radar renderer uses ``&#39;`` (numeric).
    All three faithfully mirror their respective grok source.
    """
    assert _escape_xml("a&b<c>d\"e'f") == "a&amp;b&lt;c&gt;d&quot;e&apos;f"


def test_escape_xml_ampersand_not_double_escaped() -> None:
    """``&`` is escaped first so it does not double-escape introduced entities."""
    assert _escape_xml("<") == "&lt;"


# === float formatting bridge (_fmt) =======================================


def test_fmt_integer_valued_float_drops_trailing_dot_zero() -> None:
    """An integer-valued float formats as a bare integer (Rust Display bridge)."""
    assert _fmt(1026.0) == "1026"
    assert _fmt(0.0) == "0"


def test_fmt_non_integer_uses_repr() -> None:
    """A non-integer float uses ``repr`` (shortest round-trippable decimal)."""
    assert _fmt(62.5) == "62.5"
    assert _fmt(270.5) == "270.5"


# === u32 parse bridge (_parse_u32) ========================================


def test_parse_u32_accepts_digit_only() -> None:
    """A digit-only token parses to its int value (mirrors Rust u32::from_str)."""
    assert _parse_u32("12", 1, kind="bit") == 12
    assert _parse_u32(" 12 ", 1, kind="bit") == 12  # surrounding whitespace trimmed


def test_parse_u32_rejects_plus_prefix() -> None:
    """``+12`` rejects -- Rust u32::from_str does not accept a ``+`` sign."""
    with pytest.raises(ParseError) as exc_info:
        _parse_u32("+12", 2, kind="bit")
    assert exc_info.value.line == 2
    assert exc_info.value.message == "Invalid packet bit index: +12"


def test_parse_u32_rejects_underscore_grouping() -> None:
    """``1_2`` rejects -- Rust u32::from_str does not accept ``_`` grouping."""
    with pytest.raises(ParseError) as exc_info:
        _parse_u32("1_2", 1, kind="bit")
    assert exc_info.value.message == "Invalid packet bit index: 1_2"


def test_parse_u32_rejects_empty() -> None:
    """An empty token rejects (after trim) -- mirrors Rust u32::from_str."""
    with pytest.raises(ParseError) as exc_info:
        _parse_u32("", 1, kind="bit")
    assert exc_info.value.message == "Invalid packet bit index: "


def test_parse_u32_kind_does_not_affect_success_return() -> None:
    """``kind`` selects only the error wording -- success returns the same int for any kind."""
    assert _parse_u32("5", 1, kind="start") == 5
    assert _parse_u32("5", 1, kind="end") == 5
    assert _parse_u32("5", 1, kind="bit") == 5


def test_parse_u32_kind_selects_message_wording() -> None:
    """The ``kind`` selects start / end / bit-index error wording (grok L192/L199/L214)."""
    with pytest.raises(ParseError) as exc_info:
        _parse_u32("x", 1, kind="start")
    assert exc_info.value.message == "Invalid packet start: x"
    with pytest.raises(ParseError) as exc_info:
        _parse_u32("x", 1, kind="end")
    assert exc_info.value.message == "Invalid packet end: x"


# === geometry constants (grok L4-L9) ======================================


def test_packet_constants_match_grok() -> None:
    """The six geometry constants are carried verbatim from grok L4-L9."""
    assert DEFAULT_ROW_HEIGHT == 32.0
    assert DEFAULT_BIT_WIDTH == 32.0
    assert DEFAULT_BITS_PER_ROW == 32
    assert DEFAULT_SHOW_BITS is True
    assert DEFAULT_PADDING_X == 5.0
    assert DEFAULT_PADDING_Y == 5.0
    # DEFAULT_BITS_PER_ROW stays int -- it is a modulo / row-counter operand,
    # not an SVG coord (mirrors grok's u32 vs f64 split).
    assert isinstance(DEFAULT_BITS_PER_ROW, int)


# === module surface =======================================================


def test_packet_diagram_module_all_is_single_public_symbol() -> None:
    """The leaf exports exactly its one public symbol (grok ``pub fn``)."""
    assert packet_mod.__all__ == ["render_packet_diagram_to_svg"]


def test_packet_diagram_symbol_is_not_in_to_svg_barrel() -> None:
    """The renderer stays out of the barrel -- dispatch-only reach (R283).

    grok's ``lib.rs`` never re-exports ``packet_diagram``'s symbols at the
    crate root; the renderer is invoked only via the packet dispatch arm. The
    barrel ``__all__`` does not grow for R283.
    """
    assert "render_packet_diagram_to_svg" not in to_svg.__all__
    assert not hasattr(to_svg, "render_packet_diagram_to_svg")


def test_packet_diagram_dataclasses_are_frozen() -> None:
    """The two dataclasses mirror grok's immutable structs (frozen=True)."""
    block = PacketBlock(start=0, end=3, label="A")
    diagram = PacketDiagram(title=None, rows=[[block]])
    assert diagram.rows == [[block]]
    with pytest.raises(FrozenInstanceError):
        block.start = 5  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        diagram.title = "x"  # type: ignore[misc]


# === front-matter dispatch (R283 contract lift) ===========================


def test_render_mermaid_to_svg_packet_strips_frontmatter_before_dispatch() -> None:
    """R283: dispatch passes the front-matter-stripped body to the packet arm.

    Mirrors grok lib.rs L47 (``let mermaid_source = parsed_source.body``) --
    the per-diagram arm receives the body, not the raw source. Without the
    strip, the ``---`` fence would reach ``parse_packet_diagram`` and break
    the header scan; the rendered SVG proves dispatch saw the clean body.
    Same lift applied to the info / radar / pie arms for dispatch consistency.
    """
    source = '---\ntitle: Demo\n---\npacket-beta\n0-3: "Header"\n4-7: "Payload"\n8: "CRC"'
    svg = render_mermaid_to_svg(source)
    assert "<svg" in svg
    assert "packetBlock" in svg
