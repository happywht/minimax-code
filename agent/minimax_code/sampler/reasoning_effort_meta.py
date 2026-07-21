"""Reasoning-effort meta read/write subsystem (R213, ``xai-grok-sampling-types``
``types.rs`` 765-1008).

R213 lands a cohesive subsystem from ``types.rs`` -- the per-model
**reasoning-effort meta** readers/writers that the ACP ``meta`` dict carries
under three keys (``reasoningEffort`` singular persisted pref,
``supportsReasoningEffort`` capability flag, ``reasoningEfforts`` per-model
menu). This is the single home for the skip-invalid forward-compat rule
shared by the meta reader and the remote ``/models`` parser.

Migration scope (zero external crate dependency -- consumes only the R206
:class:`ReasoningEffort` wire-string enum + ``dict`` / ``list``):

- 3 wire constants (:data:`REASONING_EFFORT_META_KEY` +
  :data:`SUPPORTS_REASONING_EFFORT_META_KEY` + :data:`REASONING_EFFORTS_META_KEY`).
- :func:`parse_canonical_effort_token` -- canonical wire token parser
  accepting the ``"max"`` CLI/UX alias of ``Xhigh`` (mirror grok
  ``FromStr::parse().ok()``).
- :func:`supports_reasoning_effort_meta` -- read the capability bool
  (default ``False``).
- :func:`parse_reasoning_effort_meta` -- read the persisted-effort string
  (``None`` on type-mismatch / unknown variant, with a warn so a bad value
  never overwrites the user's pref on the next save).
- :func:`reasoning_effort_meta_value` -- serialize an effort back to its
  wire string (mirror grok ``Value::String(effort.as_str())``).
- :class:`ReasoningEffortOption` -- a single selectable menu entry
  (``id`` / ``value`` / ``label`` / ``description`` / ``default``).
- :func:`parse_reasoning_effort_options` -- parse a JSON array element-by-
  element with the untagged Bare-string-vs-Full-table shape (skip-invalid +
  warn).
- :func:`parse_reasoning_efforts_meta` -- read the per-model menu
  (``None`` when absent / not an array / yields no usable options -- so
  "absent" and "present-but-unusable" collapse to the same fallback path).
- :func:`reasoning_efforts_meta_value` -- serialize a menu back to its
  wire array (mirror grok ``serde_json::to_value``).

Deferred (YAGNI / blocked): grok ``impl ReasoningEffort``'s
``to_responses_api`` / ``from_responses_api`` (both depend on the un-landed
``crate::rs::ReasoningEffort`` peer), and ``as_str`` / ``Display`` (the R206
:class:`ReasoningEffort` is a :class:`enum.StrEnum`, so :func:`str` already
covers both faithfully). ``to_messages_api`` (the Anthropic Messages API
``output_config.effort`` string with the ``Xhigh -> "max"`` wire remap) is a
method on the enum, not part of the meta subsystem -- it lands with the
Messages-API serializer that consumes it.

This module is no-I/O (pure value-level transforms over ``dict`` / ``list``).
Migration map (grok -> Python):

- ``serde_json::Map<String, Value>`` -> :class:`collections.abc.Mapping` of
  ``str`` -> ``Any`` (the wire ``meta`` dict); ``Option<&Map>`` ->
  ``Mapping | None``.
- ``serde_json::Value::String(s)`` / ``Value::Array(a)`` -> ``str`` /
  :class:`list` (Python's native JSON-equivalent primitives).
- ``tracing::warn!(...)`` -> :class:`logging.getLogger(__name__).warning`
  (non-blocking diagnostic -- the skip-invalid rule is the behavior; the log
  is observability).
- ``Vec<ReasoningEffortOption>`` -> :class:`tuple` (the project-wide
  ``Vec -> tuple`` convention for parsed wire sequences).
- ``&[Value]`` / ``&[ReasoningEffortOption]`` -> the read-only
  :class:`~collections.abc.Sequence`.

Naming: the 3 constants and 7 free functions keep the grok names verbatim
(free functions, no Anthropic Messages API peer collision). The private
``_humanize_effort_id`` helper mirrors grok's private ``humanize_effort_id``.

YAGNI: the meta subsystem's contract IS the read/write + skip-invalid rule.
No ``Serialize`` derive surface beyond :meth:`ReasoningEffortOption.to_payload`
(the grok ``#[derive(Serialize)]`` mirror; ``description: None`` serializes
to ``"description": null`` -- serde's default for ``Option`` without
``skip_serializing_if``, faithfully preserved).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from minimax_code.sampler.chat_completion_leaves import ReasoningEffort

__all__ = [
    "REASONING_EFFORT_META_KEY",
    "REASONING_EFFORTS_META_KEY",
    "SUPPORTS_REASONING_EFFORT_META_KEY",
    "ReasoningEffortOption",
    "parse_canonical_effort_token",
    "parse_reasoning_effort_meta",
    "parse_reasoning_effort_options",
    "parse_reasoning_efforts_meta",
    "reasoning_effort_meta_value",
    "reasoning_efforts_meta_value",
    "supports_reasoning_effort_meta",
]

_LOGGER = logging.getLogger(__name__)

# --- wire keys (grok ``pub const ... : &str``) ------------------------------

REASONING_EFFORT_META_KEY = "reasoningEffort"
"""Singular persisted-effort key in a model's ACP ``meta`` dict."""

