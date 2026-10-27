"""Real SQLite and filesystem evidence for explicit immutable text snapshots."""
from __future__ import annotations

import asyncio
import hashlib
import os

import pytest

from minimax_code import text_artifacts as module
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.db import AsyncDatabase
from minimax_code.text_artifacts import MAX_BYTES, ArtifactService


@pytest.fixture
async def artifacts(tmp_path):
    db = AsyncDatabase(tmp_path / "data" / "test.db")
    await db.connect()
    await db.migrate()
    root = tmp_path / "project"
    root.mkdir()
    await ProjectsDAO(db).create(id="p", name="project", root_path=str(root))
    service = ArtifactService(db, db.path.parent)
    yield service, root, db
    await db.close()


async def test_original_bytes_versions_restart_and_project_delete(artifacts):
    service, root, db = artifacts
    original = b"\xef\xbb\xbf# title\r\n<script>alert(1)</script>\r\n"
    source = root / "report.md"
    source.write_bytes(original)
    first = await service.register("p", "report.md")
    unchanged = await service.register("p", "report.md")
    assert (first["version"], unchanged["version"]) == (1, 2)
    assert first["snapshot_id"] != unchanged["snapshot_id"]
    assert first["sha256"] == hashlib.sha256(original).hexdigest()
    assert first["parse_status"] == "parseable" and first["verification_status"] == "unverified"
    assert source.read_bytes() == original
    source.write_text("changed", encoding="utf-8")
    third = await service.register("p", "report.md")
    assert third["version"] == 3
    await db.close()
    await db.connect()
    restarted = ArtifactService(db, db.path.parent)
    assert (await restarted.content("p", first["id"]))[1] == original
    assert (await restarted.get("p", first["id"]))["preview_text"] == original.decode("utf-8")
    assert (await restarted.list("p"))["service_id"] == first["service_id"]
    assert len((await restarted.list("p"))["artifacts"]) == 3
    assert [row["version"] for row in (await restarted.list("p"))["artifacts"]] == [3, 2, 1]
    await ProjectsDAO(db).delete("p")
    assert (await db.fetchone("SELECT COUNT(*) n FROM text_artifacts"))["n"] == 3
    assert (service.vault / first["snapshot_id"]).read_bytes() == original
    for operation in (restarted.list("p"), restarted.get("p", first["id"]),
                      restarted.content("p", first["id"])):
        with pytest.raises(ValueError, match="existing project"):
            await operation


@pytest.mark.parametrize("path", ["", "../escape.txt", "/tmp/a.txt", "C:/a.txt",
                                  "a/../a.txt", "a//a.txt", "./a.txt", "a\\b.txt",
                                  "a.txt:secret", "a.html", "a.pdf", "a.txt\x00", "a/./b.md"])
async def test_invalid_paths_never_publish(artifacts, path):
    service, _, db = artifacts
    with pytest.raises((ValueError, OSError)):
        await service.register("p", path)
    assert not service.vault.exists()
    assert not await db.fetchall("SELECT * FROM text_artifacts")


@pytest.mark.parametrize("content,name", [(b"\xff", "bad.txt"), (b"hi\0", "bad.md"),
                                          (b'"unfinished', "bad.csv"),
                                          (b"x" * (MAX_BYTES + 1), "large.txt")],
                         ids=["invalid-utf8", "nul", "bad-csv", "oversize"])
async def test_invalid_content_never_publishes(artifacts, content, name):
    service, root, db = artifacts
    (root / name).write_bytes(content)
    with pytest.raises(ValueError):
        await service.register("p", name)
    assert not service.vault.exists()
    assert not await db.fetchall("SELECT * FROM text_artifacts")


async def test_csv_formula_remains_original_text_and_max_txt(artifacts):
    service, root, _ = artifacts
    csv = b'name,formula\r\nitem,"=2+3"\r\n'
    (root / "data.csv").write_bytes(csv)
    row = await service.register("p", "data.csv")
    assert (await service.get("p", row["id"]))["preview_text"] == csv.decode()
    (root / "max.txt").write_bytes(b"x" * MAX_BYTES)
    row = await service.register("p", "max.txt")
    assert row["size_bytes"] == MAX_BYTES


