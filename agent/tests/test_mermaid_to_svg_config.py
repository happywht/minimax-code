"""Black-box + white-box tests for the migrated mermaid-to-svg config layer (R270).

Exercises :mod:`minimax_code.mermaid.to_svg.config` -- the front-matter layer
of the ``mermaid-to-svg`` render stack (direction (1), leaf 2) -- through its
five public symbols (:class:`MermaidFrontmatter`, :class:`FlowchartConfig`,
:class:`RenderConfig`, :class:`ParsedMermaidSource`,
:func:`parse_mermaid_frontmatter`) plus the private YAML value-tree extractors.
This is a **different layer** from the R269 theme types it consumes: where R269
defines the resolved-palette vocabulary, R270 reads a mermaid ``---\\n...\\n---``
block, narrows the YAML through serde_yaml-equivalent extractors, and feeds
the R269 :meth:`MermaidThemePreset.parse` / :meth:`MermaidThemeVariables`
pipeline. Covers:

* :class:`MermaidFrontmatter` / :class:`FlowchartConfig` white-box: the data
  types default to all-``None`` (grok ``impl Default`` over ``Option<T>``).
* :class:`RenderConfig` defaults: theme ``None``, empty variables bag, empty
  flowchart block (the fall-through shape a source with no ``config:`` block
  yields).
* :meth:`RenderConfig.to_mermaid_theme` mirrors grok: ``None`` preset + empty
  bag -> ``None`` (caller renders default); preset-only -> that palette;
  overrides-only -> ``DEFAULT`` preset + overrides; preset + overrides ->
  preset palette with the set slots stamped.
* :meth:`RenderConfig.font_size_px` parses ``"16px"`` / ``" 16 "`` ->
  ``16.0``; rejects ``"0"`` / ``"-3"`` / ``"abc"`` / ``"inf"`` (finite-positive
  filter).
* :func:`parse_mermaid_frontmatter` end-to-end: no front-matter -> body is
  the source verbatim, ``frontmatter`` is ``None``; an empty fence -> empty
  frontmatter + default config; title-only; a full ``config:`` block (theme +
  flowchart + themeVariables) wired through the R269 pipeline; malformed YAML
  -> empty frontmatter + default config (the ``_PARSE_FAILED`` sentinel path).
* **CJK / codepoint-offset correctness**: a Chinese ``title`` round-trips and
  the body is sliced correctly even though Python indexes ``str`` by codepoint
  where grok indexes ``&str`` by byte (the structural ``---`` / ``\\n``
  markers are ASCII so the extracted substrings are byte-for-byte identical).
* The four serde_yaml-equivalence traps: ``_value_to_string`` lowercases a
  bool (``True`` -> ``"true"``, not ``"True"``); ``_value_to_u32`` rejects a
  negative string (``"-1"`` -> ``None``, mirroring ``u32::from_str``) and the
  u32 range (``2**32`` -> ``None``, ``2**32 - 1`` -> itself), and rejects a
  native ``bool`` (``isinstance(True, int)`` is ``True`` in Python, but grok's
  ``Value::Number`` never carries a bool).
* The barrel surface contract: the ``to_svg`` sub-package re-exports the eight
  symbols (ASCII-sorted ``__all__``); the R38 ``mermaid`` root surface is
  untouched (``__all__`` stays at 23, ``to_svg`` not promoted).
"""

from __future__ import annotations

import minimax_code.mermaid as mermaid
import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import (
    FlowchartConfig,
    MermaidFrontmatter,
    MermaidTheme,
    MermaidThemePreset,
    MermaidThemeVariables,
    ParsedMermaidSource,
    RenderConfig,
    parse_mermaid_frontmatter,
)
from minimax_code.mermaid.to_svg.config import (
    _frontmatter_bounds,
    _next_line_end,
    _parse_font_size,
    _parse_yaml_value,
    _value_to_bool,
    _value_to_string,
    _value_to_u32,
)

# === MermaidFrontmatter / FlowchartConfig defaults =========================


def test_mermaid_frontmatter_defaults_to_none_title() -> None:
    """``MermaidFrontmatter`` defaults to a ``None`` title (grok ``Default``)."""
    fm = MermaidFrontmatter()
    assert fm.title is None


