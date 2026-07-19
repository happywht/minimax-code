"""Tests for the R114 tool_runtime notification module.

Covers the migration of ``xai-tool-runtime/src/notification.rs``. The Rust
source carries no inline ``#[test]`` block, so these are Python-style
semantic-equivalence checks:

- ``BashNotificationBase.output_lossy``: lossy UTF-8 decode (invalid bytes
  -> ``U+FFFD``).
- ``BashExecutionComplete.was_signaled``: ``signal`` is / is not set.
- ``#[serde(flatten)]`` -> dataclass inheritance: inherited fields are
  accessible on the subclass; two equal-by-field instances compare equal.
- ``FileWritten``: ``previous_content`` / ``is_new_file`` defaults.
- ``TaskKind`` StrEnum: snake_case values + ``TaskSnapshot`` default kind.
- ``TaskSnapshot.duration_secs``: completed (end - start) vs running (now).
- ``ToolNotification`` tagged union: classmethod constructors set
  ``kind`` + ``payload``; ``variant_name`` returns ``kind``.
- ``ToolNotificationHandle``: ``channel`` pairs sender + receiver queue;
  ``send`` best-effort; ``send_*`` typed helpers stay in lockstep with the
  enum; ``noop`` swallows; ``eq=False`` -> identity-only; ``new`` /
  ``from_sender`` aliases wrap the same queue (Rust ``Clone`` sharing).
"""

from __future__ import annotations

import asyncio

import pytest

from minimax_code.tool_runtime.notification import (
    BashExecutionBackgrounded,
    BashExecutionComplete,
    BashExecutionFailed,
    BashExecutionTimeout,
    BashNotificationBase,
    BashOutputChunk,
    FileRead,
    FileWritten,
    LspServerReady,
    MonitorEvent,
    PlanModeExited,
    ScheduledTaskCreated,
    TaskKind,
    TaskSnapshot,
    ToolNotification,
    ToolNotificationHandle,
)

# ---------------------------------------------------------------------------
# BashNotificationBase.output_lossy.
# ---------------------------------------------------------------------------


def test_bash_notification_base_output_lossy_decodes_valid_bytes():
    base = BashNotificationBase(
        tool_call_id="c1", command="ls", output=b"hello",
        total_bytes=5, truncated=False, cwd="/tmp",
    )
    assert base.output_lossy() == "hello"


def test_bash_notification_base_output_lossy_replaces_invalid_utf8():
    # \xff\xfe are invalid UTF-8 lead bytes -> U+FFFD each (Rust
    # String::from_utf8_lossy).
    base = BashNotificationBase(
        tool_call_id="c1", command="ls", output=b"a\xff\xfeb",
        total_bytes=4, truncated=False, cwd="/tmp",
    )
    rendered = base.output_lossy()
    assert rendered[0] == "a"
    assert rendered[-1] == "b"
    # The two invalid bytes each become the replacement character.
    assert rendered[1:3] == "��"


# ---------------------------------------------------------------------------
# BashExecutionComplete.was_signaled.
# ---------------------------------------------------------------------------


def _bash_complete(**overrides) -> BashExecutionComplete:
    fields = dict(
        tool_call_id="c1", command="ls", output=b"out",
        total_bytes=3, truncated=False, cwd="/tmp",
        exit_code=0, signal=None,
    )
    fields.update(overrides)
    return BashExecutionComplete(**fields)


def test_bash_execution_complete_was_signaled_false_for_normal_exit():
    assert _bash_complete(exit_code=0, signal=None).was_signaled() is False


def test_bash_execution_complete_was_signaled_true_when_signal_set():
    assert _bash_complete(signal="SIGKILL").was_signaled() is True


# ---------------------------------------------------------------------------
# #[serde(flatten)] -> dataclass inheritance.
# ---------------------------------------------------------------------------


def test_flatten_inherited_base_fields_accessible_on_subclass():
    chunk = BashOutputChunk(
        tool_call_id="c1", command="ls", output=b"x",
        total_bytes=1, truncated=False, cwd="/tmp",
    )
    # The base fields live on the subclass instance (Rust flatten).
    assert chunk.tool_call_id == "c1"
    assert chunk.command == "ls"
    assert chunk.cwd == "/tmp"
    # The subclass is still a BashNotificationBase.
    assert isinstance(chunk, BashNotificationBase)


def test_flatten_subclass_equality_is_field_wise_including_base():
    a = _bash_complete(exit_code=2)
    b = _bash_complete(exit_code=2)
    c = _bash_complete(exit_code=0)
    assert a == b  # all fields incl. base match
    assert a != c  # exit_code differs


def test_bash_execution_failed_has_no_base_struct():
    # BashExecutionFailed carries its own fields (no flatten) — it is NOT a
    # BashNotificationBase subclass.
    failed = BashExecutionFailed(
        tool_call_id="c1", command="bad", cwd="/tmp", error="spawn failed",
    )
    assert failed.error == "spawn failed"
    assert not isinstance(failed, BashNotificationBase)


