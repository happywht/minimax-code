"""Black-box tests for the migrated mermaid-to-svg theme layer (R269).

Exercises :mod:`minimax_code.mermaid.to_svg.theme` -- the resolved-palette
type layer that opens the ``mermaid-to-svg`` render-stack sub-package
(direction (1), leaf 1) -- through its three public types
(:class:`MermaidTheme`, :class:`MermaidThemePreset`,
:class:`MermaidThemeVariables`). This is a **different layer** from the R38
``xai-grok-mermaid`` host theme (:class:`~minimax_code.mermaid.types.MermaidTheme`,
a coarse 2-variant light/dark split): the render stack's theme carries 7
resolved colors + 5 named presets + a 7-slot front-matter override bag.
Covers:

* :class:`MermaidTheme` white-box: the 5 named-preset factories stamp the
  canonical palettes verbatim (``light`` lavender on white, ``dark`` gray on
  near-black, ``forest`` green, ``neutral`` grayscale, ``base`` aliases
  ``light``); ``default()`` mirrors grok's ``impl Default`` (== ``light``);
  two ``light()`` calls produce equal-but-distinct mutable instances (no
  shared state),
* :class:`MermaidThemePreset` white-box: the 5 wire strings round-trip
  through :meth:`parse`; an unknown string returns ``None``;
  :meth:`to_theme` dispatches each preset to its matching factory (``BASE``
  resolves equal to ``light``),
* :class:`MermaidThemeVariables` white-box: a fresh bag is empty; setting
  one slot makes it non-empty; :meth:`apply_mermaid_alias` maps the mermaid
  front-matter aliases onto slots (``mainBkg`` / ``primaryColor`` ->
  ``node_fill``, the multi-alias-per-slot case), an unknown alias returns
  ``False`` and leaves the bag untouched; :meth:`apply_to` overwrites only
  the set slots on a theme (``None`` slots leave the theme field),
* the barrel surface contract: the ``to_svg`` sub-package re-exports the
  three theme symbols (ASCII-sorted ``__all__``); the R38 ``mermaid`` root
  barrel is untouched (``to_svg`` is a sub-package, not promoted to the
  root surface; the root ``__all__`` count stays at 17).
"""

from __future__ import annotations

import minimax_code.mermaid as mermaid
import minimax_code.mermaid.to_svg as to_svg
from minimax_code.mermaid.to_svg import (
    MermaidTheme,
    MermaidThemePreset,
    MermaidThemeVariables,
)

# === MermaidTheme: named-preset factories ==================================


def test_light_factory_stamps_canonical_palette() -> None:
    """``light()`` returns the canonical mermaid ``default``-theme palette."""
    theme = MermaidTheme.light()
    assert theme.background == "#ffffff"
    assert theme.node_fill == "#ECECFF"
    assert theme.node_stroke == "#9370DB"
    assert theme.text_color == "#333333"
    assert theme.edge_color == "#333333"
    assert theme.subgraph_fill == "#ffffde"
    assert theme.subgraph_stroke == "#aaaa33"


def test_dark_factory_stamps_canonical_palette() -> None:
    """``dark()`` returns the mid-gray-on-near-black palette."""
    theme = MermaidTheme.dark()
    assert theme.background == "#1e1e1e"
    assert theme.node_fill == "#2d2d2d"
    assert theme.node_stroke == "#888888"
    assert theme.text_color == "#ffffff"
    assert theme.edge_color == "#888888"
    assert theme.subgraph_fill == "#3a3a20"
    assert theme.subgraph_stroke == "#888844"


def test_forest_factory_stamps_canonical_palette() -> None:
    """``forest()`` returns the green-on-gray palette."""
    theme = MermaidTheme.forest()
    assert theme.background == "#f4f4f4"
    assert theme.node_fill == "#cde498"
    assert theme.node_stroke == "#13540c"
    assert theme.text_color == "#333333"
    assert theme.edge_color == "#333333"
    assert theme.subgraph_fill == "#cde498"
    assert theme.subgraph_stroke == "#13540c"


