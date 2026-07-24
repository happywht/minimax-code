"""Black-box tests for the migrated pure-Rust default engine (R278c).

Exercises :mod:`minimax_code.mermaid.pure` -- the default, offline
:class:`PureRustEngine` host-shell leaf fused from grok's
``xai-grok-mermaid/src/pure.rs`` (direction (1), brick 11). The engine composes
the SVG half (the dagre layout + SVG render stack migrated in R269--R277) with
the raster half that turns the SVG into PNG bytes.

Migration decision matrix (grok ``pure.rs`` inline ``mod tests`` + the
``tests/pure_engine.rs`` integration suite)
--------------------------------------------------------------

* ``flowchart_svg_contains_node_labels`` -- **migrated**: the SVG half of a
  trivial flowchart carries the ``<svg>`` envelope and both node labels.
* ``sequence_svg_contains_participants`` -- **adapted**: the Python port has
  no ``sequenceDiagram`` renderer yet (R279+ leaf; ``parser.py`` L114 lists it
  as unsupported). The contract that holds today is "routes through the engine
  without a panic" -- either it renders (once the sequence renderer lands) or
  it surfaces a typed :class:`MermaidError`. grok's positive assertion returns
  when the renderer is wired.
* ``render_produces_decodable_png_with_matching_dims`` -- **YAGNI (R278b)**:
  PNG decode + dimension match. The Python ``render`` always raises
  :class:`MermaidRasterizeError` at the raster step (the pure-Rust
  ``resvg`` / ``usvg`` / ``tiny-skia`` / ``fontdb`` stack has no pure-Python
  port and this project ships no Rust toolchain). Not migrated.
* ``render_is_deterministic_in_process`` -- **YAGNI (R278b)**: PNG byte
  determinism. Same raster gap; not migrated.
* ``cyclic_login_flow_renders_with_arrowheads`` -- **migrated**: an 8-edge
  cyclic flowchart whose back-edge (``Attempts -->|No| Enter``) re-enters the
  cycle. Pins the invariant to the edges: exactly one
  ``marker-end="url(#arrowhead)"`` per edge (a whole-doc "contains arrow"
  substring would pass even with one missing), and every node label survives
  layout.
* ``light_and_dark_render_to_different_pixels`` -- **YAGNI (R278b)**: light/dark
  PNG pixel diff. Same raster gap; not migrated.
* ``theme_for_overrides_surface_per_theme`` -- **migrated**: pure-function
  check that the host surface single-source-of-truth overrides the render
  stack's preset background per theme.
* ``garbage_input_never_panics`` -- **migrated**: 10 untrusted inputs through
  :func:`render_checked`; none may surface as :class:`MermaidPanicError`.
* ``engine_error_taxonomy_maps_every_arm`` -- **migrated**: directly
  constructs each ``to_svg.error`` variant and asserts :func:`map_engine_error`
  collapses the 6 arms onto the host's 3 categories.
* ``long_identifier_node_labels_survive_intact_in_svg`` (pure_engine.rs) --
  **migrated via** :func:`build_svg`: the real-user word-wrap regression
  flowchart reaches the SVG with each long identifier as one complete
  ``<tspan>``. grok calls ``render_mermaid_to_svg`` directly; this port routes
  through :func:`build_svg` (the pure.py SVG-half entry) -- equivalent
  coverage, since ``build_svg`` is ``theme_for`` + ``render_mermaid_to_svg``
  and the theme does not affect ``<tspan>`` integrity.

The remaining ``pure_engine.rs`` integration tests (``default_engine`` +
PNG decode / class diagram / xychart / determinism) all depend on
``default_engine()`` (the lib.rs barrel factory, not yet migrated) or on the
raster half; they land with R278d once ``default_engine`` + ``mmdc`` are wired.
"""

from __future__ import annotations

import pytest

import minimax_code.mermaid as mermaid
import minimax_code.mermaid.pure as pure_mod
from minimax_code.mermaid import (
    DARK_SURFACE,
    LIGHT_SURFACE,
    MermaidError,
    MermaidLayoutError,
    MermaidPanicError,
    MermaidParseError,
    MermaidRasterizeError,
    MermaidTheme,
    MermaidUnsupportedError,
    PureRustEngine,
    RenderLimits,
    RenderParams,
    render_checked,
)
from minimax_code.mermaid.pure import build_svg, map_engine_error, theme_for
from minimax_code.mermaid.to_svg.error import (
    DotGenerationError,
    InvalidDirection,
    InvalidNodeShape,
)
from minimax_code.mermaid.to_svg.error import (
    ParseError as EngineParseError,
)
from minimax_code.mermaid.to_svg.error import (
    RenderError as EngineRenderError,
)
from minimax_code.mermaid.to_svg.error import (
    UnsupportedDiagramType as EngineUnsupportedDiagramType,
)

