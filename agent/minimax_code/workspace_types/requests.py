"""Workspace request discriminators (R79).

Fusion of grok's ``xai-grok-workspace-types::requests`` — the three
tagged request enums (``WorkspaceRequest`` / ``WorkspaceOpsRequest`` /
``SessionLifecycleRequest``) plus the ``ToolCallArgs`` tool-call struct,
the second of the crate's four top-level dispatch modules (``request``
→ ``requests`` → ``events`` → ``chunks``). Where R78 landed the generic
:class:`RequestMessage` envelope, R79 lands the **tagged request
discriminators** that ride inside it.

All four enums share one serde shape — adjacent-tagged
(``#[serde(tag = "type", content = "data", rename_all = "snake_case")]``)
— so all four reuse the R67 :class:`AdjacentTagged` base exactly as
:class:`WorkspaceError` does: each subclass declares ``_VARIANTS`` (the
snake_case wire tags in grok declaration order) and one factory
classmethod per variant. No new serde pattern lands in this layer; this
is a mechanical expansion of the established adjacent-tagged recipe.

The one subtlety is **newtype variant payload coercion**. Rust's
adjacent-tagged newtype variants wrap a single inner value:
``MemoryWrite(String)`` → ``{"type": "memory_write", "data": "..."}``,
where the serde ``content`` slot carries the inner value verbatim
(strings stay strings, nested wire enums serialise via their own
``to_wire``, structs via theirs). Python's :attr:`AdjacentTagged.payload`
holds that inner value as-typed; the per-variant factory must reduce its
argument to the wire-ready form. :func:`_payload` does this uniformly:
anything with ``to_wire`` (a nested wire enum like :class:`HunkAction`,
a :class:`WireModel` struct like :class:`ToolCallArgs`) delegates; raw
``str`` / ``list`` / ``int`` pass through untouched (transparent
newtypes such as :class:`SessionId` have no ``to_wire`` and serialise as
bare strings via their pydantic core schema, so they also pass through
here).

Struct variants (``MemorySearch { query, limit }``,
``BeginPrompt { session, idx }``, etc.) hand-build the inner dict in
their factory — there is no named struct type to delegate to, mirroring
how grok declares the fields inline on the variant. The ``u32`` / ``u64``
field widths (per the crate's wire-stability rationale: ``usize`` is
host-dependent and would arbitrarily codegen to ``uint64``) are honoured
by Python ``int`` — :func:`int` is applied defensively so a ``bool`` or
a numeric subclass cannot widen the wire type.
"""

from __future__ import annotations

from typing import Any, ClassVar

from minimax_code.workspace_types._tagged import AdjacentTagged
from minimax_code.workspace_types._wire import WireModel
from minimax_code.workspace_types.identity import SessionId, ToolCallId
from minimax_code.workspace_types.types.config import AgentSessionConfig
from minimax_code.workspace_types.types.git import GitDiffArgs, GitStatusOpts
from minimax_code.workspace_types.types.hunk import HunkAction
from minimax_code.workspace_types.types.search import FuzzySearchArgs, RipgrepArgs

__all__ = [
    "ToolCallArgs",
    "ToolRequest",
    "WorkspaceOpsRequest",
    "SessionLifecycleRequest",
    "WorkspaceRequest",
]


def _payload(obj: Any) -> Any:
    """Coerce a typed enum-variant argument to its wire-ready inner value.

    Adjacent-tagged newtype variants carry a single inner value in the
    serde ``content`` slot. Nested wire enums (:class:`AdjacentTagged`
    subclasses like :class:`HunkAction`) and :class:`WireModel` structs
    (like :class:`ToolCallArgs` / :class:`AgentSessionConfig`) must
    serialise via their own :meth:`to_wire`; transparent newtypes
    (:class:`SessionId`, :class:`ToolCallId`) and raw ``str`` / ``list``
    values are already JSON-safe and pass through untouched. This mirrors
    Rust's ``serde`` recursing into each variant's inner ``Serialize``.
    """
    if hasattr(obj, "to_wire"):
        return obj.to_wire()
    return obj


class ToolCallArgs(WireModel):
    """Arguments for ``ToolRequest::Call`` (``requests::tool::ToolCallArgs``).

    Plain wire struct — ``session`` / ``tool_name`` / ``input_json``
    (``#[serde(default)]`` → empty string when absent) / ``call_id``.
    Field order in the wire dict is BTreeMap-sorted per the R67
    :class:`WireModel` contract (sorted mapping keys), not Rust
    declaration order.
    """

    session: SessionId
    tool_name: str
    input_json: str = ""
    call_id: ToolCallId