@pytest.mark.parametrize("kind", ["missing", "corrupt", "truncated"])
async def test_bad_vault_never_falls_back_to_source(artifacts, kind):
    service, root, _ = artifacts
    (root / "source.txt").write_bytes(b"original")
    row = await service.register("p", "source.txt")
    path = service.vault / row["snapshot_id"]
    if kind == "missing":
        path.unlink()
    else:
        path.write_bytes(b"tampered" if kind == "corrupt" else b"ori")
    with pytest.raises(ValueError, match="missing or corrupt"):
        await service.content("p", row["id"])
    assert (root / "source.txt").read_bytes() == b"original"


async def test_run_project_binding_and_wrong_project_lookup(artifacts, tmp_path):
    service, root, db = artifacts
    (root / "a.txt").write_bytes(b"a")
    await ProjectsDAO(db).create(id="other", name="other", root_path=str(tmp_path))
    async with db.transaction() as conn:
        for session, project in (("s", "p"), ("s2", "other")):
            await conn.execute("INSERT INTO sessions(id,title,created_at,updated_at,project_id) "
                               "VALUES(?, '', 'now', 'now', ?)", (session, project))
            await conn.execute("INSERT INTO agent_runs(id,session_id,mode,status,title,created_at) "
                               "VALUES(?,?,'execute','completed','','now')", (session, session))
    for run in ("missing", "s2"):
        with pytest.raises(ValueError, match="Run must belong"):
            await service.register("p", "a.txt", run)
    assert not service.vault.exists()
    row = await service.register("p", "a.txt", "s")
    assert row["run_id"] == "s"
    with pytest.raises(ValueError, match="does not exist in this project"):
        await service.get("other", row["id"])
    async with db.transaction() as conn:
        await conn.execute("UPDATE sessions SET workspace_path=? WHERE id='s'", (str(tmp_path),))
    with pytest.raises(ValueError, match="workspace differs"):
        await service.register("p", "a.txt", "s")


async def test_concurrent_versions_are_serialized(artifacts):
    service, root, _ = artifacts
    (root / "a.txt").write_bytes(b"a")
    rows = await asyncio.gather(*(service.register("p", "a.txt") for _ in range(5)))
    assert sorted(row["version"] for row in rows) == [1, 2, 3, 4, 5]


async def test_hardlink_directory_and_symbolic_links_rejected(artifacts):
    service, root, _ = artifacts
    source = root / "a.txt"
    source.write_bytes(b"a")
    os.link(source, root / "hard.txt")
    (root / "dir.txt").mkdir()
    for name in ("a.txt", "hard.txt", "dir.txt"):
        with pytest.raises((ValueError, OSError)):
            await service.register("p", name)
    source.unlink()
    try:
        source.symlink_to(root / "hard.txt")
    except OSError:
        pytest.skip("OS account cannot create a symlink; hardlink/directory rejection tested")
    with pytest.raises((ValueError, OSError)):
        await service.register("p", "a.txt")


async def test_read_detects_same_size_mutation(artifacts, monkeypatch):
    service, root, db = artifacts
    source = root / "a.txt"
    source.write_bytes(b"original")
    read = module.os.read
    calls = 0

    def mutate(fd, length):
        nonlocal calls
        result = read(fd, length)
        calls += 1
        # POSIX allows concurrent writes; Windows file handle denies them.
        if calls == 1:
            try:
                source.write_bytes(b"mutated!")
            except PermissionError:
                pass
        return result

    monkeypatch.setattr(module.os, "read", mutate)
    if os.name == "nt":
        row = await service.register("p", "a.txt")
        assert (await service.content("p", row["id"]))[1] == b"original"
    else:
        with pytest.raises(ValueError, match="changed while reading"):
            await service.register("p", "a.txt")
        assert not await db.fetchall("SELECT * FROM text_artifacts")


async def test_failed_publication_leaves_no_row_or_pending(artifacts, monkeypatch):
    service, root, db = artifacts
    (root / "a.txt").write_bytes(b"a")

    def fail(*args, **kwargs):
        raise OSError("publication failed")

    monkeypatch.setattr(module.os, "link", fail)
    with pytest.raises(OSError, match="publication failed"):
        await service.register("p", "a.txt")
    assert not list(service.vault.iterdir())
    assert not await db.fetchall("SELECT * FROM text_artifacts")