# ---------------------------------------------------------------------------
# FileWritten / FileRead defaults.
# ---------------------------------------------------------------------------


def test_file_written_defaults_previous_content_none_and_is_new_file_false():
    w = FileWritten(
        tool_call_id="c1", absolute_path="/tmp/a.txt", content="hi",
    )
    assert w.previous_content is None
    assert w.is_new_file is False


def test_file_read_carries_path_only():
    r = FileRead(tool_call_id="c1", absolute_path="/tmp/a.txt")
    assert r.absolute_path == "/tmp/a.txt"


# ---------------------------------------------------------------------------
# TaskKind StrEnum + TaskSnapshot.
# ---------------------------------------------------------------------------


def test_task_kind_snake_case_wire_values():
    assert TaskKind.BASH == "bash"
    assert TaskKind.MONITOR == "monitor"
    # StrEnum members ARE str (wire-shaped).
    assert isinstance(TaskKind.BASH, str)


def test_task_snapshot_default_kind_is_bash():
    snap = TaskSnapshot(
        task_id="t1", command="ls", cwd="/tmp", start_time=0.0,
        output="", output_file="/tmp/log", truncated=False, completed=False,
    )
    assert snap.kind is TaskKind.BASH


def test_task_snapshot_duration_secs_completed_is_end_minus_start():
    snap = TaskSnapshot(
        task_id="t1", command="ls", cwd="/tmp", start_time=1000.0,
        output="", output_file="/tmp/log", truncated=False, completed=True,
        end_time=1005.0,
    )
    assert snap.duration_secs() == 5.0


def test_task_snapshot_duration_secs_running_falls_back_to_now():
    snap = TaskSnapshot(
        task_id="t1", command="ls", cwd="/tmp", start_time=1_000_000_000.0,
        output="", output_file="/tmp/log", truncated=False, completed=False,
        # end_time None -> duration uses time.time(); must be > 0 for a
        # start far in the past.
    )
    assert snap.duration_secs() > 0.0


def test_task_snapshot_duration_secs_negative_clamped_to_zero():
    # A future start_time with an earlier end_time would compute negative;
    # Rust clamps via unwrap_or(0.0) on the duration error path.
    snap = TaskSnapshot(
        task_id="t1", command="ls", cwd="/tmp", start_time=2000.0,
        output="", output_file="/tmp/log", truncated=False, completed=True,
        end_time=1000.0,
    )
    assert snap.duration_secs() == 0.0


# ---------------------------------------------------------------------------
# ToolNotification tagged union.
# ---------------------------------------------------------------------------


def test_tool_notification_classmethod_sets_kind_and_payload():
    complete = _bash_complete()
    n = ToolNotification.BashExecutionComplete(complete)
    assert n.kind == "BashExecutionComplete"
    assert n.payload is complete


def test_tool_notification_variant_name_returns_kind():
    n = ToolNotification.LspServerReady(LspServerReady(server_name="rust-analyzer"))
    assert n.variant_name() == "LspServerReady"
    assert n.variant_name() == n.kind


def test_tool_notification_distinct_variants_have_distinct_kinds():
    a = ToolNotification.MonitorEvent(
        MonitorEvent(task_id="t", description="d", event_text="<x/>", raw_text="x")
    )
    b = ToolNotification.ScheduledTaskCreated(
        ScheduledTaskCreated(
            task_id="t", prompt="p", human_schedule="daily",
            next_fire_at="2026-08-01T00:00:00Z",
        )
    )
    c = ToolNotification.PlanModeExited(
        PlanModeExited(tool_call_id="c1", plan_content="plan", plan_file_path="/p")
    )
    assert {a.kind, b.kind, c.kind} == {
        "MonitorEvent", "ScheduledTaskCreated", "PlanModeExited",
    }


def test_tool_notification_file_written_variant():
    w = FileWritten(
        tool_call_id="c1", absolute_path="/tmp/a", content="x",
        previous_content="y", is_new_file=False,
    )
    n = ToolNotification.FileWritten(w)
    assert n.kind == "FileWritten"
    assert n.payload.is_new_file is False


# ---------------------------------------------------------------------------
# ToolNotificationHandle — channel + send + noop.
# ---------------------------------------------------------------------------


def _drain(queue: asyncio.Queue) -> list[ToolNotification]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def test_handle_channel_pairs_sender_and_receiver_queue():
    handle, queue = ToolNotificationHandle.channel()
    handle.send(ToolNotification.LspServerReady(LspServerReady(server_name="s")))
    assert queue.qsize() == 1
    (received,) = _drain(queue)
    assert received.kind == "LspServerReady"


