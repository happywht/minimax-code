"""Tool notifications — typed execution-visibility messages (R114).

Fusion of grok-build's ``xai-tool-runtime/src/notification.rs``. A
:class:`ToolNotification` is a typed message a running tool emits to
subscribers (TUI, gateway, audit log, ...) for live visibility into
execution. Each variant has a parallel ``send_*`` convenience on
:class:`ToolNotificationHandle`; the two surfaces are kept in lockstep —
when adding a variant, add the constructor too.

Rust ``futures::channel::mpsc`` -> Python ``asyncio.Queue``
-----------------------------------------------------------

The Rust handle wraps an ``mpsc::UnboundedSender<ToolNotification>`` so the
trait crate stays runtime-neutral (no tokio pin). The Python landing wraps
an :class:`asyncio.Queue` — the standard-library, executor-neutral
equivalent. Sends are non-blocking (``put_nowait``) and best-effort: a
full queue (impossible for the default unbounded queue, but reserved for a
future bounded consumer) is silently dropped, matching the Rust
fire-and-forget convention. The handle has no ``PartialEq`` derive (only
``Clone``), so it compares by identity — :func:`dataclasses.dataclass` with
``eq=False`` mirrors that.

serde ``#[serde(flatten)]`` -> dataclass inheritance
----------------------------------------------------

The bash variants share a ``BashNotificationBase`` payload hoisted via
``#[serde(flatten)]``: on the wire the base fields sit next to the
variant's own fields at the same JSON level. Python :func:`dataclasses.dataclass`
inheritance is the faithful landing — inherited fields live on the instance
exactly alongside the subclass fields, so ``dataclasses.asdict`` and
field-wise ``__eq__`` behave like Rust's flattened serde + derived
``PartialEq``.

serde ``#[serde(tag = "type")]`` -> ``kind`` + ``payload`` tagged union
----------------------------------------------------------------------

Rust's internally-tagged enum serialises as ``{"type": "VariantName",
...payload fields}``. The Python landing is a single
:class:`ToolNotification` dataclass carrying a ``kind`` discriminator (the
PascalCase variant name, i.e. the serde tag value) plus the typed
``payload`` struct. This mirrors the existing tagged-union landings
(:class:`ToolError` ``kind``/``detail`` in R107, :class:`ToolStreamItem`
``kind``/``terminal`` in R109) and keeps :meth:`variant_name` a trivial
``self.kind`` accessor. Each Rust variant ``ToolNotification::X(p)`` gets a
PascalCase classmethod ``ToolNotification.X(p)`` so construction reads
identically at the call site.

Wire scalar mapping
-------------------

- ``String`` -> ``str``; ``Vec<u8>`` -> ``bytes`` (``output_lossy`` decodes
  with ``errors="replace"`` -> ``U+FFFD``, matching
  ``String::from_utf8_lossy``).
- ``usize`` / ``i32`` / ``u32`` / ``u64`` -> ``int``.
- ``Option<T>`` -> ``T | None`` (dataclass fields default to ``None``).
- ``PathBuf`` -> ``str`` (project-wide path convention; no ``pathlib``
  wrapper to stay wire-shaped).
- ``Duration`` -> ``float`` seconds (``as_secs_f64`` equivalent).
- ``SystemTime`` -> ``float`` Unix-epoch seconds.
- ``serde_json::Value`` -> ``Any``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "BashExecutionBackgrounded",
    "BashExecutionComplete",
    "BashExecutionFailed",
    "BashExecutionTimeout",
    "BashNotificationBase",
    "BashOutputChunk",
    "FileRead",
    "FileWritten",
    "LspServerCrashed",
    "LspServerFailed",
    "LspServerReady",
    "LspServerRetrying",
    "LspServerStarting",
    "MonitorEvent",
    "PlanModeEntered",
    "PlanModeExited",
    "ScheduledTaskCreated",
    "ScheduledTaskFired",
    "ScheduledTaskRemoved",
    "TaskKind",
    "TaskSnapshot",
    "ToolNotification",
    "ToolNotificationHandle",
    "UserQuestionAsked",
]


# ---------------------------------------------------------------------------
# Bash notification family — shared base + 5 variants (flatten -> inheritance).
# ---------------------------------------------------------------------------


@dataclass
class BashNotificationBase:
    """Common fields shared by every bash notification variant.

    Hoisted into a dedicated struct so the variants stay in lockstep on
    ``tool_call_id`` / ``command`` / ``output`` / ``cwd``, and so payload
    shape changes only need to be made once (Rust
    ``BashNotificationBase``).
    """

    #: Tool call id, used to correlate with the originating tool call.
    tool_call_id: str
    #: The command being executed.
    command: str
    #: Captured output bytes. May be truncated; use :meth:`output_lossy`
    #: for a ``str`` rendering that handles invalid UTF-8.
    output: bytes
    #: Total bytes received before any truncation.
    total_bytes: int
    #: Whether :attr:`output` was truncated to fit a size cap.
    truncated: bool
    #: Working directory the command ran in.
    cwd: str

    def output_lossy(self) -> str:
        """Lossy UTF-8 rendering of :attr:`output`.

        Invalid bytes become ``U+FFFD`` (Rust
        ``String::from_utf8_lossy``).
        """
        return self.output.decode("utf-8", errors="replace")


@dataclass
class BashOutputChunk(BashNotificationBase):
    """Incremental output chunk streamed during a bash command.

    Sent periodically while the process is still running. Carries only the
    shared :class:`BashNotificationBase` fields (Rust
    ``#[serde(flatten)]`` -> inheritance here).
    """


@dataclass
class BashExecutionComplete(BashNotificationBase):
    """Sent when a bash process exits.

    Carries the exit status, or the killing signal name when the process
    didn't exit normally.
    """

    #: ``Some(code)`` for a normal exit; ``None`` when the process was
    #: killed by a signal before reaching ``exit(2)``.
    exit_code: int | None = None
    #: Signal that terminated the process (e.g. ``"SIGKILL"``). ``None``
    #: when the process exited normally.
    signal: str | None = None

    def was_signaled(self) -> bool:
        """``True`` when termination was triggered by a signal."""
        return self.signal is not None


@dataclass
class BashExecutionTimeout(BashNotificationBase):
    """Sent when a bash command exceeded its configured timeout and was killed."""

    #: Wall time the command ran for before being killed (seconds).
    elapsed: float = 0.0
    #: Configured timeout that was exceeded (seconds).
    timeout: float = 0.0


@dataclass
class BashExecutionBackgrounded(BashNotificationBase):
    """Sent when a foreground bash command was moved to the background.

    The process keeps running; a downstream task monitor emits the eventual
    :class:`BashExecutionComplete`.
    """

    #: File the full output stream is being written to. Background tasks
    #: always tee to disk so consumers can fetch the rest later.
    output_file: str = ""
    #: Background task registry id. Distinct from
    #: :attr:`~BashNotificationBase.tool_call_id`: the task id is generated
    #: when backgrounding, the tool call id was assigned when the
    #: originating tool was invoked.
    task_id: str = ""


@dataclass
class BashExecutionFailed:
    """Sent when a bash command failed to spawn.

    Distinct from :class:`BashExecutionComplete` with a non-zero
    :attr:`~BashExecutionComplete.exit_code` because the process never
    started.
    """

    tool_call_id: str
    command: str
    cwd: str
    #: Error message describing the spawn / IO failure.
    error: str


# ---------------------------------------------------------------------------
# File notifications.
# ---------------------------------------------------------------------------


@dataclass
class FileRead:
    """Emitted when a tool reads a file.

    **Reserved for a future ``ToolNotification::FileRead`` variant.** The
    struct is kept in the public API so adapters can construct it ahead of
    time, but it is not currently dispatched by any
    :class:`ToolNotificationHandle` helper. Adding the enum variant is a
    breaking change for exhaustive ``match`` consumers, so it is deferred
    until a downstream crate has a real consumer wired up.
    """

    tool_call_id: str
    #: Absolute filesystem path of the file that was read.
    absolute_path: str


@dataclass
class FileWritten:
    """Emitted when a tool writes a file.

    Carries the full pre- and post-edit content so subscribers can rewind
    without re-reading the disk.
    """

    tool_call_id: str
    #: Absolute filesystem path of the file that was written.
    absolute_path: str
    #: Full file content after the write.
    content: str
    #: Full file content before the write. ``None`` for a fresh file.
    previous_content: str | None = None
    #: Whether the write created a new file.
    is_new_file: bool = False


# ---------------------------------------------------------------------------
# Plan-mode + user-question notifications.
# ---------------------------------------------------------------------------


@dataclass
class PlanModeEntered:
    """Sent when the agent transitions into plan mode."""

    tool_call_id: str


@dataclass
class PlanModeExited:
    """Sent when the agent transitions out of plan mode.

    Carries the plan document so subscribers can present it for approval
    without an extra file read.
    """

    tool_call_id: str
    #: Plan content as captured at exit time. ``None`` when the plan file
    #: did not exist or was empty.
    plan_content: str | None = None
    #: Path the plan file lives at.
    plan_file_path: str = ""


@dataclass
class UserQuestionAsked:
    """Sent when the agent issues a structured question to the user."""

    tool_call_id: str
    #: Serialised question payload. Subscribers render it directly; the
    #: runtime does not introspect its shape.
    questions_json: Any = None


# ---------------------------------------------------------------------------
# LSP server lifecycle notifications.
# ---------------------------------------------------------------------------


@dataclass
class LspServerStarting:
    """LSP server is being spawned and is waiting for the initialise handshake."""

    server_name: str
    command: str


@dataclass
class LspServerReady:
    """LSP server completed initialisation and is ready to serve requests."""

    server_name: str


@dataclass
class LspServerCrashed:
    """LSP server process died unexpectedly."""

    server_name: str


@dataclass
class LspServerRetrying:
    """LSP server is being retried after a crash.

    Carries the retry attempt count and computed backoff so subscribers
    can render progress.
    """

    server_name: str
    attempt: int = 0
    max_restarts: int = 0
    backoff_ms: int = 0


@dataclass
class LspServerFailed:
    """LSP server is permanently dead.

    Either init failed (``attempts == 0``) or the configured retry budget
    was exhausted.
    """

    server_name: str
    error: str
    #: ``0`` for init failure, ``> 0`` when the retry budget was exhausted.
    attempts: int = 0


# ---------------------------------------------------------------------------
# Scheduled-task notifications.
# ---------------------------------------------------------------------------


@dataclass
class ScheduledTaskFired:
    """Sent when a scheduled task fired and its prompt should be executed."""

    task_id: str
    prompt: str
    human_schedule: str
    #: RFC 3339 timestamp of the next fire, when the task is recurring.
    next_fire_at: str | None = None


@dataclass
class ScheduledTaskRemoved:
    """Sent when a scheduled task is removed (deleted, expired, or one-shot completed)."""

    task_id: str


@dataclass
class ScheduledTaskCreated:
    """Sent when a scheduled task is created."""

    task_id: str
    prompt: str
    human_schedule: str
    #: RFC 3339 timestamp of the upcoming first fire.
    next_fire_at: str | None = None


# ---------------------------------------------------------------------------
# Monitor + background-task snapshots.
# ---------------------------------------------------------------------------


@dataclass
class MonitorEvent:
    """Streaming event from a Monitor tool background process.

    Each event is already XML-wrapped for direct injection into the
    conversation; the raw text is preserved for plain-text consumers.
    """

    task_id: str
    description: str
    #: XML-wrapped event text, ready for conversation injection.
    event_text: str
    #: Raw text without XML wrapping.
    raw_text: str


class TaskKind(StrEnum):
    """Distinguishes background-task kinds (Rust ``#[serde(rename_all = "snake_case")]``)."""

    #: Regular bash command (Rust ``#[default]``).
    BASH = "bash"
    #: Monitor tool — streams stdout events with rate limiting.
    MONITOR = "monitor"


@dataclass
class TaskSnapshot:
    """Snapshot of a background task's state.

    Identical shape to the Grok Build ``TaskSnapshot`` so subscribers can
    decode without per-source adapters. Required fields are declared first
    (matching Rust's non-``Option`` non-``default`` fields); the
    ``Option`` / ``serde(default)`` fields follow with Python defaults so
    the dataclass stays constructible with positional + keyword args.
    """

    task_id: str
    #: Actual command that was executed (may be wrapped by an isolation
    #: harness).
    command: str
    cwd: str
    #: Wall-clock start time (Unix-epoch seconds).
    start_time: float
    output: str
    output_file: str
    truncated: bool
    completed: bool
    #: Original user-provided command before isolation wrapping. When
    #: present, model- and user-facing surfaces should prefer it over
    #: :attr:`command` (Rust ``#[serde(default, skip_serializing_if)]``).
    display_command: str | None = None
    #: Wall-clock end time (Unix-epoch seconds). ``None`` while running.
    end_time: float | None = None
    exit_code: int | None = None
    signal: str | None = None
    #: Distinguishes monitor tasks from regular bash tasks (Rust
    #: ``#[serde(default)]``).
    kind: TaskKind = field(default_factory=lambda: TaskKind.BASH)

    def duration_secs(self) -> float:
        """Wall-time duration in seconds.

        Falls back to ``time.time()`` for tasks that haven't completed
        (Rust ``SystemTime::now()``).
        """
        import time

        end = self.end_time if self.end_time is not None else time.time()
        delta = end - self.start_time
        return delta if delta > 0 else 0.0


# ---------------------------------------------------------------------------
# ToolNotification — tagged union (serde tag = "type" -> kind + payload).
# ---------------------------------------------------------------------------


@dataclass
class ToolNotification:
    """A typed notification a tool emits during or after execution.

    Internally-tagged union: ``kind`` is the PascalCase variant name (the
    serde ``tag = "type"`` discriminator value), ``payload`` is the typed
    struct the variant carries. Each Rust variant ``X(p)`` has a
    PascalCase classmethod :meth:`X` so ``ToolNotification.X(p)`` reads
    identically to Rust's ``ToolNotification::X(p)``.
    """

    kind: str
    payload: Any

    def variant_name(self) -> str:
        """Stable PascalCase name of the active variant.

        Mirrors the serde ``tag = "type"`` discriminator used on the wire.
        """
        return self.kind

    # --- variant constructors (one per Rust enum variant). ---

    @classmethod
    def BashOutputChunk(cls, chunk: BashOutputChunk) -> ToolNotification:
        return cls(kind="BashOutputChunk", payload=chunk)

    @classmethod
    def BashExecutionComplete(cls, complete: BashExecutionComplete) -> ToolNotification:
        return cls(kind="BashExecutionComplete", payload=complete)

    @classmethod
    def BashExecutionTimeout(cls, timeout: BashExecutionTimeout) -> ToolNotification:
        return cls(kind="BashExecutionTimeout", payload=timeout)

    @classmethod
    def BashExecutionBackgrounded(
        cls, backgrounded: BashExecutionBackgrounded
    ) -> ToolNotification:
        return cls(kind="BashExecutionBackgrounded", payload=backgrounded)

    @classmethod
    def BashExecutionFailed(cls, failed: BashExecutionFailed) -> ToolNotification:
        return cls(kind="BashExecutionFailed", payload=failed)

    @classmethod
    def FileWritten(cls, written: FileWritten) -> ToolNotification:
        return cls(kind="FileWritten", payload=written)

    @classmethod
    def TaskCompleted(cls, task_completed: TaskSnapshot) -> ToolNotification:
        return cls(kind="TaskCompleted", payload=task_completed)

    @classmethod
    def PlanModeEntered(cls, entered: PlanModeEntered) -> ToolNotification:
        return cls(kind="PlanModeEntered", payload=entered)

    @classmethod
    def PlanModeExited(cls, exited: PlanModeExited) -> ToolNotification:
        return cls(kind="PlanModeExited", payload=exited)

    @classmethod
    def UserQuestionAsked(cls, asked: UserQuestionAsked) -> ToolNotification:
        return cls(kind="UserQuestionAsked", payload=asked)

    @classmethod
    def LspServerStarting(cls, starting: LspServerStarting) -> ToolNotification:
        return cls(kind="LspServerStarting", payload=starting)

    @classmethod
    def LspServerReady(cls, ready: LspServerReady) -> ToolNotification:
        return cls(kind="LspServerReady", payload=ready)

    @classmethod
    def LspServerCrashed(cls, crashed: LspServerCrashed) -> ToolNotification:
        return cls(kind="LspServerCrashed", payload=crashed)

    @classmethod
    def LspServerRetrying(cls, retrying: LspServerRetrying) -> ToolNotification:
        return cls(kind="LspServerRetrying", payload=retrying)

    @classmethod
    def LspServerFailed(cls, failed: LspServerFailed) -> ToolNotification:
        return cls(kind="LspServerFailed", payload=failed)

    @classmethod
    def ScheduledTaskFired(cls, fired: ScheduledTaskFired) -> ToolNotification:
        return cls(kind="ScheduledTaskFired", payload=fired)

    @classmethod
    def ScheduledTaskRemoved(cls, removed: ScheduledTaskRemoved) -> ToolNotification:
        return cls(kind="ScheduledTaskRemoved", payload=removed)

    @classmethod
    def ScheduledTaskCreated(cls, created: ScheduledTaskCreated) -> ToolNotification:
        return cls(kind="ScheduledTaskCreated", payload=created)

    @classmethod
    def MonitorEvent(cls, event: MonitorEvent) -> ToolNotification:
        return cls(kind="MonitorEvent", payload=event)


# ---------------------------------------------------------------------------
# ToolNotificationHandle — mpsc::UnboundedSender -> asyncio.Queue wrapper.
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class ToolNotificationHandle:
    """Cloneable handle for emitting :class:`ToolNotification` messages.

    Built on an :class:`asyncio.Queue` — the executor-neutral equivalent
    of Rust's ``futures::channel::mpsc::UnboundedSender``. Sends are
    non-blocking and best-effort: errors (a full queue) are silently
    dropped, matching the fire-and-forget convention for notification
    streams. No ``PartialEq`` derive (Rust derives only ``Clone``), so
    handles compare by identity.
    """

    _queue: asyncio.Queue[ToolNotification] = field(default_factory=asyncio.Queue)

    @classmethod
    def new(cls, queue: asyncio.Queue[ToolNotification]) -> ToolNotificationHandle:
        """Wrap a queue obtained elsewhere (Rust ``new``)."""
        return cls(_queue=queue)

    @classmethod
    def from_sender(cls, queue: asyncio.Queue[ToolNotification]) -> ToolNotificationHandle:
        """Alias for :meth:`new` (Rust ``from_sender``)."""
        return cls(_queue=queue)

    @classmethod
    def channel(cls) -> tuple[ToolNotificationHandle, asyncio.Queue[ToolNotification]]:
        """Build both halves of a fresh channel and return them paired.

        The handle is the sender half; the returned queue is the receiver
        half a consumer drains with ``await queue.get()``.
        """
        queue: asyncio.Queue[ToolNotification] = asyncio.Queue()
        return cls(_queue=queue), queue

    @classmethod
    def noop(cls) -> ToolNotificationHandle:
        """Build a handle whose sends are silently dropped.

        Use for callers that don't care about notifications (smoke tests,
        dry-run utilities). NOT a sensible default for production paths —
        the silent-drop behaviour makes notification bugs invisible.
        """
        return cls()

    def send(self, notification: ToolNotification) -> None:
        """Send a fully-built notification. Errors are deliberately swallowed."""
        try:
            self._queue.put_nowait(notification)
        except asyncio.QueueFull:
            # Best-effort: a full (bounded) queue drops the notification.
            pass

    # --- typed send_* helpers, one per variant (lockstep with the enum). ---

    def send_bash_output_chunk(self, chunk: BashOutputChunk) -> None:
        """Send a :meth:`ToolNotification.BashOutputChunk`."""
        self.send(ToolNotification.BashOutputChunk(chunk))

    def send_bash_complete(self, complete: BashExecutionComplete) -> None:
        """Send a :meth:`ToolNotification.BashExecutionComplete`."""
        self.send(ToolNotification.BashExecutionComplete(complete))

    def send_bash_timeout(self, timeout: BashExecutionTimeout) -> None:
        """Send a :meth:`ToolNotification.BashExecutionTimeout`."""
        self.send(ToolNotification.BashExecutionTimeout(timeout))

    def send_bash_backgrounded(self, backgrounded: BashExecutionBackgrounded) -> None:
        """Send a :meth:`ToolNotification.BashExecutionBackgrounded`."""
        self.send(ToolNotification.BashExecutionBackgrounded(backgrounded))

    def send_bash_failed(self, failed: BashExecutionFailed) -> None:
        """Send a :meth:`ToolNotification.BashExecutionFailed`."""
        self.send(ToolNotification.BashExecutionFailed(failed))

    def send_file_written(self, written: FileWritten) -> None:
        """Send a :meth:`ToolNotification.FileWritten`."""
        self.send(ToolNotification.FileWritten(written))

    def send_task_complete(self, task_completed: TaskSnapshot) -> None:
        """Send a :meth:`ToolNotification.TaskCompleted`."""
        self.send(ToolNotification.TaskCompleted(task_completed))

    def send_plan_mode_entered(self, entered: PlanModeEntered) -> None:
        """Send a :meth:`ToolNotification.PlanModeEntered`."""
        self.send(ToolNotification.PlanModeEntered(entered))

    def send_plan_mode_exited(self, exited: PlanModeExited) -> None:
        """Send a :meth:`ToolNotification.PlanModeExited`."""
        self.send(ToolNotification.PlanModeExited(exited))

    def send_user_question_asked(self, asked: UserQuestionAsked) -> None:
        """Send a :meth:`ToolNotification.UserQuestionAsked`."""
        self.send(ToolNotification.UserQuestionAsked(asked))

    def send_lsp_starting(self, starting: LspServerStarting) -> None:
        """Send a :meth:`ToolNotification.LspServerStarting`."""
        self.send(ToolNotification.LspServerStarting(starting))

    def send_lsp_ready(self, ready: LspServerReady) -> None:
        """Send a :meth:`ToolNotification.LspServerReady`."""
        self.send(ToolNotification.LspServerReady(ready))

    def send_lsp_crashed(self, crashed: LspServerCrashed) -> None:
        """Send a :meth:`ToolNotification.LspServerCrashed`."""
        self.send(ToolNotification.LspServerCrashed(crashed))

    def send_lsp_retrying(self, retrying: LspServerRetrying) -> None:
        """Send a :meth:`ToolNotification.LspServerRetrying`."""
        self.send(ToolNotification.LspServerRetrying(retrying))

    def send_lsp_failed(self, failed: LspServerFailed) -> None:
        """Send a :meth:`ToolNotification.LspServerFailed`."""
        self.send(ToolNotification.LspServerFailed(failed))

    def send_scheduled_task_fired(self, fired: ScheduledTaskFired) -> None:
        """Send a :meth:`ToolNotification.ScheduledTaskFired`."""
        self.send(ToolNotification.ScheduledTaskFired(fired))

    def send_scheduled_task_removed(self, removed: ScheduledTaskRemoved) -> None:
        """Send a :meth:`ToolNotification.ScheduledTaskRemoved`."""
        self.send(ToolNotification.ScheduledTaskRemoved(removed))

    def send_scheduled_task_created(self, created: ScheduledTaskCreated) -> None:
        """Send a :meth:`ToolNotification.ScheduledTaskCreated`."""
        self.send(ToolNotification.ScheduledTaskCreated(created))

    def send_monitor_event(self, event: MonitorEvent) -> None:
        """Send a :meth:`ToolNotification.MonitorEvent`."""
        self.send(ToolNotification.MonitorEvent(event))