def test_flowchart_config_defaults_to_all_none() -> None:
    """``FlowchartConfig`` defaults to every field ``None`` (grok ``Default``)."""
    fc = FlowchartConfig()
    assert fc.curve is None
    assert fc.html_labels is None
    assert fc.node_spacing is None
    assert fc.rank_spacing is None
    assert fc.padding is None
    assert fc.diagram_padding is None
    assert fc.wrapping_width is None
    assert fc.use_max_width is None
    assert fc.default_renderer is None


# === RenderConfig defaults =================================================


def test_render_config_defaults() -> None:
    """``RenderConfig`` defaults: no preset, empty variables, empty flowchart."""
    config = RenderConfig()
    assert config.theme is None
    assert config.theme_variables == MermaidThemeVariables()
    assert config.theme_variables.is_empty()
    assert config.layout is None
    assert config.look is None
    assert config.security_level is None
    assert config.font_family is None
    assert config.font_size is None
    assert config.flowchart == FlowchartConfig()


# === RenderConfig.to_mermaid_theme =========================================


def test_to_mermaid_theme_none_when_no_preset_and_empty_bag() -> None:
    """No preset + empty bag -> ``None`` (caller renders the default palette)."""
    assert RenderConfig().to_mermaid_theme() is None
    assert RenderConfig(theme=None).to_mermaid_theme() is None


def test_to_mermaid_theme_preset_only_returns_that_palette() -> None:
    """A preset with no overrides resolves to that preset's palette verbatim."""
    config = RenderConfig(theme=MermaidThemePreset.DARK)
    assert config.to_mermaid_theme() == MermaidTheme.dark()


def test_to_mermaid_theme_overrides_only_default_preset() -> None:
    """Overrides with no preset use ``DEFAULT`` as the base palette.

    grok defaults the preset to ``DEFAULT`` (== ``light``) when only variables
    are set, then stamps the overrides on top.
    """
    config = RenderConfig(
        theme_variables=MermaidThemeVariables(node_fill="#override"),
    )
    theme = config.to_mermaid_theme()
    assert theme is not None
    assert theme.node_fill == "#override"  # overridden.
    assert theme.background == "#ffffff"  # light() default survives.
    assert theme.edge_color == "#333333"  # untouched light() field.


def test_to_mermaid_theme_preset_and_overrides_merge() -> None:
    """A preset + overrides resolve to the preset palette with overrides stamped."""
    config = RenderConfig(
        theme=MermaidThemePreset.FOREST,
        theme_variables=MermaidThemeVariables(
            node_fill="#x",
            edge_color="#y",
        ),
    )
    theme = config.to_mermaid_theme()
    assert theme is not None
    assert theme.node_fill == "#x"  # override wins over forest's #cde498.
    assert theme.edge_color == "#y"  # override wins over forest's #333333.
    assert theme.background == "#f4f4f4"  # untouched forest field.
    assert theme.node_stroke == "#13540c"  # untouched forest field.


def test_to_mermaid_theme_returns_fresh_instance() -> None:
    """Two calls return equal-but-distinct instances (no shared mutable state)."""
    config = RenderConfig(theme=MermaidThemePreset.DARK)
    a = config.to_mermaid_theme()
    b = config.to_mermaid_theme()
    assert a == b
    assert a is not b


# === RenderConfig.font_size_px =============================================


def test_font_size_px_none_when_unset() -> None:
    """No font size -> ``None``."""
    assert RenderConfig().font_size_px() is None


def test_font_size_px_parses_px_suffix() -> None:
    """``"16px"`` -> ``16.0``."""
    assert RenderConfig(font_size="16px").font_size_px() == 16.0


def test_font_size_px_parses_plain_number_with_padding() -> None:
    """``" 16 "`` -> ``16.0`` (trim + parse tolerates surrounding spaces)."""
    assert RenderConfig(font_size=" 16 ").font_size_px() == 16.0


def test_font_size_px_parses_decimal() -> None:
    """``"16.5"`` -> ``16.5``."""
    assert RenderConfig(font_size="16.5").font_size_px() == 16.5


