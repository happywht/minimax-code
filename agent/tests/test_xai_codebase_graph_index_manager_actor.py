"""Black-box tests for the ``index_manager`` channel-actor loop (R306f).

Ported **by function** from grok ``IndexManager`` (``index_manager.rs``):

* the actor struct (L556-L574) -- :class:`IndexManager` owns a
  :class:`ScopeGraphIndex`, drains the mailbox of :class:`IndexCommand`, and
  resolves each request-response :class:`asyncio.Future`.
* ``run`` (L1154-L1170) -- :meth:`IndexManager.run_loop` (``recv -> process;
  break on false``) with a ``finally`` that saves the cache + closes the
  beacon.
* ``process_command`` (L1174-L1241) -- :meth:`IndexManager._process_command`,
  the 14-variant ``isinstance`` dispatch (Python's tagged-union match).
* ``drain_and_apply`` (L1243-L1269) -- :meth:`IndexManager._drain_and_apply`,
  the coalescing drain loop + depth-1 recursion on the first non-file command.
* ``apply_coalesced`` (L1273-L1294) -- :meth:`IndexManager._apply_coalesced`,
  gate + mutation + cache-save throttle.
* ``get_symbol_at_position`` (L1046+) -- :meth:`IndexManager._get_symbol_at_position`,
  the R306f error-path guards (parse lands R306g).
* ``process_background_refresh`` (L734-L754) --
  :meth:`IndexManager._process_background_refresh`, deleted-file eviction
  (reindex stub lands R306g).

The suite drives the actor via ``asyncio.create_task(actor.run_loop())`` +
``await asyncio.sleep(0)`` drain ticks (the R306d/R306e pattern), pinning the
dispatch / drain / cache / lifecycle behavior without instantiating the
production ``spawn`` path (which would walk the real filesystem).
"""

from __future__ import annotations

import asyncio
import time

import pytest

import minimax_code.xai_codebase_graph as xcg_root
from minimax_code.xai_codebase_graph import (
    IndexStats,
    LanguageRegistry,
    QueryVersion,
    ScopeGraphIndex,
)
from minimax_code.xai_codebase_graph import index_manager as im
from minimax_code.xai_codebase_graph.index_manager import (
    BackgroundRefreshCommand,
    CoalescedEvents,
    ExitBeacon,
    FileEvent,
    FileEventBatchCommand,
    FileEventCommand,
    FileEventKind,
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
    IndexManager,
    IndexManagerConfig,
    QueryError,
    QueryResult,
    RebuildCommand,
    ShutdownCommand,
    SymbolLocation,
)

# === actor factories ========================================================


def _make_actor(
    *,
    index: ScopeGraphIndex | None = None,
    config: IndexManagerConfig | None = None,
    beacon: ExitBeacon | None = None,
    mailbox: asyncio.Queue[IndexCommand] | None = None,
) -> IndexManager:
    """Construct an :class:`IndexManager` with cache-save disabled.

    Cache-save is off (``without_cache_save`` + ``cache_path=None``) so the
    ``finally`` block + throttle paths never touch the real filesystem; each
    test that *wants* a cache write re-enables it explicitly via ``config=``.
    """
    return IndexManager(
        index=index or ScopeGraphIndex(),
        registry=LanguageRegistry.new(),
        config=config or IndexManagerConfig.new(".").without_cache_save(),
        mailbox=mailbox or asyncio.Queue(),
        beacon=beacon or ExitBeacon(),
    )


async def _drive(actor: IndexManager, *commands: IndexCommand) -> asyncio.Task[None]:
    """Enqueue ``commands``, start ``run_loop``, drain one tick, return task.

    The task is **not** awaited -- callers either enqueue a
    :class:`ShutdownCommand` (to let the loop exit) or leave the actor
    blocked on ``mailbox.get()`` and cancel the task. The single
    ``sleep(0)`` is enough because every drain step after the first
    ``await get()`` is synchronous (``get_nowait`` does not yield).
    """
    mailbox = actor._mailbox  # noqa: SLF001
    for cmd in commands:
        mailbox.put_nowait(cmd)
    task = asyncio.create_task(actor.run_loop())
    await asyncio.sleep(0)
    return task


