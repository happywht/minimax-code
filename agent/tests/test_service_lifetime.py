"""Real OS ownership and CLI lifecycle regressions; no personal configuration."""
from __future__ import annotations

import json
import os
import queue
import runpy
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from minimax_code.runtime.crash_detect import (
    marker_path,
    prepare_startup_marker,
    startup_previous_crash,
)
from minimax_code.runtime.service_lease import (
    LOCK_NAME,
    ServiceAlreadyRunning,
    ServiceLeaseError,
    acquire_service_lease,
    assert_owned,
)

AGENT = Path(__file__).resolve().parents[1]


def child_env(root):
    safe = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "HOME", "USERPROFILE", "LANG", "LC_ALL", "HOMEDRIVE", "HOMEPATH"}
    env = {key: value for key, value in os.environ.items() if key.upper() in safe}
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    skills = root / "skills"
    skills.mkdir(exist_ok=True)
    env.update(MINIMAX_CODE_DATA_DIR=str(root / "data"),
               MINIMAX_CODE_WORKSPACE=str(workspace), MINIMAX_CODE_SKILLS_DIR=str(skills),
               MINIMAX_CODE_FORCE_MOCK="1", MINIMAX_API_KEY="",
               PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
               PYTHONUNBUFFERED="1", PYTHONPATH=str(AGENT))
    return env


def probe_lock(path):
    return subprocess.run(
        [sys.executable, "-c", "import sys;from minimax_code.runtime.service_lease import "
         "acquire_service_lease; lease=acquire_service_lease(sys.argv[1]);lease.close()", str(path)],
        env={**child_env(path.parent), "PYTHONPATH": str(AGENT)}, cwd=AGENT,
        capture_output=True, text=True, timeout=12,
    )


def test_real_competition_release_and_noninheritance(tmp_path):
    path = tmp_path / "data"
    lease = acquire_service_lease(path)
    try:
        assert not os.get_inheritable(lease._fd)
        assert_owned(path, path / "data.db")
        with pytest.raises(ServiceAlreadyRunning):
            acquire_service_lease(path / ".." / "data")
        with pytest.raises(ServiceLeaseError, match="outside"):
            lease.assert_owned(path, tmp_path / "other.db")
        conflict = probe_lock(path)
        assert conflict.returncode != 0
        assert "already in use" in conflict.stderr
        assert (path / LOCK_NAME).exists()
    finally:
        lease.close()
    assert probe_lock(path).returncode == 0
    assert (path / LOCK_NAME).exists()
    with pytest.raises(ServiceLeaseError):
        assert_owned(path)


def test_startup_consumes_previous_once_without_deleting_current(tmp_path):
    lease = acquire_service_lease(tmp_path)
    marker_path(tmp_path).write_text(json.dumps({"pid": os.getpid(), "started_at": "old"}))
    try:
        owner = prepare_startup_marker(lease)
        current = marker_path(tmp_path).read_bytes()
        previous = startup_previous_crash(tmp_path)
        assert previous.crashed_pid == os.getpid()  # no PID-equality heuristic
        assert previous.started_at == "old"
        assert startup_previous_crash(tmp_path) is None
        assert marker_path(tmp_path).read_bytes() == current
        owner.mark_clean()
        owner.mark_clean()
        assert not marker_path(tmp_path).exists()
    finally:
        lease.close()
    later_lease = acquire_service_lease(tmp_path)
    try:
        later = prepare_startup_marker(later_lease)
        owner.mark_clean()  # stale owner cannot remove the later marker
        assert marker_path(tmp_path).exists()
        later.mark_clean()
    finally:
        later_lease.close()


def test_clean_start_and_foreign_marker_fail_closed(tmp_path):
    lease = acquire_service_lease(tmp_path)
    try:
        owner = prepare_startup_marker(lease)
        assert startup_previous_crash(tmp_path) is None
        assert marker_path(tmp_path).exists()
        marker_path(tmp_path).write_text('{"owner":"foreign"}')
        with pytest.raises(RuntimeError, match="ownership"):
            owner.mark_clean()
        assert marker_path(tmp_path).exists()
    finally:
        lease.close()


def test_unclosed_old_marker_owner_cannot_delete_new_owner(tmp_path):
    old_lease = acquire_service_lease(tmp_path)
    old_owner = prepare_startup_marker(old_lease)
    old_lease.close()
    new_lease = acquire_service_lease(tmp_path)
    try:
        new_owner = prepare_startup_marker(new_lease)
        current = marker_path(tmp_path).read_bytes()
        with pytest.raises(ServiceLeaseError):
            old_owner.mark_clean()
        assert marker_path(tmp_path).read_bytes() == current
        new_owner.mark_clean()
    finally:
        new_lease.close()