class ToolRequest(AdjacentTagged):
    """Tool-call discriminator (``requests::tool::ToolRequest``).

    Two variants: ``Call`` (newtype over :class:`ToolCallArgs`) and
    ``Definitions`` (unit — list the registered tool definitions).
    """

    _VARIANTS: ClassVar[tuple[str, ...]] = ("call", "definitions")

    @classmethod
    def call(cls, args: ToolCallArgs) -> ToolRequest:
        """Invoke a tool with the given args (newtype over ToolCallArgs)."""
        return cls("call", _payload(args))

    @classmethod
    def definitions(cls) -> ToolRequest:
        """List the registered tool definitions (unit variant)."""
        return cls("definitions", None)


class WorkspaceOpsRequest(AdjacentTagged):
    """Workspace operations discriminator (``requests::ops::WorkspaceOpsRequest``).

    The 18-variant tagged enum covering VCS inspection, hunk actions,
    search, plugin/skill discovery, project config / permissions / env,
    the ``@``-file provider, memory, and the plugin marketplace. All
    variants share a single streaming RPC; the per-variant chunk
    contract is documented on ``OpsChunk`` (most are unary; ripgrep and
    fuzzy_search stream).
    """

    _VARIANTS: ClassVar[tuple[str, ...]] = (
        "git_status",
        "git_diff",
        "git_branch_info",
        "git_metadata",
        "list_hunks",
        "act_on_hunk",
        "ripgrep",
        "fuzzy_search",
        "discover_skills",
        "discover_plugins",
        "load_project_config",
        "load_permissions",
        "load_envrc",
        "resolve_file_refs",
        "memory_search",
        "memory_write",
        "install_plugin",
        "refresh_plugins",
    )

    # --- VCS ------------------------------------------------------------

    @classmethod
    def git_status(cls, opts: GitStatusOpts) -> WorkspaceOpsRequest:
        """Read git status (newtype over GitStatusOpts)."""
        return cls("git_status", _payload(opts))

    @classmethod
    def git_diff(cls, args: GitDiffArgs) -> WorkspaceOpsRequest:
        """Read a git diff (newtype over GitDiffArgs)."""
        return cls("git_diff", _payload(args))

    @classmethod
    def git_branch_info(cls) -> WorkspaceOpsRequest:
        """Read git branch info (unit variant)."""
        return cls("git_branch_info", None)

    @classmethod
    def git_metadata(cls) -> WorkspaceOpsRequest:
        """Read git repository metadata (unit variant)."""
        return cls("git_metadata", None)

    # --- hunks ----------------------------------------------------------

    @classmethod
    def list_hunks(cls) -> WorkspaceOpsRequest:
        """List all currently-tracked hunks (unit variant)."""
        return cls("list_hunks", None)

    @classmethod
    def act_on_hunk(cls, action: HunkAction) -> WorkspaceOpsRequest:
        """Apply an action (accept / reject / revert) to a hunk (newtype over HunkAction)."""
        return cls("act_on_hunk", _payload(action))

    # --- search ---------------------------------------------------------

    @classmethod
    def ripgrep(cls, args: RipgrepArgs) -> WorkspaceOpsRequest:
        """Run a ripgrep search (newtype over RipgrepArgs)."""
        return cls("ripgrep", _payload(args))

    @classmethod
    def fuzzy_search(cls, args: FuzzySearchArgs) -> WorkspaceOpsRequest:
        """Run a fuzzy file search (newtype over FuzzySearchArgs)."""
        return cls("fuzzy_search", _payload(args))

    # --- discovery / config ---------------------------------------------

    @classmethod
    def discover_skills(cls) -> WorkspaceOpsRequest:
        """Discover skills from the configured search paths (unit variant)."""
        return cls("discover_skills", None)

    @classmethod
    def discover_plugins(cls) -> WorkspaceOpsRequest:
        """Discover plugins from the configured search paths (unit variant)."""
        return cls("discover_plugins", None)

    @classmethod
    def load_project_config(cls) -> WorkspaceOpsRequest:
        """Load the project config (unit variant)."""
        return cls("load_project_config", None)

    @classmethod
    def load_permissions(cls) -> WorkspaceOpsRequest:
        """Load the active permission policy (unit variant)."""
        return cls("load_permissions", None)

    @classmethod
    def load_envrc(cls) -> WorkspaceOpsRequest:
        """Load ``.envrc`` (and similar) into a flat env map (unit variant)."""
        return cls("load_envrc", None)

    # --- @file provider --------------------------------------------------

    @classmethod
    def resolve_file_refs(cls, refs: list[str]) -> WorkspaceOpsRequest:
        """Resolve a batch of ``@``-references to absolute paths (newtype over Vec<String>)."""
        return cls("resolve_file_refs", [str(r) for r in refs])

    # --- memory ---------------------------------------------------------

    @classmethod
    def memory_search(cls, query: str, limit: int) -> WorkspaceOpsRequest:
        """Query the memory store (struct variant: query + limit).

        ``limit`` is ``u32`` on the wire (bounded search depth — ``usize``
        would be host-dependent); :func:`int` is applied defensively.
        """
        return cls("memory_search", {"query": str(query), "limit": int(limit)})

    @classmethod
    def memory_write(cls, text: str) -> WorkspaceOpsRequest:
        """Append content to the memory store (newtype over String)."""
        return cls("memory_write", str(text))

    # --- marketplace ----------------------------------------------------

    @classmethod
    def install_plugin(cls, source: str) -> WorkspaceOpsRequest:
        """Install a plugin from the marketplace (newtype over String)."""
        return cls("install_plugin", str(source))

    @classmethod
    def refresh_plugins(cls) -> WorkspaceOpsRequest:
        """Force a refresh of the plugin discovery cache (unit variant)."""
        return cls("refresh_plugins", None)


