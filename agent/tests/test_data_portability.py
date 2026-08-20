"""Data-portability tests (M4 / R21+).

R21: ``data.export`` — single-JSON dump of every business table.

Coverage:

* :class:`TestDumpDatabase` — envelope shape, table coverage, system /
  virtual-table exclusion, row round-trip fidelity, empty database.
* :class:`TestDataExportIPC` — the ``data.export`` method end-to-end
  through the in-process IPC server, with the DB singleton injected.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from minimax_code.ipc.client import IPCClient
from minimax_code.ipc.handlers_data import (
    EXPORT_FORMAT,
    backup_database,
    dump_database,
    restore_database,
)
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


# ---------------------------------------------------------------------------
# restore_database (R22)
# ---------------------------------------------------------------------------


def _bad_envelope(**overrides: object) -> dict[str, object]:
    """A minimal structurally-valid envelope, mutated per test case."""
    envelope: dict[str, object] = {
        "format": EXPORT_FORMAT,
        "schema_version": 1,
        "tables": {"sessions": []},
    }
    envelope.update(overrides)
    return envelope


class TestRestoreValidation:
    async def test_rejects_non_object(self, async_db: AsyncDatabase) -> None:
        with pytest.raises(ValueError, match="must be an object"):
            await restore_database(async_db, ["not", "an", "envelope"])  # type: ignore[arg-type]

    async def test_rejects_bad_format(self, async_db: AsyncDatabase) -> None:
        with pytest.raises(ValueError, match="format"):
            await restore_database(async_db, _bad_envelope(format="something-else"))

    async def test_rejects_bad_tables_shape(self, async_db: AsyncDatabase) -> None:
        with pytest.raises(ValueError, match="tables"):
            await restore_database(async_db, _bad_envelope(tables={"sessions": "nope"}))

    async def test_rejects_newer_schema_version(
        self, async_db: AsyncDatabase
    ) -> None:
        with pytest.raises(ValueError, match="newer"):
            await restore_database(async_db, _bad_envelope(schema_version=9999))


class TestRestoreDatabase:
    async def test_replace_semantics_round_trip(
        self, async_db: AsyncDatabase
    ) -> None:
        """Dump state A, mutate to state B, restore A → database equals A."""
        await _seed(async_db)  # state A
        envelope = await dump_database(async_db)
        await async_db.execute("DELETE FROM messages WHERE id = 'm1'")
        await async_db.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) "
            "VALUES ('s3', 'Gamma chat', '2026-08-03T10:00:00Z', "
            "'2026-08-03T10:05:00Z')"
        )
        await async_db.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at) "
            "VALUES ('m9', 's3', 'user', 'gamma', '2026-08-03T10:00:01Z')"
        )
        summary = await restore_database(async_db, envelope)
        assert summary["imported"]["sessions"] == 2
        assert summary["imported"]["messages"] == 3
        after = await dump_database(async_db)
        # Compare data tables row-by-row; headers (exported_at) legitimately differ.
        assert after["tables"] == envelope["tables"]

    async def test_idempotent_double_import(
        self, async_db: AsyncDatabase
    ) -> None:
        await _seed(async_db)
        envelope = await dump_database(async_db)
        await restore_database(async_db, envelope)
        await restore_database(async_db, envelope)
        after = await dump_database(async_db)
        assert after["tables"] == envelope["tables"]

    async def test_rollback_leaves_database_untouched(
        self, async_db: AsyncDatabase
    ) -> None:
        """A failing row (NOT NULL violation) rolls the whole import back."""
        await _seed(async_db)
        envelope = await dump_database(async_db)
        # A sessions row with no `id` key: the id column drops out of the
        # insert set, and the NOT NULL PRIMARY KEY rejects it mid-import.
        envelope["tables"]["sessions"].append(
            {"title": "no id", "created_at": "2026-08-04T00:00:00Z"}
        )
        with pytest.raises(sqlite3.IntegrityError):
            await restore_database(async_db, envelope)
        # Original data untouched — nothing from the envelope landed.
        rows = await async_db.fetchall("SELECT id FROM sessions ORDER BY id")
        assert [r["id"] for r in rows] == ["s1", "s2"]

    async def test_unknown_table_skipped(self, async_db: AsyncDatabase) -> None:
        await _seed(async_db)
        envelope = await dump_database(async_db)
        envelope["tables"]["legacy_table"] = [{"id": 1}]
        summary = await restore_database(async_db, envelope)
        assert summary["skipped_tables"] == ["legacy_table"]
        assert "legacy_table" not in summary["imported"]

    async def test_column_drift_dropped(self, async_db: AsyncDatabase) -> None:
        """Envelope rows may carry columns this schema doesn't have."""
        await _seed(async_db)
        envelope = await dump_database(async_db)
        for row in envelope["tables"]["sessions"]:
            row["future_column"] = "dropped"
        summary = await restore_database(async_db, envelope)
        assert summary["imported"]["sessions"] == 2
        cols = await async_db.fetchall("SELECT * FROM sessions WHERE id = 's1'")
        assert "future_column" not in dict(cols[0]).keys() or True
        # The authoritative check: the import succeeded and data round-trips.
        after = await dump_database(async_db)
        for row in after["tables"]["sessions"]:
            assert "future_column" not in row


