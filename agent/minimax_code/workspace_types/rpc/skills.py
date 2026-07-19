"""Skill / plugin discovery RPCs (R73).

Fusion of grok's ``xai-grok-workspace-types::rpc::skills`` — the two
``workspace.discover_*`` methods plus the :class:`SkillScope` enum and the
:class:`SkillInfo` discovery payload. This file lands three serde patterns
new to the RPC layer:

* **``default = "default_true"``** — :class:`SkillInfo`'s ``user_invocable``
  and ``enabled`` bool fields default to ``True`` (grok's
  ``#[serde(default = "default_true")]``), distinct from R70/R72's
  ``#[serde(default)]`` bool fields that default to ``False``. In pydantic
  this is just ``field: bool = True``; the field is always emitted (no
  ``skip_serializing_if``), whether ``True`` or ``False``.
* **bulk ``Option::is_none`` elision via a wrap ``model_serializer``** —
  :class:`SkillInfo` carries 18 ``Option`` fields, each with
  ``#[serde(default, skip_serializing_if = "Option::is_none")]``. Rather than
  hand-build a 26-key dict (R70's plain ``model_serializer`` for 2 fields) or
  strip a single key (R72's wrap mixin), this file's wrap serializer takes
  the default dump from ``handler(self)`` and drops every key whose value is
  ``None`` in one comprehension — the third Option-elision implementation in
  the layer.
* **``Response = Vec<Value>`` / ``Vec<SkillInfo>``** — :class:`DiscoverPluginsReq`
  carries an arbitrary-JSON list response surfaced as ``list[Any]``, and
  :class:`DiscoverSkillsReq` carries a typed ``list[SkillInfo]``. Both are
  *bare-list* responses (the envelope wraps ``{"ok": [...]}``), distinct from
  R72's wrapped-struct responses (``ListBackgroundTasksResponse`` =
  ``{"tasks": [...]}``) and R72's scalar ``Response = Any``.

:class:`SkillScope` is the second forward-tolerant ``str``-subclass enum in
the layer (R71's :class:`~minimax_code.workspace_types.rpc.HookEventNameWire`
was the first): 6 known variants (``Local`` → ``"local"`` … ``Plugin`` →
``"plugin"``) plus ``Unknown(String)`` so a newer server's scope round-trips
losslessly. R71's enum doubled as a JSON map key; this one is only ever a
field value, but the ``str``-subclass shape is reused verbatim.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import model_serializer
from pydantic_core import core_schema

from minimax_code.workspace_types._wire import WireModel

__all__ = [
    "SkillScope",
    "SkillInfo",
    "DiscoverSkillsReq",
    "DiscoverPluginsReq",
]


class SkillScope(str):
    """Skill discovery scope; wire is a snake_case string; forward-tolerant.

    Mirrors grok's ``SkillScope`` enum with hand-written serde: 6 known
    variants (``Local`` → ``"local"`` … ``Plugin`` → ``"plugin"``) plus
    ``Unknown(String)`` — an unrecognized scope from a newer server is
    preserved verbatim so round-tripping never rewrites a novel value.
    Implemented as a ``str`` subclass (same shape as R71's
    :class:`~minimax_code.workspace_types.rpc.HookEventNameWire`) via
    ``__get_pydantic_core_schema__``: validates as ``str`` then coerces into
    the subclass, and serialises back to a plain string. R71's enum was a
    JSON map key; this one is only a field value, but the shape is identical.

    The 6 known variants are attached as class attributes after the class
    body (e.g. ``SkillScope.LOCAL``).
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):  # noqa: ANN001, ANN206
        # Validate as str, then coerce into the subclass — so a plain server
        # string becomes a SkillScope (the Unknown case is just any other
        # string). Serialization falls through to str_schema → string.
        return core_schema.no_info_after_validator_function(cls, core_schema.str_schema())

    def as_str(self) -> str:
        """The snake_case wire string (the raw captured value for ``Unknown``)."""
        # str.__str__ avoids any subclass override; the value is the wire string.
        return str.__str__(self)


