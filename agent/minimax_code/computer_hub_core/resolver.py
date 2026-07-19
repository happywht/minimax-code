"""Tool resolution strategy + type-erased dispatch wrapper (R117).

Fusion of grok-build's ``xai-computer-hub-core/src/resolver.rs``. This
module is the **third leaf** of the ``xai-computer-hub-core`` crate
(R115 landed ``transport``, R116 landed ``registry``; ``inner`` / ``local``
/ ``remote`` follow). It owns four symbols:

- :class:`ResolvedTool` — the value :meth:`ToolRegistry.find_tool` returns
  (closes the R116 ``registry`` <-> ``resolver`` cycle by defining the
  type R116 only name-checked under ``TYPE_CHECKING``).
- :class:`ToolHandle` — the object-safe dispatch surface a resolved tool
  exposes (the ``Arc<dyn ToolHandle>`` the resolver stores).
- :class:`ErasedTool` — the blanket ``impl<T: Tool> ToolHandle for T``
  adapter: it drives a typed :class:`Tool`'s stream and re-encodes every
  terminal item into a :class:`TypedToolOutput`.
- :class:`CompoundResolver` — local-first, remote-fallback resolution
  strategy + the ``resolve_and_dispatch`` entry point the router calls.

R116 cycle, closed
------------------

The ``registry`` <-> ``resolver`` dependency is a true bidirectional cycle
in Rust (``resolver`` holds ``Arc<dyn ToolRegistry>``, ``registry``
returns ``Option<ResolvedTool>``). R116 broke it the R109 way — a
``TYPE_CHECKING``-only import of :class:`ResolvedTool` plus
``from __future__ import annotations`` string deferral. This leaf defines
the real :class:`ResolvedTool`, so R116's forward reference now resolves.
No runtime import from ``registry`` is needed here at module load (the
``registry`` -> ``resolver`` edge stays ``TYPE_CHECKING``-only); only the
reverse edge is real, and it is one-directional (``resolver`` imports
``ToolRegistry`` from ``registry``, which never imports ``resolver`` at
runtime). The cycle is broken cleanly.

trait ToolHandle -> abc.ABC (object-safe, NOT a Protocol)
---------------------------------------------------------

Rust's ``#[async_trait] pub trait ToolHandle: Send + Sync + Debug`` is
held behind ``Arc<dyn ToolHandle>`` — that is object-safe dynamic
dispatch, the same family as :class:`Transport` (R115),
:class:`ToolRegistry` (R116), and :class:`ToolDispatch` (R113). So
:class:`ToolHandle` lands as :class:`abc.ABC`, NOT :class:`Protocol`.

This is deliberately distinct from R109's :class:`ToolDyn`
(:class:`typing.Protocol`). :class:`ToolDyn` is a structural seam — a
duck-typed contract with no dyn-erased caller; :class:`ToolHandle` is the
object-safe surface the resolver stores and dispatches through. The two
shapes overlap (:class:`ToolHandle` is a near-superset — it adds
:meth:`capabilities` and the concrete :meth:`should_list` default) but
serve different roles, so they stay separate types rather than one
Protocol masquerading as both.

The four abstract methods (:meth:`id`, :meth:`description`,
:meth:`capabilities`, :meth:`execute`) plus one **concrete** provided
method (:meth:`should_list`, default ``True``) follow the R113
concrete-default pattern (:meth:`ToolDispatch.call_terminal`,
:meth:`ToolRegistry.get_server_id`): subclasses MAY override
:meth:`should_list` to hide a tool from listing while still resolving it.

ErasedTool — the stream-mapping the R109 docstring deferred
-----------------------------------------------------------

R109 landed :class:`Tool` / :class:`ToolDyn` but explicitly marked the
blanket stream-mapping as YAGNI ("until a caller drives it").
:class:`ErasedTool` is that caller. Its job is twofold:

1. **Adapt** a typed :class:`Tool` (which yields
   :class:`ToolStreamItem` carrying its own typed ``Output``) to the
   type-erased :class:`ToolHandle` surface (which yields
   :class:`ToolStreamItem` carrying :class:`TypedToolOutput`).
2. **Re-encode** each terminal success item into a
   :class:`TypedToolOutput` — serialise the typed output, derive
   ``model_output`` (custom blocks if the output implements
   :class:`ToolOutput`, else :func:`extract_content_blocks`), and attach
   the optional chat-completion card.

Rust's ``ErasedTool<T>`` needs the type parameter for two reasons:
``serde_json::from_value::<T::Args>`` decodes the incoming args, and
``serde_json::to_value(&T::Output)`` encodes the typed output. Python is
dynamically typed, so:

- **Args decode is a no-op.** R109's :class:`Tool.execute` already
  accepts ``Any`` (the dispatcher hands it the decoded value); the Rust
  ``serde_json::from_value`` step has no Python counterpart, and the
  ``Err(InvalidArguments)`` decode-failure path cannot occur here (a
  concrete :class:`Tool` that wants typed args is responsible for its own
  decoding and its own :class:`ToolError`). :class:`ErasedTool.execute`
  forwards ``args`` straight through.
- **Output encode is real.** :class:`TypedToolOutput.value` needs a JSON
  value; the typed output may be a primitive, a dataclass, a pydantic
  model, or a plain object. :func:`_encode_to_json_value` walks those
  shapes. An encode failure maps to ``ToolError.custom("output_encoding",
  ...)`` exactly as the Rust ``to_value`` failure does.

The terminal-success re-encoding implements the Rust
custom-or-extract rule in full: if the typed output exposes a
``model_output()`` that returns a non-empty block list, those blocks win
(the tool rendered itself); otherwise :func:`extract_content_blocks`
derives blocks from the serialised value. :meth:`TypedToolOutput.from_value`
covers only the extract branch, so :class:`ErasedTool` cannot delegate to
it — it builds the :class:`TypedToolOutput` directly.

``Progress`` and terminal-error items pass through unchanged (a progress
frame has no payload to encode; an error is already a :class:`ToolError`).

Field access replaces Rust accessor methods
-------------------------------------------

Rust's :class:`ResolvedTool` and :class:`CompoundResolver` expose
``registration()`` / ``handle()`` and ``local()`` / ``remote()`` *methods*
that return references. In Python a field IS a readable attribute, so
the landing collapses method-into-field and uses attribute access
directly (``resolved.handle``, ``resolver.local``) — the same collapse
R109 applied to :class:`TypedToolOutput`'s ``model_output`` /
``chat_completion_output`` (fields that ARE the ``ToolOutput`` trait
methods). This keeps the surface Pythonic without losing the Rust
semantics; callers that read ``resolved.handle().execute()`` in Rust read
``resolved.handle.execute()`` here.

Object-safety bounds (``Send + Sync + Debug``) have no Python equivalent
under the GIL — Python objects are already reference-shared; ``Debug`` is
a soft expectation that implementations provide a ``__repr__``.
"""

