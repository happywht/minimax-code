"""Both-vault durability and real committed-v1 backwards compatibility."""
from __future__ import annotations

import gzip
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import httpx
import pytest

from minimax_code import artifact_http, attachment_http, bundle
from minimax_code.attachments import AttachmentService
from minimax_code.config import Config
from minimax_code.http_server import build_app
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.db import AsyncDatabase
from minimax_code.text_artifacts import ArtifactService

FIXTURES = Path(__file__).parent / "fixtures"


def cli(*args):
    completed = subprocess.run([sys.executable, "-m", "minimax_code.bundle", *map(str, args)],
                               cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def legacy_writer():
    path = FIXTURES / "bundle-v1-writer.py.txt"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "11fb0bd93206e86514bae8928e65645084c75a58c4c4a38bc8c55ac1464ad863"
    loader = importlib.machinery.SourceFileLoader("minimax_code._frozen_v1_writer", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    assert module.FORMAT_VERSION == 1 and module.SCHEMA_VERSION == 41
    return module


def legacy_v2_writer():
    # Exact HEAD writer and its historical attachment constants in a separate
    # namespace. Current v2 algorithm constants must not change the old reader.
    import minimax_code
    name = "minimax_code._frozen_v2"
    package = types.ModuleType(name)
    package.__path__ = [str(Path(minimax_code.__file__).parent)]
    package.__version__ = minimax_code.__version__
    sys.modules[name] = package
    for suffix, filename, digest in (
        ("attachments", "bundle-v2-attachments.py.txt", "6b37320be1b76db7d1153f89a4ec04554e9f9bed3855028e1652fce6e57a2025"),
        ("bundle", "bundle-v2-writer.py.txt", "4e5ca9bb8cfaebb051193d3b23a72367a0183dfd8149d095eace510243874602"),
    ):
        path = FIXTURES / filename
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        loader = importlib.machinery.SourceFileLoader(name + "." + suffix, str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = module
        loader.exec_module(module)
    assert module.FORMAT_VERSION == 2 and module.SCHEMA_VERSION == 42
    return module


async def test_actual_v2_fixture_restore_without_migration_then_043_preserves_all_v1_values(tmp_path):
    meta = json.loads((FIXTURES / "bundle-v2-schema042.json").read_bytes())
    compressed = (FIXTURES / "bundle-v2-schema042.zip.gz").read_bytes()
    assert len(compressed) == meta["compressed_bytes"]
    assert hashlib.sha256(compressed).hexdigest() == meta["compressed_sha256"]
    raw = gzip.decompress(compressed)
    assert len(raw) == meta["bundle_bytes"] and hashlib.sha256(raw).hexdigest() == meta["bundle_sha256"]
    archive = tmp_path / "v2.zip"
    archive.write_bytes(raw)
    assert legacy_v2_writer().verify_bundle(archive)["schema_version"] == 42
    assert bundle.verify_bundle(archive)["format_version"] == 2
    target = tmp_path / "restored"
    bundle.restore_bundle(archive, target)
    with sqlite3.connect(target / "data.db") as conn:
        before = conn.execute("SELECT * FROM attachments").fetchone()
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (42,)
        assert conn.execute("SELECT root_path FROM projects WHERE id='legacy-project'").fetchone() == ("/original/v2/project",)
    conn.close()
    db = AsyncDatabase(target / "data.db")
    await db.connect()
    try:
        assert await db.migrate() == [43]
        after = await db.fetchone("SELECT * FROM attachments")
        assert tuple(after)[:-2] == before
        assert after["parser_version"] == 1 and after["parser_reason"] == after["parser_scope"] == "legacy_v1"
        row, data = await AttachmentService(db, target).content("legacy-project", meta["attachment"]["id"])
        assert data == bytes.fromhex(meta["original_hex"])
        assert row["service_id"] == meta["attachment"]["service_id"]
    finally:
        await db.close()
    new = tmp_path / "v3.zip"
    assert bundle.create_bundle(target, new)["schema_version"] == 43
    with pytest.raises(ValueError):
        legacy_v2_writer().verify_bundle(new)
    assert bundle.verify_bundle(new)["format_version"] == 3


async def stream(data):
    yield data


@pytest.fixture
async def both_vaults(tmp_path):
    data = tmp_path / "data"
    db = AsyncDatabase(data / "data.db")
    await db.connect()
    await db.migrate()
    project = tmp_path / "project"
    project.mkdir()
    await ProjectsDAO(db).create(id="p", name="Project", root_path=str(project))
    text = b"\xef\xbb\xbfGenerated original\r\n"
    (project / "result.txt").write_bytes(text)
    artifact = await ArtifactService(db, data).register("p", "result.txt")
    service = AttachmentService(db, data)
    identity = (await service.list("p"))["service_id"]
    original = b"\xef\xbb\xbfAttached original\r\n=1+2\r\n"
    attachment = await service.upload("p", "input.txt", identity, stream(original))
    binary = b"%PDF-1.7\noriginal-only, not parsed"
    pdf = await service.upload("p", "input.pdf", identity, stream(binary))
    (service.vault / "orphan").write_bytes(b"unregistered")
    (service.vault / ".pending-unowned").write_bytes(b"unregistered")
    await db.close()
    yield data, artifact, text, [(attachment, original), (pdf, binary)]
    await db.close()


async def test_actual_committed_v1_fixture_verify_restore_without_migration_or_rebind(tmp_path, monkeypatch):
    metadata = json.loads((FIXTURES / "bundle-v1-schema041.json").read_bytes())
    encoded = (FIXTURES / "bundle-v1-schema041.zip.gz").read_bytes()
    assert metadata["generator_commit"] == "dc1d554"
    assert len(encoded) == metadata["compressed_bytes"]
    assert hashlib.sha256(encoded).hexdigest() == metadata["compressed_sha256"]
    raw = gzip.decompress(encoded)
    assert len(raw) == metadata["bundle_bytes"] == 676918
    assert hashlib.sha256(raw).hexdigest() == metadata["bundle_sha256"] == "3cda5c16ba9d6138881371357427bb5e133d7c016b26d4d8c5381877d29b7731"
    archive = tmp_path / "old-v1.zip"
    archive.write_bytes(raw)
    assert legacy_writer().verify_bundle(archive)["format_version"] == 1
    report = bundle.verify_bundle(archive)
    assert report["format_version"] == 1 and report["schema_version"] == 41
    async def never(*_):
        raise AssertionError("Bundle restore must not run migrations")
    monkeypatch.setattr(AsyncDatabase, "migrate", never)
    target = tmp_path / "restored"
    bundle.restore_bundle(archive, target)
    with sqlite3.connect((target / "data.db").as_uri() + "?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (41,)
        assert conn.execute("SELECT name FROM sqlite_schema WHERE name='attachments'").fetchone() is None
        assert conn.execute("SELECT root_path FROM projects WHERE id='legacy-project'").fetchone() == ("/original/v1/project",)
    assert set(target.iterdir()) == {target / "data.db", target / bundle.VAULT}
    db = AsyncDatabase(target / "data.db")
    await db.connect()
    try:
        _, content = await ArtifactService(db, target).content(metadata["project_id"], metadata["artifact_id"])
        assert content == bytes.fromhex(metadata["original_hex"])
    finally:
        await db.close()


async def test_forward_schema041_upgrade_preserves_registered_text_bytes_and_service(tmp_path):
    metadata = json.loads((FIXTURES / "bundle-v1-schema041.json").read_bytes())
    archive = tmp_path / "old.zip"
    archive.write_bytes(gzip.decompress((FIXTURES / "bundle-v1-schema041.zip.gz").read_bytes()))
    target = tmp_path / "data"
    bundle.restore_bundle(archive, target)
    db = AsyncDatabase(target / "data.db")
    await db.connect()
    try:
        before = dict(await db.fetchone("SELECT * FROM text_artifacts"))
        service = dict(await db.fetchone("SELECT * FROM artifact_service"))
        assert await db.migrate() == [42, 43]
        assert dict(await db.fetchone("SELECT * FROM text_artifacts")) == before
        assert dict(await db.fetchone("SELECT * FROM artifact_service")) == service
        assert not await db.fetchall("SELECT * FROM attachments")
        assert (await ArtifactService(db, target).content(metadata["project_id"], metadata["artifact_id"]))[1] == bytes.fromhex(metadata["original_hex"])
        assert (await AttachmentService(db, target).list(metadata["project_id"]))["service_id"] == service["service_id"]
    finally:
        await db.close()


async def test_v2_restores_all_registered_originals_and_public_authenticated_download_bytes(both_vaults, tmp_path, monkeypatch):
    data, artifact, text, attachments = both_vaults
    before = {str(path.relative_to(data)): path.read_bytes() for path in data.rglob("*") if path.is_file()}
    archive = tmp_path / "v2.zip"
    report = cli("create", "--data-dir", data, "--output", archive)
    assert not list(tmp_path.glob(".bundle-pending-*"))
    assert report["format_version"] == 3 and report["schema_version"] == 43
    assert report["artifact_count"] == 1 and report["attachment_count"] == 2
    assert report["excluded_attachment_entries"] == {"orphan": 1, "pending": 1}
    assert cli("verify", "--bundle", archive)["attachment_count"] == 2
    for name, value in before.items():
        assert (data / name).read_bytes() == value
    with pytest.raises(ValueError):
        legacy_writer().verify_bundle(archive)  # Actual old reader fails closed on v2.
    with pytest.raises(ValueError):
        legacy_v2_writer().verify_bundle(archive)
    target = tmp_path / "restored"
    assert cli("restore", "--bundle", archive, "--new-data-dir", target)["attachment_count"] == 2
    db = AsyncDatabase(target / "data.db")
    await db.connect()
    async def attachment_factory():
        return AttachmentService(db, target)
    async def artifact_factory():
        return ArtifactService(db, target)
    monkeypatch.setattr(attachment_http, "get_attachment_service", attachment_factory)
    monkeypatch.setattr(artifact_http, "get_artifact_service", artifact_factory)
    server = IPCServer(config=Config.from_env(), stdin=io.StringIO(), stdout=io.StringIO())
    app = build_app(server, version="restore-test")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            client.headers["Authorization"] = f"Bearer {app.state.auth.token}"
            response = await client.get(f"/artifacts/{artifact['id']}/content?project_id=p")
            assert response.status_code == 200 and response.content == text
            for row, original in attachments:
                response = await client.get(f"/attachments/{row['id']}/content?project_id=p")
                assert response.status_code == 200 and response.content == original
                assert response.headers["x-attachment-sha256"] == hashlib.sha256(original).hexdigest()
                assert (await AttachmentService(db, target).get("p", row["id"]))["attachment"] == row
        assert not (target / bundle.ATTACHMENT_VAULT / "orphan").exists()
        assert not (target / bundle.ATTACHMENT_VAULT / ".pending-unowned").exists()
    finally:
        server.listeners.clear()
        await db.close()


@pytest.mark.parametrize("kind", ["missing", "corrupt"])
async def test_registered_attachment_missing_or_corrupt_never_silently_omitted(both_vaults, tmp_path, kind):
    data, _, _, rows = both_vaults
    snapshot = data / bundle.ATTACHMENT_VAULT / rows[0][0]["snapshot_id"]
    if kind == "missing":
        snapshot.unlink()
    else:
        snapshot.write_bytes(b"bad")
    with pytest.raises((bundle.BundleError, OSError)):
        bundle.create_bundle(data, tmp_path / "failed.zip")
    assert not (tmp_path / "failed.zip").exists() and not list(tmp_path.glob(".bundle-pending-*"))


async def test_shared_file_count_limit_covers_both_vaults(both_vaults, tmp_path, monkeypatch):
    data, *_ = both_vaults
    monkeypatch.setattr(bundle, "MAX_FILES", 2)  # One artifact + two attachments.
    with pytest.raises(bundle.BundleError, match="Too many"):
        bundle.create_bundle(data, tmp_path / "failed.zip")
    assert not (tmp_path / "failed.zip").exists()


@pytest.mark.parametrize("change", ["unknown-version", "schema-format-mismatch", "attachment-path", "missing-original"])
async def test_v2_version_reference_member_rules_fail_closed(both_vaults, tmp_path, change):
    data, *_ = both_vaults
    good = tmp_path / "good.zip"
    bundle.create_bundle(data, good)
    with zipfile.ZipFile(good) as archive:
        members = [(item.filename, archive.read(item)) for item in archive.infolist()]
    manifest = json.loads(members[0][1])
    if change == "unknown-version":
        manifest["format_version"] = 4
    elif change == "schema-format-mismatch":
        manifest["schema_version"] = 41
    elif change == "attachment-path":
        manifest["attachments"][0]["snapshot_id"] = "../../outside"
    else:
        members.pop()
    members[0] = ("manifest.json", json.dumps(manifest).encode())
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in members:
            archive.writestr(name, content)
    with pytest.raises(bundle.BundleError):
        bundle.restore_bundle(bad, tmp_path / "new")
    assert not (tmp_path / "new").exists() and not (tmp_path / "outside").exists()
    assert not list(tmp_path.glob(".bundle-pending-*"))


async def test_schema042_requires_known_attachment_columns_not_only_version_number(both_vaults, tmp_path):
    data, *_ = both_vaults
    with sqlite3.connect(data / "data.db") as conn:
        conn.execute("DROP TABLE attachments")
        conn.execute("CREATE TABLE attachments (id TEXT)")
    conn.close()
    with pytest.raises(bundle.BundleError, match="Unsupported attachment schema"):
        bundle.create_bundle(data, tmp_path / "bad.zip")
    assert not (tmp_path / "bad.zip").exists()
