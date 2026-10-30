"""Bounded original-byte snapshots for explicit project text registration.

This is a local authenticated operator feature, not an OS sandbox. No source
file is edited; downloads read only verified vault bytes, never the source.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import os
import re
import stat
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath

from .storage.dao.text_artifacts import ArtifactDAO

MAX_BYTES = 2 * 1024 * 1024
MIMES = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv"}
_ID = re.compile(r"art_[0-9a-f]{32}\Z")
_SNAPSHOT = re.compile(r"[0-9a-f]{32}\Z")


def relative_source(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or len(value) > 1024 or path.is_absolute() or path.as_posix() != value
            or ".." in path.parts or any(char in value for char in "\\:\x00")
            or any(ord(char) < 32 for char in value)
            or path.suffix.lower() not in MIMES):
        raise ValueError("Use a canonical project-relative TXT, MD or CSV path")
    return value


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _regular(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise ValueError("Artifact must be a regular file without links or reparse points")


@contextmanager
def _open_windows(path: Path, *, directory=False):
    """Retain non-reparse ancestor handles; deny writes/deletes during read."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    attrs = kernel.GetFileInformationByHandleEx
    attrs.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    with ExitStack() as stack:
        for part in (*reversed(path.parents), path):
            leaf = part == path
            handle = create(str(part), 0x80000000 if leaf and not directory else 0x80, 1, None, 3,
                            0x00200000 | 0x02000000, None)
            if handle == wintypes.HANDLE(-1).value:
                raise OSError(ctypes.get_last_error(), "Cannot safely open artifact path")
            # FileAttributeTagInfo includes FILE_ATTRIBUTE_REPARSE_POINT.
            tag = (wintypes.DWORD * 2)()
            if not attrs(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)) or tag[0] & 0x400:
                close(handle)
                raise ValueError("Artifact paths cannot contain reparse points")
            if leaf and not directory:
                fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
                stack.callback(os.close, fd)
            else:
                stack.callback(close, handle)
        yield None if directory else fd


