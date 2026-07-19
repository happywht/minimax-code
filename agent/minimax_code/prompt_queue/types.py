"""Prompt-queue wire types — fusion of grok's ``xai-prompt-queue`` (R40).

The cross-process data contract for a queue of not-yet-running user prompts:
actor-internal metadata, the wire row, and the broadcast payload. Pure data
shapes with explicit serialization rules (camelCase aliases, defaults,
None-exclusion) — no session actor (tokio) and no shell/pager consumer; those
are wiring-round concerns.

Mapping
-------

Per the payload-decides-the-mapping policy, the two roles map differently:

* :class:`QueueEntryMeta` (grok ``Clone + Debug + PartialEq + Eq``, **no**
  ``Serialize`` / ``Deserialize`` — actor-internal, never on the wire) →
  **frozen=True, slots=True dataclass**. It is a value type held in actor state
  and compared by value; it is never serialized, so pydantic would be overhead.
  This is the same mapping used for every non-serializing value type since R32.
* :class:`QueueEntryWire` and :class:`QueueChanged` (grok
  ``Serialize + Deserialize + PartialEq + Eq`` with ``#[serde(rename_all =
  "camelCase")]``) → **pydantic v2 ``BaseModel``**. The project already uses
  pydantic for its IPC data models, and pydantic maps the serde surface exactly:

  - ``#[serde(rename_all = "camelCase")]`` → ``alias_generator=to_camel`` +
    ``populate_by_name=True`` (Python fields stay snake_case; the wire uses
    camelCase aliases like ``lastEditor`` / ``runningPromptId`` / ``sessionId``).
  - ``#[serde(default)]`` → pydantic field defaults (``version=0``, ``kind=""``,
    ``text=""``, ``position=0``, ``entries=[]``).
  - ``#[serde(default, skip_serializing_if = "Option::is_none")]`` →
    ``Optional`` fields defaulting to ``None``, serialized with
    ``exclude_none=True`` (see :meth:`to_wire_json`).
  - Unknown JSON fields ignored (``extra="ignore"``, pydantic's default).
  - ``QueueChanged::default()`` (Rust ``#[derive(Default)]``) → the
    :meth:`QueueChanged.default` classmethod, which builds ``session_id=""``
    programmatically. Deserialization still *requires* ``sessionId`` (the field
    has no default), mirroring serde's "required field" rule — the two are not
    in conflict (programmatic default vs. parse requirement).

This is the **first R-round to map a wire-types crate to pydantic**. Previous
frozen-dataclass ports (R32 ``VoiceEvent``, R35/R37/R38 unit enums, R39
``HunkSource``) were all for non-serializing value types. The policy stays one
rule — **payload decides: (de)serialization surface → pydantic; plain value
equality → frozen dataclass** — this round just exercises the pydantic branch.

Product fusion
--------------

MiniMax Code's IPC is JSON-RPC 2.0, but the agent drains user input as it
arrives — there is no "queue of pending prompts" the user can see, edit, or
reorder. This package is the vocabulary for that interaction: each queued
prompt carries a stable ``id``, a monotonic ``version`` (so an edit against a
stale version is a no-op), and client attribution (``owner`` never overwritten,
``last_editor`` replaced on each edit). A future wiring round feeds these into
the agent conversation loop and a front-end queue panel; the wire contract here
is what both sides speak unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


@dataclass(frozen=True, slots=True)
class QueueEntryMeta:
    """Per-item queue metadata the session actor attaches to user-originated inputs.

    (grok ``QueueEntryMeta`` — ``Clone + Debug + PartialEq + Eq``, no Serialize.)

    Synthetic inputs (auto-wake, nudges) carry none and never appear in the
    visible queue. Held in actor state, never serialized itself. Frozen value
    type: ``version`` is bumped by building a new entry, never by mutating in
    place.
    """

    #: Stable id, reusing the prompt's unique ``prompt_id``.
    id: str
    #: Monotonic, bumped on each in-place edit; an edit against a stale version is a no-op.
    version: int
    #: Enqueuing client identifier (attribution); never overwritten by edits.
    owner: str | None
    #: Most recent editor's client identifier, replaced on every in-place edit.
    last_editor: str | None
    #: Display kind label; client-cosmetic kinds resolve to their send-intent before enqueue.
    kind: str
    #: Plain prompt text for the shared queue display.
    text: str


class QueueEntryWire(BaseModel):
    """One queue row on the wire (grok ``QueueEntryWire``).

    The serialized view of a queued prompt: camelCase aliases, defaults for
    every optional field, and ``None`` fields omitted on serialization.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        frozen=True,
    )

    id: str
    version: int = 0
    owner: str | None = None
    last_editor: str | None = None
    kind: str = ""
    text: str = ""
    #: 0-based position among queued, not-yet-running prompts.
    position: int = 0

    def to_wire_json(self) -> str:
        """Serialize with camelCase aliases and ``None`` fields omitted.

        Mirrors grok ``#[serde(skip_serializing_if = "Option::is_none")]`` on
        ``owner`` / ``last_editor``: ``exclude_none=True`` drops them when unset.
        """
        return self.model_dump_json(by_alias=True, exclude_none=True)


class QueueChanged(BaseModel):
    """Broadcast payload for the ``x.ai/queue/changed`` notification (grok ``QueueChanged``).

    Drives per-session fan-out routing (``session_id``) and lets subscribers
    adopt ``running_prompt_id`` for notification routing.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        frozen=True,
    )

    #: The session this queue belongs to; required on the wire (no default).
    session_id: str
    entries: list[QueueEntryWire] = Field(default_factory=list)
    #: The prompt the actor is currently draining, ``None`` when no turn runs.
    running_prompt_id: str | None = None

    def to_wire_json(self) -> str:
        """Serialize with camelCase aliases and ``None`` fields omitted."""
        return self.model_dump_json(by_alias=True, exclude_none=True)

    @classmethod
    def default(cls) -> QueueChanged:
        """Programmatic default (grok ``#[derive(Default)]``).

        Empty session id, no entries, nothing running. Note this builds
        ``session_id=""`` programmatically; deserialization still *requires*
        ``sessionId`` to be present (the field has no default). One is a
        constructor, the other a parse requirement — not in conflict.
        """
        return cls(session_id="")


__all__ = [
    "QueueEntryMeta",
    "QueueEntryWire",
    "QueueChanged",
]