async def _stop(actor: IndexManager, task: asyncio.Task[None]) -> None:
    """Enqueue ``Shutdown`` + drain so the loop exits cleanly, then await."""
    actor._mailbox.put_nowait(ShutdownCommand())  # noqa: SLF001
    await asyncio.sleep(0)
    await task


# === module-level surface ===================================================


def test_cache_save_interval_is_thirty_seconds() -> None:
    """grok ``CACHE_SAVE_INTERVAL_SECS = 30`` -- the throttle window."""
    assert im._CACHE_SAVE_INTERVAL_SECS == 30.0  # noqa: SLF001


def test_active_actor_tasks_is_a_set() -> None:
    """``_ACTIVE_ACTOR_TASKS`` holds strong refs to spawned actor tasks."""
    assert isinstance(im._ACTIVE_ACTOR_TASKS, set)  # noqa: SLF001


def test_resolve_response_is_module_level_helper() -> None:
    """``_resolve_response`` is the done-guarded Future resolver."""
    assert callable(im._resolve_response)  # noqa: SLF001


def test_index_manager_not_in_leaf_all() -> None:
    """grok ``IndexManager`` is crate-private (no leaf ``__all__`` entry)."""
    assert "IndexManager" not in im.__all__


def test_index_manager_not_in_crate_root_all() -> None:
    """The actor struct never reaches the crate-root barrel either."""
    assert "IndexManager" not in xcg_root.__all__
    assert not hasattr(xcg_root, "IndexManager")


def test_exit_beacon_stays_test_only() -> None:
    """grok gates ``ExitBeacon`` behind ``#[cfg(test)]``; Python mirrors via ``__all__``."""
    assert "ExitBeacon" not in im.__all__


# === construction + slots ===================================================


def test_constructor_binds_fields() -> None:
    """``__init__`` stores every kwarg on the matching slot."""
    index = ScopeGraphIndex()
    registry = LanguageRegistry.new()
    config = IndexManagerConfig.new(".")
    mailbox: asyncio.Queue[IndexCommand] = asyncio.Queue()
    beacon = ExitBeacon()
    actor = IndexManager(
        index=index,
        registry=registry,
        config=config,
        mailbox=mailbox,
        beacon=beacon,
    )
    assert actor._index is index  # noqa: SLF001
    assert actor._registry is registry  # noqa: SLF001
    assert actor._config is config  # noqa: SLF001
    assert actor._mailbox is mailbox  # noqa: SLF001
    assert actor._beacon is beacon  # noqa: SLF001


def test_constructor_seeds_counters() -> None:
    """``updates_processed`` starts at 0; ``last_cache_save`` starts at 0.0."""
    actor = _make_actor()
    assert actor._updates_processed == 0  # noqa: SLF001
    assert actor._last_cache_save == 0.0  # noqa: SLF001


def test_actor_uses_slots() -> None:
    """``__slots__`` -> no ``__dict__`` (the mailbox can hold thousands)."""
    actor = _make_actor()
    assert not hasattr(actor, "__dict__")


def test_actor_has_seven_slots() -> None:
    """7 slots (grok L556-L574 minus parser_cache / query_cache, which land R306g)."""
    assert len(IndexManager.__slots__) == 7


# === run_loop: shutdown + finally ===========================================


async def test_run_loop_breaks_on_shutdown() -> None:
    """``ShutdownCommand`` -> ``_process_command`` returns False -> loop exits."""
    actor = _make_actor()
    task = await _drive(actor, ShutdownCommand())
    await task
    assert task.done()


async def test_run_loop_finally_closes_beacon() -> None:
    """The ``finally`` block flips the beacon flag on normal shutdown."""
    actor = _make_actor()
    assert actor._beacon.exited is False  # noqa: SLF001
    task = await _drive(actor, ShutdownCommand())
    await task
    assert actor._beacon.exited is True  # noqa: SLF001


async def test_run_loop_seeds_last_cache_save_on_start() -> None:
    """``run_loop`` seeds ``last_cache_save`` so the first tick never thrashes."""
    actor = _make_actor()
    assert actor._last_cache_save == 0.0  # noqa: SLF001
    task = await _drive(actor, ShutdownCommand())
    await task
    assert actor._last_cache_save > 0.0  # noqa: SLF001