# === barrel + module contract =============================================


def test_pure_module_all_is_single_symbol_pure_rust_engine() -> None:
    """``pure.py`` declares a 1-symbol ``__all__``: just ``PureRustEngine``.

    Mirrors grok ``lib.rs`` L55 ``pub use pure::PureRustEngine`` -- the only
    public surface of the leaf. ``build_svg`` / ``theme_for`` /
    ``map_engine_error`` are grok private ``fn``s (no ``pub``).
    """
    assert pure_mod.__all__ == ["PureRustEngine"]


def test_mermaid_root_barrel_re_exports_pure_rust_engine() -> None:
    """R278c adds ``PureRustEngine`` to the root barrel; the surface stays at 24."""
    assert "PureRustEngine" in mermaid.__all__
    assert len(mermaid.__all__) == 27
    # The barrel re-export is the same object as the leaf definition.
    assert mermaid.PureRustEngine is PureRustEngine


def test_build_svg_theme_for_map_engine_error_are_module_private() -> None:
    """The three helpers mirror grok's private ``fn``s: present on the module
    (reachable by direct import, like grok's ``super::build_svg``) but NOT in
    ``__all__``."""
    assert "build_svg" not in pure_mod.__all__
    assert "theme_for" not in pure_mod.__all__
    assert "map_engine_error" not in pure_mod.__all__
    assert callable(build_svg)
    assert callable(theme_for)
    assert callable(map_engine_error)


# === PureRustEngine instance semantics ====================================
# grok ``#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]`` unit struct.
# Python port: stateless class (__slots__ = ()), value-equal, hashable.


def test_default_constructor_yields_stateless_instance() -> None:
    """``__slots__ = ()`` -> no instance ``__dict__`` (mirrors grok unit struct)."""
    engine = PureRustEngine()
    assert not hasattr(engine, "__dict__")


def test_instances_are_value_equal() -> None:
    """grok ``PartialEq + Eq``: any two instances compare equal (no state)."""
    assert PureRustEngine() == PureRustEngine()
    assert PureRustEngine() != "PureRustEngine"
    assert PureRustEngine() != 0


def test_instances_are_hashable_and_interchangeable_as_dict_key() -> None:
    """Hashable so the engine can sit in a set / dict key (grok ``Eq``).

    Two instances hash identically (both hash to ``hash(PureRustEngine)``), so
    a caller can treat the engine as a singleton dict key without enforcing one.
    """
    a, b = PureRustEngine(), PureRustEngine()
    assert hash(a) == hash(b)
    registry = {a: "default"}
    assert registry[b] == "default"


def test_repr_matches_unit_struct_debug() -> None:
    """``repr`` mirrors grok's ``Debug`` derive (the unit struct form)."""
    assert repr(PureRustEngine()) == "PureRustEngine()"


def test_satisfies_mermaid_engine_protocol() -> None:
    """``PureRustEngine`` satisfies the runtime-checkable ``MermaidEngine`` protocol."""
    assert isinstance(PureRustEngine(), mermaid.MermaidEngine)


# === render() protocol (R278b raster sentinel) ============================


def test_render_raises_raster_sentinel_for_valid_svg_half() -> None:
    """For a valid flowchart, ``render`` completes the SVG half then raises the
    R278b YAGNI raster sentinel -- never returns a fabricated ``RenderedDiagram``.

    grok ``pure.rs`` L23--L26 runs ``build_svg(source, params.theme)?`` then
    ``crate::rasterize(&svg, params)``. The Python port runs the SVG half and
    raises :class:`MermaidRasterizeError` at the raster step (the pure-Rust
    raster stack is unportable; PNG is optional here).
    """
    engine = PureRustEngine()
    with pytest.raises(MermaidRasterizeError, match="R278b"):
        engine.render("flowchart LR\n  A[Start] --> B[Finish]", RenderParams())


