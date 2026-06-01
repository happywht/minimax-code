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
:class:`AgentCore` instance, its own system prompt, and (in a future
phase) a tool registry filtered to the row's ``tool_allowlist``. The
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

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — only for type hints
    from ..agent.core import AgentCore

logger = logging.getLogger(__name__)


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
    """

    name: str
    system_prompt: str = ""
    tool_allowlist: list[str] | None = None
    model: str | None = None
    # Filled in by the DAO when the row is loaded; the runtime
    # doesn't generate new ids, it consumes them.
    id: str | None = None


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

    def __init__(self, *, llm: Any = None) -> None:
        # ``llm`` is accepted for forward compatibility — once a real
        # MiniMax client is wired into sub-agents we'll pass it through
        # to the new :class:`AgentCore`. In PoC mode it's never used.
        self._llm = llm

    # -- construction ------------------------------------------------------

    def build(
        self,
        config: SubAgentConfig,
        *,
        registry: Any = None,
    ) -> SubAgentHandle:
        """Materialise a :class:`SubAgentHandle` from a config.

        The core's :class:`~minimax_code.agent.core.AgentConfig` is
        seeded with the sub-agent's ``system_prompt`` and ``model``
        (when provided). The tool registry defaults to the global
        one — Phase 2 will swap in a filtered view derived from
        ``config.tool_allowlist``.
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

        core_config = AgentConfig(
            model=config.model or "MiniMax-M3",
            system_prompt_extra=config.system_prompt or None,
        )
        core = AgentCore(llm=self._llm, registry=registry, config=core_config)
        return SubAgentHandle(agent_id=config.id, config=config, core=core)

    # -- stub invoke -------------------------------------------------------

    async def invoke(
        self,
        handle: SubAgentHandle,
        *,
        session_id: str,
        request: str,
    ) -> dict[str, Any]:
        """Stub invoke — produces a deterministic response envelope.

        The PoC shape is::

            {
                "agent": <name>,
                "request": <echoed request>,
                "session_id": <echoed session_id>,
                "text": "stub: agent <name> would handle: <request>",
                "iterations": 0,
                "tool_calls": [],
                "stub": True,
            }

        When the real LLM is wired in, this method will forward to
        :meth:`AgentCore.run` and stitch the streaming response +
        progress events through the same ``emit`` callback the
        tracker uses.
        """
        if not request or not isinstance(request, str):
            raise SubAgentConfigError(
                f"request must be a non-empty string, got {request!r}"
            )
        if not session_id or not isinstance(session_id, str):
            raise SubAgentConfigError(
                f"session_id must be a non-empty string, got {session_id!r}"
            )
        return {
            "agent": handle.config.name,
            "request": request,
            "session_id": session_id,
            "text": f"{self.STUB_PREFIX} {handle.config.name} would handle: {request}",
            "iterations": 0,
            "tool_calls": [],
            "stub": True,
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


__all__ = [
    "SubAgentConfig",
    "SubAgentHandle",
    "SubAgentRuntime",
    "SubAgentConfigError",
    "make_session_id",
]
