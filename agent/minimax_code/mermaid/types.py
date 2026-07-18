"""Mermaid render types (R38).

Ports the **type layer** of grok's ``xai-grok-mermaid`` — the color primitive,
the light/dark theme, the render-parameter model, and the rendered-diagram
container. These are the host-agnostic data shapes a render call threads
through; the layout engine (``mermaid-to-svg``) and the SVG rasterizer
(``resvg``/``usvg``/``tiny-skia``) they wrap are Rust rendering stacks and are
NOT ported — they belong to a future wiring round that picks a Python renderer
(``mmdc`` CLI subprocess or ``mermaid.js`` over a headless browser).

What lives here (pure, zero non-stdlib dependency):

* :class:`Rgba` — a straight 8-bit-per-channel, non-premultiplied RGBA color.
* :class:`MermaidTheme` — the light/dark split (the only theme fact relevant to
  diagram rendering) and its default opaque surface color.
* :data:`LIGHT_SURFACE` / :data:`DARK_SURFACE` — the single source of truth for
  the surface a diagram blends into, shared by the theme mapping and (in grok)
  the raster background.
* :class:`RenderParams` — the sizing/theme/background model for one render.
* :class:`RenderedDiagram` — the ``png + dimensions`` product of a render.

Mapping
-------

* ``Rgba`` carries ``#[derive(Debug, Clone, Copy, PartialEq, Eq)]`` — a value
  type → **frozen=True, slots=True dataclass** (R32 policy: immutable, value
  equality, compact). ``u8`` channels → ``int``. The ``const fn new`` → the
  dataclass constructor; ``to_hex`` mirrors verbatim.
* ``MermaidTheme`` is a unit enum with ``#[default] Light`` → ``@unique
  enum.Enum`` (R35 policy: singleton value semantics); the default is exposed
  via :data:`DEFAULT_THEME` and :meth:`MermaidTheme.surface_background`.
* ``RenderParams`` is ``#[derive(Debug, Clone, Copy, PartialEq)]`` (**no**
  ``Eq`` — it carries an ``f32``) with an ``impl Default`` and a
  ``for_os_viewer`` constructor → **frozen=True, slots=True dataclass** with
  field defaults + a ``classmethod``. ``f32`` → ``float``; ``Option<Rgba>`` →
  ``Rgba | None``; ``u32`` → ``int``.
* ``RenderedDiagram`` is ``#[derive(Debug, Clone, PartialEq, Eq)]`` with
  ``png: Vec<u8>`` → **frozen=True, slots=True dataclass**; ``Vec<u8>`` →
  ``bytes`` (hashable, so the frozen instance stays hashable, mirroring
  grok's ``Eq``).

Product fusion
--------------

MiniMax Code's front end renders model output as markdown; mermaid diagram
blocks are common model output. Before a future wiring round renders them
(via ``mmdc`` or ``mermaid.js``), the back end needs the parameter model
(theme/size/background), a color primitive to express surfaces and fills, and
a typed product to hand back. This module is that vocabulary — host-agnostic,
so the same types front end and back end agree on regardless of renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

__all__ = [
    "Rgba",
    "MermaidTheme",
    "LIGHT_SURFACE",
    "DARK_SURFACE",
    "DEFAULT_THEME",
    "RenderParams",
    "RenderedDiagram",
]


@dataclass(frozen=True, slots=True)
class Rgba:
    """A straight 8-bit-per-channel, non-premultiplied RGBA color.

    Mirrors grok ``Rgba`` — four ``u8`` channels. Frozen value type: instances
    are immutable and compare by value (grok ``PartialEq + Eq``); hashable, so
    usable as a dict/set key.
    """

    #: Red channel, 0–255.
    r: int
    #: Green channel, 0–255.
    g: int
    #: Blue channel, 0–255.
    b: int
    #: Alpha channel, 0 (transparent) – 255 (opaque).
    a: int

    def to_hex(self) -> str:
        """Format as an opaque ``#RRGGBB`` hex string (alpha is ignored).

        Mirrors grok ``Rgba::to_hex``: uppercase ``#RRGGBB``, two digits per
        channel, alpha dropped. The dark surface const renders to the hex the
        dark theme uses (``#18181B``).
        """
        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"


#: Default opaque light surface (grok ``LIGHT_SURFACE``). Single source of
#: truth shared by the light theme's surface color and (in grok) the raster
#: background fill. ``#FAFAFA``.
LIGHT_SURFACE: Rgba = Rgba(0xFA, 0xFA, 0xFA, 0xFF)
#: Default opaque dark surface (grok ``DARK_SURFACE``). ``#18181B``.
DARK_SURFACE: Rgba = Rgba(0x18, 0x18, 0x1B, 0xFF)


@unique
class MermaidTheme(Enum):
    """Which color scheme a diagram should be rendered for.

    Mapped from the host's theme by the caller; only the light/dark split is
    relevant to diagram rendering. grok ``#[derive(Debug, Clone, Copy,
    PartialEq, Eq, Default)]`` with ``#[default] Light`` — exposed here as
    :data:`DEFAULT_THEME`.
    """

    #: Light surfaces with dark text (e.g. ``GrokDay``).
    LIGHT = "light"
    #: Dark surfaces with light text (e.g. ``GrokNight``, ``TokyoNight``).
    DARK = "dark"

    def surface_background(self) -> Rgba:
        """The default opaque surface color a diagram blends into for this theme.

        Used as the raster background when the caller does not supply an
        explicit :attr:`RenderParams.background`; chosen to approximate a
        typical terminal scrollback surface so the PNG sits flush with the
        grid. Mirrors grok ``MermaidTheme::surface_background``.
        """
        if self is MermaidTheme.LIGHT:
            return LIGHT_SURFACE
        return DARK_SURFACE


#: The default theme (grok ``#[default] Light``). Exposed as a value because
#: ``enum.Enum`` members cannot carry a Rust-style ``#[default]`` attribute.
DEFAULT_THEME: MermaidTheme = MermaidTheme.LIGHT


@dataclass(frozen=True, slots=True)
class RenderParams:
    """Parameters controlling a single diagram render.

    Mirrors the sizing model: :attr:`target_width_px` is the primary size
    driver (already HiDPI-oversampled by the caller), :attr:`max_height_px`
    clamps tall diagrams, and :attr:`scale` is the fallback oversample used
    only when ``target_width_px == 0``. :attr:`min_width_px` raises the scale
    so small diagrams still rasterize wide enough for OS viewers. The default
    config is **target-width-driven** (``target_width_px`` non-zero), so the
    default :attr:`scale` is inert.

    Frozen value type (grok ``Clone + Copy``); the ``for_os_viewer`` factory is
    a ``classmethod`` that returns a fresh instance.
    """

    #: Color scheme to render for.
    theme: MermaidTheme = DEFAULT_THEME
    #: Target output width in pixels. Non-zero drives the output size (the SVG
    #: is scaled so its width matches). ``0`` falls back to :attr:`scale`.
    target_width_px: int = 1024
    #: Hard ceiling on output height in pixels; the render is scaled down to
    #: fit. ``0`` disables the height clamp (output area is still bounded by
    #: the rasterizer's megapixel cap, which lives in the render stack and is
    #: not ported here).
    max_height_px: int = 4096
    #: Oversample factor applied **only** when ``target_width_px == 0``; inert
    #: otherwise (the default config is target-width-driven). ``1.0`` so a
    #: caller that zeroes ``target_width_px`` without touching scale gets 1:1.
    scale: float = 1.0
    #: Minimum output width in pixels. Non-zero raises scale so the raster is
    #: at least this wide (before height / megapixel clamps). ``0`` disables.
    min_width_px: int = 0
    #: Opaque background fill. ``None`` renders on a transparent background so
    #: the terminal cell color shows through.
    background: Rgba | None = None

    @classmethod
    def for_os_viewer(
        cls,
        theme: MermaidTheme,
        min_width_px: int,
        max_height_px: int,
    ) -> RenderParams:
        """Sizing tuned for opening a PNG in an OS image viewer.

        Prefer 2× the SVG's intrinsic size, ensure at least ``min_width_px``
        width for small diagrams, and allow a taller canvas than the
        terminal-budget path. Height and the renderer's megapixel/axis caps
        still apply. Mirrors grok ``RenderParams::for_os_viewer``.
        """
        return cls(
            theme=theme,
            # Drive from `scale` + `min_width_px` so large SVGs keep aspect at
            # 2× and small SVGs are upscaled to a readable minimum width.
            target_width_px=0,
            max_height_px=max_height_px,
            scale=2.0,
            min_width_px=min_width_px,
            background=theme.surface_background(),
        )


@dataclass(frozen=True, slots=True)
class RenderedDiagram:
    """A rendered diagram: PNG bytes plus the exact raster dimensions.

    Frozen value type (grok ``Debug + Clone + PartialEq + Eq``). ``png`` is
    ``bytes`` (hashable), so the instance stays hashable — mirroring grok's
    ``Eq`` even though Python does not distinguish ``Eq`` from ``PartialEq``.
    """

    #: The encoded PNG image.
    png: bytes
    #: Output width in pixels.
    width_px: int
    #: Output height in pixels.
    height_px: int
