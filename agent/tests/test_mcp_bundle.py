"""Schema045 rebuild durability and bounded data-only bundle5 interoperability."""
from __future__ import annotations

import importlib
import json
import sqlite3
import zipfile

import pytest
from test_attachment_bundle import (
    legacy_v2_writer,
    legacy_v3_writer,
    legacy_v4_pdf_writer,
    legacy_writer,
)

from minimax_code import bundle
from minimax_code.storage import db as db_module
from minimax_code.storage.db import AsyncDatabase, Database

migration = importlib.import_module("minimax_code.storage.migrations.045_mcp_streamable_http")
STAMP = "2026-10-28T10:00:00+00:00"
COLUMNS = (
    "id,name,transport,command,url,env,enabled,created_at,updated_at,bearer_token,headers,"
    "oauth_client_id,oauth_client_secret,oauth_scopes,oauth_callback_port,tool_states"
)


def seed(db):
    for number, transport in enumerate(("stdio", "sse")):
        values = (
            f"mcp_{number}", f"原服务器 {number}", transport,
            '["python", "-m", "server; unchanged"]', "https://example.invalid/mcp?x=1",
            '{"EXAMPLE": "原值"}', number, STAMP, "older-opaque-stamp",
            "fake-local-token", '{"X-Token": "exact  spacing"}', "fake-client",
            "fake-secret", '["tools", "read"]', 8768, '{"tool.one": false, "two": true}',
        )
        db.execute(f"INSERT INTO mcp_servers({COLUMNS}) VALUES({','.join('?' for _ in values)})",
                   values)
    # Default stdio config includes every nullable auth/tool value as SQL NULL.
    db.execute("INSERT INTO mcp_servers(id,name,created_at,updated_at) "
               "VALUES('mcp_default','default',?,?)", (STAMP, STAMP))
    db.execute("CREATE TABLE mcp_test_log (server_id TEXT)")
    db.execute("CREATE INDEX mcp_test_name ON mcp_servers(name COLLATE NOCASE)")
    db.execute("CREATE TRIGGER mcp_test_update AFTER UPDATE ON mcp_servers BEGIN "
               "INSERT INTO mcp_test_log VALUES(NEW.id); END")
    db.execute("CREATE TABLE mcp_test_references (server_id TEXT REFERENCES mcp_servers(id) "
               "ON DELETE CASCADE)")
    db.execute("INSERT INTO mcp_test_references VALUES('mcp_0')")


def state(db):
    return {
        "rows": [tuple(row) for row in db.fetchall("SELECT rowid,* FROM mcp_servers ORDER BY rowid")],
        "columns": [tuple(row) for row in db.fetchall("PRAGMA table_info(mcp_servers)")],
        "objects": [tuple(row) for row in db.fetchall(
            "SELECT type,name,sql FROM sqlite_master WHERE tbl_name='mcp_servers' "
            "AND type IN ('index','trigger') ORDER BY name")],
        "references": [tuple(row) for row in db.fetchall("SELECT * FROM mcp_test_references")],
        "log": [tuple(row) for row in db.fetchall("SELECT * FROM mcp_test_log")],
    }


def historical_db(path, version, monkeypatch):
    discover = db_module.discover_migrations
    db = Database(path)
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "discover_migrations", lambda applied: [
            (number, run) for number, run in discover(applied) if number <= version])
        assert db.migrate()[-1] == version
    return db


@pytest.fixture
def old(tmp_path, monkeypatch):
    db = historical_db(tmp_path / "source" / "data.db", 44, monkeypatch)
    seed(db)
    try:
        yield db
    finally:
        db.close()


