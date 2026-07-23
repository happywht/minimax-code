"""Mermaid theme presets and variables (R269 -- direction (1) leaf 1).

Ports the **type layer** of grok's vendored ``mermaid-to-svg`` layout crate --
the resolved theme a flowchart renders with, the named presets a front-matter
``config.theme`` string maps to, and the ``themeVariables`` override bag the
front-matter carries. This is the first leaf of the render-stack migration
(direction (1): wire dagre so mermaid source renders to SVG). It is a pure
data layer with **zero non-stdlib dependency**, mirroring the R246 dagre
``lib.rs`` type-layer-first opening move.

.. note::

   This is a **different crate** from the R38 ``xai-grok-mermaid`` host. R38
   ported the host's color primitive (:class:`~minimax_code.mermaid.types.Rgba`)
   and its coarse light/dark :class:`~minimax_code.mermaid.types.MermaidTheme`
   used by the raster surface. This module ports the **render stack's own**
   richer theme (7 resolved colors + 5 named presets + 7-slot variable bag with
   mermaid front-matter aliases) -- the palette the dagre layout hands the SVG
   renderer. The two coexist on purpose: the host picks a surface; the render
   stack picks the in-diagram fills/strokes.

What lives here (pure, zero non-stdlib dependency):

* :class:`MermaidTheme` -- the 7 resolved colors a diagram renders with
  (background / node_fill / node_stroke / text_color / edge_color /
  subgraph_fill / subgraph_stroke), plus the 5 named-preset factories
  (``light`` / ``dark`` / ``base`` / ``forest`` / ``neutral``).
* :class:`MermaidThemePreset` -- the 5-variant named preset a front-matter
  ``config.theme`` string parses to, with ``parse`` / ``to_theme``.
* :class:`MermaidThemeVariables` -- the 7-slot optional override bag a
  front-matter ``config.themeVariables`` carries, with ``is_empty`` /
  ``apply_mermaid_alias`` / ``apply_to``.

Mapping
-------

* ``MermaidTheme`` carries ``#[derive(Debug, Clone, PartialEq, Eq, Hash)]`` --
  a value type. grok mutates it in place via
  ``MermaidThemeVariables::apply_to(&mut theme)``; a Python ``@dataclass``
  (mutable, value-equality via the generated ``__eq__``) mirrors that
  in-place mutation contract. The 7 ``String`` colors -> 7 ``str`` fields.
  The 5 associated ``fn light/dark/base/forest/neutral`` -> 5
  ``staticmethod`` factories returning fresh instances; ``impl Default``
  -> ``default()`` (== ``light()``).
* ``MermaidThemePreset`` is a unit enum with no ``#[default]`` -> ``@unique
  enum.Enum`` (R35 policy: singleton value semantics; the lowercase wire
  string is the member value, matching grok's ``parse`` match arms).
  ``parse`` returns ``None`` for an unknown string (grok ``Option<Self>``);
  ``to_theme`` dispatches to the matching factory.
* ``MermaidThemeVariables`` carries the same derives as ``MermaidTheme``; its
  7 ``Option<String>`` slots -> 7 ``str | None`` fields (``None`` = unset).
  ``is_empty`` is the AND of ``is_none()`` over the 7 slots;
  ``apply_mermaid_alias`` is grok's ``match key { ... }`` table (one mermaid
  front-matter alias may target one variable slot; unknown -> ``False``);
  ``apply_to`` copies every set slot onto the theme (``None`` slots leave the
  theme unchanged).

Product fusion
--------------

A future wiring round renders model mermaid output through the dagre layout
stack ported in R246--R268. The renderer needs the resolved palette to fill
nodes, stroke edges, and tint subgraph clusters -- this module is that
palette, plus the front-matter vocabulary a user uses to override it
(``config: { theme: forest, themeVariables: { mainBkg: ... } }``). It is the
first render-stack leaf; ``config.rs`` (the front-matter parser, R270) will
consume ``MermaidThemePreset.parse`` and ``MermaidThemeVariables`` to build a
:class:`MermaidTheme`, and the SVG renderer (later leaf) will read its 7
fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

__all__ = [
    "MermaidTheme",
    "MermaidThemePreset",
    "MermaidThemeVariables",
]


@dataclass
class MermaidTheme:
    """The 7 resolved colors a flowchart renders with.

    Mirrors grok ``MermaidTheme`` -- a value type (``Debug + Clone + PartialEq
    + Eq + Hash``) mutated in place by
    :meth:`MermaidThemeVariables.apply_to`. The 5 named-preset factories
    return fresh instances with the canonical palettes; :meth:`default`
    mirrors grok's ``impl Default`` (the light palette).
    """

    #: Diagram background fill.
    background: str = ""
    #: Node body fill.
    node_fill: str = ""
    #: Node border stroke.
    node_stroke: str = ""
    #: Node + edge label text color.
    text_color: str = ""
    #: Edge stroke color.
    edge_color: str = ""
    #: Subgraph cluster background fill.
    subgraph_fill: str = ""
    #: Subgraph cluster border stroke.
    subgraph_stroke: str = ""

    @staticmethod
    def light() -> MermaidTheme:
        """The default light palette (grok ``MermaidTheme::light``).

        The canonical mermaid ``default``-theme colors: lavender node fill on
        a white background with a dark-gray text/edge pair.
        """
        return MermaidTheme(
            background="#ffffff",
            node_fill="#ECECFF",
            node_stroke="#9370DB",
            text_color="#333333",
            edge_color="#333333",
            subgraph_fill="#ffffde",
            subgraph_stroke="#aaaa33",
        )

    @staticmethod
    def dark() -> MermaidTheme:
        """The dark palette (grok ``MermaidTheme::dark``).

        Mid-gray nodes on a near-black background with off-white text.
        """
        return MermaidTheme(
            background="#1e1e1e",
            node_fill="#2d2d2d",
            node_stroke="#888888",
            text_color="#ffffff",
            edge_color="#888888",
            subgraph_fill="#3a3a20",
            subgraph_stroke="#888844",
        )

    @staticmethod
    def base() -> MermaidTheme:
        """The ``base`` preset palette (grok ``MermaidTheme::base``).

        ``base`` aliases :meth:`light` -- the resolved palette is identical.
        """
        return MermaidTheme.light()

    @staticmethod
    def forest() -> MermaidTheme:
        """The ``forest`` palette (grok ``MermaidTheme::forest``).

        Green node fills/strokes on a light-gray background.
        """
        return MermaidTheme(
            background="#f4f4f4",
            node_fill="#cde498",
            node_stroke="#13540c",
            text_color="#333333",
            edge_color="#333333",
            subgraph_fill="#cde498",
            subgraph_stroke="#13540c",
        )

    @staticmethod
    def neutral() -> MermaidTheme:
        """The ``neutral`` palette (grok ``MermaidTheme::neutral``).

        Grayscale nodes on a white background.
        """
        return MermaidTheme(
            background="#ffffff",
            node_fill="#eeeeee",
            node_stroke="#999999",
            text_color="#333333",
            edge_color="#333333",
            subgraph_fill="#eeeeee",
            subgraph_stroke="#999999",
        )

    @staticmethod
    def default() -> MermaidTheme:
        """The default theme (grok ``impl Default for MermaidTheme``).

        grok's ``Default`` delegates to :meth:`light`; exposed verbatim so a
        caller that wants "the theme" without picking a preset reads as grok
        code that constructs ``MermaidTheme::default()``.
        """
        return MermaidTheme.light()


@unique
class MermaidThemePreset(Enum):
    """A named theme preset a front-matter ``config.theme`` string maps to.

    Mirrors grok ``MermaidThemePreset`` -- a unit enum (no ``#[default]``).
    The member value is the lowercase wire string grok's ``parse`` matches
    against, so ``MermaidThemePreset("dark")`` round-trips the front-matter
    token verbatim. Exposed as an :class:`~enum.Enum` (R35 policy: singleton
    value semantics).
    """

    #: The canonical light palette (aliases :meth:`MermaidTheme.light`).
    DEFAULT = "default"
    #: The ``base`` preset (aliases :meth:`MermaidTheme.light`).
    BASE = "base"
    #: The dark palette.
    DARK = "dark"
    #: The green forest palette.
    FOREST = "forest"
    #: The grayscale neutral palette.
    NEUTRAL = "neutral"

    @classmethod
    def parse(cls, value: str) -> MermaidThemePreset | None:
        """Parse a front-matter ``config.theme`` string into a preset.

        Mirrors grok ``MermaidThemePreset::parse``: an exact (lowercase)
        match returns the preset; any other string returns ``None`` (grok
        ``Option<Self>``). The caller treats ``None`` as "leave the theme
        unset" rather than "fall back to default".
        """
        for preset in cls:
            if preset.value == value:
                return preset
        return None

    def to_theme(self) -> MermaidTheme:
        """Resolve this preset into its :class:`MermaidTheme` palette.

        Mirrors grok ``MermaidThemePreset::to_theme`` -- dispatches to the
        matching named-preset factory. Returns a fresh instance so two
        callers resolving the same preset never share mutable state.
        """
        factories: dict[MermaidThemePreset, type] = {
            MermaidThemePreset.DEFAULT: MermaidTheme.light,
            MermaidThemePreset.BASE: MermaidTheme.base,
            MermaidThemePreset.DARK: MermaidTheme.dark,
            MermaidThemePreset.FOREST: MermaidTheme.forest,
            MermaidThemePreset.NEUTRAL: MermaidTheme.neutral,
        }
        factory = factories[self]
        # All five factories are staticmethods bound as functions on the
        # class; calling them produces a fresh MermaidTheme instance.
        return factory()


@dataclass
class MermaidThemeVariables:
    """The 7-slot optional override bag a front-matter ``themeVariables`` carries.

    Mirrors grok ``MermaidThemeVariables`` -- a value type with the same 7
    color slots as :class:`MermaidTheme`, each ``Option<String>`` -> ``str |
    None`` (``None`` = unset / "do not override"). Front-matter keys are
    mermaid aliases (``mainBkg``, ``primaryColor``, ...); the
    :meth:`apply_mermaid_alias` table maps each alias onto a slot. Once
    populated, :meth:`apply_to` stamps the set slots onto a resolved theme.
    """

    #: Diagram background fill override.
    background: str | None = None
    #: Node body fill override.
    node_fill: str | None = None
    #: Node border stroke override.
    node_stroke: str | None = None
    #: Node + edge label text color override.
    text_color: str | None = None
    #: Edge stroke color override.
    edge_color: str | None = None
    #: Subgraph cluster background fill override.
    subgraph_fill: str | None = None
    #: Subgraph cluster border stroke override.
    subgraph_stroke: str | None = None

    def is_empty(self) -> bool:
        """``True`` iff no slot is set (grok ``MermaidThemeVariables::is_empty``).

        The caller uses this to decide whether to build a theme at all: an
        empty bag plus no preset means "render with the default palette".
        """
        return all(getattr(self, slot) is None for slot in _VARIABLE_SLOTS)

    def apply_mermaid_alias(self, key: str, value: str) -> bool:
        """Apply one mermaid front-matter alias onto a slot (grok ``apply_mermaid_alias``).

        Mirrors grok's ``match key { ... }`` table: each mermaid alias maps
        onto exactly one variable slot (several aliases may target the same
        slot). On a hit the slot is set and ``True`` is returned; an unknown
        key leaves the bag untouched and returns ``False``. The value
        round-trips verbatim (grok moves the ``String`` straight onto the
        slot).
        """
        slot = _MERMAID_ALIAS_TO_SLOT.get(key)
        if slot is None:
            return False
        setattr(self, slot, value)
        return True

    def apply_to(self, theme: MermaidTheme) -> None:
        """Stamp every set slot onto ``theme`` in place (grok ``apply_to``).

        Mirrors grok ``MermaidThemeVariables::apply_to(&mut theme)``: each
        slot that is set overwrites the matching theme field; each ``None``
        slot leaves the theme field unchanged. Mutates ``theme`` in place
        (the caller passes the resolved preset theme and gets the override
        merged in).
        """
        for slot in _VARIABLE_SLOTS:
            value = getattr(self, slot)
            if value is not None:
                setattr(theme, slot, value)


#: The 7 variable/theme slot names in declaration order. Shared by
#: :meth:`MermaidThemeVariables.is_empty` (AND of ``is_none``) and
#: :meth:`MermaidThemeVariables.apply_to` (copy set slots) so the two never
#: drift on a slot rename.
_VARIABLE_SLOTS: tuple[str, ...] = (
    "background",
    "node_fill",
    "node_stroke",
    "text_color",
    "edge_color",
    "subgraph_fill",
    "subgraph_stroke",
)

#: The mermaid front-matter alias -> variable slot table (grok
#: ``MermaidThemeVariables::apply_mermaid_alias`` match arms). One alias maps
#: onto exactly one slot; several aliases may share a slot (e.g. both
#: ``primaryColor`` and ``mainBkg`` set ``node_fill``). An alias absent here
#: is unknown -> ``apply_mermaid_alias`` returns ``False``.
_MERMAID_ALIAS_TO_SLOT: dict[str, str] = {
    "background": "background",
    "primaryColor": "node_fill",
    "mainBkg": "node_fill",
    "primaryBorderColor": "node_stroke",
    "nodeBorder": "node_stroke",
    "primaryTextColor": "text_color",
    "nodeTextColor": "text_color",
    "textColor": "text_color",
    "lineColor": "edge_color",
    "defaultLinkColor": "edge_color",
    "clusterBkg": "subgraph_fill",
    "clusterBorder": "subgraph_stroke",
}
