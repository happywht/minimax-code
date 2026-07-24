"""Black-box tests for the migrated mermaid-to-svg text wrapper (R273).

Exercises :mod:`minimax_code.mermaid.to_svg.text_wrap` -- the text measurement
+ wrapping primitive fused from grok's ``mermaid-to-svg/src/text_wrap.rs``
(direction (1), leaf 5). This leaf is the hard prerequisite for both
``layout.rs`` (node-box sizing) and ``svg_renderer.rs`` (label placement).

Like grok's private ``mod text_wrap;`` (consumed by ``layout.rs`` /
``svg_renderer.rs``, never ``pub use``-d at the crate root), this module is
**internal**: it declares its own ``__all__`` (7 functions + 5 constants) but
is NOT re-exported through the ``to_svg`` barrel. Future ``layout.py`` will
consume it via ``from .text_wrap import ...``. Covers:

* the 5 public constants (exact f64 values from grok),
* the 7 public functions and the stdlib dependency mapping
  (:func:`display_width_units` via ``unicodedata.east_asian_width`` with
  combining-mark / control-char zeroing; :func:`wrap_text_lines` mirroring
  mermaid ``splitText.ts``),
* grok's 7 in-tree tests reproduced as behavioral assertions -- single long
  token kept whole, over-cap token force-broken on an identifier boundary
  (``_`` / ``-`` / ``.`` / ``/``), the grapheme-break fallback when no
  identifier boundary exists, and CJK wide-char counting (``中`` == 2 units),
* the barrel contract: ``text_wrap`` is internal -- reachable by deep path,
  NOT re-exported through the ``to_svg`` barrel; the ``mermaid`` root stays
  at 23.
"""

from __future__ import annotations

import math

import pytest

import minimax_code.mermaid as mermaid
import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import text_wrap as text_wrap_mod
from minimax_code.mermaid.to_svg.text_wrap import (
    DEFAULT_CHAR_WIDTH,
    DEFAULT_FONT_SIZE,
    DEFAULT_LINE_HEIGHT,
    DEFAULT_TEXT_HEIGHT,
    DEFAULT_WRAP_WIDTH,
    display_width_units,
    line_width,
    line_width_words,
    measure_wrapped_lines_with_font_size,
    scale_char_width,
    wrap_text_lines,
    wrapped_text_height_with_font_size,
)

# === public constants (exact f64 values from grok) =========================


def test_public_constants_match_grok_values() -> None:
    """The 5 ``pub const`` values mirror grok exactly."""
    assert DEFAULT_FONT_SIZE == 16.0
    assert DEFAULT_LINE_HEIGHT == 1.1
    assert DEFAULT_WRAP_WIDTH == 200.0
    assert DEFAULT_CHAR_WIDTH == 8.0
    assert DEFAULT_TEXT_HEIGHT == 24.0


# === display_width_units (unicode_width crate -> stdlib) ===================


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", 0.0),
        ("hello", 5.0),
        ("a", 1.0),
        ("AB", 2.0),
        ("中", 2.0),  # East-Asian Wide
        ("中文", 4.0),
        ("a中b", 4.0),  # 1 + 2 + 1
    ],
)
def test_display_width_units(text: str, expected: float) -> None:
    """Narrow chars count 1, East-Asian Wide/Fullwidth count 2, empty -> 0."""
    assert display_width_units(text) == expected


def test_display_width_units_zeroes_combining_marks() -> None:
    """A combining mark (Mn) contributes zero advance (mirrors unicode-width None).

    NFC ``é`` (U+00E9) is one code point of width 1; NFD ``e`` + U+0301 is two
    code points but the combining accent adds no width, so both forms measure 1.
    """
    assert display_width_units("é") == 1.0  # NFC precomposed
    assert display_width_units("é") == 1.0  # NFD e + combining acute
    assert display_width_units("café") == 4.0


def test_display_width_units_zeroes_control_chars() -> None:
    """Cc control chars (null, tab, newline) contribute zero advance."""
    assert display_width_units("\x00") == 0.0
    assert display_width_units("\t") == 0.0
    assert display_width_units("\n") == 0.0


# === line_width / line_width_words ========================================


def test_line_width_empty_is_zero() -> None:
    """Empty line short-circuits to 0 (mirrors grok)."""
    assert line_width("", DEFAULT_CHAR_WIDTH) == 0.0


