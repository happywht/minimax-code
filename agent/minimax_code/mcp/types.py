"""Pydantic models for the MCP domain — the canonical type reference for
MiniMax Code's MCP client and server halves.

Mirrors the public MCP spec (``2024-11-05``). The models are
intentionally permissive (``extra="allow"``) so spec extensions and
forward-compatible fields from upstream servers do not break parsing.

Design notes
------------

* Content blocks (text / image / embedded resource) are modelled as a
  discriminated union on the ``type`` field, matching the wire format.
* Capabilities are split into client and server halves — the
  ``initialize`` handshake negotiates which features each side offers.
* Every model subclasses the project's ``_Base`` so it picks up the
  same ``extra="allow"`` + ``populate_by_name`` policy used by the IPC
  layer, keeping validation behaviour uniform across the codebase.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import protocol


class _Base(BaseModel):
    """Shared Pydantic config — mirrors :mod:`minimax_code.ipc.protocol`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ---------------------------------------------------------------------------
# Identity & capabilities
# ---------------------------------------------------------------------------


class Implementation(_Base):
    """Describes a participant in the MCP handshake (client or server)."""

    name: str
    version: str


class ClientCapabilities(_Base):
    """Features the client advertises during ``initialize``."""

    roots: dict[str, Any] | bool | None = None
    sampling: dict[str, Any] | None = None
    experimental: dict[str, Any] | None = None


class ServerCapabilities(_Base):
    """Features the server advertises during ``initialize``."""

    tools: dict[str, Any] | bool | None = None
    resources: dict[str, Any] | bool | None = None
    prompts: dict[str, Any] | bool = None
    completions: dict[str, Any] | None = None
    logging: dict[str, Any] | None = None
    experimental: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Content blocks (discriminated union on ``type``)
# ---------------------------------------------------------------------------


class TextContent(_Base):
    type: Literal["text"] = "text"
    text: str


class ImageContent(_Base):
    type: Literal["image"] = "image"
    data: str  # base64-encoded
    mimeType: str = "image/png"


class EmbeddedResource(_Base):
    type: Literal["resource"] = "resource"
    resource: dict[str, Any] = Field(default_factory=dict)


Content = Annotated[
    TextContent | ImageContent | EmbeddedResource,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class ToolAnnotations(_Base):
    """Optional human-facing hints about a tool (read-only, destructive…)."""

    title: str | None = None
    readOnlyHint: bool | None = None
    destructiveHint: bool | None = None
    idempotentHint: bool | None = None
    openWorldHint: bool | None = None


class Tool(_Base):
    """A tool exposed by an MCP server."""

    name: str
    description: str | None = None
    inputSchema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    annotations: ToolAnnotations | None = None


class CallToolResult(_Base):
    """Return value of ``tools/call``."""

    content: list[Content] = Field(default_factory=list)
    isError: bool = False


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class Resource(_Base):
    """A concrete resource exposed by a server (has a concrete URI)."""

    uri: str
    name: str
    description: str | None = None
    mimeType: str | None = None


class ResourceTemplate(_Base):
    """A parameterised resource (URI template with variables)."""

    uriTemplate: str
    name: str
    description: str | None = None
    mimeType: str | None = None


class ResourceContents(_Base):
    """The payload of a single resource read."""

    uri: str
    mimeType: str | None = None
    text: str | None = None
    blob: str | None = None  # base64


class ReadResourceResult(_Base):
    contents: list[ResourceContents] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


class PromptArgument(_Base):
    name: str
    description: str | None = None
    required: bool | None = None


class Prompt(_Base):
    name: str
    description: str | None = None
    arguments: list[PromptArgument] | None = None


class PromptMessage(_Base):
    role: Literal["user", "assistant"]
    content: Content


class GetPromptResult(_Base):
    messages: list[PromptMessage] = Field(default_factory=list)
    description: str | None = None


# ---------------------------------------------------------------------------
# Handshake envelopes
# ---------------------------------------------------------------------------


class InitializeRequestParams(_Base):
    """Params the client sends with ``initialize``."""

    protocolVersion: str = protocol.LATEST_PROTOCOL_VERSION
    capabilities: ClientCapabilities = Field(default_factory=ClientCapabilities)
    clientInfo: Implementation


class InitializeResult(_Base):
    """Server's reply to ``initialize``."""

    protocolVersion: str = protocol.LATEST_PROTOCOL_VERSION
    capabilities: ServerCapabilities = Field(default_factory=ServerCapabilities)
    serverInfo: Implementation
    instructions: str | None = None


# ---------------------------------------------------------------------------
# List helpers
# ---------------------------------------------------------------------------


class ListResult(_Base):
    """Generic paginated list envelope (cursor is opaque)."""

    nextCursor: str | None = None


class ListToolsResult(ListResult):
    tools: list[Tool] = Field(default_factory=list)


class ListResourcesResult(ListResult):
    resources: list[Resource] = Field(default_factory=list)


class ListResourceTemplatesResult(ListResult):
    resourceTemplates: list[ResourceTemplate] = Field(default_factory=list)


class ListPromptsResult(ListResult):
    prompts: list[Prompt] = Field(default_factory=list)


__all__ = [
    "ClientCapabilities",
    "CallToolResult",
    "Content",
    "EmbeddedResource",
    "GetPromptResult",
    "ImageContent",
    "Implementation",
    "InitializeRequestParams",
    "InitializeResult",
    "ListPromptsResult",
    "ListResourcesResult",
    "ListResourceTemplatesResult",
    "ListResult",
    "ListToolsResult",
    "Prompt",
    "PromptArgument",
    "PromptMessage",
    "ReadResourceResult",
    "Resource",
    "ResourceContents",
    "ResourceTemplate",
    "ServerCapabilities",
    "TextContent",
    "Tool",
    "ToolAnnotations",
]
