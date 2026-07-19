"""Reasoning-effort type layer — fusion of grok's ``xai-grok-sampling-types`` (R53).

Pure data types for the reasoning-effort control axis (the companion to the
R45 model vocabulary — together they are the two configuration axes of an LLM
call). Mirrors grok ``xai-grok-sampling-types`` (types.rs:762-1008):

* :class:`ReasoningEffort` — six variants (``none`` / ``minimal`` / ``low`` /
  ``medium`` [default] / ``high`` / ``xhigh``), wire format lowercase (serde
  ``rename_all = "lowercase"``); ``max`` is a CLI/UX alias of ``xhigh`` accepted
  only by the strict/token parsers. On the Anthropic Messages API ``none`` /
  ``minimal`` are omitted (→ ``None``) and ``xhigh`` surfaces as ``"max"``.
* :func:`parse_effort_token` / :func:`parse_effort_strict` — canonical wire
  parse (``max`` → ``Xhigh``); the strict form raises on unknown variants.
* The model-meta readers (:func:`supports_reasoning_effort_meta`,
  :func:`parse_reasoning_effort_meta`, :func:`parse_reasoning_effort_options`,
  :func:`parse_reasoning_efforts_meta`) collapse absent / wrong-type /
  unknown-variant into a single fallback path (``None``) and skip invalid
  array entries with a warn — forward-compat for tiers a newer server
  introduces, so a persisted user pref is never overwritten by a transient
  mismatch.
* :class:`ReasoningEffortOption` — a selectable menu entry that accepts either
  a bare canonical value string (``"xhigh"``) or a full table (``value``
  required, the rest optional); bare form derives ``id`` / ``label``.

No I/O (no HTTP, no file system) — depended on by downstream request builders.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

logger = logging.getLogger(__name__)

# --- meta keys (grok pub const) ---------------------------------------------

REASONING_EFFORT_META_KEY = "reasoningEffort"
SUPPORTS_REASONING_EFFORT_META_KEY = "supportsReasoningEffort"
REASONING_EFFORTS_META_KEY = "reasoningEfforts"

# CLI/UX alias: ``max`` is accepted on input as ``xhigh`` (grok FromStr rule).
_MAX_ALIAS = "max"

_VALID_EFFORTS = "none, minimal, low, medium, high, xhigh, max"


class ReasoningEffort(StrEnum):
    """Reasoning effort level (grok ``ReasoningEffort``).

    Wire format is lowercase (serde ``rename_all = "lowercase"``); ``Medium``
    is the default. ``None`` / ``Minimal`` are omitted on the Anthropic Messages
    API (``to_messages_api`` returns ``None``); ``Xhigh`` surfaces as ``"max"``
    there. As a :class:`StrEnum` the member value *is* the wire string and
    ``str(member)`` returns it (grok ``Display → as_str``), so pydantic
    serialisation (``model_dump(mode="json")``) yields the lowercase token —
    parity with grok's serde ``lowercase``. Object-field coercion only accepts
    canonical tokens (``"max"`` is rejected), matching grok's serde rename; the
    ``max`` alias is honoured solely by the bare-string / token parsers.
    """

    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"

    @classmethod
    def default(cls) -> ReasoningEffort:
        """The default variant (grok ``#[default]`` on ``Medium``)."""
        return cls.MEDIUM

    def as_str(self) -> str:
        """Canonical wire string (grok ``as_str``).

        Equivalent to ``str(self)`` (StrEnum value semantics) but named to
        mirror grok's accessor for symmetry with the meta readers.
        """
        return self.value

    def to_messages_api(self) -> str | None:
        """Anthropic Messages API ``output_config.effort`` token; ``None`` if unsupported.

        The Anthropic counterpart of :meth:`to_openai_effort_token` (the OpenAI
        emit seam, R56). The two differ because the wire contracts differ — the
        Anthropic ``OutputConfigParam.effort`` Literal is
        ``low`` / ``medium`` / ``high`` / ``xhigh`` / ``max`` (SDK-confirmed),
        so it accepts ``xhigh`` and ``max`` directly and has no ``minimal`` /
        ``none`` tier:

        * ``None`` variant → ``None`` (not injected) — same as
          :meth:`to_openai_effort_token`; the field has no ``none`` tier, so the
          endpoint falls back to its default.
        * ``Minimal`` → ``None`` — *dropped*, unlike
          :meth:`to_openai_effort_token` (where ``Minimal`` → ``"minimal"``):
          Anthropic's effort Literal has no ``minimal`` value, so it cannot be
          emitted and is omitted rather than erroring.
        * ``Xhigh`` → ``"max"`` — *kept at the max tier*, unlike
          :meth:`to_openai_effort_token` (where ``Xhigh`` → ``"high"``):
          Anthropic accepts both ``xhigh`` and ``max``; grok's ``to_messages_api``
          picks ``"max"`` as the XHIGH wire token (the highest tier).
        * ``Low`` / ``Medium`` / ``High`` → their own value (pass-through).
        """
        if self is ReasoningEffort.NONE or self is ReasoningEffort.MINIMAL:
            return None
        if self is ReasoningEffort.XHIGH:
            return "max"
        return self.value

    def to_openai_effort_token(self) -> str | None:
        """OpenAI chat-completions ``reasoning_effort`` token; ``None`` if unsupported.

        The OpenAI counterpart of :meth:`to_messages_api` (the Anthropic Messages
        API seam). The two differ because the wire contracts differ — the
        official OpenAI ``reasoning_effort`` request field accepts only
        ``minimal`` / ``low`` / ``medium`` / ``high`` (it rejects ``none`` /
        ``xhigh`` / ``max``, and many OpenAI-compatible endpoints reject the
        field outright):

        * ``None`` variant → ``None`` (not injected) — same as
          :meth:`to_messages_api`; the field has no ``none`` tier, so the
          endpoint falls back to its default.
        * ``Minimal`` → ``"minimal"`` — *kept*, unlike :meth:`to_messages_api`
          (where ``Minimal`` → ``None``): the OpenAI field accepts ``minimal``
          as its lowest tier, so it is emitted rather than dropped.
        * ``Xhigh`` → ``"high"`` — *degraded*, unlike :meth:`to_messages_api`
          (where ``Xhigh`` → ``"max"``): OpenAI's highest tier is ``high``, so
          xhigh maps to the closest supported token (best-effort semantic
          fidelity — deeper thinking was requested, so give the deepest
          available rather than dropping the signal).
        * ``Low`` / ``Medium`` / ``High`` → their own value (pass-through).
        """
        if self is ReasoningEffort.NONE:
            return None
        if self is ReasoningEffort.XHIGH:
            return "high"
        return self.value


def parse_effort_token(token: str) -> ReasoningEffort | None:
    """Canonical wire parse only (``max`` → ``Xhigh``); ``None`` on unknown.

    Mirrors grok ``parse_canonical_effort_token`` (``token.parse().ok()``).
    Remapped menu ids still need a model catalog — this is the wire parser.
    Case-insensitive.
    """
    lowered = token.lower()
    if lowered == _MAX_ALIAS:
        return ReasoningEffort.XHIGH
    try:
        return ReasoningEffort(lowered)
    except ValueError:
        return None


def parse_effort_strict(s: str) -> ReasoningEffort:
    """Strict parse (``max`` → ``Xhigh``); raises ``ValueError`` on invalid.

    Mirrors grok ``FromStr`` (``Err = String`` → Python ``ValueError``).
    Case-insensitive; ``max`` is accepted as a CLI/UX alias of ``xhigh``.
    """
    lowered = s.lower()
    if lowered == _MAX_ALIAS:
        return ReasoningEffort.XHIGH
    try:
        return ReasoningEffort(lowered)
    except ValueError as exc:
        raise ValueError(
            f"invalid reasoning effort: {s!r} (expected one of: {_VALID_EFFORTS})"
        ) from exc


def coerce_effort(value: ReasoningEffort | str | None) -> ReasoningEffort | None:
    """Normalise a runtime effort value to ``ReasoningEffort | None`` (R54 wiring).

    The transport ``stream_chat`` call sites receive the reasoning effort from
    three sources — an already-typed :class:`ReasoningEffort`, a bare wire-token
    string (e.g. ``"max"`` from a CLI/UI alias), or ``None`` (the default, "do
    not send"). This collapses all three into the canonical enum-or-None the
    wire layer consumes:

    * ``None`` → ``None`` (no effort to send — the default).
    * :class:`ReasoningEffort` → itself (already canonical).
    * ``str`` → :func:`parse_effort_token` (case-insensitive, honours the
      ``max`` alias of ``xhigh``; an unknown token → ``None`` with no raise, so
      a caller passing a typo degrades to "send nothing" rather than crashing
      the turn).

    Mirrors grok's permissive *input* surface (the CLI accepts both the typed
    enum and the ``max`` alias string) while keeping the wire layer's strict
    enum boundary intact — this is the parse seam; :meth:`ReasoningEffort.
    to_messages_api` is the emit seam (R54 pipe-through normalises here; a later
    round calls the emit seam once the MiniMax/xAI effort wire contract settles).
    """
    if value is None:
        return None
    if isinstance(value, ReasoningEffort):
        return value
    return parse_effort_token(value)


def _humanize_effort_id(effort_id: str) -> str:
    """Uppercase the first character of an id for a default label.

    ``"xhigh"`` → ``"Xhigh"``, ``"deep"`` → ``"Deep"`` (grok
    ``humanize_effort_id``). Returns ``""`` for an empty id.
    """
    if not effort_id:
        return ""
    return effort_id[0].upper() + effort_id[1:]


def supports_reasoning_effort_meta(meta: Mapping[str, Any] | None) -> bool:
    """Read ``supportsReasoningEffort`` from model meta; ``False`` if absent/non-bool.

    Mirrors grok ``supports_reasoning_effort_meta``
    (``.as_bool().unwrap_or(false)``).
    """
    if meta is None:
        return False
    value = meta.get(SUPPORTS_REASONING_EFFORT_META_KEY)
    return isinstance(value, bool) and value


def parse_reasoning_effort_meta(
    meta: Mapping[str, Any] | None,
) -> ReasoningEffort | None:
    """Read ``reasoningEffort`` from model meta; ``None`` on type-mismatch/unknown.

    Returns ``None`` (with a warn log) on a non-string value or an unknown
    variant, so a persisted user pref is never overwritten by a transient
    mismatch (grok ``parse_reasoning_effort_meta``).
    """
    if meta is None:
        return None
    raw = meta.get(REASONING_EFFORT_META_KEY)
    if raw is None:
        return None
    if not isinstance(raw, str):
        logger.warning(
            "meta.reasoningEffort: expected string, ignoring (got %r)", raw
        )
        return None
    effort = parse_effort_token(raw)
    if effort is None:
        logger.warning(
            "meta.reasoningEffort: parse failed, ignoring (value=%r)", raw
        )
    return effort


def reasoning_effort_meta_value(effort: ReasoningEffort) -> str:
    """Serialise an effort to its meta wire value (grok ``reasoning_effort_meta_value``).

    grok emits ``serde_json::Value::String``; the Python parity is the canonical
    wire string.
    """
    return effort.as_str()


class ReasoningEffortOption(BaseModel):
    """A single selectable reasoning-effort option for a model (grok).

    ``id`` / ``label`` are presentation and input; ``value`` is the canonical
    value sent on the wire. Accepts either a bare canonical value string
    (``"xhigh"`` — derives ``id`` / ``label``) or a full table (``value``
    required, the rest optional). Mirrors grok's ``Serialize`` derive + custom
    ``Deserialize`` (untagged ``Bare`` / ``Full``): the bare form parses via
    ``FromStr`` (so accepts ``max``), while an object's ``value`` is coerced by
    pydantic to the enum (so rejects ``max`` — parity with grok's serde rename).
    """

    model_config = ConfigDict(extra="ignore")

    value: ReasoningEffort
    id: str = ""
    label: str = ""
    description: str | None = None
    default: bool = False

    @model_validator(mode="before")
    @classmethod
    def _accept_bare(cls, data: Any) -> Any:
        """Bare value string → full option with derived ``id`` / ``label``.

        The bare path uses :func:`parse_effort_strict` (honours the ``max``
        alias), matching grok's ``RawReasoningEffortOption::Bare`` branch which
        parses via ``FromStr``.
        """
        if isinstance(data, str):
            value = parse_effort_strict(data)
            effort_id = value.as_str()
            return {
                "value": value,
                "id": effort_id,
                "label": _humanize_effort_id(effort_id),
                "description": None,
                "default": False,
            }
        return data

    @model_validator(mode="after")
    def _fill_defaults(self) -> ReasoningEffortOption:
        """Backfill ``id`` / ``label`` defaults from ``value`` (grok ``unwrap_or``).

        ``id`` defaults to ``value.as_str()``; ``label`` defaults to
        ``humanize_effort_id(id)``.
        """
        if not self.id:
            self.id = self.value.as_str()
        if not self.label:
            self.label = _humanize_effort_id(self.id)
        return self


def parse_reasoning_effort_options(
    arr: list[Any],
) -> list[ReasoningEffortOption]:
    """Parse a JSON array of options element-by-element, skipping invalid entries.

    Forward-compat for tiers a newer server introduces: invalid entries are
    skipped with a warn (grok ``parse_reasoning_effort_options``).
    """
    options: list[ReasoningEffortOption] = []
    for el in arr:
        try:
            options.append(ReasoningEffortOption.model_validate(el))
        except Exception as exc:  # noqa: BLE001 — mirror grok's broad skip
            logger.warning("reasoningEfforts: skipping invalid entry (%s)", exc)
    return options


def parse_reasoning_efforts_meta(
    meta: Mapping[str, Any] | None,
) -> list[ReasoningEffortOption] | None:
    """Read the per-model ``reasoningEfforts`` menu from meta; ``None`` when unusable.

    Returns ``None`` when the key is absent, is not an array, or yields no
    usable options after skip-invalid — so "absent" and "present-but-unusable"
    collapse to the same fallback path (grok ``parse_reasoning_efforts_meta``).
    """
    if meta is None:
        return None
    raw = meta.get(REASONING_EFFORTS_META_KEY)
    if raw is None:
        return None
    if not isinstance(raw, list):
        logger.warning(
            "meta.reasoningEfforts: expected array, ignoring (got %r)", raw
        )
        return None
    options = parse_reasoning_effort_options(raw)
    return options or None


def reasoning_efforts_meta_value(
    opts: list[ReasoningEffortOption],
) -> list[dict[str, Any]]:
    """Serialise a list of options to its meta wire value (grok
    ``reasoning_efforts_meta_value``).

    grok emits ``serde_json::to_value(opts)``; the Python parity is a list of
    JSON-native dicts (``value`` is the lowercase wire token).
    """
    return [opt.model_dump(mode="json") for opt in opts]


def enrich_model_reasoning_meta(model: Mapping[str, Any]) -> dict[str, Any]:
    """Attach normalised reasoning-effort fields to a model dict (R58).

    The first consumer of the R53 meta readers (:func:`supports_reasoning_
    effort_meta` / :func:`parse_reasoning_effort_meta` /
    :func:`parse_reasoning_efforts_meta`): reads a model catalog entry's raw
    ``reasoningEffort`` / ``reasoningEfforts`` / ``supportsReasoningEffort``
    meta (grok's per-model reasoning vocabulary, ``xai-grok-sampling-types``
    consumed at the catalog seam) and attaches normalised, frontend-ready
    fields so the ``model.list`` IPC response can drive an effort selector
    without every caller re-parsing the meta.

    The model dict itself is the meta container (flat fields, not a nested
    ``meta`` sub-object) — matching how :meth:`ProviderDAO.list_models`
    flattens each provider's stored model JSON. Attachments are added **only
    when the corresponding meta is present and parseable**, so a model that
    declares no reasoning-effort meta is returned with no new keys (zero
    regression — byte-identical keys to the pre-R58 dict, just a shallow
    copy).

    Attachments (all snake_case, matching the existing ``model.list``
    response fields like ``provider_id`` / ``protocol``):

    * ``supports_reasoning_effort``: ``True`` — only when
      :func:`supports_reasoning_effort_meta` reads a truthy
      ``supportsReasoningEffort``.
    * ``reasoning_effort_default``: the canonical wire token
      (:func:`reasoning_effort_meta_value`, e.g. ``"medium"``) — only when
      :func:`parse_reasoning_effort_meta` resolves a known tier.
    * ``reasoning_effort_options``: the selectable menu
      (:func:`reasoning_efforts_meta_value`, a list of ``{id, label, value,
      …}`` dicts) — only when :func:`parse_reasoning_efforts_meta` yields a
      non-empty list.

    Returns a *copy* of ``model`` with these fields merged in (never mutates
    the input). Mirrors grok's catalog-read flow: the raw meta stays in the
    model dict (source of truth); the normalised fields are a derived view
    for presentation. An empty / unparseable meta adds nothing — the
    caller's existing keys pass through untouched.
    """
    enriched = dict(model)
    if supports_reasoning_effort_meta(model):
        enriched["supports_reasoning_effort"] = True
    default_effort = parse_reasoning_effort_meta(model)
    if default_effort is not None:
        enriched["reasoning_effort_default"] = reasoning_effort_meta_value(
            default_effort
        )
    options = parse_reasoning_efforts_meta(model)
    if options is not None:
        enriched["reasoning_effort_options"] = reasoning_efforts_meta_value(options)
    return enriched
