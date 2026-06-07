"""Team orchestrator — parallel, sequential, and round-robin multi-agent runs.

The orchestrator takes a named team template (from the ``agent_teams`` table),
resolves its member agents, and drives them according to the team's
``orchestration_mode``:

* **parallel**  — ``asyncio.gather``; all agents receive the same request.
* **sequential** — agents run one-by-one; each receives the original request
  plus the accumulated output of the previous agents.
* **round-robin** — all agents receive the same request; the orchestrator
  returns the first successful result.

After all agents finish, the orchestrator runs a lightweight **conflict
detection** pass that checks whether multiple agents attempted to write
to the same file path (via ``write_file`` / ``edit_file`` tool calls).

v0.8.0 — Enterprise Multi-Agent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — type hints only
    from ..agent.llm import MiniMaxClient
    from ..storage.dao.agent_teams import AgentTeamDAO
    from ..storage.dao.agents import AgentDAO

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AgentRunResult:
    """Outcome of a single agent within a team run."""

    agent_name: str
    success: bool
    text: str = ""
    error: str = ""
    iterations: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stub: bool = True


@dataclass
class TeamConflict:
    """A detected file-write conflict between agents."""

    file_path: str
    agents: list[str]
    conflict_type: str = "concurrent_write"  # concurrent_write | overlapping_edit


@dataclass
class TeamRunResult:
    """The full result of a team orchestration run."""

    team_name: str
    orchestration_mode: str
    agents_run: list[AgentRunResult] = field(default_factory=list)
    merged_text: str = ""
    conflicts: list[TeamConflict] = field(default_factory=list)
    task_id: str | None = None
    success: bool = True


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class TeamOrchestrator:
    """Drive a named team through its orchestration mode.

    Parameters
    ----------
    team_dao:
        DAO for loading team templates.
    agent_dao:
        DAO for loading agent configs.
    llm:
        Optional :class:`MiniMaxClient`. When ``None``, agents use the
        stub path (no real LLM calls).
    emit_event:
        Optional async callback ``async (event_name, payload) -> None``
        for pushing ``agent.team_progress`` events over the WebSocket.
    """

    def __init__(
        self,
        *,
        team_dao: "AgentTeamDAO | None" = None,
        agent_dao: "AgentDAO | None" = None,
        llm: "MiniMaxClient | None" = None,
        emit_event: Any | None = None,
    ) -> None:
        self._team_dao = team_dao
        self._agent_dao = agent_dao
        self._llm = llm
        self._emit_event = emit_event

    # -- public API ---------------------------------------------------------

    async def run(
        self,
        team_name: str,
        request: str,
        *,
        session_id: str | None = None,
        parent_session_id: str | None = None,
    ) -> TeamRunResult:
        """Execute a team run.

        Resolves the team template, builds handles for each member
        agent, drives them according to the orchestration mode, and
        returns a merged :class:`TeamRunResult`.
        """
        task_id = f"teamrun_{uuid.uuid4().hex[:10]}"

        if self._team_dao is None:
            return TeamRunResult(
                team_name=team_name,
                orchestration_mode="parallel",
                task_id=task_id,
                success=False,
                merged_text="Error: team DAO not available",
            )

        # Resolve team template
        team = await self._team_dao.get_by_name(team_name)
        if team is None:
            return TeamRunResult(
                team_name=team_name,
                orchestration_mode="parallel",
                task_id=task_id,
                success=False,
                merged_text=f"Error: unknown team {team_name!r}",
            )
        if not team.get("enabled", True):
            return TeamRunResult(
                team_name=team_name,
                orchestration_mode=team["orchestration_mode"],
                task_id=task_id,
                success=False,
                merged_text=f"Error: team {team_name!r} is disabled",
            )

        mode = team["orchestration_mode"]
        agent_names: list[str] = team.get("agents", [])
        if not agent_names:
            return TeamRunResult(
                team_name=team_name,
                orchestration_mode=mode,
                task_id=task_id,
                success=True,
                merged_text="No agents configured in team.",
            )

        await self._emit_progress(
            "started", task_id, team_name, progress=0.0,
            agents_total=len(agent_names), agents_completed=0,
        )

        # Build SubAgentConfig for each member
        configs = await self._resolve_agents(agent_names)
        if not configs:
            await self._emit_progress("failed", task_id, team_name, progress=0.0)
            return TeamRunResult(
                team_name=team_name,
                orchestration_mode=mode,
                task_id=task_id,
                success=False,
                merged_text="Error: no valid agents found for team members.",
            )

        # Drive orchestration
        if mode == "parallel":
            results = await self._run_parallel(
                configs, request, session_id, task_id, team_name,
            )
        elif mode == "sequential":
            results = await self._run_sequential(
                configs, request, session_id, task_id, team_name,
            )
        elif mode == "round-robin":
            results = await self._run_round_robin(
                configs, request, session_id, task_id, team_name,
            )
        else:
            results = await self._run_parallel(
                configs, request, session_id, task_id, team_name,
            )

        # Merge results
        merged_text = "\n\n---\n\n".join(
            f"## {r.agent_name}\n{r.text}" for r in results if r.success
        )
        conflicts = self._detect_conflicts(results)
        success = any(r.success for r in results)

        await self._emit_progress(
            "completed", task_id, team_name, progress=1.0,
            agents_total=len(agent_names), agents_completed=len(results),
            summary=merged_text[:200],
        )

        return TeamRunResult(
            team_name=team_name,
            orchestration_mode=mode,
            agents_run=results,
            merged_text=merged_text,
            conflicts=conflicts,
            task_id=task_id,
            success=success,
        )

    # -- orchestration modes ------------------------------------------------

    async def _run_parallel(
        self,
        configs: list[Any],
        request: str,
        session_id: str | None,
        task_id: str,
        team_name: str,
    ) -> list[AgentRunResult]:
        """Run all agents concurrently via ``asyncio.gather``."""
        coros = [
            self._run_single_agent(cfg, request, session_id, task_id, team_name, idx, len(configs))
            for idx, cfg in enumerate(configs)
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        out: list[AgentRunResult] = []
        for r in results:
            if isinstance(r, Exception):
                out.append(AgentRunResult(
                    agent_name="unknown", success=False, error=str(r),
                ))
            else:
                out.append(r)
        return out

    async def _run_sequential(
        self,
        configs: list[Any],
        request: str,
        session_id: str | None,
        task_id: str,
        team_name: str,
    ) -> list[AgentRunResult]:
        """Run agents one-by-one; each receives prior accumulated output."""
        results: list[AgentRunResult] = []
        accumulated = request
        for idx, cfg in enumerate(configs):
            result = await self._run_single_agent(
                cfg, accumulated, session_id, task_id, team_name, idx, len(configs),
            )
            results.append(result)
            if result.success and result.text:
                accumulated = f"{request}\n\n--- Previous agent ({result.agent_name}) output ---\n{result.text}"
        return results

    async def _run_round_robin(
        self,
        configs: list[Any],
        request: str,
        session_id: str | None,
        task_id: str,
        team_name: str,
    ) -> list[AgentRunResult]:
        """All agents receive the same request; return all results."""
        # Semantically identical to parallel for the stub path.
        # In a real system, round-robin might distribute sub-tasks.
        return await self._run_parallel(
            configs, request, session_id, task_id, team_name,
        )

    # -- single agent execution --------------------------------------------

    async def _run_single_agent(
        self,
        config: Any,
        request: str,
        session_id: str | None,
        task_id: str,
        team_name: str,
        index: int,
        total: int,
    ) -> AgentRunResult:
        """Build, invoke, and wrap the result for one agent."""
        from .subagent import SubAgentRuntime

        agent_name = config.name
        sid = session_id or f"teamrun_session_{uuid.uuid4().hex[:8]}"

        await self._emit_progress(
            "agent_started", task_id, team_name,
            agent_name=agent_name,
            progress=(index) / total,
            agents_total=total,
            agents_completed=index,
        )

        try:
            runtime = SubAgentRuntime(llm=self._llm)
            handle = runtime.build(config)
            envelope = await runtime.invoke(
                handle, session_id=sid, request=request,
            )
            await self._emit_progress(
                "agent_completed", task_id, team_name,
                agent_name=agent_name,
                progress=(index + 1) / total,
                agents_total=total,
                agents_completed=index + 1,
            )
            return AgentRunResult(
                agent_name=agent_name,
                success=True,
                text=envelope.get("text", ""),
                iterations=envelope.get("iterations", 0),
                tool_calls=envelope.get("tool_calls", []),
                stub=envelope.get("stub", True),
            )
        except Exception as exc:
            logger.exception("Agent %s failed in team %s", agent_name, team_name)
            return AgentRunResult(
                agent_name=agent_name,
                success=False,
                error=str(exc),
            )

    # -- conflict detection ------------------------------------------------

    @staticmethod
    def _detect_conflicts(results: list[AgentRunResult]) -> list[TeamConflict]:
        """Detect file-write conflicts across agent tool calls.

        A conflict is flagged when two or more agents have tool calls
        that target the same file path with ``write_file`` or ``edit_file``.
        """
        # Map file_path -> [agent_name, ...]
        writes_by_file: dict[str, list[str]] = {}
        for r in results:
            for tc in r.tool_calls:
                name = tc.get("name", "")
                if name not in ("write_file", "edit_file"):
                    continue
                args = tc.get("args", {})
                fpath = args.get("path") or args.get("file_path")
                if fpath:
                    writes_by_file.setdefault(fpath, []).append(r.agent_name)

        conflicts: list[TeamConflict] = []
        for fpath, agents in writes_by_file.items():
            if len(agents) > 1:
                conflicts.append(TeamConflict(
                    file_path=fpath,
                    agents=agents,
                    conflict_type="concurrent_write",
                ))
        return conflicts

    # -- event emission -----------------------------------------------------

    async def _emit_progress(
        self,
        status: str,
        task_id: str,
        team_name: str,
        *,
        agent_name: str | None = None,
        progress: float = 0.0,
        agents_total: int = 0,
        agents_completed: int = 0,
        summary: str | None = None,
    ) -> None:
        """Push a ``agent.team_progress`` event if an emitter is configured."""
        if self._emit_event is None:
            return
        try:
            payload = {
                "team_name": team_name,
                "task_id": task_id,
                "status": status,
                "progress": round(progress, 3),
                "agents_total": agents_total,
                "agents_completed": agents_completed,
            }
            if agent_name:
                payload["agent_name"] = agent_name
            if summary:
                payload["summary"] = summary
            await self._emit_event("agent.team_progress", payload)
        except Exception:  # pragma: no cover — emitter failure shouldn't kill the run
            logger.warning("Failed to emit team_progress event", exc_info=True)

    # -- agent resolution ---------------------------------------------------

    async def _resolve_agents(self, names: list[str]) -> list[Any]:
        """Load :class:`SubAgentConfig` objects for the given agent names."""
        if self._agent_dao is None:
            return []

        from .subagent import SubAgentConfig

        configs: list[SubAgentConfig] = []
        for name in names:
            row = await self._agent_dao.get(name)
            if row is None:
                logger.warning("Agent %r not found, skipping", name)
                continue
            configs.append(SubAgentConfig(
                name=row["name"],
                system_prompt=row.get("system_prompt", ""),
                tool_allowlist=row.get("tool_allowlist"),
                model=row.get("model"),
                id=row.get("id"),
                description=row.get("description", ""),
                enabled=row.get("enabled", True),
                icon=row.get("icon", ""),
                color=row.get("color", ""),
                category=row.get("category", ""),
                tags=row.get("tags"),
                team_id=row.get("team_id"),
                skills=row.get("skills"),
                max_iterations=row.get("max_iterations", 8),
                temperature=row.get("temperature"),
            ))
        return configs


__all__ = [
    "AgentRunResult",
    "TeamConflict",
    "TeamOrchestrator",
    "TeamRunResult",
]
