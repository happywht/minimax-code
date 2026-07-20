"""Local tool registry + DynTool adapter + ToolHarness actor (harness.rs port).

Forward-port of grok-build ``xai-computer-hub-sdk/src/harness.rs`` in leaves:

* R166 (leaf 2): ``LocalRegistry`` + ``LocalRegistryInner`` (lines 102-283,
  in-process tool registry a ToolHarness owns) + ``DynToolAdapter``
  (lines 285-320, ``Arc<dyn ToolDyn>`` -> ``ToolHandle`` adapter).
* R168 (leaf 4): ``ToolHarness`` actor handle (lines 564-566 + Clone 708-714
  + Debug 716-723) + bind state-machine type layer (``BindFuture`` /
  ``PendingBind`` aliases 568-574, ``LazyBind`` 599-602, ``DeferredBind``
  enum 622-625) + ``ToolHarnessInner`` (lines 627-648, 10-field inner state).

The ``ToolHarnessBuilder`` setter layer is R167 (``harness_builder.py``);
``build`` (459-542), ``ToolHarness`` impl methods (725-1775),
``ToolHarnessInner`` impl methods (650-707), ``spawn_pending_bind`` behaviour
(579-594), ``LazyBind::start`` (604-617) and ``Drop`` (1846) land in later
leaves -- they depend on the live ``HubConnection`` / ``ConnectionBorrow``
runtime and the inbound hook/permission/notification dispatch paths.

tokio -> asyncio / Rust -> Python adaptations (no behavior change):

* ``RwLock<IndexMap<ToolId, Arc<dyn ToolHandle>>>`` ->
  ``dict[ToolId, ToolHandle]``. Python ``dict`` preserves insertion order
  (3.7+) so ``list_tools`` returns descriptions in registration order as
  Rust's ``IndexMap`` does. The ``RwLock`` is dropped: the harness runs in
  a single asyncio thread, dict ops are atomic under the GIL, and the
  Rust lock only guards cross-thread hot-add/remove which has no Python
  equivalent here.
* ``DashMap<ToolId, ModelOutputExtractor>`` -> ``dict[ToolId, ...]`` (same
  single-thread reasoning).
* ``Arc<LocalRegistryInner>`` (``#[derive(Clone, Default)]`` on
  ``LocalRegistry``) -> a plain class holding the two dicts. Python
  references are shared, so the ``Arc`` is implicit; ``Clone`` is the
  default reference-copy semantics and ``Default`` is the no-arg ctor.
* ``register<T: Tool + Debug + 'static>`` / ``register_arc<T>`` ->
  ``register(tool)`` / ``register_arc(tool)`` taking any :class:`Tool`.
  Python has no static generics; the ``T: 'static`` bound is moot (all
  Python objects are ``'static``) and ``Debug`` has no runtime role. The
  ``Arc::new(t)`` step collapses -- :class:`ErasedTool.from_arc` stores
  the reference directly. ``register`` delegates to ``register_arc`` for
  call-site fidelity.
* ``register_dyn(Arc<dyn ToolDyn>)`` -> ``register_dyn(tool: ToolDyn)``
  wrapping in :class:`DynToolAdapter`.
* ``register_with_model_output<T>`` (generic ``T::Output:
  DeserializeOwned``) + ``extractor_for<T>()`` are YAGNI here -- Python
  has no serde ``DeserializeOwned``; the per-type extractor factory lands
  only if a consumer needs it. ``register_extractor`` (the
  already-extracted callback path) is ported so ``register_alias`` /
  ``model_output`` keep working.

``DynToolAdapter.capabilities`` / ``should_list`` use ``getattr`` with the
Rust trait default body as fallback (``default_capabilities()`` /
``True``): the Python :class:`ToolDyn` :class:`typing.Protocol` only
declares ``id`` / ``description`` / ``execute`` (R109), so a dyn instance
without those accessors still resolves to the Rust default rather than
raising ``AttributeError``.

ToolHarness actor (R168):

* ``Arc<ToolHarnessInner>`` -> a direct reference (Python references are
  shared, so the ``Arc`` + ``Clone`` cheap-clone collapse to assignment).
  Cooperative ``shutdown`` is preferred; the ``Drop`` fallback lands in a
  later leaf.
* ``BoxFuture<'static, Result<ToolHarness, Arc<str>>>`` ->
  :class:`asyncio.Future` [:class:`ToolHarness`]; the ``Err(Arc<str>)``
  bind failure maps to ``set_exception`` (the future resolves to a
  ``ToolHarness`` on success or raises on failure). ``Shared<BindFuture>``
  collapses to the same :class:`asyncio.Future`: its cached result is
  observable by multiple awaiters, matching Rust's ``Shared`` multi-
  observer semantics.
* ``LazyBind`` (``parking_lot::Mutex<Option<BindFuture>>`` +
  ``std::sync::OnceLock<PendingBind>``) -> a dataclass with ``fut`` /
  ``started`` attributes. Single asyncio thread: the ``Mutex`` is dropped
  and ``OnceLock`` becomes a plain nullable slot (set-once enforced by
  ``LazyBind::start`` in a later leaf).
* ``DeferredBind`` enum (``Eager(PendingBind)`` / ``Lazy(LazyBind)``) ->
  ``EagerBind`` / ``LazyBind`` dataclasses united by the ``DeferredBind``
  type alias (Python has no Rust-style tagged enums; a union of dataclasses
  carries the same two-variant shape).
* ``arc_swap::ArcSwap<Vec<ToolDescription>>`` / ``ArcSwapOption<...>`` ->
  plain attributes (``list`` / nullable). Single asyncio thread: the atomic
  snapshot-swap collapses to attribute assignment.
* ``parking_lot::Mutex<Option<tokio::task::JoinHandle<()>>>`` (discovery
  handle) -> ``asyncio.Task | None`` (single thread, no lock). The
  ``Arc<parking_lot::Mutex<Option<HookRequestHandler>>>`` -> nullable
  attribute (the ``Arc`` let the inbox loop clone the slot; Python
  references share without an explicit ``Arc``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from minimax_code.computer_hub_core.resolver import ErasedTool, ToolHandle
from minimax_code.computer_hub_sdk.connection_borrow import ConnectionBorrow
from minimax_code.computer_hub_sdk.harness_types import (
    HookRequestHandler,
    ModelOutputExtractor,
    SessionBindReport,
    TraceContextProvider,
)
from minimax_code.tool_protocol.capabilities import ToolCapabilities
from minimax_code.tool_protocol.ids import SessionId, ToolId
from minimax_code.tool_runtime.context import (
    ListToolsContext,
    ToolCallContext,
    TypedExtensions,
)
from minimax_code.tool_runtime.tool import (
    ContentBlock,
    Tool,
    ToolDyn,
    ToolStream,
    default_capabilities,
)
from minimax_code.tool_types.types import ToolDescription

__all__ = ["DynToolAdapter", "LocalRegistry", "ToolHarness"]


# ===========================================================================
# LocalRegistry -- in-process registry of tool handles (lines 102-283).
# ===========================================================================
class LocalRegistry:
    """In-process registry of tool handles owned by a ``ToolHarness``.

    Tools registered here resolve in-process: a harness ``call``
    short-circuits the wire dispatch and invokes the handle directly.
    Mirrors ``pub struct LocalRegistry`` (``#[derive(Clone, Default)]``)
    wrapping ``Arc<LocalRegistryInner>`` -- in Python the two dicts live
    on the instance directly (Python references are shared, so the
    ``Arc<LocalRegistryInner>`` + ``Clone`` collapse to plain attribute
    storage) and ``Default`` is the no-arg ``__init__``.

    Mutations preserve insertion order (Python ``dict`` is ordered), so
    :meth:`list_tools` returns descriptions in registration order
    matching Rust's ``RwLock<IndexMap<...>>``. The ``RwLock`` / ``DashMap``
    are dropped: the harness runs in a single asyncio thread and dict ops
    are atomic under the GIL.
    """

    def __init__(self) -> None:
        # Insertion-ordered tool table: ToolId -> ToolHandle. Mirrors
        # RwLock<IndexMap<ToolId, Arc<dyn ToolHandle>>>. Python references
        # are shared, so the Arc step is implicit.
        self._entries: dict[ToolId, ToolHandle] = {}
        # Per-tool model-output extractor map. Mirrors
        # DashMap<ToolId, ModelOutputExtractor>.
        self._extractors: dict[ToolId, ModelOutputExtractor] = {}

    # -- registration ----------------------------------------------------

    def register(self, tool: Tool) -> ToolHandle | None:
        """Register a typed :class:`Tool` (Rust ``register<T>``).

        Subsequent registrations of the same id replace the previous
        handle and return the displaced handle (or ``None`` if fresh).
        Delegates to :meth:`register_arc`; the ``Arc::new(t)`` step
        collapses in Python.
        """
        return self.register_arc(tool)

    def register_arc(self, tool: Tool) -> ToolHandle | None:
        """Register a typed :class:`Tool` already shared (Rust ``register_arc<T>``).

        Wraps via :class:`ErasedTool.from_arc` and inserts; returns the
        displaced handle if any.
        """
        tid = tool.id()
        handle: ToolHandle = ErasedTool.from_arc(tool)
        displaced = self._entries.get(tid)
        self._entries[tid] = handle
        return displaced

    def register_dyn(self, tool: ToolDyn) -> ToolHandle | None:
        """Register a type-erased :class:`ToolDyn` (Rust ``register_dyn``).

        Wraps in :class:`DynToolAdapter` and inserts; returns the
        displaced handle if any.
        """
        tid = tool.id()
        handle: ToolHandle = DynToolAdapter(tool)
        displaced = self._entries.get(tid)
        self._entries[tid] = handle
        return displaced

    def register_alias(self, alias_id: ToolId, target_id: ToolId) -> bool:
        """Register ``alias_id`` pointing at ``target_id``'s handle (Rust ``register_alias``).

        Copies both the handle and any registered extractor. Returns
        ``True`` if the target exists (alias created), ``False``
        otherwise. Used for MCP prefix-fallback: the model may emit the
        bare remote name instead of the full prefixed name.
        """
        handle = self.find(target_id)
        if handle is None:
            return False
        extractor = self._extractors.get(target_id)
        if extractor is not None:
            self._extractors[alias_id] = extractor
        self._entries[alias_id] = handle
        return True

    def register_extractor(
        self, tool_id: ToolId, extractor: ModelOutputExtractor
    ) -> None:
        """Attach a model-output extractor for ``tool_id`` (Rust ``register_extractor``).

        Replaces any previous extractor.
        """
        self._extractors[tool_id] = extractor

    # -- lookup ----------------------------------------------------------

    def find(self, tool_id: ToolId) -> ToolHandle | None:
        """Resolve ``tool_id`` to its in-process handle, if registered (Rust ``find``).

        Returns the handle (a shared reference) so the caller can read
        without holding a lock across an await point.
        """
        return self._entries.get(tool_id)

    def contains(self, tool_id: ToolId) -> bool:
        """``True`` iff ``tool_id`` is currently registered (Rust ``contains``)."""
        return tool_id in self._entries

    def unregister(self, tool_id: ToolId) -> bool:
        """Drop the handle bound to ``tool_id`` (Rust ``unregister``).

        Returns ``True`` iff a matching entry was removed.
        """
        return self._entries.pop(tool_id, None) is not None

    def __len__(self) -> int:
        """Number of tools currently registered (Rust ``len``)."""
        return len(self._entries)

    def is_empty(self) -> bool:
        """``True`` iff no tools are registered (Rust ``is_empty``)."""
        return not self._entries

    # -- model output ----------------------------------------------------

    def model_output(
        self, tool_id: ToolId, output: Any
    ) -> list[ContentBlock] | None:
        """Extract model-facing content blocks from a tool's output (Rust ``model_output``).

        Returns ``None`` if no extractor is registered for ``tool_id``;
        otherwise the extractor's result (which may itself be ``None``).
        """
        extractor = self._extractors.get(tool_id)
        if extractor is None:
            return None
        return extractor(output)

    # -- listing ---------------------------------------------------------

    def list_tools(self, ctx: ListToolsContext) -> list[ToolDescription]:
        """Descriptions of registered tools filtered by ``should_list`` (Rust ``list_tools``).

        Returns descriptions in **insertion order** -- the order tools
        were registered -- so the caller sees the same ordering as the
        config-defined tool list.
        """
        return [
            handle.description(ctx)
            for handle in self._entries.values()
            if handle.should_list(ctx)
        ]


# ===========================================================================
# DynToolAdapter -- Arc<dyn ToolDyn> -> ToolHandle (lines 285-320).
# ===========================================================================
class DynToolAdapter(ToolHandle):
    """Thin adapter from :class:`ToolDyn` to :class:`ToolHandle`.

    Mirrors ``struct DynToolAdapter(Arc<dyn ToolDyn>)`` and its
    ``#[async_trait] impl ToolHandle``. ``ToolDyn::execute`` already
    yields a ``TypedToolOutput`` stream matching ``ToolHandle::execute``,
    so the adapter is trivial delegation. ``capabilities`` and
    ``should_list`` use ``getattr`` with the Rust trait default body as
    fallback because the Python :class:`ToolDyn` :class:`typing.Protocol`
    only declares ``id`` / ``description`` / ``execute``.
    """

    def __init__(self, inner: ToolDyn) -> None:
        self._inner = inner

    def __repr__(self) -> str:
        # Mirrors Rust Debug: debug_struct("DynToolAdapter").field("id", ..)
        try:
            tid = self._inner.id()
        except Exception:
            tid = "<unknown>"
        return f"DynToolAdapter(id={tid!r})"

    def id(self) -> ToolId:
        return self._inner.id()

    def description(self, ctx: ListToolsContext) -> ToolDescription:
        return self._inner.description(ctx)

    def capabilities(self) -> ToolCapabilities:
        cap = getattr(self._inner, "capabilities", None)
        if callable(cap):
            return cap()
        # Rust trait default body: ToolCapabilities::default().
        return default_capabilities()

    def should_list(self, ctx: ListToolsContext) -> bool:
        sl = getattr(self._inner, "should_list", None)
        if callable(sl):
            return sl(ctx)
        # Rust trait default body: true.
        return True

    async def execute(self, ctx: ToolCallContext, args: Any) -> ToolStream:
        # ToolDyn.execute returns the TypedToolOutput stream directly (it
        # is a plain method returning an AsyncIterator, not a coroutine),
        # so no `await` -- mirror Rust `self.0.execute(ctx, args).await`
        # where the .await unwraps ToolDyn's async-fn into the stream.
        return self._inner.execute(ctx, args)


# ===========================================================================
# Bind state-machine type layer (lines 568-625) -- R168.
# ===========================================================================
# An owned, type-erased server-bind future: any awaitable that resolves to the
# server-connected ToolHarness on success, or raises on bind failure (Rust
# ``BoxFuture<'static, Result<ToolHarness, Arc<str>>>``; the ``Err(Arc<str>)``
# stringified bind error maps to the awaited future raising). The bind may be
# a plain coroutine (the common case -- a Rust ``async {}`` block) or an
# asyncio.Future; ``spawn_pending_bind`` wraps it into a ``PendingBind``.
BindFuture: TypeAlias = Awaitable["ToolHarness"]

# Cloneable handle to the deferred server bind; every clone observes the same
# single bind (Rust ``Shared<BindFuture>``). Collapses to the same Future: its
# cached result is observable by multiple awaiters without re-running the bind.
PendingBind: TypeAlias = asyncio.Future["ToolHarness"]


def spawn_pending_bind(bind: Awaitable[ToolHarness]) -> PendingBind:
    """Spawn the owned bind future as a cloneable ``PendingBind`` (Rust ``spawn_pending_bind``).

    Mirrors ``harness.rs:579-594``: ``tokio::spawn(bind)`` runs the bind on the
    runtime; awaiting the ``JoinHandle`` projects a panic (``JoinError``) into
    ``Err(Arc::<str>::from(format!("server bind task panicked: {join_err}")))``;
    the result is then ``.boxed().shared()`` so every clone observes the single
    bind. Used by the ``Eager`` deferred-bind constructor (Rust: raced at
    build time) and :meth:`LazyBind.start` -- a single spawn path keeps the two
    from drifting (harness.rs:576-578 comment).

    tokio -> asyncio adaptation (no behavior change):

    * ``tokio::spawn`` -> a driver task on the running loop via
      :meth:`asyncio.AbstractEventLoop.create_task`. Like its Rust counterpart
      this is a **sync** fn and must run inside a running loop (callers are
      ``build`` / :meth:`LazyBind.start`, invoked from async contexts).
    * ``JoinError`` panic projection -> :meth:`Future.set_exception`. Python has
      no tokio ``JoinError`` / panic distinction: any exception the bind raises
      (auth failure, transport error, sandbox provisioning fault) becomes the
      future's exception, observable by every awaiter. ``CancelledError`` is NOT
      caught here -- asyncio cancellation is a control-flow signal, not a bind
      failure; the driver task is cancelled and the caller's ``CancelledError``
      propagates independently (mirroring how Rust's panic is caught but an
      explicit ``abort`` is not).
    * ``Shared<BindFuture>`` -> the returned :class:`asyncio.Future` itself.
      :class:`asyncio.Future` caches its result, so every clone / a second
      :meth:`LazyBind.start` call observes the same single resolution without
      re-running the bind -- matching ``Shared``'s multi-observer semantics.
    """
    loop = asyncio.get_running_loop()
    pending: asyncio.Future[ToolHarness] = loop.create_future()

    async def _drive() -> None:
        # tokio::spawn(bind).await -> Ok(result) => result,
        # Err(join_err) => Err(Arc::<str>::from("server bind task panicked: ...")).
        try:
            harness = await bind
        except Exception as exc:
            # Rust projects JoinError (panic) -> Err(Arc<str>). Python has no
            # panic concept; any bind exception becomes the future's exception.
            # CancelledError is asyncio's cancellation signal, not a bind
            # failure, and is left to propagate (it is BaseException, not
            # Exception, so this except does not swallow it).
            if not pending.done():
                pending.set_exception(exc)
        else:
            if not pending.done():
                pending.set_result(harness)

    loop.create_task(_drive())
    return pending


@dataclass
class LazyBind:
    """A bind future kept unspawned until the first ``await_bound`` (Rust ``LazyBind``).

    The server connection -- and the sandbox provisioning it performs -- is
    deferred to the first remote tool dispatch. ``fut`` is the owned bind
    future, taken once when ``start`` spawns it; ``started`` is the shared
    handle, set once on the first ``start`` call.

    ``parking_lot::Mutex<Option<BindFuture>>`` -> ``fut: BindFuture | None``
    (single asyncio thread: the ``Mutex`` is dropped; the ``take`` semantics
    are realised by nulling the slot in ``start``, a later leaf).
    ``std::sync::OnceLock<PendingBind>`` -> ``started: PendingBind | None``
    (set-once enforced by ``start``).
    """

    fut: BindFuture | None
    started: PendingBind | None = None

    def start(self) -> PendingBind:
        """Spawn the deferred bind exactly once (Rust ``LazyBind::start``).

        Mirrors ``harness.rs:604-617``::

            self.started.get_or_init(|| {
                let fut = self.fut.lock().take().expect(
                    "LazyBind future taken more than once",
                );
                spawn_pending_bind(fut)
            }).clone()

        ``OnceLock::get_or_init`` -> ``if self.started is None`` (single asyncio
        thread: no race, so a plain None-check enforces set-once). ``fut.lock()
        .take()`` -> ``fut = self.fut`` followed by ``self.fut = None`` (the
        ``parking_lot::Mutex`` is dropped; the ``take`` semantics are nulling
        the slot). ``.expect("LazyBind future taken more than once")`` -> an
        ``assert`` carrying the same message. ``spawn_pending_bind(fut)`` -> the
        module-level port above. The returned clone is the same ``asyncio.Future``
        reference -- Python references are shared, so ``Clone`` is identity.
        """
        if self.started is None:
            assert self.fut is not None, "LazyBind future taken more than once"
            fut = self.fut
            self.fut = None  # take (consume the owned bind future)
            self.started = spawn_pending_bind(fut)
        return self.started


@dataclass
class EagerBind:
    """Eager deferred bind: spawned at construction, races sampling (Rust ``DeferredBind::Eager``)."""

    pending: PendingBind


# Deferred server bind (Rust ``enum DeferredBind``): ``Eager`` is spawned at
# construction; ``Lazy`` spawns on the first ``await_bound``. Python has no
# tagged enums -- a union of the two dataclasses carries the same shape, and
# ``isinstance`` dispatch replaces Rust's ``match``.
DeferredBind: TypeAlias = EagerBind | LazyBind


# ===========================================================================
# ToolHarnessInner -- inner state of a ToolHarness (lines 627-648) -- R168.
# ===========================================================================
@dataclass
class ToolHarnessInner:
    """Inner state of a :class:`ToolHarness` (Rust ``struct ToolHarnessInner``).

    Held behind ``Arc<ToolHarnessInner>`` in Rust (``ToolHarness.inner``);
    in Python the :class:`ToolHarness` holds it directly (reference sharing
    is the ``Arc``). The ``arc_swap`` / ``parking_lot`` concurrency primitives
    collapse to plain attributes -- the harness runs in a single asyncio
    thread, so an atomic snapshot-swap is plain attribute assignment and a
    mutex-guarded slot is a nullable attribute.

    Construction is performed by ``build`` (a later leaf); the dataclass gives
    the actor skeleton its field layout now so the bind / dispatch paths can
    be ported against it.
    """

    # Session id bound on the underlying connection. Required.
    session: SessionId
    # Default extensions merged into every ToolCallContext before dispatch.
    default_extensions: TypedExtensions
    # In-process tool registry. Non-Option in Rust; empty by default, populated
    # by the builder / live discovery.
    local_registry: LocalRegistry = field(default_factory=LocalRegistry)
    # None for local-only harnesses (no server connection).
    borrow: ConnectionBorrow | None = None
    # Host-supplied W3C traceparent source (Rust Option<TraceContextProvider>).
    trace_context_provider: TraceContextProvider | None = None
    # Discovered remote tool descriptions. arc_swap::ArcSwap<Vec<ToolDescription>>
    # -> list (single thread: atomic swap = attribute assignment).
    remote_tools: list[ToolDescription] = field(default_factory=list)
    # Most recent session-bind report. arc_swap::ArcSwapOption<SessionBindReport>
    # -> nullable attribute.
    last_bind_report: SessionBindReport | None = None
    # Handle to the background discovery task. parking_lot::Mutex<
    # Option<tokio::task::JoinHandle<()>>> -> asyncio.Task | None (single
    # thread, no lock).
    discovery_handle: asyncio.Task[Any] | None = None
    # Deferred server bind (prompt-before-bind). Eager races sampling; Lazy
    # defers provisioning to the first remote tool dispatch.
    pending_bind: DeferredBind | None = None
    # Sink for inbound reverse-direction hook requests. Arc<parking_lot::Mutex<
    # Option<HookRequestHandler>>> -> nullable attribute (the Arc let the inbox
    # loop clone this slot alone; Python references share without it).
    hook_request_handler: HookRequestHandler | None = None


# ===========================================================================
# ToolHarness -- actor handle (lines 564-566 + Clone 708-714 + Debug 716-723).
# ===========================================================================
class ToolHarness:
    """Harness attached to a pooled :class:`HubConnection` (Rust ``ToolHarness``).

    Clone-cheap in Rust (``Arc`` bump); in Python assignment shares the
    inner reference (the ``Arc`` is implicit). Cooperative teardown via
    ``shutdown`` is preferred; the ``Drop`` impl schedules a best-effort
    asynchronous cleanup as a fallback when no explicit shutdown ran,
    firing at most once across all clones -- the ``Drop`` lands in a
    later leaf.

    Mirrors Rust ``Debug``: surfaces ``session`` + ``local_tool_count`` and
    is otherwise non-exhaustive (sensitive inner state is not exposed).
    """

    __slots__ = ("_inner",)

    def __init__(self, inner: ToolHarnessInner) -> None:
        self._inner = inner

    @property
    def inner(self) -> ToolHarnessInner:
        """The underlying :class:`ToolHarnessInner` (shared across clones)."""
        return self._inner

    def __repr__(self) -> str:
        # Mirrors Rust Debug: debug_struct("ToolHarness")
        # .field("session", ..).field("local_tool_count", ..)
        # .finish_non_exhaustive().
        return (
            f"ToolHarness(session={self._inner.session!r}, "
            f"local_tool_count={len(self._inner.local_registry)}, ...)"
        )
