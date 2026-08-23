"""Team orchestrator — parallel, sequential, round-robin, vote, and review.

The orchestrator takes a named team template (from the ``agent_teams`` table),
resolves its member agents, and drives them according to the team's
``orchestration_mode``:

* **parallel**  — ``asyncio.gather``; all agents receive the same request.
  Concurrency is bounded (see ``MINIMAX_CODE_TEAM_MAX_CONCURRENCY``).
* **sequential** — agents run one-by-one; each receives the original request
  plus the accumulated output of the previous agents.
* **round-robin** — currently identical to parallel: every agent gets the
  same request and every result is returned. (An earlier revision of this
  docstring promised "first successful result wins"; that behaviour never
  shipped — kept as an extension point.)
* **vote** — currently identical to parallel; merging/selection of the
  outputs is left to the caller (see ``merged_text`` / ``agents_run``).
* **review** — all agents run in parallel as writers, then a designated
  reviewer agent consolidates their outputs into a single response.

Every agent runs under a wall-clock timeout (``MINIMAX_CODE_SUBAGENT_TIMEOUT_S``,
default 600 s) — a hung agent (dead LLM connection, infinite tool loop) used
to hang the whole team run forever.

After all agents finish, the orchestrator runs a lightweight **conflict
detection** pass that checks whether multiple agents attempted to write
to the same file path (via ``write_file`` / ``edit_file`` tool calls).
Partial failure is surfaced up front in ``merged_text`` (a ``> ⚠`` advisory
line) rather than silently swallowed by ``success = any(...)``.

The final :class:`TeamRunResult` is persisted to the ``agent_runs`` table
(``mode="team"``) so callers can query it later via ``team.run.get``.

v0.11.0 — Agent Studio orchestration enhancements.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — type hints only
    from ..agent.llm import MiniMaxClient
    from ..storage.dao.agent_teams import AgentTeamDAO
    from ..storage.dao.agents import AgentDAO

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# v1.2.2 stability knobs (read per-run so tests / operators can tune live)
# ---------------------------------------------------------------------------


def _team_concurrency_limit() -> int:
    """Max agents of one team run executing at once.

    ``MINIMAX_CODE_TEAM_MAX_CONCURRENCY`` (default 4); <= 0 disables the
    cap. A large team used to fan every agent out at once, each with its
    own LLM loop and tool budget.
    """
    raw = os.environ.get("MINIMAX_CODE_TEAM_MAX_CONCURRENCY", "")
    try:
        return int(raw)
    except ValueError:
        return 4


def _subagent_wall_clock_s() -> float:
    """Per-agent wall-clock timeout in seconds.

    ``MINIMAX_CODE_SUBAGENT_TIMEOUT_S`` (default 600); <= 0 disables the
    timeout.
    """
    raw = os.environ.get("MINIMAX_CODE_SUBAGENT_TIMEOUT_S", "")
    try:
        return float(raw)
    except ValueError:
        return 600.0


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
        team_dao: AgentTeamDAO | None = None,
        agent_dao: AgentDAO | None = None,
        llm: MiniMaxClient | None = None,
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
        """Execute a team run and persist the result.

        Resolves the team template, builds handles for each member
        agent, drives them according to the orchestration mode, and
        returns a merged :class:`TeamRunResult`. The result is stored
        in ``agent_runs`` (``mode="team"``) under ``task_id``.
        """
        task_id = f"teamrun_{uuid.uuid4().hex[:10]}"
        run_session_id = session_id or task_id

        # Persist run start. Fail-open: persistence problems should not
        # kill the team run.
        runs_dao: Any | None = None
        if self._team_dao is not None:
            try:
                runs_dao = self._make_runs_dao()
                await self._ensure_session(run_session_id)
                await runs_dao.create_run(
                    id=task_id,
                    session_id=run_session_id,
                    mode="team",
                    status="running",
                    title=f"Team run: {team_name}",
                    metadata={"team_result": None, "parent_session_id": parent_session_id},
                )
            except Exception:
                logger.warning("Failed to persist team run start", exc_info=True)
                runs_dao = None

        result = await self._run_body(
            team_name,
            request,
            session_id=run_session_id,
            task_id=task_id,
            parent_session_id=parent_session_id,
        )

        if runs_dao is not None:
            try:
                await runs_dao.update_run_status(
                    task_id,
                    status="completed" if result.success else "failed",
                    metadata={
                        "team_result": self._result_to_dict(result),
                        "parent_session_id": parent_session_id,
                    },
                )
            except Exception:
                logger.warning("Failed to persist team run completion", exc_info=True)

        return result

    async def _run_body(
        self,
        team_name: str,
        request: str,
        *,
        session_id: str,
        task_id: str,
        parent_session_id: str | None,
    ) -> TeamRunResult:
        """Core orchestration logic (persistence-agnostic)."""
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
        if mode == "review":
            results, merged_text, success = await self._run_review(
                configs, request, session_id, task_id, team_name, team
            )
            conflicts = self._detect_conflicts(results)
        else:
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
            elif mode == "vote":
                results = await self._run_vote(
                    configs, request, session_id, task_id, team_name,
                )
            else:
                results = await self._run_parallel(
                    configs, request, session_id, task_id, team_name,
                )

            # Merge results
            merged_text, success = self._merge_texts(results)
            conflicts = self._detect_conflicts(results)

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
        session_id: str,
        task_id: str,
        team_name: str,
    ) -> list[AgentRunResult]:
        """Run all agents concurrently via ``asyncio.gather``.

        v1.2.2: concurrency is bounded by a semaphore
        (``MINIMAX_CODE_TEAM_MAX_CONCURRENCY``, default 4).
        """
        limit = _team_concurrency_limit()
        sem = asyncio.Semaphore(limit) if limit > 0 else None
        total = len(configs)

        async def _bounded(idx: int, cfg: Any) -> AgentRunResult:
            if sem is None:
                return await self._run_single_agent(
                    cfg, request, session_id, task_id, team_name, idx, total,
                )
            async with sem:
                return await self._run_single_agent(
                    cfg, request, session_id, task_id, team_name, idx, total,
                )

        results = await asyncio.gather(
            *(_bounded(idx, cfg) for idx, cfg in enumerate(configs)),
            return_exceptions=True,
        )
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
        session_id: str,
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
        session_id: str,
        task_id: str,
        team_name: str,
    ) -> list[AgentRunResult]:
        """All agents receive the same request; return all results."""
        # Semantically identical to parallel for the stub path.
        # In a real system, round-robin might distribute sub-tasks.
        return await self._run_parallel(
            configs, request, session_id, task_id, team_name,
        )

    async def _run_vote(
        self,
        configs: list[Any],
        request: str,
        session_id: str,
        task_id: str,
        team_name: str,
    ) -> list[AgentRunResult]:
        """Run all agents in parallel and let the caller merge outputs."""
        return await self._run_parallel(
            configs, request, session_id, task_id, team_name,
        )

    async def _run_review(
        self,
        configs: list[Any],
        request: str,
        session_id: str,
        task_id: str,
        team_name: str,
        team: dict[str, Any],
    ) -> tuple[list[AgentRunResult], str, bool]:
        """Run writers in parallel, then a reviewer consolidates.

        The reviewer is either the agent named in
        ``team.orchestration_config.review_agent`` or the last agent in
        ``configs``. If no reviewer can be determined, fall back to
        parallel behaviour.

        Returns ``(results, merged_text, success)``.
        """
        # Run all agents as writers first.
        writer_results = await self._run_parallel(
            configs, request, session_id, task_id, team_name,
        )

        # Determine reviewer config.
        review_agent_name: str | None = None
        orchestration_config = team.get("orchestration_config") or {}
        if isinstance(orchestration_config, dict):
            review_agent_name = orchestration_config.get("review_agent")

        reviewer_config: Any | None = None
        if review_agent_name:
            for cfg in configs:
                if cfg.name == review_agent_name:
                    reviewer_config = cfg
                    break
        if reviewer_config is None and configs:
            reviewer_config = configs[-1]

        if reviewer_config is None:
            # No reviewer available — parallel fallback.
            merged_text, success = self._merge_texts(writer_results)
            return writer_results, merged_text, success

        # Build a consolidation prompt from writer outputs.
        writer_outputs = "\n\n".join(
            f"## {r.agent_name}\n{r.text}" for r in writer_results if r.success
        )
        review_prompt = (
            f"Original request:\n{request}\n\n"
            f"Here are outputs from multiple writers:\n\n{writer_outputs}\n\n"
            "Please review and consolidate the above outputs into a single coherent response."
        )

        review_result = await self._run_single_agent(
            reviewer_config,
            review_prompt,
            session_id,
            task_id,
            team_name,
            index=len(configs),
            total=len(configs) + 1,
        )

        results = [*writer_results, review_result]
        if review_result.success and review_result.text:
            merged_text = review_result.text
            success = True
            failed = [r for r in writer_results if not r.success]
            if failed:
                reasons = ", ".join(
                    f"{r.agent_name} ({r.error or 'failed'})" for r in failed
                )
                merged_text = (
                    f"> ⚠ {len(failed)} of {len(writer_results)} writer agents "
                    f"failed: {reasons}\n\n{merged_text}"
                )
        else:
            # Reviewer failed: fall back to merged writer output.
            merged_text, success = self._merge_texts(writer_results)
        return results, merged_text, success

    # -- single agent execution --------------------------------------------

    async def _run_single_agent(
        self,
        config: Any,
        request: str,
        session_id: str,
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
            timeout_s = _subagent_wall_clock_s()
            if timeout_s > 0:
                envelope = await asyncio.wait_for(
                    runtime.invoke(handle, session_id=sid, request=request),
                    timeout=timeout_s,
                )
            else:
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
        except TimeoutError:
            # v1.2.2: a hung agent fails itself instead of hanging the
            # whole team run forever.
            logger.warning(
                "Agent %s timed out (%ss wall clock) in team %s",
                agent_name, timeout_s, team_name,
            )
            return AgentRunResult(
                agent_name=agent_name,
                success=False,
                error=f"timed out after {timeout_s:g}s wall clock",
            )
        except Exception as exc:
            logger.exception("Agent %s failed in team %s", agent_name, team_name)
            return AgentRunResult(
                agent_name=agent_name,
                success=False,
                error=str(exc),
            )

    # -- result merging -----------------------------------------------------

    @staticmethod
    def _merge_texts(results: list[AgentRunResult]) -> tuple[str, bool]:
        """Join successful outputs; surface partial failure up front.

        ``success = any(...)`` means one healthy agent marks the run
        successful even when its peers failed — the advisory line keeps
        that partial failure visible instead of silently dropped.
        """
        merged = "\n\n---\n\n".join(
            f"## {r.agent_name}\n{r.text}" for r in results if r.success
        )
        failed = [r for r in results if not r.success]
        if failed:
            reasons = ", ".join(
                f"{r.agent_name} ({r.error or 'failed'})" for r in failed
            )
            merged = (
                f"> ⚠ {len(failed)} of {len(results)} agents failed: "
                f"{reasons}\n\n{merged}"
            )
        return merged, any(r.success for r in results)

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

    # -- persistence helpers ------------------------------------------------

    def _make_runs_dao(self) -> Any:
        """Build an :class:`AgentRunsDAO` from the team DAO's database."""
        from ..storage.dao.runs import AgentRunsDAO

        return AgentRunsDAO(self._team_dao._db)

    async def _ensure_session(self, session_id: str) -> None:
        """Create a placeholder session row if ``session_id`` does not exist."""
        from ..storage.dao.sessions import SessionsDAO

        sessions_dao = SessionsDAO(self._team_dao._db)
        existing = await sessions_dao.get(session_id)
        if existing is not None:
            return
        await sessions_dao.create(id=session_id, title=f"Team run session {session_id}")

    @staticmethod
    def _result_to_dict(result: TeamRunResult) -> dict[str, Any]:
        """Serialisable representation stored in ``agent_runs.metadata``."""
        return {
            "team_name": result.team_name,
            "orchestration_mode": result.orchestration_mode,
            "merged_text": result.merged_text,
            "success": result.success,
            "agents_run": [
                {
                    "agent_name": r.agent_name,
                    "success": r.success,
                    "text": r.text,
                    "error": r.error,
                    "iterations": r.iterations,
                    "stub": r.stub,
                }
                for r in result.agents_run
            ],
            "conflicts": [
                {
                    "file_path": c.file_path,
                    "agents": c.agents,
                    "conflict_type": c.conflict_type,
                }
                for c in result.conflicts
            ],
        }

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
                max_iterations=row.get("max_iterations", 50),
                temperature=row.get("temperature"),
            ))
        return configs


__all__ = [
    "AgentRunResult",
    "TeamConflict",
    "TeamOrchestrator",
    "TeamRunResult",
]
