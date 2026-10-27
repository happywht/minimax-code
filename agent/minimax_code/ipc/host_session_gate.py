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


async def _session_history(session_id: str | None, *, run_id: str | None = None) -> list:
    """Parse raw durable authority, including terminal runs and duplicate keys."""
    if session_id is None and run_id is None:
        return []
    if session_id is not None and (not isinstance(session_id, str) or not session_id):
        raise _deny("session_execution_binding_invalid")
    try:
        from ..app import ensure_db

        db = await ensure_db()
        if db is None:
            raise RuntimeError("Session database is unavailable")
        if session_id is None:
            rows = await db.fetchall(
                "SELECT id, session_id, metadata FROM agent_runs WHERE session_id="
                "(SELECT session_id FROM agent_runs WHERE id=?)", (run_id,)
            )
        else:
            rows = await db.fetchall(
                "SELECT id, session_id, metadata FROM agent_runs WHERE session_id=?", (session_id,)
            )
    except Exception as exc:
        raise _deny("session_execution_binding_unavailable") from exc

    try:
        from ..agent_execution import AgentExecutionSpec
        from ..process_binding import load_execution_binding

        history = []
        for row in rows:
            metadata = json.loads(row["metadata"], object_pairs_hook=_unique_object)
            if not isinstance(metadata, dict):
                raise ValueError("Run metadata must be an object")
            authorities = {"agent_execution", "process_execution_owner"} & metadata.keys()
            if not authorities:
                history.append((row["id"], metadata, None))
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
            history.append((row["id"], metadata, binding))
        return history
    except HandlerError:
        raise
    except Exception as exc:
        raise _deny("session_execution_binding_invalid") from exc


async def require_host_session(session_id: str | None) -> None:
    """Check every durable run in this execution session, including terminal runs.

    Source-session references do not grant or restrict authority. No caller
    flag, mutable Agent configuration, active-run filter, or ContextVar can
    replace this history check. Anonymous legacy terminals have no binding.
    """
    for _, _, binding in await _session_history(session_id):
        if binding is not None and binding.target["mode"] == "podman":
            raise _deny("restricted_execution_session")


async def require_public_recovery(session_id: str | None, run_id: str | None) -> None:
    """Internal children need their own recovery protocol, never the host loop."""
    for saved_id, metadata, _ in await _session_history(session_id, run_id=run_id):
        if saved_id == run_id and "process_execution_owner" in metadata:
            raise _deny("internal_process_recovery_unsupported")


async def require_chat_session(session_id: str, *, internal_agent=None) -> None:
    """Authorize host chat or the actual server-owned public Agent profile.

    This is an internal Python API, not a caller-selectable RPC bypass. Public
    Agent recovery still goes through its original restore/validation path.
    Its frozen profile must agree with every saved authority in the session.
    """
    if internal_agent is None:
        await require_host_session(session_id)
        return
    from ..agent_execution import AgentExecutionSpec

    try:
        if not isinstance(internal_agent, AgentExecutionSpec):
            raise ValueError("Public Agent profile is required")
        expected = AgentExecutionSpec(internal_agent.snapshot()).data
        if expected["execution_session_id"] != session_id:
            raise ValueError("Public Agent profile belongs to a different session")
        # Preparation legitimately updates these fields after admission. All
        # other frozen fields, including target and source, must stay identical.
        preparation = {"digest", "prepared", "directory_identity", "copy_binding"}
        frozen = {key: value for key, value in expected.items() if key not in preparation}
        for _, metadata, _ in await _session_history(session_id):
            if "process_execution_owner" in metadata:
                raise _deny("internal_process_recovery_unsupported")
            saved = metadata.get("agent_execution")
            if saved is None:
                continue  # Ordinary terminal/runner tracking grants no new authority.
            if {
                key: value for key, value in saved.items() if key not in preparation
            } != frozen:
                raise ValueError("Public Agent profile differs from durable session authority")
            if saved["prepared"] == expected["prepared"] and {
                key: value for key, value in saved.items() if key != "digest"
            } != {key: value for key, value in expected.items() if key != "digest"}:
                raise ValueError("Public Agent preparation differs from durable session authority")
    except HandlerError:
        raise
    except Exception as exc:
        raise _deny("session_execution_binding_invalid") from exc
