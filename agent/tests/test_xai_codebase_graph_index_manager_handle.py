"""Black-box tests for the ``index_manager`` actor-sender handle (R306d).

Ported **by function** from grok ``IndexManagerHandle`` (``index_manager.rs``
L233-L495) -- the producer side of the channel-actor mailbox. This suite
covers the three method families the handle exposes:

* fire-and-forget senders (4): enqueue a command, raise on a dead actor
* strict request-response (5): enqueue + ``await`` a Future, raise on dead
* lightweight probes (4): enqueue + ``await`` a Future, swallow closed -> ``None``

plus the channel-adaptation contract (``asyncio.Queue`` mailbox, ``Future``
response, union value on query miss) and the barrel contract
(:class:`IndexManagerHandle` at the crate root; :class:`ManagerClosedError`
leaf-module-only -- grok has no crate-root ``SendError`` re-export).

The actor loop that *drains* the mailbox lands in R306f; these tests
simulate one drain tick (``asyncio.create_task`` + ``sleep(0)`` +
``set_result``) so the producer surface is pinned without the actor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

import minimax_code.xai_codebase_graph as xcg_root
from minimax_code.xai_codebase_graph import index_manager as im
from minimax_code.xai_codebase_graph.index_manager import (
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
    IndexManagerHandle,
    ManagerClosedError,
    QueryError,
    QueryResult,
    RebuildCommand,
    ShutdownCommand,
    SymbolLocation,
)

# === handle factories ====================================================


def _open_handle() -> tuple[IndexManagerHandle, asyncio.Queue[IndexCommand]]:
    """Build a handle bound to a fresh open mailbox (actor still alive)."""
    mailbox: asyncio.Queue[IndexCommand] = asyncio.Queue()
    return IndexManagerHandle(mailbox=mailbox, closed=asyncio.Event()), mailbox


def _closed_handle() -> IndexManagerHandle:
    """Build a handle whose actor has already exited (``_closed`` set)."""
    closed = asyncio.Event()
    closed.set()
    return IndexManagerHandle(mailbox=asyncio.Queue(), closed=closed)


# Caller tables -- each row is ``(label, caller, expected_command_class)``.
# ``caller`` takes a handle and returns either ``None`` (fire-and-forget) or a
# coroutine (request-response / probe) to await.

FIRE_AND_FORGET: list[
    tuple[str, Callable[[IndexManagerHandle], object], type[IndexCommand]]
] = [
    ("send_event", lambda h: h.send_event(FileEvent.modified("a.py")), FileEventCommand),
    ("rebuild", lambda h: h.rebuild(), RebuildCommand),
    ("shutdown", lambda h: h.shutdown(), ShutdownCommand),
]

STRICT_QUERIES: list[
    tuple[str, Callable[[IndexManagerHandle], Awaitable[object]], type[IndexCommand]]
] = [
    ("goto_definition", lambda h: h.goto_definition("a.py", 1, 2), GotoDefinitionCommand),
    (
        "goto_references",
        lambda h: h.goto_references("a.py", 1, 2, True),
        GotoReferencesCommand,
    ),
    ("find_definitions", lambda h: h.find_definitions("foo", "a.py"), FindDefinitionsCommand),
    ("find_references", lambda h: h.find_references("foo", None), FindReferencesCommand),
    ("get_snapshot_async", lambda h: h.get_snapshot_async(), GetSnapshotCommand),
]

LIGHTWEIGHT_PROBES: list[
    tuple[str, Callable[[IndexManagerHandle], Awaitable[object]], type[IndexCommand]]
] = [
    ("get_file_count_async", lambda h: h.get_file_count_async(), GetFileCountCommand),
    ("get_stats_async", lambda h: h.get_stats_async(), GetStatsCommand),
    (
        "get_query_version_async",
        lambda h: h.get_query_version_async(),
        GetQueryVersionCommand,
    ),
    ("has_definition_async", lambda h: h.has_definition_async("foo"), HasDefinitionCommand),
]


# === construction + liveness probe =======================================


def test_handle_binds_mailbox_and_closed_flag() -> None:
    """The handle owns nothing but a mailbox reference + a closed flag."""
    mailbox: asyncio.Queue[IndexCommand] = asyncio.Queue()
    closed = asyncio.Event()
    handle = IndexManagerHandle(mailbox=mailbox, closed=closed)
    # both attributes are the exact objects passed in (by reference, not copy).
    assert handle._mailbox is mailbox  # noqa: SLF001
    assert handle._closed is closed  # noqa: SLF001


def test_is_closed_reflects_the_closed_flag() -> None:
    """``is_closed`` is the public mirror of grok ``has_run_loop_exited``."""
    closed = asyncio.Event()
    handle = IndexManagerHandle(mailbox=asyncio.Queue(), closed=closed)
    assert handle.is_closed is False
    closed.set()
    assert handle.is_closed is True


def test_is_closed_is_a_boolean_property() -> None:
    """``is_closed`` is a property (not a callable) -- Pythonic liveness probe."""
    handle = IndexManagerHandle(mailbox=asyncio.Queue(), closed=asyncio.Event())
    assert isinstance(handle.is_closed, bool)


def test_handle_uses_slots() -> None:
    """``__slots__`` -> instances carry no ``__dict__`` (lean handle)."""
    handle = IndexManagerHandle(mailbox=asyncio.Queue(), closed=asyncio.Event())
    assert not hasattr(handle, "__dict__")


# === ManagerClosedError class hierarchy ==================================


def test_manager_closed_error_is_runtime_error_subclass() -> None:
    """``ManagerClosedError`` subclasses :class:`RuntimeError` (catchable broadly)."""
    assert issubclass(ManagerClosedError, RuntimeError)


def test_manager_closed_error_is_distinct_from_index_command() -> None:
    """The error is a value type, not part of the ``IndexCommand`` hierarchy."""
    assert not issubclass(ManagerClosedError, IndexCommand)


# === fire-and-forget senders ============================================


@pytest.mark.parametrize(
    ("label", "caller", "cmd_cls"),
    FIRE_AND_FORGET,
    ids=[row[0] for row in FIRE_AND_FORGET],
)
def test_fire_and_forget_enqueues_one_command(
    label: str,
    caller: Callable[[IndexManagerHandle], object],
    cmd_cls: type[IndexCommand],
) -> None:
    """Each fire-and-forget sender enqueues exactly one command, no Future."""
    handle, mailbox = _open_handle()
    result = caller(handle)
    assert result is None  # fire-and-forget: no return value on success
    cmd = mailbox.get_nowait()
    assert isinstance(cmd, cmd_cls)
    assert not hasattr(cmd, "response")  # no Future carried
    assert mailbox.empty()  # exactly one command, not more


@pytest.mark.parametrize(
    ("label", "caller", "cmd_cls"),
    FIRE_AND_FORGET,
    ids=[row[0] for row in FIRE_AND_FORGET],
)
def test_fire_and_forget_raises_on_closed_actor(
    label: str,
    caller: Callable[[IndexManagerHandle], object],
    cmd_cls: type[IndexCommand],
) -> None:
    """A dead actor surfaces as :class:`ManagerClosedError` (grok ``SendError``)."""
    handle = _closed_handle()
    with pytest.raises(ManagerClosedError):
        caller(handle)


def test_send_event_carries_the_event_payload() -> None:
    """grok ``send_event(FileEvent)`` -> the event is reachable on the command."""
    handle, mailbox = _open_handle()
    event = FileEvent.created("src/main.py")
    handle.send_event(event)
    cmd = mailbox.get_nowait()
    assert isinstance(cmd, FileEventCommand)
    assert cmd.event is event


def test_send_events_empty_short_circuits_without_enqueue() -> None:
    """grok L257-L259 returns ``Ok(())`` on an empty batch -- no mailbox hop."""
    handle, mailbox = _open_handle()
    handle.send_events([])
    assert mailbox.empty()  # short-circuited: nothing enqueued


def test_send_events_batches_into_one_command() -> None:
    """grok ``FileEventBatch(Vec<FileEvent>)`` -> one command, not ``len(events)``."""
    handle, mailbox = _open_handle()
    events = [FileEvent.created("a.py"), FileEvent.modified("b.py")]
    handle.send_events(events)
    cmd = mailbox.get_nowait()
    assert isinstance(cmd, FileEventBatchCommand)
    assert cmd.events is events
    assert mailbox.empty()  # one command for the whole batch


def test_send_events_raises_on_closed_actor() -> None:
    """The empty-list short-circuit fires before the closed check; a non-empty
    batch on a dead actor raises."""
    handle = _closed_handle()
    with pytest.raises(ManagerClosedError):
        handle.send_events([FileEvent.created("a.py")])


# === strict request-response (raise on dead actor) ======================


@pytest.mark.parametrize(
    ("label", "caller", "cmd_cls"),
    STRICT_QUERIES,
    ids=[row[0] for row in STRICT_QUERIES],
)
async def test_strict_query_enqueues_command_and_resolves(
    label: str,
    caller: Callable[[IndexManagerHandle], Awaitable[object]],
    cmd_cls: type[IndexCommand],
) -> None:
    """Each strict query enqueues its command + a Future; the actor resolves it."""
    handle, mailbox = _open_handle()
    task = asyncio.create_task(caller(handle))
    await asyncio.sleep(0)  # let the coroutine run up to ``await fut``
    cmd = mailbox.get_nowait()
    assert isinstance(cmd, cmd_cls)
    assert isinstance(cmd.response, asyncio.Future)  # carries a Future
    sentinel = object()
    cmd.response.set_result(sentinel)
    assert await task is sentinel  # the resolved value flows back unchanged


@pytest.mark.parametrize(
    ("label", "caller", "cmd_cls"),
    STRICT_QUERIES,
    ids=[row[0] for row in STRICT_QUERIES],
)
async def test_strict_query_raises_on_closed_actor(
    label: str,
    caller: Callable[[IndexManagerHandle], Awaitable[object]],
    cmd_cls: type[IndexCommand],
) -> None:
    """A dead actor surfaces as :class:`ManagerClosedError` on the send half."""
    handle = _closed_handle()
    with pytest.raises(ManagerClosedError):
        await caller(handle)


async def test_goto_definition_miss_is_value_not_exception() -> None:
    """grok ``Result<QueryResult, QueryError>`` -> the ``Err`` arm is a VALUE.

    The actor ``set_result`` on both arms; a query miss (no symbol at position)
    is normal control flow, so the caller inspects the resolved value via
    ``isinstance(resolved, QueryError)`` rather than ``except``.
    """
    handle, mailbox = _open_handle()
    task = asyncio.create_task(handle.goto_definition("a.py", 5, 5))
    await asyncio.sleep(0)
    cmd = mailbox.get_nowait()
    assert isinstance(cmd, GotoDefinitionCommand)
    assert cmd.row == 5
    assert cmd.col == 5
    miss = QueryError.no_symbol_at_position(row=5, col=5)
    cmd.response.set_result(miss)
    resolved = await task
    assert resolved is miss
    assert isinstance(resolved, QueryError)
    # the Future itself did NOT raise -- the value flowed through cleanly.
    assert cmd.response.exception() is None


async def test_goto_definition_hit_resolves_to_query_result() -> None:
    """The ``Ok`` arm flows through as a :class:`QueryResult` value."""
    handle, mailbox = _open_handle()
    task = asyncio.create_task(handle.goto_definition("a.py", 1, 2))
    await asyncio.sleep(0)
    cmd = mailbox.get_nowait()
    hit = QueryResult(symbol="foo", locations=[SymbolLocation.new("a.py", 1)])
    cmd.response.set_result(hit)
    resolved = await task
    assert resolved is hit
    assert isinstance(resolved, QueryResult)


async def test_goto_references_passes_include_definition_flag() -> None:
    """grok ``GotoReferences`` carries ``include_definition`` (L133) through."""
    handle, mailbox = _open_handle()
    task = asyncio.create_task(
        handle.goto_references("a.py", 10, 3, include_definition=True)
    )
    await asyncio.sleep(0)
    cmd = mailbox.get_nowait()
    assert isinstance(cmd, GotoReferencesCommand)
    assert cmd.include_definition is True
    cmd.response.set_result(QueryResult(symbol="foo", locations=[]))
    await task


async def test_find_definitions_passes_symbol_and_context() -> None:
    """grok ``FindDefinitions { symbol, context_file }`` reaches the command."""
    handle, mailbox = _open_handle()
    task = asyncio.create_task(handle.find_definitions("MyClass", "ctx.py"))
    await asyncio.sleep(0)
    cmd = mailbox.get_nowait()
    assert isinstance(cmd, FindDefinitionsCommand)
    assert cmd.symbol == "MyClass"
    assert cmd.context_file == "ctx.py"
    locations = [SymbolLocation.new("def.py", 42)]
    cmd.response.set_result(locations)
    assert await task == locations


# === lightweight probes (Option semantics: None on dead actor) ==========


@pytest.mark.parametrize(
    ("label", "caller", "cmd_cls"),
    LIGHTWEIGHT_PROBES,
    ids=[row[0] for row in LIGHTWEIGHT_PROBES],
)
async def test_lightweight_probe_enqueues_command_and_resolves(
    label: str,
    caller: Callable[[IndexManagerHandle], Awaitable[object]],
    cmd_cls: type[IndexCommand],
) -> None:
    """Each lightweight probe enqueues its command + Future; actor resolves it."""
    handle, mailbox = _open_handle()
    task = asyncio.create_task(caller(handle))
    await asyncio.sleep(0)
    cmd = mailbox.get_nowait()
    assert isinstance(cmd, cmd_cls)
    assert isinstance(cmd.response, asyncio.Future)
    sentinel = object()
    cmd.response.set_result(sentinel)
    assert await task is sentinel


@pytest.mark.parametrize(
    ("label", "caller", "cmd_cls"),
    LIGHTWEIGHT_PROBES,
    ids=[row[0] for row in LIGHTWEIGHT_PROBES],
)
async def test_lightweight_probe_returns_none_on_closed_actor(
    label: str,
    caller: Callable[[IndexManagerHandle], Awaitable[object]],
    cmd_cls: type[IndexCommand],
) -> None:
    """A dead actor is swallowed into ``None`` (grok ``Option`` semantics)."""
    handle = _closed_handle()
    assert await caller(handle) is None


async def test_get_file_count_resolves_to_int_value() -> None:
    """The happy path resolves the file-count integer (not a wrapped value)."""
    handle, mailbox = _open_handle()
    task = asyncio.create_task(handle.get_file_count_async())
    await asyncio.sleep(0)
    cmd = mailbox.get_nowait()
    assert isinstance(cmd, GetFileCountCommand)
    cmd.response.set_result(42)
    assert await task == 42


async def test_has_definition_resolves_to_bool_value() -> None:
    """The happy path resolves a plain bool (cheaper than ``find_definitions``)."""
    handle, mailbox = _open_handle()
    task = asyncio.create_task(handle.has_definition_async("Foo"))
    await asyncio.sleep(0)
    cmd = mailbox.get_nowait()
    assert isinstance(cmd, HasDefinitionCommand)
    assert cmd.symbol == "Foo"
    cmd.response.set_result(True)
    assert await task is True


async def test_lightweight_probe_never_raises_on_dead_actor() -> None:
    """The whole point of the lightweight family: closed -> ``None``, never raise."""
    handle = _closed_handle()
    # all four probes return None on a dead actor (no exception escapes).
    assert await handle.get_file_count_async() is None
    assert await handle.get_stats_async() is None
    assert await handle.get_query_version_async() is None
    assert await handle.has_definition_async("foo") is None


# === strict vs lightweight closed-state divergence ======================


async def test_closed_state_diverges_strict_vs_lightweight() -> None:
    """The two families disagree on a dead actor: strict raises, light returns None.

    This is the central functional distinction grok draws between
    ``rx.await.expect(...)`` (strict -- panics on a dropped actor) and
    ``rx.blocking_recv().ok()`` (lightweight -- swallows into ``None``).
    """
    strict_handle = _closed_handle()
    with pytest.raises(ManagerClosedError):
        await strict_handle.get_snapshot_async()

    light_handle = _closed_handle()
    assert await light_handle.get_file_count_async() is None


# === barrel contract =====================================================


def test_crate_root_exports_index_manager_handle() -> None:
    """grok ``lib.rs`` L85 re-exports ``IndexManagerHandle`` at the crate root."""
    assert "IndexManagerHandle" in xcg_root.__all__
    assert xcg_root.IndexManagerHandle is IndexManagerHandle
    assert hasattr(xcg_root, "IndexManagerHandle")


def test_manager_closed_error_stays_leaf_module_only() -> None:
    """grok has no crate-root ``SendError`` re-export -- neither does Python.

    :class:`ManagerClosedError` (the asyncio carrier for crossbeam's
    ``SendError``) is reachable only via the leaf module, mirroring grok's
    leaf-only ``SendError``.
    """
    assert "ManagerClosedError" not in xcg_root.__all__
    assert not hasattr(xcg_root, "ManagerClosedError")
    # ... but it IS reachable via the leaf module.
    assert hasattr(im, "ManagerClosedError")
    assert im.ManagerClosedError is ManagerClosedError


def test_leaf_module_all_has_handle_and_closed_error() -> None:
    """Both R306d symbols land in the leaf ``__all__`` (one crate-root, one leaf)."""
    assert "IndexManagerHandle" in im.__all__
    assert "ManagerClosedError" in im.__all__


def test_crate_root_index_manager_handle_is_leaf_identity() -> None:
    """The crate-root ``IndexManagerHandle`` is the same class object (not a copy)."""
    assert xcg_root.IndexManagerHandle is im.IndexManagerHandle