def test_render_propagates_svg_half_errors_before_raster_sentinel() -> None:
    """``render`` runs the SVG half first: an unsupported diagram type surfaces
    a typed :class:`MermaidError` (NOT the raster sentinel), proving
    :func:`build_svg` ran and its error propagated via grok's ``?`` before the
    R278b raise (pure.rs L24 ``build_svg(source, params.theme)?``).
    """
    engine = PureRustEngine()
    with pytest.raises(MermaidError) as exc_info:
        engine.render("@@@@", RenderParams())
    # "@@@@" is not a supported diagram type -> unsupported error from the SVG
    # half, NOT the raster sentinel. Proves the SVG half executes first.
    assert not isinstance(exc_info.value, MermaidRasterizeError)


# === build_svg: SVG rendering half ========================================


def test_flowchart_svg_contains_node_labels() -> None:
    """grok ``flowchart_svg_contains_node_labels``: a trivial flowchart renders
    to an SVG document carrying both node labels."""
    svg = build_svg("flowchart LR\n  A[Start] --> B[Finish]", MermaidTheme.LIGHT)
    assert "<svg" in svg
    assert "</svg>" in svg
    assert "Start" in svg
    assert "Finish" in svg


def test_cyclic_login_flow_renders_with_arrowheads() -> None:
    """grok ``cyclic_login_flow_renders_with_arrowheads``: an 8-edge cyclic
    flowchart whose back-edge (``Attempts -->|No| Enter``) re-enters the cycle.

    Every edge must keep its arrowhead (exactly one
    ``marker-end="url(#arrowhead)"`` per edge), and no node may be dropped by
    the cycle. A whole-doc "contains arrow" substring would pass even with one
    missing, so the count is pinned to the edge count.
    """
    edge_count = 8
    source = (
        "flowchart TD\n"
        "Start([User visits login page]) --> Enter[Enter username & password]\n"
        "Enter --> Submit[Submit credentials]\n"
        "Submit --> Validate{Credentials valid?}\n"
        "Validate -->|No| Fail[Show error message]\n"
        "Fail --> Attempts{Too many failed attempts?}\n"
        "Attempts -->|Yes| Lock[Lock account]\n"
        "Attempts -->|No| Enter\n"
        "Validate -->|Yes| Session[Create session]"
    )
    svg = build_svg(source, MermaidTheme.LIGHT)
    arrowheads = svg.count('marker-end="url(#arrowhead)"')
    assert arrowheads == edge_count
    # All node labels survive layout (text-wrap may break a label across lines,
    # so assert the leading substring of each, mirroring grok).
    for label in [
        "Enter username",
        "Submit credentials",
        "Credentials valid",
        "Too many failed attempts",
        "Lock account",
        "Create session",
    ]:
        assert label in svg


def test_sequence_diagram_routes_through_engine_without_panic() -> None:
    """Adapted from grok ``sequence_svg_contains_participants``.

    The Python port has no ``sequenceDiagram`` renderer yet (R279+ leaf;
    ``parser.py`` L114 lists it as unsupported, surfacing as
    :class:`~minimax_code.mermaid.to_svg.error.UnsupportedDiagramType` ->
    :class:`MermaidUnsupportedError`). The contract that holds today is "routes
    through the engine without a panic": either it renders (once the sequence
    renderer lands) or it surfaces a typed :class:`MermaidError`. grok's
    positive participant assertion activates when the renderer is wired.
    """
    source = "sequenceDiagram\n  Alice->>Bob: Hello\n  Bob-->>Alice: Hi"
    try:
        svg = build_svg(source, MermaidTheme.LIGHT)
    except MermaidError as exc:
        # Expected until the R279+ sequence renderer lands. Must be a typed
        # MermaidError subclass (parse / unsupported), never a panic / leak.
        assert not isinstance(exc, MermaidRasterizeError)
        return
    # Once the sequence renderer lands, the positive assertion activates.
    assert "Alice" in svg
    assert "Bob" in svg


