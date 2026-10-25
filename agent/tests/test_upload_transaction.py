"""Exercise the actual remote helper on disk, without external SSH or models."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from minimax_code.hosts.upload_transaction import transact

HOST = {"hostname": "unused", "username": "user"}


def request_for(target, content=b"incoming"):
    return {"target": str(target), "transfer_id": uuid.uuid4().hex,
            "sha256": hashlib.sha256(content).hexdigest(), "action": "prepare"}


def test_upload_commit_retains_backup_and_is_idempotent(tmp_path):
    target = tmp_path / "report.txt"
    target.write_bytes(b"original")
    req = request_for(target)
    prepared = transact(req)
    Path(prepared["staged_path"]).write_bytes(b"incoming")
    result = transact({**req, "action": "commit"})
    assert result["state"] == "published"
    assert target.read_bytes() == b"incoming"
    assert Path(result["backup_path"]).read_bytes() == b"original"
    assert transact({**req, "action": "commit"}) == result
    assert transact({**req, "action": "abort"}) == result
    assert target.read_bytes() == b"incoming"


@pytest.mark.parametrize("reason", ["checksum", "changed", "created"])
def test_upload_does_not_publish_over_invalid_or_changed_data(tmp_path, reason):
    target = tmp_path / "report.txt"
    if reason != "created":
        target.write_bytes(b"original")
    req = request_for(target)
    prepared = transact(req)
    Path(prepared["staged_path"]).write_bytes(b"corrupt" if reason == "checksum" else b"incoming")
    if reason in {"changed", "created"}:
        target.write_bytes(b"user edit")
    with pytest.raises(ValueError, match="checksum|changed"):
        transact({**req, "action": "commit"})
    assert target.read_bytes() == (b"original" if reason == "checksum" else b"user edit")
    assert transact({**req, "action": "abort"})["state"] == "aborted"
    assert not Path(prepared["staged_path"]).exists()


def test_two_uploads_cannot_overwrite_one_anothers_publication(tmp_path):
    target = tmp_path / "report.txt"
    target.write_bytes(b"original")
    a, b = request_for(target, b"a"), request_for(target, b"b")
    Path(transact(a)["staged_path"]).write_bytes(b"a")
    Path(transact(b)["staged_path"]).write_bytes(b"b")
    assert transact({**a, "action": "commit"})["state"] == "published"
    with pytest.raises(ValueError, match="changed"):
        transact({**b, "action": "commit"})
    assert target.read_bytes() == b"a"


def test_status_recovers_after_replace_before_receipt(tmp_path, monkeypatch):
    import minimax_code.hosts.upload_transaction as helper

    target = tmp_path / "report.txt"
    target.write_bytes(b"original")
    req = request_for(target)
    Path(transact(req)["staged_path"]).write_bytes(b"incoming")
    original_save = helper.save

    def crash_after_replace(stage, value):
        if value["state"] == "published":
            raise OSError("simulated crash after replace")
        original_save(stage, value)

    monkeypatch.setattr(helper, "save", crash_after_replace)
    with pytest.raises(OSError, match="simulated crash"):
        transact({**req, "action": "commit"})
    assert target.read_bytes() == b"incoming"
    monkeypatch.setattr(helper, "save", original_save)
    result = transact({**req, "action": "status"})
    assert result["state"] == "published"
    assert Path(result["backup_path"]).read_bytes() == b"original"


@pytest.mark.parametrize("corruption", ["target_link", "stage_link"])
def test_symlink_substitution_is_rejected(tmp_path, corruption):
    target = tmp_path / "report.txt"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"protected")
    req = request_for(target)
    prepared = transact(req)
    link = target if corruption == "target_link" else Path(prepared["staged_path"])
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    with pytest.raises(ValueError):
        transact({**req, "action": "commit"})
    assert outside.read_bytes() == b"protected"


async def test_client_queries_lost_commit_receipt_without_repeating_write(tmp_path, monkeypatch):
    from minimax_code.hosts import ssh

    local = tmp_path / "local.txt"
    local.write_bytes(b"incoming")
    remote = tmp_path / "remote.txt"
    remote.write_bytes(b"original")
    calls = []
    staged = None

    async def action(host, target, transfer_id, name, **kwargs):
        nonlocal staged
        calls.append(name)
        result = transact({"target": str(remote), "transfer_id": transfer_id,
                           "sha256": kwargs["sha256"], "action": name})
        staged = Path(result["staged_path"])
        if name == "commit":
            return ssh.SshResult(False, None, "", "connection lost", transfer_id=transfer_id,
                                 publication_state="unknown")
        return ssh.SshResult(True, 0, json.dumps(result), "", transfer_id=transfer_id,
                             publication_state=result["state"], backup_path=result["backup_path"],
                             newly_created=result["newly_created"])

    async def scp(argv, **kwargs):
        shutil.copyfile(local, staged)
        return ssh.SshResult(True, 0, "", "")

    monkeypatch.setattr(ssh, "upload_action", action)
    monkeypatch.setattr(ssh, "_run_scp", scp)
    result = await ssh.push_file({"hostname": "unused"}, local, "/remote.txt")
    assert result.ok and result.publication_state == "published"
    assert calls == ["prepare", "commit", "status"]
    assert Path(result.backup_path).read_bytes() == b"original"


def test_new_destination_and_reused_id_are_bound_to_content(tmp_path):
    target = tmp_path / "new.txt"
    request = request_for(target)
    prepared = transact(request)
    assert prepared["newly_created"]
    assert not transact(request)["newly_created"]
    with pytest.raises(ValueError, match="different content"):
        transact({**request, "sha256": "0" * 64})
    Path(prepared["staged_path"]).write_bytes(b"incoming")
    result = transact({**request, "action": "commit"})
    assert result["state"] == "published" and result["backup_path"] is None
    assert target.read_bytes() == b"incoming"


@pytest.mark.parametrize("change", ["deleted", "recreated"])
def test_destination_identity_changes_are_detected(tmp_path, change):
    target = tmp_path / "report.txt"
    target.write_bytes(b"original")
    request = request_for(target)
    Path(transact(request)["staged_path"]).write_bytes(b"incoming")
    target.unlink()
    if change == "recreated":
        target.write_bytes(b"original")
    with pytest.raises(ValueError, match="changed"):
        transact({**request, "action": "commit"})
    assert not target.exists() if change == "deleted" else target.read_bytes() == b"original"


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable/readonly mode preservation")
@pytest.mark.parametrize("mode", [0o444, 0o755])
def test_publication_preserves_destination_mode(tmp_path, mode):
    target = tmp_path / "report.txt"
    target.write_bytes(b"original")
    target.chmod(mode)
    request = request_for(target)
    Path(transact(request)["staged_path"]).write_bytes(b"incoming")
    assert transact({**request, "action": "commit"})["state"] == "published"
    assert target.stat().st_mode & 0o777 == mode


@pytest.mark.parametrize("existing", [False, True])
def test_readonly_payload_can_be_published_without_changing_existing_mode(tmp_path, existing):
    target = tmp_path / "report.txt"
    if existing:
        target.write_bytes(b"original")
    request = request_for(target)
    staged = Path(transact(request)["staged_path"])
    staged.write_bytes(b"incoming")
    staged.chmod(0o444)
    try:
        assert transact({**request, "action": "commit"})["state"] == "published"
        assert target.read_bytes() == b"incoming"
        assert bool(target.stat().st_mode & 0o200) == existing
    finally:
        if target.exists():
            target.chmod(0o600)
        if staged.exists():
            staged.chmod(0o600)


def test_abort_removes_readonly_owned_payload(tmp_path):
    request = request_for(tmp_path / "report.txt")
    staged = Path(transact(request)["staged_path"])
    staged.write_bytes(b"incoming")
    staged.chmod(0o444)
    assert transact({**request, "action": "abort"})["state"] == "aborted"
    assert not staged.exists()


@pytest.mark.parametrize("state", ["publishing", "published"])
def test_status_keeps_backup_queryable_after_target_becomes_a_link(tmp_path, monkeypatch, state):
    from minimax_code.hosts import upload_transaction as helper

    target = tmp_path / "report.txt"
    target.write_bytes(b"original")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"protected")
    request = request_for(target)
    Path(transact(request)["staged_path"]).write_bytes(b"incoming")
    original_save = helper.save

    def fail_final_receipt(stage, data):
        if data["state"] == "published":
            raise OSError("receipt lost")
        original_save(stage, data)

    if state == "publishing":
        monkeypatch.setattr(helper, "save", fail_final_receipt)
        with pytest.raises(OSError, match="receipt lost"):
            transact({**request, "action": "commit"})
        monkeypatch.setattr(helper, "save", original_save)
    else:
        transact({**request, "action": "commit"})
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    status = transact({**request, "action": "status"})
    assert status["state"] == state
    assert Path(status["backup_path"]).read_bytes() == b"original"
    assert outside.read_bytes() == b"protected"


@pytest.mark.parametrize("journal_state", ["publishing", "published"])
def test_process_death_leaves_orphan_journal_but_status_still_resolves(tmp_path, journal_state):
    from minimax_code.hosts import upload_transaction as helper

    target = tmp_path / "report.txt"
    target.write_bytes(b"original")
    request = request_for(target)
    staged = Path(transact(request)["staged_path"])
    staged.write_bytes(b"incoming")
    script = '''
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("upload_helper", sys.argv[1])
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
original = os.replace
def interrupted(source, destination):
    if str(destination).endswith("manifest.json"):
        with open(source, encoding="utf-8") as stream:
            if json.load(stream)["state"] == sys.argv[3]:
                os._exit(71)
    original(source, destination)
os.replace = interrupted
helper.transact(json.loads(sys.argv[2]))
'''
    result = subprocess.run([sys.executable, "-c", script, helper.__file__,
                             json.dumps({**request, "action": "commit"}), journal_state],
                            capture_output=True, timeout=10)
    assert result.returncode == 71, result.stderr
    assert list(staged.parent.glob("manifest-*.next"))
    assert (staged.parent / "previous").read_bytes() == b"original"
    status = transact({**request, "action": "status"})
    assert status["state"] == ("staged" if journal_state == "publishing" else "published")
    assert target.read_bytes() == (b"original" if journal_state == "publishing" else b"incoming")
    if journal_state == "published":
        # Reconciliation can also persist a receipt without orphan temp names
        # poisoning future journal updates.
        assert transact({**request, "action": "commit"})["state"] == "published"


def test_real_process_lock_contention_refuses_second_publisher(tmp_path):
    from minimax_code.hosts import upload_transaction as helper

    target = tmp_path / "report.txt"
    target.write_bytes(b"original")
    request = request_for(target)
    with helper.lock(target.parent, target.name):
        blocked = subprocess.run([sys.executable, helper.__file__, json.dumps(request)],
                                 capture_output=True, timeout=10)
    assert blocked.returncode == 1
    assert "error" in json.loads(blocked.stdout)
    assert target.read_bytes() == b"original"
    assert not (tmp_path / (".minimax-upload-" + request["transfer_id"])).exists()


def test_unattributable_same_content_is_not_reported_published(tmp_path, monkeypatch):
    from minimax_code.hosts import upload_transaction as helper

    target = tmp_path / "report.txt"
    target.write_bytes(b"original")
    request = request_for(target)
    staged = Path(transact(request)["staged_path"])
    staged.write_bytes(b"incoming")
    original = helper.os.replace

    def interrupt(source, destination):
        if destination == target:
            raise OSError("replace failed")
        return original(source, destination)

    monkeypatch.setattr(helper.os, "replace", interrupt)
    with pytest.raises(OSError, match="replace failed"):
        transact({**request, "action": "commit"})
    assert target.read_bytes() == b"original"
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_bytes(b"incoming")
    original(unrelated, target)
    staged.unlink()
    assert transact({**request, "action": "status"})["state"] == "publishing"
    assert (staged.parent / "previous").read_bytes() == b"original"


class LocalRemote:
    """Run the actual SSH helper command locally; emulate only SCP transport."""

    def __init__(self, remote):
        self.remote = remote
        self.calls = []
        self.lose_commit = False
        self.lose_status = False
        self.scp_started = asyncio.Event()
        self.release_scp = asyncio.Event()
        self.release_scp.set()
        self.cancel_commit = False
        self.fail_scp = False
        self.transfer_id = None
        self.scp_argv = []

    async def __call__(self, argv):
        self.calls.append(argv[0])
        if argv[0] == "scp":
            self.scp_argv.append(argv)
            self.scp_started.set()
            await self.release_scp.wait()
            staged = self.remote.parent / (".minimax-upload-" + self.transfer_id) / "payload"
            shutil.copyfile(argv[-2], staged)
            return (1, b"", b"scp failed") if self.fail_scp else (0, b"", b"")
        _, _, bootstrap, encoded_request = shlex.split(argv[-1])
        request = json.loads(encoded_request)
        self.transfer_id = request["transfer_id"]
        request["target"] = str(self.remote)
        action = request["action"]
        self.calls[-1] = action
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", bootstrap, json.dumps(request),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate()
        if action == "commit" and self.cancel_commit:
            raise asyncio.CancelledError
        if action == "commit" and self.lose_commit or action == "status" and self.lose_status:
            return 255, b"", b"lost receipt"
        return process.returncode, out, err


@pytest.mark.parametrize("failure", ["scp", "cancel_scp", "cancel_commit", "lost_receipts"])
async def test_upload_transport_faults_remain_queryable(tmp_path, failure):
    from minimax_code.hosts.ssh import push_file, upload_action

    local = tmp_path / "local.txt"
    local.write_bytes(b"incoming")
    remote = tmp_path / "remote.txt"
    remote.write_bytes(b"original")
    transport = LocalRemote(remote)
    transfer_id = uuid.uuid4().hex
    transport.fail_scp = failure == "scp"
    transport.cancel_commit = failure == "cancel_commit"
    transport.lose_commit = transport.lose_status = failure == "lost_receipts"
    if failure == "cancel_scp":
        transport.release_scp.clear()
    task = asyncio.create_task(push_file(HOST, local, "/remote.txt",
                                        runner=transport, transfer_id=transfer_id))
    if failure == "cancel_scp":
        await asyncio.wait_for(transport.scp_started.wait(), timeout=5)
        task.cancel()
    if failure.startswith("cancel"):
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        assert not result.ok
        assert result.transfer_id == transfer_id
        if failure == "lost_receipts":
            assert result.publication_state == "unknown"
    transport.lose_status = False
    status = await upload_action(HOST, "/remote.txt", transfer_id,
                                 "status", runner=transport)
    published = failure in {"cancel_commit", "lost_receipts"}
    assert status.publication_state == ("published" if published else "aborted")
    assert remote.read_bytes() == (b"incoming" if published else b"original")
    if published:
        assert Path(status.backup_path).read_bytes() == b"original"
    assert transport.calls.count("commit") == (1 if published else 0)
    assert not (remote.parent / (".minimax-upload-" + transfer_id) / "payload").exists()


async def test_duplicate_inflight_id_never_repeats_scp_or_aborts_owner(tmp_path):
    from minimax_code.hosts.ssh import push_file

    local = tmp_path / "local.txt"
    local.write_bytes(b"incoming")
    remote = tmp_path / "remote.txt"
    remote.write_bytes(b"original")
    transport = LocalRemote(remote)
    transport.release_scp.clear()
    transfer_id = uuid.uuid4().hex
    first = asyncio.create_task(push_file(HOST, local, "/remote.txt",
                                         runner=transport, transfer_id=transfer_id))
    await asyncio.wait_for(transport.scp_started.wait(), timeout=5)
    duplicate = await push_file(HOST, local, "/remote.txt",
                                runner=transport, transfer_id=transfer_id)
    assert not duplicate.ok and duplicate.publication_state == "staged"
    assert transport.calls.count("scp") == 1
    assert "abort" not in transport.calls
    transport.release_scp.set()
    assert (await first).ok
    assert (await push_file(HOST, local, "/remote.txt",
                            runner=transport, transfer_id=transfer_id)).ok
    changed = tmp_path / "changed.txt"
    changed.write_bytes(b"other content")
    wrong_source = await push_file(HOST, changed, "/remote.txt",
                                   runner=transport, transfer_id=transfer_id)
    assert not wrong_source.ok and "different content" in wrong_source.stderr
    assert transport.calls.count("scp") == transport.calls.count("commit") == 1


async def test_upload_forces_sftp_and_preserves_literal_special_path(tmp_path):
    from minimax_code.hosts.ssh import push_file

    local = tmp_path / "source.txt"
    local.write_bytes(b"incoming")
    transport = LocalRemote(tmp_path / "remote.txt")
    special = "/tmp/a $(touch x); quote' name/target.txt"
    result = await push_file(HOST, local, special, runner=transport)
    assert result.ok
    assert "-s" in transport.scp_argv[0]
    assert transport.scp_argv[0][-1].startswith("user@unused:/tmp/a $(touch x); quote' name/")
    assert transport.calls == ["prepare", "scp", "commit"]