SUPPORTS_REASONING_EFFORT_META_KEY = "supportsReasoningEffort"
"""Capability flag -- whether the model honors ``reasoningEffort`` at all."""

REASONING_EFFORTS_META_KEY = "reasoningEfforts"
"""Plural per-model menu key -- the list of selectable effort tiers."""

# --- canonical token parse (grok ``impl FromStr`` + ``parse_canonical_effort_token``) --
#
# grok's ``FromStr`` lowercases then matches; ``"max"`` is a CLI/UX alias of
# ``Xhigh`` (NOT a wire variant -- the serde Deserialize of ``ReasoningEffort``
# itself does NOT accept ``"max"``, only the ``FromStr`` does). This asymmetry
# matters for the untagged Bare-vs-Full option parse below: a Bare string
# routes through ``FromStr`` (accepts ``max``), but a Full table's ``value``
# field routes through serde Deserialize (rejects ``max``).
_CANONICAL_TOKENS: dict[str, ReasoningEffort] = {
    "none": ReasoningEffort.NONE,
    "minimal": ReasoningEffort.MINIMAL,
    "low": ReasoningEffort.LOW,
    "medium": ReasoningEffort.MEDIUM,
    "high": ReasoningEffort.HIGH,
    "xhigh": ReasoningEffort.XHIGH,
    "max": ReasoningEffort.XHIGH,
}


def parse_canonical_effort_token(token: str) -> ReasoningEffort | None:
    """Mirror grok ``parse_canonical_effort_token``: canonical wire parse
    (``max`` -> :attr:`ReasoningEffort.XHIGH` alias), returning ``None`` on
    unknown tokens (grok ``token.parse().ok()``).

    Case-insensitive (grok lowercases before matching). Unlike the strict
    :meth:`ReasoningEffort.from_payload` serde parser, this accepts ``"max"``
    as a forward alias of ``Xhigh`` (it is the single canonical-token entry
    point used by the meta readers + the Bare option path).
    """
    return _CANONICAL_TOKENS.get(str(token).lower())


# --- singular meta read/write ----------------------------------------------


def supports_reasoning_effort_meta(meta: Mapping[str, Any] | None) -> bool:
    """Mirror grok ``supports_reasoning_effort_meta``: read the
    ``supportsReasoningEffort`` bool, defaulting to ``False`` on absent /
    non-bool (grok ``meta.and_then(get).and_then(as_bool).unwrap_or(false)``).
    """
    if meta is None:
        return False
    raw = meta.get(SUPPORTS_REASONING_EFFORT_META_KEY)
    return raw if isinstance(raw, bool) else False


def parse_reasoning_effort_meta(
    meta: Mapping[str, Any] | None,
) -> ReasoningEffort | None:
    """Mirror grok ``parse_reasoning_effort_meta``: read the
    ``reasoningEffort`` string.

    Returns ``None`` on type-mismatch or unknown variant, logging a warn so a
    bad persisted value is ignored rather than overwriting the user's pref on
    the next save. The string parse accepts the ``"max"`` alias (routes through
    :func:`parse_canonical_effort_token`, mirroring grok's ``s.parse()`` which
    is ``FromStr``, NOT the serde Deserialize).
    """
    if meta is None or REASONING_EFFORT_META_KEY not in meta:
        return None
    raw = meta[REASONING_EFFORT_META_KEY]
    if not isinstance(raw, str):
        _LOGGER.warning(
            "meta.reasoningEffort: expected string, ignoring: %r", raw
        )
        return None
    effort = parse_canonical_effort_token(raw)
    if effort is None:
        _LOGGER.warning(
            "meta.reasoningEffort: parse failed, ignoring: %r", raw
        )
        return None
    return effort


