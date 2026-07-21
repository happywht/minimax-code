"""Conversation-layer tool call + tool spec (R222, ``xai-grok-sampling-types``
``conversation.rs``).

R222 lands :class:`ToolCall` + :class:`ToolSpec` (``conversation.rs`` ~453 and
~464) -- the conversation-layer tool-definition-and-call pair from the "Tool
Definitions and Calls" block. A :class:`ToolCall` is an assistant-emitted tool
invocation the client must execute locally (``id`` + ``name`` + JSON-encoded
``arguments``); a :class:`ToolSpec` is a tool/function definition the model is
offered (``name`` + optional ``description`` + JSON-Schema ``parameters``).

Both are plain ``#[derive(Serialize, Deserialize)]`` structs with NO
``#[serde(tag)]`` discriminator -- flat wire objects. This is the first
**plain struct** shape to land in the ``conversation.rs`` migration: the prior
five slices were all enums or free functions (R217
:class:`DanglingToolCallReason` in-program enum + 2 free functions, R218
:class:`SyntheticReason` / :class:`PriorTurnInterrupt` catch-all wire enums,
R219 :class:`ConversationStopReason` strict enum + :class:`TokenUsage`, R220
:class:`ConversationToolChoice` externally-tagged mixed enum, R221
:class:`ContentPart` internally-tagged struct-variant union). The plain flat
struct itself is serde's most common shape -- it already landed in the
ChatCompletion family (R206 :class:`ImageUrl` / :class:`ToolCallFunction` /
:class:`PromptTokensDetails`) -- so R222 is not a new serde *shape* at the
package level; the milestone is that it is the first ``conversation.rs``
struct, and the first leaf here to carry the **strict-required** parse
discipline.

Strict-required vs tolerant: the R206 flat structs use a *tolerant*
:meth:`from_payload` (a missing field -> the default) because they parse
ChatCompletion **responses** where the platform never fails a malformed reply.
:class:`ToolCall` / :class:`ToolSpec` are **conversation persistence**
structs -- they cross the wire in both directions (Serialize + Deserialize)
and live in JSONL session files. Their grok fields carry NO
``#[serde(default)]``, so a missing required field is a serde **failure** (a
corrupt session record). :meth:`from_payload` mirrors that: a missing or
wrong-typed required field raises ``ValueError``; the optional ``description``
on :class:`ToolSpec` (``#[serde(default)]``) -> ``None`` when absent; extra
keys are tolerated (serde's default ignores unknown struct fields). This is
the same strict-no-catch-all philosophy as the R221 :class:`ContentPart` /
R220 :class:`ConversationToolChoice` enums, applied to a flat struct.

Dependency closure: zero external. Each field is ``Arc<str>`` / ``String`` /
``serde_json::Value`` (Python ``str`` / ``str`` / ``Any``); no sampler types
referenced. The grok ``impl From<ToolDefinition> for ToolSpec`` (~500) is
YAGNI -- ``ToolDefinition`` is a ``crate::rs`` re-export (the
``xai-grok-tools`` ``ToolDefinition`` / ``FunctionTool`` family) that is not
yet migrated. :class:`ToolCall` unblocks the :class:`AssistantItem` consumer
layer (its ``tool_calls: Vec<ToolCall>`` field) for a later slice -- the
strategic reason this leaf pair lands before the other zero-dependency
``conversation.rs`` struct leaves (``SystemItem`` / ``ToolResultItem`` sit on
different axes).

No barrel collision: ``ToolCall`` / ``ToolSpec`` are free at the package
surface. The wire-layer exposes ``ToolCallFunction`` / ``ToolCallRequest`` /
``ToolCallResponse`` / ``ToolCallDelta`` / ``ToolCallFunctionDelta`` (R206-R208
ChatCompletion family) -- all distinct names, no bare ``ToolCall`` or
``ToolSpec`` -- so, like the R221 :class:`ContentPart`, no ``Conversation``
prefix is needed; the bare names mirror grok's own module-local ``ToolCall``
/ ``ToolSpec``.

This module is no-I/O (pure value-level). Migration map (grok -> Python):

- ``struct ToolCall { id: Arc<str>, name: String, arguments: Arc<str> }`` ->
  :class:`ToolCall` (``id: str``, ``name: str``, ``arguments: str``).
- ``struct ToolSpec { name: String, description: Option<String>, parameters:
  serde_json::Value }`` -> :class:`ToolSpec` (``name: str``, ``parameters:
  Any``, ``description: str | None = None``). The grok field order is
  ``name`` / ``description`` / ``parameters``; Python dataclass rules force the
  defaulted ``description`` last, so the Python field order is ``name`` /
  ``parameters`` / ``description`` (keyword construction is unaffected).
  ``#[serde(default, skip_serializing_if = "Option::is_none")]`` on
  ``description`` -> ``None`` default + omitted from :meth:`as_payload` when
  ``None``.
- ``#[derive(Serialize, Deserialize)]`` (both, no ``#[serde(tag)]``) ->
  hand-written :meth:`from_payload` / :meth:`as_payload` pair. Required fields
  (no ``#[serde(default)]``) -> strict: missing key or wrong-typed value ->
  ``ValueError`` (mirrors serde's failure on a default-less required field);
  the optional ``description`` -> ``None`` when absent/null; extra keys
  tolerated.
- ``impl From<ToolDefinition> for ToolSpec`` -> YAGNI (``ToolDefinition`` is a
  ``crate::rs`` re-export not yet migrated).

YAGNI: ``From<ToolDefinition> for ToolSpec`` -- the ``ToolDefinition`` source
type (``crate::rs`` re-export of the ``xai-grok-tools`` family) is not yet
migrated; the conversion lands with that family.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool call the client must execute locally (conversation-layer).

    Emitted by the assistant as part of an :class:`AssistantItem`, consumed by
    the client to dispatch a local tool. ``id`` uniquely identifies this call
    (matches a later :class:`ToolResultItem`); ``name`` is the function to
    call; ``arguments`` is a JSON-encoded argument string (the model streams a
    partial JSON fragment during streaming, a complete JSON object when
    non-streaming). All three fields are required (no ``#[serde(default)]``) --
    a missing key or non-string value on the wire raises ``ValueError``
    (mirrors serde's failure on a default-less required struct field). Distinct
    from the wire-layer :class:`ToolCallFunction` (R206, the ChatCompletion
    ``function`` payload -- ``name`` + ``arguments`` only, no ``id``, tolerant
    parse); this is the conversation-persistence peer carrying the binding
    ``id``."""

    id: str
    name: str
    arguments: str

    @classmethod
    def from_payload(cls, raw: Any) -> ToolCall:
        """Strict wire parser (mirrors serde Deserialize on a default-less struct).

        A dict carrying string ``id`` / ``name`` / ``arguments`` -> the struct;
        anything else (non-dict, missing key, non-string value) ->
        ``ValueError``. Extra keys are tolerated (serde's default ignores
        unknown struct fields)."""
        if not isinstance(raw, dict):
            raise ValueError(f"tool call must be a dict, got {type(raw).__name__}")
        call_id = raw.get("id")
        if not isinstance(call_id, str):
            raise ValueError(
                "tool call 'id' must be a string, " f"got {type(call_id).__name__}"
            )
        name = raw.get("name")
        if not isinstance(name, str):
            raise ValueError(
                "tool call 'name' must be a string, " f"got {type(name).__name__}"
            )
        arguments = raw.get("arguments")
        if not isinstance(arguments, str):
            raise ValueError(
                "tool call 'arguments' must be a string, "
                f"got {type(arguments).__name__}"
            )
        return cls(id=call_id, name=name, arguments=arguments)

    def as_payload(self) -> dict[str, Any]:
        """Wire serializer (mirrors serde Serialize).

        -> ``{"id": <id>, "name": <name>, "arguments": <arguments>}``."""
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool/function definition offered to the model (conversation-layer).

    ``name`` is the tool name; ``description`` is an optional human-readable
    summary (``None`` when absent -- ``#[serde(default)]`` + omitted on
    serialize via ``skip_serializing_if = "Option::is_none"``); ``parameters``
    is the JSON-Schema for the arguments (any JSON value -- grok
    ``serde_json::Value`` -- in practice a dict). ``name`` + ``parameters`` are
    required (no ``#[serde(default)]``); ``description`` is optional. New at
    the package surface (no wire-layer peer)."""

    name: str
    parameters: Any
    description: str | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> ToolSpec:
        """Strict-required + tolerant-optional wire parser.

        ``name`` (string) + ``parameters`` (any JSON value, key must be present)
        are required; ``description`` defaults to ``None`` when absent or null
        and must be a string when present. A non-dict, missing ``name`` /
        ``parameters``, or a non-string ``name`` / ``description`` ->
        ``ValueError``. Extra keys are tolerated.

        ``parameters`` accepts any JSON value (grok ``serde_json::Value``):
        the key must be present (a missing key is a serde failure on the
        required field), but a ``null`` value is a legal JSON value and is
        preserved as ``None``."""
        if not isinstance(raw, dict):
            raise ValueError(f"tool spec must be a dict, got {type(raw).__name__}")
        name = raw.get("name")
        if not isinstance(name, str):
            raise ValueError(
                "tool spec 'name' must be a string, " f"got {type(name).__name__}"
            )
        if "parameters" not in raw:
            raise ValueError("tool spec 'parameters' is required")
        parameters = raw["parameters"]
        description_raw = raw.get("description")
        if description_raw is None:
            description: str | None = None
        elif isinstance(description_raw, str):
            description = description_raw
        else:
            raise ValueError(
                "tool spec 'description' must be a string or null, "
                f"got {type(description_raw).__name__}"
            )
        return cls(name=name, parameters=parameters, description=description)

    def as_payload(self) -> dict[str, Any]:
        """Wire serializer (mirrors serde Serialize).

        -> ``{"name": <name>, "parameters": <parameters>}`` plus
        ``"description": <description>`` only when it is not ``None``
        (``skip_serializing_if = "Option::is_none"``)."""
        payload: dict[str, Any] = {"name": self.name, "parameters": self.parameters}
        if self.description is not None:
            payload["description"] = self.description
        return payload


__all__ = [
    "ToolCall",
    "ToolSpec",
]