from __future__ import annotations

import abc
import dataclasses
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from minimax_code.computer_hub_core.registry import ToolRegistry
from minimax_code.tool_protocol import (
    SessionId,
    ToolCapabilities,
    ToolId,
    ToolRegistration,
)
from minimax_code.tool_runtime import (
    ListToolsContext,
    Tool,
    ToolCallContext,
    ToolError,
    ToolStream,
    ToolStreamItem,
    TypedToolOutput,
    extract_content_blocks,
    terminal_only,
)
from minimax_code.tool_types import ToolDescription

__all__ = [
    "CompoundResolver",
    "ErasedTool",
    "ResolvedTool",
    "ToolHandle",
]


# ---------------------------------------------------------------------------
# ResolvedTool — the value find_tool returns (closes the R116 cycle).
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class ResolvedTool:
    """A tool the resolver has located, paired with its registration.

    Rust ``enum ResolvedTool { Local { tool, registration }, Remote {
    proxy, registration } }`` with ``#[derive(Debug, Clone)]`` (no
    ``PartialEq`` — ``Arc<dyn ToolHandle>`` is not ``Eq``). Lands as one
    dataclass with a ``variant`` discriminator (``"local"`` / ``"remote"``)
    plus a unified :attr:`handle` (the ``tool`` / ``proxy`` field collapsed
    into one) and the shared :attr:`registration`. ``eq=False`` mirrors the
    absent ``PartialEq``: two resolved tools compare by identity only,
    even when their fields are equal (the handle is an arbitrary
    :class:`ToolHandle` with no value equality).

    Rust's ``registration()`` / ``handle()`` accessor methods collapse into
    attribute access — ``resolved.handle`` / ``resolved.registration`` —
    per the module-level field-access convention.
    """

    #: Discriminator — ``"local"`` (in-process tool) or ``"remote"``
    #: (forwarded via a connection proxy).
    variant: str
    #: The dispatch surface (Rust ``tool`` for ``Local``, ``proxy`` for
    #: ``Remote`` — unified into one field).
    handle: ToolHandle
    #: The registration metadata captured when the tool was registered.
    registration: ToolRegistration

    @classmethod
    def Local(
        cls, handle: ToolHandle, registration: ToolRegistration
    ) -> ResolvedTool:
        """``Local { tool, registration }`` variant constructor."""
        return cls(variant="local", handle=handle, registration=registration)

    @classmethod
    def Remote(
        cls, handle: ToolHandle, registration: ToolRegistration
    ) -> ResolvedTool:
        """``Remote { proxy, registration }`` variant constructor."""
        return cls(variant="remote", handle=handle, registration=registration)


