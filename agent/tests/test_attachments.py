"""Real SQLite + bounded streamed originals + inert parsing and safe vault failure cases."""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import struct
import threading
import zipfile

import pytest

from minimax_code import attachments as module
from minimax_code.attachments import MAX_BYTES, AttachmentService, inspect_text
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.db import AsyncDatabase


@pytest.fixture
async def originals(tmp_path):
    db = AsyncDatabase(tmp_path / "data" / "data.db")
    await db.connect()
    await db.migrate()
    await ProjectsDAO(db).create(id="p", name="Project")
    await ProjectsDAO(db).create(id="other", name="Other")
    service = AttachmentService(db, db.path.parent)
    service_id = (await service.list("p"))["service_id"]
    yield service, db, service_id
    await db.close()


async def chunks(data):
    for start in range(0, len(data), 65536):
        yield data[start:start + 65536]


async def save(originals, data=b"original\r\n", name="report.txt"):
    service, _, identity = originals
    return await service.upload("p", name, identity, chunks(data))


async def test_original_versions_restart_display_paths_and_full_integrity(originals, tmp_path):
    service, db, _ = originals
    first_bytes = b"\xef\xbb\xbfOriginal\r\n<script>literal</script>\r\n"
    first = await save(originals, first_bytes, "../external/report.txt")
    second = await save(originals, b"new bytes\r\n", "../external/report.txt")
    assert first["version"] == 1 and second["version"] == 2
    assert first["snapshot_id"] != second["snapshot_id"]
    assert first["sha256"] == hashlib.sha256(first_bytes).hexdigest()
    assert first["quality_status"] == "unverified" and first["parser_status"] == "parsed"
    assert first["recognized_type"] == "utf8_text" and first["declared_type"] == "txt"
    assert "preview_text" not in first
    listed = await service.list("p")
    assert [row["version"] for row in listed["attachments"]] == [2, 1]
    assert all("preview_text" not in row for row in listed["attachments"])
    assert not (tmp_path / "external").exists()
    await db.close()
    await db.connect()
    restarted = AttachmentService(db, db.path.parent)
    assert (await restarted.content("p", first["id"]))[1] == first_bytes
    assert (await restarted.get("p", first["id"]))["preview_text"] == first_bytes.decode()
    with pytest.raises(ValueError, match="this project"):
        await restarted.get("other", first["id"])
    await ProjectsDAO(db).delete("p")
    assert len(await db.fetchall("SELECT * FROM attachments")) == 2
    assert (service.vault / first["snapshot_id"]).read_bytes() == first_bytes
    with pytest.raises(ValueError, match="existing project"):
        await restarted.get("p", first["id"])


@pytest.mark.parametrize("data,name,status", [
    (b"", "empty.txt", "empty"), (b"", "empty.csv", "empty"),
    (b"\xff\xfe", "bad.txt", "parse_failed"), (b"a\x00b", "nul.csv", "parse_failed"),
    (b'"unfinished', "bad.csv", "parse_failed"),
    (b"%PDF-1.7\nwrong parser", "paper.txt", "type_mismatch"),
    (b"plain text", "pretend.pdf", "type_mismatch"),
    (b"\x89PNG\r\n\x1a\nnot decoded", "image.png", "ocr_not_attempted"),
    (b"GIF89aimage", "image.jpg", "type_mismatch"),
    (b"plain unknown original", "file.bin", "parser_unavailable"),
])
async def test_explicit_parser_states_retain_original_without_false_read_success(originals, data, name, status):
    service, *_ = originals
    row = await save(originals, data, name)
    assert row["parser_status"] == status and row["quality_status"] == "unverified"
    assert (await service.content("p", row["id"]))[1] == data
    result = await service.get("p", row["id"])
    if status != "parsed":
        assert result["preview_text"] == ""
    assert result["attachment"]["preview_truncated"] is False


