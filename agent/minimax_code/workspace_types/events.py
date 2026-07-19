"""Workspace pub/sub event types (R80).

Fusion of grok's ``xai-grok-workspace-types::events`` — the
subscription event surface, the third of the crate's four top-level
dispatch modules (``request`` → ``requests`` → ``events`` →
``chunks``). Where R78 landed the generic ``RequestMessage<T>``
envelope and R79 the tagged request discriminators, R80 lands the
**event stream** a client subscribes to: workspace-observed state
changes, plus the topic-filter bitmask and the lag signal.

Three serde patterns land in this layer:

* **adjacent-tagged event union** — :class:`WorkspaceEvent` (12
  variants) and :class:`EventLag` (single variant) reuse the R67
  :class:`AdjacentTagged` base exactly as :class:`WorkspaceError` /
  :class:`WorkspaceRequest` do: declare ``_VARIANTS`` + one factory
  classmethod per arm.
* **plain snake_case enum** — :class:`WorkspaceTopic` (7 variants) is
  ``#[serde(rename_all = "snake_case")]`` (an externally-tagged enum),
  *not* adjacent-tagged: it serialises to a bare ``"fs"`` / ``"vcs"`` /
  ... string. Reproduced as a :class:`enum.StrEnum`.
* **transparent u32 bitmask newtype** — :class:`WorkspaceTopicSet` is
  ``#[serde(transparent)]`` over ``bits: u32``, so it serialises to a
  bare integer (no field-name wrapper). This is the layer's first
  transparent-*non-string* newtype: unlike :class:`SessionId` (a
  transparent *string* newtype via a pydantic core schema),
  :class:`WorkspaceTopicSet` carries methods and is never used as a
  :class:`WireModel` field, so it implements ``to_wire`` / ``from_wire``
  directly, returning a bare ``int``.

The crate's design intent (``events/mod.rs``): there is **no**
``SessionEvent`` enum. The EventBus carries only
:class:`WorkspaceEvent` — workspace-observed *external* state.
Sampler-caused state (prompt boundaries, tool-call lifecycle,
plan-mode, subagent, compaction) does not flow through the EventBus;
that rides the chunk stream (R81).

Two Rust ``mut self`` builder methods carry move semantics
(:meth:`WorkspaceTopicSet.with_topic`); the Rust name ``with`` is a
Python keyword and is renamed ``with_topic`` (documented inline).
``DateTime<Utc>`` (``GitLockHeld.until``) reuses R78's
:func:`_dt_to_wire` — its third consumer (``rpc.hunks``, ``request``,
``events``); lifting the helper into ``_wire`` is deferred to a
dedicated round.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from minimax_code.workspace_types._tagged import AdjacentTagged
from minimax_code.workspace_types.request import _dt_to_wire
from minimax_code.workspace_types.types import (
    FsEventKind,
    HookInfo,
    LspServerStatus,
    McpServerStatus,
    PluginInfo,
    SkillInfo,
    VcsKind,
)

__all__ = [
    "EventLag",
    "WorkspaceEvent",
    "WorkspaceTopic",
    "WorkspaceTopicSet",
]


def _payload(obj: Any) -> Any:
    """Coerce a typed event-field value to its wire-ready form.

    Twin of ``requests._payload`` (R79); duplicated deliberately to keep
    the module self-contained (same call made for ``_dt_to_wire`` in
    R78). Nested wire enums (:class:`AdjacentTagged` subclasses) and
    :class:`WireModel` structs serialise via their own ``to_wire``;
    ``StrEnum`` members (``FsEventKind`` / ``VcsKind`` /
    ``McpServerStatus`` / ``LspServerStatus``) and raw ``str`` / ``list``
    are already JSON-safe and pass through untouched.
    """
    if hasattr(obj, "to_wire"):
        return obj.to_wire()
    return obj


class WorkspaceTopic(StrEnum):
    """Subscription topic channel (``events::workspace::WorkspaceTopic``).

    Plain ``#[serde(rename_all = "snake_case")]`` enum — serialises to a
    bare snake_case string, *not* adjacent-tagged. One topic per
    workspace state category; :meth:`WorkspaceEvent.topic` maps each
    event variant to its topic.
    """

    Fs = "fs"
    Vcs = "vcs"
    Discovery = "discovery"
    Servers = "servers"
    Index = "index"
    Config = "config"
    Tools = "tools"


#: Stable bit index per topic (do not reorder without a wire-compat bump).
#: Mirrors ``topic_index(topic) -> usize`` in the source crate.
_TOPIC_INDEX: dict[WorkspaceTopic, int] = {
    WorkspaceTopic.Fs: 0,
    WorkspaceTopic.Vcs: 1,
    WorkspaceTopic.Discovery: 2,
    WorkspaceTopic.Servers: 3,
    WorkspaceTopic.Index: 4,
    WorkspaceTopic.Config: 5,
    WorkspaceTopic.Tools: 6,
}

#: Bit mask covering all seven topics (``(1 << N) - 1``).
_ALL_TOPIC_BITS = (1 << len(_TOPIC_INDEX)) - 1


class WorkspaceTopicSet:
    """Topic subscription filter (``events::workspace::WorkspaceTopicSet``).

    ``#[serde(transparent)]`` newtype over ``bits: u32`` — serialises to
    a bare integer bitmask, no field-name wrapper. Each topic maps to a
    stable bit (see :data:`_TOPIC_INDEX`); the set is the union of those
    bits. Not a :class:`WireModel` (the wire shape is a bare ``int``,
    not a sorted-key dict), so ``to_wire`` / ``from_wire`` are
    implemented directly on the class.

    The Rust ``with(mut self, topic) -> Self`` builder is renamed
    :meth:`with_topic` because ``with`` is a Python keyword.
    """

    __slots__ = ("bits",)

    def __init__(self, bits: int = 0) -> None:
        # Defensive int() + u32 mask: a bool or numeric subclass cannot
        # widen the wire type, and the mask keeps the value in range
        # even if a caller hands in a negative or >u32 literal.
        self.bits = int(bits) & 0xFFFFFFFF

    # -- wire ---------------------------------------------------------------

    def to_wire(self) -> int:
        """Bare u32 bitmask (transparent newtype — no wrapper)."""
        return self.bits

    @classmethod
    def from_wire(cls, data: int) -> WorkspaceTopicSet:
        """Reconstruct from a bare u32 bitmask.

        Rejects non-int and bool inputs to mirror Rust's ``u32``
        deserialiser (``bool`` is an ``int`` subclass and must be
        excluded explicitly).
        """
        if isinstance(data, bool) or not isinstance(data, int):
            raise ValueError(
                f"WorkspaceTopicSet.from_wire expects u32, got {type(data).__name__}"
            )
        return cls(data)

    # -- factories ----------------------------------------------------------

    @classmethod
    def empty(cls) -> WorkspaceTopicSet:
        """No topics subscribed (``Self::empty()``)."""
        return cls(0)

    @classmethod
    def all(cls) -> WorkspaceTopicSet:
        """All topics subscribed (``Self::all()``).

        Method name shadows the ``all`` builtin to match the grok
        factory; the body does not use the builtin. (Ruff's rule set
        does not include ``flake8-builtins``, so ``A003`` is not raised
        — same precedent as ``SessionLifecycleRequest.list`` in R79.)
        """
        return cls(_ALL_TOPIC_BITS)

    # -- ops ----------------------------------------------------------------

    def with_topic(self, topic: WorkspaceTopic) -> WorkspaceTopicSet:
        """Builder: add a topic (in-place, mirrors ``mut self``).

        Renamed from Rust ``with`` (a Python keyword).
        """
        self.bits |= 1 << _TOPIC_INDEX[topic]
        return self

    def contains(self, topic: WorkspaceTopic) -> bool:
        """Whether the given topic is in the set (``Self::contains``)."""
        return bool(self.bits & (1 << _TOPIC_INDEX[topic]))

    def is_empty(self) -> bool:
        """Whether no topics are subscribed (``Self::is_empty``)."""
        return self.bits == 0

    # -- dunder -------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        return isinstance(other, WorkspaceTopicSet) and self.bits == other.bits

    def __hash__(self) -> int:
        return hash((WorkspaceTopicSet, self.bits))

    def __repr__(self) -> str:
        return f"WorkspaceTopicSet(bits={self.bits})"


class WorkspaceEvent(AdjacentTagged):
    """Workspace state-change event (``events::workspace::WorkspaceEvent``).

    Adjacent-tagged, 12 variants. Carries only **workspace-observed**
    external state; sampler-caused state rides the chunk stream (R81),
    not this enum.

    Struct-variant fields hand-build the inner dict in their factory
    (mirrors Rust's inline variant fields); nested :class:`WireModel`
    lists (``SkillInfo`` / ``PluginInfo`` / ``HookInfo``) delegate
    element-wise via :func:`_payload`, ``StrEnum`` members pass through
    as bare strings, and the ``DateTime<Utc>`` field
    (``GitLockHeld.until``) reuses R78's :func:`_dt_to_wire`.
    """

    _VARIANTS: ClassVar[tuple[str, ...]] = (
        "fs_changed",
        "git_head_changed",
        "git_lock_held",
        "skills_changed",
        "plugins_changed",
        "hooks_changed",
        "mcp_server_state_changed",
        "lsp_server_state_changed",
        "codebase_index_updated",
        "project_config_changed",
        "permission_policy_changed",
        "tools_changed",
    )

    # -- fs -----------------------------------------------------------------

    @classmethod
    def fs_changed(cls, path: str, kind: FsEventKind) -> WorkspaceEvent:
        """A watched file changed (struct variant: path + kind)."""
        return cls("fs_changed", {"path": str(path), "kind": _payload(kind)})

    # -- vcs ----------------------------------------------------------------

    @classmethod
    def git_head_changed(
        cls, commit: str, branch: str | None, vcs: VcsKind
    ) -> WorkspaceEvent:
        """The VCS head moved (struct variant: commit + branch? + vcs).

        ``branch`` is ``Option<String>`` — ``None`` when the head is
        detached.
        """
        return cls(
            "git_head_changed",
            {
                "commit": str(commit),
                "branch": None if branch is None else str(branch),
                "vcs": _payload(vcs),
            },
        )

    @classmethod
    def git_lock_held(cls, until: datetime) -> WorkspaceEvent:
        """A VCS lock is held until an absolute time (struct variant: until).

        ``until`` is ``DateTime<Utc>`` — RFC 3339 with a ``Z`` suffix via
        R78's :func:`_dt_to_wire` (third consumer).
        """
        return cls("git_lock_held", {"until": _dt_to_wire(until)})

    # -- discovery ----------------------------------------------------------

    @classmethod
    def skills_changed(
        cls, added: list[SkillInfo], removed: list[str]
    ) -> WorkspaceEvent:
        """The discovered skill set changed (struct variant: added + removed)."""
        return cls(
            "skills_changed",
            {
                "added": [_payload(s) for s in added],
                "removed": [str(r) for r in removed],
            },
        )

    @classmethod
    def plugins_changed(
        cls, plugins: list[PluginInfo], project_trusted: bool
    ) -> WorkspaceEvent:
        """The discovered plugin set changed (struct variant: plugins + project_trusted)."""
        return cls(
            "plugins_changed",
            {
                "plugins": [_payload(p) for p in plugins],
                "project_trusted": bool(project_trusted),
            },
        )

    @classmethod
    def hooks_changed(
        cls, hooks: list[HookInfo], project_trusted: bool
    ) -> WorkspaceEvent:
        """The discovered hook set changed (struct variant: hooks + project_trusted)."""
        return cls(
            "hooks_changed",
            {
                "hooks": [_payload(h) for h in hooks],
                "project_trusted": bool(project_trusted),
            },
        )

    # -- servers ------------------------------------------------------------

    @classmethod
    def mcp_server_state_changed(
        cls, server: str, status: McpServerStatus
    ) -> WorkspaceEvent:
        """An MCP server lifecycle state changed (struct variant: server + status)."""
        return cls(
            "mcp_server_state_changed",
            {"server": str(server), "status": _payload(status)},
        )

    @classmethod
    def lsp_server_state_changed(
        cls, server: str, status: LspServerStatus
    ) -> WorkspaceEvent:
        """An LSP server lifecycle state changed (struct variant: server + status)."""
        return cls(
            "lsp_server_state_changed",
            {"server": str(server), "status": _payload(status)},
        )

    # -- index --------------------------------------------------------------

    @classmethod
    def codebase_index_updated(cls, files_indexed: int) -> WorkspaceEvent:
        """The codebase index progress advanced (struct variant: files_indexed).

        ``files_indexed`` is ``u64`` (not ``usize`` — host-dependent);
        :func:`int` is applied defensively.
        """
        return cls("codebase_index_updated", {"files_indexed": int(files_indexed)})

    # -- config -------------------------------------------------------------

    @classmethod
    def project_config_changed(cls) -> WorkspaceEvent:
        """The project config changed (unit variant)."""
        return cls("project_config_changed", None)

    @classmethod
    def permission_policy_changed(cls) -> WorkspaceEvent:
        """The permission policy changed (unit variant)."""
        return cls("permission_policy_changed", None)

    # -- tools --------------------------------------------------------------

    @classmethod
    def tools_changed(cls, session_id: str) -> WorkspaceEvent:
        """The tool set for a session changed (struct variant: session_id)."""
        return cls("tools_changed", {"session_id": str(session_id)})

    # -- classification -----------------------------------------------------

    def topic(self) -> WorkspaceTopic:
        """The subscription topic this event publishes on (``impl WorkspaceEvent``).

        Matches ``WorkspaceEvent::topic`` in the source crate: each
        variant maps to exactly one :class:`WorkspaceTopic`. ``kind`` is
        constrained to ``_VARIANTS`` by :meth:`AdjacentTagged.__init__`,
        so every arm returns.
        """
        match self.kind:
            case "fs_changed":
                return WorkspaceTopic.Fs
            case "git_head_changed" | "git_lock_held":
                return WorkspaceTopic.Vcs
            case "skills_changed" | "plugins_changed" | "hooks_changed":
                return WorkspaceTopic.Discovery
            case "mcp_server_state_changed" | "lsp_server_state_changed":
                return WorkspaceTopic.Servers
            case "codebase_index_updated":
                return WorkspaceTopic.Index
            case "project_config_changed" | "permission_policy_changed":
                return WorkspaceTopic.Config
            case "tools_changed":
                return WorkspaceTopic.Tools


class EventLag(AdjacentTagged):
    """Event-bus lag signal (``events::lag::EventLag``).

    Single-variant adjacent-tagged enum wrapping a ``u64`` count of
    dropped events. The Rust source also implements ``thiserror::Error``
    (``#[error("lagged by {0} events")]``); reproduced here as
    :meth:`__str__` (Python has no Error trait, and this type is a wire
    DTO, not a raised exception — it rides the event stream to tell a
    subscriber it fell behind).
    """

    _VARIANTS: ClassVar[tuple[str, ...]] = ("lagged",)

    @classmethod
    def lagged(cls, n: int) -> EventLag:
        """Signal that ``n`` events were dropped (newtype over u64)."""
        return cls("lagged", int(n))

    def __str__(self) -> str:
        """Display form (mirrors ``#[error("lagged by {0} events")]``).

        Single variant — ``kind`` is always ``"lagged"``.
        """
        return f"lagged by {self.payload} events"
