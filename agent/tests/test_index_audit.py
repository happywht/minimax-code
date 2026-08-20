"""Index audit — hot DAO queries must never do a bare table SCAN.

Runs ``EXPLAIN QUERY PLAN`` over the hot-path SQL shapes the DAOs
actually issue (sidebar refresh, chat transcript load, task board,
run timeline, memory lookup) and asserts each resolves to an index
search rather than a full table scan. If someone adds a hot query
without an index — or drops an index a hot query leans on — this
file goes red and names the query.

Rules
-----
- ``SEARCH ... USING INDEX ...`` and ``SEARCH ... USING INTEGER ROWID``
  pass.
- ``SCAN ... USING (COVERING )?INDEX`` passes (index-ordered or
  index-only scan is fine — e.g. the sidebar's leading-wildcard
  title LIKE walks the updated_at index instead of sorting).
- ``USE TEMP B-TREE FOR ORDER BY`` passes (a sort, not a scan).
- A bare ``SCAN <table>`` fails — zero exemptions today.

The SQL here is transcribed from the DAO call sites (each entry
names its source). Transcribing — rather than intercepting DAO
calls at runtime — keeps the audit deterministic and independent
of handler wiring; if a DAO query changes shape, update the entry
in the same PR and the audit keeps tracking it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from minimax_code.storage.db import Database, make_temp_database_path

# Session/message ids the queries are planned against. EXPLAIN QUERY
# PLAN only inspects shapes — the values never match rows — so any
# non-empty literal works.
SID = "session-audit"
RID = "run-audit"

# (case id, sql, params). Each entry cites the DAO call site it audits.
HOT_QUERIES = [
    # Sidebar session list — every refresh (dao/sessions.py list).
    (
        "sessions: archived list, newest first",
        "SELECT * FROM sessions WHERE archived = ? ORDER BY updated_at DESC LIMIT 100",
        (0,),
    ),
    (
        "sessions: archived count (dao/sessions.py count)",
        "SELECT COUNT(*) FROM sessions WHERE archived = ?",
        (1,),
    ),
    # Project-scoped sidebar view (dao/sessions.py list with project_id).
    (
        "sessions: project + archived list",
        "SELECT * FROM sessions WHERE project_id = ? AND archived = ? "
        "ORDER BY updated_at DESC LIMIT 100",
        ("inbox", 0),
    ),
    # Sidebar title search (dao/sessions.py list with search). The
    # leading-wildcard LIKE cannot seek, but SQLite walks the
    # updated_at index in order and filters — no bare table scan,
    # and the LIMIT stays cheap. Audited like everything else.
    (
        "sessions: title search (LIKE)",
        "SELECT * FROM sessions WHERE title LIKE ? COLLATE NOCASE "
        "ORDER BY updated_at DESC LIMIT 100",
        ("%audit%",),
    ),
    # Chat transcript load — the single hottest read in the app
    # (dao/messages.py list_by_session).
    (
        "messages: transcript by session, oldest first",
        "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC LIMIT 500",
        (SID,),
    ),
    # Message count badge (dao/messages.py count).
    (
        "messages: count per session",
        "SELECT COUNT(*) FROM messages WHERE session_id = ?",
        (SID,),
    ),
    # Branch/fork navigation (idx_messages_parent).
    (
        "messages: children by parent",
        "SELECT * FROM messages WHERE parent_id = ?",
        ("msg-audit",),
    ),
    # Tool-result correlation (idx_messages_tool_call_id).
    (
        "messages: by tool_call_id",
        "SELECT * FROM messages WHERE tool_call_id = ?",
        ("call-audit",),
    ),
    # Cascading delete on session removal (dao/messages.py delete_by_session).
    (
        "messages: delete by session",
        "DELETE FROM messages WHERE session_id = ?",
        (SID,),
    ),
    # Task board (dao/tasks.py list by session / by status).
    (
        "tasks: by session, newest first",
        "SELECT * FROM tasks WHERE session_id = ? ORDER BY created_at DESC LIMIT 100",
        (SID,),
    ),
    (
        "tasks: by status",
        "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT 100",
        ("running",),
    ),
    # Run timeline (dao/runs.py list_by_session, agent_run_steps by run).
    (
        "agent_runs: by session",
        "SELECT * FROM agent_runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 50",
        (SID,),
    ),
    (
        "agent_run_steps: by run, ordinal order",
        "SELECT * FROM agent_run_steps WHERE run_id = ? ORDER BY ordinal ASC",
        (RID,),
    ),
    # Long-term memory lookup (dao/memories.py, idx_memories_session_id).
    (
        "memories: by session",
        "SELECT * FROM memories WHERE session_id = ? ORDER BY created_at DESC LIMIT 100",
        (SID,),
    ),
    # Audit trail drill-in (dao/audit.py, idx_audit_session).
    (
        "audit_log: by session, newest first",
        "SELECT * FROM audit_log WHERE session_id = ? ORDER BY created_at DESC LIMIT 100",
        (SID,),
    ),
]

@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    path = make_temp_database_path(tmp_path)
    database = Database(path)
    database.migrate()
    try:
        yield database
    finally:
        database.close()


def plan_details(database: Database, sql: str, params: tuple) -> list[str]:
    rows = database.fetchall(f"EXPLAIN QUERY PLAN {sql}", params)
    return [str(row["detail"]) for row in rows]


def bare_table_scans(details: list[str]) -> list[str]:
    """Filter plan lines down to the failing kind: a bare table scan.

    ``SCAN tbl USING COVERING INDEX ...`` (index-only) and anything
    with ``USING`` is fine; ``SCAN tbl`` alone means row-by-row over
    the table.
    """
    return [d for d in details if d.startswith("SCAN ") and " USING " not in d]


@pytest.mark.parametrize(
    "case_id, sql, params",
    HOT_QUERIES,
    ids=[case_id for case_id, _, _ in HOT_QUERIES],
)
def test_hot_query_is_indexed(db: Database, case_id: str, sql: str, params: tuple) -> None:
    details = plan_details(db, sql, params)
    assert details, f"empty query plan for: {sql}"
    scans = bare_table_scans(details)
    assert not scans, (
        f"{case_id}: bare table scan in query plan:\n  {sql}\n"
        f"plan:\n  " + "\n  ".join(details)
    )