async def test_run_loop_finally_closes_beacon_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The beacon flips even when ``_process_command`` raises (Pythonic ``Drop``)."""
    actor = _make_actor()

    def _boom(_self: IndexManager, _cmd: IndexCommand) -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(IndexManager, "_process_command", _boom)
    actor._mailbox.put_nowait(ShutdownCommand())  # noqa: SLF001
    task = asyncio.create_task(actor.run_loop())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="boom"):
        await task
    assert actor._beacon.exited is True  # noqa: SLF001


# === _process_command: fire-and-forget dispatch ============================


async def test_file_event_command_consumed_without_break() -> None:
    """FileEventCommand -> coalesce + drain -> return True (loop stays alive)."""
    actor = _make_actor()
    task = await _drive(actor, FileEventCommand(FileEvent.modified("a.py")))
    # No shutdown yet -> the command was consumed, actor blocked on next get().
    assert actor._mailbox.empty()  # noqa: SLF001
    await _stop(actor, task)


async def test_file_event_batch_command_consumed_without_break() -> None:
    """FileEventBatchCommand -> coalesce every event -> drain -> return True."""
    actor = _make_actor()
    batch = [FileEvent.created("a.py"), FileEvent.removed("b.py")]
    task = await _drive(actor, FileEventBatchCommand(batch))
    assert actor._mailbox.empty()  # noqa: SLF001
    await _stop(actor, task)


async def test_background_refresh_consumed_without_break(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BackgroundRefreshCommand -> eviction (R306f stub) -> return True."""
    actor = _make_actor()
    removed: list[str] = []
    monkeypatch.setattr(
        ScopeGraphIndex, "remove_file", lambda _self, p: removed.append(p)
    )
    task = await _drive(
        actor, BackgroundRefreshCommand(stale_files=[], deleted_files=["gone.py"])
    )
    await _stop(actor, task)
    assert removed == ["gone.py"]
    assert actor._updates_processed == 1  # noqa: SLF001


async def test_rebuild_command_swaps_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RebuildCommand -> ``_rebuild_index`` swaps ``self._index`` to a fresh build."""
    actor = _make_actor()
    original = actor._index  # noqa: SLF001
    sentinel = ScopeGraphIndex()
    monkeypatch.setattr(
        IndexManager, "_build_fresh_index", lambda _self: sentinel
    )
    task = await _drive(actor, RebuildCommand())
    assert actor._index is sentinel  # noqa: SLF001
    assert actor._index is not original  # noqa: SLF001
    await _stop(actor, task)


async def test_unknown_command_keeps_loop_alive() -> None:
    """Forward-tolerant default: an unrecognized variant returns True (no break)."""
    actor = _make_actor()

    class _UnknownCommand(IndexCommand):
        """A variant the dispatch table does not recognize."""

    task = await _drive(actor, _UnknownCommand())
    # Actor did not break on the unknown command -> still alive.
    await _stop(actor, task)
    assert task.done()


# === _process_command: request-response dispatch ===========================


async def test_get_snapshot_resolves_to_shared_index() -> None:
    """GetSnapshotCommand -> resolve the shared index (grok ``Arc`` -> identity)."""
    actor = _make_actor()
    index = actor._index  # noqa: SLF001
    fut: asyncio.Future[ScopeGraphIndex] = asyncio.Future()
    task = await _drive(actor, GetSnapshotCommand(response=fut))
    assert fut.result() is index
    await _stop(actor, task)


async def test_get_file_count_resolves_to_index_count() -> None:
    """GetFileCountCommand -> ``self._index.file_count()`` (empty index -> 0)."""
    actor = _make_actor()
    fut: asyncio.Future[int] = asyncio.Future()
    task = await _drive(actor, GetFileCountCommand(response=fut))
    assert fut.result() == 0
    await _stop(actor, task)


async def test_get_stats_resolves_to_index_stats() -> None:
    """GetStatsCommand -> ``IndexStats.new(*stats())`` (empty index -> all zeros)."""
    actor = _make_actor()
    fut: asyncio.Future[IndexStats] = asyncio.Future()
    task = await _drive(actor, GetStatsCommand(response=fut))
    stats = fut.result()
    assert isinstance(stats, IndexStats)
    assert (stats.files, stats.definitions, stats.references) == (0, 0, 0)
    await _stop(actor, task)


async def test_get_query_version_resolves_to_index_version() -> None:
    """GetQueryVersionCommand -> resolve the index's ``query_version`` verbatim."""
    actor = _make_actor()
    index = actor._index  # noqa: SLF001
    fut: asyncio.Future[QueryVersion] = asyncio.Future()
    task = await _drive(actor, GetQueryVersionCommand(response=fut))
    assert fut.result() is index.query_version
    await _stop(actor, task)