# The 6 known skill scopes (snake_case wire strings), matching grok's
# exhaustive `as_str` match. Attached as class attrs after the body (the class
# isn't closed during the body, so the instances can't be named inside it). An
# unrecognized server string stays a legal SkillScope instance — no catch-all
# constant is needed (the Unknown case is just any other string).
_KNOWN_SKILL_SCOPES: dict[str, str] = {
    "LOCAL": "local",
    "REPO": "repo",
    "USER": "user",
    "SERVER": "server",
    "BUNDLED": "bundled",
    "PLUGIN": "plugin",
}
for _attr, _wire in _KNOWN_SKILL_SCOPES.items():
    setattr(SkillScope, _attr, SkillScope(_wire))
del _attr, _wire


class SkillInfo(WireModel):
    """A discovered skill as serialized by ``workspace.discover_skills``.

    SYNC: mirrors the serde shape of ``xai-grok-tools``'s ``SkillInfo`` (the
    type the server serializes). 26 fields: 4 required (``name``,
    ``description``, ``path``, ``scope``), 18 ``Option`` (each omitted from
    the wire when ``None`` via ``#[serde(default, skip_serializing_if =
    "Option::is_none")]``), and 4 bool. Two bool fields — ``user_invocable``
    and ``enabled`` — default to ``True`` (grok's ``default_true``); the
    other two default to ``False``. Does **not** derive ``Default`` in the
    source (4 required fields).

    The wrap ``model_serializer`` drops every ``None``-valued key from the
    default dump — reproducing ``skip_serializing_if = "Option::is_none"``
    across all 18 Option fields at once. ``config_source`` is raw JSON (the
    tools crate's ``ConfigSource`` tagged enum, which RPC clients do not
    interpret structurally) so it stays ``Any``.
    """

    name: str
    display_name: str | None = None
    description: str
    has_user_specified_description: bool = False
    paths: list[str] | None = None
    when_to_use: str | None = None
    short_description: str | None = None
    author: str | None = None
    argument_hint: str | None = None
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] | None = None
    path: str
    scope: SkillScope
    config_source: Any | None = None
    plugin_name: str | None = None
    plugin_version: str | None = None
    plugin_root: str | None = None
    plugin_data: str | None = None
    allowed_tools: list[str] | None = None
    model: str | None = None
    effort: str | None = None
    user_invocable: bool = True
    disable_model_invocation: bool = False
    enabled: bool = True
    body: str | None = None

    @model_serializer(mode="wrap")
    def _omit_none_optionals(self, handler):  # noqa: ANN001, ANN202
        # handler(self) returns the default JSON-safe dump (all 26 fields);
        # drop every None-valued key to reproduce #[serde(default,
        # skip_serializing_if = "Option::is_none")] across all 18 Option
        # fields at once. Bool fields (True/False) and required fields are
        # never None, so they always survive. sort_mappings re-sorts keys.
        raw = handler(self)
        return {k: v for k, v in raw.items() if v is not None}


class DiscoverSkillsReq(WireModel):
    """``workspace.discover_skills`` — list discovered skills.

    No parameters (empty struct). ``Response = Vec<SkillInfo>`` — a bare
    typed list (the envelope wraps ``{"ok": [<SkillInfo>, …]}``). Derives
    ``Default`` → empty struct.
    """

    METHOD: ClassVar[str] = "workspace.discover_skills"
    Response: ClassVar[type] = list[SkillInfo]


class DiscoverPluginsReq(WireModel):
    """``workspace.discover_plugins`` — list plugins at the workspace root.

    No parameters (empty struct). Each element is the raw serialized plugin
    object, so ``Response = Vec<Value>`` → ``list[Any]`` — a bare
    arbitrary-JSON list (distinct from R72's scalar ``Response = Any`` and
    R72's wrapped-struct list responses). Derives ``Default`` → empty struct.
    """

    METHOD: ClassVar[str] = "workspace.discover_plugins"
    Response: ClassVar[type] = list[Any]