def test_schema045_preserves_all_fields_indexes_constraints_and_foreign_keys(old):
    before = state(old)
    old.execute("PRAGMA foreign_keys=ON")
    assert old.migrate() == [45]
    assert state(old) == before
    assert old.fetchone("PRAGMA foreign_keys")[0] == 1
    assert not old.fetchall("PRAGMA foreign_key_check")
    assert old.migrate() == []
    old.execute("INSERT INTO mcp_servers(id,name,transport,created_at,updated_at) "
                "VALUES('new','new','streamable_http',?,?)", (STAMP, STAMP))
    old.execute("UPDATE mcp_servers SET enabled=0 WHERE id='new'")
    assert old.fetchone("SELECT server_id FROM mcp_test_log")[0] == "new"
    # The previous CHECK/NOT NULL/UNIQUE constraints still reject invalid writes.
    for field, value in (("transport", "http"), ("enabled", 2), ("name", None),
                         ("transport", None), ("created_at", None), ("name", "原服务器 0")):
        with pytest.raises(sqlite3.IntegrityError):
            old.execute(f"UPDATE mcp_servers SET {field}=? WHERE id='new'", (value,))
    assert old.fetchone("SELECT transport FROM mcp_servers WHERE id='new'")[0] == "streamable_http"


def test_schema045_refuses_unrecognized_columns_without_losing_values(old):
    old.execute("ALTER TABLE mcp_servers ADD COLUMN external_value TEXT")
    old.execute("UPDATE mcp_servers SET external_value='must survive'")
    before = state(old)
    with pytest.raises(RuntimeError, match="refusing a lossy schema rebuild"):
        old.migrate()
    assert state(old) == before
    assert 45 not in old.applied_versions()
    assert old.fetchone("PRAGMA foreign_keys")[0] == 1


@pytest.mark.parametrize("change", ["check", "default", "not-null", "unique", "collation",
                                  "foreign-key", "without-rowid", "strict"])
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_schema045_refuses_changed_table_contract_before_ddl(old, change, asynchronous):
    sql = old.fetchone("SELECT sql FROM sqlite_master WHERE name='mcp_servers'")[0]
    objects = [row[0] for row in old.fetchall(
        "SELECT sql FROM sqlite_master WHERE tbl_name='mcp_servers' "
        "AND type IN ('index','trigger') AND sql IS NOT NULL")]
    values = [tuple(row) for row in old.fetchall("SELECT * FROM mcp_servers")]
    original_columns = [row[1] for row in old.fetchall("PRAGMA table_xinfo(mcp_servers)")]
    if change == "check":
        changed = sql.replace("IN ('stdio', 'sse')", "IN ('stdio', 'sse', 'http')")
    elif change == "default":
        changed = sql.replace("DEFAULT 'stdio'", "DEFAULT 'sse'")
    elif change == "not-null":
        changed = sql.replace("name        TEXT NOT NULL UNIQUE", "name        TEXT UNIQUE")
    elif change == "unique":
        changed = sql.replace("name        TEXT NOT NULL UNIQUE", "name        TEXT NOT NULL")
    elif change == "collation":
        changed = sql.replace("name        TEXT", "name        TEXT COLLATE NOCASE")
    elif change == "foreign-key":
        old.execute("CREATE TABLE mcp_custom_ports(port INTEGER PRIMARY KEY)")
        old.execute("INSERT INTO mcp_custom_ports VALUES(8768)")
        changed = sql.replace("oauth_callback_port INTEGER",
                              "oauth_callback_port INTEGER REFERENCES mcp_custom_ports(port)")
    else:
        changed = sql + (" WITHOUT ROWID" if change == "without-rowid" else " STRICT")
    assert changed != sql
    old.execute("PRAGMA foreign_keys=OFF")
    with old.transaction() as conn:
        conn.execute("DROP TABLE mcp_servers")
        conn.execute(changed)
        conn.executemany(f"INSERT INTO mcp_servers VALUES({','.join('?' for _ in original_columns)})",
                         values)
        for statement in objects:
            conn.execute(statement)
    old.execute("PRAGMA foreign_keys=ON")
    assert [row[1] for row in old.fetchall("PRAGMA table_xinfo(mcp_servers)")] == original_columns
    assert not old.fetchall("PRAGMA foreign_key_check")

    def snapshot():
        return (
            [tuple(row) for row in old.fetchall("SELECT * FROM mcp_servers ORDER BY id")],
            [tuple(row) for row in old.fetchall("SELECT * FROM sqlite_master ORDER BY type,name")],
            [tuple(row) for row in old.fetchall("PRAGMA table_xinfo(mcp_servers)")],
            [tuple(row) for row in old.fetchall("SELECT * FROM mcp_test_references")],
        )

    before = snapshot()
    # SQL trace proves the guard rejects before CREATE/COPY/DROP, independently
    # of the migration runner's rollback restoring an already rewritten table.
    trace = []
    if asynchronous:
        db = AsyncDatabase(old.path)
        await db.connect()
        try:
            assert db._conn is not None
            await db._conn.set_trace_callback(trace.append)
            with pytest.raises(RuntimeError, match="Unsupported MCP server table definition"):
                await db.migrate()
            assert (await db.fetchone("PRAGMA foreign_keys"))[0] == 1
        finally:
            await db.close()
    else:
        old.raw_connection.set_trace_callback(trace.append)
        with pytest.raises(RuntimeError, match="Unsupported MCP server table definition"):
            old.migrate()
        old.raw_connection.set_trace_callback(None)
    assert not any("mcp_servers_v45" in statement for statement in trace)
    assert snapshot() == before
    assert 45 not in old.applied_versions()
    assert old.fetchone("PRAGMA foreign_keys")[0] == 1


