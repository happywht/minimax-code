"""Connection-shape and tool-definition-mode enums (R82).

Fusion of grok-build's ``xai-tool-protocol::connection`` — two small
enums the computer hub uses to classify a WebSocket connection and to
decide how the registered tool set is exposed to the model.

Two serde shapes land here:

* **plain externally-tagged enum** — :class:`ConnectionKind`
  (``#[serde(rename_all = "snake_case")]``) serialises to a bare
  ``"harness"`` / ``"tool_server"`` string. Reproduced as a
  :class:`enum.StrEnum`.
* **internally-tagged enum** — :class:`ToolDefinitionMode`
  (``#[serde(tag = "mode", rename_all = "snake_case")]``) folds the
  discriminator into the content object: ``Full`` serialises as
  ``{"mode": "full"}`` and ``Concise`` as
  ``{"mode": "concise", "meta_search": "...", "meta_call": "..."}``.
  This is the crate's first internally-tagged enum (the workspace-types
  layer used adjacent-tagged exclusively), so it establishes the shape
  inline rather than via a shared base.
"""

from __future__ import annotations

from enum import StrEnum

from minimax_code.tool_protocol.ids import ToolId

__all__ = ["ConnectionKind", "ToolDefinitionMode"]


class ConnectionKind(StrEnum):
    """Role of a WebSocket connection (``connection::ConnectionKind``).

    Plain ``#[serde(rename_all = "snake_case")]`` enum — serialises to a
    bare snake_case string. The hub uses this to decide which methods are
    valid on a given socket.
    """

    Harness = "harness"
    ToolServer = "tool_server"


class ToolDefinitionMode:
    """How the hub exposes the registered tool set (``connection::ToolDefinitionMode``).

    Internally-tagged on ``mode`` (``#[serde(tag = "mode", rename_all =
    "snake_case")]``): the discriminator rides *inside* the content object
    rather than beside it (the adjacent-tagged shape). ``Full`` is a unit
    variant (``{"mode": "full"}``); ``Concise`` carries a configurable
    meta-tool pair so callers can choose the model-facing names of the
    search/invoke meta-tools per session.

    ``Copy`` is intentionally NOT derived in Rust (``Concise``'s
    :class:`ToolId` fields wrap heap strings); reproduced by making this a
    plain reference class with no ``__copy__`` hook.
    """

    __slots__ = ("mode", "meta_search", "meta_call")

    def __init__(
        self,
        mode: str,
        meta_search: ToolId | None = None,
        meta_call: ToolId | None = None,
    ) -> None:
        self.mode = mode
        self.meta_search = meta_search
        self.meta_call = meta_call

    # -- factories ---------------------------------------------------------

    @classmethod
    def full(cls) -> ToolDefinitionMode:
        """Every ``ToolDescription`` is sent to the model directly (unit variant)."""
        return cls("full")

    @classmethod
    def concise(
        cls, meta_search: ToolId, meta_call: ToolId
    ) -> ToolDefinitionMode:
        """Only the meta-tool pair is sent; the rest is discoverable (struct variant).

        ``meta_search`` is the model-facing name of the search/discovery
        meta-tool; ``meta_call`` the call/invoke meta-tool.
        """
        return cls("concise", meta_search, meta_call)

    # -- wire --------------------------------------------------------------

    def to_wire(self) -> dict[str, object]:
        """Internally-tagged dict (``#[serde(tag = "mode")]``)."""
        if self.mode == "full":
            return {"mode": "full"}
        if self.mode == "concise":
            # ``meta_search`` / ``meta_call`` are :class:`ToolId` (transparent
            # str newtypes) — serialise as the bare string.
            assert self.meta_search is not None and self.meta_call is not None
            return {
                "mode": "concise",
                "meta_search": str(self.meta_search),
                "meta_call": str(self.meta_call),
            }
        raise ValueError(f"unknown ToolDefinitionMode mode {self.mode!r}")

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> ToolDefinitionMode:
        """Reconstruct from an internally-tagged dict."""
        mode = data["mode"]
        if mode == "full":
            return cls.full()
        if mode == "concise":
            return cls.concise(
                ToolId(str(data["meta_search"])),
                ToolId(str(data["meta_call"])),
            )
        raise ValueError(f"unknown ToolDefinitionMode mode {mode!r}")

    # -- dunder ------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ToolDefinitionMode):
            return NotImplemented
        return (
            self.mode == other.mode
            and self.meta_search == other.meta_search
            and self.meta_call == other.meta_call
        )

    def __hash__(self) -> int:
        return hash((ToolDefinitionMode, self.mode, self.meta_search, self.meta_call))

    def __repr__(self) -> str:
        if self.mode == "full":
            return "ToolDefinitionMode.full()"
        return (
            f"ToolDefinitionMode.concise(meta_search={self.meta_search!r}, "
            f"meta_call={self.meta_call!r})"
        )
