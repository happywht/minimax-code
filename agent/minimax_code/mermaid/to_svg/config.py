"""Mermaid front-matter parser and render config (R270 -- direction (1) leaf 2).

Ports the **front-matter layer** of grok's vendored ``mermaid-to-svg`` layout
crate -- the parser that splits a mermaid source string into the diagram
``body`` and the YAML ``front-matter`` metadata, and the typed
:class:`RenderConfig` that metadata resolves into. This is the second leaf of
the render-stack migration (direction (1): wire dagre so mermaid source renders
to SVG). It consumes the R269 theme layer -- :meth:`MermaidThemePreset.parse`
turns a ``config.theme`` string into a preset, and
:meth:`MermaidThemeVariables.apply_mermaid_alias` / :meth:`apply_to` carry the
``themeVariables`` overrides. It is the first render-stack leaf with a
non-stdlib dependency (``pyyaml``), mirroring grok's ``serde_yaml``.

.. note::

   This is a **different crate** from the R38 ``xai-grok-mermaid`` host. The
   host picks a raster surface and an engine; this layer is what runs first --
   it reads the ``---\\n...\\n---`` block a user may put at the top of a
   mermaid diagram, resolves the theme + spacing knobs, and hands the body
   downstream. The R269 theme module supplies the resolved palette this layer
   selects; a later leaf (the parser/AST) consumes the ``body`` this layer
   returns.

What lives here:

* :class:`MermaidFrontmatter` -- the ``title`` a front-matter may carry (the
  only front-matter field grok reads outside ``config:``).
* :class:`FlowchartConfig` -- the ``config.flowchart`` sub-block (curve,
  spacing, padding, html_labels, ...). Several fields are parsed for mermaid
  front-matter compatibility but not currently rendered (mirrors grok's
  doc-comments verbatim).
* :class:`RenderConfig` -- the typed ``config:`` block. Carries the resolved
  :class:`MermaidThemePreset`, the :class:`MermaidThemeVariables` override bag,
  and the font/flowchart knobs. :meth:`RenderConfig.to_mermaid_theme` runs the
  R269 pipeline (preset -> palette -> overrides); :meth:`font_size_px` parses
  the ``fontSize`` string into a positive pixel number.
* :class:`ParsedMermaidSource` -- the (body, frontmatter, config) triple
  :func:`parse_mermaid_frontmatter` returns.
* :func:`parse_mermaid_frontmatter` -- the entry point. Splits the source,
  parses the YAML block, and builds the typed config.

Mapping
-------

* ``ParsedMermaidSource<'a>`` carries ``body: Cow<'a, str>`` -- ``Borrowed``
  when the source has no front-matter (the body *is* the source), ``Owned``
  when it does (the body is the tail past the closing ``---``). Python
  ``str`` is immutable reference semantics, so a single ``body: str`` field
  covers both arms (``source`` verbatim vs ``source[body_start:]``). The
  lifetime parameter evaporates.
* ``Cow<'a, str>`` byte offsets: grok's ``frontmatter_bounds`` /
  ``next_line_end`` index ``&str`` by **byte** (``find('\\n')`` returns a byte
  position). Python ``str`` indexes by **codepoint**. The structural markers
  (``\\n``, ``---``) are ASCII, so a codepoint-based scan locates the same
  boundaries on sources with non-ASCII front-matter (e.g. a CJK ``title``);
  the internal offset numbers differ from grok's but the extracted
  body/yaml substrings are byte-for-byte equivalent. This is a semantic clone,
  not a byte-offset clone.
* The YAML value tree: grok uses ``serde_yaml::Value`` (Null / Bool / Number /
  String / Sequence / Mapping); this layer uses ``yaml.safe_load`` (None /
  bool / int / float / str / list / dict). The helpers below narrow the tree
  to the four scalar kinds grok's extractors accept.
* ``parse_yaml_value`` is **three-state** in grok: empty YAML ->
  ``Some(Value::Null)`` (success, but null), a parse error -> ``None``
  (failure), anything else -> ``Some(Value)``. The caller treats failure
  (``None``) as "fall back to defaults" but null (``Some(Null)``) as "continue
  extracting" (every extractor returns ``None`` on a null root, yielding the
  all-default config). Python ``None`` is ambiguous between "YAML null" and
  "parse error", so :func:`_parse_yaml_value` returns the ``_PARSE_FAILED``
  sentinel on error to keep the two paths distinct.
* ``value_to_string`` on a bool: grok ``Value::Bool(true).to_string()`` is
  ``"true"`` (Rust ``bool`` Display is lowercase); Python ``str(True)`` is
  ``"True"``. :func:`_value_to_string` lowercases the bool explicitly so a
  ``theme: true`` oddity round-trips identically.
* ``value_to_u32``: grok ``u32::from_str`` rejects ``"-1"`` (unsigned), so a
  string scalar is parsed then range-checked ``0 <= v < 2**32``. Python
  ``int("-1")`` would otherwise succeed and leak a negative through. ``bool``
  is a subclass of ``int`` in Python, so the bool arm is checked first
  (grok's ``Value::Number`` never carries a bool).
* ``Option<T>`` -> ``T | None`` (UP007); ``String`` -> ``str``; ``u32`` ->
  ``int`` (range-checked at the parse boundary).

Product fusion
--------------

A future wiring round passes model mermaid output through
:func:`parse_mermaid_frontmatter`, reads :meth:`RenderConfig.to_mermaid_theme`
for the resolved palette, and hands the ``body`` to the parser/AST leaf (next
render-stack leaf). The dagre layout (ported R246--R268) then positions the
parsed nodes; the SVG renderer (later leaf) fills them with the palette
resolved here. This module is the front-door the renderer pipeline starts at.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import yaml

from .theme import MermaidTheme, MermaidThemePreset, MermaidThemeVariables

__all__ = [
    "FlowchartConfig",
    "MermaidFrontmatter",
    "ParsedMermaidSource",
    "RenderConfig",
    "parse_mermaid_frontmatter",
]


@dataclass
class MermaidFrontmatter:
    """The mermaid front-matter metadata outside the ``config:`` block.

    Mirrors grok ``MermaidFrontmatter`` -- a value type (``Debug + Clone +
    Default + PartialEq + Eq``) carrying only the optional diagram ``title``.
    Every other front-matter field lives under ``config:`` (see
    :class:`RenderConfig`).
    """

    #: Optional diagram title (``front-matter`` ``title:`` key).
    title: str | None = None


@dataclass
class FlowchartConfig:
    """The ``config.flowchart`` sub-block.

    Mirrors grok ``FlowchartConfig`` -- a value type with the flowchart
    spacing/curve knobs. Fields tagged "parsed for compatibility, not
    rendered" mirror grok's doc-comments verbatim: the parser accepts them so
    real-world mermaid front-matter does not error, but the renderer pipeline
    does not yet read them.
    """

    #: Edge curve style (``linear`` / ``basis`` / ...).
    curve: str | None = None
    #: Parsed for mermaid front-matter compatibility, not currently rendered.
    html_labels: bool | None = None
    #: Horizontal spacing between nodes in the same rank.
    node_spacing: int | None = None
    #: Vertical spacing between ranks.
    rank_spacing: int | None = None
    #: Padding inside each node.
    padding: int | None = None
    #: Parsed for mermaid front-matter compatibility, not currently rendered.
    diagram_padding: int | None = None
    #: Text wrapping width for node labels.
    wrapping_width: int | None = None
    #: Parsed for mermaid front-matter compatibility, not currently rendered.
    use_max_width: bool | None = None
    #: Parsed for mermaid front-matter compatibility, not currently rendered.
    default_renderer: str | None = None


@dataclass
class RenderConfig:
    """The typed ``config:`` block a mermaid front-matter resolves into.

    Mirrors grok ``RenderConfig`` -- a value type (``Debug + Clone + Default +
    PartialEq``). Carries the resolved :class:`MermaidThemePreset`, the
    :class:`MermaidThemeVariables` override bag, and the font + flowchart
    knobs. :meth:`to_mermaid_theme` runs the R269 pipeline (preset -> palette
    -> overrides applied) and is the entry point the SVG renderer will read
    for the resolved palette.
    """

    #: The named theme preset a ``config.theme`` string resolved to.
    theme: MermaidThemePreset | None = None
    #: The ``config.themeVariables`` override bag (defaults to empty).
    theme_variables: MermaidThemeVariables = field(default_factory=MermaidThemeVariables)
    #: Parsed for mermaid front-matter compatibility, not currently rendered.
    layout: str | None = None
    #: Parsed for mermaid front-matter compatibility, not currently rendered.
    look: str | None = None
    #: Parsed for mermaid front-matter compatibility, not currently rendered.
    security_level: str | None = None
    #: Label font family.
    font_family: str | None = None
    #: Label font size (string form, e.g. ``"16px"``; see :meth:`font_size_px`).
    font_size: str | None = None
    #: The ``config.flowchart`` sub-block (defaults to empty).
    flowchart: FlowchartConfig = field(default_factory=FlowchartConfig)

    def to_mermaid_theme(self) -> MermaidTheme | None:
        """Resolve this config into a :class:`MermaidTheme` palette (grok ``to_mermaid_theme``).

        Returns ``None`` when neither a preset nor any override is set (the
        caller renders with the default palette). Otherwise resolves the
        preset (defaulting to :attr:`MermaidThemePreset.DEFAULT` when only
        overrides are set) and stamps the overrides onto it via the R269
        :meth:`MermaidThemeVariables.apply_to` pipeline. Returns a fresh
        instance (the preset factory + apply_to both allocate).
        """
        if self.theme is None and self.theme_variables.is_empty():
            return None
        preset = self.theme if self.theme is not None else MermaidThemePreset.DEFAULT
        theme = preset.to_theme()
        self.theme_variables.apply_to(theme)
        return theme

    def font_size_px(self) -> float | None:
        """Parse :attr:`font_size` into a positive pixel number (grok ``font_size_px``).

        Returns ``None`` when no font size is set or the string does not parse
        to a finite positive number. See :func:`_parse_font_size` for the
        ``"16px"`` / ``" 16 "`` tolerance rules.
        """
        if self.font_size is None:
            return None
        return _parse_font_size(self.font_size)


@dataclass
class ParsedMermaidSource:
    """The (body, frontmatter, config) triple :func:`parse_mermaid_frontmatter` returns.

    Mirrors grok ``ParsedMermaidSource<'a>`` minus the lifetime parameter: the
    ``Cow<'a, str>`` body collapses to a plain ``str`` (Python strings are
    immutable reference-semantic, so the borrowed-vs-owned distinction is
    moot). ``frontmatter`` is ``None`` when the source has no front-matter
    block; ``config`` is always present (defaulting to an empty
    :class:`RenderConfig`).
    """

    #: The diagram body (source verbatim when no front-matter; tail past ``---`` otherwise).
    body: str = ""
    #: The parsed front-matter metadata (``None`` when absent).
    frontmatter: MermaidFrontmatter | None = None
    #: The typed render config (always present; default when no ``config:`` block).
    config: RenderConfig = field(default_factory=RenderConfig)


def parse_mermaid_frontmatter(source: str) -> ParsedMermaidSource:
    """Split ``source`` into body + front-matter metadata + render config.

    Mirrors grok ``parse_mermaid_frontmatter``: when the source has no
    opening ``---`` fence the whole source is the body and ``frontmatter`` is
    ``None``; when it has a ``---\\n...\\n---`` block the body is the tail past
    the closing fence, ``frontmatter`` carries the title, and ``config``
    carries the typed ``config:`` block. A malformed YAML block (or a fence
    with no closing ``---``) yields an empty :class:`MermaidFrontmatter` +
    empty :class:`RenderConfig` rather than raising -- mirrors grok's
    fall-through-to-default behavior.
    """
    bounds = _frontmatter_bounds(source)
    if bounds is None:
        return ParsedMermaidSource(body=source, frontmatter=None, config=RenderConfig())

    yaml_start, yaml_end, body_start = bounds
    body = source[body_start:]
    yaml_text = source[yaml_start:yaml_end]

    value = _parse_yaml_value(yaml_text)
    if value is _PARSE_FAILED:
        # grok: parse_yaml_value returned None (serde_yaml error). A fence is
        # present (so frontmatter is Some) but the YAML does not parse -- fall
        # through to an empty frontmatter + default config.
        return ParsedMermaidSource(
            body=body,
            frontmatter=MermaidFrontmatter(),
            config=RenderConfig(),
        )

    return ParsedMermaidSource(
        body=body,
        frontmatter=_parse_frontmatter_metadata(value),
        config=_parse_render_config(value),
    )


# === YAML value-tree extractors ============================================
#
# grok models these as ``Option<&Value> -> .and_then(extractor)`` chains over
# the serde_yaml::Value enum. Python narrows the yaml.safe_load result (None /
# bool / int / float / str / list / dict) with isinstance checks. The two
# representations line up kind-for-kind on the four scalar arms grok accepts.


def _parse_yaml_value(yaml_text: str) -> Any:
    """Parse a YAML block, distinguishing empty/null from parse error.

    Mirrors grok ``parse_yaml_value``: empty/whitespace YAML returns ``None``
    (grok ``Value::Null``; Python ``None`` stands in for YAML null); a parse
    error returns the :data:`_PARSE_FAILED` sentinel (grok ``None``); anything
    else returns the parsed value. The sentinel keeps "empty YAML -> continue
    extracting (yields all-default)" distinct from "parse error -> fall back
    to defaults", which a plain ``None`` would conflate.
    """
    if not yaml_text.strip():
        return None  # grok Value::Null
    try:
        return yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return _PARSE_FAILED


def _parse_frontmatter_metadata(value: Any) -> MermaidFrontmatter:
    """Extract the ``title`` from the YAML root (grok ``parse_frontmatter_metadata``)."""
    return MermaidFrontmatter(title=_value_to_string(_mapping_value(value, "title")))


def _parse_render_config(value: Any) -> RenderConfig:
    """Build a :class:`RenderConfig` from the ``config:`` mapping.

    Mirrors grok ``parse_render_config``: a missing ``config`` key (or a null
    / non-mapping value) yields the default. Each field is extracted by its
    mermaid wire name (``securityLevel`` / ``fontFamily`` / ``nodeSpacing`` /
    ...) and narrowed through the matching scalar extractor.
    """
    config = _mapping_value(value, "config")
    if config is None:
        return RenderConfig()

    theme_str = _value_to_string(_mapping_value(config, "theme"))
    theme = MermaidThemePreset.parse(theme_str) if theme_str is not None else None

    return RenderConfig(
        theme=theme,
        theme_variables=_parse_theme_variables(_mapping_value(config, "themeVariables")),
        layout=_value_to_string(_mapping_value(config, "layout")),
        look=_value_to_string(_mapping_value(config, "look")),
        security_level=_value_to_string(_mapping_value(config, "securityLevel")),
        font_family=_value_to_string(_mapping_value(config, "fontFamily")),
        font_size=_value_to_string(_mapping_value(config, "fontSize")),
        flowchart=_parse_flowchart_config(_mapping_value(config, "flowchart")),
    )


def _parse_theme_variables(value: Any) -> MermaidThemeVariables:
    """Build a :class:`MermaidThemeVariables` override bag from ``themeVariables``.

    Mirrors grok ``parse_theme_variables``: a non-mapping value yields an empty
    bag; otherwise each (key, value) entry is stringified and routed through
    :meth:`MermaidThemeVariables.apply_mermaid_alias` (unknown aliases drop
    silently, matching grok's per-entry ``apply_mermaid_alias`` return value).
    """
    variables = MermaidThemeVariables()
    if not isinstance(value, dict):
        return variables

    for key, val in value.items():
        if not isinstance(key, str):
            continue
        value_str = _value_to_string(val)
        if value_str is None:
            continue
        variables.apply_mermaid_alias(key, value_str)
    return variables


def _parse_flowchart_config(value: Any) -> FlowchartConfig:
    """Build a :class:`FlowchartConfig` from the ``flowchart:`` sub-mapping.

    Mirrors grok ``parse_flowchart_config``: a missing/null ``flowchart`` value
    yields the default; otherwise each flowchart knob is extracted by its
    mermaid wire name and narrowed through the matching scalar extractor.
    """
    if value is None:
        return FlowchartConfig()

    return FlowchartConfig(
        curve=_value_to_string(_mapping_value(value, "curve")),
        html_labels=_value_to_bool(_mapping_value(value, "htmlLabels")),
        node_spacing=_value_to_u32(_mapping_value(value, "nodeSpacing")),
        rank_spacing=_value_to_u32(_mapping_value(value, "rankSpacing")),
        padding=_value_to_u32(_mapping_value(value, "padding")),
        diagram_padding=_value_to_u32(_mapping_value(value, "diagramPadding")),
        wrapping_width=_value_to_u32(_mapping_value(value, "wrappingWidth")),
        use_max_width=_value_to_bool(_mapping_value(value, "useMaxWidth")),
        default_renderer=_value_to_string(_mapping_value(value, "defaultRenderer")),
    )


def _mapping_value(value: Any, key: str) -> Any:
    """Fetch ``key`` from a YAML mapping (grok ``mapping_value``).

    Returns ``None`` when ``value`` is not a mapping or the key is absent.
    Matches grok's ``Value::Mapping(mapping).get(&Value::String(key))``: only
    string keys match (a numeric YAML key does not).
    """
    if not isinstance(value, dict):
        return None
    return value.get(key)


def _value_to_string(value: Any) -> str | None:
    """Narrow a YAML scalar to a string (grok ``value_to_string``).

    Strings round-trip verbatim; numbers render via ``str()``; booleans render
    lowercase (``"true"`` / ``"false"``) to match grok's ``bool`` Display.
    Everything else (None / list / dict) returns ``None``. ``bool`` is checked
    before ``int`` because ``isinstance(True, int)`` is ``True`` in Python.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return None


def _value_to_bool(value: Any) -> bool | None:
    """Narrow a YAML scalar to a bool (grok ``value_to_bool``).

    A native bool round-trips; the strings ``"true"`` / ``"false"`` parse;
    every other scalar (including ``"True"``, ``1``, ``0``) returns ``None``.
    Matches grok's two match arms exactly.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value == "true":
            return True
        if value == "false":
            return False
        return None
    return None


def _value_to_u32(value: Any) -> int | None:
    """Narrow a YAML scalar to a u32 (grok ``value_to_u32``).

    A number must fit in ``0 <= v < 2**32`` (mirrors ``u32::try_from``); a
    float is accepted only when integer-valued (mirrors ``Number::as_u64``).
    A string parses via ``int()`` then the same range check (so ``"-1"``
    rejects, matching ``u32::from_str``). ``bool`` is rejected -- grok's
    ``Value::Number`` never carries a bool, and ``bool`` is an ``int``
    subclass in Python.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value < _U32_MAX else None
    if isinstance(value, float):
        if value.is_integer():
            iv = int(value)
            return iv if 0 <= iv < _U32_MAX else None
        return None
    if isinstance(value, str):
        try:
            iv = int(value)
        except ValueError:
            return None
        return iv if 0 <= iv < _U32_MAX else None
    return None


def _parse_font_size(value: str) -> float | None:
    """Parse a font-size string into a finite positive float (grok ``parse_font_size``).

    Trims, drops an optional ``px`` suffix, parses as float, and keeps only
    finite positive values. ``"16px"`` / ``" 16 "`` / ``"16.5"`` -> 16[.5];
    ``"0"`` / ``"-3"`` / ``"inf"`` / ``"NaN"`` / ``"abc"`` -> ``None``.
    """
    trimmed = value.strip()
    if trimmed.endswith("px"):
        numeric = trimmed[:-2].strip()
    else:
        numeric = trimmed
    try:
        size = float(numeric)
    except ValueError:
        return None
    if math.isfinite(size) and size > 0.0:
        return size
    return None


# === Front-matter fence scanner ============================================
#
# grok indexes &str by byte; this port indexes str by codepoint. The fence
# markers (---, \n) are ASCII so the scan lands on the same boundaries; the
# extracted substrings are byte-for-byte identical to grok's on any source.


def _frontmatter_bounds(source: str) -> tuple[int, int, int] | None:
    """Locate the ``---\\n...\\n---`` front-matter block (grok ``frontmatter_bounds``).

    Returns ``(yaml_start, yaml_end, body_start)`` -- the yaml block spans
    ``source[yaml_start:yaml_end]`` and the body starts at ``body_start``.
    Returns ``None`` when the source has no leading ``---`` fence or the
    fence is unclosed. Leading blank lines are skipped (grok scans past them
    to the first content line, which must be the opening ``---``).
    """
    cursor = 0
    while cursor < len(source):
        end = _next_line_end(source, cursor)
        line = source[cursor:end].strip()
        if line == "":
            cursor = end
            continue
        if line != "---":
            return None

        # Opening fence found; scan for the closing "---".
        yaml_start = end
        scan = end
        while scan < len(source):
            scan_end = _next_line_end(source, scan)
            if source[scan:scan_end].strip() == "---":
                return (yaml_start, scan, scan_end)
            scan = scan_end
        return None

    return None


def _next_line_end(source: str, start: int) -> int:
    """Index of the character after the next ``\\n`` at/after ``start`` (grok ``next_line_end``).

    Returns ``len(source)`` when there is no further newline (the tail is the
    final line, consumed verbatim).
    """
    nl = source.find("\n", start)
    if nl == -1:
        return len(source)
    return nl + 1


#: Sentinel distinguishing a YAML parse error (fall back to defaults) from an
#: empty/null YAML block (continue extracting, yields all-default). grok splits
#: these via ``Option<Value>`` (``None`` vs ``Some(Value::Null)``); a bare
#: Python ``None`` cannot tell them apart, so the parse-error path returns this
#: unique object instead.
_PARSE_FAILED: Any = object()

#: Exclusive upper bound for u32 (``2**32``). Used by :func:`_value_to_u32` to
#: mirror grok's ``u32::try_from`` range check.
_U32_MAX: int = 1 << 32