@pytest.mark.parametrize("point", ["DROP TABLE", "ALTER TABLE", "CREATE INDEX idx_mcp"])
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_schema045_failure_rolls_back_rebuild_and_version(old, monkeypatch, point, asynchronous):
    before, run = state(old), migration.run
    sql = old.fetchone("SELECT sql FROM sqlite_master WHERE name='mcp_servers'")[0]

    class Fault:
        def __getattr__(self, name):
            return getattr(old_connection, name)

        def execute(self, statement, *args):
            result = old_connection.execute(statement, *args)
            if statement.startswith(point):
                raise RuntimeError("injected rebuild failure")
            return result

    def fail(conn):
        nonlocal old_connection
        old_connection = conn
        run(Fault())

    old_connection = None
    monkeypatch.setattr(migration, "run", fail)
    if asynchronous:
        db = AsyncDatabase(old.path)
        await db.connect()
        try:
            with pytest.raises(RuntimeError, match="injected rebuild failure"):
                await db.migrate()
            assert (await db.fetchone("PRAGMA foreign_keys"))[0] == 1
        finally:
            await db.close()
    else:
        with pytest.raises(RuntimeError, match="injected rebuild failure"):
            old.migrate()
        assert old.fetchone("PRAGMA foreign_keys")[0] == 1
    assert state(old) == before
    assert old.fetchone("SELECT sql FROM sqlite_master WHERE name='mcp_servers'")[0] == sql
    assert 45 not in old.applied_versions()
    assert old.fetchone("SELECT name FROM sqlite_master WHERE name='mcp_servers_v45'") is None
    monkeypatch.setattr(migration, "run", run)
    assert old.migrate() == [45]


async def test_schema045_async_upgrade_preserves_populated_schema044(old):
    before = state(old)
    db = AsyncDatabase(old.path)
    await db.connect()
    try:
        assert await db.migrate() == [45]
        assert (await db.fetchone("PRAGMA foreign_keys"))[0] == 1
        await db.execute("UPDATE mcp_servers SET transport='streamable_http' WHERE id='mcp_1'")
        await db.execute("UPDATE mcp_servers SET transport='sse' WHERE id='mcp_1'")
        # Updating exercises the preserved trigger; the migration itself never fires it.
        before["log"] = [("mcp_1",), ("mcp_1",)]
        assert state(old) == before
    finally:
        await db.close()


@pytest.mark.parametrize("version,writer", [(41, legacy_writer), (42, legacy_v2_writer),
                                           (43, legacy_v3_writer), (44, legacy_v4_pdf_writer)])
