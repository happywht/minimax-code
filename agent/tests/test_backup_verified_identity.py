from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from minimax_code.agent.backup import BackupManager


@pytest.mark.skipif(os.name != "nt", reason="Windows path and handle ctime semantics")
def test_verified_backup_accepts_different_path_and_handle_ctime(tmp_path, monkeypatch):
    source = tmp_path / "file.txt"
    source.write_bytes(b"reviewed")
    digest = hashlib.sha256(b"reviewed").hexdigest()
    original_fstat = os.fstat

    class HandleStat:
        def __init__(self, info):
            self.info = info

        def __getattr__(self, name):
            if name == "st_ctime_ns":
                return self.info.st_ctime_ns + 1_000_000_000
            return getattr(self.info, name)

    monkeypatch.setattr(os, "fstat", lambda fd: HandleStat(original_fstat(fd)))
    result = BackupManager(tmp_path).backup_verified(source, expected_sha256=digest)
    assert Path(result["backup_path"]).read_bytes() == b"reviewed"
    assert result["sha256"] == digest
    assert Path(result["manifest_path"]).is_file()


def test_verified_backup_rejects_same_content_replacement(tmp_path, monkeypatch):
    source = tmp_path / "file.txt"
    replacement = tmp_path / "replacement.txt"
    source.write_bytes(b"original")
    replacement.write_bytes(b"original")
    digest = hashlib.sha256(b"original").hexdigest()
    original_open = os.open

    def replace_before_open(path, flags, *args, **kwargs):
        if path == source and replacement.exists():
            os.replace(replacement, source)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_before_open)
    with pytest.raises(OSError, match="changed before copying"):
        BackupManager(tmp_path).backup_verified(source, expected_sha256=digest)
    assert not list((tmp_path / ".minimax" / "backups").iterdir())


def test_verified_backup_rejects_source_change_during_copy(tmp_path, monkeypatch):
    source = tmp_path / "file.txt"
    source.write_bytes(b"original")
    digest = hashlib.sha256(b"original").hexdigest()
    original_sha256 = hashlib.sha256

    class MutatingHash:
        def __init__(self, *args, **kwargs):
            self.inner = original_sha256(*args, **kwargs)

        def update(self, data):
            self.inner.update(data)
            info = source.stat()
            os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns - 2_000_000_000))

        def hexdigest(self):
            return self.inner.hexdigest()

    monkeypatch.setattr(hashlib, "sha256", MutatingHash)
    with pytest.raises(OSError, match="changed or does not match"):
        BackupManager(tmp_path).backup_verified(source, expected_sha256=digest)
    assert source.read_bytes() == b"original"
    assert not list((tmp_path / ".minimax" / "backups").iterdir())


def test_verified_backup_rejects_wrong_digest(tmp_path):
    source = tmp_path / "file.txt"
    source.write_bytes(b"original")
    with pytest.raises(OSError, match="does not match"):
        BackupManager(tmp_path).backup_verified(source, expected_sha256="0" * 64)
    assert source.read_bytes() == b"original"
    assert not list((tmp_path / ".minimax" / "backups").iterdir())
