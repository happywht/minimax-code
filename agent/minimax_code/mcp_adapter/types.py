"""MCP protocol types used by the adapter (R179).

Fusion of grok-build's ``xai-computer-hub-mcp-adapter/src/types.rs`` (108
lines) -- the mcp_adapter crate's 1st leaf. Pure wire contract types for the
MCP (Model Context Protocol) JSON-RPC shapes a bridge exchanges with an MCP
server: server metadata (``initialize``), tool definitions (``tools/list``),
call results (``tools/call``), the content-block enum, and the error union.

These are intentionally decoupled from any transport implementation so the
bridge stays testable with in-memory mocks (mirrors the Rust docstring).

Python-specific adaptations (no behavior change)
------------------------------------------------

* Rust ``serde`` ``Serialize``/``Deserialize`` + ``#[serde(rename_all =
  "camelCase")]`` -> pydantic v2 ``BaseModel`` with ``Field(alias=...)`` and
  ``ConfigDict(populate_by_name=True)``, so both the camelCase wire name and
  the snake_case Python attribute are accepted on input and the camelCase
  form is emitted on output (matches the MCP spec).
* Rust ``#[serde(default)]`` -> a pydantic default (``None`` / ``False`` /
  ``default_factory``), so an absent field round-trips to the same value.
* Rust ``serde_json::Value`` (arbitrary JSON) -> ``Any`` (faithful to
  ``Value``'s any-type acceptance; pydantic v2 validates ``Any`` permissively).
* Rust ``enum McpContent { Text, Image, Resource }`` with
  ``#[serde(tag = "type", rename_all = "camelCase")]`` -> an
  :data:`Annotated` ``Union`` of three ``BaseModel`` variants discriminated
  by a ``type: Literal[...]`` field -- pydantic's native internally-tagged
  union, the direct equivalent of serde's ``tag = "type"``.
* Rust ``enum McpError`` (``thiserror::Error``) -> :class:`McpError` (base
  ``Exception``) plus four subclasses (:class:`McpTransportError` /
  :class:`McpProtocolError` / :class:`McpTimeoutError` /
  :class:`McpDecodeError`). Each subclass formats the Rust ``#[error(...)]``
  template into its ``args[0]`` message, so ``str(err)`` matches the Rust
  ``Display`` output; callers ``isinstance``-dispatch (the Rust ``match``
  equivalent) and ``except McpError`` catches all four.
* Rust ``i64`` error code -> Python ``int`` (Python ``int`` is unbounded, a
  superset of ``i64``).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "McpServerInfo",
    "McpToolDefinition",
    "McpCallResult",
    "McpContent",
    "McpTextContent",
    "McpImageContent",
    "McpResourceContent",
    "McpError",
    "McpTransportError",
    "McpProtocolError",
    "McpTimeoutError",
    "McpDecodeError",
]

#: pydantic config shared by every wire model: accept both the snake_case
#: Python attribute and the camelCase wire alias, and ignore unknown fields
#: (serde's default -- the Rust structs have no ``deny_unknown_fields``).
_WIRE_CONFIG = ConfigDict(populate_by_name=True, extra="ignore")


class McpServerInfo(BaseModel):
    """Metadata returned by a successful MCP ``initialize`` handshake.

    Mirrors ``McpServerInfo`` in ``types.rs``. ``capabilities`` is arbitrary
    JSON (``serde_json::Value``) -- the free-form capability flags the server
    advertises during init; absent on the wire -> ``None`` (Rust
    ``#[serde(default)]`` -> ``Value::Null``).
    """

    model_config = _WIRE_CONFIG

    #: Human-readable server name (e.g. ``"linear"``, ``"github"``).
    name: str
    #: Semver-ish version reported by the server.
    version: str
    #: Free-form capability flags advertised during init (``Value::Null`` when
    #: absent, faithful to ``#[serde(default)]`` on ``serde_json::Value``).
    capabilities: Any = None


class McpToolDefinition(BaseModel):
    """A single tool definition from MCP ``tools/list``.

    Mirrors ``McpToolDefinition`` (``#[serde(rename_all = "camelCase")]``):
    ``input_schema`` rides the wire as ``inputSchema``.
    """

    model_config = _WIRE_CONFIG

    #: Unqualified tool name (e.g. ``"create_issue"``).
    name: str
    #: Model-facing description of the tool (``None`` when the server omits it).
    description: str | None = None
    #: JSON Schema for the tool's input arguments (wire name ``inputSchema``).
    input_schema: Any = Field(default=None, alias="inputSchema")


class McpTextContent(BaseModel):
    """Plain-text content block (``McpContent::Text``, wire tag ``"text"``).

    The ``type`` literal pins the discriminator so :data:`McpContent` routes
    a ``{"type": "text", ...}`` payload here.
    """

    model_config = _WIRE_CONFIG

    type: Literal["text"] = "text"
    #: The text payload.
    text: str


class McpImageContent(BaseModel):
    """Base64-encoded image content (``McpContent::Image``, wire tag ``"image"``).

    ``mime_type`` rides the wire as ``mimeType`` (``rename = "mimeType"``).
    """

    model_config = _WIRE_CONFIG

    type: Literal["image"] = "image"
    #: MIME type (e.g. ``"image/png"``).
    mime_type: str = Field(alias="mimeType")
    #: Base64-encoded image bytes.
    data: str


class McpResourceContent(BaseModel):
    """Embedded resource content (``McpContent::Resource``, wire tag ``"resource"``).

    ``mime_type`` is optional (``#[serde(default, rename = "mimeType")]``);
    ``text`` is optional (``#[serde(default)]``).
    """

    model_config = _WIRE_CONFIG

    type: Literal["resource"] = "resource"
    #: Resource URI.
    uri: str
    #: Optional MIME type (wire name ``mimeType``).
    mime_type: str | None = Field(default=None, alias="mimeType")
    #: Optional text body.
    text: str | None = None


#: Typed sum of the three MCP content-block shapes (the Rust
#: ``#[serde(tag = "type", rename_all = "camelCase")] enum McpContent``
#: equivalent). pydantic discriminates on the ``type`` literal, routing each
#: payload to the matching variant; callers ``isinstance``-dispatch.
McpContent = Annotated[
    McpTextContent | McpImageContent | McpResourceContent,
    Field(discriminator="type"),
]


class McpCallResult(BaseModel):
    """Result of an MCP ``tools/call`` invocation.

    Mirrors ``McpCallResult`` (``#[serde(rename_all = "camelCase")]``):
    ``is_error`` rides the wire as ``isError``; ``content`` defaults to empty
    when absent (``#[serde(default)]``).
    """

    model_config = _WIRE_CONFIG

    #: Content blocks returned by the tool (empty when the server omits it).
    content: list[McpContent] = Field(default_factory=list)
    #: When ``True``, the tool signalled an application-level error (wire name
    #: ``isError``).
    is_error: bool = Field(default=False, alias="isError")


class McpError(Exception):
    """Base for errors originating from MCP transport or protocol handling.

    Mirrors the ``enum McpError`` (``thiserror::Error``) discriminant: the
    four variants below subclass this base, so ``except McpError`` catches
    every MCP-originated error and ``isinstance`` selects the variant (the
    Rust ``match`` equivalent).
    """


class McpTransportError(McpError):
    """The underlying transport failed (connection refused, pipe broken, ...).

    Mirrors ``McpError::Transport(String)``; the ``#[error("transport error:
    {0}")]`` template becomes the ``str(err)`` form.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"transport error: {message}")


class McpProtocolError(McpError):
    """The server returned a JSON-RPC error response.

    Mirrors ``McpError::Protocol { code, message }``; the ``#[error("protocol
    error (code {code}): {message}")]`` template becomes the ``str(err)`` form.
    """

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"protocol error (code {code}): {message}")


class McpTimeoutError(McpError):
    """Timeout waiting for an MCP server response.

    Mirrors ``McpError::Timeout(String)``; the ``#[error("timeout: {0}")]``
    template becomes the ``str(err)`` form.

    Note: named ``McpTimeoutError`` (not ``McpTimeout``) to avoid shadowing
    the built-in :class:`TimeoutError`, which UP041 prefers for generic
    timeouts; this is the MCP-specific variant carrying a descriptive string.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"timeout: {message}")


class McpDecodeError(McpError):
    """The response could not be decoded.

    Mirrors ``McpError::Decode(String)``; the ``#[error("decode error: {0}")]``
    template becomes the ``str(err)`` form.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"decode error: {message}")