# ---------------------------------------------------------------------------
# ToolHandle — object-safe dispatch surface (abc.ABC, NOT Protocol).
# ---------------------------------------------------------------------------


class ToolHandle(abc.ABC):
    """Object-safe dispatch surface a resolved tool exposes.

    Rust ``#[async_trait] pub trait ToolHandle: Send + Sync + Debug`` held
    behind ``Arc<dyn ToolHandle>`` -> :class:`abc.ABC` (object-safe dynamic
    dispatch, same family as :class:`Transport` / :class:`ToolRegistry`).
    Deliberately distinct from R109's :class:`ToolDyn`
    :class:`typing.Protocol` (a structural seam) — :class:`ToolHandle` is
    the stored, dyn-erased surface.

    Subclasses MUST implement :meth:`id`, :meth:`description`,
    :meth:`capabilities`, and :meth:`execute`. :meth:`should_list` carries
    a default body (returns ``True``) and is the one non-abstract provided
    method — subclasses MAY override it to hide a tool from listing while
    still resolving it.
    """

    @abc.abstractmethod
    def id(self) -> ToolId:
        """Stable tool identity (Rust ``fn id(&self) -> ToolId``)."""
        ...

    @abc.abstractmethod
    def description(self, ctx: ListToolsContext) -> ToolDescription:
        """Tool description as it should appear in ``session`` (Rust sync ``fn``)."""
        ...

    @abc.abstractmethod
    def capabilities(self) -> ToolCapabilities:
        """Static capability flags (Rust sync ``fn capabilities``)."""
        ...

    def should_list(self, ctx: ListToolsContext) -> bool:
        """Whether this tool appears in a list_tools response.

        Concrete default (Rust default body ``true``): subclasses MAY
        override to hide a tool from listing while still resolving it.
        """
        return True

    @abc.abstractmethod
    async def execute(
        self, ctx: ToolCallContext, args: Any
    ) -> ToolStream:
        """Dispatch the tool call (Rust ``async fn execute``).

        Returns a :type:`ToolStream` of :class:`ToolStreamItem` carrying
        :class:`TypedToolOutput` (Rust ``ToolStream<TypedToolOutput>``).
        ``args`` is the JSON value (Rust ``serde_json::Value`` -> ``Any``);
        a concrete handle that wants typed args is responsible for its own
        decoding.
        """
        ...


# ---------------------------------------------------------------------------
# Encoding helpers — serialise a typed output + read the ToolOutput trait.
# ---------------------------------------------------------------------------


def _encode_to_json_value(out: Any) -> Any:
    """Serialise a typed tool output to a JSON-compatible value.

    Rust's ``serde_json::to_value(&out)`` walks the ``Serialize`` impl.
    Python's dynamic typing means the typed output may be any of several
    shapes; this helper walks them in priority order:

    1. JSON primitives (``None`` / ``bool`` / ``int`` / ``float`` / ``str``)
       pass through unchanged.
    2. ``list`` / ``dict`` recurse element-wise (so nested dataclasses /
       pydantic models inside a container still serialise).
    3. ``@dataclasses.dataclass`` instances -> :func:`dataclasses.asdict`.
    4. pydantic v2 models -> ``model_dump()``.
    5. Plain objects with ``__dict__`` -> that mapping.
    6. Fallback: the :func:`repr` string (never raises).

    Returns the serialised value; never raises (matching serde_json's
    infallible behaviour for these shapes — a true encode failure, which
    Python cannot easily produce for these branches, would surface from
    the caller's :class:`ToolOutput` methods instead).
    """
    if out is None or isinstance(out, (bool, int, float, str)):
        return out
    if isinstance(out, list):
        return [_encode_to_json_value(item) for item in out]
    if isinstance(out, dict):
        return {key: _encode_to_json_value(val) for key, val in out.items()}
    if dataclasses.is_dataclass(out) and not isinstance(out, type):
        return dataclasses.asdict(out)
    dump = getattr(out, "model_dump", None)
    if callable(dump):
        return dump()
    if hasattr(out, "__dict__"):
        return {
            key: _encode_to_json_value(val)
            for key, val in vars(out).items()
            if not key.startswith("_")
        }
    return repr(out)