def office_bytes(kind):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "never parsed")
        archive.writestr("word/document.xml" if kind == "docx" else "xl/workbook.xml",
                         "<macro>never executed or extracted</macro>")
    return output.getvalue()


@pytest.mark.parametrize("kind", ["docx", "xlsx"])
async def test_office_candidate_is_not_proof_of_a_valid_parseable_package(originals, kind):
    row = await save(originals, office_bytes(kind), "document." + kind)
    assert row["recognized_type"] == kind and row["parser_status"] == "parse_failed"
    mismatch = await save(originals, office_bytes(kind), "document.txt")
    assert mismatch["parser_status"] == "type_mismatch"


def test_office_oversized_member_name_and_count_fail_before_zip_allocation(monkeypatch):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("x" * 513, "not decompressed")
    count = bytearray(office_bytes("docx"))
    struct.pack_into("<2H", count, len(count) - 22 + 8, 2049, 2049)
    def fail(*_args, **_kwargs):
        raise AssertionError("ZipFile allocation must not be reached")
    monkeypatch.setattr(module.zipfile, "ZipFile", fail)
    assert inspect_text(raw.getvalue(), "bad.docx")["parser_status"] == "type_mismatch"
    assert inspect_text(bytes(count), "broken.docx")["parser_status"] == "type_mismatch"


@pytest.mark.parametrize("data", ["汉" * 30_000, "row\r\n" * 3000], ids=["utf8-byte-bound", "line-bound"])
async def test_preview_truncation_utf8_boundary_and_line_bound_explicit(originals, data):
    service, *_ = originals
    raw = data.encode()
    row = await save(originals, raw)
    result = await service.get("p", row["id"])
    assert row["parser_status"] == "truncated" and row["preview_truncated"] is True
    assert len(result["preview_text"].encode()) <= module.MAX_PREVIEW_BYTES
    assert len(result["preview_text"].splitlines()) <= module.MAX_PREVIEW_LINES
    assert data.startswith(result["preview_text"]) and "\ufffd" not in result["preview_text"]
    assert (await service.content("p", row["id"]))[1] == raw


async def test_csv_formulas_are_literal_and_full_syntax_is_checked_after_preview_limit(originals):
    service, *_ = originals
    data = b'name,formula\r\nrow,"=2+3"\r\n'
    row = await save(originals, data, "data.csv")
    assert (await service.get("p", row["id"]))["preview_text"] == data.decode()
    bad = (b"a,b\n" * 20_000) + b'"unfinished'
    row = await save(originals, bad, "bad.csv")
    assert row["parser_status"] == "parse_failed" and not row["preview_truncated"]


async def test_limit_stream_interruption_timeout_and_no_partial_publication(originals, monkeypatch):
    service, db, identity = originals
    with pytest.raises(OverflowError):
        await save(originals, b"x" * (MAX_BYTES + 1))
    async def interrupted():
        yield b"first"
        raise ConnectionError("stream interrupted")
    with pytest.raises(ConnectionError):
        await service.upload("p", "interrupted.txt", identity, interrupted())
    monkeypatch.setattr(module, "UPLOAD_TIMEOUT", 0.02)
    async def slow():
        yield b"first"
        await asyncio.sleep(1)
    with pytest.raises(TimeoutError):
        await service.upload("p", "slow.txt", identity, slow())
    assert not await db.fetchall("SELECT * FROM attachments")
    assert not list(service.vault.iterdir())


async def test_cancel_drains_owned_worker_before_pending_cleanup(originals, monkeypatch):
    service, db, identity = originals
    started, release = threading.Event(), threading.Event()
    write = module._Upload.write
    def delayed(self, value):
        write(self, value)
        started.set()
        assert release.wait(3)
    monkeypatch.setattr(module._Upload, "write", delayed)
    task = asyncio.create_task(service.upload("p", "cancel.txt", identity, chunks(b"owned")))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done() and list(service.vault.glob(".pending-*"))
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(service.vault.iterdir()) and not await db.fetchall("SELECT * FROM attachments")


