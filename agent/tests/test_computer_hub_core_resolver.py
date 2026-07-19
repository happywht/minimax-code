"""Tests for the R117 computer_hub_core resolver module.

Covers the migration of ``xai-computer-hub-core/src/resolver.rs``. The
Rust source carries no inline ``#[test]`` block; these are Python-style
semantic-equivalence checks across the four symbols:

- ``ResolvedTool``: discriminator dataclass (``eq=False`` — Rust derive is
  ``Debug, Clone`` only, no ``PartialEq`` because ``Arc<dyn ToolHandle>``
  is not ``Eq``); ``Local`` / ``Remote`` classmethod constructors set the
  ``variant`` discriminator; Rust ``registration()`` / ``handle()``
  accessor methods collapse into attribute access (R109 field-as-accessor
  convention).
- ``trait ToolHandle`` -> ``abc.ABC`` (object-safe, ``Arc<dyn
  ToolHandle>``): four abstract methods (``id`` / ``description`` /
  ``capabilities`` / ``execute``) + one concrete default (``should_list``
  -> ``True``); a subclass missing any abstract method stays abstract.
- ``ErasedTool<T>``: the blanket ``impl<T: Tool> ToolHandle for T``
  adapter. ``from_arc`` / ``new`` mirror the Rust constructors; sync
  metadata + ``should_list`` delegate to ``inner``; ``execute`` drives
  the typed stream and re-encodes each item — ``Progress`` passthrough,
  terminal ``Err`` passthrough, terminal ``Ok`` -> ``TypedToolOutput``
  (custom ``model_output`` wins over ``extract_content_blocks``, optional
  chat-completion card attached), encode failure ->
  ``ToolError.custom("output_encoding", ...)``.
- ``CompoundResolver``: ``eq=False`` dataclass (Rust ``#[derive(Debug)]``
  only); ``local_only`` / ``compound`` constructors; ``resolve`` is
  local-first (local hit shadows remote); ``resolve_and_dispatch`` hits
  forward to the handle's ``execute``, misses return a single-item
  terminal stream carrying ``ToolError::not_found``.
"""

from __future__ import annotations

import abc
import inspect
from typing import Any

import pytest

from minimax_code.computer_hub_core import (
    CompoundResolver,
    ErasedTool,
    ResolvedTool,
    ToolHandle,
)
from minimax_code.tool_protocol import SessionId, ToolId
from minimax_code.tool_runtime import (
    ListToolsContext,
    ToolCallContext,
    ToolError,
    ToolStreamItem,
    TypedToolOutput,
)

# ---------------------------------------------------------------------------
# Sentinels / fixtures.
# ---------------------------------------------------------------------------

_DESCRIPTION = object()
_CAPABILITIES = object()
_REGISTRATION = object()
_BLOCK = object()
_CARD = object()
_CTX = object()  # ErasedTool / CompoundResolver tests thread ctx opaquely


def _tid() -> ToolId:
    return ToolId("search__web")


def _tid2() -> ToolId:
    return ToolId("fs__read")


def _session() -> SessionId:
    return SessionId("session-3")


def _reg() -> Any:
    return _REGISTRATION


def _progress_item(payload: Any = None) -> ToolStreamItem:
    """A ``Progress`` variant (Rust ``ToolStreamItem::Progress``)."""
    return ToolStreamItem(
        kind="progress", progress=payload if payload is not None else object()
    )


def _terminal_ok_item(output: Any) -> ToolStreamItem:
    """A terminal ``Ok`` variant carrying a typed output."""
    return ToolStreamItem(kind="terminal", terminal=output)


def _terminal_err_item(error: ToolError) -> ToolStreamItem:
    """A terminal ``Err`` variant carrying a :class:`ToolError`."""
    return ToolStreamItem(kind="terminal", terminal=error)


async def _stream(items: list[ToolStreamItem]) -> Any:
    """Yield ``items`` as an async generator (R109 Tool.execute shape)."""
    for item in items:
        yield item