def test_line_width_scales_with_char_width() -> None:
    """``line_width`` is display-width units times ``char_width``."""
    assert line_width("hello", 8.0) == 40.0
    assert line_width("hello", 10.0) == 50.0
    assert line_width("中", 8.0) == 16.0  # wide char


def test_line_width_words_joins_with_spaces() -> None:
    """``line_width_words`` joins words with single spaces before measuring."""
    # "a b" -> display width 3 (space counts as 1)
    assert line_width_words(["a", "b"], 8.0) == 24.0
    assert line_width_words([], 8.0) == 0.0


# === wrapped_text_height_with_font_size / scale_char_width ================


def test_wrapped_text_height_zero_lines_is_zero() -> None:
    """Zero lines -> height 0 (early return)."""
    assert wrapped_text_height_with_font_size(0, DEFAULT_FONT_SIZE) == 0.0


def test_wrapped_text_height_single_line_is_text_height() -> None:
    """One line at the default font size is just DEFAULT_TEXT_HEIGHT (24)."""
    assert wrapped_text_height_with_font_size(1, DEFAULT_FONT_SIZE) == 24.0


def test_wrapped_text_height_multi_line_adds_spacing() -> None:
    """Each line past the first adds ``size * DEFAULT_LINE_HEIGHT``."""
    # 2 lines at size 16: 24 + 1 * (16 * 1.1) = 24 + 17.6 = 41.6
    assert wrapped_text_height_with_font_size(2, DEFAULT_FONT_SIZE) == pytest.approx(41.6)
    # 3 lines: 24 + 2 * 17.6 = 59.2
    assert wrapped_text_height_with_font_size(3, DEFAULT_FONT_SIZE) == pytest.approx(59.2)


def test_wrapped_text_height_scales_with_font_size() -> None:
    """Height scales linearly with font size (anchored at DEFAULT_FONT_SIZE)."""
    # size 32: text_height = 24 * 32/16 = 48; spacing = 32 * 1.1 = 35.2
    assert wrapped_text_height_with_font_size(1, 32.0) == pytest.approx(48.0)
    assert wrapped_text_height_with_font_size(2, 32.0) == pytest.approx(83.2)


@pytest.mark.parametrize("bad_size", [-1.0, 0.0, math.nan, math.inf, -math.inf])
def test_font_size_helpers_fallback_on_non_positive_or_non_finite(bad_size: float) -> None:
    """Non-finite / non-positive font size falls back to DEFAULT_FONT_SIZE."""
    # scale_char_width(char_w, bad) == scale at default size (identity when char_w fixed)
    assert scale_char_width(8.0, bad_size) == 8.0
    # height at bad size == height at default size for 1 line (24)
    assert wrapped_text_height_with_font_size(1, bad_size) == 24.0


def test_scale_char_width_identity_at_default_size() -> None:
    """At DEFAULT_FONT_SIZE, ``scale_char_width`` is the identity."""
    assert scale_char_width(8.0, DEFAULT_FONT_SIZE) == 8.0
    assert scale_char_width(10.0, 32.0) == 20.0  # double size -> double width


# === measure_wrapped_lines_with_font_size =================================


def test_measure_empty_lines_is_zero_pair() -> None:
    """Empty ``lines`` -> ``(0.0, 0.0)``."""
    assert measure_wrapped_lines_with_font_size([], DEFAULT_CHAR_WIDTH, DEFAULT_FONT_SIZE) == (
        0.0,
        0.0,
    )


def test_measure_returns_widest_line_width_and_total_height() -> None:
    """Width is the widest sub-line; height follows ``wrapped_text_height``."""
    lines = [["short"], ["a", "muchlongerword"]]
    width, height = measure_wrapped_lines_with_font_size(
        lines, DEFAULT_CHAR_WIDTH, DEFAULT_FONT_SIZE
    )
    # widest sub-line: "a muchlongerword" = 16 chars * 8 = 128
    assert width == 128.0
    # 2 lines -> 41.6
    assert height == pytest.approx(41.6)


# === wrap_text_lines: grok in-tree test 1 (single long token kept whole) ===


