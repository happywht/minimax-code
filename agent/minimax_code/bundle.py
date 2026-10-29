"""Offline DATA-ONLY SQLite + registered text/attachment vault bundles.

This module deliberately imports no application, scheduler, plugin or process
runtime. Restore is a byte-preserving data clone, not an operational recovery.
The format is bounded ZIP_STORED with an exact, versioned member allowlist.
Hashes detect corruption, not authenticity: only restore trusted bundles.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import sqlite3
import stat
import struct
import sys
import tempfile
import time
import zipfile
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import __version__
from .attachment_metadata import REASONS
from .attachment_metadata import validate as validate_parser_metadata
from .attachments import (
    DECLARED_TYPES,
    MAX_PREVIEW_BYTES,
    MAX_PREVIEW_LINES,
    RECOGNIZED_TYPES,
    STATUSES,
    display_name,
)
from .attachments import (
    MAX_BYTES as MAX_ATTACHMENT_BYTES,
)
from .attachments import (
    VAULT as ATTACHMENT_VAULT,
)
from .checkpoint_git_bundle import capture_pack, import_pack, validate_pack
from .managed_paths import managed_child
from .office_metadata import RECORD_KEYS as OFFICE_RECORD_KEYS
from .office_metadata import validate_record as validate_office_record
from .runtime.service_lease import ServiceLeaseError, acquire_service_lease
from .text_artifacts import (
    MAX_BYTES,
    MIMES,
    _identity,
    _parse,
    _regular,
    _safe_open,
    relative_source,
)

FORMAT = "minimax-code-data-vault"
FORMAT_VERSION = 11
SCHEMA_VERSION = 49
SUPPORTED_SCHEMAS = {1: 41, 2: 42, 3: 43, 4: 44, 5: 45, 6: 46, 7: 47, 8: 48, 9: 49, 10: 49, 11: 49}
MAX_FILES = 10_000
MAX_DATABASE_BYTES = 512 * 1024 * 1024
MAX_WAL_BYTES = MAX_DATABASE_BYTES
MAX_SHM_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES + 8 * 1024 * 1024
VAULT = "text-artifact-vault"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SNAPSHOT = re.compile(r"[0-9a-f]{32}\Z")
_SERVICE = re.compile(r"svc_[0-9a-f]{32}\Z")
_ARTIFACT = re.compile(r"art_[0-9a-f]{32}\Z")
_ATTACHMENT = re.compile(r"att_[0-9a-f]{32}\Z")
EXCLUSIONS = [
    "project trees and .minimax child copies",
    "handoff files and unregistered external attachments",
    "configuration, skills and plugins",
    "SSH material and OS keyring",
    "containers and images",
    "service locks, runtime markers and installation ownership files",
    "unregistered vault files and pending files",
    "unregistered checkpoint snapshot files and directories",
    "submodule repositories and external Git LFS objects",
]
NOTICE = (
    "DATA-ONLY clone. Original host paths, credentials stored in SQLite, scheduling/run "
    "and execution metadata are preserved. Not ready for operational use until explicit "
    "safe rebinding; no service is started and no ownership is claimed. Treat the bundle "
    "as sensitive. Hashes verify integrity, not provenance."
)


class BundleError(ValueError):
    """Fail-closed bundle validation or publication failure."""


def _require(condition, message):
    if not condition:
        raise BundleError(message)


def _path(value):
    path = Path(value).expanduser()
    _require(".." not in path.parts, "Paths must not contain parent traversal")
    return path.absolute()


def _owner(info):
    if hasattr(os, "getuid"):
        _require(info.st_uid == os.getuid(), "Files and directories must belong to the operator")


def _directory(path, *, owned=True):
    info = path.lstat()
    _require(
        stat.S_ISDIR(info.st_mode)
        and not path.is_symlink()
        and not getattr(info, "st_file_attributes", 0) & 0x400,
        "Directory must not be a link or reparse point",
    )
    if owned:
        _owner(info)
    return info.st_dev, info.st_ino


def _check_directory(path, identity, *, owned=True):
    _require(_directory(path, owned=owned) == identity, "Directory identity changed")


def _absent(path):
    _require(not os.path.lexists(path), "Destination must not already exist")


def _file_meta(path, limit, *, expected=None):
    with _safe_open(path) as fd:
        before = os.fstat(fd)
        _regular(before)
        _owner(before)
        _require(before.st_size <= limit, "File exceeds the bundle size limit")
        digest, total = hashlib.sha256(), 0
        while chunk := os.read(fd, min(1024 * 1024, limit + 1 - total)):
            digest.update(chunk)
            total += len(chunk)
            _require(total <= limit, "File exceeds the bundle size limit")
        _require(
            _identity(before) == _identity(os.fstat(fd))
            and _identity(before)[:4] == _identity(path.lstat())[:4]
            and total == before.st_size,
            "File changed during verification",
        )
    result = {"size_bytes": total, "sha256": digest.hexdigest()}
    _require(expected is None or result == expected, "File size or hash does not match")
    return result


class _Stage:
    """Private staging, deleting only explicitly recorded files of our identity."""

    def __init__(self, parent):
        self.path = Path(tempfile.mkdtemp(prefix=".bundle-pending-", dir=parent))
        os.chmod(self.path, 0o700)
        self.directories = [(self.path, _directory(self.path))]
        self.files = []
        self.published = False

    def directory(self, name):
        path = self.path / name
        path.mkdir(mode=0o700)
        self.directories.append((path, _directory(path)))
        return path

    def parents(self, name):
        path = Path(name)
        for parent in reversed(path.parents):
            if str(parent) == ".":
                continue
            target = self.path / parent
            if not target.exists():
                self.directory(str(parent))

    @contextmanager
    def file(self, name):
        path = self.path / name
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
        info = os.fstat(fd)
        self.files.append((path, (info.st_dev, info.st_ino)))
        with os.fdopen(fd, "wb") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())

    def cleanup(self):
        if self.published:
            return
        # Never follow a replacement directory or recursively remove a computed path.
        for directory, identity in self.directories:
            if not os.path.lexists(directory):
                return
            try:
                _check_directory(directory, identity)
            except (OSError, BundleError):
                return
        for path, identity in reversed(self.files):
            try:
                info = path.lstat()
                if (info.st_dev, info.st_ino) == identity and stat.S_ISREG(info.st_mode):
                    path.unlink()
            except FileNotFoundError:
                pass
        for directory, _ in reversed(self.directories):
            try:
                directory.rmdir()
            except (FileNotFoundError, OSError):
                pass  # A foreign/untracked file is retained, never deleted.

    def relocate(self, destination):
        original = self.path
        self.path = destination
        self.files = [
            (destination / path.relative_to(original), identity) for path, identity in self.files
        ]
        self.directories = [
            (destination / path.relative_to(original), identity)
            for path, identity in self.directories
        ]


@contextmanager
def _stage(parent, *, owned_parent=True):
    with _safe_open(parent, directory=True):
        identity = _directory(parent, owned=owned_parent)
        stage = _Stage(parent)
        stage.parent_identity = identity
        try:
            yield stage
        finally:
            stage.cleanup()


def _fsync_directory(path):
    if os.name == "nt":
        # Python/Win32 supplies no portable reliable equivalent for directories.
        return
    with _safe_open(path, directory=True) as fd:
        os.fsync(fd)


@contextmanager
def _database(path, *, immutable=True):
    # immutable is only for our closed, standalone SQLite backup; never source WAL.
    uri = path.as_uri() + ("?mode=ro&immutable=1" if immutable else "?mode=ro")
    conn = sqlite3.connect(uri, uri=True, timeout=1)
    try:
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("PRAGMA query_only=ON")
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_DATABASE_BYTES)
        # The extension path comes exclusively from the installed package.
        import sqlite_vec

        conn.enable_load_extension(True)
        try:
            conn.load_extension(sqlite_vec.loadable_path())
        finally:
            conn.enable_load_extension(False)
        yield conn
    finally:
        conn.close()


def _inventory(path):
    with _database(path) as conn:
        _require(
            conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
            "SQLite integrity check failed",
        )
        _require(
            not conn.execute("PRAGMA foreign_key_check").fetchmany(1),
            "SQLite foreign key check failed",
        )
        for table in ("schema_migrations", "artifact_service", "text_artifacts"):
            row = conn.execute("SELECT type FROM sqlite_schema WHERE name=?", (table,)).fetchone()
            _require(row == ("table",), "Required metadata must be ordinary SQLite tables")
        versions = [
            row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        schema_version = versions[-1] if versions else 0
        _require(schema_version in SUPPORTED_SCHEMAS.values()
                 and versions == list(range(1, schema_version + 1)),
                 "Unsupported SQLite schema version")
        attachment_table = conn.execute("SELECT type,sql FROM sqlite_schema WHERE name='attachments'").fetchone()
        if schema_version == 41:
            _require(attachment_table is None, "Schema041 must not contain attachment metadata")
        if schema_version < 46:
            _require(conn.execute("SELECT 1 FROM sqlite_schema WHERE name='office_deliveries'")
                     .fetchone() is None, "Historical schema must not contain Office metadata")
        columns = [row[1] for row in conn.execute("PRAGMA table_info(text_artifacts)")]
        _require(
            columns
            == [
                "id",
                "project_id",
                "run_id",
                "service_id",
                "source_path",
                "snapshot_id",
                "mime_type",
                "size_bytes",
                "sha256",
                "version",
                "parse_status",
                "verification_status",
                "created_at",
            ],
            "Unsupported text artifact schema",
        )
        services = conn.execute("SELECT singleton,service_id FROM artifact_service").fetchall()
        _require(
            len(services) <= 1
            and (
                not services
                or services[0][0] == 1
                and isinstance(services[0][1], str)
                and _SERVICE.fullmatch(services[0][1])
            ),
            "Invalid artifact service identity",
        )
        service_id = services[0][1] if services else None
        rows = conn.execute(
            "SELECT id,snapshot_id,size_bytes,sha256,version,service_id,source_path,"
            "mime_type,parse_status,verification_status FROM text_artifacts "
            "ORDER BY snapshot_id LIMIT ?",
            (MAX_FILES + 1,),
        ).fetchall()
        _require(len(rows) <= MAX_FILES, "Too many registered artifacts")
        artifacts, total = [], 0
        for (
            artifact,
            snapshot,
            size,
            digest,
            version,
            service,
            source,
            mime,
            parsed,
            verified,
        ) in rows:
            _require(
                isinstance(artifact, str)
                and _ARTIFACT.fullmatch(artifact)
                and isinstance(snapshot, str)
                and _SNAPSHOT.fullmatch(snapshot)
                and type(size) is int
                and 0 <= size <= MAX_BYTES
                and isinstance(digest, str)
                and _HEX.fullmatch(digest)
                and type(version) is int
                and version > 0
                and service == service_id
                and isinstance(source, str)
                and parsed == "parseable"
                and verified == "unverified",
                "Invalid artifact reference in SQLite",
            )
            relative_source(source)  # Pure validation; never open this host source path.
            _require(mime == MIMES[Path(source).suffix.lower()], "Invalid artifact MIME metadata")
            total += size
            _require(total <= MAX_TOTAL_BYTES, "Registered vault exceeds bundle limits")
            artifacts.append(
                {
                    "artifact_id": artifact,
                    "snapshot_id": snapshot,
                    "size_bytes": size,
                    "sha256": digest,
                    "version": version,
                }
            )
        _require(
            len({row["snapshot_id"] for row in artifacts}) == len(artifacts),
            "Duplicate SQLite snapshot reference",
        )
        attachments = []
        if schema_version >= 42:
            attachments = _attachment_inventory(conn, service_id, len(artifacts), schema_version)
            _require(total + sum(row["size_bytes"] for row in attachments) <= MAX_TOTAL_BYTES,
                     "Registered vaults exceed shared bundle limit")
        schema = conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        ).fetchall()
        schema_hash = hashlib.sha256(
            json.dumps(schema, ensure_ascii=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "schema_version": schema_version,
            "schema_sha256": schema_hash,
            "service_id": service_id,
            "artifacts": artifacts,
            **({"attachments": attachments} if schema_version >= 42 else {}),
            **({"office_deliveries": _office_inventory(conn, service_id)}
               if schema_version >= 46 else {}),
        }


def _attachment_inventory(conn, service_id, artifact_count, schema_version):
    row = conn.execute("SELECT type,sql FROM sqlite_schema WHERE name='attachments'").fetchone()
    _require(row is not None and row[0] == "table" and row[1].lstrip().upper().startswith("CREATE TABLE"),
             "Schema042 requires an ordinary attachments table")
    columns = [row[1] for row in conn.execute("PRAGMA table_info(attachments)")]
    _require(columns == ["id", "project_id", "service_id", "snapshot_id", "original_name",
                         "declared_type", "recognized_type", "size_bytes", "sha256", "version",
                         "parser_status", "parser_version", "preview_text", "preview_truncated",
                         "quality_status", "created_at"] + (["parser_reason", "parser_scope"] if schema_version >= 43 else []), "Unsupported attachment schema")
    rows = conn.execute("SELECT id,snapshot_id,size_bytes,sha256,version,service_id,original_name,"
                        "declared_type,recognized_type,parser_status,parser_version,preview_text,"
                        "preview_truncated,quality_status,"
                        + ("parser_reason,parser_scope" if schema_version >= 43 else "'legacy_v1','legacy_v1'")
                        + " FROM attachments ORDER BY snapshot_id LIMIT ?",
                        (MAX_FILES + 1,)).fetchall()
    _require(len(rows) + artifact_count <= MAX_FILES, "Too many registered vault files")
    result, ids, snapshots = [], set(), set()
    for (attachment, snapshot, size, digest, version, service, name, declared, recognized,
         status, parser, preview, truncated, quality, reason, scope) in rows:
        _require(isinstance(attachment, str) and _ATTACHMENT.fullmatch(attachment)
                 and isinstance(snapshot, str) and _SNAPSHOT.fullmatch(snapshot)
                 and type(size) is int and 0 <= size <= MAX_ATTACHMENT_BYTES
                 and isinstance(digest, str) and _HEX.fullmatch(digest)
                 and type(version) is int and version > 0 and service is not None and service == service_id
                 and declared in DECLARED_TYPES and recognized in RECOGNIZED_TYPES
                 and status in STATUSES and type(parser) is int
                 and (parser == 1 and reason == scope == "legacy_v1"
                      or schema_version >= 43 and parser == 2 and reason in REASONS
                      and scope in {"inert_text", "docx_body_paragraphs_tables", "original_only"})
                 and isinstance(preview, str) and len(preview.encode("utf-8")) <= MAX_PREVIEW_BYTES
                 and len(preview.splitlines()) <= MAX_PREVIEW_LINES and truncated in (0, 1)
                 and bool(truncated) == (status == "truncated") and quality == "unverified",
                 "Invalid attachment reference or parser metadata in SQLite")
        if parser == 2 and declared == recognized == "pdf" and scope == "inert_text":
            try:
                validate_parser_metadata({
                    "parser_version": parser, "parser_scope": scope, "parser_reason": reason,
                    "parser_status": status, "preview_text": preview, "preview_truncated": truncated,
                    "quality_status": quality,
                }, expected_scope="inert_text")
            except (ValueError, TypeError, UnicodeError) as exc:
                raise BundleError("Invalid PDF parser metadata in SQLite") from exc
        if parser == 2 and declared == recognized == "xlsx" and scope == "inert_text":
            try:
                validate_parser_metadata({
                    "parser_version": parser, "parser_scope": scope, "parser_reason": reason,
                    "parser_status": status, "preview_text": preview, "preview_truncated": truncated,
                    "quality_status": quality,
                }, expected_scope="inert_text", expected_kind="xlsx")
            except (ValueError, TypeError, UnicodeError) as exc:
                raise BundleError("Invalid XLSX parser metadata in SQLite") from exc
        display_name(name)
        _require(attachment not in ids and snapshot not in snapshots, "Duplicate attachment reference")
        ids.add(attachment)
        snapshots.add(snapshot)
        result.append({"attachment_id": attachment, "snapshot_id": snapshot, "size_bytes": size,
                       "sha256": digest, "version": version})
    return result


def _office_inventory(conn, service_id):
    table = conn.execute("SELECT type,sql FROM sqlite_schema WHERE name='office_deliveries'")\
        .fetchone()
    _require(table is not None and table[0] == "table"
             and table[1].lstrip().upper().startswith("CREATE TABLE"),
             "Schema046 requires an ordinary Office deliveries table")
    _require([row[1] for row in conn.execute("PRAGMA table_xinfo(office_deliveries)")]
             == list(OFFICE_RECORD_KEYS), "Unsupported Office deliveries schema")
    columns = "id,project_id,service_id,sha256,size_bytes,declared_type,recognized_type"
    attachments = {
        row[0]: dict(zip(columns.split(","), row, strict=True))
        for row in conn.execute(f"SELECT {columns} FROM attachments LIMIT ?", (MAX_FILES + 1,))
    }
    rows = conn.execute("SELECT * FROM office_deliveries ORDER BY id LIMIT ?", (MAX_FILES + 1,))\
        .fetchall()
    _require(len(rows) <= len(attachments), "Invalid Office delivery count")
    result, ids, references, requests = [], set(), set(), set()
    for values in rows:
        try:
            row = dict(zip(OFFICE_RECORD_KEYS, values, strict=True))
            attachment = attachments.get(row["attachment_id"])
            _require(attachment is not None, "Missing Office attachment reference")
            row = validate_office_record(row, attachment)
        except (ValueError, TypeError, UnicodeError) as exc:
            raise BundleError("Invalid Office delivery metadata in SQLite") from exc
        request = (row["project_id"], row["request_key"])
        _require(row["service_id"] == service_id and row["id"] not in ids
                 and row["attachment_id"] not in references and request not in requests,
                 "Invalid or duplicate Office reference")
        ids.add(row["id"])
        references.add(row["attachment_id"])
        requests.add(request)
        result.append(row)
    return result


def _snapshot(root, stage, lease):
    """Never let SQLite open a source sidecar, even its writable shared-memory index."""
    lease.assert_owned(root, root / "data.db")
    _require(
        not os.path.lexists(root / "data.db-journal"),
        "Existing rollback journal requires explicit source recovery first",
    )
    private = stage.directory("sqlite-input")
    inputs = [
        ("data.db", MAX_DATABASE_BYTES),
        ("data.db-wal", MAX_WAL_BYTES),
        ("data.db-shm", MAX_SHM_BYTES),
    ]
    with ExitStack() as handles:
        opened = []
        for name, limit in inputs:
            path = root / name
            if name != "data.db" and not os.path.lexists(path):
                continue
            fd = handles.enter_context(_safe_open(path))
            before = os.fstat(fd)
            _regular(before)
            _owner(before)
            _require(before.st_size <= limit, "SQLite input exceeds size limit")
            opened.append((path, fd, before, limit))
        for path, fd, before, limit in opened:
            if path.name == "data.db-shm":
                continue  # Rebuild the index privately; never write or copy source SHM.
            digest, total = hashlib.sha256(), 0
            with stage.file(f"sqlite-input/{path.name}") as output:
                while chunk := os.read(fd, min(1024 * 1024, limit + 1 - total)):
                    total += len(chunk)
                    _require(total <= limit, "SQLite input exceeds size limit")
                    output.write(chunk)
                    digest.update(chunk)
            os.lseek(fd, 0, os.SEEK_SET)
            second, remaining = hashlib.sha256(), limit + 1
            while remaining and (chunk := os.read(fd, min(1024 * 1024, remaining))):
                second.update(chunk)
                remaining -= len(chunk)
            _require(
                total == before.st_size and digest.digest() == second.digest(),
                "SQLite input changed during private copy",
            )
        for path, fd, before, _ in opened:
            _require(
                _identity(before) == _identity(os.fstat(fd))
                and _identity(before)[:4] == _identity(path.lstat())[:4],
                "SQLite input identity changed",
            )
        lease.assert_owned(root)
    # SQLite can create an empty WAL even for a closed source without a sidecar.
    # Own both possible private sidecars before it opens anything, so cleanup
    # never needs to delete untracked SQLite-created files.
    if not (private / "data.db-wal").exists():
        with stage.file("sqlite-input/data.db-wal"):
            pass
    # Track the rebuilt SHM before SQLite opens it, including on exception paths.
    with stage.file("sqlite-input/data.db-shm"):
        pass
    with _database(private / "data.db", immutable=False) as src:
        dest = sqlite3.connect(str(stage.path / "data.db"))
        try:
            deadline = time.monotonic() + 60
            page_size = src.execute("PRAGMA page_size").fetchone()[0]

            def progress(_status, _remaining, pages):
                _require(pages * page_size <= MAX_DATABASE_BYTES, "Database exceeds bundle limit")
                _require(time.monotonic() <= deadline, "Offline SQLite backup deadline exceeded")

            src.backup(dest, pages=256, progress=progress)
            dest.execute("PRAGMA journal_mode=DELETE")
        finally:
            dest.close()
    lease.assert_owned(root)


def _copy_file(source, stage, name, expected, limit=MAX_BYTES):
    with _safe_open(source) as fd, stage.file(name) as output:
        before = os.fstat(fd)
        _regular(before)
        _owner(before)
        _require(before.st_size == expected["size_bytes"], "Vault size does not match SQLite")
        digest, total = hashlib.sha256(), 0
        while chunk := os.read(fd, min(65536, limit + 1 - total)):
            total += len(chunk)
            _require(total <= limit, "Vault snapshot exceeds limit")
            output.write(chunk)
            digest.update(chunk)
        _require(
            total == expected["size_bytes"]
            and digest.hexdigest() == expected["sha256"]
            and _identity(before) == _identity(os.fstat(fd))
            and _identity(before)[:4] == _identity(source.lstat())[:4],
            "Vault snapshot changed or does not match SQLite",
        )


def _json(data):
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _checkpoint_path(value):
    _require(type(value) is str and 0 < len(value.encode("utf-8")) <= 1024,
             "Invalid checkpoint file path")
    parts = value.split("/")
    windows = PureWindowsPath(value)
    _require(not windows.is_absolute() and not windows.drive and "\\" not in value
             and all(part not in {"", ".", ".."} and not part.endswith((".", " "))
                     and not any(c in '<>:"|?*' or ord(c) < 32 for c in part)
                     and not PureWindowsPath(part).is_reserved() for part in parts)
             and str(PurePosixPath(value)) == value, "Unsafe checkpoint file path")
    return value


def _checkpoint_references(database):
    with _database(database) as conn:
        rows = conn.execute("SELECT id,untracked_files,has_untracked_snapshot FROM session_checkpoints ORDER BY id LIMIT ?", (MAX_FILES + 1,)).fetchall()
    _require(len(rows) <= MAX_FILES, "Too many checkpoints")
    refs, folded, identifiers = [], set(), set()
    for identifier, raw, has_snapshot in rows:
        _require(type(raw) is str and len(raw.encode("utf-8")) <= MAX_MANIFEST_BYTES,
                 "Invalid checkpoint file list")
        try:
            managed_child(Path.cwd().absolute(), identifier)
            files = json.loads(raw)
        except (ValueError, TypeError, RecursionError) as exc:
            raise BundleError("Invalid checkpoint references") from exc
        _require(type(files) is list and type(has_snapshot) is int and has_snapshot in (0, 1),
                 "Invalid checkpoint metadata")
        _require(identifier.casefold() not in identifiers, "Duplicate checkpoint identity")
        identifiers.add(identifier.casefold())
        _require(bool(files) == bool(has_snapshot), "Checkpoint snapshot is incomplete")
        for filename in files:
            filename = _checkpoint_path(filename)
            key = (identifier.casefold(), filename.casefold())
            _require(key not in folded, "Duplicate checkpoint file path")
            folded.add(key)
            refs.append((identifier, filename))
            _require(len(refs) <= MAX_FILES, "Too many checkpoint files")
    for identifier, filename in refs:
        parts = filename.split("/")
        _require(not any((identifier.casefold(), "/".join(parts[:i]).casefold()) in folded
                         for i in range(1, len(parts))), "Conflicting checkpoint paths")
    return sorted(refs)


def _checkpoint_member(identifier, filename):
    digest = hashlib.sha256(_json([identifier, filename])).hexdigest()
    return f"checkpoint-files/{digest}"


def _collect_checkpoints(root, stage, refs, remaining):
    stage.directory("checkpoint-files")
    rows = []
    for identifier, filename in refs:
        source = root / "checkpoints" / identifier / Path(*filename.split("/"))
        metadata = _file_meta(source, MAX_TOTAL_BYTES)
        remaining -= metadata["size_bytes"]
        _require(remaining >= 0, "Bundle exceeds shared total size limit")
        name = _checkpoint_member(identifier, filename)
        _copy_file(source, stage, name, metadata, MAX_TOTAL_BYTES)
        rows.append({"checkpoint_id": identifier, "path": filename, **metadata})
    return rows


def _git_references(database):
    with _database(database) as conn:
        rows = conn.execute("SELECT id,git_stash_ref,source_root FROM session_checkpoints WHERE git_stash_ref IS NOT NULL AND git_stash_ref != '' ORDER BY id LIMIT ?", (MAX_FILES + 1,)).fetchall()
    _require(len(rows) <= MAX_FILES, "Too many checkpoint Git objects")
    refs = []
    for identifier, oid, source in rows:
        try:
            managed_child(Path.cwd().absolute(), identifier)
        except (ValueError, TypeError) as exc:
            raise BundleError("Invalid checkpoint identity") from exc
        _require(type(oid) is str and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid)
                 and type(source) is str and source, "Checkpoint Git source or object is unknown")
        refs.append((identifier, oid, source))
    return refs


def _git_member(identifier):
    return "checkpoint-git/" + hashlib.sha256(identifier.encode("utf-8")).hexdigest()


def _collect_git_packs(stage, refs, remaining):
    stage.directory("checkpoint-git")
    rows = []
    for identifier, oid, source in refs:
        name = _git_member(identifier)
        try:
            with stage.file(name) as output:
                repository = capture_pack(source, oid, output, remaining)
            metadata = _file_meta(stage.path / name, remaining)
            remaining -= metadata["size_bytes"]
            validate_pack(stage.path / name, oid)
        except (OSError, ValueError) as exc:
            raise BundleError(f"Checkpoint {identifier} Git pack unavailable or unsafe; verify source, object availability and repository storage") from exc
        rows.append({"checkpoint_id": identifier, "oid": oid, "repository": repository, **metadata})
    return rows


def _report(manifest, operation):
    return {
        "operation": operation,
        "format": FORMAT,
        "format_version": manifest["format_version"],
        "scope": "data-only",
        "schema_version": manifest["schema_version"],
        "artifact_count": len(manifest["artifacts"]),
        "attachment_count": len(manifest.get("attachments", [])),
        **({"checkpoint_file_count": len(manifest["checkpoint_files"])}
           if manifest["format_version"] >= 10 else {}),
        **({"checkpoint_git_pack_count": len(manifest["checkpoint_git_packs"])}
           if manifest["format_version"] >= 11 else {}),
        **({"checkpoint_git_notice": "Git packs are stored separately and are not imported into repositories during restore. "
                                     "They can include repository history. Repository identity and concurrent Git operations are not proven."}
           if manifest["format_version"] >= 11 else {}),
        **({"office_delivery_count": len(manifest["office_deliveries"])}
           if manifest["format_version"] >= 6 else {}),
        "excluded_vault_entries": manifest["excluded_vault_entries"],
        **({"excluded_attachment_entries": manifest["excluded_attachment_entries"]}
           if manifest["format_version"] >= 2 else {}),
        "notice": NOTICE,
        "exclusions": EXCLUSIONS + (["checkpoint untracked-file snapshots"]
                                    if manifest["format_version"] < 10 else [])
                      + (["Git stash objects (checkpoint tracked changes)"]
                         if manifest["format_version"] < 11 else []),
        "publication_durability": (
            "File payloads flushed and atomic no-overwrite publication; directory-entry "
            "power-loss durability is unverified on Windows"
            if os.name == "nt"
            else "File payloads and publication directory entries fsynced"
        ),
    }


def _collect_vault(root, stage, name, rows, limit):
    stage.directory(name)
    refs = {row["snapshot_id"] for row in rows}
    excluded = {"orphan": 0, "pending": 0}
    vault = root / name
    if not os.path.lexists(vault):
        _require(not refs, "Registered vault is missing")
        return excluded
    with _safe_open(vault, directory=True):
        info = vault.lstat()
        _owner(info)
        _require(os.name == "nt" or not info.st_mode & 0o077, "Vault must be private")
        for entry in os.scandir(vault):
            if entry.name not in refs:
                excluded["pending" if entry.name.startswith(".pending-") else "orphan"] += 1
        for row in rows:
            _copy_file(vault / row["snapshot_id"], stage, f"{name}/{row['snapshot_id']}",
                       {key: row[key] for key in ("size_bytes", "sha256")}, limit)
    return excluded


def create_bundle(data_dir, output):
    root, output = _path(data_dir), _path(output)
    _directory(root)
    _absent(output)
    _require(
        not output.is_relative_to(root), "Bundle output must be outside the source data directory"
    )
    with _safe_open(root, directory=True), acquire_service_lease(root) as lease:
        root_identity = _directory(root)
        with _stage(output.parent) as stage:
            with stage.file("data.db"):
                pass
            _snapshot(root, stage, lease)
            inventory = _inventory(stage.path / "data.db")
            _require(inventory["schema_version"] == SCHEMA_VERSION,
                     "Create requires known Schema049; bundle v1 through v9 are verify/restore only")
            database = _file_meta(stage.path / "data.db", MAX_DATABASE_BYTES)
            excluded = _collect_vault(root, stage, VAULT, inventory["artifacts"], MAX_BYTES)
            excluded_attachments = _collect_vault(root, stage, ATTACHMENT_VAULT,
                                                  inventory["attachments"], MAX_ATTACHMENT_BYTES)
            references = _checkpoint_references(stage.path / "data.db")
            remaining = MAX_TOTAL_BYTES - database["size_bytes"] - sum(
                row["size_bytes"] for row in inventory["artifacts"] + inventory["attachments"]
            )
            checkpoint_files = _collect_checkpoints(root, stage, references, remaining)
            remaining -= sum(row["size_bytes"] for row in checkpoint_files)
            git_packs = _collect_git_packs(stage, _git_references(stage.path / "data.db"), remaining)
            _require(len(checkpoint_files) + len(git_packs) + len(inventory["artifacts"]) + len(inventory["attachments"]) <= MAX_FILES,
                     "Too many files in bundle")
            _require(
                database["size_bytes"] + sum(row["size_bytes"] for row in
                                             inventory["artifacts"] + inventory["attachments"] + checkpoint_files + git_packs)
                <= MAX_TOTAL_BYTES,
                "Bundle exceeds total size limit",
            )
            manifest = {
                "format": FORMAT,
                "format_version": FORMAT_VERSION,
                "app_version": __version__,
                "created_at": datetime.now(UTC).isoformat(),
                **inventory,
                "database": database,
                "excluded_vault_entries": excluded,
                "excluded_attachment_entries": excluded_attachments,
                "checkpoint_files": checkpoint_files,
                "checkpoint_git_packs": git_packs,
            }
            payload = _json(manifest)
            _require(len(payload) <= MAX_MANIFEST_BYTES, "Manifest exceeds limit")
            with stage.file("manifest.json") as stream:
                stream.write(payload)
            _fsync_directory(stage.path / VAULT)
            _fsync_directory(stage.path / ATTACHMENT_VAULT)
            _fsync_directory(stage.path)
            with (
                stage.file("bundle.zip") as stream,
                zipfile.ZipFile(
                    stream, "w", compression=zipfile.ZIP_STORED, allowZip64=False
                ) as archive,
            ):
                for name in [
                    "manifest.json",
                    "data.db",
                    *[f"{VAULT}/{row['snapshot_id']}" for row in inventory["artifacts"]],
                    *[f"{ATTACHMENT_VAULT}/{row['snapshot_id']}" for row in inventory["attachments"]],
                    *[_checkpoint_member(row["checkpoint_id"], row["path"]) for row in checkpoint_files],
                    *[_git_member(row["checkpoint_id"]) for row in git_packs],
                ]:
                    archive.write(stage.path / name, name)
            # Verify the complete archive with the same path used by restore.
            verify_bundle(stage.path / "bundle.zip")
            for row in checkpoint_files:
                source = root / "checkpoints" / row["checkpoint_id"] / Path(*row["path"].split("/"))
                _file_meta(source, MAX_TOTAL_BYTES,
                           expected={key: row[key] for key in ("size_bytes", "sha256")})
            lease.assert_owned(root)
            _check_directory(root, root_identity)
            _check_directory(output.parent, stage.parent_identity)
            _absent(output)
            os.link(stage.path / "bundle.zip", output, follow_symlinks=False)
            try:
                _fsync_directory(output.parent)
            except BaseException:
                original = (stage.path / "bundle.zip").stat()
                current = output.lstat()
                if (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino):
                    output.unlink()
                raise
            return _report(manifest, "create")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


def _manifest(payload):
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(BundleError("Invalid JSON")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise BundleError("Invalid bundle manifest") from exc
    keys = {
        "format",
        "format_version",
        "app_version",
        "created_at",
        "schema_version",
        "schema_sha256",
        "service_id",
        "artifacts",
        "database",
        "excluded_vault_entries",
    }
    _require(type(value) is dict, "Invalid manifest fields")
    version = value.get("format_version")
    _require(type(version) is int and version in SUPPORTED_SCHEMAS, "Unsupported bundle format")
    if version >= 2:
        keys |= {"attachments", "excluded_attachment_entries"}
    if version >= 6:
        keys.add("office_deliveries")
    if version >= 10:
        keys.add("checkpoint_files")
    if version >= 11:
        keys.add("checkpoint_git_packs")
    _require(set(value) == keys, "Invalid manifest fields")
    _require(
        value["format"] == FORMAT
        and type(value["format_version"]) is int
        and value["format_version"] in SUPPORTED_SCHEMAS,
        "Unsupported bundle format",
    )
    _require(
        value["app_version"] == __version__
        and type(value["schema_version"]) is int
        and value["schema_version"] == SUPPORTED_SCHEMAS[version],
        "Unsupported app or schema version",
    )
    _require(
        type(value["created_at"]) is str and len(value["created_at"]) <= 100,
        "Invalid bundle timestamp",
    )
    _require(
        type(value["schema_sha256"]) is str and _HEX.fullmatch(value["schema_sha256"]),
        "Invalid schema hash",
    )
    _require(
        value["service_id"] is None
        or type(value["service_id"]) is str
        and _SERVICE.fullmatch(value["service_id"]),
        "Invalid service identity",
    )
    _require(
        type(value["artifacts"]) is list and len(value["artifacts"]) <= MAX_FILES,
        "Invalid artifact count",
    )
    database = value["database"]
    _metadata(database, MAX_DATABASE_BYTES)
    total, snapshots, ids = database["size_bytes"], set(), set()
    for row in value["artifacts"]:
        _require(
            type(row) is dict
            and set(row) == {"artifact_id", "snapshot_id", "size_bytes", "sha256", "version"},
            "Invalid artifact manifest",
        )
        _require(
            type(row["artifact_id"]) is str
            and _ARTIFACT.fullmatch(row["artifact_id"])
            and type(row["snapshot_id"]) is str
            and _SNAPSHOT.fullmatch(row["snapshot_id"])
            and type(row["version"]) is int
            and row["version"] > 0,
            "Invalid artifact identity",
        )
        _metadata({key: row[key] for key in ("size_bytes", "sha256")}, MAX_BYTES)
        _require(
            row["snapshot_id"] not in snapshots and row["artifact_id"] not in ids,
            "Duplicate artifact identity",
        )
        snapshots.add(row["snapshot_id"])
        ids.add(row["artifact_id"])
        total += row["size_bytes"]
    if version >= 2:
        attachments = value["attachments"]
        _require(type(attachments) is list and len(attachments) + len(value["artifacts"]) <= MAX_FILES,
                 "Invalid shared vault file count")
        attachment_ids, attachment_snapshots = set(), set()
        for row in attachments:
            _require(type(row) is dict and set(row) == {"attachment_id", "snapshot_id", "size_bytes",
                                                      "sha256", "version"}, "Invalid attachment manifest")
            _require(type(row["attachment_id"]) is str and _ATTACHMENT.fullmatch(row["attachment_id"])
                     and type(row["snapshot_id"]) is str and _SNAPSHOT.fullmatch(row["snapshot_id"])
                     and type(row["version"]) is int and row["version"] > 0, "Invalid attachment identity")
            _metadata({key: row[key] for key in ("size_bytes", "sha256")}, MAX_ATTACHMENT_BYTES)
            _require(row["attachment_id"] not in attachment_ids
                     and row["snapshot_id"] not in attachment_snapshots, "Duplicate attachment identity")
            attachment_ids.add(row["attachment_id"])
            attachment_snapshots.add(row["snapshot_id"])
            total += row["size_bytes"]
    if version >= 6:
        _office_manifest(value["office_deliveries"], value["service_id"], value["attachments"])
    if version >= 10:
        rows = value["checkpoint_files"]
        _require(type(rows) is list and len(rows) + len(value["artifacts"]) + len(value["attachments"]) <= MAX_FILES,
                 "Invalid checkpoint file count")
        paths, members = set(), set()
        for row in rows:
            _require(type(row) is dict and set(row) == {"checkpoint_id", "path", "size_bytes", "sha256"},
                     "Invalid checkpoint file metadata")
            try:
                managed_child(Path.cwd().absolute(), row["checkpoint_id"])
            except (TypeError, ValueError) as exc:
                raise BundleError("Invalid checkpoint identity") from exc
            filename = _checkpoint_path(row["path"])
            key = (row["checkpoint_id"].casefold(), filename.casefold())
            member = _checkpoint_member(row["checkpoint_id"], filename)
            _require(key not in paths and member not in members, "Duplicate checkpoint file")
            paths.add(key)
            members.add(member)
            _metadata({key: row[key] for key in ("size_bytes", "sha256")}, MAX_TOTAL_BYTES)
            total += row["size_bytes"]
    if version >= 11:
        packs = value["checkpoint_git_packs"]
        _require(type(packs) is list and len(packs) + len(value["checkpoint_files"]) + len(value["artifacts"]) + len(value["attachments"]) <= MAX_FILES,
                 "Invalid checkpoint Git pack count")
        ids = set()
        for row in packs:
            _require(type(row) is dict and set(row) == {"checkpoint_id", "oid", "size_bytes", "sha256", "repository"},
                     "Invalid checkpoint Git pack metadata")
            try:
                managed_child(Path.cwd().absolute(), row["checkpoint_id"])
            except (ValueError, TypeError) as exc:
                raise BundleError("Invalid checkpoint Git identity") from exc
            _require(type(row["oid"]) is str and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", row["oid"])
                     and row["checkpoint_id"].casefold() not in ids, "Invalid checkpoint Git reference")
            ids.add(row["checkpoint_id"].casefold())
            identity = row["repository"]
            _require(type(identity) is dict and set(identity) == {"worktree_device", "worktree_inode", "git_device", "git_inode"}
                     and all(type(value) is int and value >= 0 for value in identity.values())
                     and identity["worktree_inode"] > 0 and identity["git_inode"] > 0,
                     "Invalid checkpoint repository identity")
            _metadata({key: row[key] for key in ("size_bytes", "sha256")}, MAX_TOTAL_BYTES)
            total += row["size_bytes"]
    _require(total <= MAX_TOTAL_BYTES, "Bundle exceeds shared total size limit")
    for key in ["excluded_vault_entries", *(["excluded_attachment_entries"] if version >= 2 else [])]:
        counts = value[key]
        _require(type(counts) is dict and set(counts) == {"orphan", "pending"}
                 and all(type(count) is int and count >= 0 for count in counts.values()),
                 "Invalid excluded entry counts")
    return value


def _office_manifest(rows, service_id, attachments):
    _require(type(rows) is list and len(rows) <= len(attachments), "Invalid Office delivery count")
    by_id = {row["attachment_id"]: row for row in attachments}
    ids, references, requests = set(), set(), set()
    for raw in rows:
        _require(type(raw) is dict and set(raw) == set(OFFICE_RECORD_KEYS),
                 "Invalid Office manifest fields")
        try:
            row = validate_office_record({
                **raw, "validation_report": json.dumps(raw["validation_report"],
                                                       ensure_ascii=False, allow_nan=False),
            })
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise BundleError("Invalid Office manifest metadata") from exc
        attachment = by_id.get(row["attachment_id"])
        report = row["validation_report"]
        request = (row["project_id"], row["request_key"])
        _require(attachment is not None and row["service_id"] == service_id
                 and attachment["sha256"] == report["file_sha256"]
                 and attachment["size_bytes"] == report["size_bytes"]
                 and row["id"] not in ids and row["attachment_id"] not in references
                 and request not in requests, "Invalid or duplicate Office manifest reference")
        ids.add(row["id"])
        references.add(row["attachment_id"])
        requests.add(request)


def _metadata(value, limit):
    _require(
        type(value) is dict
        and set(value) == {"size_bytes", "sha256"}
        and type(value["size_bytes"]) is int
        and 0 <= value["size_bytes"] <= limit
        and type(value["sha256"]) is str
        and _HEX.fullmatch(value["sha256"]),
        "Invalid file size or hash metadata",
    )


def _read_member(archive, member, output, expected):
    digest, total = hashlib.sha256(), 0
    with archive.open(member, "r") as stream:
        while chunk := stream.read(min(1024 * 1024, expected["size_bytes"] + 1 - total)):
            total += len(chunk)
            _require(total <= expected["size_bytes"], "Archive member exceeds declared size")
            digest.update(chunk)
            output.write(chunk)
    _require(
        total == expected["size_bytes"] and digest.hexdigest() == expected["sha256"],
        "Archive member size or hash does not match",
    )


def _zip_header(stream, size):
    # Bound central-directory allocation BEFORE ZipFile parses it. Our v1 writer
    # emits no comments, ZIP64, prefixes or trailing payloads.
    _require(size >= 22, "Truncated archive")
    stream.seek(size - 22)
    end = stream.read(22)
    signature, disk, start_disk, disk_count, count, central_bytes, offset, comment = struct.unpack(
        "<4s4H2IH", end
    )
    _require(
        signature == b"PK\x05\x06"
        and disk == start_disk == comment == 0
        and disk_count == count
        and 2 <= count <= MAX_FILES + 2
        and central_bytes <= (MAX_FILES + 2) * 256
        and offset + central_bytes == size - 22,
        "Unsupported archive structure or member count",
    )
    stream.seek(0)
    return offset, count


def _zip_layout(stream, members, central_offset):
    offset = 0
    for row in sorted(members, key=lambda item: item.header_offset):
        _require(row.header_offset == offset, "Archive contains unlisted local payload")
        stream.seek(offset)
        raw = stream.read(30)
        _require(len(raw) == 30, "Truncated archive header")
        signature, version, flags, compression, _time, _date, crc, compressed, size, name, extra = (
            struct.unpack("<4s5H3I2H", raw)
        )
        _require(
            signature == b"PK\x03\x04"
            and version <= 20
            and flags == 0
            and compression == zipfile.ZIP_STORED
            and extra == 0
            and name <= 256
            and crc == row.CRC
            and size == row.file_size
            and compressed == row.compress_size
            and stream.read(name) == row.filename.encode("ascii"),
            "Invalid local archive header",
        )
        offset += 30 + name + size
    _require(offset == central_offset, "Archive contains extra local data")


def _unpack(bundle, stage):
    with _safe_open(bundle) as fd:
        before = os.fstat(fd)
        _regular(before)
        _owner(before)
        _require(before.st_size <= MAX_ARCHIVE_BYTES, "Archive exceeds bundle limit")
        # dup keeps safe-open's descriptor lifetime independent of the zip stream.
        with os.fdopen(os.dup(fd), "rb") as stream:
            central_offset, count = _zip_header(stream, before.st_size)
            with zipfile.ZipFile(stream) as archive:
                return _unpack_archive(
                    bundle, stage, archive, stream, fd, before, central_offset, count
                )


def _unpack_archive(bundle, stage, archive, stream, fd, before, central_offset, count):
    members = archive.infolist()
    _require(len(members) == count, "Invalid archive member count")
    names = [row.filename for row in members]
    _require(len(names) == len(set(names)), "Duplicate archive member")
    for row in members:
        mode = row.external_attr >> 16
        _require(
            row.compress_type == zipfile.ZIP_STORED
            and row.flag_bits == 0
            and not row.is_dir()
            and stat.S_IFMT(mode) in (0, stat.S_IFREG)
            and not row.extra
            and row.file_size == row.compress_size,
            "Archive members must be plain stored files without links or extra fields",
        )
    _zip_layout(stream, members, central_offset)
    _require("manifest.json" in names, "Missing bundle manifest")
    manifest_info = archive.getinfo("manifest.json")
    _require(manifest_info.file_size <= MAX_MANIFEST_BYTES, "Manifest exceeds size limit")
    manifest = _manifest(archive.read(manifest_info))
    expected = {
        "data.db": manifest["database"],
        **{
            f"{VAULT}/{row['snapshot_id']}": {key: row[key] for key in ("size_bytes", "sha256")}
            for row in manifest["artifacts"]
        },
        **{
            _checkpoint_member(row["checkpoint_id"], row["path"]):
            {key: row[key] for key in ("size_bytes", "sha256")}
            for row in manifest.get("checkpoint_files", [])
        },
        **{
            _git_member(row["checkpoint_id"]): {key: row[key] for key in ("size_bytes", "sha256")}
            for row in manifest.get("checkpoint_git_packs", [])
        },
        **{
            f"{ATTACHMENT_VAULT}/{row['snapshot_id']}": {key: row[key] for key in ("size_bytes", "sha256")}
            for row in manifest.get("attachments", [])
        },
    }
    _require(set(names) == {"manifest.json", *expected}, "Extra or missing archive members")
    stage.directory(VAULT)
    if manifest["format_version"] >= 2:
        stage.directory(ATTACHMENT_VAULT)
    checkpoint_destinations = {
        _checkpoint_member(row["checkpoint_id"], row["path"]):
        "checkpoints/" + row["checkpoint_id"] + "/" + row["path"]
        for row in manifest.get("checkpoint_files", [])
    }
    checkpoint_destinations.update({
        _git_member(row["checkpoint_id"]): "checkpoint-git-packs/" + row["checkpoint_id"] + ".pack"
        for row in manifest.get("checkpoint_git_packs", [])
    })
    for name, metadata in expected.items():
        member = archive.getinfo(name)
        _require(member.file_size == metadata["size_bytes"], "Archive size mismatch")
        destination = checkpoint_destinations.get(name, name)
        stage.parents(destination)
        with stage.file(destination) as target:
            _read_member(archive, member, target, metadata)
    actual = _inventory(stage.path / "data.db")
    _require(
        all(manifest[key] == value for key, value in actual.items()),
        "SQLite metadata and manifest references differ",
    )
    if manifest["format_version"] >= 10:
        _require(_checkpoint_references(stage.path / "data.db") == sorted(
            (row["checkpoint_id"], row["path"]) for row in manifest["checkpoint_files"]),
            "SQLite and checkpoint manifest references differ")
    if manifest["format_version"] >= 11:
        _require([(row["checkpoint_id"], row["oid"]) for row in manifest["checkpoint_git_packs"]]
                 == [(identifier, oid) for identifier, oid, _ in _git_references(stage.path / "data.db")],
                 "SQLite and checkpoint Git references differ")
        for row in manifest["checkpoint_git_packs"]:
            validate_pack(stage.path / "checkpoint-git-packs" / (row["checkpoint_id"] + ".pack"), row["oid"])
            with stage.file("checkpoint-git-packs/" + row["checkpoint_id"] + ".json") as target:
                target.write(_json(row))
    with _database(stage.path / "data.db") as conn:
        for snapshot, source in conn.execute("SELECT snapshot_id,source_path FROM text_artifacts"):
            _parse((stage.path / VAULT / snapshot).read_bytes(), source)
    _require(
        _identity(before) == _identity(os.fstat(fd))
        and _identity(before)[:4] == _identity(bundle.lstat())[:4],
        "Archive changed during verification",
    )
    return manifest


def verify_bundle(bundle):
    bundle = _path(bundle)
    # Verification needs a bounded private scratch space; does not modify the archive.
    with _stage(Path(tempfile.gettempdir()).absolute(), owned_parent=False) as stage:
        return _report(_unpack(bundle, stage), "verify")


def _publish_directory(source, target):
    """Atomic directory rename with no replacement, including racing empty targets."""
    _absent(target)
    if os.name == "nt":
        os.rename(source, target)  # Windows refuses replacement of any existing destination.
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        _require(rename is not None, "Atomic no-overwrite directory publication is unavailable")
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
            raise OSError(
                ctypes.get_errno(), "Cannot publish restore directory without replacement"
            )
    elif sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(os.fsencode(source), os.fsencode(target), 4) != 0:  # RENAME_EXCL
            raise OSError(
                ctypes.get_errno(), "Cannot publish restore directory without replacement"
            )
    else:
        raise BundleError("Atomic no-overwrite directory publication is unsupported")


def restore_bundle(bundle, new_data_dir):
    bundle, target = _path(bundle), _path(new_data_dir)
    _absent(target)
    _require(
        target != Path.cwd().absolute() and target.parent != target,
        "Restore requires a new explicitly named directory",
    )
    parent_identity = _directory(target.parent)
    with _stage(target.parent) as stage:
        manifest = _unpack(bundle, stage)
        _check_directory(target.parent, parent_identity)
        _fsync_directory(stage.path / VAULT)
        if manifest["format_version"] >= 2:
            _fsync_directory(stage.path / ATTACHMENT_VAULT)
        for directory, _ in reversed(stage.directories[1:]):
            _fsync_directory(directory)
        _fsync_directory(stage.path)
        _publish_directory(stage.path, target)
        stage.relocate(target)
        _fsync_directory(target.parent)
        stage.published = True
        return _report(manifest, "restore")


def import_checkpoint_git(data_dir, checkpoint_id, repo, *, confirmed=False):
    """Explicitly import a verified pack into its recorded repository; do not apply it."""
    _require(confirmed is True, "Explicit confirmation of the recorded repository is required")
    root, target = _path(data_dir), _path(repo)
    _directory(root)
    try:
        managed_child(root / "checkpoint-git-packs", checkpoint_id)
    except (ValueError, TypeError) as exc:
        raise BundleError("Invalid checkpoint identity") from exc
    with _safe_open(root, directory=True), acquire_service_lease(root) as lease:
        with _stage(Path(tempfile.gettempdir()).absolute(), owned_parent=False) as stage:
            with stage.file("data.db"):
                pass
            _snapshot(root, stage, lease)
            refs = _git_references(stage.path / "data.db")
            selected = [(oid, source) for identifier, oid, source in refs if identifier == checkpoint_id]
            _require(len(selected) == 1, "Checkpoint Git reference is unavailable")
            oid, source = selected[0]
            _require(str(target) == source, "Target does not match recorded checkpoint source")
            pack = root / "checkpoint-git-packs" / (checkpoint_id + ".pack")
            metadata_path = root / "checkpoint-git-packs" / (checkpoint_id + ".json")
            with _safe_open(metadata_path) as fd:
                info = os.fstat(fd)
                _regular(info)
                _require(info.st_size <= 8192, "Checkpoint Git metadata exceeds limit")
                payload = os.read(fd, 8193)
                _require(len(payload) == info.st_size and _identity(info) == _identity(os.fstat(fd))
                         and _identity(info)[:4] == _identity(metadata_path.lstat())[:4],
                         "Checkpoint Git metadata changed while reading")
            try:
                saved = json.loads(payload, object_pairs_hook=_pairs)
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise BundleError("Invalid checkpoint Git metadata") from exc
            _require(type(saved) is dict and set(saved) == {"checkpoint_id", "oid", "size_bytes", "sha256", "repository"}
                     and saved["checkpoint_id"] == checkpoint_id and saved["oid"] == oid,
                     "Checkpoint Git metadata differs from database")
            _metadata({key: saved[key] for key in ("size_bytes", "sha256")}, MAX_TOTAL_BYTES)
            identity = saved["repository"]
            _require(type(identity) is dict and set(identity) == {"worktree_device", "worktree_inode", "git_device", "git_inode"}
                     and all(type(value) is int and value >= 0 for value in identity.values())
                     and identity["worktree_inode"] > 0 and identity["git_inode"] > 0,
                     "Invalid checkpoint repository identity")
            digest = import_pack(pack, oid, source, str(target), MAX_TOTAL_BYTES, saved)
            lease.assert_owned(root)
            return {"operation": "import-checkpoint-git", "checkpoint_id": checkpoint_id,
                    "oid": oid, "pack_sha256": digest,
                    "notice": "Objects imported without changing refs or applying the checkpoint. "
                              "Repository path and base commit do not prove repository identity; "
                              "concurrent Git operations and GC are not fenced. "
                              "Failure or interruption may leave unreachable objects."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Offline SQLite + registered vault snapshot only")
    create.add_argument("--data-dir", required=True)
    create.add_argument("--output", required=True)
    verify = commands.add_parser(
        "verify", help="Verify a trusted data-only bundle without starting anything"
    )
    verify.add_argument("--bundle", required=True)
    restore = commands.add_parser(
        "restore", help="Data-only clone into a directory that must not exist"
    )
    restore.add_argument("--bundle", required=True)
    restore.add_argument("--new-data-dir", required=True)
    import_git = commands.add_parser("import-checkpoint-git", help="Explicitly import checkpoint objects; does not apply changes")
    import_git.add_argument("--data-dir", required=True)
    import_git.add_argument("--checkpoint-id", required=True)
    import_git.add_argument("--repo", required=True)
    import_git.add_argument("--confirm-source", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create_bundle(args.data_dir, args.output)
        elif args.command == "verify":
            result = verify_bundle(args.bundle)
        elif args.command == "restore":
            result = restore_bundle(args.bundle, args.new_data_dir)
        else:
            result = import_checkpoint_git(args.data_dir, args.checkpoint_id, args.repo,
                                           confirmed=args.confirm_source)
    except (
        BundleError,
        ServiceLeaseError,
        ValueError,
        OSError,
        sqlite3.Error,
        zipfile.BadZipFile,
        RuntimeError,
    ) as exc:
        # No database row, credentials or arbitrary archive-controlled text in diagnostics.
        message = (
            str(exc)
            if isinstance(exc, (BundleError, ServiceLeaseError))
            else "Bundle operation failed validation or I/O"
        )
        if args.command == "import-checkpoint-git":
            message += "; import may have left unreachable Git objects; refs and worktree were not intentionally changed"
        print(json.dumps({"ok": False, "error": message}), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