def test_font_size_px_rejects_zero_and_negative() -> None:
    """``"0"`` / ``"-3"`` -> ``None`` (finite-positive filter)."""
    assert RenderConfig(font_size="0").font_size_px() is None
    assert RenderConfig(font_size="-3").font_size_px() is None


def test_font_size_px_rejects_non_numeric_and_inf() -> None:
    """``"abc"`` / ``"inf"`` / ``"NaN"`` -> ``None``."""
    assert RenderConfig(font_size="abc").font_size_px() is None
    assert RenderConfig(font_size="inf").font_size_px() is None
    assert RenderConfig(font_size="NaN").font_size_px() is None


# === parse_mermaid_frontmatter: no front-matter ===========================


def test_parse_no_frontmatter_body_is_source_verbatim() -> None:
    """A source with no leading ``---`` fence: body is the source, no frontmatter."""
    source = "graph TD\n    A-->B\n    B-->C\n"
    parsed = parse_mermaid_frontmatter(source)
    assert parsed.body == source
    assert parsed.frontmatter is None
    assert parsed.config == RenderConfig()


def test_parse_no_frontmatter_plain_text() -> None:
    """Even a single-line source with no fence: body is the source verbatim."""
    parsed = parse_mermaid_frontmatter("just a diagram body")
    assert parsed.body == "just a diagram body"
    assert parsed.frontmatter is None


def test_parse_first_content_line_not_fence_returns_none_bounds() -> None:
    """Leading content that is not ``---`` means no front-matter.

    A first line like ``%% comment`` (mermaid directive) is not a fence, so the
    whole source is the body.
    """
    source = "%%{init: {...}}%%\ngraph TD\n    A-->B"
    parsed = parse_mermaid_frontmatter(source)
    assert parsed.body == source
    assert parsed.frontmatter is None


# === parse_mermaid_frontmatter: fence variants ============================


def test_parse_empty_fence_yields_empty_frontmatter() -> None:
    """An empty ``---\\n---`` fence: empty frontmatter + default config."""
    parsed = parse_mermaid_frontmatter("---\n---\ngraph TD\n    A-->B")
    assert parsed.body == "graph TD\n    A-->B"
    assert parsed.frontmatter == MermaidFrontmatter()
    assert parsed.config == RenderConfig()


def test_parse_fence_with_only_whitespace_yaml() -> None:
    """A fence whose YAML is only whitespace parses to null -> all defaults."""
    parsed = parse_mermaid_frontmatter("---\n   \n  \n---\nbody here")
    assert parsed.body == "body here"
    assert parsed.frontmatter == MermaidFrontmatter()
    assert parsed.config == RenderConfig()


def test_parse_unclosed_fence_returns_none_bounds() -> None:
    """A fence with no closing ``---`` scans to end-of-source and yields no block.

    grok's ``frontmatter_bounds`` returns ``None`` when the closing fence is
    absent; the whole source is the body.
    """
    source = "---\ntitle: X\ngraph TD\n    A-->B"
    parsed = parse_mermaid_frontmatter(source)
    assert parsed.body == source
    assert parsed.frontmatter is None


def test_parse_leading_blank_lines_before_fence() -> None:
    """Leading blank lines are skipped; the fence still opens a block."""
    parsed = parse_mermaid_frontmatter("\n\n---\ntitle: Hi\n---\nbody")
    assert parsed.frontmatter is not None
    assert parsed.frontmatter.title == "Hi"
    assert parsed.body == "body"


# === parse_mermaid_frontmatter: title + full config (end-to-end) ===========


def test_parse_title_only() -> None:
    """A front-matter carrying only a title."""
    parsed = parse_mermaid_frontmatter("---\ntitle: My Diagram\n---\ngraph TD")
    assert parsed.frontmatter is not None
    assert parsed.frontmatter.title == "My Diagram"
    assert parsed.config == RenderConfig()
    assert parsed.body == "graph TD"


