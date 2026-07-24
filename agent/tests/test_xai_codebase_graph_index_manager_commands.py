"""Black-box tests for the ``index_manager`` channel-command layer (R306c).

Ported **by function** from grok ``xai-codebase-graph/src/index_manager.rs``.
This suite covers the 14-variant tagged union that flows through the actor
mailbox (grok ``IndexCommand``, L110-L168):

* the :class:`IndexCommand` base marker (sealed-hierarchy root, ``__slots__``)
* the 5 fire-and-forget variants (no response Future)
* the 9 request-response variants (carry an :class:`asyncio.Future`)
* the tokio -> asyncio channel-adaptation contract (``oneshot`` ->
  :class:`asyncio.Future`, ``Result`` -> union value, miss = value not raise)
* the barrel contract (:class:`IndexCommand` at the crate root; the 14
  variant subclasses stay leaf-module-only)

The companion actor loop (R306f) will dispatch these via ``isinstance``;
these tests pin the dispatch surface and the Future resolution semantics
without instantiating the actor itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import fields

import pytest

import minimax_code.xai_codebase_graph as xcg_root
from minimax_code.xai_codebase_graph import index_manager as im
from minimax_code.xai_codebase_graph.index_manager import (
    BackgroundRefreshCommand,
    FileEvent,
    FileEventBatchCommand,
    FileEventCommand,
    FindDefinitionsCommand,
    FindReferencesCommand,
    GetFileCountCommand,
    GetQueryVersionCommand,
    GetSnapshotCommand,
    GetStatsCommand,
    GotoDefinitionCommand,
    GotoReferencesCommand,
    HasDefinitionCommand,
    IndexCommand,
    QueryError,
    QueryResult,
    RebuildCommand,
    ShutdownCommand,
    SymbolLocation,
)

# A module-level event loop so :class:`asyncio.Future` instances bind without a
# running loop -- this avoids the Python 3.12 ``There is no current event loop``
# DeprecationWarning when the sync tests below call ``_fut()``. ``set_result`` /
# ``result()`` / ``set_exception`` are synchronous APIs: they mark / read the
# resolved state and do not need the loop to actually run.
_LOOP = asyncio.new_event_loop()


def _fut() -> asyncio.Future:
    """Create a Future bound to the module-level loop (no DeprecationWarning)."""
    return _LOOP.create_future()


# The 14 concrete variants in grok declaration order (L110-L168).
ALL_VARIANT_CLASSES: list[type[IndexCommand]] = [
    FileEventCommand,
    FileEventBatchCommand,
    RebuildCommand,
    BackgroundRefreshCommand,
    ShutdownCommand,
    GetSnapshotCommand,
    GotoDefinitionCommand,
    GotoReferencesCommand,
    FindDefinitionsCommand,
    FindReferencesCommand,
    GetFileCountCommand,
    GetStatsCommand,
    GetQueryVersionCommand,
    HasDefinitionCommand,
]

# The 5 fire-and-forget variants carry no response Future (grok L110-L122).
FIRE_AND_FORGET: list[type[IndexCommand]] = [
    FileEventCommand,
    FileEventBatchCommand,
    RebuildCommand,
    BackgroundRefreshCommand,
    ShutdownCommand,
]

# The 9 request-response variants carry an :class:`asyncio.Future` (grok L123-L168).
REQUEST_RESPONSE: list[type[IndexCommand]] = [
    GetSnapshotCommand,
    GotoDefinitionCommand,
    GotoReferencesCommand,
    FindDefinitionsCommand,
    FindReferencesCommand,
    GetFileCountCommand,
    GetStatsCommand,
    GetQueryVersionCommand,
    HasDefinitionCommand,
]


def _sample(cls: type[IndexCommand]) -> IndexCommand:
    """Build one valid instance of each variant for invariant checks."""
    if cls is FileEventCommand:
        return FileEventCommand(event=FileEvent.created("a.py"))
    if cls is FileEventBatchCommand:
        return FileEventBatchCommand(events=[FileEvent.created("a.py")])
    if cls is RebuildCommand:
        return RebuildCommand()
    if cls is BackgroundRefreshCommand:
        return BackgroundRefreshCommand(stale_files=["a.py"], deleted_files=["b.py"])
    if cls is ShutdownCommand:
        return ShutdownCommand()
    if cls is GetSnapshotCommand:
        return GetSnapshotCommand(response=_fut())
    if cls is GotoDefinitionCommand:
        return GotoDefinitionCommand(file_path="a.py", row=1, col=2, response=_fut())
    if cls is GotoReferencesCommand:
        return GotoReferencesCommand(
            file_path="a.py", row=1, col=2, include_definition=True, response=_fut()
        )
    if cls is FindDefinitionsCommand:
        return FindDefinitionsCommand(symbol="foo", context_file="a.py", response=_fut())
    if cls is FindReferencesCommand:
        return FindReferencesCommand(symbol="foo", context_file=None, response=_fut())
    if cls is GetFileCountCommand:
        return GetFileCountCommand(response=_fut())
    if cls is GetStatsCommand:
        return GetStatsCommand(response=_fut())
    if cls is GetQueryVersionCommand:
        return GetQueryVersionCommand(response=_fut())
    if cls is HasDefinitionCommand:
        return HasDefinitionCommand(symbol="foo", response=_fut())
    raise AssertionError(f"unknown variant {cls!r}")


# === base marker =========================================================


def test_base_index_command_slots_is_empty() -> None:
    """``__slots__ = ()`` keeps the subclasses ``__dict__``-free (lean mailbox)."""
    assert IndexCommand.__slots__ == ()


def test_every_variant_subclasses_base() -> None:
    """The hierarchy is sealed: all 14 variants inherit :class:`IndexCommand`."""
    for cls in ALL_VARIANT_CLASSES:
        assert issubclass(cls, IndexCommand)


def test_exactly_fourteen_variants_and_no_leak() -> None:
    """grok ``IndexCommand`` has exactly 14 enum variants (L110-L168).

    ``IndexCommand.__subclasses__`` returns the *direct* children only -- the
    14 variants all inherit the base directly, so the discovered set must equal
    the declared list. A 15th subclass leaking into the hierarchy would fail
    this (guards against accidental extension of the sealed union).
    """
    assert len(ALL_VARIANT_CLASSES) == 14
    assert set(IndexCommand.__subclasses__()) == set(ALL_VARIANT_CLASSES)
    # the two-class split partitions the 14 exactly (5 fire-and-forget + 9 rr).
    assert len(FIRE_AND_FORGET) == 5
    assert len(REQUEST_RESPONSE) == 9
    assert len(FIRE_AND_FORGET) + len(REQUEST_RESPONSE) == 14


# === fire-and-forget variants ===========================================


def test_file_event_command_carries_event() -> None:
    """grok ``FileEvent(FileEvent)`` -> single-event ingestion."""
    event = FileEvent.created("a.py")
    cmd = FileEventCommand(event=event)
    assert cmd.event is event


def test_file_event_batch_command_carries_events_list() -> None:
    """grok ``FileEventBatch(Vec<FileEvent>)`` -> batched ingestion."""
    events = [FileEvent.created("a.py"), FileEvent.modified("b.py")]
    cmd = FileEventBatchCommand(events=events)
    assert cmd.events is events
    assert len(cmd.events) == 2


def test_rebuild_command_has_no_fields() -> None:
    """grok ``Rebuild`` -> field-less (no payload, no response)."""
    cmd = RebuildCommand()
    # no dataclass fields beyond the base marker.
    assert len(fields(cmd)) == 0


def test_background_refresh_command_carries_two_lists() -> None:
    """grok ``BackgroundRefresh { stale, deleted }`` -> two path lists."""
    cmd = BackgroundRefreshCommand(stale_files=["a.py"], deleted_files=["b.py", "c.py"])
    assert cmd.stale_files == ["a.py"]
    assert cmd.deleted_files == ["b.py", "c.py"]


def test_shutdown_command_has_no_fields() -> None:
    """grok ``Shutdown`` -> field-less."""
    cmd = ShutdownCommand()
    assert len(fields(cmd)) == 0


# === request-response variants ==========================================


def test_get_snapshot_command_carries_future() -> None:
    """grok ``GetSnapshot`` -> ``Arc<ScopeGraphIndex>`` (Arc collapsed in Python)."""
    fut = _fut()
    cmd = GetSnapshotCommand(response=fut)
    assert cmd.response is fut


def test_goto_definition_command_carries_position_and_future() -> None:
    """grok ``GotoDefinition { file_path, row, col }`` -> 0-indexed point + Future."""
    fut = _fut()
    cmd = GotoDefinitionCommand(file_path="a.py", row=10, col=3, response=fut)
    assert cmd.file_path == "a.py"
    assert cmd.row == 10
    assert cmd.col == 3
    assert cmd.response is fut


def test_goto_references_command_carries_include_definition_flag() -> None:
    """grok ``GotoReferences`` adds ``include_definition`` (L133)."""
    cmd_true = GotoReferencesCommand(
        file_path="a.py", row=0, col=0, include_definition=True, response=_fut()
    )
    cmd_false = GotoReferencesCommand(
        file_path="a.py", row=0, col=0, include_definition=False, response=_fut()
    )
    assert cmd_true.include_definition is True
    assert cmd_false.include_definition is False


def test_find_definitions_command_context_file_optional() -> None:
    """grok ``FindDefinitions { symbol, context_file }`` -> context_file is Optional."""
    with_ctx = FindDefinitionsCommand(symbol="foo", context_file="a.py", response=_fut())
    no_ctx = FindDefinitionsCommand(symbol="foo", context_file=None, response=_fut())
    assert with_ctx.context_file == "a.py"
    assert no_ctx.context_file is None


def test_find_references_command_mirrors_find_definitions() -> None:
    cmd = FindReferencesCommand(symbol="bar", context_file="b.py", response=_fut())
    assert cmd.symbol == "bar"
    assert cmd.context_file == "b.py"


def test_get_file_count_command_carries_future() -> None:
    cmd = GetFileCountCommand(response=_fut())
    assert isinstance(cmd.response, asyncio.Future)


def test_get_stats_command_carries_future() -> None:
    cmd = GetStatsCommand(response=_fut())
    assert isinstance(cmd.response, asyncio.Future)


def test_get_query_version_command_carries_future() -> None:
    cmd = GetQueryVersionCommand(response=_fut())
    assert isinstance(cmd.response, asyncio.Future)


def test_has_definition_command_carries_symbol() -> None:
    cmd = HasDefinitionCommand(symbol="foo", response=_fut())
    assert cmd.symbol == "foo"


# === parametrized invariants ============================================


@pytest.mark.parametrize("cls", ALL_VARIANT_CLASSES, ids=lambda c: c.__name__)
def test_variant_isinstance_index_command(cls: type[IndexCommand]) -> None:
    """Every variant dispatches to the ``isinstance(cmd, IndexCommand)`` arm."""
    assert isinstance(_sample(cls), IndexCommand)


@pytest.mark.parametrize("cls", ALL_VARIANT_CLASSES, ids=lambda c: c.__name__)
def test_variant_uses_slots(cls: type[IndexCommand]) -> None:
    """``@dataclass(slots=True)`` -> no ``__dict__`` on instances (lean mailbox)."""
    assert not hasattr(_sample(cls), "__dict__")


@pytest.mark.parametrize("cls", FIRE_AND_FORGET, ids=lambda c: c.__name__)
def test_fire_and_forget_has_no_response_field(cls: type[IndexCommand]) -> None:
    """The 5 fire-and-forget variants carry no ``response`` Future."""
    assert not hasattr(_sample(cls), "response")


@pytest.mark.parametrize("cls", REQUEST_RESPONSE, ids=lambda c: c.__name__)
def test_request_response_carries_future(cls: type[IndexCommand]) -> None:
    """The 9 request-response variants each carry an :class:`asyncio.Future`."""
    inst = _sample(cls)
    assert hasattr(inst, "response")
    assert isinstance(inst.response, asyncio.Future)


def test_isinstance_arms_do_not_cross_talk() -> None:
    """Each variant dispatches only to its own arm (no leakage across variants)."""
    a = FileEventCommand(event=FileEvent.created("a.py"))
    b = RebuildCommand()
    c = GetFileCountCommand(response=_fut())
    assert isinstance(a, FileEventCommand)
    assert not isinstance(a, RebuildCommand)
    assert not isinstance(a, GetFileCountCommand)
    assert isinstance(b, RebuildCommand)
    assert isinstance(c, GetFileCountCommand)
    # all three still satisfy the base arm.
    assert isinstance(a, IndexCommand)
    assert isinstance(b, IndexCommand)
    assert isinstance(c, IndexCommand)


# === channel-adaptation contract (tokio -> asyncio) =====================


def test_future_set_result_then_result_synchronous() -> None:
    """``set_result`` resolves the Future to a value with no loop run (sync API).

    Mirrors the actor's happy path: drain the command, run the query,
    ``set_result`` on the carried Future, caller reads ``.result()``.
    """
    cmd = GetFileCountCommand(response=_fut())
    cmd.response.set_result(42)
    assert cmd.response.done()
    assert cmd.response.result() == 42


def test_query_miss_resolves_to_value_not_exception() -> None:
    """grok ``Result<T, QueryError>`` -> the ``Err`` arm is a VALUE, not a raise.

    The actor ``set_result`` on both arms: a query miss (no symbol at position)
    is normal control flow, so the caller inspects the resolved value via
    ``isinstance(resolved, QueryError)`` rather than ``except``.
    """
    cmd = GotoDefinitionCommand(file_path="a.py", row=5, col=5, response=_fut())
    miss = QueryError.no_symbol_at_position(row=5, col=5)
    cmd.response.set_result(miss)
    resolved = cmd.response.result()
    assert resolved is miss
    assert isinstance(resolved, QueryError)
    # the Future itself did NOT raise -- the value flowed through cleanly.
    assert cmd.response.exception() is None


def test_query_hit_resolves_to_query_result_value() -> None:
    """The ``Ok`` arm flows through as a :class:`QueryResult` value."""
    cmd = FindDefinitionsCommand(symbol="foo", context_file=None, response=_fut())
    hit = QueryResult(symbol="foo", locations=[SymbolLocation.new("a.py", 1)])
    cmd.response.set_result(hit)
    assert cmd.response.result() is hit
    assert isinstance(cmd.response.result(), QueryResult)


def test_real_runtime_fault_propagates_via_set_exception() -> None:
    """A genuine runtime fault is the ONLY path that raises through the Future.

    Mirrors the actor's fault path: an unexpected error (IO, panic) propagates
    via ``set_exception`` -- distinct from a query miss, which is a value.
    """
    cmd = GetSnapshotCommand(response=_fut())
    cmd.response.set_exception(RuntimeError("index closed"))
    with pytest.raises(RuntimeError, match="index closed"):
        cmd.response.result()


# === asyncio.Queue mailbox end-to-end (async) ===========================


async def test_mailbox_drain_resolves_future() -> None:
    """End-to-end: caller enqueues, actor drains + resolves, caller awaits.

    The mailbox is an :class:`asyncio.Queue[IndexCommand]` (tokio ``mpsc``
    adaptation). The actor pops the command, dispatches via ``isinstance``,
    and ``set_result`` on the carried Future; the caller ``await``s it.
    """
    queue: asyncio.Queue[IndexCommand] = asyncio.Queue()
    fut: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    await queue.put(GetFileCountCommand(response=fut))

    # actor drain (R306f will own this loop; here we simulate one tick).
    cmd = await queue.get()
    assert isinstance(cmd, GetFileCountCommand)
    cmd.response.set_result(7)

    assert await fut == 7


async def test_mailbox_drain_can_short_circuit_on_shutdown() -> None:
    """A :class:`ShutdownCommand` ends the drain loop without touching a Future."""
    queue: asyncio.Queue[IndexCommand] = asyncio.Queue()
    await queue.put(ShutdownCommand())
    cmd = await queue.get()
    assert isinstance(cmd, ShutdownCommand)
    # fire-and-forget: no Future to resolve, the actor just exits the loop.


async def test_mailbox_batch_then_query_in_order() -> None:
    """Fire-and-forget and request-response commands share one FIFO mailbox."""
    queue: asyncio.Queue[IndexCommand] = asyncio.Queue()
    count_fut: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    await queue.put(FileEventCommand(event=FileEvent.modified("a.py")))
    await queue.put(GetFileCountCommand(response=count_fut))

    first = await queue.get()
    second = await queue.get()
    assert isinstance(first, FileEventCommand)
    assert isinstance(second, GetFileCountCommand)
    # FIFO ordering preserved (the mailbox is a single channel, not two).
    second.response.set_result(1)
    assert await count_fut == 1


# === barrel contract ====================================================


def test_crate_root_exports_index_command() -> None:
    """grok ``lib.rs`` L84 re-exports the ``IndexCommand`` enum at the crate root."""
    assert "IndexCommand" in xcg_root.__all__
    assert xcg_root.IndexCommand is IndexCommand
    assert hasattr(xcg_root, "IndexCommand")


def test_variant_subclasses_stay_leaf_module_only() -> None:
    """The 14 variants are NOT at the crate root (grok models them as enum members).

    grok re-exports just the ``IndexCommand`` enum name at ``lib.rs`` L84; the
    individual variants are enum members, not free symbols, so they never reach
    the crate root. Callers reach them via the leaf module explicitly.
    """
    variant_names = [cls.__name__ for cls in ALL_VARIANT_CLASSES]
    assert len(variant_names) == 14
    for name in variant_names:
        assert name not in xcg_root.__all__, f"{name} leaked into crate-root barrel"
        assert not hasattr(xcg_root, name), f"{name} leaked as crate-root attr"
        # ... but each is reachable via the leaf module.
        assert hasattr(im, name)


def test_leaf_module_all_has_fifteen_command_symbols() -> None:
    """R306c adds 15 command symbols (base + 14 variants) to the leaf ``__all__``."""
    command_symbols = {"IndexCommand", *{cls.__name__ for cls in ALL_VARIANT_CLASSES}}
    assert len(command_symbols) == 15
    assert command_symbols.issubset(set(im.__all__))
