"""Immutable uploaded originals with bounded inert text inspection.

Names are display metadata, never filesystem paths. Parsing is not quality
verification, Office execution, OCR, context injection, or a delivery receipt.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import os
import re
import stat
import struct
import threading
import uuid
import zipfile
from contextlib import ExitStack
from pathlib import Path

from .storage.dao.attachments import AttachmentDAO, metadata
from .text_artifacts import ArtifactService, _identity, _regular, _safe_open

MAX_BYTES = 10 * 1024 * 1024
MAX_PREVIEW_BYTES = 64 * 1024
MAX_PREVIEW_LINES = 2000
UPLOAD_TIMEOUT = 120
PARSER_VERSION = 2
VAULT = "attachment-vault"
STATUSES = {"parsed", "empty", "truncated", "parse_failed", "type_mismatch",
            "parser_unavailable", "ocr_not_attempted"}
DECLARED_TYPES = {"txt", "csv", "pdf", "docx", "xlsx", "png", "jpeg", "gif", "webp", "unknown"}
RECOGNIZED_TYPES = DECLARED_TYPES | {"utf8_text", "zip", "binary"}
_ID = re.compile(r"att_[0-9a-f]{32}\Z")
_SNAPSHOT = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_PARSE_GATE = threading.BoundedSemaphore(2)


def display_name(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 240 or not value.strip()
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)):
        raise ValueError("Filename must be 1–240 display characters without controls")
    # Bound encoded headers/UI as well as code points; reject lone surrogates.
    if len(value.encode("utf-8")) > 960:
        raise ValueError("Filename exceeds its encoded display limit")
    return value


def declared_type(name):
    # Separators affect this display label only; no Path operation opens it.
    leaf = name.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = leaf.rsplit(".", 1)[-1].lower() if "." in leaf else ""
    suffix = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
    return suffix if suffix in DECLARED_TYPES else "unknown"


def download_name(name):
    return re.sub(r'[\\/:*?"<>|]', "_", display_name(name))[:240]


def _office_type(data):
    # Bound the central directory BEFORE ZipFile can allocate member objects.
    tail = data[-65557:]
    index = tail.rfind(b"PK\x05\x06")
    if index < 0 or len(tail) - index < 22:
        return "zip"
    signature, disk, start, disk_count, count, size, offset, comment = struct.unpack(
        "<4s4H2IH", tail[index:index + 22])
    if (disk or start or disk_count != count or count > 2048 or size > 1024 * 1024
            or len(tail) - index != 22 + comment or offset + size != len(data) - len(tail) + index):
        return "zip"
    central = memoryview(data)[offset:offset + size]
    cursor = 0
    for _ in range(count):
        if cursor + 46 > len(central) or central[cursor:cursor + 4] != b"PK\x01\x02":
            return "zip"
        name, extra, annotation = struct.unpack_from("<3H", central, cursor + 28)
        if name > 512 or extra > 1024 or annotation > 1024:
            return "zip"
        cursor += 46 + name + extra + annotation
    if cursor != len(central):
        return "zip"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = {row.filename for row in archive.infolist()}
            if "[Content_Types].xml" not in names:
                return "zip"
            word, sheet = "word/document.xml" in names, "xl/workbook.xml" in names
            return "docx" if word and not sheet else "xlsx" if sheet and not word else "zip"
    except (ValueError, zipfile.BadZipFile):
        return "zip"


def recognize(data):
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"PK\x03\x04"):
        return _office_type(data)
    try:
        data.decode("utf-8", errors="strict")
        return "utf8_text"
    except UnicodeDecodeError:
        return "binary"


def inspect_text(data, name):
    declared, recognized = declared_type(name), recognize(data)
    result = {"declared_type": declared, "recognized_type": recognized,
              "parser_version": PARSER_VERSION, "preview_text": "", "preview_truncated": 0,
              "quality_status": "unverified", "parser_reason": "unsupported_type",
              "parser_scope": "inert_text" if declared in {"txt", "csv"} else "original_only"}
    if declared in {"txt", "csv"}:
        if recognized not in {"utf8_text", "binary"}:
            return {**result, "parser_status": "type_mismatch", "parser_reason": "type_mismatch"}
        try:
            text = data.decode("utf-8", errors="strict")
            if "\x00" in text:
                raise ValueError("NUL is not supported text")
            if declared == "csv":
                # Iterate rows; do not build a sheet, follow links or evaluate formulas.
                for _ in csv.reader(io.StringIO(text, newline=""), strict=True):
                    pass
        except (UnicodeDecodeError, ValueError, csv.Error):
            return {**result, "parser_status": "parse_failed", "parser_reason": "invalid_text"}
        prefix = text.encode("utf-8")[:MAX_PREVIEW_BYTES].decode("utf-8", errors="ignore")
        lines = prefix.splitlines(keepends=True)
        prefix = "".join(lines[:MAX_PREVIEW_LINES])
        truncated = prefix != text
        return {**result, "preview_text": prefix, "preview_truncated": int(truncated),
                "parser_reason": "preview_limit" if truncated else "ok",
                "parser_status": "empty" if not text else "truncated" if truncated else "parsed"}
    if declared != "unknown" and recognized != declared:
        return {**result, "parser_status": "type_mismatch", "parser_reason": "type_mismatch"}
    return {**result, "parser_reason": "ocr_not_attempted" if recognized in
            {"png", "jpeg", "gif", "webp"} else "unsupported_type",
            "parser_status": "ocr_not_attempted" if recognized in
            {"png", "jpeg", "gif", "webp"} else "parser_unavailable"}


def read_original(path):
    with _safe_open(path) as fd:
        before = os.fstat(fd)
        _regular(before)
        if hasattr(os, "getuid") and before.st_uid != os.getuid():
            raise ValueError("Attachment must belong to this operator")
        if before.st_size > MAX_BYTES:
            raise ValueError("Attachment exceeds 10 MiB")
        chunks, total = [], 0
        while chunk := os.read(fd, min(65536, MAX_BYTES + 1 - total)):
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError("Attachment exceeds 10 MiB")
            chunks.append(chunk)
        data = b"".join(chunks)
        os.lseek(fd, 0, os.SEEK_SET)
        second, remaining = hashlib.sha256(), MAX_BYTES + 1
        while remaining and (chunk := os.read(fd, min(65536, remaining))):
            second.update(chunk)
            remaining -= len(chunk)
        if (_identity(before) != _identity(os.fstat(fd)) or len(data) != before.st_size
                or _identity(before)[:4] != _identity(path.lstat())[:4]
                or hashlib.sha256(data).digest() != second.digest()):
            raise ValueError("Attachment changed while reading")
        return data


def _inspect_pending(path, name):
    with _PARSE_GATE:
        return inspect_text(read_original(path), name)


async def _blocking(operation, *args):
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Repeated cancellation (deadline plus shutdown/disconnect) must never
        # cancel the to_thread wrapper: its OS worker still owns these handles.
        # Drain under a fresh shield on every cancellation before cleanup starts.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not task.cancelled():
            task.exception()  # Consume a worker failure while preserving cancellation.
        raise


class _Upload:
    def __init__(self, vault):
        self.vault, self.handles, self.fd = vault, ExitStack(), None
        self.name, self.snapshot = f".pending-{uuid.uuid4().hex}", uuid.uuid4().hex
        self.size, self.hash = 0, hashlib.sha256()
        self.identity = None
        try:
            self.directory = self.handles.enter_context(_safe_open(vault, directory=True))
            self.vault_identity = (vault.stat().st_dev, vault.stat().st_ino)
            self.options = {} if os.name == "nt" else {"dir_fd": self.directory}
            pending = vault / self.name if os.name == "nt" else self.name
            self.fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                              | getattr(os, "O_BINARY", 0), 0o600, **self.options)
            info = os.fstat(self.fd)
            self.identity = info.st_dev, info.st_ino
        except BaseException:
            self.close()
            raise

    def write(self, data):
        if self.size + len(data) > MAX_BYTES:
            raise OverflowError("Attachment exceeds 10 MiB")
        view = memoryview(data)
        while view:
            written = os.write(self.fd, view)
            if not written:
                raise OSError("Attachment pending write failed")
            view = view[written:]
        self.hash.update(data)
        self.size += len(data)

    def seal(self):
        os.fsync(self.fd)
        os.close(self.fd)
        self.fd = None

    def publish(self):
        if (self.vault.stat().st_dev, self.vault.stat().st_ino) != self.vault_identity:
            raise ValueError("Attachment vault identity changed")
        pending = self.vault / self.name if os.name == "nt" else self.name
        final = self.vault / self.snapshot if os.name == "nt" else self.snapshot
        options = {} if os.name == "nt" else {
            "src_dir_fd": self.directory, "dst_dir_fd": self.directory, "follow_symlinks": False}
        os.link(pending, final, **options)
        os.unlink(pending, **self.options)
        if os.name != "nt":
            os.fsync(self.directory)

    def close(self):
        try:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            if self.identity is not None:
                try:
                    info = (self.vault / self.name).lstat()
                    if (info.st_dev, info.st_ino) == self.identity and stat.S_ISREG(info.st_mode):
                        pending = self.vault / self.name if os.name == "nt" else self.name
                        os.unlink(pending, **self.options)
                except FileNotFoundError:
                    pass
        finally:
            self.handles.close()


class AttachmentService:
    def __init__(self, db, data_dir):
        self.dao = AttachmentDAO(db)
        self.vault = Path(data_dir).absolute() / VAULT

    def _vault(self):
        helper = ArtifactService(self.dao.db, self.vault.parent)
        helper.vault = self.vault
        helper._vault()
        if hasattr(os, "getuid") and self.vault.stat().st_uid != os.getuid():
            raise ValueError("Attachment vault must belong to this operator")

    async def upload(self, project_id, name, expected_service, chunks, *, require_parsed=False, expected_session=None):
        name = display_name(name)
        project = await self.dao.project(project_id)
        if expected_service != await self.dao.service_id():
            raise ValueError("Attachment service identity changed; refresh")
        await _blocking(self._vault)
        # Constructor is only bounded handle/metadata setup. Keep ownership in
        # this coroutine before its next cancellation point.
        upload = _Upload(self.vault)
        try:
            async with asyncio.timeout(UPLOAD_TIMEOUT):
                async for chunk in chunks:
                    await _blocking(upload.write, chunk)
            await _blocking(upload.seal)
            original = await _blocking(read_original, self.vault / upload.name)
            if declared_type(name) == "docx" and recognize(original) in {"docx", "zip"}:
                from .attachment_parser import inspect_docx

                # Fixed trusted parser, anonymous input bytes; filename is metadata.
                parsed = {"declared_type": "docx", "recognized_type": recognize(original),
                          **await inspect_docx(original)}
            elif declared_type(name) == "pdf" and recognize(original) == "pdf":
                from .attachment_parser import inspect_pdf

                parsed = {"declared_type": "pdf", "recognized_type": "pdf",
                          **await inspect_pdf(original)}
            elif declared_type(name) == "xlsx" and recognize(original) == "xlsx":
                from .attachment_parser import inspect_xlsx

                parsed = {"declared_type": "xlsx", "recognized_type": "xlsx",
                          **await inspect_xlsx(original)}
            else:
                parsed = await _blocking(_inspect_pending, self.vault / upload.name, name)
            if require_parsed and parsed["parser_status"] not in {"parsed", "truncated"}:
                raise ValueError("Generated document could not be parsed")
            await _blocking(upload.publish)
            published = await _blocking(read_original, self.vault / upload.snapshot)
            if len(published) != upload.size or hashlib.sha256(published).hexdigest() != upload.hash.hexdigest():
                raise ValueError("Published attachment byte verification failed")
            # Publication precedes DB commit. Ambiguous DB failure retains a private
            # orphan for explicit recovery, never a fabricated available record.
            return await self.dao.insert({
                "id": f"att_{uuid.uuid4().hex}", "project_id": project_id,
                "service_id": expected_service, "snapshot_id": upload.snapshot,
                "original_name": name, "size_bytes": upload.size,
                "sha256": upload.hash.hexdigest(), **parsed,
            }, expected_project=project, expected_session=expected_session)
        finally:
            await _blocking(upload.close)

    async def generate_docx(self, project_id, expected_service_id, filename, title, paragraphs=None,
                            sections=None, sources=None, *, expected_session=None):
        from .execution import require_write_access
        from .report_docx import generate_document

        require_write_access()
        await self.dao.project(project_id)
        if expected_service_id != await self.dao.service_id():
            raise ValueError("Attachment service identity changed; refresh")
        data = await _blocking(generate_document, filename, title, paragraphs, sections, sources)

        async def chunks():
            yield data

        row = await self.upload(project_id, filename, expected_service_id, chunks(), require_parsed=True,
                                expected_session=expected_session)
        return await self.get(project_id, row["id"])

    async def generate_pdf(self, project_id, expected_service_id, filename, title, paragraphs=None,
                           sections=None, sources=None, *, expected_session=None):
        from .execution import require_write_access
        from .report_pdf import generate_pdf

        require_write_access()
        await self.dao.project(project_id)
        if expected_service_id != await self.dao.service_id():
            raise ValueError("Attachment service identity changed; refresh")
        data = await _blocking(generate_pdf, filename, title, paragraphs, sections, sources)

        async def chunks():
            yield data

        row = await self.upload(project_id, filename, expected_service_id, chunks(), require_parsed=True,
                                expected_session=expected_session)
        return await self.get(project_id, row["id"])

    async def generate_report_pair(self, project_id, expected_service_id, filename, title,
                                   paragraphs=None, sections=None, formats=None, sources=None, *, expected_session=None):
        """Return per-format evidence; never claim an ambiguous commit failed."""
        from .execution import require_write_access
        from .ipc.protocol import AttachmentGenerateReportPairParams
        from .report_content import report_blocks

        require_write_access()
        params = AttachmentGenerateReportPairParams.model_validate({
            "project_id": project_id, "expected_service_id": expected_service_id,
            "filename": filename, "title": title, "paragraphs": paragraphs,
            "sections": sections, "formats": formats if formats is not None else ["docx", "pdf"],
            "sources": [] if sources is None else sources,
        })
        values = params.model_dump()
        await self.dao.project(project_id)
        if expected_service_id != await self.dao.service_id():
            raise ValueError("Attachment service identity changed; refresh")
        for kind in values["formats"]:
            report_blocks(f"{filename}.{kind}", title, values["paragraphs"], values["sections"], f".{kind}", values["sources"])
        results = []
        binding_lost = False
        for kind in values["formats"]:
            if binding_lost:
                results.append({"format": kind, "status": "unconfirmed", "error": "Session project binding changed; stopped"})
                continue
            try:
                if expected_session is not None:
                    row = await self.dao.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (expected_session,))
                    if row is None or row["project_id"] != project_id:
                        binding_lost = True
                        raise ValueError("Current session project binding changed")
                operation = self.generate_docx if kind == "docx" else self.generate_pdf
                item = await operation(project_id, expected_service_id, f"{filename}.{kind}", title,
                                       values["paragraphs"], values["sections"], values["sources"], expected_session=expected_session)
                results.append({"format": kind, "status": "saved", **item})
            except ValueError as error:
                results.append({"format": kind, "status": "unconfirmed", "error": str(error)})
            except OSError:
                results.append({"format": kind, "status": "unconfirmed", "error": "Storage or generation unavailable"})
            except Exception:
                results.append({"format": kind, "status": "unconfirmed", "error": "Generation unavailable"})
            if expected_session is not None:
                row = await self.dao.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (expected_session,))
                if row is None or row["project_id"] != project_id:
                    binding_lost = True
        saved = sum(item["status"] == "saved" for item in results)
        return {"status": "complete" if saved == len(results) else "partial" if saved else "unconfirmed",
                "results": results}

    async def list(self, project_id):
        await self.dao.project(project_id)
        return {"attachments": await self.dao.list(project_id), "service_id": await self.dao.service_id()}

    async def content(self, project_id, attachment_id):
        if not _ID.fullmatch(attachment_id):
            raise ValueError("Invalid attachment ID")
        await self.dao.project(project_id)
        row = await self.dao.get(project_id, attachment_id)
        if (row["service_id"] != await self.dao.current_service()
                or not _SNAPSHOT.fullmatch(row["snapshot_id"])):
            raise ValueError("Attachment vault identity is inconsistent")
        display_name(row["original_name"])
        try:
            data = await _blocking(read_original, self.vault / row["snapshot_id"])
        except (ValueError, OSError) as exc:
            raise ValueError("Attachment original is missing or corrupt") from exc
        if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Attachment original is missing or corrupt")
        return row, data

    async def get(self, project_id, attachment_id):
        row, _ = await self.content(project_id, attachment_id)
        preview = row.pop("preview_text")
        if (not isinstance(preview, str) or len(preview.encode("utf-8")) > MAX_PREVIEW_BYTES
                or len(preview.splitlines()) > MAX_PREVIEW_LINES):
            raise ValueError("Attachment preview metadata is inconsistent")
        return {"attachment": metadata(row), "preview_text": preview}


async def get_attachment_service():
    from .app import ensure_db

    db = await ensure_db()
    if db is None:
        raise ValueError("Attachment storage unavailable")
    return AttachmentService(db, db.path.parent)