def test_parse_full_config_end_to_end_wires_r269_pipeline() -> None:
    """A full ``config:`` block resolves theme/flowchart/themeVariables.

    This is the integration point with the R269 theme layer: ``config.theme``
    routes through :meth:`MermaidThemePreset.parse`, ``config.themeVariables``
    routes each alias through :meth:`MermaidThemeVariables.apply_mermaid_alias`,
    and :meth:`RenderConfig.to_mermaid_theme` merges them.
    """
    source = (
        "---\n"
        "title: My Flow\n"
        "config:\n"
        "  theme: forest\n"
        "  securityLevel: loose\n"
        "  fontFamily: Arial\n"
        "  fontSize: 18px\n"
        "  flowchart:\n"
        "    curve: basis\n"
        "    nodeSpacing: 60\n"
        "    htmlLabels: true\n"
        "  themeVariables:\n"
        "    mainBkg: '#abc'\n"
        "    lineColor: '#def'\n"
        "---\n"
        "graph TD\n"
        "    A-->B\n"
    )
    parsed = parse_mermaid_frontmatter(source)

    # Front-matter metadata.
    assert parsed.frontmatter is not None
    assert parsed.frontmatter.title == "My Flow"

    # Config scalars.
    assert parsed.config.theme is MermaidThemePreset.FOREST
    assert parsed.config.security_level == "loose"
    assert parsed.config.font_family == "Arial"
    assert parsed.config.font_size == "18px"

    # Flowchart sub-block.
    assert parsed.config.flowchart.curve == "basis"
    assert parsed.config.flowchart.node_spacing == 60
    assert parsed.config.flowchart.html_labels is True

    # themeVariables aliases routed onto the variable bag.
    assert parsed.config.theme_variables.node_fill == "#abc"  # mainBkg alias.
    assert parsed.config.theme_variables.edge_color == "#def"  # lineColor alias.

    # Body is the tail past the closing fence.
    assert parsed.body.startswith("graph TD")

    # font_size_px parses the carried fontSize.
    assert parsed.config.font_size_px() == 18.0

    # to_mermaid_theme merges forest palette + overrides.
    theme = parsed.config.to_mermaid_theme()
    assert theme is not None
    assert theme.node_fill == "#abc"  # override wins over forest default.
    assert theme.edge_color == "#def"  # override wins over forest default.
    assert theme.background == "#f4f4f4"  # untouched forest field.


def test_parse_unknown_theme_string_leaves_preset_none() -> None:
    """An unrecognized ``config.theme`` value leaves the preset unset.

    :meth:`MermaidThemePreset.parse` returns ``None`` for an unknown string; the
    front-matter parser propagates that (theme stays ``None``), so a bogus
    theme name does not crash the parse.
    """
    parsed = parse_mermaid_frontmatter(
        "---\nconfig:\n  theme: cyberpunk\n---\ngraph TD"
    )
    assert parsed.config.theme is None


# === parse_mermaid_frontmatter: malformed YAML fallback ===================


def test_parse_malformed_yaml_falls_back_to_empty_frontmatter() -> None:
    """Malformed YAML inside a fence -> empty frontmatter + default config.

    This is the ``_PARSE_FAILED`` sentinel path: a YAML parse error (here, the
    reserved ``@`` character) must NOT raise; grok falls through to an empty
    :class:`MermaidFrontmatter` + default :class:`RenderConfig` so a broken
    front-matter does not break the render.
    """
    parsed = parse_mermaid_frontmatter("---\n@\n---\ngraph TD")
    assert parsed.body == "graph TD"
    assert parsed.frontmatter == MermaidFrontmatter()
    assert parsed.config == RenderConfig()


# === CJK / codepoint-offset correctness ====================================


def test_parse_cjk_title_round_trips() -> None:
    """A Chinese title round-trips through the codepoint-based fence scan.

    grok indexes ``&str`` by byte; this port indexes ``str`` by codepoint. The
    fence markers (``---`` / ``\\n``) are ASCII, so the body/title substrings
    are byte-for-byte identical to grok's even though the internal offset
    numbers differ.
    """
    parsed = parse_mermaid_frontmatter("---\ntitle: 中文标题\n---\ngraph TD")
    assert parsed.frontmatter is not None
    assert parsed.frontmatter.title == "中文标题"
    assert parsed.body == "graph TD"


