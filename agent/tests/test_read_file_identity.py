from __future__ import annotations

import hashlib
import os

import pytest

from minimax_code.agent.tools.file_ops import ReadFileTool
from minimax_code.execution import ExecutionPolicy, execution_scope


@pytest.mark.skipif(os.name != "nt", reason="Windows path and handle ctime semantics")
async def test_read_file_accepts_different_path_and_handle_ctime(tmp_path, monkeypatch):
    target = tmp_path / "file.txt"
    target.write_bytes(b"reviewed content")
    original_fstat = os.fstat

    class HandleStat:
        def __init__(self, info):
            self.info = info

        def __getattr__(self, name):
            if name == "st_ctime_ns":
                return self.info.st_ctime_ns + 1_000_000_000
            return getattr(self.info, name)

    monkeypatch.setattr(os, "fstat", lambda fd: HandleStat(original_fstat(fd)))
    with execution_scope(ExecutionPolicy(tmp_path)):
        result = await ReadFileTool().run(path="file.txt")
    assert result.success
    assert result.output["content"] == "reviewed content"
    assert result.output["sha256"] == hashlib.sha256(b"reviewed content").hexdigest()


async def test_read_file_rejects_same_content_replacement(tmp_path, monkeypatch):
    target = tmp_path / "file.txt"
    replacement = tmp_path / "replacement.txt"
    target.write_bytes(b"original")
    replacement.write_bytes(b"original")
    original_open = os.open

    def replace_before_open(path, flags, *args, **kwargs):
        if path == target and replacement.exists():
            os.replace(replacement, target)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_before_open)
    with execution_scope(ExecutionPolicy(tmp_path)):
        result = await ReadFileTool().run(path="file.txt")
    assert not result.success
    assert "changed before reading" in result.error


async def test_read_file_rejects_metadata_change_while_hashing(tmp_path, monkeypatch):
    target = tmp_path / "file.txt"
    target.write_bytes(b"original")
    original_sha256 = hashlib.sha256

    class MutatingHash:
        def __init__(self):
            self.inner = original_sha256()

        def update(self, data):
            self.inner.update(data)
            info = target.stat()
            os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns - 2_000_000_000))

        def hexdigest(self):
            return self.inner.hexdigest()

    monkeypatch.setattr(hashlib, "sha256", MutatingHash)
    with execution_scope(ExecutionPolicy(tmp_path)):
        result = await ReadFileTool().run(path="file.txt")
    assert not result.success
    assert "changed while reading" in result.error
