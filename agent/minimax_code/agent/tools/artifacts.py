"""Sub-agent artifact protocol — on-disk handoff between agents.

A sub-agent run owns ``<workspace_root>/.minimax/artifacts/<run_id>/``.
``spawn_subagent`` anchors the run with ``BRIEF.md`` (the shared task
brief); the sub-agent's structured handoff lands there as
``COMPLETION.md`` / ``REPORT.json`` (v1.4.0 commit 5). The main agent
reads everything back through ``read_artifact`` — containment-checked
against that run's directory, so a hostile rel_path cannot escape.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult, register_tool

logger = logging.getLogger(__name__)

BRIEF_NAME = "BRIEF.md"
COMPLETION_NAME = "COMPLETION.md"
REPORT_NAME = "REPORT.json"
# v1.5.2 — append-only interim progress ledger (one JSON object per line).
PROGRESS_NAME = "PROGRESS.jsonl"

_VALID_REPORT_STATUS = ("completed", "partial", "blocked")


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


def completion_reported(run_id: str) -> bool:
    """True when the sub-agent published its ``COMPLETION.md``.

    Soft-enforcement probe: ``_drive_run`` checks this at wind-down to
    flag runs that skipped the report protocol (``reported=False`` in
    the envelope — a warning, never an error).
    """
    base = artifact_dir(run_id)
    if base is None:
        return False
    try:
        return (base / COMPLETION_NAME).is_file()
    except OSError:  # pragma: no cover — defensive
        return False


# ---------------------------------------------------------------------------
# Interim progress ledger (v1.5.2)
# ---------------------------------------------------------------------------


def read_progress(run_id: str, *, limit: int | None = 5) -> list[dict[str, Any]]:
    """Parse the run's ``PROGRESS.jsonl`` (tail ``limit``; all when ``None``).

    Fail-open by contract — a missing ledger, an unreadable file, or a
    corrupt line yields fewer entries, never an exception. Corrupt lines
    are skipped individually so one bad write cannot hide the rest.
    """
    base = artifact_dir(run_id)
    if base is None:
        return []
    try:
        lines = (base / PROGRESS_NAME).read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    if limit is not None:
        entries = entries[-limit:]
    return entries


def progress_summary(run_id: str, *, recent: int = 3) -> dict[str, Any] | None:
    """Compact envelope projection of the ledger (``None`` when empty).

    Feeds ``check_subagent`` / ``wait_subagent`` / the final envelope so
    the coordinator can follow a long run without blocking on it — the
    v1.5.2 answer to the "sub-agent invisible until done" field report.
    """
    entries = read_progress(run_id, limit=None)
    if not entries:
        return None
    latest = entries[-1]
    return {
        "total": len(entries),
        "recent": [
            {
                "note": entry.get("note"),
                **({"percent": entry["percent"]} if entry.get("percent") is not None else {}),
            }
            for entry in entries[-recent:]
        ],
        "latest_percent": latest.get("percent"),
    }


def _completion_markdown(report: dict[str, Any]) -> str:
    """Render the machine report as the human-readable ``COMPLETION.md``."""
    lines = [
        f"# Sub-Agent Completion — {report['agent']}",
        "",
        f"- **Run**: `{report['run_id']}`",
        f"- **Status**: {report['status']}",
        f"- **Written**: {report['written_at']}",
        "",
        "## Summary",
        "",
        report["summary"],
        "",
    ]
    for heading, key in (("Files", "files"), ("Gaps", "gaps"), ("Next steps", "next_steps")):
        items = report.get(key) or []
        lines.append(f"## {heading}")
        lines.append("")
        if items:
            lines.extend(f"- {item}" for item in items)
        else:
            lines.append("- _none_")
        lines.append("")
    return "\n".join(lines)


class ReportCompletionTool(Tool):
    """Per-run tool injected into the sub-agent's cloned registry.

    Intentionally **not** ``@register_tool``-decorated: it must never
    appear in the main agent's tool surface — only the spawned sub-agent
    sees it, bound to its own ``run_id``. Writes the dual handoff
    (``COMPLETION.md`` for humans, ``REPORT.json`` for machines).
    """

    name = "report_completion"
    description = (
        "Publish this run's final structured handoff. Call exactly once "
        "before finishing: writes COMPLETION.md (human-readable) and "
        "REPORT.json (machine-readable) into the run's artifact directory "
        "so the main agent can read them back with read_artifact."
    )
    parameters = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": list(_VALID_REPORT_STATUS),
                "description": (
                    "'completed' (goal met), 'partial' (some findings, "
                    "goal not fully met), or 'blocked' (could not proceed)."
                ),
            },
            "summary": {
                "type": "string",
                "description": "1-3 sentences describing the outcome.",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Files read, written, or changed during the run.",
            },
            "gaps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "What remains unknown, unverified, or untested.",
            },
            "next_steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Suggested follow-ups for the main agent.",
            },
        },
        "required": ["status", "summary"],
        "additionalProperties": False,
    }

    def __init__(self, run_id: str, *, agent_name: str = "") -> None:
        super().__init__()
        self._run_id = run_id
        self._agent_name = agent_name

    async def run(
        self,
        status: str,
        summary: str,
        files: list[str] | None = None,
        gaps: list[str] | None = None,
        next_steps: list[str] | None = None,
    ) -> ToolResult:
        if status not in _VALID_REPORT_STATUS:
            return ToolResult.fail(
                f"status must be one of {list(_VALID_REPORT_STATUS)}, got {status!r}"
            )
        if not str(summary).strip():
            return ToolResult.fail("summary is required")

        report: dict[str, Any] = {
            "run_id": self._run_id,
            "agent": self._agent_name,
            "status": status,
            "summary": summary,
            "files": [str(f) for f in files or []],
            "gaps": [str(g) for g in gaps or []],
            "next_steps": [str(s) for s in next_steps or []],
            "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        base = artifact_dir(self._run_id)
        if base is None:
            # No workspace root — nothing to anchor. Soft protocol: the
            # sub-agent did its part; report success with skipped=True.
            return ToolResult.ok({**report, "skipped": True, "reason": "no workspace root"})
        try:
            base.mkdir(parents=True, exist_ok=True)
            (base / REPORT_NAME).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (base / COMPLETION_NAME).write_text(
                _completion_markdown(report), encoding="utf-8"
            )
        except OSError as exc:
            return ToolResult.fail(f"report write failed: {exc}")
        return ToolResult.ok({**report, "skipped": False, "artifact_dir": str(base)})


class ReportProgressTool(Tool):
    """Per-run interim progress tool injected into the cloned registry (v1.5.2).

    Same containment as :class:`ReportCompletionTool` — deliberately not
    ``@register_tool``-decorated, so it never leaks onto the main agent's
    surface. Appends one JSON line to the run's ``PROGRESS.jsonl`` and
    best-effort pushes a live ``agent.subagent_progress`` event through
    the routed emit so the UI panel updates without waiting for the run
    to finish; the coordinator polls the same ledger through
    ``check_subagent``'s ``progress`` summary.
    """

    name = "report_progress"
    description = (
        "Report interim progress on a long-running task: appends a "
        "one-line note (and optional 0-100 percent) to this run's "
        "progress ledger and notifies the coordinator / UI immediately. "
        "Call it at meaningful milestones — phase finished, halfway "
        "point, blocked investigation — not after every single step."
    )
    parameters = {
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": "One-line status note: what was just done / what is next.",
            },
            "percent": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Optional overall completion estimate, 0-100.",
            },
        },
        "required": ["note"],
        "additionalProperties": False,
    }

    def __init__(
        self, run_id: str, *, agent_name: str = "", agent_id: str | None = None
    ) -> None:
        super().__init__()
        self._run_id = run_id
        self._agent_name = agent_name
        self._agent_id = agent_id

    async def _push_event(self, entry: dict[str, Any]) -> None:
        """Live UI push through the routed emit; fail-open.

        Reuses the ``thinking`` status on purpose: the frontend's
        STATUS_TONE map is a closed union, and an unknown status would
        render nothing. ``summary`` carries the note text; ``progress``
        carries the percent when the sub-agent supplied one.
        """
        try:
            from ...orchestrator.subagent import (
                current_parent_session,
                resolve_subagent_emit,
            )
            from .subagents import _emit_safe, _subagent_event

            parent = current_parent_session()
            emit = await resolve_subagent_emit(parent)
            if emit is None:
                return
            pct = entry.get("percent")
            await _emit_safe(
                emit,
                _subagent_event(
                    run_id=self._run_id,
                    agent_id=self._agent_id,
                    parent_session_id=parent,
                    status="thinking",
                    progress=(float(pct) / 100.0) if pct is not None else 0.4,
                    summary=str(entry.get("note", ""))[:80],
                ),
            )
        except Exception:  # noqa: BLE001 — the ledger write already succeeded
            logger.debug("report_progress event push failed", exc_info=True)

    async def run(self, note: str, percent: int | None = None) -> ToolResult:
        if not isinstance(note, str) or not note.strip():
            return ToolResult.fail("'note' must be a non-empty string")
        pct: int | None = None
        if percent is not None:
            try:
                pct = max(0, min(100, int(percent)))
            except (TypeError, ValueError):
                return ToolResult.fail("'percent' must be an integer between 0 and 100")

        entry: dict[str, Any] = {
            "run_id": self._run_id,
            "note": note.strip(),
            **({"percent": pct} if pct is not None else {}),
            "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        base = artifact_dir(self._run_id)
        if base is None:
            # No workspace root — nothing to anchor. The event push
            # below may still reach the UI, so run it before returning.
            await self._push_event(entry)
            return ToolResult.ok({**entry, "skipped": True, "reason": "no workspace root"})
        try:
            base.mkdir(parents=True, exist_ok=True)
            with (base / PROGRESS_NAME).open("a", encoding="utf-8", newline="") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            return ToolResult.fail(f"progress write failed: {exc}")
        await self._push_event(entry)
        return ToolResult.ok(
            {
                **entry,
                "skipped": False,
                "ledger": str(base / PROGRESS_NAME),
                "entries_total": len(read_progress(self._run_id, limit=None)),
            }
        )


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
