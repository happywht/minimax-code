"""Sub-agent artifact protocol — on-disk handoff between agents.

A sub-agent run owns ``<workspace_root>/.minimax/artifacts/<run_id>/``.
``spawn_subagent`` anchors the run with ``BRIEF.md`` (the shared task
brief); the sub-agent's structured handoff lands there as
``COMPLETION.md`` / ``REPORT.json`` (v1.4.0 commit 5). The main agent
reads everything back through ``read_artifact`` — containment-checked
against that run's directory, so a hostile rel_path cannot escape.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult, register_tool

logger = logging.getLogger(__name__)

BRIEF_NAME = "BRIEF.md"


def artifact_dir(run_id: str) -> Path | None:
    """Resolve ``<root>/.minimax/artifacts/<run_id>`` (``None`` w/o root).

    The root comes from ``workspace_ctx`` — per-project by construction
    (v1.3.0), so artifacts never cross project boundaries. ``None`` is
    the "no workspace" signal callers treat as skip-artifacts.
    """
    from ...workspace_ctx import current_root

    root = current_root()
    if root is None:
        return None
    return (root / ".minimax" / "artifacts" / run_id).resolve()


def write_brief(
    run_id: str,
    *,
    agent_name: str,
    prompt: str,
    parent_session_id: str | None,
) -> Path | None:
    """Write the run's ``BRIEF.md``; fail-open (``None`` when skipped).

    The brief is the contract both agents can point at: task, agent,
    lineage, timestamp. A write failure (read-only root, disk full)
    must never break the spawn — the run just loses its on-disk anchor.
    """
    base = artifact_dir(run_id)
    if base is None:
        return None
    try:
        base.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        lineage = parent_session_id or "—"
        content = (
            f"# Sub-Agent Brief — {agent_name}\n\n"
            f"- **Run**: `{run_id}`\n"
            f"- **Agent**: `{agent_name}`\n"
            f"- **Parent session**: `{lineage}`\n\n"
            "## Task\n\n"
            f"{prompt}\n\n"
            f"_Written {stamp} by spawn_subagent._\n"
        )
        target = base / BRIEF_NAME
        target.write_text(content, encoding="utf-8")
        return target
    except Exception:  # pragma: no cover — fail-open by contract
        logger.debug("artifact brief write failed for %s", run_id, exc_info=True)
        return None


@register_tool
class ReadArtifactTool(Tool):
    name = "read_artifact"
    description = (
        "Read a file from a sub-agent run's artifact directory "
        "(.minimax/artifacts/<run_id>/). Defaults to BRIEF.md — the task "
        "brief written at spawn time. The sub-agent's final handoff lands "
        "there as COMPLETION.md (human) and REPORT.json (machine)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "Run id returned by spawn_subagent.",
            },
            "rel_path": {
                "type": "string",
                "description": "File name inside the run's artifact directory.",
                "default": BRIEF_NAME,
            },
        },
        "required": ["run_id"],
        "additionalProperties": False,
    }

    async def run(self, run_id: str, rel_path: str = BRIEF_NAME) -> ToolResult:
        if not run_id.strip():
            return ToolResult.fail("run_id is required")
        if not rel_path.strip():
            return ToolResult.fail("rel_path is required")
        rid = run_id.strip()
        base = artifact_dir(rid)
        if base is None:
            return ToolResult.fail(
                "no workspace root is active — artifact directory is unavailable"
            )

        # Containment: anchored at THIS run's directory, traversal and
        # out-of-bounds absolute paths rejected (same checks as the
        # file tools, narrowed to the artifact scope).
        from .file_ops import PathSecurityError, safe_resolve

        try:
            target = safe_resolve(rel_path, workspace=base)
        except PathSecurityError as exc:
            return ToolResult.fail(f"artifact path rejected: {exc}")

        if not target.is_file():
            return ToolResult.fail(f"artifact not found: {rel_path!r} for run {rid!r}")
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult.fail(f"artifact read failed: {exc}")
        return ToolResult.ok(
            {
                "run_id": rid,
                "rel_path": rel_path,
                "path": str(target),
                "content": content,
            }
        )


def artifact_info(run_id: str) -> dict[str, Any]:
    """Small descriptor for spawn envelopes (fail-open, nullable)."""
    base = artifact_dir(run_id)
    if base is None:
        return {"artifact_dir": None}
    return {"artifact_dir": str(base)}
