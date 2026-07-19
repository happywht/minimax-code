"""Hunk-tracker type layer (R39).

Ports the **data shapes** of grok's ``xai-hunk-tracker`` — the hunk identity,
the baseline-vs-current line-range descriptor, the source-attribution union,
and the hunk record itself. These are the inputs and outputs of the diff
computation in :mod:`.diff`; no actor (tokio) or git (gix) surface is pulled
in — that is a wiring-round concern.

Mapping
-------

Per the R32/R38 policy (payload decides the mapping):

* :class:`HunkId` (grok ``HunkId(Arc<str>)`` + ``uuid::Uuid::new_v4``) →
  **frozen=True, slots=True dataclass** wrapping a ``str``. ``Arc<str>`` is
  cheap-clone shared storage; the Python analogue is an immutable ``str``
  (already shared/refcounted), so the wrapper only adds value-equality and the
  ``new`` / ``from_string`` / ``as_str`` API surface.
* :class:`HunkLineInfo` (grok ``Debug + Clone + Copy + PartialEq + Eq`` +
  ``Display``) → **frozen=True, slots=True dataclass**; ``Display`` maps to
  ``__str__`` emitting the unified-diff hunk header
  (``@@ -{os},{oc} +{ns},{nc} @@``).
* ``HunkSource`` is a Rust enum with **mixed variants** — one struct variant
  carrying ``prompt_index`` (``AgentEdit``) and two unit variants
  (``ExternalEditOnAgentFile`` / ``External``). A mixed enum maps to a
  **frozen-dataclass union** (PEP 604 ``A | B | C``); unit variants become
  field-less frozen dataclasses so every variant stays value-equal and
  hashable (mirrors Rust unit-variant ``PartialEq + Eq``). This is the same
  decision applied in R32 (``VoiceEvent``).
* :class:`Hunk` (grok ``Debug + Clone + Serialize + Deserialize``) →
  **frozen=True, slots=True dataclass**. The actor-layer accept/reject
  lifecycle is *not* a field on grok's ``Hunk`` — it lives in the tracker — so
  the ported field set is complete. ``PathBuf`` → ``str`` (the pure-logic
  subset: the path is only compared and formatted, never dereferenced on
  disk). ``DateTime<Utc>`` → ``datetime.datetime`` (tz-aware UTC).

Product fusion
--------------

MiniMax Code's edit tool overwrites files wholesale; the user has no
block-level view of what the agent just changed. This type layer is the
vocabulary a future "review the agent's edit" pass speaks: each edit becomes a
:class:`Hunk` attributed to an :class:`AgentEdit` (carrying the prompt index
that made it), so the UI can show "turn 3 changed these 2 hunks" rather than
"the file is different". The diff computation that produces these hunks lives
in :mod:`.diff`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _now_utc() -> datetime:
    """Aware UTC now (grok ``chrono::Utc::now()``)."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class HunkId:
    """Unique identifier for a hunk (grok ``HunkId(Arc<str>)``).

    Wraps a UUID v4 string (grok ``uuid::Uuid::new_v4``). Value-equal and
    hashable so hunks can be used as set/dict keys.
    """

    value: str

    @classmethod
    def new(cls) -> HunkId:
        """Generate a fresh random id (grok ``HunkId::new``)."""
        return cls(str(uuid.uuid4()))

    @classmethod
    def from_string(cls, value: str) -> HunkId:
        """Reconstruct from an existing id string (grok ``from_string``)."""
        return cls(value)

    def as_str(self) -> str:
        """The underlying id string (grok ``as_str``)."""
        return self.value


@dataclass(frozen=True, slots=True)
class HunkLineInfo:
    """Line-range descriptor of a hunk (grok ``HunkLineInfo``).

    All line numbers are 1-indexed (``old_start`` / ``new_start``); the counts
    are the number of changed lines on each side. ``old_count == 0`` marks a
    pure insertion; ``new_count == 0`` marks a pure deletion.
    """

    #: 1-indexed start line in the baseline (old) file.
    old_start: int
    #: Number of lines from the baseline that were changed/deleted.
    old_count: int
    #: 1-indexed start line in the current (new) file.
    new_start: int
    #: Number of lines in the current file that were added/modified.
    new_count: int

    def __str__(self) -> str:
        """Unified-diff hunk header (grok ``Display``)."""
        return f"@@ -{self.old_start},{self.old_count} +{self.new_start},{self.new_count} @@"


@dataclass(frozen=True, slots=True)
class AgentEdit:
    """A change made by an agent tool at a specific turn (grok ``AgentEdit``).

    ``prompt_index`` identifies which agent turn made this change so the UI can
    attribute edits to turns.
    """

    prompt_index: int


@dataclass(frozen=True, slots=True)
class ExternalEditOnAgentFile:
    """External (user) edit to a file the agent has touched (grok unit variant)."""


@dataclass(frozen=True, slots=True)
class External:
    """External edit to a file the agent has NOT touched (grok unit variant)."""


# Who made a change. Union of the three grok ``HunkSource`` variants.
HunkSource = AgentEdit | ExternalEditOnAgentFile | External


@dataclass(frozen=True, slots=True)
class Hunk:
    """One tracked change to a file (grok ``Hunk``).

    The id, the file it belongs to, where it sits in baseline vs current, who
    made it, the old/new text fragments, an optional unified-diff patch, when
    it was first detected, and whether the UI has it selected. Frozen value
    type: lifecycle mutations (accept/reject) are modeled by the tracker
    replacing or dropping the hunk, never by mutating fields in place.
    """

    id: HunkId
    path: str
    line_info: HunkLineInfo
    source: HunkSource
    old_text: str | None
    new_text: str
    patch: str | None = None
    created_at: datetime = field(default_factory=_now_utc)
    selected: bool = False

    @classmethod
    def file_created(cls, path: str, content: str, source: HunkSource) -> Hunk:
        """A hunk for a file that was created (no baseline) (grok ``file_created``).

        The ``old_*`` fields are zero (no baseline lines), ``new_start`` is 1,
        and ``new_count`` is the content's line count (at least 1, mirroring
        grok's ``content.lines().count().max(1)``).
        """
        line_count = max(len(content.splitlines()), 1)
        return cls(
            id=HunkId.new(),
            path=path,
            line_info=HunkLineInfo(0, 0, 1, line_count),
            source=source,
            old_text=None,
            new_text=content,
        )


__all__ = [
    "HunkId",
    "HunkLineInfo",
    "AgentEdit",
    "ExternalEditOnAgentFile",
    "External",
    "HunkSource",
    "Hunk",
]