def test_neutral_factory_stamps_canonical_palette() -> None:
    """``neutral()`` returns the grayscale-on-white palette."""
    theme = MermaidTheme.neutral()
    assert theme.background == "#ffffff"
    assert theme.node_fill == "#eeeeee"
    assert theme.node_stroke == "#999999"
    assert theme.text_color == "#333333"
    assert theme.edge_color == "#333333"
    assert theme.subgraph_fill == "#eeeeee"
    assert theme.subgraph_stroke == "#999999"


def test_base_factory_aliases_light() -> None:
    """``base()`` aliases ``light()`` -- the resolved palette is identical."""
    assert MermaidTheme.base() == MermaidTheme.light()


def test_default_factory_equals_light() -> None:
    """``default()`` mirrors grok ``impl Default`` (delegates to ``light``)."""
    assert MermaidTheme.default() == MermaidTheme.light()


def test_light_returns_distinct_mutable_instances() -> None:
    """Two ``light()`` calls produce equal-but-distinct instances.

    grok returns a fresh ``MermaidTheme`` value each call; mutating one never
    leaks into the other (the contract :meth:`MermaidThemeVariables.apply_to`
    relies on).
    """
    a = MermaidTheme.light()
    b = MermaidTheme.light()
    assert a == b
    assert a is not b
    a.node_fill = "#mutated"
    assert b.node_fill == "#ECECFF"


# === MermaidThemePreset: parse / to_theme =================================


def test_preset_wire_strings_round_trip() -> None:
    """The 5 lowercase wire strings map to the 5 presets verbatim."""
    assert MermaidThemePreset("default") is MermaidThemePreset.DEFAULT
    assert MermaidThemePreset("base") is MermaidThemePreset.BASE
    assert MermaidThemePreset("dark") is MermaidThemePreset.DARK
    assert MermaidThemePreset("forest") is MermaidThemePreset.FOREST
    assert MermaidThemePreset("neutral") is MermaidThemePreset.NEUTRAL


def test_preset_parse_known_strings() -> None:
    """``parse`` resolves each canonical wire string to its preset."""
    assert MermaidThemePreset.parse("default") is MermaidThemePreset.DEFAULT
    assert MermaidThemePreset.parse("base") is MermaidThemePreset.BASE
    assert MermaidThemePreset.parse("dark") is MermaidThemePreset.DARK
    assert MermaidThemePreset.parse("forest") is MermaidThemePreset.FOREST
    assert MermaidThemePreset.parse("neutral") is MermaidThemePreset.NEUTRAL


def test_preset_parse_unknown_string_returns_none() -> None:
    """An unknown string returns ``None`` (grok ``Option<Self>``)."""
    assert MermaidThemePreset.parse("cyberpunk") is None
    assert MermaidThemePreset.parse("") is None
    # parse is exact + case-sensitive: grok matches lowercase arms only.
    assert MermaidThemePreset.parse("Dark") is None
    assert MermaidThemePreset.parse("DEFAULT") is None


def test_preset_to_theme_dispatches_to_factory() -> None:
    """``to_theme`` resolves each preset to its matching palette factory."""
    assert MermaidThemePreset.DEFAULT.to_theme() == MermaidTheme.light()
    assert MermaidThemePreset.BASE.to_theme() == MermaidTheme.base()
    assert MermaidThemePreset.DARK.to_theme() == MermaidTheme.dark()
    assert MermaidThemePreset.FOREST.to_theme() == MermaidTheme.forest()
    assert MermaidThemePreset.NEUTRAL.to_theme() == MermaidTheme.neutral()


def test_preset_to_theme_base_resolves_equal_to_light() -> None:
    """``BASE.to_theme()`` resolves to a palette equal to ``light()``."""
    assert MermaidThemePreset.BASE.to_theme() == MermaidTheme.light()


