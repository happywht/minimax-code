"""Turn-hook protocol — full core landed (R91).

Fusion of grok-build's ``xai-tool-protocol::turn_hook`` — the
sampler↔workspace turn-hook protocol. The module is large (700 lines,
``pub mod turn_hook`` with **no** ``pub use`` re-export in lib.rs, so none
of its symbols travel the barrel); R90 landed only the minimal leaf that
:mod:`session_event` depended on (:class:`TurnHookOutcome` +
:data:`TURN_HOOK_KIND`). R91 lands the rest of the module — the
before/after payloads, the request/response envelope
(:class:`TurnHookRequest`), the injection/control reply types
(:class:`HookInjection` / :class:`TurnControl` / :class:`HookReply`), and
the artifact-handling ack (:class:`AfterTurnAckStatus` /
:class:`AfterTurnAckPayload`).

Three crate-first serde shapes land here:

1. **``#[serde(default = "fn")]`` — custom default function — the crate's
   first.** :attr:`BeforeTurnPayload.session_relationship` /
   :attr:`BeforeTurnPayload.schema_version` default via *named functions*
   (``default_session_relationship`` / ``default_schema_version``) rather
   than the field type's own zero-value default. In Python the dataclass
   field default is the constant the helper returns; the named-function
   indirection is preserved as the module constants
   :data:`DEFAULT_SESSION_RELATIONSHIP` / :data:`DEFAULT_SCHEMA_VERSION`
   that the private ``_default_session_relationship`` /
   ``_default_schema_version`` helpers return (mirroring the Rust
   ``fn default_session_relationship() -> String { ... }`` stubs).
2. **``#[serde(tag = "phase")]`` — the 6th tag-key name.** Prior enums
   tagged on ``code`` / ``kind`` / ``shape`` / ``type`` / ``event_type``
   (R83 / R88 / R89 / R90). :class:`TurnHookRequest` is internally tagged
   on ``phase`` with ``rename_all = "snake_case"``: ``Before`` →
   ``"before"``, ``After`` → ``"after"``.
3. **``#[serde(deny_unknown_fields)]`` — strict reject of unknown fields —
   the crate's first.** :class:`HookInjection` and :class:`HookReply`
   carry it; ``from_wire`` raises :class:`ValueError` on any key outside
   the known set. This is the strictest deserialise mode in the crate —
   no other type rejects unknown fields (even the ``#[serde(other)]``
   catch-alls tolerate them at the union level).

Plus a subtle **two-layer kind/phase distinction** that is the module's
signature footgun: :data:`BEFORE_TURN_KIND` / :data:`AFTER_TURN_KIND`
(``"before_turn"`` / ``"after_turn"``) are the *outer*
``HookEvent::Custom { kind, payload }`` kind strings (the label a
``TurnHookRequest`` carries when it rides inside a :class:`HookEvent`),
while :class:`TurnHookRequest`'s *inner* ``phase`` tag is ``"before"`` /
``"after"`` (no ``_turn``). The two must never be conflated — a wire
shape ``{"kind": "before_turn", "payload": {"phase": "before", ...}}``
uses both at once.

Strict↔tolerant contrast
------------------------

Every enum here is **strict** — no ``#[serde(other)]`` arm on any of them
(:class:`TurnHookOutcome` (R90), :class:`InjectionRole`,
:class:`TurnControl`, :class:`AfterTurnAckStatus`), and
:class:`TurnHookRequest` itself has ``#[non_exhaustive]`` but no
``#[serde(other)]`` so an unknown ``phase`` raises. This keeps R90's
strict↔tolerant turning point intact: the turn-hook protocol rejects what
it does not understand, while :mod:`session_event` tolerates forward-compat
unknowns via ``#[serde(other)]``.

Serde shape
-----------

* :class:`BeforeTurnPayload`: plain struct, 6 fields, four ``#[serde(default)]``
  (two bare, two ``= "fn"``); no ``deny_unknown_fields``.
* :class:`AfterTurnPayload`: plain struct, 8 fields; ``outcome`` is a strict
  :class:`TurnHookOutcome`; two ``Option`` fields with
  ``skip_serializing_if = "Option::is_none"``; ``cancellation_context`` is
  opaque ``serde_json::Value`` (mapped to :class:`object`).
* :class:`TurnHookRequest`: ``#[serde(tag = "phase", rename_all =
  "snake_case")]`` internally-tagged enum, two tuple variants wrapping the
  payloads; ``#[non_exhaustive]`` + no ``#[serde(other)]`` → unknown phase
  raises.
* :class:`InjectionRole` / :class:`TurnControl` / :class:`AfterTurnAckStatus`:
  ``#[serde(rename_all = "snake_case")]`` ``#[derive(Copy)]`` string-enums
  (bare-string wire form, no tag); :class:`TurnControl` is also ``Default``
  (``Auto``).
* :class:`HookInjection` / :class:`HookReply`: plain structs with
  ``#[serde(deny_unknown_fields)]``; :class:`HookReply` is also ``Default``
  (empty injections, ``Auto`` control, ``None`` ack).
* :class:`AfterTurnAckPayload`: plain struct, 4 fields, one ``Option`` with
  ``skip_serializing_if`` and one ``#[serde(default)]``.

``to_wire`` lives on each dataclass; the module-level ``*_from_wire``
functions reconstruct each type, with :func:`turn_hook_request_from_wire`
dispatching on the ``phase`` tag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    # outer HookEvent::Custom kind constants
    "TURN_HOOK_KIND",
    "BEFORE_TURN_KIND",
    "AFTER_TURN_KIND",
    # serde-default-fn backing constants
    "DEFAULT_SESSION_RELATIONSHIP",
    "DEFAULT_SCHEMA_VERSION",
    # strict string-enums
    "TurnHookOutcome",
    "InjectionRole",
    "TurnControl",
    "AfterTurnAckStatus",
    # payloads
    "BeforeTurnPayload",
    "AfterTurnPayload",
    "HookInjection",
    "HookReply",
    "AfterTurnAckPayload",
    # request union
    "TurnHookRequest",
    "TurnHookRequestBefore",
    "TurnHookRequestAfter",
    # wire converters
    "before_turn_payload_from_wire",
    "after_turn_payload_from_wire",
    "hook_injection_from_wire",
    "hook_reply_from_wire",
    "after_turn_ack_payload_from_wire",
    "turn_hook_request_from_wire",
]

# ────────────────────────────────────────────────────────────────────────────
# Outer HookEvent::Custom kind constants + serde-default-fn backing constants
# ────────────────────────────────────────────────────────────────────────────

#: ``HookEvent::Custom`` kind for the request/response turn hook
#: (mirrors ``pub const TURN_HOOK_KIND: &str = "turn_hook";``). This is the
#: *outer* label a :class:`TurnHookRequest` carries when it rides inside a
#: :class:`~minimax_code.tool_protocol.hook.HookEvent` — distinct from the
#: *inner* ``phase`` tag below.
TURN_HOOK_KIND: str = "turn_hook"

#: Well-known ``HookEvent::Custom`` kind string for before-turn hooks
#: (the *outer* kind; mirrors ``pub const BEFORE_TURN_KIND: &str =
#: "before_turn";``). Do NOT confuse with the inner ``phase`` value
#: ``"before"``.
BEFORE_TURN_KIND: str = "before_turn"

#: Well-known ``HookEvent::Custom`` kind string for after-turn hooks
#: (the *outer* kind; mirrors ``pub const AFTER_TURN_KIND: &str =
#: "after_turn";``). Do NOT confuse with the inner ``phase`` value
#: ``"after"``.
AFTER_TURN_KIND: str = "after_turn"

#: Default ``session_relationship`` wire value (mirrors
#: ``xai_file_utils::events::SessionRelationship::Primary``). Backs the
#: ``#[serde(default = "default_session_relationship")]`` on
#: :attr:`BeforeTurnPayload.session_relationship`.
DEFAULT_SESSION_RELATIONSHIP: str = "primary"

#: Default ``schema_version`` wire value. Bare literal (not the
#: ``xai-file-utils`` constant) to avoid a dependency cycle. Backs the
#: ``#[serde(default = "default_schema_version")]`` on
#: :attr:`BeforeTurnPayload.schema_version`.
DEFAULT_SCHEMA_VERSION: str = "1.0"


def _default_session_relationship() -> str:
    """Serde ``default = "fn"`` backing for ``session_relationship``.

    Mirrors ``fn default_session_relationship() -> String`` in the crate —
    a named default function rather than the type's zero value, so the
    default source is traceable to :data:`DEFAULT_SESSION_RELATIONSHIP`.
    """
    return DEFAULT_SESSION_RELATIONSHIP


def _default_schema_version() -> str:
    """Serde ``default = "fn"`` backing for ``schema_version``.

    Mirrors ``fn default_schema_version() -> String`` in the crate — a
    named default function so the default source is traceable to
    :data:`DEFAULT_SCHEMA_VERSION`.
    """
    return DEFAULT_SCHEMA_VERSION


# ────────────────────────────────────────────────────────────────────────────
# Strict string-enums (no #[serde(other)] — unknown raises)
# ────────────────────────────────────────────────────────────────────────────


class TurnHookOutcome(StrEnum):
    """Turn outcome as observed by the sampler.

    ``#[serde(rename_all = "snake_case")]``, ``#[derive(Copy)]``,
    ``#[non_exhaustive]``. Strict — **no** ``#[serde(other)]`` arm, so an
    unknown wire value raises :class:`ValueError` (contrast
    :class:`~minimax_code.tool_protocol.session_event.ToolCallOutcome`,
    which deserialises unknown values as ``UNKNOWN``). Named
    ``TurnHookOutcome`` (not ``TurnOutcome``) to avoid colliding with the
    shell's existing ``TurnOutcome`` and the telemetry crate's
    ``TurnOutcomeLabel``; module-qualified usage
    (``turn_hook::TurnHookOutcome``) is still recommended.
    """

    #: Turn completed normally (model finished generating).
    COMPLETED = "completed"
    #: Turn was cancelled by the user (Ctrl+C / abort).
    CANCELLED = "cancelled"
    #: Turn ended due to an error.
    ERROR = "error"

    @classmethod
    def from_wire(cls, value: str) -> TurnHookOutcome:
        """Reconstruct a :class:`TurnHookOutcome` from its wire string.

        Strict — no ``#[serde(other)]`` catch-all. Unknown values raise
        :class:`ValueError` (mirrors
        ``serde_json::from_value::<TurnHookOutcome>("timeout").is_err()``).
        """
        # raises ValueError on unknown — strict, no catch-all
        return cls(value)


class InjectionRole(StrEnum):
    """Conversation role for a turn the workspace asks the sampler to append.

    ``#[serde(rename_all = "snake_case")]``, ``#[derive(Copy)]``,
    ``#[non_exhaustive]``. Strict — no ``#[serde(other)]``; unknown values
    raise.
    """

    #: Append as a system turn.
    SYSTEM = "system"
    #: Append as a developer turn.
    DEVELOPER = "developer"
    #: Append as a user turn (e.g. a ``<system-reminder>``-wrapped message).
    USER = "user"

    @classmethod
    def from_wire(cls, value: str) -> InjectionRole:
        """Reconstruct from its wire string. Strict — unknown raises."""
        return cls(value)


class TurnControl(StrEnum):
    """Override of the sampler's loop decision at a turn boundary.

    ``#[serde(rename_all = "snake_case")]``, ``#[derive(Copy, Default)]``,
    ``#[non_exhaustive]``. The default variant is :data:`AUTO`
    (``#[default]``) — a no-op override. Strict — no ``#[serde(other)]``;
    unknown values raise.
    """

    #: No override — the sampler proceeds with its own completion logic.
    AUTO = "auto"  # #[default]
    #: Force another turn even if the model ended without a tool call.
    FORCE_CONTINUE = "force_continue"
    #: Force the loop to stop after this turn.
    FORCE_STOP = "force_stop"

    @classmethod
    def from_wire(cls, value: str) -> TurnControl:
        """Reconstruct from its wire string. Strict — unknown raises."""
        return cls(value)


class AfterTurnAckStatus(StrEnum):
    """Terminal status of the workspace's per-turn artifact handling.

    ``#[serde(rename_all = "snake_case")]``, ``#[derive(Copy)]``. NOTE:
    unlike the other turn_hook enums this one has **no** ``#[non_exhaustive]``
    — the variant set is closed. Strict — no ``#[serde(other)]``; unknown
    values raise. Carried in :attr:`AfterTurnAckPayload.status`; the ack
    is informational — the shell never blocks its agent loop on it.
    """

    #: Every archive the workspace attempted was durably handed off to its
    #: upload queue. The caller MAY advance.
    ENQUEUED = "enqueued"
    #: At least one archive could not be handed off. The workspace has done
    #: what it can — the caller MUST NOT retry.
    FAILED = "failed"
    #: The workspace skipped uploads before touching disk. ``error_message``
    #: carries the reason.
    SKIPPED = "skipped"

    @classmethod
    def from_wire(cls, value: str) -> AfterTurnAckStatus:
        """Reconstruct from its wire string. Strict — unknown raises."""
        return cls(value)


# ────────────────────────────────────────────────────────────────────────────
# Payloads
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class BeforeTurnPayload:
    """Payload for ``before_turn`` custom hooks.

    Sent by the harness before the agent loop begins a new turn. Recipients
    can use this to prepare state (clear caches, initialize tracking) but
    MUST NOT block — hooks are fire-and-forget. Four fields carry serde
    defaults: :attr:`yolo_mode` / :attr:`conversation_message_count` via bare
    ``#[serde(default)]``, :attr:`session_relationship` /
    :attr:`schema_version` via ``#[serde(default = "fn")]`` (the crate's
    first named-default-function shape). No ``deny_unknown_fields`` —
    forward-compatible on the read side.
    """

    #: Monotonically increasing turn counter within the session.
    turn_number: int
    #: Model being used for this turn (e.g. ``"grok-3"``).
    model_id: str
    #: Whether the session is in YOLO / auto-approve mode. ``#[serde(default)]``
    #: → ``False``.
    yolo_mode: bool = False
    #: Mirrors ``Event::TurnStarted::conversation_message_count``.
    #: ``#[serde(default)]`` → ``0``.
    conversation_message_count: int = 0
    #: Snake-case mirror of ``Event::TurnStarted::session_relationship``
    #: (``"primary"`` | ``"subagent"``). A :class:`str`, not the
    #: ``xai-file-utils`` enum, to avoid a dependency cycle.
    #: ``#[serde(default = "default_session_relationship")]`` →
    #: :data:`DEFAULT_SESSION_RELATIONSHIP`.
    session_relationship: str = DEFAULT_SESSION_RELATIONSHIP
    #: Mirrors ``Event::TurnStarted::schema_version``.
    #: ``#[serde(default = "default_schema_version")]`` →
    #: :data:`DEFAULT_SCHEMA_VERSION`.
    schema_version: str = DEFAULT_SCHEMA_VERSION

    def to_wire(self) -> dict[str, object]:
        return {
            "turn_number": self.turn_number,
            "model_id": self.model_id,
            "yolo_mode": self.yolo_mode,
            "conversation_message_count": self.conversation_message_count,
            "session_relationship": self.session_relationship,
            "schema_version": self.schema_version,
        }


@dataclass
class AfterTurnPayload:
    """Payload for ``after_turn`` custom hooks.

    Sent by the harness after the agent loop completes a turn. Carries
    ``tool_call_count`` but intentionally omits per-tool names (the workspace
    correlates from its own ``ActivityTracker``); ``written_repo_paths`` is
    the exception, bounded by distinct files edited. :attr:`outcome` is a
    strict :class:`TurnHookOutcome` (unknown raises). The two cancellation
    fields are :class:`object`-typed / opaque and ``skip_serializing_if`` on
    ``None``; ``written_repo_paths`` is ``#[serde(default)]``.
    """

    #: Same turn counter as the preceding ``before_turn``.
    turn_number: int
    #: High-level outcome of the turn. Strict — unknown raises.
    outcome: TurnHookOutcome
    #: Wall-clock duration of the turn in milliseconds.
    duration_ms: int
    #: Number of tool calls made during the turn.
    tool_call_count: int
    #: Model used (may differ from ``before_turn`` if switched mid-turn).
    model_id: str
    #: Repo-relative agent writes. ``#[serde(default)]`` → empty.
    written_repo_paths: list[str] = field(default_factory=list)
    #: Snake-case mirror of ``Event::TurnEnded::cancellation_category`` (e.g.
    #: ``"doom_loop_repetition"``). ``None`` for non-cancelled turns.
    #: ``#[serde(default, skip_serializing_if = "Option::is_none")]``.
    cancellation_category: str | None = None
    #: Opaque JSON mirror of ``Event::TurnEnded::cancellation_context``
    #: (e.g. ``{"reason": "max_turns_reached", "limit": 50}``). Passed
    #: through verbatim. ``None`` when there is no context. Mapped from
    #: ``serde_json::Value`` as a bare :class:`object`.
    #: ``#[serde(default, skip_serializing_if = "Option::is_none")]``.
    cancellation_context: object | None = None

    def to_wire(self) -> dict[str, object]:
        out: dict[str, object] = {
            "turn_number": self.turn_number,
            "outcome": str(self.outcome),
            "duration_ms": self.duration_ms,
            "tool_call_count": self.tool_call_count,
            "model_id": self.model_id,
            "written_repo_paths": list(self.written_repo_paths),
        }
        # skip_serializing_if = "Option::is_none"
        if self.cancellation_category is not None:
            out["cancellation_category"] = self.cancellation_category
        if self.cancellation_context is not None:
            out["cancellation_context"] = self.cancellation_context
        return out


@dataclass
class HookInjection:
    """A single turn the workspace asks the sampler to append.

    ``#[serde(deny_unknown_fields)]`` — :func:`hook_injection_from_wire`
    raises on any key outside ``{role, content}``. This is the crate's
    strictest struct deserialise mode (first appearance of
    ``deny_unknown_fields``).
    """

    #: Role to append the content as.
    role: InjectionRole
    #: Verbatim turn content.
    content: str

    def to_wire(self) -> dict[str, object]:
        return {"role": str(self.role), "content": self.content}


#: Known :class:`HookInjection` fields — the allowlist enforcing
#: ``#[serde(deny_unknown_fields)]``.
_HOOK_INJECTION_FIELDS: frozenset[str] = frozenset({"role", "content"})


@dataclass
class AfterTurnAckPayload:
    """Artifact-handling ack for a :class:`TurnHookRequest` after-phase.

    Returned by the workspace on :attr:`HookReply.after_turn_ack`.
    :attr:`error_message` is ``skip_serializing_if`` on ``None``;
    :attr:`artifact_count` is ``#[serde(default)]`` → ``0``.
    """

    #: The turn this ack corresponds to (matches
    #: :attr:`AfterTurnPayload.turn_number`).
    turn_number: int
    #: Terminal artifact-handling status for the turn.
    status: AfterTurnAckStatus
    #: Failure / skip reason. ``Some`` only for
    #: :data:`AfterTurnAckStatus.FAILED` or :data:`AfterTurnAckStatus.SKIPPED`;
    #: omitted from the wire when ``None``.
    #: ``#[serde(default, skip_serializing_if = "Option::is_none")]``.
    error_message: str | None = None
    #: Count of archives this turn that landed durably on the queue's on-disk
    #: spill — ``0``, ``1``, or ``2``. Informational; defaults to ``0``.
    #: ``#[serde(default)]``.
    artifact_count: int = 0

    def to_wire(self) -> dict[str, object]:
        out: dict[str, object] = {
            "turn_number": self.turn_number,
            "status": str(self.status),
            "artifact_count": self.artifact_count,
        }
        # skip_serializing_if = "Option::is_none"
        if self.error_message is not None:
            out["error_message"] = self.error_message
        return out


@dataclass
class HookReply:
    """Reply to a :class:`TurnHookRequest`.

    Turns to inject plus a loop-control decision; default (``{}``) is a
    no-op. ``#[serde(deny_unknown_fields)]`` —
    :func:`hook_reply_from_wire` raises on any key outside
    ``{injections, control, after_turn_ack}``. ``#[derive(Default)]`` —
    empty injections, :data:`TurnControl.AUTO`, ``None`` ack.
    """

    #: Turns to append before the next sampling step, in order.
    #: ``#[serde(default)]`` → empty.
    injections: list[HookInjection] = field(default_factory=list)
    #: Optional loop-control override. ``#[serde(default)]`` →
    #: :data:`TurnControl.AUTO`.
    control: TurnControl = TurnControl.AUTO
    #: Artifact-handling ack for an after-phase request; ``None`` on
    #: before-phase replies and from workspaces that predate the ack.
    #: Informational only. ``#[serde(default, skip_serializing_if)]``.
    after_turn_ack: AfterTurnAckPayload | None = None

    def to_wire(self) -> dict[str, object]:
        out: dict[str, object] = {
            "injections": [inj.to_wire() for inj in self.injections],
            "control": str(self.control),
        }
        # skip_serializing_if = "Option::is_none"
        if self.after_turn_ack is not None:
            out["after_turn_ack"] = self.after_turn_ack.to_wire()
        return out


#: Known :class:`HookReply` fields — the allowlist enforcing
#: ``#[serde(deny_unknown_fields)]``.
_HOOK_REPLY_FIELDS: frozenset[str] = frozenset({"injections", "control", "after_turn_ack"})


# ────────────────────────────────────────────────────────────────────────────
# Request union (internally tagged on phase)
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class TurnHookRequestBefore:
    """Before-phase arm of :class:`TurnHookRequest`.

    ``#[serde(tag = "phase", rename_all = "snake_case")]`` → the payload's
    fields are flattened to the top level with ``"phase": "before"`` added.
    The wrapper holds a typed :class:`BeforeTurnPayload` so the payload type
    stays independently usable (mirroring Rust's
    ``TurnHookRequest::Before(BeforeTurnPayload)`` tuple variant).
    """

    payload: BeforeTurnPayload

    def to_wire(self) -> dict[str, object]:
        out = self.payload.to_wire()
        out["phase"] = "before"  # NOT BEFORE_TURN_KIND ("before_turn")
        return out


@dataclass
class TurnHookRequestAfter:
    """After-phase arm of :class:`TurnHookRequest`.

    ``phase`` → ``"after"``. See :class:`TurnHookRequestBefore`.
    """

    payload: AfterTurnPayload

    def to_wire(self) -> dict[str, object]:
        out = self.payload.to_wire()
        out["phase"] = "after"  # NOT AFTER_TURN_KIND ("after_turn")
        return out


#: Request/response turn hook (sampler → bound workspace), internally tagged
#: on ``phase``. ``phase`` is a reserved key — :class:`BeforeTurnPayload` /
#: :class:`AfterTurnPayload` must not define a field of that name.
#: ``#[non_exhaustive]`` + no ``#[serde(other)]`` → unknown ``phase``
#: raises in :func:`turn_hook_request_from_wire`.
TurnHookRequest = TurnHookRequestBefore | TurnHookRequestAfter


# ────────────────────────────────────────────────────────────────────────────
# Wire converters (from_wire)
# ────────────────────────────────────────────────────────────────────────────


def before_turn_payload_from_wire(data: dict[str, object]) -> BeforeTurnPayload:
    """Reconstruct a :class:`BeforeTurnPayload` from its wire form.

    The four defaulted fields fall back to their serde defaults when the
    wire omits them (bare ``#[serde(default)]`` for
    :attr:`~BeforeTurnPayload.yolo_mode` /
    :attr:`~BeforeTurnPayload.conversation_message_count`; named-fn
    ``#[serde(default = "fn")]`` for
    :attr:`~BeforeTurnPayload.session_relationship` /
    :attr:`~BeforeTurnPayload.schema_version`). No
    ``deny_unknown_fields`` — unknown keys are ignored.
    """
    return BeforeTurnPayload(
        turn_number=int(data["turn_number"]),
        model_id=str(data["model_id"]),
        yolo_mode=bool(data.get("yolo_mode", False)),  # #[serde(default)]
        conversation_message_count=int(data.get("conversation_message_count", 0)),  # #[serde(default)]
        # #[serde(default = "default_session_relationship")]
        session_relationship=str(data.get("session_relationship", _default_session_relationship())),
        # #[serde(default = "default_schema_version")]
        schema_version=str(data.get("schema_version", _default_schema_version())),
    )


def after_turn_payload_from_wire(data: dict[str, object]) -> AfterTurnPayload:
    """Reconstruct an :class:`AfterTurnPayload` from its wire form.

    :attr:`~AfterTurnPayload.outcome` is a strict :class:`TurnHookOutcome`
    (unknown raises). :attr:`~AfterTurnPayload.written_repo_paths` falls
    back to empty (``#[serde(default)]``). The two cancellation fields are
    optional (``#[serde(default)]`` + ``skip_serializing_if``);
    :attr:`~AfterTurnPayload.cancellation_context` is opaque
    (:class:`object`).
    """
    cancellation_category_raw = data.get("cancellation_category")
    return AfterTurnPayload(
        turn_number=int(data["turn_number"]),
        outcome=TurnHookOutcome.from_wire(str(data["outcome"])),  # strict
        duration_ms=int(data["duration_ms"]),
        tool_call_count=int(data["tool_call_count"]),
        model_id=str(data["model_id"]),
        written_repo_paths=[str(p) for p in data.get("written_repo_paths", [])],  # #[serde(default)]
        cancellation_category=(
            None if cancellation_category_raw is None else str(cancellation_category_raw)
        ),
        cancellation_context=data.get("cancellation_context"),  # opaque, #[serde(default)]
    )


def hook_injection_from_wire(data: dict[str, object]) -> HookInjection:
    """Reconstruct a :class:`HookInjection` from its wire form.

    ``#[serde(deny_unknown_fields)]`` — any key outside ``{role, content}``
    raises :class:`ValueError`. :attr:`~HookInjection.role` is a strict
    :class:`InjectionRole` (unknown raises).
    """
    extra = set(data) - _HOOK_INJECTION_FIELDS
    if extra:  # #[serde(deny_unknown_fields)]
        raise ValueError(f"unknown HookInjection fields: {sorted(extra)}")
    return HookInjection(
        role=InjectionRole.from_wire(str(data["role"])),  # strict
        content=str(data["content"]),
    )


def after_turn_ack_payload_from_wire(data: dict[str, object]) -> AfterTurnAckPayload:
    """Reconstruct an :class:`AfterTurnAckPayload` from its wire form.

    :attr:`~AfterTurnAckPayload.status` is a strict
    :class:`AfterTurnAckStatus` (unknown raises).
    :attr:`~AfterTurnAckPayload.error_message` is optional
    (``#[serde(default)]`` + ``skip_serializing_if``);
    :attr:`~AfterTurnAckPayload.artifact_count` defaults to ``0``
    (``#[serde(default)]``).
    """
    error_message_raw = data.get("error_message")
    return AfterTurnAckPayload(
        turn_number=int(data["turn_number"]),
        status=AfterTurnAckStatus.from_wire(str(data["status"])),  # strict
        error_message=None if error_message_raw is None else str(error_message_raw),
        artifact_count=int(data.get("artifact_count", 0)),  # #[serde(default)]
    )


def hook_reply_from_wire(data: dict[str, object]) -> HookReply:
    """Reconstruct a :class:`HookReply` from its wire form.

    ``#[serde(deny_unknown_fields)]`` — any key outside
    ``{injections, control, after_turn_ack}`` raises :class:`ValueError`.
    :attr:`~HookReply.injections` / :attr:`~HookReply.control` fall back
    to their serde defaults (empty / :data:`TurnControl.AUTO`);
    :attr:`~HookReply.after_turn_ack` is optional.
    """
    extra = set(data) - _HOOK_REPLY_FIELDS
    if extra:  # #[serde(deny_unknown_fields)]
        raise ValueError(f"unknown HookReply fields: {sorted(extra)}")
    injections = [hook_injection_from_wire(d) for d in data.get("injections", [])]  # #[serde(default)]
    control = (
        TurnControl.from_wire(str(data["control"])) if "control" in data else TurnControl.AUTO
    )  # #[serde(default)] → AUTO
    after_turn_ack: AfterTurnAckPayload | None = None  # Option, skip_serializing_if
    ack_raw = data.get("after_turn_ack")
    if ack_raw is not None:
        after_turn_ack = after_turn_ack_payload_from_wire(ack_raw)  # type: ignore[arg-type]
    return HookReply(injections=injections, control=control, after_turn_ack=after_turn_ack)


def turn_hook_request_from_wire(data: dict[str, object]) -> TurnHookRequest:
    """Reconstruct a :class:`TurnHookRequest` from its wire form.

    Internally tagged on ``phase`` (``rename_all = "snake_case"``):
    ``"before"`` → :class:`TurnHookRequestBefore` wrapping a
    :class:`BeforeTurnPayload`, ``"after"`` →
    :class:`TurnHookRequestAfter` wrapping an :class:`AfterTurnPayload`.
    The payload fields are flattened to the top level alongside ``phase``,
    so the same ``data`` dict is handed to the payload ``from_wire``.

    Strict — ``#[non_exhaustive]`` with **no** ``#[serde(other)]``: an
    unknown ``phase`` raises :class:`ValueError`.

    NOTE: the ``phase`` values here (``"before"`` / ``"after"``) are the
    *inner* tag and differ from the *outer* :data:`BEFORE_TURN_KIND` /
    :data:`AFTER_TURN_KIND` strings (``"before_turn"`` / ``"after_turn"``)
    that label the enclosing :class:`~minimax_code.tool_protocol.hook.HookEvent`.
    """
    phase = str(data["phase"])
    if phase == "before":
        return TurnHookRequestBefore(payload=before_turn_payload_from_wire(data))
    if phase == "after":
        return TurnHookRequestAfter(payload=after_turn_payload_from_wire(data))
    # #[non_exhaustive] + no #[serde(other)] — strict
    raise ValueError(f"unknown TurnHookRequest phase: {phase!r}")
