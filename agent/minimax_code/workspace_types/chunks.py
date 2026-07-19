"""Streaming response chunk types (R81).

Fusion of grok's ``xai-grok-workspace-types::chunks`` — the RPC
response chunk surface, the fourth and last of the crate's four
top-level dispatch modules (``request`` → ``requests`` → ``events`` →
``chunks``). Where R78 landed the generic ``RequestMessage<T>``
envelope, R79 the tagged request discriminators, and R80 the event
subscription stream, R81 lands the **response chunks** that flow back
from every workspace RPC: tool-call output / progress / final / defs /
Need* handshakes, workspace-ops unary and streaming results, and
session-lifecycle acks — plus the paired sampler→workspace
:class:`ToolResponse` messages that satisfy the bidi Need* handshake.

Serde shape is uniform across the layer: all four enums
(:class:`ToolChunk` / :class:`OpsChunk` / :class:`SessionChunk` /
:class:`ToolResponse`) are adjacent-tagged
(``#[serde(tag = "type", content = "data", rename_all = "snake_case")]``),
so all four reuse the R67 :class:`AdjacentTagged` base exactly as
:class:`WorkspaceError` / :class:`WorkspaceRequest` /
:class:`WorkspaceEvent` do: each subclass declares ``_VARIANTS``
(snake_case wire tags in grok declaration order) and one factory
classmethod per arm. No new serde pattern lands here; this is a
mechanical expansion of the established adjacent-tagged recipe, plus
one discriminator accessor per chunk enum.

The discriminator accessor warrants a comment. The grok source gives
each chunk enum a ``kind() -> ChunkKind`` method returning the static
:class:`ChunkKind` for the current variant (used as the ``got`` field
of ``WorkspaceError::ProtocolMismatch`` when an unexpected chunk
arrives on the wrong stream). :class:`AdjacentTagged` already exposes
a ``.kind`` *property* holding the wire tag string (e.g. ``"ack"``);
the two are not the same thing — the property is the serde tag, the
method returns the typed :class:`ChunkKind` member. To avoid the name
clash the accessor is renamed :meth:`chunk_kind`. The tag→ChunkKind
mapping is **not** always the identity: ``SessionChunk::Ack`` maps to
``ChunkKind.SessionAck`` (value ``"session_ack"``), not
``ChunkKind.Ack`` (value ``"ack"`` — that is ``OpsChunk::Ack``'s
discriminator). The two chunk enums both expose an ``Ack`` arm but
they map to distinct :class:`ChunkKind` variants, so the map is
spelled out exhaustively per class rather than derived from the wire
value via ``ChunkKind(tag)``.

Two serde subtleties land in this layer:

* **``Option<T>`` newtype** — :meth:`OpsChunk.git_metadata` wraps
  ``Option<GitMetadata>`` (``None`` when the workspace is not a git
  repo). :func:`_payload` returns ``None`` verbatim for ``None``;
  :class:`AdjacentTagged.to_wire` emits JSON ``null`` in the ``data``
  slot, matching serde's ``Option`` rendering.
* **``BTreeMap`` sorted keys** — :meth:`OpsChunk.envrc` wraps
  ``BTreeMap<String, String>``; grok chose ``BTreeMap`` over
  ``HashMap`` for wire determinism (same rationale as
  :class:`Metadata`). The factory sorts keys so the rendered JSON is
  byte-stable.

Three ``Need*`` struct variants (:class:`ToolChunk`) and three matching
:class:`ToolResponse` arms carry a ``req_id`` correlation id plus a
typed payload; the factory hand-builds the inner ``{"req_id": ...,
<field>: ...}`` dict because there is no named struct type to delegate
to — mirroring how grok declares the fields inline on the variant.
:class:`ToolResponse` has **no** ``chunk_kind()`` accessor: it is the
response direction, not a chunk that can be mismatched, so grok does
not give it a ``kind()`` method.
"""

from __future__ import annotations

from typing import Any, ClassVar

from minimax_code.workspace_types._tagged import AdjacentTagged
from minimax_code.workspace_types.chunk_kind import ChunkKind
from minimax_code.workspace_types.identity import SessionId
from minimax_code.workspace_types.types import (
    AgentSessionInfo,
    ContentMatch,
    FuzzyMatch,
    GitBranchInfo,
    GitDiff,
    GitMetadata,
    GitStatus,
    Hunk,
    MemoryChunk,
    PermissionDecision,
    PermissionPolicy,
    PermissionRequest,
    PlanModeDecision,
    PlanModeTransition,
    PluginInfo,
    ProjectConfig,
    ResolvedFile,
    RewindPoint,
    RewindResult,
    RipgrepStats,
    SkillInfo,
    ToolCallResult,
    ToolDef,
    ToolOutputChunk,
    ToolProgress,
    UserAnswer,
    UserQuestion,
)

