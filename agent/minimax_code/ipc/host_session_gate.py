"""Fail closed before public host execution for durably restricted sessions."""

from __future__ import annotations

import json

from .handler_utils import HandlerError

_HOST_EXECUTION_ERROR = -32000
_MESSAGE = "Host execution is unavailable for this session"


def _deny(reason: str) -> HandlerError:
    return HandlerError(_HOST_EXECUTION_ERROR, _MESSAGE, {"reason": reason})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate metadata key")
        result[key] = value
    return result


async def require_host_session(session_id: str | None) -> None:
    """Check every durable run in this execution session, including terminal runs.

    Source-session references do not grant or restrict authority. No caller
    flag, mutable Agent configuration, active-run filter, or ContextVar can
    replace this history check. Anonymous legacy terminals have no binding.
    """
    if session_id is None:
        return
    if not isinstance(session_id, str) or not session_id:
        raise _deny("session_execution_binding_invalid")
    try:
        from ..app import ensure_db

        db = await ensure_db()
        if db is None:
            raise RuntimeError("Session database is unavailable")
        rows = await db.fetchall(
            "SELECT id, session_id, metadata FROM agent_runs WHERE session_id=?", (session_id,)
        )
    except Exception as exc:
        raise _deny("session_execution_binding_unavailable") from exc

    try:
        from ..agent_execution import AgentExecutionSpec
        from ..process_binding import load_execution_binding

        for row in rows:
            metadata = json.loads(row["metadata"], object_pairs_hook=_unique_object)
            if not isinstance(metadata, dict):
                raise ValueError("Run metadata must be an object")
            authorities = {"agent_execution", "process_execution_owner"} & metadata.keys()
            if not authorities:
                continue
            if len(authorities) != 1:
                raise ValueError("Conflicting execution authorities")
            authority = next(iter(authorities))
            if not isinstance(metadata[authority], dict):
                raise ValueError("Execution authority must be an object")
            if authority == "agent_execution":
                snapshot = AgentExecutionSpec(metadata[authority]).data
                if snapshot["execution_session_id"] != row["session_id"]:
                    raise ValueError("Execution authority belongs to a different session")
            binding = load_execution_binding({"id": row["id"], "metadata": metadata})
            if binding is not None and binding.target["mode"] == "podman":
                raise _deny("restricted_execution_session")
    except HandlerError:
        raise
    except Exception as exc:
        raise _deny("session_execution_binding_invalid") from exc
