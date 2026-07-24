"""Packet diagram renderer -- behavioral-equivalent port of grok's ``packet_diagram.rs``.

Direction (1) brick 16 (R283). The fifth per-diagram leaf and the fourth
per-diagram *renderer* (a self-contained SVG emitter, like R279's ``info``,
R281's ``radar``, and R282's ``pie``; unlike R280's ``stateDiagram`` parser
which rides the dagre stack). The ``packet-beta`` diagram is mermaid's
network-packet / bit-field visualizer: a sequence of contiguous bit ranges
``<start>-<end>: "<label>"`` laid out as labeled rectangles on a 32-bit-per-
row grid, each block annotated with its start / end bit index. Pure grid
geometry, so this leaf emits SVG directly from the parsed model -- no AST,
no dagre.

Behavioral-equivalence mapping (function-not-line)
--------------------------------------------------

* grok ``pub fn render_packet_diagram_to_svg(src, theme) -> Result<String,
  MermaidError>`` -> ``render_packet_diagram_to_svg(src, theme) -> str``.
  grok's ``Result<...>`` (``Ok(svg)`` / ``Err(ParseError)``) maps to
  "return the SVG / raise :class:`ParseError`".
* grok's two private structs ``PacketDiagram`` / ``PacketBlock`` ->
  :class:`PacketDiagram` / :class:`PacketBlock` as ``frozen`` dataclasses
  (grok's are plain ``struct``s; frozen mirrors their construct-once use).
* grok ``parse_packet_diagram`` / ``parse_range`` / ``parse_label`` /
  ``ensure_contiguous`` / ``split_into_rows`` /
  ``split_block_at_row_boundary`` / ``escape_xml`` -> module-level Python
  functions (``parse_packet_diagram`` public for direct testing; the rest
  private with a leading underscore).
* grok's six geometry constants (row height / bit width / bits-per-row /
  show-bits / padding-x / padding-y) -> module constants verbatim. The
  renderer hard-codes ``#efefef`` block fills and ``black`` strokes / text
  (grok ignores the theme), so the ``_theme`` param is unused -- mirrors
  R282's pie.

Float formatting bridge
-----------------------

Like R281's radar and R282's pie, integer-valued floats (viewBox dims,
block geometry) route through :func:`_fmt`, which collapses ``1026.0`` ->
``"1026"`` (Rust ``Display`` drops the trailing ``.0``; Python ``str``
keeps it). Bit indices are ``u32`` in grok and stay Python ``int`` in the
port, so they render as bare decimals without :func:`_fmt`.

u32 parse bridge
----------------

grok parses bit indices with ``u32::from_str``, which accepts only
``[0-9]+`` (rejects ``+12``, ``1_2``, ``-5``, ``1.5``, empty). Python
``int()`` is looser (accepts ``+12`` and ``1_2``), so :func:`_parse_u32`
gates on a strict ``\\d+`` full-match to mirror Rust. grok's
``saturating_add`` / ``saturating_mul`` in the row-split never saturate for
real packet sizes (bit indices are tiny), so the port uses plain Python
``int`` -- a documented fidelity non-issue (values are far below u32::MAX).

Dispatch wiring (R283)
----------------------

The dispatch arm in :func:`render.render_mermaid_to_svg` passes the
front-matter-stripped ``body`` to this renderer, mirroring grok lib.rs L47
(the per-diagram dispatch contract lifted in R282). The token
``packet-beta`` leaves ``_UNSUPPORTED_DIAGRAM_TYPES`` (20 -> 19 tokens).

Public surface (1 symbol): :func:`render_packet_diagram_to_svg`. The
dataclasses, parser, and helpers stay out of ``__all__``; the renderer is
reached only through the dispatch arm added in R283.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .error import ParseError
from .theme import MermaidTheme

__all__ = ["render_packet_diagram_to_svg"]

#: Packet-grid geometry (grok L4-L9). Each row holds 32 bits; each bit is
#: 32px wide; rows are 32px tall plus a 15px bit-index gutter (padding_y
#: = 5 + 10 because show_bits is always on). Carried as ``float`` because
#: the SVG viewBox / block geometry is emitted via Rust ``Display`` (drops
#: ``.0``); :func:`_fmt` reproduces that rendering. ``DEFAULT_BITS_PER_ROW``
#: stays ``int`` -- it is a modulo / row-counter operand, not an SVG coord.
DEFAULT_ROW_HEIGHT: float = 32.0
DEFAULT_BIT_WIDTH: float = 32.0
DEFAULT_BITS_PER_ROW: int = 32
DEFAULT_SHOW_BITS: bool = True
DEFAULT_PADDING_X: float = 5.0
DEFAULT_PADDING_Y: float = 5.0


@dataclass(frozen=True)
class PacketBlock:
    """One contiguous bit range (grok ``PacketBlock``).

    ``start`` / ``end`` are inclusive bit indices (``u32`` in grok, ``int``
    here); ``label`` is the rectangle's centered text.
    """

    start: int
    end: int
    label: str


@dataclass(frozen=True)
class PacketDiagram:
    """Parsed packet model (grok ``PacketDiagram``).

    ``title`` is optional (mermaid ``title <text>`` line); ``rows`` is the
    block list split into 32-bit-per-row rows (the split runs at parse, not
    render -- mirrors grok building ``rows`` in ``parse_packet_diagram``).
    """

    title: str | None
    rows: list[list[PacketBlock]]


def _fmt(value: float) -> str:
    """Render a float the way Rust ``Display`` would (R281/R282 bridge).

    Integer-valued floats drop the trailing ``.0`` (``1026.0`` -> ``"1026"``);
    non-integers fall back to ``repr`` (shortest round-trippable decimal,
    matching Rust's ``Display`` for f64).
    """
    if value == int(value):
        return str(int(value))
    return repr(value)


def _escape_xml(s: str) -> str:
    """Escape the five XML-significant characters (grok ``escape_xml``).

    Note: grok's ``packet_diagram.rs`` maps ``'`` -> ``&apos;`` (XML named
    entity), matching R282's pie and unlike R281's radar (``&#39;``). Both
    faithfully mirror their respective grok source -- the two Rust functions
    disagree.
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _parse_u32(raw: str, line: int, *, kind: str) -> int:
    """Parse a ``u32`` bit index the way Rust ``u32::from_str`` would.

    Accepts only ``[0-9]+`` (strict full-match) so ``+12`` / ``1_2`` /
    empty reject the way grok's ``u32::parse`` does -- Python ``int()`` is
    looser. ``kind`` selects the error-message wording (``start`` / ``end``
    / ``bit``); the message embeds ``raw`` verbatim (grok formats the
    pre-``.trim()`` token for the start/end arms, the trimmed token for the
    single-bit arm -- the caller controls which by what it passes).
    """
    s = raw.strip()
    if re.fullmatch(r"\d+", s):
        return int(s)
    if kind == "start":
        raise ParseError(line, f"Invalid packet start: {raw}")
    if kind == "end":
        raise ParseError(line, f"Invalid packet end: {raw}")
    raise ParseError(line, f"Invalid packet bit index: {raw}")


def _parse_range(s: str, line: int) -> tuple[int, int]:
    """Parse a ``start-end`` range or a single bit index (grok ``parse_range``).

    Splits on the first ``-``: a ``start-end`` pair parses both as ``u32``
    and requires ``end >= start``; a lone token parses as both start and
    end. The pair arms pass the *untrimmed* substring to :func:`_parse_u32`
    so the error message embeds it verbatim (mirrors grok's
    ``format!("Invalid packet start: {start_str}")`` capturing the
    pre-``.trim()`` value); the single-bit arm passes the already-trimmed
    token (grok parses ``s`` itself, post-trim).
    """
    s = s.strip()
    start_str, sep, end_str = s.partition("-")
    if sep:
        start = _parse_u32(start_str, line, kind="start")
        end = _parse_u32(end_str, line, kind="end")
        if end < start:
            raise ParseError(line, f"Packet block {start}-{end} is invalid (end < start)")
        return (start, end)
    start = _parse_u32(s, line, kind="bit")
    return (start, start)


def _parse_label(s: str, line: int) -> str:
    """Parse a block label, stripping one surrounding quote pair (grok ``parse_label``).

    A surrounding ``"..."`` or ``'...'`` pair is stripped; an unquoted,
    non-empty token is taken verbatim; an empty unquoted token raises.
    Mirrors grok's ``strip_prefix(q).and_then(strip_suffix(q))`` -- the
    ``len >= 2`` guard is the Python equivalent (a lone quote does not pair).
    """
    s = s.strip()
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    if len(s) >= 2 and s.startswith("'") and s.endswith("'"):
        return s[1:-1]
    if not s:
        raise ParseError(line, "Packet block label cannot be empty")
    return s


def _ensure_contiguous(blocks: list[PacketBlock]) -> None:
    """Verify each block's start is one past the prior block's end (grok ``ensure_contiguous``).

    Raises pinning line 1 -- the contiguity check is cross-line, so grok
    reports the diagram-level line rather than any one block's line.
    """
    last: int | None = None
    for block in blocks:
        if last is not None and block.start != last + 1:
            raise ParseError(
                1,
                f"Packet block {block.start}-{block.end} is not contiguous. "
                f"It should start from {last + 1}.",
            )
        last = block.end


def _split_block_at_row_boundary(
    block: PacketBlock, row: int, bits_per_row: int
) -> tuple[PacketBlock, PacketBlock | None]:
    """Split a block at the current row's end boundary (grok ``split_block_at_row_boundary``).

    If the block ends inside the current row (``end + 1 <= row *
    bits_per_row``), returned whole with no remainder. Otherwise split into
    a first fragment ending at the row's last bit and a second fragment
    starting the next row (both inheriting the block's label).
    """
    row_end_exclusive = row * bits_per_row
    if block.end + 1 <= row_end_exclusive:
        return (block, None)
    first = PacketBlock(
        start=block.start,
        end=row_end_exclusive - 1,
        label=block.label,
    )
    second = PacketBlock(
        start=row_end_exclusive,
        end=block.end,
        label=block.label,
    )
    return (first, second)


def _split_into_rows(
    blocks: list[PacketBlock], bits_per_row: int
) -> list[list[PacketBlock]]:
    """Lay blocks out on the ``bits_per_row``-wide grid (grok ``split_into_rows``).

    Walks the contiguous block list; a block crossing a row boundary is
    split into a fitting fragment (pushed to the current word) and a
    remainder (carried to the next loop iteration on the next row). A word
    whose last block reaches the row's final bit closes the row and
    advances the row counter. The trailing partial word becomes the final
    row. ``bits_per_row`` stays ``int`` -- the modulo / multiplication
    operands mirror grok's ``u32`` row math (never saturates for real
    packet sizes).
    """
    rows: list[list[PacketBlock]] = []
    word: list[PacketBlock] = []
    row = 1

    for block in blocks:
        cur = block
        while True:
            fitting, remainder = _split_block_at_row_boundary(cur, row, bits_per_row)
            word.append(fitting)
            # A word whose last block reaches the row's final bit is full --
            # close it and advance to the next row (grok L276-L282).
            if word[-1].end + 1 == row * bits_per_row:
                rows.append(word)
                word = []
                row += 1
            if remainder is None:
                break
            cur = remainder

    if word:
        rows.append(word)

    return rows


def parse_packet_diagram(input: str) -> PacketDiagram:
    """Parse mermaid ``packet-beta`` source into a :class:`PacketDiagram` (grok ``parse_packet_diagram``).

    Scans for the ``packet-beta`` header token, then reads ``title <text>``
    and ``<range>:<label>`` block lines. Blank / ``%%`` lines are skipped.
    Blocks must be contiguous (each start one past the prior end) and at
    least one block is required.

    Raises:
        ParseError: when the first substantial token is not ``packet-beta``;
            a block line has no ``:`` separator; a range / index is not a
            ``u32``; a block label is empty; the diagram has zero blocks;
            or the blocks are not contiguous.
    """
    found_header = False
    title: str | None = None
    blocks: list[PacketBlock] = []

    for idx, raw in enumerate(input.splitlines()):
        line_no = idx + 1
        line = raw.strip()

        if not line or line.startswith("%%"):
            continue

        if not found_header:
            # grok L132: ``line.split_whitespace().next()`` -- the first
            # whitespace token must be the ``packet-beta`` declaration.
            if line.split()[0] != "packet-beta":
                raise ParseError(line_no, "Expected 'packet-beta' declaration")
            found_header = True
            continue

        # ``title <text>`` sets the diagram title (grok strip_prefix("title ")).
        if line.startswith("title "):
            rest = line[len("title ") :].strip()
            if rest:
                title = rest
            continue

        # Otherwise the line must be a ``<range>:<label>`` block.
        range_raw, sep, label_raw = line.partition(":")
        if not sep:
            raise ParseError(line_no, f"Invalid packet block: {line}")

        start, end = _parse_range(range_raw.strip(), line_no)
        label = _parse_label(label_raw.strip(), line_no)
        blocks.append(PacketBlock(start=start, end=end, label=label))

    if not found_header:
        raise ParseError(1, "Expected 'packet-beta' declaration")

    if not blocks:
        raise ParseError(1, "Packet diagram requires at least one block")

    _ensure_contiguous(blocks)
    rows = _split_into_rows(blocks, DEFAULT_BITS_PER_ROW)

    return PacketDiagram(title=title, rows=rows)


def render_packet_diagram_to_svg(mermaid_source: str, _theme: MermaidTheme) -> str:
    """Render a mermaid ``packet-beta`` diagram into an SVG string (grok
    ``render_packet_diagram_to_svg``).

    Parses the source, lays the contiguous bit ranges out on the
    32-bit-per-row grid, and emits a fixed-width (1026px) SVG: one
    ``#efefef`` rectangle per block fragment with a centered label and
    start / end bit-index annotations, plus an optional title centered in
    the bottom row. The canvas height grows with the row count (and one
    extra row reserved when a title is present).

    Args:
        mermaid_source: mermaid ``packet-beta`` source. The dispatch in
            :func:`render.render_mermaid_to_svg` passes the front-matter-
            stripped body (mirroring grok lib.rs L47, which shadows
            ``mermaid_source`` to ``parsed_source.body`` before the packet
            arm).
        _theme: the resolved :class:`MermaidTheme` palette. Currently unused
            -- grok's packet renderer hard-codes ``#efefef`` fills and
            ``black`` strokes / text and ignores the theme, so the port
            matches with ``_theme``.

    Returns:
        The packet-diagram SVG string.

    Raises:
        ParseError: propagated from :func:`parse_packet_diagram`.
    """
    diagram = parse_packet_diagram(mermaid_source)

    padding_y = DEFAULT_PADDING_Y + (10.0 if DEFAULT_SHOW_BITS else 0.0)
    total_row_height = DEFAULT_ROW_HEIGHT + padding_y

    svg_width = DEFAULT_BIT_WIDTH * DEFAULT_BITS_PER_ROW + 2.0
    svg_height = total_row_height * (len(diagram.rows) + 1) - (
        0.0 if diagram.title is not None else DEFAULT_ROW_HEIGHT
    )

    svg: list[str] = []

    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {_fmt(svg_width)} {_fmt(svg_height)}">'
    )
    svg.append(
        "<style>"
        ".packetByte{font-size:10px;}"
        ".packetByte.start{fill:black;}"
        ".packetByte.end{fill:black;}"
        ".packetLabel{fill:black;font-size:12px;}"
        ".packetTitle{fill:black;font-size:14px;}"
        ".packetBlock{stroke:black;stroke-width:1;fill:#efefef;}"
        "</style>"
    )

    svg.append("<g>")
    for row_idx, row in enumerate(diagram.rows):
        word_y = row_idx * total_row_height + padding_y

        for block in row:
            block_x = 1.0 + (block.start % DEFAULT_BITS_PER_ROW) * DEFAULT_BIT_WIDTH
            width = (block.end - block.start + 1) * DEFAULT_BIT_WIDTH - DEFAULT_PADDING_X

            svg.append(
                f'<rect class="packetBlock" x="{_fmt(block_x)}" y="{_fmt(word_y)}" '
                f'width="{_fmt(width)}" height="{_fmt(DEFAULT_ROW_HEIGHT)}"/>'
            )

            label_x = block_x + width / 2.0
            label_y = word_y + DEFAULT_ROW_HEIGHT / 2.0
            svg.append(
                f'<text class="packetLabel" x="{_fmt(label_x)}" y="{_fmt(label_y)}" '
                f'text-anchor="middle" dominant-baseline="middle">'
                f"{_escape_xml(block.label)}</text>"
            )

            if DEFAULT_SHOW_BITS:
                bit_y = word_y - 2.0
                if block.start == block.end:
                    # Single-bit block: one centered index (grok L66-L70).
                    svg.append(
                        f'<text class="packetByte start" x="{_fmt(label_x)}" '
                        f'y="{_fmt(bit_y)}" text-anchor="middle" dominant-baseline="auto">'
                        f"{block.start}</text>"
                    )
                else:
                    # Multi-bit block: start index at the left, end index at
                    # the right (grok L71-L81).
                    end_x = block_x + width
                    svg.append(
                        f'<text class="packetByte start" x="{_fmt(block_x)}" '
                        f'y="{_fmt(bit_y)}" text-anchor="start" dominant-baseline="auto">'
                        f"{block.start}</text>"
                    )
                    svg.append(
                        f'<text class="packetByte end" x="{_fmt(end_x)}" '
                        f'y="{_fmt(bit_y)}" text-anchor="end" dominant-baseline="auto">'
                        f"{block.end}</text>"
                    )
    svg.append("</g>")

    title_x = svg_width / 2.0
    title_y = svg_height - total_row_height / 2.0
    title_text = _escape_xml(diagram.title) if diagram.title is not None else ""
    svg.append(
        f'<text class="packetTitle" x="{_fmt(title_x)}" y="{_fmt(title_y)}" '
        f'text-anchor="middle" dominant-baseline="middle">{title_text}</text>'
    )

    svg.append("</svg>")
    return "".join(svg)