def test_handle_noop_swallows_send_without_queue_to_drain():
    # noop builds a handle whose receiver half is dropped; send must not raise.
    handle = ToolNotificationHandle.noop()
    handle.send(ToolNotification.LspServerReady(LspServerReady(server_name="s")))


def test_handle_send_multiple_preserves_order():
    handle, queue = ToolNotificationHandle.channel()
    n1 = ToolNotification.LspServerReady(LspServerReady(server_name="s"))
    n2 = ToolNotification.LspServerReady(LspServerReady(server_name="t"))
    handle.send(n1)
    handle.send(n2)
    received = _drain(queue)
    assert received == [n1, n2]


# ---------------------------------------------------------------------------
# ToolNotificationHandle — typed send_* helpers in lockstep with the enum.
# ---------------------------------------------------------------------------


def test_send_bash_complete_helper_emits_bash_execution_complete():
    handle, queue = ToolNotificationHandle.channel()
    complete = _bash_complete(exit_code=0)
    handle.send_bash_complete(complete)
    (received,) = _drain(queue)
    assert received.kind == "BashExecutionComplete"
    assert received.payload is complete


def test_send_file_written_helper_emits_file_written():
    handle, queue = ToolNotificationHandle.channel()
    w = FileWritten(
        tool_call_id="c1", absolute_path="/tmp/a", content="x",
    )
    handle.send_file_written(w)
    (received,) = _drain(queue)
    assert received.kind == "FileWritten"


def test_send_monitor_event_helper_emits_monitor_event():
    handle, queue = ToolNotificationHandle.channel()
    ev = MonitorEvent(task_id="t", description="d", event_text="<x/>", raw_text="x")
    handle.send_monitor_event(ev)
    (received,) = _drain(queue)
    assert received.kind == "MonitorEvent"


def test_send_task_complete_helper_emits_task_completed():
    handle, queue = ToolNotificationHandle.channel()
    snap = TaskSnapshot(
        task_id="t1", command="ls", cwd="/tmp", start_time=0.0,
        output="", output_file="/tmp/log", truncated=False, completed=True,
    )
    handle.send_task_complete(snap)
    (received,) = _drain(queue)
    assert received.kind == "TaskCompleted"
    assert received.payload is snap


# ---------------------------------------------------------------------------
# ToolNotificationHandle — eq=False (identity) + new/from_sender sharing.
# ---------------------------------------------------------------------------


def test_handle_has_no_partialeq_identity_only():
    queue: asyncio.Queue = asyncio.Queue()
    a = ToolNotificationHandle.new(queue)
    b = ToolNotificationHandle.new(queue)
    # eq=False -> `==` falls back to object identity.
    assert a != b
    assert a == a


def test_handle_new_and_from_sender_are_aliases_wrapping_same_queue():
    # Rust `new` and `from_sender` are aliases; both wrap a shared sender.
    queue: asyncio.Queue = asyncio.Queue()
    a = ToolNotificationHandle.new(queue)
    b = ToolNotificationHandle.from_sender(queue)
    n = ToolNotification.LspServerReady(LspServerReady(server_name="s"))
    a.send(n)
    b.send(n)
    # Two handles sharing one queue -> both sends land in the same queue.
    assert queue.qsize() == 2


# ---------------------------------------------------------------------------
# BashExecutionTimeout / Backgrounded inheritance sanity (flatten shapes).
# ---------------------------------------------------------------------------


def test_bash_execution_timeout_inherits_base_and_adds_durations():
    t = BashExecutionTimeout(
        tool_call_id="c1", command="sleep", output=b"",
        total_bytes=0, truncated=False, cwd="/tmp",
        elapsed=5.0, timeout=3.0,
    )
    assert isinstance(t, BashNotificationBase)
    assert t.elapsed == 5.0
    assert t.timeout == 3.0
    assert t.tool_call_id == "c1"


def test_bash_execution_backgrounded_inherits_base_and_adds_task_fields():
    bg = BashExecutionBackgrounded(
        tool_call_id="c1", command="serve", output=b"x",
        total_bytes=1, truncated=False, cwd="/tmp",
        output_file="/tmp/log", task_id="task-9",
    )
    assert isinstance(bg, BashNotificationBase)
    assert bg.task_id == "task-9"
    assert bg.output_file == "/tmp/log"


# ---------------------------------------------------------------------------
# asyncio import smoke — the handle is executor-neutral (no running loop
# needed to construct or send; only to `await queue.get()`).
# ---------------------------------------------------------------------------


def test_handle_constructs_without_running_loop():
    # Building + sending must not require a running event loop (matches the
    # mpsc::UnboundedSender non-async send surface).
    handle = ToolNotificationHandle.noop()
    assert handle is not None


@pytest.mark.asyncio
async def test_handle_queue_consumer_drains_via_await_get():
    handle, queue = ToolNotificationHandle.channel()
    handle.send(ToolNotification.LspServerReady(LspServerReady(server_name="s")))
    received = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert received.kind == "LspServerReady"
