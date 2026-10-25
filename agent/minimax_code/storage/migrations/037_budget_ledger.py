"""Durable token liabilities; historical usage is only a lower bound."""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from . import run_script

VERSION = 37


def run(conn):
    run_script(conn, """
CREATE TABLE budget_families (
 id TEXT PRIMARY KEY, root_run_id TEXT NOT NULL, cap_tokens INTEGER,
 legacy_lower_bound INTEGER NOT NULL DEFAULT 0, legacy_liability INTEGER,
 legacy_incomplete INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, authorizations JSON NOT NULL DEFAULT '[]'
);
CREATE TABLE budget_attempts (
 id TEXT PRIMARY KEY, family_id TEXT NOT NULL REFERENCES budget_families(id),
 run_id TEXT NOT NULL, provider TEXT, model TEXT, purpose TEXT NOT NULL,
 pricing JSON NOT NULL, reservation_day TEXT NOT NULL, estimated_tokens INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','sent','settled','unknown','released','reconciled')),
 known_tokens INTEGER, liability_tokens INTEGER, usage JSON, provenance TEXT,
 created_at TEXT NOT NULL, sent_at TEXT, settled_at TEXT, reason TEXT
);
CREATE INDEX idx_budget_family ON budget_attempts(family_id);
CREATE INDEX idx_budget_day ON budget_attempts(reservation_day);
CREATE TABLE budget_days (
 day TEXT PRIMARY KEY, known_lower_bound INTEGER NOT NULL DEFAULT 0,
 historical_liability INTEGER, incomplete INTEGER NOT NULL DEFAULT 0,
 reconciled_at TEXT, reason TEXT
);
CREATE TABLE budget_coverage (id INTEGER PRIMARY KEY CHECK(id=1), started_at TEXT NOT NULL);
"""
    )
    ts = datetime.now(UTC).isoformat()
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    conn.execute("INSERT INTO budget_coverage VALUES(1,?)", (ts,))
    # Shanghai is UTC+8 without DST. Historical started calls can be paid even
    # when their usage columns are empty; a zero sum is not proof of no spend.
    row = conn.execute(
        "SELECT COUNT(*),COALESCE(SUM(tokens_in+tokens_out),0) FROM messages "
        "WHERE role='assistant' AND date(created_at,'+8 hours')=?", (day,)
    ).fetchone()
    active = conn.execute(
        "SELECT COUNT(*) FROM agent_runs WHERE started_at IS NOT NULL "
        "AND date(started_at,'+8 hours')=?", (day,)
    ).fetchone()[0]
    incomplete = int(bool(active or row[0]))
    conn.execute("INSERT INTO budget_days(day,known_lower_bound,incomplete) VALUES(?,?,?)",
                 (day, int(row[1]), incomplete))
