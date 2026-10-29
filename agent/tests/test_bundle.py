"""Real migrated SQLite, original vault bytes, and hostile archive CLI checks."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from minimax_code import bundle
from minimax_code.runtime.service_lease import acquire_service_lease
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.db import AsyncDatabase
from minimax_code.text_artifacts import ArtifactService


@pytest.fixture
async def source(tmp_path):
    data = tmp_path / "source-data"
    db = AsyncDatabase(data / "data.db")
    await db.connect()
    await db.migrate()
    project = tmp_path / "project"
    project.mkdir()
    await ProjectsDAO(db).create(id="project", name="original", root_path=str(project))
    service = ArtifactService(db, data)
    original = b"\xef\xbb\xbf# original\r\n<script>original text</script>\r\n"
    changed = b"\xef\xbb\xbf# version two\r\n"
    path = project / "document.md"
    path.write_bytes(original)
    first = await service.register("project", "document.md")
    path.write_bytes(changed)
    second = await service.register("project", "document.md")
    (service.vault / "orphan").write_bytes(b"not included")
    (service.vault / ".pending-unowned").write_bytes(b"not included")
    # Not a live installation; preservation of these original opaque bindings is essential.
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO execution_installation VALUES "
            "(1,'original-install',123,'host','store','nonce','location',"
            "'/original/data','dev','ino','marker','active','now','now')"
        )
    await db.close()
    # A stopped/crashed offline service can leave committed WAL pages behind.
    # Abrupt exit avoids connection-close checkpoint, without any live DB writer.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sqlite3,sys,os; "
            "c=sqlite3.connect(sys.argv[1]); "
            "c.execute('PRAGMA journal_mode=WAL'); "
            "c.execute('PRAGMA wal_autocheckpoint=0'); "
            "c.execute(\"UPDATE projects SET name='committed only in WAL' WHERE id='project'\"); "
            "c.commit(); os._exit(0)",
            str(data / "data.db"),
        ],
        check=True,
        timeout=10,
    )
    yield data, db, service, path, [(first, original), (second, changed)]
    await db.close()


def cli(*args):
    result = subprocess.run(
        [sys.executable, "-m", "minimax_code.bundle", *map(str, args)],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout if result.returncode == 0 else result.stderr
    return result, json.loads(output)


def read_archive(path):
    with zipfile.ZipFile(path) as archive:
        return [(row.filename, archive.read(row)) for row in archive.infolist()]


def write_archive(path, members):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in members:
            archive.writestr(name, data)


def files_below(path):
    return {
        str(file.relative_to(path)): file.read_bytes() for file in path.rglob("*") if file.is_file()
    }


async def test_cli_round_trip_original_bytes_all_versions_readonly(source, tmp_path):
    data, db, service, path, rows = source
    # A plain data.db copy omits this committed WAL update.
    before = files_below(data)
    business_source = path.read_bytes()
    archive = tmp_path / "data.bundle"
    result, report = cli("create", "--data-dir", data, "--output", archive)
    assert result.returncode == 0, report
    assert report["artifact_count"] == 2
    assert report["scope"] == "data-only"
    assert "checkpoint untracked-file snapshots and Git stash objects" in report["exclusions"]
    assert report["excluded_vault_entries"] == {"orphan": 1, "pending": 1}
    assert "DATA-ONLY" in report["notice"] and "safe rebinding" in report["notice"]
    assert "original-install" not in result.stdout
    assert path.read_bytes() == business_source
    after = files_below(data)
    for name, content in before.items():
        assert after[name] == content, name
    names = [name for name, _ in read_archive(archive)]
    assert set(names) == {
        "manifest.json",
        "data.db",
        *[f"{bundle.VAULT}/{row['snapshot_id']}" for row, _ in rows],
    }
    result, report = cli("verify", "--bundle", archive)
    assert result.returncode == 0 and report["operation"] == "verify"
    assert "checkpoint untracked-file snapshots and Git stash objects" in report["exclusions"]
    target = tmp_path / "new-data"
    result, report = cli("restore", "--bundle", archive, "--new-data-dir", target)
    assert result.returncode == 0, report
    assert report["operation"] == "restore"
    assert "checkpoint untracked-file snapshots and Git stash objects" in report["exclusions"]
    assert set(target.iterdir()) == {target / "data.db", target / bundle.VAULT,
                                    target / bundle.ATTACHMENT_VAULT}

    # Adapter proves ArtifactService can read all historical versions without a migration,
    # source access, service startup or even a write-capable SQLite connection.
    class Readonly:
        def __init__(self):
            self.conn = sqlite3.connect((target / "data.db").as_uri() + "?mode=ro", uri=True)
            self.conn.row_factory = sqlite3.Row

        async def fetchone(self, sql, params=()):
            return self.conn.execute(sql, params).fetchone()

        async def fetchall(self, sql, params=()):
            return self.conn.execute(sql, params).fetchall()

    readonly = Readonly()
    restored = ArtifactService(readonly, target)

    # DAO.service_id normally uses INSERT OR IGNORE; inspection path is explicitly readonly.
    async def service_id():
        return readonly.conn.execute("SELECT service_id FROM artifact_service").fetchone()[0]

    restored.dao.service_id = service_id
    try:
        assert readonly.conn.execute(
            "SELECT name,root_path FROM projects WHERE id='project'"
        ).fetchone()[:] == ("committed only in WAL", str(path.parent))
        assert readonly.conn.execute(
            "SELECT install_id,data_dir FROM execution_installation"
        ).fetchone()[:] == ("original-install", "/original/data")
        for row, original in rows:
            metadata, content = await restored.content("project", row["id"])
            assert content == original and metadata["version"] == row["version"]
        assert readonly.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        readonly.conn.close()
    assert not list(tmp_path.glob(".bundle-pending-*"))


async def test_running_service_lease_cli_refuses_before_publication(source, tmp_path):
    data, *_ = source
    archive = tmp_path / "bundle.zip"
    with acquire_service_lease(data):
        result, report = cli("create", "--data-dir", data, "--output", archive)
    assert result.returncode == 1 and "already in use" in report["error"]
    assert not archive.exists() and not list(tmp_path.glob(".bundle-pending-*"))


@pytest.mark.parametrize("kind", ["existing-file", "existing-directory", "current-directory"])
async def test_restore_never_overwrites_existing_target(source, tmp_path, kind):
    data, *_ = source
    archive = tmp_path / "bundle.zip"
    bundle.create_bundle(data, archive)
    target = tmp_path / "existing"
    if kind == "existing-file":
        target.write_bytes(b"keep")
    elif kind == "existing-directory":
        target.mkdir()
        (target / "keep").write_bytes(b"keep")
    else:
        target = Path.cwd()
    result, report = cli("restore", "--bundle", archive, "--new-data-dir", target)
    assert result.returncode == 1 and report["ok"] is False
    if kind == "existing-file":
        assert target.read_bytes() == b"keep"
    elif kind == "existing-directory":
        assert (target / "keep").read_bytes() == b"keep"


async def test_create_output_no_overwrite_and_no_source_output(source, tmp_path):
    data, *_ = source
    output = tmp_path / "existing.zip"
    output.write_bytes(b"keep")
    with pytest.raises(bundle.BundleError, match="already exist"):
        bundle.create_bundle(data, output)
    assert output.read_bytes() == b"keep"
    with pytest.raises(bundle.BundleError, match="outside"):
        bundle.create_bundle(data, data / "nested.zip")


@pytest.mark.parametrize(
    "kind",
    [
        "truncated",
        "corrupt-db",
        "corrupt-vault",
        "missing-vault",
        "traversal",
        "absolute",
        "backslash",
        "duplicate",
        "extra",
        "unsupported-format",
        "unsupported-app",
        "unsupported-schema",
        "reference-mismatch",
        "size-mismatch",
        "db-hash-mismatch",
        "service-mismatch",
        "json-duplicate",
        "json-not-object",
    ],
)
async def test_malicious_or_corrupt_archive_fails_closed_cli(source, tmp_path, kind):
    data, _, _, _, rows = source
    archive = tmp_path / "good.zip"
    bundle.create_bundle(data, archive)
    members = read_archive(archive)
    manifest = json.loads(members[0][1])
    if kind == "truncated":
        bad_bytes = archive.read_bytes()[:-18]
    elif kind in {"corrupt-db", "corrupt-vault"}:
        name = "data.db" if kind == "corrupt-db" else f"{bundle.VAULT}/{rows[0][0]['snapshot_id']}"
        members = [(key, b"corrupt" if key == name else value) for key, value in members]
    elif kind == "missing-vault":
        members.pop()
    elif kind in {"traversal", "absolute", "backslash", "extra"}:
        name = {
            "traversal": "../escape",
            "absolute": "/escape",
            "backslash": "..\\escape",
            "extra": "unlisted",
        }[kind]
        members.append((name, b"must not extract"))
    elif kind == "duplicate":
        members.append(members[-1])
    elif kind == "json-duplicate":
        members[0] = ("manifest.json", members[0][1][:-1] + b',"format_version":1}')
    elif kind == "json-not-object":
        members[0] = ("manifest.json", b"[]")
    else:
        if kind == "unsupported-format":
            manifest["format_version"] = 999
        elif kind == "unsupported-app":
            manifest["app_version"] = "future"
        elif kind == "unsupported-schema":
            manifest["schema_version"] = 999
        elif kind == "reference-mismatch":
            manifest["artifacts"][0]["version"] += 1
        elif kind == "size-mismatch":
            manifest["artifacts"][0]["size_bytes"] += 1
        elif kind == "db-hash-mismatch":
            manifest["database"]["sha256"] = "0" * 64
        elif kind == "service-mismatch":
            manifest["service_id"] = "svc_" + "0" * 32
        members[0] = ("manifest.json", json.dumps(manifest).encode())
    bad = tmp_path / "bad.zip"
    if kind == "truncated":
        bad.write_bytes(bad_bytes)
    else:
        write_archive(bad, members)
    target = tmp_path / "target"
    result, report = cli("restore", "--bundle", bad, "--new-data-dir", target)
    assert result.returncode == 1 and report["ok"] is False
    assert not target.exists() and not (tmp_path / "escape").exists()
    assert not list(tmp_path.glob(".bundle-pending-*"))


@pytest.mark.parametrize(
    "kind", ["symlink-member", "compressed-member", "huge-member", "zip-extra"]
)
async def test_non_plain_archive_members_rejected(source, tmp_path, kind):
    data, *_ = source
    good = tmp_path / "good.zip"
    bundle.create_bundle(data, good)
    members = read_archive(good)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        for index, (name, payload) in enumerate(members):
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            if index == 1:
                if kind == "symlink-member":
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                elif kind == "compressed-member":
                    info.compress_type = zipfile.ZIP_DEFLATED
                elif kind == "zip-extra":
                    info.extra = b"\x99\x99\x00\x00"
                else:
                    payload += b"exceeds declared size"
            archive.writestr(info, payload)
    with pytest.raises((bundle.BundleError, zipfile.BadZipFile)):
        bundle.restore_bundle(bad, tmp_path / "new")
    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.glob(".bundle-pending-*"))


async def test_corrupt_sqlite_even_with_matching_manifest_hash_rejected(source, tmp_path):
    data, *_ = source
    good = tmp_path / "good.zip"
    bundle.create_bundle(data, good)
    members = read_archive(good)
    manifest = json.loads(members[0][1])
    payload = b"not a SQLite database"
    manifest["database"] = {
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    members[0] = ("manifest.json", json.dumps(manifest).encode())
    members[1] = ("data.db", payload)
    bad = tmp_path / "bad.zip"
    write_archive(bad, members)
    result, report = cli("restore", "--bundle", bad, "--new-data-dir", tmp_path / "new")
    assert result.returncode == 1 and report["ok"] is False
    assert not (tmp_path / "new").exists()


async def test_create_forced_verify_failure_cleans_only_owned_temporary(
    source, tmp_path, monkeypatch
):
    data, *_ = source
    unowned = tmp_path / ".bundle-pending-unowned"
    unowned.mkdir()
    keep = unowned / "keep"
    keep.write_bytes(b"keep")

    def fail(_):
        raise bundle.BundleError("forced verification failure")

    monkeypatch.setattr(bundle, "verify_bundle", fail)
    with pytest.raises(bundle.BundleError, match="forced"):
        bundle.create_bundle(data, tmp_path / "bundle.zip")
    assert not (tmp_path / "bundle.zip").exists() and keep.read_bytes() == b"keep"
    assert list(tmp_path.glob(".bundle-pending-*")) == [unowned]


async def test_restore_publish_failure_leaves_no_partial_target(source, tmp_path, monkeypatch):
    data, *_ = source
    good = tmp_path / "good.zip"
    bundle.create_bundle(data, good)

    def fail(*_):
        raise OSError("forced publication failure")

    monkeypatch.setattr(bundle, "_publish_directory", fail)
    with pytest.raises(OSError, match="forced"):
        bundle.restore_bundle(good, tmp_path / "new")
    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.glob(".bundle-pending-*"))


async def test_hardlinked_vault_and_database_rejected(source, tmp_path):
    data, _, _, _, rows = source
    snapshot = data / bundle.VAULT / rows[0][0]["snapshot_id"]
    os.link(snapshot, tmp_path / "hardlink")
    with pytest.raises(ValueError, match="links"):
        bundle.create_bundle(data, tmp_path / "bad.zip")
    (tmp_path / "hardlink").unlink()
    os.link(data / "data.db", tmp_path / "db-link")
    with pytest.raises(ValueError, match="links"):
        bundle.create_bundle(data, tmp_path / "bad.zip")
    assert not (tmp_path / "bad.zip").exists()


async def test_symlink_parent_rejected_where_supported(source, tmp_path):
    data, *_ = source
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(data, target_is_directory=True)
    except OSError:
        pytest.skip("OS account cannot create symbolic links")
    with pytest.raises((ValueError, OSError)):
        bundle.create_bundle(alias, tmp_path / "bad.zip")


def test_restore_atomic_publish_refuses_racing_existing_empty_directory(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "data.db").write_bytes(b"complete")
    target = tmp_path / "new"
    target.mkdir()
    with pytest.raises((bundle.BundleError, OSError)):
        bundle._publish_directory(stage, target)
    assert not list(target.iterdir()) and (stage / "data.db").read_bytes() == b"complete"


async def test_limits_fail_closed_before_publishing(source, tmp_path, monkeypatch):
    data, *_ = source
    monkeypatch.setattr(bundle, "MAX_FILES", 1)
    with pytest.raises(bundle.BundleError, match="Too many"):
        bundle.create_bundle(data, tmp_path / "limited.zip")
    assert not (tmp_path / "limited.zip").exists()
    assert not list(tmp_path.glob(".bundle-pending-*"))


@pytest.mark.parametrize("sidecar", ["data.db-wal", "data.db-shm"])
@pytest.mark.parametrize("alias", ["hardlink", "symlink"])
async def test_sidecar_aliases_never_touch_external_canary(source, tmp_path, sidecar, alias):
    data, *_ = source
    canary = tmp_path / "external-canary"
    original = b"external bytes must never be read as SQLite or mutated"
    canary.write_bytes(original)
    path = data / sidecar
    path.unlink()
    if alias == "hardlink":
        os.link(canary, path)
    else:
        try:
            path.symlink_to(canary)
        except OSError:
            pytest.skip("OS account cannot create symbolic links")
    result, report = cli("create", "--data-dir", data, "--output", tmp_path / "bad.zip")
    assert result.returncode == 1 and report["ok"] is False
    assert canary.read_bytes() == original and not (tmp_path / "bad.zip").exists()
    assert not list(tmp_path.glob(".bundle-pending-*"))


async def test_existing_rollback_journal_requires_explicit_recovery(source, tmp_path):
    data, *_ = source
    journal = data / "data.db-journal"
    journal.write_bytes(b"must not execute recovery against this source")
    with pytest.raises(bundle.BundleError, match="explicit source recovery"):
        bundle.create_bundle(data, tmp_path / "bad.zip")
    assert journal.read_bytes() == b"must not execute recovery against this source"
    assert not list(tmp_path.glob(".bundle-pending-*"))


@pytest.mark.parametrize("operation", ["create", "restore"])
@pytest.mark.parametrize("point", ["before-publication", "after-publication"])
async def test_directory_fsync_failure_reports_failure_and_cleans_owned_result(
    source, tmp_path, monkeypatch, operation, point
):
    data, *_ = source
    archive = tmp_path / "good.zip"
    bundle.create_bundle(data, archive)
    actual = bundle._fsync_directory
    target = tmp_path / ("failed.zip" if operation == "create" else "failed-data")

    def fail(path):
        is_parent = path == tmp_path
        if is_parent == (point == "after-publication"):
            raise OSError("forced directory fsync failure")
        return actual(path)

    monkeypatch.setattr(bundle, "_fsync_directory", fail)
    with pytest.raises(OSError, match="forced directory fsync"):
        if operation == "create":
            bundle.create_bundle(data, target)
        else:
            bundle.restore_bundle(archive, target)
    assert not target.exists() and archive.exists()
    assert not list(tmp_path.glob(".bundle-pending-*"))


@pytest.mark.parametrize("kind", ["trailing", "prefix", "comment", "forged-count"])
async def test_archive_wire_structure_fail_closed(source, tmp_path, kind):
    data, *_ = source
    good = tmp_path / "good.zip"
    bundle.create_bundle(data, good)
    wire = good.read_bytes()
    if kind == "trailing":
        wire += b"unlisted trailing payload"
    elif kind == "prefix":
        wire = b"unlisted prefix" + wire
    elif kind == "comment":
        wire = wire[:-2] + b"\x03\x00" + b"abc"
    else:
        wire = wire[:-14] + b"\xff\xff\xff\xff" + wire[-10:]
    bad = tmp_path / "bad.zip"
    bad.write_bytes(wire)
    result, report = cli("restore", "--bundle", bad, "--new-data-dir", tmp_path / "new")
    assert result.returncode == 1 and report["ok"] is False
    assert not (tmp_path / "new").exists() and not list(tmp_path.glob(".bundle-pending-*"))


async def test_no_artifacts_or_service_identity_is_supported_without_bootstrap(tmp_path):
    db = AsyncDatabase(tmp_path / "data" / "data.db")
    await db.connect()
    await db.migrate()
    await db.close()
    archive = tmp_path / "empty.zip"
    assert bundle.create_bundle(tmp_path / "data", archive)["artifact_count"] == 0
    target = tmp_path / "clone"
    assert bundle.restore_bundle(archive, target)["artifact_count"] == 0
    conn = sqlite3.connect((target / "data.db").as_uri() + "?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM artifact_service").fetchone() == (0,)
    finally:
        conn.close()
    assert not list((target / bundle.VAULT).iterdir())
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o700
        assert (target / bundle.VAULT).stat().st_mode & 0o777 == 0o700
        assert (target / "data.db").stat().st_mode & 0o777 == 0o600


def test_bundle_import_does_not_load_operational_runtimes():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import minimax_code.bundle; "
            "assert not any(name in sys.modules for name in "
            "('minimax_code.app', 'minimax_code.scheduler.service', 'minimax_code.plugins.registry', "
            "'minimax_code.process_adapter', 'minimax_code.agent.core'))",
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