def test_preset_to_theme_returns_fresh_instance() -> None:
    """``to_theme`` returns a fresh mutable instance per call (no shared state)."""
    a = MermaidThemePreset.DARK.to_theme()
    b = MermaidThemePreset.DARK.to_theme()
    assert a == b
    assert a is not b


# === MermaidThemeVariables: is_empty ======================================


def test_variables_fresh_bag_is_empty() -> None:
    """A fresh bag has no slot set (grok ``is_empty`` -> ``True``)."""
    assert MermaidThemeVariables().is_empty()


def test_variables_one_slot_set_is_not_empty() -> None:
    """Setting any one slot makes the bag non-empty."""
    variables = MermaidThemeVariables(background="#000000")
    assert not variables.is_empty()


def test_variables_all_slots_set_is_not_empty() -> None:
    """A fully-populated bag is non-empty."""
    variables = MermaidThemeVariables(
        background="#000000",
        node_fill="#111111",
        node_stroke="#222222",
        text_color="#333333",
        edge_color="#444444",
        subgraph_fill="#555555",
        subgraph_stroke="#666666",
    )
    assert not variables.is_empty()


# === MermaidThemeVariables: apply_mermaid_alias ===========================


def test_apply_alias_canonical_background_slot() -> None:
    """``background`` maps onto the ``background`` slot; returns ``True``."""
    variables = MermaidThemeVariables()
    assert variables.apply_mermaid_alias("background", "#abcdef") is True
    assert variables.background == "#abcdef"
    assert not variables.is_empty()


def test_apply_alias_multi_alias_per_slot_node_fill() -> None:
    """Both ``primaryColor`` and ``mainBkg`` target ``node_fill`` (multi-alias)."""
    variables = MermaidThemeVariables()
    assert variables.apply_mermaid_alias("primaryColor", "#aaa") is True
    assert variables.node_fill == "#aaa"
    assert variables.apply_mermaid_alias("mainBkg", "#bbb") is True
    assert variables.node_fill == "#bbb"  # last write wins.


def test_apply_alias_all_twelve_aliases_target_correct_slot() -> None:
    """Every grok match arm maps onto its documented slot."""
    cases = [
        ("background", "background"),
        ("primaryColor", "node_fill"),
        ("mainBkg", "node_fill"),
        ("primaryBorderColor", "node_stroke"),
        ("nodeBorder", "node_stroke"),
        ("primaryTextColor", "text_color"),
        ("nodeTextColor", "text_color"),
        ("textColor", "text_color"),
        ("lineColor", "edge_color"),
        ("defaultLinkColor", "edge_color"),
        ("clusterBkg", "subgraph_fill"),
        ("clusterBorder", "subgraph_stroke"),
    ]
    for alias, slot in cases:
        variables = MermaidThemeVariables()
        assert variables.apply_mermaid_alias(alias, "#x") is True, alias
        assert getattr(variables, slot) == "#x", alias


def test_apply_alias_unknown_key_returns_false_and_leaves_bag_untouched() -> None:
    """An unknown alias returns ``False`` and sets no slot."""
    variables = MermaidThemeVariables(background="#preset")
    assert variables.apply_mermaid_alias("notAnAlias", "#ignored") is False
    assert variables.is_empty() is False  # only the preset slot set.
    assert variables.background == "#preset"
    assert variables.node_fill is None


# === MermaidThemeVariables: apply_to ======================================


def test_apply_to_overwrites_only_set_slots() -> None:
    """Set slots overwrite the theme; ``None`` slots leave it unchanged.

    ``node_fill`` + ``edge_color`` set; the other 5 slots ``None``. After
    ``apply_to`` only those two fields differ from ``light()``.
    """
    theme = MermaidTheme.light()
    variables = MermaidThemeVariables(
        node_fill="#override-fill",
        edge_color="#override-edge",
    )
    variables.apply_to(theme)
    assert theme.node_fill == "#override-fill"
    assert theme.edge_color == "#override-edge"
    # The unset slots retain the light() defaults.
    assert theme.background == "#ffffff"
    assert theme.node_stroke == "#9370DB"
    assert theme.text_color == "#333333"
    assert theme.subgraph_fill == "#ffffde"
    assert theme.subgraph_stroke == "#aaaa33"


