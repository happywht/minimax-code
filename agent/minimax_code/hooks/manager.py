"""HookManager — high-level facade binding a registry to an executor.

AgentCore calls the four ``fire_*`` methods at lifecycle points; the
manager aggregates per-hook outcomes so the core only has to inspect a
single :class:`PreToolOutcome` per tool call. Everything stays
fail-open: a hook that raises or crashes is logged and never propagates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .executor import HookExecutionResult, HookExecutor
from .registry import HookRegistry
from .types import HookContext, HookEvent

logger = logging.getLogger(__name__)


@dataclass
class PreToolOutcome:
    """Aggregated verdict of all pre_tool_use hooks for one tool call.

    ``blocked`` latches True on the first hook returning
    ``{"block": true}``; subsequent hooks are not run (first block
    wins). ``should_run`` is the convenience property the dispatcher
    checks.
    """

    results: list[HookExecutionResult] = field(default_factory=list)
    blocked: bool = False
    block_reason: str | None = None

    @property
    def should_run(self) -> bool:
        return not self.blocked


class HookManager:
    """Binds a :class:`HookRegistry` to a :class:`HookExecutor`.

    Construct one and hand it to :class:`~minimax_code.agent.core.AgentCore`
    (optional, ``None`` = no hooks, zero overhead). The four ``fire_*``
    methods are the lifecycle entry points.
    """

    def __init__(
        self,
        registry: HookRegistry | None = None,
        executor: HookExecutor | None = None,
    ) -> None:
        self.registry = registry or HookRegistry()
        self.executor = executor or HookExecutor()

    # -- loading ------------------------------------------------------------

    def load_hooks(self, data: dict) -> int:
        """Convenience: load Claude-style hooks JSON. Returns count added."""
        return self.registry.load_dict(data)

    def load_hooks_file(self, path: str) -> int:
        return self.registry.load_file(path)

    # -- session lifecycle (notifications) ---------------------------------

    async def fire_session_start(
        self, session_id: str, **extra: Any
    ) -> list[HookExecutionResult]:
        return await self._fire(HookEvent.SESSION_START, session_id, extra=extra)

    async def fire_session_end(
        self, session_id: str, **extra: Any
    ) -> list[HookExecutionResult]:
        return await self._fire(HookEvent.SESSION_END, session_id, extra=extra)

    # -- tool lifecycle -----------------------------------------------------

    async def fire_pre_tool_use(
        self,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
    ) -> PreToolOutcome:
        hooks = self.registry.for_event(HookEvent.PRE_TOOL_USE, tool_name=tool_name)
        outcome = PreToolOutcome()
        for hook in hooks:
            ctx = HookContext(
                event=HookEvent.PRE_TOOL_USE,
                session_id=session_id,
                tool_name=tool_name,
                tool_input=tool_input,
            )
            try:
                result = await self.executor.run(hook, ctx)
            except Exception as exc:  # noqa: BLE001 — fail-open
                logger.warning("pre_tool_use hook %s raised: %s", hook.command, exc)
                result = HookExecutionResult(error=str(exc))
            outcome.results.append(result)
            decision = result.decision
            if decision is not None and decision.block and not outcome.blocked:
                cmd = hook.command[0] if hook.command else "<hook>"
                outcome.blocked = True
                outcome.block_reason = decision.block_reason or (
                    f"blocked by pre_tool_use hook ({cmd})"
                )
                break  # first block wins
        return outcome

    async def fire_post_tool_use(
        self,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        tool_output: Any = None,
    ) -> list[HookExecutionResult]:
        return await self._fire(
            HookEvent.POST_TOOL_USE,
            session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            extra={"tool_output": tool_output},
        )

    # -- internal -----------------------------------------------------------

    async def _fire(
        self,
        event: HookEvent,
        session_id: str,
        *,
        tool_name: str | None = None,
        tool_input: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> list[HookExecutionResult]:
        hooks = self.registry.for_event(event, tool_name=tool_name)
        results: list[HookExecutionResult] = []
        for hook in hooks:
            ctx = HookContext(
                event=event,
                session_id=session_id,
                tool_name=tool_name,
                tool_input=tool_input,
                **(extra or {}),
            )
            try:
                result = await self.executor.run(hook, ctx)
            except Exception as exc:  # noqa: BLE001 — fail-open
                logger.warning("%s hook %s raised: %s", event.value, hook.command, exc)
                result = HookExecutionResult(error=str(exc))
            results.append(result)
        return results


__all__ = ["HookManager", "PreToolOutcome"]
