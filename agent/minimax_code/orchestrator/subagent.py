"""Sub-agent runtime — instantiate a configured agent for a single request.

A *sub-agent* is a named, persistent agent identity. The configuration
row stored in the ``agents`` table (system prompt, optional tool
allowlist, preferred model) is the "persona" — the runtime is the
mechanism that turns a row into a live :class:`~minimax_code.agent.core.AgentCore`
ready to be run.

Scope (PoC)
-----------

This module deliberately does **not** spawn a child process. A sub-agent
runs in the same Python interpreter as its parent, with its own
:class:`AgentCore` instance, its own system prompt, and (since R22) a
tool registry filtered to the row's ``tool_allowlist`` via
:func:`.resolution.resolve_subagent_spec`. The
LLM call is currently **stubbed** — the runtime builds the core but
does not stream a real response, so the IPC layer can demonstrate
end-to-end flow (config CRUD + invoke + progress) without spending
tokens.

When the real LLM wiring lands, swap :meth:`SubAgentRuntime.invoke`
to forward to :meth:`AgentCore.run` instead of synthesising a stub
chunk stream. The :class:`SubAgentHandle` shape stays the same.

Why a runtime + handle (vs. just a factory function)
----------------------------------------------------

* The :class:`SubAgentHandle` carries the constructed core so callers
  can pre-warm anything they want (custom callbacks, history provider)
  before calling :meth:`invoke`. This is a hook for the LLM-integration
  follow-up — the PoC builds the core, future phases wire it up.
* The :class:`SubAgentRuntime` is the singleton-ish entry point the
  IPC handlers use so that, later, we can add a pool / cache without
  changing the handler call site.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
import weakref
from collections.abc import Awaitable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — only for type hints
    from ..agent.llm import MiniMaxClient
    from ..workspace.child import ChildWorkspace

logger = logging.getLogger(__name__)


async def _assert_restricted_child_parent(
    target: dict[str, Any],
    installation_id: str | None,
    *,
    child_run_id: str | None = None,
    session_id: str | None = None,
    child_workspace: ChildWorkspace | None = None,
) -> None:
    """Require the live admitted parent before child file or process effects."""
    from ..app import get_db, get_execution_profiles
    from ..budget_ledger import current_handle
    from ..process_context import current_process_target
    from ..process_types import ProcessBoundaryError
    from ..run_ownership import owned_runtime
    from ..storage.dao.process_executions import ProcessExecutionsDAO
    from ..storage.dao.runs import AgentRunsDAO
    from ..workspace_ctx import current_root

    required, budget, context, db = current_process_target(), current_handle(), owned_runtime.get(), get_db()
    if (required is None or required.owner is None or budget is None or context is None or db is None
            or required.target.to_dict() != target or context.get("execution_target") != target
            or context.get("execution_installation_id") != installation_id
            or required.owner.family_id != budget.family_id
            or budget.run_id not in {required.owner.run_id, child_run_id}):
        raise ProcessBoundaryError("Restricted child has no current durable parent authority")
    root, _ = required.owner.verify_workspace()
    expected_root = child_workspace.root if child_workspace is not None else root
    if current_root() != expected_root or (child_workspace is not None and child_workspace.parent != root):
        raise ProcessBoundaryError("Restricted child workspace differs from its bound parent")
    catalog = get_execution_profiles()
    if catalog is None:
        raise ProcessBoundaryError("Restricted child profile service is unavailable")
    catalog.assert_compatible(target, installation_id)
    runs, processes = AgentRunsDAO(db), ProcessExecutionsDAO(db)
    parent = await runs.get_run(required.owner.run_id)
    binding = await processes.binding_for_run(required.owner.run_id)
    if (parent is None or parent["status"] not in {"running", "planning"}
            or (parent.get("metadata") or {}).get("budget_family_id") != budget.family_id
            or binding is None or binding.owner != required.owner or binding.target != target
            or binding.installation_id != installation_id):
        raise ProcessBoundaryError("Restricted child durable parent is inactive or changed")
    if child_run_id is not None:
        child = await runs.get_run(child_run_id)
        child_binding = await processes.binding_for_run(child_run_id)
        if (child is None or child["status"] not in {"running", "planning"}
                or child["session_id"] != session_id
                or (child.get("metadata") or {}).get("budget_family_id") != budget.family_id
                or child_binding is None or child_binding.target != target
                or child_binding.installation_id != installation_id
                or child_binding.parent_run_id != required.owner.run_id):
            raise ProcessBoundaryError("Restricted child admitted run authority changed")
        if child_workspace is not None:
            if (child_binding.owner is None
                    or child_binding.owner.family_root_run_id != required.owner.family_root_run_id
                    or child_binding.owner.verify_workspace()[0] != child_workspace.root):
                raise ProcessBoundaryError("Restricted child prepared copy authority changed")


# ---------------------------------------------------------------------------
# Wall-clock knob (single source of truth)
# ---------------------------------------------------------------------------


def subagent_wall_clock_s() -> float:
    """Per-sub-agent wall-clock timeout in seconds.

    ``MINIMAX_CODE_SUBAGENT_TIMEOUT_S`` (default 600); ``<= 0`` disables
    the timeout. Extracted from ``team_orchestrator`` in v1.4.0 so the
    tool path (``spawn_subagent``'s dispatch-timeout exemption) and the
    team path share one implementation.
    """
    raw = os.environ.get("MINIMAX_CODE_SUBAGENT_TIMEOUT_S", "")
    try:
        return float(raw)
    except ValueError:
        return 600.0


# ---------------------------------------------------------------------------
# Parent-session context (v1.4.0)
# ---------------------------------------------------------------------------
#
# Tool-path sub-agents run inside ``spawn_subagent.run()`` with no
# idea which chat session spawned them. The run needs the parent
# session id for two things: the ``agent_runs.session_id`` FK (the
# timeline row lives under the parent session) and the
# ``agent.subagent_progress`` routing key (the frontend filters by
# ``parent_session_id``). ``builtins.send_message`` publishes the
# current session at the same layer as the v1.3.0 workspace root;
# asyncio tasks inherit the ContextVar, so sub-agents spawned via
# ``create_task`` (teams, background runs) see it too.

_current_parent_session: ContextVar[str | None] = ContextVar(
    "minimax_parent_session", default=None
)


def current_parent_session() -> str | None:
    """Return the chat session id that spawned this sub-agent, if any."""
    return _current_parent_session.get()


def set_parent_session(session_id: str | None) -> Any:
    """Publish ``session_id`` as the spawning session; returns a reset token.

    Token-based (same shape as ``workspace_ctx.set_current_root``):
    pass the token to :func:`reset_parent_session` in a ``finally``
    to restore the previous value. The reset never raises.
    """
    return _current_parent_session.set(session_id)


def reset_parent_session(token: Any) -> None:
    """Restore the parent-session context to its pre-``set`` value."""
    try:
        _current_parent_session.reset(token)
    except Exception:  # pragma: no cover — defensive (bad token)
        logger.debug("parent-session reset failed", exc_info=True)


# ---------------------------------------------------------------------------
# Event routes (v1.4.0)
# ---------------------------------------------------------------------------
#
# The tool path (``spawn_subagent``) has no access to ``ctx.emit`` —
# ``app.py`` doesn't hold the IPCServer singleton and the fs_bus is
# in-memory only. Same-shaped problem (and solution) as
# ``core._ASK_USER_ROUTES``: ``builtins.send_message`` registers an
# emit callable keyed by session id while a run is in flight; the
# tool looks it up and skips silently (fail-open) when absent (stdio
# transport, CLI, tests without routes).

Emit = Awaitable[None]

_SUBAGENT_EVENT_ROUTES: dict[str, Any] = {}


def register_subagent_emit(session_id: str, emit: Any) -> None:
    """Route ``agent.subagent_progress`` events for ``session_id`` to ``emit``.

    ``emit`` is an ``async (event, payload) -> None`` callable —
    typically a thin wrapper over ``ctx.emit``.
    """
    _SUBAGENT_EVENT_ROUTES[session_id] = emit


def pop_subagent_emit(session_id: str) -> Any:
    """Drop the route for ``session_id``; returns the callable or ``None``."""
    return _SUBAGENT_EVENT_ROUTES.pop(session_id, None)


async def resolve_subagent_emit(session_id: str | None) -> Any:
    """Resolve the emit callable for ``session_id`` (``None`` when absent).

    Callers treat ``None`` as "no sink configured" and skip the event.
    """
    if not session_id:
        return None
    return _SUBAGENT_EVENT_ROUTES.get(session_id)


# ---------------------------------------------------------------------------
# Background run registry (v1.4.0)
# ---------------------------------------------------------------------------
#
# ``spawn_subagent(wait=False)`` hands the run to a background task. The
# event loop only keeps *weak* references to tasks, so a module-level
# strong-ref table is required or the task can be garbage-collected
# mid-flight (same lesson as the scheduler's ``_spawn_fire`` fire-and-
# forget set). Entries are popped by a done-callback — never manually —
# so the registry cannot leak; crashed tasks log their exception here
# because nothing else ever awaits them.

_BACKGROUND_RUNS: dict[str, asyncio.Task] = {}


class _SpawnLease:
    active = True


_spawn_lease: ContextVar[_SpawnLease | None] = ContextVar("subagent_spawn_lease", default=None)


class _SpawnFence:
    def __init__(self) -> None:
        self.closed = False
        self.active = 0
        self.tasks: set[asyncio.Task] = set()
        self.failures: list[BaseException] = []
        self.changed = asyncio.Event()

    @contextmanager
    def enter(self, *, drive: bool = False):
        parent = _spawn_lease.get()
        if self.closed and not drive and not (parent and parent.active):
            raise RuntimeError("Sub-agent admission closed for maintenance")
        lease = _SpawnLease()
        self.active += 1
        token = _spawn_lease.set(lease)
        try:
            yield
        finally:
            lease.active = False
            _spawn_lease.reset(token)
            self.active -= 1
            self.changed.set()

    def track(self, task: asyncio.Task) -> None:
        if task in self.tasks:
            return
        self.tasks.add(task)
        def done(completed: asyncio.Task) -> None:
            self.tasks.discard(completed)
            if self.closed:
                if completed.cancelled():
                    self.failures.append(asyncio.CancelledError())
                elif (error := completed.exception()) is not None:
                    self.failures.append(error)
            self.changed.set()
        task.add_done_callback(done)

    async def drain(self) -> None:
        self.closed = True
        while self.active or self.tasks:
            self.changed.clear()
            await self.changed.wait()
        if self.failures:
            raise RuntimeError("Background sub-agent cleanup was not confirmed") from self.failures[0]


_SPAWN_FENCES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def subagent_spawn_fence() -> _SpawnFence:
    loop = asyncio.get_running_loop()
    if loop not in _SPAWN_FENCES:
        _SPAWN_FENCES[loop] = _SpawnFence()
    return _SPAWN_FENCES[loop]


async def drain_background_subagents() -> None:
    """Fence new tool spawns and wait for admitted handoffs and descendants."""
    await subagent_spawn_fence().drain()


def _on_background_run_done(run_id: str, task: asyncio.Task) -> None:
    """Discard the finished task; log crashes nobody else will see."""
    _BACKGROUND_RUNS.pop(run_id, None)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:  # pragma: no cover — defensive logging
        logger.error("background sub-agent run %s crashed", run_id, exc_info=exc)


_team_children: ContextVar[Any | None] = ContextVar("team_background_children", default=None)
_team_permissions: ContextVar[Any | None] = ContextVar("team_permissions", default=None)
_run_children: ContextVar[Any | None] = ContextVar("run_background_children", default=None)


@contextmanager
def run_children_scope(children):
    """Bind background descendants to this run without changing its permissions."""
    token = _run_children.set(children)
    try:
        yield
    finally:
        _run_children.reset(token)


def current_run_children():
    return _run_children.get()


def register_background_run(run_id: str, task: asyncio.Task) -> None:
    """Hold a strong reference to a background sub-agent run task."""
    _BACKGROUND_RUNS[run_id] = task
    subagent_spawn_fence().track(task)
    from ..run_ownership import owned_runtime
    from ..schedule_context import scheduled_runtime
    schedule = owned_runtime.get() or scheduled_runtime.get()
    if schedule and schedule.get("children"):
        schedule["children"].register(run_id,task)
    elif _team_children.get() is not None:
        _team_children.get().register(run_id, task)
    elif _run_children.get() is not None:
        _run_children.get().register(run_id, task)
    task.add_done_callback(lambda _t, rid=run_id: _on_background_run_done(rid, _t))


def get_background_run(run_id: str) -> asyncio.Task | None:
    """Return the live background task for ``run_id``, if any.

    ``None`` means "not currently running" — the run either finished
    (its ``agent_runs`` row carries the outcome) or never existed.
    """
    return _BACKGROUND_RUNS.get(run_id)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SubAgentConfigError(ValueError):
    """Raised when an agent config is structurally invalid."""


# ---------------------------------------------------------------------------
# Configuration + handle
# ---------------------------------------------------------------------------


@dataclass
class SubAgentConfig:
    """The fields a sub-agent needs at runtime.

    Mirrors the columns of the ``agents`` table, but with native
    Python types (``tool_allowlist`` is a list, not a JSON string).
    v0.8.0 adds description, icon, color, category, tags, team_id,
    skills, max_iterations, and temperature for enterprise features.
    """

    name: str
    system_prompt: str = ""
    tool_allowlist: list[str] | None = None
    model: str | None = None
    # Filled in by the DAO when the row is loaded; the runtime
    # doesn't generate new ids, it consumes them.
    id: str | None = None
    # v0.8.0 extended fields
    description: str = ""
    enabled: bool = True
    icon: str = ""
    color: str = ""
    category: str = ""
    tags: list[str] | None = None
    team_id: str | None = None
    skills: list[str] | None = None
    # v1.1.1: sub-agent iteration budget, aligned with the other
    # sub-agent defaults (DAO / team orchestrator / skills runtime).
    # P0-3 v1.5.3: raised default 50 → 100 so a sub-agent writing a
    # non-trivial deliverable (HTML/JS app, multi-file refactor) can
    # reach ``report_completion`` before the valve fires. Operators
    # with a tighter SLA can still set per-row ``max_iterations``.
    # The actual runtime cap is enforced in ``AgentCore``; the agent
    # loop's *soft* completion nudge (also P0-3) fires earlier so a
    # self-aware sub-agent finishes cleanly instead of stranding a
    # truncated run.
    max_iterations: int = 100
    temperature: float | None = None
    execution_profile_id: str | None = None


@dataclass
class SubAgentHandle:
    """The live instance a caller can drive.

    Attributes
    ----------
    agent_id:
        The row's primary key (auto-generated when the row was
        created). ``None`` for ad-hoc configs that were never
        persisted.
    config:
        The resolved :class:`SubAgentConfig` the core was built from.
    core:
        The :class:`AgentCore` instance. In PoC mode ``llm=None`` is
        fine — the core is built but never actually streams.
    """

    agent_id: str | None
    config: SubAgentConfig
    core: Any = field(default=None)
    skill_profile: dict | None = None
    execution_target: dict | None = None
    execution_installation_id: str | None = None


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class SubAgentRuntime:
    """Builds and drives sub-agents.

    The runtime is intentionally small at PoC stage — its job is to
    take a :class:`SubAgentConfig` and produce a :class:`SubAgentHandle`
    whose ``.core`` is an :class:`AgentCore` configured with that
    agent's persona (system prompt + model + tool-allowlist-derived
    config knobs).

    When the real LLM wiring arrives, :meth:`invoke` will forward
    to :meth:`AgentCore.run`; today it returns a deterministic
    stub chunk so the IPC layer can demonstrate the full event
    flow without spending tokens.
    """

    #: Marker emitted as the first chunk of every stub response.
    #: Tests grep for this so they don't accidentally match user
    #: data.
    STUB_PREFIX = "stub: agent"

    def __init__(
        self,
        *,
        llm: MiniMaxClient | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        # ``llm`` is the process-wide :class:`MiniMaxClient`. When
        # ``None`` (the default), :meth:`invoke` returns the
        # deterministic stub envelope so the IPC layer can still
        # exercise the full event flow without an API key. When
        # a real client is injected, :meth:`invoke` forwards the
        # request to the :class:`AgentCore` instance and returns
        # the streamed LLM response — the same wire shape, but
        # the ``stub`` flag is set to ``False`` and ``text`` is
        # the model's actual answer.
        self._llm = llm
        # Installed only for the owning interactive run, never for a
        # process-wide/headless runtime.
        self.bind_interaction = None
        # R62: the persisted reasoning-effort override (loaded from
        # ``model_prefs`` by ``_rebuild_subagent_llm`` into the
        # process-wide ``_REASONING_EFFORT_OVERRIDE`` singleton, then
        # threaded into the runtime at construction). Threaded into every
        # sub-agent's :class:`AgentConfig` at :meth:`build` time so
        # ``model.set_reasoning_effort`` actually reaches the LLM call —
        # mirrors the main-agent injection in ``builtins.py``. ``None``
        # means "no override; use the model's own default effort" (the
        # pre-R62 behaviour), so a runtime with no stored override behaves
        # byte-identically to before.
        self._reasoning_effort = reasoning_effort

    def set_reasoning_effort(self, effort: str | None) -> None:
        """Update the runtime reasoning-effort override in place.

        Called after ``model.set_reasoning_effort`` (via
        ``rebuild_subagent_llm`` → ``_set_subagent_llm``, which rebuilds
        the whole runtime with the fresh singleton value) so the next
        :meth:`build` picks up the new effort. The LLM client itself is
        effort-agnostic — effort is a per-call parameter on
        ``stream_chat`` (R54/R55 wired config → client), not a client
        constructor field — so a hot-swap here does not require
        reconnecting the underlying ``httpx`` pool.
        """
        self._reasoning_effort = effort

    # -- construction ------------------------------------------------------

    def build(
        self,
        config: SubAgentConfig,
        *,
        registry: Any = None,
    ) -> SubAgentHandle:
        """Materialise a :class:`SubAgentHandle` from a config.

        The core's :class:`~minimax_code.agent.core.AgentConfig` is
        seeded with the sub-agent's ``system_prompt`` and the
        R22-resolved effective model. The tool registry is resolved
        via :func:`.resolution.resolve_subagent_spec`: when the config
        grants a ``tool_allowlist``, the base registry is wrapped in a
        :class:`~.resolution.FilteredToolRegistry` read-only view so
        the sub-agent can only dispatch its allowed tools; otherwise
        it inherits the parent (caller-supplied or global) registry
        in full.
        """
        if isinstance(error := getattr(self._llm, "configuration_error", None), str) and error:
            raise SubAgentConfigError(error)
        if not config.name or not isinstance(config.name, str):
            raise SubAgentConfigError(
                f"name must be a non-empty string, got {config.name!r}"
            )
        if not isinstance(config.system_prompt, str):
            raise SubAgentConfigError(
                f"system_prompt must be a string, got {type(config.system_prompt).__name__}"
            )
        # Lazy import: agent.core pulls in the LLM client + tool
        # registry, which we don't want every consumer of the
        # orchestrator module to import.
        from ..agent.core import AgentConfig, AgentCore
        from ..agent.tools import get_default_registry
        from ..models import context_window_for, default_model
        from .resolution import FilteredToolRegistry, resolve_subagent_spec

        # R22: resolve the effective model + capability surface in one
        # pure-logic step, then map the spec onto a real registry. The
        # base registry is the caller-supplied one (the global default
        # when the handler didn't inject one) — i.e. the parent surface
        # the sub-agent inherits from.
        base_registry = registry or get_default_registry()
        from ..run_ownership import owned_runtime
        from ..skill_bindings import SkillBindings, tool_dependencies, tool_identity
        owned = owned_runtime.get()
        from ..process_children import child_target
        target, installation_id = child_target(config.execution_profile_id)
        restricted = target["mode"] == "podman"
        if restricted:
            from ..process_boundary import assert_text_skills
            assert_text_skills(owned.get("skill_runtime"), config.skills)
        child_profile = None
        if owned is not None:
            bindings = SkillBindings.capture(owned.get("skill_runtime"), config.skills)
            # Reinstall only the same inherited provider identity. A different
            # skill exporting an already-present name remains a collision even
            # when it happens to use the same Tool class/schema.
            inherited_names = {
                name for item in bindings.data["skills"]
                for parent in (owned.get("tool_bindings") or {}).get("skills", [])
                if item["id"] == parent["id"] and item["provider_digest"] == parent["provider_digest"]
                and item["tool_identity"] == parent["tool_identity"]
                for name in item["provider_tools"]
            }
            parent_registry = owned.get("registry")
            if parent_registry is None:
                raise SubAgentConfigError("Accepted parent tool capability is unavailable")
            # Existing run-scoped receipt channels do not grant file/process
            # capabilities; all business tools remain bounded by the parent.
            from ..agent.tools.artifacts import ReportCompletionTool, ReportProgressTool
            channels = {t.name for t in base_registry.list()
                        if isinstance(t, (ReportCompletionTool, ReportProgressTool))}
            parent_cap = set(parent_registry.names()) | channels
            tools = bindings.candidates(base_registry, config.tool_allowlist,
                                        parent_cap=parent_cap, inherited_provider_names=inherited_names)
            if restricted:
                from ..process_boundary import assert_supported_tools
                assert_supported_tools(tools)
            from ..agent.tools.base import ToolRegistry
            private_registry = ToolRegistry()
            for tool in tools:
                private_registry.register(tool)
            base_registry = private_registry
            inherited_bindings = tool_dependencies([owned["tool_bindings"]], base_registry.names())
            child_profile = dict(version=1, skill_bindings=bindings.data,
                                 inherited_tool_bindings=inherited_bindings,
                                 allowlist=list(base_registry.names()), tool_identity=tool_identity(tools),
                                 instructions=config.system_prompt + bindings.instructions)
        spec = resolve_subagent_spec(
            config, available_tool_names=base_registry.names(),
            parent_model=str(getattr(self._llm, "default_model", "") or default_model()),
        )
        if spec.is_restricted:
            # ALLOWLIST mode: wrap the base in a read-only view that
            # hides disallowed tools. The agent loop holds this view,
            # so it literally cannot dispatch a restricted tool.
            effective_registry: Any = FilteredToolRegistry(
                base_registry, spec.allowed_tools
            )
        else:
            # ALL mode: inherit the parent's full surface unchanged.
            effective_registry = base_registry

        # R62: thread the persisted reasoning-effort override into the
        # sub-agent's AgentConfig so it reaches ``stream_chat`` (R55 wired
        # config → client → transport). ``self._reasoning_effort`` defaults
        # to ``None`` (no override — the model's own default effort),
        # preserving the pre-R62 behaviour when no override is stored or
        # when the runtime was built before any preference was loaded.
        from ..run_ownership import owned_runtime
        from ..schedule_context import scheduled_runtime
        scheduled = owned_runtime.get() or scheduled_runtime.get()
        saved = scheduled["context"] if scheduled else {}
        child_model = saved.get("model", spec.model)
        if scheduled and scheduled.get("profile_kind") == "agent" and config.model:
            if config.model not in scheduled["verified_models"]:
                raise SubAgentConfigError("Child model is not verified on the saved agent provider")
            child_model = config.model
        core_config = AgentConfig(
            model=child_model,
            system_prompt_extra=(child_profile["instructions"] if child_profile is not None else config.system_prompt) or None,
            max_iterations=config.max_iterations,
            reasoning_effort=self._reasoning_effort,
            # v1.1.0: real context window so the compaction gate opens for
            # sub-agents too (long tool loops blow the window fastest).
            context_window=context_window_for(child_model) if child_model != saved.get("model") else saved.get("context_window", context_window_for(spec.model)),
        )
        team_permissions = _team_permissions.get()
        core = AgentCore(
            llm=self._llm, registry=effective_registry, config=core_config,
            **({"permission_store": scheduled["permissions"], "permission_gater": scheduled["gater"]} if scheduled
               else {"permission_store": team_permissions["store"], "permission_gater": team_permissions["gater"]} if team_permissions else {}),
        )
        # M2 — parent+child shared budget. The parent run publishes its
        # BudgetState via ContextVar before dispatching tools; a sub-agent
        # spawned inside that scope spends from the same pot, so the run
        # cap covers every token the task (and its children) burn.
        from ..budget import current_budget

        shared_budget = current_budget()
        if shared_budget is not None:
            core.budget = shared_budget
        return SubAgentHandle(agent_id=config.id, config=config, core=core, skill_profile=child_profile,
            execution_target=target, execution_installation_id=installation_id)

    # -- invoke ------------------------------------------------------------

    async def invoke(
        self,
        handle: SubAgentHandle,
        *,
        session_id: str,
        request: str,
    ) -> dict[str, Any]:
        """Drive the sub-agent and return a final-text envelope.

        Two paths:

        1. **Real LLM** (``self._llm is not None``) — forward to
           :meth:`AgentCore.run` with ``user_message=request``. The
           response envelope uses the model's actual ``final_text``
           and reports ``stub=False``.
        2. **Stub fallback** (``self._llm is None``) — produce the
           deterministic canned response so the IPC layer can
           still exercise the full event flow without an API key.
           ``stub=True``.

        The wire shape is identical::

            {
                "agent": <name>,
                "request": <echoed request>,
                "session_id": <echoed session_id>,
                "text": <final text or stub>,
                "iterations": N,
                "tool_calls": [...],
                "stub": <True|False>,
                "usage": {...},          # v1.4.0 — bubbled token usage
                "cancelled": <bool>,     # v1.4.0 — cooperative cancel flag
                "truncated": <bool>,     # v1.4.0 — compaction flag
            }
        """
        if not request or not isinstance(request, str):
            raise SubAgentConfigError(
                f"request must be a non-empty string, got {request!r}"
            )
        if not session_id or not isinstance(session_id, str):
            raise SubAgentConfigError(
                f"session_id must be a non-empty string, got {session_id!r}"
            )

        if self.bind_interaction is not None:
            self.bind_interaction(handle.core, session_id, handle.config.name)

        # Stub path — keeps backwards compatibility for callers
        # that haven't yet wired an LLM (legacy unit tests, docs
        # examples, the no-API-key CI lane).
        if self._llm is None:
            from ..process_context import current_process_target
            from ..process_types import ProcessBoundaryError
            if current_process_target() is not None or (handle.execution_target or {}).get("mode") == "podman":
                raise ProcessBoundaryError("Restricted children require a configured runtime; stub completion is unavailable")
            return {
                "agent": handle.config.name,
                "request": request,
                "session_id": session_id,
                "text": f"{self.STUB_PREFIX} {handle.config.name} would handle: {request}",
                "iterations": 0,
                "tool_calls": [],
                "stub": True,
                "usage": {},
                "cancelled": False,
                "truncated": False,
            }

        # Real-LLM path — drive the AgentCore. The core already
        # has ``self._llm`` wired in by :meth:`build`, so the
        # response will be a genuine model answer (or a mock-mode
        # canned string if the client was built with ``mock=True``).
        core = handle.core
        if core is None:  # pragma: no cover — defensive
            raise SubAgentConfigError(
                f"sub-agent {handle.config.name!r} has no AgentCore; "
                "did you forget to call SubAgentRuntime.build?"
            )
        from ..budget_ledger import budget_scope, current_handle
        from ..execution import require_write_access
        from ..run_errors import RunPersistenceError
        from ..run_ownership import owned_runtime
        from ..workspace.child import active_child, prepare_child
        from ..workspace_ctx import (
            current_root,
            current_run_id,
            current_sandbox,
            env_or_cwd_root,
            reset_sandbox,
            set_sandbox,
        )

        # All real execution paths (tool, RPC and team) cross this boundary.
        # The legacy explicit file overlay remains file-only; otherwise a
        # child always receives an owned working copy, even sandbox=false.
        require_write_access()
        from ..process_children import child_target, prepare_child_owner
        target, installation_id = child_target(handle.config.execution_profile_id)
        if (target["mode"] == "podman" or handle.execution_target is not None) and (target != handle.execution_target
                or installation_id != handle.execution_installation_id):
            raise RunPersistenceError("Child execution profile changed after construction")
        restricted = target["mode"] == "podman"
        owned = owned_runtime.get() is not None
        durable_owner = current_run_id() if owned else None
        inherited = current_handle()
        if owned and (not durable_owner or inherited is None):
            raise RunPersistenceError("Owned sub-agent has no durable run/budget owner")
        if owned:
            from ..skill_bindings import SkillBindings, tool_dependencies, tool_identity
            profile = handle.skill_profile
            if profile is None:
                raise RunPersistenceError("Owned child skill profile is missing")
            from .. import secrets
            from ..app import get_db
            from ..schedule_context import provider_fingerprint
            from ..storage.dao.agents import AgentsDAO
            from ..storage.dao.providers import ProviderDAO
            context = owned_runtime.get()
            provider_id = context["context"].get("provider_id")
            provider = await ProviderDAO(get_db()).get(provider_id) if provider_id else None
            fingerprint = context.get("model_provider_fingerprint")
            if fingerprint is not None and (not provider or not provider.get("enabled")
                    or provider_fingerprint(provider) != fingerprint):
                raise RunPersistenceError("Owned child model provider changed or was revoked")
            if not context.get("mock", True) and (not provider or not secrets.get_provider_key(provider_id)):
                raise RunPersistenceError("Owned child provider credentials are unavailable")
            row = await AgentsDAO(get_db()).get(handle.agent_id)
            if row is None or not row.get("enabled", True):
                raise RunPersistenceError("Owned child agent is unavailable or disabled")
            latest_target, _ = child_target(row.get("execution_profile_id"))
            if latest_target != target:
                raise RunPersistenceError("Owned child execution profile was revoked")
            SkillBindings(profile["skill_bindings"]).validate(
                owned_runtime.get().get("skill_runtime"), row.get("skills"))
            inherited_bindings = SkillBindings(profile["inherited_tool_bindings"])
            inherited_bindings.validate(context.get("skill_runtime"),
                [item["id"] for item in inherited_bindings.data["skills"]])
            current = row.get("tool_allowlist")
            if current is not None and set(profile["allowlist"]) - set(current) - {"report_completion", "report_progress"}:
                raise RunPersistenceError("Owned child tool capability was revoked")
            if tool_identity(core.registry.list()) != profile["tool_identity"]:
                raise RunPersistenceError("Owned child tool identity changed")
        if restricted:
            await _assert_restricted_child_parent(target, installation_id,
                child_run_id=durable_owner, session_id=session_id)
        child = None
        if restricted or current_sandbox() is None:
            run_id = (current_run_id() if not active_child() else None) or f"run_{uuid.uuid4().hex[:12]}"
            child = await prepare_child(current_root() or env_or_cwd_root(), run_id,
                **({"restricted": True} if restricted else {}))
        try:

            if owned:
                from ..app import get_db
                from ..storage.dao.runs import AgentRunsDAO
                db = get_db()
                owner = await AgentRunsDAO(db).get_run(durable_owner) if db is not None else None
                metadata = (owner or {}).get("metadata") or {}
                family = metadata.get("budget_family_id") or (metadata.get("budget") or {}).get("family_id")
                if (owner is None or owner["session_id"] != session_id
                        or (family is not None and family != inherited.family_id)):
                    raise RunPersistenceError("Owned sub-agent durable run/session/budget binding is unavailable")
            else:
                inherited = current_handle()
            # Standalone team children have no durable child run row. Keep
            # their attempts on the durable team owner, not a workspace id.
            budget_owner = durable_owner or (inherited.run_id if inherited else current_run_id())
            descendant = inherited.descendant(budget_owner or inherited.run_id) if inherited else None
            from contextlib import ExitStack
            with ExitStack() as scopes:
                if restricted:
                    scopes.callback(reset_sandbox, set_sandbox(None))
                scopes.enter_context(budget_scope(descendant))
                if child is not None:
                    scopes.enter_context(child.scope())
                if restricted:
                    from ..process_context import registered_child_target_scope
                    binding = await prepare_child_owner(db, durable_owner, child,
                        target=target, installation_id=installation_id)
                    await _assert_restricted_child_parent(target, installation_id,
                        child_run_id=durable_owner, session_id=session_id, child_workspace=child)
                    scopes.enter_context(registered_child_target_scope(binding.owner,
                        parent_run_id=binding.parent_run_id))
                from ..run_ownership import capture_provider_identities
                child_bindings = tool_dependencies([handle.skill_profile["skill_bindings"], handle.skill_profile["inherited_tool_bindings"]], core.registry.names()) if owned else None
                owner_token = owned_runtime.set({**owned_runtime.get(), "registry": core.registry,
                    "execution_target": target, "execution_installation_id": installation_id,
                    "tool_bindings": child_bindings,
                    "provider_identities": capture_provider_identities(owned_runtime.get().get("skill_runtime"), child_bindings)}) if owned else None
                try:
                    run_result = await core.run(session_id=session_id, user_message=request)
                finally:
                    if owner_token is not None:
                        owned_runtime.reset(owner_token)
        except BaseException as exc:
            if child is not None:
                child.finish("cancelled" if isinstance(exc, asyncio.CancelledError) else "failed")
            raise
        if child is not None:
            child.finish("cancelled" if run_result.cancelled else "failed" if getattr(run_result, "budget_exhausted", False) or run_result.truncated else "completed")
        return {
            "agent": handle.config.name,
            "request": request,
            "session_id": session_id,
            "text": run_result.final_text or "",
            "iterations": run_result.iterations,
            "tool_calls": list(run_result.tool_calls),
            "verification": {"requires_review": bool(getattr(run_result, "budget_exhausted", False) or run_result.truncated or any(not item.success for item in getattr(run_result,"tool_results",[]) or []))},
            "stub": False,
            # v1.4.0 — bubble the run-level facts so callers (team
            # merger, tool envelope, telemetry) can see the real cost
            # without re-running the core.
            "usage": dict(run_result.usage),
            "cancelled": bool(run_result.cancelled),
            "truncated": bool(run_result.truncated),
            "budget_exhausted": bool(getattr(run_result, "budget_exhausted", False)),
            "partial": bool(getattr(run_result, "budget_exhausted", False) or run_result.truncated or run_result.cancelled),
            **({"workspace": child.summary(), "files_written": child.record["changes"]}
               if child is not None else {}),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_id(prefix: str = "subagent") -> str:
    """Generate a synthetic session id when the caller didn't supply one.

    Used by the IPC layer when ``agent.invoke`` arrives without a
    ``session_id`` — sub-agent invocations don't need to live inside
    a real chat session, but the ``tasks`` table has a NOT-NULL FK
    to ``sessions.id``, so we mint a placeholder row.
    """
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Process-wide runtime singleton
# ---------------------------------------------------------------------------
#
# The IPC layer needs a single :class:`SubAgentRuntime` instance so
# that the LLM client injected at app boot (see
# :func:`minimax_code.app._maybe_open_db`) is reused across every
# ``agent.invoke`` request. We don't want to construct a fresh
# ``MiniMaxClient`` (and a fresh ``httpx.AsyncClient`` under it)
# for every call.
#
# Tests use :func:`set_subagent_runtime` to swap in a runtime
# bound to a mock LLM, then restore via ``set_subagent_runtime(None)``
# or by saving/replacing the previous value.

_SUBAGENT_RUNTIME: SubAgentRuntime | None = None
_RUN_RUNTIME: ContextVar[SubAgentRuntime | None] = ContextVar("run_subagent_runtime", default=None)


@contextmanager
def subagent_runtime_scope(runtime: SubAgentRuntime):
    """Children inherit the accepted parent client through task context."""
    token = _RUN_RUNTIME.set(runtime)
    try:
        yield
    finally:
        _RUN_RUNTIME.reset(token)


def get_subagent_runtime() -> SubAgentRuntime | None:
    """Return the process-wide :class:`SubAgentRuntime`, or ``None``.

    Returns ``None`` if the runtime hasn't been built yet (e.g.
    very early at process boot, or in a test that never set it).
    Callers that need a runtime should fall back to constructing
    a default :class:`SubAgentRuntime` themselves.
    """
    return _RUN_RUNTIME.get() or _SUBAGENT_RUNTIME


def get_run_subagent_runtime() -> SubAgentRuntime | None:
    """Return only the runtime scoped to this run, never the global fallback."""
    return _RUN_RUNTIME.get()


def set_subagent_runtime(runtime: SubAgentRuntime | None) -> None:
    """Replace the cached :class:`SubAgentRuntime` (test seam).

    Pass ``None`` to clear. :func:`get_subagent_runtime` will then
    return ``None`` until something is set again.
    """
    global _SUBAGENT_RUNTIME
    _SUBAGENT_RUNTIME = runtime


__all__ = [
    "SubAgentConfig",
    "SubAgentHandle",
    "SubAgentRuntime",
    "SubAgentConfigError",
    "current_parent_session",
    "get_background_run",
    "get_subagent_runtime",
    "make_session_id",
    "pop_subagent_emit",
    "register_background_run",
    "register_subagent_emit",
    "reset_parent_session",
    "resolve_subagent_emit",
    "set_parent_session",
    "set_subagent_runtime",
    "subagent_wall_clock_s",
]