@pytest.mark.parametrize("phase", ["write", "seal", "publish"])
async def test_repeated_cancel_waits_for_actual_worker_before_close(originals, monkeypatch, phase):
    service, db, identity = originals
    started, release, closing = threading.Event(), threading.Event(), threading.Event()
    operation, close = getattr(module._Upload, phase), module._Upload.close
    owner = []
    def blocked(self, *args):
        owner.append(self)
        started.set()
        assert release.wait(3)
        return operation(self, *args)
    def observed_close(self):
        closing.set()
        close(self)
    monkeypatch.setattr(module._Upload, phase, blocked)
    monkeypatch.setattr(module._Upload, "close", observed_close)
    task = asyncio.create_task(service.upload("p", "twice.txt", identity, chunks(b"owned")))
    assert await asyncio.to_thread(started.wait, 2)
    try:
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done() and not closing.is_set()
        assert list(service.vault.glob(".pending-*"))
        if phase != "publish":
            assert os.fstat(owner[0].fd).st_size >= 0
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closing.is_set() and not list(service.vault.glob(".pending-*"))
    assert not await db.fetchall("SELECT * FROM attachments")
    # Cancellation during atomic publication can retain a complete unregistered
    # original; it can never expose a half-written available DB record.
    assert [path.read_bytes() for path in service.vault.iterdir()] == ([b"owned"] if phase == "publish" else [])


async def test_repeated_cancel_during_cleanup_drains_close_worker(originals, monkeypatch):
    service, db, identity = originals
    started, release = threading.Event(), threading.Event()
    close = module._Upload.close
    def blocked_close(self):
        started.set()
        assert release.wait(3)
        close(self)
    monkeypatch.setattr(module._Upload, "close", blocked_close)
    async def interrupted():
        yield b"owned"
        raise ConnectionError("interrupted")
    task = asyncio.create_task(service.upload("p", "cleanup.txt", identity, interrupted()))
    assert await asyncio.to_thread(started.wait, 2)
    try:
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done() and list(service.vault.glob(".pending-*"))
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(service.vault.iterdir()) and not await db.fetchall("SELECT * FROM attachments")


@pytest.mark.parametrize("change", ["service", "project"])
async def test_transaction_authority_recheck_no_available_record(originals, change):
    service, db, identity = originals
    async def changed():
        yield b"first"
        if change == "service":
            await db.execute("UPDATE artifact_service SET service_id=?", ("svc_" + "1" * 32,))
        else:
            await db.execute("UPDATE projects SET root_path='changed' WHERE id='p'")
        yield b"last"
    with pytest.raises(ValueError, match="changed"):
        await service.upload("p", "binding.txt", identity, changed())
    assert not await db.fetchall("SELECT * FROM attachments")
    assert not list(service.vault.glob(".pending-*"))


@pytest.mark.parametrize("name", ["", " ", "x\r\nInject.txt", "x\x00.txt", "x" * 241])
async def test_invalid_display_names_fail_before_vault(originals, name):
    service, *_ = originals
    with pytest.raises(ValueError):
        await save(originals, name=name)
    assert not service.vault.exists()


@pytest.mark.parametrize("kind", ["missing", "corrupt", "hardlink", "symlink"])
async def test_missing_corrupt_or_aliased_vault_never_falls_back(originals, tmp_path, kind):
    service, *_ = originals
    row = await save(originals)
    path = service.vault / row["snapshot_id"]
    if kind == "missing":
        path.unlink()
    elif kind == "corrupt":
        path.write_bytes(b"bad")
    elif kind == "hardlink":
        os.link(path, tmp_path / "alias")
    else:
        path.unlink()
        (tmp_path / "canary").write_bytes(b"external")
        try:
            path.symlink_to(tmp_path / "canary")
        except OSError:
            pytest.skip("OS account cannot create symbolic links")
    with pytest.raises(ValueError, match="missing or corrupt"):
        await service.get("p", row["id"])