def reasoning_effort_meta_value(effort: ReasoningEffort) -> str:
    """Mirror grok ``reasoning_effort_meta_value``: serialize an effort to its
    wire string (grok ``Value::String(effort.as_str())``). The R206
    :class:`ReasoningEffort` is a :class:`enum.StrEnum`, so :func:`str` yields
    the canonical wire token (``"xhigh"`` etc.) -- ``as_str`` has no Python
    peer beyond :func:`str`.
    """
    return str(effort)


# --- ReasoningEffortOption (grok struct + untagged Deserialize) ------------


@dataclass(frozen=True, slots=True)
class ReasoningEffortOption:
    """A single selectable reasoning-effort menu entry (grok
    ``ReasoningEffortOption``).

    ``id`` / ``label`` are presentation and input; ``value`` is the canonical
    value sent on the wire; ``description`` is optional UI copy; ``default``
    marks the model's preferred tier. Frozen + slotted to mirror grok's
    immutable, field-stable ``#[derive(Clone, Debug, PartialEq, Eq, Serialize)]``
    struct.
    """

    id: str
    value: ReasoningEffort
    label: str
    description: str | None
    default: bool

    def to_payload(self) -> dict[str, Any]:
        """Mirror grok ``#[derive(Serialize)]``: emit all 5 fields in struct
        order. ``description: None`` serializes to ``"description": null``
        (serde's default for ``Option`` without ``skip_serializing_if``,
        faithfully preserved). The ``value`` field emits its lowercase wire
        string (the R206 StrEnum value).
        """
        return {
            "id": self.id,
            "value": str(self.value),
            "label": self.label,
            "description": self.description,
            "default": self.default,
        }


def _humanize_effort_id(effort_id: str) -> str:
    """Mirror grok private ``humanize_effort_id``: uppercase the first
    character of an id for a default label (``"xhigh"`` -> ``"Xhigh"``,
    ``"deep"`` -> ``"Deep"``). Empty input -> empty string.
    """
    if not effort_id:
        return ""
    return effort_id[0].upper() + effort_id[1:]


def _optional_str_field(
    meta: Mapping[str, Any], key: str
) -> tuple[str | None, bool]:
    """Extract an ``Option<String>`` field from a Full-option table.

    Returns ``(value, ok)``: ``ok=False`` signals a type mismatch (the JSON
    value is present but neither a string nor null), which mirrors serde
    rejecting a non-string for ``Option<String>`` -- the caller skips the
    whole entry. Absent key or explicit ``None`` -> ``(None, True)``.
    """
    if key not in meta:
        return None, True
    raw = meta[key]
    if raw is None:
        return None, True
    if isinstance(raw, str):
        return raw, True
    return None, False