class _TypedTool:
    """Mock :class:`Tool` (R109 Protocol shape) for ErasedTool tests.

    Only the five members ErasedTool touches are implemented (``id`` /
    ``description`` / ``capabilities`` / ``should_list`` / ``execute``);
    the rest of the Protocol is irrelevant because ErasedTool never calls
    them.
    """

    def __init__(
        self,
        tool_id: ToolId,
        items: list[ToolStreamItem],
        *,
        should_list_value: bool = True,
    ) -> None:
        self._id = tool_id
        self._items = items
        self._should_list = should_list_value
        self.execute_calls: list[tuple[Any, Any]] = []

    def id(self) -> ToolId:
        return self._id

    def description(self, ctx: ListToolsContext) -> Any:
        return _DESCRIPTION

    def capabilities(self) -> Any:
        return _CAPABILITIES

    def should_list(self, ctx: ListToolsContext) -> bool:
        return self._should_list

    def execute(self, ctx: ToolCallContext, args: Any) -> Any:
        self.execute_calls.append((ctx, args))
        return _stream(list(self._items))


class _RecordingHandle(ToolHandle):
    """Minimal :class:`ToolHandle` impl recording ``execute`` calls."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self.execute_calls: list[tuple[Any, Any]] = []

    def id(self) -> ToolId:
        return _tid()

    def description(self, ctx: ListToolsContext) -> Any:
        return _DESCRIPTION

    def capabilities(self) -> Any:
        return _CAPABILITIES

    async def execute(self, ctx: ToolCallContext, args: Any) -> Any:
        self.execute_calls.append((ctx, args))
        return self._stream


class _Registry:
    """Mock :class:`ToolRegistry` — only ``find_tool`` is exercised."""

    def __init__(self, resolved: Any = None) -> None:
        self._resolved = resolved
        self.find_calls: list[tuple[Any, ToolId]] = []

    def find_tool(self, session: SessionId, tool: ToolId) -> Any:
        self.find_calls.append((session, tool))
        return self._resolved


# ===========================================================================
# ResolvedTool — discriminator dataclass, eq=False, field accessors.
# ===========================================================================


def test_resolvedtool_local_constructor_sets_variant():
    h = _RecordingHandle(None)
    r = _reg()
    resolved = ResolvedTool.Local(h, r)
    assert resolved.variant == "local"
    assert resolved.handle is h
    assert resolved.registration is r


def test_resolvedtool_remote_constructor_sets_variant():
    h = _RecordingHandle(None)
    r = _reg()
    resolved = ResolvedTool.Remote(h, r)
    assert resolved.variant == "remote"
    assert resolved.handle is h
    assert resolved.registration is r


def test_resolvedtool_field_access_handle_and_registration():
    # Rust registration() / handle() accessor methods collapse into
    # attribute access (R109 field-as-accessor convention).
    h = _RecordingHandle(None)
    r = _reg()
    resolved = ResolvedTool.Local(h, r)
    assert resolved.handle is resolved.handle  # attribute, not method call
    assert resolved.registration is resolved.registration


def test_resolvedtool_no_partialeq_identity_only():
    # Rust derive is `Debug, Clone` (no PartialEq/Eq) -> eq=False -> two
    # resolved tools with identical fields are NOT equal (identity only).
    h = _RecordingHandle(None)
    r = _reg()
    a = ResolvedTool.Local(h, r)
    b = ResolvedTool.Local(h, r)
    assert a != b
    assert a == a


def test_resolvedtool_local_and_remote_distinct_variants():
    h = _RecordingHandle(None)
    r = _reg()
    local = ResolvedTool.Local(h, r)
    remote = ResolvedTool.Remote(h, r)
    assert local.variant == "local"
    assert remote.variant == "remote"
    # Different objects -> not equal (eq=False); variant is the discriminator.
    assert local != remote


# ===========================================================================
# ToolHandle — abc.ABC object-safety shape.
# ===========================================================================


def test_toolhandle_is_abstract_cannot_instantiate():
    with pytest.raises(TypeError):
        ToolHandle()


def test_toolhandle_has_four_abstract_methods():
    # id / description / capabilities / execute are abstract; should_list
    # is the concrete default and NOT in __abstractmethods__.
    abstract = set(ToolHandle.__abstractmethods__)
    assert abstract == {"id", "description", "capabilities", "execute"}


def test_toolhandle_should_list_is_concrete_default_true():
    # The one concrete provided method — not abstract, default body True.
    assert "should_list" not in ToolHandle.__abstractmethods__
    assert getattr(ToolHandle.should_list, "__isabstractmethod__", False) is False


def test_subclass_missing_id_stays_abstract():
    class _NoId(ToolHandle):
        def description(self, ctx):
            return None

        def capabilities(self):
            return None

        async def execute(self, ctx, args):
            return None

    with pytest.raises(TypeError):
        _NoId()


def test_subclass_missing_execute_stays_abstract():
    class _NoExecute(ToolHandle):
        def id(self):
            return _tid()

        def description(self, ctx):
            return None

        def capabilities(self):
            return None

    with pytest.raises(TypeError):
        _NoExecute()


def test_subclass_re_abstracting_should_list_stays_abstract():
    # A subclass that re-marks the concrete default as abstract is itself
    # non-instantiable (proves should_list is genuinely overridable).
    class _ReAbstract(_RecordingHandle):
        @abc.abstractmethod
        def should_list(self, ctx):  # noqa: D401 - re-abstracted for the test
            ...

    with pytest.raises(TypeError):
        _ReAbstract(None)


def test_subclass_implementing_all_methods_is_concrete():
    h = _RecordingHandle(None)
    assert isinstance(h, ToolHandle)


def test_should_list_can_be_overridden():
    class _Hidden(ToolHandle):
        def id(self):
            return _tid()

        def description(self, ctx):
            return None

        def capabilities(self):
            return None

        async def execute(self, ctx, args):
            return None

        def should_list(self, ctx):
            return False

    assert _Hidden().should_list(None) is False


def test_toolhandle_execute_is_coroutine_function():
    # execute is `async def` (Rust `async fn`) — mapping 1, same as R115
    # Transport.call. A bare subclass impl preserves coroutine shape.
    assert inspect.iscoroutinefunction(_RecordingHandle(None).execute)


# ===========================================================================
# ErasedTool — constructors + delegation.
# ===========================================================================


def test_erasedtool_from_arc_and_new_both_construct():
    inner = _TypedTool(_tid(), [])
    from_arc = ErasedTool.from_arc(inner)
    new = ErasedTool.new(inner)
    assert isinstance(from_arc, ErasedTool)
    assert isinstance(new, ErasedTool)
    assert from_arc.inner is inner
    assert new.inner is inner


def test_erasedtool_delegates_id_description_capabilities():
    inner = _TypedTool(_tid(), [])
    erased = ErasedTool(inner)
    assert erased.id() is inner.id()
    assert erased.description(None) is _DESCRIPTION
    assert erased.capabilities() is _CAPABILITIES


def test_erasedtool_should_list_delegates_to_inner():
    inner_listed = _TypedTool(_tid(), [], should_list_value=True)
    inner_hidden = _TypedTool(_tid2(), [], should_list_value=False)
    assert ErasedTool(inner_listed).should_list(None) is True
    assert ErasedTool(inner_hidden).should_list(None) is False


def test_erasedtool_is_a_toolhandle():
    erased = ErasedTool(_TypedTool(_tid(), []))
    assert isinstance(erased, ToolHandle)


def test_erasedtool_repr_contains_inner():
    inner = _TypedTool(_tid(), [])
    repr_str = repr(ErasedTool(inner))
    assert "ErasedTool" in repr_str


# ===========================================================================
# ErasedTool.execute — stream-mapping (the R109-deferred blanket impl).
# ===========================================================================


async def _collect(stream: Any) -> list[ToolStreamItem]:
    return [item async for item in stream]


@pytest.mark.asyncio
async def test_erasedtool_execute_progress_passes_through():
    progress = _progress_item()
    inner = _TypedTool(_tid(), [progress])
    erased = ErasedTool(inner)
    stream = await erased.execute(_CTX, {"q": 1})
    items = await _collect(stream)
    assert items == [progress]
    assert items[0].kind == "progress"
    # args / ctx threaded to inner.execute verbatim.
    assert inner.execute_calls == [(_CTX, {"q": 1})]


@pytest.mark.asyncio
async def test_erasedtool_execute_terminal_err_passes_through():
    err = ToolError.execution(_tid(), "boom")
    inner = _TypedTool(_tid(), [_terminal_err_item(err)])
    erased = ErasedTool(inner)
    items = await _collect(await erased.execute(_CTX, None))
    assert len(items) == 1
    assert items[0].is_error()
    assert items[0].terminal is err


@pytest.mark.asyncio
async def test_erasedtool_execute_terminal_ok_extracts_model_output():
    # Plain dict output: no custom model_output -> extract_content_blocks.
    output = {"text": "hello"}
    tool_id = _tid()
    inner = _TypedTool(tool_id, [_terminal_ok_item(output)])
    erased = ErasedTool(inner)
    items = await _collect(await erased.execute(_CTX, None))
    assert len(items) == 1
    assert not items[0].is_error()
    typed = items[0].terminal
    assert isinstance(typed, TypedToolOutput)
    assert typed.tool_id is tool_id
    assert typed.value == output
    # model_output derived via extract_content_blocks (custom was empty).
    from minimax_code.tool_runtime import extract_content_blocks

    assert typed.model_output == extract_content_blocks(output)
    # No chat-completion card by default.
    assert typed.chat_completion_output is None


@pytest.mark.asyncio
async def test_erasedtool_execute_terminal_ok_custom_model_output_wins():
    # Output implements ToolOutput.model_output() returning non-empty ->
    # custom blocks win, extract_content_blocks is NOT used.
    class _CustomOutput:
        def model_output(self) -> list:
            return [_BLOCK]

    tool_id = _tid()
    inner = _TypedTool(tool_id, [_terminal_ok_item(_CustomOutput())])
    erased = ErasedTool(inner)
    items = await _collect(await erased.execute(_CTX, None))
    typed = items[0].terminal
    assert typed.model_output == [_BLOCK]  # custom wins
    assert typed.chat_completion_output is None


@pytest.mark.asyncio
async def test_erasedtool_execute_terminal_ok_attaches_chat_completion():
    # Output implements ToolOutput.chat_completion_output() -> card attached.
    class _OutputWithCard:
        def chat_completion_output(self) -> Any:
            return _CARD

    tool_id = _tid()
    inner = _TypedTool(tool_id, [_terminal_ok_item(_OutputWithCard())])
    erased = ErasedTool(inner)
    items = await _collect(await erased.execute(_CTX, None))
    typed = items[0].terminal
    assert typed.chat_completion_output is _CARD


@pytest.mark.asyncio
async def test_erasedtool_execute_terminal_ok_dataclass_output_encoded():
    # dataclass output -> dataclasses.asdict -> value is the dict form.
    import dataclasses

    @dataclasses.dataclass
    class _Out:
        result: str
        count: int

    output = _Out(result="ok", count=3)
    tool_id = _tid()
    inner = _TypedTool(tool_id, [_terminal_ok_item(output)])
    erased = ErasedTool(inner)
    items = await _collect(await erased.execute(_CTX, None))
    typed = items[0].terminal
    assert typed.value == {"result": "ok", "count": 3}


@pytest.mark.asyncio
async def test_erasedtool_execute_encode_failure_yields_output_encoding_error():
    # Output whose model_dump() raises -> _encode_to_json_value propagates
    # -> ErasedTool catches and yields ToolError.custom("output_encoding").
    class _BadEncode:
        def model_dump(self) -> Any:
            raise ValueError("boom")

    inner = _TypedTool(_tid(), [_terminal_ok_item(_BadEncode())])
    erased = ErasedTool(inner)
    items = await _collect(await erased.execute(_CTX, None))
    assert len(items) == 1
    assert items[0].is_error()
    err = items[0].terminal
    assert isinstance(err, ToolError)
    assert err.details == {"code": "output_encoding"}
    assert "boom" in err.detail


@pytest.mark.asyncio
async def test_erasedtool_execute_mixed_stream_preserves_order():
    # Progress, then Ok, then another Progress, then Err — order + kinds
    # preserved; only the Ok item is re-encoded.
    ok_output = {"text": "partial"}
    err = ToolError.execution(_tid2(), "late failure")
    inner = _TypedTool(
        _tid(),
        [
            _progress_item(),
            _terminal_ok_item(ok_output),
            _progress_item(),
            _terminal_err_item(err),
        ],
    )
    erased = ErasedTool(inner)
    items = await _collect(await erased.execute(_CTX, None))
    assert len(items) == 4
    assert items[0].kind == "progress"
    assert isinstance(items[1].terminal, TypedToolOutput)
    assert items[2].kind == "progress"
    assert items[3].terminal is err


# ===========================================================================
# CompoundResolver — constructors, eq=False, field accessors.
# ===========================================================================


def test_compoundresolver_local_only_constructs_no_remote():
    local = _Registry()
    resolver = CompoundResolver.local_only(local)
    assert resolver.local is local
    assert resolver.remote is None


def test_compoundresolver_compound_constructs_both():
    local = _Registry()
    remote = _Registry()
    resolver = CompoundResolver.compound(local, remote)
    assert resolver.local is local
    assert resolver.remote is remote


def test_compoundresolver_direct_constructor_defaults_remote_none():
    local = _Registry()
    resolver = CompoundResolver(local=local)
    assert resolver.remote is None


def test_compoundresolver_field_access_local_and_remote():
    # Rust local() / remote() accessor methods collapse into attribute access.
    local = _Registry()
    remote = _Registry()
    resolver = CompoundResolver.compound(local, remote)
    assert resolver.local is resolver.local  # attribute, not method call
    assert resolver.remote is resolver.remote


def test_compoundresolver_no_partialeq_identity_only():
    # Rust derive is `Debug` only (no PartialEq/Clone) -> eq=False.
    local = _Registry()
    a = CompoundResolver.local_only(local)
    b = CompoundResolver.local_only(local)
    assert a != b
    assert a == a


# ===========================================================================
# CompoundResolver.resolve — local-first, remote-fallback.
# ===========================================================================


def test_resolve_local_hit_shadows_remote():
    local_hit = ResolvedTool.Local(_RecordingHandle(None), _reg())
    local = _Registry(resolved=local_hit)
    remote = _Registry(resolved=object())  # would-be remote hit, never read
    resolver = CompoundResolver.compound(local, remote)
    result = resolver.resolve(_session(), _tid())
    assert result is local_hit
    # Local is consulted first; remote is NOT consulted when local hits.
    assert len(local.find_calls) == 1
    assert len(remote.find_calls) == 0


def test_resolve_remote_fallback_when_local_miss():
    local = _Registry(resolved=None)
    remote_hit = ResolvedTool.Remote(_RecordingHandle(None), _reg())
    remote = _Registry(resolved=remote_hit)
    resolver = CompoundResolver.compound(local, remote)
    result = resolver.resolve(_session(), _tid())
    assert result is remote_hit
    assert len(local.find_calls) == 1
    assert len(remote.find_calls) == 1


def test_resolve_returns_none_when_both_miss():
    local = _Registry(resolved=None)
    remote = _Registry(resolved=None)
    resolver = CompoundResolver.compound(local, remote)
    assert resolver.resolve(_session(), _tid()) is None
    assert len(local.find_calls) == 1
    assert len(remote.find_calls) == 1


def test_resolve_returns_none_when_local_miss_and_no_remote():
    local = _Registry(resolved=None)
    resolver = CompoundResolver.local_only(local)
    assert resolver.resolve(_session(), _tid()) is None
    assert len(local.find_calls) == 1


def test_resolve_threads_session_and_tool_id():
    local = _Registry(resolved=None)
    resolver = CompoundResolver.local_only(local)
    session = _session()
    tid = _tid()
    resolver.resolve(session, tid)
    assert local.find_calls == [(session, tid)]


# ===========================================================================
# CompoundResolver.resolve_and_dispatch — resolve + forward / not-found.
# ===========================================================================


@pytest.mark.asyncio
async def test_resolve_and_dispatch_hit_forwards_to_handle_execute():
    stream = object()  # the handle returns this opaque stream verbatim
    handle = _RecordingHandle(stream)
    resolved = ResolvedTool.Local(handle, _reg())
    local = _Registry(resolved=resolved)
    resolver = CompoundResolver.local_only(local)
    args = {"q": "rust"}
    ctx = _CTX
    result = await resolver.resolve_and_dispatch(_session(), _tid(), args, ctx)
    # The resolved handle's execute was called with the forwarded args/ctx,
    # and its return value is what resolve_and_dispatch hands back.
    assert result is stream
    assert handle.execute_calls == [(ctx, args)]


@pytest.mark.asyncio
async def test_resolve_and_dispatch_miss_yields_single_not_found_terminal():
    local = _Registry(resolved=None)
    resolver = CompoundResolver.local_only(local)
    tid = _tid()
    stream = await resolver.resolve_and_dispatch(_session(), tid, {"x": 1}, _CTX)
    items = await _collect(stream)
    assert len(items) == 1
    assert items[0].is_terminal()
    assert items[0].is_error()


@pytest.mark.asyncio
async def test_resolve_and_dispatch_miss_error_is_not_found_kind():
    local = _Registry(resolved=None)
    resolver = CompoundResolver.local_only(local)
    tid = _tid()
    stream = await resolver.resolve_and_dispatch(_session(), tid, None, _CTX)
    items = await _collect(stream)
    err = items[0].terminal
    assert isinstance(err, ToolError)
    assert err.details == {"tool_id": str(tid)}


@pytest.mark.asyncio
async def test_resolve_and_dispatch_uses_local_first_then_remote():
    # Local miss + remote hit -> remote handle executes; local was tried.
    remote_stream = object()
    remote_handle = _RecordingHandle(remote_stream)
    remote_resolved = ResolvedTool.Remote(remote_handle, _reg())
    local = _Registry(resolved=None)
    remote = _Registry(resolved=remote_resolved)
    resolver = CompoundResolver.compound(local, remote)
    result = await resolver.resolve_and_dispatch(_session(), _tid2(), {}, _CTX)
    assert result is remote_stream
    assert len(local.find_calls) == 1
    assert len(remote.find_calls) == 1
    assert len(remote_handle.execute_calls) == 1


# ===========================================================================
# Cross-leaf integration — R116 cycle resolution.
# ===========================================================================


def test_r116_forward_reference_now_resolves_to_real_resolvedtool():
    # R116 registry.py annotates find_tool -> ResolvedTool | None under a
    # TYPE_CHECKING-only import. R117 defines the real ResolvedTool. The
    # barrel re-export is the SAME class object the annotation names.
    from minimax_code.computer_hub_core import ResolvedTool as BarrelResolvedTool
    from minimax_code.computer_hub_core.resolver import (
        ResolvedTool as ModuleResolvedTool,
    )

    assert BarrelResolvedTool is ModuleResolvedTool