async def test_has_definition_resolves_to_bool() -> None:
    """HasDefinitionCommand -> ``self._index.has_definition(symbol)`` (empty -> False)."""
    actor = _make_actor()
    fut: asyncio.Future[bool] = asyncio.Future()
    task = await _drive(actor, HasDefinitionCommand("foo", response=fut))
    assert fut.result() is False
    await _stop(actor, task)


async def test_find_definitions_resolves_to_locations() -> None:
    """FindDefinitionsCommand -> ``find_definitions_smart`` wrapped as SymbolLocation."""
    actor = _make_actor()
    fut: asyncio.Future[list[SymbolLocation]] = asyncio.Future()
    task = await _drive(actor, FindDefinitionsCommand("foo", None, response=fut))
    # Empty index -> find_definitions_smart returns [] -> wrapped as [].
    assert fut.result() == []
    await _stop(actor, task)


async def test_find_definitions_wraps_smart_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``find_definitions_smart`` results are wrapped as ``SymbolLocation.new``."""
    actor = _make_actor()
    monkeypatch.setattr(
        ScopeGraphIndex,
        "find_definitions_smart",
        lambda _self, _symbol, _ctx, _reg: [("def.py", 42)],
    )
    fut: asyncio.Future[list[SymbolLocation]] = asyncio.Future()
    task = await _drive(actor, FindDefinitionsCommand("foo", "ctx.py", response=fut))
    assert fut.result() == [SymbolLocation.new("def.py", 42)]
    await _stop(actor, task)


async def test_find_references_wraps_smart_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``find_references_smart`` results are wrapped as ``SymbolLocation.with_symbol``."""
    actor = _make_actor()
    monkeypatch.setattr(
        ScopeGraphIndex,
        "find_references_smart",
        lambda _self, _symbol, _ctx, _reg: [("bar", "ref.py", 7)],
    )
    fut: asyncio.Future[list[SymbolLocation]] = asyncio.Future()
    task = await _drive(actor, FindReferencesCommand("foo", "ctx.py", response=fut))
    assert fut.result() == [SymbolLocation.with_symbol("ref.py", 7, "bar")]
    await _stop(actor, task)


async def test_goto_definition_row_zero_resolves_to_error() -> None:
    """``row == 0`` -> ``_get_symbol_at_position`` guard fires -> ``QueryError``."""
    actor = _make_actor()
    fut: asyncio.Future[QueryResult | QueryError] = asyncio.Future()
    task = await _drive(actor, GotoDefinitionCommand("a.py", 0, 5, response=fut))
    resolved = fut.result()
    assert isinstance(resolved, QueryError)
    assert not isinstance(resolved, QueryResult)
    assert resolved.row == 0
    await _stop(actor, task)


async def test_goto_definition_col_zero_resolves_to_error() -> None:
    """``col == 0`` -> ``_get_symbol_at_position`` guard fires -> ``QueryError``."""
    actor = _make_actor()
    fut: asyncio.Future[QueryResult | QueryError] = asyncio.Future()
    task = await _drive(actor, GotoDefinitionCommand("a.py", 5, 0, response=fut))
    resolved = fut.result()
    assert isinstance(resolved, QueryError)
    assert resolved.col == 0
    await _stop(actor, task)


async def test_goto_definition_in_bounds_resolves_to_error_stub() -> None:
    """R306f stub: in-bounds also reports ``NoSymbolAtPosition`` (parse is R306g)."""
    actor = _make_actor()
    fut: asyncio.Future[QueryResult | QueryError] = asyncio.Future()
    task = await _drive(actor, GotoDefinitionCommand("a.py", 5, 5, response=fut))
    resolved = fut.result()
    assert isinstance(resolved, QueryError)
    assert resolved.kind == QueryError.KIND_NO_SYMBOL_AT_POSITION
    assert (resolved.row, resolved.col) == (5, 5)
    await _stop(actor, task)


