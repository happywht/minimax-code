"""Tests for minimax_code.crash.symbolicate (R227, backtrace symbolication)."""

from __future__ import annotations

from minimax_code.crash.format import CrashBlob
from minimax_code.crash.symbolicate import format_report, resolve_frames
from minimax_code.crash.types import ResolvedFrame


def _sample_blob() -> CrashBlob:
    """A representative crash blob (SIGBUS, mirrors grok's smoke test)."""
    return CrashBlob(
        signal=10,  # SIGBUS (macOS value; grok smoke uses 10)
        si_code=2,  # BUS_ADRERR
        si_addr=0x7F8A12340000,
        pid=42,
        timestamp=1_712_678_587,
        frames=(0xDEADBEEF, 0xCAFEBABE),
        app_version="0.8.0",
    )


class TestResolveFrames:
    """grok ``resolve_frames`` (best-effort placeholder)."""

    def test_one_resolved_frame_per_blob_frame(self) -> None:
        blob = _sample_blob()
        frames = resolve_frames(blob)
        assert len(frames) == len(blob.frames)

    def test_ip_values_preserved_in_order(self) -> None:
        blob = _sample_blob()
        frames = resolve_frames(blob)
        assert [f.ip for f in frames] == [0xDEADBEEF, 0xCAFEBABE]

    def test_all_symbol_fields_none(self) -> None:
        # Best-effort placeholder: no DWARF/symbol-table lookup in pure Python,
        # so every frame mirrors grok's stripped-binary fallback (all None).
        blob = _sample_blob()
        for frame in resolve_frames(blob):
            assert frame.symbol_name is None
            assert frame.filename is None
            assert frame.lineno is None

    def test_empty_frames_returns_empty_tuple(self) -> None:
        blob = CrashBlob(
            signal=11,
            si_code=1,
            si_addr=0,
            pid=0,
            timestamp=0,
            frames=(),
            app_version="x",
        )
        assert resolve_frames(blob) == ()


class TestFormatReportHeader:
    """grok ``format_report`` header (signal / si_code / address / pid / ...)."""

    def test_report_envelope_uses_product_brand(self) -> None:
        report = format_report(_sample_blob(), [])
        assert report.startswith("=== MiniMax Code Crash Report ===\n")
        assert report.endswith("=== End Report ===\n")

    def test_signal_line_uses_signal_name_vocabulary(self) -> None:
        report = format_report(_sample_blob(), [])
        assert "Signal:  SIGBUS (Bus error)\n" in report

    def test_si_code_line_uses_si_code_name_vocabulary(self) -> None:
        report = format_report(_sample_blob(), [])
        assert "si_code: 2 (BUS_ADRERR - non-existent physical address)\n" in report

    def test_address_line_is_zero_padded_18_char_hex(self) -> None:
        report = format_report(_sample_blob(), [])
        assert "Address: 0x00007f8a12340000\n" in report

    def test_pid_line(self) -> None:
        report = format_report(_sample_blob(), [])
        assert "PID:     42\n" in report

    def test_version_line(self) -> None:
        report = format_report(_sample_blob(), [])
        assert "Version: 0.8.0\n" in report

    def test_time_line_is_unix_best_effort(self) -> None:
        report = format_report(_sample_blob(), [])
        assert "Time:    1712678587 (unix)\n" in report


class TestFormatReportBacktrace:
    """grok ``format_report`` backtrace frame rendering."""

    def test_frame_count_in_backtrace_header(self) -> None:
        frames = resolve_frames(_sample_blob())
        report = format_report(_sample_blob(), frames)
        assert "Backtrace (2 frames):\n" in report

    def test_unknown_symbol_shown_when_symbol_none(self) -> None:
        frames = resolve_frames(_sample_blob())
        report = format_report(_sample_blob(), frames)
        assert "0x00000000deadbeef - <unknown>\n" in report
        assert "0x00000000cafebabe - <unknown>\n" in report

    def test_known_symbol_rendered(self) -> None:
        blob = _sample_blob()
        frames = (ResolvedFrame(ip=0xDEADBEEF, symbol_name="xai_grok_pager::main"),)
        report = format_report(blob, frames)
        assert "0x00000000deadbeef - xai_grok_pager::main\n" in report

    def test_at_line_when_filename_and_lineno_present(self) -> None:
        blob = _sample_blob()
        frames = (
            ResolvedFrame(
                ip=0xDEADBEEF,
                symbol_name="xai_grok_pager::main",
                filename="src/main.rs",
                lineno=42,
            ),
        )
        report = format_report(blob, frames)
        assert "           at src/main.rs:42\n" in report

    def test_no_at_line_when_symbol_fields_none(self) -> None:
        # resolve_frames placeholder emits all-None frames; no `at` line follows.
        blob = _sample_blob()
        frames = resolve_frames(blob)
        report = format_report(blob, frames)
        assert " at " not in report

    def test_no_at_line_when_only_filename_present(self) -> None:
        # grok binds the `at` sub-line only when BOTH filename AND lineno are
        # Some; filename alone must not emit it.
        blob = _sample_blob()
        frames = (ResolvedFrame(ip=0xDEADBEEF, filename="src/main.rs"),)
        report = format_report(blob, frames)
        assert " at " not in report

    def test_frame_index_right_aligned_width_3(self) -> None:
        blob = CrashBlob(
            signal=11,
            si_code=1,
            si_addr=0,
            pid=0,
            timestamp=0,
            frames=tuple(range(5)),  # indices 0..4 -> "  0".."  4"
            app_version="x",
        )
        report = format_report(blob, resolve_frames(blob))
        assert "    0: 0x0000000000000000 - <unknown>\n" in report
        assert "    4: 0x0000000000000004 - <unknown>\n" in report


class TestFormatReportSmoke:
    """End-to-end smoke (mirrors grok ``format_report_smoke``)."""

    def test_grok_smoke_parity(self) -> None:
        # Mirrors grok's format_report_smoke: SIGBUS / BUS_ADRERR / known symbol
        # + file:line all present in the rendered report.
        blob = CrashBlob(
            signal=10,
            si_code=2,
            si_addr=0x7F8A12340000,
            pid=42,
            timestamp=1_712_678_587,
            frames=(0xDEADBEEF,),
            app_version="0.1.169",
        )
        frames = (
            ResolvedFrame(
                ip=0xDEADBEEF,
                symbol_name="xai_grok_pager::main",
                filename="src/main.rs",
                lineno=42,
            ),
        )
        report = format_report(blob, frames)
        assert "SIGBUS" in report
        assert "BUS_ADRERR" in report
        assert "xai_grok_pager::main" in report
        assert "src/main.rs:42" in report
