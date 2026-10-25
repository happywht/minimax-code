"""Durable Codex conversation state. One writer holds the RunService session lease."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..storage.dao._base import dumps_json, loads_json, now_iso
from ..storage.dao.runs import _append_event, _fetchone, _hydrate_run
from .codex import SUPPORTED_PROTOCOL_PIN


class CodexConversation:
    def __init__(self, db: Any, job: Any) -> None:
        self.db, self.job = db, job
        self.state: dict[str, Any] = {}
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.text = ""
        self.usage: dict[str, int] | None = None
        self.certainty = "unknown"
        self.recovery_interval = False
        self.bound = False

    async def load(self) -> None:
        row = await self.db.fetchone("SELECT * FROM sessions WHERE id=?", (self.job.session_id,))
        if not row or row["engine"] != "codex":
            raise ValueError("Session engine binding changed")
        self.state = loads_json(row["engine_state"]) or {"version": 1}
        self.thread_id = row["engine_thread_id"]
        if self.state.get("handoff_required"):
            raise ValueError("Ambiguous legacy Codex history; create a new session with a handoff")
        workspace = self.state.get("workspace")
        if workspace and Path(workspace).resolve() != Path(self.job.workspace).resolve():
            raise ValueError("Codex workspace binding changed; restore it or create a new session")
        if self.thread_id and self.state.get("protocol") != SUPPORTED_PROTOCOL_PIN:
            raise ValueError("Codex protocol binding is missing or incompatible")
        if (self.state.get("last_turn_id") or self.state.get("protocol")) and not self.thread_id:
            raise ValueError("Codex thread binding is missing; create a new session with a handoff")
        self.recovery_interval = bool(self.state.get("active_turn_id") or self.state.get("uncertain_interval"))

    async def bind(self, response: dict[str, Any]) -> None:
        thread = response.get("thread") or {}
        tid = thread.get("id")
        cwd = response.get("cwd") or thread.get("cwd")
        if not isinstance(tid, str) or not tid or (self.thread_id and tid != self.thread_id):
            raise ValueError("Codex returned a different or missing thread ID; refusing a fresh thread")
        if not cwd or Path(cwd).resolve() != Path(self.job.workspace).resolve():
            raise ValueError("Codex returned a different or missing workspace")
        thread_status = thread.get("status") or {}
        active = thread_status.get("type") == "active" if isinstance(thread_status, dict) else thread_status == "active"
        if active or any(t.get("status") in {"inProgress", "pending"} for t in thread.get("turns", [])):
            raise ValueError("Codex thread still has an active external turn; inspect it before recovery")
        for key in ("model", "modelProvider", "reasoningEffort"):
            if key in self.state and response.get(key) != self.state[key]:
                raise ValueError(f"Codex {key} changed from its pinned setting")
        new = self.thread_id is None
        self.thread_id = tid
        self.state.update(version=1, workspace=self.job.workspace, protocol=SUPPORTED_PROTOCOL_PIN)
        for key in ("model", "modelProvider", "reasoningEffort"):
            if key in response:
                self.state[key] = response[key]
        if new:
            self.state["usage_watermark"] = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}
        await self._save("running")  # Binding must commit before turn/start can have effects.
        self.bound = True

    async def start_turn(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self.state["known_turn_ids"] = [*self.state.get("known_turn_ids", []), turn_id][-100:]
        self.state.update(active_turn_id=turn_id, last_turn_id=turn_id)
        await self._save("running")

    async def delta(self, text: str) -> None:
        self.text += text
        await self._save("running", message=True)

    async def record_usage(self, total: dict[str, Any]) -> bool:
        """Return false for a reset/uncertain watermark. Never reset accounting to zero."""
        keys = ("inputTokens", "outputTokens", "totalTokens")
        if not isinstance(total, dict) or not all(type(total.get(k)) is int and total[k] >= 0 for k in keys):
            raise ValueError("Invalid Codex token usage notification")
        previous = self.state.get("usage_watermark")
        if (not isinstance(previous, dict) or any(total[k] < previous.get(k, 0) for k in keys)
                or self.state.get("usage_certainty") == "uncertain"):
            self.certainty = "uncertain"
            self.state["usage_certainty"] = "uncertain"
            await self._save("running", message=True)
            return False
        increment = {k: total[k] - previous[k] for k in keys}
        self.usage = {k: (self.usage or {}).get(k, 0) + increment[k] for k in keys}
        self.state["usage_watermark"] = {k: total[k] for k in keys}
        self.certainty = "recovery_interval" if self.recovery_interval else "observed"
        self.state["usage_certainty"] = self.certainty
        await self._save("running", message=True)
        return True

    def receipt(self, status: str) -> dict[str, Any]:
        return {"session_id": self.job.session_id, "run_id": self.job.run_id,
                "message_id": self.job.message_id, "engine": "codex", "text": self.text,
                "codex_thread_id": self.thread_id, "external_turn_id": self.turn_id,
                "tokens_in": self.usage["inputTokens"] if self.usage else None,
                "tokens_out": self.usage["outputTokens"] if self.usage else None,
                "tokens_total": self.usage["totalTokens"] if self.usage else None,
                "usage_certainty": self.certainty, "iterations": None, "stub": False,
                "cancelled": status == "cancelled", "budget_paused": status == "budget_paused",
                "run_status": status, "truncated": False, "verification": None}

    async def finish(self, status: str, *, error: str | None = None,
                     receipt: dict[str, Any] | None = None) -> dict[str, Any]:
        # Every interrupted external turn has uncertain side effects, including budget stops.
        self.state["uncertain_interval"] = ((status != "completed" and bool(self.turn_id))
                                            or self.usage is None or self.certainty == "uncertain")
        self.state["active_turn_id"] = None
        result = {**self.receipt(status), **(receipt or {})}
        from ..verification import verification_evidence
        result["verification"] = verification_evidence(status=status, engine="codex")
        if error:
            result["error"] = {"message": error}
        await self._save(status, message=bool(self.turn_id or self.text or self.usage), result=result, error=error)
        self.job.result = result
        return result

    async def _save(self, status: str, *, message: bool = False,
                    result: dict[str, Any] | None = None, error: str | None = None) -> None:
        terminal = status in {"completed", "failed", "cancelled", "interrupted", "budget_paused"}
        async with self.db.transaction() as conn:
            session = await _fetchone(conn, "SELECT engine,engine_thread_id FROM sessions WHERE id=?",
                                      (self.job.session_id,))
            if (not session or session["engine"] != "codex"
                    or session["engine_thread_id"] not in {None, self.thread_id}):
                raise ValueError("Codex thread binding changed while persisting")
            await conn.execute("UPDATE sessions SET engine_thread_id=?, engine_state=?,updated_at=? WHERE id=?",
                (self.thread_id, dumps_json(self.state), now_iso(), self.job.session_id))
            run = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id=?", (self.job.run_id,)))
            md = {**(run.get("metadata") or {}), "engine": "codex", "codex_thread_id": self.thread_id,
                  "external_turn_id": self.turn_id, "codex_protocol": SUPPORTED_PROTOCOL_PIN,
                  "workspace_snapshot": self.job.workspace, "codex_settings": {
                      k: self.state[k] for k in ("model", "modelProvider", "reasoningEffort") if k in self.state},
                  "usage_certainty": self.certainty}
            if result is not None:
                md["verification"] = result["verification"]
            if message:
                message_md = {"run_id": self.job.run_id, "engine": "codex",
                              "external_turn_id": self.turn_id, "status": status,
                              "partial": status != "completed", "usage_certainty": self.certainty,
                              "tokens_in": self.usage["inputTokens"] if self.usage else None,
                              "tokens_out": self.usage["outputTokens"] if self.usage else None}
                if result is not None:
                    message_md["verification"] = result["verification"]
                await conn.execute(
                    "INSERT INTO messages(id,session_id,role,content,created_at,tokens_in,tokens_out,metadata) "
                    "VALUES(?,?,'assistant',?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET content=excluded.content, "
                    "tokens_in=excluded.tokens_in,tokens_out=excluded.tokens_out,metadata=excluded.metadata",
                    (self.job.message_id, self.job.session_id, self.text, now_iso(),
                     self.usage["inputTokens"] if self.usage else 0,
                     self.usage["outputTokens"] if self.usage else 0, dumps_json(message_md)))
            await conn.execute(
                "UPDATE agent_runs SET status=?,metadata=?,result=COALESCE(?,result),error=?, "
                "completed_at=CASE WHEN ? THEN COALESCE(completed_at,?) ELSE completed_at END WHERE id=?",
                (status, dumps_json(md), dumps_json(result) if result is not None else None,
                 error, terminal, now_iso(), self.job.run_id))
            run.update(status=status, metadata=md)
            await _append_event(conn, self.job.run_id, "status", {"run": run})