def test_parse_config_value_with_cjk_string_field() -> None:
    """A CJK font-family value survives the ``_value_to_string`` narrowing."""
    parsed = parse_mermaid_frontmatter(
        "---\nconfig:\n  fontFamily: 微软雅黑\n---\ngraph TD"
    )
    assert parsed.config.font_family == "微软雅黑"


def test_next_line_end_codepoint_indexing() -> None:
    """``_next_line_end`` indexes by codepoint, not byte.

    A CJK char occupies 3 UTF-8 bytes but 1 Python codepoint; the newline after
    ``中`` is at codepoint index 4 here (``中\\n`` -> the ``\\n`` is codepoint 1,
    +1 = 2 for the position after it).
    """
    assert _next_line_end("中\nrest", 0) == 2  # past the newline (codepoint 1).
    assert _next_line_end("no newline here", 0) == 15  # len(), no newline.


def test_frontmatter_bounds_returns_none_for_plain_source() -> None:
    """No leading fence -> ``_frontmatter_bounds`` returns ``None``."""
    assert _frontmatter_bounds("graph TD\n    A-->B") is None


def test_frontmatter_bounds_locates_simple_fence() -> None:
    """A simple ``---\\ntitle\\n---`` block is bounded correctly."""
    bounds = _frontmatter_bounds("---\ntitle: X\n---\nbody")
    assert bounds is not None
    yaml_start, yaml_end, body_start = bounds
    assert "---\ntitle: X\n---\nbody"[yaml_start:yaml_end] == "title: X\n"
    assert "---\ntitle: X\n---\nbody"[body_start:] == "body"


# === _value_to_string (bool lowercase trap) ================================


def test_value_to_string_bool_is_lowercase() -> None:
    """A bool renders lowercase (``True`` -> ``"true"``), matching Rust ``Display``.

    Python ``str(True)`` is ``"True"``; grok ``Value::Bool(true).to_string()``
    is ``"true"``. ``_value_to_string`` lowercases the bool explicitly so the
    serde_yaml string form round-trips.
    """
    assert _value_to_string(True) == "true"
    assert _value_to_string(False) == "false"


def test_value_to_string_int_and_float() -> None:
    """Numbers render via ``str()``."""
    assert _value_to_string(42) == "42"
    assert _value_to_string(3.14) == "3.14"
    assert _value_to_string(0) == "0"


def test_value_to_string_str_round_trips() -> None:
    """A string round-trips verbatim."""
    assert _value_to_string("hello") == "hello"
    assert _value_to_string("") == ""


def test_value_to_string_none_and_collections_return_none() -> None:
    """``None`` / list / dict -> ``None`` (no scalar form)."""
    assert _value_to_string(None) is None
    assert _value_to_string([1, 2]) is None
    assert _value_to_string({"a": 1}) is None


# === _value_to_bool ========================================================


def test_value_to_bool_native_bool_round_trips() -> None:
    """A native bool round-trips."""
    assert _value_to_bool(True) is True
    assert _value_to_bool(False) is False


def test_value_to_bool_string_true_false() -> None:
    """The strings ``"true"`` / ``"false"`` parse; any other string -> ``None``."""
    assert _value_to_bool("true") is True
    assert _value_to_bool("false") is False
    # Case-sensitive: grok matches only the lowercase arms.
    assert _value_to_bool("True") is None
    assert _value_to_bool("FALSE") is None
    assert _value_to_bool("yes") is None


def test_value_to_bool_number_returns_none() -> None:
    """A number is not a bool (grok ``Value::Number`` != ``Value::Bool``)."""
    assert _value_to_bool(1) is None
    assert _value_to_bool(0) is None


# === _value_to_u32 (range + bool-is-int + negative-string traps) ===========


def test_value_to_u32_int_in_range() -> None:
    """An in-range int round-trips; the u32 max boundary is inclusive."""
    assert _value_to_u32(0) == 0
    assert _value_to_u32(42) == 42
    assert _value_to_u32(4294967295) == 4294967295  # u32::MAX = 2**32 - 1.


