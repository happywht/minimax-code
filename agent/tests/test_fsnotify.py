"""Unit tests for the filesystem change bus (R16).

Covers:
* :mod:`events` — FsEvent is frozen pure data.
* :class:`FsEventBus` — emit → recent + subscriber fan-out, fail-open,
  causal order, filtering, unsubscribe, capacity guards.
* integration — WriteFileTool / EditFileTool emit on success; a rejected
  (path-unsafe) write emits nothing.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from minimax_code.app import set_fs_bus
from minimax_code.fsnotify import FsEvent, FsEventBus, FsEventKind


@pytest.fixture(autouse=True)
def _reset_fs_bus() -> None:
    """Isolate each test from the process-wide bus singleton."""
    set_fs_bus(None)
    yield
    set_fs_bus(None)


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


def test_fs_event_is_frozen_pure_data() -> None:
    e = FsEvent(
        kind=FsEventKind.CREATED,
        paths=("/a/b.txt",),
        cause="write_file",
        seq=7,
        monotonic_ns=0,
        attributes=(("size_bytes", "42"),),
    )
    assert e.kind is FsEventKind.CREATED
    assert e.attribute("size_bytes") == "42"
    assert e.attribute("missing", "fallback") == "fallback"
    with pytest.raises(FrozenInstanceError):
        e.seq = 99  # frozen dataclass


# ---------------------------------------------------------------------------
# bus — emit + recent + causal order
# ---------------------------------------------------------------------------


def test_bus_emit_appends_to_recent_and_returns_event() -> None:
    bus = FsEventBus()
    ev = bus.emit("created", ["/x.txt"], "write_file", size_bytes=3)
    assert ev is not None
    assert ev.kind is FsEventKind.CREATED
    assert ev.attribute("size_bytes") == "3"
    assert bus.recent() == [ev]


def test_bus_preserves_causal_order() -> None:
    bus = FsEventBus()
    seqs: list[int] = []
    for i in range(5):
        ev = bus.emit("modified", [f"/{i}"], "edit_file")
        assert ev is not None
        seqs.append(ev.seq)
    assert seqs == sorted(seqs) and len(set(seqs)) == 5  # strictly monotonic
    recent = bus.recent()
    assert [e.paths[0] for e in recent] == [f"/{i}" for i in range(5)]


class _BrokenRecent:
    """Stand-in for a deque whose ``append`` always blows up."""

    def append(self, _item: object) -> None:
        raise RuntimeError("append exploded")

    def __iter__(self):
        return iter(())

    def clear(self) -> None:
        pass

    def __len__(self) -> int:
        return 0


def test_bus_emit_is_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = FsEventBus()
    monkeypatch.setattr(bus, "_recent", _BrokenRecent())
    # Must not raise even though storage is broken; returns None.
    assert bus.emit("created", ["/x"], "write_file") is None


def test_bus_subscribe_receives_events() -> None:
    bus = FsEventBus()
    q = bus.subscribe()
    assert bus.subscriber_count == 1
    bus.emit("created", ["/a"], "write_file")
    bus.emit("modified", ["/b"], "edit_file")
    first = q.get_nowait()
    second = q.get_nowait()
    assert first.paths == ("/a",)
    assert second.kind is FsEventKind.MODIFIED


def test_bus_slow_subscriber_drops_not_blocks() -> None:
    bus = FsEventBus(subscriber_maxsize=1)
    q = bus.subscribe()
    bus.emit("created", ["/1"], "write_file")  # fills the queue
    overflow = bus.emit("created", ["/2"], "write_file")  # dropped for subscriber
    assert overflow is not None  # still returned + buffered
    assert bus.buffered_count == 2  # recent stream keeps both
    assert q.qsize() == 1  # subscriber queue held only the first


def test_bus_recent_filters() -> None:
    bus = FsEventBus()
    bus.emit("created", ["/a"], "write_file", session_id="s1")
    bus.emit("modified", ["/b"], "edit_file", session_id="s1")
    bus.emit("created", ["/c"], "write_file", session_id="s2")
    assert len(bus.recent(kind="created")) == 2
    assert len(bus.recent(cause="edit_file")) == 1
    assert len(bus.recent(session_id="s2")) == 1
    assert len(bus.recent(kind="created", session_id="s1")) == 1


def test_bus_unsubscribe_stops_delivery() -> None:
    bus = FsEventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    assert bus.subscriber_count == 0
    bus.emit("created", ["/a"], "write_file")
    assert q.empty()


def test_bus_rejects_bad_capacity() -> None:
    with pytest.raises(ValueError):
        FsEventBus(capacity=0)
    with pytest.raises(ValueError):
        FsEventBus(subscriber_maxsize=0)


# ---------------------------------------------------------------------------
# integration — tools emit on success
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the path-safety policy at ``tmp_path`` for the duration of the test."""
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_write_file_emits_event(workspace: Path) -> None:
    from minimax_code.agent.tools.file_ops import WriteFileTool

    bus = FsEventBus()
    set_fs_bus(bus)
    tool = WriteFileTool()
    result = await tool.run(path="new.txt", content="hello")
    assert result.success
    events = bus.recent()
    assert len(events) == 1
    assert events[0].kind is FsEventKind.CREATED
    assert events[0].cause == "write_file"
    assert events[0].paths[0].endswith("new.txt")
    assert events[0].attribute("size_bytes") == "5"


@pytest.mark.asyncio
async def test_edit_file_emits_event(workspace: Path) -> None:
    from minimax_code.agent.tools.edit import EditFileTool

    (workspace / "e.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    bus = FsEventBus()
    set_fs_bus(bus)
    tool = EditFileTool()
    result = await tool.run(path="e.txt", old_string="alpha", new_string="gamma")
    assert result.success
    events = bus.recent()
    assert len(events) == 1
    assert events[0].kind is FsEventKind.MODIFIED
    assert events[0].cause == "edit_file"


@pytest.mark.asyncio
async def test_rejected_write_emits_nothing(workspace: Path) -> None:
    from minimax_code.agent.tools.file_ops import WriteFileTool

    bus = FsEventBus()
    set_fs_bus(bus)
    tool = WriteFileTool()
    result = await tool.run(path="../../evil.txt", content="x")
    assert not result.success
    assert bus.recent() == []
