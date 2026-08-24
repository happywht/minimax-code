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
from collections.abc import Awaitable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — only for type hints
    from ..agent.llm import MiniMaxClient

logger = logging.getLogger(__name__)


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


def _on_background_run_done(run_id: str, task: asyncio.Task) -> None:
    """Discard the finished task; log crashes nobody else will see."""
    _BACKGROUND_RUNS.pop(run_id, None)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:  # pragma: no cover — defensive logging
        logger.error("background sub-agent run %s crashed", run_id, exc_info=exc)


def register_background_run(run_id: str, task: asyncio.Task) -> None:
    """Hold a strong reference to a background sub-agent run task."""
    _BACKGROUND_RUNS[run_id] = task
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
    max_iterations: int = 50
    temperature: float | None = None


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
        from ..models import context_window_for
        from .resolution import FilteredToolRegistry, resolve_subagent_spec

        # R22: resolve the effective model + capability surface in one
        # pure-logic step, then map the spec onto a real registry. The
        # base registry is the caller-supplied one (the global default
        # when the handler didn't inject one) — i.e. the parent surface
        # the sub-agent inherits from.
        base_registry = registry or get_default_registry()
        spec = resolve_subagent_spec(
            config, available_tool_names=base_registry.names()
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
        core_config = AgentConfig(
            model=spec.model,
            system_prompt_extra=config.system_prompt or None,
            max_iterations=config.max_iterations,
            reasoning_effort=self._reasoning_effort,
            # v1.1.0: real context window so the compaction gate opens for
            # sub-agents too (long tool loops blow the window fastest).
            context_window=context_window_for(spec.model),
        )
        core = AgentCore(
            llm=self._llm, registry=effective_registry, config=core_config
        )
        return SubAgentHandle(agent_id=config.id, config=config, core=core)

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

        # Stub path — keeps backwards compatibility for callers
        # that haven't yet wired an LLM (legacy unit tests, docs
        # examples, the no-API-key CI lane).
        if self._llm is None:
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
        run_result = await core.run(
            session_id=session_id, user_message=request
        )
        return {
            "agent": handle.config.name,
            "request": request,
            "session_id": session_id,
            "text": run_result.final_text or "",
            "iterations": run_result.iterations,
            "tool_calls": list(run_result.tool_calls),
            "stub": False,
            # v1.4.0 — bubble the run-level facts so callers (team
            # merger, tool envelope, telemetry) can see the real cost
            # without re-running the core.
            "usage": dict(run_result.usage),
            "cancelled": bool(run_result.cancelled),
            "truncated": bool(run_result.truncated),
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


def get_subagent_runtime() -> SubAgentRuntime | None:
    """Return the process-wide :class:`SubAgentRuntime`, or ``None``.

    Returns ``None`` if the runtime hasn't been built yet (e.g.
    very early at process boot, or in a test that never set it).
    Callers that need a runtime should fall back to constructing
    a default :class:`SubAgentRuntime` themselves.
    """
    return _SUBAGENT_RUNTIME


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
