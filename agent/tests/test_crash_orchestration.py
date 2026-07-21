"""Tests for minimax_code.crash.recovery (R228, check_previous_crash orchestration).

Distinct from ``test_crash_recovery.py`` (R12, the ``runtime.crash_detect``
marker-file protocol): this file targets the grok ``xai-crash-handler``
``lib.rs`` ``check_previous_crash`` orchestration leaf migrated into the
``crash`` package -- the read half of persisted-crash-record recovery that
consumes the format (R226) / symbolicate (R227) / archive (R225) leaves.
"""

from __future__ import annotations

import json
from pathlib import Path

from minimax_code.crash.format import CrashBlob
from minimax_code.crash.recovery import (
    LAST_CRASH_FILE,
    LAST_CRASH_REPORT_FILE,
    check_previous_crash,
)


def _sample_payload() -> dict[str, object]:
    """A valid crash-record payload (SIGBUS, mirrors grok's smoke test)."""
    blob = CrashBlob(
        signal=10,  # SIGBUS (macOS value; grok smoke uses 10)
        si_code=2,  # BUS_ADRERR
        si_addr=0x7F8A12340000,
        pid=42,
        timestamp=1_712_678_587,
        frames=(0xDEADBEEF, 0xCAFEBABE),
        app_version="0.8.0",
    )
    return blob.as_payload()


def _write_last_crash(crash_dir: Path, payload: dict[str, object]) -> Path:
    """Persist ``payload`` as ``last-crash.json`` under ``crash_dir``."""
    crash_file = crash_dir / LAST_CRASH_FILE
    crash_file.write_text(json.dumps(payload), encoding="utf-8")
    return crash_file


class TestCheckPreviousCrashMissing:
    """grok ``check_previous_crash`` no-record paths (returns None)."""

    def test_returns_none_when_crash_dir_absent(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        assert check_previous_crash(missing) is None

    def test_returns_none_when_no_last_crash_file(self, tmp_path: Path) -> None:
        # Directory exists but holds no last-crash.json.
        assert check_previous_crash(tmp_path) is None

    def test_returns_none_when_last_crash_path_is_a_directory(
        self, tmp_path: Path
    ) -> None:
        # read_text on a directory raises OSError (IsADirectoryError) -> None.
        (tmp_path / LAST_CRASH_FILE).mkdir()
        assert check_previous_crash(tmp_path) is None


class TestCheckPreviousCrashValid:
    """grok ``check_previous_crash`` happy path + side effects."""

    def test_returns_crash_report_with_blob_fields(self, tmp_path: Path) -> None:
        _write_last_crash(tmp_path, _sample_payload())
        report = check_previous_crash(tmp_path)
        assert report is not None
        assert report.signal_name == "SIGBUS (Bus error)"
        assert report.si_code == 2
        assert report.faulting_address == 0x7F8A12340000
        assert report.timestamp == 1_712_678_587
        assert report.app_version == "0.8.0"

    def test_signal_name_uses_signals_vocabulary(self, tmp_path: Path) -> None:
        # DRY: signal_name comes from crash.signals, not re-derived here.
        _write_last_crash(tmp_path, _sample_payload())
        report = check_previous_crash(tmp_path)
        assert report is not None
        assert "SIGBUS" in report.signal_name

    def test_report_path_points_at_last_crash_report_file(
        self, tmp_path: Path
    ) -> None:
        _write_last_crash(tmp_path, _sample_payload())
        report = check_previous_crash(tmp_path)
        assert report is not None
        assert report.report_path == tmp_path / LAST_CRASH_REPORT_FILE

    def test_report_text_written_to_report_path(self, tmp_path: Path) -> None:
        _write_last_crash(tmp_path, _sample_payload())
        report = check_previous_crash(tmp_path)
        assert report is not None
        written = report.report_path.read_text(encoding="utf-8")
        assert written.startswith("=== MiniMax Code Crash Report ===\n")
        assert written.endswith("=== End Report ===\n")

    def test_report_archived_to_history_with_timestamp_filename(
        self, tmp_path: Path
    ) -> None:
        _write_last_crash(tmp_path, _sample_payload())
        check_previous_crash(tmp_path)
        archived = tmp_path / "history" / "crash-1712678587.txt"
        assert archived.exists()
        assert archived.read_text(encoding="utf-8").startswith(
            "=== MiniMax Code Crash Report ==="
        )

    def test_last_crash_file_removed_after_successful_processing(
        self, tmp_path: Path
    ) -> None:
        crash_file = _write_last_crash(tmp_path, _sample_payload())
        assert crash_file.exists()
        check_previous_crash(tmp_path)
        assert not crash_file.exists()

    def test_backtrace_matches_resolve_frames_output(self, tmp_path: Path) -> None:
        _write_last_crash(tmp_path, _sample_payload())
        report = check_previous_crash(tmp_path)
        assert report is not None
        # resolve_frames best-effort placeholder: one frame per blob frame,
        # all-None symbol fields.
        assert len(report.backtrace) == 2
        assert [f.ip for f in report.backtrace] == [0xDEADBEEF, 0xCAFEBABE]
        for frame in report.backtrace:
            assert frame.symbol_name is None
            assert frame.filename is None
            assert frame.lineno is None


class TestCheckPreviousCrashMalformed:
    """grok ``check_previous_crash`` parse-failure paths (None, file kept)."""

    def test_returns_none_for_malformed_json(self, tmp_path: Path) -> None:
        crash_file = tmp_path / LAST_CRASH_FILE
        crash_file.write_text("{not valid json", encoding="utf-8")
        assert check_previous_crash(tmp_path) is None

    def test_returns_none_for_non_object_json(self, tmp_path: Path) -> None:
        # Valid JSON but not an object -> from_payload rejects non-dict.
        (tmp_path / LAST_CRASH_FILE).write_text("42", encoding="utf-8")
        assert check_previous_crash(tmp_path) is None

    def test_returns_none_for_bad_magic(self, tmp_path: Path) -> None:
        payload = _sample_payload()
        payload["magic"] = "XXXX"
        _write_last_crash(tmp_path, payload)
        assert check_previous_crash(tmp_path) is None

    def test_returns_none_for_bad_version(self, tmp_path: Path) -> None:
        payload = _sample_payload()
        payload["version"] = 999
        _write_last_crash(tmp_path, payload)
        assert check_previous_crash(tmp_path) is None

    def test_returns_none_for_wrong_frame_type(self, tmp_path: Path) -> None:
        payload = _sample_payload()
        payload["frames"] = ["not", "ints"]
        _write_last_crash(tmp_path, payload)
        assert check_previous_crash(tmp_path) is None

    def test_malformed_json_leaves_file_intact(self, tmp_path: Path) -> None:
        # Parse fails before the unlink step, so the raw record survives.
        crash_file = tmp_path / LAST_CRASH_FILE
        crash_file.write_text("{not valid json", encoding="utf-8")
        check_previous_crash(tmp_path)
        assert crash_file.exists()

    def test_bad_magic_leaves_file_intact(self, tmp_path: Path) -> None:
        payload = _sample_payload()
        payload["magic"] = "XXXX"
        crash_file = _write_last_crash(tmp_path, payload)
        check_previous_crash(tmp_path)
        assert crash_file.exists()

    def test_malformed_record_writes_no_report(self, tmp_path: Path) -> None:
        # A rejected record must not write last-crash-report.txt.
        (tmp_path / LAST_CRASH_FILE).write_text("42", encoding="utf-8")
        check_previous_crash(tmp_path)
        assert not (tmp_path / LAST_CRASH_REPORT_FILE).exists()
        assert not (tmp_path / "history").exists()