def test_value_to_u32_int_out_of_range_returns_none() -> None:
    """A negative int or one >= 2**32 is rejected (mirrors ``u32::try_from``)."""
    assert _value_to_u32(-1) is None
    assert _value_to_u32(4294967296) is None  # 2**32, just over the range.
    assert _value_to_u32(10_000_000_000) is None


def test_value_to_u32_negative_string_returns_none() -> None:
    """The string ``"-1"`` is rejected (mirrors ``u32::from_str``).

    Python ``int("-1")`` would otherwise succeed and leak a negative through;
    the range check after the parse catches it, matching grok's unsigned parse.
    """
    assert _value_to_u32("-1") is None
    assert _value_to_u32("-42") is None


def test_value_to_u32_numeric_string_in_range() -> None:
    """A numeric string in range parses; out of range is rejected."""
    assert _value_to_u32("60") == 60
    assert _value_to_u32("0") == 0
    assert _value_to_u32("4294967296") is None  # over the range.
    assert _value_to_u32("abc") is None  # non-numeric.


def test_value_to_u32_float_integer_valued() -> None:
    """An integer-valued float is accepted (mirrors ``Number::as_u64``)."""
    assert _value_to_u32(60.0) == 60
    assert _value_to_u32(0.0) == 0


def test_value_to_u32_float_non_integer_returns_none() -> None:
    """A non-integer float is rejected."""
    assert _value_to_u32(3.14) is None


def test_value_to_u32_bool_is_rejected() -> None:
    """A native ``bool`` is rejected even though ``isinstance(True, int)`` is ``True``.

    grok's ``Value::Number`` never carries a bool, so ``_value_to_u32`` checks
    the bool arm first and returns ``None`` (otherwise ``True`` would slip
    through as ``1``).
    """
    assert _value_to_u32(True) is None
    assert _value_to_u32(False) is None


def test_value_to_u32_none_and_collections_return_none() -> None:
    """``None`` / list / dict -> ``None``."""
    assert _value_to_u32(None) is None
    assert _value_to_u32([1]) is None


# === _parse_font_size ======================================================


def test_parse_font_size_px_suffix() -> None:
    """``"16px"`` -> ``16.0``."""
    assert _parse_font_size("16px") == 16.0


def test_parse_font_size_caps_px_suffix() -> None:
    """``"16PX"`` -> ``None``: the suffix match is case-sensitive (lowercase ``px``).

    grok's ``ends_with("px")`` is lowercase-sensitive; this port matches that
    exactly so a ``"16PX"`` oddity is rejected rather than mis-parsed.
    """
    assert _parse_font_size("16PX") is None


def test_parse_font_size_whitespace_tolerance() -> None:
    """``" 16 "`` / ``"  16px  "`` -> ``16.0`` (trim inside and outside the px)."""
    assert _parse_font_size(" 16 ") == 16.0
    assert _parse_font_size("  16px  ") == 16.0


def test_parse_font_size_decimal() -> None:
    """A decimal font size parses."""
    assert _parse_font_size("16.5") == 16.5
    assert _parse_font_size("0.5px") == 0.5


def test_parse_font_size_rejects_non_positive() -> None:
    """``"0"`` / ``"-3"`` -> ``None`` (finite-positive filter)."""
    assert _parse_font_size("0") is None
    assert _parse_font_size("-3") is None
    assert _parse_font_size("0px") is None


def test_parse_font_size_rejects_non_numeric_and_special() -> None:
    """``"abc"`` / ``"inf"`` / ``"NaN"`` / ``""`` -> ``None``."""
    assert _parse_font_size("abc") is None
    assert _parse_font_size("inf") is None
    assert _parse_font_size("NaN") is None
    assert _parse_font_size("") is None


# === _parse_yaml_value (three-state sentinel) ==============================


def test_parse_yaml_value_empty_returns_none_for_null() -> None:
    """Empty/whitespace YAML returns ``None`` (stands in for YAML null)."""
    assert _parse_yaml_value("") is None
    assert _parse_yaml_value("   ") is None
    assert _parse_yaml_value("\n\n") is None