async def test_project_binding_changed_before_commit_preserves_orphan(artifacts, monkeypatch):
    service, root, db = artifacts
    (root / "a.txt").write_bytes(b"a")
    insert = service.dao.insert

    async def changed(fields, **kwargs):
        await ProjectsDAO(db).update("p", root_path=str(root.parent))
        return await insert(fields, **kwargs)

    monkeypatch.setattr(service.dao, "insert", changed)
    with pytest.raises(ValueError, match="binding changed"):
        await service.register("p", "a.txt")
    assert len(list(service.vault.iterdir())) == 1
    assert not await db.fetchall("SELECT * FROM text_artifacts")


async def test_source_replacement_is_denied_or_detected(artifacts, monkeypatch):
    service, root, _ = artifacts
    source, replacement = root / "a.txt", root / "replacement.txt"
    source.write_bytes(b"original")
    replacement.write_bytes(b"changed!")
    read = module.os.read
    attempted = False

    def replace(fd, length):
        nonlocal attempted
        result = read(fd, length)
        if not attempted:
            attempted = True
            try:
                replacement.replace(source)
            except PermissionError:
                pass
        return result

    monkeypatch.setattr(module.os, "read", replace)
    if os.name == "nt":
        row = await service.register("p", "a.txt")
        assert (await service.content("p", row["id"]))[1] == b"original"
    else:
        with pytest.raises(ValueError, match="changed while reading"):
            await service.register("p", "a.txt")


async def test_source_truncated_read_is_rejected(artifacts, monkeypatch):
    service, root, _ = artifacts
    (root / "a.txt").write_bytes(b"original")
    monkeypatch.setattr(module.os, "read", lambda *args: b"")
    with pytest.raises(ValueError, match="changed while reading"):
        await service.register("p", "a.txt")
    assert not service.vault.exists()


async def test_linked_ancestor_and_vault_are_rejected(artifacts, tmp_path):
    service, root, _ = artifacts
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_bytes(b"outside")
    try:
        (root / "alias").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("OS account cannot create directory symlinks")
    with pytest.raises((ValueError, OSError)):
        await service.register("p", "alias/a.txt")
    (root / "a.txt").write_bytes(b"inside")
    service.vault.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="links or reparse"):
        await service.register("p", "a.txt")
    assert list(outside.iterdir()) == [outside / "a.txt"]


def test_schema_40_upgrade_preserves_existing_rows(tmp_path, monkeypatch):
    from minimax_code.storage import db as storage

    discover = storage.discover_migrations
    monkeypatch.setattr(storage, "discover_migrations", lambda *args, **kwargs: [
        (version, run) for version, run in discover(*args, **kwargs) if version <= 40])
    db = storage.Database(tmp_path / "upgrade.db")
    try:
        assert db.migrate()[-1] == 40
        db.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES('old','old','now','now')")
        monkeypatch.setattr(storage, "discover_migrations", discover)
        assert db.migrate() == [41, 42, 43]
        assert db.fetchone("SELECT name FROM projects WHERE id='old'")[0] == "old"
        assert db.fetchone("SELECT COUNT(*) FROM text_artifacts")[0] == 0
        assert db.migrate() == []
    finally:
        db.close()


async def test_snapshot_publication_never_overwrites(artifacts):
    service, _, db = artifacts
    snapshot = "1" * 32
    service._publish(b"original", snapshot)
    with pytest.raises(FileExistsError):
        service._publish(b"replacement", snapshot)
    assert (service.vault / snapshot).read_bytes() == b"original"
    assert sorted(path.name for path in service.vault.iterdir()) == [snapshot]
    assert not await db.fetchall("SELECT * FROM text_artifacts")


async def test_service_factory_uses_opened_database_not_changed_environment(artifacts, monkeypatch):
    from minimax_code import app

    service, _, db = artifacts

    async def opened():
        return db

    monkeypatch.setattr(app, "ensure_db", opened)
    monkeypatch.setenv("MINIMAX_CODE_DATA_DIR", str(db.path.parent / "different"))
    assert (await module.get_artifact_service()).vault == service.vault