class SessionLifecycleRequest(AdjacentTagged):
    """Session lifecycle discriminator (``requests::session::SessionLifecycleRequest``).

    The 8-variant tagged enum covering session fork / destroy / list,
    worktree application, prompt begin / end markers, rewind, and
    rewind-point enumeration.
    """

    _VARIANTS: ClassVar[tuple[str, ...]] = (
        "fork",
        "destroy",
        "list",
        "apply_worktree",
        "begin_prompt",
        "end_prompt",
        "rewind",
        "get_rewind_points",
    )

    @classmethod
    def fork(cls, config: AgentSessionConfig) -> SessionLifecycleRequest:
        """Fork a new session (newtype over AgentSessionConfig)."""
        return cls("fork", _payload(config))

    @classmethod
    def destroy(cls, session: SessionId) -> SessionLifecycleRequest:
        """Destroy a session (newtype over SessionId)."""
        return cls("destroy", _payload(session))

    @classmethod
    def list(cls) -> SessionLifecycleRequest:
        """List all sessions (unit variant).

        Method name shadows the ``list`` builtin to match the grok
        variant's snake_case wire tag; the method body does not use the
        builtin. (Ruff's rule set does not include ``flake8-builtins``,
        so ``A003`` is not raised.)
        """
        return cls("list", None)

    @classmethod
    def apply_worktree(cls, session: SessionId) -> SessionLifecycleRequest:
        """Apply a (sub)session's worktree back into the parent (newtype over SessionId)."""
        return cls("apply_worktree", _payload(session))

    @classmethod
    def begin_prompt(cls, session: SessionId, idx: int) -> SessionLifecycleRequest:
        """Mark the start of a prompt (struct variant: session + idx).

        ``idx`` is ``u64`` on the wire (not ``usize`` — host-dependent);
        :func:`int` is applied defensively.
        """
        return cls("begin_prompt", {"session": _payload(session), "idx": int(idx)})

    @classmethod
    def end_prompt(cls, session: SessionId, idx: int) -> SessionLifecycleRequest:
        """Mark the end of a prompt (struct variant: session + idx)."""
        return cls("end_prompt", {"session": _payload(session), "idx": int(idx)})

    @classmethod
    def rewind(cls, session: SessionId, target: int) -> SessionLifecycleRequest:
        """Rewind a session to a target prompt index (struct variant: session + target)."""
        return cls("rewind", {"session": _payload(session), "target": int(target)})

    @classmethod
    def get_rewind_points(cls, session: SessionId) -> SessionLifecycleRequest:
        """Enumerate the available rewind points for a session (newtype over SessionId)."""
        return cls("get_rewind_points", _payload(session))


class WorkspaceRequest(AdjacentTagged):
    """Top-level workspace request discriminator (``requests::WorkspaceRequest``).

    The three-way transport-layer dispatch into the tool / ops / session
    sub-enums (``Events`` is a separate subscription type and does not
    appear here). Each variant is a newtype over the corresponding
    sub-enum, so the nested discriminator serialises via :func:`_payload`.
    """

    _VARIANTS: ClassVar[tuple[str, ...]] = ("tool", "ops", "session")

    @classmethod
    def tool(cls, request: ToolRequest) -> WorkspaceRequest:
        """Dispatch a tool RPC (newtype over ToolRequest)."""
        return cls("tool", _payload(request))

    @classmethod
    def ops(cls, request: WorkspaceOpsRequest) -> WorkspaceRequest:
        """Dispatch a workspace-ops RPC (newtype over WorkspaceOpsRequest)."""
        return cls("ops", _payload(request))

    @classmethod
    def session(cls, request: SessionLifecycleRequest) -> WorkspaceRequest:
        """Dispatch a session-lifecycle RPC (newtype over SessionLifecycleRequest)."""
        return cls("session", _payload(request))
