"""Per-tool capabilities and notification schemas (R86).

Fusion of grok-build's ``xai-tool-protocol::capabilities`` — the wire-
traveling capability bitset a tool advertises, the streaming/notification
schemas it declares, and the two small snake_case enums
(:class:`HookKind` / :class:`ToolScope`) those structures reference.

This module is the type of ``HelloAckMsg.capabilities`` (R82 handshake's
``hello_ack`` carries the hub's capabilities advertisement) and a
dependency of ``registration`` (a tool-server registration payload carries
its per-tool capabilities). It lands ahead of both as the dependency-free
leaf.

Serde shapes
------------

* :class:`ToolCapabilities` / :class:`StreamingSpec` /
  :class:`NotificationSchemas` — plain structs with per-field
  ``#[serde(default)]`` and ``skip_serializing_if`` (``Option::is_none`` /
  ``Vec::is_empty`` / ``HashMap::is_empty``). The two ``bool`` fields on
  :class:`ToolCapabilities` (``supports_cancel`` / ``is_read_only``) carry
  only ``#[serde(default)]`` with **no** skip, so they always serialise —
  even when ``false`` — matching Rust exactly.
* :class:`HookKind` / :class:`ToolScope` — ``#[serde(rename_all =
  "snake_case")]`` unit variants with **no** ``#[serde(other)]`` arm, so
  unknown wire strings fail to deserialise (not silently swallowed).
  They land as :class:`enum.StrEnum` whose member values are the
  snake_case wire strings.

``to_wire`` / ``from_wire`` live on every type here, matching the crate's
other wire modules (:mod:`error_wire`, :mod:`output_wire`). The barrel
re-exports the five type names only, mirroring Rust ``lib.rs``
``pub use capabilities::{HookKind, NotificationSchemas, StreamingSpec,
ToolCapabilities, ToolScope}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "ToolCapabilities",
    "StreamingSpec",
    "HookKind",
    "ToolScope",
    "NotificationSchemas",
]


class HookKind(StrEnum):
    """Lifecycle hook a tool may opt in to receive.

    ``#[serde(rename_all = "snake_case")]`` with no ``#[serde(other)]``:
    member values are the snake_case wire strings; an unknown wire string
    fails :meth:`from_wire` (serde rejects rather than swallowing).
    """

    OnSessionOpen = "on_session_open"
    OnSessionClose = "on_session_close"
    OnToolCallStart = "on_tool_call_start"
    OnToolCallResult = "on_tool_call_result"
    OnCancel = "on_cancel"
    OnNotification = "on_notification"

    def to_wire(self) -> str:
        """The snake_case wire string (``#[serde(rename_all)]``)."""
        return self.value

    @classmethod
    def from_wire(cls, data: str) -> HookKind:
        """Reconstruct from a wire string; reject unknown values.

        Mirrors serde with no ``#[serde(other)]`` arm: an unknown string
        raises :class:`ValueError` rather than being silently swallowed.
        """
        member = cls._value2member_map_.get(data)
        if member is None:
            raise ValueError(f"unknown HookKind wire value: {data!r}")
        return member  # type: ignore[return-value]


class ToolScope(StrEnum):
    """Multi-agent write-coordination scope.

    Tools that mutate external state declare :attr:`Write` so the computer
    hub routes them to the leader agent only; absence is treated as
    :attr:`Read`. ``#[serde(rename_all = "snake_case")]``, no
    ``#[serde(other)]``.
    """

    Read = "read"
    Write = "write"

    def to_wire(self) -> str:
        """The snake_case wire string."""
        return self.value

    @classmethod
    def from_wire(cls, data: str) -> ToolScope:
        """Reconstruct from a wire string; reject unknown values."""
        member = cls._value2member_map_.get(data)
        if member is None:
            raise ValueError(f"unknown ToolScope wire value: {data!r}")
        return member  # type: ignore[return-value]


@dataclass
class StreamingSpec:
    """How a tool streams partial results.

    Declared once in :attr:`ToolCapabilities.streaming` and consumed at the
    source to stamp a self-describing progress envelope; downstream layers
    dispatch on that envelope rather than the tool's identity.
    """

    #: Stable snake_case discriminator the tool stamps on its
    #: ``ToolProgress::Custom.subkind`` (e.g. ``"bash_output_chunk"``).
    subkind: str
    #: Per-frame ``delta`` byte cap (UTF-8-safe). Unset falls back to the
    #: runtime's 16 KiB default. Independent of
    #: :attr:`ToolCapabilities.max_frame_bytes` (whole-frame cap).
    max_delta_bytes: int | None = None

    def to_wire(self) -> dict[str, object]:
        d: dict[str, object] = {"subkind": self.subkind}
        if self.max_delta_bytes is not None:
            d["max_delta_bytes"] = self.max_delta_bytes
        return d

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> StreamingSpec:
        return cls(
            subkind=str(data["subkind"]),
            max_delta_bytes=(
                int(data["max_delta_bytes"]) if "max_delta_bytes" in data else None
            ),
        )


@dataclass
class ToolCapabilities:
    """Per-tool wire-traveling capabilities.

    Defaults conservatively (no progress, no cancel, single concurrency,
    no hooks) via ``#[derive(Default)]`` semantics — every field carries a
    default so an empty ``{}`` wire payload round-trips to the all-off
    instance. The two ``bool`` fields (``supports_cancel`` /
    ``is_read_only``) always serialise even when ``false`` (Rust gives
    them ``#[serde(default)]`` with no ``skip_serializing_if``); the rest
    omit when ``None`` / empty.
    """

    #: Streaming declaration. ``None`` (the default) means the tool never
    #: emits partial-result progress.
    streaming: StreamingSpec | None = None
    #: Tool honours ``hook { Cancel }``. Always serialised (even ``False``).
    supports_cancel: bool = False
    #: Maximum concurrent invocations the tool accepts. ``None`` = unlimited.
    max_concurrency: int | None = None
    #: Mirrors ``Tool::is_read_only``; used by doom-loop detection. Always
    #: serialised (even ``False``).
    is_read_only: bool = False
    #: Lifecycle hooks the tool opts in to receive. Omitted when empty.
    hooks: list[HookKind] = field(default_factory=list)
    #: Opaque per-tool behaviour version. Bytewise-compared (NOT semver).
    behavior_version: str | None = None
    #: Per-tool override for the per-frame size cap; service clamps to the
    #: 16 MiB hard ceiling.
    max_frame_bytes: int | None = None
    #: Per-call timeout override (defaults to 60_000 ms when omitted).
    timeout_ms: int | None = None
    #: Multi-agent write-coordination scope. Absence is treated as ``Read``.
    tool_scope: ToolScope | None = None

    def to_wire(self) -> dict[str, object]:
        d: dict[str, object] = {
            # bool fields: #[serde(default)] with NO skip → always present.
            "supports_cancel": self.supports_cancel,
            "is_read_only": self.is_read_only,
        }
        if self.streaming is not None:
            d["streaming"] = self.streaming.to_wire()
        if self.max_concurrency is not None:
            d["max_concurrency"] = self.max_concurrency
        if self.hooks:  # skip_serializing_if = "Vec::is_empty"
            d["hooks"] = [h.to_wire() for h in self.hooks]
        if self.behavior_version is not None:
            d["behavior_version"] = self.behavior_version
        if self.max_frame_bytes is not None:
            d["max_frame_bytes"] = self.max_frame_bytes
        if self.timeout_ms is not None:
            d["timeout_ms"] = self.timeout_ms
        if self.tool_scope is not None:
            d["tool_scope"] = self.tool_scope.to_wire()
        return d

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> ToolCapabilities:
        streaming_raw = data.get("streaming")
        hooks_raw = data.get("hooks")
        return cls(
            streaming=(
                StreamingSpec.from_wire(streaming_raw)  # type: ignore[arg-type]
                if streaming_raw is not None
                else None
            ),
            supports_cancel=bool(data.get("supports_cancel", False)),
            max_concurrency=(
                int(data["max_concurrency"]) if "max_concurrency" in data else None
            ),
            is_read_only=bool(data.get("is_read_only", False)),
            hooks=(
                [HookKind.from_wire(h) for h in hooks_raw]  # type: ignore[union-attr]
                if hooks_raw
                else []
            ),
            behavior_version=(
                str(data["behavior_version"])
                if "behavior_version" in data
                else None
            ),
            max_frame_bytes=(
                int(data["max_frame_bytes"]) if "max_frame_bytes" in data else None
            ),
            timeout_ms=int(data["timeout_ms"]) if "timeout_ms" in data else None,
            tool_scope=(
                ToolScope.from_wire(str(data["tool_scope"]))
                if "tool_scope" in data
                else None
            ),
        )


@dataclass
class NotificationSchemas:
    """Per-tool notification schemas.

    Keys are the notification ``kind`` strings the computer hub validates
    against; values are opaque JSON schemas (``serde_json::Value``).
    """

    #: Schemas for notifications the tool emits to subscribers.
    outbound: dict[str, Any] = field(default_factory=dict)
    #: Schemas for notifications the harness sends to the tool.
    inbound: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, object]:
        d: dict[str, object] = {}
        if self.outbound:  # skip_serializing_if = "HashMap::is_empty"
            d["outbound"] = dict(self.outbound)
        if self.inbound:
            d["inbound"] = dict(self.inbound)
        return d

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> NotificationSchemas:
        outbound_raw = data.get("outbound")
        inbound_raw = data.get("inbound")
        return cls(
            outbound=dict(outbound_raw) if outbound_raw else {},
            inbound=dict(inbound_raw) if inbound_raw else {},
        )