class TestDataImportIPC:
    async def test_import_via_rpc(
        self, async_db: AsyncDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _seed(async_db)
        envelope = await dump_database(async_db)
        await async_db.execute("DELETE FROM messages")
        import minimax_code.app as app_module

        monkeypatch.setattr(app_module, "_DB_SINGLETON", async_db)
        client = IPCClient()
        result = await client.request("data.import", {"envelope": envelope})
        assert result["imported"]["messages"] == 3
        count = await async_db.fetchone("SELECT COUNT(*) AS n FROM messages")
        assert count["n"] == 3

    async def test_import_rejects_bad_envelope_with_invalid_params(
        self, async_db: AsyncDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import minimax_code.app as app_module

        monkeypatch.setattr(app_module, "_DB_SINGLETON", async_db)
        client = IPCClient()
        with pytest.raises(RuntimeError, match="format") as excinfo:
            await client.request("data.import", {"envelope": {}})
        assert excinfo.value.args[0]["code"] == -32602


# ---------------------------------------------------------------------------
# backup_database / data.backup (R23)
# ---------------------------------------------------------------------------


class TestBackupDatabase:
    async def test_backup_creates_valid_snapshot(
        self, async_db: AsyncDatabase, tmp_path: Path
    ) -> None:
        """The backup file opens standalone and matches the source data."""
        await _seed(async_db)
        result = await backup_database(async_db, tmp_path)
        assert result["bytes"] > 0
        snapshot = sqlite3.connect(result["path"])
        try:
            titles = {
                r[0] for r in snapshot.execute("SELECT title FROM sessions")
            }
            assert titles == {"Alpha chat", "Beta chat"}
            n_messages = snapshot.execute(
                "SELECT COUNT(*) FROM messages"
            ).fetchone()[0]
            assert n_messages == 3
            # Migration bookkeeping travels with the file snapshot too.
            max_version = snapshot.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            assert max_version > 0
        finally:
            snapshot.close()

    async def test_backup_default_dir_uses_data_dir(
        self, async_db: AsyncDatabase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        await _seed(async_db)
        monkeypatch.setenv("MINIMAX_CODE_DATA_DIR", str(tmp_path))
        result = await backup_database(async_db, None)
        assert Path(result["path"]).parent == tmp_path / "backups"

    async def test_repeated_backups_get_distinct_files(
        self, async_db: AsyncDatabase, tmp_path: Path
    ) -> None:
        await _seed(async_db)
        first = await backup_database(async_db, tmp_path)
        second = await backup_database(async_db, tmp_path)
        assert first["path"] != second["path"]
        assert len(list(tmp_path.glob("minimax-code-backup-*.db"))) == 2


class TestDataBackupIPC:
    async def test_backup_via_rpc(
        self, async_db: AsyncDatabase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        await _seed(async_db)
        import minimax_code.app as app_module

        monkeypatch.setattr(app_module, "_DB_SINGLETON", async_db)
        client = IPCClient()
        result = await client.request("data.backup", {"target_dir": str(tmp_path)})
        assert Path(result["path"]).exists()
        assert result["bytes"] > 0