@contextmanager
def _safe_open(path: Path, *, directory=False):
    if not path.is_absolute():
        raise ValueError("Project root must be absolute")
    if os.name == "nt":
        with _open_windows(path, directory=directory) as fd:
            yield fd
        return
    with ExitStack() as stack:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, fd)
        for index, part in enumerate(path.parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if directory or index < len(path.parts) - 2:
                flags |= os.O_DIRECTORY
            fd = os.open(part, flags, dir_fd=fd)
            stack.callback(os.close, fd)
        yield fd


def _read(path: Path) -> bytes:
    with _safe_open(path) as fd:
        before = os.fstat(fd)
        _regular(before)
        if before.st_size > MAX_BYTES:
            raise ValueError("Artifact exceeds the 2 MiB limit")
        chunks, total = [], 0
        while chunk := os.read(fd, min(65536, MAX_BYTES + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError("Artifact exceeds the 2 MiB limit")
        data = b"".join(chunks)
        # A second pass detects same-size mutations during the first pass.
        os.lseek(fd, 0, os.SEEK_SET)
        second = hashlib.sha256()
        remaining = MAX_BYTES + 1
        while remaining and (chunk := os.read(fd, min(65536, remaining))):
            second.update(chunk)
            remaining -= len(chunk)
        if (_identity(before) != _identity(os.fstat(fd)) or len(data) != before.st_size
                or hashlib.sha256(data).digest() != second.digest()
                # Windows path stat ctime is creation time; CRT fd stat may
                # report change time. Compare like-for-like fd ctime above.
                or _identity(before)[:4] != _identity(path.lstat())[:4]):
            raise ValueError("Artifact changed while reading; retry registration")
        return data


def _parse(data: bytes, source: str) -> str:
    text = data.decode("utf-8", errors="strict")
    if "\x00" in text:
        raise ValueError("Artifact text cannot contain NUL")
    if PurePosixPath(source).suffix.lower() == ".csv":
        # Parse only; no spreadsheet engine or formula evaluation.
        try:
            for _ in csv.reader(io.StringIO(text, newline=""), strict=True):
                pass
        except csv.Error as exc:
            raise ValueError("CSV is not parseable") from exc
    return text


class ArtifactService:
    def __init__(self, db, data_dir: Path):
        self.dao = ArtifactDAO(db)
        self.vault = Path(data_dir).absolute() / "text-artifact-vault"

    def _vault(self):
        for path in (self.vault.parent, *self.vault.parent.parents):
            info = path.lstat()
            if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Artifact vault cannot contain links or reparse points")
        self.vault.mkdir(mode=0o700, exist_ok=True)
        info = self.vault.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Artifact vault cannot contain links or reparse points")
        if os.name != "nt" and self.vault.stat().st_mode & 0o077:
            raise ValueError("Artifact vault must be private")

    def _publish(self, data, snapshot):
        self._vault()
        temporary = f".pending-{uuid.uuid4().hex}"
        with _safe_open(self.vault, directory=True) as directory:
            # POSIX uses a pinned directory; Windows retains locked ancestors.
            options = {} if os.name == "nt" else {"dir_fd": directory}
            pending = self.vault / temporary if os.name == "nt" else temporary
            final = self.vault / snapshot if os.name == "nt" else snapshot
            fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, **options)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                link_options = {} if os.name == "nt" else {
                    "src_dir_fd": directory, "dst_dir_fd": directory, "follow_symlinks": False}
                os.link(pending, final, **link_options)  # Atomic, no overwrite.
            finally:
                os.unlink(pending, **options)
            if os.name != "nt":
                os.fsync(directory)
            published = _read(self.vault / snapshot)
            if published != data:
                raise ValueError("Published artifact snapshot failed byte verification")

    async def register(self, project_id, source_path, run_id=None, expected_service_id=None,
                       expected_session_id=None, request_id=None):
        guarded = (expected_service_id, expected_session_id, request_id)
        if any(value is not None for value in guarded):
            if not run_id or not all(guarded) or not _SNAPSHOT.fullmatch(request_id):
                raise ValueError("Run registration requires service, session and request identities")
        source_path = relative_source(source_path)
        project = await self.dao.project(project_id)
        await self.dao.check_run(project_id, run_id, project["root_path"])
        root = Path(project["root_path"])
        if not root.is_absolute() or root != root.absolute() or ".." in root.parts:
            raise ValueError("Project root must be canonical and absolute")
        service_id = await self.dao.service_id()
        if expected_service_id is not None and service_id != expected_service_id:
            raise ValueError("Service identity changed")
        if request_id is not None:
            existing = await self.dao.existing_request({
                "id": f"art_{request_id}", "project_id": project_id, "run_id": run_id,
                "service_id": service_id, "source_path": source_path,
            }, expected_root=project["root_path"], expected_service_id=expected_service_id,
                expected_session_id=expected_session_id)
            if existing is not None:
                # Return only a still-readable, verified snapshot. The source may
                # have changed or disappeared since the original registration.
                row, _ = await self.content(project_id, existing["id"])
                return row
        data = await asyncio.to_thread(_read, root / source_path)
        await asyncio.to_thread(_parse, data, source_path)
        snapshot = uuid.uuid4().hex
        await asyncio.to_thread(self._publish, data, snapshot)
        # Orphaned snapshots on DB failure are retained for explicit recovery.
        return await self.dao.insert({
            "id": f"art_{request_id or uuid.uuid4().hex}", "project_id": project_id, "run_id": run_id,
            "service_id": service_id, "source_path": source_path, "snapshot_id": snapshot,
            "mime_type": MIMES[PurePosixPath(source_path).suffix.lower()],
            "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        }, expected_root=project["root_path"], expected_service_id=expected_service_id,
            expected_session_id=expected_session_id, idempotent=request_id is not None)

    async def candidates(self, project_id, run_id, expected_session_id, expected_service_id):
        """Bounded path hints from successful native write calls; never read source files."""
        from .ipc.host_session_gate import require_host_session

        await require_host_session(expected_session_id)
        project = await self.dao.project(project_id)
        await self.dao.check_run(project_id, run_id, project["root_path"])
        owner = await self.dao.db.fetchone(
            "SELECT session_id, json_extract(metadata, '$.workspace_snapshot') AS workspace "
            "FROM agent_runs WHERE id=? AND json_valid(metadata)", (run_id,))
        if (not owner or owner["session_id"] != expected_session_id
                or owner["workspace"] != project["root_path"]
                or await self.dao.service_id() != expected_service_id):
            raise ValueError("Run or service identity changed")
        rows = await self.dao.db.fetchall(
            "SELECT substr(json_extract(payload, '$.args.path'),1,1025) AS path "
            "FROM agent_run_steps WHERE run_id=? AND session_id=? AND kind='tool_call' "
            "AND tool_name='write_file' AND status='completed' AND json_valid(payload) "
            "ORDER BY ordinal DESC LIMIT 201", (run_id, expected_session_id))
        root = Path(project["root_path"])
        paths = []
        for row in rows[:200]:
            raw = row["path"]
            if not isinstance(raw, str) or len(raw) > 1024:
                continue
            candidate = Path(raw)
            if candidate.is_absolute():
                try:
                    candidate = candidate.relative_to(root)
                except ValueError:
                    continue
            try:
                value = relative_source(candidate.as_posix())
            except ValueError:
                continue
            if candidate.suffix.lower() in {".md", ".txt"} and value not in paths:
                paths.append(value)
        await require_host_session(expected_session_id)
        await self.dao.check_run(project_id, run_id, project["root_path"])
        current = await self.dao.project(project_id)
        owner_after = await self.dao.db.fetchone(
            "SELECT session_id, json_extract(metadata, '$.workspace_snapshot') AS workspace "
            "FROM agent_runs WHERE id=? AND json_valid(metadata)", (run_id,))
        if (current["root_path"] != project["root_path"] or not owner_after
                or owner_after["session_id"] != expected_session_id
                or owner_after["workspace"] != project["root_path"]
                or await self.dao.service_id() != expected_service_id):
            raise ValueError("Project or service identity changed")
        return {"project_id": project_id, "run_id": run_id, "session_id": expected_session_id,
                "service_id": expected_service_id, "paths": paths, "truncated": len(rows) > 200}

    async def list(self, project_id, run_id=None, cursor=None, query=None):
        from .artifact_cursor import decode_cursor, encode_cursor
        await self.dao.project(project_id)
        service = await self.dao.service_id()
        anchor = decode_cursor(cursor, "art", project_id, service, run_id, query)
        rows = await self.dao.list(project_id, run_id, anchor, limit=201, query=query)
        next_id = rows[199]["id"] if len(rows) > 200 else None
        next_cursor = encode_cursor("art", project_id, service, run_id, query, next_id) if next_id and query is not None else next_id
        return {"artifacts": rows[:200], "next_cursor": next_cursor, "service_id": service}

    async def content(self, project_id, artifact_id):
        if not _ID.fullmatch(artifact_id):
            raise ValueError("Invalid artifact ID")
        await self.dao.project(project_id)
        row = await self.dao.get(project_id, artifact_id)
        if (row["service_id"] != await self.dao.service_id()
                or not _SNAPSHOT.fullmatch(row["snapshot_id"])):
            raise ValueError("Artifact vault identity is inconsistent")
        self._vault()
        try:
            data = await asyncio.to_thread(_read, self.vault / row["snapshot_id"])
        except (OSError, ValueError) as exc:
            raise ValueError("Artifact snapshot is missing or corrupt") from exc
        if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Artifact snapshot is missing or corrupt")
        return row, data

    async def get(self, project_id, artifact_id):
        row, data = await self.content(project_id, artifact_id)
        return {"artifact": row, "preview_text": _parse(data, row["source_path"])}


async def get_artifact_service():
    from .app import ensure_db

    db = await ensure_db()
    if db is None:
        raise ValueError("Artifact storage is unavailable")
    # Anchor to the opened database, rather than a later environment change.
    return ArtifactService(db, db.path.parent)
