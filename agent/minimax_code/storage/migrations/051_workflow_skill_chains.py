"""Bind a manual skill chain key to an immutable workflow and execution scope."""

VERSION = 51


def run(conn):
    conn.execute("""CREATE TABLE workflow_skill_chains (
        workflow_id TEXT NOT NULL,
        trigger_key TEXT NOT NULL,
        session_id TEXT NOT NULL,
        service_id TEXT NOT NULL,
        project_id TEXT,
        steps_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (workflow_id, trigger_key)
    )""")
