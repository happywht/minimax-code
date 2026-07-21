"""Tests for minimax_code.crash.handler (R229, install + write half of recovery).

Targets the grok ``xai-crash-handler`` ``handler.rs`` signal-installation leaf
migrated into the ``crash`` package: the ``install`` entry point (create
``crash_dir`` + arm ``faulthandler`` + replace ``sys.excepthook`` /
``threading.excepthook``) and the ``_persist_crash`` writer (build a
``CrashBlob`` and write it as ``last-crash.json``). Distinct from
``test_crash_orchestration.py`` (R228, the read-side ``check_previous_crash``
reader); the two halves meet in :class:`TestReadWriteLoop`.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from minimax_code.crash import handler as crash_handler
from minimax_code.crash.format import MAGIC, VERSION, CrashBlob
from minimax_code.crash.handler import (
    _PYTHON_EXCEPTION_SIGNAL,
    _persist_crash,
    install,
)
from minimax_code.crash.recovery import LAST_CRASH_FILE, check_previous_crash
from minimax_code.crash.types import CrashHandlerConfig


@pytest.fixture(autouse=True)
def _isolate_crash_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Save / restore module state + hooks ``install`` mutates.

    ``install`` writes the module-global ``_STATE`` and replaces the
    process-global ``sys.excepthook`` / ``threading.excepthook``; left
    unrestored these would leak into sibling tests. ``faulthandler.enable``
    is also process-scoped, so it is stubbed out (no real handler install).
    """
    monkeypatch.setattr(crash_handler.faulthandler, "enable", lambda *a, **k: None)
    saved_state = crash_handler._STATE
    saved_sys_hook = sys.excepthook
    saved_threading_hook = threading.excepthook
    yield
    crash_handler._STATE = saved_state
    sys.excepthook = saved_sys_hook
    threading.excepthook = saved_threading_hook


def _config(crash_dir: Path, app_version: str = "0.8.0") -> CrashHandlerConfig:
    return CrashHandlerConfig(app_version=app_version, crash_dir=crash_dir)


def _read_last_crash(crash_dir: Path) -> dict[str, object]:
    return json.loads((crash_dir / LAST_CRASH_FILE).read_text(encoding="utf-8"))


