"""Tests for minimax_code.__main__._wire_xai_crash_handler (R230, startup wiring).

The wiring closes the crash-recovery loop opened R225-R229: it calls
``crash.install`` early in ``cli_entry`` (arm the CrashBlob write half), re-runs
R12's ``install_faulthandler`` so the ``last_fault.trace`` file sink survives
``crash.install``'s stderr-default faulthandler re-enable, then reads back the
previous session's structured ``CrashReport`` via ``check_previous_crash`` and
logs a recovery warning. Any failure is swallowed fail-open like R12.

These tests stub ``install_faulthandler`` (R12's own behavior is covered by
``test_crash_detect.py``) and ``crash.install``'s ``faulthandler.enable`` (R229's
own behavior is covered by ``test_crash_handler.py``); R230's contract under test
is the *composition* -- install -> re-arm faulthandler -> recover -> log ->
fail-open -- not the leaves it stitches together.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from minimax_code import crash as crash_pkg
from minimax_code.__main__ import _wire_xai_crash_handler
from minimax_code.crash import handler as crash_handler
from minimax_code.crash.handler import _persist_crash, install
from minimax_code.crash.types import CrashHandlerConfig
from minimax_code.runtime import crash_detect


@pytest.fixture(autouse=True)
def _isolate_crash_wiring(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> SimpleNamespace:
    """Isolate the process-global side effects of ``_wire_xai_crash_handler``.

    The wiring touches three module-level mutators:

    * ``crash.install`` replaces :data:`sys.excepthook` /
      :data:`threading.excepthook`, sets ``crash.handler._STATE``, and calls
      :func:`faulthandler.enable` (all process-scoped).
    * R12 ``install_faulthandler`` opens a ``last_fault.trace`` file sink, sets
      ``runtime.crash_detect._FAULT_SINK``, and calls
      ``faulthandler.enable(sink)``.
    * ``default_data_dir`` reads ``MINIMAX_CODE_DATA_DIR``.

    ``crash.install``'s ``faulthandler.enable`` is stubbed (no real handler
    install); ``install_faulthandler`` is replaced with a recording stub so R230
    does not re-test R12's file-sink behavior and does not leak an open file
    handle; the data dir is pinned to ``tmp_path`` so
    ``default_data_dir() / "crashes"`` is sandboxed. Hooks + ``_STATE`` +
    ``_FAULT_SINK`` are saved / restored around the yield.
    """
    monkeypatch.setenv("MINIMAX_CODE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(crash_handler.faulthandler, "enable", lambda *a, **k: None)
    install_fh_calls: list[Path] = []
    monkeypatch.setattr(
        crash_detect,
        "install_faulthandler",
        lambda data_dir: install_fh_calls.append(data_dir) or True,
    )
    saved_state = crash_handler._STATE
    saved_sys_hook = sys.excepthook
    saved_threading_hook = threading.excepthook
    saved_fault_sink = crash_detect._FAULT_SINK
    yield SimpleNamespace(install_fh_calls=install_fh_calls, data_dir=tmp_path)
    crash_handler._STATE = saved_state
    sys.excepthook = saved_sys_hook
    threading.excepthook = saved_threading_hook
    crash_detect._FAULT_SINK = saved_fault_sink


def _seed_last_crash(crash_dir: Path, app_version: str = "0.8.0") -> None:
    """Write a canonical ``last-crash.json`` via the R229 writer half.

    Going through ``install`` + ``_persist_crash`` (rather than hand-writing
    JSON) keeps the payload format in lockstep with the R228 reader, so a format
    drift between writer and reader surfaces here rather than silently degrading
    to ``None``.
    """
    install(CrashHandlerConfig(app_version=app_version, crash_dir=crash_dir))
    _persist_crash(signal=10, si_code=2, si_addr=0xDEADBEEF)


class TestWireXaiCrashHandler:
    """``_wire_xai_crash_handler`` composition paths (R230)."""

    def test_no_previous_crash_returns_none_and_creates_crash_dir(
        self, _isolate_crash_wiring: SimpleNamespace
    ) -> None:
        crash_dir = _isolate_crash_wiring.data_dir / "crashes"
        assert not crash_dir.exists()
        result = _wire_xai_crash_handler("0.8.0")
        assert result is None
        assert crash_dir.is_dir()

    def test_recovers_previous_crash_report_and_warns(
        self,
        _isolate_crash_wiring: SimpleNamespace,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        crash_dir = _isolate_crash_wiring.data_dir / "crashes"
        _seed_last_crash(crash_dir)
        with caplog.at_level(logging.WARNING, logger="minimax_code.__main__"):
            result = _wire_xai_crash_handler("0.8.0")
        assert result is not None
        assert result.signal_name == "SIGBUS (Bus error)"
        assert result.faulting_address == 0xDEADBEEF
        assert result.app_version == "0.8.0"
        assert "previous session crash recovered" in caplog.text

    def test_re_arms_r12_faulthandler_after_crash_install(
        self, _isolate_crash_wiring: SimpleNamespace
    ) -> None:
        # crash.install calls faulthandler.enable() (stderr default), which would
        # reset R12's last_fault.trace file sink. _wire_xai_crash_handler must
        # re-run install_faulthandler AFTER install so R12's sink wins; the call
        # is recorded exactly once with crash_dir.parent (== data_dir).
        _wire_xai_crash_handler("0.8.0")
        assert _isolate_crash_wiring.install_fh_calls == [
            _isolate_crash_wiring.data_dir
        ]

    def test_install_failure_is_fail_open(
        self,
        _isolate_crash_wiring: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def _raise(*_a: object, **_k: object) -> bool:
            raise RuntimeError("install boom")

        monkeypatch.setattr(crash_pkg, "install", _raise)
        with caplog.at_level(logging.DEBUG, logger="minimax_code.__main__"):
            result = _wire_xai_crash_handler("0.8.0")
        assert result is None
        assert "xai-crash-handler wiring failed" in caplog.text

    def test_check_previous_crash_failure_is_fail_open(
        self,
        _isolate_crash_wiring: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def _raise(*_a: object, **_k: object) -> None:
            raise RuntimeError("recovery boom")

        monkeypatch.setattr(crash_pkg, "check_previous_crash", _raise)
        with caplog.at_level(logging.DEBUG, logger="minimax_code.__main__"):
            result = _wire_xai_crash_handler("0.8.0")
        assert result is None
        assert "xai-crash-handler wiring failed" in caplog.text

    def test_app_version_stamped_into_install_config(
        self,
        _isolate_crash_wiring: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: list[CrashHandlerConfig] = []

        def _capture(config: CrashHandlerConfig) -> bool:
            captured.append(config)
            return True

        monkeypatch.setattr(crash_pkg, "install", _capture)
        _wire_xai_crash_handler("0.9.0-rc1")
        assert len(captured) == 1
        assert captured[0].app_version == "0.9.0-rc1"
        assert captured[0].crash_dir == _isolate_crash_wiring.data_dir / "crashes"
