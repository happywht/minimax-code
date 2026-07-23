"""Mermaid-to-svg text measurement + wrapping -- fusion of grok
``mermaid-to-svg/src/text_wrap.rs``.

Direction (1) leaf 5. The text-measurement primitive that both ``layout.rs``
(node-box sizing) and ``svg_renderer.rs`` (label placement) consume. Mirrors
mermaid.js ``splitText.ts`` / ``createText.ts`` width estimation: a label is
split into wrapped lines whose estimated display width fits a target, with a
single unbreakable token kept whole so its box can widen (matching mermaid's
default ``htmlLabels``). ``layout.rs`` (the next leaf) consumes
:func:`measure_wrapped_lines_with_font_size`, :func:`scale_char_width`,
:func:`wrap_text_lines`, :data:`DEFAULT_CHAR_WIDTH`, :data:`DEFAULT_FONT_SIZE`,
:data:`DEFAULT_WRAP_WIDTH` -- exactly the symbols grok's ``layout.rs`` imports.

Dependency mapping (Rust crate -> Python stdlib)
-------------------------------------------------

grok leans on two Unicode crates. This module reaches the same semantics with
the standard library alone -- no new dependency is added (the agent
``pyproject.toml`` deliberately keeps a tight dependency set):

* ``unicode_width::UnicodeWidthStr::width`` -> :func:`_char_width` via
  :func:`unicodedata.east_asian_width` (East-Asian Wide/Fullwidth count as 2;
  everything else as 1) with :func:`unicodedata.category` zeroing Cc control
  chars and nonspacing/enclosing combining marks (``Mn``/``Me``), mirroring the
  unicode-width crate's ``None`` -> 0 advance rule. grok's test
  ``display_width_units("中") == 2.0`` holds.
* ``unicode_segmentation::UnicodeSegmentation::graphemes(true)`` ->
  :func:`_iter_graphemes`, a UAX #29 *approximation* that merges nonspacing /
  enclosing combining marks onto the preceding base char. NFC label text (the
  mermaid-node-label common case) is handled exactly; only emoji-ZWJ sequences
  (vanishingly rare in flowchart labels) diverge.

Type mapping
------------

* ``f64`` -> ``float``; ``usize`` -> ``int``.
* ``Vec<String>`` -> ``list[str]``; ``Vec<Vec<String>>`` -> ``list[list[str]]``.
* ``Option<f64>`` / ``(f64, f64)`` -> ``float | None`` / ``tuple[float, float]``.
* ``std::collections::VecDeque`` with ``pop_front`` / ``push_front`` ->
  :class:`collections.deque` with ``popleft`` / ``appendleft``.
* ``line_count.saturating_sub(1)`` -> ``max(line_count - 1, 0)`` (the early
  return on ``line_count == 0`` makes the clamp defensive, matching grok).

Internal module
---------------

grok declares ``mod text_wrap;`` (private) -- consumed by ``layout.rs`` and
``svg_renderer.rs``, never ``pub use``-d at the crate root. This module mirrors
that: it has its own ``__all__`` (7 functions + 5 constants) but is NOT
re-exported through the ``to_svg`` barrel (the barrel tracks grok's crate-root
public surface, which omits ``text_wrap``). Future ``layout.py`` consumes it
via ``from .text_wrap import ...``.
"""

from __future__ import annotations

import math
import unicodedata
from collections import deque
from collections.abc import Iterator

# === public constants (grok ``pub const``, 5 f64) ==========================

DEFAULT_FONT_SIZE: float = 16.0
DEFAULT_LINE_HEIGHT: float = 1.1
DEFAULT_WRAP_WIDTH: float = 200.0
DEFAULT_CHAR_WIDTH: float = 8.0
DEFAULT_TEXT_HEIGHT: float = 24.0

# === private constants =====================================================

# A single unbreakable token is kept whole (its box widens to fit it, matching
# mermaid's default htmlLabels) unless it is wider than this many wrap-widths.
# ~5x keeps the worst-case whole-token box near one target-width frame.
_SINGLE_TOKEN_WIDTH_CAP_FACTOR: float = 5.0

# Identifier-boundary characters preferred as break points when an over-cap
# token must be split (mirrors grok ``[char; 4]``; frozenset for O(1) lookup).
_TOKEN_BREAK_CHARS: frozenset[str] = frozenset({"_", "-", ".", "/"})


# === display-width primitives (unicode_width crate -> stdlib) ==============


