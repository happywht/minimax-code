"""Black-box tests for the ``manager`` lock wrapper (R305h).

Ported **by function** from grok ``xai-codebase-graph/src/manager/lock.rs``
(539 lines). The inline ``#[cfg(test)] mod tests`` in grok contributes 6
tests (the exclusive/shared contention matrix + cross-workspace independence
+ the exclusive lock-file lifecycle); this suite ports those 6 verbatim and
adds Python-specific coverage for the parts grok's enum/RAII model does not
exercise directly:

* :class:`IndexOperation` enum semantics (``value`` / ``is_exclusive`` /
  ``stale_timeout`` / ``__str__``) and the 4 stale-timeout constants
* :class:`LockResult` discriminated-union behaviour (acquired/busy factories,
  ``unwrap`` both arms, ``release`` + context-manager lifetime)
* :class:`WorkspaceLockGuard` idempotent release + missing-file tolerance
* shared-lock does **not** materialise a lock file (only exclusive does)
* file-lock-contested -> in-memory rollback (the rollback is load-bearing:
  a half-acquired workspace must not stay exclusive-locked in memory)
* :func:`is_operation_in_progress` from both the in-memory and file views
* stale lock-file takeover (timeout expiry lets a contender reclaim)
* :func:`_parse_lock_file` happy path + missing-field / garbage returns
* :func:`_is_process_alive` current-process + dead-PID (POSIX) branches
* the barrel contract (``manager`` 22 symbols incl. the 5 lock re-exports;
  crate root mirrors grok ``lib.rs`` L89-L93)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import minimax_code.xai_codebase_graph as xcg_root
from minimax_code.xai_codebase_graph.manager import (
    IndexOperation,
    LockResult,
    WorkspaceLockGuard,
    is_operation_in_progress,
    try_lock,
)
from minimax_code.xai_codebase_graph.manager import lock as lock_mod
from minimax_code.xai_codebase_graph.manager.lock import (
    BG_REFRESH_STALE_DURATION_SEC,
    BUILD_STALE_DURATION_SEC,
    LOAD_STALE_DURATION_SEC,
    SAVE_STALE_DURATION_SEC,
)

# === isolation fixture ====================================================


@pytest.fixture(autouse=True)
def _isolate_in_memory_locks() -> None:
    """Clear the module-global in-memory registry before and after each test.

    grok's global ``DashMap`` is implicitly isolated by distinct ``tempdir``
    workspaces; the Python port adds an explicit clear so a forgotten
    ``release`` in one test cannot leak exclusive state into the next.
    """
    lock_mod._IN_MEMORY_LOCKS.clear()
    yield
    lock_mod._IN_MEMORY_LOCKS.clear()


# === IndexOperation enum ==================================================


def test_index_operation_values_match_grok_lock_file_strings() -> None:
    """grok ``as_str`` outputs are the exact strings written to lock files."""
    assert IndexOperation.Load.value == "load"
    assert IndexOperation.Save.value == "save"
    assert IndexOperation.Build.value == "build"
    assert IndexOperation.BackgroundRefresh.value == "background_refresh"


def test_index_operation_is_exclusive_load_shared_others_exclusive() -> None:
    """grok ``is_exclusive``: ``Load`` shared, the other three exclusive."""
    assert IndexOperation.Load.is_exclusive() is False
    assert IndexOperation.Save.is_exclusive() is True
    assert IndexOperation.Build.is_exclusive() is True
    assert IndexOperation.BackgroundRefresh.is_exclusive() is True


def test_index_operation_stale_timeouts_match_grok_constants() -> None:
    """grok ``stale_timeout``: LOAD/SAVE 120s, BUILD 600s, BG_REFRESH 300s."""
    assert IndexOperation.Load.stale_timeout() == LOAD_STALE_DURATION_SEC == 120
    assert IndexOperation.Save.stale_timeout() == SAVE_STALE_DURATION_SEC == 120
    assert IndexOperation.Build.stale_timeout() == BUILD_STALE_DURATION_SEC == 600
    assert (
        IndexOperation.BackgroundRefresh.stale_timeout()
        == BG_REFRESH_STALE_DURATION_SEC
        == 300
    )


def test_index_operation_str_returns_value() -> None:
    """grok ``Display`` impl -> ``__str__`` returns the enum value."""
    assert str(IndexOperation.Load) == "load"
    assert str(IndexOperation.BackgroundRefresh) == "background_refresh"


# === LockResult ===========================================================
# grok ``enum LockResult { Acquired(guard), Busy { operation, holder_pid } }``
# -> single class with an ``is_acquired`` discriminator.


class _RecordingGuard:
    """Stand-in for :class:`WorkspaceLockGuard` that counts ``release`` calls."""

    def __init__(self) -> None:
        self.release_calls = 0

    def release(self) -> None:
        self.release_calls += 1


def test_lock_result_acquired_is_acquired_true() -> None:
    """``LockResult::Acquired`` -> ``is_acquired`` True."""
    guard = _RecordingGuard()
    result = LockResult.acquired(guard)  # type: ignore[arg-type]
    assert result.is_acquired() is True
    assert result.busy_operation is None
    assert result.holder_pid is None


def test_lock_result_busy_is_acquired_false() -> None:
    """``LockResult::Busy`` -> ``is_acquired`` False."""
    result = LockResult.busy("build (same process)", holder_pid=4242)
    assert result.is_acquired() is False


def test_lock_result_busy_carries_operation_and_pid() -> None:
    """grok ``Busy { operation, holder_pid }`` fields surface as attributes."""
    result = LockResult.busy("save", holder_pid=99)
    assert result.busy_operation == "save"
    assert result.holder_pid == 99


def test_lock_result_unwrap_returns_guard_when_acquired() -> None:
    """``unwrap`` on ``Acquired`` returns the held guard."""
    guard = _RecordingGuard()
    result = LockResult.acquired(guard)  # type: ignore[arg-type]
    assert result.unwrap() is guard


def test_lock_result_unwrap_raises_when_busy() -> None:
    """grok ``unwrap`` panics on ``Busy`` -> Python raises :class:`RuntimeError`."""
    result = LockResult.busy("build")
    with pytest.raises(RuntimeError, match="Lock was busy"):
        result.unwrap()


def test_lock_result_release_releases_guard_once() -> None:
    """``release`` drops the guard exactly once (idempotent)."""
    guard = _RecordingGuard()
    result = LockResult.acquired(guard)  # type: ignore[arg-type]
    result.release()
    result.release()  # second call must be a no-op
    assert guard.release_calls == 1
    assert result.is_acquired() is False


def test_lock_result_context_manager_unwraps_and_releases() -> None:
    """``with LockResult`` yields the guard and releases it on exit."""
    guard = _RecordingGuard()
    result = LockResult.acquired(guard)  # type: ignore[arg-type]
    with result as yielded:
        assert yielded is guard
    assert guard.release_calls == 1


# === WorkspaceLockGuard ===================================================


def test_guard_release_idempotent(tmp_path: Path) -> None:
    """``release`` is idempotent (mirrors RAII drop firing at most once)."""
    guard = WorkspaceLockGuard(
        tmp_path.resolve(),
        tmp_path / "absent.lock",
        IndexOperation.Build,
    )
    guard.release()
    guard.release()  # must not raise
    # guard has no public ``released`` flag, but re-releasing an exclusive
    # guard whose lock file never existed proves the missing-file path is
    # tolerated on every call.


def test_guard_release_ignores_missing_lock_file(tmp_path: Path) -> None:
    """grok ``Drop`` swallows ``NotFound`` from ``remove_file`` -> no raise."""
    guard = WorkspaceLockGuard(
        tmp_path.resolve(),
        tmp_path / "never-existed.lock",
        IndexOperation.Build,
    )
    guard.release()  # unlink -> FileNotFoundError, swallowed


def test_guard_context_manager_releases_on_exit(tmp_path: Path) -> None:
    """``with guard`` releases on ``__exit__`` (the structured-release path)."""
    lock_file = tmp_path / "ctx.lock"
    lock_file.write_text("operation=build\n", encoding="utf-8")
    assert lock_file.exists()
    guard = WorkspaceLockGuard(
        tmp_path.resolve(), lock_file, IndexOperation.Build
    )
    with guard:
        assert lock_file.exists()  # still held inside the block
    assert not lock_file.exists()  # removed on exit


# === try_lock: same-process contention matrix (port grok 6 tests) ========


def test_exclusive_lock_blocks_exclusive(tmp_path: Path) -> None:
    """grok ``test_exclusive_lock_blocks_exclusive``."""
    guard1 = try_lock(tmp_path, IndexOperation.Build)
    assert guard1.is_acquired()

    result2 = try_lock(tmp_path, IndexOperation.Build)
    assert not result2.is_acquired()

    guard1.release()

    guard3 = try_lock(tmp_path, IndexOperation.Build)
    assert guard3.is_acquired()
    guard3.release()


def test_shared_locks_coexist(tmp_path: Path) -> None:
    """grok ``test_shared_locks_coexist`` -- multiple Load holders allowed."""
    guard1 = try_lock(tmp_path, IndexOperation.Load)
    guard2 = try_lock(tmp_path, IndexOperation.Load)
    guard3 = try_lock(tmp_path, IndexOperation.Load)
    assert guard1.is_acquired()
    assert guard2.is_acquired()
    assert guard3.is_acquired()
    guard1.release()
    guard2.release()
    guard3.release()


def test_exclusive_blocks_shared(tmp_path: Path) -> None:
    """grok ``test_exclusive_blocks_shared``."""
    guard1 = try_lock(tmp_path, IndexOperation.Build)
    assert guard1.is_acquired()

    result2 = try_lock(tmp_path, IndexOperation.Load)
    assert not result2.is_acquired()
    guard1.release()


def test_shared_blocks_exclusive(tmp_path: Path) -> None:
    """grok ``test_shared_blocks_exclusive``."""
    guard1 = try_lock(tmp_path, IndexOperation.Load)
    assert guard1.is_acquired()

    result2 = try_lock(tmp_path, IndexOperation.Build)
    assert not result2.is_acquired()

    guard1.release()

    guard3 = try_lock(tmp_path, IndexOperation.Build)
    assert guard3.is_acquired()
    guard3.release()


def test_different_workspaces_independent(tmp_path: Path) -> None:
    """grok ``test_different_workspaces_independent``."""
    dir1 = tmp_path / "a"
    dir2 = tmp_path / "b"
    dir1.mkdir()
    dir2.mkdir()

    guard1 = try_lock(dir1, IndexOperation.Build)
    guard2 = try_lock(dir2, IndexOperation.Build)
    assert guard1.is_acquired()
    assert guard2.is_acquired()
    guard1.release()
    guard2.release()


def test_lock_file_created_and_removed_for_exclusive(tmp_path: Path) -> None:
    """grok ``test_lock_file_created_for_exclusive`` (+ removal on release)."""
    lock_file = lock_mod._get_lock_file_path(tmp_path.resolve())
    assert not lock_file.exists()

    guard = try_lock(tmp_path, IndexOperation.Build)
    assert guard.is_acquired()

    assert lock_file.exists()
    contents = lock_file.read_text(encoding="utf-8")
    assert "operation=build" in contents
    assert f"pid={os.getpid()}" in contents

    guard.release()
    assert not lock_file.exists()


# === try_lock: shared path does not materialise a lock file ===============


def test_shared_lock_does_not_create_lock_file(tmp_path: Path) -> None:
    """Only exclusive operations write a lock file (grok ``is_exclusive`` gate)."""
    lock_file = lock_mod._get_lock_file_path(tmp_path.resolve())
    guard = try_lock(tmp_path, IndexOperation.Load)
    assert guard.is_acquired()
    assert not lock_file.exists()  # shared path skips the file lock entirely
    guard.release()
    assert not lock_file.exists()


# === try_lock: file-lock-contested -> in-memory rollback ==================


def test_file_lock_contested_releases_in_memory(tmp_path: Path) -> None:
    """A contested file lock rolls back the in-memory acquire (no half-lock).

    Plants a *live* lock file (fresh ``started`` + the current PID), then
    :func:`try_lock` for ``Build`` must report ``Busy`` **from the file-lock
    stage** (``busy_operation == "build"``, not ``"build (same process)"``),
    proving the in-memory acquire succeeded and was then rolled back. A
    subsequent ``Load`` then succeeds, proving the in-memory exclusive flag
    did not linger.
    """
    lock_file = lock_mod._get_lock_file_path(tmp_path.resolve())
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(
        f"operation=build\npid={os.getpid()}\n"
        f"started={lock_mod._now_epoch()}\nworkspace=planted\n",
        encoding="utf-8",
    )

    result = try_lock(tmp_path, IndexOperation.Build)
    assert not result.is_acquired()
    # busy_operation comes from the lock-file ``operation=`` line, proving we
    # reached the file-lock stage (in-memory acquire succeeded first).
    assert result.busy_operation == "build"

    # rollback verified: a shared Load is not blocked by a lingering exclusive
    # in-memory flag (which would exist if rollback did not happen).
    shared = try_lock(tmp_path, IndexOperation.Load)
    assert shared.is_acquired()
    shared.release()


# === is_operation_in_progress =============================================


def test_is_operation_in_progress_memory_view(tmp_path: Path) -> None:
    """An in-memory exclusive holder is reported as in-progress."""
    guard = try_lock(tmp_path, IndexOperation.Build)
    assert guard.is_acquired()
    assert is_operation_in_progress(tmp_path, IndexOperation.Build) is True
    # a conflicting shared op also sees the exclusive holder
    assert is_operation_in_progress(tmp_path, IndexOperation.Load) is True
    guard.release()


def test_is_operation_in_progress_false_when_free(tmp_path: Path) -> None:
    """No holder -> not in progress."""
    assert is_operation_in_progress(tmp_path, IndexOperation.Build) is False
    assert is_operation_in_progress(tmp_path, IndexOperation.Load) is False


def test_is_operation_in_progress_file_view(tmp_path: Path) -> None:
    """A live lock file (no in-memory holder) still reports in-progress.

    Exclusive ``is_operation_in_progress`` consults the cross-process lock
    file; a fresh file with the current PID is live -> True.
    """
    lock_file = lock_mod._get_lock_file_path(tmp_path.resolve())
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(
        f"operation=build\npid={os.getpid()}\n"
        f"started={lock_mod._now_epoch()}\nworkspace=planted\n",
        encoding="utf-8",
    )
    assert is_operation_in_progress(tmp_path, IndexOperation.Build) is True


# === stale lock-file takeover =============================================


def test_stale_lock_file_is_taken_over(tmp_path: Path) -> None:
    """A lock file older than the stale timeout is reclaimed by a contender.

    Plants a lock file with an ancient ``started`` (well past BUILD's 600s
    ceiling); :func:`try_lock` for ``Build`` must take it over and succeed.
    """
    lock_file = lock_mod._get_lock_file_path(tmp_path.resolve())
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    ancient = lock_mod._now_epoch() - 9999
    lock_file.write_text(
        f"operation=build\npid={os.getpid()}\nstarted={ancient}\n"
        f"workspace=stale\n",
        encoding="utf-8",
    )

    guard = try_lock(tmp_path, IndexOperation.Build)
    assert guard.is_acquired()

    # the takeover overwrites the file with our own claim.
    contents = lock_file.read_text(encoding="utf-8")
    assert f"pid={os.getpid()}" in contents
    guard.release()


# === _parse_lock_file =====================================================


def test_parse_lock_file_valid() -> None:
    """All three fields present -> ``(operation, pid, started)`` tuple."""
    body = "operation=save\npid=1234\nstarted=1700000000\nworkspace=x\n"
    assert lock_mod._parse_lock_file(body) == ("save", 1234, 1700000000)


@pytest.mark.parametrize(
    "body",
    [
        "pid=1\nstarted=2\nworkspace=x\n",  # missing operation
        "operation=build\nstarted=2\nworkspace=x\n",  # missing pid
        "operation=build\npid=1\nworkspace=x\n",  # missing started
        "operation=build\npid=abc\nstarted=2\n",  # pid non-numeric
        "operation=build\npid=1\nstarted=xyz\n",  # started non-numeric
        "",
    ],
    ids=[
        "missing-op",
        "missing-pid",
        "missing-started",
        "bad-pid",
        "bad-started",
        "empty",
    ],
)
def test_parse_lock_file_returns_none_when_malformed(body: str) -> None:
    """grok ``Option<(_,_ ,_)>`` final match -> ``None`` on any missing field."""
    assert lock_mod._parse_lock_file(body) is None


def test_parse_lock_file_ignores_extra_lines() -> None:
    """Lines outside the known keys are skipped (forward-compatible)."""
    body = (
        "operation=build\npid=7\nstarted=10\nworkspace=w\n"
        "future_field=ignored\nanother=v\n"
    )
    assert lock_mod._parse_lock_file(body) == ("build", 7, 10)


# === _is_process_alive ====================================================


def test_is_process_alive_current_process_true() -> None:
    """The current process is alive on every platform."""
    assert lock_mod._is_process_alive(os.getpid()) is True


def test_is_process_alive_dead_pid_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIX: ``os.kill`` ``ProcessLookupError`` -> dead.

    Skipped on non-POSIX, where grok's design returns ``True`` unconditionally
    (stale detection falls back to the timeout path alone).
    """
    if os.name != "posix":
        pytest.skip("POSIX-only liveness probe")

    def fake_kill(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(lock_mod.os, "kill", fake_kill)
    assert lock_mod._is_process_alive(999999) is False


# === _get_lock_file_path ==================================================


def test_get_lock_file_path_replaces_bin_suffix_with_lock(tmp_path: Path) -> None:
    """grok ``with_extension("lock")`` -> ``.goto_index.bin`` becomes ``.lock``."""
    lock_file = lock_mod._get_lock_file_path(tmp_path.resolve())
    assert lock_file.name == ".goto_index.lock"
    assert lock_file.parent == tmp_path.resolve()


# === barrel contract ======================================================


def test_manager_barrel_includes_lock_five() -> None:
    """``manager/__init__`` re-exports the 5 lock symbols (grok ``mod.rs``).

    Total is now 22 (12 cache + 5 builder + 5 lock); the exhaustive count
    assertion lives here because the lock brick is what completes the
    ``manager`` barrel merge.
    """
    from minimax_code.xai_codebase_graph import manager

    lock_subset = {
        "IndexOperation",
        "LockResult",
        "WorkspaceLockGuard",
        "is_operation_in_progress",
        "try_lock",
    }
    assert lock_subset <= set(manager.__all__)
    assert len(manager.__all__) == 22


def test_crate_root_barrel_exports_lock_five() -> None:
    """grok ``lib.rs`` L89-L93 re-exports the 5 lock symbols at crate root."""
    for sym in (
        "IndexOperation",
        "LockResult",
        "WorkspaceLockGuard",
        "is_operation_in_progress",
        "try_lock",
    ):
        assert sym in xcg_root.__all__, f"{sym} missing from crate-root barrel"
    # the crate-root symbol is the same object as the manager leaf symbol.
    assert xcg_root.try_lock is try_lock
    assert xcg_root.IndexOperation is IndexOperation
    assert xcg_root.LockResult is LockResult


def test_crate_root_does_not_export_lock_constants() -> None:
    """grok lock ``const`` block is private -> not hoisted to the crate root.

    The 4 stale-timeout constants stay in the ``lock`` leaf module (importable
    from :mod:`manager.lock` directly for tests) and never reach grok's
    ``lib.rs`` re-export; the Python port mirrors that visibility.
    """
    for const in (
        "LOAD_STALE_DURATION_SEC",
        "SAVE_STALE_DURATION_SEC",
        "BUILD_STALE_DURATION_SEC",
        "BG_REFRESH_STALE_DURATION_SEC",
    ):
        assert const not in xcg_root.__all__


# === type-narrowing guards (compile-time only, no runtime effect) =========


def test_try_lock_workspace_accepts_str_and_pathlike(tmp_path: Path) -> None:
    """``workspace`` accepts ``str`` and any ``os.PathLike`` (grok ``&Path``)."""
    guard_from_str = try_lock(str(tmp_path), IndexOperation.Load)
    assert guard_from_str.is_acquired()
    guard_from_str.release()

    guard_from_path = try_lock(tmp_path, IndexOperation.Load)
    assert guard_from_path.is_acquired()
    guard_from_path.release()
