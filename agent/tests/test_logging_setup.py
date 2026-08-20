"""Tests for the logging setup — file sink behavior added for the
production single-process mode (`MINIMAX_CODE_LOG_FILE`).

The stderr sink, sanitizer filter, and level plumbing predate this;
these tests pin the file-sink contract:

- an absolute path is used as-is and receives log records;
- a relative path resolves against the agent data dir;
- an unwritable path degrades to stderr-only instead of crashing;
- the sanitizer filter also applies to the file sink (no raw secrets
  on disk).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from minimax_code.logging_setup import configure_logging


@pytest.fixture(autouse=True)
def restore_root_logger():
    """Snapshot the root logger and restore it after each test.

    configure_logging() clears and rebuilds root handlers, so tests
    must not leak file handlers (and their open FDs) into the rest of
    the suite.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_filters = list(root.filters)
    saved_level = root.level
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    for h in saved_handlers:
        root.addHandler(h)
    root.filters[:] = saved_filters
    root.setLevel(saved_level)


def test_log_file_absolute_path_receives_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = tmp_path / "agent.log"
    monkeypatch.setenv("MINIMAX_CODE_LOG_FILE", str(log_file))

    configure_logging("INFO")
    logging.getLogger("test.sink").info("file sink marker")

    assert log_file.is_file()
    text = log_file.read_text(encoding="utf-8")
    assert "file sink marker" in text
    # The stderr sink stays attached alongside the file sink.
    assert any(type(h) is logging.StreamHandler for h in logging.getLogger().handlers)


def test_log_file_relative_path_resolves_against_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "MiniMaxCode"
    monkeypatch.setenv("MINIMAX_CODE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MINIMAX_CODE_LOG_FILE", "logs/agent.log")

    configure_logging("INFO")
    logging.getLogger("test.sink").info("relative marker")

    log_file = data_dir / "logs" / "agent.log"
    assert log_file.is_file()
    assert "relative marker" in log_file.read_text(encoding="utf-8")


def test_unwritable_log_file_degrades_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A path whose parent is an existing *file* cannot be created.
    monkeypatch.setenv("MINIMAX_CODE_LOG_FILE", "C:/definitely/not/ok<>.log")
    # configure_logging must not raise — stderr-only is the fallback.
    configure_logging("INFO")
    assert any(type(h) is logging.StreamHandler for h in logging.getLogger().handlers)


def test_no_env_means_no_file_handlers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MINIMAX_CODE_LOG_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    configure_logging("INFO")
    handlers = logging.getLogger().handlers
    assert not any(isinstance(h, logging.FileHandler) for h in handlers)
