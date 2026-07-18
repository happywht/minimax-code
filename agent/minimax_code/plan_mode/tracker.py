"""Plan-mode state machine (R26).

Ports the pure state-machine half of grok-build's
``xai-grok-shell::session::plan_mode`` — the :class:`PlanModeTracker`
that owns the four-state lifecycle
(``Inactive`` → ``Pending`` → ``Active`` → ``ExitPending``) plus the
:class:`PromptMode` enum. No AgentCore, no storage, no async I/O: pure
transition logic, tested in isolation, exactly like the Rust original.

The four states
---------------
* **Inactive** — normal operating mode, no plan-mode constraints.
* **Pending** — client toggled plan mode ON but no prompt has been sent
  yet; the model does not know about plan mode.
* **Active** — plan mode is in force; write tools are blocked except for
  the plan file.
* **ExitPending** — client toggled plan mode OFF while a model turn is
  in-flight; we wait for the turn to finish, then cleanly exit.

Python simplification
---------------------
grok guards the tracker behind a ``Mutex`` because the SessionActor has
concurrent callers; MiniMax's asyncio run loop is single-threaded, so the
lock is dropped. ``PathBuf`` becomes :class:`pathlib.Path`; Rust's
``let-chains`` (``if let Some(p) = opt && state == Active``) unroll into
nested ``if``. Snapshot serde is a plain ``to_dict`` / ``from_dict`` pair
(dataclass ``field(default=...)`` covers grok's ``#[serde(default)]``).
``is_multiple_of(2)`` is plain ``% 2 == 0``.

Serialization parity
--------------------
grok's ``PlanModeState`` keeps serde's default PascalCase tagging
(``"Inactive"`` / ``"Pending"`` / ``"Active"`` / ``"ExitPending"``) while
the snapshot *fields* are snake_case — the :class:`PlanModeState` values
below match that exactly so a persisted ``plan_mode.json`` round-trips
across the two implementations. ``PromptMode`` is snake_case-tagged
(``"agent"`` / ``"ask"`` / ``"plan"``), again matching grok.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "PlanModeState",
    "PromptMode",
    "PendingActivation",
    "PlanModeSnapshot",
    "PlanModeTracker",
    "is_plan_file_write",
    "is_markdown_file_path",
]

#: Markdown suffixes recognised by :func:`is_markdown_file_path`.
#: Aligns with grok's ``MARKDOWN_SUFFIXES``.
_MARKDOWN_SUFFIXES: tuple[str, ...] = (
    ".md",
    ".markdown",
    ".mdown",
    ".mkd",
    ".mkdn",
    ".mdx",
)


class PlanModeState(StrEnum):
    """Four-state plan-mode lifecycle (faithful grok port).

    Values are PascalCase to match grok's default serde tagging — a
    snapshot persisted by the Rust implementation deserialises here
    unchanged.
    """

    INACTIVE = "Inactive"
    PENDING = "Pending"
    ACTIVE = "Active"
    EXIT_PENDING = "ExitPending"


class PromptMode(StrEnum):
    """The prompt mode the client sends in ``_meta.mode``.

    Determines whether a prompt expects tool use / file edits
    (:attr:`AGENT`) or is read-only (:attr:`ASK` / :attr:`PLAN`). Values
    are snake_case to match grok's ``#[serde(rename_all = "snake_case")]``.
    """

    AGENT = "agent"
    ASK = "ask"
    PLAN = "plan"

    @classmethod
    def from_meta_str(cls, s: str) -> PromptMode:
        """Parse ``_meta.mode``; unknown values default to :attr:`AGENT`.

        Matching is case-sensitive (only lowercase ``"ask"`` / ``"plan"``
        hit) — parity with grok, so ``"ASK"`` falls through to AGENT.
        """
        if s == "ask":
            return cls.ASK
        if s == "plan":
            return cls.PLAN
        return cls.AGENT

    @classmethod
    def default(cls) -> PromptMode:
        """The default prompt mode (mirrors grok's ``#[default]`` on Agent)."""
        return cls.AGENT

    @property
    def is_read_only(self) -> bool:
        """``True`` for modes that expect no file mutations (Ask / Plan)."""
        return self in (PromptMode.ASK, PromptMode.PLAN)


@dataclass
class PendingActivation:
    """A buffered mid-turn activation reminder plus rollback state.

    When the client toggles plan mode ON mid-turn, the activation reminder
    is pre-rendered and buffered here for delivery at the running turn's
    next safe drain point. If the client toggles back OFF before delivery,
    :attr:`prior_was_previously_active` is restored so a rolled-back
    activation does not fake a reentry.
    """

    text: str
    prior_was_previously_active: bool


@dataclass
class PlanModeSnapshot:
    """Serializable plan-mode lifecycle state.

    Persisted to ``plan_mode.json`` in the session directory and restored
    on session reload/resume so plan mode survives process restarts.
    ``plan_file_path`` is NOT persisted — it is recomputed from session
    metadata, mirroring grok.
    """

    state: PlanModeState
    was_previously_active: bool
    reminder_count: int
    pending_exit_reminder: bool
    awaiting_plan_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict (snake_case keys)."""
        return {
            "state": self.state.value,
            "was_previously_active": self.was_previously_active,
            "reminder_count": self.reminder_count,
            "pending_exit_reminder": self.pending_exit_reminder,
            "awaiting_plan_approval": self.awaiting_plan_approval,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanModeSnapshot:
        """Deserialize from a dict.

        Every field is read through ``.get(..., default)`` so a legacy
        snapshot missing ``awaiting_plan_approval`` (or any newer field)
        degrades gracefully — parity with grok's ``#[serde(default)]``.
        An unknown ``state`` string falls back to :attr:`PlanModeState.INACTIVE`.
        """
        try:
            state = PlanModeState(data.get("state", PlanModeState.INACTIVE.value))
        except ValueError:
            state = PlanModeState.INACTIVE
        return cls(
            state=state,
            was_previously_active=bool(data.get("was_previously_active", False)),
            reminder_count=int(data.get("reminder_count", 0)),
            pending_exit_reminder=bool(data.get("pending_exit_reminder", False)),
            awaiting_plan_approval=bool(data.get("awaiting_plan_approval", False)),
        )


@dataclass
class PlanModeTracker:
    """Tracks the full plan-mode lifecycle for a session.

    Pure state machine — no AgentCore / storage / async I/O references.
    Designed to be tested in isolation, exactly like grok's original. The
    host owns one instance (no Mutex needed: asyncio is single-threaded)
    and calls the transition methods at the appropriate points.
    """

    state: PlanModeState = PlanModeState.INACTIVE
    was_previously_active: bool = False
    reminder_count: int = 0
    pending_exit_reminder: bool = False
    awaiting_plan_approval: bool = False
    pending_activation: PendingActivation | None = None
    plan_file_path: Path = field(default_factory=lambda: Path("plan.md"))

    # -- construction ------------------------------------------------------

    @classmethod
    def new(cls, session_dir: Path) -> PlanModeTracker:
        """Create a fresh tracker; the plan file lives at ``session_dir/plan.md``."""
        return cls(plan_file_path=session_dir / "plan.md")

    @classmethod
    def from_snapshot(
        cls, session_dir: Path, snapshot: PlanModeSnapshot
    ) -> PlanModeTracker:
        """Restore a tracker from a persisted snapshot.

        ``session_dir`` recomputes :attr:`plan_file_path`. Transient states
        collapse (they depend on in-flight client/turn interactions that
        don't survive a restart): ``Pending`` → ``Inactive``;
        ``ExitPending`` → ``Inactive`` with the exit reminder armed.
        """
        state = snapshot.state
        pending_exit = snapshot.pending_exit_reminder
        if state is PlanModeState.PENDING:
            state = PlanModeState.INACTIVE
        elif state is PlanModeState.EXIT_PENDING:
            state = PlanModeState.INACTIVE
            pending_exit = True
        return cls(
            state=state,
            was_previously_active=snapshot.was_previously_active,
            reminder_count=snapshot.reminder_count,
            pending_exit_reminder=pending_exit,
            awaiting_plan_approval=snapshot.awaiting_plan_approval,
            pending_activation=None,
            plan_file_path=session_dir / "plan.md",
        )

    # -- snapshot / approval chrome ---------------------------------------

    def snapshot(self) -> PlanModeSnapshot:
        """Capture the current lifecycle state as a persistable snapshot."""
        return PlanModeSnapshot(
            state=self.state,
            was_previously_active=self.was_previously_active,
            awaiting_plan_approval=self.awaiting_plan_approval,
            reminder_count=self.reminder_count,
            pending_exit_reminder=self.pending_exit_reminder,
        )

    def set_awaiting_plan_approval(self, awaiting: bool) -> None:
        """Mark that the client is waiting on plan approval (``exit_plan_mode`` parked)."""
        self.awaiting_plan_approval = awaiting

    def is_awaiting_plan_approval(self) -> bool:
        """Whether approval is outstanding (also true after resume from snapshot)."""
        return self.awaiting_plan_approval

    # -- queries -----------------------------------------------------------

    def is_active(self) -> bool:
        """``True`` if plan mode is currently :attr:`PlanModeState.ACTIVE`."""
        return self.state is PlanModeState.ACTIVE

    def should_auto_approve_edit(self, edit_path: Path) -> bool:
        """``True`` if plan mode is active and ``edit_path`` targets the plan file.

        Used to bypass the permission prompt for plan-file edits during
        plan mode.
        """
        return self.is_active() and is_plan_file_write(edit_path, self.plan_file_path)

    def should_use_full_reminder(self) -> bool:
        """Whether the next reminder should be the full variant.

        Even :attr:`reminder_count` → full; odd → sparse.
        """
        return self.reminder_count % 2 == 0

    def has_pending_exit_reminder(self) -> bool:
        """Whether we need to inject an exit reminder on the next turn."""
        return self.pending_exit_reminder

    def has_pending_activation(self) -> bool:
        """Whether a mid-turn activation reminder is buffered (undelivered)."""
        return self.pending_activation is not None

    def is_reentry(self) -> bool:
        """``True`` if entering plan mode again after a prior activation this session."""
        return self.was_previously_active and self.state is PlanModeState.PENDING

    # -- transitions ------------------------------------------------------

    def enter_pending(self) -> bool:
        """Client toggled plan mode ON.

        Returns ``True`` if state actually changed. Handles re-entry from
        :attr:`ExitPending` by cancelling the deferred exit and returning
        directly to :attr:`Active` (the model already has plan-mode context).
        """
        if self.state is PlanModeState.INACTIVE:
            self.state = PlanModeState.PENDING
            self.pending_exit_reminder = False
            return True
        if self.state is PlanModeState.EXIT_PENDING:
            self.state = PlanModeState.ACTIVE
            self.pending_exit_reminder = False
            return True
        return False

    def activate(self) -> bool:
        """First user prompt while Pending — activate plan mode.

        Returns ``True`` if state actually changed.
        """
        if self.state is not PlanModeState.PENDING:
            return False
        self.state = PlanModeState.ACTIVE
        self.was_previously_active = True
        self.reminder_count = 0
        return True

    def activate_mid_turn(self, rendered_reminder: str) -> bool:
        """Mid-turn toggle: activate immediately and buffer the pre-rendered reminder.

        Only valid from :attr:`Pending` (an ``ExitPending → Active`` re-entry
        needs no reminder). Returns ``True`` if activated. The reminder is
        recorded (alternation counter) at delivery
        (:meth:`take_pending_activation` + :meth:`record_reminder_injected`),
        not here, so a withdrawn or restart-lost buffer doesn't advance the
        full/sparse cycle.
        """
        if self.state is not PlanModeState.PENDING:
            return False
        prior = self.was_previously_active
        self.state = PlanModeState.ACTIVE
        self.was_previously_active = True
        self.reminder_count = 0
        self.pending_activation = PendingActivation(
            text=rendered_reminder,
            prior_was_previously_active=prior,
        )
        return True

    def take_pending_activation(self) -> str | None:
        """Take the buffered mid-turn activation reminder for delivery.

        The caller pushes the returned text into the conversation and then
        calls :meth:`record_reminder_injected`. Returns ``None`` if nothing
        is buffered.
        """
        if self.pending_activation is None:
            return None
        text = self.pending_activation.text
        self.pending_activation = None
        return text

    def activate_from_tool(self) -> bool:
        """Agent called the ``EnterPlanMode`` tool — go directly to Active.

        Returns ``True`` if state actually changed.
        """
        if self.state is not PlanModeState.INACTIVE:
            return False
        self.state = PlanModeState.ACTIVE
        self.was_previously_active = True
        self.reminder_count = 0
        self.pending_exit_reminder = False
        return True

    def deactivate_approved(self) -> bool:
        """``ExitPlanMode`` approved (agent-initiated exit).

        Returns ``True`` if state actually changed. Does NOT set
        :attr:`pending_exit_reminder`: callers must ensure the model gets
        an in-context exit signal — either by pushing a tool result that
        states the exit, or by explicitly arming :meth:`queue_exit_reminder`
        when the result text carries no such signal. A reminder armed here
        would only drain at the next turn start, arriving a turn late.
        """
        if self.state is not PlanModeState.ACTIVE:
            return False
        self.state = PlanModeState.INACTIVE
        self.reminder_count = 0
        self.awaiting_plan_approval = False
        self.pending_activation = None
        return True

    def user_exit(self, turn_in_flight: bool) -> None:
        """Client toggled plan mode OFF.

        ``turn_in_flight``: whether a model turn is currently running.

        If an activation reminder is still buffered (the model never saw
        plan mode), the activation is rolled back to Inactive and the
        prior reentry flag is restored — rather than deferring an exit the
        model never knew about.
        """
        self.awaiting_plan_approval = False
        if (
            self.pending_activation is not None
            and self.state is PlanModeState.ACTIVE
        ):
            prior = self.pending_activation.prior_was_previously_active
            self.pending_activation = None
            self.state = PlanModeState.INACTIVE
            self.was_previously_active = prior
            return
        if self.state is PlanModeState.PENDING:
            self.state = PlanModeState.INACTIVE
        elif self.state is PlanModeState.ACTIVE:
            if turn_in_flight:
                self.state = PlanModeState.EXIT_PENDING
            else:
                self.state = PlanModeState.INACTIVE
                self.pending_exit_reminder = True
        # ExitPending / Inactive: no-op.

    def complete_deferred_exit(self) -> None:
        """Current turn completed while in :attr:`ExitPending`."""
        if self.state is not PlanModeState.EXIT_PENDING:
            return
        self.state = PlanModeState.INACTIVE
        self.pending_exit_reminder = True

    def queue_exit_reminder(self) -> None:
        """Arm the one-shot exit reminder for the next turn.

        For exit paths whose tool result carries no exit signal.
        """
        self.pending_exit_reminder = True

    def record_reminder_injected(self) -> None:
        """Called after injecting a per-turn reminder. Advances the counter."""
        self.reminder_count += 1

    def clear_pending_exit_reminder(self) -> None:
        """Called after injecting the exit reminder. Clears the flag."""
        self.pending_exit_reminder = False

    def reset_after_compaction(self) -> None:
        """Called after compaction.

        Resets the reminder counter so the next injection is the full
        variant, and drops any undelivered buffered activation.
        """
        if self.state is PlanModeState.ACTIVE:
            self.reminder_count = 0
            self.pending_activation = None


def is_plan_file_write(target_path: Path, plan_file: Path) -> bool:
    """``True`` if ``target_path`` matches the plan file exactly."""
    return target_path == plan_file


def is_markdown_file_path(path: Path) -> bool:
    """Whether ``path``'s final component ends with a markdown suffix.

    Suffixes align with grok's ``MARKDOWN_SUFFIXES``: ``.md``, ``.markdown``,
    ``.mdown``, ``.mkd``, ``.mkdn``, ``.mdx`` (case-insensitive). In plan
    mode the host rejects ``write`` / ``str_replace`` when this is false.
    """
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in _MARKDOWN_SUFFIXES)