def _char_width(ch: str) -> int:
    """Display column width of one code point (mirrors ``unicode_width::width``).

    East-Asian Wide/Fullwidth -> 2; nonspacing/enclosing combining marks
    (``Mn``/``Me``) and Cc control chars -> 0 (zero advance, like the crate's
    ``None`` rule); everything else -> 1.
    """
    category = unicodedata.category(ch)
    if category == "Cc" or category in ("Mn", "Me"):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def display_width_units(text: str) -> float:
    """Display width of ``text`` in narrow-character units.

    East Asian wide characters count as two (grok ``UnicodeWidthStr::width``).
    Empty ``text`` -> 0.
    """
    return float(sum(_char_width(ch) for ch in text))


def line_width(line: str, char_width: float) -> float:
    """Estimated rendered width of ``line`` at ``char_width`` px/unit.

    Empty ``line`` -> 0 (mirrors grok's empty-string short-circuit).
    """
    if not line:
        return 0.0
    return display_width_units(line) * char_width


def line_width_words(words: list[str], char_width: float) -> float:
    """Estimated rendered width of ``words`` joined by single spaces."""
    return line_width(_join_words(words), char_width)


# === font-size-aware sizing ================================================


def _normalized_font_size(font_size: float) -> float:
    """Fall back to :data:`DEFAULT_FONT_SIZE` for non-finite / non-positive sizes."""
    if math.isfinite(font_size) and font_size > 0.0:
        return font_size
    return DEFAULT_FONT_SIZE


def scale_char_width(char_width: float, font_size: float) -> float:
    """Scale ``char_width`` linearly with ``font_size`` (anchored at the default)."""
    return char_width * _normalized_font_size(font_size) / DEFAULT_FONT_SIZE


def wrapped_text_height_with_font_size(line_count: int, font_size: float) -> float:
    """Height of ``line_count`` wrapped lines at ``font_size``.

    The first line carries the box text height
    (``DEFAULT_TEXT_HEIGHT * size / DEFAULT_FONT_SIZE``); each extra line adds
    one line spacing (``size * DEFAULT_LINE_HEIGHT``). Zero lines -> 0.
    """
    if line_count == 0:
        return 0.0
    size = _normalized_font_size(font_size)
    text_height = DEFAULT_TEXT_HEIGHT * size / DEFAULT_FONT_SIZE
    line_spacing = size * DEFAULT_LINE_HEIGHT
    return text_height + max(line_count - 1, 0) * line_spacing


def measure_wrapped_lines_with_font_size(
    lines: list[list[str]], char_width: float, font_size: float
) -> tuple[float, float]:
    """Return ``(width, height)`` of wrapped ``lines`` at ``char_width`` / ``font_size``.

    Width is the widest sub-line; height delegates to
    :func:`wrapped_text_height_with_font_size`. Empty ``lines`` -> ``(0.0, 0.0)``.
    """
    max_width = max(
        (line_width_words(line, char_width) for line in lines), default=0.0
    )
    return (max_width, wrapped_text_height_with_font_size(len(lines), font_size))


# === wrapping core (mermaid splitText.ts mirror) ===========================


def wrap_text_lines(text: str, max_width: float, char_width: float) -> list[list[str]]:
    """Split ``text`` into wrapped lines fitting ``max_width`` at ``char_width``.

    Mirrors mermaid.js ``splitText.ts splitLineToFitWidth`` for non-markdown
    labels. Each ``\\n``-separated raw line is trimmed, split to words, then
    greedily packed into sub-lines. A blank trimmed line emits a single empty
    sub-line. A non-finite ``max_width`` is treated as unbounded (infinity).
    Empty ``text`` -> ``[]``.
    """
    if not text:
        return []
    width = max_width if math.isfinite(max_width) else math.inf

    lines: list[list[str]] = []
    for raw_line in text.split("\n"):
        trimmed = raw_line.strip()
        if not trimmed:
            lines.append([""])
            continue
        words = _split_line_to_words(trimmed)
        wrapped = _split_line_to_fit_width(words, width, char_width)
        lines.extend(wrapped)
    return lines


def _split_line_to_words(text: str) -> list[str]:
    """Split a single line on runs of whitespace; empty input -> ``[""]``."""
    words = text.split()
    if not words:
        return [""]
    return words


