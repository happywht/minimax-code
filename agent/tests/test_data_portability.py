"""Data-portability tests (M4 / R21+).

R21: ``data.export`` — single-JSON dump of every business table.

Coverage:

* :class:`TestDumpDatabase` — envelope shape, table coverage, system /
  virtual-table exclusion, row round-trip fidelity, empty database.
* :class:`TestDataExportIPC` — the ``data.export`` method end-to-end
  through the in-process IPC server, with the DB singleton injected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minimax_code.ipc.client import IPCClient
from minimax_code.ipc.handlers_data import EXPORT_FORMAT, dump_database
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_db(tmp_path: Path) -> AsyncDatabase:
    """A migrated async Database backed by a per-test temp file."""
    path = make_temp_database_path(tmp_path)
    db = AsyncDatabase(path)
    await db.connect()
    await db.migrate()
    yield db
    await db.close()


async def _seed(db: AsyncDatabase) -> None:
    """Insert a small, cross-referencing dataset (FK chain included)."""
    await db.execute(
        "INSERT INTO sessions (id, title, created_at, updated_at) "
        "VALUES ('s1', 'Alpha chat', '2026-08-01T10:00:00Z', "
        "'2026-08-01T10:05:00Z')"
    )
    await db.execute(
        "INSERT INTO sessions (id, title, created_at, updated_at, archived) "
        "VALUES ('s2', 'Beta chat', '2026-08-02T10:00:00Z', "
        "'2026-08-02T10:05:00Z', 1)"
    )
    await db.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at, "
        "tokens_in, tokens_out) VALUES "
        "('m1', 's1', 'user', 'hello there', '2026-08-01T10:00:01Z', 2, 0), "
        "('m2', 's1', 'assistant', 'hi!', '2026-08-01T10:00:05Z', 0, 3), "
        "('m3', 's2', 'user', 'bye', '2026-08-02T10:00:01Z', 1, 0)"
    )


# ---------------------------------------------------------------------------
# dump_database (pure function)
# ---------------------------------------------------------------------------


class TestDumpDatabase:
    async def test_envelope_metadata(self, async_db: AsyncDatabase) -> None:
        envelope = await dump_database(async_db)
        assert envelope["format"] == EXPORT_FORMAT
        assert envelope["schema_version"] > 0
        assert envelope["app_version"]
        assert envelope["exported_at"]
        assert set(envelope["counts"]) == set(envelope["tables"])

    async def test_covers_core_business_tables(
        self, async_db: AsyncDatabase
    ) -> None:
        """Table discovery is dynamic; pin the core set so none silently
        drops out of the export (e.g. via an over-eager exclusion)."""
        envelope = await dump_database(async_db)
        core = {
            "sessions", "messages", "projects", "memories",
            "permission_rules", "audit_log", "scheduled_jobs", "tasks",
            "agents", "mcp_servers", "mobile_devices", "model_prefs",
        }
        missing = core - set(envelope["tables"])
        assert not missing, f"core tables missing from export: {missing}"

    async def test_excludes_system_and_virtual_tables(
        self, async_db: AsyncDatabase
    ) -> None:
        envelope = await dump_database(async_db)
        names = set(envelope["tables"])
        assert "schema_migrations" not in names
        # Virtual tables (FTS / vec) are derived state, not exportable data.
        virtual = await async_db.fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND sql LIKE 'CREATE VIRTUAL TABLE%'"
        )
        for row in virtual:
            assert row["name"] not in names

    async def test_rows_round_trip(self, async_db: AsyncDatabase) -> None:
        await _seed(async_db)
        envelope = await dump_database(async_db)
        assert envelope["counts"]["sessions"] == 2
        assert envelope["counts"]["messages"] == 3
        by_id = {s["id"]: s for s in envelope["tables"]["sessions"]}
        assert by_id["s1"]["title"] == "Alpha chat"
        assert by_id["s2"]["archived"] == 1
        msg = {m["id"]: m for m in envelope["tables"]["messages"]}
        assert msg["m2"]["role"] == "assistant"
        assert msg["m2"]["tokens_out"] == 3
        assert msg["m3"]["session_id"] == "s2"

    async def test_empty_database_exports_empty_lists(
        self, async_db: AsyncDatabase
    ) -> None:
        envelope = await dump_database(async_db)
        # "Empty" = no user data. Migrations seed the default 'inbox'
        # project row, so only user-data tables are asserted empty.
        user_tables = ("sessions", "messages", "memories", "permission_rules",
                       "audit_log", "scheduled_jobs", "tasks")
        for table in user_tables:
            assert envelope["tables"][table] == [], table
            assert envelope["counts"][table] == 0, table


# ---------------------------------------------------------------------------
# data.export over IPC
# ---------------------------------------------------------------------------


class TestDataExportIPC:
    async def test_export_round_trip_via_rpc(
        self, async_db: AsyncDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _seed(async_db)
        import minimax_code.app as app_module

        monkeypatch.setattr(app_module, "_DB_SINGLETON", async_db)
        client = IPCClient()
        result = await client.request("data.export", {})
        assert result["format"] == EXPORT_FORMAT
        assert result["counts"]["messages"] == 3
        titles = {s["title"] for s in result["tables"]["sessions"]}
        assert titles == {"Alpha chat", "Beta chat"}
