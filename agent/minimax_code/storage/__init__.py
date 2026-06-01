"""SQLite storage layer for MiniMax Code.

This package owns the on-disk database the Python agent uses for
sessions, messages, tasks, skills, scheduled jobs, sub-agent
configuration, permission rules, and paired mobile devices.

Public surface
--------------
* :class:`Database`, :class:`AsyncDatabase` — connection wrappers.
* :func:`default_data_dir`, :func:`default_database_path` —
  OS-appropriate data directory resolution.
* :func:`make_temp_database_path` — fresh path under tempdir for tests.
* The :mod:`.dao` subpackage — one DAO class per entity.
* :mod:`.migrations` — the migration system (used internally by
  ``Database.migrate`` / ``AsyncDatabase.migrate``).
"""

from __future__ import annotations

from . import dao, migrations
from .db import (
    AsyncDatabase,
    Database,
    default_data_dir,
    default_database_path,
    ensure_data_dir,
    make_temp_database_path,
)

__all__ = [
    "AsyncDatabase",
    "Database",
    "dao",
    "default_data_dir",
    "default_database_path",
    "ensure_data_dir",
    "make_temp_database_path",
    "migrations",
]
