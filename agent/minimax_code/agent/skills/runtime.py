"""Skill runtime — invoke a skill through the agent loop.

The runtime is the bridge between the skill catalogue (a static
:mod:`SkillRegistry`) and the conversation loop (:class:`AgentCore`).
Invoking a skill is a four-step dance:

1. **Inject instructions.** The skill's :meth:`Skill.instructions`
   are appended to the agent's system prompt so the LLM knows what
   behaviour the skill represents.
2. **Expose tools.** Each name the skill declares in its
   ``tools:`` frontmatter is *added* to the global
   :class:`ToolRegistry` (replacing the previous instance if the
   name collides), so the LLM sees them in its function-calling
   payload.
3. **Run.** The user request is dispatched to a fresh
   :class:`AgentCore` (so the per-skill system prompt and tool
   list stay isolated from the main agent). Streaming events are
   forwarded via the supplied callbacks.
4. **Tear down.** Skill-specific tools are unregistered so the
   main agent loop returns to its baseline tool list.

The runtime is deliberately state-light — it borrows the supplied
LLM client, tool registry, and storage handle, and never owns a
lifecycle of its own. This makes it trivial to test (just hand it
a fake LLM and an in-memory tool registry).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .loader import Skill
from .registry import SkillRegistry, SkillNotFoundError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SkillInvokeError(RuntimeError):
    """Wraps any failure that prevents a skill from producing output."""


# ---------------------------------------------------------------------------
# Callback aliases
# ---------------------------------------------------------------------------
#
# The runtime exposes the same callback shape the IPC layer hooks
# up to the agent core. The runtime's callers (mostly the IPC
# handlers) translate them into ``agent.*`` events for the frontend.


ChunkCallback = Callable[[str, bool], Awaitable[None]]
"""(delta, done) — pushed after every streamed chunk."""

ToolCallCallback = Callable[[dict[str, Any]], Awaitable[None]]
"""Pushed when a tool call starts executing."""

ToolResultCallback = Callable[[dict[str, Any], Any], Awaitable[None]]
"""Pushed after a tool finishes, with the result payload."""

StatusCallback = Callable[[str, dict[str, Any]], Awaitable[None]]
"""High-level status updates: "thinking", "calling_tool", "finalizing", …."""


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class SkillInvokeResult:
    """Final state of a :func:`invoke_skill` call."""

    skill_id: str
    final_text: str
    iterations: int
    tool_calls: list[dict[str, Any]]
    cancelled: bool = False
    truncated: bool = False


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class SkillRuntime:
    """Bridges a :class:`SkillRegistry` and the agent conversation loop.

    Parameters
    ----------
    registry:
        The skill catalogue (source of truth for what skills
        exist). The runtime never mutates the registry's
        enable/disable flags; it only *reads*.
    llm:
        Configured LLM client. The runtime does not close it.
    tool_registry:
        The agent's global tool registry. Skill-specific tools
        are added on top of this and removed on tear-down.
    storage:
        Optional storage layer (currently unused; reserved for
        future persistence hooks).
    agent_factory:
        Callable that builds a new :class:`AgentCore` instance.
        Defaults to a constructor that uses the supplied
        ``llm`` and ``tool_registry``. Tests inject a factory
        that returns a stub core so they can assert on the
        call shape without hitting the LLM.
    """

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        llm: Any | None = None,
        tool_registry: Any | None = None,
        storage: Any | None = None,
        agent_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.registry = registry
        self.llm = llm
        self._default_tool_registry = tool_registry
        self.storage = storage
        self._agent_factory = agent_factory or _default_agent_factory
        # Per-skill lock so a single skill isn't invoked twice in
        # parallel (the agent core is not re-entrant).
        self._invoke_locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        # Active tool providers keyed by skill_id. A "tool
        # provider" is a callable that takes the global tool
        # registry and registers a set of tools on it. We keep
        # it weak-coupling (no inheritance, just a callable) so
        # the runtime stays easy to test.
        self._tool_providers: dict[str, SkillToolProvider] = {}

    # -- tool-provider registration ----------------------------------------

    def register_tool_provider(self, skill: Skill, provider: "SkillToolProvider") -> None:
        """Register a callable that exposes ``skill``'s tools to the agent.

        The runtime calls ``provider.install(tool_registry)`` when
        the skill is invoked, and ``provider.uninstall(...)`` on
        tear-down. Built-in skills ship their tools in
        :mod:`minimax_code.agent.skills._builtin.<name>.tools`;
        the loader wires them up via this method on startup.
        """
        self._tool_providers[skill.skill_id] = provider

    def has_tool_provider(self, skill_id: str) -> bool:
        return skill_id in self._tool_providers

    # -- main entry point --------------------------------------------------

    async def invoke(
        self,
        skill_id: str,
        *,
        request: str,
        session_id: str,
        on_chunk: ChunkCallback | None = None,
        on_tool_call: ToolCallCallback | None = None,
        on_tool_result: ToolResultCallback | None = None,
        on_status: StatusCallback | None = None,
        model: str | None = None,
        max_iterations: int | None = None,
    ) -> SkillInvokeResult:
        """Invoke ``skill_id`` with the user's ``request``.

        The method streams chunks / tool events through the
        supplied callbacks, and returns a :class:`SkillInvokeResult`
        with the final assistant text and accounting.

        Raises
        ------
        SkillNotFoundError
            If ``skill_id`` is not in the registry.
        SkillInvokeError
            If the skill is disabled, the tool provider is not
            installed, or the agent core raises a non-LLM error.
        """
        if not isinstance(request, str):
            raise SkillInvokeError("'request' must be a string")
        if not request.strip():
            raise SkillInvokeError("'request' is empty")

        skill = self.registry.get(skill_id)  # raises SkillNotFoundError
        if not skill.enabled:
            raise SkillInvokeError(f"skill {skill_id!r} is disabled")

        provider = self._tool_providers.get(skill_id)
        if provider is None and not skill.tools:
            provider = _NoopSkillToolProvider()
        if provider is None:
            raise SkillInvokeError(
                f"no tool provider registered for skill {skill_id!r}"
            )

        lock = await self._lock_for(skill_id)
        async with lock:
            return await self._invoke_locked(
                skill=skill,
                request=request,
                session_id=session_id,
                on_chunk=on_chunk,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                on_status=on_status,
                model=model,
                max_iterations=max_iterations,
                provider=provider,
            )

    async def _invoke_locked(
        self,
        *,
        skill: Skill,
        request: str,
        session_id: str,
        provider: "SkillToolProvider",
        on_chunk: ChunkCallback | None,
        on_tool_call: ToolCallCallback | None,
        on_tool_result: ToolResultCallback | None,
        on_status: StatusCallback | None,
        model: str | None,
        max_iterations: int | None,
    ) -> SkillInvokeResult:
        tool_registry = self._default_tool_registry
        if tool_registry is None:
            # No global tool registry was provided — we still
            # allow the skill to run, but tools won't be wired
            # to anything the LLM can call.
            logger.warning(
                "no tool_registry supplied to SkillRuntime; skill %s will run without tools",
                skill.skill_id,
            )
            tool_registry = None

        installed = provider.install(tool_registry) if tool_registry is not None else set()
        self.registry.register_skill_tools(skill, installed)
        try:
            agent = self._agent_factory(
                llm=self.llm,
                tool_registry=tool_registry,
                build_core=_build_agent_core,
                skill=skill,
                model=model,
                max_iterations=max_iterations,
            )
            if on_chunk is not None:
                agent.on_chunk = on_chunk  # type: ignore[attr-defined]
            if on_tool_call is not None:
                agent.on_tool_call = on_tool_call  # type: ignore[attr-defined]
            if on_tool_result is not None:
                agent.on_tool_result = on_tool_result  # type: ignore[attr-defined]
            if on_status is not None:
                agent.on_status = on_status  # type: ignore[attr-defined]

            await _emit(on_status, "skill_invoked", {"skill_id": skill.skill_id})
            try:
                outcome = await agent.run(session_id=session_id, user_message=request)
            except Exception as exc:
                raise SkillInvokeError(f"agent loop failed: {exc}") from exc
            return SkillInvokeResult(
                skill_id=skill.skill_id,
                final_text=outcome.final_text,
                iterations=outcome.iterations,
                tool_calls=list(outcome.tool_calls),
                cancelled=outcome.cancelled,
                truncated=outcome.truncated,
            )
        finally:
            try:
                provider.uninstall(tool_registry) if tool_registry is not None else None
            except Exception:  # pragma: no cover — defensive
                logger.exception("provider.uninstall failed for %s", skill.skill_id)
            self.registry.unregister_skill_tools(skill.skill_id)

    # -- helpers -----------------------------------------------------------

    async def _lock_for(self, skill_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._invoke_locks.get(skill_id)
            if lock is None:
                lock = asyncio.Lock()
                self._invoke_locks[skill_id] = lock
            return lock


# ---------------------------------------------------------------------------
# Tool provider protocol
# ---------------------------------------------------------------------------


class SkillToolProvider:
    """Abstract interface for "expose these tools to the agent".

    A provider is constructed once per skill, then handed to the
    runtime via :meth:`SkillRuntime.register_tool_provider`. The
    runtime calls :meth:`install` when the skill is invoked and
    :meth:`uninstall` when the invocation completes. Implementations
    are typically very thin wrappers around a list of
    :class:`~minimax_code.agent.tools.Tool` instances, but the
    protocol is async so a provider *could* reach out to the
    network (e.g. a marketplace skill) without blocking the rest
    of the system.
    """

    def install(self, tool_registry: Any) -> set[str]:
        """Add this skill's tools to ``tool_registry``. Return names added."""
        raise NotImplementedError

    def uninstall(self, tool_registry: Any) -> None:
        """Remove the tools added in :meth:`install`. Idempotent."""
        raise NotImplementedError


