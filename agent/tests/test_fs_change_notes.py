"""v1.5.1 — fs-bus → agent ephemeral foreign-change notes.

``fsnotify.notes.foreign_change_note`` aggregates file writes by *other*
in-flight runs into one short system note that ``AgentCore`` appends to
the LLM payload only (never persisted, never acknowledged — the same
ephemeral contract as the v1.1.1 nudge). These tests pin:

* watermark / self filtering (both ``None`` = the main agent's own
  writes; a sub-agent *does* see the main agent's writes)
* per-path de-duplication, overflow capping, sandbox path projection
* ``drain_foreign_changes`` fail-open semantics and watermark bootstrap
* the AgentCore integration: a foreign event landing mid-run appears in
  the *next* LLM payload but never in the persisted history
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.core import AgentCore
from minimax_code.agent.llm import StreamChunk
from minimax_code.agent.tools import Tool, ToolRegistry, ToolResult
from minimax_code.fsnotify.bus import FsEventBus
from minimax_code.fsnotify.events import FsEvent, FsEventKind
from minimax_code.fsnotify.notes import (
    _display_path,
    drain_foreign_changes,
    foreign_change_note,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(
    seq: int,
    paths: tuple[str, ...],
    *,
    cause: str = "write_file",
    kind: FsEventKind = FsEventKind.MODIFIED,
    run_id: str | None = None,
) -> FsEvent:
    return FsEvent(
        kind=kind,
        paths=paths,
        cause=cause,
        seq=seq,
        monotonic_ns=0,
        attributes=(("run_id", run_id),) if run_id is not None else (),
    )


# ---------------------------------------------------------------------------
# foreign_change_note — filtering & aggregation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_note_skips_watermark_and_own_run() -> None:
    events = [
        _event(1, ("old.txt",), run_id="other"),
        _event(2, ("mine.txt",), run_id="run_a"),
        _event(3, ("theirs.txt",), run_id="other"),
    ]
    note, wm = foreign_change_note(events, self_run_id="run_a", watermark=1)
    assert wm == 3
    assert note is not None
    assert "old.txt" not in note  # at/below watermark
    assert "mine.txt" not in note  # own run
    assert "theirs.txt" in note


@pytest.mark.asyncio
async def test_main_agent_hides_own_unattributed_writes() -> None:
    # The main agent's writes carry no run_id attribute; self_run_id is
    # None too — both sides None means "mine".
    events = [
        _event(1, ("own.txt",), run_id=None),
        _event(2, ("sub.txt",), run_id="run_sub"),
    ]
    note, wm = foreign_change_note(events, self_run_id=None, watermark=0)
    assert wm == 2
    assert note is not None
    assert "own.txt" not in note
    assert "sub.txt" in note


@pytest.mark.asyncio
async def test_subagent_sees_main_agent_writes() -> None:
    events = [_event(1, ("boss.txt",), run_id=None)]
    note, _ = foreign_change_note(events, self_run_id="run_a", watermark=0)
    assert note is not None
    assert "boss.txt" in note


@pytest.mark.asyncio
async def test_all_filtered_still_advances_watermark() -> None:
    events = [_event(5, ("mine.txt",), run_id="run_a")]
    note, wm = foreign_change_note(events, self_run_id="run_a", watermark=0)
    assert note is None
    assert wm == 5  # skipped events must not be re-scanned forever


@pytest.mark.asyncio
async def test_dedupe_keeps_latest_per_path() -> None:
    events = [
        _event(1, ("f.txt",), kind=FsEventKind.CREATED),
        _event(2, ("f.txt",), kind=FsEventKind.REMOVED, cause="other_tool"),
    ]
    note, _ = foreign_change_note(
        events, self_run_id="run_a", watermark=0
    )
    assert note is not None
    assert "removed" in note
    assert "other_tool" in note
    assert note.count("f.txt") == 1


@pytest.mark.asyncio
async def test_sandbox_mirror_path_projected() -> None:
    events = [
        _event(
            1,
            ("D:/proj/.minimax/sandboxes/run_x/src/a.py",),
            run_id="run_x",
        )
    ]
    note, _ = foreign_change_note(events, self_run_id="main", watermark=0)
    assert note is not None
    assert "src/a.py (sandboxed by run_x)" in note
    assert ".minimax/sandboxes" not in note


def test_display_path_handles_backslashes_and_edges() -> None:
    assert _display_path("plain/rel.txt") == "plain/rel.txt"
    assert (
        _display_path("D:\\proj\\.minimax\\sandboxes\\r1\\b.py")
        == "b.py (sandboxed by r1)"
    )
    # Malformed sandbox tails pass through untouched.
    assert _display_path("x/.minimax/sandboxes/") == "x/.minimax/sandboxes/"


@pytest.mark.asyncio
async def test_overflow_caps_at_eight_entries() -> None:
    events = [
        _event(i + 1, (f"f{i}.txt",), run_id="other") for i in range(10)
    ]
    note, _ = foreign_change_note(events, self_run_id="me", watermark=0)
    assert note is not None
    assert "f7.txt" in note
    assert "f8.txt" not in note
    assert "and 2 more" in note


@pytest.mark.asyncio
async def test_no_foreign_events_returns_none() -> None:
    note, wm = foreign_change_note([], self_run_id=None, watermark=9)
    assert note is None
    assert wm == 9


# ---------------------------------------------------------------------------
# drain_foreign_changes — fail-open wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_bootstraps_watermark_without_note() -> None:
    bus = FsEventBus()
    bus.emit("modified", ["old.txt"], "write_file", run_id="other")
    note, wm = drain_foreign_changes(bus, self_run_id=None, watermark=None)
    # Bootstrap: "now" means the pre-existing event is history.
    assert note is None
    assert wm == 1
    # A new foreign event after the bootstrap does surface.
    bus.emit("modified", ["new.txt"], "write_file", run_id="other")
    note, wm = drain_foreign_changes(bus, self_run_id=None, watermark=wm)
    assert note is not None
    assert "new.txt" in note
    assert wm == 2


@pytest.mark.asyncio
async def test_drain_fails_open() -> None:
    assert drain_foreign_changes(None, self_run_id=None, watermark=3) == (
        None,
        3,
    )

    class BrokenBus:
        def recent(self, **_kw):
            raise RuntimeError("boom")

    assert drain_foreign_changes(
        BrokenBus(), self_run_id=None, watermark=3
    ) == (None, 3)


# ---------------------------------------------------------------------------
# AgentCore integration — ephemeral injection into the next payload
# ---------------------------------------------------------------------------


class _FakeLLM:
    """Records every payload; returns queued chunk lists per call."""

    def __init__(self, responses: list[list[StreamChunk]]) -> None:
        self._queue = list(responses)
        self.payloads: list[list[dict[str, Any]]] = []

    async def stream_chat(self, messages, **_kwargs):  # type: ignore[no-untyped-def]
        self.payloads.append([dict(m) for m in messages])
        for chunk in self._queue.pop(0):
            yield chunk


class _ForeignWriterTool(Tool):
    """On dispatch, simulates another run writing a workspace file."""

    name = "touch_foreign"
    description = "emit one foreign-run fs event"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self, bus: FsEventBus) -> None:
        self.bus = bus

    async def run(self, **_kwargs: Any) -> ToolResult:
        self.bus.emit("modified", ["shared.txt"], "write_file", run_id="run_x")
        return ToolResult.ok(output={"touched": True})


def _tool_call_chunk(name: str, args: dict) -> StreamChunk:
    return StreamChunk(
        tool_call_deltas=[
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args),
                },
            }
        ],
        finish_reason="tool_calls",
        usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    )


@pytest.mark.asyncio
async def test_core_injects_foreign_note_ephemerally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus = FsEventBus()
    monkeypatch.setattr("minimax_code.app.ensure_fs_bus", lambda: bus)

    fake = _FakeLLM(
        [
            [_tool_call_chunk("touch_foreign", {})],
            [
                StreamChunk(delta="done"),
                StreamChunk(
                    finish_reason="stop",
                    usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                ),
            ],
        ]
    )
    persisted: list[dict[str, Any]] = []

    reg = ToolRegistry()
    reg.register(_ForeignWriterTool(bus))
    core = AgentCore(
        llm=fake,
        registry=reg,
        persist_message=lambda m: persisted.append(dict(m)),
    )

    result = await core.run(session_id="s1", user_message="run it")

    assert result.final_text == "done"
    # First payload: no note yet (the foreign event hasn't happened).
    assert not any(
        "Files changed by other agents" in str(m.get("content"))
        for m in fake.payloads[0]
    )
    # Second payload: the note rides along, at the tail.
    second = fake.payloads[1]
    note_msgs = [
        m
        for m in second
        if "Files changed by other agents" in str(m.get("content"))
    ]
    assert len(note_msgs) == 1
    assert "shared.txt" in str(note_msgs[0]["content"])
    assert second[-1] is note_msgs[0]
    # Ephemeral contract: nothing persisted carries the note.
    assert not any(
        "Files changed by other agents" in str(m.get("content"))
        for m in persisted
    )