async def test_goto_references_in_bounds_resolves_to_error_stub() -> None:
    """R306f stub: in-bounds GotoReferences also reports ``NoSymbolAtPosition``."""
    actor = _make_actor()
    fut: asyncio.Future[QueryResult | QueryError] = asyncio.Future()
    task = await _drive(
        actor, GotoReferencesCommand("a.py", 5, 5, False, response=fut)
    )
    resolved = fut.result()
    assert isinstance(resolved, QueryError)
    assert resolved.kind == QueryError.KIND_NO_SYMBOL_AT_POSITION
    await _stop(actor, task)


# === _drain_and_apply: coalescing ===========================================


async def test_drain_coalesces_back_to_back_file_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two queued FileEvents coalesce into ONE ``_apply_coalesced`` call."""
    actor = _make_actor()
    captured: list[CoalescedEvents] = []
    monkeypatch.setattr(
        IndexManager,
        "_apply_coalesced",
        lambda _self, c: captured.append(c),
    )
    task = await _drive(
        actor,
        FileEventCommand(FileEvent.modified("a.py")),
        FileEventCommand(FileEvent.created("b.py")),
        ShutdownCommand(),
    )
    await task
    assert len(captured) == 1  # one apply call for both events
    assert captured[0].events == {
        "a.py": FileEventKind.MODIFIED,
        "b.py": FileEventKind.CREATED,
    }


async def test_drain_file_event_batch_into_coalesced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FileEventBatchCommand drains every event into the coalesced map."""
    actor = _make_actor()
    captured: list[CoalescedEvents] = []
    monkeypatch.setattr(
        IndexManager,
        "_apply_coalesced",
        lambda _self, c: captured.append(c),
    )
    task = await _drive(
        actor,
        FileEventBatchCommand(
            [FileEvent.created("a.py"), FileEvent.removed("b.py")]
        ),
        ShutdownCommand(),
    )
    await task
    assert captured[0].events == {
        "a.py": FileEventKind.CREATED,
        "b.py": FileEventKind.REMOVED,
    }


async def test_drain_renamed_splits_into_removed_and_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RENAMED -> from_path=REMOVED + to_path=CREATED in the coalesced map."""
    actor = _make_actor()
    captured: list[CoalescedEvents] = []
    monkeypatch.setattr(
        IndexManager,
        "_apply_coalesced",
        lambda _self, c: captured.append(c),
    )
    task = await _drive(
        actor,
        FileEventCommand(FileEvent.renamed("old.py", "new.py")),
        ShutdownCommand(),
    )
    await task
    assert captured[0].events == {
        "old.py": FileEventKind.REMOVED,
        "new.py": FileEventKind.CREATED,
    }


async def test_drain_recurses_one_deep_on_non_file_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-file command drained mid-batch is processed at depth 1."""
    actor = _make_actor()
    applied: list[CoalescedEvents] = []
    monkeypatch.setattr(
        IndexManager, "_apply_coalesced", lambda _self, c: applied.append(c)
    )
    # RebuildCommand is non-file -> apply fires once, then Rebuild is processed
    # directly (no re-drain). Mock _build_fresh_index to avoid the FS walk.
    monkeypatch.setattr(
        IndexManager, "_build_fresh_index", lambda _self: ScopeGraphIndex()
    )
    task = await _drive(
        actor,
        FileEventCommand(FileEvent.modified("a.py")),
        RebuildCommand(),
        ShutdownCommand(),
    )
    await task
    assert len(applied) == 1  # apply called exactly once for the coalesced batch


async def test_drain_propagates_shutdown_after_coalesce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown drained mid-batch propagates -> ``run_loop`` exits."""
    actor = _make_actor()
    monkeypatch.setattr(IndexManager, "_apply_coalesced", lambda _self, _c: None)
    task = await _drive(
        actor,
        FileEventCommand(FileEvent.modified("a.py")),
        ShutdownCommand(),
    )
    await task  # exits cleanly (Shutdown returned False after apply)
    assert task.done()
    assert actor._beacon.exited is True  # noqa: SLF001


# === _apply_coalesced: gate + mutation + throttle ==========================


async def test_apply_removes_file_on_removed_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REMOVED -> ``_index.remove_file(path)``; ``updates_processed`` increments."""
    actor = _make_actor()
    removed: list[str] = []
    monkeypatch.setattr(
        ScopeGraphIndex, "remove_file", lambda _self, p: removed.append(p)
    )
    coalesced = CoalescedEvents()
    coalesced.add(FileEvent.removed("gone.py"))
    actor._apply_coalesced(coalesced)
    assert removed == ["gone.py"]
    assert actor._updates_processed == 1  # noqa: SLF001


