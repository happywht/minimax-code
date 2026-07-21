"""Conversation-layer forward-tolerant wire enums (R218, ``xai-grok-sampling-types``
``conversation.rs`` second slice).

R218 continues the ``conversation.rs`` migration (opened at R217) with the two
``#[serde(other)]`` catch-all wire enums that classify *why* a given
conversation item exists -- the user-input semantics axis. Both carry an
``Unknown`` catch-all variant so an older client reading a session written by a
newer version never fails to deserialize when the newer version added a reason
tag the older one does not know.

The two landed enums:

1. :class:`SyntheticReason` (``conversation.rs`` ~75) -- *why* a ``UserItem``
   was synthesized by the runtime rather than typed by a real user (compaction
   meta, system reminder, project instructions, auto-continue, auto-recovery,
   interjection, the five auto-wake reasons, and the catch-all). 12 typed
   variants + ``Unknown``. Carries the pure :meth:`starts_prompt_turn`
   predicate (the auto-wake variants consumed a ``prompt_index`` slot; the
   mid-turn injections did not) used by ``conversation_truncate_for_prompt``'s
   counting fallback for items persisted before ``UserItem::prompt_index``
   existed.
2. :class:`PriorTurnInterrupt` (``conversation.rs`` ~172) -- *how* the user
   *fatally* interrupted (cancelled) the turn immediately preceding a real
   user message. 3 typed variants (mid-turn abort, permission rejected,
   permission cancelled) + ``Unknown``.

Both enums are ``#[derive(Serialize, Deserialize)]`` with
``#[serde(rename_all = "snake_case")]`` + ``#[serde(other)]`` -- the *unit*
catch-all form (``Unknown`` carries no data, unlike the R201
:class:`~minimax_code.sampler.StopReason`'s ``Unknown(String)`` which preserves
the wire string). The unit catch-all maps to a :class:`enum.StrEnum` ``UNKNOWN``
member whose :meth:`from_payload` maps any unknown / non-string wire value to
it (never raises) -- the typed parse cannot fail across independently-versioned
session files.

This module is no-I/O (pure value-level). Migration map (grok -> Python):

- ``#[serde(rename_all = "snake_case")] enum`` + ``#[serde(other)] Unknown``
  (unit catch-all) -> :class:`enum.StrEnum` with an ``UNKNOWN = "unknown"``
  member; :meth:`from_payload` does ``try: cls(raw) except ValueError: UNKNOWN``
  (a non-string also -> ``UNKNOWN`` -- serde would fail on the type mismatch,
  but the platform layer never crashes a session read on a malformed reason).
- ``#[derive(Copy)]`` (PriorTurnInterrupt) -> no Python peer (StrEnum members
  are immutable singletons; ``Copy`` is a Rust size optimization with no
  behavioral surface).
- ``fn SyntheticReason::starts_prompt_turn`` ->
  :meth:`SyntheticReason.starts_prompt_turn`: an exhaustive membership test
  (``self in (TASK_COMPLETED, SUBAGENT_COMPLETED, NOTIFICATION_DRAIN,
  GOAL_CLASSIFIER_NUDGE, SCHEDULER_FIRED)``); ``GoalSummary`` is deliberately
  excluded (it tags both the index-consuming turn and the in-turn directive;
  counting it would over-truncate the common in-turn case).

Naming: the two enum *names* are kept verbatim (no barrel collision -- neither
matches an existing sampler symbol). The ``#[serde(other)]`` variant is named
``UNKNOWN`` (project convention, matching the R102
:class:`~minimax_code.tool_protocol.frames.AttachRoute` catch-all StrEnum and
the R85 ``UNKNOWN_METHOD`` forward-tolerant method enum).

YAGNI: ``Serialize`` round-trip beyond :func:`str` (the :class:`enum.StrEnum`
value IS the wire string, so ``str(SyntheticReason.TASK_COMPLETED)`` yields
``"task_completed"`` -- grok never serializes the ``#[serde(other)]`` variant
itself, so the ``UNKNOWN = "unknown"`` emission is a platform-only convenience
with no grok peer); the ``UserItem`` consumer (it pulls in the un-migrated
:class:`ContentPart` union + the ``Vec<ContentPart>`` body -- lands in a later
round).
"""

from __future__ import annotations

from enum import StrEnum