def test_frozen_writers_interoperate_at_original_schema_only(tmp_path, monkeypatch, version, writer):
    source = tmp_path / "source"
    db = historical_db(source / "data.db", version, monkeypatch)
    seed(db)
    before = state(db)
    db.close()
    frozen = writer()
    archive = tmp_path / "old.zip"
    assert frozen.create_bundle(source, archive)["schema_version"] == version
    assert bundle.verify_bundle(archive)["schema_version"] == version
    restored = tmp_path / "restored"
    bundle.restore_bundle(archive, restored)
    with zipfile.ZipFile(archive) as packed:
        assert (restored / "data.db").read_bytes() == packed.read("data.db")
    with Database(restored / "data.db") as clone:
        assert state(clone) == before
        assert max(clone.applied_versions()) == version
    # Current reader's exact restore is still readable/writable by the same old writer.
    reexport = tmp_path / "same-version.zip"
    assert frozen.create_bundle(restored, reexport)["schema_version"] == version
    assert frozen.verify_bundle(reexport)["schema_version"] == version
    with pytest.raises(bundle.BundleError, match="Create requires known Schema045"):
        bundle.create_bundle(restored, tmp_path / "not-current.zip")


def test_bundle5_exact_restore_no_migration_and_frozen4_rejects_future(old, tmp_path, monkeypatch):
    old.migrate()
    old.execute("UPDATE mcp_servers SET transport='streamable_http' WHERE id='mcp_1'")
    before = state(old)
    source = old.path.parent
    old.close()
    archive = tmp_path / "current.zip"
    assert bundle.SUPPORTED_SCHEMAS == {1: 41, 2: 42, 3: 43, 4: 44, 5: 45}
    assert bundle.create_bundle(source, archive)["format_version"] == 5

    def never(*args, **kwargs):
        pytest.fail("Data-only verify/restore must not run a migration")

    monkeypatch.setattr(Database, "migrate", never)
    monkeypatch.setattr(AsyncDatabase, "migrate", never)
    assert bundle.verify_bundle(archive)["schema_version"] == 45
    target = tmp_path / "restored"
    assert bundle.restore_bundle(archive, target)["schema_version"] == 45
    with zipfile.ZipFile(archive) as packed:
        assert (target / "data.db").read_bytes() == packed.read("data.db")
    with Database(target / "data.db") as clone:
        assert state(clone) == before
        assert max(clone.applied_versions()) == 45
    frozen = legacy_v4_pdf_writer()
    with pytest.raises(ValueError):
        frozen.verify_bundle(archive)
    with pytest.raises(ValueError):
        frozen.restore_bundle(archive, tmp_path / "old-rejected")
    with pytest.raises(ValueError):
        frozen.create_bundle(source, tmp_path / "old-export-rejected.zip")
    assert not (tmp_path / "old-rejected").exists()


@pytest.mark.parametrize("format_version,schema_version", [(6, 45), (5, 46), (4, 45), (5, 44)])
def test_bundle5_future_and_mismatched_versions_fail_closed(old, tmp_path, format_version, schema_version):
    old.migrate()
    source = old.path.parent
    old.close()
    archive = tmp_path / "good.zip"
    bundle.create_bundle(source, archive)
    with zipfile.ZipFile(archive) as packed:
        members = [(item.filename, packed.read(item)) for item in packed.infolist()]
    manifest = json.loads(members[0][1])
    manifest.update(format_version=format_version, schema_version=schema_version)
    members[0] = ("manifest.json", json.dumps(manifest).encode())
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w", compression=zipfile.ZIP_STORED) as packed:
        for name, data in members:
            packed.writestr(name, data)
    with pytest.raises(bundle.BundleError):
        bundle.verify_bundle(bad)
    with pytest.raises(bundle.BundleError):
        bundle.restore_bundle(bad, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_bundle5_rejects_future_database_schema(old, tmp_path):
    old.migrate()
    old.execute("INSERT INTO schema_migrations VALUES(46,?)", (STAMP,))
    source = old.path.parent
    old.close()
    with pytest.raises(bundle.BundleError, match="Unsupported SQLite schema version"):
        bundle.create_bundle(source, tmp_path / "future.zip")
    assert not (tmp_path / "future.zip").exists()
