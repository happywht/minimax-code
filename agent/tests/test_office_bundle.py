"""Strict046 receipts and byte-preserving format6/old1..5 data-only bundles."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import sqlite3
import zipfile

import pytest
from test_attachment_bundle import (
    legacy_v2_writer,
    legacy_v3_writer,
    legacy_v4_pdf_writer,
    legacy_v5_writer,
    legacy_v6_writer,
    legacy_writer,
)
from xlsx_fixture import cell, worksheet, xlsx_bytes

from minimax_code import office_metadata
bundle = legacy_v6_writer()
from minimax_code.storage import db as db_module
from minimax_code.storage.db import AsyncDatabase, Database

STAMP = "2026-10-29T10:00:00+00:00"
SERVICE = "svc_" + "1" * 32
ATTACHMENT = "att_" + "2" * 32
OFFICE = "off_" + "3" * 32
migration = importlib.import_module("minimax_code.storage.migrations.046_office_deliveries")


@pytest.fixture(autouse=True)
def schema046_only(monkeypatch):
    discover = db_module.discover_migrations
    monkeypatch.setattr(db_module, "discover_migrations", lambda applied: [
        (number, run) for number, run in discover(applied) if number <= 46])


def report(**changes):
    return {
        "version": 1, "scope": "xlsx_plain_cells_v1", "input_sha256": "4" * 64,
        "file_sha256": "5" * 64, "size_bytes": 10, "sheet_name": "清单",
        "data_rows": 1, "column_count": 2, "cell_count": 4, "formula_count": 0,
        "external_relationship_count": 0, "macro_count": 0, "structure_status": "passed",
        "business_quality": "unverified", "render_status": "not_attempted",
        "writer": {"name": "openpyxl", "version": "3.1.5"}, "validator_version": 1,
        **changes,
    }


def record(receipt=None, **changes):
    return {
        "id": OFFICE, "project_id": "project", "service_id": SERVICE,
        "attachment_id": ATTACHMENT, "run_id": "historical-run", "request_key": "request-1",
        "request_hash": "6" * 64, "input_sha256": "4" * 64, "writer_version": "3.1.5",
        "validator_version": 1, "validation_report": json.dumps(receipt or report()),
        "created_at": STAMP, **changes,
    }


def attachment():
    return {"id": ATTACHMENT, "project_id": "project", "service_id": SERVICE,
            "sha256": "5" * 64, "size_bytes": 10, "declared_type": "xlsx",
            "recognized_type": "xlsx", "other_attachment_fields": "allowed"}


def insert(db, table, row):
    keys = tuple(row)
    db.execute(f"INSERT INTO {table}({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",
               tuple(row.values()))


def historical(path, version, monkeypatch):
    discover = db_module.discover_migrations
    db = Database(path)
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "discover_migrations", lambda applied: [
            (number, run) for number, run in discover(applied) if number <= version])
        assert db.migrate()[-1] == version
    return db


def seed(db, *, office=False):
    """Raw historical metadata fixtures; no generator/parser/service is used."""
    version = max(db.applied_versions())
    db.execute("INSERT INTO artifact_service VALUES(1,?)", (SERVICE,))
    text = b"original text\r\n"
    snapshot = "7" * 32
    (db.path.parent / bundle.VAULT).mkdir(mode=0o700)
    (db.path.parent / bundle.VAULT / snapshot).write_bytes(text)
    insert(db, "text_artifacts", {
        "id": "art_" + "8" * 32, "project_id": "project", "run_id": "historical-run",
        "service_id": SERVICE, "source_path": "original.txt", "snapshot_id": snapshot,
        "mime_type": "text/plain", "size_bytes": len(text), "sha256": hashlib.sha256(text).hexdigest(),
        "version": 1, "parse_status": "parseable", "verification_status": "unverified",
        "created_at": STAMP,
    })
    if version < 42:
        return
    vault = db.path.parent / bundle.ATTACHMENT_VAULT
    vault.mkdir(mode=0o700)
    workbook = xlsx_bytes([("清单", worksheet([
        (1, cell("A1", "材料", kind="inlineStr") + cell("B1", "数量", kind="inlineStr")),
        (2, cell("A2", "钢材", kind="inlineStr") + cell("B2", "12")),
    ], dimension="A1:B2"), "visible")])
    for number, name, payload, kind in (
        (2, "清单.xlsx", workbook, "xlsx"), (9, "ordinary.txt", b"ordinary original", "txt"),
    ):
        row = {
            "id": f"att_{str(number) * 32}", "project_id": "project", "service_id": SERVICE,
            "snapshot_id": str(number) * 32, "original_name": name, "declared_type": kind,
            "recognized_type": kind, "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(), "version": 1,
            "parser_status": "parsed", "parser_version": 1, "preview_text": "legacy preview",
            "preview_truncated": 0, "quality_status": "unverified", "created_at": STAMP,
        }
        if version >= 43:
            row.update(parser_reason="legacy_v1", parser_scope="legacy_v1")
        insert(db, "attachments", row)
        (vault / row["snapshot_id"]).write_bytes(payload)
    if office:
        insert(db, "office_deliveries", record(report(
            file_sha256=hashlib.sha256(workbook).hexdigest(), size_bytes=len(workbook))))


@pytest.fixture
def current(tmp_path, monkeypatch):
    db = historical(tmp_path / "source" / "data.db", 46, monkeypatch)
    seed(db, office=True)
    db.close()
    return db.path.parent


def members(path):
    with zipfile.ZipFile(path) as archive:
        return {item.filename: archive.read(item) for item in archive.infolist()}


def rewrite(source, target, *, manifest_edit=None, database_edit=None):
    payloads = members(source)
    manifest = json.loads(payloads["manifest.json"])
    if database_edit:
        database = target.with_suffix(".db")
        database.write_bytes(payloads["data.db"])
        with sqlite3.connect(database) as conn:
            database_edit(conn)
        payloads["data.db"] = database.read_bytes()
        manifest["database"] = {"size_bytes": len(payloads["data.db"]),
                                "sha256": hashlib.sha256(payloads["data.db"]).hexdigest()}
    if manifest_edit:
        manifest_edit(manifest)
    payloads["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in payloads.items():
            archive.writestr(name, data)


def test_report_and_record_return_contract_and_strict_attachment_binding():
    receipt = report()
    assert office_metadata.validate_report(receipt) is receipt
    row = record(receipt)
    original = copy.deepcopy(row)
    decoded = office_metadata.validate_record(row, attachment())
    assert decoded is not row and decoded["validation_report"] == receipt
    assert row == original and type(row["validation_report"]) is str
    assert office_metadata.validate_record(record(run_id=None))["run_id"] is None
    assert office_metadata.validate_report(report(data_rows=0, cell_count=2))["data_rows"] == 0
    assert office_metadata.validate_report(report(size_bytes=2097152))["size_bytes"] == 2097152
    assert office_metadata.validate_report(report(
        data_rows=249, column_count=20, cell_count=5000))["cell_count"] == 5000


@pytest.mark.parametrize("key,value", [
    ("version", True), ("version", 2), ("scope", "xlsx_plain_cells_v2"),
    ("input_sha256", "A" * 64), ("file_sha256", "0" * 63),
    ("size_bytes", 0), ("size_bytes", 2097153), ("size_bytes", True),
    ("sheet_name", ""), ("sheet_name", "s" * 32), ("sheet_name", "\ud800"),
    ("sheet_name", "[]"), ("sheet_name", "'name"), ("sheet_name", "name'"),
    ("sheet_name", "   "), ("sheet_name", "bad\nname"), ("sheet_name", "bad\uffffname"),
    ("data_rows", -1), ("data_rows", 1001), ("data_rows", 1.0),
    ("column_count", 0), ("column_count", 21), ("column_count", False),
    ("cell_count", 3), ("cell_count", 5001), ("cell_count", 4.0),
    ("formula_count", 1), ("formula_count", False), ("external_relationship_count", 1),
    ("macro_count", 1), ("structure_status", "failed"), ("business_quality", "verified"),
    ("render_status", "passed"), ("validator_version", 2), ("validator_version", True),
    ("writer", {"name": "openpyxl", "version": "3.1.6"}),
    ("writer", {"name": "openpyxl", "version": "3.1.5", "extra": None}),
    ("writer", []), ("data_rows", float("nan")), ("size_bytes", float("inf")),
])
def test_report_rejects_types_bounds_future_and_claims(key, value):
    with pytest.raises(ValueError):
        office_metadata.validate_report(report(**{key: value}))


@pytest.mark.parametrize("value", [None, [], {}, {**report(), "preview_truncated": False},
                                  {key: value for key, value in report().items() if key != "scope"}])
def test_report_requires_exact_fields(value):
    with pytest.raises(ValueError):
        office_metadata.validate_report(value)


@pytest.mark.parametrize("key,value", [
    ("id", "off_bad"), ("project_id", ""), ("project_id", 1), ("project_id", "x" * 129),
    ("service_id", "svc_bad"), ("attachment_id", "att_bad"), ("run_id", ""),
    ("run_id", False), ("run_id", "x" * 129), ("request_key", ""),
    ("request_key", "x" * 129), ("request_hash", "broken"), ("input_sha256", "a" * 64),
    ("request_key", "with spaces"), ("request_key", "with/slash"), ("request_key", "中文"),
    ("writer_version", "3.1.6"), ("validator_version", True), ("validator_version", 2),
    ("created_at", ""), ("created_at", "x" * 101), ("validation_report", report()),
])
def test_record_rejects_invalid_types_versions_and_input_hash(key, value):
    with pytest.raises(ValueError):
        office_metadata.validate_record(record(**{key: value}))


@pytest.mark.parametrize("raw", [
    "{", "null", "[]", '{"version":1,"version":1}',
    json.dumps(report()).replace('"name": "openpyxl"', '"name":"openpyxl","name":"openpyxl"'),
    json.dumps(report()).replace('"data_rows": 1', '"data_rows": NaN'),
    json.dumps(report()).replace('"size_bytes": 10', '"size_bytes": Infinity'),
    " " * 65537 + json.dumps(report()), "\ud800", "[" * 2000 + "]" * 2000,
], ids=["syntax", "null", "array", "duplicate", "nested-duplicate", "nan", "infinity",
        "oversize", "surrogate", "depth"])
def test_record_rejects_invalid_duplicate_nonfinite_oversize_json(raw):
    with pytest.raises(ValueError):
        office_metadata.validate_record(record(validation_report=raw))


def test_record_requires_exact_fields_and_report_byte_limit():
    row = record()
    for bad in ({**row, "origin": "operator_generated"},
                {key: value for key, value in row.items() if key != "run_id"}, []):
        with pytest.raises(ValueError):
            office_metadata.validate_record(bad)
    text = row["validation_report"]
    padded = text + " " * (65536 - len(text.encode()))
    assert office_metadata.validate_record(record(validation_report=padded))
    with pytest.raises(ValueError):
        office_metadata.validate_record(record(validation_report=padded + " "))


@pytest.mark.parametrize("key,value", [
    ("id", "att_" + "9" * 32), ("project_id", "elsewhere"),
    ("service_id", "svc_" + "9" * 32), ("sha256", "9" * 64),
    ("size_bytes", 11), ("size_bytes", 10.0), ("declared_type", "txt"),
    ("recognized_type", "zip"),
])
def test_record_rejects_attachment_mismatch(key, value):
    with pytest.raises(ValueError, match="attachment binding"):
        office_metadata.validate_record(record(), {**attachment(), key: value})


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_045_to_046_only_adds_receipt_table_and_index(tmp_path, monkeypatch, asynchronous):
    db = historical(tmp_path / "source" / "data.db", 45, monkeypatch)
    seed(db)
    before = [tuple(row) for row in db.fetchall("SELECT * FROM sqlite_schema ORDER BY type,name")]
    values = {name: [tuple(row) for row in db.fetchall(f"SELECT * FROM {name}")]
              for name in ("text_artifacts", "attachments", "mcp_servers")}
    trace = []
    if asynchronous:
        active = AsyncDatabase(db.path)
        await active.connect()
        try:
            await active._conn.set_trace_callback(trace.append)
            assert await active.migrate() == [46]
            assert await active.migrate() == []
        finally:
            await active.close()
    else:
        db.raw_connection.set_trace_callback(trace.append)
        assert db.migrate() == [46]
        assert db.migrate() == []
        db.raw_connection.set_trace_callback(None)
    after = [tuple(row) for row in db.fetchall("SELECT * FROM sqlite_schema ORDER BY type,name")]
    assert all(row in after for row in before)
    additions = [row for row in after if row not in before]
    assert len(additions) == 5 and all(row[2] == "office_deliveries" for row in additions)
    assert [row[1] for row in db.fetchall("PRAGMA table_info(office_deliveries)")] == list(
        office_metadata.RECORD_KEYS)
    assert [row[2] for row in db.fetchall("PRAGMA index_info(idx_office_deliveries_project)")] == [
        "project_id", "created_at", "id"]
    assert not db.fetchall("PRAGMA foreign_key_list(office_deliveries)")
    assert not any(sql.lstrip().upper().startswith(("DROP ", "ALTER ", "UPDATE ")) for sql in trace)
    for name, rows in values.items():
        assert [tuple(row) for row in db.fetchall(f"SELECT * FROM {name}")] == rows
    insert(db, "office_deliveries", record())
    for changed in ({"id": OFFICE}, {"id": "off_" + "a" * 32},
                    {"id": "off_" + "b" * 32, "attachment_id": "att_" + "b" * 32},
                    {"id": "off_" + "c" * 32, "request_key": "new", "validator_version": 2}):
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "office_deliveries", record(**changed))
    assert db.fetchone("PRAGMA foreign_keys")[0] == 1
    db.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("point", ["CREATE TABLE", "CREATE INDEX"])
async def test_046_failure_rolls_back_ddl_and_version(tmp_path, monkeypatch, asynchronous, point):
    db = historical(tmp_path / "source" / "data.db", 45, monkeypatch)
    seed(db)
    before = [tuple(row) for row in db.fetchall("SELECT * FROM sqlite_schema ORDER BY type,name")]
    original = migration.run

    def fail(connection):
        class Fault:
            def execute(self, sql, *args):
                value = connection.execute(sql, *args)
                if sql.startswith(point):
                    raise RuntimeError("injected046 failure")
                return value
        original(Fault())

    monkeypatch.setattr(migration, "run", fail)
    if asynchronous:
        active = AsyncDatabase(db.path)
        await active.connect()
        try:
            with pytest.raises(RuntimeError, match="injected046"):
                await active.migrate()
        finally:
            await active.close()
    else:
        with pytest.raises(RuntimeError, match="injected046"):
            db.migrate()
    assert 46 not in db.applied_versions()
    assert [tuple(row) for row in db.fetchall("SELECT * FROM sqlite_schema ORDER BY type,name")] == before
    assert db.fetchone("PRAGMA foreign_keys")[0] == 1
    monkeypatch.setattr(migration, "run", original)
    assert db.migrate() == [46]
    db.close()


def test_bundle6_exact_restore_no_migration_generation_or_parsing(current, tmp_path, monkeypatch):
    from minimax_code import attachment_xlsx, office_process, office_xlsx

    original = {str(path.relative_to(current)): path.read_bytes()
                for path in current.rglob("*") if path.is_file()}

    def never(*args, **kwargs):
        pytest.fail("A data-only operation invoked migration/generation/Office validation/parsing")

    for owner, names in ((Database, ("migrate",)), (AsyncDatabase, ("migrate",)),
                         (office_xlsx, ("generate", "verify")),
                         (office_process, ("generate_xlsx",)), (attachment_xlsx, ("parse_xlsx",))):
        for name in names:
            monkeypatch.setattr(owner, name, never)
    archive = tmp_path / "current.zip"
    assert bundle.SUPPORTED_SCHEMAS == {1: 41, 2: 42, 3: 43, 4: 44, 5: 45, 6: 46}
    result = bundle.create_bundle(current, archive)
    assert result["format_version"] == 6 and result["schema_version"] == 46
    assert result["office_delivery_count"] == 1
    packed = members(archive)
    manifest = json.loads(packed["manifest.json"])
    assert len(manifest["office_deliveries"]) == 1
    assert len(packed) == 5  # manifest, DB, one text, two attachments; no extra Office bytes.
    total = manifest["database"]["size_bytes"] + sum(
        row["size_bytes"] for key in ("artifacts", "attachments") for row in manifest[key])
    with monkeypatch.context() as patch:
        patch.setattr(bundle, "MAX_TOTAL_BYTES", total)
        assert bundle._manifest(packed["manifest.json"]) == manifest
        patch.setattr(bundle, "MAX_TOTAL_BYTES", total - 1)
        with pytest.raises(bundle.BundleError, match="shared total"):
            bundle._manifest(packed["manifest.json"])
    assert bundle.verify_bundle(archive)["office_delivery_count"] == 1
    target = tmp_path / "restored"
    assert bundle.restore_bundle(archive, target)["office_delivery_count"] == 1
    for name, payload in packed.items():
        if name != "manifest.json":
            assert (target / name).read_bytes() == payload
    with Database(target / "data.db") as db:
        assert max(db.applied_versions()) == 46
        assert db.fetchone("SELECT run_id FROM office_deliveries")[0] == "historical-run"
    for name, payload in original.items():
        assert (current / name).read_bytes() == payload
    assert bundle.create_bundle(target, tmp_path / "reexport.zip")["format_version"] == 6
    frozen = legacy_v5_writer()
    for function, args in ((frozen.verify_bundle, (archive,)),
                           (frozen.restore_bundle, (archive, tmp_path / "old-rejected")),
                           (frozen.create_bundle, (current, tmp_path / "old-export.zip"))):
        with pytest.raises(ValueError):
            function(*args)
    assert not (tmp_path / "old-rejected").exists()


@pytest.mark.parametrize("version,writer", [(41, legacy_writer), (42, legacy_v2_writer),
                                           (43, legacy_v3_writer), (44, legacy_v4_pdf_writer),
                                           (45, legacy_v5_writer)])
def test_old1_to_5_frozen_raw_writers_interoperate_at_original_schema(
    tmp_path, monkeypatch, version, writer,
):
    db = historical(tmp_path / "old" / "data.db", version, monkeypatch)
    seed(db)
    db.close()
    frozen = writer()
    archive = tmp_path / "historical.zip"
    assert frozen.create_bundle(db.path.parent, archive)["schema_version"] == version
    assert bundle.verify_bundle(archive)["schema_version"] == version
    assert "office_deliveries" not in json.loads(members(archive)["manifest.json"])
    target = tmp_path / "restored"
    bundle.restore_bundle(archive, target)
    for name, payload in members(archive).items():
        if name != "manifest.json":
            assert (target / name).read_bytes() == payload
    with Database(target / "data.db") as clone:
        assert max(clone.applied_versions()) == version
    reexport = tmp_path / "same-version.zip"
    assert frozen.create_bundle(target, reexport)["schema_version"] == version
    assert frozen.verify_bundle(reexport)["schema_version"] == version
    assert bundle.verify_bundle(reexport)["schema_version"] == version
    with pytest.raises(bundle.BundleError, match="Create requires known Schema046"):
        bundle.create_bundle(target, tmp_path / "not-current.zip")


def test_empty_office_inventory_is_required_only_in_format6(tmp_path, monkeypatch):
    db = historical(tmp_path / "empty" / "data.db", 46, monkeypatch)
    db.close()
    archive = tmp_path / "empty.zip"
    assert bundle.create_bundle(db.path.parent, archive)["office_delivery_count"] == 0
    manifest = json.loads(members(archive)["manifest.json"])
    assert manifest["office_deliveries"] == []
    manifest.pop("office_deliveries")
    with pytest.raises(bundle.BundleError, match="manifest fields"):
        bundle._manifest(json.dumps(manifest).encode())
    manifest.update(format_version=5, schema_version=45, office_deliveries=[])
    with pytest.raises(bundle.BundleError, match="manifest fields"):
        bundle._manifest(json.dumps(manifest).encode())


@pytest.mark.parametrize("format_version,schema_version", [
    (7, 47), (7, 46), (6, 47), (6, 45), (5, 46), (5, 45), (True, 46), (6, True),
])
def test_future_mismatch_and_old_format_extra_inventory_rejected(
    current, tmp_path, format_version, schema_version,
):
    archive, bad = tmp_path / "good.zip", tmp_path / "bad.zip"
    bundle.create_bundle(current, archive)
    rewrite(archive, bad, manifest_edit=lambda value: value.update(
        format_version=format_version, schema_version=schema_version))
    with pytest.raises(bundle.BundleError):
        bundle.verify_bundle(bad)
    with pytest.raises(bundle.BundleError):
        bundle.restore_bundle(bad, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("sql,args", [
    ("UPDATE office_deliveries SET project_id=?", ("different-project",)),
    ("UPDATE office_deliveries SET attachment_id=?", ("att_" + "9" * 32,)),
    ("UPDATE office_deliveries SET attachment_id=?", ("att_" + "a" * 32,)),
    ("UPDATE office_deliveries SET service_id=?", ("svc_" + "9" * 32,)),
    ("UPDATE office_deliveries SET input_sha256=?", ("a" * 64,)),
    ("UPDATE office_deliveries SET validation_report=?", (json.dumps(report(macro_count=1)),)),
    ("UPDATE office_deliveries SET validation_report=?", (" " * 65537 + json.dumps(report()),)),
    ("UPDATE attachments SET declared_type='txt' WHERE id=?", (ATTACHMENT,)),
    ("UPDATE attachments SET recognized_type='zip' WHERE id=?", (ATTACHMENT,)),
    ("ALTER TABLE office_deliveries ADD COLUMN future TEXT", ()),
    ("DROP TABLE office_deliveries", ()),
    ("INSERT INTO schema_migrations VALUES(47,?)", (STAMP,)),
])
def test_tampered_database_office_metadata_or_reference_rejected(current, tmp_path, sql, args):
    archive, bad = tmp_path / "good.zip", tmp_path / "bad.zip"
    bundle.create_bundle(current, archive)
    rewrite(archive, bad, database_edit=lambda conn: conn.execute(sql, args))
    with pytest.raises(bundle.BundleError):
        bundle.verify_bundle(bad)
    with pytest.raises(bundle.BundleError):
        bundle.restore_bundle(bad, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("change", ["report", "hash", "size", "attachment", "project", "duplicate"])
def test_tampered_manifest_office_report_or_reference_rejected(current, tmp_path, change):
    archive, bad = tmp_path / "good.zip", tmp_path / "bad.zip"
    bundle.create_bundle(current, archive)

    def alter(value):
        row = value["office_deliveries"][0]
        if change == "report":
            row["validation_report"]["business_quality"] = "verified"
        elif change == "hash":
            row["validation_report"]["file_sha256"] = "a" * 64
        elif change == "size":
            row["validation_report"]["size_bytes"] += 1
        elif change == "attachment":
            row["attachment_id"] = "att_" + "9" * 32
        elif change == "project":
            row["project_id"] = "different-project"
        else:
            value["office_deliveries"].append(copy.deepcopy(row))

    rewrite(archive, bad, manifest_edit=alter)
    with pytest.raises(bundle.BundleError):
        bundle.verify_bundle(bad)
    with pytest.raises(bundle.BundleError):
        bundle.restore_bundle(bad, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()