def _parse_option(element: object) -> ReasoningEffortOption | None:
    """Mirror the untagged ``RawReasoningEffortOption::Bare | Full`` deserialize
    for a single array element.

    Two shapes, tried in grok's untagged order:

    - **Bare** (a canonical value string, e.g. ``"xhigh"``): parse via
      :func:`parse_canonical_effort_token` (``FromStr``, accepts ``"max"``);
      ``id`` defaults to the wire string, ``label`` to the humanized id,
      ``description`` -> ``None``, ``default`` -> ``False``.
    - **Full** (a table with ``value`` required, rest optional): ``value``
      parses via the STRICT :meth:`ReasoningEffort.from_payload` (serde
      Deserialize, does NOT accept ``"max"``); ``id`` defaults to the wire
      string, ``label`` to the humanized id, ``description`` -> ``None``,
      ``default`` -> ``False``.

    Returns ``None`` (skip + warn) on any shape / type / parse failure --
    mirrors grok's skip-invalid forward-compat for tiers a newer server
    introduces.
    """
    # Bare canonical value string.
    if isinstance(element, str):
        value = parse_canonical_effort_token(element)
        if value is None:
            _LOGGER.warning(
                "reasoningEfforts: skipping invalid bare entry: %r", element
            )
            return None
        opt_id = str(value)
        return ReasoningEffortOption(
            id=opt_id,
            value=value,
            label=_humanize_effort_id(opt_id),
            description=None,
            default=False,
        )
    # Full table.
    if not isinstance(element, Mapping):
        _LOGGER.warning(
            "reasoningEfforts: skipping invalid entry (not string or object): %r",
            element,
        )
        return None
    table = dict(element)
    if "value" not in table:
        _LOGGER.warning(
            "reasoningEfforts: skipping entry missing value: %r", table
        )
        return None
    try:
        # Full.value uses serde Deserialize (strict, NO "max" alias).
        value = ReasoningEffort.from_payload(table["value"])
    except (ValueError, TypeError):
        _LOGGER.warning(
            "reasoningEfforts: skipping entry with invalid value: %r", table
        )
        return None
    # id: Option<String> -- default to the value's wire string.
    opt_id, ok = _optional_str_field(table, "id")
    if not ok:
        _LOGGER.warning(
            "reasoningEfforts: skipping entry with non-string id: %r", table
        )
        return None
    if opt_id is None:
        opt_id = str(value)
    # label: Option<String> -- default to the humanized id.
    label, ok = _optional_str_field(table, "label")
    if not ok:
        _LOGGER.warning(
            "reasoningEfforts: skipping entry with non-string label: %r", table
        )
        return None
    if label is None:
        label = _humanize_effort_id(opt_id)
    # description: Option<String>.
    description, ok = _optional_str_field(table, "description")
    if not ok:
        _LOGGER.warning(
            "reasoningEfforts: skipping entry with non-string description: %r",
            table,
        )
        return None
    # default: bool (#[serde(default)] -> False when absent).
    raw_default = table.get("default", False)
    if not isinstance(raw_default, bool):
        _LOGGER.warning(
            "reasoningEfforts: skipping entry with non-bool default: %r", table
        )
        return None
    return ReasoningEffortOption(
        id=opt_id,
        value=value,
        label=label,
        description=description,
        default=raw_default,
    )


def parse_reasoning_effort_options(
    arr: Sequence[object],
) -> tuple[ReasoningEffortOption, ...]:
    """Mirror grok ``parse_reasoning_effort_options``: parse a JSON array of
    reasoning-effort options element-by-element, skipping (and warning on) any
    entry whose shape / ``value`` fails to parse.

    Forward-compat for tiers a newer server introduces. The single home for
    the skip-invalid rule, shared by the meta reader and the remote
    ``/models`` parser. ``Vec`` -> :class:`tuple` (project convention).
    """
    return tuple(
        option
        for element in arr
        if (option := _parse_option(element)) is not None
    )


def parse_reasoning_efforts_meta(
    meta: Mapping[str, Any] | None,
) -> tuple[ReasoningEffortOption, ...] | None:
    """Mirror grok ``parse_reasoning_efforts_meta``: read the per-model
    reasoning-effort menu from a model's ACP ``meta``.

    Returns ``None`` when the key is absent, is not an array, or yields no
    usable options after skip-invalid -- so "absent" and "present-but-unusable"
    collapse to the same fallback path in every consumer (grok
    ``(!options.is_empty()).then_some(options)``).
    """
    if meta is None or REASONING_EFFORTS_META_KEY not in meta:
        return None
    raw = meta[REASONING_EFFORTS_META_KEY]
    if not isinstance(raw, list):
        _LOGGER.warning(
            "meta.reasoningEfforts: expected array, ignoring: %r", raw
        )
        return None
    options = parse_reasoning_effort_options(raw)
    return options if options else None


def reasoning_efforts_meta_value(
    opts: Sequence[ReasoningEffortOption],
) -> list[dict[str, Any]]:
    """Mirror grok ``reasoning_efforts_meta_value``: serialize a menu of
    options to its wire array (grok ``serde_json::to_value(opts)`` with the
    ``unwrap_or_else(|_| Value::Array(Vec::new()))`` fallback). Python's dict
    construction cannot fail, so the fallback is structurally unreachable --
    the direct list comprehension is the faithful equivalent.
    """
    return [opt.to_payload() for opt in opts]
