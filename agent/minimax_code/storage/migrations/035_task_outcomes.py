"""Task execution projections with durable links to run evidence."""
from __future__ import annotations

from typing import Any

VERSION = 35
DISABLE_FOREIGN_KEYS = True


def run(conn: Any) -> None:
    if conn.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError("Task schema rebuild must use the migration runner")
    objects = conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name='tasks' "
                           "AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
    conn.execute("""CREATE TABLE tasks_v35 (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN
            ('pending','running','completed','failed','cancelled','budget_paused','interrupted','requires_review')),
        progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
        created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT, error TEXT, result TEXT,
        run_id TEXT REFERENCES agent_runs(id) ON DELETE SET NULL
    )""")
    columns = "id,session_id,title,status,progress,created_at,started_at,completed_at,error,result"
    conn.execute(f"INSERT INTO tasks_v35({columns}) SELECT {columns} FROM tasks")
    conn.execute("DROP TABLE tasks")
    conn.execute("ALTER TABLE tasks_v35 RENAME TO tasks")
    for row in objects:
        conn.execute(row[0])
    conn.execute("CREATE INDEX idx_tasks_run ON tasks(run_id) WHERE run_id IS NOT NULL")
