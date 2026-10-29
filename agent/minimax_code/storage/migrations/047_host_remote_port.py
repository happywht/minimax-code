"""Persist the remote agent port for each SSH host."""

VERSION = 47


def run(conn):
    conn.execute("ALTER TABLE hosts ADD COLUMN remote_port INTEGER NOT NULL DEFAULT 8765 "
                 "CHECK(remote_port BETWEEN 1 AND 65535)")