__all__ = [
    "OpsChunk",
    "SessionChunk",
    "ToolChunk",
    "ToolResponse",
]


def _payload(obj: Any) -> Any:
    """Coerce a typed chunk-field value to its wire-ready form.

    Twin of ``requests._payload`` (R79) and ``events._payload`` (R80);
    duplicated deliberately to keep the module self-contained. Nested
    wire enums (:class:`AdjacentTagged` subclasses like
    :class:`ToolProgress`) and :class:`WireModel` structs (like
    :class:`GitStatus`) serialise via their own ``to_wire``; ``None``
    (``Option<T>``) and raw ``str`` / ``list`` are already JSON-safe and
    pass through untouched.
    """
    if hasattr(obj, "to_wire"):
        return obj.to_wire()
    return obj


class OpsChunk(AdjacentTagged):
    """Workspace-ops streaming chunk (``chunks::ops::OpsChunk``).

    The 17-variant response enum for workspace-ops RPCs. Most variants
    are unary (one chunk then close); :meth:`fuzzy_match` and
    :meth:`ripgrep_hit` stream zero-or-more, and :meth:`ripgrep_hit` is
    followed by a single explicit :meth:`ripgrep_done` terminator.
    Adjacent-tagged; reuses the R67 :class:`AdjacentTagged` base.
    """

    _VARIANTS: ClassVar[tuple[str, ...]] = (
        "git_status",
        "git_diff",
        "git_branch_info",
        "git_metadata",
        "hunks",
        "skills",
        "plugins",
        "project_config",
        "permissions",
        "envrc",
        "resolved_files",
        "memory_chunks",
        "plugin",
        "ack",
        "fuzzy_match",
        "ripgrep_hit",
        "ripgrep_done",
    )

    _KIND_MAP: ClassVar[dict[str, ChunkKind]] = {
        "git_status": ChunkKind.GitStatus,
        "git_diff": ChunkKind.GitDiff,
        "git_branch_info": ChunkKind.GitBranchInfo,
        "git_metadata": ChunkKind.GitMetadata,
        "hunks": ChunkKind.Hunks,
        "skills": ChunkKind.Skills,
        "plugins": ChunkKind.Plugins,
        "project_config": ChunkKind.ProjectConfig,
        "permissions": ChunkKind.Permissions,
        "envrc": ChunkKind.Envrc,
        "resolved_files": ChunkKind.ResolvedFiles,
        "memory_chunks": ChunkKind.MemoryChunks,
        "plugin": ChunkKind.Plugin,
        "ack": ChunkKind.Ack,
        "fuzzy_match": ChunkKind.FuzzyMatch,
        "ripgrep_hit": ChunkKind.RipgrepHit,
        "ripgrep_done": ChunkKind.RipgrepDone,
    }

    # --- VCS -----------------------------------------------------------

    @classmethod
    def git_status(cls, status: GitStatus) -> OpsChunk:
        """Response to ``WorkspaceOpsRequest::GitStatus`` (newtype over GitStatus)."""
        return cls("git_status", _payload(status))

    @classmethod
    def git_diff(cls, diff: GitDiff) -> OpsChunk:
        """Response to ``WorkspaceOpsRequest::GitDiff`` (newtype over GitDiff)."""
        return cls("git_diff", _payload(diff))

    @classmethod
    def git_branch_info(cls, info: GitBranchInfo) -> OpsChunk:
        """Response to ``WorkspaceOpsRequest::GitBranchInfo`` (newtype over GitBranchInfo)."""
        return cls("git_branch_info", _payload(info))

    @classmethod
    def git_metadata(cls, metadata: GitMetadata | None) -> OpsChunk:
        """Response to ``WorkspaceOpsRequest::GitMetadata`` (newtype over Option<GitMetadata>).

        ``None`` when the workspace is not a git repo; :func:`_payload`
        returns ``None`` verbatim, which serde emits as JSON ``null``.
        """
        return cls("git_metadata", _payload(metadata))

    # --- discovery / read ---------------------------------------------

    @classmethod
    def hunks(cls, hunks: list[Hunk]) -> OpsChunk:
        """Response to ``WorkspaceOpsRequest::ListHunks`` (newtype over Vec<Hunk>)."""
        return cls("hunks", [_payload(h) for h in hunks])

    @classmethod
    def skills(cls, skills: list[SkillInfo]) -> OpsChunk:
        """Response to ``WorkspaceOpsRequest::DiscoverSkills`` (newtype over Vec<SkillInfo>)."""
        return cls("skills", [_payload(s) for s in skills])

    @classmethod
    def plugins(cls, plugins: list[PluginInfo]) -> OpsChunk:
        """Response to ``WorkspaceOpsRequest::DiscoverPlugins`` (newtype over Vec<PluginInfo>)."""
        return cls("plugins", [_payload(p) for p in plugins])

    @classmethod
    def project_config(cls, config: ProjectConfig) -> OpsChunk:
        """Response to ``WorkspaceOpsRequest::LoadProjectConfig`` (newtype over ProjectConfig)."""
        return cls("project_config", _payload(config))

    @classmethod
    def permissions(cls, policy: PermissionPolicy) -> OpsChunk:
        """Response to ``WorkspaceOpsRequest::LoadPermissions`` (newtype over PermissionPolicy)."""
        return cls("permissions", _payload(policy))

    @classmethod
    def envrc(cls, env: dict[str, str]) -> OpsChunk:
        """Response to ``WorkspaceOpsRequest::LoadEnvrc`` (newtype over BTreeMap<String,String>).

        Keys are sorted for wire determinism — mirrors grok's choice of
        ``BTreeMap`` over ``HashMap`` (same rationale as
        :class:`Metadata`). The on-wire JSON shape is identical (an
        object); only key order is constrained.
        """
        return cls("envrc", {k: str(env[k]) for k in sorted(env)})

    @classmethod
    def resolved_files(cls, files: list[ResolvedFile]) -> OpsChunk:
        """Response to ``WorkspaceOpsRequest::ResolveFileRefs`` (newtype over Vec<ResolvedFile>)."""
        return cls("resolved_files", [_payload(f) for f in files])

    @classmethod
    def memory_chunks(cls, chunks: list[MemoryChunk]) -> OpsChunk:
        """Response to ``WorkspaceOpsRequest::MemorySearch`` (newtype over Vec<MemoryChunk>)."""
        return cls("memory_chunks", [_payload(c) for c in chunks])

    @classmethod
    def plugin(cls, info: PluginInfo) -> OpsChunk:
        """Response to ``WorkspaceOpsRequest::InstallPlugin`` (newtype over PluginInfo)."""
        return cls("plugin", _payload(info))

    @classmethod
    def ack(cls) -> OpsChunk:
        """Acknowledgement for void ops (unit variant).

        Covers ``ActOnHunk`` / ``MemoryWrite`` / ``RefreshPlugins`` /
        similar accepted void calls.
        """
        return cls("ack", None)

    # --- streaming -----------------------------------------------------

    @classmethod
    def fuzzy_match(cls, match: FuzzyMatch) -> OpsChunk:
        """One fuzzy-search match, streamed zero-or-more (newtype over FuzzyMatch)."""
        return cls("fuzzy_match", _payload(match))

    @classmethod
    def ripgrep_hit(cls, hit: ContentMatch) -> OpsChunk:
        """One ripgrep hit, streamed zero-or-more before RipgrepDone (newtype over ContentMatch)."""
        return cls("ripgrep_hit", _payload(hit))

    @classmethod
    def ripgrep_done(cls, stats: RipgrepStats) -> OpsChunk:
        """Explicit ripgrep stream terminator (newtype over RipgrepStats).

        Positive end-of-stream marker — ripgrep hits are repeatable, so a
        terminator is needed to signal completion (absence of further
        hits is not itself a signal).
        """
        return cls("ripgrep_done", _payload(stats))

    # --- discriminator -------------------------------------------------

    def chunk_kind(self) -> ChunkKind:
        """Static :class:`ChunkKind` for the current variant.

        Mirrors grok's ``OpsChunk::kind() -> ChunkKind`` (used as the
        ``got`` field of ``WorkspaceError::ProtocolMismatch``). Renamed
        from ``kind`` to avoid clashing with :class:`AdjacentTagged`'s
        ``.kind`` property (the wire tag string). The map is exhaustive
        over :attr:`_VARIANTS`.
        """
        return self._KIND_MAP[self.kind]


