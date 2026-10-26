"""Durable ownership of individually supervised restricted processes."""
from __future__ import annotations

from . import run_script

VERSION = 39


def run(conn):
    run_script(conn, """
CREATE TABLE execution_installation (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 install_id TEXT NOT NULL UNIQUE, uid INTEGER NOT NULL,
 host_fingerprint TEXT NOT NULL, store_fingerprint TEXT NOT NULL,
 bootstrap_nonce TEXT NOT NULL, location_fingerprint TEXT NOT NULL,
 data_dir TEXT NOT NULL, data_dir_device TEXT NOT NULL, data_dir_inode TEXT NOT NULL,
 marker_hash TEXT NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('bootstrap_pending','active')),
 created_at TEXT NOT NULL, activated_at TEXT
);
CREATE TABLE process_executions (
 exec_id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
 family_id TEXT NOT NULL REFERENCES budget_families(id) ON DELETE RESTRICT,
 family_root_run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
 install_id TEXT NOT NULL REFERENCES execution_installation(install_id) ON DELETE RESTRICT,
 location_fingerprint TEXT NOT NULL,
 container_name TEXT NOT NULL UNIQUE, container_id TEXT UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('intent','created','starting','running','stopped',
  'removing','removed','cleanup_pending','needs_review')),
 revision INTEGER NOT NULL DEFAULT 0,
 target_json TEXT NOT NULL, target_hash TEXT NOT NULL,
 workspace TEXT NOT NULL, workspace_device TEXT NOT NULL, workspace_inode TEXT NOT NULL,
 argv_hash TEXT NOT NULL, container_cwd TEXT NOT NULL, deadline REAL NOT NULL,
 deadline_boot_id TEXT,
 deadline_boottime_ns INTEGER CHECK(deadline_boottime_ns>0),
 reserved_memory INTEGER NOT NULL CHECK(reserved_memory>0),
 reserved_cpus INTEGER NOT NULL CHECK(reserved_cpus>0),
 reserved_pids INTEGER NOT NULL CHECK(reserved_pids>0),
 resource_held INTEGER NOT NULL DEFAULT 1 CHECK(resource_held IN (0,1)),
 outcome TEXT, exit_code INTEGER, oom_killed INTEGER, error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_process_executions_active ON process_executions(install_id,resource_held);
CREATE INDEX idx_process_executions_family ON process_executions(family_id,resource_held);
CREATE TABLE process_execution_events (
 exec_id TEXT NOT NULL REFERENCES process_executions(exec_id) ON DELETE RESTRICT,
 ordinal INTEGER NOT NULL, from_state TEXT, to_state TEXT NOT NULL,
 evidence TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(exec_id,ordinal)
);
""")
