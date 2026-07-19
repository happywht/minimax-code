"""Hook-registry RPC (R71).

Fusion of grok's ``xai-grok-workspace-types::rpc::hooks`` — the
``workspace.hook_registry`` method. This file lands four serde patterns new to
the RPC layer:

* **``#[serde(skip)]`` field elision** — the upstream ``matcher`` field (a
  compiled regex, never on the wire) is omitted entirely from
  :class:`HookSpecWire`; the Python model simply does not declare the field
  (not ``skip_serializing_if`` — it is *never* serialized or deserialized).
* **forward-tolerant enum** — :class:`HookEventNameWire` mirrors grok's
  hand-written ``Serialize``/``Deserialize``: 15 known variants plus an
  ``Unknown(String)`` catch-all so a newer server's event decodes losslessly
  (deploy-skew tolerant; the ``hook_registry`` decode never fails, and distinct
  unknown events stay distinct map keys).
* **enum as JSON map key** — :class:`HookRegistryWire` carries a
  ``HashMap<HookEventNameWire, Vec<HookSpecWire>>``. Because the enum serializes
  to a plain string, it serves natively as a JSON object key.
* **empty-parameter request** — :class:`HookRegistryReq` has no fields.

:class:`HookEventNameWire` is a ``str`` subclass (not an ``Enum``) because the
``Unknown`` variant carries a dynamic string — an open vocabulary that
``Enum`` cannot express — and ``str`` lets it act as a map key without a custom
serializer, while any server string is a legal instance (the ``Unknown`` case).
R69's :class:`DeployError` (a *closed* vocabulary) used a plain ``Enum``; this
file's open-vocabulary counterpart needs the ``str``-subclass shape.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field
from pydantic_core import core_schema

from minimax_code.workspace_types._wire import WireModel

__all__ = [
    "HookEventNameWire",
    "HookSpecWire",
    "HookRegistryWire",
    "HookRegistryReq",
]


class HookEventNameWire(str):
    """Hook-event name; wire is a snake_case string; forward-tolerant.

    Mirrors grok's ``HookEventNameWire`` enum with hand-written serde: 15 known
    variants (``SessionStart`` → ``"session_start"`` … ``PostCompact`` →
    ``"post_compact"``) plus ``Unknown(String)`` — an unrecognized event from a
    newer server is preserved verbatim so decode never fails and distinct
    unknowns stay distinct map keys. Implemented as a ``str`` subclass so it
    works natively as a JSON map key and any server string is a legal instance
    (the ``Unknown`` case). The 15 known variants are attached as class
    attributes after the class body (e.g. ``HookEventNameWire.PRE_TOOL_USE``).

    Pydantic treats it as ``str`` (validate as str, then coerce to the
    subclass) via ``__get_pydantic_core_schema__`` — this is what lets it ride
    a :class:`HookSpecWire` field and a :class:`HookRegistryWire` map key
    without ``arbitrary_types_allowed``, serialising to a plain string on the
    wire.
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):  # noqa: ANN001, ANN206
        # Validate as str, then coerce into the subclass — so a plain server
        # string becomes a HookEventNameWire (the Unknown case is just any
        # other string). Serialization falls through to str_schema → string.
        return core_schema.no_info_after_validator_function(cls, core_schema.str_schema())

    def as_str(self) -> str:
        """The snake_case wire string (the raw captured value for ``Unknown``)."""
        # str.__str__ avoids any subclass override; the value is the wire string.
        return str.__str__(self)


# The 15 known hook events (snake_case wire strings), matching grok's exhaustive
# `as_str` match. Attached as class attrs after the body (the class isn't closed
# during the body, so the instances can't be named inside it). An unrecognized
# server string stays a legal HookEventNameWire instance — no catch-all constant
# is needed (the Unknown case is just any other string).
_KNOWN_HOOK_EVENTS: dict[str, str] = {
    "SESSION_START": "session_start",
    "SESSION_END": "session_end",
    "STOP": "stop",
    "STOP_FAILURE": "stop_failure",
    "PRE_TOOL_USE": "pre_tool_use",
    "POST_TOOL_USE": "post_tool_use",
    "POST_TOOL_USE_FAILURE": "post_tool_use_failure",
    "PERMISSION_DENIED": "permission_denied",
    "USER_PROMPT_SUBMIT": "user_prompt_submit",
    "NOTIFICATION": "notification",
    "SUBAGENT_START": "subagent_start",
    "SUBAGENT_STOP": "subagent_stop",
    "SUBAGENT_END": "subagent_end",
    "PRE_COMPACT": "pre_compact",
    "POST_COMPACT": "post_compact",
}
for _attr, _wire in _KNOWN_HOOK_EVENTS.items():
    setattr(HookEventNameWire, _attr, HookEventNameWire(_wire))
del _attr, _wire


class HookSpecWire(WireModel):
    """Wire mirror of one hook spec. snake_case on wire (no ``rename_all``).

    The upstream ``matcher`` field is ``#[serde(skip)]`` (a compiled regex,
    never on the wire) and is therefore **not declared here** — clients
    recompile it from ``configured_matcher``. Every other field keeps its
    snake_case name. ``command`` / ``source_dir`` are ``PathBuf`` on the wire as
    OS strings, so plain ``str`` carries them. ``extra_env`` is a required
    ``HashMap`` (no ``#[serde(default)]`` in the source).
    """

    name: str
    event: HookEventNameWire
    handler_type: str
    configured_matcher: str | None = None
    enabled: bool
    command: str | None = None
    command_raw: str | None = None
    url: str | None = None
    url_raw: str | None = None
    timeout_ms: int
    source_dir: str
    extra_env: dict[str, str]


class HookRegistryWire(WireModel):
    """Response for ``workspace.hook_registry`` — the hooks map.

    Wire shape ``{"hooks": {"<event>": [<HookSpecWire>, …]}}``. The map key is
    :class:`HookEventNameWire` (a ``str`` subclass) so it serializes natively as
    a JSON object key. Derives ``Default`` in the source → empty map.
    """

    hooks: dict[HookEventNameWire, list[HookSpecWire]] = Field(default_factory=dict)


class HookRegistryReq(WireModel):
    """``workspace.hook_registry`` — request the loaded hook registry.

    No parameters (empty struct). ``Response = HookRegistryWire``. Derives
    ``Default`` in the source, so :meth:`default` succeeds.
    """

    METHOD: ClassVar[str] = "workspace.hook_registry"
    Response: ClassVar[type] = HookRegistryWire