class TestInstall:
    """grok ``handler::install`` entry-point paths."""

    def test_creates_crash_dir_when_absent(self, tmp_path: Path) -> None:
        crash_dir = tmp_path / "crashes"
        assert install(_config(crash_dir)) is True
        assert crash_dir.is_dir()

    def test_returns_true_when_crash_dir_already_exists(self, tmp_path: Path) -> None:
        crash_dir = tmp_path / "crashes"
        crash_dir.mkdir()
        assert install(_config(crash_dir)) is True

    def test_returns_false_when_mkdir_fails(self, tmp_path: Path) -> None:
        # crash_dir's parent is a file -> mkdir raises OSError -> False.
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        crash_dir = blocker / "crashes"
        assert install(_config(crash_dir)) is False

    def test_arms_state_with_crash_dir_and_version(self, tmp_path: Path) -> None:
        crash_dir = tmp_path / "crashes"
        install(_config(crash_dir, app_version="0.9.0"))
        state = crash_handler._STATE
        assert state is not None
        assert state.crash_dir == crash_dir
        assert state.app_version == "0.9.0"

    def test_replaces_sys_excepthook(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        assert sys.excepthook is crash_handler._python_excepthook

    def test_replaces_threading_excepthook(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        assert threading.excepthook is crash_handler._threading_excepthook

    def test_enables_faulthandler(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[object, ...]] = []
        monkeypatch.setattr(
            crash_handler.faulthandler, "enable", lambda *a, **k: calls.append((a, k))
        )
        install(_config(tmp_path))
        assert len(calls) == 1


class TestPersistCrash:
    """grok ``write_crash_blob`` persistence paths."""

    def test_returns_none_when_not_installed(self) -> None:
        # _STATE is None until install runs (default fixture state).
        assert crash_handler._STATE is None
        assert _persist_crash(signal=10) is None

    def test_writes_last_crash_json_with_gcrx_envelope(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        _persist_crash(signal=10)
        payload = _read_last_crash(tmp_path)
        assert payload["magic"] == MAGIC
        assert payload["version"] == VERSION

    def test_stamps_app_version_from_install_config(self, tmp_path: Path) -> None:
        install(_config(tmp_path, app_version="0.9.0"))
        _persist_crash(signal=10)
        assert _read_last_crash(tmp_path)["app_version"] == "0.9.0"

    def test_stamps_live_pid(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        _persist_crash(signal=10)
        assert _read_last_crash(tmp_path)["pid"] == os.getpid()

    def test_stamps_epoch_timestamp(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        _persist_crash(signal=10)
        timestamp = _read_last_crash(tmp_path)["timestamp"]
        assert isinstance(timestamp, int)
        assert timestamp > 0

    def test_passes_signal_si_code_si_addr(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        _persist_crash(signal=11, si_code=2, si_addr=0xDEADBEEF)
        payload = _read_last_crash(tmp_path)
        assert payload["signal"] == 11
        assert payload["si_code"] == 2
        assert payload["si_addr"] == 0xDEADBEEF

    def test_passes_frames_chain(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        _persist_crash(signal=11, frames=(0xDEADBEEF, 0xCAFEBABE))
        assert _read_last_crash(tmp_path)["frames"] == [0xDEADBEEF, 0xCAFEBABE]

    def test_returns_crash_file_path(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        assert _persist_crash(signal=10) == tmp_path / LAST_CRASH_FILE

    def test_swallows_write_failure_and_still_returns_path(
        self, tmp_path: Path
    ) -> None:
        install(_config(tmp_path))
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            result = _persist_crash(signal=10)
        # Best-effort (grok ``let _ =``): no raise, path surfaced, file absent.
        assert result == tmp_path / LAST_CRASH_FILE
        assert not result.exists()


class TestExcepthooks:
    """``sys.excepthook`` / ``threading.excepthook`` replacement behavior."""

    def test_python_excepthook_persists_blob_with_exception_signal(
        self, tmp_path: Path
    ) -> None:
        install(_config(tmp_path))
        crash_handler._python_excepthook(ValueError, ValueError("boom"), None)
        payload = _read_last_crash(tmp_path)
        assert payload["signal"] == _PYTHON_EXCEPTION_SIGNAL
        assert payload["signal"] == 0

    def test_python_excepthook_skips_system_exit(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        crash_handler._python_excepthook(SystemExit, SystemExit(0), None)
        assert not (tmp_path / LAST_CRASH_FILE).exists()

    def test_python_excepthook_skips_keyboard_interrupt(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        crash_handler._python_excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        assert not (tmp_path / LAST_CRASH_FILE).exists()

    def test_threading_excepthook_persists_blob(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        args = SimpleNamespace(exc_type=ValueError)
        crash_handler._threading_excepthook(args)
        assert _read_last_crash(tmp_path)["signal"] == _PYTHON_EXCEPTION_SIGNAL

    def test_threading_excepthook_skips_system_exit(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        args = SimpleNamespace(exc_type=SystemExit)
        crash_handler._threading_excepthook(args)
        assert not (tmp_path / LAST_CRASH_FILE).exists()


class TestReadWriteLoop:
    """End-to-end: install -> _persist_crash -> check_previous_crash (R228)."""

    def test_round_trip_yields_crash_report(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        _persist_crash(signal=10, si_code=2, si_addr=0x7F8A12340000, frames=(0xDEADBEEF,))
        report = check_previous_crash(tmp_path)
        assert report is not None
        assert report.signal_name == "SIGBUS (Bus error)"
        assert report.si_code == 2
        assert report.faulting_address == 0x7F8A12340000
        assert report.app_version == "0.8.0"
        assert [f.ip for f in report.backtrace] == [0xDEADBEEF]

    def test_round_trip_consumes_last_crash_file(self, tmp_path: Path) -> None:
        install(_config(tmp_path))
        _persist_crash(signal=10)
        assert (tmp_path / LAST_CRASH_FILE).exists()
        check_previous_crash(tmp_path)
        assert not (tmp_path / LAST_CRASH_FILE).exists()

    def test_python_exception_round_trip_reports_unknown_signal(
        self, tmp_path: Path
    ) -> None:
        # An uncaught Python exception persists signal=0; recovery surfaces it
        # as the honest "Unknown signal" label rather than a misleading name.
        install(_config(tmp_path))
        crash_handler._python_excepthook(ValueError, ValueError("boom"), None)
        report = check_previous_crash(tmp_path)
        assert report is not None
        assert report.signal_name == "Unknown signal"

    def test_blob_as_payload_round_trips_through_format_leaf(
        self, tmp_path: Path
    ) -> None:
        # DRY: _persist_crash builds via CrashBlob.as_payload; ensure that payload
        # parses back through CrashBlob.from_payload (the R228 reader path).
        install(_config(tmp_path))
        _persist_crash(signal=11, si_code=1, si_addr=0x1000, frames=(0x2000, 0x3000))
        blob = CrashBlob.from_payload(_read_last_crash(tmp_path))
        assert blob is not None
        assert blob.signal == 11
        assert blob.si_addr == 0x1000
        assert blob.frames == (0x2000, 0x3000)
