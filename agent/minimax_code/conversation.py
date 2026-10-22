"""Project persisted conversation evidence into a valid model history.

Recovery placeholders describe missing evidence; they never claim a tool ran or
write invented results back to storage. Original records remain available to review.
"""

from __future__ import annotations

import copy
from typing import Any

from .run_errors import RunPersistenceError


def project_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    pending: dict[str, None] = {}

    def finish_pending() -> None:
        for call_id in pending:
            history.append({
                "role": "tool", "tool_call_id": call_id,
                "content": "[Runtime recovery: no saved result exists for this tool call. "
                           "Its execution outcome is UNKNOWN. It may already have changed files "
                           "or external state. Inspect existing effects before deciding whether "
                           "to repeat it; this placeholder is not execution evidence.]",
            })
        pending.clear()

    for row in rows:
        role = row.get("role") or "user"
        message = {"role": role, "content": row.get("content") or ""}
        if role == "tool":
            call_id = row.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                raise RunPersistenceError("Conversation has an unmatched tool result; review its history before continuing")
            pending.pop(call_id)
            message["tool_call_id"] = call_id
        else:
            finish_pending()
            calls = row.get("tool_calls")
            if calls:
                if role != "assistant" or not isinstance(calls, list):
                    raise RunPersistenceError("Conversation has invalid tool-call evidence")
                for call in calls:
                    call_id = call.get("id") if isinstance(call, dict) else None
                    if not isinstance(call_id, str) or not call_id or call_id in pending:
                        raise RunPersistenceError("Conversation has invalid or duplicate tool-call IDs")
                    pending[call_id] = None
                message["tool_calls"] = copy.deepcopy(calls)
        history.append(message)
    finish_pending()
    return history
