"""Read bounded pages from this run's captured tool output."""
from __future__ import annotations

from .base import Tool, ToolResult, register_tool


@register_tool
class ReadToolOutput(Tool):
    name = "read_tool_output"
    description = "Read a bounded page of captured tool output belonging to the current run. Captured command output may already be truncated."
    parameters = {"type": "object", "properties": {
        "output_ref": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 8000}},
        "required": ["output_ref"], "additionalProperties": False}

    async def run(self, output_ref: str, offset: int = 0, limit: int = 4000) -> ToolResult:
        from ...app import get_db
        from ...budget_ledger import current_handle
        from ...ipc.host_session_gate import require_host_session
        from ...storage.dao.runs import AgentRunsDAO
        from ...storage.dao.text_artifacts import ArtifactDAO
        from ...tool_outputs import read

        try:
            db, handle = get_db(), current_handle()
            if db is None or handle is None:
                raise ValueError("Durable run identity is unavailable")
            run = await AgentRunsDAO(db).get_run(handle.run_id)
            if not run:
                raise ValueError("Durable run is unavailable")
            await require_host_session(run["session_id"])
            service = await ArtifactDAO(db).service_id()
            return ToolResult.ok(await read(db, run_id=handle.run_id, session_id=run["session_id"],
                                            ref=output_ref, service_id=service, offset=offset, limit=limit))
        except Exception as exc:
            return ToolResult.fail(f"Captured output unavailable: {exc}")