def test_wraps_long_single_token_whole_without_slicing() -> None:
    """A single long identifier stays whole on one line (mermaid htmlLabels)."""
    label = "mark_filter_restore_context"
    lines = wrap_text_lines(label, DEFAULT_WRAP_WIDTH, DEFAULT_CHAR_WIDTH)
    assert lines == [[label]]


def test_long_single_token_measures_wider_than_wrap_cap() -> None:
    """Keeping the token whole means measured width exceeds the wrap cap."""
    lines = wrap_text_lines(
        "mark_filter_restore_context", DEFAULT_WRAP_WIDTH, DEFAULT_CHAR_WIDTH
    )
    width, _ = measure_wrapped_lines_with_font_size(
        lines, DEFAULT_CHAR_WIDTH, DEFAULT_FONT_SIZE
    )
    assert width > DEFAULT_WRAP_WIDTH


# === wrap_text_lines: grok in-tree test 3 (long token + trailing words) ====


def test_long_token_with_trailing_words_keeps_token_on_first_line() -> None:
    """A long leading token stays whole on its own line; trailing words wrap."""
    lines = wrap_text_lines(
        "_render_sidebar_for_active column mgmt",
        DEFAULT_WRAP_WIDTH,
        DEFAULT_CHAR_WIDTH,
    )
    assert lines == [["_render_sidebar_for_active"], ["column", "mgmt"]]


# === wrap_text_lines: grok in-tree test 4 (multi-word still wraps) =========


def test_multi_word_label_still_wraps_at_spaces() -> None:
    """A normal multi-word label exceeding the wrap width wraps at spaces."""
    phrase = "the quick brown fox jumps over the lazy dog"
    lines = wrap_text_lines(phrase, DEFAULT_WRAP_WIDTH, DEFAULT_CHAR_WIDTH)
    assert len(lines) >= 2
    flat: list[str] = [word for sub in lines for word in sub]
    assert flat == phrase.split(" ")


# === wrap_text_lines: grok in-tree test 5 (over-cap breaks on boundary) ====


def test_pathologically_long_token_breaks_on_identifier_boundary() -> None:
    """An over-cap token is force-broken on an identifier boundary (``_``)."""
    token = "segment_" * 25
    cap = 5.0 * DEFAULT_WRAP_WIDTH
    assert line_width(token, DEFAULT_CHAR_WIDTH) > cap
    lines = wrap_text_lines(token, DEFAULT_WRAP_WIDTH, DEFAULT_CHAR_WIDTH)
    assert len(lines) >= 2
    assert len(lines[0]) == 1  # each broken piece is a single word
    assert lines[0][0].endswith("_")  # break lands on an identifier boundary
    rejoined = "".join(word for sub in lines for word in sub)
    assert rejoined == token


# === wrap_text_lines: grok in-tree test 6 (grapheme fallback) ==============


def test_over_cap_token_without_break_char_falls_back_to_grapheme_break() -> None:
    """No identifier boundary -> the grapheme-break fallback bounds each line."""
    token = "a" * 200
    cap = 5.0 * DEFAULT_WRAP_WIDTH
    assert line_width(token, DEFAULT_CHAR_WIDTH) > cap
    lines = wrap_text_lines(token, DEFAULT_WRAP_WIDTH, DEFAULT_CHAR_WIDTH)
    assert len(lines) >= 2
    # each line stays within the cap
    assert line_width("".join(lines[0]), DEFAULT_CHAR_WIDTH) <= cap
    rejoined = "".join(word for sub in lines for word in sub)
    assert rejoined == token


# === wrap_text_lines: grok in-tree test 7 (CJK wide chars) =================


def test_over_cap_cjk_token_breaks_on_boundary_and_counts_wide_chars() -> None:
    """Wide chars count as two units; an over-cap CJK token breaks at ``_``."""
    assert display_width_units("中") == 2.0
    token = "中文_" * 50
    cap = 5.0 * DEFAULT_WRAP_WIDTH
    assert line_width(token, DEFAULT_CHAR_WIDTH) > cap
    lines = wrap_text_lines(token, DEFAULT_WRAP_WIDTH, DEFAULT_CHAR_WIDTH)
    assert len(lines) >= 2
    assert lines[0][0].endswith("_")  # CJK break lands on a boundary
    rejoined = "".join(word for sub in lines for word in sub)
    assert rejoined == token


# === wrap_text_lines: edge cases ==========================================


