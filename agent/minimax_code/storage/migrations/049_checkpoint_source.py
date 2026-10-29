"""Record checkpoint working-directory provenance without guessing legacy paths."""

VERSION = 49


def run(conn):
    conn.execute("ALTER TABLE session_checkpoints ADD COLUMN source_root TEXT")
