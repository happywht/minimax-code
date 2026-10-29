"""Explicit operator generation, immutable publication and committed receipts.

No run admission. An optional existing run is a checked
historical association, never execution authority or a completion claim.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from .attachments import AttachmentService, _blocking, _Upload, read_original
from .execution import require_write_access
from .office_metadata import validate_record
from .office_process import generate_xlsx
from .office_xlsx import canonical, digest
from .storage.dao.attachments import metadata
from .storage.dao.office_deliveries import OfficeDAO

_GATE = threading.BoundedSemaphore(1)
QUEUE_SECONDS = 5
TOTAL_SECONDS = 40
_ID = re.compile(r"off_[0-9a-f]{32}\Z")


@asynccontextmanager
async def _slot():
    deadline = time.monotonic() + QUEUE_SECONDS
    while not _GATE.acquire(blocking=False):
        if time.monotonic() >= deadline:
            raise ValueError("Office generation queue is full; retry later")
        await asyncio.sleep(.02)
    try:
        yield
    finally:
        _GATE.release()


async def _commit(operation):
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise


class OfficeService:
    def __init__(self, db, data_dir):
        self.dao = OfficeDAO(db)
        self.originals = AttachmentService(db, data_dir)

    async def _identity(self, project_id, expected_service):
        project = await self.originals.dao.project(project_id)
        service = await self.originals.dao.service_id()
        if service != expected_service:
            raise ValueError("Office service identity changed; refresh")
        root = project["root_path"]
        if not root or not Path(root).is_absolute() or not Path(root).resolve(strict=True).is_dir():
            raise ValueError("Office generation requires an explicit absolute project directory")
        return project

    async def list(self, project_id, cursor=None, query=None):
        from .artifact_cursor import decode_cursor, encode_cursor
        await self.originals.dao.project(project_id)
        service = await self.originals.dao.service_id()
        anchor = decode_cursor(cursor, "off", project_id, service, None, query or "")
        records = await self.dao.list(project_id, anchor, query)
        next_cursor = (encode_cursor("off", project_id, service, None, query or "", records[199]["id"])
                       if len(records) > 200 else None)
        rows = []
        for row in records[:200]:
            attachment = await self.originals.dao.get(project_id, row["attachment_id"])
            if row["service_id"] != service:
                raise ValueError("Office service identity is inconsistent")
            rows.append({"delivery": validate_record(row, attachment), "attachment": metadata(attachment)})
        return {"deliveries": rows, "service_id": service, "next_cursor": next_cursor}

    async def get(self, project_id, delivery_id):
        if not _ID.fullmatch(delivery_id):
            raise ValueError("Invalid Office delivery ID")
        await self.originals.dao.project(project_id)
        row = await self.dao.get(project_id, delivery_id)
        attachment, _ = await self.originals.content(project_id, row["attachment_id"])
        delivery = validate_record(row, attachment)
        result = await self.originals.get(project_id, attachment["id"])
        return {"delivery": delivery, **result}

    async def generate(self, request, *, expected_session=None):
        from .attachment_parser import inspect_xlsx

        require_write_access()
        async with asyncio.timeout(TOTAL_SECONDS), _slot():
            project = await self._identity(request.project_id, request.expected_service_id)
            fingerprint = digest({"request": request.model_dump(exclude={"request_key"}), "project": project})
            existing = await self.dao.find(request.project_id, request.request_key, expected_session=expected_session)
            if existing is not None:
                if existing["request_hash"] != fingerprint:
                    raise ValueError("Office request key already belongs to different input")
                return await self.get(request.project_id, existing["id"])
            binding = await self.dao.binding(project, request.run_id)
            data, report = await generate_xlsx(request.spec)
            parsed = await inspect_xlsx(data)
            if parsed["parser_status"] not in {"parsed", "empty", "truncated"}:
                raise ValueError("Office preview verification is unavailable")
            await _blocking(self.originals._vault)
            upload = _Upload(self.originals.vault)
            try:
                await _blocking(upload.write, data)
                await _blocking(upload.seal)
                await _blocking(upload.publish)
                published = await _blocking(read_original, self.originals.vault / upload.snapshot)
                if published != data:
                    raise ValueError("Office published snapshot failed byte verification")
                attachment_id = f"att_{uuid.uuid4().hex}"
                fields = {
                    "id": f"off_{uuid.uuid4().hex}", "project_id": request.project_id,
                    "service_id": request.expected_service_id, "attachment_id": attachment_id,
                    "run_id": request.run_id, "request_key": request.request_key,
                    "request_hash": fingerprint, "input_sha256": report["input_sha256"],
                    "writer_version": report["writer"]["version"], "validator_version": 1,
                    "validation_report": canonical(report).decode("utf-8"),
                }
                attachment = {
                    "id": attachment_id, "project_id": request.project_id,
                    "service_id": request.expected_service_id, "snapshot_id": upload.snapshot,
                    "original_name": request.filename, "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "declared_type": "xlsx", "recognized_type": "xlsx", **parsed,
                }
                row = await _commit(self.dao.insert(
                    fields, attachment, expected_project=project, expected_run=binding,
                    expected_session=expected_session))
                return await self.get(request.project_id, row["id"])
            finally:
                # Publication can leave only a private orphan on ambiguous DB
                # failure. Never fabricate a receipt or delete another snapshot.
                await _blocking(upload.close)


async def get_office_service():
    from .app import ensure_db

    db = await ensure_db()
    if db is None:
        raise ValueError("Office storage is unavailable")
    return OfficeService(db, db.path.parent)