def test_lock_symlink_is_rejected_without_touching_target(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    directory = tmp_path / "data"
    directory.mkdir()
    try:
        (directory / LOCK_NAME).symlink_to(outside)
    except OSError:
        pytest.skip("OS does not permit test symlink creation")
    with pytest.raises(ServiceLeaseError, match="symbolic"):
        acquire_service_lease(directory)
    assert outside.read_text() == "untouched"


@pytest.mark.parametrize("payload", [[], None, {"pid": "bad"}, {"pid": None}, "not-json"])
def test_unknown_previous_marker_survives_as_report_and_current_marker(tmp_path, payload):
    lease = acquire_service_lease(tmp_path)
    try:
        marker_path(tmp_path).write_text(json.dumps(payload) if payload != "not-json" else "{")
        owner = prepare_startup_marker(lease)
        assert startup_previous_crash(tmp_path).crashed_pid == -1
        assert startup_previous_crash(tmp_path) is None
        assert marker_path(tmp_path).exists()
        owner.mark_clean()
    finally:
        lease.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork semantics")
def test_fork_child_cannot_unlock_or_clean_parent(tmp_path):
    lease = acquire_service_lease(tmp_path)
    owner = prepare_startup_marker(lease)
    child = os.fork()
    if child == 0:
        try:
            lease.close()
            owner.mark_clean()
            os._exit(0)
        except BaseException:
            os._exit(2)
    try:
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        assert marker_path(tmp_path).exists()
        assert probe_lock(tmp_path).returncode != 0
        owner.mark_clean()
    finally:
        lease.close()


class Cli:
    def __init__(self, root, code=None):
        args = (["-c", code] if code else ["-m", "minimax_code", "--stdio"])
        command = [sys.executable, *args]
        if sys.platform == "win32":
            host = AGENT / "minimax_code/_windows_process_host.py"
            helpers = runpy.run_path(str(host))
            command = [getattr(sys, "_base_executable", sys.executable), "-I", "-S", str(host),
                       *helpers["parent_arguments"](), "--exec", *command]
        self.proc = subprocess.Popen(command, cwd=AGENT, env=child_env(root),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                     start_new_session=sys.platform != "win32")
        self.replies = queue.Queue()
        self.errors = []
        self.threads = [threading.Thread(target=self._read, daemon=True),
                        threading.Thread(target=self._errors, daemon=True)]
        for thread in self.threads:
            thread.start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                self.replies.put(json.loads(line))
            except json.JSONDecodeError:
                pass

    def _errors(self):
        for line in self.proc.stderr:
            self.errors.append(line)

    def rpc(self, method):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "id": method}) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                result = self.replies.get(timeout=0.1)
                if result.get("id") == method:
                    assert "error" not in result, result
                    return result["result"]
            except queue.Empty:
                assert self.proc.poll() is None, "".join(self.errors)
        raise AssertionError("No CLI reply: " + "".join(self.errors))

    def close(self):
        if self.proc.poll() is None:
            if sys.platform == "win32":
                self.proc.kill()  # Job host owns its redirector and actual agent.
            else:
                os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc.wait(timeout=10)
        self.proc.stdin.close()
        for thread in self.threads:
            thread.join(timeout=3)
        self.proc.stdout.close()
        self.proc.stderr.close()


def test_actual_cli_rejects_duplicate_keeps_marker_and_closes_sqlite(tmp_path):
    first = Cli(tmp_path)
    second = None
    try:
        first.rpc("ping")
        marker = tmp_path / "data" / ".minimax_code_running"
        original = marker.read_bytes()
        recovery = first.rpc("runtime.recovery_status")
        assert recovery["previous_crash"] is False
        second = Cli(tmp_path)
        assert second.proc.wait(timeout=25) == 1
        for thread in second.threads:
            thread.join(timeout=3)
        assert "already in use" in "".join(second.errors)
        assert marker.read_bytes() == original
        first.rpc("ping")
        first.rpc("shutdown")
        assert first.proc.wait(timeout=12) == 0, "".join(first.errors)
        assert not marker.exists()
        assert probe_lock(tmp_path / "data").returncode == 0
    finally:
        if second:
            second.close()
        first.close()


def test_unconfirmed_exit_retains_lock_and_marker_until_process_death(tmp_path):
    code = '''
import asyncio,sys,threading,time
import minimax_code.__main__ as entry
sys.argv=["minimax-code-agent","--stdio"]
entry._wire_xai_crash_handler=lambda _:None
async def broken(*args,**kwargs):
    threading.Thread(target=time.sleep,args=(45,),daemon=False).start()
    print('{"unconfirmed":true}',flush=True)
    raise RuntimeError("synthetic unconfirmed cleanup")
entry.amain=broken
entry.cli_entry()
'''
    child = Cli(tmp_path, code)
    try:
        assert child.replies.get(timeout=25) == {"unconfirmed": True}
        assert child.proc.poll() is None
        assert marker_path(tmp_path / "data").exists()
        assert probe_lock(tmp_path / "data").returncode != 0
    finally:
        child.close()
    assert marker_path(tmp_path / "data").exists()
    assert probe_lock(tmp_path / "data").returncode == 0


def test_actual_abrupt_exit_reports_previous_crash_on_restart(tmp_path):
    first = Cli(tmp_path)
    try:
        first.rpc("ping")
        assert marker_path(tmp_path / "data").exists()
        actual_pid = json.loads(marker_path(tmp_path / "data").read_text())["pid"]
    finally:
        first.close()  # abrupt process death deliberately retains the marker
    second = Cli(tmp_path)
    try:
        recovery = second.rpc("runtime.recovery_status")
        assert recovery["previous_crash"] is True
        assert recovery["crash"]["crashed_pid"] == actual_pid
        assert marker_path(tmp_path / "data").exists()
        second.rpc("shutdown")
        assert second.proc.wait(timeout=12) == 0, "".join(second.errors)
        assert not marker_path(tmp_path / "data").exists()
    finally:
        second.close()
