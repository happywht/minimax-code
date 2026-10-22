"""Non-storage unit tests use this reply-aware DAO double.

Durability, transactions and state transitions are tested against real SQLite in
test_run_admission/test_run_service, never inferred from this in-memory double.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock


def make_run_dao_stub():
    dao = MagicMock()
    rows, receipts = {}, {}

    async def admit(**kwargs):
        run_id = f"run_{uuid.uuid4().hex}"
        row = {"id": run_id, "session_id": kwargs["session_id"], "status": "queued",
               "assistant_message_id": f"msg_{uuid.uuid4().hex}", "metadata": kwargs["metadata"]}
        rows[run_id] = row
        return dict(row), True

    async def claim(run_id):
        rows[run_id]["status"] = "running"
        return dict(rows[run_id])

    async def update(run_id, **kwargs):
        rows[run_id].update(kwargs)
        if kwargs.get("result") is not None:
            receipts[run_id] = kwargs["result"]
        return dict(rows[run_id])

    async def get(run_id):
        return rows.get(run_id)

    async def receipt(run_id):
        return receipts.get(run_id)

    dao.admit_run = AsyncMock(side_effect=admit)
    dao.claim_run = AsyncMock(side_effect=claim)
    dao.get_run = AsyncMock(side_effect=get)
    dao.result_payload = AsyncMock(side_effect=receipt)
    dao.find_request = AsyncMock(return_value=None)
    dao.update_run_status = AsyncMock(side_effect=update)
    dao.terminate_run = AsyncMock(side_effect=update)
    dao.create_run = AsyncMock(return_value={"id": "run_legacy"})
    dao.create_step = AsyncMock(return_value={"id": "step_unit"})
    dao.complete_step = AsyncMock(return_value={"id": "step_unit"})
    return dao
