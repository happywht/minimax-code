"""Host port migration and offline bundle compatibility."""
from __future__ import annotations

import sqlite3

import pytest
from test_attachment_bundle import legacy_v6_writer
from test_office_bundle import historical

from minimax_code import bundle
from minimax_code.storage.db import AsyncDatabase, Database


def test_old_bundle_restore_migrate_and_new_bundle_roundtrip(tmp_path, monkeypatch):
    db = historical(tmp_path / "old" / "data.db", 46, monkeypatch)
    db.execute("INSERT INTO hosts(id,name,hostname,username,created_at,updated_at) "
               "VALUES('h','old','linux','me','a','b')")
    db.close()
    old = tmp_path / "old.zip"
    frozen = legacy_v6_writer()
    assert frozen.create_bundle(tmp_path / "old", old)["schema_version"] == 46
    assert bundle.verify_bundle(old)["format_version"] == 6
    restored = tmp_path / "restored"
    assert bundle.restore_bundle(old, restored)["schema_version"] == 46
    assert frozen.create_bundle(restored, tmp_path / "old-again.zip")["schema_version"] == 46
    assert frozen.verify_bundle(tmp_path / "old-again.zip")["format_version"] == 6
    with Database(restored / "data.db") as db:
        assert db.migrate() == [47]
        assert db.fetchone("SELECT name,remote_port FROM hosts WHERE id='h'")[:] == ("old", 8765)
        db.execute("UPDATE hosts SET remote_port=9999 WHERE id='h'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE hosts SET remote_port=0 WHERE id='h'")
    new = tmp_path / "new.zip"
    assert bundle.create_bundle(restored, new)["format_version"] == 7
    assert bundle.verify_bundle(new)["schema_version"] == 47
    target = tmp_path / "new-restored"
    bundle.restore_bundle(new, target)
    with Database(target / "data.db") as db:
        assert db.fetchone("SELECT remote_port FROM hosts WHERE id='h'")[0] == 9999
    with pytest.raises(ValueError):
        frozen.verify_bundle(new)


async def test_legacy_json_host_import_defaults_and_current_export(tmp_path):
    from minimax_code.ipc.handlers_data import dump_database, restore_database

    async_db = AsyncDatabase(tmp_path / "data.db")
    await async_db.connect()
    await async_db.migrate()
    row = {"id": "old", "name": "old", "hostname": "linux", "username": "me",
           "created_at": "a", "updated_at": "b"}
    envelope = {"format": "minimax-code-export", "schema_version": 46,
                "tables": {"hosts": [row]}}
    await restore_database(async_db, envelope)
    assert (await async_db.fetchone("SELECT remote_port FROM hosts WHERE id='old'"))[0] == 8765
    await async_db.execute("UPDATE hosts SET remote_port=9999 WHERE id='old'")
    exported = await dump_database(async_db)
    assert exported["tables"]["hosts"][0]["remote_port"] == 9999
    await async_db.close()