async def test_apply_reindexes_on_created_modified_renamed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CREATED/MODIFIED/RENAMED-to arm -> ``_reindex_file`` (R306f stub)."""
    actor = _make_actor()
    reindexed: list[str] = []
    removed: list[str] = []
    monkeypatch.setattr(
        IndexManager, "_reindex_file", lambda _self, p: reindexed.append(p)
    )
    monkeypatch.setattr(
        ScopeGraphIndex, "remove_file", lambda _self, p: removed.append(p)
    )
    coalesced = CoalescedEvents()
    coalesced.add(FileEvent.created("new.py"))
    coalesced.add(FileEvent.modified("mod.py"))
    coalesced.add(FileEvent.renamed("old.py", "new_name.py"))
    actor._apply_coalesced(coalesced)
    # renamed splits: old.py=REMOVED (remove), new_name.py=CREATED (reindex).
    assert removed == ["old.py"]
    assert reindexed == ["new.py", "mod.py", "new_name.py"]
    assert actor._updates_processed == 4  # noqa: SLF001


async def test_apply_skips_hidden_dir_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_should_index`` gate: ``.git/config`` is skipped (hidden dir)."""
    actor = _make_actor()
    reindexed: list[str] = []
    monkeypatch.setattr(
        IndexManager, "_reindex_file", lambda _self, p: reindexed.append(p)
    )
    coalesced = CoalescedEvents()
    coalesced.add(FileEvent.modified(".git/config"))
    coalesced.add(FileEvent.modified("src/.cache/x.py"))
    coalesced.add(FileEvent.modified("visible.py"))
    actor._apply_coalesced(coalesced)
    assert reindexed == ["visible.py"]
    assert actor._updates_processed == 1  # noqa: SLF001


async def test_apply_throttles_cache_save_within_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Within 30s of the last save, ``_save_cache`` is NOT called."""
    actor = _make_actor(
        config=IndexManagerConfig.new(".").with_cache_path("cache.bin")
    )
    saved: list[str] = []
    monkeypatch.setattr(im, "save_index", lambda _path, _index: saved.append(_path))
    actor._last_cache_save = time.monotonic()  # noqa: SLF001 -- just saved
    coalesced = CoalescedEvents()
    coalesced.add(FileEvent.modified("a.py"))
    actor._apply_coalesced(coalesced)
    assert saved == []  # throttled: within the 30s window
    assert actor._updates_processed == 1  # noqa: SLF001


async def test_apply_saves_cache_after_throttle_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the 30s window, ``_save_cache`` runs once + reseeds the timestamp."""
    actor = _make_actor(
        config=IndexManagerConfig.new(".").with_cache_path("cache.bin")
    )
    saved: list[str] = []
    monkeypatch.setattr(im, "save_index", lambda _path, _index: saved.append(_path))
    before = time.monotonic()
    actor._last_cache_save = before - 31.0  # noqa: SLF001 -- past the window
    coalesced = CoalescedEvents()
    coalesced.add(FileEvent.modified("a.py"))
    actor._apply_coalesced(coalesced)
    assert saved == ["cache.bin"]
    assert actor._last_cache_save >= before  # noqa: SLF001 -- reseeded


