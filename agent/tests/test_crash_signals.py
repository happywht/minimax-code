"""Tests for minimax_code.crash.signals (R225, POSIX signal vocabulary)."""

from __future__ import annotations

import pytest

from minimax_code.crash.signals import si_code_name, signal_name


class TestSignalName:
    """grok ``symbolicate::signal_name``."""

    @pytest.mark.parametrize(
        ("sig", "expected"),
        [
            (4, "SIGILL (Illegal instruction)"),
            (7, "SIGBUS (Bus error)"),  # Linux
            (10, "SIGBUS (Bus error)"),  # macOS
            (11, "SIGSEGV (Segmentation fault)"),
        ],
    )
    def test_known_signals(self, sig: int, expected: str) -> None:
        assert signal_name(sig) == expected

    @pytest.mark.parametrize("sig", [0, 1, 2, 3, 5, 6, 8, 9, 12, 15, 99])
    def test_unknown_signal_returns_placeholder(self, sig: int) -> None:
        assert signal_name(sig) == "Unknown signal"


class TestSiCodeName:
    """grok ``symbolicate::si_code_name``."""

    @pytest.mark.parametrize(
        ("sig", "code", "expected"),
        [
            (7, 1, "BUS_ADRALN - invalid address alignment"),
            (10, 1, "BUS_ADRALN - invalid address alignment"),  # macOS SIGBUS
            (7, 2, "BUS_ADRERR - non-existent physical address"),
            (10, 2, "BUS_ADRERR - non-existent physical address"),
            (7, 3, "BUS_OBJERR - object-specific hardware error"),
            (11, 1, "SEGV_MAPERR - address not mapped"),
            (11, 2, "SEGV_ACCERR - invalid permissions"),
        ],
    )
    def test_known_codes(self, sig: int, code: int, expected: str) -> None:
        assert si_code_name(sig, code) == expected

    @pytest.mark.parametrize("sig", [7, 10])
    def test_bus_unknown_code(self, sig: int) -> None:
        assert si_code_name(sig, 99) == "unknown"
        assert si_code_name(sig, 0) == "unknown"

    def test_segfault_unknown_code(self) -> None:
        assert si_code_name(11, 99) == "unknown"
        assert si_code_name(11, 0) == "unknown"

    def test_non_bus_signal_uses_segfault_table(self) -> None:
        # grok's else-branch (any non-BUS signal) consults the SEGV table.
        assert si_code_name(99, 1) == "SEGV_MAPERR - address not mapped"
        assert si_code_name(99, 2) == "SEGV_ACCERR - invalid permissions"
        assert si_code_name(99, 99) == "unknown"
