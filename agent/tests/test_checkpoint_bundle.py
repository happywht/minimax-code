"""Checkpoint bytes in v10 bundles and frozen v9 compatibility."""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from minimax_code import bundle
from minimax_code.checkpoint_git_bundle import validate_pack
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
    assert report["format_version"] == 11
    assert bundle.verify_bundle(archive)["format_version"] == 11
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


def test_frozen_v10_remains_readable(source, tmp_path):
    root, path = source
    source_file = Path(__file__).parent / "fixtures" / "bundle-v10-writer.py.txt"
    loader = importlib.machinery.SourceFileLoader("minimax_code._frozen_v10_writer", str(source_file))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    writer = importlib.util.module_from_spec(spec)
    loader.exec_module(writer)
    archive = tmp_path / "v10.zip"
    assert writer.create_bundle(root, archive)["format_version"] == 10
    assert bundle.verify_bundle(archive)["format_version"] == 10
    target = tmp_path / "v10-restored"
    bundle.restore_bundle(archive, target)
    assert (target / "checkpoints" / "ckpt_a" / "nested" / "中文.txt").read_bytes() == path.read_bytes()


@pytest.fixture
async def git_checkpoint(source, tmp_path):
    root, _ = source
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    git("init")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (repo / "tracked.txt").write_text("original", encoding="utf-8")
    git("add", "tracked.txt")
    git("commit", "-m", "base")
    (repo / "tracked.txt").write_text("changed", encoding="utf-8")
    oid = git("stash", "create")
    db = AsyncDatabase(root / "data.db")
    await db.connect()
    await db.execute("UPDATE session_checkpoints SET git_stash_ref=?,source_root=? WHERE id='ckpt_a'", (oid, str(repo.resolve())))
    await db.close()
    return root, repo, oid


def test_git_environment_does_not_inherit_secrets(monkeypatch, tmp_path):
    from minimax_code.checkpoint_git_bundle import _env, _git

    monkeypatch.setenv("MINIMAX_API_KEY", "must-not-be-passed")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "unsafe")
    env = _env(str(tmp_path))
    assert "MINIMAX_API_KEY" not in env
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_NO_REPLACE_OBJECTS"] == env["GIT_NO_LAZY_FETCH"] == "1"
    passed = {}
    def fake_run(*args, **kwargs):
        passed.update(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout=b"ok")
    monkeypatch.setattr(subprocess, "run", fake_run)
    _git(["--version"], cwd=tmp_path, env=env, output=True)
    assert "MINIMAX_API_KEY" not in passed


def test_git_pack_roundtrip_keeps_repo_untouched(git_checkpoint, tmp_path):
    root, repo, oid = git_checkpoint
    before = (repo / "tracked.txt").read_bytes()
    archive = tmp_path / "git.zip"
    report = bundle.create_bundle(root, archive)
    assert report["checkpoint_git_pack_count"] == 1
    assert "Git stash objects (checkpoint tracked changes)" not in report["exclusions"]
    target = tmp_path / "git-restored"
    bundle.restore_bundle(archive, target)
    validate_pack(target / "checkpoint-git-packs" / "ckpt_a.pack", oid)
    assert (repo / "tracked.txt").read_bytes() == before


def test_explicit_git_import_requires_matching_target_and_base(git_checkpoint, tmp_path):
    root, repo, oid = git_checkpoint
    archive = tmp_path / "git.zip"
    bundle.create_bundle(root, archive)
    target = tmp_path / "git-restored"
    bundle.restore_bundle(archive, target)
    loose = repo / ".git" / "objects" / oid[:2] / oid[2:]
    assert loose.exists()
    loose.chmod(stat.S_IWRITE | stat.S_IREAD)
    loose.unlink()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    before_head = git("rev-parse", "HEAD").stdout
    before_status = git("status", "--porcelain").stdout
    assert git("cat-file", "-e", oid).returncode != 0
    with pytest.raises(ValueError):
        bundle.import_checkpoint_git(target, "ckpt_a", repo)
    with pytest.raises(ValueError):
        bundle.import_checkpoint_git(target, "ckpt_a", tmp_path, confirmed=True)
    assert git("cat-file", "-e", oid).returncode != 0
    report = bundle.import_checkpoint_git(target, "ckpt_a", repo, confirmed=True)
    assert report["oid"] == oid
    assert git("cat-file", "-e", oid).returncode == 0
    assert git("rev-parse", "HEAD").stdout == before_head
    assert git("status", "--porcelain").stdout == before_status


