"""Per-call / per-turn context types + the typed-extension store they share (R108).

Fusion of grok-build's ``xai-tool-runtime::context``. These are the
per-invocation values a tool reads off its context — the working
directory, the behaviour version, the trace/session correlation ids, the
cooperative-cancellation handle, and the per-user feature-flag bag — plus
the open typed-extension store (:class:`TypedExtensions`) they ride in,
and the two composite contexts (:class:`ToolCallContext` per call,
:class:`ListToolsContext` per turn).

Why context lands second
------------------------

R107 migrated the error leaf; this round lands the *context* leaf. Like
``error``, ``context`` has no in-crate dependencies beyond
``xai_tool_protocol::ToolCallId`` — it is the second leaf of the
``xai-tool-runtime`` dependency graph. It is also the upstream of
``tool.rs`` (``Tool::call`` takes a ``ToolCallContext``; ``should_list``
takes a ``ListToolsContext``) and ``dispatch.rs`` (``ToolDispatch::call``
threads a ``ToolCallContext``), so it must land before either.

TypeId -> type
--------------

Rust's ``TypedExtensions`` keys entries by ``TypeId::of::<T>()`` and
stores ``Arc<dyn Any + Send + Sync>``; ``get::<T>()`` downcasts back.
Python's natural TypeId is the class object itself (every class has a
unique identity), so the store keys by ``type`` and stores the value
directly — ``get(SomeType)`` returns the value as-is, no downcast needed
(Python is dynamically typed). The generic ``insert::<T>(value)`` /
``get::<T>()`` surface degrades to ``insert(value)`` (keys on
``type(value)``) / ``get(key_type)``: the caller passes the type where
Rust infers it from the type parameter. Because the key is the *exact*
type, a subclass instance is NOT retrievable by its base type — matching
Rust's ``TypeId`` precision.

Cancellation
------------

Rust's ``Cancellation(pub tokio_util::sync::CancellationToken)`` wraps
tokio's structured-concurrency token. Its cooperative surface —
``is_cancelled()`` / ``cancel()`` / ``await cancelled()`` — maps onto
:class:`asyncio.Event`. The structured-concurrency surface
(``child_token``, ``guard``, ``CancelLedger``) is **not** migrated: R23
fused the dispatcher's two-stage parallel scheduling and per-file
locking, and Python's hard-cancel is ``task.cancel()`` rather than a
token tree. This leaf carries only the cooperative surface tools await.
Two ``Cancellation`` dataclasses sharing the same ``asyncio.Event`` see
each other's ``cancel()`` — the Rust ``CancellationToken::clone`` sibling
semantics.

WorkspaceBindMetadata
---------------------

The wire shape of the Computer Hub ``session.bind`` metadata. Rust gives
it ``#[derive(Serialize, Deserialize)]`` with a per-field
``deserialize_with = "ok_or_default"`` (a malformed value drops to its
default rather than failing the whole struct, so valid siblings survive)
and ``skip_serializing_if`` hints (``None`` fields, empty ``tools``, and
``rpc_only == false`` are omitted on the wire). The Python equivalent is
:class:`WorkspaceBindMetadata` with ``to_dict`` / ``from_dict`` methods
encoding the same omit-on-default serialise policy and the same
per-field try-and-fall-back deserialise policy. The ``tools`` field is
typed ``list[Any]``: the Rust
``Vec<xai_grok_tools_api::ToolConfigEntry>`` depends on an
as-yet-unmigrated crate, so the element type stays opaque until that
crate lands (YAGNI — the wire shape is the contract, not the element
struct).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from minimax_code.tool_protocol.ids import ToolCallId

__all__ = [
    "BehaviorVersion",
    "Cancellation",
    "Cwd",
    "ListToolsContext",
    "SessionContext",
    "ToolCallContext",
    "TraceContext",
    "TypedExtensions",
    "WorkspaceBindMetadata",
    "WorkspaceViewerContext",
]


# -----------------------------------------------------------------------
# TypedExtensions — open typed-extension store keyed by exact type.
# -----------------------------------------------------------------------


class TypedExtensions:
    """Open typed-extension store keyed by exact ``type``.

    Fusion of ``xai_tool_runtime::TypedExtensions``. One entry per
    concept type: dispatchers install exactly what they have, tools
    depend on exactly what they need. The store is keyed by the value's
    exact ``type`` (Python's TypeId), so a subclass instance is NOT
    retrievable by its base type — matching Rust's ``TypeId`` precision.

    Provides the full crate API surface (``new`` / ``insert`` /
    ``insert_arc`` / ``get`` / ``contains`` / ``remove`` / ``len`` /
    ``is_empty`` / ``merge_defaults``) plus the natural Python dunder
    surface (``len(ext)`` / ``bool(ext)`` / ``key in ext``) so it behaves
    as a typed map.
    """

    def __init__(self) -> None:
        self._map: dict[type, Any] = {}

    @classmethod
    def new(cls) -> TypedExtensions:
        """Empty store (Rust ``TypedExtensions::new`` / ``Default``)."""
        return cls()

    def insert(self, value: Any) -> TypedExtensions:
        """Store ``value`` keyed by its exact ``type`` (Rust ``insert::<T>``).

        A second insert of the same type replaces the prior value (Rust
        ``HashMap::insert`` returns the old value; Python's ``dict``
        assignment has the same overwrite semantics). Returns ``self`` for
        chaining (Rust ``&mut self``).
        """
        self._map[type(value)] = value
        return self

    def insert_arc(self, value: Any) -> TypedExtensions:
        """Store ``value`` keyed by its exact ``type`` (Rust ``insert_arc::<T>``).

        Rust's ``insert_arc`` takes an ``Arc<T>`` the caller already owns
        (avoiding a second ``Arc::new`` wrap) but still keys by
        ``TypeId::of::<T>()``. Python has no ``Arc`` — objects are already
        reference-shared — so ``insert_arc`` is identical to
        :meth:`insert`. Kept as a distinct method so the crate API surface
        ports one-for-one.
        """
        self._map[type(value)] = value
        return self

    def get(self, key: type) -> Any | None:
        """Fetch the entry stored under ``key`` (Rust ``get::<T>``).

        Returns ``None`` if no entry of that exact type is present. The
        Rust generic ``get::<T>()`` infers the key from the type
        parameter; Python passes the type object explicitly.
        """
        return self._map.get(key)

    def contains(self, key: type) -> bool:
        """Whether an entry of exact type ``key`` is present."""
        return key in self._map

    def remove(self, key: type) -> Any | None:
        """Remove and return the entry under ``key`` (``None`` if absent)."""
        return self._map.pop(key, None)

    def len(self) -> int:
        """Number of entries (crate API name; also available as ``len(ext)``)."""
        return len(self._map)

    def is_empty(self) -> bool:
        """Whether the store holds no entries."""
        return not self._map

    def merge_defaults(self, defaults: TypedExtensions) -> None:
        """Copy entries from ``defaults`` that are not already present.

        Mirrors Rust ``merge_defaults``: for each ``(key, value)`` in
        ``defaults``, insert into ``self`` only if ``key`` is not already
        present (``HashMap::entry(key).or_insert_with(|| value.clone())``).
        Existing entries win; the caller's ``self`` is mutated in place.
        """
        for key, value in defaults._map.items():
            if key not in self._map:
                self._map[key] = value

    # -- Pythonic dunder surface (map-like) ------------------------------

    def __len__(self) -> int:
        return len(self._map)

    def __bool__(self) -> bool:
        return bool(self._map)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, type) and key in self._map


# -----------------------------------------------------------------------
# Per-call / per-turn composite contexts.
# -----------------------------------------------------------------------


@dataclass
class ToolCallContext:
    """Per-call context threaded through ``Tool::call`` (Rust ``ToolCallContext``).

    Carries the call's id and its typed-extension store. The
    :meth:`insert` / :meth:`get` helpers delegate to
    :attr:`extensions` so tool bodies can install/read concept values
    (:class:`Cwd`, :class:`BehaviorVersion`, :class:`Cancellation`, …)
    without naming the store.
    """

    call_id: ToolCallId
    extensions: TypedExtensions = field(default_factory=TypedExtensions)

    @classmethod
    def new(cls, call_id: ToolCallId) -> ToolCallContext:
        """Construct with an explicit ``call_id`` and an empty store."""
        return cls(call_id=call_id)

    @classmethod
    def default(cls) -> ToolCallContext:
        """Default context: a fresh unique ``call_id`` + empty store.

        Rust's ``Default`` impl calls ``ToolCallId::new_v7()``. Python's
        stdlib has no UUID v7 generator (deferred per R82
        ``ToolCallId.new_v7``), so this falls back to ``uuid4`` — the
        surface contract is "fresh unique call id", not v7 specifically.
        """
        return cls(call_id=ToolCallId(str(uuid.uuid4())))

    def insert(self, value: Any) -> ToolCallContext:
        """Delegate to :meth:`TypedExtensions.insert` (returns self)."""
        self.extensions.insert(value)
        return self

    def get(self, key: type) -> Any | None:
        """Delegate to :meth:`TypedExtensions.get`."""
        return self.extensions.get(key)


@dataclass
class ListToolsContext:
    """Per-turn context consumed by ``Tool::should_list`` (Rust ``ListToolsContext``).

    Carries only the typed-extension store — ``should_list`` does not
    need a ``call_id`` (it runs once per turn, not once per call).
    """

    extensions: TypedExtensions = field(default_factory=TypedExtensions)

    @classmethod
    def new(cls) -> ListToolsContext:
        """Empty context (Rust ``ListToolsContext::new`` / ``Default``)."""
        return cls()


# -----------------------------------------------------------------------
# Runtime-blessed per-concept extension newtypes.
# One type per concept so dispatchers install exactly what they have and
# tools depend on exactly what they need.
# -----------------------------------------------------------------------


@dataclass
class Cwd:
    """Working directory for relative path resolution (Rust ``Cwd(pub PathBuf)``)."""

    path: Path


@dataclass
class BehaviorVersion:
    """Opaque behaviour version (Rust ``BehaviorVersion(pub String)``).

    Tools that branch on this MUST treat unknown values as a hard error.
    """

    version: str


@dataclass
class TraceContext:
    """Distributed-trace correlation context, e.g. W3C ``traceparent``.

    Receive-side carrier only: stamped from the inbound wire value for
    tool impls to read, never serialized back out.
    """

    value: str


@dataclass
class SessionContext:
    """Session-id context — which hub session this call belongs to.

    Used by multi-session tool servers to dispatch to the correct
    per-session state.
    """

    value: str


@dataclass
class Cancellation:
    """Cooperative-cancellation handle for the current tool call.

    Fusion of ``xai_tool_runtime::Cancellation(pub CancellationToken)``.
    Tools MAY poll :meth:`is_cancelled` or ``await`` :meth:`cancelled`
    for graceful shutdown; the dispatcher also hard-cancels by cancelling
    the call task when it fires. The token is an :class:`asyncio.Event`
    (set = cancelled); two dataclasses sharing the same event see each
    other's :meth:`cancel` (sibling cancellation), matching tokio
    ``CancellationToken::clone``.
    """

    token: asyncio.Event = field(default_factory=asyncio.Event)

    def is_cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        return self.token.is_set()

    def cancel(self) -> None:
        """Request cancellation (idempotent; wakes all ``cancelled()`` awaiters)."""
        self.token.set()

    async def cancelled(self) -> None:
        """Resolve once cancellation is requested (cooperative await point).

        Mirrors tokio ``CancellationToken::cancelled``. After
        :meth:`cancel` has been called, this returns immediately; before,
        it blocks the caller until another task fires :meth:`cancel`.
        """
        await self.token.wait()


# -----------------------------------------------------------------------
# Per-user feature-flag bag + session.bind wire metadata.
# -----------------------------------------------------------------------


@dataclass
class WorkspaceViewerContext:
    """Per-user feature-flag bag attached as a ``ToolCallContext`` extension.

    Dispatcher resolves; tools read. Default = "off" for every field so
    an absent extension never accidentally opts a feature in. Extend by
    adding fields with safe defaults (``False``); new fields stay
    deserialisable from older ``session.bind`` payloads.
    """

    #: When ``True``, ``BashTool`` emits ``bash_output_chunk`` Progress frames.
    stream_tool_progress: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise (Rust ``#[derive(Serialize)]``; no skip hints)."""
        return {"stream_tool_progress": self.stream_tool_progress}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceViewerContext:
        """Deserialise with per-field fall-back (Rust ``#[serde(default)]``)."""
        raw = data.get("stream_tool_progress")
        return cls(stream_tool_progress=raw if isinstance(raw, bool) else False)


@dataclass
class WorkspaceBindMetadata:
    """Wire shape of the Computer Hub ``session.bind`` metadata.

    One definition shared by the emitter (serialises) and the workspace
    consumer (deserialises), so the two can't drift on field names /
    types. Excludes anything not meant for the workspace (cached tool
    definitions, terminal-provisioning inputs) so they can never reach
    the wire. Every field tolerates a missing / malformed value (drops to
    default) to keep valid siblings and mixed-version compatibility.

    The ``tools`` field is ``list[Any]``: the Rust
    ``Vec<xai_grok_tools_api::ToolConfigEntry>`` depends on an
    as-yet-unmigrated crate, so the element type stays opaque until that
    crate lands (the wire shape is the contract; the element struct is
    not).
    """

    preset: str | None = None
    capability_mode: str | None = None
    tools: list[Any] = field(default_factory=list)
    viewer_ctx: WorkspaceViewerContext | None = None
    yolo_mode: bool | None = None
    manifest_version: str | None = None
    manifest_hash: str | None = None
    system_notifications: bool | None = None
    #: Omitted on the wire when ``False`` (legacy / mixed-version compat).
    rpc_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise with the crate's skip-on-default omit policy.

        Mirrors the Rust ``skip_serializing_if`` hints: ``None`` fields,
        an empty ``tools`` list, and ``rpc_only == False`` are omitted so
        a default instance serialises to ``{}``.
        """
        out: dict[str, Any] = {}
        if self.preset is not None:
            out["preset"] = self.preset
        if self.capability_mode is not None:
            out["capability_mode"] = self.capability_mode
        if self.tools:
            out["tools"] = list(self.tools)
        if self.viewer_ctx is not None:
            out["viewer_ctx"] = self.viewer_ctx.to_dict()
        if self.yolo_mode is not None:
            out["yolo_mode"] = self.yolo_mode
        if self.manifest_version is not None:
            out["manifest_version"] = self.manifest_version
        if self.manifest_hash is not None:
            out["manifest_hash"] = self.manifest_hash
        if self.system_notifications is not None:
            out["system_notifications"] = self.system_notifications
        if self.rpc_only:
            out["rpc_only"] = True
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceBindMetadata:
        """Deserialise with per-field ``ok_or_default`` fall-back.

        Mirrors the Rust ``deserialize_with = "ok_or_default"`` helper: a
        malformed value for one field drops to that field's default
        rather than failing the whole struct, so valid siblings survive
        (e.g. a non-list ``tools`` yields ``[]`` but a well-formed
        ``preset`` is still read).
        """

        def _opt_str(key: str) -> str | None:
            raw = data.get(key)
            return raw if isinstance(raw, str) else None

        def _opt_bool(key: str) -> bool | None:
            raw = data.get(key)
            return raw if isinstance(raw, bool) else None

        def _tools() -> list[Any]:
            raw = data.get("tools")
            return list(raw) if isinstance(raw, list) else []

        def _viewer_ctx() -> WorkspaceViewerContext | None:
            raw = data.get("viewer_ctx")
            if not isinstance(raw, dict):
                return None
            try:
                return WorkspaceViewerContext.from_dict(raw)
            except Exception:
                # ok_or_default: a malformed viewer_ctx drops to None
                # rather than failing the whole metadata struct.
                return None

        def _rpc_only() -> bool:
            raw = data.get("rpc_only")
            return raw if isinstance(raw, bool) else False

        return cls(
            preset=_opt_str("preset"),
            capability_mode=_opt_str("capability_mode"),
            tools=_tools(),
            viewer_ctx=_viewer_ctx(),
            yolo_mode=_opt_bool("yolo_mode"),
            manifest_version=_opt_str("manifest_version"),
            manifest_hash=_opt_str("manifest_hash"),
            system_notifications=_opt_bool("system_notifications"),
            rpc_only=_rpc_only(),
        )
