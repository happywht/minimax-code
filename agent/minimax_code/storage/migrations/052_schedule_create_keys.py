"""Persist schedule creation identities alongside their original plan."""

VERSION = 52


def run(conn):
    conn.execute("""CREATE TABLE schedule_create_keys (
        request_key TEXT PRIMARY KEY,
        request_hash TEXT NOT NULL,
        job_id TEXT NOT NULL REFERENCES scheduled_jobs(id),
        created_at TEXT NOT NULL
    )""")