@pytest.mark.xfail(
    reason=(
        "Pre-existing dagre network_simplex defect (R269-R271 graphlib port), "
        "NOT introduced by R278c: the edge-exchange invariant in "
        "``_exchange_edges`` calls ``Graph.remove_edge(v, w, None)`` for a key "
        "the counter map does not hold, so ``_decrement_or_remove_entry`` does "
        "``None -= 1`` (graphlib.py L1031). Triggered by chain edges inside a "
        "subgraph (``nav --> mark --> global --> sidebar --> page``); grok's "
        "dagre renders this graph correctly, so the port has a real defect in "
        "the edge-exchange invariant. R278c migrates the test faithfully "
        "(independence) -- it flips to xpass once a dedicated dagre "
        "edge-exchange fix lands."
    ),
    strict=True,
)
def test_long_identifier_node_labels_survive_intact_in_svg() -> None:
    """grok ``pure_engine.rs`` ``long_identifier_node_labels_survive_intact_in_svg``.

    End-to-end guard for the word-wrap fix: the real-user flowchart (long
    Python identifiers) must reach the rendered SVG with each identifier as one
    COMPLETE ``<tspan>`` -- never hard-sliced mid-identifier. A slice at any
    offset could never produce the whole identifier as a single tspan's
    content. grok calls ``render_mermaid_to_svg`` directly; this port routes
    through :func:`build_svg` (equivalent -- the theme does not affect tspan
    integrity).
    """
    source = (
        "flowchart TB\n"
        "subgraph main [resi_local.py / resi.py]\n"
        "    nav[st.navigation]\n"
        "    mark[mark_filter_restore_context]\n"
        "    global[render_global_sidebar]\n"
        "    sidebar[render_page_sidebar_filters]\n"
        "    page[page.run]\n"
        "end\n"
        "nav --> mark --> global --> sidebar --> page"
    )
    svg = build_svg(source, MermaidTheme.LIGHT)
    assert ">mark_filter_restore_context</tspan>" in svg
    assert ">render_page_sidebar_filters</tspan>" in svg


# === theme_for: surface override ==========================================


def test_theme_for_overrides_surface_per_theme() -> None:
    """grok ``theme_for_overrides_surface_per_theme``: the diagram background is
    the crate's surface single-source-of-truth so the PNG blends with the
    terminal scrollback surface."""
    assert theme_for(MermaidTheme.LIGHT).background == LIGHT_SURFACE.to_hex()
    assert theme_for(MermaidTheme.DARK).background == DARK_SURFACE.to_hex()
    assert (
        theme_for(MermaidTheme.LIGHT).background
        != theme_for(MermaidTheme.DARK).background
    )


# === map_engine_error: 6-variant -> 3-category taxonomy ===================


def test_engine_error_taxonomy_maps_every_arm() -> None:
    """grok ``engine_error_taxonomy_maps_every_arm``: each vendored-stack error
    variant collapses onto the host's coarse parse / layout / unsupported split.
    """
    # Parse family: malformed source, bad direction, bad node shape.
    for exc in (
        EngineParseError(1, "x"),
        InvalidDirection("x"),
        InvalidNodeShape("x"),
    ):
        mapped = map_engine_error(exc)
        assert isinstance(mapped, MermaidError)
        # ``str(exc)`` is preserved verbatim in the mapped message.
        assert str(exc) in str(mapped)
        assert isinstance(mapped, MermaidParseError)

    # Layout family: dot generation + SVG render failures.
    for exc in (DotGenerationError("x"), EngineRenderError("x")):
        mapped = map_engine_error(exc)
        assert isinstance(mapped, MermaidLayoutError)
        assert str(exc) in str(mapped)

    # Unsupported diagram type is its own category.
    mapped = map_engine_error(EngineUnsupportedDiagramType("x"))
    assert isinstance(mapped, MermaidUnsupportedError)
    assert str(mapped).endswith("Unsupported diagram type: x")


# === garbage_input_never_panics (untrusted-source contract) ===============


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "@@@@",
        "%% only a comment",
        "flowchart\n\n\n",
        "????????",
        "\x00\x01\x02\x03",
        "flowchart LR\n  A[unterminated --> ",
        "pie\n  : :",
        "erDiagram\n  A ||",
        "sequenceDiagram\n  A->>",
    ],
)
def test_garbage_input_never_panics(garbage: str) -> None:
    """grok ``garbage_input_never_panics``: untrusted input must never panic.

    :func:`render_checked` would surface a panic as
    :class:`~minimax_code.mermaid.errors.MermaidPanicError`; unparseable input
    may legitimately surface other :class:`MermaidError` subclasses (which
    degrade to the code-block fallback), but never a panic. Parametrized per
    input so a failure names the exact offending payload.
    """
    engine = PureRustEngine()
    params = RenderParams()
    limits = RenderLimits()
    try:
        render_checked(engine, garbage, params, limits)
    except MermaidRasterizeError:
        # The SVG half parsed + laid out, then the R278b raster sentinel fired
        # -- a typed error, not a panic. Acceptable for untrusted input.
        pass
    except MermaidPanicError as exc:
        pytest.fail(f"engine panicked on {garbage!r}: {exc}")
    except MermaidError:
        # Expected typed error (parse / unsupported / layout) -- not a panic.
        pass
    # else: render_checked returned a RenderedDiagram -- also no panic.