class SessionChunk(AdjacentTagged):
    """Session-lifecycle streaming chunk (``chunks::session::SessionChunk``).

    The 5-variant response enum for session-lifecycle RPCs. Most
    variants are unary; :meth:`session_info` is streamed by
    ``SessionLifecycleRequest::List`` (one chunk per session).
    Adjacent-tagged; reuses the R67 :class:`AdjacentTagged` base.
    """

    _VARIANTS: ClassVar[tuple[str, ...]] = (
        "session_id",
        "session_info",
        "rewind_result",
        "rewind_points",
        "ack",
    )

    _KIND_MAP: ClassVar[dict[str, ChunkKind]] = {
        "session_id": ChunkKind.SessionId,
        "session_info": ChunkKind.SessionInfo,
        "rewind_result": ChunkKind.RewindResult,
        "rewind_points": ChunkKind.RewindPoints,
        # NOTE: tag "ack" -> ChunkKind.SessionAck (value "session_ack"),
        # NOT ChunkKind.Ack (value "ack" — that is OpsChunk::Ack's
        # discriminator). Both chunk enums expose an ``Ack`` arm but
        # they map to distinct ChunkKind variants; hence the explicit
        # map rather than ``ChunkKind(self.kind)``.
        "ack": ChunkKind.SessionAck,
    }

    @classmethod
    def session_id(cls, session_id: SessionId) -> SessionChunk:
        """Response to ``SessionLifecycleRequest::Fork`` — the new session id (newtype over SessionId)."""
        return cls("session_id", _payload(session_id))

    @classmethod
    def session_info(cls, info: AgentSessionInfo) -> SessionChunk:
        """One session metadata snapshot, streamed by ``SessionLifecycleRequest::List`` (newtype over AgentSessionInfo)."""
        return cls("session_info", _payload(info))

    @classmethod
    def rewind_result(cls, result: RewindResult) -> SessionChunk:
        """Response to ``SessionLifecycleRequest::Rewind`` (newtype over RewindResult)."""
        return cls("rewind_result", _payload(result))

    @classmethod
    def rewind_points(cls, points: list[RewindPoint]) -> SessionChunk:
        """Response to ``SessionLifecycleRequest::GetRewindPoints`` (newtype over Vec<RewindPoint>)."""
        return cls("rewind_points", [_payload(p) for p in points])

    @classmethod
    def ack(cls) -> SessionChunk:
        """Acknowledgement for void session ops (unit variant).

        Covers ``Destroy`` / ``ApplyWorktree`` / ``BeginPrompt`` /
        ``EndPrompt`` accepted void calls.
        """
        return cls("ack", None)

    def chunk_kind(self) -> ChunkKind:
        """Static :class:`ChunkKind` for the current variant (mirrors ``SessionChunk::kind()``)."""
        return self._KIND_MAP[self.kind]


