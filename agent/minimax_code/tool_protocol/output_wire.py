"""Wire-friendly tool-call output (R83).

Fusion of grok-build's ``xai-tool-protocol::output_wire`` — the stable
wire representation of a tool-call result. A tool server emits one of three
shapes:

* :class:`Text` — pre-formatted prompt text (the in-process default via
  ``ToolOutput::to_prompt_format``);
* :class:`Json` — opaque JSON escape hatch (mirrors ``ToolOutput::Dynamic``);
* :class:`Mcp` — MCP-style structured blocks.

Two serde shapes land here:

* **adjacent-tagged enum** — :class:`ToolOutputWire`
  (``#[serde(tag = "kind", content = "value", rename_all =
  "snake_case")]``) splits the discriminator from the payload:
  ``{"kind": "text", "value": "..."}``. The three variants carry
  heterogeneous payload shapes (bare string / bare JSON / struct), so each
  implements ``to_wire`` / ``from_wire`` inline rather than via a shared
  base — the payload extraction differs per variant.
* **internally-tagged enum** — :class:`McpBlock`
  (``#[serde(tag = "type", rename_all = "snake_case")]``) is the crate's
  third internal-tag enum; ``{"type": "text", "text": "..."}`` etc.

Naming
------

``ToolOutputWire``'s ``Text`` / ``Json`` / ``Mcp`` variants keep their Rust
names at the top level. ``McpBlock``'s ``Text`` / ``Image`` / ``Resource``
variants would collide, so they carry a ``Block`` suffix
(:class:`TextBlock` / :class:`ImageBlock` / :class:`ResourceBlock`);
``McpBlock`` is the union alias.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "ToolOutputWire",
    "Text",
    "Json",
    "Mcp",
    "McpBlock",
    "TextBlock",
    "ImageBlock",
    "ResourceBlock",
    "from_wire",
    "mcp_block_from_wire",
]


# -----------------------------------------------------------------------
# ToolOutputWire — adjacent-tagged on ``kind`` / ``value``.
# -----------------------------------------------------------------------


@dataclass
class Text:
    """Pre-formatted prompt text (``ToolOutputWire::Text``).

    Serialises as ``{"kind": "text", "value": <text>}``.
    """

    text: str

    def to_wire(self) -> dict[str, object]:
        return {"kind": "text", "value": self.text}

    @classmethod
    def from_value(cls, value: object) -> Text:
        return cls(text=str(value))


@dataclass
class Json:
    """Opaque JSON escape hatch (``ToolOutputWire::Json``).

    Serialises as ``{"kind": "json", "value": <json>}``. The payload is
    arbitrary JSON (``serde_json::Value``), passed through untouched.
    """

    json: Any

    def to_wire(self) -> dict[str, object]:
        return {"kind": "json", "value": self.json}

    @classmethod
    def from_value(cls, value: object) -> Json:
        return cls(json=value)


@dataclass
class Mcp:
    """MCP-style structured blocks (``ToolOutputWire::Mcp``).

    Serialises as ``{"kind": "mcp", "value": {"blocks": [...]}}`` — the
    adjacent-tag ``content`` wraps the struct's serialised form, so the
    ``blocks`` key sits one level deeper than the ``kind`` discriminator.
    """

    blocks: list[TextBlock | ImageBlock | ResourceBlock]

    def to_wire(self) -> dict[str, object]:
        return {
            "kind": "mcp",
            "value": {"blocks": [b.to_wire() for b in self.blocks]},
        }

    @classmethod
    def from_value(cls, value: dict[str, object]) -> Mcp:
        return cls(
            blocks=[mcp_block_from_wire(b) for b in value["blocks"]]  # type: ignore[arg-type]
        )


#: Discriminated union of the three output shapes.
ToolOutputWire = Text | Json | Mcp


def from_wire(data: dict[str, object]) -> ToolOutputWire:
    """Reconstruct a :class:`ToolOutputWire` from ``{"kind", "value"}``.

    Dispatches on ``data["kind"]``; unknown kinds raise :class:`ValueError`.
    """
    kind = data["kind"]
    value = data["value"]
    if kind == "text":
        return Text.from_value(value)
    if kind == "json":
        return Json.from_value(value)
    if kind == "mcp":
        return Mcp.from_value(value)  # type: ignore[arg-type]
    raise ValueError(f"unknown ToolOutputWire kind {kind!r}")


# -----------------------------------------------------------------------
# McpBlock — internally-tagged on ``type``.
# -----------------------------------------------------------------------


@dataclass
class TextBlock:
    """A text block (``McpBlock::Text``).

    Serialises as ``{"type": "text", "text": <text>}``.
    """

    text: str

    def to_wire(self) -> dict[str, object]:
        return {"type": "text", "text": self.text}

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> TextBlock:
        return cls(text=str(data["text"]))


@dataclass
class ImageBlock:
    """A base64-encoded image block (``McpBlock::Image``).

    Serialises as ``{"type": "image", "mime_type": ..., "data": ...}``.
    """

    mime_type: str
    data: str

    def to_wire(self) -> dict[str, object]:
        return {
            "type": "image",
            "mime_type": self.mime_type,
            "data": self.data,
        }

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> ImageBlock:
        return cls(mime_type=str(data["mime_type"]), data=str(data["data"]))


@dataclass
class ResourceBlock:
    """A resource reference block (``McpBlock::Resource``).

    :attr:`mime_type` and :attr:`text` are optional (omitted when ``None``);
    :attr:`uri` is always present. Serialises as
    ``{"type": "resource", "uri": ..., "mime_type"?, "text"?}``.
    """

    uri: str
    mime_type: str | None = None
    text: str | None = None

    def to_wire(self) -> dict[str, object]:
        d: dict[str, object] = {"type": "resource", "uri": self.uri}
        if self.mime_type is not None:
            d["mime_type"] = self.mime_type
        if self.text is not None:
            d["text"] = self.text
        return d

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> ResourceBlock:
        return cls(
            uri=str(data["uri"]),
            mime_type=data.get("mime_type"),  # type: ignore[arg-type]
            text=data.get("text"),  # type: ignore[arg-type]
        )


#: Discriminated union of the three MCP block shapes (the ``McpBlock`` enum).
McpBlock = TextBlock | ImageBlock | ResourceBlock


def mcp_block_from_wire(data: dict[str, object]) -> McpBlock:
    """Reconstruct a :class:`McpBlock` from ``{"type": ..., ...}``.

    Dispatches on ``data["type"]``; unknown types raise :class:`ValueError`.
    """
    type_ = data["type"]
    if type_ == "text":
        return TextBlock.from_wire(data)
    if type_ == "image":
        return ImageBlock.from_wire(data)
    if type_ == "resource":
        return ResourceBlock.from_wire(data)
    raise ValueError(f"unknown McpBlock type {type_!r}")
