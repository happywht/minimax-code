"""Tests for the mermaid render type layer (R38).

Mirrors the type-level assertions of grok's ``xai-grok-mermaid`` lib tests
(``theme_surface_background_differs_light_vs_dark``, ``rgba_to_hex_is_opaque_rrggbb``,
``default_params_are_target_width_driven`` minus the rasterize call it makes)
and pins the Python-specific value-type behavior (frozen immutability, value
equality, hashability) that the frozen-dataclass mapping adds.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.mermaid import (
    DARK_SURFACE,
    DEFAULT_THEME,
    LIGHT_SURFACE,
    MermaidTheme,
    RenderedDiagram,
    RenderParams,
    Rgba,
)
from minimax_code.mermaid.types import (
    DARK_SURFACE as DARK_SURFACE_FROM_MODULE,
)
from minimax_code.mermaid.types import (
    LIGHT_SURFACE as LIGHT_SURFACE_FROM_MODULE,
)
from minimax_code.mermaid.types import (
    MermaidTheme as MermaidThemeFromModule,
)
from minimax_code.mermaid.types import (
    RenderedDiagram as RenderedDiagramFromModule,
)
from minimax_code.mermaid.types import (
    RenderParams as RenderParamsFromModule,
)
from minimax_code.mermaid.types import Rgba as RgbaFromModule


def test_reexport():
    assert Rgba is RgbaFromModule
    assert MermaidTheme is MermaidThemeFromModule
    assert RenderParams is RenderParamsFromModule
    assert RenderedDiagram is RenderedDiagramFromModule
    assert LIGHT_SURFACE is LIGHT_SURFACE_FROM_MODULE
    assert DARK_SURFACE is DARK_SURFACE_FROM_MODULE


# --- Rgba ------------------------------------------------------------------


def test_rgba_to_hex_is_opaque_rrggbb():
    """Mirrors grok ``rgba_to_hex_is_opaque_rrggbb``: uppercase #RRGGBB, alpha ignored."""
    assert Rgba(0x12, 0xAB, 0xCD, 0xFF).to_hex() == "#12ABCD"
    # Alpha is ignored — fully transparent still formats the RGB channels.
    assert Rgba(0, 0, 0, 0).to_hex() == "#000000"
    # The shared dark surface const renders to the hex the dark theme uses.
    assert DARK_SURFACE.to_hex() == "#18181B"


def test_rgba_value_equality():
    assert Rgba(1, 2, 3, 4) == Rgba(1, 2, 3, 4)
    assert Rgba(1, 2, 3, 4) != Rgba(1, 2, 3, 5)
    assert Rgba(1, 2, 3, 4) != Rgba(9, 2, 3, 4)


def test_rgba_is_frozen():
    """Frozen value type — channel mutation is rejected (grok ``Clone + Copy``)."""
    rgba = Rgba(1, 2, 3, 4)
    with pytest.raises(FrozenInstanceError):
        rgba.r = 99  # type: ignore[misc]


def test_rgba_is_hashable():
    """grok ``Eq`` (no ``Hash``) — Python frozen dataclass is hashable as a free
    side ability; that does not contradict grok's semantics."""
    by_color = {Rgba(1, 2, 3, 4): "bg"}
    assert by_color[Rgba(1, 2, 3, 4)] == "bg"


# --- surfaces + theme ------------------------------------------------------


def test_theme_surface_background_differs_light_vs_dark():
    """Mirrors grok ``theme_surface_background_differs_light_vs_dark``."""
    light = MermaidTheme.LIGHT.surface_background()
    dark = MermaidTheme.DARK.surface_background()
    assert light != dark, "light and dark must map to different surfaces"
    # Light surface is brighter than dark on every channel; both opaque.
    assert light.r > dark.r and light.g > dark.g and light.b > dark.b
    assert light.a == 0xFF
    assert dark.a == 0xFF


def test_surfaces_match_constants():
    assert MermaidTheme.LIGHT.surface_background() is LIGHT_SURFACE
    assert MermaidTheme.DARK.surface_background() is DARK_SURFACE


def test_theme_has_two_variants():
    assert {m.name for m in MermaidTheme} == {"LIGHT", "DARK"}


def test_default_theme_is_light():
    """grok ``#[default] Light`` — exposed as :data:`DEFAULT_THEME`."""
    assert DEFAULT_THEME is MermaidTheme.LIGHT


# --- RenderParams ----------------------------------------------------------


def test_default_params_are_target_width_driven():
    """Mirrors grok ``default_params_are_target_width_driven`` (minus the rasterize
    call): the real default path is target-width-driven regardless of ``scale``."""
    p = RenderParams()
    assert p.theme is MermaidTheme.LIGHT
    assert p.target_width_px == 1024
    assert p.max_height_px == 4096
    assert p.scale == 1.0
    assert p.min_width_px == 0
    assert p.background is None


def test_for_os_viewer_factory():
    """Mirrors grok ``RenderParams::for_os_viewer``: scale-driven (target_width=0),
    2× scale, explicit min width/height, background = theme surface."""
    p = RenderParams.for_os_viewer(MermaidTheme.DARK, min_width_px=512, max_height_px=8192)
    assert p.theme is MermaidTheme.DARK
    assert p.target_width_px == 0
    assert p.scale == 2.0
    assert p.min_width_px == 512
    assert p.max_height_px == 8192
    assert p.background == MermaidTheme.DARK.surface_background()


def test_render_params_value_equality():
    a = RenderParams(theme=MermaidTheme.DARK, target_width_px=0, scale=2.0)
    b = RenderParams(theme=MermaidTheme.DARK, target_width_px=0, scale=2.0)
    assert a == b
    assert a != RenderParams(theme=MermaidTheme.LIGHT, target_width_px=0, scale=2.0)


def test_render_params_is_frozen():
    p = RenderParams()
    with pytest.raises(FrozenInstanceError):
        p.target_width_px = 999  # type: ignore[misc]


# --- RenderedDiagram -------------------------------------------------------


def test_rendered_diagram_value_equality():
    a = RenderedDiagram(png=b"\x89PNG\r\n\x1a\n", width_px=10, height_px=20)
    b = RenderedDiagram(png=b"\x89PNG\r\n\x1a\n", width_px=10, height_px=20)
    assert a == b
    assert a != RenderedDiagram(png=b"different", width_px=10, height_px=20)


def test_rendered_diagram_is_hashable():
    """``png: bytes`` is hashable, so the frozen instance stays hashable (grok ``Eq``)."""
    d = RenderedDiagram(png=b"\x00\x01", width_px=2, height_px=3)
    assert {d: 1}[RenderedDiagram(png=b"\x00\x01", width_px=2, height_px=3)] == 1