class ToolChunk(AdjacentTagged):
    """Tool-call streaming chunk (``chunks::tool::ToolChunk``).

    The 7-variant chunk enum for a tool invocation. Stream contract:
    zero-or-more :meth:`output` / :meth:`progress` chunks then **exactly
    one** :meth:`final` chunk and close; a tool may additionally yield
    zero-or-more :meth:`need_permission` / :meth:`need_user_answer` /
    :meth:`need_plan_mode_change` chunks before its ``Final``, each
    blocking the tool until the sampler replies with the matching
    :class:`ToolResponse` on the paired bidi response sender (correlated
    by ``req_id``). For ``ToolRequest::Definitions`` the stream emits
    exactly one :meth:`definitions` chunk and closes. Adjacent-tagged;
    reuses the R67 :class:`AdjacentTagged` base.
    """

    _VARIANTS: ClassVar[tuple[str, ...]] = (
        "output",
        "progress",
        "final",
        "definitions",
        "need_permission",
        "need_user_answer",
        "need_plan_mode_change",
    )

    _KIND_MAP: ClassVar[dict[str, ChunkKind]] = {
        "output": ChunkKind.ToolOutput,
        "progress": ChunkKind.ToolProgress,
        "final": ChunkKind.ToolFinal,
        "definitions": ChunkKind.ToolDefinitions,
        "need_permission": ChunkKind.NeedPermission,
        "need_user_answer": ChunkKind.NeedUserAnswer,
        "need_plan_mode_change": ChunkKind.NeedPlanModeChange,
    }

    # --- stream body ---------------------------------------------------

    @classmethod
    def output(cls, chunk: ToolOutputChunk) -> ToolChunk:
        """Incremental tool output (e.g. bash stdout), zero-or-more (newtype over ToolOutputChunk)."""
        return cls("output", _payload(chunk))

    @classmethod
    def progress(cls, progress: ToolProgress) -> ToolChunk:
        """Tool-emitted progress / lifecycle event (newtype over ToolProgress)."""
        return cls("progress", _payload(progress))

    @classmethod
    def final(cls, result: ToolCallResult) -> ToolChunk:
        """Terminal result — exactly one, last; stream closes after this (newtype over ToolCallResult)."""
        return cls("final", _payload(result))

    @classmethod
    def definitions(cls, defs: list[ToolDef]) -> ToolChunk:
        """Tool definitions response — single chunk (newtype over Vec<ToolDef>)."""
        return cls("definitions", [_payload(d) for d in defs])

    # --- bidi Need* handshakes (struct variants with req_id) -----------

    @classmethod
    def need_permission(cls, req_id: str, request: PermissionRequest) -> ToolChunk:
        """Tool needs a permission decision (struct variant: req_id + request).

        The sampler must reply with :meth:`ToolResponse.permission`
        echoing ``req_id`` on the bidi response sender.
        """
        return cls(
            "need_permission",
            {"req_id": str(req_id), "request": _payload(request)},
        )

    @classmethod
    def need_user_answer(
        cls, req_id: str, questions: list[UserQuestion]
    ) -> ToolChunk:
        """Tool needs user answers (struct variant: req_id + questions).

        The sampler must reply with :meth:`ToolResponse.user_answer`
        echoing ``req_id``, supplying one :class:`UserAnswer` per
        :class:`UserQuestion` in the same order.
        """
        return cls(
            "need_user_answer",
            {"req_id": str(req_id), "questions": [_payload(q) for q in questions]},
        )

    @classmethod
    def need_plan_mode_change(
        cls, req_id: str, transition: PlanModeTransition
    ) -> ToolChunk:
        """Tool needs a plan-mode transition approval (struct variant: req_id + transition).

        The sampler must reply with
        :meth:`ToolResponse.plan_mode_change` echoing ``req_id``.
        Plan-mode transitions are deliberately *not* broadcast on the
        EventBus: sampler-caused state flows back via the call's stream
        chunks (here) and the resulting ``Final`` payload, never via
        EventBus.
        """
        return cls(
            "need_plan_mode_change",
            {"req_id": str(req_id), "transition": _payload(transition)},
        )

    def chunk_kind(self) -> ChunkKind:
        """Static :class:`ChunkKind` for the current variant (mirrors ``ToolChunk::kind()``)."""
        return self._KIND_MAP[self.kind]


