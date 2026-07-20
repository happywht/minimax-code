"""Local tool registry + DynTool adapter (R166, SDK module harness.rs leaf 2).

Forward-port of grok-build ``xai-computer-hub-sdk/src/harness.rs`` lines
102-320: the in-process tool registry a ``ToolHarness`` owns
(``LocalRegistry`` / ``LocalRegistryInner``) and the thin
``Arc<dyn ToolDyn>`` -> ``ToolHandle`` adapter (``DynToolAdapter``).

Tools registered here resolve in-process -- ``ToolHarness::call``
short-circuits the wire dispatch and invokes the handle directly. The
``ToolHarnessBuilder`` / ``ToolHarness`` actor itself (lines 324-2940) is
a later leaf; this module ports only the registry it seeds + the dyn
adapter ``register_dyn`` relies on, mirroring the R150 ``connection_types``
+ R151 ``connection`` split.

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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from minimax_code.computer_hub_core.resolver import ErasedTool, ToolHandle
from minimax_code.computer_hub_sdk.harness_types import ModelOutputExtractor
from minimax_code.tool_protocol.capabilities import ToolCapabilities
from minimax_code.tool_protocol.ids import ToolId
from minimax_code.tool_runtime.context import ListToolsContext, ToolCallContext
from minimax_code.tool_runtime.tool import (
    ContentBlock,
    Tool,
    ToolDyn,
    ToolStream,
    default_capabilities,
)
from minimax_code.tool_types.types import ToolDescription

if TYPE_CHECKING:
    pass

__all__ = ["DynToolAdapter", "LocalRegistry"]


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
