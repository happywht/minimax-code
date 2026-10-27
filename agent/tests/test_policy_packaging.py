"""Real Git checkout/archive preserve the immutable seccomp asset bytes."""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from minimax_code.process_assets import SECCOMP_SHA256

POLICY_PATH = "agent/minimax_code/assets/linux_l1/seccomp-pid1-v1.json"


def test_policy_bytes_survive_autocrlf_commit_fresh_checkout_and_archive(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is unavailable; real policy checkout/archive verification requires Git")
    root = Path(__file__).resolve().parents[2]
    original = (root / POLICY_PATH).read_bytes()
    assert hashlib.sha256(original).hexdigest() == SECCOMP_SHA256
    source, fresh = tmp_path / "source", tmp_path / "fresh"
    source.mkdir()
    empty_template = tmp_path / "empty-template"
    empty_template.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0", GIT_TEMPLATE_DIR=str(empty_template))

    def run(cwd, *args):
        # Binary pipes are essential: text mode would hide newline corruption.
        return subprocess.run([git, *args], cwd=cwd, env=env, capture_output=True,
            check=True, timeout=30).stdout

    run(source, "init", "--quiet")
    # Enable conversion before staging or committing, not only at archive time.
    run(source, "config", "core.autocrlf", "true")
    run(source, "config", "core.hooksPath", str(empty_template))
    run(source, "config", "commit.gpgsign", "false")
    run(source, "config", "user.name", "Policy packaging test")
    run(source, "config", "user.email", "policy-packaging@example.invalid")
    (source / ".gitattributes").write_bytes((root / ".gitattributes").read_bytes())
    policy = source / POLICY_PATH
    policy.parent.mkdir(parents=True)
    policy.write_bytes(original)
    # This ordinary text file proves the fixture really applies CRLF conversion.
    (source / "line-ending-control.txt").write_bytes(b"control\n")
    run(source, "add", "--", ".gitattributes", POLICY_PATH, "line-ending-control.txt")
    run(source, "commit", "--quiet", "-m", "Record immutable policy")
    assert run(source, "show", "HEAD:" + POLICY_PATH) == original

    run(tmp_path, "clone", "--quiet", "--no-checkout", "--local", "--", str(source), str(fresh))
    run(fresh, "config", "core.autocrlf", "true")
    run(fresh, "config", "core.hooksPath", str(empty_template))
    run(fresh, "checkout", "--quiet", "--force", "HEAD")
    checked_out = (fresh / POLICY_PATH).read_bytes()
    assert checked_out == original
    assert hashlib.sha256(checked_out).hexdigest() == SECCOMP_SHA256
    assert (fresh / "line-ending-control.txt").read_bytes() == b"control\r\n"

    archive = run(fresh, "archive", "--format=tar", "HEAD")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as contents:
        stream = contents.extractfile(POLICY_PATH)
        assert stream is not None
        archived = stream.read()
        assert archived == original
        assert hashlib.sha256(archived).hexdigest() == SECCOMP_SHA256
        control = contents.extractfile("line-ending-control.txt")
        assert control is not None and control.read() == b"control\r\n"