def test_wrap_empty_text_returns_empty_list() -> None:
    """Empty text -> ``[]`` (mirrors grok ``Vec::new()``)."""
    assert wrap_text_lines("", DEFAULT_WRAP_WIDTH, DEFAULT_CHAR_WIDTH) == []


def test_wrap_preserves_blank_lines_as_empty_sublines() -> None:
    """A blank raw line (after trim) emits a single empty sub-line."""
    # "a\n\nb": blank middle line -> [""] sub-line
    lines = wrap_text_lines("a\n\nb", DEFAULT_WRAP_WIDTH, DEFAULT_CHAR_WIDTH)
    assert lines == [["a"], [""], ["b"]]


def test_wrap_splits_on_newlines_into_separate_subline_groups() -> None:
    """``\\n`` separates raw lines, each producing its own wrapped sub-lines."""
    lines = wrap_text_lines("hello\nworld", DEFAULT_WRAP_WIDTH, DEFAULT_CHAR_WIDTH)
    assert lines == [["hello"], ["world"]]


def test_wrap_non_finite_max_width_treats_as_unbounded() -> None:
    """A non-finite ``max_width`` (inf) packs everything onto one line."""
    long_text = "word " * 100
    lines = wrap_text_lines(long_text.strip(), math.inf, DEFAULT_CHAR_WIDTH)
    assert len(lines) == 1
    assert len(lines[0]) == 100


# === _iter_graphemes: combining-mark merge (internal API) ==================


def test_iter_graphemes_merges_combining_marks_onto_base() -> None:
    """A base char + its combining mark form one grapheme (UAX #29 approx).

    NFC ``é`` is already one code point; NFD ``e`` + U+0301 must cluster into a
    single grapheme so width / split logic treats it as one unit.
    """
    from minimax_code.mermaid.to_svg.text_wrap import _iter_graphemes

    # NFC: one grapheme
    assert list(_iter_graphemes("é")) == ["é"]
    # NFD: e + combining acute -> still one grapheme
    assert list(_iter_graphemes("é")) == ["é"]
    # mixed: "café" (NFC) -> 4 graphemes
    assert list(_iter_graphemes("café")) == ["c", "a", "f", "é"]


def test_iter_graphemes_empty_yields_nothing() -> None:
    """Empty text yields no graphemes."""
    from minimax_code.mermaid.to_svg.text_wrap import _iter_graphemes

    assert list(_iter_graphemes("")) == []


# === barrel contract: text_wrap is internal ===============================


def test_text_wrap_module_all_is_12_symbols_ascii_sorted() -> None:
    """``text_wrap.py`` declares a 12-symbol ``__all__`` (5 const + 7 fn).

    Mirrors grok's private ``mod text_wrap;`` -- reachable by deep path,
    exposing the 5 ``pub const`` + 7 ``pub fn`` as its intended surface.
    """
    assert text_wrap_mod.__all__ == [
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


def test_text_wrap_not_in_to_svg_barrel() -> None:
    """``text_wrap`` is internal: its symbols are NOT re-exported via barrel.

    The ``to_svg`` barrel tracks grok's crate-root ``pub use`` surface, which
    omits ``text_wrap`` (the crate calls ``text_wrap::*`` internally from
    ``layout.rs`` / ``svg_renderer.rs`` -- never ``pub use``-d at the root).
    """
    for symbol in text_wrap_mod.__all__:
        assert symbol not in to_svg.__all__


def test_text_wrap_importable_via_deep_path() -> None:
    """``text_wrap`` is importable as ``minimax_code.mermaid.to_svg.text_wrap``.

    The deep path is how the future ``layout`` leaf will consume the wrapper
    (``from .text_wrap import wrap_text_lines, ...``) -- no barrel re-export.
    """
    import importlib

    deep = importlib.import_module("minimax_code.mermaid.to_svg.text_wrap")
    assert deep.wrap_text_lines is wrap_text_lines
    assert deep.measure_wrapped_lines_with_font_size is measure_wrapped_lines_with_font_size


def test_mermaid_root_barrel_unchanged_by_text_wrap_leaf() -> None:
    """R273 adds an internal module; the R38 root surface stays at 24."""
    assert len(mermaid.__all__) == 27
    assert "to_svg" not in mermaid.__all__