def test_git_import_rejects_changed_base_without_writing(git_checkpoint, tmp_path):
    root, repo, _ = git_checkpoint
    archive = tmp_path / "git.zip"
    bundle.create_bundle(root, archive)
    target = tmp_path / "git-restored"
    bundle.restore_bundle(archive, target)
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "new base"], cwd=repo, check=True, capture_output=True)
    before = set((repo / ".git" / "objects" / "pack").glob("*.pack"))
    with pytest.raises(ValueError, match="base commit"):
        bundle.import_checkpoint_git(target, "ckpt_a", repo, confirmed=True)
    assert set((repo / ".git" / "objects" / "pack").glob("*.pack")) == before


def test_git_import_rejects_repository_identity_mismatch(git_checkpoint, tmp_path):
    root, repo, _ = git_checkpoint
    archive = tmp_path / "git.zip"
    bundle.create_bundle(root, archive)
    target = tmp_path / "git-restored"
    bundle.restore_bundle(archive, target)
    meta = target / "checkpoint-git-packs" / "ckpt_a.json"
    data = json.loads(meta.read_text())
    data["repository"]["git_inode"] += 1
    meta.write_text(json.dumps(data))
    before = set((repo / ".git" / "objects" / "pack").glob("*.pack"))
    with pytest.raises(ValueError, match="identity"):
        bundle.import_checkpoint_git(target, "ckpt_a", repo, confirmed=True)
    assert set((repo / ".git" / "objects" / "pack").glob("*.pack")) == before


def test_interrupted_git_import_reports_possible_objects_and_keeps_pack(git_checkpoint, tmp_path, monkeypatch):
    from minimax_code import checkpoint_git_bundle as git_bundle

    root, repo, _ = git_checkpoint
    archive = tmp_path / "git.zip"
    bundle.create_bundle(root, archive)
    target = tmp_path / "git-restored"
    bundle.restore_bundle(archive, target)
    run = git_bundle.subprocess.run
    def fail_import(args, **kwargs):
        if any(str(arg).startswith("--git-dir=") for arg in args):
            raise subprocess.TimeoutExpired(args, 1)
        return run(args, **kwargs)
    monkeypatch.setattr(git_bundle.subprocess, "run", fail_import)
    with pytest.raises(ValueError, match="unreachable objects may remain"):
        bundle.import_checkpoint_git(target, "ckpt_a", repo, confirmed=True)
    assert (target / "checkpoint-git-packs" / "ckpt_a.pack").exists()


def test_corrupt_git_pack_cannot_be_imported(git_checkpoint, tmp_path):
    root, repo, _ = git_checkpoint
    archive = tmp_path / "git.zip"
    bundle.create_bundle(root, archive)
    target = tmp_path / "git-restored"
    bundle.restore_bundle(archive, target)
    (target / "checkpoint-git-packs" / "ckpt_a.pack").write_bytes(b"not a pack")
    before = set((repo / ".git" / "objects" / "pack").glob("*.pack"))
    with pytest.raises(ValueError):
        bundle.import_checkpoint_git(target, "ckpt_a", repo, confirmed=True)
    assert set((repo / ".git" / "objects" / "pack").glob("*.pack")) == before