def test_apply_to_empty_bag_leaves_theme_unchanged() -> None:
    """An empty bag mutates nothing on the theme."""
    theme = MermaidTheme.dark()
    original = MermaidTheme(
        background=theme.background,
        node_fill=theme.node_fill,
        node_stroke=theme.node_stroke,
        text_color=theme.text_color,
        edge_color=theme.edge_color,
        subgraph_fill=theme.subgraph_fill,
        subgraph_stroke=theme.subgraph_stroke,
    )
    MermaidThemeVariables().apply_to(theme)
    assert theme == original


def test_apply_to_full_bag_overwrites_every_field() -> None:
    """A fully-populated bag overwrites all 7 theme fields."""
    theme = MermaidTheme.forest()
    variables = MermaidThemeVariables(
        background="#1",
        node_fill="#2",
        node_stroke="#3",
        text_color="#4",
        edge_color="#5",
        subgraph_fill="#6",
        subgraph_stroke="#7",
    )
    variables.apply_to(theme)
    assert theme == MermaidTheme(
        background="#1",
        node_fill="#2",
        node_stroke="#3",
        text_color="#4",
        edge_color="#5",
        subgraph_fill="#6",
        subgraph_stroke="#7",
    )


def test_apply_to_then_preset_pipeline_mirrors_grok_config_flow() -> None:
    """``to_theme`` + ``apply_to`` is the pipeline grok ``RenderConfig`` runs.

    ``config: { theme: forest, themeVariables: { mainBkg: #x } }`` should
    resolve to the forest palette with ``node_fill`` overridden -- the exact
    pipeline the R270 ``config.rs`` front-matter parser will drive.
    """
    theme = MermaidThemePreset.FOREST.to_theme()
    assert theme.node_fill == "#cde498"  # forest default before override.
    variables = MermaidThemeVariables()
    assert variables.apply_mermaid_alias("mainBkg", "#overridden") is True
    variables.apply_to(theme)
    assert theme.node_fill == "#overridden"
    # Untouched forest fields survive.
    assert theme.background == "#f4f4f4"
    assert theme.node_stroke == "#13540c"


# === barrel surface contract ==============================================


def test_to_svg_subpackage_barrel_includes_theme_symbols() -> None:
    """The ``to_svg`` barrel includes the three R269 theme symbols.

    R270 extends the barrel to 8 symbols (adds the config layer); this test
    locks only the theme-symbols subset (not the full barrel list, which is
    owned by the latest leaf) so R269 stays valid as the barrel grows.
    """
    for name in ("MermaidTheme", "MermaidThemePreset", "MermaidThemeVariables"):
        assert name in to_svg.__all__
    assert to_svg.MermaidTheme is MermaidTheme
    assert to_svg.MermaidThemePreset is MermaidThemePreset
    assert to_svg.MermaidThemeVariables is MermaidThemeVariables


def test_mermaid_root_barrel_unchanged_by_to_svg_subpackage() -> None:
    """R269 adds a sub-package; the R38 root surface stays at 17 symbols.

    ``to_svg`` is a sub-package, not promoted to the ``mermaid`` root
    surface (the R38 host types/errors stay the root vocabulary). The root
    ``__all__`` count is unchanged.
    """
    assert len(mermaid.__all__) == 17
    assert "to_svg" not in mermaid.__all__


def test_to_svg_subpackage_reachable_via_mermaid() -> None:
    """``mermaid.to_svg`` is reachable as a sub-package of ``mermaid``."""
    import types

    assert isinstance(mermaid.to_svg, types.ModuleType)
    assert mermaid.to_svg is to_svg
    assert mermaid.to_svg.MermaidTheme is MermaidTheme
