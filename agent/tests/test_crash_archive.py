"""Tests for minimax_code.crash.archive (R225, report persistence + retention)."""

from __future__ import annotations

from pathlib import Path

from minimax_code.crash.archive import archive_report, prune_history
from minimax_code.crash.types import MAX_HISTORY


class TestArchiveReport:
    """grok ``lib::archive_report``."""

    def test_writes_report_and_returns_path(self, tmp_path: Path) -> None:
        crash_dir = tmp_path / "crash"
        path = archive_report(crash_dir, "=== REPORT ===", 1_712_678_587)
        assert path is not None
        assert path == crash_dir / "history" / "crash-1712678587.txt"
        assert path.read_text(encoding="utf-8") == "=== REPORT ==="

    def test_creates_history_dir_when_absent(self, tmp_path: Path) -> None:
        crash_dir = tmp_path / "crash"
        assert not crash_dir.exists()
        archive_report(crash_dir, "body", 100)
        assert (crash_dir / "history").is_dir()

    def test_unicode_report_text_round_trips(self, tmp_path: Path) -> None:
        crash_dir = tmp_path / "crash"
        body = "崩溃报告 — SIGSEGV @ 0x00000042"
        path = archive_report(crash_dir, body, 1)
        assert path is not None
        assert path.read_text(encoding="utf-8") == body

    def test_returns_none_when_history_dir_cannot_be_created(self, tmp_path: Path) -> None:
        # crash_dir is a file, so mkdir of crash_dir/history must fail.
        crash_dir = tmp_path / "blocker"
        crash_dir.write_text("I am a file, not a directory")
        assert archive_report(crash_dir, "body", 1) is None


class TestPruneHistory:
    """Extracted from grok ``archive_report`` inline pruning loop."""

    def test_keeps_all_when_at_max(self, tmp_path: Path) -> None:
        history = tmp_path / "history"
        history.mkdir()
        for i in range(MAX_HISTORY):
            (history / f"crash-{i}.txt").write_text(str(i))
        prune_history(history)
        assert len(list(history.glob("*.txt"))) == MAX_HISTORY

    def test_keeps_all_when_below_max(self, tmp_path: Path) -> None:
        history = tmp_path / "history"
        history.mkdir()
        for i in range(MAX_HISTORY - 1):
            (history / f"crash-{i}.txt").write_text(str(i))
        prune_history(history)
        assert len(list(history.glob("*.txt"))) == MAX_HISTORY - 1

    def test_prunes_oldest_beyond_max(self, tmp_path: Path) -> None:
        history = tmp_path / "history"
        history.mkdir()
        count = MAX_HISTORY + 3
        for i in range(count):
            (history / f"crash-{i}.txt").write_text(str(i))
        prune_history(history)
        remaining = sorted(history.glob("*.txt"))
        assert len(remaining) == MAX_HISTORY
        # filenames embed a monotonic timestamp, so lexical == chronological;
        # the oldest slice is pruned, the newest MAX_HISTORY kept.
        expected_names = [f"crash-{i}.txt" for i in range(count - MAX_HISTORY, count)]
        assert [p.name for p in remaining] == expected_names

    def test_only_txt_files_counted_for_pruning(self, tmp_path: Path) -> None:
        history = tmp_path / "history"
        history.mkdir()
        for i in range(MAX_HISTORY + 2):
            (history / f"crash-{i}.txt").write_text(str(i))
        (history / "README.md").write_text("keep me")
        (history / "crash-latest.log").write_text("log")
        prune_history(history)
        assert (history / "README.md").exists()
        assert (history / "crash-latest.log").exists()
        assert len(list(history.glob("*.txt"))) == MAX_HISTORY


class TestArchiveReportRetention:
    """End-to-end retention via repeated archive_report calls."""

    def test_repeated_archives_retain_only_max_history(self, tmp_path: Path) -> None:
        crash_dir = tmp_path / "crash"
        count = MAX_HISTORY + 4
        for i in range(count):
            path = archive_report(crash_dir, f"report-{i}", i)
            assert path is not None
        history = crash_dir / "history"
        remaining = sorted(history.glob("*.txt"))
        assert len(remaining) == MAX_HISTORY
        assert remaining[-1].name == f"crash-{count - 1}.txt"
        # oldest reports evicted
        assert not (history / "crash-0.txt").exists()
        assert not (history / "crash-1.txt").exists()

    def test_equal_timestamp_overwrites_same_file(self, tmp_path: Path) -> None:
        crash_dir = tmp_path / "crash"
        archive_report(crash_dir, "first", 500)
        archive_report(crash_dir, "second", 500)
        history = crash_dir / "history"
        files = list(history.glob("*.txt"))
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8") == "second"