def test_git_pack_rejects_unrelated_extra_objects(git_checkpoint, tmp_path):
    _, repo, oid = git_checkpoint
    extra = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo,
                           input=b"unrelated object", capture_output=True, check=True).stdout.strip()
    packed = subprocess.run(["git", "pack-objects", "--stdout", "--revs"], cwd=repo,
                            input=oid.encode() + b"\n" + extra + b"\n",
                            capture_output=True, check=True).stdout
    path = tmp_path / "extra.pack"
    path.write_bytes(packed)
    with pytest.raises(ValueError, match="outside its closure"):
        validate_pack(path, oid)


def test_archive_with_rehashed_invalid_git_pack_fails_before_restore(git_checkpoint, tmp_path):
    root, _, _ = git_checkpoint
    archive = tmp_path / "git.zip"
    bundle.create_bundle(root, archive)
    with zipfile.ZipFile(archive) as reader:
        members = [(item.filename, reader.read(item)) for item in reader.infolist()]
    manifest = json.loads(members[0][1])
    bad = b"not a Git pack"
    manifest["checkpoint_git_packs"][0]["sha256"] = hashlib.sha256(bad).hexdigest()
    manifest["checkpoint_git_packs"][0]["size_bytes"] = len(bad)
    members[0] = ("manifest.json", json.dumps(manifest).encode())
    members = [(name, bad if name.startswith("checkpoint-git/") else data) for name, data in members]
    invalid = tmp_path / "invalid-git.zip"
    with zipfile.ZipFile(invalid, "w", compression=zipfile.ZIP_STORED) as writer:
        for name, data in members:
            writer.writestr(name, data)
    target = tmp_path / "unsafe-restored"
    with pytest.raises(ValueError):
        bundle.restore_bundle(invalid, target)
    assert not target.exists()


@pytest.mark.parametrize("kind", ["missing", "alternates", "partial", "unknown-source"])
async def test_unavailable_git_objects_fail_closed(git_checkpoint, tmp_path, kind):
    root, repo, _ = git_checkpoint
    if kind == "missing":
        db = AsyncDatabase(root / "data.db")
        await db.connect()
        await db.execute("UPDATE session_checkpoints SET git_stash_ref=?", ("f" * 40,))
        await db.close()
    elif kind == "unknown-source":
        db = AsyncDatabase(root / "data.db")
        await db.connect()
        await db.execute("UPDATE session_checkpoints SET source_root=NULL")
        await db.close()
    elif kind == "alternates":
        (repo / ".git" / "objects" / "info" / "alternates").write_text("not-allowed")
    else:
        subprocess.run(["git", "config", "remote.origin.promisor", "true"], cwd=repo, check=True)
    output = tmp_path / "unavailable.zip"
    with pytest.raises(ValueError):
        bundle.create_bundle(root, output)
    assert not output.exists()


@pytest.mark.parametrize("part", ["worktree", "git-common"])
def test_replaced_repository_path_is_detected(git_checkpoint, tmp_path, monkeypatch, part):
    from minimax_code import checkpoint_git_bundle as git_bundle

    root, repo, _ = git_checkpoint
    run_pack = git_bundle._run_pack
    lstat = Path.lstat

    def simulate_replacement(*args):
        run_pack(*args)
        def replaced(path):
            result = lstat(path)
            if path == (repo if part == "worktree" else repo / ".git"):
                return SimpleNamespace(st_dev=result.st_dev, st_ino=result.st_ino + 1,
                                       st_size=result.st_size, st_mtime_ns=result.st_mtime_ns,
                                       st_ctime_ns=result.st_ctime_ns)
            return result
        monkeypatch.setattr(Path, "lstat", replaced)

    monkeypatch.setattr(git_bundle, "_run_pack", simulate_replacement)
    output = tmp_path / "replaced.zip"
    with pytest.raises(ValueError, match="Git pack unavailable or unsafe"):
        bundle.create_bundle(root, output)
    assert not output.exists()


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
