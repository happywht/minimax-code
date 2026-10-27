"""A real autocrlf checkout/archive must retain the old writer's proven bytes."""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

FIXTURE = "agent/tests/fixtures/bundle-v1-writer.py.txt"
SHA256 = "11fb0bd93206e86514bae8928e65645084c75a58c4c4a38bc8c55ac1464ad863"


def test_frozen_old_writer_survives_autocrlf_fresh_checkout_and_archive(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is required for real immutable-fixture packaging verification")
    root = Path(__file__).resolve().parents[2]
    original = (root / FIXTURE).read_bytes()
    assert hashlib.sha256(original).hexdigest() == SHA256
    source, fresh, template = tmp_path / "source", tmp_path / "fresh", tmp_path / "empty-template"
    source.mkdir()
    template.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_TEMPLATE_DIR=str(template))
    def run(cwd, *args):
        return subprocess.run([git, *args], cwd=cwd, env=env, capture_output=True,
                              check=True, timeout=30).stdout
    run(source, "init", "--quiet")
    run(source, "config", "core.autocrlf", "true")
    run(source, "config", "core.hooksPath", str(template))
    run(source, "config", "commit.gpgsign", "false")
    run(source, "config", "user.name", "Bundle fixture packaging")
    run(source, "config", "user.email", "bundle-fixture@example.invalid")
    (source / ".gitattributes").write_bytes((root / ".gitattributes").read_bytes())
    path = source / FIXTURE
    path.parent.mkdir(parents=True)
    path.write_bytes(original)
    (source / "control.txt").write_bytes(b"control\n")
    run(source, "add", "--", ".gitattributes", FIXTURE, "control.txt")
    run(source, "commit", "--quiet", "-m", "Record original writer fixture")
    assert run(source, "show", "HEAD:" + FIXTURE) == original
    run(tmp_path, "clone", "--quiet", "--no-checkout", "--local", "--", str(source), str(fresh))
    run(fresh, "config", "core.autocrlf", "true")
    run(fresh, "config", "core.hooksPath", str(template))
    run(fresh, "checkout", "--quiet", "--force", "HEAD")
    assert (fresh / FIXTURE).read_bytes() == original
    assert hashlib.sha256((fresh / FIXTURE).read_bytes()).hexdigest() == SHA256
    assert (fresh / "control.txt").read_bytes() == b"control\r\n"
    with tarfile.open(fileobj=io.BytesIO(run(fresh, "archive", "--format=tar", "HEAD"))) as archive:
        assert archive.extractfile(FIXTURE).read() == original
        assert archive.extractfile("control.txt").read() == b"control\r\n"