def _model_output_of(out: Any) -> list:
    """Read the ``ToolOutput::model_output`` impl from a typed output.

    Returns the custom block list if ``out`` exposes a callable
    ``model_output()`` returning a list; otherwise an empty list (the
    Rust default body, signalling "use auto-extraction"). A
    ``model_output()`` that raises is treated as the default — the caller
    then falls back to :func:`extract_content_blocks`.
    """
    method = getattr(out, "model_output", None)
    if callable(method):
        try:
            result = method()
        except Exception:
            return []
        if isinstance(result, list):
            return result
    return []


def _chat_completion_output_of(out: Any) -> Any:
    """Read the ``ToolOutput::chat_completion_output`` impl from a typed output.

    Returns the chat-completion card if ``out`` exposes a callable
    ``chat_completion_output()``; otherwise ``None`` (the Rust default
    body, no card). A raising impl is treated as the default.
    """
    method = getattr(out, "chat_completion_output", None)
    if callable(method):
        try:
            return method()
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# ErasedTool — the blanket impl<T: Tool> ToolHandle adapter.
# ---------------------------------------------------------------------------


class ErasedTool(ToolHandle):
    """Type-erased :class:`ToolHandle` adapter over a typed :class:`Tool`.

    Rust ``ErasedTool<T>`` wraps ``Arc<T>`` and implements ``ToolHandle``
    by delegating the sync metadata accessors and driving the typed
    :meth:`Tool.execute` stream, re-encoding each terminal success item
    into a :class:`TypedToolOutput`. The Python landing keeps the same
    shape: ``inner`` holds the typed :class:`Tool` (Python references are
    already shared, so the ``Arc`` is implicit), and :meth:`execute`
    runs the stream-mapping the R109 docstring deferred.

    ``from_arc`` / ``new`` mirror the Rust constructors. In Rust ``new(t)``
    wraps ``Arc::new(t)`` while ``from_arc(arc)`` takes a pre-built ``Arc``;
    in Python both simply store the reference (Python has no ``Arc`` step),
    so they collapse to the same storage but are kept distinct for
    call-site fidelity with Rust.
    """

    def __init__(self, inner: Tool) -> None:
        self.inner = inner

    @classmethod
    def from_arc(cls, inner: Tool) -> ErasedTool:
        """Build from an already-shared reference (Rust ``from_arc``)."""
        return cls(inner=inner)

    @classmethod
    def new(cls, inner: Tool) -> ErasedTool:
        """Build from a fresh instance (Rust ``new`` = ``from_arc(Arc::new(t))``)."""
        return cls(inner=inner)

    def id(self) -> ToolId:
        return self.inner.id()

    def description(self, ctx: ListToolsContext) -> ToolDescription:
        return self.inner.description(ctx)

    def capabilities(self) -> ToolCapabilities:
        return self.inner.capabilities()

    def should_list(self, ctx: ListToolsContext) -> bool:
        # Delegate (Rust ``self.inner.should_list(ctx)``) rather than use
        # the ToolHandle default — the typed Tool may hide itself.
        return self.inner.should_list(ctx)

    async def execute(self, ctx: ToolCallContext, args: Any) -> ToolStream:
        # Drive the typed Tool's stream and re-tag each item. R109's
        # Tool.execute returns the typed AsyncIterator directly (args are
        # already Any — no serde decode step, no InvalidArguments path
        # here). The tool's id is captured once so every terminal item
        # carries it (Rust reads ``self.inner.id()`` outside the loop).
        return self._erase_stream(self.inner.execute(ctx, args), self.inner.id())

    async def _erase_stream(
        self,
        typed_stream: AsyncIterator[ToolStreamItem[Any]],
        tool_id: ToolId,
    ) -> AsyncIterator[ToolStreamItem[TypedToolOutput]]:
        """Re-encode a typed stream into a type-erased stream.

        - ``Progress`` items pass through unchanged (no payload to encode).
        - Terminal ``Err`` items (a :class:`ToolError`) pass through
          unchanged (already the erased error type).
        - Terminal ``Ok`` items (the typed output) are re-encoded: the
          output is serialised to a JSON value, ``model_output`` is the
          custom block list if the output implements
          :class:`ToolOutput` (else :func:`extract_content_blocks`), and
          the optional chat-completion card is attached. An encode failure
          surfaces as ``ToolError.custom("output_encoding", ...)``.
        """
        async for item in typed_stream:
            if item.kind == "progress":
                yield item
                continue
            if item.is_error():
                # Terminal Err — already a ToolError; pass through.
                yield item
                continue
            # Terminal Ok — re-encode the typed output.
            out = item.terminal
            try:
                value = _encode_to_json_value(out)
                custom = _model_output_of(out)
                model_output = (
                    custom if custom else extract_content_blocks(value)
                )
                chat_completion_output = _chat_completion_output_of(out)
            except Exception as exc:  # noqa: BLE001 - mirror serde encode failure
                yield ToolStreamItem.Terminal(
                    ToolError.custom("output_encoding", str(exc))
                )
                continue
            yield ToolStreamItem.Terminal(
                TypedToolOutput(
                    tool_id=tool_id,
                    value=value,
                    model_output=model_output,
                    chat_completion_output=chat_completion_output,
                )
            )

    def __repr__(self) -> str:
        return f"ErasedTool(inner={self.inner!r})"


