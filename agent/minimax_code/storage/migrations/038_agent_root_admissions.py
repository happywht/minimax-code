"""Public agent roots bind source intent to one independently leased run."""
from __future__ import annotations

from . import run_script

VERSION = 38


def run(conn):
    run_script(conn, """
CREATE TABLE agent_root_admissions (
 source_session_id TEXT NOT NULL REFERENCES sessions(id),
 request_key TEXT NOT NULL,
 request_hash TEXT NOT NULL,
 execution_session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id),
 run_id TEXT NOT NULL UNIQUE REFERENCES agent_runs(id),
 created_at TEXT NOT NULL,
 PRIMARY KEY(source_session_id, request_key)
);
""")