def _split_line_to_fit_width(
    words: list[str], max_width: float, char_width: float
) -> list[list[str]]:
    """Greedily pack ``words`` into sub-lines fitting ``max_width``.

    A word that alone exceeds ``max_width`` is kept whole when within the cap
    (``max_width * _SINGLE_TOKEN_WIDTH_CAP_FACTOR``); over-cap tokens are
    force-broken on an identifier boundary, else on a grapheme boundary.
    """
    remaining: deque[str] = deque(words)
    lines: list[list[str]] = []
    current: list[str] = []

    while True:
        if not remaining:
            if current:
                lines.append(current)
            break

        next_word = remaining.popleft()
        line_with_next = current + [next_word]
        if _check_fit(line_with_next, max_width, char_width):
            current = line_with_next
            continue

        if current:
            lines.append(current)
            current = []
            remaining.appendleft(next_word)
            continue

        if next_word:
            cap = max_width * _SINGLE_TOKEN_WIDTH_CAP_FACTOR
            if line_width(next_word, char_width) <= cap:
                lines.append([next_word])
            else:
                first, rest = _split_token_at_cap(next_word, cap, char_width)
                lines.append([first])
                if rest:
                    remaining.appendleft(rest)
    return lines


def _check_fit(words: list[str], max_width: float, char_width: float) -> bool:
    """Whether ``words`` joined fit within ``max_width``."""
    return line_width_words(words, char_width) <= max_width


def _join_words(words: list[str]) -> str:
    """Join ``words`` with single spaces (no trailing/leading space)."""
    return " ".join(words)


# === over-cap token splitting (grapheme + identifier-boundary aware) =======


def _iter_graphemes(text: str) -> Iterator[str]:
    """Yield grapheme clusters (UAX #29 approximation).

    Merges nonspacing/enclosing combining marks (Unicode category ``Mn``/``Me``)
    onto the preceding base char -- mirrors rust ``unicode_segmentation``
    ``graphemes(true)`` for the label text this stack handles (NFC text + CJK).
    Emoji ZWJ sequences are not specially clustered (rare in mermaid labels).
    """
    current = ""
    for ch in text:
        if unicodedata.category(ch) in ("Mn", "Me") and current:
            current += ch
        else:
            if current:
                yield current
            current = ch
    if current:
        yield current


def _split_word_to_fit_width(
    word: str, max_width: float, char_width: float
) -> tuple[str, str]:
    """Grapheme-prefix of ``word`` fitting ``max_width``; guarantees >=1 grapheme.

    Returns ``(used, remaining)``. The first grapheme is always kept (forward
    progress), mirroring grok's ``|| used.is_empty()`` clause. Empty ``word``
    -> ``("", "")``.
    """
    graphemes = list(_iter_graphemes(word))
    if not graphemes:
        return ("", "")

    used: list[str] = []
    remaining_start = len(graphemes)
    for idx, grapheme in enumerate(graphemes):
        candidate = used + [grapheme]
        candidate_str = "".join(candidate)
        if line_width(candidate_str, char_width) <= max_width or not used:
            used = candidate
            continue
        remaining_start = idx
        break

    if not used:
        used = [graphemes[0]]
        remaining_start = 1

    remaining = (
        "".join(graphemes[remaining_start:]) if remaining_start < len(graphemes) else ""
    )
    return ("".join(used), remaining)


def _split_token_at_cap(word: str, cap: float, char_width: float) -> tuple[str, str]:
    """Split an over-cap token: prefer the last identifier boundary within the
    graphemic prefix, else fall back to the grapheme break.

    Break chars are single-byte ASCII, so ``boundary + 1`` is a valid char
    boundary that keeps the separator on the first line.
    """
    graphemic_first, graphemic_rest = _split_word_to_fit_width(word, cap, char_width)
    boundary = -1
    for break_char in _TOKEN_BREAK_CHARS:
        pos = graphemic_first.rfind(break_char)
        if pos > boundary:
            boundary = pos
    if boundary != -1:
        split_pos = boundary + 1
        return (word[:split_pos], word[split_pos:])
    return (graphemic_first, graphemic_rest)


__all__ = [
    "DEFAULT_CHAR_WIDTH",
    "DEFAULT_FONT_SIZE",
    "DEFAULT_LINE_HEIGHT",
    "DEFAULT_TEXT_HEIGHT",
    "DEFAULT_WRAP_WIDTH",
    "display_width_units",
    "line_width",
    "line_width_words",
    "measure_wrapped_lines_with_font_size",
    "scale_char_width",
    "wrapped_text_height_with_font_size",
    "wrap_text_lines",
]