class ToolResponse(AdjacentTagged):
    """Sampler→workspace bidi response (``chunks::tool::ToolResponse``).

    One :class:`ToolResponse` per :class:`ToolChunk` ``Need*`` arm,
    correlated by ``req_id`` (echoed back from the ``Need*`` chunk).
    Adjacent-tagged (``tag = "type", content = "data"``) to match every
    other wire enum in the crate. grok does **not** give this enum a
    ``kind()`` accessor — it is the response direction, not a chunk that
    can be mismatched against a stream contract — so it has no
    ``_KIND_MAP`` / :meth:`chunk_kind`. The three arms are struct
    variants carrying ``req_id`` plus a typed payload.
    """

    _VARIANTS: ClassVar[tuple[str, ...]] = (
        "permission",
        "user_answer",
        "plan_mode_change",
    )

    @classmethod
    def permission(
        cls, req_id: str, decision: PermissionDecision
    ) -> ToolResponse:
        """Reply to :meth:`ToolChunk.need_permission` (struct variant: req_id + decision)."""
        return cls(
            "permission",
            {"req_id": str(req_id), "decision": _payload(decision)},
        )

    @classmethod
    def user_answer(
        cls, req_id: str, answers: list[UserAnswer]
    ) -> ToolResponse:
        """Reply to :meth:`ToolChunk.need_user_answer` (struct variant: req_id + answers).

        One :class:`UserAnswer` per :class:`UserQuestion` in the original
        ``need_user_answer`` chunk, in the same order.
        """
        return cls(
            "user_answer",
            {"req_id": str(req_id), "answers": [_payload(a) for a in answers]},
        )

    @classmethod
    def plan_mode_change(
        cls, req_id: str, decision: PlanModeDecision
    ) -> ToolResponse:
        """Reply to :meth:`ToolChunk.need_plan_mode_change` (struct variant: req_id + decision)."""
        return cls(
            "plan_mode_change",
            {"req_id": str(req_id), "decision": _payload(decision)},
        )