async def test_apply_skips_cache_save_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``save_to_cache=False`` -> no save even past the window."""
    actor = _make_actor()  # factory default: without_cache_save
    saved: list[str] = []
    monkeypatch.setattr(im, "save_index", lambda _path, _index: saved.append(_path))
    actor._last_cache_save = 0.0  # noqa: SLF001 -- past the window
    coalesced = CoalescedEvents()
    coalesced.add(FileEvent.modified("a.py"))
    actor._apply_coalesced(coalesced)
    assert saved == []


# === _should_index: hidden-dir gate ========================================


async def test_should_index_accepts_normal_path() -> None:
    """A normal path passes the gate (R306f subset; binary/size gates land R306g)."""
    actor = _make_actor()
    assert actor._should_index("src/main.py") is True  # noqa: SLF001


async def test_should_index_rejects_hidden_dir() -> None:
    """Any path segment starting with ``.`` (len > 1) is gated out."""
    actor = _make_actor()
    assert actor._should_index(".git/config") is False  # noqa: SLF001
    assert actor._should_index("src/.cache/x.py") is False  # noqa: SLF001
    assert actor._should_index("a/.hidden/b.py") is False  # noqa: SLF001


async def test_should_index_accepts_single_dot_segment() -> None:
    """A bare ``.`` (current-dir) is NOT a hidden dir -- len must exceed 1."""
    actor = _make_actor()
    assert actor._should_index("./a.py") is True  # noqa: SLF001


# === _get_symbol_at_position: error guards =================================


async def test_get_symbol_row_zero_returns_error() -> None:
    """``row == 0`` -> ``QueryError`` ``NoSymbolAtPosition`` (1-indexed guard)."""
    actor = _make_actor()
    result = actor._get_symbol_at_position("a.py", 0, 5)  # noqa: SLF001
    assert isinstance(result, QueryError)
    assert result.kind == QueryError.KIND_NO_SYMBOL_AT_POSITION
    assert (result.row, result.col) == (0, 5)


async def test_get_symbol_col_zero_returns_error() -> None:
    """``col == 0`` -> ``QueryError`` ``NoSymbolAtPosition``."""
    actor = _make_actor()
    result = actor._get_symbol_at_position("a.py", 5, 0)  # noqa: SLF001
    assert isinstance(result, QueryError)
    assert (result.row, result.col) == (5, 0)


async def test_get_symbol_in_bounds_returns_error_stub() -> None:
    """R306f stub: in-bounds also returns ``NoSymbolAtPosition`` (parse is R306g)."""
    actor = _make_actor()
    result = actor._get_symbol_at_position("a.py", 5, 5)  # noqa: SLF001
    assert isinstance(result, QueryError)
    assert result.kind == QueryError.KIND_NO_SYMBOL_AT_POSITION


# === _save_cache: gate ======================================================


async def test_save_cache_noop_when_save_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``save_to_cache=False`` -> ``save_index`` never reached."""
    actor = _make_actor(
        config=IndexManagerConfig.new(".").with_cache_path("cache.bin").without_cache_save()
    )
    saved: list[str] = []
    monkeypatch.setattr(im, "save_index", lambda _path, _index: saved.append(_path))
    actor._save_cache()  # noqa: SLF001
    assert saved == []


async def test_save_cache_noop_when_no_cache_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``cache_path is None`` -> ``save_index`` never reached."""
    actor = _make_actor()  # cache_path=None
    saved: list[str] = []
    monkeypatch.setattr(im, "save_index", lambda _path, _index: saved.append(_path))
    actor._save_cache()  # noqa: SLF001
    assert saved == []


async def test_save_cache_writes_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``save_to_cache=True`` + ``cache_path`` set -> ``save_index`` called once."""
    actor = _make_actor(
        config=IndexManagerConfig.new(".").with_cache_path("cache.bin")
    )
    saved: list[tuple[str, ScopeGraphIndex]] = []
    monkeypatch.setattr(im, "save_index", lambda _path, _index: saved.append((_path, _index)))
    actor._save_cache()  # noqa: SLF001
    assert len(saved) == 1
    assert saved[0][0] == "cache.bin"
    assert saved[0][1] is actor._index  # noqa: SLF001


# === _resolve_response: done-guard =========================================


async def test_resolve_response_sets_value_on_pending_future() -> None:
    """A pending Future receives the value verbatim."""
    fut: asyncio.Future[object] = asyncio.Future()
    im._resolve_response(fut, "v")  # noqa: SLF001
    assert fut.result() == "v"


async def test_resolve_response_skips_done_future() -> None:
    """An already-done Future is left untouched (no ``InvalidStateError``)."""
    fut: asyncio.Future[object] = asyncio.Future()
    fut.set_result("original")
    im._resolve_response(fut, "ignored")  # noqa: SLF001
    assert fut.result() == "original"