class SyntheticReason(StrEnum):
    """Why a ``UserItem`` was synthesized by the runtime (not typed by a user).

    ``#[serde(rename_all = "snake_case")]`` + ``#[serde(other)] Unknown``.
    12 typed variants + :attr:`UNKNOWN` (the forward-compat catch-all for
    reasons added in newer versions -- an older client reading a newer
    session never fails to deserialize). Use :meth:`from_payload` for the
    wire-string -> variant mapping."""

    COMPACTION_META = "compaction_meta"
    SYSTEM_REMINDER = "system_reminder"
    PROJECT_INSTRUCTIONS = "project_instructions"
    AUTO_CONTINUE = "auto_continue"
    AUTO_RECOVERY = "auto_recovery"
    INTERJECTION = "interjection"
    TASK_COMPLETED = "task_completed"
    SUBAGENT_COMPLETED = "subagent_completed"
    NOTIFICATION_DRAIN = "notification_drain"
    GOAL_SUMMARY = "goal_summary"
    GOAL_CLASSIFIER_NUDGE = "goal_classifier_nudge"
    SCHEDULER_FIRED = "scheduler_fired"
    #: ``#[serde(other)]`` forward-compat catch-all (unit variant, no data).
    UNKNOWN = "unknown"

    @classmethod
    def from_payload(cls, raw: object) -> SyntheticReason:
        """Forward-tolerant wire-string parser (catch-all -> :attr:`UNKNOWN`).

        Mirrors grok ``#[serde(other)]``: an unknown string -> :attr:`UNKNOWN`
        (forward-compat for sessions written by newer versions). A non-string
        also -> :attr:`UNKNOWN` (serde would fail on the type mismatch; the
        platform layer never crashes a session read on a malformed
        ``synthetic_reason``). Never raises."""
        if not isinstance(raw, str):
            return cls.UNKNOWN
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN

    def starts_prompt_turn(self) -> bool:
        """Whether a user item with this reason starts a prompt turn.

        Mirrors grok ``SyntheticReason::starts_prompt_turn``: the auto-wake
        reasons (a background task / subagent completed, an idle notification
        drain, a goal-classifier nudge, a scheduler fire) consumed a
        ``prompt_index`` slot -> ``True``; every mid-turn / synthetic-injection
        reason -> ``False``. :attr:`GOAL_SUMMARY` is deliberately ``False``:
        the same reason tags both the legacy goal-continuation *turn*
        (index-consuming) and the in-turn goal directive (mid-turn) -- counting
        it would over-truncate the common in-turn case."""
        return self in (
            SyntheticReason.TASK_COMPLETED,
            SyntheticReason.SUBAGENT_COMPLETED,
            SyntheticReason.NOTIFICATION_DRAIN,
            SyntheticReason.GOAL_CLASSIFIER_NUDGE,
            SyntheticReason.SCHEDULER_FIRED,
        )


class PriorTurnInterrupt(StrEnum):
    """How the user *fatally* interrupted the turn before a real user message.

    ``#[serde(rename_all = "snake_case")]`` + ``#[derive(Copy, Eq)]`` +
    ``#[serde(other)] Unknown``. 3 typed variants (mid-turn abort, permission
    rejected, permission cancelled) + :attr:`UNKNOWN` (forward-compat catch-all).
    Set only on genuine user messages that directly follow a cancelled turn.
    Use :meth:`from_payload` for the wire-string -> variant mapping."""

    MID_TURN_ABORT = "mid_turn_abort"
    PERMISSION_REJECTED = "permission_rejected"
    PERMISSION_CANCELLED = "permission_cancelled"
    #: ``#[serde(other)]`` forward-compat catch-all (unit variant, no data).
    UNKNOWN = "unknown"

    @classmethod
    def from_payload(cls, raw: object) -> PriorTurnInterrupt:
        """Forward-tolerant wire-string parser (catch-all -> :attr:`UNKNOWN`).

        Mirrors grok ``#[serde(other)]``: an unknown string -> :attr:`UNKNOWN`
        (forward-compat for sessions written by newer versions). A non-string
        also -> :attr:`UNKNOWN` (serde would fail on the type mismatch; the
        platform layer never crashes a session read on a malformed
        ``prior_turn_interrupt``). Never raises."""
        if not isinstance(raw, str):
            return cls.UNKNOWN
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


__all__ = ["PriorTurnInterrupt", "SyntheticReason"]