class _NoopSkillToolProvider(SkillToolProvider):
    """Provider for instruction-only skills that declare no tools."""

    def install(self, tool_registry: Any) -> set[str]:
        return set()

    def uninstall(self, tool_registry: Any) -> None:
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_agent_core(
    *,
    llm: Any | None,
    tool_registry: Any | None,
    skill: Skill,
    model: str | None,
    max_iterations: int | None,
) -> Any:
    """Construct an :class:`AgentCore` for one skill invocation.

    Imports are lazy so :mod:`minimax_code.agent.skills` does not
    pull the whole agent package at import time (which would
    create a cycle on cold start).
    """
    from ..core import AgentConfig, AgentCore  # lazy: avoids circular import

    config = AgentConfig(
        model=model or "MiniMax-M3",
        max_iterations=int(max_iterations) if max_iterations else 8,
        skill_instructions=skill.instructions,
    )
    return AgentCore(
        llm=llm,
        registry=tool_registry,
        config=config,
    )


def _default_agent_factory(
    *,
    llm: Any | None,
    tool_registry: Any | None,
    build_core: Callable[..., Any],
    skill: Skill,
    model: str | None,
    max_iterations: int | None,
) -> Any:
    """Build a fresh :class:`AgentCore` for one skill invocation.

    The factory keeps the runtime agnostic of the agent-core
    import (so unit tests don't pay the cost of the LLM client).
    """
    return build_core(
        llm=llm,
        tool_registry=tool_registry,
        skill=skill,
        model=model,
        max_iterations=max_iterations,
    )


async def _emit(
    callback: Callable[..., Awaitable[None]] | None,
    status: str,
    detail: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        await callback(status, detail)
    except Exception:  # pragma: no cover — defensive
        logger.exception("status callback raised")


__all__ = [
    "ChunkCallback",
    "SkillInvokeError",
    "SkillInvokeResult",
    "SkillNotFoundError",
    "SkillRuntime",
    "SkillToolProvider",
    "StatusCallback",
    "ToolCallCallback",
    "ToolResultCallback",
]