def test_parse_yaml_value_parses_mapping() -> None:
    """A mapping YAML returns the parsed dict."""
    assert _parse_yaml_value("title: X\n") == {"title": "X"}


def test_parse_yaml_value_malformed_returns_sentinel() -> None:
    """A YAML parse error returns the ``_PARSE_FAILED`` sentinel (not ``None``).

    The sentinel keeps "empty YAML -> null" distinct from "parse error ->
    fall back to defaults"; a bare ``None`` would conflate them.
    """
    from minimax_code.mermaid.to_svg.config import _PARSE_FAILED

    # Two distinct scanner errors: the reserved ``@`` character and an
    # unterminated flow mapping. Both return the sentinel (not ``None``, not
    # raised), so the caller can fall back to defaults without a try/except.
    assert _parse_yaml_value("@") is _PARSE_FAILED
    assert _parse_yaml_value("{a: b") is _PARSE_FAILED


# === barrel surface contract ===============================================


def test_to_svg_subpackage_barrel_reexports_eighteen_symbols() -> None:
    """The ``to_svg`` barrel re-exports the 18 R269--R277 symbols.

    R269 seeded the barrel (theme, 3 symbols); R270 grew it (config, 5
    symbols -> 8); R271 grew it again (error, 7 symbols -> 15); R277 grew it
    a final time (render crate-root dispatch, 3 symbols -> 18). The list is
    ASCII-sorted; ``ast`` (R271) stays internal and is intentionally absent.
    """
    assert to_svg.__all__ == [
        "DotGenerationError",
        "FlowchartConfig",
        "InvalidDirection",
        "InvalidNodeShape",
        "MermaidError",
        "MermaidFrontmatter",
        "MermaidTheme",
        "MermaidThemePreset",
        "MermaidThemeVariables",
        "ParseError",
        "ParsedMermaidSource",
        "RenderConfig",
        "RenderError",
        "UnsupportedDiagramType",
        "is_mermaid_diagram",
        "parse_mermaid_frontmatter",
        "render_mermaid_to_svg",
        "strip_mermaid_frontmatter",
    ]
    # R270 additions are reachable through the barrel.
    assert to_svg.FlowchartConfig is FlowchartConfig
    assert to_svg.MermaidFrontmatter is MermaidFrontmatter
    assert to_svg.ParsedMermaidSource is ParsedMermaidSource
    assert to_svg.RenderConfig is RenderConfig
    assert to_svg.parse_mermaid_frontmatter is parse_mermaid_frontmatter
    # R269 symbols still reachable (not clobbered by the barrel expansion).
    assert to_svg.MermaidTheme is MermaidTheme
    assert to_svg.MermaidThemePreset is MermaidThemePreset
    assert to_svg.MermaidThemeVariables is MermaidThemeVariables
    # R271 error symbols reachable; AST symbols absent (internal module).
    assert to_svg.MermaidError is not None
    assert to_svg.ParseError is not None
    assert to_svg.RenderError is not None
    assert "FlowchartGraph" not in to_svg.__all__
    assert "NodeShape" not in to_svg.__all__
    # R277 render crate-root dispatch symbols are present in ``__all__`` (the
    # 3-symbol expansion: 15 -> 18). Object-identity reachability for these is
    # covered by ``test_mermaid_to_svg_render.py`` (the render leaf's own
    # barrel test), keeping this config-layer test focused on its own symbols.
    assert "render_mermaid_to_svg" in to_svg.__all__
    assert "strip_mermaid_frontmatter" in to_svg.__all__
    assert "is_mermaid_diagram" in to_svg.__all__


def test_mermaid_root_barrel_unchanged_by_r270() -> None:
    """R270 adds a leaf to a sub-package; the R38 root surface stays at 23."""
    assert len(mermaid.__all__) == 23
    assert "to_svg" not in mermaid.__all__


def test_parsed_mermaid_source_defaults() -> None:
    """``ParsedMermaidSource`` defaults: empty body, no frontmatter, default config."""
    parsed = ParsedMermaidSource()
    assert parsed.body == ""
    assert parsed.frontmatter is None
    assert parsed.config == RenderConfig()
