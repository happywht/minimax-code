"""Pin conversation engines; ambiguous legacy histories require an explicit handoff."""
from __future__ import annotations

import json
from typing import Any

VERSION = 34

def run(conn: Any) -> None:
    columns = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    if {"engine", "engine_thread_id", "engine_state"} <= columns:
        return
    for name, ddl in (("engine", "TEXT NOT NULL DEFAULT 'native' CHECK(engine IN ('native','codex'))"),
                      ("engine_thread_id", "TEXT"), ("engine_state", "TEXT")):
        if name not in columns:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {ddl}")
    for session in conn.execute("SELECT id FROM sessions").fetchall():
        runs = conn.execute("SELECT metadata FROM agent_runs WHERE session_id=?", (session[0],)).fetchall()
        records = []
        for row in runs:
            try:
                records.append(json.loads(row[0] or "{}"))
            except (TypeError, ValueError):
                records.append({})
        codex = [r for r in records if r.get("engine") == "codex"]
        if not codex:
            continue
        threads = {r.get("codex_thread_id") for r in codex}
        workspaces = {r.get("workspace_snapshot") for r in codex}
        safe = (len(codex) == len(records) and len(threads) == 1 and None not in threads
                and len(workspaces) == 1 and None not in workspaces
                and all(r.get("codex_protocol") == "codex-cli-0.163.0" for r in codex))
        state = {"version": 1, "handoff_required": not safe, "usage_certainty": "unknown"}
        if safe:
            state.update(workspace=next(iter(workspaces)), protocol="codex-cli-0.163.0")
        conn.execute("UPDATE sessions SET engine='codex', engine_thread_id=?, engine_state=? WHERE id=?",
                     (next(iter(threads)) if safe else None, json.dumps(state), session[0]))
