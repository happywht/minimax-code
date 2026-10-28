"""Both-vault durability and real committed-v1 backwards compatibility."""
from __future__ import annotations

import builtins
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
from minimax_code.storage import db as db_module
from minimax_code.storage.dao.budgets import BudgetsDAO
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
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


def legacy_v3_writer():
    # Raw e7f7e22 writer, independent of the current writer/version constants.
    path = FIXTURES / "bundle-v3-writer.py.txt"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "6787414419eb71f3405fe767b675d9a56bcea8130464f3c62bc772bdd7b58ed2"
    loader = importlib.machinery.SourceFileLoader("minimax_code._frozen_v3_writer", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    assert module.FORMAT_VERSION == 3 and module.SCHEMA_VERSION == 43
    return module


def legacy_v4_pdf_writer():
    """Frozen raw bundle4 source/enums; other utilities use today's runtime."""
    import minimax_code

    name = "minimax_code._frozen_v4_pdf"
    package = types.ModuleType(name)
    package.__path__ = [str(Path(minimax_code.__file__).parent)]
    package.__version__ = minimax_code.__version__
    sys.modules[name] = package
    for suffix, digest in (
        ("attachment_metadata", "8a98687cc48efe1a1365608c50fc7da984c96640d09e481cc80dbe88ddd1c1d2"),
        ("attachments", "209e6445742d660c91ba5e9c909641cca877ee87a42f65612ff2e4faa034ef39"),
        ("bundle", "abdabae16f1e68be5d5918e07c3d666d4541d0923b4856aaf001efdaf3da7e14"),
    ):
        label = "metadata" if suffix == "attachment_metadata" else "writer" if suffix == "bundle" else suffix
        path = FIXTURES / f"bundle-v4-{label}.py.txt"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        loader = importlib.machinery.SourceFileLoader(name + "." + suffix, str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = module
        loader.exec_module(module)
    assert module.FORMAT_VERSION == 4 and module.SCHEMA_VERSION == 44
    return module


async def test_pdf_v2_raw_bundle4_reader_verify_restore_and_new_strict_branch(tmp_path):
    pytest.importorskip("pypdf")
    from pdf_fixture import pdf_bytes

    data = tmp_path / "pdf-data"
    db = AsyncDatabase(data / "data.db")
    await db.connect()
    await db.migrate()
    await ProjectsDAO(db).create(id="pdf", name="PDF temporary test")
    service = AttachmentService(db, data)
    identity = (await service.list("pdf"))["service_id"]
    original = pdf_bytes(("Real PDF preview", "中文第二页"))
    actual = await service.upload("pdf", "paper.pdf", identity, stream(original))
    assert actual["parser_status"] == "parsed"
    template = dict(await db.fetchone("SELECT * FROM attachments WHERE id=?", (actual["id"],)))
    cases = [("parsed", "ok", "[page 1]\ntext\n", 0),
             ("empty", "no_extracted_body_text_no_ocr", "", 0),
             ("truncated", "preview_limit", "[page 1]\ncut\n", 1)]
    cases += [("parse_failed", reason, "", 0) for reason in (
        "invalid_package", "unsafe_package", "package_limit", "unsupported_feature",
        "parser_timeout", "parser_crash", "output_limit", "invalid_parser_output")]
    cases += [("parser_unavailable", reason, "", 0) for reason in (
        "missing_dependency", "limits_unavailable", "queue_timeout")]
    for number, (status, reason, preview, truncated) in enumerate(cases, 1):
        row = {**template, "id": f"att_{number:032x}", "snapshot_id": f"{number:032x}",
               "version": number + 1, "parser_status": status, "parser_reason": reason,
               "preview_text": preview, "preview_truncated": truncated}
        (service.vault / row["snapshot_id"]).write_bytes(original)
        keys = tuple(row)
        await db.execute(f"INSERT INTO attachments ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", tuple(row.values()))
    # Historical txt-with-PDF-bytes mismatch is also inert_text, but is not a
    # PDF-parser result and must not acquire the new PDF wire constraints.
    mismatch = await service.upload("pdf", "pretend.txt", identity, stream(original))
    assert mismatch["parser_status"] == "type_mismatch" and mismatch["parser_scope"] == "inert_text"
    before = [tuple(row) for row in await db.fetchall("SELECT * FROM attachments ORDER BY id")]
    await db.close()
    frozen = legacy_v4_pdf_writer()
    archive = tmp_path / "new-v4.zip"
    assert bundle.create_bundle(data, archive)["format_version"] == 4
    assert frozen.verify_bundle(archive)["schema_version"] == 44
    restored = tmp_path / "old-reader-restore"
    frozen.restore_bundle(archive, restored)
    with zipfile.ZipFile(archive) as packed:
        assert (restored / "data.db").read_bytes() == packed.read("data.db")
    conn = sqlite3.connect(restored / "data.db")
    try:
        assert conn.execute("SELECT * FROM attachments ORDER BY id").fetchall() == before
    finally:
        conn.close()
    assert all(path.read_bytes() == original for path in (restored / "attachment-vault").iterdir())
    old_archive = tmp_path / "raw-writer-v4.zip"
    frozen.create_bundle(data, old_archive)
    assert bundle.verify_bundle(old_archive)["schema_version"] == 44
    bundle.restore_bundle(old_archive, tmp_path / "new-reader-restore")
    for status, reason, preview, truncated in [
        ("parsed", "preview_limit", "[page 1]\nx", 0),
        ("parsed", "ok", "", 0),
        ("parse_failed", "invalid_package", "leaked document", 0),
        ("empty", "ok", "", 0),
    ]:
        conn = sqlite3.connect(data / "data.db")
        try:
            conn.execute("UPDATE attachments SET parser_status=?,parser_reason=?,preview_text=?,preview_truncated=? WHERE id=?",
                         (status, reason, preview, truncated, actual["id"]))
            conn.commit()
        finally:
            conn.close()
        # The frozen raw reader accepts these old broad combinations; only the
        # precise new PDF branch is tightened, without changing the data format.
        with pytest.raises(bundle.BundleError, match="Invalid PDF parser metadata"):
            bundle.create_bundle(data, tmp_path / f"invalid-{status}-{reason}-{len(preview)}.zip")


async def test_xlsx_exact_metadata_and_frozen_raw_bundle4_roundtrip_without_reparse(tmp_path, monkeypatch):
    pytest.importorskip("openpyxl")
    from xlsx_fixture import cell, worksheet, xlsx_bytes

    from minimax_code import attachment_parser, attachment_xlsx

    data = tmp_path / "xlsx-data"
    db = AsyncDatabase(data / "data.db")
    await db.connect()
    try:
        await db.migrate()
        await ProjectsDAO(db).create(id="xlsx", name="Stored XLSX values")
        service = AttachmentService(db, data)
        identity = (await service.list("xlsx"))["service_id"]
        original = xlsx_bytes([("Values", worksheet([(1, cell("A1", "999", formula="1+1"))]), "visible")])
        actual = await service.upload("xlsx", "cells.xlsx", identity, stream(original))
        assert actual["parser_status"] == "parsed"
        template = dict(await db.fetchone("SELECT * FROM attachments WHERE id=?", (actual["id"],)))
        cases = [("empty", "ok", "", 0), ("truncated", "preview_limit", '{"cell":"A1"}\n', 1)]
        cases += [("parse_failed", reason, "", 0) for reason in (
            "invalid_package", "unsafe_package", "package_limit", "unsupported_feature",
            "parser_timeout", "parser_crash", "output_limit", "invalid_parser_output")]
        cases += [("parser_unavailable", reason, "", 0) for reason in (
            "missing_dependency", "limits_unavailable", "queue_timeout")]
        for number, (status, reason, preview, truncated) in enumerate(cases, 1):
            row = {**template, "id": f"att_{number:032x}", "snapshot_id": f"{number:032x}",
                   "version": number + 1, "parser_status": status, "parser_reason": reason,
                   "preview_text": preview, "preview_truncated": truncated}
            (service.vault / row["snapshot_id"]).write_bytes(original)
            keys = tuple(row)
            await db.execute(f"INSERT INTO attachments ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", tuple(row.values()))
        mismatch = await service.upload("xlsx", "pretend.txt", identity, stream(original))
        assert mismatch["parser_status"] == "type_mismatch" and mismatch["parser_scope"] == "inert_text"
        before = [tuple(row) for row in await db.fetchall("SELECT * FROM attachments ORDER BY id")]
    finally:
        await db.close()
    frozen = legacy_v4_pdf_writer()  # The three immutable raw CRLF fixtures and old enums.
    def no_parse(*args, **kwargs):
        pytest.fail("Data-only bundle must not reparse an attachment")
    monkeypatch.setattr(attachment_xlsx, "parse_xlsx", no_parse)
    monkeypatch.setattr(attachment_parser, "inspect_xlsx", no_parse)
    original_import = builtins.__import__
    def no_parser_import(name, *args, **kwargs):
        if name.split(".")[0] in {"openpyxl", "defusedxml"} or name.endswith("attachment_xlsx"):
            pytest.fail("Data-only bundle must not import the XLSX parser or its dependencies")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_parser_import)
    archive = tmp_path / "new-xlsx-bundle4.zip"
    assert bundle.create_bundle(data, archive)["format_version"] == 4
    assert frozen.verify_bundle(archive)["schema_version"] == 44
    restored = tmp_path / "old-reader-xlsx"
    frozen.restore_bundle(archive, restored)
    with zipfile.ZipFile(archive) as packed:
        assert (restored / "data.db").read_bytes() == packed.read("data.db")
    conn = sqlite3.connect(restored / "data.db")
    try:
        assert conn.execute("SELECT * FROM attachments ORDER BY id").fetchall() == before
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (44,)
    finally:
        conn.close()
    assert all(path.read_bytes() == original for path in (restored / "attachment-vault").iterdir())
    old_archive = tmp_path / "raw-v4-xlsx.zip"
    frozen.create_bundle(data, old_archive)
    assert bundle.verify_bundle(old_archive)["schema_version"] == 44
    bundle.restore_bundle(old_archive, tmp_path / "current-reader-xlsx")
    for status, reason, preview in [("empty", "no_extracted_body_text_no_ocr", ""),
                                    ("parsed", "ok", ""), ("parse_failed", "invalid_package", "leak")]:
        conn = sqlite3.connect(data / "data.db")
        try:
            conn.execute("UPDATE attachments SET parser_status=?,parser_reason=?,preview_text=?,preview_truncated=0 WHERE id=?", (status, reason, preview, actual["id"]))
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(bundle.BundleError, match="Invalid XLSX parser metadata"):
            bundle.create_bundle(data, tmp_path / f"invalid-xlsx-{status}.zip")


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
        assert await db.migrate() == [43, 44]
        after = await db.fetchone("SELECT * FROM attachments")
        assert tuple(after)[:-2] == before
        assert after["parser_version"] == 1 and after["parser_reason"] == after["parser_scope"] == "legacy_v1"
        row, data = await AttachmentService(db, target).content("legacy-project", meta["attachment"]["id"])
        assert data == bytes.fromhex(meta["original_hex"])
        assert row["service_id"] == meta["attachment"]["service_id"]
    finally:
        await db.close()
    new = tmp_path / "v4.zip"
    assert bundle.create_bundle(target, new)["schema_version"] == 44
    with pytest.raises(ValueError):
        legacy_v2_writer().verify_bundle(new)
    assert bundle.verify_bundle(new)["format_version"] == 4


async def test_frozen_v3_export_restore_preserves_bytes_until_explicit_044(tmp_path, monkeypatch):
    import docx

    source = tmp_path / "schema043"
    db = AsyncDatabase(source / "data.db")
    await db.connect()
    discover = db_module.discover_migrations
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "discover_migrations", lambda applied: [
            (version, run) for version, run in discover(applied) if version <= 43])
        assert (await db.migrate())[-1] == 43
    project = tmp_path / "original-project"
    project.mkdir()
    await ProjectsDAO(db).create(id="v3", name="original", root_path=str(project))
    text = b"\xef\xbb\xbfFrozen v3 original\r\n"
    (project / "original.txt").write_bytes(text)
    artifact = await ArtifactService(db, source).register("v3", "original.txt")
    document = docx.Document()
    document.add_paragraph("Frozen schema043 DOCX body")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Frozen table"
    buffer = io.BytesIO()
    document.save(buffer)
    original_docx = buffer.getvalue()
    attachments = AttachmentService(db, source)
    identity = (await attachments.list("v3"))["service_id"]
    attachment = await attachments.upload("v3", "original.docx", identity, stream(original_docx))
    assert attachment["parser_version"] == 2
    await SessionsDAO(db).create(id="v3-session")
    run, _ = await AgentRunsDAO(db).admit_run(
        session_id="v3-session", request={"content": "offline fixture"}, title="fixture",
        metadata={"budget": {"limit_tokens": 60000}, "model_snapshot": {
            "engine": "native", "protocol": "anthropic", "provider_id": "builtin-minimax",
            "model": "MiniMax-M3"}})
    family = run["metadata"]["budget_family_id"]
    dao = BudgetsDAO(db)
    aid = await dao.reserve(family_id=family, run_id=run["id"], provider="builtin-minimax",
                            model="MiniMax-M3", estimate=100)
    await dao.sent(aid)
    await dao.settle(aid, {"prompt_tokens": 0, "completion_tokens": 10, "total_tokens": 10})
    await AgentRunsDAO(db).update_run_status(run["id"], status="completed")
    before_attempt = dict(await db.fetchone("SELECT * FROM budget_attempts WHERE id=?", (aid,)))
    before_attachment = dict(await db.fetchone("SELECT * FROM attachments"))
    await db.close()
    archive = tmp_path / "actual-v3.zip"
    old_writer = legacy_v3_writer()
    assert old_writer.create_bundle(source, archive)["format_version"] == 3
    assert old_writer.verify_bundle(archive)["schema_version"] == 43
    assert bundle.verify_bundle(archive)["format_version"] == 3
    with zipfile.ZipFile(archive) as exported:
        frozen_database = exported.read("data.db")
    database_digest = hashlib.sha256(frozen_database).hexdigest()

    async def never_migrate(*args):
        raise AssertionError("Verify/restore must not migrate historical data")

    target = tmp_path / "restored043"
    with monkeypatch.context() as patch:
        patch.setattr(AsyncDatabase, "migrate", never_migrate)
        bundle.restore_bundle(archive, target)
        assert bundle.verify_bundle(archive)["schema_version"] == 43
    assert hashlib.sha256((target / "data.db").read_bytes()).hexdigest() == database_digest
    assert (target / bundle.VAULT / artifact["snapshot_id"]).read_bytes() == text
    assert (target / bundle.ATTACHMENT_VAULT / attachment["snapshot_id"]).read_bytes() == original_docx
    with sqlite3.connect(target / "data.db") as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 43
        assert dict(conn.execute("SELECT * FROM budget_attempts WHERE id=?", (aid,)).fetchone()) == before_attempt
        assert dict(conn.execute("SELECT * FROM attachments").fetchone()) == before_attachment
        assert conn.execute("SELECT root_path FROM projects WHERE id='v3'").fetchone()[0] == str(project)
    conn.close()
    with pytest.raises(bundle.BundleError, match="Create requires known Schema044"):
        bundle.create_bundle(target, tmp_path / "must-not-upgrade.zip")
    assert hashlib.sha256((target / "data.db").read_bytes()).hexdigest() == database_digest
    db = AsyncDatabase(target / "data.db")
    await db.connect()
    try:
        assert await db.migrate() == [44]
        after = dict(await db.fetchone("SELECT * FROM budget_attempts WHERE id=?", (aid,)))
        assert after["state"] == "unknown" and after["reason"].startswith("migration 044:")
        assert {key: value for key, value in after.items() if key not in {"state", "reason"}} == {
            key: value for key, value in before_attempt.items() if key not in {"state", "reason"}}
        assert dict(await db.fetchone("SELECT * FROM attachments")) == before_attachment
    finally:
        await db.close()
    current = tmp_path / "current-v4.zip"
    assert bundle.create_bundle(target, current)["format_version"] == 4
    assert bundle.verify_bundle(current)["schema_version"] == 44
    with pytest.raises(ValueError):
        old_writer.verify_bundle(current)


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
        assert await db.migrate() == [42, 43, 44]
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
    assert report["format_version"] == 4 and report["schema_version"] == 44
    assert report["artifact_count"] == 1 and report["attachment_count"] == 2
    assert report["excluded_attachment_entries"] == {"orphan": 1, "pending": 1}
    assert cli("verify", "--bundle", archive)["attachment_count"] == 2
    for name, value in before.items():
        assert (data / name).read_bytes() == value
    with pytest.raises(ValueError):
        legacy_writer().verify_bundle(archive)  # Actual old reader fails closed on v2.
    with pytest.raises(ValueError):
        legacy_v2_writer().verify_bundle(archive)
    with pytest.raises(ValueError):
        legacy_v3_writer().verify_bundle(archive)
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
        manifest["format_version"] = 5
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