# ---------------------------------------------------------------------------
# CompoundResolver — local-first, remote-fallback resolution + dispatch.
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class CompoundResolver:
    """Local-first, remote-fallback tool resolution strategy.

    Rust ``struct CompoundResolver { local: Arc<dyn ToolRegistry>, remote:
    Option<Arc<dyn ToolRegistry>> }`` with ``#[derive(Debug)]`` (no
    ``PartialEq``, no ``Clone`` — it holds trait objects). Lands as a
    dataclass with ``eq=False`` (identity-only ``__eq__``, mirroring the
    absent ``PartialEq``). :meth:`resolve` is local-first: a hit on the
    local registry shadows the remote one; the remote registry is only
    consulted when the local lookup misses AND a remote is configured.

    Rust's ``local()`` / ``remote()`` accessor methods collapse into
    attribute access — ``resolver.local`` / ``resolver.remote`` — per the
    module-level field-access convention. ``local_only`` / ``compound``
    are the classmethod constructors (Rust ``Self::local_only`` /
    ``Self::compound``).
    """

    #: The local (in-process) registry. Always present.
    local: ToolRegistry
    #: The remote (connection-forwarded) registry. ``None`` for a
    #: local-only resolver.
    remote: ToolRegistry | None = None

    @classmethod
    def local_only(cls, local: ToolRegistry) -> CompoundResolver:
        """Build a resolver with no remote plane (Rust ``Self::local_only``)."""
        return cls(local=local)

    @classmethod
    def compound(
        cls, local: ToolRegistry, remote: ToolRegistry
    ) -> CompoundResolver:
        """Build a resolver with both planes (Rust ``Self::compound``)."""
        return cls(local=local, remote=remote)

    def resolve(self, session: SessionId, tool_id: ToolId) -> ResolvedTool | None:
        """Resolve ``(session, tool_id)`` local-first.

        Returns the local hit if one exists (it shadows any remote
        registration); otherwise consults the remote registry when
        configured. Returns ``None`` when neither plane has an active
        resolution.
        """
        hit = self.local.find_tool(session, tool_id)
        if hit is not None:
            return hit
        if self.remote is not None:
            return self.remote.find_tool(session, tool_id)
        return None

    async def resolve_and_dispatch(
        self,
        session: SessionId,
        tool_id: ToolId,
        args: Any,
        ctx: ToolCallContext,
    ) -> ToolStream:
        """Resolve a tool then dispatch it in one call (Rust ``async fn``).

        On a hit, hands ``args`` / ``ctx`` to the resolved handle's
        :meth:`ToolHandle.execute`. On a miss, returns a single-item
        terminal stream carrying ``ToolError::not_found`` — the caller
        sees one stream shape regardless of whether resolution succeeded.
        """
        resolved = self.resolve(session, tool_id)
        if resolved is not None:
            return await resolved.handle.execute(ctx, args)
        return terminal_only(
            ToolError.not_found(tool_id, f"tool not found: {tool_id}")
        )
