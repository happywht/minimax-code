"""Durable inbound webhook claims; pending records require reconciliation."""
VERSION = 48


def run(conn):
    conn.execute("""CREATE TABLE webhook_deliveries (
        webhook_id TEXT NOT NULL,
        body_sha256 TEXT NOT NULL,
        delivery_id TEXT,
        state TEXT NOT NULL CHECK(state IN ('pending', 'completed', 'review')),
        error_id TEXT,
        stage TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (webhook_id, body_sha256),
        UNIQUE (webhook_id, delivery_id)
    )""")
