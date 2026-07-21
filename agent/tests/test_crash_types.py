"""Tests for minimax_code.crash.types (R225, crash-handler type contract)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from minimax_code.crash.types import (
    MAX_HISTORY,
    CrashHandlerConfig,
    CrashReport,
    ResolvedFrame,
)


class TestMaxHistory:
    """grok ``const MAX_HISTORY``."""

    def test_value_matches_grok(self) -> None:
        assert MAX_HISTORY == 5


class TestResolvedFrame:
    """grok ``symbolicate::ResolvedFrame``."""

    def test_defaults_optional_fields_to_none(self) -> None:
        frame = ResolvedFrame(ip=0xDEADBEEF)
        assert frame.ip == 0xDEADBEEF
        assert frame.symbol_name is None
        assert frame.filename is None
        assert frame.lineno is None

    def test_full_construction(self) -> None:
        frame = ResolvedFrame(
            ip=0xCAFE,
            symbol_name="minimax_code.agent.core::step",
            filename="agent/minimax_code/agent/core.py",
            lineno=42,
        )
        assert frame.ip == 0xCAFE
        assert frame.symbol_name == "minimax_code.agent.core::step"
        assert frame.filename == "agent/minimax_code/agent/core.py"
        assert frame.lineno == 42

    def test_is_frozen(self) -> None:
        frame = ResolvedFrame(ip=1)
        first_field = next(iter(type(frame).__slots__))
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(frame, first_field, 999)

    def test_slots_block_dynamic_attrs(self) -> None:
        # frozen's __setattr__ raises first; bypass it to confirm slots also guards.
        frame = ResolvedFrame(ip=1)
        with pytest.raises(AttributeError):
            object.__setattr__(frame, "unexpected", "no dynamic attrs")


class TestCrashReport:
    """grok ``lib::CrashReport``."""

    def test_construction_round_trips_all_fields(self) -> None:
        path = Path("/tmp/crash-report.txt")
        frames = (ResolvedFrame(ip=1), ResolvedFrame(ip=2))
        report = CrashReport(
            signal_name="SIGSEGV (Segmentation fault)",
            si_code=1,
            faulting_address=0x7F0000000000,
            timestamp=1_712_678_587,
            app_version="0.8.0",
            backtrace=frames,
            report_path=path,
        )
        assert report.signal_name == "SIGSEGV (Segmentation fault)"
        assert report.si_code == 1
        assert report.faulting_address == 0x7F0000000000
        assert report.timestamp == 1_712_678_587
        assert report.app_version == "0.8.0"
        assert report.backtrace is frames
        assert report.report_path == path

    def test_backtrace_accepts_empty_tuple(self) -> None:
        report = CrashReport(
            signal_name="Unknown signal",
            si_code=0,
            faulting_address=0,
            timestamp=0,
            app_version="0.0.0",
            backtrace=(),
            report_path=Path("/x"),
        )
        assert report.backtrace == ()

    def test_is_frozen(self) -> None:
        report = CrashReport(
            signal_name="x",
            si_code=0,
            faulting_address=0,
            timestamp=0,
            app_version="x",
            backtrace=(),
            report_path=Path("/x"),
        )
        first_field = next(iter(type(report).__slots__))
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(report, first_field, "y")

    def test_value_equality(self) -> None:
        kwargs: dict[str, object] = {
            "signal_name": "x",
            "si_code": 1,
            "faulting_address": 2,
            "timestamp": 3,
            "app_version": "v",
            "backtrace": (ResolvedFrame(ip=4),),
            "report_path": Path("/x"),
        }
        assert CrashReport(**kwargs) == CrashReport(**kwargs)


class TestCrashHandlerConfig:
    """grok ``lib::CrashHandlerConfig``."""

    def test_construction(self) -> None:
        cfg = CrashHandlerConfig(app_version="0.8.0", crash_dir=Path("/tmp/crash"))
        assert cfg.app_version == "0.8.0"
        assert cfg.crash_dir == Path("/tmp/crash")

    def test_is_frozen(self) -> None:
        cfg = CrashHandlerConfig(app_version="x", crash_dir=Path("/x"))
        first_field = next(iter(type(cfg).__slots__))
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(cfg, first_field, "y")

    def test_value_equality(self) -> None:
        a = CrashHandlerConfig(app_version="1", crash_dir=Path("/a"))
        b = CrashHandlerConfig(app_version="1", crash_dir=Path("/a"))
        assert a == b
