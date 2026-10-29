"""Checkpoint bytes in v10 bundles and frozen v9 compatibility."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import zipfile
from pathlib import Path

import pytest

from minimax_code import bundle
from minimax_code.storage.db import AsyncDatabase


@pytest.fixture
async def source(tmp_path):
    root = tmp_path / "data"
    db = AsyncDatabase(root / "data.db")
    await db.connect()
    await db.migrate()
    await db.execute("INSERT INTO session_checkpoints(id,session_id,label,created_at,untracked_files,has_untracked_snapshot) VALUES(?,?,?,?,?,?)",
                     ("ckpt_a", "s", "saved", "now", json.dumps(["nested/中文.txt"]), 1))
    await db.close()
    path = root / "checkpoints" / "ckpt_a" / "nested" / "中文.txt"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"checkpoint bytes\x00")
    return root, path


def test_v10_roundtrip_preserves_checkpoint_bytes(source, tmp_path):
    root, path = source
    archive = tmp_path / "backup.zip"
    report = bundle.create_bundle(root, archive)
    assert report["format_version"] == 10
    assert "Git stash objects (checkpoint tracked changes)" in report["exclusions"]
    assert bundle.verify_bundle(archive)["format_version"] == 10
    target = tmp_path / "restored"
    bundle.restore_bundle(archive, target)
    assert (target / "checkpoints" / "ckpt_a" / "nested" / "中文.txt").read_bytes() == path.read_bytes()
    with pytest.raises(ValueError):
        bundle.restore_bundle(archive, target)


def test_frozen_v9_remains_readable_without_checkpoint_files(source, tmp_path):
    root, _ = source
    path = Path(__file__).parent / "fixtures" / "bundle-v9-writer.py.txt"
    loader = importlib.machinery.SourceFileLoader("minimax_code._frozen_v9_writer", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    writer = importlib.util.module_from_spec(spec)
    loader.exec_module(writer)
    assert writer.FORMAT_VERSION == 9
    archive = tmp_path / "old.zip"
    writer.create_bundle(root, archive)
    report = bundle.verify_bundle(archive)
    assert report["format_version"] == 9
    assert "checkpoint untracked-file snapshots" in report["exclusions"]
    target = tmp_path / "old-restored"
    bundle.restore_bundle(archive, target)
    assert not (target / "checkpoints").exists()


@pytest.mark.parametrize("kind", ["missing", "hardlink", "symlink", "changed"])
def test_unsafe_or_changing_snapshot_never_publishes(source, tmp_path, monkeypatch, kind):
    root, path = source
    if kind == "missing":
        path.unlink()
    elif kind == "hardlink":
        os.link(path, tmp_path / "hardlink")
    elif kind == "symlink":
        path.unlink()
        target = tmp_path / "elsewhere"
        target.write_bytes(b"outside")
        try:
            path.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation unavailable")
    else:
        original = bundle._copy_file
        def change(source_path, stage, name, expected, limit=bundle.MAX_BYTES):
            if name.startswith("checkpoint-files/"):
                path.write_bytes(b"changed")
            return original(source_path, stage, name, expected, limit)
        monkeypatch.setattr(bundle, "_copy_file", change)
    output = tmp_path / "unsafe.zip"
    with pytest.raises((ValueError, OSError)):
        bundle.create_bundle(root, output)
    assert not output.exists()


@pytest.mark.parametrize("files", [["x", "x"], ["../outside"], ["x", "x/y"]])
async def test_invalid_references_reject_without_publishing(source, tmp_path, files):
    root, _ = source
    db = AsyncDatabase(root / "data.db")
    await db.connect()
    await db.execute("UPDATE session_checkpoints SET untracked_files=?", (json.dumps(files),))
    await db.close()
    output = tmp_path / "invalid.zip"
    with pytest.raises(ValueError):
        bundle.create_bundle(root, output)
    assert not output.exists()


@pytest.mark.parametrize("kind", ["hash", "duplicate", "missing"])
def test_corrupt_checkpoint_archive_never_publishes(source, tmp_path, kind):
    root, _ = source
    archive = tmp_path / "valid.zip"
    bundle.create_bundle(root, archive)
    with zipfile.ZipFile(archive) as reader:
        members = [(item.filename, reader.read(item)) for item in reader.infolist()]
    manifest = json.loads(members[0][1])
    if kind == "hash":
        manifest["checkpoint_files"][0]["sha256"] = "0" * 64
    elif kind == "duplicate":
        manifest["checkpoint_files"].append(manifest["checkpoint_files"][0].copy())
    else:
        members = [(name, data) for name, data in members if not name.startswith("checkpoint-files/")]
    members[0] = ("manifest.json", json.dumps(manifest).encode())
    invalid = tmp_path / "invalid.zip"
    with zipfile.ZipFile(invalid, "w", compression=zipfile.ZIP_STORED) as writer:
        for name, data in members:
            writer.writestr(name, data)
    target = tmp_path / "unsafe-restored"
    with pytest.raises(ValueError):
        bundle.restore_bundle(invalid, target)
    assert not target.exists()


@pytest.mark.parametrize("limit", ["count", "size"])
def test_checkpoint_bundle_limits_fail_without_publication(source, tmp_path, monkeypatch, limit):
    root, _ = source
    if limit == "count":
        monkeypatch.setattr(bundle, "MAX_FILES", 0)
    else:
        monkeypatch.setattr(bundle, "MAX_TOTAL_BYTES", (root / "data.db").stat().st_size + 1)
    output = tmp_path / "too-large.zip"
    with pytest.raises(ValueError):
        bundle.create_bundle(root, output)
    assert not output.exists()


def test_snapshot_change_after_archive_verification_prevents_publication(source, tmp_path, monkeypatch):
    root, path = source
    verify = bundle.verify_bundle

    def verify_then_change(archive):
        report = verify(archive)
        path.write_bytes(b"changed after archive was written")
        return report

    monkeypatch.setattr(bundle, "verify_bundle", verify_then_change)
    output = tmp_path / "changed.zip"
    with pytest.raises(ValueError):
        bundle.create_bundle(root, output)
    assert not output.exists()
